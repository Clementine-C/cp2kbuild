"""Self-test: generate every task, validate it, then lint real CP2K inputs.

Part 1 builds one input per task from the bundled examples and checks that
cp2kbuild's own output is clean.

Part 2 feeds a sample of CP2K's own regression inputs through the linter.  Those
files are known-good, so anything the linter reports as an *error* there is a bug
in the linter or a gap in the manual database.

Usage::

    python tools/selftest.py --tests E:/desktop/cp2k/cp2k-2025.1/tests --limit 120
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from cp2kbuild import DataIndex, Spec, build, read_input, read_structure  # noqa: E402
from cp2kbuild.manualdb import load_db  # noqa: E402
from cp2kbuild.metadyn import (Cv, Metadyn, Metavar, Wall,  # noqa: E402
                               problems as metadyn_problems)
from cp2kbuild.model import render_document  # noqa: E402
from cp2kbuild.parser import parse_input  # noqa: E402
from cp2kbuild.recipes import MmLennardJones, MmMolecule  # noqa: E402
from cp2kbuild.structures import Cell  # noqa: E402
from cp2kbuild.validate import Validator, summarise  # noqa: E402

EXAMPLES = ROOT / "examples"

#: any CJK character - the report (messages *and* hints) is written in Chinese,
#: with CP2K's own English wording quoted inside the Chinese sentences
_CJK = re.compile(r"[\u4e00-\u9fff]")


def metadyn_wt() -> Metadyn:
    """Well-tempered metadynamics on two CVs, one of them walled.

    Used both by the round-trip test (``part1_import``) and by the generated-input
    checks, so the two cannot drift apart.
    """
    return Metadyn(
        enabled=True, do_hills=True, nt_hills=100, hill_height=4.2e-4,
        well_tempered=True, temperature=330.0, delta_t=3000.0,
        min_disp=0.05, hill_tail_cutoff=3.0,
        cvs=(Cv(kind="distance", atoms=(1, 2)),
             Cv(kind="coordination", kinds_from="O", kinds_to="H", r0=1.2,
                nn=6, nd=8)),
        metavars=(Metavar(colvar=1, scale=0.5,
                          wall=Wall(position=2.4, type="QUADRATIC",
                                    direction="WALL_MINUS", k=0.5)),
                  Metavar(colvar=2)),
        print_colvar=True, print_hills=True, print_every=20)


def metadyn_lagrange() -> Metadyn:
    """A combined CV, a path/RMSD CV and extended-Lagrangian CVs."""
    return Metadyn(
        enabled=True, do_hills=True, nt_hills=25, hill_height=2.0e-3,
        lagrange=True, lagrange_langevin=True,
        cvs=(Cv(kind="combine", function="(CV1^P+CV2^P)^(1./P)",
                parameters=("P",), values=(4.0,), variables=("CV1", "CV2"),
                children=(Cv(kind="distance", atoms=(1, 2)),
                          Cv(kind="angle", atoms=(1, 2, 3)))),
             Cv(kind="path", atoms=(1, 2, 3), frames=("a.xyz", "b.xyz"),
                rmsd=True, distances_rmsd=True, align_frames=True, lam=0.5,
                subset_type="LIST"),
             Cv(kind="rmsd", atoms=(1, 2, 3), frames=("a.xyz",),
                align_frames=True),
             Cv(kind="torsion", atoms=(1, 2, 3, 1))),
        metavars=(Metavar(colvar=1),
                  Metavar(colvar=2, mass=2.0, gamma=1.0, lam=0.03),
                  Metavar(colvar=3, mass=1.0),
                  Metavar(colvar=4, mass=5.0)),
        print_every=1)


def part1(index: DataIndex, db, quiet: bool = False,
          source: Path | None = None) -> int:
    print("=" * 78)
    print("part 1: generated inputs")
    print("=" * 78)
    cases = [
        ("energy", "water.xyz", {"vacuum": 12.0, "dispersion": "d3bj"}),
        ("geo_opt", "water.xyz", {"vacuum": 12.0, "dispersion": "d3bj"}),
        ("cell_opt", "silicon.xyz", {}),
        ("md", "water.xyz", {"vacuum": 12.0, "ensemble": "NVT", "steps": 2000}),
        ("md", "silicon.xyz", {"ensemble": "NPT_I", "steps": 2000, "temperature": 400}),
        ("freq", "water.xyz", {"vacuum": 12.0, "intensities": True}),
        ("freq", "silicon.xyz", {}),
        ("band", "silicon.xyz", {"kpath": "GAMMA-X-W-K-GAMMA-L-U-W-L-K|U-X",
                                 "lattice": "fcc", "npoints": 60}),
        ("pdos", "silicon.xyz", {"pdos_ldos": [[1, 2, 3]], "pdos_nlumo": 10}),
        ("energy", "water.xyz", {"vacuum": 12.0, "functional": "PBE0",
                                 "scf": "DIAGONALIZATION", "smearing": False}),
        ("energy", "water.xyz", {"vacuum": 10.0, "multiplicity": 2, "charge": 1}),
        ("energy", "water.xyz", {"vacuum": 10.0, "basis": "TZV2P-MOLOPT-GTH",
                                 "cutoff": 800}),
    ]
    failures = 0
    # ---- 摘要那一行必须把"调用者自己打印的"发现也算进去 -------------------- #
    # 真实反馈：表格里明明有「警告 build-warning」，上方却写着「0 错误 / 0 警告」
    # （生成器的发现被单独算成"生成期问题"）。CLI 的收尾行是同一个毛病：
    # `new` 先打 `warning:`/`error:` 行，再把只含校验器发现的计数写进摘要。
    from cp2kbuild.validate import Diagnostic as _Diag

    if (summarise([_Diag("error", "x", "m")], extra_errors=2, extra_warnings=1)
            == "3 个错误、1 个警告、0 条提示"
            and summarise([], extra_warnings=2) == "0 个错误、2 个警告、0 条提示"
            and summarise([]) == "干净：没有发现问题"):
        print("  ok   摘要行把「调用者自己打印的」发现也数进去"
              "（不会再出现 0 错误 / 0 警告）")
    else:
        failures += 1
        print(f"  FAIL 摘要计数不对：{summarise([_Diag('error', 'x', 'm')], 2, 1)} / "
              f"{summarise([], 0, 2)}")

    for task, structure, overrides in cases:
        spec = Spec(task=task, structure=read_structure(EXAMPLES / structure),
                    **overrides)
        result = build(spec, index)
        text = render_document(result.sections, title="cp2kbuild selftest",
                               notes=result.notes)
        diagnostics = Validator(db, index).run(result.sections)
        errors = [d for d in diagnostics if d.level == "error"]
        warnings = [d for d in diagnostics if d.level == "warning"]
        label = f"{task:9s} {structure:14s} {overrides or ''}"
        status = "OK  " if not errors else "FAIL"
        print(f"\n[{status}] {label}")
        print(f"        {summarise(diagnostics)}")
        for item in diagnostics:
            marker = {"error": "  !!", "warning": "  ~ ", "info": "  . "}[item.level]
            print(f"{marker} {item.code}: {item.message}")
            if item.hint:
                print(f"       {item.hint}")
            if not quiet and item.level == "error":
                print(text)
        if errors:
            failures += 1
    failures += part1_classical(index, db)
    failures += part1_relay(index, db)
    failures += part1_units(index, db, source)
    failures += part1_import(index, db)
    return failures


#: route -> (PARMTYPE, CONNECTIVITY, COORDINATE, coordinate file name).  The
#: values are taken from CP2K's own enumerations (``input_cp2k_mm.F`` /
#: ``input_cp2k_subsys.F``); the test re-checks the table in recipes.py against
#: these literals, so a typo in either place shows up here instead of in a job
#: that will not start.  The file *suffix* is what decides COORDINATE when the
#: user did not choose a format explicitly.
CLASSICAL_EXPECTED = {
    "AMBER": ("AMBER", "AMBER", "CRD", "sys.inpcrd"),
    "CHARMM": ("CHM", "PSF", "PDB", "sys.pdb"),
    "GROMOS96": ("G96", "G96", "G96", "sys.g96"),
    "GROMOS87": ("G87", "G87", "G96", "sys.g96"),
    "IN_INPUT": ("OFF", "GENERATE", "XYZ", None),
    "PAIR": ("OFF", "GENERATE", "XYZ", None),
}


def part1_import(index: DataIndex, db=None) -> int:
    """A generated input must describe itself.

    Write an input, read its parameters back out (``importinp``), generate again
    from *those* and compare: if the two files differ, the form would show
    something other than what the file says - which is exactly the confusion
    「读参数」sexists to remove.
    """
    import tempfile

    from cp2kbuild.importinp import import_input
    from cp2kbuild.recipes import MmLennardJones, MmMolecule

    print("\n" + "-" * 78)
    print("reading the parameters back out of a file (importinp)")
    print("-" * 78)
    failures = 0
    water = read_structure(EXAMPLES / "water.xyz")
    silicon = read_structure(EXAMPLES / "silicon.xyz")
    workdir = Path(tempfile.mkdtemp(prefix="cp2kbuild_import_"))

    cases = [
        ("energy + Molden", dict(
            task="energy", structure=water, vacuum=12.0, dispersion="d3bj",
            project="watt", print_level="LOW", print_molden=True,
            molden_nlumo=-1, molden_unit="BOHR", molden_write_pseudo=True)),
        ("geo_opt CG/77", dict(task="geo_opt", structure=silicon,
                               optimizer="CG", max_iter=77)),
        ("cell_opt 5 bar + keep angles", dict(
            task="cell_opt", structure=silicon, optimizer="BFGS", max_iter=200,
            external_pressure=5.0, keep_angles=True)),
        ("MD NVT 400 K + seed", dict(
            task="md", structure=silicon, ensemble="NVT", temperature=400.0,
            steps=2000, timestep=0.5, thermostat="NOSE", seed=7,
            trajectory_stride=25)),
        # TEMP_TOL is obsolescent in CP2K ("Using a CSVR thermostat with a short
        # timeconstant is recommended", input_cp2k_md.F) and its default is 0, so
        # the tool must not write it at all
        ("MD NVT + CSVR", dict(
            task="md", structure=silicon, ensemble="NVT", temperature=330.0,
            steps=1000, thermostat="CSVR", thermostat_timecon=50.0)),
        ("MD NPT + barostat", dict(
            task="md", structure=silicon, ensemble="NPT_I", pressure=1.0,
            barostat_timecon=750.0, steps=500)),
        ("freq + intensities", dict(task="freq", structure=water, vacuum=12.0,
                                    intensities=True, thermochemistry=True)),
        ("对角化 + SMEAR", dict(
            task="energy", structure=silicon, scf="DIAGONALIZATION",
            added_mos=24, smearing=True, electronic_temperature=1000.0,
            mixing_alpha=0.1, diag_algorithm="DAVIDSON", level_shift=0.1)),
        ("OT 子参数 + 外层循环", dict(
            task="energy", structure=silicon, ot_minimizer="CG",
            ot_preconditioner="FULL_ALL", ot_energy_gap=0.002,
            ot_linesearch="GOLD", outer_max_scf=10, outer_eps_scf=1e-7)),
        ("接力（只取坐标）", dict(
            task="md", structure=silicon, steps=100,
            ext_restart_file="1_geo-1.restart", ext_restart_take=("pos",))),
        ("FIST AMBER", dict(
            task="md", structure=water, method="FIST", mm_route="AMBER",
            mm_parm_file="mix.prmtop", mm_conn_file="mix.prmtop",
            mm_coord_file="mix.pdb", mm_ewald="SPME", mm_ewald_gmax=64,
            mm_ewald_alpha=3.5, steps=500)),
        ("FIST SPME 不给 GMAX（工具自己估）", dict(
            task="md", structure=water, method="FIST", mm_route="AMBER",
            mm_parm_file="mix.prmtop", mm_conn_file="mix.prmtop",
            mm_coord_file="mix.pdb", mm_ewald="SPME", steps=500)),
        ("FIST 混合体系 &MOL_SET", dict(
            task="md", structure=water, method="FIST", mm_route="AMBER",
            mm_parm_file="mix.prmtop", mm_coord_file="mix.pdb",
            mm_molecules=[MmMolecule("a.prmtop", "AMBER", 4),
                          MmMolecule("b.prmtop", "AMBER", 20)], steps=500)),
        ("FIST 纯对动 PAIR", dict(
            task="energy", structure=water, method="FIST", mm_route="PAIR",
            mm_lj=[MmLennardJones(["Ar", "Ar"], 0.2379, 3.405, 10.0)],
            mm_charges=[("Ar", 0.0)])),
        ("k 点 + 立方体 + 固定原子", dict(
            task="geo_opt", structure=silicon, kpoint_grid=(6, 6, 6),
            kpoint_full_grid=True, kpoint_verbose=True, kpoint_gamma_centered=True,
            print_mo_cubes=True, mo_cubes_nhomo=1, mo_cubes_nlumo=2,
            mo_cubes_stride="2 2 2", print_density_cube=True,
            density_cube_stride="1 1 1", fixed_atoms="1..4",
            fixed_atoms_components="Z", eps_default=1.0e-9,
            extrapolation="USE_GUESS")),
        ("显式 k 点（GENERAL）", dict(
            task="energy", structure=silicon,
            kpoint_points=[(0.0, 0.0, 0.0, 1.0), (0.5, 0.5, 0.5, 2.0)])),
        ("MD 温度/能量打印", dict(
            task="md", structure=silicon, ensemble="NVT", steps=1000,
            md_temp_kind_print=True, md_temp_kind_every=25,
            md_energy_print=True, md_energy_every=50, md_energy_file="run",
            thermostat_temp_print=True, thermostat_temp_every=20,
            thermostat_region="MOLECULE")),
        ("氘代（H→D）", dict(
            task="md", structure=water, vacuum=12.0, steps=1000, timestep=1.0,
            h_to_d=True)),
        ("元动力学（井温 + 距离/配位 CV + 墙）", dict(
            task="md", structure=water, vacuum=12.0, ensemble="NVT",
            steps=20000, temperature=330.0, metadyn=metadyn_wt())),
        ("元动力学（组名 + 路径 CV + 扩展拉格朗日）", dict(
            task="md", structure=water, vacuum=12.0, steps=5000,
            metadyn=metadyn_lagrange())),
        ("元动力学（PLUMED 驱动）", dict(
            task="md", structure=water, vacuum=12.0, steps=5000,
            metadyn=Metadyn(enabled=True, use_plumed=True,
                            plumed_file="water.plumedinp"))),
    ]

    spec_fields = set(Spec.__dataclass_fields__)
    for label, kwargs in cases:
        spec = Spec(**kwargs)
        original = render_document(build(spec, index).sections, title="t", notes=[])
        path = workdir / (label.replace("/", "_").replace(" ", "_") + ".inp")
        path.write_text(original, encoding="utf-8")
        try:
            report = import_input(path)
        except Exception as exc:      # noqa: BLE001 - report, never crash
            failures += 1
            print(f"  FAIL {label}: 导入抛异常 {type(exc).__name__}: {exc}")
            continue

        values = {k: v for k, v in report.values.items()
                  if k in spec_fields and k not in ("structure", "cell", "vacuum")}
        # the values that live in the file but not in Spec: the form's tables
        rows = report.get("mm_lj")
        if rows:
            values["mm_lj"] = [MmLennardJones(r[:2], r[2], r[3], r[4]) for r in rows]
        rows = report.get("mm_charges")
        if rows:
            values["mm_charges"] = [(r[0], r[1]) for r in rows]
        rows = report.get("mm_molecules")
        if rows:
            values["mm_molecules"] = [MmMolecule(r[0], r[1], int(r[2])) for r in rows]
        values["task"] = report.get("task", kwargs.get("task"))
        values["structure"] = kwargs.get("structure")
        values["vacuum"] = kwargs.get("vacuum")
        values["cell"] = kwargs.get("cell")
        if report.get("mm_route"):
            values["mm_route"] = report.get("mm_route")
        try:
            again = render_document(build(Spec(**values), index).sections,
                                    title="t", notes=[])
        except Exception as exc:      # noqa: BLE001
            failures += 1
            print(f"  FAIL {label}: 用读出来的参数生成失质 {type(exc).__name__}: {exc}")
            continue

        if again == original:
            print(f"  ok   {label}：读出来的参数能原样生成同一份输入"
                  f"（{len(report.settings)} 项）")
            continue
        failures += 1
        import difflib

        diff = [line for line in difflib.unified_diff(
            original.splitlines(), again.splitlines(), n=0, lineterm="")
            if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
        print(f"  FAIL {label}：读出来的参数与文件不一致（{len(diff)} 行差异）")
        for line in diff[:6]:
            print(f"       {line}")

    # a hand-written input that uses the *other* spellings must work too
    handwritten = workdir / "handwritten.inp"
    handwritten.write_text(
        "&GLOBAL\n  PROJECT_NAME hand\n  RUN_TYPE MD\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD QUICKSTEP\n  &DFT\n"
        "    &QS\n      METHOD GPW\n    &END QS\n"
        "    &MGRID\n      CUTOFF 500\n      REL_CUTOFF 50\n    &END MGRID\n"
        "    &XC\n      &XC_FUNCTIONAL\n        &PBE\n        &END PBE\n"
        "      &END XC_FUNCTIONAL\n"
        "      &VDW_POTENTIAL\n        POTENTIAL_TYPE PAIR_POTENTIAL\n"
        "        &PAIR_POTENTIAL\n          TYPE DFTD3(BJ)\n        &END PAIR_POTENTIAL\n"
        "      &END VDW_POTENTIAL\n    &END XC\n"
        "    &SCF\n      SCF_GUESS ATOMIC\n      MAX_SCF 12\n      &OT\n"
        "        MINIMIZER CG\n      &END OT\n    &END SCF\n"
        "  &END DFT\n  &SUBSYS\n    &CELL\n      ABC 10 10 10\n    &END CELL\n"
        "    &COORD\n      Si 0 0 0\n    &END COORD\n  &END SUBSYS\n&END FORCE_EVAL\n"
        "&MOTION\n  &MD\n    ENSEMBLE NVE\n    STEPS 42\n    &THERMOSTAT\n"
        "      TYPE NONE\n    &END THERMOSTAT\n  &END MD\n&END MOTION\n",
        encoding="utf-8")
    report = import_input(handwritten)
    want = {"task": "md", "project": "hand", "functional": "PBE", "dispersion": "d3bj",
            "cutoff": 500.0, "rel_cutoff": 50.0, "scf": "OT", "scf_guess": "ATOMIC",
            "max_scf": 12, "ot_minimizer": "CG", "ensemble": "NVE", "steps": 42,
            "thermostat": "NONE"}
    bad = {k: (v, report.get(k)) for k, v in want.items() if report.get(k) != v}
    if bad:
        failures += 1
        print(f"  FAIL 手写输入的另一种写法没读对：{bad}")
    else:
        print(f"  ok   手写输入，EXC_FUNCTIONAL/&PBE、DFTD3(BJ)。 OT）读对了 "
              f"{len(want)} 项")
    if report.get("mm_connectivity") or report.get("mm_ewald"):
        pass

    # &SUBSYS/&CELL 也要读回来：力场输入的原子在 COORD_FILE_NAME 里，盒子只在
    # 这一处（cell_methods.F：CELL_FILE_NAME > ABC > A/B/C > COORD_FILE_NAME），
    # 少了它力场文件路线的预览就一直是「还差一个晶胞」（用户就是这么问的）
    cell_input = workdir / "fist_cell.inp"
    cell_input.write_text(
        "&GLOBAL\n  PROJECT_NAME cell\n  RUN_TYPE MD\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD FIST\n  &MM\n    &FORCEFIELD\n"
        "      PARMTYPE AMBER\n      PARM_FILE_NAME mix.prmtop\n"
        "    &END FORCEFIELD\n  &END MM\n  &SUBSYS\n    &CELL\n"
        "      ABC [angstrom] 24.51 24.4 24.41\n      PERIODIC XYZ\n    &END CELL\n"
        "    &TOPOLOGY\n      CONNECTIVITY AMBER\n      CONN_FILE_NAME mix.prmtop\n"
        "      COORDINATE CRD\n      COORD_FILE_NAME mix.inpcrd\n    &END TOPOLOGY\n"
        "  &END SUBSYS\n&END FORCE_EVAL\n&MD\n  STEPS 100\n&END MD\n",
        encoding="utf-8")
    # ---- &EXT_RESTART：CP2K 自己写的 restart 里那一块是**空的** -------------- #
    # 真实反馈：「拖进 restart → 读参数 → 从上次续算会被填上 true」
    own_restart = workdir / "own-1.restart"
    own_restart.write_text(
        "&GLOBAL\n  RUN_TYPE MD\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD FIST\n  &MM\n    &FORCEFIELD\n      PARMTYPE AMBER\n"
        "      PARM_FILE_NAME \n    &END FORCEFIELD\n  &END MM\n&END FORCE_EVAL\n"
        "&EXT_RESTART\n   RESTART_FILE_NAME \n   RESTART_DEFAULT F\n"
        "   RESTART_VEL T\n&END EXT_RESTART\n"
        "&MOTION\n  &MD\n    ENSEMBLE NPT_I\n    STEPS 100\n  &END MD\n&END MOTION\n",
        encoding="utf-8")
    own = import_input(own_restart)
    if own.get("ext_restart_file") is None and own.get("ext_restart_take") is None \
            and own.get("restart_default") is None \
            and any("EXT_RESTART" in note for note in own.unread):
        print("  ok   CP2K 自己写的 restart（RESTART_FILE_NAME 空）不填「从上次续算」，"
              "并在「没读」里说明原因")
    else:
        failures += 1
        print(f"  FAIL 空的 RESTART_FILE_NAME 还是被读了："
              f"{own.get('ext_restart_file')!r} / {own.get('ext_restart_take')!r} / "
              f"{own.get('restart_default')!r} / {own.unread[:1]}")
    if own.get("mm_parm_file") is None:
        print("  ok   没值的关键字不会变成字符串 'True'（PARM_FILE_NAME 空行）")
    else:
        failures += 1
        print(f"  FAIL 没值的关键字被读成了 {own.get('mm_parm_file')!r}")
    relay = workdir / "relay.inp"
    relay.write_text(
        "&GLOBAL\n  RUN_TYPE MD\n&END GLOBAL\n"
        "&EXT_RESTART\n  RESTART_FILE_NAME prev-1.restart\n  RESTART_DEFAULT F\n"
        "  RESTART_VEL T\n  RESTART_COUNTERS T\n&END EXT_RESTART\n"
        "&MOTION\n  &MD\n    ENSEMBLE NVT\n    STEPS 100\n  &END MD\n&END MOTION\n",
        encoding="utf-8")
    relay_report = import_input(relay)
    if relay_report.get("ext_restart_file") == "prev-1.restart" \
            and relay_report.get("ext_restart_take") == ("vel", "counters"):
        print("  ok   真的指了文件的 &EXT_RESTART 照读（含取哪些量）")
    else:
        failures += 1
        print(f"  FAIL 正常的 &EXT_RESTART 没读对："
              f"{relay_report.get('ext_restart_file')!r} / "
              f"{relay_report.get('ext_restart_take')!r}")

    cell_report = import_input(cell_input)
    if cell_report.get("cell") == "24.51,24.4,24.41" \
            and cell_report.get("periodic", "").upper() == "XYZ":
        print("  ok   力场输入：&CELL 的 ABC 与 PERIODIC 也读回来了"
              "（文件路线少了它预览就出不来）")
    else:
        failures += 1
        print(f"  FAIL &CELL 没读回来：{cell_report.get('cell')!r} / "
              f"{cell_report.get('periodic')!r}")
    vector_input = workdir / "cell_vectors.inp"
    vector_input.write_text(
        "&FORCE_EVAL\n  METHOD FIST\n  &SUBSYS\n    &CELL\n"
        "      A 10 0 0\n      B 0 20 0\n      C 0 0 30\n    &END CELL\n"
        "  &END SUBSYS\n&END FORCE_EVAL\n", encoding="utf-8")
    vector_report = import_input(vector_input)
    if vector_report.get("cell") == "10,20,30":
        print("  ok   &CELL 写成 A/B/C 矢量时也换算成长度（10,20,30）")
    else:
        failures += 1
        print(f"  FAIL A/B/C 矢量没换算：{vector_report.get('cell')!r}")

    print(f"  note 导入测试的工作目录：{workdir}")
    return failures


def part1_units(index: DataIndex, db, source: Path | None = None) -> int:
    """Unit annotations: CP2K resolves ``[unit]`` while *reading* the input.

    A spelling it does not know aborts the run before the first SCF step
    (``[ABORT] Unknown unit: a.u.``, ``common/cp_units.F:384``), so the table in
    ``cp2kbuild/units.py`` has to be right in both directions: never reject what
    CP2K accepts, never accept what it aborts on.
    """
    import re
    import tempfile

    from cp2kbuild.units import ACCEPTED_UNITS, check, is_valid
    from cp2kbuild.units import units_in
    from cp2kbuild.parser import parse_input as _parse
    from cp2kbuild.validate import Validator as _Validator

    print("\n" + "-" * 78)
    print("unit annotations CP2K would accept")
    print("-" * 78)
    failures = 0
    silicon = read_structure(EXAMPLES / "silicon.xyz")

    # (a) the table against CP2K's own source, when it is there
    if source is not None:
        units_file = source / "src" / "common" / "cp_units.F"
        if units_file.is_file():
            text = units_file.read_text(encoding="utf-8", errors="ignore")
            start = text.find("SUBROUTINE cp_unit_create(")
            end = text.find("END SUBROUTINE cp_unit_create", start)
            block = text[start:end] if start >= 0 and end > start else ""
            names = set()
            for line in block.splitlines():
                if "CASE" not in line:
                    continue
                for token in re.findall(r'"([^"]*)"', line):
                    for part in token.split('", "'):
                        value = part.strip().upper()
                        if re.fullmatch(r"[A-Z][A-Z_0-9-]*", value):
                            names.add(value)
            ours = set(ACCEPTED_UNITS)
            missing = sorted(names - ours - {"AU"})
            extra = sorted(ours - names)
            if missing or extra:
                failures += 1
                print(f"  FAIL 单位表和 CP2K 源码不一致：漂 {missing} / 多 {extra}")
            else:
                print(f"  ok   单位表与 CP2K 源码一致（{len(ours)} 个，"
                      "`AU` 单独按「必须带种类」处理）")
        else:
            print("  skip 没找分 cp_units.F，跳过源码对照")

    # (b) CP2K's own spellings must pass, `a.u.` and a bare `au` must not
    accepted = ("angstrom", "eV", "angstrom^-1", "hartree", "deg", "au_l", "fs",
                "bohr", "K", "au_e", "kcalmol", "bar", "rad^2kjmol", "kjmol",
                "nm^-2kjmol", "angstrom^-2kcalmol", "internal_cp2k", "va-1")
    rejected = ("a.u.", "au", "a.u", "au_f.", "amu2", "rydberg")
    bad = [u for u in accepted if not is_valid(u)]
    wrong = [u for u in rejected if is_valid(u)]
    if bad or wrong:
        failures += 1
        print(f"  FAIL 单位判定不对：该收的漏了 {bad} / 该拒的收了 {wrong}")
    else:
        print(f"  ok   {len(accepted)} 种 CP2K 写法都收，" 
              f"{len(rejected)} 种错写法都拒（含 `a.u.` / 光秃秃的 `au`）")

    # (c) our own generator must never emit a bad annotation
    cases = [
        dict(task="energy", structure=silicon, level_shift=0.1),
        dict(task="energy", structure=silicon, ot_energy_gap=0.002),
        dict(task="geo_opt", structure=silicon),
        dict(task="md", structure=silicon, ensemble="NPT_I", steps=100),
        dict(task="md", structure=silicon, ensemble="NVT", steps=100,
             thermostat_timecon=250.0, barostat_timecon=1000.0),
        dict(task="freq", structure=silicon),
        dict(task="band", structure=silicon, kpoint_grid=(4, 4, 4)),
        dict(task="cell_opt", structure=silicon, external_pressure=5.0),
    ]
    offenders: list[str] = []
    for kwargs in cases:
        built = build(Spec(**kwargs), index)
        for path, keyword, unit in units_in(built.sections):
            message = check(unit)
            if message:
                offenders.append(f"{kwargs['task']}: {path}/{keyword} [{unit}]")
    if offenders:
        failures += 1
        print(f"  FAIL 生成的输入里有 CP2K 不认识单位：{offenders}")
    else:
        print(f"  ok   {len(cases)} 类生成的输入里没有任何非法单位标法")

    # (d) the validator has to catch a hand-written one
    validator = _Validator(db=db, index=index)
    sample = ("&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n"
              "&FORCE_EVAL\n  METHOD QUICKSTEP\n  &DFT\n    &SCF\n"
              "      LEVEL_SHIFT [a.u.] 0.1\n      &OT\n"
              "        ENERGY_GAP [au] 0.002\n      &END OT\n"
              "    &END SCF\n  &END DFT\n&END FORCE_EVAL\n"
              "&MOTION\n  &GEO_OPT\n    MAX_FORCE [a.u.] 4.5E-4\n"
              "  &END GEO_OPT\n&END MOTION\n")
    levels = [d.code for d in validator.run(_parse(sample).sections)]
    if levels.count("unknown-unit") == 3:
        print("  ok   手写 `[a.u.]`/`[au]` 都被校验器点名为 error，  处）")
    else:
        failures += 1
        print(f"  FAIL 校验器只抓到 {levels.count('unknown-unit')} 处非法单体")
    # ... and must stay quiet on the same input without the annotations
    clean = sample.replace(" [a.u.] 0.1", " 0.1").replace(" [au] 0.002", " 0.002") \
                  .replace(" [a.u.] 4.5E-4", " 4.5E-4")
    if "unknown-unit" not in [d.code for d in validator.run(_parse(clean).sections)]:
        print("  ok   去掉标注后不再报（内部单位本来就不该写）")
    else:
        failures += 1
        print("  FAIL 去掉标注后仍在报非法单位")

    # (e) a character keyword with brackets is *not* a unit:
    #     CP2K's own `ELECTRON_CONFIGURATION [Rn] 6d1 7s2`
    noble = ("&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n&ATOM\n  &PRINT\n"
             "  &END PRINT\n  ELECTRON_CONFIGURATION [Rn] 6d1 7s2\n&END ATOM\n")
    codes = [d.code for d in validator.run(_parse(noble).sections)]
    if "unknown-unit" not in codes:
        print("  ok   `ELECTRON_CONFIGURATION [Rn] …` 不被当成单位"
              "（CP2K 只对数值关键字解析 `[...]`）")
    else:
        failures += 1
        print("  FAIL 把电子排布的 `[Rn]` 误判成单体")
    del tempfile
    return failures


def part1_relay(index: DataIndex, db=None) -> int:
    """A CP2K restart as the structure: 179 atoms in, 179 atoms out.

    CP2K writes its restart as a complete input and **removes** ``COORD_FILE_NAME``
    while inlining ``&COORD`` (``input_restart_force_eval.F``), so a relay needs no
    inpcrd.  Two things have to hold for that to work here:

    * the ``&COORD`` labels of an AMBER run are *atom names* (``CA3``, ``OH2``),
      not element symbols - reading them with the element table dropped 127 of the
      179 atoms and invented calcium/holmium;
    * the coordinate-file control must be out of the way, not demanded again.
    """
    import tempfile

    from cp2kbuild.recipes import coordinates_are_inline, reads_as_cp2k_input
    from cp2kbuild.structures import element_from_label
    from cp2kbuild.validate import Validator as _Validator

    print("\n" + "-" * 78)
    print("restart relay: &COORD from a FIST/AMBER restart")
    print("-" * 78)
    failures = 0
    validator = _Validator(db=db, index=index)
    workdir = Path(tempfile.mkdtemp(prefix="cp2kbuild_relay_"))

    # the labels an AMBER topology puts into &COORD
    cases = {"CA3": "C", "CA1": "C", "CT1": "C", "OH2": "O", "OS1": "O",
             "HO3": "H", "HA2": "H", "HC1": "H", "NT1": "N", "HN2": "H",
             "SH1": "S", "CL": "Cl", "NA+": "Na", "CA2": "C", "CA2+": "Ca",
             "Na": "Na", "Cl": "Cl", "Si": "Si"}
    wrong = {label: (want, element_from_label(label, amber=True))
             for label, want in cases.items()
             if element_from_label(label, amber=True) != want}
    if wrong:
        failures += 1
        print(f"  FAIL AMBER 原子名]→ 元素 映射不对：{wrong}")
    else:
        print(f"  ok   {len(cases)} 一 AMBER 原子名都映射对了"
              "（CA3→C、HO3→H、NA+→Na、CA2+→Ca、Cl→Cl 不误判）")

    # the real file when it is on this machine (point CP2KBUILD_TEST_RESTART at it),
    # else a synthetic one - the repository carries no personal paths
    real = Path(os.environ.get("CP2KBUILD_TEST_RESTART", ""))
    if real.is_file():
        source = real
        expected_atoms = 179
        label = "用户那份 GEO_OPT restart"
    else:
        source = workdir / "cp2k-1.restart"
        atoms = "\n".join(f"      CA{i} {i}.0 1.0 2.0" for i in range(1, 21))
        source.write_text(
            "&GLOBAL\n  RUN_TYPE GEO_OPT\n&END GLOBAL\n"
            "&FORCE_EVAL\n  METHOD FIST\n  &MM\n    &FORCEFIELD\n"
            "      PARMTYPE AMBER\n      PARM_FILE_NAME a.prmtop\n"
            "    &END FORCEFIELD\n  &END MM\n  &SUBSYS\n    &CELL\n"
            "      ABC 24.4 24.4 24.4\n      PERIODIC XYZ\n    &END CELL\n"
            "    &COORD\n" + atoms + "\n    &END COORD\n    &TOPOLOGY\n"
            "      CONN_FILE_NAME a.prmtop\n      CONN_FILE_FORMAT AMBER\n"
            "    &END TOPOLOGY\n  &END SUBSYS\n&END FORCE_EVAL\n",
            encoding="utf-8")
        expected_atoms = 20
        label = "合成的_restart"
    structure = read_structure(str(source))
    if structure.n_atoms() == expected_atoms:
        print(f"  ok   {label}：读分 {structure.n_atoms()} 个原子（一个都没丢）")
    else:
        failures += 1
        print(f"  FAIL {label}：读分 {structure.n_atoms()} 个原子，"
              f"应为 {expected_atoms}， COORD 里的名字被元素表吃掉了）")
    odd = sorted({a.symbol for a in structure.atoms} - {"C", "H", "N", "O", "S", "B",
                                                        "Cl", "Na", "Ca", "F", "P"})
    if not odd:
        print("  ok   推断出的元素都在合理范围（没有凭空出现 Ca/Ho 这类）")
    else:
        failures += 1
        print(f"  FAIL 有可疑元素：{odd}")

    # a relay generated from it: &COORD in, COORD_FILE_NAME out
    relay = Spec(task="md", structure=structure, method="FIST", mm_route="AMBER",
                 mm_parm_file="a.prmtop", mm_conn_file="a.prmtop",
                 mm_ewald="SPME", cell=structure.cell, steps=1000,
                 ext_restart_file=source.name, ext_restart_take=("pos",))
    built = build(relay, index)
    text = render_document(built.sections, title="t", notes=[])
    topology_block = text.split("&TOPOLOGY", 1)[1].split("&END TOPOLOGY", 1)[0]
    # a *keyword* line, not the word inside the explanatory comment
    has_coord_keyword = any(line.strip().startswith("COORD_FILE_NAME")
                            for line in topology_block.splitlines())
    if "&COORD" in text and not has_coord_keyword:
        print("  ok   接力输入写的是 `&COORD`，`&TOPOLOGY` 里没有 `COORD_FILE_NAME` 关键子")
    else:
        failures += 1
        print("  FAIL 接力输入里应该只有 &COORD，没有 COORD_FILE_NAME")
    lines = text.splitlines()
    start = next(index for index, line in enumerate(lines)
                 if line.strip() == "&COORD")
    end = next(index for index, line in enumerate(lines[start:], start=start)
               if line.strip() == "&END COORD")
    written = [line for line in lines[start + 1:end] if line.strip()]
    if len(written) == expected_atoms:
        print(f"  ok   `&COORD` 里也是 {expected_atoms} 个原子")
    else:
        failures += 1
        print(f"  FAIL `&COORD` 里写了 {len(written)} 个原子，应为 {expected_atoms}")
    labels_kept = {line.split()[0] for line in written}
    if any(name.startswith(("CA", "OH", "HO", "NT")) for name in labels_kept):
        print("  ok   CP2K 自己的原子名被原样保留（CA3/OH2…），和 restart 一自")
    else:
        print("  note 这份文件里的名字不是 AMBER 类型名，跳过名字保留检查")
    levels = {d.code: d.level for d in validator.run(built.sections)}
    if not [code for code, level in levels.items() if level == "error"]:
        print("  ok   接力输入校验 0 error")
    else:
        failures += 1
        print(f"  FAIL 接力输入有nerror：{levels}")

    # the helper has to tell CP2K restarts apart from AMBER CRD files
    crd = workdir / "system.restart"
    crd.write_text("    179\n   1.0 2.0 3.0\n", encoding="utf-8")
    if reads_as_cp2k_input(str(source)) and not reads_as_cp2k_input(str(crd)):
        print("  ok   `.restart` 按内容区分：CP2K 输入 vs AMBER CRD")
    else:
        failures += 1
        print("  FAIL reads_as_cp2k_input 没把 CP2K restart 和 CRD 分开")
    if coordinates_are_inline(relay):
        print("  ok   结构来自 CP2K 输入/restart 时标记为「坐标已内联」"
              "（界面据此清空坐标文件那一格，控件本身仍可用）")
    else:
        failures += 1
        print("  FAIL coordinates_are_inline 没有认出这份 restart")
    return failures


def part1_classical(index: DataIndex, db) -> int:
    """The classical (FIST) routes: layout, coupling rules and validator."""
    print("\n" + "-" * 78)
    print("classical MD (FORCE_EVAL METHOD FIST)")
    print("-" * 78)
    failures = 0
    water = read_structure(EXAMPLES / "water.xyz")
    cell = Cell.cubic(30.0)

    def check(condition: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'} {label}"
              + (f"  [{detail}]" if detail and not condition else ""))
        if not condition:
            failures += 1

    for route, (parmtype, connectivity, coord_format, coord_file) in \
            CLASSICAL_EXPECTED.items():
        spec = Spec(task="md", method="FIST", structure=water, cell=cell,
                    ensemble="NVT", steps=1000, timestep=1.0, mm_route=route,
                    mm_parm_file="sys.parm", mm_conn_file="sys.top",
                    mm_coord_file=coord_file)
        if route == "PAIR":
            spec.mm_element_lj = {"O": (78.198, 3.166), "H": (0.0, 3.30523)}
        if route == "IN_INPUT":
            spec.mm_charges = [("O", -0.8476), ("H", 0.4238)]
            spec.mm_lj = [MmLennardJones(["O", "O"], 78.198, 3.166, 11.4),
                          MmLennardJones(["O", "H"], 0.0, 3.6705, 11.4),
                          MmLennardJones(["H", "H"], 0.0, 3.30523, 11.4)]
        result = build(spec, index)
        text = render_document(result.sections)
        force_eval = result.sections[[s.name for s in result.sections].index("FORCE_EVAL")]
        forcefield = force_eval.find("MM/FORCEFIELD")
        topology = force_eval.find("SUBSYS/TOPOLOGY")
        check(force_eval.get("METHOD") == "FIST",
              f"{route}: METHOD FIST")
        check(force_eval.subsection("DFT") is None,
              f"{route}: no &DFT section (CP2K aborts if it is there)")
        check(str(forcefield.get("PARMTYPE")) == parmtype,
              f"{route}: PARMTYPE {parmtype}", str(forcefield.get("PARMTYPE")))
        check(str(topology.get("CONNECTIVITY", topology.get("CONN_FILE_FORMAT")))
              == connectivity,
              f"{route}: CONNECTIVITY {connectivity}")
        if route in ("IN_INPUT", "PAIR"):
            check(topology.get("COORD_FILE_NAME") is None
                  and force_eval.find("SUBSYS/COORD") is not None,
                  f"{route}: coordinates come from the inline &COORD")
        else:
            got = topology.get("COORDINATE", topology.get("COORD_FILE_FORMAT"))
            check(str(got) == coord_format,
                  f"{route}: COORDINATE {coord_format}（按文件后缀自动判）", str(got))
        check("STRESS_TENSOR" not in text or route not in ("AMBER",),
              f"{route}: no stray STRESS_TENSOR")
        diagnostics = Validator(db, index).run(
            parse_input(text).sections)
        errors = [d.code for d in diagnostics if d.level == "error"]
        check(not errors, f"{route}: validator reports no error", str(errors))

    # ---- the pieces that are easy to get subtly wrong --------------------- #
    npt = build(Spec(task="md", method="FIST", structure=water, cell=cell,
                     ensemble="NPT_I", steps=1000, mm_route="AMBER",
                     mm_parm_file="a.prmtop", mm_conn_file="a.prmtop",
                     mm_coord_file="a.inpcrd"), index)
    npt_text = render_document(npt.sections)
    check("STRESS_TENSOR ANALYTICAL" in npt_text,
          "NPT writes STRESS_TENSOR ANALYTICAL (CP2K's default is NONE)")
    check("VDW_SCALE14 0.5" in npt_text and "EI_SCALE14 0.833333333333" in npt_text,
          "AMBER gets its PARM94 1-4 scaling, not CP2K's EI_SCALE14 0.0")
    paths = {path for path, _ in npt.sections[0].walk()}
    for root in npt.sections[1:]:
        paths.update(path for path, _ in root.walk())
    check(not any(path.endswith(("DFT", "SCF", "QS", "MGRID", "KPOINTS"))
                  for path in paths),
          "a FIST input has no &DFT/&SCF/&QS/&MGRID/&KPOINTS in its tree",
          str(sorted(p for p in paths if "DFT" in p or "SCF" in p))[:80])
    check("EWALD_TYPE SPME" in npt_text,
          "a periodic cell gets SPME")
    check("&RESTART ON" in npt_text and "ADD_LAST NUMERIC" in npt_text,
          "the run restart file is still requested on the classical route")

    vacuum = build(Spec(task="md", method="FIST", structure=water,
                        vacuum=12.0, ensemble="NVE", steps=100, mm_route="AMBER",
                        mm_parm_file="a.prmtop", mm_conn_file="a.prmtop",
                        mm_coord_file="a.inpcrd"), index)
    check("EWALD_TYPE NONE" in render_document(vacuum.sections),
          "a non-periodic cell falls back to EWALD_TYPE NONE")

    mixed = build(Spec(task="md", method="FIST", structure=water, cell=cell,
                       ensemble="NVT", steps=100, mm_route="AMBER",
                       mm_parm_file="mix.prmtop", mm_coord_file="mix.inpcrd",
                       mm_molecules=[MmMolecule("mol1.prmtop", "AMBER", 100),
                                     MmMolecule("mol2.prmtop", "AMBER", 50)]),
                  index)
    mixed_force_eval = next(s for s in mixed.sections
                            if s.name == "FORCE_EVAL")
    topology = mixed_force_eval.find("SUBSYS/TOPOLOGY")
    molecule_blocks = topology.find("MOL_SET").subsections("MOLECULE")
    check(str(topology.get("CONNECTIVITY")) == "MOL_SET"
          and len(molecule_blocks) == 2
          and str(molecule_blocks[0].get("NMOL")) == "100",
          "mixed system: CONNECTIVITY MOL_SET with one &MOLECULE per component")
    check(topology.get("CONN_FILE_NAME") is None,
          "mixed system: no top-level CONN_FILE_NAME (each &MOLECULE has its own)")

    # ---- the validator has to catch the classical mistakes ---------------- #
    # NB: METHOD is a *keyword* inside &FORCE_EVAL, not a section parameter -
    # "&FORCE_EVAL METHOD FIST" would paste the whole thing into the section
    # parameter and CP2K would never see a METHOD at all.
    def fist(body: str) -> str:
        return ("&FORCE_EVAL\n METHOD FIST\n" + body + "&END FORCE_EVAL")

    mistake_cases = [
        ("PARMTYPE AMBER without PARM_FILE_NAME", "mm-parmtype-without-file",
         fist(" &MM\n  &FORCEFIELD\n   PARMTYPE AMBER\n  &END FORCEFIELD\n"
              " &END MM\n")),
        ("FIST without &MM", "fist-without-mm", fist("")),
        ("FIST with a &DFT section", "method-mismatch",
         fist(" &DFT\n &END DFT\n &MM\n  &FORCEFIELD\n   PARMTYPE OFF\n"
              "  &END FORCEFIELD\n &END MM\n")),
        ("AMBER without EI_SCALE14", "mm-ei-scale14-default",
         fist(" &MM\n  &FORCEFIELD\n   PARMTYPE AMBER\n   PARM_FILE_NAME a.prmtop\n"
              "  &END FORCEFIELD\n &END MM\n &SUBSYS\n  &TOPOLOGY\n"
              "   CONNECTIVITY AMBER\n   CONN_FILE_NAME a.prmtop\n"
              "   COORDINATE CRD\n   COORD_FILE_NAME a.inpcrd\n"
              "  &END TOPOLOGY\n &END SUBSYS\n")),
        ("CONNECTIVITY AMBER without a file", "mm-connectivity-without-file",
         fist(" &MM\n  &FORCEFIELD\n   PARMTYPE OFF\n  &END FORCEFIELD\n &END MM\n"
              " &SUBSYS\n  &TOPOLOGY\n   CONNECTIVITY AMBER\n  &END TOPOLOGY\n"
              " &END SUBSYS\n")),
        ("MOL_SET without &MOLECULE", "mm-mol-set-empty",
         fist(" &MM\n  &FORCEFIELD\n   PARMTYPE OFF\n  &END FORCEFIELD\n &END MM\n"
              " &SUBSYS\n  &TOPOLOGY\n   CONNECTIVITY MOL_SET\n  &END TOPOLOGY\n"
              " &END SUBSYS\n")),
        ("&LENNARD-JONES without EPSILON", "mm-lj-without-parameters",
         fist(" &MM\n  &FORCEFIELD\n   PARMTYPE OFF\n   &NONBONDED\n"
              "    &LENNARD-JONES\n     ATOMS Ar Ar\n    &END LENNARD-JONES\n"
              "   &END NONBONDED\n  &END FORCEFIELD\n &END MM\n")),
        ("NPT without a stress tensor", "npt-without-stress-tensor",
         "&GLOBAL\n RUN_TYPE MD\n&END GLOBAL\n&MOTION\n &MD\n  ENSEMBLE NPT_I\n"
         " &END MD\n&END MOTION\n"
         + fist(" &MM\n  &FORCEFIELD\n   PARMTYPE OFF\n  &END FORCEFIELD\n &END MM\n"
                " &SUBSYS\n  &CELL\n   ABC 20 20 20\n   PERIODIC XYZ\n"
                "  &END CELL\n &END SUBSYS\n")),
    ]
    for label, code, body in mistake_cases:
        found = {d.code: d.level
                 for d in Validator(db, index).run(parse_input(body).sections)}
        check(found.get(code) in ("error", "warning"),
              f"validator catches: {label}", str(sorted(found)))

    # ---- nothing on the Quickstep route may claim to be classical --------- #
    quickstep = render_document(build(
        Spec(task="md", structure=water, vacuum=12.0, steps=100), index).sections)
    check("&MM" not in quickstep and "PARMTYPE" not in quickstep,
          "the default route still writes &DFT and no &MM")

    # ---- the CLI refuses a cutoff scan on a force field ------------------- #
    from cp2kbuild import cli as cli_module
    quiet_argv = ["new", "--task", "md", "--method", "fist",
                  "-s", str(EXAMPLES / "water.xyz"), "--stdout"]
    try:
        cli_module.main(quiet_argv + ["--quiet", "--no-validate"])
        cli_ok = True
    except SystemExit as exc:                       # pragma: no cover
        cli_ok = exc.code in (0, None)
    check(cli_ok, "CLI: cp2kbuild new --method fist works")
    failures += part1_mmcheck(index, db)
    return failures


#: Minimal but format-faithful fixtures for the ``mmcheck`` tests.  They are
#: written out and read back, so the readers are exercised on the real syntax
#: (``%FLAG``/``%FORMAT`` for a prmtop, the ``!NATOM`` window for a PSF).
FIXTURE_PRMTOP = """\
%VERSION  VERSION_STAMP = V0001.000  DATE = 01/01/01  00:00:00
%FLAG TITLE
%FORMAT(20a4)
TEST
%FLAG POINTERS
%FORMAT(10I8)
       2       1       0       0       0       0       0       0       0       0
