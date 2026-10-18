# ARCHIVED: this module is no longer part of cp2kbuild.
# It implemented the old scan/converge machinery (generating
# arbitrary sweeps and reading CP2K outputs back).  The tool now only
# writes the official CUTOFF/REL_CUTOFF script set - see
# cp2kbuild/cutoffkit.py - and the analysis happens on the cluster.
# Kept for reference; it is not imported, tested or bundled.
"""Read CP2K output files and turn a convergence scan into a table.

Everything is taken from the output itself, so it does not matter how the inputs
were produced (a flat ``cp2kbuild scan`` directory, a hand-written template with
``@SET``/``@INCLUDE``, or one directory per cutoff value):

* ``QS| Density cutoff [a.u.]``          - the actual CUTOFF, in Hartree
* ``QS| Relative density cutoff [a.u.]`` - the actual REL_CUTOFF, in Hartree
* ``count for grid N: ... cutoff [a.u.]`` - how many grid points each level holds
* ``ENERGY| Total FORCE_EVAL ...``        - the final total energy
* ``Total energy:``                       - fallback, printed by the SCF itself
* ``Atomic kind: X  Number of atoms: N``  - to get the atom count

The print formats were checked against real CP2K output and against the Fortran
source (``force_env_methods.F``, ``qs_environment_types.F``).  Cutoffs are
reported in Ry, which is how CP2K inputs usually express them and how the
convergence tables in the manual are written (1 Ry = 0.5 Hartree).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Hartree is CP2K's internal energy unit
ENERGY_TO_HARTREE = {
    "hartree": 1.0,
    "a.u.": 1.0,
    "au": 1.0,
    "ry": 0.5,
    "rydberg": 0.5,
    "ev": 1.0 / 27.211386245988,
    "kj/mol": 1.0 / 2625.4996394799,
    "kcal/mol": 1.0 / 627.5094740631,
    "kelvin": 1.0 / 315775.02480407,
}

HARTREE_TO_EV = 27.211386245988
HARTREE_TO_RY = 2.0

RE_ENERGY = re.compile(
    r"ENERGY\|\s*Total FORCE_EVAL\s*\(\s*([A-Za-z0-9_]+)\s*\)\s*"
    r"energy\s*\[([^\]]+)\]\s*:?\s*(-?\d+\.?\d*(?:[EeDd][-+]?\d+)?)")
RE_TOTAL_ENERGY = re.compile(
    r"^\s*Total energy:\s+(-?\d+\.?\d*(?:[EeDd][-+]?\d+)?)\s*$")
RE_CUTOFF_AU = re.compile(r"QS\|\s*Density cutoff \[a\.u\.\]:\s+([0-9.eE+-]+)")
RE_RELCUTOFF_AU = re.compile(
    r"QS\|\s*Relative density cutoff \[a\.u\.\]:\s+([0-9.eE+-]+)")
RE_GRID_LEVELS = re.compile(r"QS\|\s*Number of grid levels:\s+(\d+)")
RE_GRID_COUNT = re.compile(
    r"^\s*count for grid\s+(\d+):\s+(\d+)\s+cutoff \[a\.u\.\]\s+([0-9.]+)")
RE_KIND_ATOMS = re.compile(
    r"Atomic kind:\s*([A-Za-z][A-Za-z0-9_+-]*)\s+Number of atoms:\s+(\d+)")
RE_PWGRID_CUTOFF = re.compile(r"PW_GRID\|\s*Cutoff \[a\.u\.\]\s+([0-9.]+)")
RE_PROGRAM_ENDED = re.compile(r"PROGRAM ENDED AT", re.I)
RE_SCF_FAILED = re.compile(r"SCF run NOT converged", re.I)
RE_OUTER_FAILED = re.compile(r"outer SCF loop FAILED to converge", re.I)
RE_NOT_CONVERGED = re.compile(r"NOT converged", re.I)
RE_ABORT = re.compile(r"^\s*ABORT|ERROR in|CP2K \| \*\*\* ", re.I)

RE_FILENAME_CUTOFF = re.compile(r"cutoff[_ -]?([0-9.]+)", re.I)
RE_FILENAME_RELCUTOFF = re.compile(r"rel[_ -]?cutoff[_ -]?([0-9.]+)", re.I)
RE_DIR_CUTOFF = re.compile(r"(?:^|[^a-z])cutoff[_ -]?([0-9.]+)\s*ry?$", re.I)
RE_DIR_RELCUTOFF = re.compile(r"rel[_ -]?cutoff[_ -]?([0-9.]+)\s*ry?$", re.I)

# the two ways a CP2K input can carry the value of a keyword
RE_SET_LINE = re.compile(r"^\s*@SET\s+([A-Za-z_][A-Za-z0-9_]*)\s+(\S+)", re.M)
RE_LITERAL_LINE = re.compile(r"^(\s*)(CUTOFF|REL_CUTOFF)\s+(\S+)", re.M)
RE_INCLUDE = re.compile(r"^\s*@INCLUDE\s+(\S+)", re.M)


@dataclass
class RunResult:
    path: Path
    cutoff: float | None = None
    rel_cutoff: float | None = None
    cutoff_source: str | None = None
    rel_cutoff_source: str | None = None
    energy_hartree: float | None = None
    energy_source: str | None = None
    program: str | None = None
    unit: str | None = None
    n_atoms: int | None = None
    n_electrons: int | None = None
    grid_levels: int | None = None
    grid_counts: list[tuple[int, int, float]] = field(default_factory=list)
    finished: bool = False
    scf_converged: bool | None = None
    outer_converged: bool | None = None
    ignore_failure: bool = False
    problems: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        parts = []
        if self.cutoff is not None:
            parts.append(f"CUTOFF {self.cutoff:g}")
        if self.rel_cutoff is not None:
            parts.append(f"REL_CUTOFF {self.rel_cutoff:g}")
        return ", ".join(parts) or self.path.stem

    @property
    def n_points(self) -> int | None:
        if not self.grid_counts:
            return None
        return sum(count for _, count, _ in self.grid_counts)


def to_hartree(value: float, unit: str) -> float | None:
    factor = ENERGY_TO_HARTREE.get(unit.strip().lower())
    return None if factor is None else value * factor


def _float(token: str) -> float | None:
    try:
        return float(token.replace("D", "E").replace("d", "e"))
    except ValueError:
        return None


def _sibling_input(out_path: Path) -> Path | None:
    for suffix in (".inp", ".restart"):
        candidate = out_path.with_suffix(suffix)
        if candidate.is_file():
            return candidate
    return None


def read_include(path: Path, seen: set[Path] | None = None,
                 depth: int = 0) -> str:
    """Text of ``path`` with ``@INCLUDE`` files spliced in.

    Enough to find ``CUTOFF``/``REL_CUTOFF``/``&COORD`` in a template that keeps
    its sections in include files.  ``@IF`` branches are *not* evaluated - all of
    them are read, which is fine for looking a keyword up and is never used to
    validate an input.
    """
    seen = set() if seen is None else seen
    try:
        resolved = path.resolve()
    except OSError:
        return ""
    if resolved in seen or depth > 8:
        return ""
    seen.add(resolved)
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""

    def replace(match: re.Match) -> str:
        name = match.group(1).strip('"\'')
        target = path.parent / name
        if not target.is_file():
            return match.group(0)
        return read_include(target, seen, depth + 1)

    return RE_INCLUDE.sub(replace, text)


def _from_input(path: Path) -> tuple[float | None, float | None, int | None]:
    """CUTOFF, REL_CUTOFF and the atom count, read from a CP2K input file."""
    try:
        text = read_include(path)
    except OSError:
        return None, None, None

    cutoff = rel_cutoff = None
    atoms = 0

    # a template that keeps its parameters in ``@SET <NAME> <value>`` lines
    for name, value in RE_SET_LINE.findall(text):
        number = _float(value)
        if number is None:
            continue
        if name.upper() == "CUTOFF":
            cutoff = number
        elif name.upper() == "REL_CUTOFF":
            rel_cutoff = number

    # a plain ``CUTOFF 450`` / ``REL_CUTOFF 60`` line
    for _, name, value in RE_LITERAL_LINE.findall(text):
        number = _float(value)
        if number is None:
            continue
        if name.upper() == "CUTOFF":
            cutoff = number
        elif name.upper() == "REL_CUTOFF":
            rel_cutoff = number

    # the section tree, which also gives the coordinates
    from .parser import read_input

    try:
        parsed = read_input(path)
    except Exception:  # noqa: BLE001 - a broken input is not fatal here
        parsed = None
    if parsed is not None:
        for root in parsed.sections:
            for _, node in root.walk():
                if node.name.upper() == "MGRID":
                    value = node.get("CUTOFF")
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        cutoff = float(value)
                    value = node.get("REL_CUTOFF")
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        rel_cutoff = float(value)
                elif node.name.upper() == "COORD":
                    for keyword in node.keywords:
                        if len(keyword.values) >= 3 and all(
                                isinstance(v, (int, float)) and not isinstance(v, bool)
                                for v in keyword.values[:3]):
                            atoms += 1
    return cutoff, rel_cutoff, atoms or None


def _from_path(path: Path) -> tuple[float | None, float | None]:
    """CUTOFF / REL_CUTOFF encoded in a directory or file name.

    ``cutoff_450Ry/system.out`` and ``cutoff_450/system.out`` are both common
    layouts, so the value signed by "cutoff" in a directory name is trusted.
    """
    cutoff = rel_cutoff = None
    for part in list(path.parts[:-1])[::-1] + [path.stem]:
        match = RE_DIR_RELCUTOFF.search(part)
        if match and rel_cutoff is None:
            rel_cutoff = _float(match.group(1))
            continue
        match = RE_DIR_CUTOFF.search(part)
        if match and cutoff is None:
            cutoff = _float(match.group(1))
            continue
        if rel_cutoff is None:
            match = RE_FILENAME_RELCUTOFF.search(part)
            if match:
                rel_cutoff = _float(match.group(1))
                continue
        if cutoff is None:
            match = RE_FILENAME_CUTOFF.search(part)
            if match:
                cutoff = _float(match.group(1))
    return cutoff, rel_cutoff


def read_output(path: str | Path) -> RunResult:
    """Parse one CP2K output file, streaming it so big files stay cheap."""
    file = Path(path)
    result = RunResult(file)

    energy: tuple[str, str, str] | None = None       # (program, unit, raw)
    total_energy: float | None = None
    kinds: dict[str, int] = {}
    grid_counts: list[tuple[int, int, float]] = []
    grid_levels: int | None = None
    cutoff_au = rel_cutoff_au = None
    inner_cutoffs: list[float] = []
    finished = scf_failed = outer_failed = aborted = False

    try:
        handle = file.open("r", encoding="utf-8", errors="ignore")
    except OSError as exc:
        result.problems.append(str(exc))
        return result

    with handle:
        for line in handle:
            if "ENERGY|" in line:
                match = RE_ENERGY.search(line)
                if match:
                    energy = match.groups()
            elif "Total energy:" in line:
                match = RE_TOTAL_ENERGY.match(line)
                if match:
                    total_energy = _float(match.group(1))
            elif "QS|" in line:
                if "Number of grid levels" in line:
                    match = RE_GRID_LEVELS.search(line)
                    if match:
                        grid_levels = int(match.group(1))
                elif "Density cutoff" in line:
                    match = RE_CUTOFF_AU.search(line)
                    if match:
                        cutoff_au = _float(match.group(1))
                elif "Relative density cutoff" in line:
                    match = RE_RELCUTOFF_AU.search(line)
                    if match:
                        rel_cutoff_au = _float(match.group(1))
            elif "count for grid" in line:
                match = RE_GRID_COUNT.match(line)
                if match:
                    grid_counts.append((int(match.group(1)),
                                        int(match.group(2)),
                                        float(match.group(3))))
            elif "PW_GRID|" in line:
                match = RE_PWGRID_CUTOFF.search(line)
                if match:
                    inner_cutoffs.append(float(match.group(1)))
            elif "Atomic kind:" in line:
                match = RE_KIND_ATOMS.search(line)
                if match and match.group(1) not in kinds:
                    kinds[match.group(1)] = int(match.group(2))
            elif "PROGRAM ENDED" in line:
                finished = True
            elif "NOT converged" in line or "FAILED to converge" in line:
                if RE_SCF_FAILED.search(line):
                    scf_failed = True
                elif RE_OUTER_FAILED.search(line):
                    outer_failed = True
                elif RE_NOT_CONVERGED.search(line) and not finished:
                    aborted = True

    if energy is not None:
        program, unit, raw = energy
        value = to_hartree(float(raw.replace("D", "E").replace("d", "e")), unit)
        if value is None:
            result.problems.append(f"unknown energy unit {unit!r}")
        else:
            result.energy_hartree = value
            result.program = program
            result.unit = unit
            result.energy_source = "ENERGY|"
    elif total_energy is not None:
        # some runs (and PRINT_LEVEL settings) only print the SCF total energy
        result.energy_hartree = total_energy
        result.unit = "hartree"
        result.energy_source = "Total energy:"
    else:
        result.problems.append("no ENERGY| Total FORCE_EVAL line found")

    result.finished = finished
    result.scf_converged = None if not (scf_failed or outer_failed) else False
    if scf_failed:
        result.outer_converged = False if outer_failed else result.outer_converged
    if aborted and result.energy_hartree is None:
        result.problems.append("run did not finish")
    if result.energy_hartree is None and scf_failed:
        result.problems.append("SCF did not converge")

    result.grid_levels = grid_levels
    result.grid_counts = grid_counts
    result.n_atoms = sum(kinds.values()) or None

    # the actual values used, straight from the output - this is the only source
    # that cannot be fooled by how the input was assembled
    if cutoff_au is not None:
        result.cutoff = cutoff_au * HARTREE_TO_RY
        result.cutoff_source = "output"
    if rel_cutoff_au is not None:
        result.rel_cutoff = rel_cutoff_au * HARTREE_TO_RY
        result.rel_cutoff_source = "output"
    if result.cutoff is None and inner_cutoffs:
        result.cutoff = max(inner_cutoffs) * HARTREE_TO_RY
        result.cutoff_source = "PW_GRID"

    # a sibling input is only read when it is actually needed: for a parameter
    # the output did not report, or to explain an unconverged SCF
    if (result.cutoff is None or result.rel_cutoff is None
            or result.n_atoms is None or scf_failed):
        source = _sibling_input(file)
        if source is not None:
            if scf_failed:
                result.ignore_failure = _ignores_failure(source)
            if result.cutoff is None or result.rel_cutoff is None or result.n_atoms is None:
                in_cutoff, in_rel, atoms = _from_input(source)
                if result.cutoff is None and in_cutoff is not None:
                    result.cutoff = in_cutoff
                    result.cutoff_source = "input"
                if result.rel_cutoff is None and in_rel is not None:
                    result.rel_cutoff = in_rel
                    result.rel_cutoff_source = "input"
                if result.n_atoms is None and atoms:
                    result.n_atoms = atoms

    if result.cutoff is None or result.rel_cutoff is None:
        path_cutoff, path_rel = _from_path(file)
        if result.cutoff is None and path_cutoff is not None:
            result.cutoff = path_cutoff
            result.cutoff_source = "name"
        if result.rel_cutoff is None and path_rel is not None:
            result.rel_cutoff = path_rel
            result.rel_cutoff_source = "name"

    return result


def _ignores_failure(path: Path) -> bool:
    try:
        text = read_include(path)
    except OSError:
        return False
    return bool(re.search(r"IGNORE_CONVERGENCE_FAILURE\s+[Tt](rue)?\b", text))


def collect(paths: list[str], recursive: bool = True) -> list[RunResult]:
    """Read outputs from files, inputs or directories.

    Directories are searched recursively so that one-directory-per-value layouts
    (``cutoff_450Ry/system.out``) work as well as a flat scan directory.
    """
    results: list[RunResult] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found = sorted(path.glob("*.out"))
            if recursive:
                found += sorted(candidate for candidate in path.rglob("*.out")
                                if candidate.parent != path)
            for candidate in found:
                results.append(read_output(candidate))
        elif path.is_file():
            results.append(read_output(path))
    return results


@dataclass
class Summary:
    rows: list[dict]
    reference: RunResult | None
    suggested_cutoff: float | None
    suggested_rel_cutoff: float | None
    threshold: float
    swept: str = "cutoff"
    notes: list[str] = field(default_factory=list)
    reference_path: str | None = None
    warnings: list[str] = field(default_factory=list)


def _empty_levels(row: dict) -> list[int]:
    """Grid levels that hold no points at all - a sign REL_CUTOFF is too small."""
    return [level for level, count, _ in row.get("grid_counts") or [] if count == 0]


def _suggest(rows: list[dict], key: str, threshold: float) -> float | None:
    """Smallest value of ``key`` from which every higher value is converged.

    Needs at least two distinct values: convergence is a statement about how
    much the answer still moves when the parameter grows, so the finest point on
    its own can never demonstrate it.
    """
    usable = [row for row in rows if row[key] is not None and row["finished"]]
    for row in usable:
        value = float(row[key])
        higher = [other for other in usable if float(other[key]) >= value]
        if len({float(other[key]) for other in higher}) < 2:
            continue
        if all(abs(other["delta_mev_per_atom"]) < threshold for other in higher):
            return value
    return None


def _dedupe(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Drop outputs that repeat a parameter point, complaining if they disagree.

    Two directories can hold the same CUTOFF/REL_CUTOFF pair (a cutoff sweep and
    a rel_cutoff sweep overlap in exactly one point).  Keeping both would double
    count it in the table; keeping them silently would hide a real problem, so
    identical duplicates are merged and *disagreeing* ones are reported.
    """
    seen: dict[tuple, dict] = {}
    notes: list[str] = []
    for row in rows:
        key = (row["cutoff"], row["rel_cutoff"])
        first = seen.get(key)
        if first is None:
            seen[key] = dict(row, duplicates=[])
            continue
        first["duplicates"].append(row["path"])
        if abs(first["energy_hartree"] - row["energy_hartree"]) > 1e-9:
            notes.append(
                f"同一个参数点（CUTOFF {key[0]:g} / REL_CUTOFF {key[1]:g}）算出了两个"
                f"不同的能量：{first['energy_hartree']:.12f} 与 "
                f"{row['energy_hartree']:.12f}。表里只保留了前者，"
                "但这说明两次运行的设置并不完全一样，值得查一下。")
    return list(seen.values()), notes


