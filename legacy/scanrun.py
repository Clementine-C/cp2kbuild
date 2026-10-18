# ARCHIVED: this module is no longer part of cp2kbuild.
# It implemented the old scan/converge machinery (generating
# arbitrary sweeps and reading CP2K outputs back).  The tool now only
# writes the official CUTOFF/REL_CUTOFF script set - see
# cp2kbuild/cutoffkit.py - and the analysis happens on the cluster.
# Kept for reference; it is not imported, tested or bundled.
"""The scan core, shared by the command line and the GUI.

What a scan produces:

* one input per parameter point - either a directory per point (``cutoff_450Ry/``
  with the input and its ``@INCLUDE`` files) or one flat ``.inp`` per point;
* ``<label>_run.sh`` / ``.ps1`` to run them (serial by default, MPI slots if you
  ask for them);
* ``<label>_analyse.sh`` to read the results on the cluster - plain ``sh`` +
  ``awk``, no Python needed;
* a ``README.md`` describing the layout and how to read the result.

Both front ends share this module: the CLI prints the lines it returns and the
GUI shows the very same strings, so the two cannot drift apart.
"""

from __future__ import annotations

import itertools
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from . import scanscripts
from . import template as template_module
from .datafiles import DataIndex
from .model import Section, render_document
from .parser import read_input
from .paths import read_template_text, write_template_text, write_text_lf
from .recipes import BuildResult, Spec, build

#: the two parameters this tool knows how to sweep as a pair
MG = "FORCE_EVAL/DFT/MGRID/"

#: CP2K's own tutorial for these two keywords
#: (``https://manual.cp2k.org/trunk/methods/dft/cutoff.html``) does not converge
#: the SCF at all: it runs ``RUN_TYPE ENERGY`` with a single SCF step and
#: ``IGNORE_CONVERGENCE_FAILURE``, because *the difference between two grid
#: settings* is what the study is about - self-consistency is not.  One grid
#: point then costs one SCF step instead of ten to twenty, which is what makes a
#: ten-point sweep affordable.  ``PRINT_LEVEL MEDIUM`` is part of the protocol
#: too: without it CP2K does not print the MULTIGRID INFO block, and that block
#: (how many Gaussians land on which grid level) is the evidence the REL_CUTOFF
#: answer rests on.
GRID_PROTOCOL_NOTE = (
    "官方网格扫描协议（省机时）：MAX_SCF 1 + OUTER_SCF MAX_SCF 0 + "
    "IGNORE_CONVERGENCE_FAILURE TRUE"
    "（不做 SCF 收敛——要的是两个网格设置之间的差）、SCF_GUESS ATOMIC、"
    "RUN_TYPE ENERGY（不算力）、PRINT_LEVEL MEDIUM（否则没有 MULTIGRID INFO）")

#: How to get the same effect by hand, for the modes that keep your own input.
GRID_PROTOCOL_HAND_LINES = ("SCF_GUESS ATOMIC", "MAX_SCF 1",
                            "IGNORE_CONVERGENCE_FAILURE TRUE")

_PRINT_LEVELS = ("SILENT", "LOW", "MEDIUM", "HIGH", "DEBUG")


def protocol_applies(variations: list[tuple[str, list]],
                     property_name: str = "energy") -> bool:
    """Whether the tutorial's cheap protocol is for this sweep.

    It is a *grid* recipe (CUTOFF/REL_CUTOFF) and it needs an energy-only run:
    with ``MAX_SCF 1`` there is no converged density, so forces and geometries
    would be meaningless.
    """
    grid_sweep = any(keyword_of(path).upper() in ("CUTOFF", "REL_CUTOFF")
                     for path, _ in variations)
    return bool(grid_sweep and (property_name or "energy") == "energy")