%FLAG ATOM_NAME
%FORMAT(20a4)
C1  H1
"""

FIXTURE_PSF = """\
PSF

       1 !NTITLE
 REMARKS fixture

       2 !NATOM
       1 PROA 1    ALA  N    NH3   -0.300000      14.0070           0
       2 PROA 1    ALA  HT1  HC     0.300000       1.0080           0

       0 !NBOND: bonds
"""

FIXTURE_PDB = """\
CRYST1   20.000   20.000   20.000  90.00  90.00  90.00 P 1           1
ATOM      1  C1  ALA A   1       0.000   0.000   0.000  1.00  0.00           C
ATOM      2  H1  ALA A   1       1.000   0.000   0.000  1.00  0.00           H
END
"""

FIXTURE_INPCRD = """\
test
       2   0.0000000
   0.0000000   0.0000000   0.0000000   1.0000000   0.0000000   0.0000000
  20.0000000  20.0000000  20.0000000  90.0000000  90.0000000  90.0000000
"""

FIXTURE_XYZ = """\
2
test
C 0.000 0.000 0.000
H 1.000 0.000 0.000
"""


def part1_mmcheck(index: DataIndex, db=None) -> int:
    """``cp2kbuild mmcheck``: does the coordinate file match the topology?"""
    import tempfile
    from pathlib import Path as _Path

    from cp2kbuild.mmcheck import (check_mixture, check_single, read_inpcrd,
                                   read_prmtop, read_psf)
    from cp2kbuild.recipes import MmMolecule

    print("\n" + "-" * 78)
    print("mmcheck: coordinate file vs topology file(s)")
    print("-" * 78)
    failures = 0

    def check(condition: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        print(f"  {'ok  ' if condition else 'FAIL'} {label}"
              + (f"  [{detail}]" if detail and not condition else ""))
        if not condition:
            failures += 1

    work = _Path(tempfile.mkdtemp(prefix="cp2kbuild_mmcheck_"))
    prmtop = work / "mol.prmtop"
    prmtop.write_text(FIXTURE_PRMTOP, encoding="utf-8")
    psf = work / "mol.psf"
    psf.write_text(FIXTURE_PSF, encoding="utf-8")
    pdb = work / "good.pdb"
    pdb.write_text(FIXTURE_PDB, encoding="utf-8")
    inpcrd = work / "good.inpcrd"
    inpcrd.write_text(FIXTURE_INPCRD, encoding="utf-8")
    xyz = work / "good.xyz"
    xyz.write_text(FIXTURE_XYZ, encoding="utf-8")

    info = read_prmtop(prmtop)
    check(info.natom == 2 and info.names == ["C1", "H1"],
          "prmtop：读出 NATOM 一 ATOM_NAME， FLAG/%FORMAT 解析，)",
          str(info))
    info = read_psf(psf)
    check(info.natom == 2 and info.names == ["N", "HT1"],
          "PSF：读出 !NATOM 与原子名", str(info))
    check(read_inpcrd(inpcrd).box is not None,
          "inpcrd：认出坐标后面的盒子行")
    no_box = work / "nobox.inpcrd"
    no_box.write_text(FIXTURE_INPCRD.replace(
        "  20.0000000  20.0000000  20.0000000  90.0000000  90.0000000  90.0000000\n",
        ""), encoding="utf-8")
    check(read_inpcrd(no_box).box is None, "inpcrd：没有盒子行时不乱认")

    good = check_single(prmtop, pdb)
    check(good.ok and good.expected_atoms == 2,
          "单拓手 + 匹配的 PDB：通过", good.text())
    check(any("会被 CP2K 忽略" in note for note in good.notes),
          "报告里指出坐标文件里的盒子会被 CP2K 忽略（必须写 &CELL）e")

    # a four-atom PDB, so that a two-molecule list has something to match
    four = work / "four.pdb"
    four.write_text(FIXTURE_PDB.replace("END\n", "") + (
        "ATOM      3  N   ALA A   1       2.000   0.000   0.000  1.00  0.00           N\n"
        "ATOM      4  HT1 ALA A   1       3.000   0.000   0.000  1.00  0.00           H\n"
        "END\n"), encoding="utf-8")

    short = work / "extra.pdb"
    short.write_text(FIXTURE_PDB + (
        "ATOM      3  H2  ALA A   1       2.000   0.000   0.000  1.00  0.00           H\n"),
        encoding="utf-8")
    bad = check_single(prmtop, short)
    check(not bad.ok and "原子数对不上" in bad.problems[0],
          "原子数不一自--> 报错（CP2K 是「不相等就 abort」）", bad.text())

    two = check_mixture([MmMolecule(str(prmtop), "AMBER", 1),
                         MmMolecule(str(psf), "PSF", 1)], four, "PDB")
    check(two.ok and two.expected_atoms == 4 == two.blocks[1].first_atom + 1,
          "两个 &MOLECULE：第二块从第 3 个原子开始", two.text())

    wrong = check_mixture([MmMolecule(str(prmtop), "AMBER", 3)], pdb, "PDB")
    check(not wrong.ok and "相差 -4" in wrong.problems[0],
          "NMOL 填错 -> 报出差额", wrong.text())

    renamed = work / "renamed.pdb"
    renamed.write_text(FIXTURE_PDB.replace(" C1 ", " CT1").replace(" H1 ", " HC1"),
                       encoding="utf-8")
    renamed_report = check_single(prmtop, renamed)
    check(renamed_report.ok,
          "原子名不名 *不算锛 *（CP2K 按位置分配，官方 multi_psf 就是这样的）",
          renamed_report.text())
    check(any("这本身不是错" in note for note in renamed_report.notes),
          "但会把名字差异作为提示说出来")

    xyz_report = check_single(prmtop, xyz)
    check(xyz_report.ok and any("XYZ" in note for note in xyz_report.notes),
          "XYZ 坐标：只核对数量并说明原因", xyz_report.text())

    # a packmol-shaped PDB: every molecule gets residue UNL, renumbered from 1,
    # and the chain letter changes - the block sizes are the only visible clue
    # about the composition
    packmol = work / "packmol.pdb"
    rows = ["CRYST1   30.000   30.000   30.000  90.00  90.00  90.00 P 1           1"]
    serial = 0
    for resid in range(1, 4):                     # three "molecules" of 2 atoms
        for offset in range(2):
            serial += 1
            rows.append(f"HETATM{serial:5d}  C{offset}  UNL A{resid:4d}      "
                        f"{serial:8.3f}   0.000   0.000  1.00  0.00           C")
    rows.append("END")
    packmol.write_text("\n".join(rows) + "\n", encoding="utf-8")
    blocks = check_mixture([MmMolecule(str(prmtop), "AMBER", 3)], packmol, "PDB")
    check(blocks.ok and any("分成这些段：2, 2, 2" in note for note in blocks.notes),
          "packmol 形状的 PDB：报出残基段大小，), 2, 2 = 3 个分子）",
          blocks.text())
    check(any("不一定等于分子边界" in note for note in blocks.notes),
          "同时说明段边界只是线索（packmol 每分子都写 UNL 并重新编号）")

    # the same block structure is what the GUI fills the &MOL_SET table from
    from cp2kbuild.mmcheck import detect_components, read_coordinates, suggest_molecules

    detected, notes = detect_components(read_coordinates(packmol, "PDB"))
    check([(c.natom, c.count) for c in detected] == [(2, 3)] and not notes,
          "识别组成，  一 2 原子分子", str(detected))
    check(suggest_molecules(detected) == ["--mol AMBER:<2 原子的拓扑文件>:3"],
          "给出可直接粘贴的 --mol 行", str(suggest_molecules(detected)))
    # 交错顺序 MOL_SET 表达不了，必须点名 
    interleaved = work / "inter.pdb"
    rows = ["CRYST1   30.000   30.000   30.000  90.00  90.00  90.00 P 1           1"]
    serial = 0
    for block_index, size in enumerate((3, 2, 3, 2)):
        for offset in range(size):
            serial += 1
            rows.append(f"HETATM{serial:5d}  C{offset}   UNL A{block_index + 1:4d}      "
                        f"{float(serial):8.3f}   0.000   0.000  1.00  0.00           C")
    rows.append("END")
    interleaved.write_text("\n".join(rows) + "\n", encoding="utf-8")
    inter, inter_notes = detect_components(read_coordinates(interleaved, "PDB"))
    check([(c.natom, c.count, c.runs) for c in inter] == [(3, 2, 2), (2, 2, 2)]
          and inter_notes and not all(c.grouped for c in inter),
          "交错的段顺序会被点名， MOL_SET 表达不了，)", str(inter_notes))

    # COORDINATE 与文件后缀不匹配，两个方向都要把 
    from cp2kbuild.validate import Validator as _Validator
    from cp2kbuild.parser import parse_input as _parse

    def mismatch(coord_format: str, coord_file: str) -> str | None:
        body = ("&FORCE_EVAL\n METHOD FIST\n &MM\n  &FORCEFIELD\n   PARMTYPE AMBER\n"
                "   PARM_FILE_NAME a.prmtop\n   EI_SCALE14 0.8333\n  &END FORCEFIELD\n"
                " &END MM\n &SUBSYS\n  &TOPOLOGY\n   CONNECTIVITY AMBER\n"
                "   CONN_FILE_NAME a.prmtop\n"
                f"   COORDINATE {coord_format}\n   COORD_FILE_NAME {coord_file}\n"
                "  &END TOPOLOGY\n &END SUBSYS\n&END FORCE_EVAL")
        found = [d for d in Validator(db, index).run(_parse(body).sections)
                 if d.code == "mm-coord-format-suspect"]
        return found[0].message if found else None

    check(mismatch("CRD", "mix.inpcrd") is None, "COORDINATE CRD + inpcrd：不报警")
    check(mismatch("PDB", "mix.pdb") is None, "COORDINATE PDB + pdb：不报警")
    check(mismatch("CRD", "mix.pdb") is not None, "COORDINATE CRD + pdb：报警")
    check(mismatch("PDB", "mix.inpcrd") is not None, "COORDINATE PDB + inpcrd：报警")
    check(mismatch("PDB", "mix.unknown") is None, "后缀认不出来时不猜")

    # ---- 坐标：没有 = error，两套 = warning ------------------------------ #
    def levels(body: str, code: str) -> list[str]:
        parsed = _parse(body)
        return [d.level for d in Validator(db, index).run(parsed.sections,
                                                          parsed.directives)
                if d.code == code]

    periodic_cell = ("&FORCE_EVAL\n METHOD QUICKSTEP\n &SUBSYS\n  &CELL\n"
                     "   ABC 20 20 20\n   PERIODIC XYZ\n  &END CELL\n")
    check(levels(periodic_cell + "&END SUBSYS\n&END FORCE_EVAL",
                 "no-coordinates") == ["error"],
          "周期体系里既没有 &COORD 也没有坐标文件 → error")
    check(levels(periodic_cell + "  @INCLUDE coords.inc\n&END SUBSYS\n&END FORCE_EVAL",
                 "no-coordinates") == ["info"],
          "...但输入里有 @INCLUDE 时降为 info（坐标可能写在被包含的文件里）")
    check(not levels(periodic_cell + "  &TOPOLOGY\n   COORD_FILE mix.xyz\n"
                     "  &END TOPOLOGY\n&END SUBSYS\n&END FORCE_EVAL",
                     "no-coordinates"),
          "CP2K 自己的短写法 COORD_FILE（46 个官方输入这么写）不算没有坐标")
    both = (periodic_cell + "  &TOPOLOGY\n   COORD_FILE_NAME mix.inpcrd\n"
            "  &END TOPOLOGY\n  &COORD\n   O 0 0 0\n  &END COORD\n"
            "&END SUBSYS\n&END FORCE_EVAL")
    check(levels(both, "coord-and-file") == ["warning"],
          "同时给了 &COORD 和坐标文件（两套坐标）→ warning")
    both_hint = [d.hint for d in Validator(db, index).run(_parse(both).sections)
                 if d.code == "coord-and-file"]
    check(both_hint and "覆盖" in (both_hint[0] or ""),
          f"两套坐标的提示说清哪一套生效：{(both_hint or [''])[0][:60]!r}")

    # with &MOL_SET the AMBER parameters still come from PARM_FILE_NAME alone
    # (the per-molecule topologies are read with do_forcefield=FALSE), so a
    # component's own prmtop is not a valid parameter file for another component
    def mol_set_body(parm_file: str, molecules: list[tuple[str, int]]) -> str:
        blocks = "".join(
            "   &MOLECULE\n    CONNECTIVITY AMBER\n"
            f"    CONN_FILE_NAME {path}\n    NMOL {count}\n   &END MOLECULE\n"
            for path, count in molecules)
        return ("&FORCE_EVAL\n METHOD FIST\n &MM\n  &FORCEFIELD\n   PARMTYPE AMBER\n"
                f"   PARM_FILE_NAME {parm_file}\n   EI_SCALE14 0.8333\n"
                "  &END FORCEFIELD\n &END MM\n &SUBSYS\n  &TOPOLOGY\n"
                "   CONNECTIVITY MOL_SET\n   COORDINATE PDB\n"
                "   COORD_FILE_NAME mix.pdb\n   &MOL_SET\n" + blocks
                + "   &END MOL_SET\n  &END TOPOLOGY\n &END SUBSYS\n&END FORCE_EVAL")

    def coverage(path: str, molecules: list[tuple[str, int]]) -> str | None:
        found = [d for d in Validator(db, index).run(
            _parse(mol_set_body(path, molecules)).sections)
            if d.code == "mm-parm-file-covers-all-components"]
        return found[0].level if found else None

    check(coverage("lamol.top", [("lamol.top", 4)]) is None,
          "PARM_FILE_NAME 与唯一的组分拓扑相同（官方 lamol 模式）：不提示")
    check(coverage("a.prmtop", [("a.prmtop", 1), ("b.prmtop", 20)]) == "warning",
          "两个组分却把 PARM_FILE_NAME 指向其中一个：warning")
    check(coverage("all.prmtop", [("a.prmtop", 1), ("b.prmtop", 20)]) == "info",
          "PARM_FILE_NAME 是第三个（合并）文件：info，提醒确认覆盖所有组分")

    # ---- a file-based force field needs no structure file at all ---------- #
    no_structure = build(Spec(task="md", method="FIST", mm_route="AMBER",
                              structure=None, cell=Cell.cubic(24.4),
                              mm_parm_file="mix.prmtop",
                              mm_coord_file="mixture.pdb", steps=1000), index)
    text = render_document(no_structure.sections, notes=no_structure.notes)
    check("&CELL" in text and "ABC [angstrom] 24.4" in text,
          "没有结构文件时，晶胞来自 --cell / 界面上的「晶胞覆盖」")
    check("&COORD" not in text,
          "没有结构文件也照样生成（原子来自 COORD_FILE_NAME，不写 &COORD） ")
    check("原子来自坐标文件" in text,
          "头部说明原子由坐标文件决定，并指名 mmcheck")
    check(no_structure.warnings == [] or all("在这里不存在" in w
                                             for w in no_structure.warnings),
          "没有结构文件本身不产生额外警周", str(no_structure.warnings))
    no_cell = build(Spec(task="md", method="FIST", mm_route="AMBER",
                         structure=None, mm_parm_file="mix.prmtop",
                         mm_coord_file="mixture.crd", steps=1000), index)
    # CP2K cannot run this one: with no CELL_FILE_NAME/ABC/A,B,C it looks for the
    # box inside the coordinate file, and for an AMBER CRD that road is closed -
    # topology_amber.F ignores the BOX it finds ("BOX information found in CRD
    # file. They will be ignored.") and cell_methods.F aborts on a format it
    # cannot parse for a cell.  So it is an **error**, exactly like the
    # validator's `periodic-without-cell` - it used to be a warning here and an
    # error there, i.e. one fact printed twice at two levels.
    check(any("没有 &CELL" in e for e in no_cell.errors),
          "没有结构也没有晶胞、坐标是 CRD 时点名缺 &CELL，而且报成 error",
          str(no_cell.errors))
    check("periodic-without-cell" in no_cell.covered_codes,
          "这条 error 覆盖了校验器的 periodic-without-cell，"
          "合起来的报告不会再出现两遍", str(no_cell.covered_codes))
    check("&COORD" not in render_document(no_cell.sections),
          "缺晶胞时也不会退回写 &COORD")
    # a PDB *can* carry the box (CRYST1), and CP2K reads it (cell_methods.F
    # "case 4" lists PDB) - so the very same situation is a warning there, and
    # the message has to say which way it can go
    no_cell_pdb = build(Spec(task="md", method="FIST", mm_route="AMBER",
                             structure=None, mm_parm_file="mix.prmtop",
                             mm_coord_file="mixture.pdb", steps=1000), index)
    check(not no_cell_pdb.errors
          and any("找盒子" in w for w in no_cell_pdb.warnings),
          "坐标是 PDB 时同一条改成 warning（CP2K 会去读 CRYST1）",
          str(no_cell_pdb.warnings))
    check("periodic-without-cell" in no_cell_pdb.covered_codes,
          "PDB 那条也覆盖了 periodic-without-cell（一种事实一个说法）",
          str(no_cell_pdb.covered_codes))
    no_parm = build(Spec(task="md", method="FIST", mm_route="AMBER",
                         structure=None, cell=Cell.cubic(24.4),
                         mm_coord_file="mixture.crd", steps=1000), index)
    check(any("PARM_FILE_NAME 是空的" in e for e in no_parm.errors),
          "AMBER 路线没给参数文件 → error（CP2K 直接中止）", str(no_parm.errors))
    check("mm-parmtype-without-file" in no_parm.covered_codes
          and "mm-connectivity-without-file" in no_parm.covered_codes,
          "这两条 error 覆盖了校验器的同名规则（一份事实只说一次）",
          str(no_parm.covered_codes))
    check(not any("no PARM_FILE_NAME" in w or "no CONN_FILE_NAME" in w
                  for w in no_parm.warnings),
          "同样的句子不会再以 warning 的身份出现一次", str(no_parm.warnings))

    # ---- 坐标从哪来：文件与 &COORD 互斥，来源不同要核对 ----------------- #
    # 一个真实问题：「FIST 下拖进来的 restart 和指定的 inpcrd 同时存在坐标，
    # 工具怎么处理？」——生成端只写 COORD_FILE_NAME（不写 &COORD），
    # 而 &CELL 仍来自载入的结构，于是位置和盒子可能来自两份不同的东西。
    from cp2kbuild.recipes import coordinate_source_warnings
    from cp2kbuild.structures import read_structure as _read_structure

    # 3 个原子 + 一个 20 Å 的盒子（定宽卡片格式；盒子行按 AMBER 的规矩给 6 个数：
    # a b c α β γ —— 只给 3 个数 CP2K 的 read_coordinate_crd 也不认）
    three = work / "three.inpcrd"
    three.write_text(
        "three atoms\n"
        "     3\n"
        "   0.0000000   0.0000000   0.0000000   0.0000000   0.0000000   0.9572000\n"
        "   0.7570000   0.0000000   0.5865000\n"
        "  20.0000000  20.0000000  20.0000000  90.0000000  90.0000000  90.0000000\n",
        encoding="utf-8")
    coord_info = read_inpcrd(three)
    check(coord_info.natom == 3 and coord_info.box == (20.0, 20.0, 20.0),
          f"测试用 inpcrd 读得出 3 个原子 + 20 Å 盒子：{coord_info.natom} "
          f"{coord_info.box}")

    water_like = _read_structure(xyz)          # 2 个原子的结构文件
    count_clash = Spec(task="geo_opt", method="FIST", mm_route="AMBER",
                       structure=water_like, mm_parm_file="mix.prmtop",
                       mm_coord_file=str(three))
    count_notes = coordinate_source_warnings(count_clash, water_like)
    check(any("个原子" in note and "不一致" in note for note in count_notes),
          f"坐标文件与结构的原子数不同 → 警告：{count_notes[:1]}")
    check(coordinate_source_warnings(Spec(
        task="geo_opt", method="FIST", mm_route="AMBER", structure=water_like,
        mm_parm_file="mix.prmtop",
        mm_coord_file=getattr(water_like, "source", None)), water_like) == [],
        "结构文件本身就是那个坐标文件时**不**误报（GUI 拖 pdb 的常规路径）")

    # restart（CP2K 输入）当结构 + 另外的坐标文件 = 位置与盒子两份来源
    restart_path = work / "restart_like.inp"
    restart_path.write_text(
        "&GLOBAL\n  RUN_TYPE MD\n&END GLOBAL\n&FORCE_EVAL\n  METHOD FIST\n"
        "  &SUBSYS\n    &CELL\n      ABC 24.4 24.4 24.4\n    &END CELL\n    &COORD\n"
        "      O 0.0 0.0 0.0\n      H 0.0 0.0 0.9572\n      H 0.757 0.0 0.5865\n"
        "    &END COORD\n  &END SUBSYS\n&END FORCE_EVAL\n", encoding="utf-8")
    restart_structure = _read_structure(restart_path)
    split = Spec(task="geo_opt", method="FIST", mm_route="AMBER",
                 structure=restart_structure, mm_parm_file="mix.prmtop",
                 mm_coord_file=str(three))
    split_notes = coordinate_source_warnings(split, restart_structure)
    split_text = render_document(build(split, index).sections)
    check(any("restart" in note and "&CELL" in note for note in split_notes),
          f"restart 当结构 + 另给坐标文件 → 说明位置/盒子两份来源："
          f"{split_notes[:1]}")
    check(any("盒子" in note and "不一致" in note for note in split_notes),
          f"inpcrd 的 box 与 &CELL 不同 → 点出优先级：{split_notes[-1:]}")
    check("COORD_FILE_NAME" in split_text and "&COORD" not in split_text
          and "ABC [angstrom] 24.4" in split_text,
          "生成的输入：坐标用文件、盒子用结构（不写 &COORD）")

    # inpcrd 当结构必须给出人话错误，而不是 POSCAR 解析崩掉
    try:
        _read_structure(three)
        failures += 1
        print("  FAIL inpcrd 当结构没有报错")
    except ValueError as error:
        if "只有坐标、没有元素名" in str(error):
            print("  ok   inpcrd 当结构文件 → 明确说它没有元素名、该怎么办")
        else:
            failures += 1
            print(f"  FAIL inpcrd 的错误信息不对：{error}")

    # 真实反馈：这两条不能只是警告——没有坐标 CP2K 直接中止，所以它是**错误**；
    # 而「坐标文件与结构的原子数不一致」保持警告（用户确认过）。
    no_atoms = build(Spec(task="md", method="FIST", mm_route="AMBER",
                          structure=None, cell=Cell.cubic(24.4),
                          mm_parm_file="mix.prmtop", mm_conn_file="mix.prmtop",
                          steps=1000), index)
    check(any("一个原子都没有" in text for text in no_atoms.errors),
          f"既没有坐标文件也没有结构 → 生成期 error：{no_atoms.errors[:1]}")
    check(not any("一个原子都没有" in text for text in no_atoms.warnings),
          "同一条不再同时出现在 warnings 里")
    no_atoms_subsys = next(sub for force_eval in no_atoms.sections
                           if force_eval.name.upper() == "FORCE_EVAL"
                           for sub in force_eval.sections
                           if sub.name.upper() == "SUBSYS")
    check(no_atoms_subsys.subsection("COORD") is None
          and no_atoms_subsys.subsection("CELL") is not None,
          "这时不写空的 &COORD（写了等于 0 个原子），但 &CELL 照写")
    mismatch_levels = build(Spec(task="geo_opt", method="FIST", mm_route="AMBER",
                                 structure=water_like, cell=Cell.cubic(24.4),
                                 mm_parm_file="mix.prmtop",
                                 mm_coord_file=str(three)), index)
    check(any("个原子" in text for text in mismatch_levels.warnings)
          and not any("个原子" in text for text in mismatch_levels.errors),
          "坐标文件与结构原子数不一致仍是**警告**（不是错误）")

    # the CLI accepts that shape too, and still refuses a structure-less DFT run
    from cp2kbuild import cli as _cli

    code = _cli.main(["new", "--task", "md", "--method", "fist",
                      "--mm-route", "AMBER", "--mm-parm-file", "mix.prmtop",
                      "--mm-coord-file", "mixture.pdb", "--cell", "24.4,24.4,24.4",
                      "--stdout", "--quiet", "--no-validate"])
    check(code == 0, "CLI：力场路线可以不给 --structure（只要有 --cell）e", str(code))
    code = _cli.main(["new", "--task", "md", "--method", "fist",
                      "--mm-route", "AMBER", "--mm-parm-file", "mix.prmtop",
                      "--mm-coord-file", "mixture.pdb",
                      "--stdout", "--quiet", "--no-validate"])
    check(code == 2, "CLI：力场路线缺 --cell 时拒给", str(code))
    code = _cli.main(["new", "--task", "energy", "--stdout", "--quiet",
                      "--no-validate"])
    check(code == 2, "CLI：DFT 任务仍然必须要---structure", str(code))

    # ---- how the force-field file names are spelled ----------------------- #
    # The input is generated on Windows and run on a Linux cluster, so an
    # absolute Windows path inside it is useless; when the files sit next to the
    # input the bare name is what has to be written.
    from cp2kbuild.recipes import mm_path_for

    here = work / "run"
    here.mkdir(exist_ok=True)
    elsewhere = work / "ff"
    elsewhere.mkdir(exist_ok=True)
    local = here / "mol.prmtop"
    local.write_text(FIXTURE_PRMTOP, encoding="utf-8")
    far = elsewhere / "mol.prmtop"
    far.write_text(FIXTURE_PRMTOP, encoding="utf-8")

    def spelled(style: str, path, output_dir) -> str | None:
        return mm_path_for(Spec(mm_path_style=style, output_dir=str(output_dir)),
                           str(path))

    check(spelled("auto", local, here) == "mol.prmtop",
          "auto：文件就在输入旁输 -> 只写文件名（Windows 路径带不过去）)",
          str(spelled("auto", local, here)))
    check(spelled("auto", far, here) == far.as_posix(),
          "auto：文件在别的目录 -> 照原样写路径，不会静默改坐",
          str(spelled("auto", far, here)))
    check(spelled("name", far, here) == "mol.prmtop",
          "name：无论如何都只写文件名")
    check(spelled("absolute", local, here) == local.as_posix(),
          "absolute：无论如何都写完整路径")
    check(spelled("auto", local, None) == local.as_posix(),
          "auto 且不知道输入会写到哪（纯库调用）-> 不猜，照原样写")
    check(mm_path_for(Spec(), str(far)) == "mol.prmtop"
          and Spec().mm_path_style == "name",
          "**默认就是 name**：文件在别的目录里也只写文件名",
          str(mm_path_for(Spec(), str(far))))

    # the same directory seen from the CLI's -o
    out = here / "run.inp"
    code = _cli.main(["new", "--task", "md", "--method", "fist", "--mm-route",
                      "AMBER", "--mm-parm-file", str(local),
                      "--mm-coord-file", str(here / "mix.pdb"),
                      "--cell", "24.4,24.4,24.4", "-o", str(out), "--quiet",
                      "--no-validate"])
    written = out.read_text(encoding="utf-8")
    check(code == 0 and "PARM_FILE_NAME mol.prmtop" in written
          and "CONN_FILE_NAME mol.prmtop" in written,
          "CLI -o 与文件同目录时写出的是纯文件名", written[:0])
    check(str(here) not in written.replace("\\", "/"),
          "写出的输入里不出现那台机器的绝对路径")
    code = _cli.main(["new", "--task", "md", "--method", "fist", "--mm-route",
                      "AMBER", "--mm-parm-file", str(far),
                      "--mm-coord-file", str(here / "mix.pdb"),
                      "--cell", "24.4,24.4,24.4", "-o", str(out),
                      "--mm-path-style", "absolute", "--quiet", "--no-validate"])
    check(code == 0 and far.as_posix() in out.read_text(encoding="utf-8"),
          "--mm-path-style absolute 时保留完整路径")

    missing = check_single(prmtop, work / "nope.pdb")
    check(not missing.ok, "坐标文件不存在--> 报错而不是抛异常")

    # the command line front end, on the same fixtures
    from cp2kbuild import cli as cli_module
    code = cli_module.main(["mmcheck", "--conn-file", str(prmtop),
                            "--coord", str(pdb), "--json"])
    check(code == 0, "CLI mmcheck：通过时退出码 0")
    code = cli_module.main(["mmcheck", "--conn-file", str(prmtop),
                            "--coord", str(short)])
    check(code == 1, "CLI mmcheck：对不上时退出码 1")
    code = cli_module.main(["mmcheck", "--mol", f"AMBER:{prmtop}:2",
                            "--conn-file", str(prmtop), "--coord", str(pdb)])
    check(code == 2, "CLI mmcheck， -mol 和 --conn-file 同时给--> 拒绝")

    # if a CP2K checkout is around, check the readers against its own samples
    samples = _Path(r"E:\desktop\cp2k\cp2k-2026.2\tests\Fist")
    if (samples / "sample_psf" / "solvent.psf").is_file():
        official = check_mixture(
            [MmMolecule(str(samples / "sample_psf" / "solute1.psf"), "PSF", 1),
             MmMolecule(str(samples / "sample_psf" / "solvent.psf"), "PSF", 145)],
            str(samples / "sample_pdb" / "full_system.pdb"), "PDB")
        check(official.ok and official.expected_atoms == 1762,
              "官方 multi_psf.inp 的 PSF+PDB：读出来对得上（1762 个原子）",
              official.text())
        lamol = check_mixture(
            [MmMolecule(str(samples / "sample_top" / "lamol.top"), "AMBER", 4)],
            str(samples / "sample_xyz" / "4mol.xyz"), "XYZ")
        check(lamol.ok and lamol.expected_atoms == 200,
              "官方 lamol.inp 的 prmtop+xyz，  × 50 = 200", lamol.text())
    else:
        print("  skip 官方样例核对（找不到 CP2K 源码树）")
    return failures


def tested_directories(tests: Path) -> list[Path]:
    """Directories the CP2K regression suite actually runs, from TEST_DIRS."""
    listing = tests / "TEST_DIRS"
    if not listing.is_file():
        return []
    out: list[Path] = []
    for raw in listing.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        candidate = tests / line.split()[0]
        if candidate.is_dir():
            # the third column is a *build* requirement ("regtests that will only
            # run if a certain library has been linked in", see the header of
            # TEST_DIRS).  `quip` is an external interface: `&QUIP` exists only in
            # a build linked against the QUIP library and is documented nowhere
            # in the manual or the stock source tree, so its regtest is not a fair
            # target for the schema check (it was the corpus' second known false
            # positive).
            if any(flag.lower() == "quip" for flag in line.split()[1:]):
                continue
            out.append(candidate)
    return out


def part1b_knobs(index, db, quiet: bool = False) -> int:
    """Every option the form exposes must produce a valid input.

    The GUI and the CLI can only set what the core supports, so this walks the
    knobs that were added last - SCF robustness, grid, mixing, fractional
    coordinates, MD coupling - and requires 0 errors from the validator.
    """
    from cp2kbuild.recipes import suggest_ewald_gmax
    from cp2kbuild.structures import Cell as _Cell
    from cp2kbuild.structures import read_structure
    from cp2kbuild.validate import Validator

    import tempfile

    print()
    print("=" * 78)
    print("part 1b: the parameter knobs")
    print("=" * 78)

    failures = 0
    validator = Validator(db=db, index=index)
    silicon = read_structure(str(Path(__file__).resolve().parents[1]
                                / "examples" / "silicon.xyz"))
    water = read_structure(str(Path(__file__).resolve().parents[1]
                              / "examples" / "water.xyz"))

    cases = [
        ("IGNORE_CONVERGENCE_FAILURE", dict(
            task="energy", structure=silicon, max_scf=1,
            ignore_convergence_failure=True),
         ["IGNORE_CONVERGENCE_FAILURE"]),
        ("SCALED 分数坐标", dict(task="energy", structure=silicon,
                             scaled_coords=True),
         ["SCALED"]),
        ("MIXING 可调", dict(task="energy", structure=silicon,
                          scf="DIAGONALIZATION", mixing_alpha=0.1,
                          mixing_nbuffer=16, mixing_beta=1.5),
         ["ALPHA 0.1", "NBUFFER 16", "BETA 1.5"]),
        ("THERMOSTAT/BAROSTAT TIMECON", dict(
            task="md", structure=silicon, ensemble="NPT_I", thermostat="NOSE",
            steps=100, thermostat_timecon=250.0, barostat_timecon=1000.0),
         ["TIMECON [fs] 250.0", "TIMECON [fs] 1000.0"]),
        ("MD NVT + CSVR（不写已过时的 TEMP_TOL）e", dict(
            task="md", structure=silicon, ensemble="NVT", temperature=330.0,
            steps=1000, thermostat="CSVR", thermostat_timecon=50.0),
         ["TYPE CSVR", "TEMPERATURE [K] 330.0", "TIMECON [fs] 50.0"]),
        ("NGRIDS/GAPW/ADDED_MOS/SMEAR", dict(
            task="energy", structure=silicon, ngrids=5, qs_method="GAPW",
            scf="DIAGONALIZATION", added_mos=24, smearing=True,
            electronic_temperature=1000.0),
         ["NGRIDS 5", "METHOD GAPW", "ADDED_MOS 24", "ELECTRONIC_TEMPERATURE"]),
        ("EPS_SCF/MAX_SCF", dict(task="energy", structure=water, vacuum=12.0,
                                eps_scf=1e-8, max_scf=150),
         ["EPS_SCF 1.000000E-08", "MAX_SCF 150"]),
        ("内层 1 / 外层 0（固定步数）", dict(
            task="energy", structure=silicon, max_scf=1, outer_max_scf=0,
            ignore_convergence_failure=True),
         ["MAX_SCF 1", "IGNORE_CONVERGENCE_FAILURE T", "MAX_SCF 0"]),
        ("外层循环 10 / 1e-7", dict(
            task="energy", structure=silicon, scf="DIAGONALIZATION", max_scf=50,
            outer_max_scf=10, outer_eps_scf=1e-7),
         ["MAX_SCF 50", "MAX_SCF 10", "EPS_SCF 1.000000E-07"]),
        ("SCF 波函数重名", dict(
            task="energy", structure=silicon, scf_guess="ATOMIC",
            wfn_restart_file="prev-RESTART.wfn"),
         ["SCF_GUESS ATOMIC", "WFN_RESTART_FILE_NAME prev-RESTART.wfn"]),
        ("初猜默认 RESTART + 默认写f.wfn", dict(task="energy", structure=silicon),
         ["SCF_GUESS RESTART", "&RESTART ON", "cp2k-RESTART.wfn"]),
        ("初猜 ATOMIC + 不写 .wfn", dict(
            task="energy", structure=silicon, scf_guess="ATOMIC",
            wfn_restart_print=False),
         ["SCF_GUESS ATOMIC"]),
        ("波函数重启文件 每 5 步 / 备份 3 件", dict(
            task="energy", structure=silicon, wfn_restart_every=5,
            wfn_backup_copies=3),
         ["QS_SCF 5", "BACKUP_COPIES 3"]),
        ("k 点跑 .kp 而不是 .wfn", dict(
            task="band", structure=silicon, kpoint_grid=(4, 4, 4)),
         ["RESTART.kp", "k-point runs use .kp"]),
        ("EXT_RESTART 续算", dict(
            task="md", structure=silicon, steps=100, ext_restart_file="run-1.restart"),
         ["&EXT_RESTART", "RESTART_FILE_NAME run-1.restart", "RESTART_DEFAULT T"]),
        ("EXT_RESTART 接力：GEO_OPT → MD（只取坐标）", dict(
            task="md", structure=silicon, steps=100,
            ext_restart_file="1_geo-1.restart", ext_restart_take=("pos",)),
         ["&EXT_RESTART", "RESTART_FILE_NAME 1_geo-1.restart",
          "RESTART_DEFAULT F", "RESTART_POS T"]),
        ("EXT_RESTART 接力：MD → NPT（坐标 + 速度 + 晶胞 + 恒温器/压浴）", dict(
            task="md", structure=silicon, steps=100, ensemble="NPT_I",
            ext_restart_file="2_nvt-1.restart",
            ext_restart_take=("pos", "vel", "cell", "thermostat", "barostat")),
         ["RESTART_DEFAULT F", "RESTART_POS T", "RESTART_VEL T",
          "RESTART_CELL T", "RESTART_THERMOSTAT T", "RESTART_BAROSTAT T"]),
        ("写重启文件", dict(
            task="md", structure=silicon, steps=100, restart_every=50,
            restart_backups=3),
         ["BACKUP_COPIES 3", "MD 50", "&RESTART ON"]),
        # the wave-function file counts the *task's* level (MD here), the run
        # restart file its own (also MD); QS_SCF only appears for a single point
        ("重启文件打印的默认值（&EACH 一 ADD_LAST 分开，层级按任务）", dict(
            task="md", structure=silicon, steps=100),
         ["MD 0", "MD 20", "ADD_LAST NUMERIC"]),
        ("最后一步也不写", dict(
            task="md", structure=silicon, steps=100, restart_every=5,
            restart_add_last=False, wfn_restart_every=10,
            wfn_restart_add_last=False),
         ["MD 10", "MD 5", "ADD_LAST NO"]),
        ("波函数间隔按单点任务的层级（QS_SCF）", dict(
            task="energy", structure=silicon, wfn_restart_every=25),
         ["QS_SCF 25", "ADD_LAST NUMERIC"]),
        ("波函数间隔可以显式指定层线", dict(
            task="md", structure=silicon, steps=100, wfn_restart_every=25,
            wfn_restart_level="CELL_OPT"),
         ["CELL_OPT 25", "ADD_LAST NUMERIC"]),
        ("OT 子参数", dict(
            task="energy", structure=silicon, ot_minimizer="CG",
            ot_preconditioner="FULL_ALL", ot_energy_gap=0.002,
            ot_linesearch="GOLD", ot_stepsize=0.2, ot_rotation=True,
            ot_safe_diis=False, outer_optimizer="DIIS"),
         ["MINIMIZER CG", "PRECONDITIONER FULL_ALL", "ENERGY_GAP 0.002",
          "LINESEARCH GOLD", "STEPSIZE 0.2", "ROTATION T", "SAFE_DIIS F",
          "OPTIMIZER DIIS"]),
        ("OT ALGORITHM IRAC", dict(task="energy", structure=silicon,
                                  ot_algorithm="IRAC"),
         ["ALGORITHM IRAC"]),
        ("对角化路线参数", dict(
            task="energy", structure=silicon, scf="DIAGONALIZATION",
            diag_algorithm="DAVIDSON", level_shift=0.1,
            mixing_method="PULAY_MIXING"),
         ["LEVEL_SHIFT 0.1", "ALGORITHM DAVIDSON", "METHOD PULAY_MIXING"]),
        ("强制 OT + SMEAR（自动补 ROTATION T）", dict(
            task="energy", structure=silicon, scf="OT", smearing=True),
         ["ROTATION T", "METHOD FERMI_DIRAC", "ELECTRONIC_TEMPERATURE [K] 300.0"]),
        ("SMEAR 方法可换", dict(
            task="energy", structure=silicon, smearing=True,
            smear_method="METHFESSEL_PAXTON"),
         ["METHOD METHFESSEL_PAXTON"]),
        ("LEVEL_SHIFT 在NOT 路线不写", dict(
            task="energy", structure=silicon, level_shift=0.1),
         ["MINIMIZER DIIS"]),
        ("Poisson 求解器（三维周期 + WAVELET）", dict(
            task="energy", structure=silicon, poisson_solver="WAVELET"),
         ["&POISSON", "PERIODIC XYZ", "POISSON_SOLVER WAVELET"]),
        ("Poisson 求解器（分子 + MT）", dict(
            task="energy", structure=water, vacuum=12.0, poisson_solver="MT"),
         ["PERIODIC NONE", "POISSON_SOLVER MT"]),
        ("项目名默认 cp2k（重启文件才能继承）", dict(
            task="energy", structure=silicon),
         ["PROJECT_NAME cp2k", "cp2k-RESTART.wfn"]),
        ("项目名可改", dict(task="energy", structure=silicon, project="mysystem"),
         ["PROJECT_NAME mysystem", "mysystem-RESTART.wfn"]),
        # ---- k-points: for every Quickstep task, not only band/PDOS -------- #
        ("k 网格（energy 任务也给）", dict(
            task="energy", structure=silicon, kpoint_grid=(4, 4, 4)),
         ["&KPOINTS", "SCHEME MONKHORST-PACK 4 4 4", "SYMMETRY T",
          "WAVEFUNCTIONS REAL"]),
        ("k 点：按间距自动（20 Å）", dict(
            task="energy", structure=silicon, kpoint_spacing=20.0),
         ["SCHEME MONKHORST-PACK 4 4 4"]),
        ("k 点：按间距自动（60 Å，上陵q8）", dict(
            task="energy", structure=silicon, kpoint_spacing=60.0),
         ["SCHEME MONKHORST-PACK 8 8 8"]),
        ("k 点：完整网格 + 打印列表 + 平移分 Γ", dict(
            task="geo_opt", structure=silicon, kpoint_grid=(6, 6, 6),
            kpoint_full_grid=True, kpoint_gamma_centered=True,
            kpoint_verbose=True),
         ["FULL_GRID T", "GAMMA_CENTERED T", "VERBOSE T"]),
        ("k 点：复波函数 + 不约化", dict(
            task="energy", structure=silicon, kpoint_grid=(8, 8, 8),
            kpoint_wavefunctions="COMPLEX", kpoint_symmetry=False),
         ["WAVEFUNCTIONS COMPLEX", "SYMMETRY F"]),
        ("k 点：显式 GENERAL 列表", dict(
            task="energy", structure=silicon,
            kpoint_points=[(0.0, 0.0, 0.0, 1.0), (0.5, 0.5, 0.5, 2.0)]),
         ["SCHEME GENERAL", "KPOINT 0.0 0.0 0.0 1.0",
          "KPOINT 0.5 0.5 0.5 2.0"]),
        # the manual: "For MONKHORST-PACK the number of k points in all 3
        # dimensions has to be supplied along with the keyword" - a bare scheme
        # (easy to type on the command line) must not become a broken input
        ("k 点：只给方案名，网格由晶胞估算", dict(
            task="energy", structure=silicon, kpoint_scheme="MONKHORST-PACK"),
         ["SCHEME MONKHORST-PACK 4 4 4"]),
        ("k 点：方案名 + 手填网格", dict(
            task="energy", structure=silicon, kpoint_scheme="MONKHORST-PACK",
            kpoint_grid=(3, 3, 3)),
         ["SCHEME MONKHORST-PACK 3 3 3"]),
        # CP2K reads KPOINT lines only under SCHEME GENERAL, so the explicit list
        # wins over a leftover scheme instead of writing both
        ("k 点：冲突时显式列表赢", dict(
            task="energy", structure=silicon, kpoint_scheme="MONKHORST-PACK 6 6 6",
            kpoint_points=[(0.5, 0.5, 0.5, 1.0)]),
         ["SCHEME GENERAL", "KPOINT 0.5 0.5 0.5 1.0"]),
        ("立方体：轨道 + 密度", dict(
            task="energy", structure=silicon, print_mo_cubes=True,
            mo_cubes_nlumo=1, mo_cubes_stride="1 1 1",
            print_density_cube=True, density_cube_stride="2 2 2"),
         ["&MO_CUBES ON", "NHOMO 1", "NLUMO 1", "STRIDE 1 1 1",
          "&TOT_DENSITY_CUBE ON"]),
        ("固定原子（GEO_OPT）", dict(
            task="geo_opt", structure=silicon, fixed_atoms="1..4",
            fixed_atoms_components="Z"),
         ["&CONSTRAINT", "&FIXED_ATOMS", "LIST 1..4", "COMPONENTS_TO_FIX Z"]),
        ("EPS_DEFAULT + 外推", dict(
            task="energy", structure=silicon, eps_default=1e-9,
            extrapolation="LINEAR_P"),
         ["EPS_DEFAULT 1.000000E-09", "EXTRAPOLATION LINEAR_P"]),
        # ---- 氘代（H→D）：只改核质量，元素/基组/赝势不变 ------------------ #
        ("氘代 H→D（AIMD 用 1 fs 步长）", dict(
            task="md", structure=water, vacuum=12.0, steps=1000, timestep=1.0,
            h_to_d=True),
         ["&KIND H", "ELEMENT H", "MASS 2.0141017781", "deuterium"]),
        # ---- MD 运行中的温度 / 能量打印 ---------------------------------- #
        # `&MD/&PRINT/&TEMP_KIND` alone does nothing at all: CP2K only warns
        # unless the keyword `&MD TEMP_KIND` switches the computation on, so the
        # tool has to write both (md_energies.F).
        ("MD 温度打印：每个原子种类的温度（关键字 + 打印键）", dict(
            task="md", structure=silicon, ensemble="NVT", steps=1000,
            md_temp_kind_print=True, md_temp_kind_every=25),
         ["TEMP_KIND T", "&TEMP_KIND ON", "MD 25", "cp2k-1.temp"]),
        ("MD 温度打印：能量文件--1.ener（间随 + 文件名）", dict(
            task="md", structure=silicon, ensemble="NVT", steps=1000,
            md_energy_print=True, md_energy_every=50, md_energy_file="run"),
         ["&ENERGY ON", "FILENAME run", "MD 50", "cp2k-1.ener", "Temp[K]"]),
        ("MD 温度打印：热浴区域温度（每分子一个热浴）", dict(
            task="md", structure=silicon, ensemble="NPT_I", thermostat="CSVR",
            steps=1000, thermostat_region="MOLECULE",
            thermostat_temp_print=True, thermostat_temp_every=20),
         ["REGION MOLECULE", "&TEMPERATURE ON", "MD 20",
          "cp2k-1.PARTICLES.temp"]),
        # ---- 升温 / 降温 ------------------------------------------------- #
        ("升温：LANGEVIN + TEMPERATURE_ANNEALING + GAMMA", dict(
            task="md", structure=water, vacuum=12.0, ensemble="LANGEVIN",
            temperature=300.0, steps=20000, timestep=0.5,
            temperature_annealing=1.00003465796),
         ["ENSEMBLE LANGEVIN", "TEMPERATURE_ANNEALING 1.00003465796",
          "&LANGEVIN", "GAMMA [fs^-1] 0.01"]),
        ("降温：NVE + 速度缩放", dict(
            task="md", structure=water, vacuum=12.0, ensemble="NVE",
            temperature=300.0, steps=20000, annealing=0.999982671),
         ["ENSEMBLE NVE", "ANNEALING 0.999982671"]),
        ("晶胞退火：NPE_I + ANNEALING_CELL", dict(
            task="md", structure=water, vacuum=12.0, ensemble="NPE_I",
            steps=20000, annealing=0.9999, annealing_cell=0.9999),
         ["ENSEMBLE NPE_I", "ANNEALING_CELL 0.9999"]),
        ("FIST SPME 不给 GMAX（工具自己估）", dict(
            task="md", structure=water, method="FIST", mm_route="AMBER",
            mm_parm_file="mix.prmtop", mm_conn_file="mix.prmtop",
            mm_coord_file="mix.pdb", mm_ewald="SPME", steps=500,
            cell=_Cell.cubic(24.4)),
         ["EWALD_TYPE SPME", "GMAX "]),
        # ---- 元动力学（metadynamics） ------------------------------------ #
        # The CV definitions and the bias live in two different places, and the
        # `&METAVAR COLVAR n` index counts the CVs in the order they appear under
        # &SUBSYS - so both halves and the numbering are checked here.
        ("元动力学：井温 + 2 一 CV + 二次墙", dict(
            task="md", structure=water, vacuum=12.0, ensemble="NVT",
            steps=20000, temperature=330.0, metadyn=metadyn_wt()),
         ["&COLVAR", "&DISTANCE", "ATOMS 1 2", "&COORDINATION", "KINDS_FROM O",
          "KINDS_TO H", "R0 [angstrom] 1.2", "NN 6", "ND 8",
          "&FREE_ENERGY", "METHOD METADYN", "&METADYN", "DO_HILLS T",
          "NT_HILLS 100", "WW 0.00042", "WELL_TEMPERED T",
          "TEMPERATURE [K] 330.0", "DELTA_T [K] 3000.0", "MIN_DISP 0.05",
          "HILL_TAIL_CUTOFF 3.0", "&METAVAR", "COLVAR 1",
          # SCALE is the hill width, and it is a length for a distance CV: CP2K
          # declares it `internal_cp2k` (= bohr), so asking for angstroms needs
          # the unit annotation; the wall position is the same story
          "SCALE [angstrom] 0.5",
          "COLVAR 2", "&WALL", "POSITION [angstrom] 2.4", "TYPE QUADRATIC",
          "DIRECTION WALL_MINUS", "K [hartree] 0.5",
          "&COLVAR ON", "&HILLS ON", "MD 20"]),
        ("元动力学：组名 + 路径/RMSD CV + 扩展拉格朗日 + Langevin", dict(
            task="md", structure=water, vacuum=12.0, steps=5000,
            metadyn=metadyn_lagrange()),
         ["&COMBINE_COLVAR", "FUNCTION (CV1^P+CV2^P)^(1./P)",
          "PARAMETERS P", "VALUES 4.0", "VARIABLES CV1 CV2",
          "&DISTANCE_FROM_PATH", "ALIGN_FRAMES T", "RMSD T",
          "DISTANCES_RMSD T", "LAMBDA 0.5", "SUBSET_TYPE LIST", "&FRAME",
          "COORD_FILE_NAME a.xyz", "&RMSD", "LAGRANGE T", "LANGEVIN T",
          "MASS 2.0", "GAMMA 1.0", "LAMBDA 0.03"]),
        ("元动力学：PLUMED 驱动（CV 用 plumed.dat 定义，本段只写文件名）", dict(
            task="md", structure=water, vacuum=12.0, steps=5000,
            metadyn=Metadyn(enabled=True, use_plumed=True,
                            plumed_file="water.plumedinp")),
         ["&FREE_ENERGY", "METHOD METADYN", "USE_PLUMED T",
          "PLUMED_INPUT_FILE water.plumedinp"]),
    ]

    for label, kwargs, wanted in cases:
        spec = Spec(**kwargs)
        result = build(spec, index)
        text = render_document(result.sections, title="t", notes=[])
        diagnosis = validator.run(result.sections)
        errors = [d for d in diagnosis if d.level == "error"]
        missing = [needle for needle in wanted if needle not in text]
        if errors or missing:
            failures += 1
            print(f"  FAIL {label}: 缩 {missing}, error {len(errors)}")
            for item in errors[:3]:
                print(f"       {item.code}: {item.message}")
        else:
            print(f"  ok   {label}：参数写进去了，校验 0 error"
                  f"（{len(text.splitlines())} 行）")

    # ------------------------------------------------------------------ #
    # 任务接力: the take-list must be exactly "RESTART_DEFAULT F + the named
    # keywords", and every short name has to be a real CP2K keyword
    # ------------------------------------------------------------------ #
    print()
    print("  -- EXT_RESTART 取哪些量（任务接力） --")

    from cp2kbuild.recipes import EXT_RESTART_TAKES

    relay = Spec(task="md", structure=silicon, steps=100,
                 ext_restart_file="prev-1.restart",
                 ext_restart_take=("pos", "vel"))
    relay_text = render_document(build(relay, index).sections, title="t", notes=[])
    on = [line.strip() for line in relay_text.splitlines()
          if line.strip().startswith("RESTART_")
          and not line.strip().startswith("RESTART_DEFAULT")
          and line.strip().endswith(" T")]
    off = [line.strip() for line in relay_text.splitlines()
           if line.strip().startswith("RESTART_")
           and not line.strip().startswith("RESTART_DEFAULT")
           and line.strip().endswith(" F")]
    if on == ["RESTART_POS T", "RESTART_VEL T"]:
        print("  ok   只写出 RESTART_DEFAULT F + 点名的两项AT，其余一概不写")
    else:
        failures += 1
        print(f"  FAIL 接力块写出来的开关不对：{on}")
    if not off:
        print("  ok   COUNTERS/THERMOSTAT/— 一个字都不写（继承 RESTART_DEFAULT F） ")
    else:
        failures += 1
        print(f"  FAIL 不该显式关掉别的开关（会让后面的新关键字漏掉）：{off}")

    if db is not None:
        unknown = [f"RESTART_{EXT_RESTART_TAKES[key][0]}"
                   for key in EXT_RESTART_TAKES
                   if not db.has_keyword(["EXT_RESTART"],
                                         f"RESTART_{EXT_RESTART_TAKES[key][0]}")]
        if unknown:
            failures += 1
            print(f"  FAIL 接力表里有手册里没有的关键字：{unknown}")
        else:
            print(f"  ok   接力表的 {len(EXT_RESTART_TAKES)} 个关键字手册里都有")
        default = db.default_of(["EXT_RESTART"], "RESTART_DEFAULT")
        if (default or "").strip().upper() in ("T", ".TRUE.", "TRUE"):
            print("  ok   手册确认 RESTART_DEFAULT 的默认值是 T（所以接力要显式写IF）")
        else:
            failures += 1
            print(f"  FAIL RESTART_DEFAULT 的默认值不是AT，而是 {default!r}")

    relay_diag = validator.run(build(relay, index).sections)
    relay_errors = [d for d in relay_diag if d.level == "error"]
    if relay_errors:
        failures += 1
        print(f"  FAIL 接力输入校验有nerror：{relay_errors[0].code}")
    else:
        print("  ok   接力输入校验 0 error")

    # ------------------------------------------------------------------ #
    # 元动力学: the two halves, the CV numbering, the checks the schema
    # cannot make, and the "only in an MD run" rule
    # ------------------------------------------------------------------ #
    print()
    print("  -- 元动力学，NCOLVAR / &FREE_ENERGY/&METADYN， --")

    wt = build(Spec(task="md", structure=water, vacuum=12.0, ensemble="NVT",
                    steps=20000, temperature=330.0, metadyn=metadyn_wt()),
               index)
    wt_text = render_document(wt.sections, title="t", notes=[])
    # the &METAVAR index is a *position* in the &COLVAR list under &SUBSYS, so
    # CV order and numbering have to agree
    colvars = [block for block in
               next(s for s in wt.sections if s.name.upper() == "FORCE_EVAL")
               .subsection("SUBSYS").subsections("COLVAR")]
    kinds = [block.sections[0].name.upper() for block in colvars]
    if kinds == ["DISTANCE", "COORDINATION"]:
        print(f"  ok   &SUBSYS 里的 &COLVAR 顺序 = CV 表顺序：{kinds}，"
              f"&METAVAR COLVAR 1/2 指的就是它们，")
    else:
        failures += 1
        print(f"  FAIL &COLVAR 顺序不对：{kinds}")

    # every metadynamics keyword the tool writes must be in the manual, at the
    # path the tool writes it to (a typo in metadyn.py would show up here instead
    # of in a job that will not start)
    if db is not None:
        checks = [
            (["MOTION", "FREE_ENERGY", "METADYN"],
             ["DO_HILLS", "NT_HILLS", "WW", "WELL_TEMPERED", "TEMPERATURE",
              "DELTA_T", "MIN_DISP", "HILL_TAIL_CUTOFF", "USE_PLUMED",
              "PLUMED_INPUT_FILE", "LAGRANGE", "LANGEVIN"]),
            (["MOTION", "FREE_ENERGY", "METADYN", "METAVAR"],
             ["COLVAR", "SCALE", "MASS", "GAMMA", "LAMBDA"]),
            (["MOTION", "FREE_ENERGY", "METADYN", "METAVAR", "WALL"],
             ["POSITION", "TYPE"]),
            (["FORCE_EVAL", "SUBSYS", "COLVAR", "DISTANCE"], ["ATOMS"]),
            (["FORCE_EVAL", "SUBSYS", "COLVAR", "ANGLE"], ["ATOMS"]),
            (["FORCE_EVAL", "SUBSYS", "COLVAR", "TORSION"], ["ATOMS"]),
            (["FORCE_EVAL", "SUBSYS", "COLVAR", "COORDINATION"],
             ["KINDS_FROM", "KINDS_TO", "ATOMS_FROM", "ATOMS_TO", "R0", "NN",
              "ND"]),
            (["FORCE_EVAL", "SUBSYS", "COLVAR", "COMBINE_COLVAR"],
             ["FUNCTION", "VARIABLES", "PARAMETERS", "VALUES"]),
            (["FORCE_EVAL", "SUBSYS", "COLVAR", "DISTANCE_FROM_PATH"],
             ["ATOMS", "ALIGN_FRAMES", "RMSD", "DISTANCES_RMSD", "SUBSET_TYPE",
              "LAMBDA"]),
            (["FORCE_EVAL", "SUBSYS", "COLVAR", "DISTANCE_FROM_PATH", "FRAME"],
             ["COORD_FILE_NAME"]),
            (["FORCE_EVAL", "SUBSYS", "COLVAR", "RMSD"],
             ["ATOMS", "ALIGN_FRAMES", "SUBSET_TYPE"]),
        ]
        missing = [f"{'/'.join(path)}/{name}" for path, names in checks
                   for name in names if not db.has_keyword(path, name)]
        # &EACH is a *section* (`&EACH MD n`), and the wall types are sections too
        sections = [
            (["MOTION", "FREE_ENERGY", "METADYN", "PRINT", "COLVAR"], "EACH"),
            (["MOTION", "FREE_ENERGY", "METADYN", "PRINT", "HILLS"], "EACH"),
            (["MOTION", "FREE_ENERGY", "METADYN", "METAVAR", "WALL"], "QUADRATIC"),
            (["MOTION", "FREE_ENERGY", "METADYN", "METAVAR", "WALL"], "QUARTIC"),
            (["MOTION", "FREE_ENERGY", "METADYN", "METAVAR", "WALL"], "REFLECTIVE"),
            (["MOTION", "FREE_ENERGY", "METADYN", "METAVAR", "WALL"], "GAUSSIAN"),
        ]
        for path, name in sections:
            info = db.section(f"CP2K_INPUT/{'/'.join(path)}")
            if info is None or name not in info.subsections:
                missing.append(f"{'/'.join(path)}/&{name}")
        if missing:
            failures += 1
            print(f"  FAIL 这些元动力学关键字不在手册里（或换了位置）：{missing}")
        else:
            total = sum(len(names) for _path, names in checks) + len(sections)
            print(f"  ok   {total} 个元动力学关键字/子段都在手册里，"
                  "段路径和写法一致（名a4 种墙）")

    # the mistakes that are valid CP2K but certainly not meant
    bad = Metadyn(enabled=True, do_hills=False, well_tempered=True, lagrange=True,
                  cvs=(Cv(kind="distance", atoms=(1, 99)),
                       Cv(kind="combine", function=""),
                       Cv(kind="path", atoms=(1, 2), frames=()),
                       Cv(kind="angle", atoms=(1, 2))),
                  metavars=(Metavar(colvar=9),
                            Metavar(colvar=1, gamma=0.5,
                                    wall=Wall(type="NOPE"))))
    found = metadyn_problems(bad, natoms=3)
    wanted = ["超出体系范围", "DO_HILLS F", "TEMPERATURE", "DELTA_T",
              "COLVAR 9", "MASS", "GAMMA", "不是 CP2K 认识", "FUNCTION",
              "&FRAME", "要 3 个原子序号"]
    absent = [needle for needle in wanted
              if not any(needle in line for line in found)]
    if absent:
        failures += 1
        print(f"  FAIL 元动力学的自检漏掉了：{absent}")
        for line in found:
            print(f"       {line}")
    else:
        print(f"  ok   元动力学自检抓到 {len(found)} 种写法问题"
              "（原子越界、DO_HILLS F/井温缺参数/METAVAR 越界/缺 MASS/"
              "GAMMA 没开 LANGEVIN/墙类型错/组合缺 FUNCTION/路径缺 FRAME/原子数不对）")

    # an MD-only feature: every other task must say so instead of writing the
    # sections into an input CP2K would ignore them in
    for task in ("energy", "geo_opt", "cell_opt", "freq", "band", "pdos", "neb"):
        result = build(Spec(task=task, structure=silicon, metadyn=metadyn_wt()),
                       index)
        text = render_document(result.sections, title="t", notes=[])
        leaked = [name for name in ("&COLVAR", "&METADYN", "&FREE_ENERGY")
                  if name in text]
        silent = not any("metadynamics only applies to an MD run" in warning
                         for warning in result.warnings)
        if leaked or silent:
            failures += 1
            print(f"  FAIL {task}：泄了 {leaked}，警告 {'缺' if silent else '有'}")
            break
    else:
        print("  ok   面 MD 任务（energy/geo_opt/cell_opt/freq/band/pdos/neb）l"
              "不写这些段，并给出一条「只在IMD 里生效」的警告")

    # the PLUMED route defines its CVs in plumed.dat: writing &COLVAR as well
    # would leave sections nothing reads (the CLI refuses that combination)
    both = build(Spec(task="md", structure=water, vacuum=12.0, steps=1000,
                      metadyn=Metadyn(enabled=True, use_plumed=True,
                                      cvs=(Cv(kind="distance", atoms=(1, 2)),))),
                 index)
    subsys = next(s for s in both.sections
                  if s.name.upper() == "FORCE_EVAL").subsection("SUBSYS")
    written = subsys.subsections("COLVAR")
    if not written:
        print("  ok   PLUMED 路线不写 &SUBSYS/&COLVAR（CV 变成 plumed.dat 草稿）")
    else:
        failures += 1
        print(f"  FAIL PLUMED + CV 没有处理干净（写了 {len(written)} 一 &COLVAR）e")

    # ---- plumed.dat: the same CV/hill settings in PLUMED's language ---------- #
    from cp2kbuild.plumed import (ANGSTROM_TO_NM, HARTREE_TO_KJMOL,
                                  plumed_input)

    # the unit factors: CP2K's PLUMED interface announces atomic units to PLUMED
    # (metadynamics.F:174-176), and CP2K's own test file is written in PLUMED's
    # units (HEIGHT=5.251 kJ/mol = 2 mHartree)
    if (ANGSTROM_TO_NM == 0.1
            and abs(2.0e-3 * HARTREE_TO_KJMOL - 5.251) < 1.0e-3):
        print("  ok   plumed.dat 的单位换算：Å→nm ×0.1、Hartree→kJ/mol ×2625.5"
              "（和 CP2K 测试文件的 HEIGHT=5.251 对得上）")
    else:
        failures += 1
        print("  FAIL plumed.dat 的单位换算因子不对")

    plumed_meta = Metadyn(
        enabled=True, use_plumed=True, plumed_file="water.plumedinp",
        nt_hills=5, hill_height=2.0e-3, well_tempered=True, temperature=330.0,
        delta_t=3000.0, sigma=0.03,
        cvs=(Cv(kind="distance", atoms=(1, 2)),
             Cv(kind="coordination", kinds_from="O", kinds_to="H", r0=1.25,
                nn=6, nd=14),
             Cv(kind="angle", atoms=(2, 1, 3))),
        metavars=(Metavar(colvar=1, scale=0.5,
                          wall=Wall(position=2.5, type="QUADRATIC",
                                    direction="WALL_PLUS", k=0.5)),
                  Metavar(colvar=2), Metavar(colvar=3)))
    draft = plumed_input(plumed_meta, water, temperature=330.0, version="t")
    wanted_draft = [
        "cv1: DISTANCE ATOMS=1,2 NOPBC",           # 非周期体系加 NOPBC
        "cv2: COORDINATION GROUPA=1 GROUPB=2,3",    # KINDS 按结构展开
        "R_0=0.125",                                # 1.25 Å → nm
        "MM=14",                                    # CP2K 的 ND = PLUMED 的 MM
        "cv3: ANGLE ATOMS=2,1,3",
        # SCALE (0.5 Å) is the *same* quantity as SIGMA - 0.05 nm, not a COMBINE;
        # cv2/cv3 have no SCALE of their own, so the panel default 0.03 is used
        "m: METAD ARG=cv1,cv2,cv3 SIGMA=0.05,0.03,0.03 HEIGHT=5.251 PACE=5",
        "UPPER_WALLS ARG=cv1 AT=0.25",             # WALL_PLUS → 上墙，位置inm
        "KAPPA=468791",                            # 0.5 Ha/bohr² → kJ/mol/nm²
        "TEMP=330", "BIASFACTOR=10.0909",          # PLUMED 的 gamma = 1+DeltaT/T = 1+3000/330
        "FILE=HILLS",
        "PRINT ARG=cv1,cv2,cv3,m.bias FILE=COLVAR STRIDE=1",
        "# cv1: σ 0.5 Å → 0.05 nm",                # 换算是说清楚了的
        # the default σ is one number but its unit follows the CV: 配位数 纯数)
        # and 角度(rad) cannot share it, and the draft says so
        "同一个默认丘陵宽度用在了纯数、角度（rad）的 CV 上",
    ]
    missing = [needle for needle in wanted_draft if needle not in draft]
    if missing:
        failures += 1
        print(f"  FAIL plumed.dat 少了：{missing}")
    else:
        print(f"  ok   plumed.dat：{len(wanted_draft)} 项都对"
              "（原子号/nm 换算/SCALE 就是 SIGMA/ND→MM/墙的 KAPPA/METAD 的参数）")

    # things that must NOT be silently invented
    combo = plumed_input(Metadyn(
        enabled=True, use_plumed=True, nt_hills=50, hill_height=1.0e-3,
        cvs=(Cv(kind="combine", function="(CV1^P+CV2^P)^(1./P)",
                parameters=("P",), values=(4.0,),
                children=(Cv(kind="distance", atoms=(1, 2)),
                          Cv(kind="distance", atoms=(1, 3)))),
             Cv(kind="path", atoms=(1, 2, 3), frames=("a.xyz", "b.xyz"),
                lam=0.5)),
        metavars=(Metavar(colvar=1), Metavar(colvar=2))), water,
        temperature=330.0)
    combo_wanted = [
        "cv1c1: DISTANCE ATOMS=1,2",                 # 子 CV 定义出来
        "#   cv1: MATHEVAL ARG=cv1c1,cv1c2",          # 表达式留注释，不假翻读 
        "(CV1^P+CV2^P)^(1./P)",
        "PATHMSD REFERENCE=refs.pdb",                # 路径 CV 是占体 
        "METAD 需要至少一个可用的 ARG",               # 不留空 ARG 的 METAD 行 
        "# * cv1: 组合 CV 的表达式没有翻译",
        "# * cv2: 路径/RMSD CV 需要手写",
    ]
    missing = [needle for needle in combo_wanted if needle not in combo]
    if missing or "m: METAD" in combo:
        failures += 1
        print(f"  FAIL plumed.dat（组名 路径）少了 {missing}，" 
              f"或者写了空 ARG 的 METAD：{'m: METAD' in combo}")
    else:
        print("  ok   plumed.dat：组名 CV 只给子 CV + 表达式注释、路径 CV 给占位。" 
              "没填 SIGMA 时写 TODO（不假装翻译）")

    # ---- the CV-rule differences between the two routes ------------------- #
    # (a) SIGMA is a *compulsory* PLUMED keyword: with no width anywhere the
    #     draft has to say that 0.1 is its own placeholder, not "PLUMED's default"
    no_width = plumed_input(Metadyn(
        enabled=True, use_plumed=True,
        cvs=(Cv(kind="distance", atoms=(1, 2)),),
        metavars=(Metavar(colvar=1),)), water, temperature=300.0)
    if "这里给 cv1 填了 0.1 当占位" in no_width and "必填" in no_width:
        print("  ok   plumed.dat：没给宽度时说清 0.1 是占位（SIGMA 是 PLUMED 必填项）")
    else:
        failures += 1
        print("  FAIL plumed.dat 把没填的 SIGMA 说成了 PLUMED 的默认值")

    # (b) CP2K's coordination number is divided by the number of "from" atoms
    #     (colvar_methods.F:4312); PLUMED's is a plain sum, so the two central
    #     hydrogens of water need a 1/2 wrapper to stay the same CV
    averaged = plumed_input(Metadyn(
        enabled=True, use_plumed=True, sigma=0.1,
        cvs=(Cv(kind="coordination", kinds_from="H", kinds_to="O", r0=1.2),),
        metavars=(Metavar(colvar=1),)), water, temperature=300.0)
    if ("cv1n: COMBINE ARG=cv1 PERIODIC=NO COEFFICIENTS=0.5" in averaged
            and "m: METAD ARG=cv1n SIGMA=0.1" in averaged):
        print("  ok   plumed.dat：配位数按 CP2K 的 1/N 平均化（COMBINE 0.5）0")
    else:
        failures += 1
        print("  FAIL plumed.dat 没有对齐 CP2K 的配位数平均化")

    # (c) SCALE is a width, and CP2K reads 0 as "infinitely wide" = no bias at
    #     all, so a &METAVAR without one has to be flagged
    from cp2kbuild.metadyn import metadyn_section, problems as md_problems

    bare = Metadyn(enabled=True, cvs=(Cv(kind="distance", atoms=(1, 2)),),
                   metavars=(Metavar(colvar=1),))
    bare_warnings = md_problems(bare, 3, 300.0)
    if (len(bare_warnings) == 1 and "SCALE" in bare_warnings[0]
            and "无限宽" in bare_warnings[0]):
        print("  ok   原生路线：&METAVAR 没有 SCALE 时警告“无限宽=没有偏置”")
    else:
        failures += 1
        print(f"  FAIL 原生路线的 SCALE 陷阱没报出来：{bare_warnings}")

    # ... and a panel-level default σ is written out as SCALE so it cannot happen
    covered = Metadyn(enabled=True, sigma=0.25,
                      cvs=(Cv(kind="distance", atoms=(1, 2)),
                           Cv(kind="coordination", kinds_from="O",
                              kinds_to="H", r0=1.2)),
                      metavars=(Metavar(colvar=1), Metavar(colvar=2, scale=0.4)))
    covered_text = render_document([metadyn_section(covered)], title="t")
    if ("SCALE [angstrom] 0.25" in covered_text and "SCALE 0.4" in covered_text
            and md_problems(covered, 3, 300.0) == []):
        print("  ok   原生路线：默认 σ 写成一一 CV 的 SCALE，单 CV 的 SCALE 优先，" 
              "长度类带 [angstrom]、配位数不带单位")
    else:
        failures += 1
        print("  FAIL 默认 σ 没有正确落到 &METAVAR SCALE")

    # (d) well-tempered takes its reference temperature from &MD TEMPERATURE
    #     (md_run.F:400) and CP2K divides by it - 0 K is a real trap
    zero_t = Metadyn(enabled=True, well_tempered=True, delta_t=3000.0,
                     cvs=(Cv(kind="distance", atoms=(1, 2)),),
                     metavars=(Metavar(colvar=1, scale=0.2),))
    zero_warnings = " ".join(md_problems(zero_t, 3, 0.0))
    wrong_t = " ".join(md_problems(
        Metadyn(enabled=True, well_tempered=True, delta_t=3000.0, temperature=330.0,
                cvs=(Cv(kind="distance", atoms=(1, 2)),),
                metavars=(Metavar(colvar=1, scale=0.2),)), 3, 330.0))
    if "必须是正数" in zero_warnings and "不起作用" in wrong_t:
        print("  ok   井温，IMD TEMPERATURE 一 0 会警告，"
              "&METADYN TEMPERATURE 被点明不是参考温度")
    else:
        failures += 1
        print(f"  FAIL 井温温度的两个陷阱没报全：{zero_warnings} / {wrong_t}")

    # ---- the PLUMED-native table (its own language, no translation) -------- #
    from cp2kbuild.plumedcv import (PlumedBias, PlumedCv, PlumedSetup, PlumedWall,
                                    draft as plumed_draft_of, problems as cv_problems,
                                    render as render_setup)

    native = PlumedSetup(
        cvs=(PlumedCv(label="cn", action="COORDINATIONNUMBER",
                      params="SPECIES=1-100 R_0=0.33 MEAN", component="mean",
                      sigma=0.3),
             PlumedCv(label="nn", action="AROUND",
                      params="DATA=cn ATOM=1 SIGMA=0.1 XLOWER=-2.0 XUPPER=2.0 MEAN",
                      component="mean")),
        walls=(PlumedWall(arg="cn.mean", kind="UPPER_WALLS", at=6.0, kappa=5000.0),),
        # the explicit list, in that order: the σ list follows it
        bias=PlumedBias(pace=500, height=1.2, sigma=0.1, temperature=330.0,
                        biasfactor=10.0, args=("cn.mean", "nn.mean")),
        free_lines=("opes: OPES_METAD ARG=cn.mean PACE=500 BARRIER=40",))
    text = render_setup(native, temperature=330.0, version="t")
    wanted_native = [
        "cn: COORDINATIONNUMBER SPECIES=1-100 R_0=0.33 MEAN",
        "#   标量引用：cn.mean",                       # 分量引用写对了
        "nn: AROUND DATA=cn ATOM=1 SIGMA=0.1",
        "UPPER_WALLS ARG=cn.mean AT=6 KAPPA=5000",
        "opes: OPES_METAD ARG=cn.mean",                # 自由行原样进文件
        # the multicolvar components become the ARG list
        "m: METAD ARG=cn.mean,nn.mean SIGMA=0.3,0.1 HEIGHT=1.2 PACE=500 TEMP=330"
        " BIASFACTOR=10 FILE=HILLS",
        "PRINT ARG=cn.mean,nn.mean,m.bias FILE=COLVAR STRIDE=1",
    ]
    missing_native = [needle for needle in wanted_native if needle not in text]
    if missing_native or cv_problems(native, 100, 330.0):
        failures += 1
        print(f"  FAIL PLUMED 专用草稿少了：{missing_native}，"
              f"警告 {cv_problems(native, 100, 330.0)}")
    else:
        print("  ok   PLUMED 专用表：动作按原样写、分量拼成 label.mean、"
              "自由行原样保留")

    # the shape this file's user actually has: a table of intermediates plus one
    # combined CV that is the only thing biased.  It must be clean, and forgetting
    # one intermediate in the combination must be reported.
    def cascade(take: int, args: tuple[str, ...] = ("cv_uu",)):
        cvs = [PlumedCv(label=f"c{k}", action="COORDINATION",
                        params=f"GROUPA={21 + 8 * (k - 1)} GROUPB=28 "
                               "R_0=1.55 NN=12 MM=24")
               for k in range(1, 21)]
        cvs.append(PlumedCv(label="cv_uu", action="COMBINE",
                            params="ARG=" + ",".join(f"c{k}"
                                                     for k in range(1, take + 1))
                                   + " PERIODIC=NO"))
        return PlumedSetup(cvs=tuple(cvs), bias=PlumedBias(
            pace=100, height=4.0, sigma=0.4, temperature=493.0,
            biasfactor=7.0852, args=args))

    if cv_problems(cascade(20), 179, 493.0):
        failures += 1
        print(f"  FAIL 20 个中间量 + COMBINE（省略 COEFFICIENTS）被误报："
              f"{cv_problems(cascade(20), 179, 493.0)}")
    elif not any("没有被任何动作引用" in item
                 for item in cv_problems(cascade(19), 179, 493.0)):
        failures += 1
        print("  FAIL 漏掉一个中间量时没有提醒")
    else:
        print("  ok   PLUMED 专用表：中间量+COMBINE 不误报（COEFFICIENTS 可省），"
              "漏掉一个中间量会提醒")

    # the checks that make the table safe to use
    broken = PlumedSetup(
        cvs=(PlumedCv(label="cn", action="COORDINATIONNUMBER",
                      params="SPECIES=1-100", component="mean"),
             PlumedCv(label="cn", action="DISTANCE", params="ATOMS=1,2"),
             PlumedCv(label="bad label", action="DISTANCE", params="ATOMS=1,9"),
             PlumedCv(label="ar", action="AROUND", params="DATA=nope MEAN"),
             PlumedCv(label="co", action="COORDINATION", params="GROUPA=1"),
             PlumedCv(label="mat", action="CONTACT_MATRIX", params="ATOMS=1-5")),
        bias=PlumedBias(sigma=0.1, temperature=300.0))
    found = " | ".join(cv_problems(broken, 5, 300.0))
    for needle in ("没写 R_0", "标签 'cn' 和前面某一行重复", "不是合法的 PLUMED 标签",
                   "原子序号 9 超出体系范围", "引用了 'nope'", "没写 SIGMA",
                   "多分量动作", "adjmat"):
        if needle not in found:
            failures += 1
            print(f"  FAIL PLUMED 专用表的检查漏了：{needle}")
            break
    else:
        print("  ok   PLUMED 专用表：必填/标签重复/引用不存在/原子越界/"
              "多分量没选分量/adjmat 模块都报出来了")

    # ---- 默认 ARG = 叶子标量（20 个中间量不该变成 21 维偏置）------------- #
    leaf_text = render_setup(cascade(20, args=()), temperature=493.0)
    leaf_problems = cv_problems(cascade(20, args=()), 179, 493.0)
    if ("m: METAD ARG=cv_uu SIGMA=0.4" in leaf_text
            and "METAD ARG=c1" not in leaf_text
            and "20 个被别的动作引用的中间量没有进 ARG" in leaf_text
            and not leaf_problems):
        print("  ok   PLUMED 专用表：ARG 留空时取「叶子」标量"
              "（20 个中间量不进 ARG，也不误报）")
    else:
        failures += 1
        print(f"  FAIL 叶子默认没生效：{leaf_problems}")

    # 多分量动作的「分量」要落到动作行上，PLUMED 才认 label.mean
    appended = PlumedCv(label="cn", action="COORDINATIONNUMBER",
                        params="SPECIES=1-100 R_0=0.33", component="mean")
    keeps = PlumedCv(label="cn", action="COORDINATIONNUMBER",
                     params="SPECIES=1-100 R_0=0.33 MEAN", component="mean")
    if (appended.line().endswith(" MEAN") and appended.arg() == "cn.mean"
            and keeps.line().endswith("MEAN")
            and keeps.line().count("MEAN") == 1):
        print("  ok   PLUMED 专用表：分量补到动作行上（已写的不重复）")
    else:
        failures += 1
        print(f"  FAIL 分量没补到动作行：{appended.line()!r} / {keeps.line()!r}")
    valued = PlumedSetup(cvs=(PlumedCv(label="d", action="DISTANCES",
                                       params="GROUPA=1-3 GROUPB=4-6",
                                       component="morethan"),))
    if any("必须带一个值" in item for item in cv_problems(valued)):
        print("  ok   PLUMED 专用表：MORE_THAN 这类分量没给值会提醒")
    else:
        failures += 1
        print(f"  FAIL 分量缺值没报：{cv_problems(valued)}")

    # ---- 单位开关、RESTART、自适应宽度 ------------------------------------ #
    switch = PlumedSetup(
        cvs=(PlumedCv(label="cn", action="COORDINATION", params="GROUPA=1 GROUPB=2 "
                                                               "R_0=3.2 NN=12 MM=24"),),
        bias=PlumedBias(pace=100, height=2.0, sigma=0.4, temperature=493.0,
                        biasfactor=5.0, restart=True, adaptive="GEOM",
                        sigma_min="0.2", sigma_max="0.8"),
        units="A")
    switched = render_setup(switch, temperature=493.0)
    first_action = next(line for line in switched.splitlines()
                        if line.strip() and not line.strip().startswith("#"))
    if (first_action == "UNITS LENGTH=A TIME=fs ENERGY=kj/mol"
            and "都是 Å" in switched
            and "m: METAD ARG=cn SIGMA=0.4 HEIGHT=2 PACE=100 TEMP=493 BIASFACTOR=5"
                " ADAPTIVE=GEOM SIGMA_MIN=0.2 SIGMA_MAX=0.8 FILE=HILLS "
                "RESTART=YES" in switched
            and "--histo" in switched):
        print("  ok   plumed.dat：Å 开关写在第一个动作行之前，"
              "RESTART/ADAPTIVE/SIGMA_MIN/SIGMA_MAX 都写对了")
    else:
        failures += 1
        print(f"  FAIL 单位开关/RESTART/ADAPTIVE 没写对：{first_action!r}")
    # PLUMED 的单位串是大小写敏感的（src/tools/Units.cpp:42-57 逐字符比较）：
    # 写 kJ/mol 会让它在启动时直接报错，所以草稿必须是小写 kj/mol
    if "ENERGY=kj/mol" in switched and "ENERGY=kJ/mol" not in switched:
        print("  ok   plumed.dat：能量单位串写成 PLUMED 认的 kj/mol（不是 kJ/mol）")
    else:
        failures += 1
        print("  FAIL 能量单位串大小写不对，PLUMED 会拒绝这一行")
    # SIGMA_MIN/MAX 每个 CV 一个值：只给一个时必须替用户铺开
    two_cv = render_setup(PlumedSetup(
        cvs=(PlumedCv(label="a", action="DISTANCE", params="ATOMS=1,2"),
             PlumedCv(label="b", action="DISTANCE", params="ATOMS=1,3")),
        bias=PlumedBias(adaptive="GEOM", sigma=0.05, sigma_min="0.02",
                        sigma_max="0.1,0.2")))
    if "SIGMA_MIN=0.02,0.02 SIGMA_MAX=0.1,0.2" in two_cv:
        print("  ok   plumed.dat：两个 CV 时 SIGMA_MIN 只有一个值会铺开成两个")
    else:
        failures += 1
        print("  FAIL SIGMA_MIN 没有按 CV 个数铺开")
    # DIFF 的 σ 是步数、必须是整数；没开 ADAPTIVE 时 SIGMA_MIN 会被 PLUMED 拒收
    diff_notes = " | ".join(cv_problems(PlumedSetup(
        cvs=(PlumedCv(label="a", action="DISTANCE", params="ATOMS=1,2"),),
        bias=PlumedBias(adaptive="DIFF", sigma=20.5))))
    if "必须是**整数**" in diff_notes:
        print("  ok   PLUMED 自适应宽度：DIFF 的 SIGMA 不是整数会提醒")
    else:
        failures += 1
        print(f"  FAIL DIFF 的整数要求没提醒：{diff_notes}")
    grid_notes = " | ".join(cv_problems(PlumedSetup(
        cvs=(PlumedCv(label="a", action="DISTANCE", params="ATOMS=1,2"),),
        bias=PlumedBias(adaptive="GEOM", sigma=0.05,
                        extra=("GRID_MIN=0", "GRID_MAX=10")))))
    if "SIGMA_MIN must be specified" in grid_notes:
        print("  ok   PLUMED 自适应宽度：存网格（GRID_*）时必须给 SIGMA_MIN")
    else:
        failures += 1
        print(f"  FAIL 网格 + 自适应没要求 SIGMA_MIN：{grid_notes}")
    adaptive_notes = " | ".join(cv_problems(
        PlumedSetup(cvs=(PlumedCv(label="cn", action="COORDINATIONNUMBER",
                                  params="SPECIES=1-100 R_0=0.33 MEAN",
                                  component="mean", sigma=0.3),),
                    bias=PlumedBias(adaptive="GEOM", sigma=0.05,
                                    sigma_min="0.9", sigma_max="0.2")), 100))
    for needle in ("SIGMA 只能给**一个**数", "SIGMA_MIN=0.9 比 SIGMA_MAX=0.2 还大",
                   "sum_hills 必须加直方图修正"):
        if needle not in adaptive_notes:
            failures += 1
            print(f"  FAIL 自适应宽度的提醒漏了：{needle} / {adaptive_notes}")
            break
    else:
        print("  ok   PLUMED 自适应宽度：单 σ、上下限写反、直方图修正都提醒了")
    if "根本不读这两个词" in " | ".join(cv_problems(PlumedSetup(
            cvs=(PlumedCv(label="d", action="DISTANCE", params="ATOMS=1,2"),),
            bias=PlumedBias(sigma_min="0.1")))):
        print("  ok   PLUMED：没开 ADAPTIVE 就写 SIGMA_MIN 会被点出来"
              "（PLUMED 会 cannot understand the following words）")
    else:
        failures += 1
        print("  FAIL 没开 ADAPTIVE 的 SIGMA_MIN 没被点出来")

    # 2D 偏置的 SIGMA 是**每个 ARG 一个值**（PLUMED 要求个数一致），
    # 读回时不能当成"不是数字"丢掉——那会把 0.15,0.5 换成默认宽度
    from cp2kbuild.plumedcv import read as read_plumed

    two_d = PlumedSetup(cvs=(
        PlumedCv(label="cv1", action="COMBINE", params="ARG=a,b PERIODIC=NO"),
        PlumedCv(label="cv2", action="COMBINE", params="ARG=c,d PERIODIC=NO")))
    two_d_text = render_setup(two_d, temperature=900.0)
    if "SIGMA=0.1,0.1" not in two_d_text:
        failures += 1
        print("  FAIL 两个 CV 时没有按个数铺默认宽度："
              + str([line for line in two_d_text.splitlines() if "METAD" in line]))
    else:
        print("  ok   plumed.dat：没给单独 σ 时按 CV 个数铺默认宽度（SIGMA=0.1,0.1）")
    read_back, read_notes = read_plumed(
        "c1: DISTANCE ATOMS=1,2\n"
        "c2: DISTANCE ATOMS=1,3\n"
        "m: METAD ARG=c1,c2 SIGMA=0.15,0.5 HEIGHT=2 PACE=100 TEMP=900 "
        "BIASFACTOR=5 FILE=HILLS RESTART=NO\n")
    widths = [cv.sigma for cv in read_back.chosen()]
    if widths == [0.15, 0.5] and read_back.bias.restart is False and not read_notes \
            and "SIGMA=0.15,0.5" in render_setup(read_back):
        print("  ok   读回 2D 的 SIGMA=0.15,0.5 → 落到每个 CV 上，回写不被默认宽度盖掉")
    else:
        failures += 1
        print(f"  FAIL 2D 的 SIGMA 列表没读对：{widths} / notes={read_notes}")
    mismatched, mismatch_notes = read_plumed(
        "c1: DISTANCE ATOMS=1,2\n"
        "m: METAD ARG=c1 SIGMA=0.1,0.2 SIGMA_MIN=0.01 HEIGHT=1 PACE=10 TEMP=300\n")
    if any("个数和 ARG" in note for note in mismatch_notes):
        print("  ok   SIGMA 个数与 ARG 对不上时说清楚（不静默改宽度）")
    else:
        failures += 1
        print(f"  FAIL SIGMA 个数不符没提醒：{mismatch_notes}")

    # 没有 &COORD 的输入：CP2K 只有 &COORD 带原子，所以要人能看懂为什么读不了
    from cp2kbuild.structures import no_atoms_message

    explanation = no_atoms_message(
        "&GLOBAL\n PROJECT_NAME cp2k\n RUN_TYPE MD\n&END GLOBAL\n&FORCE_EVAL\n"
        " METHOD FIST\n &SUBSYS\n  &TOPOLOGY\n   COORD_FILE_NAME mixture.inpcrd\n"
        "  &END TOPOLOGY\n &END SUBSYS\n&END FORCE_EVAL\n", "cp2k.inp")
    for needle in ("没有 &COORD", "COORD_FILE_NAME mixture.inpcrd",
                   "结构留空", "读参数", "-1.restart"):
        if needle not in explanation:
            failures += 1
            print(f"  FAIL 没有 &COORD 的说明漏了 {needle!r}：{explanation[:80]}")
            break
    else:
        print("  ok   没有 &COORD 的输入：说明为什么读不了 + 三条出路"
              "（力场留空 / 读参数 / 用 pdb 或 -1.restart）")

    from cp2kbuild.plumedcv import read as read_plumed
    from pathlib import Path as _Path

    fixture = _Path(__file__).resolve().parent.parent / "examples" / "plumed_urea_cn.dat"
    if fixture.exists():
        loaded, load_notes = read_plumed(fixture.read_text(encoding="utf-8"))
        loaded_problems = cv_problems(loaded, 179, 493.0)
        again = render_setup(loaded, temperature=493.0)
        original_actions = [line.strip() for line in
                            fixture.read_text(encoding="utf-8").splitlines()
                            if line.strip() and not line.strip().startswith("#")]
        rendered_actions = [line.strip() for line in again.splitlines()
                            if line.strip() and not line.strip().startswith("#")]
        same = [a for a, b in zip(original_actions, rendered_actions) if a == b]
        if (len(loaded.cvs) == 21 and loaded.units == "A"
                and loaded.bias.label == "metad" and loaded.args() == ["cv_uu"]
                and not loaded_problems and not load_notes
                and len(same) >= len(original_actions) - 1):
            print(f"  ok   读回 examples/plumed_urea_cn.dat：21 个 CV、单位 Å、"
                  f"ARG=cv_uu、0 问题，回写后 {len(same)}/{len(original_actions)} "
                  "行动作逐字节相同")
        else:
            failures += 1
            print(f"  FAIL 读回失败：cvs={len(loaded.cvs)} units={loaded.units} "
                  f"args={loaded.args()} notes={load_notes} problems={loaded_problems}")
    else:
        failures += 1
        print(f"  FAIL 缺例子文件：{fixture}")

    handwritten = (
        "# a hand-written file\n"
        "UNITS LENGTH=A TIME=fs ENERGY=kJ/mol\n"      # 故意写错大小写：PLUMED 会拒绝
        "d1: DISTANCE ATOMS=1,2 NOPBC\n"
        "mat: CONTACT_MATRIX ATOMS=1-10 SWITCH={RATIONAL R_0=3.0} # a comment\n"
        "cols: COLUMNSUMS MATRIX=mat MEAN\n"
        "UPPER_WALLS ARG=d1 AT=5.0 KAPPA=100.0 EXP=2\n"
        "custom: CUSTOM ARG=d1 PERIODIC=NO FUNC=x*x\n"
        "m: METAD ARG=d1 SIGMA=0.1 HEIGHT=1.0 PACE=500 TEMP=300 BIASFACTOR=4 "
        "GRID_MIN=0 GRID_MAX=10 FILE=HILLS RESTART=yes\n"
        "PRINT ARG=d1,m.bias FILE=COLVAR STRIDE=10\n"
        "PRINT ARG=cols.mean FILE=other.dat STRIDE=100\n")
    hand, hand_notes = read_plumed(handwritten)
    hand_text = render_setup(hand, temperature=300.0)
    if (len(hand.cvs) == 4 and hand.cvs[2].component == "mean"
            and hand.cvs[1].component == "" and hand.walls[0].exp == 2.0
            and hand.units == "A" and hand.bias.restart
            and hand.bias.extra == ("GRID_MIN=0", "GRID_MAX=10")
            and hand.bias.print_stride == 10
            and "GRID_MIN=0 GRID_MAX=10" in hand_text
            and "RESTART=YES" in hand_text
            and "PRINT ARG=cols.mean FILE=other.dat STRIDE=100" in hand_text
            and hand.free_where == "end"):
        print("  ok   读回手写文件：分量/墙/未知 METAD 关键字/PRINT 都归位，"
              "对不上的 PRINT 留成自由行")
    else:
        failures += 1
        print(f"  FAIL 手写文件读回不对：cvs={len(hand.cvs)} extra={hand.bias.extra} "
              f"where={hand.free_where} notes={hand_notes}")
    # 大小写写错的单位串会被 PLUMED 拒收，读回时必须点出来（回写会改成规范写法）
    if any("大小写敏感" in note for note in hand_notes) \
            and "ENERGY=kj/mol" in hand_text:
        print("  ok   读回时点出 UNITS 里写错的 ENERGY=kJ/mol，回写成 kj/mol")
    else:
        failures += 1
        print(f"  FAIL 单位串大小写没被点出来：{hand_notes}")

    # ---- PLUMED 的续行写法（`...`）---------------------------------------- #
    # 真实反馈：用户手写的 plumed.dat 用的是手册 "Continuation lines" 那一页的写法
    # （动作行以 `...` 结尾、关键字一行一个、再用 `...` 收尾）。旧读法把 `...` 当成了
    # 参数本身，后面每一行关键字都变成"不认识的自由行"——CV 被掏空、METAD 掉回默认值。
    continued = (
        "UNITS LENGTH=A TIME=fs ENERGY=kj/mol\n"
        "\n"
        "# HNCO carbon atoms\n"
        "HNCO_C: GROUP ATOMS=102,106,110\n"
        "# urea nitrogen atoms\n"
        "UREA_N: GROUP ATOMS=20,22,28\n"
        "\n"
        "cv_coord: COORDINATION ...\n"
        "    GROUPA=HNCO_C\n"
        "    GROUPB=UREA_N\n"
        "    SWITCH={RATIONAL R_0=2.30 D_0=0.0 NN=12 MM=24}\n"
        "    NLIST\n"
        "    NL_CUTOFF=3.5\n"
        "    NL_STRIDE=10\n"
        "...\n"
        "\n"
        "metad: METAD ...\n"
        "    ARG=cv_coord\n"
        "    SIGMA=0.15\n"
        "    HEIGHT=1.0\n"
        "    PACE=100\n"
        "    TEMP=900\n"
        "    BIASFACTOR=5\n"
        "    FILE=HILLS\n"
        "    RESTART=NO\n"
        "...\n"
        "\n"
        "PRINT ARG=cv_coord,metad.bias FILE=COLVAR STRIDE=10\n"
        "\n"
        "FLUSH STRIDE=100\n")
    folded, fold_notes = read_plumed(continued)
    folded_text = render_setup(folded, temperature=900.0)
    key_line = next((line for line in folded_text.splitlines()
                     if line.startswith("cv_coord:")), "")
    metad_line = next((line for line in folded_text.splitlines()
                       if line.strip().startswith("metad:")), "")
    problems_folded = cv_problems(folded, natoms=179, temperature=900.0)
    wanted = ("GROUPA=HNCO_C", "GROUPB=UREA_N", "NLIST", "NL_CUTOFF=3.5",
              "NL_STRIDE=10")
    if (len(folded.cvs) == 3 and not fold_notes and not problems_folded
            and all(token in key_line for token in wanted)
            and folded.bias.pace == 100 and folded.bias.height == 1.0
            and folded.bias.temperature == 900.0
            and folded.bias.biasfactor == 5.0
            and folded.bias.args == ("cv_coord",)
            and folded.bias.print_stride == 10
            and folded.free_lines == ("FLUSH STRIDE=100",)
            and "SIGMA=0.15" in metad_line and "HEIGHT=1" in metad_line
            and "BIASFACTOR=5" in metad_line):
        print("  ok   续行写法（`ACTION ...` / `...`）：CV 的关键字一个不丢，"
              "METAD 的 ARG/SIGMA/HEIGHT/PACE/TEMP/BIASFACTOR/FILE 全读回来，"
              "`FLUSH` 留成自由行")
    else:
        failures += 1
        print(f"  FAIL 续行读回不对：cvs={len(folded.cvs)} notes={fold_notes} "
              f"problems={problems_folded}\n        {key_line}\n        {metad_line}")
    if "..." not in " ".join(line for line in folded_text.splitlines()
                             if not line.startswith("#")):
        print("  ok   回写不会留下单独的 `...`（PLUMED 会把它当成续行的开头）")
    else:
        failures += 1
        print("  FAIL 回写里还有 `...`")
    # 收尾的 `... 动作名`：手册说 PLUMED 会检查它和开头是否一致
    named_close, named_notes = read_plumed(
        "d1: DISTANCE ...\n ATOMS=1,2\n... DISTANCE\n")
    bad_close, bad_notes = read_plumed(
        "d1: DISTANCE ...\n ATOMS=1,2\n... COORDINATION\n")
    if (named_close.cvs and named_close.cvs[0].params == "ATOMS=1,2"
            and not named_notes
            and bad_close.cvs and any("结尾写的是" in note for note in bad_notes)):
        print("  ok   收尾 `... 动作名` 写得对就不吭声，写错了点出来"
              "（PLUMED 会直接报错）")
    else:
        failures += 1
        print(f"  FAIL 收尾动作名没检查：{named_notes} / {bad_notes}")
    # 没有收尾的续行、以及多出来的 `...`：都要说清楚，不能默默吃掉后文
    unclosed, unclosed_notes = read_plumed(
        "d1: DISTANCE ...\n ATOMS=1,2\nd2: DISTANCE ATOMS=1,3\n")
    stray, stray_notes = read_plumed("d1: DISTANCE ATOMS=1,2\n...\n")
    if (any("没有用 `...` 收尾" in note for note in unclosed_notes)
            and unclosed.cvs and "ATOMS=1,2" in unclosed.cvs[0].params
            and any("多出来的 `...`" in note for note in stray_notes)):
        print("  ok   没收尾的续行 / 多出来的 `...` 都会点名（不静默猜）")
    else:
        failures += 1
        print(f"  FAIL 续行的两种坏情况没说清楚：{unclosed_notes} / {stray_notes}")
    # `SWITCH={RATIONAL R_0=… NN=… MM=…}` 就是 R_0/NN/MM/D_0 本身
    # （Coordination.cpp 的关键字说明："When this keyword is present you no longer
    # need the NN, MM, D_0 and R_0 keywords"）——不能报"没写 R_0"
    switch_only = PlumedSetup(cvs=(PlumedCv(
        label="cv", action="COORDINATION",
        params="GROUPA=1-20 GROUPB=21-40 "
               "SWITCH={RATIONAL R_0=2.30 D_0=0.0 NN=12 MM=24} NLIST"),))
    if not cv_problems(switch_only, natoms=179, temperature=900.0):
        print("  ok   `SWITCH={RATIONAL …}` 顶掉 R_0/NN/MM/D_0：不会被误报成缺 R_0")
    else:
        failures += 1
        print(f"  FAIL SWITCH 写法被误报：{cv_problems(switch_only, 179, 900.0)}")
    really_missing = PlumedSetup(cvs=(PlumedCv(
        label="bad", action="COORDINATION", params="GROUPA=1-20 GROUPB=21-40"),))
    if any("没写 R_0" in note for note in cv_problems(really_missing, 179, 900.0)):
        print("  ok   真的没给 R_0（也没有 SWITCH）时照样点名")
    else:
        failures += 1
        print("  FAIL 缺 R_0 不再提醒了")

    # ---- σ 是**每个 ARG 一个值**（用户反馈：「PLUMED 能分别给多个 CV 定义 σ」）-- #
    # MetaD.cpp：`parseVector("SIGMA", sigma0_); if(sigma0_.size()!=
    # getNumberOfArguments()) error("number of arguments does not match number of
    # SIGMA parameters")` ——非 ADAPTIVE 时必须一个 ARG 一个值。
    pair = ("d1: DISTANCE ATOMS=1,2\n"
            "d2: DISTANCE ATOMS=1,3\n"
            "m: METAD ARG=d1,d2 SIGMA={s} HEIGHT=1 PACE=100 TEMP=900 FILE=HILLS\n")
    uniform, _u_notes = read_plumed(pair.format(s="0.15,0.15"))
    if (uniform.bias.sigma == 0.15
            and all(cv.sigma is None for cv in uniform.cvs)
            and "SIGMA=0.15,0.15" in render_setup(uniform, temperature=900.0)):
        print("  ok   `SIGMA=0.15,0.15` 就是「宽度 0.15」：读成默认值，"
              "回写仍是 SIGMA=0.15,0.15")
    else:
        failures += 1
        print(f"  FAIL 相同的 σ 列表没被读成默认值：{uniform.bias.sigma} / "
              f"{[cv.sigma for cv in uniform.cvs]}")
    one_for_two, one_notes = read_plumed(pair.format(s="0.15"))
    if (one_for_two.bias.sigma == 0.15
            and any("number of arguments does not match" in note
                    for note in one_notes)
            and "SIGMA=0.15,0.15" in render_setup(one_for_two, temperature=900.0)):
        print("  ok   只给一个 SIGMA 却有 2 个 ARG：点出 PLUMED 会拒绝，"
              "并写成 0.15,0.15")
    else:
        failures += 1
        print(f"  FAIL 单个 SIGMA 配多个 ARG 没说清楚：{one_notes}")
    single_arg, single_notes = read_plumed(
        "d1: DISTANCE ATOMS=1,2\n"
        "m: METAD ARG=d1 SIGMA=0.15 HEIGHT=1 PACE=100 TEMP=900 FILE=HILLS\n")
    if single_arg.bias.sigma == 0.15 and not single_notes:
        print("  ok   一个 CV 一个 σ（合法写法）不吭声")
    else:
        failures += 1
        print(f"  FAIL 合法写法被多嘴：{single_notes}")

    # ---- 「这个 CV 没人引用」那条警告的**两个方向** ----------------------- #
    # 真实反馈：一份文件里 HNCO_N / UREA_C 被判成"没人引用"。查下来文件确实被改过
    # （两个 COORDINATION 都用了 HNCO_C/UREA_N），所以警告是对的 —— 但这条规则必须
    # 两边都准：只被 GROUPA=/GROUPB= 引用的 GROUP **不能**被误报（那是上一版的写法）。
    group_file = (
        "HNCO_C: GROUP ATOMS=1,2,3\n"
        "HNCO_N: GROUP ATOMS=4,5,6\n"
        "UREA_N: GROUP ATOMS=7,8,9\n"
        "UREA_C: GROUP ATOMS=10,11,12\n"
        "cv1: COORDINATION GROUPA=HNCO_C GROUPB=UREA_N R_0=0.23 NLIST\n"
        "cv2: COORDINATION GROUPA=HNCO_N GROUPB=UREA_C R_0=0.23 NLIST\n"
        "m: METAD ARG=cv1,cv2 SIGMA=0.1,0.1 HEIGHT=1 PACE=100 TEMP=900 FILE=HILLS\n")
    group_setup, _group_notes = read_plumed(group_file)
    group_problems = cv_problems(group_setup, natoms=12, temperature=900.0)
    if not group_problems:
        print("  ok   只被 GROUPA=/GROUPB= 引用的 GROUP 不会被报成「没人引用」"
              "（四个分组都用上了 → 0 问题）")
    else:
        failures += 1
        print(f"  FAIL GROUP 的引用没认出来：{group_problems}")
    # ... and the leftover direction: the *definition* stays while nothing uses it
    # (that is what the user's file looked like after the second CV was rewritten to
    # the same group pair) - then that one row is reported, with the way out spelled
    # out, and the group that is still used is not
    leftover = group_file.replace("GROUPB=UREA_C", "GROUPB=UREA_N")
    leftover_setup, _leftover_notes = read_plumed(leftover)
    leftover_problems = cv_problems(leftover_setup, natoms=12, temperature=900.0)
    if (any("UREA_C" in note and "要用它就在别的动作里写" in note
            for note in leftover_problems)
            and not any("HNCO_N" in note for note in leftover_problems)):
        print("  ok   真正没人用的那一行会被点名（并说清怎么用、怎么删），"
              "而仍在 GROUPA 里的 HNCO_N 不会被连带误报")
    else:
        failures += 1
        print(f"  FAIL 没人引用的行没被点名：{leftover_problems}")

    # ---- 接触型 CV 生成器 ------------------------------------------------- #
    from cp2kbuild.plumedcv import ContactSpec, contact_cvs, parse_blocks

    if parse_blocks("19x1,8x20") == [(19, 1), (8, 20)] \
            and parse_blocks("8x20") == [(8, 20)] \
            and parse_blocks("8*3;19") == [(8, 3), (19, 1)]:
        print("  ok   分块写法：19x1,8x20 / 8x20 / 8*3;19 都读得对")
    else:
        failures += 1
        print(f"  FAIL 分块解析不对：{parse_blocks('8*3;19')}")
    rows, row_notes = contact_cvs(ContactSpec(blocks="19x1,8x20", r0=1.55, nn=12,
                                              mm=24, combine_label="cv_uu"),
                                  natoms=179, units="A")
    if (len(rows) == 21 and rows[0].label == "c1"
            and "GROUPA=21 GROUPB=28,30,36" in rows[0].params
            and "172,174" in rows[0].params and "20," not in rows[0].params
            and rows[-1].label == "cv_uu"
            and rows[-1].params.startswith("ARG=c1,c2,")
            and rows[-1].params.count("COEFFICIENTS=1,") == 1):
        print("  ok   接触型 CV 生成器：20 个配位数 + 1 行 COMBINE，"
              "每个分子排除自己")
    else:
        failures += 1
        print(f"  FAIL 生成器不对：{len(rows)} 行，第一行 {rows[0].params[:80]!r}")
    mismatch, mismatch_notes = contact_cvs(ContactSpec(blocks="19x1,8x19"), natoms=179)
    if any("原子序号会整体错位" in note for note in mismatch_notes):
        print("  ok   接触型 CV 生成器：分块总数与体系原子数不符会提醒")
    else:
        failures += 1
        print("  FAIL 分块总数不符没提醒")
    try:
        contact_cvs(ContactSpec(blocks="19x1,8x20", center_block=3), natoms=179)
        failures += 1
        print("  FAIL 不存在的块号没有报错")
    except ValueError:
        print("  ok   接触型 CV 生成器：块号/偏移越界直接报错")

    # the CP2K table must not leak into a draft built from the PLUMED table
    routed = Metadyn(enabled=True, use_plumed=True, plumed_file="my.plumed.dat",
                     plumed_setup=native,
                     cvs=(Cv(kind="distance", atoms=(1, 2)),),
                     metavars=(Metavar(colvar=1, scale=0.5),))
    routed_text = plumed_draft_of(routed, water, temperature=330.0)
    if ("cn: COORDINATIONNUMBER" in routed_text
            and "cv1: DISTANCE" not in routed_text
            and "COMBINE" not in routed_text):
        print("  ok   PLUMED 路线：填了专用表就不再用 CP2K 的 CV 表翻译")
    else:
        failures += 1
        print("  FAIL PLUMED 专用表没有接管草稿")
    legacy = plumed_draft_of(Metadyn(enabled=True, use_plumed=True,
                                     cvs=(Cv(kind="distance", atoms=(1, 2)),),
                                     metavars=(Metavar(colvar=1, scale=0.5),)),
                             water, temperature=330.0)
    if "cv1: DISTANCE" in legacy and "PLUMED 专用表是空的" in " ".join(
            md_problems(Metadyn(enabled=True, use_plumed=True,
                                cvs=(Cv(kind="distance", atoms=(1, 2)),),
                                metavars=(Metavar(colvar=1, scale=0.5),)), 3, 330.0)):
        print("  ok   PLUMED 路线：专用表为空时退回翻译，并明说是翻译")
    else:
        failures += 1
        print("  FAIL PLUMED 路线的退回路径没说清")

    # a reflective wall has no PLUMED equivalent and must say so
    reflect = plumed_input(Metadyn(
        enabled=True, use_plumed=True,
        cvs=(Cv(kind="distance", atoms=(1, 2)),),
        metavars=(Metavar(colvar=1,
                          wall=Wall(position=2.0, type="REFLECTIVE")),)),
        water, temperature=300.0)
    if "REFLECTIVE 墙在 PLUMED 里没有直译" in reflect:
        print("  ok   plumed.dat：反射墙明说没有直译")
    else:
        failures += 1
        print("  FAIL plumed.dat 对反射墙没有说明")

    # without a structure the element groups cannot be expanded: say `?` instead
    # of guessing a number
    blind = plumed_input(plumed_meta, None, temperature=330.0)
    if "GROUPB=?" in blind and "需要结构文件才能按元素展开" in blind:
        print("  ok   plumed.dat：没有结构时不猜原子组（留]? 并提示）")
    else:
        failures += 1
        print("  FAIL plumed.dat 在没有结构时猜了原子给")

    # On the PLUMED route CP2K reads *only* PLUMED_INPUT_FILE from &METADYN -
    # "only get the file name and read nothing else" (metadynamics_utils.F:129) -
    # so the native knobs and the &PRINT keys must not be written at all
    only_plumed = build(Spec(
        task="md", structure=water, vacuum=12.0, steps=1000,
        metadyn=Metadyn(enabled=True, use_plumed=True, plumed_file="p.dat",
                        do_hills=True, nt_hills=100, hill_height=2.0e-3,
                        well_tempered=True, temperature=330.0, delta_t=3000.0,
                        min_disp=0.05, hill_tail_cutoff=3.0,
                        cvs=(Cv(kind="distance", atoms=(1, 2)),),
                        metavars=(Metavar(colvar=1, scale=0.5,
                                          wall=Wall(position=2.0,
                                                    type="QUADRATIC", k=0.1)),),
                        print_colvar=True, print_hills=True, print_every=10)),
        index)
    metadyn_block = (next(s for s in only_plumed.sections
                          if s.name.upper() == "MOTION")
                     .subsection("FREE_ENERGY").subsection("METADYN"))
    names = sorted(keyword.name for keyword in metadyn_block.keywords)
    if names == ["PLUMED_INPUT_FILE", "USE_PLUMED"] and not metadyn_block.sections:
        print("  ok   PLUMED 路线一 &METADYN 只写 USE_PLUMED + 文件名" 
              "（CV/丘陵/墙 &PRINT 一个都不写：CP2K 在那条路上不读它们）")
    else:
        failures += 1
        print(f"  FAIL PLUMED 路线的 &METADYN 写了多余的东西：{names} / "
              f"{[s.name for s in metadyn_block.sections]}")

    # DO_HILLS F with hill *parameters* left over: they would be dropped from the
    # input, so say so instead of quietly ignoring them (a negative NT_HILLS is
    # the "only use the hills that came in with the restart" spelling, so that
    # combination is not a contradiction)
    idle = Metadyn(enabled=True, do_hills=False, nt_hills=100, hill_height=2.0e-3,
                   cvs=(Cv(kind="distance", atoms=(1, 2)),),
                   metavars=(Metavar(colvar=1),))
    idle_text = render_document(build(Spec(task="md", structure=water, vacuum=12.0,
                                           steps=1000, metadyn=idle),
                                      index).sections, title="t", notes=[])
    idle_problems = metadyn_problems(idle, natoms=3)
    relay_only = metadyn_problems(
        Metadyn(enabled=True, do_hills=False, nt_hills=-1,
                cvs=(Cv(kind="distance", atoms=(1, 2)),),
                metavars=(Metavar(colvar=1),)), natoms=3)
    if (any("还留着丘陵参数" in line for line in idle_problems)
            and "NT_HILLS" not in idle_text and "WW" not in idle_text
            and not any("还留着丘陵参数" in line for line in relay_only)):
        print("  ok   DO_HILLS F 却留着丘陵参数 → 提示它们会被丢掉"
              "（NT_HILLS -1 的「只用接力丘陵」不算矛盾）")
    else:
        failures += 1
        print(f"  FAIL DO_HILLS F 的丘陵参数没被检查：{idle_problems} / "
              f"{relay_only}")

    # the metadynamics print keys follow the MD steps, so an interval counted on
    # the SCF iterations - or the whole block inside a run that never enters MD -
    # would never write a file (input_cp2k_free_energy.F: each_iter_names MD)
    stale = build(Spec(task="md", structure=water, vacuum=12.0, steps=1000,
                       metadyn=metadyn_wt()), index)
    meta_print = (next(s for s in stale.sections if s.name.upper() == "MOTION")
                  .subsection("FREE_ENERGY").subsection("METADYN")
                  .subsection("PRINT"))
    hills_each = meta_print.subsection("HILLS").subsection("EACH")
    hills_each.set("QS_SCF", 20)
    hills_each.remove("MD")
    problems = validator.run(stale.sections)
    mismatch = [d for d in problems
                if d.code == "restart-print-level-mismatch"
                and "HILLS" in (d.message + d.hint)]
    if mismatch and mismatch[0].level == "warning":
        print("  ok   &METADYN/&PRINT/&HILLS 的 &EACH 写成 QS_SCF → warning"
              "（它按IMD 步计数）")
    else:
        failures += 1
        print(f"  FAIL 元动力学打印间隔的层级没被检查："
              f"{[(d.code, d.message[:40]) for d in problems][:4]}")

    # ... and the same block in a run that never iterates MD
    from cp2kbuild.parser import parse_input as _parse_text

    hand = render_document(build(Spec(task="md", structure=water, vacuum=12.0,
                                      steps=1000, metadyn=metadyn_wt()),
                                 index).sections, title="t", notes=[])
    hand = hand.replace("RUN_TYPE MD", "RUN_TYPE GEO_OPT")
    problems = validator.run(_parse_text(hand).sections)
    if any(d.code == "restart-print-level-mismatch" and "元动力学" in d.message
           for d in problems):
        print("  ok   GEO_OPT 里的 &METADYN/&PRINT → warning：那些间隔不会触只")
    else:
        failures += 1
        print("  FAIL GEO_OPT 里的元动力学打印没被检查")

    # read back a hand-written input: CP2K's aliases must be understood, and the
    # keywords the tool does not model must be *reported*, never dropped
    import tempfile

    from cp2kbuild.importinp import import_input

    with tempfile.TemporaryDirectory() as tmp:
        alias = Path(tmp) / "alias.inp"
        alias.write_text(
            "&GLOBAL\n   RUN_TYPE MD\n   PROJECT_NAME alias\n&END GLOBAL\n"
            "&FORCE_EVAL\n   METHOD FIST\n   &MM\n   &END MM\n   &SUBSYS\n"
            "      &COLVAR\n         &COORDINATION\n"
            "            KINDS_FROM O\n            KINDS_TO H\n"
            "            R_0 1.25\n            EXPON_NUMERATOR 8\n"
            "            EXPON_DENOMINATOR 14\n            KINDS_TO_B Cl\n"
            "         &END COORDINATION\n      &END COLVAR\n   &END SUBSYS\n"
            "&END FORCE_EVAL\n"
            "&MOTION\n   &MD\n      STEPS 10\n   &END MD\n"
            "   &FREE_ENERGY\n      METHOD METADYN\n      &METADYN\n"
            "         DO_HILLS T\n         NT_HILLS 20\n         WW 1.0E-3\n"
            "         &METAVAR\n            COLVAR 1\n            SCALE 0.5\n"
            "         &END METAVAR\n         &PRINT\n            &HILLS ON\n"
            "            &END HILLS\n         &END PRINT\n      &END METADYN\n"
            "   &END FREE_ENERGY\n&END MOTION\n", encoding="utf-8")
        report = import_input(alias)
    read = report.get("metadyn")
    coordination = read.cvs[0] if read is not None and read.cvs else None
    if (coordination is not None and coordination.r0 == 1.25
            and coordination.nn == 8 and coordination.nd == 14
            and read.metavars[0].scale == 0.5 and read.nt_hills == 20
            and read.print_colvar is False and read.print_hills is True):
        print("  ok   手写输入里的 R_0 / EXPON_NUMERATOR / EXPON_DENOMINATOR "
              "被当成 R0/NN/ND 读出来（&PRINT 里缺的NCOLVAR 就是关的）")
    else:
        failures += 1
        print(f"  FAIL 别名没读出来：{coordination} / {read}")
    unread = "。".join(report.unread)
    if "KINDS_TO_B" in unread:
        print(f"  ok   工具不建模的关键字（KINDS_TO_B）进了「没读」清单，"
              f"不会悄悄消失：{unread}")
    else:
        failures += 1
        print(f"  FAIL KINDS_TO_B 被悄悄丢掉了：{unread}")

    # 温度打印：打印键与关键字必须成对（md_energies.F 只警告不写文件）， 
    # 而且每个新关键字都要在手册里真实存在
    # ------------------------------------------------------------------ #
    print()
    print("  -- MD 温度打印，IMD/&PRINT 一 &THERMOSTAT/&PRINT， --")

    def motion_md(result):
        motion = next((s for s in result.sections
                       if s.name.upper() == "MOTION"), None)
        return motion.subsection("MD") if motion is not None else None

    # CP2K writes the per-kind temperature only when the *keyword* is on: with
    # the print key alone it just warns, so that combination has to be an error
    broken = build(Spec(task="md", structure=silicon, steps=100,
                        md_temp_kind_print=True), index)
    motion_md(broken).remove("TEMP_KIND")
    codes = {d.code for d in validator.run(broken.sections)}
    if "tempkind-print-without-keyword" in codes:
        print("  ok   只开打印键不写 &MD TEMP_KIND：校验器把nerror（CP2K 只会警告）")
    else:
        failures += 1
        print(f"  FAIL 打印键缺关键字没被抓住：{sorted(codes)}")

    # the other way round: computed and thrown away
    lonely = build(Spec(task="md", structure=silicon, steps=100), index)
    motion_md(lonely).set("TEMP_KIND", True)
    codes = {d.code for d in validator.run(lonely.sections)}
    if "tempkind-keyword-without-print" in codes:
        print("  ok   只开 &MD TEMP_KIND 不写打印键：提示它算完就扔了")
    else:
        failures += 1
        print(f"  FAIL 关键字缺打印键没被提示：{sorted(codes)}")

    # REGION GLOBAL + the print key is the global temperature again
    glob = build(Spec(task="md", structure=silicon, ensemble="NVT", steps=100,
                      thermostat_temp_print=True), index)
    notes = {d.code for d in validator.run(glob.sections) if d.level == "info"}
    if "thermostat-temp-print-global" in notes:
        print("  ok   热浴区域是 GLOBAL 时说明它和--1.ener 的 Temp 列重多")
    else:
        failures += 1
        print(f"  FAIL GLOBAL 热浴温度打印没有提示：{sorted(notes)}")

    # one kind only -> the per-kind temperature is the global one
    one_kind = build(Spec(task="md", structure=silicon, steps=100,
                          md_temp_kind_print=True), index)
    notes = {d.code for d in validator.run(one_kind.sections) if d.level == "info"}
    if "tempkind-single-kind" in notes:
        print("  ok   只有一一 kind 时说明每一 kind 的温度就是全局温度")
    else:
        failures += 1
        print(f"  FAIL 单 kind 没被提示：{sorted(notes)}")

    if db is not None:
        written = [(("MOTION", "MD"), "TEMP_KIND"),                   (("MOTION", "MD", "PRINT", "TEMP_KIND", "EACH"), "MD"),
                   (("MOTION", "MD", "PRINT", "ENERGY"), "FILENAME"),
                   (("MOTION", "MD", "PRINT", "ENERGY", "EACH"), "MD"),
                   (("MOTION", "MD", "THERMOSTAT"), "REGION"),
                   (("MOTION", "MD", "THERMOSTAT", "PRINT", "TEMPERATURE", "EACH"),
                    "MD")]
        # the print keys themselves are *sections* (``&PRINT/&ENERGY``), and so
        # is ``&EACH`` - those are checked as sections, not as keywords
        written_sections = [("MOTION", "MD", "PRINT", "ENERGY"),
                            ("MOTION", "MD", "PRINT", "TEMP_KIND"),
                            ("MOTION", "MD", "PRINT", "TEMP_KIND", "EACH"),
                            ("MOTION", "MD", "THERMOSTAT", "PRINT", "TEMPERATURE"),
                            ("MOTION", "MD", "THERMOSTAT", "PRINT", "TEMPERATURE",
                             "EACH")]
        missing_kw = [f"{'/'.join(path)}/{kw}" for path, kw in written
                      if not db.has_keyword(list(path), kw)]
        missing_sec = [f"&{'/&'.join(path)}" for path in written_sections
                       if not db.has_section(list(path))]
        if missing_kw or missing_sec:
            failures += 1
            print(f"  FAIL 温度打印写出的段/关键字手册里没有：{missing_kw + missing_sec}")
        else:
            print(f"  ok   温度打印的 {len(written)} 个关键字 + "
                  f"{len(written_sections)} 个段（含 &EACH）手册里都有")
        thermostat_info = db.section(("MOTION", "MD", "THERMOSTAT"))
        region_values = {str(v) for v in
                         (thermostat_info.keywords if thermostat_info else [])}
        if "REGION" in region_values:
            print("  ok   &THERMOSTAT REGION 在手册的 THERMOSTAT 段里")
        else:
            failures += 1
            print(f"  FAIL THERMOSTAT 段里没有 REGION：{sorted(region_values)[:6]}")

    # ------------------------------------------------------------------ #
    # 升温 / 降温：因子怎么算、什么系综能用、用不上的不许写进输入 
    # ------------------------------------------------------------------ #
    print()
    print("  -- 升温 / 降温（CP2K 自带的退火开关） --")

    from cp2kbuild.recipes import annealing_mechanism, plan_ramp

    ramp = plan_ramp(300.0, 600.0, 20000, 0.5, "LANGEVIN")
    # the factor is rounded to 12 decimals so that the GUI's spin box and the
    # command line end up with the same double (byte-identical inputs)
    if abs(ramp.factor - round(2.0 ** (1 / 20000), 12)) < 1e-15 \
            and ramp.mechanism == "temperature" \
            and abs(ramp.duration - 10.0) < 1e-9 \
            and abs(ramp.rate - 30.0) < 1e-9:
        print(f"  ok   温度斜坡：LANGEVIN 用 TEMPERATURE_ANNEALING，E"
              f"f=(600/300)^(1/20000)={ramp.factor:.10f}，"
              f"{ramp.duration:g} ps 线 {ramp.rate:g} K/ps")
    else:
        failures += 1
        print(f"  FAIL 温度斜坡算错了：{ramp}")
    velocity = plan_ramp(300.0, 600.0, 20000, 0.5, "NVE")
    if velocity.mechanism == "velocity" \
            and abs(velocity.factor - round(2.0 ** (1 / 40000), 12)) < 1e-15:
        print(f"  ok   速度缩放：NVE 用 ANNEALING，温度按平方走，"
              f"所以指数是 1/(2N)：{velocity.factor:.10f}")
    else:
        failures += 1
        print(f"  FAIL 速度缩放的因子算错了：{velocity}")
    if (annealing_mechanism("LANGEVIN") == "temperature"
            and annealing_mechanism("NVE") == "velocity"
            and annealing_mechanism("NPE_I") == "velocity"
            and annealing_mechanism("NVT") is None
            and annealing_mechanism("NPT_F") is None):
        print("  ok   系综决定机制：LANGEVIN→温度、NVE/NPE→速度、NVT/NPT 两条都不行")
    else:
        failures += 1
        print("  FAIL 机制判定不对，"
              + str({e: annealing_mechanism(e)
                     for e in ("LANGEVIN", "NVE", "NPE_I", "NVT", "NPT_F")}))
    try:
        plan_ramp(300.0, 600.0, 5000, 0.5, "NVT")
        failures += 1
        print("  FAIL NVT 下要求升降温居然没有报错")
    except ValueError as exc:
        if "LANGEVIN" in str(exc) and "中止" in str(exc):
            print("  ok   NVT 下要求升降温直接报错，并说清两条路各自为什么不行")
        else:
            failures += 1
            print(f"  FAIL 报错信息没说清原因：{exc}")

    # what the ensemble cannot use must not be written - CP2K would either abort
    # (ANNEALING + thermostat) or silently ignore it (TEMPERATURE_ANNEALING)
    dropped = Spec(task="md", structure=water, vacuum=12.0, ensemble="NVT",
                   temperature_annealing=1.001, annealing=0.999,
                   annealing_cell=0.999)
    dropped_text = render_document(build(dropped, index).sections, title="t",
                                   notes=[])
    leaked = [keyword for keyword in ("ANNEALING", "ANNEALING_CELL",
                                      "TEMPERATURE_ANNEALING")
              if re.search(rf"^\s*{keyword}\s", dropped_text, re.M)]
    if not leaked:
        print("  ok   NVT 下三个退火关键字一个都不写（写了会中止或静默无效）")
    else:
        failures += 1
        print(f"  FAIL NVT 下还是写出了 {leaked}")
    # ... and the validator says so for inputs that were written by hand
    hand = ("&GLOBAL\n   RUN_TYPE MD\n   PROJECT_NAME hand\n&END GLOBAL\n"
            "&MOTION\n   &MD\n      ENSEMBLE NVT\n      STEPS 100\n"
            "      TEMPERATURE 300\n      TEMPERATURE_ANNEALING 1.001\n"
            "      ANNEALING 0.999\n      &THERMOSTAT\n         TYPE NOSE\n"
            "      &END THERMOSTAT\n   &END MD\n&END MOTION\n"
            "&FORCE_EVAL\n   METHOD QuickStep\n   &DFT\n      &MGRID\n"
            "         CUTOFF 300\n         REL_CUTOFF 60\n      &END MGRID\n"
            "   &END DFT\n   &SUBSYS\n      &COORD\n         H 0 0 0\n"
            "         H 0 0 0.74\n      &END COORD\n   &END SUBSYS\n"
            "&END FORCE_EVAL\n")
    from cp2kbuild.parser import parse_input as _parse_hand
    hand_sections = _parse_hand(hand).sections
    hand_codes = {d.code for d in Validator(db, index).run(hand_sections)}
    if "annealing-with-thermostat" in hand_codes \
            and "temperature-annealing-inactive" in hand_codes:
        print("  ok   手写输入里这两个坑也被抓：ANNEALING×恒温器 = error。"
              "TEMPERATURE_ANNEALING 面 LANGEVIN = warning")
    else:
        failures += 1
        print(f"  FAIL 手写输入的退火问题没抓全：{sorted(hand_codes)}")

    # the ramp can also be continued across a restart
    chained = Spec(task="md", structure=water, vacuum=12.0, ensemble="LANGEVIN",
                   ext_restart_file="prev-1.restart", ext_restart_take=("vel",),
                   temperature_annealing=1.00003465796,
                   restart_temperature_annealing=True)
    chained_text = render_document(build(chained, index).sections, title="t",
                                   notes=[])
    if "RESTART_TEMPERATURE_ANNEALING T" in chained_text \
            and "TEMPERATURE_ANNEALING 1.00003465796" in chained_text:
        print("  ok   接力时用 RESTART_TEMPERATURE_ANNEALING T 让斜坡接着起")
    else:
        failures += 1
        print("  FAIL 接力斜坡没有写ARESTART_TEMPERATURE_ANNEALING")
    no_ramp = Spec(task="md", structure=water, vacuum=12.0,
                   ext_restart_file="prev-1.restart",
                   restart_temperature_annealing=True)
    no_ramp_codes = {d.code for d in
                     Validator(db, index).run(build(no_ramp, index).sections)}
    if "restart-annealing-without-ramp" in no_ramp_codes:
        print("  ok   没有温度斜坡却要求接着斜坡时，校验器提醒（CP2K 自己也警告）")
    else:
        failures += 1
        print(f"  FAIL 没抓「有 RESTART_TEMPERATURE_ANNEALING 但没因子」："
              f"{sorted(no_ramp_codes)}")

    # the friction is what actually controls the temperature in Langevin
    no_friction = Spec(task="md", structure=water, vacuum=12.0,
                       ensemble="LANGEVIN", langevin_gamma=0.0,
                       temperature_annealing=1.0001)
    friction_codes = {d.code for d in
                      Validator(db, index).run(build(no_friction, index).sections)}
    if "langevin-without-friction" in friction_codes:
        print("  ok   LANGEVIN 体 GAMMA=0（CP2K 默认）时提醒：没有控温，斜坡无效")
    else:
        failures += 1
        print(f"  FAIL 没抓 Langevin 没有摩擦：{sorted(friction_codes)}")

    # ------------------------------------------------------------------ #
    # 氘代（H→D）：元素/基组/赝势不变、只改核质量；两种 用不一 要说是 
    # ------------------------------------------------------------------ #
    print()
    print("  -- 氘代（H→D） --")

    from cp2kbuild.recommend import DEUTERIUM_MASS

    heavy = Spec(task="md", structure=water, vacuum=12.0, steps=1000,
                 timestep=1.0, h_to_d=True)
    heavy_result = build(heavy, index)
    heavy_text = render_document(heavy_result.sections, title="t", notes=[])
    heavy_codes = {d.code for d in validator.run(heavy_result.sections)}
    if (f"MASS {DEUTERIUM_MASS}" in heavy_text and "ELEMENT H" in heavy_text
            and "deuterated-timestep" not in heavy_codes):
        print(f"  ok   氘代写的是 `&KIND H / MASS {DEUTERIUM_MASS}`（元素仍是OH），"
              "1.0 fs 步长时不再提示")
    else:
        failures += 1
        print(f"  FAIL 氘代没写对或提示错了：{heavy_codes}")

    slow = Spec(task="md", structure=water, vacuum=12.0, steps=1000,
                timestep=0.5, h_to_d=True)
    slow_codes = {d.code for d in validator.run(build(slow, index).sections)}
    if "deuterated-timestep" in slow_codes:
        print("  ok   氘代了却还用 0.5 fs：提示可以提分E1.0 fs（省一半机时）")
    else:
        failures += 1
        print(f"  FAIL 氘代 + 0.5 fs 没有提示：{sorted(slow_codes)}")

    no_h = build(Spec(task="md", structure=silicon, steps=100, h_to_d=True), index)
    if (f"MASS {DEUTERIUM_MASS}" not in
            render_document(no_h.sections, title="t", notes=[])
            and any("没有氢" in w for w in no_h.warnings)):
        print("  ok   体系里没有氢：不写 MASS，并说明改了也没有用")
    else:
        failures += 1
        print(f"  FAIL 没有氢的体系没被说明：{no_h.warnings}")

    classical_d = Spec(task="md", structure=water, vacuum=12.0, method="FIST",
                       mm_route="IN_INPUT", mm_ff_file="ff.inc",
                       mm_lj=[MmLennardJones(["O", "O"], 0.2, 3.2, 10.0)],
                       mm_charges=[("O", -0.8), ("H", 0.4)],
                       steps=100, h_to_d=True)
    classical_result = build(classical_d, index)
    if any("经典力场" in w and "h_to_d" in w for w in classical_result.warnings):
        print("  ok   经典力场路线：说明质量在拓扑文件里，`&KIND MASS` 不会被读")
    else:
        failures += 1
        print(f"  FAIL FIST + 氘代没有说明：{classical_result.warnings}")

    if db is not None:
        if db.has_keyword(("FORCE_EVAL", "SUBSYS", "KIND"), "MASS"):
            print("  ok   手册确认 `&KIND MASS` 存在（氘代就写在它上面）")
        else:
            failures += 1
            print("  FAIL 手册里没有 &KIND MASS")

    # ------------------------------------------------------------------ #
    # print keys take an *enum* parameter (ON/OFF/SILENT/LOW/...), never T/F:
    # CP2K's input parser maps enumerations strictly and aborts on "T"
    # ------------------------------------------------------------------ #
    print()
    print("  -- print key 的段参数 --")

    checks = [
        ("energy", dict(task="energy", structure=water, vacuum=12.0,
                        print_forces=True, print_dipole=True)),
        ("md", dict(task="md", structure=silicon, steps=100)),
        ("cell_opt", dict(task="cell_opt", structure=silicon)),
        ("freq", dict(task="freq", structure=water, vacuum=12.0)),
    ]
    bad: list[str] = []
    for label, kwargs in checks:
        text = render_document(build(Spec(**kwargs), index).sections,
                               title="t", notes=[])
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("&") or " " not in stripped:
                continue
            section, _, parameter = stripped[1:].partition(" ")
            if parameter.strip().upper() in ("T", "F", "TRUE", "FALSE") \
                    and section.split()[0].upper() in (
                        "FORCES", "MOMENTS", "MULLIKEN", "STRESS_TENSOR",
                        "RESTART", "TRAJECTORY", "VELOCITIES", "CELL",
                        "MOLDEN_VIB"):
                bad.append(f"{label}: {stripped}")
    if bad:
        failures += 1
        print(f"  FAIL 打印键写成了 T/F：{bad[:4]}")
    else:
        print("  ok   所有 print key 都用 ON（不是AT）e")

    # ... and the validator has to catch it when somebody writes T by hand
    from cp2kbuild.parser import parse_input

    # ... and print keys must sit where the manual says (&SCF/&PRINT/&RESTART,
    # not directly under &SCF - Section.add() returns the child, so a chained
    # "scf.add(Section('PRINT').add(x))" silently flattens the tree)
    default_text = render_document(
        build(Spec(task="energy", structure=silicon), index).sections,
        title="t", notes=[])
    tree = {s.name.upper(): s for s in parse_input(default_text).sections}
    force_eval = tree.get("FORCE_EVAL")
    dft = force_eval.subsection("DFT") if force_eval else None
    scf = dft.subsection("SCF") if dft else None
    print_block = scf.subsection("PRINT") if scf else None
    nested = print_block is not None and print_block.subsection("RESTART") is not None
    if nested:
        print("  ok   &SCF/&PRINT/&RESTART 挂在了正确的位置")
    else:
        failures += 1
        print("  FAIL &SCF 下的 &RESTART 不在 &PRINT 里面")

    handwritten = """