def _suggest_rel_cutoff(rows: list[dict], threshold: float) -> float | None:
    """Smallest REL_CUTOFF that satisfies the energy criterion *and* fills the grid.

    The energy alone is not enough: a REL_CUTOFF so small that the finest grid
    level holds no points still gives a stable energy, but the multi-grid
    assignment is plainly wrong.  Such a value is skipped, and the note added by
    :func:`summarise` explains why when nothing better exists.
    """
    usable = [row for row in rows if row["rel_cutoff"] is not None and row["finished"]
              and not _empty_levels(row)]
    return _suggest(usable or rows, "rel_cutoff", threshold)


def summarise(results: list[RunResult], threshold_mev_per_atom: float = 1.0) -> Summary:
    """Build the convergence table and suggest a converged cutoff.

    A value counts as converged when its energy per atom differs from the
    *finest* run by less than ``threshold`` and stays inside the threshold for
    every larger value present.  For a two-dimensional scan the cutoff is judged
    inside each rel_cutoff group and the most conservative answer is reported.
    """
    usable = [r for r in results if r.energy_hartree is not None]
    if not usable:
        return Summary([], None, None, None, threshold_mev_per_atom)

    rows: list[dict] = []
    for run in usable:
        rows.append({
            "cutoff": run.cutoff,
            "rel_cutoff": run.rel_cutoff,
            "cutoff_source": run.cutoff_source,
            "rel_cutoff_source": run.rel_cutoff_source,
            "energy_hartree": run.energy_hartree,
            "energy_source": run.energy_source,
            "n_atoms": run.n_atoms,
            "grid_levels": run.grid_levels,
            "grid_counts": run.grid_counts,
            "finished": run.finished,
            "scf_converged": run.scf_converged,
            "outer_converged": run.outer_converged,
            "ignore_failure": run.ignore_failure,
            "problems": run.problems,
            "path": str(run.path),
        })

    rows, notes = _dedupe(rows)
    summary = _build_summary(rows, threshold_mev_per_atom, notes=notes)
    summary.warnings = notes          # shared by every sweep family
    summary.reference = next(
        (r for r in usable if str(r.path) == summary.reference_path), None)
    return summary


