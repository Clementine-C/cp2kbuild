"""Which commonly changed parameters does the tool not expose?

Measured, not guessed: walks CP2K's own regression corpus (the ``TEST_DIRS``
list), resolves keyword aliases through the manual database, and subtracts
everything the generator writes.  What is left is a list of parameters that real
CP2K inputs set and this tool has no way to set.

Three families are reported separately, because they need very different amounts
of work:

* QS / DFT (the one this tool targets),
* classical force fields (FIST / MM),
* the rest (DFTB, TDDFPT, NMR, ...), counted by feature so a whole missing
  method shows up as one line instead of a thousand keywords.

Usage::

    python tools/keyword_usage.py [--top 40] [--out report.md]
"""

from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cp2kbuild.model import PATH_SEP  # noqa: E402
from cp2kbuild.parser import read_input  # noqa: E402

#: a section whose keywords are output plumbing rather than physics knobs
PRINTISH = re.compile(
    r"(^|/)PRINT(/|$)|RESTART|TRAJECTORY|DCD|CUBE|MOLDEN|VIBRATIONAL_ANALYSIS"
    r"|MULLIKEN|HIRSHFELD|^FORCE_EVAL/DFT/PRINT/|^MOTION/PRINT/|TIMINGS")

MMISH = re.compile(r"(^|/)(MM|FORCEFIELD|EWALD|TOPOLOGY|SHELL)(/|$)|/MM/")

#: a missing *method* is more useful as one line than as its hundred keywords
FEATURES = {
    "力场 / FIST（MM、力场文件、Ewald）": r"METHOD\s+FIST|&MM\b|&FORCEFIELD\b",
    "DFTB / xTB / 半经验": r"METHOD\s+DFTB|&DFTB\b|TBLITE|&SE\b|SEMI_EMPIRICAL|"
                            r"METHOD\s+(PM6|AM1|MNDO|RM1|PNNL|DFTB3)",
    "QM/MM": r"&QM_KIND\b|&QMMM\b",
    "TDDFPT / 激发态 / XAS": r"&TDDFPT\b|&XAS\b|&EXCITED_STATES\b|"
                             r"&REAL_TIME_PROPAGATION\b|&ACTIVE_SPACE\b",
    "线性响应（NMR / 极化率 / EPR / RIXS）": r"&LINRES\b|&EPR\b|&RIXS\b|"
                                            r"&POLAR\b|&RESP\b",
    "DFT+U": r"&DFT_PLUS_U\b|U_MINUS_J|&PROJECTOR\b",
    "元动力学 / 增强采样": r"&METADYN\b|&FREE_ENERGY\b|&REPLICA\b|&WEIGHTED_HISTOGRAM",
    "NEB / 过渡态": r"&NEB\b|TRANSITION_STATE|&BAND\b",
    "电偶极/电场（EFIELD）": r"&EFIELD\b|&EXTERNAL_POTENTIAL\b",
    "隐式溶剂（SCCS/PCM）": r"&SCCS\b|&POISSON\b|&IMPLICIT_SOLVATION",
    "约束（CONSTRAINT/COLLECTIVE）": r"&CONSTRAINT\b|&COLLECTIVE_VARIABLES\b",
    "核坐标量子化（NVT/NQE）": r"&NORMALMODE\b|&NOSE\b|&HELIUM\b|&NQE\b",
}


def corpus_files(tests: Path) -> list[Path]:
    listing = tests / "TEST_DIRS"
    if not listing.is_file():
        raise SystemExit(f"找不到 TEST_DIRS：{listing}")
    files: list[Path] = []
    for line in listing.read_text(encoding="utf-8", errors="ignore").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        directory = tests / name
        if not directory.is_dir():
            continue
        for candidate in sorted(directory.rglob("*.inp")):
            if "untested_inputs" in candidate.parts:
                continue
            files.append(candidate)
    return files