&GLOBAL
  PROJECT_NAME t
  RUN_TYPE ENERGY
&END GLOBAL
&FORCE_EVAL
  METHOD QUICKSTEP
  &DFT
    &SCF
    &END SCF
  &END DFT
  &PRINT
    &FORCES T
    &END FORCES
  &END PRINT
&END FORCE_EVAL
"""
    codes = {d.code for d in validator.run(parse_input(handwritten).sections)}
    if "boolean-section-parameter" in codes:
        print("  ok   校验器抓到了 &FORCES T（boolean-section-parameter）")
    else:
        failures += 1
        print(f"  FAIL 校验器没抓到 &FORCES T：{sorted(codes)}")

    # OT + SMEAR without ROTATION cannot describe fractional occupations
    ot_smear = """
&GLOBAL
  PROJECT_NAME t
  RUN_TYPE ENERGY
&END GLOBAL
&FORCE_EVAL
  METHOD QUICKSTEP
  &DFT
    &SCF
      &OT
      &END OT
      &SMEAR
        METHOD FERMI_DIRAC
      &END SMEAR
    &END SCF
  &END DFT
&END FORCE_EVAL
"""
    levels = {d.code: d.level
              for d in validator.run(parse_input(ot_smear).sections)}
    if levels.get("smear-with-ot") == "error":
        print("  ok   OT + SMEAR 且没有 ROTATION →nerror（OT 描述不了分数占据）")
    else:
        failures += 1
        print(f"  FAIL OT + SMEAR 没有 ROTATION 时应当是 error：{levels.get('smear-with-ot')}")

    ot_smear_rotation = ot_smear.replace("      &OT\n",
                                         "      &OT\n        ROTATION T\n")
    levels = {d.code: d.level
              for d in validator.run(parse_input(ot_smear_rotation).sections)}
    if levels.get("smear-with-ot") == "warning":
        print("  ok   OT + SMEAR + ROTATION T → 只剩 warning（组合少见但合法）")
    else:
        failures += 1
        print(f"  FAIL OT + SMEAR + ROTATION T 应当是 warning：{levels.get('smear-with-ot')}")
    # CP2K's ADDED_MOS requirement has *no* OT guard:
    #   qs_environment.F: IF (smear%do_smear) IF (added_mos(1) == 0) CPABORT(
    #   "Extra MOs (ADDED_MOS) are required for smearing")
    # The tool used to claim the opposite ("ADDED_MOS has no effect with OT").
    if levels.get("smear-without-added-mos") == "error":
        print("  ok   OT + SMEAR + ROTATION T 也要 ADDED_MOS（CP2K 的检查没有NOT 分支）")
    else:
        failures += 1
        print("  FAIL OT 路线一 SMEAR 缩 ADDED_MOS 应当是nerror，" 
              f"{levels.get('smear-without-added-mos')}")
    with_added = ot_smear_rotation.replace("      &SMEAR",
                                           "      ADDED_MOS 20\n      &SMEAR", 1)
    levels_added = {d.code: d.level
                    for d in validator.run(parse_input(with_added).sections)}
    if "smear-without-added-mos" not in levels_added:
        print("  ok   补上 ADDED_MOS 20 之后不再报错")
    else:
        failures += 1
        print("  FAIL 有 ADDED_MOS 时不该再报缺 ADDED_MOS")

    # ------------------------------------------------------------------ #
    # regression assertions for the audit fixes (each one was a real bug)
    # ------------------------------------------------------------------ #
    from cp2kbuild.structures import read_cif, read_cp2k_coord, read_pdb

    # (1) a fractional CIF: the operator list is a loop_ and the identity is
    #     "x,y,z" (the guard used to reject its own fallback operator, so *every*
    #     CIF raised, and the loop_ operators were never collected)
    cif = "\n".join([
        "data_test", "_cell_length_a 4.21", "_cell_length_b 4.21",
        "_cell_length_c 4.21", "_cell_angle_alpha 90", "_cell_angle_beta 90",
        "_cell_angle_gamma 90", "loop_", "_symmetry_equiv_pos_as_xyz",
        "'x,y,z'", "'-x,-y,z'", "'-x,y,-z'", "'x,-y,-z'", "loop_",
        "_atom_site_label", "_atom_site_type_symbol", "_atom_site_fract_x",
        "_atom_site_fract_y", "_atom_site_fract_z",
        "Mg1 Mg 0.0 0.0 0.0", "O1 O 0.25 0.25 0.25",
    ])
    cif_structure = read_cif(cif)
    if cif_structure.n_atoms() == 5 and cif_structure.counts() == {"Mg": 1, "O": 4}:
        print("  ok   CIF：loop_ 的 4 个对称操作被收下（Mg 在原点]→ 1 个，"
              "O 在一般位置]→ 4 个）")
    else:
        failures += 1
        print(f"  FAIL CIF 对称操作没生效：{cif_structure.counts()}")

    # (2) a PDB without the element column: "1HH3" is a hydrogen and " CA " is an
    #     alpha carbon - reading it as calcium both invented an element and
    #     dropped every name that is not an element symbol
    def _pdb(serial, name, resname, resid, x, y, z):
        return (f"ATOM  {serial:5d} {name:<4}{resname:>3} {resid:4d}      "
                f"{x:8.3f}{y:8.3f}{z:8.3f}")
    pdb = "\n".join([_pdb(1, "1HH3", "ACE", 1, 0.0, 0.0, 0.0),
                     _pdb(2, " CA ", "GLY", 2, 1.5, 0.0, 0.0),
                     _pdb(3, "2HA ", "GLY", 2, 2.0, 1.0, 0.0)])
    pdb_structure = read_pdb(pdb)
    if pdb_structure.n_atoms() == 3 and [a.symbol for a in pdb_structure.atoms] == \
            ["H", "C", "H"]:
        print("  ok   PDB：没有元素列时按名字列解码（1HH3→H。  CA '→C 不是钙）")
    else:
        failures += 1
        print(f"  FAIL PDB 元素/原子数不对：{[(a.symbol) for a in pdb_structure.atoms]}")

    # (3) &COORD with UNIT bohr: reading the numbers as angstrom scaled the
    #     structure by 1.8897 (Li2: 5.3546 A instead of 2.8336 A)
    bohr = ("&FORCE_EVAL\n &SUBSYS\n  &COORD\n   UNIT bohr\n"
            "   Li 0.0 0.0 -2.67732437187761072\n"
            "   Li 0.0 0.0 2.67732437187761072\n  &END COORD\n &END SUBSYS\n"
            "&END FORCE_EVAL\n")
    li = read_cp2k_coord(bohr)
    distance = abs(li.atoms[1].position[2] - li.atoms[0].position[2])
    if abs(distance - 2.8336) < 0.001:
        print(f"  ok   &COORD UNIT bohr 被换算：Li-Li {distance:.4f} Å")
    else:
        failures += 1
        print(f"  FAIL UNIT bohr 没换算：Li-Li {distance:.4f} Å（应一_2.8336）")

    # (4) SCALED T (not the bare SCALED) and an expression coordinate: the first
    #     was ignored (fractions read as Å) and the second dropped the atom
    scaled = ("&FORCE_EVAL\n &SUBSYS\n  &CELL\n   ABC 5.0 6.0 7.0\n  &END CELL\n"
              "  &COORD\n   SCALED T\n   Zr 0.5 0.5 0.5\n   O 1/2 1/4 0.0\n"
              "  &END COORD\n &END SUBSYS\n&END FORCE_EVAL\n")
    zro2 = read_cp2k_coord(scaled)
    positions = [tuple(round(v, 6) for v in a.position) for a in zro2.atoms]
    if zro2.n_atoms() == 2 and positions == [(2.5, 3.0, 3.5), (2.5, 1.5, 0.0)]:
        print("  ok   &COORD SCALED T + 1/2 表达式：2 个原子且坐标换算正确")
    else:
        failures += 1
        print(f"  FAIL SCALED/表达式坐标：{zro2.n_atoms()} 个原子 {positions}")

    # (5) the validator rules the audit found missing
    def _levels(text: str) -> dict:
        return {d.code: d.level for d in validator.run(parse_input(text).sections)}

    kinetics_no_stress = """&GLOBAL
  RUN_TYPE MD