def apply_grid_protocol(spec: Spec) -> None:
    """Put the tutorial's cheap grid-scan settings into ``spec``.

    Only the *grid* study wants this: with ``MAX_SCF 1`` the wave function is not
    converged, so forces, stresses and absolute energies are meaningless - but
    the energy *difference* between two cutoffs is exactly what the tutorial
    measures, at a fraction of the cost.
    """
    spec.task = "energy"                     # RUN_TYPE ENERGY: no forces either
    spec.scf_guess = "ATOMIC"                # the tutorial's template
    spec.max_scf = 1                         # "so that no self-consistent loops
    spec.ignore_convergence_failure = True   #  will be performed"
    # ... and no *outer* loop either: the tool writes &OUTER_SCF MAX_SCF 20 by
    # default, which would turn "one SCF step" into twenty (and print "outer SCF
    # loop FAILED to converge" for every point)
    spec.outer_max_scf = 0
    spec.print_forces = False
    # a throwaway grid scan does not need a wave function dump per point (the
    # tutorial's template has no such print key either)
    spec.wfn_restart_print = False
    rank = _PRINT_LEVELS.index(spec.print_level.upper()) \
        if spec.print_level.upper() in _PRINT_LEVELS else len(_PRINT_LEVELS)
    if rank < _PRINT_LEVELS.index("MEDIUM"):
        spec.print_level = "MEDIUM"


def add_total_numbers(sections: list[Section]) -> bool:
    """``&FORCE_EVAL/&PRINT/&TOTAL_NUMBERS ON`` - part of the official template.

    It prints the electron and atom count next to the energies, which is the
    cheapest sanity check that every point of a sweep really used the same
    system (``read_output`` picks the atom count up from the kind lines anyway).
    """
    force_eval = next((s for s in sections
                       if s.name.upper() == "FORCE_EVAL"), None)
    if force_eval is None:
        return False
    printing = force_eval.subsection("PRINT")
    if printing is None:
        printing = force_eval.add(Section("PRINT"))
    if printing.subsection("TOTAL_NUMBERS") is not None:
        return False
    printing.add(Section("TOTAL_NUMBERS", "ON", comment=(
        "electron/atom count per point - part of CP2K's own CUTOFF tutorial "
        "template and a cheap check that the sweep really is one system")))
    return True


@dataclass
class ScanOutcome:
    """What a scan produced, plus the lines the CLI prints and the GUI shows."""

    outdir: Path
    written: list[Path] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    mode: str = "structure"
    scripts: list[Path] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #
def parse_values(text: str) -> list:
    """``"300,400 500" -> [300, 400, 500]``; non-numbers stay strings."""
    values: list = []
    for token in re.split(r"[,\s]+", (text or "").strip()):
        if not token:
            continue
        try:
            values.append(int(token))
        except ValueError:
            try:
                values.append(float(token))
            except ValueError:
                values.append(token)
    return values


def plan_variations(cutoffs: str | None, rel_cutoffs: str | None,
                    vary: list[str] | None = None) -> list[tuple[str, list]]:
    """Turn user input into ``[(keyword path, [values]), ...]``."""
    variations: list[tuple[str, list]] = []
    for text, path in ((cutoffs, MG + "CUTOFF"), (rel_cutoffs, MG + "REL_CUTOFF")):
        if text:
            values = parse_values(text)
            if values:
                variations.append((path, values))
    for entry in vary or []:
        if "=" not in entry:
            raise ValueError(f"--vary expects PATH=V1,V2, got {entry!r}")
        path, values = entry.split("=", 1)
        parsed = parse_values(values)
        if not parsed:
            raise ValueError(f"--vary {path} 没有给出任何取值")
        variations.append((path.strip("/"), parsed))
    return variations


def keyword_of(path: str) -> str:
    return path.split("/")[-1]