def _build_summary(rows: list[dict], threshold: float, swept: str | None = None,
                   notes: list[str] | None = None) -> Summary:
    """Turn already-built rows into a table, a suggestion and its caveats.

    The reference point is chosen *here*, not by the caller: each one-parameter
    sweep needs its own finest run, otherwise the deltas of two sweeps that were
    computed at different settings would be mixed.
    """
    notes = list(notes or [])
    if not rows:
        return Summary([], None, None, None, threshold)

    reference_row = max(rows, key=lambda row: (row["cutoff"] or 0.0,
                                               row["rel_cutoff"] or 0.0))
    reference_path = reference_row["path"]
    reference_energy = reference_row["energy_hartree"]
    for row in rows:
        n_atoms = row["n_atoms"] or 1
        delta_hartree = row["energy_hartree"] - reference_energy
        delta = delta_hartree * HARTREE_TO_EV * 1000.0
        row["delta_hartree"] = delta_hartree
        # the official tutorial states its criterion as an *absolute* total
        # energy error (e.g. 1e-6 Ry), so the same difference is kept in a.u. too
        row["delta_ry"] = delta_hartree * HARTREE_TO_RY
        row["delta_mev"] = delta
        row["delta_mev_per_atom"] = delta / n_atoms
    rows.sort(key=lambda row: (row["cutoff"] if row["cutoff"] is not None
                               else float("inf"),
                               row["rel_cutoff"] if row["rel_cutoff"] is not None
                               else float("inf")))

    by_cutoff = sorted(rows, key=lambda row: (row["cutoff"] or 0.0,
                                              row["rel_cutoff"] or 0.0))
    by_rel = sorted(rows, key=lambda row: (row["rel_cutoff"] or 0.0,
                                           row["cutoff"] or 0.0))

    groups: dict[float | None, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["rel_cutoff"], []).append(row)

    if len(groups) > 1:
        # two-dimensional scan: judge the cutoff within each rel_cutoff group
        # and report the strictest result
        candidates = [
            value for value in (
                _suggest(sorted(group, key=lambda row: row["cutoff"] or 0.0),
                         "cutoff", threshold)
                for group in groups.values())
            if value is not None
        ]
        suggested_cutoff = max(candidates) if candidates else None
        suggested_rel_cutoff = _suggest_rel_cutoff(rows, threshold)
    else:
        suggested_cutoff = _suggest(by_cutoff, "cutoff", threshold)
        suggested_rel_cutoff = _suggest_rel_cutoff(rows, threshold)

    distinct_cutoffs = {row["cutoff"] for row in rows if row["cutoff"] is not None}
    distinct_rel = {row["rel_cutoff"] for row in rows if row["rel_cutoff"] is not None}
    if swept is None:
        if len(distinct_cutoffs) > 1 and len(distinct_rel) > 1:
            swept = "both"
        elif len(distinct_rel) > 1 and len(distinct_cutoffs) == 1:
            swept = "rel_cutoff"
        else:
            swept = "cutoff"

    summary = Summary(rows, None, suggested_cutoff, suggested_rel_cutoff,
                      threshold, swept, notes, reference_path)

    if suggested_cutoff is not None and len(distinct_cutoffs) > 1:
        if suggested_cutoff <= min(distinct_cutoffs):
            summary.notes.append(
                f"你扫的最小一档（CUTOFF {min(distinct_cutoffs):g} Ry）就已经满足判据了："
                "要么下限本来选得比需要的还高，要么这个体系对网格不敏感。"
                "想省机时可以往下再扫几档确认；既然最粗的一档也够，往上加只会更安全、更贵。")

    if suggested_rel_cutoff is not None:
        energy_only = _suggest(by_rel, "rel_cutoff", threshold)
        flagged = [row for row in rows
                   if row["rel_cutoff"] == suggested_rel_cutoff and _empty_levels(row)]
        if energy_only is not None and energy_only != suggested_rel_cutoff:
            bad = [row for row in rows if row["rel_cutoff"] == energy_only
                   and _empty_levels(row)]
            levels = ", ".join(str(level) for level in _empty_levels(bad[0])) if bad else "?"
            summary.notes.append(
                f"只按能量判据的话 REL_CUTOFF {energy_only:g} Ry 就够了，"
                f"但那一档下第 {levels} 档网格一个点都没有——那档网格完全没被用上，"
                f"所以建议值提到 {suggested_rel_cutoff:g} Ry。"
                "能量对这件事不敏感：网格分配错了，能量也可能看起来很稳。")
        elif flagged:
            levels = ", ".join(str(level) for level in _empty_levels(flagged[0]))
            summary.notes.append(
                f"REL_CUTOFF {suggested_rel_cutoff:g} Ry 满足能量判据，但这一档下"
                f"第 {levels} 档网格一个点都没有——那档网格完全没被用上。"
                "取更大的值（比如 40–60 Ry）更稳妥，代价是机时。")
        else:
            # the point distribution is the second half of the REL_CUTOFF story:
            # if it is still moving at the suggested value, being generous is
            # safer and the tutorial interval (60-80 Ry) is a sane default
            here = [row for row in rows
                    if row["rel_cutoff"] == suggested_rel_cutoff and row["grid_counts"]]
            finest = max((row for row in rows if row["grid_counts"]),
                         key=lambda row: row["rel_cutoff"] or 0.0, default=None)
            if here and finest and finest["rel_cutoff"] != suggested_rel_cutoff:
                first_level = sum(count for level, count, _ in here[0]["grid_counts"]
                                  if level == 1)
                last_level = sum(count for level, count, _ in finest["grid_counts"]
                                 if level == 1)
                if last_level > 2 * max(first_level, 1):
                    summary.notes.append(
                        f"提醒：能量虽然从 {suggested_rel_cutoff:g} Ry 就够了，"
                        f"但最细一档网格上的点数从 {first_level} 涨到了 {last_level}"
                        f"（REL_CUTOFF {finest['rel_cutoff']:g} Ry），说明网格分配还在变化。"
                        "这种体系实际建议取 60–80 Ry：能量一样，力的误差更小。")

    scf_flagged = [row for row in rows if row["scf_converged"] is False]
    if scf_flagged:
        ignored = [row for row in scf_flagged if row["ignore_failure"]]
        detail = ("输入里设了 IGNORE_CONVERGENCE_FAILURE，CP2K 照常打印能量——"
                  "比较不同 CUTOFF 之间的**差**可以，别把这些数当收敛好的绝对能量用。"
                  if ignored else
                  "这些能量是在 SCF 没收敛时打印的，只能当趋势看，结论要谨慎。")
        summary.notes.append(
            f"有 {len(scf_flagged)}/{len(rows)} 个输出里的 SCF 没有收敛。{detail}")

    summary.notes.extend(_multigrid_notes(rows))
    summary.notes.extend(_cutoff_distribution_notes(rows, suggested_cutoff, swept))
    return summary