&END GLOBAL
&FORCE_EVAL
  METHOD QUICKSTEP
  &DFT
    &SCF
      SCF_GUESS ATOMIC
    &END SCF
  &END DFT
&END FORCE_EVAL
&MOTION
  &MD
    ENSEMBLE NPT_I
    STEPS 10
    &THERMOSTAT
      TYPE NOSE
    &END THERMOSTAT
    &BAROSTAT
      PRESSURE 1.0
    &END BAROSTAT
  &END MD
&END MOTION
"""
    found = _levels(kinetics_no_stress)
    if found.get("md-no-stress-tensor") == "error":
        print("  ok   NPT_I 不给 STRESS_TENSOR →nerror（CP2K 会 abort）")
    else:
        failures += 1
        print(f"  FAIL NPT_I 缺应力张量应当是 error：{found.get('md-no-stress-tensor')}")
    with_stress = kinetics_no_stress.replace("  METHOD QUICKSTEP",
                                             "  METHOD QUICKSTEP\n  STRESS_TENSOR ANALYTICAL")
    if "md-no-stress-tensor" not in _levels(with_stress):
        print("  ok   加上 STRESS_TENSOR ANALYTICAL 之后不再把")
    else:
        failures += 1
        print("  FAIL 有 STRESS_TENSOR 时不该再把")
    if _levels(kinetics_no_stress.replace("NPT_I", "HYDROSTATICSHOCK")
               ).get("ensemble-not-implemented") == "error":
        print("  ok   ENSEMBLE HYDROSTATICSHOCK →nerror（CP2K 没有对应积分器）")
    else:
        failures += 1
        print("  FAIL HYDROSTATICSHOCK 应当把sensemble-not-implemented")
    both_scf = kinetics_no_stress.replace(
        "      SCF_GUESS ATOMIC",
        "      SCF_GUESS ATOMIC\n      &OT\n      &END OT\n      &DIAGONALIZATION\n"
        "      &END DIAGONALIZATION")
    if _levels(both_scf).get("ot-and-diagonalization") == "warning":
        print("  ok   &OT + &DIAGONALIZATION → warning（@IF 分支里是合法的）")
    else:
        failures += 1
        print("  FAIL OT+DIAGONALIZATION 应当把 warning")
    zero_cutoff = kinetics_no_stress.replace(
        "    &SCF",
        "    &MGRID\n      CUTOFF 0\n    &END MGRID\n    &SCF")
    if _levels(zero_cutoff).get("cutoff-not-positive") == "error":
        print("  ok   CUTOFF 0 →nerror（CP2K 的 CPASSERT 会 abort）")
    else:
        failures += 1
        print(f"  FAIL CUTOFF 0 应当是nerror：{_levels(zero_cutoff).get('cutoff-not-positive')}")
    two_global = kinetics_no_stress.replace(
        "&GLOBAL\n  RUN_TYPE MD\n&END GLOBAL\n",
        "&GLOBAL\n  RUN_TYPE MD\n&END GLOBAL\n&GLOBAL\n  RUN_TYPE MD\n&END GLOBAL\n", 1)
    if _levels(two_global).get("section-not-repeatable") == "error":
        print("  ok   两个 &GLOBAL →nerror（顶层段重复，CP2K 会 abort）")
    else:
        failures += 1
        print("  FAIL 重复的顶层 &GLOBAL 应当把nerror")

    # (6) basis set / pseudopotential pairing: the BLYP request for Cd silently
    #     got an LDA potential, and a valence-less basis reported another entry's
    #     electron count so the mismatch check could never fire
    from cp2kbuild.recommend import choose_assignments
    cd = choose_assignments(["Cd"], index, functional="BLYP")
    if cd.problems and cd.assignments["Cd"].potential.startswith("GTH-"):
        print(f"  ok   Cd + BLYP：没有 BLYP 赝势时会明说（{cd.assignments['Cd'].potential}） ")
    else:
        failures += 1
        print("  FAIL Cd + BLYP 的替换没有报周")
    iron = choose_assignments(["Fe"], index, basis_override="DZV-GTH-PADE")
    picked = iron.assignments["Fe"]
    if picked.potential_valence == index.valence("potential", str(picked.potential),
                                                 "Fe"):
        print(f"  ok   价电子记账用了选中的那个势（{picked.potential} → "
              f"{picked.potential_valence}，")
    else:
        failures += 1
        print(f"  FAIL 价电子记账串了：{picked.potential} 把 {picked.potential_valence}")
    uranium = choose_assignments(["U"], index, basis_override="DZVP-MOLOPT-GTH-q11")
    if not uranium.problems and uranium.assignments["U"].potential == "GTH-PBE-q11":
        print("  ok   U + q11 基组找到了 AcPP2 的对应赝势（GTH-PBE-q11）)")
    else:
        failures += 1
        print(f"  FAIL U/q11 配对不对：{uranium.assignments['U'].potential} "
              f"{uranium.problems}")

    # (7) CP2K's separators: "FILENAME =RESTART" is the value RESTART (not
    #     "=RESTART"), "1/3,2/3; 1/3,1/3" is six numbers, and a bare "F" is the
    #     element fluorine, not a boolean
    from cp2kbuild.parser import parse_input as _parse
    sep = _parse("&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n"
                 "&FORCE_EVAL\n  &DFT\n    &SCF\n      FILENAME =RESTART\n"
                 "      ROKS_PARAMETERS 1/3,2/3; 1/3,1/3; 2/3,1/3\n"
                 "      PROJECT F\n    &END SCF\n  &END DFT\n"
                 "&END FORCE_EVAL\n").sections
    scf = sep[1].subsection("DFT").subsection("SCF")
    filename = scf.get("FILENAME")
    roks = scf.keyword("ROKS_PARAMETERS")
    residue = scf.get("PROJECT")
    if filename == "RESTART" and residue == "F" and roks is not None \
            and len(roks.values) == 6:
        print("  ok   解析器：`=value`、逗号/分号分隔、裸 `F`（氟）都按 CP2K 的读法")
    else:
        failures += 1
        print(f"  FAIL 分隔第]装 F：FILENAME={filename!r} PROJECT={residue!r} "
              f"ROKS={roks.values if roks else None}")

    # (8) DESCRIBE is a parser directive, not a keyword of the section
    describe = "&GLOBAL\n  RUN_TYPE ENERGY\n&END GLOBAL\n&MOTION\n  &MD\n" \
               "    DESCRIBE\n    ENSEMBLE NVT\n  &END MD\n&END MOTION\n"
    if "unknown-keyword" not in _levels(describe):
        print("  ok   DESCRIBE 被当成 CP2K 的解析器指令（不再误把ounknown-keyword）e")
    else:
        failures += 1
        print("  FAIL DESCRIBE 仍然被当成未知关键字")

    # (9) a lone unknown token is a warning (it is how @IF branches and leftover
    #     placeholders look), while an unknown keyword *with* a value stays an error
    lone = _levels(kinetics_no_stress.replace("&END FORCE_EVAL",
                                              "  XXXXXXX\n&END FORCE_EVAL"))
    valued = _levels(kinetics_no_stress.replace("&END FORCE_EVAL",
                                                "  NOPE 3\n&END FORCE_EVAL"))
    if lone.get("unknown-keyword-bare") == "warning" \
            and valued.get("unknown-keyword") == "error":
        print("  ok   单个孤立记号 → warning；带取值的未知关键子]→nerror")
    else:
        failures += 1
        print(f"  FAIL 未知关键字的严重度不对：{lone} / {valued}")

    # (10) Bravais detection uses a *relative* length tolerance: a=4, b=4, c=6 is
    #      not cubic (the old 2 Å window called it cub and picked the wrong path)
    from cp2kbuild.kpoints import detect_lattice
    from cp2kbuild.structures import Cell as _Cell2
    kinds = (detect_lattice(_Cell2.cubic(4.0)),
             detect_lattice(_Cell2.orthorhombic(4.0, 4.5, 6.0)),
             detect_lattice(_Cell2.orthorhombic(4.0, 4.0, 6.0)))
    if kinds == ("cub", "orc", "tet"):
        print(f"  ok   点阵判定：立文 正交/四方 = {kinds}")
    else:
        failures += 1
        print(f"  FAIL 点阵判定错了：{kinds}")

    # (11) the template's placeholder path must not treat the value as a regex
    #      replacement (a Windows path used to inject newline/tab or raise)
    from cp2kbuild.template import replace as _replace
    template = "&SCF\n   PRECONDITIONER LT_precond\n&END SCF\n"
    out, info = _replace(template, "precond", r"C:\new\table")
    out2, _ = _replace(template, "precond", r"a\1b")
    if info is not None and out.splitlines()[1] == r"   PRECONDITIONER C:\new\table" \
            and out2.splitlines()[1] == r"   PRECONDITIONER a\1b":
        print("  ok   模板替换把值当字面量（反斜杠不再变成换行 制表符）")
    else:
        failures += 1
        print(f"  FAIL 模板替换仍在解释反斜杠：{out.splitlines()[1]!r}")

    # (12) GROMOS-96 coordinates: the tool advertised G96 but had no reader
    import tempfile as _tempfile
    from pathlib import Path as _Path
    from cp2kbuild.mmcheck import read_coordinates as _read_coordinates
    g96 = _Path(_tempfile.mkdtemp()) / "mini.g96"
    g96.write_text(
        "TITLE\nmini\nEND\nPOSITION\n"
        "    1 ALA   N          1    0.100000000    0.200000000    0.300000000\n"
        "    1 ALA   CA         2    0.400000000    0.500000000    0.600000000\n"
        "END\nBOX\n    1.500000000    1.500000000    1.500000000\nEND\n",
        encoding="utf-8")
    coordinates = _read_coordinates(g96)
    if coordinates.natom == 2 and coordinates.names == ["N", "CA"] \
            and coordinates.box == (15.0, 15.0, 15.0):
        print("  ok   G96 坐标读得出来（nm→Å，名nBOX 块）")
    else:
        failures += 1
        print(f"  FAIL G96 读取：{coordinates.natom} {coordinates.names} "
              f"{coordinates.box}")

    # (13) an enum value that is no longer a string is still checked (CP2K aborts
    #      with "invalid value for enumeration:5"), while extra values on a
    #      numeric keyword are a warning - the manual's type cannot tell a scalar
    #      from a list (``GMAX 25 25 25`` is typed ``integer``)
    enum_case = _levels(kinetics_no_stress.replace("NPT_I", "5"))
    many_case = _levels(zero_cutoff.replace("CUTOFF 0", "CUTOFF 400 500"))
    if enum_case.get("bad-enum") == "error" \
            and many_case.get("too-many-values") == "warning":
        print("  ok   枚举被强转成数字 →nerror；数值关键字多给一个值]→ warning")
    else:
        failures += 1
        print(f"  FAIL 枚举/取值个数：{enum_case.get('bad-enum')} / "
              f"{many_case.get('too-many-values')}")

    # (14) the wave-function restart interval counts the *task's* steps: MD for an
    #      MD run, GEO_OPT/CELL_OPT for the optimisers, QS_SCF only for a single
    #      point - and the validator says so when the level cannot fire
    from cp2kbuild.recipes import Spec as _Spec
    from cp2kbuild.recipes import _wfn_restart_print as _wfn
    from cp2kbuild.model import render_document as _render
    levels_ok = True
    for task, want in (("energy", "QS_SCF"), ("md", "MD"), ("geo_opt", "GEO_OPT"),
                       ("cell_opt", "CELL_OPT")):
        body = [line.strip() for line in
                _render([_wfn(_Spec(task=task), "OT")]).splitlines()]
        levels_ok = levels_ok and any(line.startswith(want + " ") for line in body)
    explicit = _render([_wfn(_Spec(task="md", wfn_restart_level="QS_SCF",
                                  wfn_restart_every=250), "OT")])
    if levels_ok and "QS_SCF 250" in explicit:
        print("  ok   波函数重启文件按任务的层级计数（单点 QS_SCF / MD / GEO_OPT / "
              "CELL_OPT），也能显式覆盖")
    else:
        failures += 1
        print("  FAIL 波函数重启文件的 &EACH 层级不对")
    mismatch = kinetics_no_stress.replace("NPT_I", "5")
    wrong_level = _levels(kinetics_no_stress.replace(
        "      SCF_GUESS ATOMIC",
        "      SCF_GUESS ATOMIC\n      &PRINT\n        &RESTART ON\n"
        "          ADD_LAST NUMERIC\n          &EACH\n            QS_SCF 500\n"
        "          &END EACH\n        &END RESTART\n      &END PRINT"))
    if wrong_level.get("restart-print-level-mismatch") == "warning" \
            and "restart-print-level-mismatch" not in mismatch:
        print("  ok   MD 里把波函数间隔写成 QS_SCF → warning（那个间隔几乎不会触发）")
    else:
        failures += 1
        print(f"  FAIL 层级不匹配的警告不对：{wrong_level.get('restart-print-level-mismatch')}")

    # (15) reading an existing input back must look at every level, not just MD
    import tempfile as _tmp2
    from pathlib import Path as _P2
    from cp2kbuild.importinp import import_input as _import
    sample = _P2(_tmp2.mkdtemp()) / "geo_levels.inp"
    sample.write_text(
        "&GLOBAL\n  RUN_TYPE GEO_OPT\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD QUICKSTEP\n  &DFT\n    &SCF\n      SCF_GUESS ATOMIC\n"
        "      &PRINT\n        &RESTART ON\n          ADD_LAST NUMERIC\n"
        "          &EACH\n            GEO_OPT 25\n          &END EACH\n"
        "        &END RESTART\n      &END PRINT\n    &END SCF\n  &END DFT\n"
        "&END FORCE_EVAL\n&MOTION\n  &PRINT\n    &TRAJECTORY ON\n      &EACH\n"
        "        GEO_OPT 5\n      &END EACH\n    &END TRAJECTORY\n  &END PRINT\n"
        "&END MOTION\n", encoding="utf-8")
    imported = {s.field: s.value for s in _import(sample).settings}
    contradicting = _P2(_tmp2.mkdtemp()) / "geo_md_level.inp"
    contradicting.write_text(
        sample.read_text(encoding="utf-8").replace("GEO_OPT 25", "MD 20"),
        encoding="utf-8")
    other = {s.field: s.value for s in _import(contradicting).settings}
    if imported.get("wfn_restart_every") == 25 \
            and imported.get("wfn_restart_level") is None \
            and imported.get("trajectory_stride") == 5 \
            and other.get("wfn_restart_level") == "MD":
        print("  ok   读回时按实际层级取值；与任务一致时留给「自动」，"
              "不一致（GEO_OPT 文件里写 MD）才点名")
    else:
        failures += 1
        print(f"  FAIL 读回层级：{imported.get('wfn_restart_every')} / "
              f"{imported.get('wfn_restart_level')} / "
              f"{imported.get('trajectory_stride')} / {other.get('wfn_restart_level')}")

    # ---- &EWALD GMAX is mandatory for EWALD/SPME ------------------------- #
    # CP2K has no default for it ("we have no defaults for gmax") and asks for
    # the value during fist_init, which aborts:
    #   Value requested, but no value set getting value from keyword GMAX
    from cp2kbuild.recipes import suggest_ewald_gmax
    estimate = suggest_ewald_gmax(_Cell.cubic(24.4))
    bigger = suggest_ewald_gmax(_Cell.cubic(24.4), 0.5)
    if estimate == (59, 59, 59) and bigger[0] > estimate[0]:
        print(f"  ok   GMAX 估算，a4.4 Å 立方 + α=0.35 → {estimate[0]}，" 
              f"α=0.5 → {bigger[0]}（CP2K 自己的XTB-Ewald 公式）")
    else:
        failures += 1
        print(f"  FAIL GMAX 估算不对：{estimate} / {bigger}")

    missing_gmax = """&GLOBAL
  RUN_TYPE MD