def label_of(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", keyword_of(path)).lower()


def slug(value) -> str:
    text = f"{value:g}" if isinstance(value, float) else str(value)
    return re.sub(r"[^A-Za-z0-9.+-]", "_", text)


def sweepable(variations: list[tuple[str, list]]) -> list[tuple[str, list]]:
    """The variations that actually sweep something (more than one value)."""
    return [(path, values) for path, values in variations if len(values) > 1]


def single_value(variations: list[tuple[str, list]], keyword: str):
    """The value pinned by a single-valued ``--cutoffs``/``--rel-cutoffs``."""
    for path, values in variations:
        if keyword_of(path).upper() == keyword.upper() and len(values) == 1:
            return values[0]
    return None


def _numbers(variations: list[tuple[str, list]], keyword: str) -> list[float]:
    values = [float(value) for path, items in variations
              if keyword_of(path).upper() == keyword.upper()
              for value in items if isinstance(value, (int, float))]
    return sorted(set(values))


# --------------------------------------------------------------------------- #
# scripts
# --------------------------------------------------------------------------- #
def write_sweep_scripts(outdir: Path, entries: list[str], layout: str,
                        input_name: str | None, keyword: str | None,
                        fixed_value, *, run_script: bool = True,
                        analyse: bool = True) -> list[Path]:
    """Write the runner (and, for a one-parameter sweep, the analysis script).

    Named after the swept keyword - ``cutoff_run.sh``, ``rel_cutoff_analyse.sh`` -
    which is what the hand-written scripts around CP2K use, and what makes a
    directory self-describing when both sweeps sit side by side.
    """
    written: list[Path] = []
    label = keyword.lower() if keyword else None
    stem = f"{label}_" if label else ""

    if run_script:
        title = (f"跑完 {keyword.upper()} 扫描（{len(entries)} 个参数点）"
                 if keyword else f"跑完所有参数点（{len(entries)} 个）")
        name = f"{stem}run.sh"
        script_layout = layout
        write_text_lf(outdir / name, scanscripts.run_script(
            entries, script_layout,
            input_name or "system.inp", title, name))
        written.append(outdir / name)
        name_ps1 = f"{stem}run.ps1"
        write_text_lf(outdir / name_ps1, scanscripts.run_script_ps1(
            entries, script_layout, input_name or "system.inp", title, name_ps1))
        written.append(outdir / name_ps1)

    if analyse and keyword:
        name = f"{stem}analyse.sh"
        title = f"汇总 {keyword.upper()} 扫描：读每个点的 .out，写成 .ssv"
        write_text_lf(outdir / name, scanscripts.analyse_script(
            keyword, fixed_value, 1.0, title, name))
        written.append(outdir / name)

    return written


def _mgrid_values(path: Path) -> tuple[float | None, float | None]:
    """CUTOFF and REL_CUTOFF actually written into a generated input.

    The unit is optional: the templates write both ``REL_CUTOFF 60.0`` and
    ``REL_CUTOFF [Ry] 60.0``.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, None

    def find(keyword: str):
        match = re.search(
            rf"^\s*{keyword}(?:\s+\[[^\]]+\])?\s+([0-9.]+)\s*$", text, re.M)
        return float(match.group(1)) if match else None

    return find("CUTOFF"), find("REL_CUTOFF")


def _template_value(text: str, keyword: str) -> float | None:
    """The value a template pins to ``keyword``, when it is a number already."""
    for pattern in (rf"^[ \t]*@SET[ \t]+{keyword}[ \t]+([0-9.eE+-]+)\s*$",
                    rf"^[ \t]*{keyword}[ \t]+([0-9.eE+-]+)"):
        match = re.search(pattern, text, re.M | re.I)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------- #
# mode 1: generate from a structure or a parsed input
# --------------------------------------------------------------------------- #
def run_scan(spec: Spec, index: DataIndex, variations: list[tuple[str, list]], *,
             outdir: str | Path, from_file: str | Path | None = None,
             run_script: bool = False, write_readme: bool = True,
             property_name: str | None = None,
             sweeps: str = "grid", grid_protocol: bool = True) -> ScanOutcome:
    """Write one input per parameter point, from a structure or an existing input.

    ``grid_protocol`` (structure mode only) is CP2K's own cheap recipe for a
    CUTOFF/REL_CUTOFF study: one SCF step per point, no forces, and a print level
    that keeps the MULTIGRID INFO block.  It is skipped for ``--property forces``
    / ``geometry`` (where forces are the point) and for the modes that keep your
    own input, which is said out loud instead.
    """
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    outcome = ScanOutcome(out, [], [], mode="input" if from_file else "structure")

    template = None
    if from_file:
        template = read_input(from_file)
        for warning in template.warnings:
            outcome.lines.append(f"WARN  parse [{from_file}]: {warning}")

    grid_sweep = any(keyword_of(path).upper() in ("CUTOFF", "REL_CUTOFF")
                     for path, _ in variations)
    protocol = (grid_protocol and template is None
                and protocol_applies(variations, property_name or "energy"))
    if protocol:
        apply_grid_protocol(spec)
        outcome.lines.append("note: " + GRID_PROTOCOL_NOTE)
    elif grid_protocol and grid_sweep:
        # the tutorial's trick only works for a plain energy run (with MAX_SCF 1
        # there is no converged density, so forces are meaningless), and the
        # template/--from modes exist precisely to leave your input alone
        reason = ("这一批比的是力/几何，不是能量：省机时协议不做 SCF 收敛，"
                  "算出来的力没有意义" if (property_name or "energy") != "energy"
                  else "用的是你自己的输入（--from/--template），模板要保持原样")
        outcome.lines.append(
            f"note: 没有加{GRID_PROTOCOL_NOTE.split('：')[0]}（{reason}）——"
            "每个点都会跑完整的 SCF。想快一个量级，在输入里加上："
            + "、".join(GRID_PROTOCOL_HAND_LINES))

    if sweeps == "separate":
        groups = _separate_groups(variations)
    else:
        groups = [("grid", variations)]

    for _, group in groups:
        entries: list[str] = []
        for combination in _combinations(group):
            if template is not None:
                sections = [section.copy() for section in template.sections]
                file_notes = [f"scan         : varied from {from_file}"]
                stem = Path(from_file).stem
            else:
                result: BuildResult = build(spec, index)
                sections = result.sections
                if protocol:
                    add_total_numbers(sections)
                file_notes = list(result.notes)
                stem = _project_of(result)

            if protocol:
                file_notes.append("scan         : " + GRID_PROTOCOL_NOTE)

            for (path, _), value in zip(group, combination):
                target = _locate(sections, path)
                if target is None:
                    raise ValueError(f"输入里找不到 {path} 对应的 section")
                parent, keyword = target
                parent.set(keyword, float(value) if isinstance(value, float) else value)

            file_notes.append("scan         : "
                              + ", ".join(f"{p}={v}" for (p, _), v in zip(group,
                                                                          combination)))
            text = render_document(
                sections, title=f"CP2K input generated by cp2kbuild {__version__}",
                notes=file_notes)

            if sweeps == "separate":
                name = _point_dir(group, combination, only_swept=True)
                (out / name).mkdir(parents=True, exist_ok=True)
                target = out / name / f"{stem}.inp"
                write_text_lf(target, text)
                entries.append(name)
            else:
                suffix = "-".join(f"{label_of(path)}_{slug(value)}"
                                  for (path, _), value in zip(group, combination))
                target = out / f"{stem}-{suffix}.inp"
                write_text_lf(target, text)
                entries.append(target.name)
            outcome.written.append(target)

        outcome.scripts += _scripts_for(out, group, entries,
                                        layout="dirs" if sweeps == "separate" else "flat",
                                        inputs=outcome.written[-len(entries):],
                                        run_script=run_script)

    if write_readme:
        _write_scan_readme(out, outcome.written, groups, spec, from_file,
                           run_script, property_name or spec.task, sweeps,
                           protocol=protocol)

    outcome.lines.append(f"wrote {len(outcome.written)} input file(s) to {out}")
    for path in outcome.written[:12]:
        relative = (path.parent.name if sweeps == "separate" else path.name)
        outcome.lines.append(f"  {relative}")
    if len(outcome.written) > 12:
        outcome.lines.append(f"  ... 还有 {len(outcome.written) - 12} 个")
    for path in outcome.scripts:
        outcome.lines.append(f"  {path.name}")
    return outcome


def _separate_groups(variations: list[tuple[str, list]],
                     pinned: dict | None = None) -> list[tuple[str, list]]:
    """One group per swept keyword, each carrying the other one as a fixed value."""
    pinned = {name.upper(): value for name, value in (pinned or {}).items()}
    sweeping = sweepable(variations)
    if not sweeping:
        raise ValueError(
            "分开生成（--sweeps separate）需要至少一个参数有两个以上取值。")
    groups: list[tuple[str, list]] = []
    for path, values in sweeping:
        keyword = keyword_of(path)
        if keyword.upper() in ("CUTOFF", "REL_CUTOFF"):
            other = "REL_CUTOFF" if keyword.upper() == "CUTOFF" else "CUTOFF"
            numbers = _numbers(variations, other)
            if len(numbers) > 1:
                raise ValueError(
                    f"{other} 有多个取值（{numbers}），没法在分开扫 "
                    f"{keyword.upper()} 时固定它。要么把 {other} 写成一个值，"
                    "要么用默认的二维扫描（grid）一次扫完。")
            # no value at all is fine here: the source (结构页的设置、已有输入、
            # 或模板里的字面值) already fixes it.  A template that leaves it as a
            # placeholder is caught by _separate_groups_template below.
        # the swept keyword plus everything that is pinned to a single value
        group = [(p, v) for p, v in variations
                 if keyword_of(p).upper() == keyword.upper() or len(v) == 1]
        groups.append((keyword, group))
    return groups


def _point_dir(group: list[tuple[str, list]], combination,
               only_swept: bool = False) -> str:
    """``cutoff_450Ry`` - the naming the hand-written scripts use.

    In separate mode only the swept keyword appears in the name, so the two
    sweeps sit side by side as ``cutoff_450Ry/`` and ``rel_cutoff_60Ry/``.
    """
    parts = []
    for (path, values), value in zip(group, combination):
        if only_swept and len(values) == 1:
            continue
        parts.append(template_module.directory_name(keyword_of(path), value))
    return "-".join(parts) or "point"


def _scripts_for(out: Path, group: list[tuple[str, list]], entries: list[str],
                 *, layout: str, inputs: list[Path], run_script: bool,
                 pinned: dict | None = None) -> list[Path]:
    """Scripts for one group: named per sweep when exactly one keyword varies."""
    sweeping = sweepable(group)
    if len(sweeping) != 1:
        return write_sweep_scripts(out, entries, layout, None, None, None,
                                   run_script=run_script, analyse=False)
    keyword = keyword_of(sweeping[0][0])
    fixed = None
    if keyword.upper() in ("CUTOFF", "REL_CUTOFF"):
        other = "REL_CUTOFF" if keyword.upper() == "CUTOFF" else "CUTOFF"
        fixed = single_value(group, other)
        if fixed is None and pinned:
            for name, value in pinned.items():
                if name.upper() == other:
                    try:
                        fixed = float(value)
                    except ValueError:
                        fixed = None
        if fixed is None and inputs:
            # what the generated input actually pins the other parameter to
            cutoff, rel = _mgrid_values(inputs[0])
            fixed = cutoff if other == "CUTOFF" else rel
            if fixed is None:
                try:
                    text = inputs[0].read_bytes().decode("utf-8-sig", errors="ignore")
                except OSError:
                    text = ""
                fixed = _template_value(text, other)
    return write_sweep_scripts(out, entries, layout, inputs[0].name if inputs else None,
                               keyword, fixed, run_script=run_script, analyse=True)


# --------------------------------------------------------------------------- #
# mode 2: sweep an existing input as text
# --------------------------------------------------------------------------- #
def run_template_scan(template_path: str | Path,
                      variations: list[tuple[str, list]], *,
                      outdir: str | Path, sets: list[str] | tuple = (),
                      layout: str = "dirs", run_script: bool = False,
                      sweeps: str = "grid") -> ScanOutcome:
    """Copy a template verbatim, replacing only the swept values.

    Mirrors the usual ``sed`` + ``cp`` shell loop, and keeps the file byte-exact
    otherwise - including its line endings, comments, tabs and ``@IF`` blocks.
    """
    template = Path(template_path)
    if not template.is_file():
        raise ValueError(f"找不到模板文件:{template}")
    original = read_template_text(template)

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    outcome = ScanOutcome(out, [], [], mode="template")
    sides = template_module.side_files(template)

    pinned: dict[str, str] = {}
    for entry in sets or ():
        if "=" not in entry:
            raise ValueError(f"固定参数要写成 NAME=VALUE：{entry!r}")
        name, value = entry.split("=", 1)
        pinned[name.strip()] = value.strip()

    if sweeps == "separate":
        layout = "dirs"
        groups = _separate_groups_template(variations, original, pinned)
    else:
        groups = [("grid", variations)]

    strategies: dict[str, str] = {}
    for _, group in groups:
        for path, _ in group:
            keyword = keyword_of(path)
            strategy = template_module.detect(original, keyword)
            if strategy is None:
                raise ValueError(
                    f"模板里找不到 {keyword} 的替换点。\n"
                    f"  这个模式只做三种替换（按优先级）：\n"
                    f"    1. @SET {keyword} <值>        <- 最常用，配合 ${{{keyword}}} 使用\n"
                    f"    2. {keyword} <值>             <- 直接写在 section 里\n"
                    f"    3. 占位符 LT_{keyword.lower()} / ${{{keyword}}} / "
                    f"@{keyword}@ / __{keyword}__\n"
                    f"  {template.name} 里一个都没有。要么在模板里加一个，"
                    "要么改用「已有输入（解析）」模式（会重排格式，但能按 section 路径改）。")
        strategies[keyword] = strategy

    seen_bodies: dict[str, str] = {}
    described: list[tuple[Path, dict]] = []
    for _, group in groups:
        entries: list[str] = []
        group_inputs: list[Path] = []
        for combination in _combinations(group):
            body = original
            values = {}
            for (path, _), value in zip(group, combination):
                keyword = keyword_of(path)
                values[keyword] = value
                body, _info = template_module.replace(body, keyword, value)
            for name, value in pinned.items():
                # a pinned name that is being swept here would overwrite the sweep
                if name.upper() in {keyword_of(p).upper() for p, items in group
                                    if len(items) > 1}:
                    continue
                body, info = template_module.replace_named(body, name, value)
                if info is None:
                    raise ValueError(f"固定参数 {name}：模板里找不到这个 @SET 名或占位符")

            key = "|".join(f"{keyword}={value}" for keyword, value in values.items())
            if body in seen_bodies:
                raise ValueError(
                    f"{key} 与 {seen_bodies[body]} 生成的文件内容完全一样——"
                    f"说明 {', '.join(strategies)} 的替换没有生效。已停止，"
                    "避免你拿到一组“看似收敛测试”的同一份输入。")
            seen_bodies[body] = key

            if layout == "dirs":
                name = _point_dir(group, combination,
                                  only_swept=(sweeps == "separate"))
                target_dir = out / name
                target_dir.mkdir(parents=True, exist_ok=True)
                for side in sides:
                    destination = target_dir / side.name
                    if not destination.exists() or \
                            destination.read_bytes() != side.read_bytes():
                        shutil.copy2(side, destination)
                target = target_dir / template.name
                entries.append(name)
            else:
                suffix = "-".join(f"{keyword.lower()}_{slug(value)}"
                                  for keyword, value in values.items())
                target = out / f"{template.stem}-{suffix}.inp"
                entries.append(target.name)
            write_template_text(target, body)
            outcome.written.append(target)
            group_inputs.append(target)
            described.append((target, dict(values)))

        outcome.scripts += _scripts_for(out, group, entries, layout=layout,
                                        inputs=group_inputs, run_script=run_script,
                                        pinned=pinned)

    _write_template_readme(out, template, groups, layout, run_script, sides,
                           outcome.scripts, described)

    outcome.lines.append(f"从模板 {template} 生成 {len(outcome.written)} 个输入"
                         f"（{layout} 布局）-> {out}")
    for path, values in described[:12]:
        where = path.parent.name if layout == "dirs" else path.name
        outcome.lines.append(f"  {where:<28} {_describe(values)}")
    if len(outcome.written) > 12:
        outcome.lines.append(f"  ... 还有 {len(outcome.written) - 12} 个")
    if sides:
        outcome.lines.append("  附属文件（每个目录都复制了一份）："
                             + ", ".join(side.name for side in sides))
    for path in outcome.scripts:
        outcome.lines.append(f"  {path.name}")
    for keyword, strategy in strategies.items():
        outcome.lines.append(
            f"  替换方式：{keyword} -> "
            f"{template_module.Substitution(keyword, strategy, 0).describe()}")
    return outcome


def _separate_groups_template(variations, original: str, pinned: dict):
    """Groups for ``--sweeps separate``, insisting the fixed value is known."""
    groups = _separate_groups(variations, pinned)
    for _keyword, group in groups:
        sweeping = sweepable(group)
        if len(sweeping) != 1:
            continue
        keyword = keyword_of(sweeping[0][0])
        if keyword.upper() not in ("CUTOFF", "REL_CUTOFF"):
            continue
        other = "REL_CUTOFF" if keyword.upper() == "CUTOFF" else "CUTOFF"
        if single_value(group, other) is not None:
            continue
        if other in {name.upper() for name in pinned}:
            continue
        if _template_value(original, other) is None:
            raise ValueError(
                f"分开扫 {keyword.upper()} 时，{other} 在模板里是个占位符、不是数字，"
                f"所以这一套测试没有确定的 {other}。\n"
                f"  加一个固定值即可，例如：--set {other}=450")
    return groups


def _describe(values: dict) -> str:
    return ", ".join(f"{keyword}={value:g}" if isinstance(value, float)
                     else f"{keyword}={value}" for keyword, value in values.items())


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _combinations(variations: list[tuple[str, list]]):
    return itertools.product(*[values for _, values in variations])


def _project_of(result: BuildResult) -> str:
    for section in result.sections:
        if section.name.upper() == "GLOBAL":
            value = section.get("PROJECT_NAME")
            if value:
                return str(value)
    return "cp2k"


def _locate(sections: list, path: str):
    """Find ``(parent_section, KEYWORD)`` for a ``A/B/KEYWORD`` style path."""
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    keyword = parts[-1]
    wanted = parts[:-1]
    for root in sections:
        if root.name.upper() != wanted[0].upper():
            continue
        node = root
        ok = True
        for name in wanted[1:]:
            nxt = node.subsection(name)
            if nxt is None:
                ok = False
                break
            node = nxt
        if ok:
            return node, keyword
    return None


# --------------------------------------------------------------------------- #
# readme
# --------------------------------------------------------------------------- #
def _script_help(scripts: list[Path], layout: str) -> list[str]:
    if not scripts:
        return ["(没有生成脚本；用 `--run-script` 可以要一份 run.sh)", ""]
    sh = [path.name for path in scripts if path.suffix == ".sh"]
    ps = [path.name for path in scripts if path.suffix == ".ps1"]
    runner = next((name for name in sh if name.endswith("run.sh")),
                  sh[0] if sh else "run.sh")
    lines = ["## How to run", "", "```sh"]
    lines += [f"sh {name}" for name in sh]
    lines += ["```", ""]
    if layout == "dirs":
        lines += ["每个参数点一个目录，运行脚本里已经列好了；"
                  f"也可以只跑其中几个：`sh {runner} 1 5 9`。", ""]
    else:
        lines += ["所有输入文件都在这一个目录里，运行脚本按参数顺序列好了；"
                  f"也可以只跑其中几个：`sh {runner} 1 5 9`。", ""]
    lines += ["并行：`NPROC_PER_CALC`（每个算例几个 MPI 进程）× "
              "`NPROC_TOTAL`（总共几个）。都是 1 就是串行。", ""]
    if ps:
        lines += ["Windows 上：`pwsh " + ps[0] + "`", ""]
    analyse = [name for name in sh if name.endswith("analyse.sh")]
    if analyse:
        lines += ["## How to read the results", "",
                  "在集群上跑分析脚本（只需要 sh + awk，不需要 Python）：", "",
                  "```sh"]
        lines += [f"sh {name}" for name in analyse]
        lines += ["```", "",
                  "它从每个 `.out` 里读 CUTOFF / REL_CUTOFF / 能量 / 每档网格点数，",
                  "写成 `cutoff_data.ssv` / `rel_cutoff_data.ssv`，并把表打到屏幕上；",
                  "末尾会打印每原子差值（相对最细的那次）。取哪一档由你判断。", ""]
    else:
        lines += ["## How to read the results", "",
                  "这一批是一次二维扫描，没有单独的分析脚本——",
                  "用 `cp2kbuild converge <目录>` 看，或者分开扫"
                  "（`--sweeps separate`）。", ""]
    return lines


def _write_scan_readme(outdir: Path, written: list[Path], groups, spec: Spec,
                       from_file, run_script: bool, property_name: str,
                       sweeps: str, protocol: bool = False) -> None:
    described = "; ".join(
        ", ".join(f"{keyword_of(path)} = {values}" for path, values in group
                  if len(values) > 1) or "fixed values only"
        for _, group in groups)
    lines = [
        "# CP2K convergence scan",
        "",
        f"Generated by cp2kbuild {__version__}.",
        f"Varied: {described}",
        f"Property: {property_name}",
        "",
    ]
    if protocol:
        lines += ["## How these inputs are meant to be run", "",
                  GRID_PROTOCOL_NOTE + "。",
                  "",
                  "所以这些点很便宜（每个 1 步 SCF），但算出的**绝对能量、力、"
                  "应力没有意义**——它们只用来定网格。要算力/做动力学，"
                  "请用定下来的 CUTOFF/REL_CUTOFF 另外写一份正常输入"
                  "（`cp2kbuild new ...`）。",
                  "",
                  "读法：能量看 `ENERGY| Total FORCE_EVAL`，"
                  "每档网格上落了多少个函数看 `count for grid N`"
                  "（`PRINT_LEVEL MEDIUM` 才有，别改小）。"
                  "判据可以把两边对齐：总能量的绝对差（教程的写法）或每原子能量差，"
                  "`cp2kbuild converge <目录>` 两种都会列出来。",
                  ""]
    lines += _script_help(
        sorted(outdir.glob("*_run.sh")) + sorted(outdir.glob("*_analyse.sh"))
        + sorted(outdir.glob("run.sh")), "dirs" if sweeps == "separate" else "flat")
    lines += ["## With cp2kbuild instead", "",
              "`cp2kbuild converge <目录>` 也会读同一批输出（不需要集群上有 Python）。",
              "",
              "## Files", ""]
    lines += [f"- `{path.name}`" for path in written]
    lines += [f"- `{path.name}`" for path in sorted(outdir.glob("*_run.*"))
              + sorted(outdir.glob("*_analyse.sh")) + sorted(outdir.glob("run.*"))]
    write_text_lf(outdir / "README.md", "\n".join(lines) + "\n")


def _write_template_readme(outdir: Path, template: Path, groups, layout: str,
                           run_script: bool, sides: list[Path],
                           scripts: list[Path], written: list[Path]) -> None:
    varied = "; ".join(
        ", ".join(f"{keyword_of(path)} = {values}" for path, values in group
                  if len(values) > 1) or "(none)"
        for _, group in groups)
    lines = [
        "# CP2K convergence scan (from an existing input)",
        "",
        f"Generated by cp2kbuild {__version__} from `{template.name}`.",
        f"Varied: {varied}",
        f"Layout: {layout}",
        "",
        "模板是**原样复制**的：`@SET`/`@IF`/`@INCLUDE`、注释、缩进、行尾都没动，",
        "只替换了被扫和固定下来的那几个值。",
        "",
    ]
    lines += _script_help(scripts, layout)
    if sides:
        lines += ["## Copied along", ""]
        lines += [f"- `{side.name}`" for side in sides]
        lines += ["", "（`@INCLUDE` 指向的文件和坐标文件，每个参数点目录里都有一份。）",
                  ""]
    lines += ["## With cp2kbuild instead", "",
              "同样的输出也可以用工具读（结论一致，而且会检查两步是否自洽）：",
              "", "```sh", f"cp2kbuild converge {outdir}", "```", "",
              "## Files", ""]
    for path, values in written[:40]:
        where = path.parent.name if layout == "dirs" else path.name
        lines.append(f"- `{where}/`  ({_describe(values)})" if layout == "dirs"
                     else f"- `{where}`  ({_describe(values)})")
    if len(written) > 40:
        lines.append(f"- ... 还有 {len(written) - 40} 个")
    write_text_lf(outdir / "README.md", "\n".join(lines) + "\n")