def _levels_of(row: dict) -> dict[int, tuple[int, float]]:
    """``{level: (count, cutoff[Ha])}`` for one row."""
    return {level: (count, cutoff) for level, count, cutoff in row["grid_counts"]}


def _multigrid_notes(rows: list[dict]) -> list[str]:
    """What the MULTIGRID INFO block says about the *settings* behind it.

    ``count for grid N`` also carries that level's plane-wave cutoff, and those
    numbers follow ``E_i = E_1 / alpha^(i-1)`` with CP2K's ``PROGRESSION_FACTOR``
    (3.0 by default).  Checking the ratios costs nothing and catches the two
    things that silently invalidate a comparison: a non-default progression
    factor, and outputs from different setups mixed into one directory.
    """
    notes: list[str] = []
    with_grid = [row for row in rows if len(row["grid_counts"]) >= 2]
    if not with_grid:
        if rows and not any(row["grid_counts"] for row in rows):
            notes.append(
                "输出里没有 MULTIGRID INFO（`count for grid`）——CP2K 只在 "
                "PRINT_LEVEL ≥ MEDIUM 时打印它，而 REL_CUTOFF 合不合适主要靠它判断"
                "（官方教程的模板就是 MEDIUM）。能量表仍然有效，但网格分配看不到。")
        return notes

    ratios: list[float] = []
    for row in with_grid:
        cutoffs = [cutoff for _, (_, cutoff) in sorted(_levels_of(row).items())]
        for low, high in zip(cutoffs, cutoffs[1:]):
            if high > 0:
                ratios.append(low / high)

    if ratios:
        alpha = sum(ratios) / len(ratios)
        spread = max(ratios) - min(ratios)
        if spread > 0.02 * alpha:
            notes.append(
                f"各档网格的截断比例不一致（算出来 α 在 {min(ratios):.3f}–"
                f"{max(ratios):.3f} 之间）：同一个目录里可能混了设置不同的输出，"
                "或者有人改过 PROGRESSION_FACTOR。结论只能按趋势看。")
        elif abs(alpha - 3.0) > 0.02:
            notes.append(
                f"网格档之间的比例 α ≈ {alpha:.3f}，不是 CP2K 默认的 3.0："
                "说明输入里改过 `&MGRID PROGRESSION_FACTOR`。CUTOFF/REL_CUTOFF "
                "的结论本身仍然成立，但和教程里的数值不能逐档对应。")

    wanted = next((row["grid_levels"] for row in rows if row["grid_levels"]), None)
    got = max(len(row["grid_counts"]) for row in with_grid)
    if wanted and got and wanted != got:
        notes.append(
            f"输出说 `Number of grid levels: {wanted}`，但 MULTIGRID INFO 里只数出 "
            f"{got} 档：这一档网格一个函数都没有，通常说明 REL_CUTOFF 相对 CUTOFF "
            "太小。")
    return notes


