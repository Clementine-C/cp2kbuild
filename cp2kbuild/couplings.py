"""Which parameters only apply under which conditions.

Most CP2K settings are silent no-ops outside their context: ``ADDED_MOS`` is not
read when OT is used, ``&MIXING`` is not written on the OT route, ``TIMECON``
needs a thermostat, ``INTENSITIES`` needs an isolated molecule, ``&EXT_RESTART``
needs a run with iteration steps, and so on.  Setting one of them and getting no
effect is the single most common way a generated input "ignores" what the user
asked for.

This module keeps that knowledge in one declarative table so all three faces of
the tool agree:

* the graphical interface greys the control out and puts the reason on the
  row's "?" (never on the control itself);
* the command line warns when an option the user gave has no effect;
* ``tools/selftest.py`` walks the table and checks both.

Each entry carries its evidence - a manual page or the CP2K source file that
implements the condition - so the rules can be re-checked after a version bump
instead of being taken on faith.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .recipes import (CELL_ANNEALING_ENSEMBLES, Spec, THERMOSTATTED_ENSEMBLES,
                      VELOCITY_ANNEALING_ENSEMBLES, coordinates_are_inline,
                      effective_route, is_classical, mm_route,
                      poisson_solvers_for)

MOTION_TASKS = ("md", "geo_opt", "cell_opt")
CELL_ENSEMBLES = ("NPT_I", "NPT_F", "NPT_IA", "NPE_I", "NPE_F", "MSST",
                  "MSST_DAMPED", "HYDROSTATICSHOCK")


def _writes_thermostat(spec: Spec) -> bool:
    """Would ``md_section`` write a ``&THERMOSTAT`` block for this spec?

    NVE has nothing to control, and the LANGEVIN ensemble brings its own friction
    and random force (its integrator never looks at ``&THERMOSTAT``), so those two
    are the ensembles without the block.
    """
    return (spec.task == "md"
            and spec.ensemble.upper() not in ("NVE", "LANGEVIN"))


#: Fields whose command-line option is *not* ``--<field>`` (or ``--no-<field>``).
#: ``Rule.flag()`` quotes this name in the "this option does not take effect"
#: warning, and ``asked_but_ignored`` uses the same list to notice that the user
#: typed the flag at all - so a wrong spelling here is a user-visible bug (the
#: warning then names an option the parser would reject, e.g. `--print-forces`).
FLAG_SPELLINGS: dict[str, tuple[str, ...]] = {
    "print_forces": ("--no-forces",),
    "print_molden": ("--molden",),
    "molden_vib": ("--no-molden-vib",),
    "wfn_restart_print": ("--no-wfn-print",),
    "wfn_restart_add_last": ("--no-wfn-add-last",),
    "mm_molecules": ("--mm-molecule",),
    "mm_lj": ("--lj", "--lj-pair", "--lj-rcut"),
    "mm_charges": ("--mm-charge",),
    "mm_shift_cutoff": ("--no-shift-cutoff",),
}


@dataclass(frozen=True)
class Rule:
    """``field`` only does something while ``applies`` holds."""

    field: str
    applies: Callable[[Spec, object], bool]
    why: str | Callable[[Spec, object], str]
    evidence: str
    #: command line options that set this field; derived from the field name
    #: unless the interface spells them differently (``--mulliken``, ``--restart``)
    flags: tuple[str, ...] = ()
    #: the value the *tool* uses when the flag is not given, when that differs
    #: from ``Spec``'s dataclass default (FIST MD defaults to 1.0 fs, not 0.5).
    #: Without it a derived default would be reported as "the user asked for it".
    tool_default: Callable[[Spec], object] | None = None
    #: A field the frozen command line interface has no option for (it exists in
    #: the library and the form only).  ``flag()`` still derives a name, because
    #: the self-test walks every rule through the warning path - but that name is
    #: never accepted by ``build_parser()``, and ``tools/dump_couplings.py`` says
    #: so instead of advertising a flag that does not exist.
    gui_only: bool = False

    def default_for(self, spec: Spec):
        if self.tool_default is not None:
            return self.tool_default(spec)
        return _default_of(self.field)

    def flag(self) -> str:
        """The command line option to name in a warning."""
        return self.all_flags()[0]

    def all_flags(self) -> tuple[str, ...]:
        if self.flags:
            return self.flags
        # the command line is what a warning quotes, so a field whose option is
        # spelled differently must not fall back to "--<field>": that name does
        # not exist, and `asked_but_ignored` would fail to recognise the flag the
        # user actually typed
        spelled = FLAG_SPELLINGS.get(self.field)
        if spelled:
            return spelled
        base = self.field.replace("_", "-")
        return (f"--{base}", f"--no-{base}")

    def reason(self, spec: Spec, structure) -> str:
        return self.why(spec, structure) if callable(self.why) else self.why


def _route(spec: Spec) -> str:
    return effective_route(spec)


def _ot(spec: Spec, structure) -> bool:
    return not is_classical(spec) and _route(spec).startswith("OT")


def _diag(spec: Spec, structure) -> bool:
    return not is_classical(spec) and not _route(spec).startswith("OT")


def _periodic(structure) -> bool:
    return bool(structure is not None and structure.is_periodic())


def _has_cell(structure) -> bool:
    return bool(structure is not None and structure.cell is not None)


def _has_hydrogen(structure) -> bool:
    """Whether the system has any hydrogen at all (``&KIND H MASS`` needs it)."""
    elements = getattr(structure, "elements", None) or []
    return any(str(element).strip().upper() == "H" for element in elements)


ROUTE_OT = "只有 OT 路线（`--scf OT`，或「自动」判定成 OT）才会写 `&OT`"
ROUTE_DIAG = ("只有对角化路线（`--scf DIAGONALIZATION`，或带 k 点 / 开 SMEAR 时"
              "「自动」判定成它）才会用到")

# --------------------------------------------------------------------------- #
# METHOD FIST: the whole electronic-structure half of the input disappears
# --------------------------------------------------------------------------- #
CLASSICAL_ONLY = "这是经典力场（METHOD FIST）的设置，Quickstep 路线下没有 `&MM` 段"
QUICKSTEP_ONLY = ("METHOD FIST 是经典力场：CP2K 不算电子，`&DFT`/`&QS`/`&SCF` 整段都不会"
                  "写，这个设置没有任何作用")

#: Settings only the Quickstep route reads.  With ``METHOD FIST`` there is no
#: ``&DFT`` section at all, so every one of them is silently dropped.
DFT_ONLY_FIELDS = (
    "functional", "dispersion", "cutoff", "rel_cutoff", "ngrids", "qs_method",
    "eps_default", "basis", "potential", "basis_target", "basis_file",
    "potential_file", "data_path_style", "scf", "eps_scf", "max_scf",
    "outer_max_scf", "outer_eps_scf", "smearing", "kpoint_scheme",
    "poisson_solver",
    # the SCF route itself, and everything hanging off it
    "ot_minimizer", "ot_preconditioner", "ot_algorithm", "ot_linesearch",
    "ot_stepsize", "ot_energy_gap", "ot_rotation", "ot_safe_diis",
    "outer_optimizer", "diag_algorithm", "level_shift", "added_mos",
    "smear_method", "electronic_temperature", "mixing_method", "mixing_alpha",
    "mixing_nbuffer", "mixing_beta",
    # a classical run has no wave function to read or write
    "scf_guess", "wfn_restart_file", "wfn_restart_print", "wfn_restart_every",
    "wfn_restart_add_last", "wfn_backup_copies",
    # ... and no electrons, so no total charge and no spin multiplicity either
    # (the charges come from the force field, not from the input)
    "charge", "multiplicity", "uks",
    # the Molden file *is* the orbitals: nothing to write without a wave function
    "print_molden", "molden_file", "molden_nlumo", "molden_unit",
    "molden_gto_kind", "molden_write_pseudo", "molden_write_cell",
)
DFT_ONLY_REASON = ("METHOD FIST 是经典力场：没有电子结构，`&DFT`/`&QS`/`&SCF`/`&KPOINTS`"
                   " 整段都不会写，这个设置不起作用")


def _classical(spec: Spec, structure) -> bool:
    return is_classical(spec)


def _mm_file_route(spec: Spec, structure) -> bool:
    """A route whose force field and topology live in files."""
    return is_classical(spec) and mm_route(spec).conn_file


def _mm_single_topology(spec: Spec, structure) -> bool:
    return _mm_file_route(spec, structure) and not spec.mm_molecules


def _mm_in_input(spec: Spec, structure) -> bool:
    """The routes that write ``&CHARGE``/``&NONBONDED`` from the spec."""
    return is_classical(spec) and mm_route(spec).key in ("IN_INPUT", "PAIR")


def _mm_ewald(spec: Spec, structure) -> bool:
    """``&EWALD`` only has real keywords while it is not switched off.

    A non-periodic cell gets ``EWALD_TYPE NONE``, which takes no ALPHA/GMAX/
    O_SPLINE/RCUT at all.
    """
    return is_classical(spec) and _has_cell(structure) and _periodic(structure)


CLASSICAL_RULES: tuple[Rule, ...] = tuple(
    Rule(field, lambda spec, structure: not is_classical(spec), DFT_ONLY_REASON,
         "cp2kbuild/recipes.py:fist_force_eval_section")
    for field in DFT_ONLY_FIELDS
) + (
    # ---- which classical route ----
    Rule(
        "vacuum",
        lambda spec, structure: not (is_classical(spec) and mm_route(spec).conn_file),
        "从文件读力场的路线（AMBER/CHARMM/GROMOS）的坐标和晶胞来自坐标文件："
        "再加真空层会把那个盒子换掉，反而和拓扑文件对不上。"
        "结构文件自带周期性晶胞时也会完全忽略真空层（晶体、以及 POSCAR/CIF 里"
        "已经带真空的表面板都属于这种）；要改那种体系的盒子请用「晶胞」",
        "cp2kbuild/recipes.py:prepare_structure",
    ),
    Rule("mm_route", _classical, CLASSICAL_ONLY,
         "FORCE_EVAL/MM/FORCEFIELD PARMTYPE", flags=("--mm-route",)),
    Rule(
        "mm_parm_file",
        lambda spec, structure: is_classical(spec) and mm_route(spec).parm_file,
        "只有从参数文件读力场的路线（AMBER / CHARMM / GROMOS）才写 `PARM_FILE_NAME`；"
        "`PARMTYPE OFF` 的路线把参数写在输入里",
        "FORCE_EVAL/MM/FORCEFIELD PARM_FILE_NAME",
    ),
    Rule(
        "mm_conn_file",
        _mm_single_topology,
        "混合体系用 `CONNECTIVITY MOL_SET`：每个 `&MOLECULE` 指向自己的拓扑文件，"
        "顶层的 `CONN_FILE_NAME` 不会写",
        "FORCE_EVAL/SUBSYS/TOPOLOGY CONN_FILE_NAME",
    ),
    Rule(
        "mm_molecules",
        _mm_file_route,
        "只有从文件读力场的路线才有拓扑文件要分配（参数写在输入里的路线用 "
        "`CONNECTIVITY GENERATE` 自己判断成键）",
        "FORCE_EVAL/SUBSYS/TOPOLOGY/MOL_SET/MOLECULE",
    ),
    Rule(
        "mm_coord_file",
        _mm_file_route,
        "参数写在输入里的路线用 `&COORD` 内联坐标，不写 `COORD_FILE_NAME`",
        "FORCE_EVAL/SUBSYS/TOPOLOGY COORD_FILE_NAME",
    ),
    Rule(
        "mm_coord_format",
        _mm_file_route,
        "没有坐标文件时 `COORDINATE` 也没有对象",
        "FORCE_EVAL/SUBSYS/TOPOLOGY COORD_FILE_FORMAT",
    ),
    Rule(
        "mm_ff_file",
        lambda spec, structure: is_classical(spec) and mm_route(spec).key == "IN_INPUT",
        "只有「参数写在输入里」的路线会去读这个力场文件；"
        "其他路线的力场来自 `PARM_FILE_NAME` 指向的参数文件",
        "cp2kbuild/recipes.py:_forcefield_from_file",
    ),
    Rule(
        "mm_element_lj",
        _mm_in_input,
        "只有参数写在输入里的路线会从 ε/σ 生成 `&LENNARD-JONES` 对势块",
        "FORCE_EVAL/MM/FORCEFIELD/NONBONDED/LENNARD-JONES",
        flags=("--lj",),
    ),
    Rule(
        "mm_lj",
        _mm_in_input,
        "只有参数写在输入里的路线会写 `&LENNARD-JONES`；"
        "其他路线的 LJ 参数来自参数文件",
        "FORCE_EVAL/MM/FORCEFIELD/NONBONDED/LENNARD-JONES",
    ),
    Rule(
        "mm_charges",
        _mm_in_input,
        "只有参数写在输入里的路线会写 `&CHARGE`；"
        "其他路线的电荷来自参数文件（PSF/prmtop）",
        "FORCE_EVAL/MM/FORCEFIELD/CHARGE",
    ),
    Rule(
        "mm_vdw_scale14",
        lambda spec, structure: is_classical(spec) and mm_route(spec).parm_file,
        "1-4 缩放只有从参数文件读力场的路线才有：排除表来自拓扑文件",
        "FORCE_EVAL/MM/FORCEFIELD VDW_SCALE14",
    ),
    Rule(
        "mm_ei_scale14",
        lambda spec, structure: is_classical(spec) and mm_route(spec).parm_file,
        "1-4 缩放只有从参数文件读力场的路线才有：排除表来自拓扑文件",
        "FORCE_EVAL/MM/FORCEFIELD EI_SCALE14",
    ),
    Rule(
        "mm_shift_cutoff",
        lambda spec, structure: is_classical(spec)
        and mm_route(spec).key in ("AMBER", "CHARMM", "GROMOS96", "GROMOS87"),
        "`SHIFT_CUTOFF` 影响的是非键相互作用的连续性，"
        "参数写在输入里的路线不需要它",
        "FORCE_EVAL/MM/FORCEFIELD SHIFT_CUTOFF",
    ),
    Rule(
        "mm_spline_emax",
        lambda spec, structure: is_classical(spec)
        and mm_route(spec).key in ("AMBER", "CHARMM", "GROMOS96", "GROMOS87"),
        "`&SPLINE` 修的是参数文件给出的大半径势能尾部，"
        "参数写在输入里的路线自己定 RCUT",
        "FORCE_EVAL/MM/FORCEFIELD/SPLINE",
    ),
    Rule("mm_multiple_potential", _classical, CLASSICAL_ONLY,
         "FORCE_EVAL/MM/FORCEFIELD MULTIPLE_POTENTIAL"),
    Rule("mm_ignore_missing_params", _classical, CLASSICAL_ONLY,
         "FORCE_EVAL/MM/FORCEFIELD IGNORE_MISSING_CRITICAL_PARAMS"),
    Rule("mm_verlet_skin", _classical, CLASSICAL_ONLY,
         "FORCE_EVAL/MM/NEIGHBOR_LISTS VERLET_SKIN"),
    Rule(
        "mm_ewald",
        _mm_ewald,
        "非周期晶胞里 `&EWALD` 只能是 `EWALD_TYPE NONE`（SPME/PME 要在周期网格上插值），"
        "那时没有类型可选",
        "FORCE_EVAL/MM/POISSON/EWALD EWALD_TYPE",
    ),
    Rule(
        "mm_ewald_gmax",
        _mm_ewald,
        "`GMAX` 是倒空间网格数，只在 EWALD/PME/SPME 下有对象",
        "FORCE_EVAL/MM/POISSON/EWALD GMAX",
    ),
    Rule(
        "mm_ewald_alpha",
        _mm_ewald,
        "`ALPHA` 是 Ewald 分裂参数，只在 EWALD/PME/SPME 下有对象",
        "FORCE_EVAL/MM/POISSON/EWALD ALPHA",
    ),
    Rule(
        "mm_o_spline",
        _mm_ewald,
        "`O_SPLINE` 是 PME/SPME 的插值阶数",
        "FORCE_EVAL/MM/POISSON/EWALD O_SPLINE",
    ),
    Rule(
        "mm_rcut",
        _mm_ewald,
        "`RCUT` 是 Ewald 实空间截断，只在 EWALD/PME/SPME 下有对象；"
        "`EWALD_TYPE NONE` 用每个 `&LENNARD-JONES` 自己的 RCUT",
        "FORCE_EVAL/MM/POISSON/EWALD RCUT",
    ),
)

RULES: tuple[Rule, ...] = (
    # ---------------- SCF route: OT ----------------
    Rule("ot_minimizer", _ot, ROUTE_OT, "FORCE_EVAL/DFT/SCF/OT"),
    Rule("ot_preconditioner", _ot, ROUTE_OT, "FORCE_EVAL/DFT/SCF/OT"),
    Rule("ot_algorithm", _ot, ROUTE_OT, "FORCE_EVAL/DFT/SCF/OT"),
    Rule("ot_linesearch", _ot, ROUTE_OT, "FORCE_EVAL/DFT/SCF/OT"),
    Rule("ot_rotation", _ot, ROUTE_OT, "FORCE_EVAL/DFT/SCF/OT"),
    Rule(
        "ot_stepsize",
        lambda spec, structure: _ot(spec, structure)
        and spec.ot_linesearch.upper() in ("2PNT", "3PNT", "GOLD"),
        ROUTE_OT + "，而且它是**线搜索**的初始步长——LINESEARCH 选 NONE/ADAPT 时没有线搜索",
        "FORCE_EVAL/DFT/SCF/OT/STEPSIZE（手册：Initial stepsize used for the line search）",
    ),
    Rule(
        "ot_energy_gap",
        lambda spec, structure: _ot(spec, structure)
        and spec.ot_preconditioner.upper() in ("FULL_ALL", "FULL_SINGLE_INVERSE"),
        "手册写明只有 `FULL_ALL` 和 `FULL_SINGLE_INVERSE` 两个预条件子会读它"
        "（其余预条件子根本不看这个值）",
        "FORCE_EVAL/DFT/SCF/OT/ENERGY_GAP（…used in preconditioning, especially "
        "effective with the FULL_ALL preconditioner）",
    ),
    Rule(
        "ot_safe_diis",
        lambda spec, structure: _ot(spec, structure)
        and spec.ot_minimizer.upper() in ("DIIS", "BROYDEN"),
        "它过滤的是 **DIIS 步**：MINIMIZER 不是 DIIS/BROYDEN 时没有 DIIS 步需要拒绝",
        "FORCE_EVAL/DFT/SCF/OT/SAFE_DIIS（Reject DIIS steps …）",
    ),
    # ---------------- SCF route: diagonalisation ----------------
    Rule("diag_algorithm", _diag, ROUTE_DIAG, "FORCE_EVAL/DFT/SCF/DIAGONALIZATION"),
    Rule(
        "level_shift",
        _diag,
        ROUTE_DIAG + "（源码里只有 `qs_scf_diagonalization.F` 用 level_shift）",
        "src/qs_scf_diagonalization.F:198",
    ),
    Rule(
        "added_mos",
        _diag,
        ROUTE_DIAG + "（`qs_environment.F` 里是 `IF (.NOT. scf_control%use_ot)` 才读 ADDED_MOS）",
        "src/qs_environment.F:2129",
    ),
    # ---------------- smearing and mixing ----------------
    Rule(
        "smear_method",
        lambda spec, structure: bool(spec.smearing),
        "没开 SMEAR（`&SMEAR` 段本身不会出现）",
        "FORCE_EVAL/DFT/SCF/SMEAR",
    ),
    Rule(
        "electronic_temperature",
        lambda spec, structure: bool(spec.smearing),
        "没开 SMEAR：占据数不需要展宽，温度无人可设",
        "FORCE_EVAL/DFT/SCF/SMEAR/ELECTRONIC_TEMPERATURE",
    ),
    Rule(
        "mixing_method",
        lambda spec, structure: _diag(spec, structure) and not spec.smearing,
        "`&MIXING` 只写在对角化路线、且没开 SMEAR 时：OT 不用它，"
        "smearing 路线留给 CP2K 自己的默认混合",
        "cp2kbuild/recipes.py:scf_section",
    ),
)
RULES += tuple(
    Rule(
        field,
        lambda spec, structure: _diag(spec, structure) and not spec.smearing,
        "`&MIXING` 只写在对角化路线、且没开 SMEAR 时：OT 不用它，"
        "smearing 路线留给 CP2K 自己的默认混合",
        "cp2kbuild/recipes.py:scf_section",
    )
    for field in ("mixing_alpha", "mixing_nbuffer", "mixing_beta")
)

RULES += (
    # ---------------- restarting ----------------
    Rule(
        "wfn_restart_file",
        lambda spec, structure: (spec.scf_guess or "").upper()
        in ("RESTART", "HISTORY_RESTART"),
        "SCF_GUESS 不是 RESTART/HISTORY_RESTART：不需要指定波函数文件",
        "FORCE_EVAL/DFT/WFN_RESTART_FILE_NAME",
    ),
    Rule(
        "wfn_restart_every",
        lambda spec, structure: bool(spec.wfn_restart_print),
        "关掉了「波函数文件」：`&SCF/&PRINT/&RESTART` 整段不会出现",
        "FORCE_EVAL/DFT/SCF/PRINT/RESTART/EACH",
    ),
    Rule(
        "wfn_restart_level",
        lambda spec, structure: bool(spec.wfn_restart_print),
        "关掉了「波函数文件」：没有间隔要计数",
        "FORCE_EVAL/DFT/SCF/PRINT/RESTART/EACH",
    ),
    Rule(
        "wfn_restart_add_last",
        lambda spec, structure: bool(spec.wfn_restart_print),
        "关掉了「波函数文件」：没有东西可以在最后一步写",
        "FORCE_EVAL/DFT/SCF/PRINT/RESTART/ADD_LAST",
        flags=("--no-wfn-add-last",),
    ),
    Rule(
        "wfn_backup_copies",
        lambda spec, structure: bool(spec.wfn_restart_print),
        "关掉了「波函数文件」：没有备份可留",
        "FORCE_EVAL/DFT/SCF/PRINT/RESTART/BACKUP_COPIES",
    ),
    Rule(
        "restart_every",
        lambda spec, structure: bool(spec.motion_restart_print),
        "关掉了「重启文件」：`&MOTION/&PRINT/&RESTART` 整段不会出现",
        "MOTION/PRINT/RESTART/EACH",
    ),
    Rule(
        "restart_add_last",
        lambda spec, structure: bool(spec.motion_restart_print),
        "关掉了「重启文件」：没有东西可以在最后一步写",
        "MOTION/PRINT/RESTART/ADD_LAST",
        flags=("--no-restart-add-last",),
    ),
    Rule(
        "restart_backups",
        lambda spec, structure: bool(spec.motion_restart_print),
        "关掉了「重启文件」：没有备份可留",
        "MOTION/PRINT/RESTART/BACKUP_COPIES",
    ),
    Rule(
        "ext_restart_file",
        lambda spec, structure: spec.task in MOTION_TASKS,
        "单点 / 频率 / 能带没有可续的迭代步，`&EXT_RESTART` 会被 CP2K 忽略"
        "（校验器也会给 `ext-restart-ignored`）",
        "FORCE_EVAL/DFT/… ; EXT_RESTART（continues a run with iteration steps）",
        flags=("--restart",),
    ),
    Rule(
        "ext_restart_default",
        lambda spec, structure: bool(spec.ext_restart_file),
        "没有给续算文件时，这个开关没有对象",
        "EXT_RESTART/RESTART_DEFAULT",
        flags=("--no-restart-default",),
    ),
    Rule(
        "ext_restart_take",
        lambda spec, structure: bool(spec.ext_restart_file),
        "没有给续算文件时，没有东西可以「取」",
        "EXT_RESTART/RESTART_<POS|VEL|CELL|…>（选了几项就写 "
        "`RESTART_DEFAULT F` + 这几项 `T`；见 cp2kbuild/recipes.py:"
        "ext_restart_section）",
        flags=("--restart-take",),
    ),
    # ---------------- k-points: DIAGONALIZATION + a periodic cell ----------
    # Two conditions, one entry per control: the reason is picked by ``why`` so
    # the "?" always names the condition that actually failed.
    Rule(
        "kpoint_grid",
        lambda spec, structure: _diag(spec, structure) and _periodic(structure),
        lambda spec, structure: (
            "k 点只有**对角化**路线能用：OT + `&KPOINTS` 时 CP2K 直接中止"
            "（`qs_scf_initialization.F`：`OT diagonalization not possible with "
            "kpoint calculations`）。把 SCF 路线换成 DIAGONALIZATION，"
            "或者只用 Γ 点（不勾这个组）"
            if not _diag(spec, structure) else
            "没有周期性晶胞：k 点采样无意义"
            "（校验器会给 `kpoints-without-periodicity`）"),
        "FORCE_EVAL/DFT/KPOINTS/SCHEME（+ FORCE_EVAL/DFT/SCF/&OT）",
        flags=("--kpoint-grid", "--kpoint-spacing"),
    ),
    Rule(
        "kpoint_scheme",
        lambda spec, structure: _diag(spec, structure) and _periodic(structure),
        lambda spec, structure: (
            "k 点只有**对角化**路线能用（OT + k 点 CP2K 会中止）"
            if not _diag(spec, structure) else
            "没有周期性晶胞：k 点采样无意义"),
        "FORCE_EVAL/DFT/KPOINTS/SCHEME",
        flags=("--kpoint-scheme",),
    ),
    Rule(
        "kpoint_points",
        lambda spec, structure: _diag(spec, structure) and _periodic(structure),
        lambda spec, structure: (
            "k 点只有**对角化**路线能用（OT + k 点 CP2K 会中止）"
            if not _diag(spec, structure) else
            "没有周期性晶胞：k 点采样无意义"),
        "FORCE_EVAL/DFT/KPOINTS/KPOINT",
        flags=("--kpoint-point",),
    ),
    # ---------------- metadynamics ----------------
    #: The whole ``&COLVAR``/``&METADYN`` pair belongs to an MD run: the bias is
    #: grown while the atoms move, so a geo_opt/cell_opt/energy input has nothing
    #: to put it on (``build`` says so instead of writing the sections anyway).
    Rule(
        "metadyn",
        lambda spec, structure: spec.task == "md",
        "元动力学靠 MD 的步数积累偏置：`&MOTION/&FREE_ENERGY/&METADYN` 和 "
        "`&SUBSYS/&COLVAR` 只在 `RUN_TYPE MD` 下会被 CP2K 读。要先用 MD 采样，"
        "或者把任务改成 MD",
        "MOTION/FREE_ENERGY/METADYN ; FORCE_EVAL/SUBSYS/COLVAR",
        flags=("--metadyn", "--meta-plumed", "--cv", "--metavar"),
    ),
    # ---------------- constraints ----------------
    Rule(
        "fixed_atoms",
        lambda spec, structure: spec.task in MOTION_TASKS,
        "这个 RUN_TYPE 不会移动原子，固定原子没有任何作用"
        "（手写输入里出现 &CONSTRAINT 时校验器也会给 `constraint-ignored`）",
        "MOTION/CONSTRAINT/FIXED_ATOMS（只有 GEO_OPT/CELL_OPT/MD/BAND 有意义）",
        flags=("--fixed-atoms",),
    ),
    # ---------------- cell, periodicity, Poisson ----------------
    Rule(
        "vacuum",
        lambda spec, structure: not _periodic(structure),
        "结构文件自带周期性晶胞时**完全忽略**真空层（晶体、以及 POSCAR/CIF 里已经"
        "带真空的表面板都属于这种）；要改那种体系的盒子请用「晶胞」",
        "cp2kbuild/recipes.py:prepare_structure",
    ),
    Rule(
        "scaled_coords",
        lambda spec, structure: _has_cell(structure),
        "没有晶胞：分数坐标需要晶胞才能换算成 Cartesian",
        "FORCE_EVAL/SUBSYS/COORD（SCALED 需要 &CELL）",
    ),
    Rule(
        "poisson_solver",
        lambda spec, structure: _has_cell(structure),
        "没有 &CELL 时不写 `&POISSON`（CP2K 会按 PERIODIC XYZ + 默认求解器处理）",
        "FORCE_EVAL/DFT/POISSON",
    ),
    # ---------------- deuterium ----------------
    Rule(
        "h_to_d",
        lambda spec, structure: (not is_classical(spec) and _has_hydrogen(structure)
                                 and spec.task in ("md", "geo_opt", "cell_opt",
                                                   "freq", "neb")),
        lambda spec, structure: (
            "氘代改的是 `&KIND H` 的质量：经典力场（METHOD FIST）的质量来自拓扑文件"
            "（AMBER 的 prmtop `%FLAG MASS`、CHARMM 的 PSF，见 "
            "topology_amber.F:526），`&KIND MASS` 不会被读"
            if is_classical(spec) else
            "体系里没有氢：没有可氘代的原子"
            if not _has_hydrogen(structure) else
            "质量只进入动力学：单点能、能带、态密度的能量和力与质量无关"),
        "FORCE_EVAL/SUBSYS/KIND MASS",
        gui_only=True,
    ),
    # ---------------- per task ----------------
    Rule(
        "print_forces",
        lambda spec, structure: spec.task == "energy",
        "只有单点能页面会写 `&FORCE_EVAL/&PRINT/&FORCES`",
        "cp2kbuild/recipes.py:force_eval_section",
    ),
    Rule(
        "print_mulliken",
        lambda spec, structure: spec.task == "energy" and not _periodic(structure),
        "Mulliken 布居只在单点能任务、且体系非周期时才请求",
        "cp2kbuild/recipes.py:force_eval_section",
        flags=("--mulliken",),
    ),
    Rule(
        "print_dipole",
        lambda spec, structure: spec.task == "energy" and not _periodic(structure),
        "偶极矩对完全周期体系没有意义，所以周期性体系不写 `&MOMENTS`",
        "cp2kbuild/recipes.py:force_eval_section",
        flags=("--no-dipole",),
    ),
    Rule(
        "optimizer",
        lambda spec, structure: spec.task in ("geo_opt", "cell_opt"),
        "只有几何优化 / 变胞优化有优化器",
        "MOTION/GEO_OPT, MOTION/CELL_OPT",
    ),
    Rule(
        "max_iter",
        lambda spec, structure: spec.task in ("geo_opt", "cell_opt"),
        "只有几何优化 / 变胞优化有迭代步数",
        "MOTION/GEO_OPT/MAX_ITER, MOTION/CELL_OPT/MAX_ITER",
    ),
    Rule(
        "external_pressure",
        lambda spec, structure: spec.task == "cell_opt",
        "外压只对变胞优化有意义",
        "MOTION/CELL_OPT/EXTERNAL_PRESSURE",
    ),
    Rule(
        "keep_angles",
        lambda spec, structure: spec.task == "cell_opt",
        "固定晶胞角度只对变胞优化有意义",
        "MOTION/CELL_OPT/KEEP_ANGLES",
    ),
    Rule(
        "ensemble",
        lambda spec, structure: spec.task == "md",
        "系综是 MD 的设置",
        "MOTION/MD/ENSEMBLE",
    ),
    Rule(
        "thermostat",
        lambda spec, structure: _writes_thermostat(spec),
        lambda spec, structure: (
            "恒温器是 MD 的设置" if spec.task != "md" else
            "LANGEVIN 的摩擦 + 随机力就是它自己的恒温器（`SUBROUTINE langevin` "
            "里不碰 `&THERMOSTAT`），CP2K 自己的 Langevin 输入也都不写这个块——"
            "温度控制请用 `&LANGEVIN GAMMA`"
            if spec.ensemble.upper() == "LANGEVIN" else
            "NVE 没有恒温器：`md_section` 里只有恒温系综才写 `&THERMOSTAT`"),
        "cp2kbuild/recipes.py:md_section",
    ),
    # ---- 升温 / 降温 ------------------------------------------------------ #
    #: ``&MD TEMPERATURE_ANNEALING`` moves the Langevin target temperature and is
    #: applied only by the Langevin integrator, so it is tied to that ensemble.
    Rule(
        "temperature_annealing",
        lambda spec, structure: spec.task == "md"
        and spec.ensemble.upper() == "LANGEVIN",
        "温度斜坡只对 LANGEVIN 生效：`TEMPERATURE_ANNEALING` 的那个乘法写在"
        " `SUBROUTINE langevin` 里，手册也写着 \"works only for the Langevin "
        "ensemble\"；别的系综下它一声不响什么都不做",
        "MOTION/MD/TEMPERATURE_ANNEALING",
    ),
    #: ``&MD ANNEALING`` rescales velocities, so CP2K aborts when a particle
    #: thermostat is on as well.
    Rule(
        "annealing",
        lambda spec, structure: spec.task == "md"
        and spec.ensemble.upper() in VELOCITY_ANNEALING_ENSEMBLES,
        "速度缩放不是恒温器：CP2K 不允许它和粒子恒温器同时开（NVT/NPT 会直接"
        "中止：\"these two methods ... act one against the other\"），LANGEVIN "
        "也自带控温",
        "MOTION/MD/ANNEALING",
    ),
    Rule(
        "annealing_cell",
        lambda spec, structure: spec.task == "md"
        and spec.ensemble.upper() in CELL_ANNEALING_ENSEMBLES,
        "晶胞速度的退火只在 NPE_I / NPE_F 里实现（`integrator.F`），"
        "别的系综写了也不生效",
        "MOTION/MD/ANNEALING_CELL",
    ),
    Rule(
        "langevin_gamma",
        lambda spec, structure: spec.task == "md"
        and spec.ensemble.upper() == "LANGEVIN",
        "`&LANGEVIN` 是 LANGEVIN 系综自己的段（CP2K 默认 GAMMA 0 = 没有摩擦也"
        "没有噪声，等于 NVE，所以工具会显式写一个值）",
        "MOTION/MD/LANGEVIN/GAMMA",
    ),
    Rule(
        "restart_temperature_annealing",
        lambda spec, structure: (bool(spec.ext_restart_file)
                                 and spec.temperature_annealing is not None),
        "只有「接力」并且真的开了温度斜坡时才写：它让 CP2K 用 restart 里存下的"
        "目标温度接着斜坡（`input_cp2k_check.F` 会把 MOTION%MD%TEMPERATURE 覆盖掉）",
        "EXT_RESTART/RESTART_TEMPERATURE_ANNEALING",
    ),

    Rule(
        "temperature",
        lambda spec, structure: spec.task == "md",
        "温度是 MD 的设置；NVE 也读它——手册写明 TEMPERATURE "
        "「用来给 init/pos restart 的速度定温」，CP2K 自己的 argon NVE 测试就写了它",
        "MOTION/MD/TEMPERATURE",
    ),
    Rule(
        "timestep",
        lambda spec, structure: spec.task == "md",
        "时间步长是 MD 的设置",
        "MOTION/MD/TIMESTEP",
        # the tool picks a bigger step for a classical force field (there is no
        # electronic structure to resolve), so that default is not "an ask"
        tool_default=lambda spec: 1.0 if is_classical(spec) else 0.5,
    ),
    Rule(
        "steps",
        lambda spec, structure: spec.task == "md",
        "步数是 MD 的设置",
        "MOTION/MD/STEPS",
    ),
    Rule(
        "pressure",
        lambda spec, structure: spec.task == "md"
        and spec.ensemble.upper() in CELL_ENSEMBLES,
        "压力是 `&BAROSTAT` 的关键字，只有改变晶胞的系综才有它",
        "MOTION/MD/BAROSTAT/PRESSURE",
    ),
    Rule(
        "thermostat_timecon",
        lambda spec, structure: spec.task == "md"
        and spec.thermostat.upper() in ("NOSE", "CSVR"),
        "TIMECON 是 NOSE / CSVR 恒温器的关键字（GLE / AD_LANGEVIN / NONE 没有它）",
        "MOTION/MD/THERMOSTAT/NOSE/TIMECON",
    ),
    Rule(
        "barostat_timecon",
        lambda spec, structure: spec.task == "md"
        and spec.ensemble.upper() in CELL_ENSEMBLES,
        "它是 `&BAROSTAT` 的关键字，只有改变晶胞的系综才有 `&BAROSTAT`",
        "MOTION/MD/BAROSTAT/TIMECON",
    ),
    Rule(
        "trajectory_stride",
        lambda spec, structure: spec.task in ("md", "geo_opt", "cell_opt", "neb"),
        "只有会走多步的任务才写轨迹（单点 / 频率没有轨迹文件）",
        "MOTION/PRINT/TRAJECTORY/EACH",
    ),
    # ---- what an MD run prints while it runs ------------------------------ #
    #: A thermostat is written only for the ensembles that control temperature
    #: (``ensemble != NVE``) and only when one was chosen - the same condition
    #: ``md_section`` uses, so the REGION keyword and its print key cannot end up
    #: in an input without a ``&THERMOSTAT``.
    Rule(
        "thermostat_region",
        lambda spec, structure: (_writes_thermostat(spec)
                                 and spec.thermostat.upper() != "NONE"),
        "REGION 是 `&THERMOSTAT` 的关键字：NVE 不写恒温器，LANGEVIN 自带控温，"
        "恒温器选 NONE 时也没有它",
        "MOTION/MD/THERMOSTAT/REGION",
        gui_only=True,
    ),
    Rule(
        "thermostat_temp_print",
        lambda spec, structure: (_writes_thermostat(spec)
                                 and spec.thermostat.upper() != "NONE"),
        "`&THERMOSTAT/&PRINT/&TEMPERATURE` 打印的是恒温器各区域的温度："
        "没有恒温器就没有可打印的区域",
        "MOTION/MD/THERMOSTAT/PRINT/TEMPERATURE",
        gui_only=True,
    ),
    Rule(
        "thermostat_temp_every",
        lambda spec, structure: (bool(spec.thermostat_temp_print)
                                 and _writes_thermostat(spec)
                                 and spec.thermostat.upper() != "NONE"),
        "没勾「热浴温度」：`&THERMOSTAT/&PRINT/&TEMPERATURE` 整段不会写",
        "MOTION/MD/THERMOSTAT/PRINT/TEMPERATURE/EACH",
        gui_only=True,
    ),
    Rule(
        "md_temp_kind_print",
        lambda spec, structure: spec.task == "md",
        "`&MD TEMP_KIND` + `&MD/&PRINT/&TEMP_KIND` 是 MD 的设置："
        "只有会走多步的动力学才算得出温度",
        "MOTION/MD/TEMP_KIND",
        gui_only=True,
    ),
    Rule(
        "md_temp_kind_every",
        lambda spec, structure: spec.task == "md" and bool(spec.md_temp_kind_print),
        "没勾「每个原子种类的温度」：`&MD TEMP_KIND` 和它的打印键都不会写",
        "MOTION/MD/PRINT/TEMP_KIND/EACH",
        gui_only=True,
    ),
    Rule(
        "md_energy_print",
        lambda spec, structure: spec.task == "md",
        "`&MD/&PRINT/&ENERGY`（`-1.ener`，含 Temp[K] 列）是 MD 的打印键",
        "MOTION/MD/PRINT/ENERGY",
        gui_only=True,
    ),
    Rule(
        "md_energy_every",
        lambda spec, structure: spec.task == "md" and bool(spec.md_energy_print),
        "没勾「能量文件」：`&ENERGY` 整段不会写（CP2K 仍会按 PRINT_LEVEL "
        "写它自己那份 `-1.ener`，间隔是每步）",
        "MOTION/MD/PRINT/ENERGY/EACH",
        gui_only=True,
    ),
    Rule(
        "md_energy_file",
        lambda spec, structure: spec.task == "md" and bool(spec.md_energy_print),
        "没勾「能量文件」：`&ENERGY` 整段不会写，也就没有文件名可改",
        "MOTION/MD/PRINT/ENERGY/FILENAME",
        gui_only=True,
    ),
    Rule(
        "intensities",
        lambda spec, structure: spec.task == "freq" and not _periodic(structure),
        "红外强度要算偶极导数，只对**非周期**体系请求（周期性体系 CP2K 不支持）",
        "FORCE_EVAL/DFT/…/VIBRATIONAL_ANALYSIS/INTENSITIES",
    ),
    Rule(
        "thermochemistry",
        lambda spec, structure: spec.task == "freq",
        "热化学量只在频率计算里",
        "VIBRATIONAL_ANALYSIS/THERMOCHEMISTRY",
    ),
    Rule(
        "molden_vib",
        lambda spec, structure: spec.task == "freq",
        "`&MOLDEN_VIB` 是 `&VIBRATIONAL_ANALYSIS/&PRINT` 下的打印键："
        "只有频率计算才有振动模式可写",
        "VIBRATIONAL_ANALYSIS/PRINT/MOLDEN_VIB",
    ),
    Rule(
        "lattice",
        lambda spec, structure: spec.task == "band",
        "晶格类型只用于自动生成能带路径",
        "FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE",
    ),
    Rule(
        "kpath",
        lambda spec, structure: spec.task == "band",
        "k 点路径只用于能带计算",
        "FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE",
    ),
    Rule(
        "npoints",
        lambda spec, structure: spec.task == "band",
        "路径点数只用于能带计算",
        "FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE",
    ),
    Rule(
        "pdos_nlumo",
        lambda spec, structure: spec.task == "pdos",
        "NLUMO 只用于投影态密度",
        "FORCE_EVAL/DFT/PRINT/DOS/PDOS",
    ),
    Rule(
        "pdos_ldos",
        lambda spec, structure: spec.task == "pdos",
        "LDOS 列表只用于投影态密度",
        "FORCE_EVAL/DFT/PRINT/DOS/PDOS",
    ),
    Rule(
        "pdos_broaden",
        lambda spec, structure: spec.task == "pdos",
        "展宽只用于投影态密度",
        "FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN",
    ),
    Rule(
        "pdos_broaden_type",
        lambda spec, structure: spec.task == "pdos" and spec.pdos_broaden is not None,
        "没有设展宽宽度 FWHM 时不会写 `&BROADEN`，也就没有展宽函数可选",
        "FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN/TYPE",
    ),
    Rule(
        "band_type",
        lambda spec, structure: spec.task == "neb",
        "NEB 类型只用于过渡态搜索",
        "MOTION/BAND/BAND_TYPE",
    ),
    Rule(
        "k_spring",
        lambda spec, structure: spec.task == "neb",
        "弹簧常数只用于 NEB",
        "MOTION/BAND/K_SPRING",
    ),
    Rule("replicas", lambda spec, structure: spec.task == "neb",
         "镜像只用于 NEB", "MOTION/BAND/REPLICA"),
)

#: The Molden details only exist once the file itself is asked for - the whole
#: ``&MO_MOLDEN`` block is written by that one switch.
RULES += tuple(
    Rule(field, lambda spec, structure: bool(spec.print_molden),
         "没勾「写分子轨道」：`&MO_MOLDEN` 整段不会写进输入",
         "FORCE_EVAL/DFT/PRINT/MO_MOLDEN")
    for field in ("molden_file", "molden_nlumo", "molden_unit",
                  "molden_gto_kind", "molden_write_pseudo", "molden_write_cell")
)

#: The classical (FIST) rules come first so that, for a setting both tables
#: describe, the *more fundamental* reason wins: "METHOD FIST has no electrons"
#: explains more than "the OT route does not use it".
RULES = CLASSICAL_RULES + RULES


def applicability(spec: Spec, structure=None) -> dict[str, Rule]:
    """field -> the rule that does *not* apply (empty dict = all apply)."""
    out: dict[str, Rule] = {}
    for rule in RULES:
        if not rule.applies(spec, structure):
            out.setdefault(rule.field, rule)
    return out


def _default_of(field: str):
    import dataclasses

    entry = Spec.__dataclass_fields__.get(field)
    if entry is None or entry.default is dataclasses.MISSING:
        return None
    return entry.default


def asked_but_ignored(spec: Spec, structure, given: set[str]) -> list[Rule]:
    """Rules the user asked for (by flag or by a non-default value) that do not
    apply, i.e. the settings this input silently drops.

    A value that means "switch it off" is never an ask, so ``--no-intensities``
    does not produce a warning about intensities.
    """
    out: list[Rule] = []
    seen: set[str] = set()
    for rule in RULES:
        if rule.applies(spec, structure):
            continue
        if rule.field in seen:
            # a field can have several rules; report the most fundamental one
            continue
        flags = rule.all_flags()
        value = getattr(spec, rule.field, None)
        positive = [flag for flag in flags if not flag.startswith("--no-")]
        negative = [flag for flag in flags if flag.startswith("--no-")]
        if any(flag in given for flag in positive):
            seen.add(rule.field)
            out.append(rule)
        elif any(flag in given for flag in negative):
            if value:
                seen.add(rule.field)
                out.append(rule)
        elif value != rule.default_for(spec) and bool(value):
            seen.add(rule.field)
            out.append(rule)
    return out


def poisson_solver_warning(spec: Spec, structure) -> str | None:
    """The chosen Poisson solver is not meant for this cell periodicity."""
    solver = (spec.poisson_solver or "").upper()
    if not solver or structure is None or structure.cell is None:
        return None
    periodicity = (structure.periodic or "XYZ").upper()
    if solver in poisson_solvers_for(periodicity):
        return None
    valid = ", ".join(poisson_solvers_for(periodicity))
    return (f"POISSON_SOLVER {solver} 不是给 {periodicity} 周期晶胞用的"
            f"（这里可用：{valid}）")