class Canon:
    """Canonical keyword names and the set of known sections, from the database."""

    def __init__(self) -> None:
        from cp2kbuild.manualdb import load_db

        self.db = load_db()
        self.alias: dict[tuple[str, str], str] = {}
        self.sections: set[str] = set()
        if self.db is None:
            return
        raw = getattr(self.db, "_keywords", {})
        for key, record in raw.items():
            parts = key.split(".")
            if len(parts) < 3:
                continue
            section = "/".join(parts[1:-1]).upper()
            name = parts[-1].upper()
            self.alias[(section, name)] = record["name"].upper()
            for alias in record.get("aliases", []) or []:
                self.alias[(section, alias.upper())] = record["name"].upper()
        prefix = "CP2K_INPUT/"
        self.sections = {
            (path[len(prefix):] if path.upper().startswith(prefix) else path).upper()
            for path in getattr(self.db, "_sections", {})}

    def name(self, section: str, keyword: str) -> str:
        return self.alias.get((section.upper(), keyword.upper()), keyword.upper())

    def known_section(self, path: str) -> bool:
        return path.upper() in self.sections


def keys_of(path: Path, canon: Canon) -> tuple[set[str], set[str]]:
    """(keyword paths, sections used with a parameter) for one input file."""
    try:
        parsed = read_input(path)
    except Exception:  # noqa: BLE001
        return set(), set()
    keywords: set[str] = set()
    sections: set[str] = set()
    for root in parsed.sections:
        for section_path, node in root.walk():
            sections.add(section_path.upper())
            for keyword in node.keywords:
                if not keyword.name or re.fullmatch(r"[-+0-9.eEdD]+", keyword.name):
                    continue
                values = [v for v in keyword.values]
                numeric = [v for v in values if isinstance(v, (int, float))
                           and not isinstance(v, bool)]
                if len(numeric) >= 3 and len(numeric) == len(values):
                    # a data line (cell vector A/B/C, a coordinate), not a keyword
                    continue
                canonical = canon.name(section_path, keyword.name)
                keywords.add(f"{section_path}{PATH_SEP}{canonical}".upper())
    return keywords, sections