def _finest_share(level_counts: dict[int, tuple[int, float]]) -> tuple[int, int]:
    """``(points on the finest level, total points)``."""
    total = sum(count for count, _ in level_counts.values())
    return level_counts.get(1, (0, 0.0))[0], total


def _cutoff_distribution_notes(rows: list[dict], suggested_cutoff: float | None,
                               swept: str) -> list[str]:
    """The tutorial's second half of the CUTOFF decision.

    "it is the lowest cutoff energy where the finest grid level is used, but at
    the same time with the majority of the Gaussians on the coarser grids" - an
    energy that stops moving is necessary but not sufficient, and both failure
    modes are visible in the point distribution.
    """
    notes: list[str] = []
    if swept not in ("cutoff", "both") or suggested_cutoff is None:
        return notes

    candidates = [row for row in rows
                  if row["cutoff"] == suggested_cutoff and row["grid_counts"]]
    if not candidates:
        return notes
    row = max(candidates, key=lambda item: item["rel_cutoff"] or 0.0)
    finest, total = _finest_share(_levels_of(row))
    rel = row["rel_cutoff"]
    where = f"CUTOFF {suggested_cutoff:g} Ry" + (f" / REL_CUTOFF {rel:g} Ry"
                                                 if rel is not None else "")
    if total and finest == 0:
        notes.append(
            f"{where} 时最细一档网格上**一个函数都没有**（{total} 个全在粗档上）："
            "按官方教程，cutoff 应当取到「最细那一档仍然被用到」。这通常说明 "
            "REL_CUTOFF 相对 CUTOFF 太小了——把 REL_CUTOFF 抬高一档再扫一遍 CUTOFF，"
            "否则能量看着收敛，其实是因为函数被推到粗网格上去了。")
    elif total and finest * 2 > total:
        notes.append(
            f"{where} 时大多数函数落在**最细**一档网格上（{finest}/{total}）："
            "官方教程推荐的分布是「最细档被用到，但大多数函数在粗档上」。"
            "说明 REL_CUTOFF 相对 CUTOFF 偏大，多花的机时买不到多少精度——"
            "可以把 REL_CUTOFF 降一档看看能量是否仍然收敛。")

    # the tutorial's other warning: raising CUTOFF alone pushes Gaussians to
    # coarser levels, so the energy can stall for the wrong reason
    spread = _grid1_spread(rows, "cutoff")
    if spread is not None:
        c_low, n_low, c_high, n_high = spread
        if n_low > 0 and n_high == 0:
            notes.append(
                f"最细一档网格上的函数个数从 CUTOFF {c_low:g} Ry 的 {n_low} 个掉到 "
                f"{c_high:g} Ry 的 0 个：REL_CUTOFF 固定不变时，CUTOFF 越大，函数越"
                "容易被推到粗档上（这正是教程提醒的「只加 CUTOFF 不加 REL_CUTOFF，"
                "能量收敛会变慢」）。建议在定下的 CUTOFF 上重扫 REL_CUTOFF。")
    return notes