&END GLOBAL
&FORCE_EVAL
  METHOD FIST
  &MM
    &FORCEFIELD
      PARMTYPE AMBER
      PARM_FILE_NAME a.prmtop
    &END FORCEFIELD
    &POISSON
      &EWALD
        EWALD_TYPE SPME
      &END EWALD
    &END POISSON
  &END MM
  &SUBSYS
    &CELL
      ABC 24.4 24.4 24.4
    &END CELL
    &TOPOLOGY
      CONNECTIVITY AMBER
      CONN_FILE_NAME a.prmtop
      COORDINATE PDB
      COORD_FILE_NAME a.pdb
    &END TOPOLOGY
  &END SUBSYS
&END FORCE_EVAL
"""
    levels = {d.code: d.level
              for d in validator.run(parse_input(missing_gmax).sections)}
    if levels.get("mm-ewald-gmax-required") == "error":
        print("  ok   手写的 SPME 缩 GMAX →nerror（这正是用户遇到的 fist_init 崩溃）")
    else:
        failures += 1
        print(f"  FAIL SPME 缩 GMAX 应当是nerror：{levels.get('mm-ewald-gmax-required')}")
    fixed = missing_gmax.replace("        EWALD_TYPE SPME\n",
                                 "        EWALD_TYPE SPME\n        GMAX 40\n")
    if "mm-ewald-gmax-required" not in [
            d.code for d in validator.run(parse_input(fixed).sections)]:
        print("  ok   补上 GMAX 后不再报")
    else:
        failures += 1
        print("  FAIL 补上 GMAX 后仍在报")

    # ---- TEMP_TOL must never be written, and an inline-coordinate relay must
    # not be told to re-read the same coordinates through &EXT_RESTART -------- #
    for kwargs in (dict(task="md", structure=silicon, ensemble="NVT", steps=100),
                   dict(task="md", structure=silicon, ensemble="NPT_I", steps=100),
                   dict(task="md", structure=silicon, ensemble="NVE", steps=100)):
        text = render_document(build(Spec(**kwargs), index).sections, title="t", notes=[])
        if "TEMP_TOL" not in text:
            print(f"  ok   {kwargs['ensemble']}：不写已过时的 TEMP_TOL"
                  "（CP2K 默认 0，温度交给恒温器）")
        else:
            failures += 1
            print(f"  FAIL {kwargs['ensemble']} 里又出现了 TEMP_TOL")

    inline_dir = Path(tempfile.mkdtemp(prefix="cp2kbuild_redundant_"))
    inline = inline_dir / "cp2k-1.restart"
    inline.write_text(
        "&GLOBAL\n  RUN_TYPE GEO_OPT\n&END GLOBAL\n&FORCE_EVAL\n  METHOD FIST\n"
        "  &MM\n    &FORCEFIELD\n      PARMTYPE AMBER\n"
        "      PARM_FILE_NAME a.prmtop\n    &END FORCEFIELD\n  &END MM\n"
        "  &SUBSYS\n    &CELL\n      ABC 20 20 20\n      PERIODIC XYZ\n"
        "    &END CELL\n    &COORD\n      CA1 1.0 1.0 1.0\n      OH1 2.0 2.0 2.0\n"
        "    &END COORD\n    &TOPOLOGY\n      CONN_FILE_NAME a.prmtop\n"
        "      CONN_FILE_FORMAT AMBER\n    &END TOPOLOGY\n  &END SUBSYS\n"
        "&END FORCE_EVAL\n", encoding="utf-8")
    structure = read_structure(str(inline))
    base = dict(task="md", structure=structure, method="FIST", mm_route="AMBER",
                mm_parm_file="a.prmtop", mm_conn_file="a.prmtop", mm_ewald="SPME",
                cell=structure.cell, steps=100, ext_restart_file=inline.name)
    only_pos = build(Spec(**base, ext_restart_take=("pos",)), index)
    levels = {d.code: d.level for d in validator.run(only_pos.sections)}
    if levels.get("ext-restart-redundant") == "info":
        print("  ok   输入自带 `&COORD` 时「只只 POS」的 &EXT_RESTART → info：可以删接")
    else:
        failures += 1
        print(f"  FAIL 只取 POS 的 &EXT_RESTART 应当提示冗余，" 
              f"{levels.get('ext-restart-redundant')}")
    with_vel = build(Spec(**base, ext_restart_take=("pos", "vel")), index)
    if "ext-restart-redundant" not in [
            d.code for d in validator.run(with_vel.sections)]:
        print("  ok   取速度时不再提示（速度是内联输入给不了的）")
    else:
        failures += 1
        print("  FAIL 只 vel 时不该提示 &EXT_RESTART 冗余")

    # ---- k-points and OT are mutually exclusive in CP2K ------------------- #    # qs_scf_initialization.F:826 CPABORTs with "OT diagonalization not possible
    # with kpoint calculations", so this has to be an error, and "auto" must
    # never pick OT when &KPOINTS is written.
    auto_kpoints = build(Spec(task="energy", structure=silicon, kpoint_grid=(4, 4, 4)),
                         index)
    dft = next(s for s in auto_kpoints.sections if s.name == "FORCE_EVAL") \
        .subsection("DFT")
    scf_block = dft.subsection("SCF") if dft else None
    if scf_block is not None and scf_block.subsection("OT") is None \
            and scf_block.subsection("DIAGONALIZATION") is not None:
        print("  ok   「自动」  k 点 → 自己选对角化（不会写出NOT + &KPOINTS）e")
    else:
        failures += 1
        print("  FAIL 「自动」  k 点 时没有切到对角化")

    forced = build(Spec(task="energy", structure=silicon, kpoint_grid=(4, 4, 4),
                        scf="OT"), index)
    levels = {d.code: d.level for d in validator.run(forced.sections)}
    if levels.get("ot-with-kpoints") == "error":
        print("  ok   明确要求 OT + k 点 →nerror（CP2K 会 abort）")
    else:
        failures += 1
        print(f"  FAIL OT + k 点应当是 error：{levels.get('ot-with-kpoints')}")

    # ---- Poisson solver vs. periodicity ---------------------------------- #
    # CP2K does *not* enforce the combination (the consistency check in
    # pw_poisson_read_parameters is commented out) and its default solver is
    # PERIODIC, so an omitted solver is never a problem; an explicit solver the
    # manual does not list for that cell is worth a warning.
    poisson_cases = [
        ("三维周期 + PERIODIC", "XYZ", "PERIODIC", None),
        ("三维周期 + MT", "XYZ", "MT", "warning"),
        ("三维周期 + WAVELET", "XYZ", "WAVELET", None),
        ("分子 + MT", "NONE", "MT", None),
        ("分子 + PERIODIC（显式）", "NONE", "PERIODIC", "warning"),
        ("分子 + 不写求解器（CP2K 默认 PERIODIC）", "NONE", "", None),
        ("分子 + MULTIPOLE", "NONE", "MULTIPOLE", None),
        ("表面来 + MT", "XY", "MT", None),
        ("表面来 + PERIODIC", "XY", "PERIODIC", "warning"),
        ("一给 + MULTIPOLE", "X", "MULTIPOLE", "warning"),
        ("一给 + ANALYTIC", "X", "ANALYTIC", None),
    ]
    wrong: list[str] = []
    for label, periodic, solver, expected in poisson_cases:
        solver_line = f"      POISSON_SOLVER {solver}\n" if solver else ""
        text = f"""