def tool_surface() -> tuple[set[str], set[str]]:
    """Every keyword path and section path the generator writes."""
    from cp2kbuild.datafiles import DataIndex, discover_data_dir
    from cp2kbuild.recipes import TASKS, Spec, build
    from cp2kbuild.structures import parse_cell_text, read_structure

    index = DataIndex(str(discover_data_dir() or ""))
    keywords: set[str] = set()
    sections: set[str] = set()
    def variants(task: str, path: Path):
        # the tool can express more than its default build does: charge, open
        # shells, dispersion, every print switch, thermochemistry, and so on
        base = dict(structure=read_structure(str(path)),
                    vacuum=None if path.name.startswith("silicon") else 12.0)
        yield Spec(task=task, **base)
        yield Spec(task=task, charge=-1, multiplicity=2, uks=True, **base)
        yield Spec(task=task, dispersion="d3bj", **base)
        yield Spec(task=task, scf="DIAGONALIZATION", basis_target="molecule",
                   **base)
        for extra in ({"print_forces": True, "print_mulliken": True,
                       "print_dipole": True},
                      {"intensities": True, "thermochemistry": True},
                      {"pdos_nlumo": 12, "pdos_ldos": [[1, 2, 3]],
                       "pdos_broaden": 0.1},
                      {"cell": None, "periodic": "XYZ", "keep_angles": True,
                       "external_pressure": 1.0}):
            spec = Spec(task=task, **base)
            for key, value in extra.items():
                setattr(spec, key, value)
            yield spec

    for task in TASKS:
        for name in ("silicon.xyz", "water.xyz"):
            path = ROOT / "examples" / name
            for spec in variants(task, path):
                if task == "neb":
                    spec.replica_files = [str(path)] * 5
                    spec.replicas = 5
                if task in ("cell_opt",) and spec.cell is None:
                    spec.cell = parse_cell_text("5.43 5.43 5.43")
                try:
                    result = build(spec, index)
                except Exception:  # noqa: BLE001
                    continue
                for root in result.sections:
                    for section_path, node in root.walk():
                        sections.add(section_path.upper())
                        for keyword in node.keywords:
                            keywords.add(f"{section_path}{PATH_SEP}"
                                         f"{keyword.name.upper()}".upper())
    # the scan layer adds these on top of the templates
    keywords |= {
        f"FORCE_EVAL{PATH_SEP}DFT{PATH_SEP}MGRID{PATH_SEP}CUTOFF".upper(),
        f"FORCE_EVAL{PATH_SEP}DFT{PATH_SEP}MGRID{PATH_SEP}REL_CUTOFF".upper(),
    }
    return keywords, sections


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tests", default=None)
    parser.add_argument("--top", type=int, default=40)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    from tools.selftest import newest_source_tree  # type: ignore

    source = newest_source_tree()
    tests = Path(args.tests) if args.tests else (source / "tests" if source else None)
    if tests is None or not tests.is_dir():
        raise SystemExit("找不到 CP2K 的 tests 目录")

    files = corpus_files(tests)
    canon = Canon()
    covered_keys, covered_sections = tool_surface()
    print(f"语料 {tests}")
    print(f"文件 {len(files)}，工具生成的关键字 {len(covered_keys)} 个、"
          f"section {len(covered_sections)} 个\n", flush=True)

    key_counts: collections.Counter[str] = collections.Counter()
    section_counts: collections.Counter[str] = collections.Counter()
    for index, path in enumerate(files, 1):
        keys, sections = keys_of(path, canon)
        key_counts.update(keys)
        section_counts.update(sections)
        if index % 800 == 0:
            print(f"  已读 {index}/{len(files)}", flush=True)

    lines = [f"# 语料里常见、但工具不生成的参数（{len(files)} 个文件）", ""]

    # ---- missing sections, QS/DFT only ---------------------------------- #
    missing_sections = [(name, used) for name, used in section_counts.most_common()
                        if name.upper() not in covered_sections
                        and not MMISH.search(name)
                        and not PRINTISH.search(name)
                        and canon.known_section(name)]
    lines += ["## 缺失的 section（QS/DFT 侧，按使用文件数）", "",
              "| 文件数 | 占比 | section |", "|---:|---:|---|"]
    for name, used in missing_sections[:args.top]:
        lines.append(f"| {used} | {100.0 * used / len(files):.1f}% | `{name}` |")

    # ---- missing keywords ------------------------------------------------- #
    buckets: dict[str, list[tuple[str, int]]] = {"dft": [], "mm": [], "other": []}
    for key, used in key_counts.most_common():
        if key in covered_keys:
            continue
        section = key.rsplit(PATH_SEP, 1)[0]
        if canon.known_section(key):
            continue                     # a section used with a parameter
        top = section.split(PATH_SEP)[0].upper()
        if MMISH.search(section):
            buckets["mm"].append((key, used))
        elif PRINTISH.search(section):
            buckets["other"].append((key, used))
        elif top in ("FORCE_EVAL", "MOTION", "GLOBAL", "EXT_RESTART"):
            buckets["dft"].append((key, used))
        else:
            buckets["other"].append((key, used))

    lines += ["", "## 缺失的关键字（QS/DFT 侧，按使用文件数）", "",
              "| 文件数 | 占比 | 路径 |", "|---:|---:|---|"]
    for key, used in buckets["dft"][:args.top]:
        lines.append(f"| {used} | {100.0 * used / len(files):.1f}% | `{key}` |")

    lines += ["", f"## 缺失的关键字（输出相关的前 15）", "",
              "| 文件数 | 占比 | 路径 |", "|---:|---:|---|"]
    for key, used in buckets["other"][:15]:
        lines.append(f"| {used} | {100.0 * used / len(files):.1f}% | `{key}` |")

    lines += ["", "## 缺失的关键字（力场 / MM 侧的前 10）", "",
              "| 文件数 | 占比 | 路径 |", "|---:|---:|---|"]
    for key, used in buckets["mm"][:10]:
        lines.append(f"| {used} | {100.0 * used / len(files):.1f}% | `{key}` |")

    # ---- whole missing methods -------------------------------------------- #
    lines += ["", "## 整块没做的方法（按出现文件数）", "",
              "| 文件数 | 占比 | 方法 |", "|---:|---:|---|"]
    feature_counts = []
    for label, pattern in FEATURES.items():
        regex = re.compile(pattern, re.I)
        used = sum(1 for path in files
                   if regex.search(path.read_text(encoding="utf-8", errors="ignore")))
        feature_counts.append((label, used))
    for label, used in sorted(feature_counts, key=lambda item: -item[1]):
        lines.append(f"| {used} | {100.0 * used / len(files):.1f}% | {label} |")

    report = "\n".join(lines) + "\n"
    out = Path(args.out) if args.out else ROOT / "packaging" / "keyword_gaps.md"
    out.write_text(report, encoding="utf-8")
    print(f"\n写了 {out}")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