def families(results: list[RunResult],
             threshold_mev_per_atom: float = 1.0) -> list[Summary]:
    """Split outputs into the one-parameter sweeps they actually form.

    A folder often holds several sweeps side by side - ``cutoff_*`` and
    ``rel_cutoff_*`` directories next to each other, as the usual shell scripts
    produce.  Judging them in a single table would be wrong (each sweep needs its
    own reference point), so they are separated here.  A genuine two-dimensional
    grid is not split: there the cutoff has to be judged inside each rel_cutoff
    group, which is what a single :class:`Summary` does.
    """
    full = summarise(results, threshold_mev_per_atom)
    if not full.rows:
        return [full]

    def mode(values: list[float | None]) -> float | None:
        counts: dict[float | None, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return max(counts.items(), key=lambda item: (item[1], -(item[0] or 0)))[0]

    cutoffs = [row["cutoff"] for row in full.rows if row["cutoff"] is not None]
    rels = [row["rel_cutoff"] for row in full.rows if row["rel_cutoff"] is not None]
    if not cutoffs or not rels:
        return [full]

    common_cutoff = mode(cutoffs)
    common_rel = mode(rels)
    cutoff_rows = [row for row in full.rows if row["rel_cutoff"] == common_rel]
    rel_rows = [row for row in full.rows if row["cutoff"] == common_cutoff]

    enough_cutoff = len({row["cutoff"] for row in cutoff_rows}) > 1
    enough_rel = len({row["rel_cutoff"] for row in rel_rows}) > 1
    covered = {id(row) for row in cutoff_rows} | {id(row) for row in rel_rows}
    if not (enough_cutoff or enough_rel) or len(covered) < len(full.rows):
        return [full]

    summaries: list[Summary] = []
    if enough_cutoff:
        summaries.append(_build_summary(
            [dict(row) for row in cutoff_rows], threshold_mev_per_atom, swept="cutoff",
            notes=[f"REL_CUTOFF 固定 {common_rel:g} Ry。"
                   if common_rel is not None else ""]))
    if enough_rel:
        summaries.append(_build_summary(
            [dict(row) for row in rel_rows], threshold_mev_per_atom, swept="rel_cutoff",
            notes=[f"CUTOFF 固定在 {common_cutoff:g} Ry。" if common_cutoff is not None else ""]))
    for summary in summaries:
        summary.notes = list(full.warnings) + [n for n in summary.notes if n]
    return summaries


# --------------------------------------------------------------------------- #
# the two-step recipe
# --------------------------------------------------------------------------- #
#: Default second-step values, used when the tool has to suggest a sweep.  These
#: are the ranges of CP2K's own tutorial (``cutoff`` 50-500 Ry in steps of 50,
#: ``rel_cutoff`` 10-100 Ry in steps of 10); both are cheap because a grid scan
#: uses the one-SCF-step protocol.
DEFAULT_REL_VALUES = (10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 80.0, 100.0)
DEFAULT_CUTOFF_VALUES = (50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 400.0, 500.0)


def _grid1_spread(rows: list[dict], key: str) -> tuple[float, int, float, int] | None:
    """How many points sit on the finest grid level at the two ends of a sweep.

    Used as evidence that REL_CUTOFF and CUTOFF are coupled: the same REL_CUTOFF
    puts the Gaussians on a different grid level when CUTOFF changes.
    """
    with_grid = [row for row in rows if row["grid_counts"] and row[key] is not None]
    if len(with_grid) < 2:
        return None

    def finest_level(row: dict) -> int:
        return sum(count for level, count, _ in row["grid_counts"] if level == 1)

    low = min(with_grid, key=lambda row: row[key])
    high = max(with_grid, key=lambda row: row[key])
    if low[key] == high[key]:
        return None
    return float(low[key]), finest_level(low), float(high[key]), finest_level(high)


def _find_source(root: Path, prefer: float | None = None) -> tuple[str, Path] | None:
    """A generated input that the next sweep can start from.

    The inputs ``scan`` writes are themselves usable templates (they carry
    ``@SET CUTOFF 450`` style lines), so the follow-up sweep can reuse the one
    closest to the value that was just chosen.
    """
    candidates = sorted(root.rglob("*.inp"))
    if not candidates:
        return None

    def prefers(path: Path) -> int:
        return 0 if (prefer is not None and f"_{prefer:g}" in str(path)) else 1

    usable: list[tuple[str, Path]] = []
    for path in candidates:
        try:
            text = path.read_bytes().decode("utf-8-sig", errors="ignore")
        except OSError:
            continue
        usable.append(("template" if ("@SET" in text or "${" in text) else "from",
                       path))
    if not usable:
        return None
    # a template beats a parsed input; within that, the closest value wins
    usable.sort(key=lambda item: (item[0] != "template", prefers(item[1]), str(item[1])))
    return usable[0]


def _sweep_values(suggested: float | None, defaults) -> str:
    if suggested is None:
        return ",".join(f"{value:g}" for value in defaults)
    return ",".join(f"{value:g}" for value in defaults)


def workflow_section(summaries: list[Summary], roots: list[str] | None = None) -> list[str]:
    """The order of the two steps, whether they agree, and what to do next.

    CUTOFF and REL_CUTOFF are **coupled**: the multigrid levels scale with
    CUTOFF while REL_CUTOFF is absolute, so the same REL_CUTOFF lands on a
    different grid level when CUTOFF changes.  That is why the usual recipe is
    sequential - fix a sane REL_CUTOFF, converge CUTOFF, then converge REL_CUTOFF
    *at that CUTOFF* - and why a REL_CUTOFF answer measured at some other CUTOFF
    cannot simply be carried over.
    """
    cutoff_sum = next((s for s in summaries if s.swept == "cutoff"), None)
    rel_sum = next((s for s in summaries if s.swept == "rel_cutoff"), None)
    both_sum = next((s for s in summaries if s.swept == "both"), None)

    lines = ["== 收敛流程（CUTOFF 与 REL_CUTOFF 是分两步定的）=="]
    lines.append("两级网格是耦合的：多级网格的每一档跟着 CUTOFF 缩放，而 REL_CUTOFF 是"
                 "绝对值，")
    lines.append("所以同一个 REL_CUTOFF 在不同 CUTOFF 下会落到不同的网格档上。"
                 "因此顺序是：")
    lines.append("  第 1 步 固定一个合理的 REL_CUTOFF（通常 60 Ry）扫 CUTOFF；")
    lines.append("  第 2 步 固定在**第 1 步定下来的 CUTOFF** 上扫 REL_CUTOFF。")

    if both_sum is not None:
        lines.append("")
        lines.append("你做的是一次二维扫描，两个值是一次同时定下来的——"
                     "这种情况直接看上面的结论就够了。")
        return lines

    root = Path(roots[0]) if roots else None

    def command(keyword: str, suggested: float | None, values: str,
                prefer: float | None = None, pin: str = "") -> str:
        flag = "--rel-cutoffs" if keyword == "REL_CUTOFF" else "--cutoffs"
        out = f"{root}-{keyword.lower()}" if root else "scan"
        source = _find_source(root, prefer) if root and root.is_dir() else None
        if source is None:
            return (f"cp2kbuild scan <用你原来的输入> {flag} {values} "
                    f"{pin}--outdir {out}")
        how, path = source
        origin = (f'--template "{path}"' if how == "template"
                  else f'--from "{path}"')
        return f"cp2kbuild scan {origin} {flag} {values} {pin}--outdir {out}"

    if cutoff_sum is None and rel_sum is None:
        return lines

    if cutoff_sum is None:
        lines.append("")
        lines.append("这份数据里只有 REL_CUTOFF 扫描，没有第 1 步。"
                     "按耦合关系，REL_CUTOFF 的结论需要先有 CUTOFF 才能定。")
        lines.append("  建议先做第 1 步（官方教程的起点：REL_CUTOFF 先固定在 60 Ry）："
                     + command("CUTOFF", None,
                               ",".join(f"{v:g}" for v in DEFAULT_CUTOFF_VALUES),
                               pin="--rel-cutoffs 60 "))
        return lines

    fixed_rel = next((row["rel_cutoff"] for row in cutoff_sum.rows
                      if row["rel_cutoff"] is not None), None)
    best_cutoff = cutoff_sum.suggested_cutoff
    lines.append("")
    step1 = (f"第 1 步（REL_CUTOFF 固定 {fixed_rel:g} Ry）→ CUTOFF "
             if fixed_rel is not None else "第 1 步 → CUTOFF ")
    if best_cutoff is not None:
        lines.append(step1 + f"{best_cutoff:g} Ry 起满足判据。")
    else:
        lines.append(step1 + "还没有定论（表格里各档还在漂），先把它的上限提高再说。")

    if rel_sum is None:
        lines.append("")
        lines.append("第 2 步（扫 REL_CUTOFF）还没做。")
        if best_cutoff is not None:
            lines.append("  在刚定下的 CUTOFF 上做：")
            lines.append("    " + command("REL_CUTOFF", best_cutoff,
                                         ",".join(f"{v:g}" for v in DEFAULT_REL_VALUES),
                                         prefer=best_cutoff))
        return lines

    fixed_cutoff = next((row["cutoff"] for row in rel_sum.rows
                         if row["cutoff"] is not None), None)
    best_rel = rel_sum.suggested_rel_cutoff
    step2 = (f"第 2 步（CUTOFF 固定 {fixed_cutoff:g} Ry）→ REL_CUTOFF "
             if fixed_cutoff is not None else "第 2 步 → REL_CUTOFF ")
    if best_rel is not None:
        lines.append(step2 + f"{best_rel:g} Ry 起满足判据。")
    else:
        lines.append(step2 + "还没到收敛判据，再往上加一档。")

    # the point of the sequential recipe: step 2 must be done at the CUTOFF that
    # step 1 chose, otherwise its answer does not transfer
    if best_cutoff is not None and fixed_cutoff is not None \
            and abs(fixed_cutoff - best_cutoff) > 1e-9:
        lines.append("")
        lines.append(f"**两步不自洽**：第 1 步说 CUTOFF {best_cutoff:g} Ry 就够，"
                     f"但你的第 2 步是在 CUTOFF {fixed_cutoff:g} Ry 上做的。")
        evidence = _grid1_spread(rel_sum.rows, "cutoff")
        if evidence is None:
            evidence = _grid1_spread(cutoff_sum.rows, "cutoff")
        if evidence is not None:
            c_low, n_low, c_high, n_high = evidence
            lines.append(f"  证据就在你自己的数据里：REL_CUTOFF 相同时，最细一档网格上的"
                         f"点数随 CUTOFF 剧烈变化——")
            lines.append(f"  CUTOFF {c_low:g} Ry 时是 {n_low} 个点，"
                         f"CUTOFF {c_high:g} Ry 时是 {n_high} 个点。")
            lines.append("  网格分配都不一样了，REL_CUTOFF 的误差自然也不同，"
                         f"所以 {best_rel:g} Ry 这个结论不能直接搬到 "
                         f"CUTOFF {best_cutoff:g} Ry 上用。")
        lines.append("  建议在第 1 步定下的 CUTOFF 上重做一遍第 2 步：")
        lines.append("    " + command("REL_CUTOFF", best_rel,
                                     ",".join(f"{v:g}" for v in DEFAULT_REL_VALUES),
                                     prefer=best_cutoff))
        lines.append("  （换过基组、赝势或体系组成的话，第 1 步也要重做——"
                     "收敛的是网格表示，不是基组本身。）")
    elif best_cutoff is not None and fixed_cutoff is not None:
        lines.append("")
        lines.append(f"两步自洽：第 2 步就是在第 1 步定下的 CUTOFF {fixed_cutoff:g} Ry "
                     "上做的，可以直接用。")
        lines.append("  想更保险，可以在定下的 REL_CUTOFF 上再扫一遍 CUTOFF "
                     "确认一次（两个参数是耦合的）。")
    return lines



def render(summary: Summary, n_atoms_hint: int | None = None) -> str:
    """Human readable convergence table."""
    if not summary.rows:
        return ("没有找到可用的能量。\n"
                "检查一下：输出文件里是否有 'ENERGY| Total FORCE_EVAL' 那一行？"
                "目录里是否有对应的 .out 文件？")

    lines: list[str] = []
    atoms = n_atoms_hint or next((r["n_atoms"] for r in summary.rows if r["n_atoms"]), None)
    if summary.swept == "rel_cutoff":
        title = "REL_CUTOFF 收敛（CUTOFF 固定）"
    elif summary.swept == "cutoff":
        title = "CUTOFF 收敛（REL_CUTOFF 固定）"
    else:
        title = "二维扫描"
    lines.append(f"== {title} ==")
    lines.append(f"参考点：网格最细的那一次（最后一行）；判据 {summary.threshold:g} meV/atom")
    if atoms:
        lines.append(f"每个构型 {atoms} 个原子")
    lines.append("")

    header = (f"{'CUTOFF':>8} {'REL_CUT':>8} {'E (a.u.)':>22} "
              f"{'ΔE (meV)':>12} {'ΔE/atom (meV)':>15} {'ΔE (Ry)':>11}  状态")
    lines.append(header)
    lines.append("-" * len(header))
    for row in summary.rows:
        cutoff = f"{row['cutoff']:g}" if row["cutoff"] is not None else "?"
        rel = f"{row['rel_cutoff']:g}" if row["rel_cutoff"] is not None else "?"
        status = "完成" if row["finished"] else "未完成"
        if row["scf_converged"] is False:
            status += "（SCF 未收敛）"
        if row["problems"]:
            status += "（" + "；".join(row["problems"]) + "）"
        if row["path"] == summary.reference_path:
            status += " ← 参考"
        lines.append(f"{cutoff:>8} {rel:>8} {row['energy_hartree']:>22.12f} "
                     f"{row['delta_mev']:>12.3f} {row['delta_mev_per_atom']:>15.4f} "
                     f"{row.get('delta_ry', 0.0):>11.2e}  {status}")

    with_grid = [row for row in summary.rows if row["grid_counts"]]
    if with_grid:
        levels = max(len(row["grid_counts"]) for row in with_grid)
        lines.append("")
        lines.append("每档网格上的点数（NG on grid N）——最细的一档是 grid 1：")
        grid_header = (f"{'CUTOFF':>8} {'REL_CUT':>8} "
                       + " ".join(f"{'grid ' + str(n):>10}" for n in range(1, levels + 1)))
        lines.append(grid_header)
        lines.append("-" * len(grid_header))
        for row in with_grid:
            cutoff = f"{row['cutoff']:g}" if row["cutoff"] is not None else "?"
            rel = f"{row['rel_cutoff']:g}" if row["rel_cutoff"] is not None else "?"
            counts = {level: count for level, count, _ in row["grid_counts"]}
            cells = " ".join(f"{counts.get(n, 0):>10}" for n in range(1, levels + 1))
            lines.append(f"{cutoff:>8} {rel:>8} {cells}")
        lines.append("")
        lines.append("同一段 MULTIGRID INFO 里的每档截断能（[Ry]，右边括号是 [Ha]）："
                     "函数落在哪一档，就由 REL_CUTOFF 与这些值比较决定。")
        cut_header = (f"{'CUTOFF':>8} {'REL_CUT':>8} "
                      + " ".join(f"{'grid ' + str(n):>16}" for n in range(1, levels + 1))
                      + f"{'α':>7}")
        lines.append(cut_header)
        lines.append("-" * len(cut_header))
        for row in with_grid:
            cutoff = f"{row['cutoff']:g}" if row["cutoff"] is not None else "?"
            rel = f"{row['rel_cutoff']:g}" if row["rel_cutoff"] is not None else "?"
            by_level = {level: cut for level, _, cut in row["grid_counts"]}
            cells = ""
            for n in range(1, levels + 1):
                cut = by_level.get(n)
                cells += (f"{cut * HARTREE_TO_RY:>10.2f}({cut:>4.1f})"
                          if cut is not None else f"{'?':>16}")
            ratios = [by_level[n] / by_level[n + 1] for n in range(1, levels)
                      if by_level.get(n) and by_level.get(n + 1)]
            alpha = f"{sum(ratios) / len(ratios):.2f}" if ratios else "?"
            lines.append(f"{cutoff:>8} {rel:>8} {cells}{alpha:>7}")

    lines.append("")
    if summary.swept in ("cutoff", "both") and summary.suggested_cutoff is not None:
        lines.append(f"→ CUTOFF：从 {summary.suggested_cutoff:g} Ry 起，"
                     f"再往上加的变化已经小于 {summary.threshold:g} meV/atom。")
        here = next((row for row in summary.rows
                     if row["cutoff"] == summary.suggested_cutoff), None)
        if here is not None:
            lines.append(
                f"   这一档相对最细一次的总能量差 {here.get('delta_hartree', 0.0):.3e} Ha"
                f"（{here.get('delta_ry', 0.0):.3e} Ry）——"
                "官方教程就是用这个绝对差值表述判据的（例如 1e-6 Ry）。")
        lines.append("   实际用的时候建议留一档余量（比如再用高一档），"
                     "尤其是后面要算力、应力或做变胞优化时。")
    else:
        distinct_cutoffs = sorted({row["cutoff"] for row in summary.rows
                                   if row["cutoff"] is not None})
        if summary.swept == "rel_cutoff":
            pass                      # CUTOFF is fixed on purpose in this sweep
        elif len(distinct_cutoffs) < 2:
            only = f"{distinct_cutoffs[0]:g} Ry" if distinct_cutoffs else "?"
            lines.append(f"→ CUTOFF：只扫了一档（{only}），无从判断收敛——"
                         "收敛说的是“再加一档还变不变”，至少要有两个不同的取值。")
        else:
            lines.append(f"→ CUTOFF：从最粗到最细，每一档的 ΔE/atom 都还超过 "
                         f"{summary.threshold:g} meV/atom，说明还在收敛中，"
                         "请把 CUTOFF 的上限提高再试。")
            if len({row["rel_cutoff"] for row in summary.rows}) > 1:
                lines.append("   （二维扫描是按每个 REL_CUTOFF 分组各自判定、"
                             "取最保守结果：只要有一档 REL_CUTOFF 自己还在漂，"
                             "这里就给不出 CUTOFF 的值。）")
    if summary.swept in ("rel_cutoff", "both") and summary.suggested_rel_cutoff is not None:
        lines.append(f"→ REL_CUTOFF：从 {summary.suggested_rel_cutoff:g} Ry 起已收敛。")
    elif summary.swept in ("rel_cutoff", "both") and len(
            {row["rel_cutoff"] for row in summary.rows}) > 1:
        lines.append("→ REL_CUTOFF：还没到收敛判据，建议再往上加一档看看。")

    lines.append("")
    lines.append("判据怎么读：meV/atom 不随体系大小变，换体系还能沿用；官方教程"
                 "（How to Converge the CUTOFF and REL_CUTOFF）用的是**总能量的绝对误差**"
                 "（例如 1e-6 Ry），所以上表也把 ΔE 的 Ry/a.u. 差值列出来了，"
                 "要对齐教程就用那一列。")
    lines.append("")
    lines.append("提醒：CUTOFF/REL_CUTOFF 收敛的是**网格表示**，不是基组本身。"
                 "换基组、换赝势或改体系组成后都要重做；"
                 "能量收敛的 cutoff 用在分子动力学/晶胞优化上往往还不够，"
                 "那类计算建议再多留一档。")

    for note in summary.notes:
        lines.append("")
        lines.append("· " + note)
    return "\n".join(lines)


def render_ssv(summary: Summary) -> str:
    """The ``.ssv`` table, in the layout the usual cut-off scripts write.

    Columns: the swept parameter, the total energy, then the number of grid
    points per level - that last part is what tells you whether REL_CUTOFF is
    large enough.
    """
    from . import __version__

    lines = ["# Grid cutoff vs total energy",
             "# Generated by cp2kbuild %s" % __version__]
    fixed = {}
    for row in summary.rows:
        for key, label in (("cutoff", "CUTOFF"), ("rel_cutoff", "REL_CUTOFF")):
            values = {r[key] for r in summary.rows if r[key] is not None}
            if len(values) == 1:
                fixed[label] = values.pop()
    if summary.swept == "rel_cutoff":
        x_key, x_label = "rel_cutoff", "Rel_Cutoff (Ry)"
    else:
        x_key, x_label = "cutoff", "Cutoff (Ry)"
    for label, value in sorted(fixed.items()):
        lines.append(f"# {label} = {value:g}")
    if summary.swept == "both":
        lines.append("# CUTOFF | REL_CUTOFF | Total Energy (Ha) | ...")
    else:
        lines.append(f"# {x_label} | Total Energy (Ha)", )

    levels = 0
    for row in summary.rows:
        levels = max(levels, len(row["grid_counts"]))
    if levels:
        lines[-1] += "".join(f" | NG on grid {n}" for n in range(1, levels + 1))

    for row in summary.rows:
        x = row[x_key]
        cells = f"{x if x is not None else float('nan'):10.2f}  " \
                f"{row['energy_hartree']:15.10f}"
        if summary.swept == "both":
            cells = (f"{row['cutoff'] or float('nan'):10.2f}  "
                     f"{row['rel_cutoff'] or float('nan'):10.2f}  "
                     f"{row['energy_hartree']:15.10f}")
        counts = {level: count for level, count, _ in row["grid_counts"]}
        for n in range(1, levels + 1):
            cells += f"  {counts.get(n, 0):6d}"
        lines.append(cells)
    return "\n".join(lines) + "\n"