&GLOBAL
  PROJECT_NAME t
  RUN_TYPE ENERGY
&END GLOBAL
&FORCE_EVAL
  METHOD QUICKSTEP
  &DFT
    &POISSON
      PERIODIC {periodic}
{solver_line}    &END POISSON
    &SCF
    &END SCF
  &END DFT
  &SUBSYS
    &CELL
      ABC 10 10 10
      PERIODIC {periodic}
    &END CELL
    &COORD
      Si 0 0 0
    &END COORD
  &END SUBSYS
&END FORCE_EVAL
"""
        found = {d.code: d.level
                 for d in validator.run(parse_input(text).sections)}
        level = found.get("poisson-solver-mismatch")
        if level != expected:
            wrong.append(f"{label}: got {level!r}, want {expected!r}")
    if wrong:
        failures += 1
        print(f"  FAIL Poisson/周期性 的判定：{wrong}")
    else:
        print("  ok   Poisson 求解器 × 周期性 全对，E1 组；不写求解器时不管，" 
              "显式写了才对——CP2K 自己那条检查是注释掉的，")

    # ---- every entry of the coupling table, both directions --------------- #
    # The table in cp2kbuild/couplings.py says which parameters only apply under
    # which conditions; the GUI greys those out and the CLI warns about them, so
    # an entry that is wrong (or that never fires) is a user-visible bug.
    from cp2kbuild.couplings import (FLAG_SPELLINGS, RULES, applicability,
                                     asked_but_ignored)
    from cp2kbuild.recipes import MmMolecule, prepare_structure
    from cp2kbuild.structures import Cell

    candidates = []
    for task in ("energy", "geo_opt", "cell_opt", "md", "freq", "band", "pdos", "neb"):
        for name, structure in (("silicon", silicon), ("water", water)):
            for smearing in (None, True):
                for ensemble in ("NVT", "NVE", "NPT_I"):
                    for vacuum in (None, 12.0):
                        for guess in ("RESTART", "ATOMIC"):
                            for printing in (True, False):
                                spec = Spec(task=task, structure=structure,
                                            vacuum=vacuum, smearing=smearing,
                                            ensemble=ensemble, scf_guess=guess,
                                            steps=100,
                                            wfn_restart_print=printing,
                                            motion_restart_print=printing)
                                if task == "neb":
                                    spec.replicas = 5
                                try:
                                    prepared = prepare_structure(
                                        spec.structure.copy(), spec)
                                except Exception:      # noqa: BLE001
                                    prepared = spec.structure
                                candidates.append((spec, prepared))

    # the classical (FIST) route is the other half of the table: with METHOD
    # FIST there is no &DFT section at all, so every Quickstep setting has to be
    # reported as inactive
    for task in ("energy", "md", "freq"):
        for route in ("AMBER", "CHARMM", "GROMOS96", "PAIR", "IN_INPUT"):
            for molecules in ([], [MmMolecule("m.prmtop", "AMBER", 2)]):
                spec = Spec(task=task, method="FIST", structure=water,
                            mm_route=route, mm_parm_file="a.prmtop",
                            mm_conn_file="a.prmtop", mm_coord_file="a.inpcrd",
                            mm_ff_file="ff.inc", mm_molecules=molecules,
                            mm_ewald="SPME", mm_ewald_gmax=64, steps=100)
                candidates.append((spec, water))
                # ... and once more with a periodic cell, where &EWALD is on
                periodic = Spec(task=task, method="FIST", structure=water,
                                cell=Cell(20.0, 20.0, 20.0), mm_route=route,
                                mm_parm_file="a.prmtop", mm_conn_file="a.prmtop",
                                mm_coord_file="a.inpcrd", mm_ff_file="ff.inc",
                                mm_molecules=molecules, mm_ewald="SPME",
                                mm_ewald_gmax=64, steps=100)
                candidates.append(
                    (periodic, prepare_structure(periodic.structure.copy(),
                                                 periodic)))

    never_fires: list[str] = []
    no_warning: list[str] = []
    false_alarm: list[str] = []
    for rule in RULES:
        violating = next(((spec, structure) for spec, structure in candidates
                          if not rule.applies(spec, structure)), None)
        if violating is None:
            never_fires.append(rule.field)
            continue
        spec, structure = violating
        # (a) an entry the table calls inactive must be visible as such
        if rule.field not in applicability(spec, structure):
            never_fires.append(rule.field)
            continue
        # (b) giving its flag has to produce a warning.  A field whose only
        # spelling is ``--no-...`` says "switch it off", which is not an ask by
        # itself - ``asked_but_ignored`` only reports it while the value is still
        # on, so the probe has to leave the setting on.
        negative_only = all(f.startswith("--no-") for f in rule.all_flags())
        probe, given = spec, {rule.flag()}
        if negative_only:
            probe = dataclasses.replace(spec, **{rule.field: True})
        if rule not in asked_but_ignored(probe, structure, given):
            no_warning.append(rule.field)
        # (c) a *default* value without the flag must stay silent
        quiet = Spec(task="energy", structure=silicon, scf_guess="ATOMIC")
        if rule in asked_but_ignored(quiet, silicon, set()):
            false_alarm.append(rule.field)

    if never_fires or no_warning or false_alarm:
        failures += 1
        print(f"  FAIL 耦合表有问题：不触发 {never_fires} / 不警周 {no_warning} / "
              f"误报 {false_alarm}")
    else:
        print(f"  ok   耦合行 {len(RULES)} 条全部有效：每条都能找到一个让它失效的"
              "输入、给出标志时会警告、默认值不误报")

    # a default the *tool* derives (1.0 fs for a classical force field) is not
    # something the user asked for, so it must not be reported as ignored
    derived = Spec(task="geo_opt", method="FIST", structure=water, timestep=1.0)
    noisy = [r.field for r in asked_but_ignored(derived, derived.structure, set())
             if r.field == "timestep"]
    if noisy:
        failures += 1
        print("  FAIL FIST 自己的默认时间步长被当成用户设定来警周")
    else:
        print("  ok   FIST 派生出来的默认值不触发「参数不会生效」警周")

    # 界面独有的字段（命令行已冻结）不能出现在 CLI 解析器里，否分 
    # packaging/couplings.md 里的「命令行」一列就是在骗人
    from cp2kbuild.cli import build_parser

    parser = build_parser()
    known_flags: set[str] = set()

    def _collect_flags(par: argparse.ArgumentParser) -> None:
        """Every option of every (sub-)parser - the options live on `new`/`scan`."""
        for action in par._actions:
            known_flags.update(action.option_strings)
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    _collect_flags(sub)

    _collect_flags(parser)
    advertised = sorted({flag for rule in RULES if rule.gui_only
                         for flag in rule.all_flags() if flag in known_flags})
    if advertised:
        failures += 1
        print(f"  FAIL 界面独有的字段却打出了命令行开关：{advertised}")
    else:
        count = sum(1 for rule in RULES if rule.gui_only)
        print(f"  ok   {count} 条「界面独有」字段在 CLI 解析器里确实没有开入"
              "（couplings.md 写「—」）")

    # ... and the other way round: the switch a warning names must really exist.
    # Only ``all_flags()[0]`` matters - the derived ``--no-<field>`` companion is
    # an internal probe (the parser often has no such option, which is fine), but
    # the name quoted in the message is what the user will try to type.
    missing = sorted({rule.flag() for rule in RULES if not rule.gui_only
                      if rule.flag() not in known_flags})
    spelled = sorted({flag for flags in FLAG_SPELLINGS.values() for flag in flags
                      if flag not in known_flags})
    if missing or spelled:
        failures += 1
        print(f"  FAIL 耦合表引用了命令行里不存在的开关：警告里 {missing}"
              f" / 别名 {spelled}")
    else:
        count = len({rule.flag() for rule in RULES if not rule.gui_only})
        print(f"  ok   耦合表警告里的 {count} 个开关在 CLI 里都真实存在"
              "（含 FLAG_SPELLINGS 的别名）")

    print(f"\npart 1b failures: {failures}")
    return failures


def part2(db, index: DataIndex, tests: Path, limit: int) -> int:
    print()
    print("=" * 78)
    print("part 2: linting CP2K's own regression inputs")
    print("=" * 78)

    directories = tested_directories(tests)
    if directories:
        files = sorted({f for directory in directories
                        for f in directory.rglob("*.inp")})
        source = f"the {len(directories)} directories listed in TEST_DIRS"
    else:
        files = sorted(tests.glob("**/*.inp"))
        source = "the whole tests tree (TEST_DIRS not found)"
    # CP2K keeps inputs it does not run in "untested_inputs" directories; they
    # are not known-good, so they are not a fair target for a false-positive test
    skipped = [f for f in files if "untested_inputs" in f.parts]
    files = [f for f in files if "untested_inputs" not in f.parts]
    if not files:
        print(f"no .inp files under {tests}")
        return 0
    step = max(1, len(files) // limit)
    sample = files[::step][:limit]
    print(f"corpus: {len(files)} inputs from {source} "
          f"({len(skipped)} untested ones skipped)")
    print(f"linting {len(sample)}")

    counter: collections.Counter = collections.Counter()
    examples: dict[str, str] = {}
    files_with_errors = 0
    parse_problems = 0
    english_rows: list[str] = []
    rows_seen = 0
    for path in sample:
        try:
            parsed = read_input(path)
        except Exception as exc:  # noqa: BLE001
            parse_problems += 1
            counter["parse-exception"] += 1
            examples.setdefault("parse-exception", f"{path.name}: {exc}")
            continue
        diagnostics = Validator(db, index).run(parsed.sections, parsed.directives)
        for item in diagnostics:
            # the whole report is Chinese (CP2K's own messages are quoted inside
            # the Chinese sentence), so a row without a single CJK character is a
            # message somebody forgot to translate - it happened for 85 of them
            rows_seen += 1
            if not _CJK.search(item.message) or (item.hint and not _CJK.search(item.hint)):
                english_rows.append(f"{item.code}: {item.message[:60]}")
        errors = [d for d in diagnostics if d.level == "error"]
        if errors:
            files_with_errors += 1
        for item in errors:
            counter[item.code] += 1
            examples.setdefault(item.code, f"{path.name}: {item.message} [{item.path}]")

    print(f"\n{files_with_errors}/{len(sample)} files produced at least one error")
    print(f"{parse_problems} files failed to parse at all")
    language_failures = 0
    if english_rows:
        language_failures = 1
        print(f"  FAIL {len(english_rows)}/{rows_seen} 条诊断还是纯英文（没有中文）")
        for line in english_rows[:8]:
            print(f"         {line}")
    else:
        print(f"  ok   {rows_seen} 条诊断（消息 + 建议）全都是中文")
    print("\nerror codes:")
    for code, count in counter.most_common():
        print(f"  {count:5d}  {code}")
        print(f"         e.g. {examples[code][:170]}")
    return files_with_errors + language_failures


def newest_source_tree() -> Path | None:
    """The newest ``cp2k-<version>`` checkout, for the default --tests/--src."""
    for root in (Path(r"E:/desktop/cp2k"), Path(r"E:/cp2k"), Path.home() / "cp2k"):
        if not root.is_dir():
            continue
        trees = sorted((child for child in root.iterdir()
                        if child.is_dir() and child.name.lower().startswith("cp2k-")),
                       key=lambda path: path.name, reverse=True)
        for tree in trees:
            if (tree / "tests").is_dir():
                return tree
    return None


def part3_cutoff_scripts(index=None) -> int:
    """The official CUTOFF/REL_CUTOFF file set, as the tool generates it.

    Nothing here needs CP2K or a shell: it is all about the *text* - which of the
    two template channels is used, planting the two placeholders (or reusing the
    ones the input already has), forcing ``RUN_TYPE ENERGY``, the script contents,
    and the byte fidelity of ``template.inp``.

    ``index`` is only needed for the "current parameters" channel, which builds
    the template out of a :class:`Spec`.
    """
    from cp2kbuild import cutoffkit
    from cp2kbuild.paths import read_template_text

    print()
    print("=" * 78)
    print("part 3: the official CUTOFF/REL_CUTOFF script set")
    print("=" * 78)

    failures = 0
    ok = True

    def report(condition: bool, message: str) -> None:
        nonlocal failures, ok
        if condition:
            print(f"  ok   {message}")
        else:
            print(f"  FAIL {message}")
            failures += 1
            ok = False

    import shutil
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="cp2kbuild_cutoff_"))
    # the fixtures that must not see each other live in their own directory:
    # ``side_files`` picks up every ``*.inc`` next to the input, so a stray
    # include file in the same directory would answer for the file under test
    src_dir = workdir / "src"
    src_dir.mkdir()

    # ---- 1. the tutorial's own style: @SET + LT_* placeholders + @INCLUDE --- #
    force_inc = workdir / "force.inc"
    force_inc.write_text(
        "&FORCE_EVAL\n   METHOD QuickStep\n   &DFT\n"
        "      BASIS_SET_FILE_NAME  BASIS_MOLOPT\n"
        "      POTENTIAL_FILE_NAME  GTH_POTENTIALS\n"
        "      &MGRID\n         CUTOFF ${CUTOFF}\n"
        "         REL_CUTOFF ${REL_CUTOFF}\n      &END MGRID\n"
        "      &SCF\n         SCF_GUESS ATOMIC\n         MAX_SCF 1\n"
        "         IGNORE_CONVERGENCE_FAILURE TRUE\n      &END SCF\n"
        "   &END DFT\n&END FORCE_EVAL\n", encoding="utf-8")
    tutorial_input = workdir / "system.inp"
    tutorial_text = ("@SET RTYPE ENERGY\n@SET CUTOFF LT_cutoff\n"
                     "@SET REL_CUTOFF LT_rel_cutoff\n"
                     "&GLOBAL\n   RUN_TYPE ${RTYPE}\n&END GLOBAL\n"
                     "@INCLUDE force.inc\n")
    tutorial_input.write_bytes(tutorial_text.encode("utf-8"))

    out = workdir / "tutorial"
    plan = cutoffkit.build_cutoff_study(tutorial_input, out)
    names = sorted(path.name for path in out.iterdir() if path.is_file())
    report(names == ["cutoff_analyse.sh", "cutoff_inputs.sh", "cutoff_run.sh",
                     "force.inc", "rel_cutoff_analyse.sh", "rel_cutoff_inputs.sh",
                     "rel_cutoff_run.sh", "template.inp"],
           f"生成的就是官方那 7 个文件（+ @INCLUDE 的文件）：{names}")
    report(read_template_text(out / "template.inp") == tutorial_text,
           "占位符已经在输入里时，template.inp 与输入逐字节相名")
    report(not [note for note in plan.notes if "RUN_TYPE" in note],
           "输入本来就是 RUN_TYPE ENERGY 时不提ARUN_TYPE（提示只在真改了的时候出现）")
    report(sorted(plan.cutoffs) == sorted(cutoffkit.DEFAULT_CUTOFFS),
           f"第E1 步默认用官方脚本的范围：{plan.cutoffs}")
    report(sorted(plan.rel_cutoffs) == sorted(cutoffkit.DEFAULT_REL_CUTOFFS),
           f"第A2 步默认用官方脚本的范围：{plan.rel_cutoffs}")
    report(plan.fixed_rel_cutoff == 60.0,
           "第 1 步固它AREL_CUTOFF = 60 Ry（教程的起点）")
    report(plan.basis_files == ["BASIS_MOLOPT"]
           and plan.potential_files == ["GTH_POTENTIALS"],
           f"从输入里读出了要复制的基给 赝势文件：{plan.basis_files} / "
           f"{plan.potential_files}")
    report(plan.side_files == ["force.inc"], f"@INCLUDE 的文件认出来了：{plan.side_files}")
    report("MAX_SCF 1" not in " ".join(plan.notes),
           "输入已经带官方协议时不再啰嗦（没有 MAX_SCF 的提醒）")

    inputs = (out / "cutoff_inputs.sh").read_text(encoding="utf-8")
    report('cutoffs="50 100 150 200 250 300 350 400 450 500"' in inputs
           and "rel_cutoff=60" in inputs,
           "cutoff_inputs.sh 里是官方脚本那一行 cutoffs 列表与固定值")
    report('sed -e "s/LT_rel_cutoff/${rel_cutoff}/g"' in inputs
           and '-e "s/LT_cutoff/${ii}/g"' in inputs,
           "cutoff_inputs.sh 用官方那两行 sed 替换占位第")
    report('input_file=system.inp' in inputs and "template_file=template.inp" in inputs,
           "生成出来的输入沿用原文件名（system.inp）")
    report('basis_files="BASIS_MOLOPT"' in inputs
           and 'side_files="force.inc"' in inputs
           and "cp \"$f\" \"$work_dir\"" in inputs,
           "脚本会把基组/赝势/@INCLUDE 文件复制进每个参数点目录")
    report("cutoff_${ii}Ry" in inputs, "目录命名与教程一致（cutoff_50Ry）")

    step2 = (out / "rel_cutoff_inputs.sh").read_text(encoding="utf-8")
    report('rel_cutoffs="10 20 30 40 50 60 70 80 90 100"' in step2
           and f'cutoff="{cutoffkit.CHANGE_ME}"' in step2,
           "第A2 步的脚本用官方脚本那一行AREL_CUTOFF 列表，CUTOFF 先留 CHANGE_ME")
    report("先把脚本开头的 cutoff 改成第 1 步定下来的值再跑" in step2
           and "exit 1" in step2,
           "没填 CUTOFF 之前第 2 步的脚本会拒绝跑（而不是生成一堆错目录）")
    # 第 1 步扫 CUTOFF、第 2 步扫 REL_CUTOFF：两行used 里的重点必须对调， 
    # 照抄第E1 步的那两行会让第 2 步变成 再扫一道 CUTOFF"（曾经就是这样）
    report('sed -e "s/LT_rel_cutoff/${rel_cutoff}/g"' in inputs
           and '-e "s/LT_cutoff/${ii}/g"' in inputs,
           "第 1 步：LT_rel_cutoff 拿固定值、LT_cutoff 拿循环值")
    report('sed -e "s/LT_cutoff/${cutoff}/g"' in step2
           and '-e "s/LT_rel_cutoff/${ii}/g"' in step2,
           "第 2 步：两行 sed 对调——LT_cutoff 拿固定值、LT_rel_cutoff 拿循环值" 
           "（抄第E1 步那两行会把 CUTOFF 又扫一遍，REL_CUTOFF 列则永远不变）")
    run_sh = (out / "cutoff_run.sh").read_text(encoding="utf-8")
    report("mpirun_bin=mpirun" in run_sh and "cp2k_bin=cp2k.popt" in run_sh
           and "no_proc_to_use=16" in run_sh,
           "run 脚本把机器相关的三个变量放在开头（教程的做法）")
    report("--bind-to none" in run_sh and "wait" in run_sh,
           "run 脚本按教程用 mpirun + --bind-to none，并分批 wait")
    report('if [ -z "$mpirun_bin" ]' in run_sh,
           "mpirun_bin 置空就走串行（不必改循环）")
    analyse = (out / "cutoff_analyse.sh").read_text(encoding="utf-8")
    report("plot_file=cutoff_data.ssv" in analyse
           and "NG on grid %d" in analyse
           and "Total energy" in analyse,
           "analyse 脚本写 cutoff_data.ssv（能里 + 每档网格点数，教程的格式）")

    # ---- 2. a literal input: the tool plants the placeholders itself -------- #
    literal = src_dir / "literal.inp"
    literal_text = (
        "&GLOBAL\n   RUN_TYPE ENERGY\n&END GLOBAL\n&FORCE_EVAL\n"
        "   METHOD Quickstep\n   &DFT\n      &MGRID\n"
        "         NGRIDS 4\n         CUTOFF 400\n         REL_CUTOFF 60\n"
        "      &END MGRID\n   &END DFT\n&END FORCE_EVAL\n")
    literal.write_bytes(literal_text.encode("utf-8"))
    plan2 = cutoffkit.build_cutoff_study(literal, workdir / "literal_out",
                                         cutoffs="100,200", rel_cutoffs="20,40",
                                         fixed_cutoff=200)
    prepared = read_template_text(workdir / "literal_out" / "template.inp")
    report("CUTOFF LT_cutoff" in prepared and "REL_CUTOFF LT_rel_cutoff" in prepared,
           "字面写法的输入会被插入 LT_cutoff / LT_rel_cutoff 占位符")
    changed = [before for before, after in
               zip(literal_text.splitlines(), prepared.splitlines())
               if before != after]
    report(changed == ["         CUTOFF 400", "         REL_CUTOFF 60"],
           f"除那两行以外一个字节没动：{changed}")
    report("MAX_SCF 1" in " ".join(plan2.notes),
           "输入不是官方协议时提醒一次（并给出要加的三行）")
    step2b = (workdir / "literal_out" / "rel_cutoff_inputs.sh").read_text(encoding="utf-8")
    report("cutoff=200" in step2b and cutoffkit.CHANGE_ME not in step2b,
           "给了 --cutoff 以后第_2 步的脚本直接可跑（没有 CHANGE_ME） ")
    analyse2 = (workdir / "literal_out" / "cutoff_analyse.sh").read_text(encoding="utf-8")
    report("rel_cutoff=60" in analyse2, "analyse 脚本里带着第E1 步的固定值")
    report("cutoffs=\"100 200\"" in (workdir / "literal_out"
                                     / "cutoff_run.sh").read_text(encoding="utf-8"),
           "扫描范围按给定的值写进三个脚有")

    # ---- 3. no CUTOFF at all: say so instead of generating nothing ---------- #
    bare = src_dir / "bare.inp"
    bare.write_text("&GLOBAL\n   RUN_TYPE ENERGY\n&END GLOBAL\n", encoding="utf-8")
    plan3 = cutoffkit.build_cutoff_study(bare, workdir / "bare_out")
    notes = " ".join(plan3.notes)
    report("找不到 CUTOFF" in notes and "找不到 REL_CUTOFF" in notes,
           "输入里没有这两个关键字时明确报出来（否则 sed 会静默替换不到）")

    # ---- 3b. the tool's own inputs carry a [unit] annotation ---------------- #
    # ``CUTOFF [Ry] 400.0`` is how cp2kbuild writes it (and CP2K accepts the
    # annotation).  The placeholder belongs *behind* the unit: putting it in the
    # unit's place would leave ``CUTOFF 100 400.0`` after sed - two values on one
    # line, which CP2K refuses.
    annotated = src_dir / "annotated.inp"
    annotated.write_bytes(
        ("&GLOBAL\n   RUN_TYPE ENERGY\n&END GLOBAL\n&FORCE_EVAL\n"
         "   METHOD QuickStep\n   &DFT\n      &MGRID\n"
         "         CUTOFF [Ry] 400.0\n         REL_CUTOFF [Ry] 60.0\n"
         "      &END MGRID\n   &END DFT\n&END FORCE_EVAL\n").encode("utf-8"))
    cutoffkit.build_cutoff_study(annotated, workdir / "annotated_out",
                                 cutoffs="100,200", rel_cutoffs="20")
    prepared_unit = read_template_text(workdir / "annotated_out" / "template.inp")
    report("CUTOFF [Ry] LT_cutoff" in prepared_unit
           and "REL_CUTOFF [Ry] LT_rel_cutoff" in prepared_unit,
           "常 [Ry] 单位标注的输入：占位符插在单位之后，单位本身留着")
    swapped = (prepared_unit.replace("LT_rel_cutoff", "60")
               .replace("LT_cutoff", "100"))
    report("CUTOFF [Ry] 100" in swapped and "100 400.0" not in swapped
           and "CUTOFF [Ry] 400.0" not in swapped,
           "sed 之后那一行只剩一个值（CUTOFF [Ry] 100），不会变成两个值")
    report(all(line.count("CUTOFF") == 1
               for line in swapped.splitlines() if "REL_CUTOFF" not in line
               and line.strip().startswith("CUTOFF")),
           "生成的每个输入里 CUTOFF 只出现一次")

    # ---- 4. line endings are the input's business --------------------------- #
    crlf = src_dir / "crlf.inp"
    crlf.write_bytes(literal_text.replace("\n", "\r\n").encode("utf-8"))
    cutoffkit.build_cutoff_study(crlf, workdir / "crlf_out", cutoffs="100",
                                 rel_cutoffs="20")
    raw = (workdir / "crlf_out" / "template.inp").read_bytes()
    report(b"CUTOFF LT_cutoff\r\n" in raw and b"\r\r" not in raw,
           "CRLF 的输入：template.inp 还是 CRLF，值后面没有多余的 \\r")
    report(all(b"\r\n" not in path.read_bytes()
               for path in (workdir / "crlf_out").glob("*.sh")),
           "生成的f.sh 一径RLF（CRLF 的脚本在 Linux 上跑不起来）")

    # ---- 8. no chain to a previous run's restart ---------------------------- #
    # &EXT_RESTART makes CP2K read (and abort on a missing) restart file - every
    # parameter point would die in input checking before the first SCF step, so
    # the template must not carry an active one.  The block is *commented*, not
    # deleted: nothing of the user's input is lost.
    def active_lines(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped and stripped[0] not in "#!":
                out.append(stripped)
        return out

    chained = src_dir / "chained.inp"
    chained.write_bytes(
        ("&GLOBAL\n   RUN_TYPE MD\n   PROJECT cp2k\n&END GLOBAL\n"
         "&EXT_RESTART\n   RESTART_FILE_NAME cp2k-1_40000.restart\n"
         "   RESTART_DEFAULT F\n   RESTART_COUNTERS T\n&END EXT_RESTART\n"
         "&FORCE_EVAL\n   METHOD Quickstep\n   &DFT\n      &MGRID\n"
         "         CUTOFF [Ry] 400.0\n         REL_CUTOFF [Ry] 60.0\n"
         "      &END MGRID\n   &END DFT\n&END FORCE_EVAL\n").encode("utf-8"))
    plan8 = cutoffkit.build_cutoff_study(chained, workdir / "chained_out",
                                         cutoffs="100", rel_cutoffs="20")
    tpl8 = read_template_text(workdir / "chained_out" / "template.inp")
    report(not [line for line in active_lines(tpl8) if "EXT_RESTART" in line
                or line.startswith("RESTART_")],
           "&EXT_RESTART 整段被注释掉（没有一条活着的 RESTART_* 行）")
    report("RESTART_FILE_NAME cp2k-1_40000.restart" in tpl8
           and "#&END EXT_RESTART" in tpl8,
           "原文还在（每行前面加了 #），不是删掉了")
    notes8 = " ".join(plan8.notes)
    report("cp2k-1_40000.restart" in notes8 and "ABORT" in notes8,
           "结果里点名了那个文件、并说明不删会 ABORT")
    report("RUN_TYPE ENERGY" in tpl8 and "CUTOFF [Ry] LT_cutoff" in tpl8,
           "注释 &EXT_RESTART 不影响同一份输入的其它处理（RUN_TYPE/占位符照旧）")

    inc_file = src_dir / "old.inc"
    inc_file.write_bytes(chained.read_bytes())
    top = src_dir / "top.inp"
    top.write_bytes(b"@INCLUDE old.inc\n")
    plan9 = cutoffkit.build_cutoff_study(top, workdir / "include_out",
                                         cutoffs="100", rel_cutoffs="20")
    report(any("@INCLUDE 进来的文件里有 &EXT_RESTART" in note for note in plan9.notes),
           "&EXT_RESTART 藏在 @INCLUDE 里时明确让用户自己删（工具不改 include 文件）")
    report("old.inc" in (workdir / "include_out" / "template.inp").read_text(
        encoding="utf-8"),
           "这种情况一 template.inp 还是原样（include 是要复制过去的文件）")

    if index is not None:
        chained_spec = cutoffkit.source_from_spec(
            Spec(task="md", structure=read_structure(EXAMPLES / "water.xyz"),
                 vacuum=12.0, ext_restart_file="cp2k-1_40000.restart",
                 ext_restart_take=("counters",),
                 wfn_restart_file="cp2k-RESTART.wfn"), index)
        report("EXT_RESTART" not in chained_spec.text
               and "WFN_RESTART_FILE_NAME" not in chained_spec.text,
               "「生成输入」通道：接动_restart 设置一个都不写进模来")
        report(any("接力用的 restart 文件" in note for note in chained_spec.notes),
               "结果里点名了被丢掉的接力选项（用的是界面上的说法，不是字段名）")

    # ---- 5. bad values are refused, not guessed ----------------------------- #
    for kwargs, why in ((dict(cutoffs="abc"), "非数字的 CUTOFF"),
                        (dict(cutoffs="-100"), "负的 CUTOFF")):
        try:
            cutoffkit.build_cutoff_study(literal, workdir / "bad", **kwargs)
            refused = False
        except ValueError:
            refused = True
        report(refused, f"{why} 被拒绝（不会写出一个跑不通的脚本）")

    # ---- 6. RUN_TYPE 一律改成MENERGY ---------------------------------------- #
    # A grid scan compares total energies, so every point has to be a single
    # point run; an MD or optimisation input would do something else entirely.
    cases = (
        ("&GLOBAL\n   RUN_TYPE MD\n&END GLOBAL\n", "RUN_TYPE ENERGY", "RUN_TYPE MD",
         "字面 RUN_TYPE MD"),
        ("&GLOBAL\n   RUN_TYPE geo_opt\n&END GLOBAL\n", "RUN_TYPE ENERGY",
         "RUN_TYPE geo_opt", "大写小写都认（GEO_OPT）"),
        ("   RUN_TYPE CELL_OPT\n", "   RUN_TYPE ENERGY", "CELL_OPT", "缩进保持不动"),
    )
    for raw, want, unwanted, why in cases:
        got, notes6 = cutoffkit.align_run_type(raw)
        report(want in got and unwanted not in got and len(notes6) == 1,
               f"{why} 被改成MENERGY，并说明为什么（{notes6[0][:24]}…）")

    set_style = ("@SET RTYPE LT_rtype\n&GLOBAL\n   RUN_TYPE ${RTYPE}\n&END GLOBAL\n")
    got, notes6 = cutoffkit.align_run_type(set_style)
    report("@SET RTYPE ENERGY" in got and "${RTYPE}" in got
           and "LT_rtype" not in got and len(notes6) == 1,
           "教程的 @SET RTYPE 写法也改（RUN_TYPE ${RTYPE} 的引用保持不动）")
    kept, notes6 = cutoffkit.align_run_type(
        "@SET RTYPE ENERGY\n&GLOBAL\n   RUN_TYPE ${RTYPE}\n&END GLOBAL\n")
    report("RTYPE ENERGY" in kept and not notes6,
           "已经是 ENERGY 就一个字节不动、也不啰嗗")
    same, notes6 = cutoffkit.align_run_type("&GLOBAL\n   RUN_TYPE ENERGY\n&END GLOBAL\n")
    report(same == "&GLOBAL\n   RUN_TYPE ENERGY\n&END GLOBAL\n" and not notes6,
           "字面 ENERGY 同样原样保留")
    empty, notes6 = cutoffkit.align_run_type("&FORCE_EVAL\n&END FORCE_EVAL\n")
    report(empty == "&FORCE_EVAL\n&END FORCE_EVAL\n" and len(notes6) == 1
           and "没有 RUN_TYPE" in notes6[0],
           "没有 RUN_TYPE 时只提醒一次（CP2K 默认就是 ENERGY）")
    unresolved, notes6 = cutoffkit.align_run_type(
        "&GLOBAL\n   RUN_TYPE ${RTYPE}\n&END GLOBAL\n")
    report(unresolved == "&GLOBAL\n   RUN_TYPE ${RTYPE}\n&END GLOBAL\n"
           and len(notes6) == 1 and "改不了" in notes6[0],
           "展开之前改不了的 RUN_TYPE， {RTYPE}）只提醒，不瞎猜")

    # the same forcing on the file channel, end to end
    geo_in = src_dir / "geo.inp"
    geo_text = ("&GLOBAL\n   RUN_TYPE GEO_OPT\n   PROJECT geojob\n&END GLOBAL\n"
                "&FORCE_EVAL\n   METHOD QuickStep\n   &DFT\n      &MGRID\n"
                "         CUTOFF 400\n         REL_CUTOFF 60\n      &END MGRID\n"
                "   &END DFT\n&END FORCE_EVAL\n")
    geo_in.write_bytes(geo_text.encode("utf-8"))
    plan6 = cutoffkit.build_cutoff_study(geo_in, workdir / "geo_out",
                                         cutoffs="100", rel_cutoffs="20")
    tpl6 = read_template_text(workdir / "geo_out" / "template.inp")
    report("RUN_TYPE ENERGY" in tpl6 and "GEO_OPT" not in tpl6,
           "「已有输入」通道：template.inp 里的 RUN_TYPE 也变成MENERGY")
    report(any("RUN_TYPE 已从 GEO_OPT 改成 ENERGY" in note for note in plan6.notes),
           "结果里写清楚改了哪一行（用户得知道自己的输入被动过）")
    report("CUTOFF LT_cutoff" in tpl6,
           "同一次里占位符照旧种上（两件事互不干扰）")
    report("RUN_TYPE ENERGY" in tpl6.split("@END GLOBAL")[0]
           and tpl6.count("RUN_TYPE") == 1,
           "输入里只有一多 RUN_TYPE，没有多写一行")

    # ---- 7. the other channel: the tool's own form -------------------------- #
    if index is None:
        print("  ..   没有 data index，跳过「生成输入」通道")
    else:
        form_source = cutoffkit.source_from_spec(
            Spec(task="md", project="gridtest",
                 structure=read_structure(EXAMPLES / "water.xyz"),
                 vacuum=12.0, steps=1000, ensemble="NVE"), index)
        report(form_source.name == "gridtest.inp",
               f"「生成输入」通道的模板文件名跟着 project 走：{form_source.name}")
        report(any("单点能" in note and "MD 步数" in note and "系综" in note
                   for note in form_source.notes),
               "结果里点名了哪些 MD/优化选项被丢掉（不能悄悄丢，而且用界面上的说法）")

        out7 = workdir / "form_out"
        plan7 = cutoffkit.build_cutoff_study(form_source, out7,
                                             cutoffs="100,200", rel_cutoffs="20")
        tpl7 = read_template_text(out7 / "template.inp")
        report("RUN_TYPE ENERGY" in tpl7 and "&MD" not in tpl7,
               "MD 页的参数渲染出来是单点能输入（没有 &MD） ")
        report("SCF_GUESS ATOMIC" in tpl7 and "MAX_SCF 1" in tpl7
               and "IGNORE_CONVERGENCE_FAILURE T" in tpl7,
               "自动写上官方协议那三行（SCF_GUESS/MAX_SCF/IGNORE_...）")
        report("MAX_SCF 0" in tpl7,
               "&OUTER_SCF 也归零（否则 MAX_SCF 1 会变成 20 一 SCF 步）")
        report("PRINT_LEVEL MEDIUM" in tpl7,
               "PRINT_LEVEL 抬到 MEDIUM（否分 MULTIGRID INFO 不打印，分析脚本数不出网格）")
        report("CUTOFF [Ry] LT_cutoff" in tpl7 and "REL_CUTOFF [Ry] LT_rel_cutoff" in tpl7,
               "工具自己写的输入：占位符插在 [Ry] 之后")
        report('input_file=gridtest.inp' in (out7 / "cutoff_inputs.sh")
               .read_text(encoding="utf-8"),
               "脚本用的是模板自己的文件名（gridtest.inp）")
        report(sorted(path.name for path in out7.iterdir() if path.is_file())
               == ["cutoff_analyse.sh", "cutoff_inputs.sh", "cutoff_run.sh",
                   "rel_cutoff_analyse.sh", "rel_cutoff_inputs.sh",
                   "rel_cutoff_run.sh", "template.inp"],
               "两条通道生成的都还是那 7 个文件")

        try:
            cutoffkit.source_from_spec(
                Spec(task="md", method="FIST",
                     structure=read_structure(EXAMPLES / "water.xyz"),
                     vacuum=12.0), index)
            refused = False
        except ValueError:
            refused = True
        report(refused, "经典力场（FIST）被拒绝：它根本没有 CUTOFF 可收数")

    shutil.rmtree(workdir, ignore_errors=True)
    print(f"\npart 3 failures: {failures}")
    return 0 if ok else 1


class ShellGone(RuntimeError):
    """The POSIX shell died mid-run instead of running the script.

    On Windows the shell is WSL, and a long test can outlive the distro: it then
    answers ``Bash/Service/E_UNEXPECTED`` (on *stdout*, UTF-16) or Windows throws
    error 1450 ("not enough system resources").  Nothing about the generated
    scripts is being tested at that point, so the shell-dependent checks say
    "not verified here" instead of reporting failures - the same treatment a
    *missing* shell already got.
    """


#: output markers of a shell that never got to run the script
SHELL_FAILURE_MARKERS = ("E_UNEXPECTED", "Bash/Service", "Wsl/Service",
                         "系统资源不足", "not enough memory",
                         "页面文件太小", "paging file is too small")


def _low_memory() -> str | None:
    """A short reason when this machine cannot host a WSL VM right now.

    A starved Windows answers a WSL start with ``E_UNEXPECTED`` /
    ``RPC_S_CALL_FAILED``, and a WSL that dies mid-run takes the *script's* files
    with it (``cp: cannot stat 'BASIS_MOLOPT'`` - the ``/mnt`` mount hiccups) or
    fails the client with ``WinError 1455`` ("the paging file is too small").
    None of that has anything to do with the generated scripts, so those two
    parts say "not verified here" instead of reporting failures.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
    except Exception:            # noqa: BLE001 - never break the tests over this
        return None
    gb = 1024 ** 3
    free_physical = status.ullAvailPhys / gb
    free_commit = status.ullAvailPageFile / gb
    if free_commit < 2.0 or free_physical < 1.5:
        return (f"可用内存不足（物理 {free_physical:.1f} GB / 提交 {free_commit:.1f} GB）："
                "WSL 会在半路被系统杀掉，所以这里不做真跑验读")
    return None


