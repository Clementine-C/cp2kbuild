"""Read the **parameters** back out of a CP2K input file.

A CP2K input file - and equally a ``<PROJECT>-1.restart`` file, because CP2K
writes the restart as a complete input - says everything about a run: the task
(``RUN_TYPE``), the method (``METHOD``), the SCF route, the MD ensemble, the
print/restart settings, and (for ``METHOD FIST``) the force-field files.

Loading such a file as a **structure** gives the geometry.  This module gives
the settings, so the form can be brought in line with the file instead of
silently building something else.  It never guesses: a keyword that is not in
the file produces no setting, and everything the tool noticed but does not model
is reported in :attr:`ImportReport.unread`.

Pure library code - no Qt, no side effects - so ``tools/selftest.py`` can check
it against the inputs the generator itself writes.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

from .parser import read_input
from .recipes import WFN_EACH_CHOICES

#: the CV sub-blocks of ``&COLVAR``, as CP2K names them
COLVAR_SECTIONS = ("DISTANCE", "ANGLE", "TORSION", "COORDINATION",
                   "COMBINE_COLVAR", "DISTANCE_FROM_PATH", "RMSD")

#: the keywords of each CV section this module reads back.  CP2K 2026.2 has more
#: CV kinds (POPULATION, GYRATION_RADIUS, CONDITIONED_DISTANCE, ...) and more
#: keywords per kind; anything outside these sets is reported in
#: ``ImportReport.unread`` rather than quietly dropped when the form regenerates.
CV_MODELLED_KEYWORDS: dict[str, frozenset[str]] = {
    "DISTANCE": frozenset({"ATOMS"}),
    "ANGLE": frozenset({"ATOMS"}),
    "TORSION": frozenset({"ATOMS"}),
    "COORDINATION": frozenset({"KINDS_FROM", "KINDS_TO", "ATOMS_FROM",
                               "ATOMS_TO", "R0", "R_0", "NN", "ND",
                               "EXPON_NUMERATOR", "EXPON_DENOMINATOR"}),
    "COMBINE_COLVAR": frozenset({"FUNCTION", "VARIABLES", "PARAMETERS", "VALUES"}),
    "DISTANCE_FROM_PATH": frozenset({"ATOMS", "ALIGN_FRAMES", "RMSD",
                                     "DISTANCES_RMSD", "SUBSET_TYPE", "LAMBDA"}),
    "RMSD": frozenset({"ATOMS", "ALIGN_FRAMES", "SUBSET_TYPE"}),
    "FRAME": frozenset({"COORD_FILE_NAME"}),
}

#: ``&METADYN`` / ``&METAVAR`` / ``&WALL`` keywords the form holds
METADYN_MODELLED_KEYWORDS = frozenset({
    "USE_PLUMED", "PLUMED_INPUT_FILE", "LAGRANGE", "LANGEVIN", "DO_HILLS",
    "NT_HILLS", "WW", "WELL_TEMPERED", "TEMPERATURE", "DELTA_T", "MIN_DISP",
    "HILL_TAIL_CUTOFF",
})
METAVAR_MODELLED_KEYWORDS = frozenset({"COLVAR", "SCALE", "MASS", "GAMMA",
                                       "LAMBDA"})
WALL_MODELLED_KEYWORDS = frozenset({"POSITION", "TYPE"})
WALL_DETAIL_KEYWORDS = frozenset({"DIRECTION", "K", "SIGMA", "WW"})
#: what the tool does not put into a ``&METADYN`` block, so a file carrying one
#: of these is reported instead of silently losing it
METADYN_UNREAD_SUBSECTIONS = ("MULTIPLE_WALKERS", "EXT_LAGRANGE_FS",
                              "EXT_LAGRANGE_SS", "EXT_LAGRANGE_SS0",
                              "EXT_LAGRANGE_VVP", "SPAWNED_HILLS_POS",
                              "SPAWNED_HILLS_HEIGHT", "SPAWNED_HILLS_SCALE",
                              "SPAWNED_HILLS_INVDT")


@dataclass(frozen=True)
class Rule:
    """One keyword the form can show: where it lives and how to read it."""

    field: str                      # Spec / widget name
    label: str                      # Chinese label, for the "jumped to" list
    path: tuple[str, ...]           # section path from the root
    keyword: str | None = None      # None = the section's own presence
    cast: str = "text"              # text | upper | int | float | bool


@dataclass
class Setting:
    field: str
    label: str
    value: Any
    source: str                     # "GLOBAL/RUN_TYPE"

    def text(self) -> str:
        if isinstance(self.value, bool):
            shown = "T" if self.value else "F"
        elif isinstance(self.value, float):
            shown = f"{self.value:g}"
        elif isinstance(self.value, (list, tuple)):
            shown = "、".join(str(v) for v in self.value)
        else:
            shown = str(self.value)
        key = self.source.rsplit("/", 1)[-1]
        # "SCF 路线 DIAGONALIZATION DIAGONALIZATION" reads badly
        return key if key.upper() == shown.upper() else f"{key} {shown}"


@dataclass
class ImportReport:
    path: Path
    run_type: str
    settings: list[Setting] = dc_field(default_factory=list)
    unread: list[str] = dc_field(default_factory=list)

    @property
    def values(self) -> dict[str, Any]:
        return {s.field: s.value for s in self.settings}

    def get(self, field: str, default: Any = None) -> Any:
        return self.values.get(field, default)

    def summary(self) -> str:
        return (f"从 {self.path.name} 读了 {len(self.settings)} 项参数"
                + (f"，没读 {len(self.unread)} 处" if self.unread else ""))


# --------------------------------------------------------------------------- #
# the table: everything the form exposes and a CP2K input can carry
# --------------------------------------------------------------------------- #
#: ``RUN_TYPE`` -> task.  ``ENERGY`` is refined below with the k-point / PDOS
#: sections, because band and PDOS are both plain ENERGY runs in CP2K.
TASK_OF_RUN_TYPE = {
    "ENERGY": "energy",
    "GEO_OPT": "geo_opt",
    "CELL_OPT": "cell_opt",
    "MD": "md",
    "VIBRATIONAL_ANALYSIS": "freq",
    "BAND": "neb",
}

#: ``PARMTYPE`` -> force-field route key.
ROUTE_OF_PARMTYPE = {
    "AMBER": "AMBER",
    "CHM": "CHARMM",
    "CHARMM": "CHARMM",
    "G96": "GROMOS96",
    "GROMOS96": "GROMOS96",
    "G87": "GROMOS87",
    "GROMOS87": "GROMOS87",
}

#: ``&VDW_POTENTIAL`` spellings -> the form's value.
DISPERSION_OF_TYPE = {
    "DFTD3": "d3",
    "DFTD3(BJ)": "d3bj",
    "DFTD3BJ": "d3bj",
    "DFTD2": "d2",
    "DFT-D3": "d3",
    "DFT-D2": "d2",
    "RVV10": "rvv10",
}

RULES: tuple[Rule, ...] = (
    # ---- GLOBAL ----
    Rule("project", "项目名", ("GLOBAL",), "PROJECT_NAME"),
    Rule("print_level", "打印级别", ("GLOBAL",), "PRINT_LEVEL", "upper"),
    Rule("walltime", "墙钟时间", ("GLOBAL",), "WALLTIME", "int"),
    Rule("seed", "随机数种子", ("GLOBAL",), "SEED", "int"),
    # ---- &DFT: system + grid ----
    Rule("charge", "体系电荷", ("FORCE_EVAL", "DFT"), "CHARGE", "int"),
    Rule("multiplicity", "自旋多重度", ("FORCE_EVAL", "DFT"), "MULTIPLICITY", "int"),
    Rule("uks", "自旋极化 UKS", ("FORCE_EVAL", "DFT"), "UKS", "bool"),
    Rule("qs_method", "GPW / GAPW", ("FORCE_EVAL", "DFT", "QS"), "METHOD", "upper"),
    Rule("eps_default", "EPS_DEFAULT", ("FORCE_EVAL", "DFT", "QS"), "EPS_DEFAULT", "float"),
    Rule("cutoff", "CUTOFF", ("FORCE_EVAL", "DFT", "MGRID"), "CUTOFF", "float"),
    Rule("rel_cutoff", "REL_CUTOFF", ("FORCE_EVAL", "DFT", "MGRID"), "REL_CUTOFF", "float"),
    Rule("ngrids", "NGRIDS", ("FORCE_EVAL", "DFT", "MGRID"), "NGRIDS", "int"),
    # ---- &DFT/&SCF ----
    Rule("scf_guess", "初始波函数", ("FORCE_EVAL", "DFT", "SCF"), "SCF_GUESS", "upper"),
    Rule("eps_scf", "EPS_SCF", ("FORCE_EVAL", "DFT", "SCF"), "EPS_SCF", "float"),
    Rule("max_scf", "MAX_SCF", ("FORCE_EVAL", "DFT", "SCF"), "MAX_SCF", "int"),
    Rule("added_mos", "空轨道 ADDED_MOS", ("FORCE_EVAL", "DFT", "SCF"), "ADDED_MOS", "int"),
    Rule("level_shift", "LEVEL_SHIFT", ("FORCE_EVAL", "DFT", "SCF"), "LEVEL_SHIFT", "float"),
    Rule("smearing", "展宽 SMEAR", ("FORCE_EVAL", "DFT", "SCF", "SMEAR")),
    Rule("smear_method", "展宽方法", ("FORCE_EVAL", "DFT", "SCF", "SMEAR"), "METHOD", "upper"),
    Rule("electronic_temperature", "电子温度",
         ("FORCE_EVAL", "DFT", "SCF", "SMEAR"), "ELECTRONIC_TEMPERATURE", "float"),
    Rule("mixing_method", "混合方法",
         ("FORCE_EVAL", "DFT", "SCF", "MIXING"), "METHOD", "upper"),
    Rule("mixing_alpha", "混合 ALPHA", ("FORCE_EVAL", "DFT", "SCF", "MIXING"), "ALPHA", "float"),
    Rule("mixing_nbuffer", "混合 NBUFFER",
         ("FORCE_EVAL", "DFT", "SCF", "MIXING"), "NBUFFER", "int"),
    Rule("mixing_beta", "混合 BETA", ("FORCE_EVAL", "DFT", "SCF", "MIXING"), "BETA", "float"),
    Rule("outer_eps_scf", "外层 EPS_SCF",
         ("FORCE_EVAL", "DFT", "SCF", "OUTER_SCF"), "EPS_SCF", "float"),
    Rule("outer_max_scf", "外层 MAX_SCF",
         ("FORCE_EVAL", "DFT", "SCF", "OUTER_SCF"), "MAX_SCF", "int"),
    Rule("outer_optimizer", "外层 OPTIMIZER",
         ("FORCE_EVAL", "DFT", "SCF", "OUTER_SCF"), "OPTIMIZER", "upper"),
    Rule("ot_minimizer", "OT MINIMIZER", ("FORCE_EVAL", "DFT", "SCF", "OT"), "MINIMIZER", "upper"),
    Rule("ot_preconditioner", "OT PRECONDITIONER",
         ("FORCE_EVAL", "DFT", "SCF", "OT"), "PRECONDITIONER", "upper"),
    Rule("ot_algorithm", "OT ALGORITHM", ("FORCE_EVAL", "DFT", "SCF", "OT"), "ALGORITHM", "upper"),
    Rule("ot_linesearch", "OT LINESEARCH",
         ("FORCE_EVAL", "DFT", "SCF", "OT"), "LINESEARCH", "upper"),
    Rule("ot_stepsize", "OT STEPSIZE", ("FORCE_EVAL", "DFT", "SCF", "OT"), "STEPSIZE", "float"),
    Rule("ot_energy_gap", "OT ENERGY_GAP",
         ("FORCE_EVAL", "DFT", "SCF", "OT"), "ENERGY_GAP", "float"),
    Rule("ot_safe_diis", "OT SAFE_DIIS", ("FORCE_EVAL", "DFT", "SCF", "OT"), "SAFE_DIIS", "bool"),
    Rule("ot_rotation", "OT ROTATION", ("FORCE_EVAL", "DFT", "SCF", "OT"), "ROTATION", "bool"),
    Rule("diag_algorithm", "对角化 ALGORITHM",
         ("FORCE_EVAL", "DFT", "SCF", "DIAGONALIZATION"), "ALGORITHM", "upper"),
    # ---- &DFT/&PRINT ----
    Rule("print_forces", "打印受力", ("FORCE_EVAL", "DFT", "PRINT", "FORCES")),
    Rule("print_mulliken", "Mulliken 电荷", ("FORCE_EVAL", "DFT", "PRINT", "MULLIKEN")),
    Rule("print_dipole", "偶极矩", ("FORCE_EVAL", "DFT", "PRINT", "MOMENTS")),
    Rule("print_molden", "写 Molden 文件", ("FORCE_EVAL", "DFT", "PRINT", "MO_MOLDEN")),
    # ---- cube output ----
    Rule("print_mo_cubes", "写轨道立方体", ("FORCE_EVAL", "DFT", "PRINT", "MO_CUBES")),
    Rule("mo_cubes_nhomo", "MO_CUBES NHOMO",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_CUBES"), "NHOMO", "int"),
    Rule("mo_cubes_nlumo", "MO_CUBES NLUMO",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_CUBES"), "NLUMO", "int"),
    Rule("mo_cubes_stride", "MO_CUBES STRIDE",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_CUBES"), "STRIDE", "vector"),
    Rule("print_density_cube", "写密度立方体",
         ("FORCE_EVAL", "DFT", "PRINT", "TOT_DENSITY_CUBE")),
    Rule("density_cube_stride", "密度 STRIDE",
         ("FORCE_EVAL", "DFT", "PRINT", "TOT_DENSITY_CUBE"), "STRIDE", "vector"),
    Rule("molden_file", "Molden 文件名",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_MOLDEN"), "FILENAME"),
    Rule("molden_nlumo", "Molden NLUMO",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_MOLDEN"), "NLUMO", "int"),
    Rule("molden_unit", "Molden 单位",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_MOLDEN"), "UNIT", "upper"),
    Rule("molden_gto_kind", "Molden GTO_KIND",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_MOLDEN"), "GTO_KIND", "upper"),
    Rule("molden_write_pseudo", "Molden [Pseudo]",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_MOLDEN"), "WRITE_PSEUDO", "bool"),
    Rule("molden_write_cell", "Molden [Cell]",
         ("FORCE_EVAL", "DFT", "PRINT", "MO_MOLDEN"), "WRITE_CELL", "bool"),
    # ---- the wave-function restart file ----
    Rule("wfn_print", "写 .wfn", ("FORCE_EVAL", "DFT", "SCF", "PRINT", "RESTART")),
    # The interval is counted at *one* of CP2K's iteration levels, and which one
    # changes what it means - QS_SCF counts the SCF iterations inside a single
    # force evaluation, MD/GEO_OPT/CELL_OPT the steps of the run.  All four are
    # mapped to the same field (a file normally uses one of them) and _derive()
    # records the level that is actually there.
    Rule("wfn_restart_every", "写 .wfn 的间隔（按 SCF 步）",
         ("FORCE_EVAL", "DFT", "SCF", "PRINT", "RESTART", "EACH"), "QS_SCF", "int"),
    Rule("wfn_restart_every", "写 .wfn 的间隔（按 MD 步）",
         ("FORCE_EVAL", "DFT", "SCF", "PRINT", "RESTART", "EACH"), "MD", "int"),
    Rule("wfn_restart_every", "写 .wfn 的间隔（按几何优化步）",
         ("FORCE_EVAL", "DFT", "SCF", "PRINT", "RESTART", "EACH"), "GEO_OPT", "int"),
    Rule("wfn_restart_every", "写 .wfn 的间隔（按晶胞优化步）",
         ("FORCE_EVAL", "DFT", "SCF", "PRINT", "RESTART", "EACH"), "CELL_OPT", "int"),
    Rule("wfn_restart_add_last", "写 .wfn 的最后一步",
         ("FORCE_EVAL", "DFT", "SCF", "PRINT", "RESTART"), "ADD_LAST", "add_last"),
    Rule("wfn_backup_copies", "写 .wfn 的备份数",
         ("FORCE_EVAL", "DFT", "SCF", "PRINT", "RESTART"), "BACKUP_COPIES", "int"),
    # ---- the run restart file (what &EXT_RESTART reads back) ----
    # Its interval is counted at the task's own level (MD for MD, GEO_OPT/CELL_OPT
    # for the optimisers), so all three have to be read - only looking at MD
    # silently dropped the interval when an optimised input was loaded back.
    Rule("motion_restart_print", "写 -1.restart", ("MOTION", "PRINT", "RESTART")),
    Rule("restart_every", "写 -1.restart 的间隔（按 MD 步）",
         ("MOTION", "PRINT", "RESTART", "EACH"), "MD", "int"),
    Rule("restart_every", "写 -1.restart 的间隔（按几何优化步）",
         ("MOTION", "PRINT", "RESTART", "EACH"), "GEO_OPT", "int"),
    Rule("restart_every", "写 -1.restart 的间隔（按晶胞优化步）",
         ("MOTION", "PRINT", "RESTART", "EACH"), "CELL_OPT", "int"),
    Rule("restart_add_last", "写 -1.restart 的最后一步",
         ("MOTION", "PRINT", "RESTART"), "ADD_LAST", "add_last"),
    Rule("restart_backups", "写 -1.restart 的备份数",
         ("MOTION", "PRINT", "RESTART"), "BACKUP_COPIES", "int"),
    Rule("trajectory_stride", "轨迹间隔（按 MD 步）",
         ("MOTION", "PRINT", "TRAJECTORY", "EACH"), "MD", "int"),
    Rule("trajectory_stride", "轨迹间隔（按几何优化步）",
         ("MOTION", "PRINT", "TRAJECTORY", "EACH"), "GEO_OPT", "int"),
    Rule("trajectory_stride", "轨迹间隔（按晶胞优化步）",
         ("MOTION", "PRINT", "TRAJECTORY", "EACH"), "CELL_OPT", "int"),
    # ---- what an MD run prints while it runs ------------------------------ #
    # `md_temp_kind_print` is not a single keyword (the `&MD TEMP_KIND` keyword
    # switches the computation on, `&MD/&PRINT/&TEMP_KIND` writes the file), so
    # `_derive` sets it from either one.
    Rule("md_temp_kind_every", "每个 kind 温度的间隔",
         ("MOTION", "MD", "PRINT", "TEMP_KIND", "EACH"), "MD", "int"),
    Rule("md_energy_print", "写 -1.ener", ("MOTION", "MD", "PRINT", "ENERGY")),
    Rule("md_energy_every", "能量文件间隔",
         ("MOTION", "MD", "PRINT", "ENERGY", "EACH"), "MD", "int"),
    Rule("md_energy_file", "能量文件名",
         ("MOTION", "MD", "PRINT", "ENERGY"), "FILENAME"),
    Rule("thermostat_region", "热浴区域",
         ("MOTION", "MD", "THERMOSTAT"), "REGION", "upper"),
    Rule("thermostat_temp_print", "写热浴区域温度",
         ("MOTION", "MD", "THERMOSTAT", "PRINT", "TEMPERATURE")),
    Rule("thermostat_temp_every", "热浴温度间隔",
         ("MOTION", "MD", "THERMOSTAT", "PRINT", "TEMPERATURE", "EACH"),
         "MD", "int"),
    # ---- &EXT_RESTART ----
    # ``RESTART_FILE_NAME`` is read in _derive: CP2K's own restart files write it
    # *empty*, and then there is nothing for this run to continue from
    Rule("restart_default", "续算以文件为准",
         ("EXT_RESTART",), "RESTART_DEFAULT", "bool"),
    # ---- &MOTION: MD ----
    Rule("ensemble", "系综", ("MOTION", "MD"), "ENSEMBLE", "upper"),
    Rule("steps", "步数", ("MOTION", "MD"), "STEPS", "int"),
    Rule("timestep", "时间步长", ("MOTION", "MD"), "TIMESTEP", "float"),
    Rule("temperature", "温度", ("MOTION", "MD"), "TEMPERATURE", "float"),
    Rule("seed", "随机数种子", ("MOTION", "MD"), "SEED", "int"),
    Rule("thermostat", "恒温器", ("MOTION", "MD", "THERMOSTAT"), "TYPE", "upper"),
    Rule("pressure", "NPT 压力", ("MOTION", "MD", "BAROSTAT"), "PRESSURE", "float"),
    Rule("barostat_timecon", "压浴 TIMECON",
         ("MOTION", "MD", "BAROSTAT"), "TIMECON", "float"),
    Rule("mm_stress_tensor", "应力张量", ("FORCE_EVAL",), "STRESS_TENSOR", "upper"),
    # ---- &MOTION: GEO_OPT / CELL_OPT ----
    Rule("optimizer", "优化器", ("MOTION", "GEO_OPT"), "OPTIMIZER", "upper"),
    Rule("max_iter", "最大迭代", ("MOTION", "GEO_OPT"), "MAX_ITER", "int"),
    Rule("keep_angles", "保持晶胞夹角", ("MOTION", "CELL_OPT"), "KEEP_ANGLES", "bool"),
    Rule("external_pressure", "外压（CELL_OPT）",
         ("MOTION", "CELL_OPT"), "EXTERNAL_PRESSURE", "float"),
    # ---- &VIBRATIONAL_ANALYSIS ----
    Rule("intensities", "红外强度", ("VIBRATIONAL_ANALYSIS",), "INTENSITIES", "bool"),
    Rule("thermochemistry", "热化学", ("VIBRATIONAL_ANALYSIS",), "THERMOCHEMISTRY", "bool"),
    Rule("molden_vib", "写振动模式 Molden",
         ("VIBRATIONAL_ANALYSIS", "PRINT", "MOLDEN_VIB")),
    # ---- METHOD FIST ----
    Rule("mm_parm_file", "力场参数文件",
         ("FORCE_EVAL", "MM", "FORCEFIELD"), "PARM_FILE_NAME"),
    Rule("mm_vdw_scale14", "1-4 范德华缩放",
         ("FORCE_EVAL", "MM", "FORCEFIELD"), "VDW_SCALE14", "float"),
    Rule("mm_ei_scale14", "1-4 静电缩放",
         ("FORCE_EVAL", "MM", "FORCEFIELD"), "EI_SCALE14", "float"),
    Rule("mm_shift_cutoff", "SHIFT_CUTOFF",
         ("FORCE_EVAL", "MM", "FORCEFIELD"), "SHIFT_CUTOFF", "bool"),
    Rule("mm_multiple_potential", "MULTIPLE_POTENTIAL",
         ("FORCE_EVAL", "MM", "FORCEFIELD"), "MULTIPLE_POTENTIAL", "bool"),
    Rule("mm_ignore_missing_params", "缺参数也继续",
         ("FORCE_EVAL", "MM", "FORCEFIELD"), "IGNORE_MISSING_CRITICAL_PARAMS", "bool"),
    Rule("mm_ewald", "Ewald 类型",
         ("FORCE_EVAL", "MM", "POISSON", "EWALD"), "EWALD_TYPE", "upper"),
    Rule("mm_ewald_gmax", "Ewald GMAX",
         ("FORCE_EVAL", "MM", "POISSON", "EWALD"), "GMAX", "int"),
    Rule("mm_ewald_alpha", "Ewald ALPHA",
         ("FORCE_EVAL", "MM", "POISSON", "EWALD"), "ALPHA", "float"),
    Rule("mm_o_spline", "Ewald O_SPLINE",
         ("FORCE_EVAL", "MM", "POISSON", "EWALD"), "O_SPLINE", "int"),
    Rule("mm_rcut", "Ewald RCUT",
         ("FORCE_EVAL", "MM", "POISSON", "EWALD"), "RCUT", "float"),
    Rule("mm_verlet_skin", "Verlet SKIN",
         ("FORCE_EVAL", "MM", "NEIGHBOR_LISTS"), "VERLET_SKIN", "float"),
    Rule("mm_conn_file", "拓扑文件",
         ("FORCE_EVAL", "SUBSYS", "TOPOLOGY"), "CONN_FILE_NAME"),
    Rule("mm_coord_file", "坐标文件",
         ("FORCE_EVAL", "SUBSYS", "TOPOLOGY"), "COORD_FILE_NAME"),
    Rule("mm_coord_format", "坐标格式",
         ("FORCE_EVAL", "SUBSYS", "TOPOLOGY"), "COORDINATE", "upper"),
    # ---- &KPOINTS ----
    Rule("kpoint_gamma_centered", "GAMMA_CENTERED",
         ("FORCE_EVAL", "DFT", "KPOINTS"), "GAMMA_CENTERED", "bool"),
    Rule("kpoint_full_grid", "FULL_GRID",
         ("FORCE_EVAL", "DFT", "KPOINTS"), "FULL_GRID", "bool"),
    Rule("kpoint_symmetry", "SYMMETRY",
         ("FORCE_EVAL", "DFT", "KPOINTS"), "SYMMETRY", "bool"),
    Rule("kpoint_wavefunctions", "WAVEFUNCTIONS",
         ("FORCE_EVAL", "DFT", "KPOINTS"), "WAVEFUNCTIONS", "upper"),
    Rule("kpoint_verbose", "VERBOSE",
         ("FORCE_EVAL", "DFT", "KPOINTS"), "VERBOSE", "bool"),
    Rule("kpoint_units", "UNITS", ("FORCE_EVAL", "DFT", "KPOINTS"), "UNITS", "upper"),
    # ---- &MOTION/&CONSTRAINT ----
    Rule("fixed_atoms", "固定原子",
         ("MOTION", "CONSTRAINT", "FIXED_ATOMS"), "LIST"),
    Rule("fixed_atoms_components", "固定方向",
         ("MOTION", "CONSTRAINT", "FIXED_ATOMS"), "COMPONENTS_TO_FIX", "upper"),
)

#: Top-level sections the importer knows about; anything else is reported.
KNOWN_TOP = {
    "GLOBAL", "FORCE_EVAL", "MOTION", "EXT_RESTART", "VIBRATIONAL_ANALYSIS",
    "PARTICLE", "SUBSYS", "TEST", "ATOM", "FARMING", "MULTIPLE_FORCE_EVALS",
}


def _bool(value: Any) -> bool:
    """CP2K writes ``T``/``F`` (and sections ``ON``/``OFF``)."""
    if isinstance(value, bool):
        return value
    token = str(value).strip().lstrip(".").rstrip(".").upper()
    return token in ("T", "TRUE", "ON", "YES", "1")


def _word(value: Any) -> str:
    """Enum-ish values: the parser turns ``ON``/``OFF``/``T``/``F`` into bools."""
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    return str(value).strip().upper()


def _num(value: Any, cast: str) -> Any:
    try:
        return int(float(str(value).split()[0])) if cast == "int" \
            else float(str(value).split()[0])
    except (TypeError, ValueError):
        return None


def _cast(value: Any, cast: str) -> Any:
    if cast == "bool":
        return _bool(value)
    if cast == "add_last":
        # ADD_LAST is an enum: NUMERIC/SYMBOLIC mean "yes, also the last step"
        return _word(value) not in ("NO", "F", "FALSE", "OFF")
    if isinstance(value, bool):
        # A keyword CP2K's own restart writer emitted **without a value** (an empty
        # ``RESTART_FILE_NAME``, for instance) parses as a flag.  Every non-boolean
        # setting has to treat that as "not set" - otherwise the field was filled
        # with the literal text "True" and the new input pointed `&EXT_RESTART` at a
        # file called True.
        return None
    if cast in ("int", "float"):
        return _num(value, cast)
    if cast == "vector":
        # "2 2 2" (STRIDE) - keep it as the text the form shows
        return " ".join(str(v) for v in (
            value if isinstance(value, list) else str(value).split()))
    if cast == "upper":
        return _word(value)
    return str(value).strip()


def _walk(sections: list, path: tuple[str, ...]):
    """Find ``path`` under ``sections``; returns the Section or None."""
    current = None
    level = sections
    for name in path:
        upper = name.upper()
        current = next((s for s in level if s.name.upper() == upper), None)
        if current is None:
            return None
        level = current.sections
    return current


def _setting(rule: Rule, value: Any) -> Setting:
    return Setting(rule.field, rule.label, value,
                   "/".join(rule.path) + (f"/{rule.keyword}" if rule.keyword else ""))


def _collect_simple(sections: list, report: ImportReport) -> None:
    for rule in RULES:
        block = _walk(sections, rule.path)
        if block is None:
            continue
        if rule.field == "restart_default":
            # "以文件为准" only means something once a file is named; CP2K's own
            # restarts carry the block with an empty RESTART_FILE_NAME, and a ticked
            # box there would make 续算 look switched on (see _derive)
            raw = block.get("RESTART_FILE_NAME")
            if isinstance(raw, bool) or not str(raw or "").strip():
                continue
        if rule.keyword is None:
            # the section itself is the setting; ``&MO_MOLDEN OFF`` means off
            parameter = getattr(block, "parameter", None)
            report.settings.append(_setting(
                rule, _bool(parameter) if isinstance(parameter, bool) else True))
            continue
        raw = block.get(rule.keyword)
        if raw is None:
            continue
        value = _cast(raw, rule.cast)
        if value is None:
            continue
        report.settings.append(_setting(rule, value))


def _add(report: ImportReport, field: str, label: str, value: Any,
         source: str) -> None:
    report.settings.append(Setting(field, label, value, source))


def _derive(sections: list, report: ImportReport) -> None:
    """Settings that are not a single keyword: task, method, route, cell, ..."""
    global_block = _walk(sections, ("GLOBAL",))
    run_type = ""
    if global_block is not None:
        run_type = str(global_block.get("RUN_TYPE", "")).strip().upper()
        report.run_type = run_type
    # &SUBSYS/&CELL: the simulation box.  A force-field input whose atoms come from
    # COORD_FILE_NAME still carries one here, and it is the only place the box
    # exists (``cell_methods.F``: CELL_FILE_NAME > ABC > A/B/C > COORD_FILE_NAME -
    # so a box inside the coordinate file loses against this one).
    cell_block = _walk(sections, ("FORCE_EVAL", "SUBSYS", "CELL"))
    if cell_block is not None:
        abc = _float_list(cell_block.get("ABC"))
        angles = _float_list(cell_block.get("ALPHA_BETA_GAMMA"))
        if len(abc) >= 3:
            text = ",".join(f"{value:g}" for value in abc[:3])
            if len(angles) >= 3:
                text += "," + ",".join(f"{value:g}" for value in angles[:3])
            _add(report, "cell", "晶胞（&CELL）", text, "FORCE_EVAL/SUBSYS/CELL/ABC")
        else:
            vectors = [_float_list(cell_block.get(name)) for name in ("A", "B", "C")]
            if all(len(vector) >= 3 for vector in vectors):
                # A/B/C are vectors, the form takes lengths (+ angles when the cell
                # is not orthogonal)
                from .structures import Cell

                cell = Cell(tuple(vectors[0][:3]), tuple(vectors[1][:3]),
                            tuple(vectors[2][:3]))          # type: ignore[arg-type]
                lengths = cell.lengths()
                angles = cell.angles()
                text = ",".join(f"{value:g}" for value in lengths)
                if max(abs(angle - 90.0) for angle in angles) > 0.01:
                    text += "," + ",".join(f"{value:g}" for value in angles)
                _add(report, "cell", "晶胞（&CELL 的 A/B/C 矢量）", text,
                     "FORCE_EVAL/SUBSYS/CELL/A")
        periodic = cell_block.get("PERIODIC")
        if periodic is not None:
            _add(report, "periodic", "周期性", _word(periodic),
                 "FORCE_EVAL/SUBSYS/CELL/PERIODIC")
    # Which &EACH level the wave-function restart interval counts.  The level is
    # the *keyword name* inside &EACH, so no Rule can read it; when the file lists
    # several, the one the run type actually iterates wins.
    each_block = _walk(sections, ("FORCE_EVAL", "DFT", "SCF", "PRINT", "RESTART",
                                 "EACH"))
    if each_block is not None:
        present = [level for level in WFN_EACH_CHOICES
                   if each_block.get(level) is not None]
        if present:
            wanted = {"MD": "MD", "GEO_OPT": "GEO_OPT",
                      "CELL_OPT": "CELL_OPT"}.get(run_type)
            level = wanted if wanted in present else present[0]
            # "自动" (no setting) already reproduces the level the task implies,
            # so only a *deliberate* different level is reported - otherwise every
            # file listing `&EACH MD` would pin the GUI's combo to MD for good.
            implied = {"MD": "MD", "GEO_OPT": "GEO_OPT",
                       "CELL_OPT": "CELL_OPT"}.get(run_type, "QS_SCF")
            if level != implied:
                _add(report, "wfn_restart_level",
                     "写 .wfn 按哪一级计数（与任务不一致）", level,
                     "FORCE_EVAL/DFT/SCF/PRINT/RESTART/EACH")
    force_eval = _walk(sections, ("FORCE_EVAL",))
    method = ""
    if force_eval is not None:
        method = str(force_eval.get("METHOD", "")).strip().upper()
    # METHOD FIST: report it as FIST so the form switches pages, and remember
    # which route the force field uses
    if method:
        _add(report, "method", "计算方法", method, "FORCE_EVAL/METHOD")
    task = TASK_OF_RUN_TYPE.get(run_type, "")
    if run_type == "ENERGY":
        # band and PDOS are both ENERGY runs; their print keys tell them apart.
        # &KPOINTS alone does *not*: an ordinary energy run can ask for a grid.
        printing = _walk(sections, ("FORCE_EVAL", "DFT", "PRINT"))
        if printing is not None and (
                printing.subsection("PDOS") is not None
                or printing.subsection("DOS") is not None):
            task = "pdos"
        elif printing is not None and printing.subsection("BAND_STRUCTURE") is not None:
            task = "band"
        else:
            task = "energy"
    if task:
        _add(report, "task", "任务", task, "GLOBAL/RUN_TYPE")
    # the force-field route, from PARMTYPE
    forcefield = _walk(sections, ("FORCE_EVAL", "MM", "FORCEFIELD"))
    if forcefield is not None:
        parmtype = _word(forcefield.get("PARMTYPE", ""))
        route = ROUTE_OF_PARMTYPE.get(parmtype)
        if route is None and parmtype in ("OFF", "ON", ""):
            # IN_INPUT and PAIR both write PARMTYPE OFF; the terms tell them apart
            terms = {s.name.upper() for s in forcefield.sections}
            route = "PAIR" if terms <= {"CHARGE", "NONBONDED"} else "IN_INPUT"
        if route:
            _add(report, "mm_route", "力场路线", route,
                 "FORCE_EVAL/MM/FORCEFIELD/PARMTYPE")
        # &CHARGE / &LENNARD-JONES are tables in the form
        charges = [[str(b.get("ATOM", "")), _num(b.get("CHARGE"), "float")]
                   for b in forcefield.subsections("CHARGE")]
        charges = [row for row in charges if row[1] is not None]
        if charges:
            _add(report, "mm_charges", "元素电荷", charges, "MM/FORCEFIELD/CHARGE")
        nonbonded = forcefield.subsection("NONBONDED")
        if nonbonded is not None:
            rows = []
            for block in nonbonded.subsections("LENNARD-JONES"):
                atoms = block.get("ATOMS")
                if isinstance(atoms, list):
                    pair = [str(a) for a in atoms]
                else:
                    pair = str(atoms or "").split()
                rows.append([pair[0] if pair else "", pair[1] if len(pair) > 1 else "",
                             _num(block.get("EPSILON"), "float"),
                             _num(block.get("SIGMA"), "float"),
                             _num(block.get("RCUT"), "float")])
            if rows:
                _add(report, "mm_lj", "ε/σ 对", rows,
                     "MM/FORCEFIELD/NONBONDED/LENNARD-JONES")
    # the SCF route: which of &OT / &DIAGONALIZATION is actually there
    scf = _walk(sections, ("FORCE_EVAL", "DFT", "SCF"))
    if scf is not None:
        if scf.subsection("OT") is not None:
            _add(report, "scf", "SCF 路线", "OT", "DFT/SCF/OT")
        elif scf.subsection("DIAGONALIZATION") is not None:
            _add(report, "scf", "SCF 路线", "DIAGONALIZATION",
                 "DFT/SCF/DIAGONALIZATION")
    # the functional is either the section's own parameter (``&XC_FUNCTIONAL BLYP``,
    # what this tool writes) or a subsection (``&XC_FUNCTIONAL`` + ``&PBE``)
    xc = _walk(sections, ("FORCE_EVAL", "DFT", "XC"))
    if xc is not None:
        functional = xc.subsection("XC_FUNCTIONAL")
        if functional is not None:
            name = ""
            if isinstance(functional.parameter, str):
                name = functional.parameter.strip().upper()
            if not name and functional.sections:
                name = functional.sections[0].name.upper()
            if name:
                _add(report, "functional", "泛函", name, "DFT/XC/XC_FUNCTIONAL")
        vdw = xc.subsection("VDW_POTENTIAL")
        if vdw is not None:
            pair = vdw.subsection("PAIR_POTENTIAL")
            kind = ""
            if pair is not None:
                kind = str(pair.get("TYPE", "")).strip().upper()
            if not kind:
                kind = str(vdw.get("POTENTIAL_TYPE", "")).strip().upper()
            dispersion = DISPERSION_OF_TYPE.get(kind)
            if dispersion:
                _add(report, "dispersion", "色散校正", dispersion,
                     "DFT/XC/VDW_POTENTIAL")
    # &MOL_SET: one component per &MOLECULE
    topology = _walk(sections, ("FORCE_EVAL", "SUBSYS", "TOPOLOGY"))
    if topology is not None:
        raw = topology.get("CONNECTIVITY", topology.get("CONN_FILE_FORMAT"))
        connectivity = _word(raw) if raw is not None else ""
        if connectivity:
            _add(report, "mm_connectivity", "连接性来源", connectivity,
                 "SUBSYS/TOPOLOGY/CONNECTIVITY")
        mol_set = topology.subsection("MOL_SET")
        if mol_set is not None:
            rows = []
            for block in mol_set.subsections("MOLECULE"):
                rows.append([str(block.get("CONN_FILE_NAME", "")),
                             _word(block.get("CONNECTIVITY",
                                             block.get("CONN_FILE_FORMAT", ""))),
                             block.get("NMOL")])
            if rows:
                _add(report, "mm_molecules", "组分表", rows,
                     "SUBSYS/TOPOLOGY/MOL_SET/MOLECULE")
    # &EXT_RESTART: which file to continue from, and which quantities to take.
    # CP2K's own restart files carry this block with an **empty** RESTART_FILE_NAME
    # (it is the run's own history: "that is where I started"), so a take-list from
    # here would make the form look like it continues a run while there is nothing
    # to continue - and the old code filled the field with the literal text "True".
    ext = _walk(sections, ("EXT_RESTART",))
    if ext is not None:
        raw = ext.get("RESTART_FILE_NAME")
        name = "" if isinstance(raw, bool) or raw is None else str(raw).strip()
        if not name:
            if ext.keywords or ext.sections:
                report.unread.append(
                    "&EXT_RESTART（RESTART_FILE_NAME 是空的：CP2K 的 restart 就是"
                    "这么写的，续算相关的设置没有读——要续算就自己填那一格）")
        else:
            _add(report, "ext_restart_file", "从上次续算", name,
                 "EXT_RESTART/RESTART_FILE_NAME")
            take = []
            # declaration order (POS, VEL, CELL, ...), which is also the order the
            # form's presets use - so a round-trip lands on a preset exactly
            for key, (label, _why) in _takes().items():
                flag = ext.get(f"RESTART_{label}")
                if flag is not None and _bool(flag):
                    take.append(key)
            if take:
                _add(report, "ext_restart_take", "取哪些量", tuple(take),
                     "EXT_RESTART/RESTART_*")
    # &KPOINTS: the scheme keeps its numbers, and the explicit points are the
    # KPOINT lines (SCHEME GENERAL); the form rebuilds its own combo from these
    kpoints = _walk(sections, ("FORCE_EVAL", "DFT", "KPOINTS"))
    if kpoints is not None:
        raw = kpoints.get("SCHEME")
        tokens = raw if isinstance(raw, list) else str(raw or "").split()
        if tokens:
            _add(report, "kpoint_scheme", "k 点方案",
                 " ".join(str(t) for t in tokens), "DFT/KPOINTS/SCHEME")
        points = []
        for keyword in kpoints.keywords_of("KPOINT"):
            numbers = [v for v in keyword.values[:4]]
            while len(numbers) < 4:
                numbers.append(1.0)
            points.append([float(v) for v in numbers])
        if points:
            _add(report, "kpoint_points", "显式 k 点", points, "DFT/KPOINTS/KPOINT")
    # &QS EXTRAPOLATION is a plain keyword; the rules table does not cover it
    # because the form's combo has an "automatic" entry that no keyword means
    qs = _walk(sections, ("FORCE_EVAL", "DFT", "QS"))
    if qs is not None and qs.get("EXTRAPOLATION") is not None:
        _add(report, "extrapolation", "外推", _word(qs.get("EXTRAPOLATION")),
             "DFT/QS/EXTRAPOLATION")
    # the thermostat's TIMECON sits in a subsection named after the thermostat
    # (&THERMOSTAT/&NOSE, &THERMOSTAT/&CSVR, ...), so one rule per kind would be
    # needed - a loop is simpler and tells the form which one it found
    for kind in ("NOSE", "CSVR", "GLE", "AD_LANGEVIN"):
        block = _walk(sections, ("MOTION", "MD", "THERMOSTAT", kind))
        if block is None or block.get("TIMECON") is None:
            continue
        value = _num(block.get("TIMECON"), "float")
        if value is not None:
            _add(report, "thermostat_timecon", "恒温器 TIMECON", value,
                 f"MOTION/MD/THERMOSTAT/{kind}/TIMECON")
        break
    # deuterium: the form's "H -> D" switch *is* `&KIND H / MASS 2.0141`, so it is
    # read from there.  A mass that is not deuterium's (the "heavy hydrogen"
    # numerical trick, e.g. MASS 2 or 3) is deliberately *not* claimed: the form
    # has no field for an arbitrary mass, and pretending it were deuterium would
    # silently rewrite the value
    subsys = _walk(sections, ("FORCE_EVAL", "SUBSYS"))
    if subsys is not None:
        from .recommend import DEUTERIUM_MASS

        for kind in subsys.subsections("KIND"):
            name = str(kind.parameter or "").strip().upper()
            element = str(kind.get("ELEMENT", name) or name).strip().upper()
            if element != "H":
                continue
            mass = _num(kind.get("MASS"), "float")
            if mass is not None and abs(mass - DEUTERIUM_MASS) < 1e-3:
                _add(report, "h_to_d", "氘代（H→D）", True,
                     "FORCE_EVAL/SUBSYS/KIND/MASS")
            break
    # per-kind temperature: the *keyword* ``&MD TEMP_KIND`` switches the
    # computation on and ``&MD/&PRINT/&TEMP_KIND`` writes the file - CP2K only
    # warns when the print key is on without the keyword (md_energies.F), so the
    # form carries one switch and either of them sets it
    md = _walk(sections, ("MOTION", "MD"))
    if md is not None:
        keyword_on = _bool(md.get("TEMP_KIND", False))
        printer = _walk(sections, ("MOTION", "MD", "PRINT", "TEMP_KIND"))
        printing = printer is not None and (
            printer.parameter is None or _bool(printer.parameter))
        if keyword_on or printing:
            _add(report, "md_temp_kind_print", "每个原子种类的温度", True,
                 "MOTION/MD/TEMP_KIND")
    _read_metadyn(sections, report)


# --------------------------------------------------------------------------- #
# metadynamics: &SUBSYS/&COLVAR and &MOTION/&FREE_ENERGY/&METADYN
# --------------------------------------------------------------------------- #
def _atom_list(value: Any) -> tuple[int, ...]:
    """``ATOMS 1 5 8`` -> ``(1, 5, 8)`` (the parser may hand back either shape)."""
    if value is None:
        return ()
    tokens = value if isinstance(value, list) else str(value).split()
    out: list[int] = []
    for token in tokens:
        number = _num(token, "int")
        if number is not None:
            out.append(number)
    return tuple(out)


def _float_list(value: Any) -> tuple[float, ...]:
    if value is None:
        return ()
    tokens = value if isinstance(value, list) else str(value).split()
    out: list[float] = []
    for token in tokens:
        number = _num(token, "float")
        if number is not None:
            out.append(number)
    return tuple(out)


def _text_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    tokens = value if isinstance(value, list) else str(value).split()
    return tuple(str(token).strip() for token in tokens if str(token).strip())


def _read_cv(section) -> Any:
    """One CV sub-block -> :class:`~cp2kbuild.metadyn.Cv` (None when unknown)."""
    from .metadyn import Cv

    name = section.name.upper()
    if name in ("DISTANCE", "ANGLE", "TORSION"):
        return Cv(kind=name.lower(), atoms=_atom_list(section.get("ATOMS")))
    if name == "COORDINATION":
        # R_0 / EXPON_NUMERATOR / EXPON_DENOMINATOR are CP2K's own aliases for
        # R0 / NN / ND (manual: "aliases"), and the tool writes the canonical name
        def alias(*names):
            for candidate in names:
                value = section.get(candidate)
                if value is not None:
                    return value
            return None
        return Cv(kind="coordination",
                  kinds_from=str(section.get("KINDS_FROM", "") or "").strip(),
                  kinds_to=str(section.get("KINDS_TO", "") or "").strip(),
                  atoms_from=_atom_list(section.get("ATOMS_FROM")),
                  atoms_to=_atom_list(section.get("ATOMS_TO")),
                  r0=_num(alias("R0", "R_0"), "float"),
                  nn=_num(alias("NN", "EXPON_NUMERATOR"), "int"),
                  nd=_num(alias("ND", "EXPON_DENOMINATOR"), "int"))
    if name == "COMBINE_COLVAR":
        children: list[Any] = []
        for colvar in section.subsections("COLVAR"):
            children.extend(_read_cv_block(colvar))
        return Cv(kind="combine",
                  function=str(section.get("FUNCTION", "") or "").strip(),
                  variables=_text_list(section.get("VARIABLES")),
                  parameters=_text_list(section.get("PARAMETERS")),
                  values=_float_list(section.get("VALUES")),
                  children=tuple(children))
    if name == "DISTANCE_FROM_PATH":
        frames: list[str] = []
        for frame in section.subsections("FRAME"):
            path = frame.get("COORD_FILE_NAME")
            if path is not None:
                frames.append(str(path).strip())
        return Cv(kind="path",
                  atoms=_atom_list(section.get("ATOMS")),
                  frames=tuple(frames),
                  rmsd=_bool(section.get("RMSD", False)),
                  distances_rmsd=_bool(section.get("DISTANCES_RMSD", False)),
                  align_frames=_bool(section.get("ALIGN_FRAMES", False)),
                  lam=_num(section.get("LAMBDA"), "float"),
                  subset_type=str(section.get("SUBSET_TYPE", "") or "").strip().upper())
    if name == "RMSD":
        frames = []
        for frame in section.subsections("FRAME"):
            path = frame.get("COORD_FILE_NAME")
            if path is not None:
                frames.append(str(path).strip())
        return Cv(kind="rmsd",
                  atoms=_atom_list(section.get("ATOMS")),
                  frames=tuple(frames),
                  align_frames=_bool(section.get("ALIGN_FRAMES", False)),
                  subset_type=str(section.get("SUBSET_TYPE", "") or "").strip().upper())
    return None


def _read_cv_block(colvar) -> list:
    """The CVs of one ``&COLVAR`` block, in the order they appear in the file.

    CP2K's ``&METAVAR COLVAR n`` counts the CVs in that flat order, and the tool
    writes exactly one CV per block, so a tool-generated file round-trips.
    """
    out = []
    for section in colvar.sections:
        if section.name.upper() not in COLVAR_SECTIONS:
            continue
        cv = _read_cv(section)
        if cv is not None:
            out.append(cv)
    return out


def _read_wall(block) -> Any:
    from .metadyn import Wall

    wall_block = block.subsection("WALL")
    if wall_block is None:
        return None
    wall_type = _word(wall_block.get("TYPE", "QUADRATIC")) or "QUADRATIC"
    if wall_type in ("", "NONE"):
        return None
    detail = wall_block.subsection(wall_type)
    return Wall(
        position=_num(wall_block.get("POSITION"), "float"),
        type=wall_type,
        direction=(_word(detail.get("DIRECTION", "WALL_PLUS"))
                   if detail is not None else "WALL_PLUS"),
        k=_num(detail.get("K"), "float") if detail is not None else None,
        sigma=_num(detail.get("SIGMA"), "float") if detail is not None else None,
        ww=_num(detail.get("WW"), "float") if detail is not None else None,
    )


def _read_metavar(block) -> Any:
    from .metadyn import Metavar

    return Metavar(colvar=_num(block.get("COLVAR"), "int") or 1,
                   scale=_num(block.get("SCALE"), "float"),
                   mass=_num(block.get("MASS"), "float"),
                   gamma=_num(block.get("GAMMA"), "float"),
                   lam=_num(block.get("LAMBDA"), "float"),
                   wall=_read_wall(block))


def _unread_metadyn(block, subsys, report: ImportReport) -> None:
    """Name the metadynamics settings the form has no field for.

    Reading a file and generating again must not lose a setting silently: CP2K's
    ``&METADYN`` has keywords the tool does not write (``TAMCSTEPS``,
    ``SLOW_GROWTH``, the ``SPAWNED_HILLS_*`` restart values, ...), a ``&COLVAR``
    block can hold CV kinds the tool does not model, and a CV kind can carry an
    extra keyword (``&COORDINATION KINDS_TO_B``).  All of them are listed here.
    """
    def note(keywords, modelled, prefix: str) -> None:
        for keyword in keywords:
            if keyword.name.upper() not in modelled:
                report.unread.append(f"{prefix}/{keyword.name.upper()}")

    if subsys is not None:
        for colvar in subsys.subsections("COLVAR"):
            for section in colvar.sections:
                prefix = f"&FORCE_EVAL/&SUBSYS/&COLVAR/&{section.name.upper()}"
                modelled = CV_MODELLED_KEYWORDS.get(section.name.upper())
                if modelled is None:
                    report.unread.append(prefix)      # a CV kind the tool cannot write
                    continue
                note(section.keywords, modelled, prefix)
                for frame in section.subsections("FRAME"):
                    note(frame.keywords, CV_MODELLED_KEYWORDS["FRAME"],
                         f"{prefix}/&FRAME")
            note(colvar.keywords, frozenset(), "&FORCE_EVAL/&SUBSYS/&COLVAR")
    prefix = "&MOTION/&FREE_ENERGY/&METADYN"
    note(block.keywords, METADYN_MODELLED_KEYWORDS, prefix)
    for name in METADYN_UNREAD_SUBSECTIONS:
        if block.subsection(name) is not None:
            report.unread.append(f"{prefix}/&{name}")
    printing = block.subsection("PRINT")
    if printing is not None:
        for sub in printing.sections:
            note(sub.keywords, frozenset(), f"{prefix}/&PRINT/&{sub.name.upper()}")
    for metavar in block.subsections("METAVAR"):
        note(metavar.keywords, METAVAR_MODELLED_KEYWORDS, f"{prefix}/&METAVAR")
        wall = metavar.subsection("WALL")
        if wall is None:
            continue
        note(wall.keywords, WALL_MODELLED_KEYWORDS, f"{prefix}/&METAVAR/&WALL")
        for detail in wall.sections:
            note(detail.keywords, WALL_DETAIL_KEYWORDS,
                 f"{prefix}/&METAVAR/&WALL/&{detail.name.upper()}")


def _read_metadyn(sections: list, report: ImportReport) -> None:
    """``&FREE_ENERGY/&METADYN`` plus the ``&COLVAR`` definitions under &SUBSYS."""
    from .metadyn import Metadyn

    free = _walk(sections, ("MOTION", "FREE_ENERGY"))
    if free is None:
        return
    block = free.subsection("METADYN")
    if block is None:
        return
    cvs: list[Any] = []
    subsys = _walk(sections, ("FORCE_EVAL", "SUBSYS"))
    if subsys is not None:
        for colvar in subsys.subsections("COLVAR"):
            cvs.extend(_read_cv_block(colvar))
    printing = block.subsection("PRINT")

    def prints(name: str) -> bool:
        # no &PRINT at all: the tool's own default is to write the file, so a file
        # without one is read as "on" - the same convention as &PRINT/&RESTART
        if printing is None:
            return True
        sub = printing.subsection(name)
        if sub is None:
            return False
        return sub.parameter is None or _bool(sub.parameter)

    every = 1
    for name in ("COLVAR", "HILLS"):
        sub = printing.subsection(name) if printing is not None else None
        if sub is None:
            continue
        each = sub.subsection("EACH")
        value = _num(each.get("MD"), "int") if each is not None else None
        if value:
            every = value
        break
    height = _num(block.get("WW"), "float")
    metadyn = Metadyn(
        enabled=True,
        use_plumed=_bool(block.get("USE_PLUMED", False)),
        plumed_file=str(block.get("PLUMED_INPUT_FILE", "plumed.dat")
                        or "plumed.dat"),
        do_hills=_bool(block.get("DO_HILLS", False)),
        nt_hills=_num(block.get("NT_HILLS"), "int") or 30,
        hill_height=height if height is not None else 1.0e-3,
        well_tempered=_bool(block.get("WELL_TEMPERED", False)),
        temperature=_num(block.get("TEMPERATURE"), "float"),
        delta_t=_num(block.get("DELTA_T"), "float"),
        min_disp=_num(block.get("MIN_DISP"), "float"),
        hill_tail_cutoff=_num(block.get("HILL_TAIL_CUTOFF"), "float"),
        lagrange=_bool(block.get("LAGRANGE", False)),
        lagrange_langevin=_bool(block.get("LANGEVIN", False)),
        metavars=tuple(_read_metavar(m) for m in block.subsections("METAVAR")),
        cvs=tuple(cvs),
        print_colvar=prints("COLVAR"),
        print_hills=prints("HILLS"),
        print_every=every,
    )
    _add(report, "metadyn", "元动力学", metadyn,
         "MOTION/FREE_ENERGY/METADYN + FORCE_EVAL/SUBSYS/COLVAR")
    _unread_metadyn(block, subsys, report)


def _takes() -> dict[str, tuple[str, str]]:
    from .recipes import EXT_RESTART_TAKES

    return EXT_RESTART_TAKES


def _unread(sections: list, report: ImportReport) -> None:
    """Say what the tool saw but does not model, instead of dropping it."""
    for section in sections:
        name = section.name.upper()
        if name not in KNOWN_TOP:
            report.unread.append(f"&{name}")
    electrons = _walk(sections, ("FORCE_EVAL", "DFT"))
    if electrons is not None:
        for key in ("KPOINTS", "WFN_RESTART_FILE_NAME", "CDFT", "TDDFPT",
                    "PROPERTIES", "LOCALIZE", "XAS", "EXCITED_STATES"):
            block = electrons.subsection(key)
            if block is not None:
                report.unread.append(f"&FORCE_EVAL/&DFT/&{key.upper()}")
    if _walk(sections, ("MOTION", "PRINT", "TRAJECTORY")) is None:
        pass


def import_input(path: str | Path) -> ImportReport:
    """Read a CP2K input (or ``-1.restart``) and report the settings in it."""
    file = Path(path)
    result = read_input(file)
    report = ImportReport(path=file, run_type="")
    _collect_simple(result.sections, report)
    _derive(result.sections, report)
    _unread(result.sections, report)
    return report


def apply_hint(report: ImportReport) -> str:
    """One line for the user: what was read, what was not."""
    text = report.summary()
    if report.unread:
        text += "（没读：" + "、".join(report.unread[:4]) + "）"
    return text