def _shell_gone(result) -> str:
    """Why the shell failed *itself* (empty string = it did run the script)."""
    text = ((result.stdout or "") + (result.stderr or "")).replace("\x00", "")
    for marker in SHELL_FAILURE_MARKERS:
        if marker in text:
            return " ".join(text.split())[:100]
    return ""


def _usable_shell(tries: int = 5) -> str | None:
    """A POSIX shell that actually runs - or None, with the reason printed.

    On Windows ``shutil.which("bash")`` finds WSL's ``System32\\bash.exe``, and a
    stopped or broken distro answers ``Wsl/Service/E_UNEXPECTED`` (on *stdout*,
    UTF-16, exit 1).  That is an environment problem: the scripts cannot be run
    here, so say "not verified" instead of reporting the generated scripts as
    broken - and instead of crashing on a parameter-point directory that was
    never created.  A cold WSL start is slow, so probe a few times first.
    """
    import shutil
    import subprocess
    import time

    shell = shutil.which("bash") or shutil.which("sh")
    if shell is None:
        print("  skip 真跑脚本 / 驱动（找不到 sh/bash）")
        return None
    starved = _low_memory()
    if starved:
        print(f"  skip 真跑脚本 / 驱动（{starved}）")
        return None
    reason = ""
    for attempt in range(1, tries + 1):
        probed = None
        try:
            probed = subprocess.run([shell, "-c", "echo cp2kbuild"],
                                    capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
        except OSError as exc:
            # a shell that cannot even be *started* is the same kind of
            # environment problem: Windows error 1450 ("not enough system
            # resources") shows up right after a heavy WSL run
            reason = f"{type(exc).__name__}: {exc}"
        if probed is not None:
            if probed.returncode == 0 and "cp2kbuild" in (probed.stdout or ""):
                if attempt > 1:
                    print(f"  note {shell} 第 {attempt} 次才起来"
                          "（WSL 冷启动的竞态，与脚本无关）")
                return shell
            reason = " ".join(((probed.stderr or "") + (probed.stdout or ""))
                              .replace("\x00", "").split())[:100]
        if attempt < tries:
            time.sleep(0.5 * attempt)
    print(f"  skip 真跑脚本 / 驱动（{shell} 起不来：{reason or '没有输出'}）")
    return None


def part4_run_scripts() -> int:
    """Run the generated scripts for real: sh + a fake CP2K.

    This is the whole point of the file set, so it is checked end to end: the
    inputs script must produce one directory per value with the right numbers in
    it, the run script must drive CP2K (through a stub ``mpirun``/``cp2k``), and
    the analyse script must turn those outputs into the ``.ssv`` table.
    """
    import os
    import shutil
    import subprocess
    import tempfile
    import time

    from cp2kbuild import cutoffkit
    from cp2kbuild.paths import write_text_lf

    print()
    print("=" * 78)
    print("part 4: running the generated scripts (sh + a fake CP2K)")
    print("=" * 78)

    failures = 0
    ok = True

    def report(condition: bool, message: str) -> None:
        nonlocal failures, ok
        if condition:
            print(f"  ok   {message}")
        else:
            print(f"  FAIL {message}")
            failures += 1
            ok = False

    shell = _usable_shell()
    if shell is None:
        print("\npart 4 failures: 0")
        return 0

    workdir = Path(tempfile.mkdtemp(prefix="cp2kbuild_run_"))
    study = workdir / "cutoff_test"

    source = workdir / "system.inp"
    source.write_text(
        "&GLOBAL\n   RUN_TYPE ENERGY\n   PRINT_LEVEL MEDIUM\n&END GLOBAL\n"
        # a chained restart: it must not survive into the parameter-point inputs
        "&EXT_RESTART\n   RESTART_FILE_NAME cp2k-1_40000.restart\n"
        "&END EXT_RESTART\n"
        "&FORCE_EVAL\n   METHOD Quickstep\n   &DFT\n"
        "      BASIS_SET_FILE_NAME  BASIS_MOLOPT\n"
        "      POTENTIAL_FILE_NAME  GTH_POTENTIALS\n      &MGRID\n"
        "         CUTOFF 400\n         REL_CUTOFF 60\n      &END MGRID\n"
        "      &SCF\n         SCF_GUESS ATOMIC\n         MAX_SCF 1\n"
        "         IGNORE_CONVERGENCE_FAILURE TRUE\n      &END SCF\n"
        "   &END DFT\n&END FORCE_EVAL\n", encoding="utf-8")
    # the files the script copies: real files next to the input, so the copy is
    # actually exercised (and the run does not stop on a missing BASIS_MOLOPT)
    (workdir / "BASIS_MOLOPT").write_text("# basis sets (fixture)\n",
                                          encoding="utf-8")
    (workdir / "GTH_POTENTIALS").write_text("# potentials (fixture)\n",
                                            encoding="utf-8")
    cutoffkit.build_cutoff_study(source, study, cutoffs="50,100",
                                 rel_cutoffs="10,20", fixed_cutoff=100)

    # a second, tiny study *without* step 2's value: its scripts must refuse to run
    guarded = workdir / "guarded"
    cutoffkit.build_cutoff_study(source, guarded, cutoffs="50", rel_cutoffs="10")

    # a WSL bash cannot read a Windows path; this machine's bash is WSL
    wsl = os.name == "nt" and "system32" in shell.lower() \
        and shell.lower().endswith(("bash.exe", "sh.exe"))

    def shell_path(target) -> str:
        resolved = Path(target).resolve()
        if not wsl:
            return str(resolved)
        drive = resolved.drive.rstrip(":").lower()
        rest = str(resolved)[len(resolved.drive):].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"

    keepalive = None
    try:
        keepalive = subprocess.Popen([shell, "-c", "sleep 600"],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
    except OSError as exc:  # noqa: BLE001
        print(f"  note 无法保持 shell 常驻（{exc}）")

    def shell_retry(args: list[str], tries: int = 5, **kwargs):
        last = None
        for attempt in range(1, tries + 1):
            try:
                last = subprocess.run(args, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", **kwargs)
            except OSError as exc:            # WinError 1450 and friends
                raise ShellGone(f"{type(exc).__name__}: {exc}") from None
            gone = _shell_gone(last)
            if gone:
                # the VM dropped between two calls.  It usually answers again, so
                # retry *once* - but each attempt boots a VM, and on a machine
                # where WSL is short of memory hammering it makes things worse, so
                # the budget here is deliberately smaller than the probe's
                if attempt < min(tries, 2):
                    time.sleep(0.5 * attempt)
                    continue
                raise ShellGone(gone)
            if last.returncode == 0 or (last.stderr or "").strip():
                if attempt > 1:
                    print(f"  note shell 第 {attempt} 次才起来"
                          "（WSL 空闲关闭的竞态，与脚本无关）")
                return last, attempt
            if attempt < tries:
                time.sleep(0.5 * attempt)
        return last, tries

    try:
        scripts = sorted(path.name for path in study.glob("*.sh"))
        listing = " && ".join(f"sh -n {name}" for name in scripts)
        checked, attempts = shell_retry([shell, "-c", listing], cwd=str(study))
        report(len(scripts) == 6 and checked.returncode == 0,
               f"{len(scripts)} 个脚本都过了 sh -n 语法检查"
               + (f"（第 {attempts} 次才起来）" if attempts > 1 else "")
               if checked.returncode == 0 else
               f"语法检查失败：{(checked.stderr or '').strip()[:160]}")

        # the step-2 scripts must refuse to run before CUTOFF is filled in
        guard, _ = shell_retry([shell, "-c", "sh rel_cutoff_inputs.sh"],
                               cwd=str(guarded))
        report(guard.returncode != 0 and "第 1 步定下来的值" in (guard.stderr or "")
               + (guard.stdout or ""),
               "第 2 步的脚本在 cutoff 还是 CHANGE_ME 时拒绝运行并说明原因")
        report(not any(path.is_dir() for path in guarded.iterdir()),
               "被拒绝时不会留下半成品参数点目录")

        # ... and the first step must produce one directory per value
        made, _ = shell_retry([shell, "-c", "sh cutoff_inputs.sh"], cwd=str(study))
        report(made.returncode == 0, "cutoff_inputs.sh 跑通")
        dirs = sorted(path.name for path in study.iterdir() if path.is_dir())
        report(dirs == ["cutoff_100Ry", "cutoff_50Ry"],
               f"每个 CUTOFF 一个目录：{dirs}")
        for value in (50, 100):
            text = (study / f"cutoff_{value}Ry" / "system.inp").read_text(
                encoding="utf-8")
            report(f"         CUTOFF {value}\n" in text
                   and "REL_CUTOFF 60" in text
                   and "LT_cutoff" not in text,
                   f"cutoff_{value}Ry 里 CUTOFF={value}、REL_CUTOFF 固定 60，" 
                   "占位符已被替换")

        # a fake mpirun (drops -np/--bind-to) plus a fake cp2k that writes an
        # output whose energy depends on the input's CUTOFF - enough for the
        # analyse script to produce a real table
        fakes = workdir / "bin"
        fakes.mkdir()
        write_text_lf(fakes / "mpirun", """#!/bin/sh
while [ $# -gt 0 ]; do
    case "$1" in
        -np) shift 2 ;;
        --bind-to) shift 2 ;;
        *) break ;;
    esac
done
exec "$@"
""")
        write_text_lf(fakes / "cp2k.popt", """#!/bin/sh
input=""
out=cp2k.out
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out=$2; shift ;;
        -i) input=$2; shift ;;
        *) input=$1 ;;
    esac
    shift
done
energy=$(awk '/^[ \\t]*CUTOFF[ \\t]+[0-9]/{printf "%.10f", -17.15 + 40/$2/1000}' "$input")
{
  echo ' QS| Number of grid levels:                                                    4'
  echo ' QS| Density cutoff [a.u.]:                                                250.0'
  echo ' QS| Relative density cutoff [a.u.]:                                        30.0'
  echo ' count for grid        1:            152          cutoff [a.u.]         250.00'
  echo ' count for grid        2:           2420          cutoff [a.u.]          83.33'
  echo ' count for grid        3:           8100          cutoff [a.u.]          27.78'
  echo ' count for grid        4:          18200          cutoff [a.u.]           9.26'
  echo "  Total energy:                                                      $energy"
  echo ' PROGRAM ENDED AT 2026-09-13 22:00:00'
} > "$out"
""")
        for name in ("mpirun", "cp2k.popt"):
            (fakes / name).chmod(0o755)

        env = {**os.environ, "PATH": f"{shell_path(fakes)}:"
                                     + os.environ.get("PATH", "")}
        # the run script's header is meant to be edited ("改开头这几个变量"),
        # so the test points it at the fakes by absolute path - PATH through a
        # WSL bash is not something the scripts should depend on
        for script in ("cutoff_run.sh", "rel_cutoff_run.sh"):
            path = study / script
            text = path.read_text(encoding="utf-8")
            text = text.replace("mpirun_bin=mpirun",
                                f"mpirun_bin={shell_path(fakes / 'mpirun')}")
            text = text.replace("cp2k_bin=cp2k.popt",
                                f"cp2k_bin={shell_path(fakes / 'cp2k.popt')}")
            write_text_lf(path, text)
        ran, _ = shell_retry([shell, "-c", "sh cutoff_run.sh"],
                             cwd=str(study), env=env)
        outs = sorted(path.parent.name for path in study.glob("cutoff_*/system.out"))
        report(ran.returncode == 0 and outs == ["cutoff_100Ry", "cutoff_50Ry"],
               f"cutoff_run.sh 通过 mpirun/CP2K（桩）跑完两档：{outs}"
               if ran.returncode == 0 and outs else
               f"cutoff_run.sh rc={ran.returncode}，产出 {outs}，" 
               f"out={(ran.stdout or '').strip()[-200:]} "
               f"err={(ran.stderr or '').strip()[-200:]}")

        analysed, _ = shell_retry([shell, "-c", "sh cutoff_analyse.sh"],
                                  cwd=str(study))
        table = study / "cutoff_data.ssv"
        rows = [line.split() for line in table.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")] if table.is_file() else []
        report(analysed.returncode == 0 and len(rows) == 2,
               f"cutoff_analyse.sh 写出 {len(rows)} 行表（cutoff_data.ssv）"
               if analysed.returncode == 0 else
               f"cutoff_analyse.sh 退出码 {analysed.returncode}，"
               f"{(analysed.stderr or '').strip()[:160]}")
        if len(rows) == 2:
            report(rows[0][0] == "50.00" and rows[1][0] == "100.00"
                   and float(rows[0][1]) != float(rows[1][1]),
                   f"能量随 CUTOFF 变化（{rows[0][1]} vs {rows[1][1]}） ")
            report(rows[0][2:] == ["152", "2420", "8100", "18200"],
                   f"每档网格点数进了表：{rows[0][2:]}")
            header = next(line for line in
                          table.read_text(encoding="utf-8").splitlines()
                          if line.startswith("# Cutoff"))
            report("NG on grid 1" in header and "REL_CUTOFF = 60" in
                   table.read_text(encoding="utf-8"),
                   "表头与注释保留了教程的格式（NG on grid N / REL_CUTOFF = 60）")

        # rerunning the inputs script must clean up stale files, and a second
        # study with --cutoff given must be runnable end to end
        (study / "cutoff_50Ry" / "stale.txt").write_text("x", encoding="utf-8")
        shell_retry([shell, "-c", "sh cutoff_inputs.sh"], cwd=str(study))
        report(not (study / "cutoff_50Ry" / "stale.txt").exists(),
               "重复路 cutoff_inputs.sh 会清掉旧的参数点目录")
        second = workdir / "second"
        cutoffkit.build_cutoff_study(source, second, cutoffs="50",
                                     rel_cutoffs="10,20", fixed_cutoff=50)
        for script in ("rel_cutoff_run.sh",):
            path = second / script
            text = path.read_text(encoding="utf-8")
            text = text.replace("mpirun_bin=mpirun",
                                f"mpirun_bin={shell_path(fakes / 'mpirun')}")
            text = text.replace("cp2k_bin=cp2k.popt",
                                f"cp2k_bin={shell_path(fakes / 'cp2k.popt')}")
            write_text_lf(path, text)
        ok2, _ = shell_retry(
            [shell, "-c", "sh rel_cutoff_inputs.sh && sh rel_cutoff_run.sh && "
                          "sh rel_cutoff_analyse.sh"],
            cwd=str(second), env={**os.environ, "PATH": f"{shell_path(fakes)}:"
                                                   + os.environ.get("PATH", "")})
        rel_table = second / "rel_cutoff_data.ssv"
        rel_rows = [line.split() for line in
                    rel_table.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")] \
            if rel_table.is_file() else []
        report(ok2.returncode == 0 and len(rel_rows) == 2,
               f"第 2 步（填好 CUTOFF 后）也能一路跑通，写出 {len(rel_rows)} 行" 
               if ok2.returncode == 0 else
               f"第A2 步退出码 {ok2.returncode}：{(ok2.stderr or '').strip()[:160]}")

        # ... and the numbers in those inputs must be the right way round: step 2
        # sweeps REL_CUTOFF and holds CUTOFF.  Checking only "it ran" is what let
        # a swapped pair of sed expressions through once - every rel_cutoff_*Ry
        # directory then contained CUTOFF=<its own name> and a constant
        # REL_CUTOFF, i.e. a second CUTOFF scan with a nonsense REL_CUTOFF.
        def numbers_in(path: Path) -> dict:
            found = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                words = line.replace("[Ry]", " ").split()
                if len(words) >= 2 and words[0] in ("CUTOFF", "REL_CUTOFF"):
                    found[words[0]] = float(words[-1])
            return found

        wrong = []
        for ii in (10, 20):
            generated = second / f"rel_cutoff_{ii}Ry" / "system.inp"
            if not generated.is_file():
                wrong.append(f"rel_cutoff_{ii}Ry/system.inp 不存在")
                continue
            got = numbers_in(generated)
            if got.get("REL_CUTOFF") != float(ii) or got.get("CUTOFF") != 50.0:
                wrong.append(f"rel_cutoff_{ii}Ry: {got}")
        report(not wrong,
               f"第A2 步每个目录扫的是 REL_CUTOFF、CUTOFF 保持固定：{wrong}"
               if wrong else
               "第A2 步每个目录扫的是 REL_CUTOFF，R0/20），CUTOFF 一直是 50")
        step1_got = numbers_in(study / "cutoff_50Ry" / "system.inp")
        report(step1_got.get("CUTOFF") == 50.0
               and step1_got.get("REL_CUTOFF") == 60.0,
               f"第E1 步反过来：CUTOFF 扫、REL_CUTOFF 固定 60：{step1_got}")

        # the chained restart from the source must not reach any parameter point
        chained_points = []
        for generated in list(study.glob("cutoff_*/system.inp")) + \
                list(second.glob("rel_cutoff_*/system.inp")):
            for line in generated.read_text(encoding="utf-8").splitlines():
                stripped = line.lstrip()
                if stripped and stripped[0] not in "#!" \
                        and "EXT_RESTART" in stripped:
                    chained_points.append(f"{generated.parent.name}: {stripped}")
        report(not chained_points,
               f"每个参数点的输入里都没有活着的 &EXT_RESTART：{chained_points}"
               if chained_points else
               "每个参数点的输入里都没有活着的 &EXT_RESTART（模板里那一段被注释掉了）")
        report("RESTART_FILE_NAME cp2k-1_40000.restart" in
               (study / "template.inp").read_text(encoding="utf-8"),
               "被注释掉的那一段还留在 template.inp 里（用户可以自己改回来）")
    except ShellGone as exc:
        print(f"  skip 真跑脚本中途停下（shell 没了：{exc}）"
              "——脚本本身没有验证过，不算失质")
    finally:
        if keepalive is not None:
            keepalive.terminate()
            try:
                keepalive.wait(timeout=5)
            except subprocess.TimeoutExpired:
                keepalive.kill()

    if not failures:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"  (工作目录保留在 {workdir} 方便排查)")
    print(f"\npart 4 failures: {failures}")
    return 0 if ok else 1


def part5_temperature_program(index=None, db=None) -> int:
    """A staged temperature program: the schedule, the stages, the driver.

    CP2K cannot do a temperature program in one run (its ``&MD`` has a single
    ``TEMPERATURE`` and one per-step factor), so the tool writes one input per
    segment and a serial driver that chains them through ``&EXT_RESTART``.  Both
    halves are checked here: the plan (steps, factors, keywords, notes) and a real
    ``sh`` run of the driver with a fake CP2K that only writes the *step-numbered*
    restart file CP2K itself produces.
    """
    from cp2kbuild import tempprogram
    from cp2kbuild.paths import write_text_lf
    from cp2kbuild.recipes import plan_ramp

    print()
    print("=" * 78)
    print("part 5: the staged temperature program (hold / heat / cool)")
    print("=" * 78)

    failures = 0
    ok = True

    def report(condition: bool, message: str) -> None:
        nonlocal failures, ok
        if condition:
            print(f"  ok   {message}")
        else:
            print(f"  FAIL {message}")
            failures += 1
            ok = False

    import shutil
    import subprocess
    import tempfile
    import time

    water = read_structure(EXAMPLES / "water.xyz")
    workdir = Path(tempfile.mkdtemp(prefix="cp2kbuild_program_"))

    program = ("# 平衡 → 两段升温 → 保温 → 降温\n"
               "hold 300 5ps\n"
               "ramp 300 500 10ps\n"
               "hold 500 5ps\n"
               "ramp 500 600 8ps\n"
               "hold 600 10ps\n"
               "ramp 600 300 20ps\n")

    # ---- 1. the schedule text ---------------------------------------------- #
    segments = tempprogram.parse_program(program, timestep=0.5)
    report([(s.kind, s.start, s.end, s.duration_ps) for s in segments] == [
        ("hold", 300.0, 300.0, 5.0), ("ramp", 300.0, 500.0, 10.0),
        ("hold", 500.0, 500.0, 5.0), ("ramp", 500.0, 600.0, 8.0),
        ("hold", 600.0, 600.0, 10.0), ("ramp", 600.0, 300.0, 20.0)],
        "程序文本读成 6 段（保持/升温/降温）")
    report(tempprogram.parse_compact("300:5,300-500:10,500:5", timestep=0.5)
           == tempprogram.parse_program("hold 300 5ps\nramp 300 500 10ps\n"
                                        "hold 500 5ps\n", timestep=0.5),
           "一行简写 `300:5,300-500:10,500:5` 与分行写法结果一致")
    steps_units = tempprogram.parse_program("hold 300 50000steps\n"
                                            "ramp 300 400 8fs\n", timestep=0.5)
    report(abs(steps_units[0].duration_ps - 25.0) < 1e-9
           and abs(steps_units[1].duration_ps - 0.008) < 1e-12,
           "时长支持 steps（精确）与 fs 单位")
    bad = []
    for text, why in (("hold 300 5xyz", "单位不对"),
                      ("hold 0 5ps", "温度不是正数"),
                      ("hold 300 0", "时长不是正数"),
                      ("hold 300 310 5ps", "hold 却给了两个温度"),
                      ("", "空程度")):
        try:
            tempprogram.parse_program(text, timestep=0.5)
            bad.append(why)
        except ValueError:
            pass
    report(not bad, f"坏程序被拒绝（没拒绝的：{bad}）" if bad else
           "坏程序（单位/温度/时长/hold 给两个温度 空）都被拒绝并说明原因")

    # ---- 2. the stages ----------------------------------------------------- #
    stages = tempprogram.plan_program(segments, timestep=0.5, ensemble="LANGEVIN")
    want_steps = [10000, 20000, 10000, 16000, 20000, 40000]
    report([stage.steps for stage in stages] == want_steps,
           f"时长换成整步数：{want_steps}")
    report([stage.keyword for stage in stages] == [
        None, "TEMPERATURE_ANNEALING", None, "TEMPERATURE_ANNEALING", None,
        "TEMPERATURE_ANNEALING"],
        "只有 ramp 段写退火关键字（hold 段什么都不写）")
    second = stages[1]
    report(second.factor == plan_ramp(300.0, 500.0, 20000, 0.5, "LANGEVIN").factor,
           f"升温因子与单段换算器一致：{second.factor:.12f}")
    nve_stages = tempprogram.plan_program(segments, timestep=0.5, ensemble="NVE")
    report(all(stage.mechanism in ("hold", "velocity") for stage in nve_stages)
           and nve_stages[1].keyword == "ANNEALING",
           "NVE 一 ramp 段改用速度缩放（ANNEALING）")
    try:
        tempprogram.plan_program(
            tempprogram.parse_program("ramp 300 500 10ps", timestep=0.5),
            timestep=0.5, ensemble="NVT")
        report(False, "NVT 下写升温程序居然没报锛")
    except ValueError as exc:
        report("LANGEVIN" in str(exc),
               "NVT 下写升温程序直接报错，并说清该换成什也")
    names = [stage.name for stage in stages]
    report(all(re.fullmatch(r"stage\d\d_[A-Za-z0-9_]+", name) for name in names)
           and len(set(names)) == len(names),
           f"每段一个安全的文件名 PROJECT_NAME：{names[1]}— ")

    notes = " ".join(tempprogram.program_notes(stages, timestep=0.5,
                                               ensemble="LANGEVIN"))
    report("跳变" not in notes, "温度连续的e6 段程序不会被当成跳变")
    jump = tempprogram.plan_program(
        tempprogram.parse_program("hold 300 5ps\nhold 600 5ps", timestep=0.5),
        timestep=0.5, ensemble="LANGEVIN")
    report("跳变" in " ".join(
        tempprogram.program_notes(jump, timestep=0.5, ensemble="LANGEVIN")),
        "上一段停在 300 K、下一段从 600 K 开始时提醒这是一次跳只")
    nve_notes = " ".join(tempprogram.program_notes(
        tempprogram.plan_program(tempprogram.parse_program("hold 300 5ps",
                                                          timestep=0.5),
                                 timestep=0.5, ensemble="NVE"),
        timestep=0.5, ensemble="NVE"))
    report("不会真的保持" in nve_notes,
           "NVE 下的「保持」段提醒它保不住温度")
    fast = tempprogram.plan_program(
        tempprogram.parse_program("ramp 300 600 0.1ps", timestep=0.5),
        timestep=0.5, ensemble="LANGEVIN")
    report("每步移动" in " ".join(
        tempprogram.program_notes(fast, timestep=0.5, ensemble="LANGEVIN")),
        "斜率过快的段会被点名")

    # ---- 3. what lands on disk --------------------------------------------- #
    spec = Spec(task="md", structure=water, vacuum=12.0, ensemble="LANGEVIN",
                timestep=0.5, langevin_gamma=0.01, steps=1)
    out = workdir / "prog"
    plan = tempprogram.build_from_text(spec, index, program, out)
    names = sorted(path.name for path in out.iterdir())
    report(names == sorted([f"{stage.name}.inp" for stage in stages]
                           + ["program.ssv", "run_program.sh", "program.txt"]),
           f"写出 {len(stages)} 个段输入 + 程序/计划行 驱动脚本：{len(names)} 个文件")
    first = (out / f"{stages[0].name}.inp").read_text(encoding="utf-8")
    last = (out / f"{stages[-1].name}.inp").read_text(encoding="utf-8")

    def sections_in(text: str) -> list[str]:
        """Active section headers (a mention inside a comment does not count)."""
        found = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("&") and len(stripped.split()) > 0:
                found.append(stripped.split()[0].upper())
        return found

    report("&EXT_RESTART" not in sections_in(first),
           "第一段没有 &EXT_RESTART（速度按起始温度重新抽取）")
    report("PROJECT_NAME " + stages[0].name in first
           and "STEPS 10000" in first and "TEMPERATURE [K] 300.0" in first
           and "TEMPERATURE_ANNEALING" not in first,
           "第一段（保持 300 K）只写 STEPS/TEMPERATURE，没有退火关键字")
    report("&EXT_RESTART" in sections_in(last)
           and f"RESTART_FILE_NAME {stages[-2].name}-1.restart" in last
           and "RESTART_POS T" in last and "RESTART_VEL T" in last
           and "RESTART_COUNTERS T" in last,
           "后面的段接上一段的 restart（坐标 速度/计数器）")
    # ... and the wave function, otherwise every stage pays an atomic guess:
    # the stages have different project names, so a plain SCF_GUESS RESTART in
    # stage 2 would look for the wrong file
    report(f"WFN_RESTART_FILE_NAME {stages[-2].name}-RESTART.wfn" in last
           and "SCF_GUESS RESTART" in last,
           "后面的段还接着上一段的波函数（WFN_RESTART_FILE_NAME）")
    report("SCF_GUESS ATOMIC" in first
           and "WFN_RESTART_FILE_NAME" not in first,
           "第一段没有可接的波函数：明确用 ATOMIC（不去打找不到文件的 warning）e")
    # the continuity of the trajectory across a boundary is the whole reason the
    # take-list looks the way it does - say it, do not just do it
    continuity = " ".join(plan.notes)
    report("连续" in continuity and "ASPC" in continuity
           and "RANDOMG" in continuity,
           "结果里说明了段边界上什么连续（坐标/速度/计数器 波函数 恒温器状态）"
           "以及什么不随_restart 走（SCF 外推历史、随机数发生器）")
    report("TEMPERATURE [K] 600.0" in last
           and "TEMPERATURE_ANNEALING 0.999982" in last,
           "降温段：起始温度 600 K + 小于 1 的因子")
    report("GAMMA [fs^-1] 0.01" in first and "&THERMOSTAT" not in first,
           "LANGEVIN 段写 GAMMA，不写 &THERMOSTAT（它自带控温）")
    table = (out / "program.ssv").read_text(encoding="utf-8").splitlines()
    report(len(table) == len(stages) + 1
           and table[1].split("\t")[1] == "hold"
           and table[2].split("\t")[-1].startswith("1.0000"),
           f"计划行 program.ssv 一行一段：{len(table) - 1} 段 + 表头")

    if db is not None:
        validator = Validator(db, index)
        broken = []
        for stage in stages:
            text = (out / f"{stage.name}.inp").read_text(encoding="utf-8")
            errors = [d for d in validator.run(parse_input(text).sections)
                      if d.level == "error"]
            if errors:
                broken.append(f"{stage.name}: {errors[0].code}")
        report(not broken, f"每一段的输入校验 0 error：{broken}" if broken else
               f"每一段的输入都过了校验（{len(stages)} 段，0 error）")

    # the seed restart lets the program continue an equilibration
    seeded = workdir / "seeded"
    plan_seed = tempprogram.build_from_text(
        spec, index, "hold 300 5ps\nramp 300 400 5ps\n", seeded,
        seed_restart="equilibrated-1.restart")
    seeded_first = (seeded / f"{plan_seed.stages[0].name}.inp").read_text(
        encoding="utf-8")
    report("RESTART_FILE_NAME equilibrated-1.restart" in seeded_first,
           "给了接力文件时，第一段也从它接手（而不是重新抽速度）")
    report("WFN_RESTART_FILE_NAME equilibrated-RESTART.wfn" in seeded_first
           and "SCF_GUESS RESTART" in seeded_first,
           "第一段的种子 restart 旁边那份波函数也顺手接上（equilibrated-RESTART.wfn）")

    # ---- 5. 和单段「升温 / 降温」互不打架，但要说清楚谁说了算-------------- #
    # Both controls live on the same page.  The program owns the temperature: a
    # stage writes the factor its own segment needs, a hold writes none - so the
    # single-run factor must NOT leak in, and the result has to say so.
    both = dataclasses.replace(spec, temperature_annealing=1.0005,
                               annealing_cell=0.9,
                               restart_temperature_annealing=True)
    both_dir = workdir / "both"
    plan_both = tempprogram.build_from_text(
        both, index, "hold 300 5ps\nramp 300 400 5ps\n", both_dir)
    hold_text = (both_dir / f"{plan_both.stages[0].name}.inp").read_text(
        encoding="utf-8")
    ramp_path = both_dir / f"{plan_both.stages[1].name}.inp"
    ramp_text = ramp_path.read_text(encoding="utf-8")
    active_hold = [line.strip() for line in hold_text.splitlines()
                   if line.strip().startswith(("ANNEALING",
                                               "TEMPERATURE_ANNEALING"))]
    report(not active_hold and "1.0005" not in hold_text,
           f"单段的因子不会漏进「保持」段：{active_hold}")
    expected = tempprogram.plan_program(
        tempprogram.parse_program("hold 300 5ps\nramp 300 400 5ps",
                                  timestep=0.5),
        timestep=0.5, ensemble="LANGEVIN")[1].factor
    motion = next(section for section in read_input(str(ramp_path)).sections
                  if section.name.upper() == "MOTION")
    written_factor = float(motion.subsection("MD").get("TEMPERATURE_ANNEALING"))
    # the input keeps ~12 significant digits, so compare with a tolerance that is
    # far below the difference from the single-run factor (5e-4)
    report(abs(written_factor - expected) < 1e-9 and "1.0005" not in ramp_text,
           f"升温段用的是程序自己算的因子（不是单段那个），"
           f"{written_factor:.12f} vs 单段的E1.0005")
    report("ANNEALING_CELL 0.9" not in hold_text,
           "LANGEVIN 一 ANNEALING_CELL 不会写进任何一段（只有 NPE 会用它）")
    notes_both = " ".join(plan_both.notes)
    report("不会进这份程序" in notes_both and "ANNEALING_CELL" in notes_both
           and "RESTART_TEMPERATURE_ANNEALING" in notes_both,
           "结果里把三件事都说明了：单段因子不进程序、ANNEALING_CELL 是程序级的、"
           "接力斜坡不需要（每段自带起始温度）")

    npe = dataclasses.replace(spec, ensemble="NPE_I", annealing_cell=0.9)
    npe_dir = workdir / "npe"
    plan_npe = tempprogram.build_from_text(
        npe, index, "hold 300 2ps\nhold 300 2ps\n", npe_dir)
    npe_stages = [(npe_dir / f"{stage.name}.inp").read_text(encoding="utf-8")
                  for stage in plan_npe.stages]
    report(all("ANNEALING_CELL 0.9" in text for text in npe_stages),
           "NPE 一 ANNEALING_CELL 是程序级的：每一段（含保持段）都带上它")
    report("不会真的保持" in " ".join(plan_npe.notes),
           "NPE 下的「保持」段照旧提醒保不住温度")

    # ---- 4. running the driver for real ------------------------------------ #
    shell = _usable_shell()
    if shell is None:
        print(f"\npart 5 failures: {failures}")
        return 0 if ok else 1

    wsl = os.name == "nt" and "system32" in shell.lower() \
        and shell.lower().endswith(("bash.exe", "sh.exe"))

    def shell_path(target) -> str:
        resolved = Path(target).resolve()
        if not wsl:
            return str(resolved)
        drive = resolved.drive.rstrip(":").lower()
        rest = str(resolved)[len(resolved.drive):].replace("\\", "/").lstrip("/")
        return f"/mnt/{drive}/{rest}"

    fakes = workdir / "bin"
    fakes.mkdir()
    # the fake CP2K only writes the *step-numbered* restart file, which is what
    # CP2K's own ADD_LAST NUMERIC naming produces - the driver has to normalise it
    write_text_lf(fakes / "mpirun", """#!/bin/sh
while [ $# -gt 0 ]; do
    case "$1" in
        -np) shift 2 ;;
        --bind-to) shift 2 ;;
        *) break ;;
    esac
done
exec "$@"
""")
    write_text_lf(fakes / "cp2k.psmp", """#!/bin/bash
input=""
out=cp2k.out
while [ $# -gt 0 ]; do
    case "$1" in
        -i) input=$2; shift ;;
        -o) out=$2; shift ;;
        *) input=$1 ;;
    esac
    shift
done
project=$(sed -n 's/^[ \\t]*PROJECT_NAME[ \\t]*//p' "$input" | head -1)
restart=$(sed -n 's/^[ \\t]*RESTART_FILE_NAME[ \\t]*//p' "$input" | head -1)
if [ -n "$restart" ] && [ ! -f "$restart" ] ; then
    echo "FAKE-CP2K: missing restart $restart" >&2
    exit 3
fi
{
  echo " SCF WAVEFUNCTION OPTIMIZATION"
  echo " MD| Step number $project"
  echo " Total energy:  -17.1234567890"
} > "$out"
echo "# fake restart for $project" > "${project}-1_1000.restart"
""")
    for name in ("mpirun", "cp2k.psmp"):
        (fakes / name).chmod(0o755)

    def patch(directory: Path, binaries: Path | None = None) -> None:
        bins = binaries or fakes
        path = directory / "run_program.sh"
        text = path.read_text(encoding="utf-8")
        text = text.replace("mpirun_bin=mpirun",
                            f"mpirun_bin={shell_path(bins / 'mpirun')}")
        text = text.replace("cp2k_bin=cp2k.psmp",
                            f"cp2k_bin={shell_path(bins / 'cp2k.psmp')}")
        write_text_lf(path, text)

    keepalive = None
    try:
        keepalive = subprocess.Popen([shell, "-c", "sleep 600"],
                                     stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
    except OSError:
        pass

    def shell_run(args: list[str], cwd: Path, tries: int = 5):
        last = None
        for attempt in range(1, tries + 1):
            try:
                last = subprocess.run(args, cwd=str(cwd), capture_output=True,
                                      text=True, encoding="utf-8",
                                      errors="replace")
            except OSError as exc:            # WinError 1450 and friends
                raise ShellGone(f"{type(exc).__name__}: {exc}") from None
            gone = _shell_gone(last)
            if gone:
                # one retry only: a WSL that is short of memory must not be
                # hammered with fresh VMs (see the part-4 twin of this helper)
                if attempt < min(tries, 2):
                    time.sleep(0.5 * attempt)
                    continue
                raise ShellGone(gone)
            if last.returncode == 0 or (last.stderr or "").strip():
                return last, attempt
            if attempt < tries:
                time.sleep(0.5 * attempt)
        return last, tries

    try:
        small = workdir / "small"
        small_program = "hold 300 0.005ps\nramp 300 400 0.01ps\nramp 400 350 0.005ps\n"
        small_plan = tempprogram.build_from_text(spec, index, small_program, small)
        patch(small)
        checked, _ = shell_run([shell, "-c", "sh -n run_program.sh"], small)
        report(checked.returncode == 0,
               "run_program.sh 这ish -n 语法检查"
               if checked.returncode == 0 else
               f"语法检查失败：{(checked.stderr or '').strip()[:160]}")
        ran, attempts = shell_run([shell, "-c", "sh run_program.sh"], small)
        outs = sorted(path.name for path in small.glob("stage*.out"))
        report(ran.returncode == 0
               and len(outs) == len(small_plan.stages),
               f"驱动脚本按顺序跑它 {len(small_plan.stages)} 段：{outs}"
               if ran.returncode == 0 else
               f"驱动脚本失败（rc={ran.returncode}）："
               f"{(ran.stderr or '').strip()[-200:]}")
        other, _ = shell_run([shell, "-c", "sh run_program.sh"], small)
        report(other.returncode == 0,
               "重复跑一遍也没问题（每段都从自己上一段的 restart 接手）"
               if other.returncode == 0 else
               f"第二遍失败：{(other.stderr or '').strip()[-200:]}")
        # 段与段是严格串行的链条：脚本里没有 几个算例同时路 那种后台/等待
        driver_text = (small / "run_program.sh").read_text(encoding="utf-8")
        report("串行" in driver_text and "不能并行" in driver_text
               and "wait" not in driver_text
               and not any(line.rstrip().endswith("&")
                           for line in driver_text.splitlines()),
               "驱动脚本写明是串行链条（没有后台任务、也没有 wait 批处理）")
        # 段内启动按项目集群的标准写法：OMP_NUM_THREADS=2 + mpirun -np 64 + cp2k.psmp
        # （注意 patch() 已经把 cp2k_bin 换成了假 CP2K 的绝对路径，所以这里只查名字）
        report("export OMP_NUM_THREADS=2" in driver_text
               and "cp2k.psmp" in driver_text
               and "no_proc=64" in driver_text
               and "resume=true" in driver_text
               and '-i "$stage.inp" -o "$stage.out"' in driver_text
               and 'if [ -z "$mpirun_bin" ]' in driver_text,
               "段内按集群标准起作业：OMP_NUM_THREADS=2 + "
               "mpirun -np 64 cp2k.psmp -i/-o（mpirun_bin 置空仍是单进程）")
        # ... and the controls really change the script (library level, so the
        # toolbox is checked without the GUI/CLI in the way)
        custom = tempprogram.program_script(
            small_plan.stages,
            run=tempprogram.RunOptions(mpi_procs=8, omp_threads=1,
                                       cp2k_bin="cp2k.popt",
                                       mpirun_extra="--bind-to none",
                                       resume=False))
        report("export OMP_NUM_THREADS=1" in custom
               and "no_proc=8" in custom and "cp2k_bin=cp2k.popt" in custom
               and 'mpirun_extra="--bind-to none"' in custom
               and "resume=false" in custom,
               "RunOptions 真的改脚本：-np 8 / OMP 1 / cp2k.popt / "
               "mpirun_extra / resume=false")
        # 断点续跑：跑完的段（<段名>-1.restart 每 <段名>.inp 新）会被跳过， 
        # 改动某一段的输入会让它和它后面的段都重跑（链条从那里断了） 
        rerun_log = (other.stdout or "")
        report(rerun_log.count("跳过") == len(small_plan.stages),
               f"再跑一遍时全部段都被跳过（{rerun_log.count('跳过')} 次）"
               if rerun_log.count("跳过") == len(small_plan.stages) else
               f"第二次跑没有按预期跳过：{rerun_log.strip()[-160:]}")
        changed = small / f"{small_plan.stages[1].name}.inp"
        # write_text_lf, not Path.write_text: the latter would rewrite the whole
        # input with CRLF on Windows, and then the restart *name* the input
        # carries ends in "\r" - which the stage guard correctly rejects
        write_text_lf(changed, changed.read_text(encoding="utf-8") + "\n")
        import time as _time
        _time.sleep(1.1)                      # mtime 的分辨率
        after, _ = shell_run([shell, "-c", "sh run_program.sh"], small)
        report(after.returncode == 0
               and (after.stdout or "").count("跳过") == 1
               and (after.stdout or "").count("开始") == len(small_plan.stages) - 1,
               f"改过第 2 段的输入后：第 1 段跳过、第 2 段起全部重跑"
               f"（跳这 {(after.stdout or '').count('跳过')} 次 / "
               f"开始 {(after.stdout or '').count('开始')} 次）")
        normalised = [f"{stage.name}-1.restart" for stage in small_plan.stages]
        report(all((small / name).is_file() for name in normalised),
               "每段写出的带步数 restart 都被归一化成 <段名>-1.restart")
        report(all((small / f"{stage.name}-1_1000.restart").is_file()
                   for stage in small_plan.stages),
               "带步数的原文件也留着（要自己看的话还在）")
        log = (ran.stdout or "")
        report("restart:" in log,
               f"日志里写明了每一步 restart 的归一名："
               f"{[line.strip() for line in log.splitlines() if 'restart:' in line][:1]}"
               if "restart:" in log else "日志里没提_restart 归一化")

        # a stage that writes no restart file must stop the chain loudly
        broken = workdir / "broken"
        plan_broken = tempprogram.build_from_text(
            spec, index, "hold 300 0.005ps\nhold 300 0.005ps\n", broken)
        # a second stub that never writes a restart file
        silent = workdir / "bin_silent"
        silent.mkdir()
        shutil.copy2(fakes / "mpirun", silent / "mpirun")
        write_text_lf(silent / "cp2k.psmp", (fakes / "cp2k.psmp")
                      .read_text(encoding="utf-8")
                      .replace('echo "# fake restart for $project" > '
                               '"${project}-1_1000.restart"', ":"))
        (silent / "mpirun").chmod(0o755)
        (silent / "cp2k.psmp").chmod(0o755)
        patch(broken, silent)
        stopped, _ = shell_run([shell, "-c", "sh run_program.sh"], broken,
                               tries=1)
        report(stopped.returncode != 0
               and "没有写出 restart" in (stopped.stderr or "") + (stopped.stdout or ""),
               "某一段没写 restart 时驱动脚本立刻停下并说明原因"
               if stopped.returncode != 0 else
               "某一段没写 restart 时驱动脚本居然继续跑了")
        report(len(list(broken.glob("stage*.out"))) == 1,
               f"停下时只跑了第一段（{len(list(broken.glob('stage*.out')))} 个 .out）")

        # ---- the completion criterion (audit: a killed stage used to be skipped
        # when CP2K itself wrote the plain <项目>-1.restart name) ------------- #
        plain_bins = workdir / "bin_plain"
        plain_bins.mkdir()
        shutil.copy2(fakes / "mpirun", plain_bins / "mpirun")
        write_text_lf(plain_bins / "cp2k.psmp", (fakes / "cp2k.psmp")
                      .read_text(encoding="utf-8")
                      .replace('"${project}-1_1000.restart"', '"${project}-1.restart"')
                      # write the plain restart, then die on the first call - a
                      # walltime kill or a dropped ssh session
                      + "if [ ! -f .killed_once ] ; then : > .killed_once ;\n"
                        "  echo 'FAKE-CP2K: killed mid-stage' >&2 ; exit 137 ; fi\n")
        (plain_bins / "mpirun").chmod(0o755)
        (plain_bins / "cp2k.psmp").chmod(0o755)

        plain = workdir / "plainnames"
        plan_plain = tempprogram.build_from_text(
            spec, index, "hold 300 0.005ps\nhold 300 0.005ps\n", plain)
        patch(plain, plain_bins)
        killed, _ = shell_run([shell, "-c", "sh run_program.sh"], plain, tries=1)
        again, _ = shell_run([shell, "-c", "sh run_program.sh"], plain, tries=1)
        again_text = again.stdout or ""
        report(killed.returncode != 0
               and (plain / f"{plan_plain.stages[0].name}-1.restart").is_file()
               and "开始" in again_text and "跳过" not in again_text,
               "被砍掉的段会重跑：判据是脚本自己写的完成标记，"
               "不再看 CP2K 写出来的 restart 名字"
               if "跳过" not in again_text else
               f"被砍掉的段又被跳过了：{again_text.strip()[-160:]}")
        # ... and re-writing the same stage inputs must not throw the finished
        # stages away: the criterion is the input's checksum, not its mtime
        import time as _time2
        for stage in plan_plain.stages:
            write_text_lf(plain / f"{stage.name}.inp",
                          (plain / f"{stage.name}.inp").read_text(encoding="utf-8"))
        _time2.sleep(1.1)
        touched, _ = shell_run([shell, "-c", "sh run_program.sh"], plain, tries=1)
        report((touched.stdout or "").count("跳过") == len(plan_plain.stages),
               f"原样重写段输入后再跑：已完成的段仍然跳过，"
               f"{(touched.stdout or '').count('跳过')} 次）")
        # a directory from the *old* tool version (hand-off files, no markers):
        # the first run after the upgrade still trusts the old criterion, so the
        # user's finished stages are not thrown away
        legacy = workdir / "legacy"
        plan_legacy = tempprogram.build_from_text(
            spec, index, "hold 300 0.005ps\nhold 300 0.005ps\n", legacy)
        for stage in plan_legacy.stages:
            write_text_lf(legacy / f"{stage.name}-1.restart", "# old chain\n")
        _time2.sleep(1.1)
        migrated, _ = shell_run([shell, "-c", "sh run_program.sh"], legacy, tries=1)
        report((migrated.stdout or "").count("跳过") == len(plan_legacy.stages),
               "老版本留下的目录（只有交接文件、没有完成标记）：升级后第一次仍然全部跳这")
    except ShellGone as exc:
        print(f"  skip 真跑驱动脚本中途停下（shell 没了：{exc}）"
              "——脚本本身没有验证过，不算失质")
    finally:
        if keepalive is not None:
            keepalive.terminate()
            try:
                keepalive.wait(timeout=5)
            except subprocess.TimeoutExpired:
                keepalive.kill()

    if not failures:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"  (工作目录保留在 {workdir} 方便排查)")
    print(f"\npart 5 failures: {failures}")
    return 0 if ok else 1


def main() -> int:
    source = newest_source_tree()
    default_tests = str(source / "tests") if source else str(Path(r"E:\desktop\cp2k\cp2k-2025.1\tests"))
    default_data = str(source / "data") if source else r"E:\desktop\cp2k\cp2k-2025.1\data"

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=default_data)
    parser.add_argument("--tests", default=default_tests)
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--skip-part1", action="store_true")
    parser.add_argument("--skip-part2", action="store_true")
    parser.add_argument("--skip-part3", action="store_true")
    parser.add_argument("--skip-part4", action="store_true")
    parser.add_argument("--skip-part5", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if source:
        print(f"source tree: {source}")

    index = DataIndex(args.data_dir)
    print("data index:", index.summary())
    db = load_db()
    if db is None:
        print("!! manual database missing - schema checks disabled")
    else:
        print("manual db :", db.stats())

    failures = 0
    if not args.skip_part1:
        failures += part1(index, db, quiet=args.quiet, source=source)
        failures += part1b_knobs(index, db, quiet=args.quiet)
    if not args.skip_part2:
        failures += part2(db, index, Path(args.tests), args.limit)
    if not args.skip_part3:
        failures += part3_cutoff_scripts(index)
    if not args.skip_part4:
        failures += part4_run_scripts()
    if not args.skip_part5:
        failures += part5_temperature_program(index, db)
    print(f"\ntotal failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

