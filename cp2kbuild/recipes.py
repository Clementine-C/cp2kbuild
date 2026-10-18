"""Task templates: turn a high-level request into a complete CP2K input tree.

Every template writes explicit, self-documented input.  Nothing is left to a
CP2K default that matters physically, and anything that is a convention (the SCF
route, the cutoff, the dispersion correction) is spelled out in a comment with a
link to the relevant manual page.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .datafiles import DataIndex
from .kpoints import build_kpath, distribute_points
from .manualdb import manual_root
from .metadyn import Metadyn, cv_blocks, free_energy_section, problems as metadyn_problems
from .model import Section
from .recommend import Setup, choose_assignments
from .structures import Cell, Structure, cell_section, coord_section

MANUAL = manual_root()
"""Manual base URL, following whichever database version is in use."""

TASKS = (
    "energy", "geo_opt", "cell_opt", "md", "freq", "band", "pdos", "neb",
)

RUN_TYPE_OF = {
    "energy": "ENERGY",
    "geo_opt": "GEO_OPT",
    "cell_opt": "CELL_OPT",
    "md": "MD",          # AIMD: RUN_TYPE MD with FORCE_EVAL METHOD QUICKSTEP
    "freq": "VIBRATIONAL_ANALYSIS",
    "band": "ENERGY",
    "pdos": "ENERGY",
    "neb": "BAND",
}

# recommended plane-wave cutoff per basis-set family (Ry)
CUTOFF_BY_FAMILY = (
    ("TZV2PX", 700.0),
    ("TZV2P", 700.0),
    ("TZVP", 600.0),
    ("DZVP", 400.0),
    ("SZV", 350.0),
    ("pob", 600.0),
    ("def2", 600.0),
    ("ccGRB", 700.0),
    ("MINBAS", 300.0),
)

DISPERSION_ALIASES = {
    "none": None,
    "off": None,
    "d3": "DFTD3",
    "dftd3": "DFTD3",
    "d3bj": "DFTD3(BJ)",
    "dftd3(bj)": "DFTD3(BJ)",
    "d2": "DFTD2",
    "dftd2": "DFTD2",
    "rvv10": "RVVE10",
}


# --------------------------------------------------------------------------- #
# classical force fields (FORCE_EVAL METHOD FIST)
# --------------------------------------------------------------------------- #
#: One classical force-field route: which ``&FORCEFIELD`` parameters and which
#: ``&TOPOLOGY`` connectivity file it needs.  Every field is taken from CP2K's own
#: enumerations - ``PARMTYPE`` in ``input_cp2k_mm.F`` (OFF/CHM/G87/G96/AMBER) and
#: ``CONN_FILE_FORMAT`` / ``COORD_FILE_FORMAT`` in ``input_cp2k_subsys.F`` - so a
#: route cannot invent a combination CP2K does not understand.
#:
#: The CHARMM route dominates CP2K's own regression suite (329 of 341 inputs that
#: carry a ``PARMTYPE``); AMBER is the one cp2kbuild recommends because the user's
#: prmtop/inpcrd files map onto it directly.
@dataclass(frozen=True)
class MmRoute:
    key: str
    label: str
    parmtype: str            # &FORCEFIELD PARMTYPE
    connectivity: str        # &TOPOLOGY CONNECTIVITY (CONN_FILE_FORMAT)
    coord_format: str        # &TOPOLOGY COORDINATE (COORD_FILE_FORMAT)
    parm_file: bool          # needs &FORCEFIELD PARM_FILE_NAME
    conn_file: bool          # needs &TOPOLOGY CONN_FILE_NAME
    vdw_scale14: float | None = None   # 1-4 scaling CP2K would get wrong by default
    ei_scale14: float | None = None
    note: str = ""
    scale_note: str = ""
    #: Whether ``PARM_FILE_NAME`` and ``CONN_FILE_NAME`` are normally the *same*
    #: file.  For AMBER the prmtop holds the parameters *and* the connectivity,
    #: so every one of CP2K's own AMBER tests points both keywords at it; for
    #: GROMOS the ``.top`` is the same story.  CHARMM is the opposite: the PSF
    #: carries the connectivity and a separate ``.pot`` the parameters.
    same_file: bool = False


MM_ROUTES: tuple[MmRoute, ...] = (
    MmRoute(
        "AMBER", "AMBER（prmtop + inpcrd/CRD）", "AMBER", "AMBER", "CRD", True, True,
        0.5, 0.8333333333333333,
        note="AMBER 拓扑（prmtop，%VERSION / %FLAG 那种）在 &TOPOLOGY 里读连接性",
        scale_note="PARM94 及以后是 0.5/0.8333；PARM91 用 0.5/0.5",
        same_file=True),
    MmRoute(
        "CHARMM", "CHARMM（PSF + .pot/.prm）", "CHM", "PSF", "PDB", True, True,
        1.0, 1.0,
        note="CHARMM/XPLOR 格式的 PSF 拓扑 + 参数文件",
        scale_note="CHARMM 不做 1-4 缩放（CP2K 默认的 EI_SCALE14 0.0 会丢掉 1-4 静电）",
        same_file=False),
    MmRoute(
        "GROMOS96", "GROMOS96（.top/.gtop + .g96）", "G96", "G96", "G96", True, True,
        None, None,
        note="GROMOS96 拓扑与坐标",
        scale_note="1-4 缩放随参数集而变，CP2K 默认是 VDW 1.0 / EI 0.0，务必自己核对",
        same_file=True),
    MmRoute(
        "GROMOS87", "GROMOS87（.top + .g96）", "G87", "G87", "G96", True, True,
        None, None,
        note="GROMOS87 拓扑（老格式）+ GROMOS96 坐标",
        scale_note="1-4 缩放随参数集而变，CP2K 默认是 VDW 1.0 / EI 0.0，务必自己核对",
        same_file=True),
    MmRoute(
        "IN_INPUT", "参数写在输入里（PARMTYPE OFF）", "OFF", "GENERATE", "XYZ",
        False, False, None, None,
        note="力场参数直接写在 &FORCEFIELD 里：&CHARGE/&BOND/&BEND/&NONBONDED",
        scale_note="", same_file=False),
    MmRoute(
        "PAIR", "只有对势（ε/σ/RCUT，Ar 那种）", "OFF", "GENERATE", "XYZ",
        False, False, None, None,
        note="纯对势体系：只给 &CHARGE 和 &NONBONDED &LENNARD-JONES，没有键角项",
        scale_note="", same_file=False),
)
MM_ROUTE_BY_KEY = {route.key: route for route in MM_ROUTES}
MM_ROUTE_KEYS = tuple(route.key for route in MM_ROUTES)

#: Routes whose force field lives in files, i.e. the ones where the coordinates
#: come from an external coordinate file rather than the ``&COORD`` section.
MM_FILE_ROUTES = tuple(r.key for r in MM_ROUTES if r.conn_file)

#: Coordinate file suffix -> ``&TOPOLOGY COORDINATE`` value.  CP2K reads
#: ``COORDINATE CRD`` as AMBER's fixed-width inpcrd/restart format and ``PDB`` as
#: a PDB, so the suffix is a reliable default and a wrong pair is a real error;
#: ``input_cp2k_subsys.F`` lists the formats themselves.
COORD_SUFFIX_FORMAT = {
    ".pdb": "PDB", ".ent": "PDB",
    ".crd": "CRD", ".inpcrd": "CRD", ".rst": "CRD", ".restart": "CRD",
    ".restrt": "CRD",
    ".xyz": "XYZ",
    ".g96": "G96",
    ".cif": "CIF",
    ".xtl": "XTL",
}

#: The four coordinate formats CP2K is willing to read a *cell* from when
#: ``&CELL`` says nothing (``cell_methods.F``, "case 4": it maps
#: ``COORD_FILE_NAME`` onto ``CELL_FILE_NAME`` for CIF/CP2K/PDB/EXTXYZ only and
#: every other format ends in ``cp_abort``).  An AMBER inpcrd is *not* one of
#: them - ``topology_amber.F`` says so out loud ("BOX information found in CRD
#: file. They will be ignored. Please provide the BOX information directly from
#: the main CP2K input!").
CELL_FROM_COORD_FORMATS = ("CIF", "CP2K", "PDB", "XYZ")


def mm_path_for(spec: "Spec", path: str | None) -> str | None:
    """How a force-field file name goes into the input.

    The files of a classical run - topology, parameters, coordinates - are
    normally copied next to the input, and the job usually runs on a cluster
    whose paths have nothing to do with the machine that generated the input.
    Writing ``E:/desktop/...`` there would simply not be found, so the default
    (``mm_path_style="name"``) writes the bare file name.  ``"absolute"`` keeps
    the path as given, and ``"auto"`` writes the bare name only when the file
    really sits in the directory the input goes to.
    """
    if not path:
        return None
    style = (spec.mm_path_style or "name").lower()
    if style == "absolute":
        return _posix(path)
    if style == "name":
        return Path(path).name
    outdir = spec.output_dir
    if outdir:
        try:
            if Path(path).resolve().parent == Path(outdir).resolve():
                return Path(path).name
        except OSError:                    # pragma: no cover - odd path
            pass
    return _posix(path)


def conn_file_for(spec: "Spec", route: MmRoute | None = None) -> str | None:
    """The topology file to write, or ``None``.

    For AMBER and GROMOS the topology file *is* the parameter file (the prmtop
    holds both), so when the user gave only ``PARM_FILE_NAME`` the same path is
    used for ``CONN_FILE_NAME`` as well.  That is what CP2K's own AMBER inputs do
    - every one of them points both keywords at the same ``.top``/prmtop - and it
    saves naming the file twice.  CHARMM is deliberately *not* inherited: there
    the PSF (connectivity) and the ``.pot`` (parameters) are different files, and
    guessing would produce a meaningless path instead of a clear "no topology".
    """
    route = route or mm_route(spec)
    if spec.mm_conn_file:
        return spec.mm_conn_file
    if route.same_file and spec.mm_parm_file:
        return spec.mm_parm_file
    return None


#: File suffixes that can hold a *CP2K input* - with an inline ``&COORD`` - rather
#: than a coordinate file.  ``.restart`` is genuinely ambiguous: AMBER's
#: ``inpcrd``/``restart`` is a CRD coordinate file, while CP2K's
#: ``<PROJECT>-1.restart`` is a complete input, so the suffix alone cannot decide
#: and the content has to (:func:`reads_as_cp2k_input`).
CP2K_INPUT_SUFFIXES = (".inp", ".restart")


def reads_as_cp2k_input(path: str | Path | None) -> bool:
    """Whether ``path`` is a CP2K input (``&COORD``) instead of a coordinate file.

    Matters because CP2K writes its restart as a **complete input**: the writer
    unsets ``COORD_FILE_NAME``/``COORD_FILE_FORMAT`` and inlines the current
    coordinates into ``&COORD`` (``input_restart_force_eval.F``: *"Remove
    COORD_FILE_NAME and COORD_FILE_FORMAT keywords from restart"*).  Handing such
    a file back as ``COORD_FILE_NAME`` would make CP2K parse a CP2K input as an
    AMBER CRD file.
    """
    if not path:
        return False
    file = Path(path)
    suffix = file.suffix.lower()
    if suffix not in CP2K_INPUT_SUFFIXES:
        return False
    try:
        head = file.read_text(encoding="utf-8", errors="ignore")[:65536]
    except OSError:
        # not readable here: `.inp` is unambiguous, `.restart` is not
        return suffix == ".inp"
    upper = head.upper()
    return ("&COORD" in upper or "&FORCE_EVAL" in upper or "&GLOBAL" in upper)


def coordinates_are_inline(spec: "Spec") -> bool:
    """Whether the loaded structure *is* a CP2K input (so ``&COORD`` is written).

    A restart file read as the structure brings the atoms with it, exactly the
    way CP2K's own restart does - so ``COORD_FILE_NAME`` has nothing left to do
    and the interface greys that row out.
    """
    source = getattr(spec.structure, "source", None) if spec.structure else None
    return reads_as_cp2k_input(source)


def coordinate_source_warnings(spec: "Spec", structure: Structure | None) -> list[str]:
    """``&COORD`` and a coordinate file are exclusive: say what that implies.

    A coordinate **file** carries its own positions, so when one is given the
    input gets ``COORD_FILE_NAME`` and *no* ``&COORD`` - but ``&CELL`` is still
    written from the loaded structure.  Two files from two runs therefore give
    positions from one and the box from the other, and CP2K never checks that
    they belong together:

    * ``topology.F`` (``coordinate_control``) reads the file first and then calls
      ``read_atoms_input(overwrite=.TRUE.)``, i.e. a ``&COORD`` block would
      **overwrite** the file's coordinates (with the warning *"Overwriting
      coordinates. Active coordinates read from &COORD section."*) - the opposite
      of what one would guess, which is exactly why the tool never writes both;
    * ``cell_methods.F`` gives the box the opposite precedence: ``CELL_FILE_NAME``
      > ``ABC``/``ALPHA_BETA_GAMMA`` > ``A``/``B``/``C`` > *only then*
      ``TOPOLOGY/COORD_FILE_NAME``.  Since the structure's cell is written as
      ``&CELL ABC``, the coordinate file's own box never wins.

    So this checks the two things CP2K leaves to the user: the atom count and the
    box.
    """
    if structure is None or not spec.mm_coord_file or not is_classical(spec):
        return []
    source = getattr(structure, "source", None)
    coord_file = str(spec.mm_coord_file)
    same_file = bool(source) and Path(source) == Path(coord_file)
    restart_like = reads_as_cp2k_input(source)
    try:
        from .mmcheck import read_coordinates

        info = read_coordinates(coord_file, spec.mm_coord_format)
    except (OSError, ValueError):
        # unreadable / unknown format: the validator reports that on its own
        info = None

    out: list[str] = []
    atoms = len(structure.atoms)
    if info is not None and info.natom != atoms:
        out.append(
            f"坐标文件 {Path(coord_file).name} 里是 {info.natom} 个原子，"
            f"而载入的结构（{Path(source).name if source else '结构'}）是 {atoms} 个："
            "生成的输入用**文件里**的坐标、不写 `&COORD`，两份数目不一致时 CP2K "
            "读坐标就会中止（`topology.F` 里 natoms 和粒子数组有断言）。"
            "restart 和 inpcrd 往往不是同一次运行的产物，先确认它们是一套体系")
    elif restart_like and not same_file:
        out.append(
            f"载入的结构是 CP2K 的输入/restart（{Path(source).name}），"
            f"同时又指定了坐标文件 {Path(coord_file).name}：生成的输入里"
            "**坐标用那个文件**（不写 `&COORD`），而 `&CELL` 用的是 restart 里的晶胞 "
            "——位置和盒子来自两份不同的东西，CP2K 不会检查它们是不是一套。"
            "想用 restart 里的坐标（接力通常就是）就把坐标文件那一格清空，"
            "工具会把坐标写成 `&COORD`；想用文件里的坐标，就让结构文件也换成"
            "同一套的坐标文件（比如同一个 packmol/leap 出来的 pdb）")
    if info is not None and info.box and structure.cell is not None and not same_file:
        lengths = structure.cell.lengths()
        if any(abs(a - b) > 0.02 * max(a, b, 1.0)
               for a, b in zip(info.box, lengths)):
            out.append(
                "坐标文件里的盒子 "
                + " ".join(f"{value:.3f}" for value in info.box)
                + " Å 与输入里 `&CELL` 的 "
                + " ".join(f"{value:.3f}" for value in lengths)
                + " Å 不一致：CP2K 的优先级是 `&CELL` 高于坐标文件里的 box"
                "（cell_methods.F：CELL_FILE_NAME > ABC > A/B/C > COORD_FILE_NAME），"
                "所以盒子取的是结构文件那个、坐标取的是文件里那个")
    return out


def coord_format_for(spec: "Spec", route: MmRoute | None = None) -> str:
    """The ``&TOPOLOGY COORDINATE`` value to write.

    An explicit choice wins; otherwise the file's suffix decides, and only when
    that says nothing does the route's own default apply.  ``--mm-coord-file
    mix.pdb`` therefore writes ``COORDINATE PDB`` and not the AMBER route's
    ``CRD`` - the two are different readers (``read_coordinate_pdb`` vs
    ``read_coordinate_crd``) and mixing them up is a run that dies on startup.
    """
    explicit = (spec.mm_coord_format or "").strip()
    if explicit:
        return explicit.upper()
    path = spec.mm_coord_file
    if path:
        guessed = COORD_SUFFIX_FORMAT.get(Path(path).suffix.lower())
        if guessed:
            return guessed
    return (route or mm_route(spec)).coord_format


def mm_route(spec: "Spec") -> MmRoute:
    """The classical route of ``spec``, falling back to AMBER for a typo."""
    return MM_ROUTE_BY_KEY.get((spec.mm_route or "AMBER").upper(),
                               MM_ROUTE_BY_KEY["AMBER"])


def is_classical(spec: "Spec") -> bool:
    """Whether this spec describes a classical (force-field) calculation."""
    return (spec.method or "QUICKSTEP").upper() in ("FIST", "MM")


@dataclass
class MmMolecule:
    """One entry of a ``&TOPOLOGY/&MOL_SET/&MOLECULE`` block.

    This is the route CP2K offers for a system built from several molecules that
    each come with their own topology file (``tests/Fist/regtest-14/lamol.inp``).
    """

    conn_file: str | None = None
    conn_format: str = "AMBER"
    nmol: int = 1


@dataclass
class MmLennardJones:
    """One ``&NONBONDED/&LENNARD-JONES`` block.

    ``epsilon`` is in K and ``sigma``/``rcut`` in angstrom - that is the unit CP2K
    declares for these keywords (``unit_str="K_e"`` / ``"angstrom"``).
    """

    atoms: list[str] = field(default_factory=list)
    epsilon: float | None = None
    sigma: float | None = None
    rcut: float | None = None


def lorentz_berthelot(per_element: dict[str, tuple[float, float]]
                      ) -> list[MmLennardJones]:
    """Mixing rules sigma_ij = (sigma_i+sigma_j)/2, epsilon_ij = sqrt(ei*ej)."""
    elements = sorted(per_element)
    out: list[MmLennardJones] = []
    for i, first in enumerate(elements):
        for second in elements[i:]:
            eps1, sig1 = per_element[first]
            eps2, sig2 = per_element[second]
            out.append(MmLennardJones([first, second],
                                      (eps1 * eps2) ** 0.5,
                                      (sig1 + sig2) / 2.0))
    return out


#: ``&LENNARD-JONES RCUT`` default, in angstrom (CP2K's own default is 10).
LJ_DEFAULT_RCUT = 10.0


def lennard_jones_pairs(explicit: list[MmLennardJones],
                        per_element: dict[str, tuple[float, float]],
                        rcut: float | None = None) -> list[MmLennardJones]:
    """The full pair list: the explicit pairs first, then the mixed ones.

    Both faces of the tool build their pair list here so that the command line
    and the graphical interface cannot disagree about the order (which would show
    up as a different input file for the same settings).
    """
    default = rcut if rcut is not None else LJ_DEFAULT_RCUT
    out = [MmLennardJones(list(pair.atoms), pair.epsilon, pair.sigma,
                          pair.rcut if pair.rcut is not None else default)
           for pair in explicit]
    out.extend(MmLennardJones(list(pair.atoms), pair.epsilon, pair.sigma, default)
               for pair in lorentz_berthelot(per_element))
    return out


@dataclass
class Spec:
    """Everything the builder needs to know."""

    task: str = "energy"
    #: ``FORCE_EVAL METHOD``: ``QUICKSTEP`` (DFT) or ``FIST`` (classical force
    #: field).  With ``FIST`` the MD task is a classical MD, not an AIMD.
    method: str = "QUICKSTEP"
    project: str | None = None
    structure: Structure | None = None
    data_dir: str | None = None

    # system
    charge: int = 0
    multiplicity: int = 1
    uks: bool | None = None
    periodic: str | None = None
    vacuum: float | None = None
    cell: Cell | None = None
    #: &DFT/&POISSON POISSON_SOLVER; None = the tool's automatic choice (nothing
    #: is written for a 3-D periodic cell, MT/WAVELET otherwise)
    poisson_solver: str | None = None

    # electronic structure
    functional: str = "PBE"
    dispersion: str | None = None
    cutoff: float | None = None
    rel_cutoff: float = 60.0
    ngrids: int | None = None
    qs_method: str = "GPW"
    eps_default: float = 1.0e-10
    #: ``&QS EXTRAPOLATION``: ``None`` = automatic (ASPC while the geometry/MD
    #: steps are running, USE_GUESS otherwise), which is what makes a long MD
    #: cheap; set it explicitly to override CP2K's own default.
    extrapolation: str | None = None
    basis: str | None = None
    potential: str | None = None
    basis_target: str = "auto"
    basis_file: str | None = None
    potential_file: str | None = None
    data_path_style: str = "name"

    scf: str = "auto"                 # auto | OT | DIAGONALIZATION
    eps_scf: float | None = None
    max_scf: int = 30
    #: inner SCF iterations per electronic step; None = the tool's default (20 on
    #: the OT route, nothing written on the diagonalisation route)
    outer_max_scf: int | None = None
    #: convergence of the outer loop; None = same threshold as the inner one
    outer_eps_scf: float | None = None
    added_mos: int | None = None
    smearing: bool | None = None
    electronic_temperature: float = 300.0
    smear_method: str = "FERMI_DIRAC"        # &SMEAR METHOD

    # SCF route details: OT (the default route) and the diagonalisation route
    ot_minimizer: str = "DIIS"               # SD | CG | DIIS | BROYDEN
    ot_preconditioner: str = "FULL_SINGLE_INVERSE"
    ot_algorithm: str = "STRICT"             # STRICT | IRAC
    ot_linesearch: str = "2PNT"              # ADAPT | NONE | 2PNT | 3PNT | GOLD
    ot_stepsize: float | None = None         # negative/None = CP2K decides
    ot_energy_gap: float | None = None       # only used by FULL_ALL / FULL_SINGLE_INVERSE
    ot_rotation: bool | None = None          # None = do not write (CP2K default F)
    ot_safe_diis: bool | None = None         # None = do not write (CP2K default T)
    outer_optimizer: str | None = None       # &OUTER_SCF OPTIMIZER (None = CP2K default)
    diag_algorithm: str = "STANDARD"         # &DIAGONALIZATION ALGORITHM
    level_shift: float | None = None         # only used on the diagonalisation route
    mixing_method: str = "BROYDEN_MIXING"    # &MIXING METHOD

    # molecular dynamics
    ensemble: str = "NVT"
    temperature: float = 300.0
    timestep: float = 0.5
    steps: int = 5000
    thermostat: str = "NOSE"
    #: There is deliberately no "barostat type": ``&BAROSTAT`` has only
    #: PRESSURE / TEMPERATURE / TEMP_TOL / TIMECON / VIRIAL (manual 2026.2,
    #: ``MOTION/MD/BAROSTAT``) - the algorithm follows from the ensemble
    #: (NPT_I / NPT_F / MSST / NPE_*), and its thermostat is a subsection.
    pressure: float = 1.0
    seed: int | None = None

    # heating / cooling (all four are CP2K's own `&MD` keywords)
    #: ``&MD ANNEALING``: multiply **all velocities** by this factor after every
    #: step.  Not a thermostat - it adds/removes kinetic energy directly, so it
    #: only exists for ensembles without a particle thermostat (NVE, NPE_*);
    #: CP2K *aborts* when it is combined with NVT/NPT.
    annealing: float | None = None
    #: ``&MD ANNEALING_CELL``: the same for the cell's velocities (NPE_I/NPE_F).
    annealing_cell: float | None = None
    #: ``&MD TEMPERATURE_ANNEALING``: multiply the **Langevin target
    #: temperature** by this factor after every step.  This is the one that
    #: heats/cools under a thermostat, and it is applied only in the Langevin
    #: integrator (``integrator.F``, ``SUBROUTINE langevin``).
    temperature_annealing: float | None = None
    #: ``&MD/&LANGEVIN GAMMA`` in fs^-1.  CP2K's own default is **0**, which is
    #: no friction and no noise - i.e. NVE - so the tool writes its own value
    #: whenever the ensemble is LANGEVIN.  0.01 fs^-1 = 10 ps^-1 couples in
    #: ~100 fs, fast enough to follow a temperature ramp and still weak enough
    #: not to take over the dynamics.
    langevin_gamma: float = 0.01
    #: ``&EXT_RESTART/RESTART_TEMPERATURE_ANNEALING T``: continue a ramp from the
    #: target temperature saved in the restart file instead of the input's
    #: ``TEMPERATURE``.  Only meaningful together with ``temperature_annealing``
    #: and a restart file.
    restart_temperature_annealing: bool = False

    # classical force field (FORCE_EVAL METHOD FIST)
    mm_route: str = "AMBER"                  # | CHARMM | GROMOS96 | GROMOS87 | IN_INPUT | PAIR
    #: How to spell the force-field file names in the input.  ``name`` (default)
    #: writes just the file name: the input is normally generated on one machine
    #: and run on a cluster where the topology/coordinates sit next to it, so a
    #: local absolute path would not be found there.  ``absolute`` keeps the full
    #: path, ``auto`` writes the bare name only when the file really is in the
    #: directory the input goes to.
    mm_path_style: str = "name"
    #: Where the input will be written.  Only ``mm_path_style="auto"`` needs it -
    #: it is what decides whether a file counts as "next to the input".
    output_dir: str | None = None
    mm_parm_file: str | None = None          # &FORCEFIELD PARM_FILE_NAME
    mm_conn_file: str | None = None          # &TOPOLOGY CONN_FILE_NAME
    mm_coord_file: str | None = None         # &TOPOLOGY COORD_FILE_NAME
    #: &TOPOLOGY COORDINATE; None = derive it from the coordinate file's suffix
    #: (falling back to the route's own format when the suffix says nothing)
    mm_coord_format: str | None = None
    #: &TOPOLOGY CONNECTIVITY MOL_SET: one &MOLECULE per component
    mm_molecules: list[MmMolecule] = field(default_factory=list)
    #: IN_INPUT: a file whose contents are placed inside &FORCEFIELD
    mm_ff_file: str | None = None
    mm_charges: list[tuple[str, float]] = field(default_factory=list)
    mm_lj: list[MmLennardJones] = field(default_factory=list)
    #: PAIR: per-element (epsilon [K], sigma [A]); pairs are mixed
    mm_element_lj: dict[str, tuple[float, float]] = field(default_factory=dict)
    mm_vdw_scale14: float | None = None      # None = the route's own value
    mm_ei_scale14: float | None = None
    mm_shift_cutoff: bool | None = None      # CP2K default T
    mm_multiple_potential: bool | None = None
    mm_ignore_missing_params: bool | None = None
    mm_ewald: str | None = None              # None = SPME (NONE when not periodic)
    mm_ewald_alpha: float | None = None      # None = CP2K derives it from EWALD_ACCURACY
    mm_ewald_gmax: int | None = None
    mm_o_spline: int | None = None           # SPME/PME interpolation order
    mm_rcut: float | None = None             # &EWALD RCUT (real-space cutoff, A)
    mm_spline_emax: float | None = None      # &FORCEFIELD &SPLINE EMAX_SPLINE (hartree)
    mm_verlet_skin: float | None = None      # &MM &NEIGHBOR_LISTS VERLET_SKIN (A)
    mm_stress_tensor: bool | None = None     # None = ANALYTICAL when NPT, else off

    # optimisation
    optimizer: str = "BFGS"
    max_iter: int = 200
    keep_angles: bool = False
    external_pressure: float = 0.0

    # vibrations
    intensities: bool = True
    thermochemistry: bool = False

    # restarting
    #: CP2K falls back to an ATOMIC guess by itself when the restart file is
    #: missing (qs_initial_guess.F warns and keeps going), so RESTART is the
    #: safe default: continue a previous run when there is something to read.
    scf_guess: str = "RESTART"           # ATOMIC | RESTART | HISTORY_RESTART | ...
    wfn_restart_file: str | None = None  # WFN_RESTART_FILE_NAME
    wfn_restart_print: bool = True       # &SCF/&PRINT/&RESTART
    #: ``&EACH`` interval of the wave-function restart file.  0 = nothing *during*
    #: the run (only the ADD_LAST dump).
    wfn_restart_every: int | None = 0
    #: Which ``&EACH`` level that interval counts.  ``None`` = pick by task: MD for
    #: an MD run, GEO_OPT/CELL_OPT for the optimisers, QS_SCF for a single point.
    #: The distinction matters - ``QS_SCF`` counts the SCF iterations *inside one*
    #: force evaluation, so "every 500 SCF steps" in an MD run means "only when a
    #: single MD step happens to need 500 SCF iterations", i.e. the interval does
    #: nothing and ADD_LAST is the only thing that writes the file.
    wfn_restart_level: str | None = None
    #: &EACH's companion: also dump at the last iteration (CP2K's own default)
    wfn_restart_add_last: bool = True
    wfn_backup_copies: int | None = None  # &SCF/&PRINT/&RESTART BACKUP_COPIES
    ext_restart_file: str | None = None  # &EXT_RESTART / RESTART_FILE_NAME
    ext_restart_default: bool = True     # RESTART_DEFAULT
    #: Which quantities to take from ``ext_restart_file``.  ``None`` = everything
    #: (``RESTART_DEFAULT T``, the CP2K default).  A tuple of short names means
    #: "take only these": the writer emits ``RESTART_DEFAULT F`` plus one
    #: ``RESTART_<NAME> T`` per entry, which is exactly what chaining two tasks
    #: needs - e.g. ``("pos",)`` hands an optimised geometry to an MD run without
    #: dragging in the optimiser's step counter or looking for velocities a
    #: geometry optimisation never wrote.  See :data:`EXT_RESTART_TAKES`.
    ext_restart_take: tuple[str, ...] | None = None
    #: Metadynamics: the CV definitions go to ``&SUBSYS/&COLVAR`` and the bias to
    #: ``&MOTION/&FREE_ENERGY/&METADYN``.  ``None`` = no metadynamics; only an MD
    #: run writes those sections (see :mod:`cp2kbuild.metadyn`).
    metadyn: "Metadyn | None" = None
    motion_restart_print: bool = True    # &MOTION/&PRINT/&RESTART
    #: &EACH/<task>: every N MD / GEO_OPT / CELL_OPT steps (CP2K's default is 20)
    restart_every: int | None = 20
    #: also dump at the last iteration (CP2K's own default)
    restart_add_last: bool = True
    restart_backups: int | None = None   # BACKUP_COPIES

    # SCF robustness / convergence
    ignore_convergence_failure: bool = False
    mixing_alpha: float = 0.4
    mixing_nbuffer: int = 8
    mixing_beta: float | None = None

    # coordinate representation
    scaled_coords: bool = False
    #: **Deuterate hydrogen**: write ``MASS 2.0141017781`` in ``&KIND H``.  Within
    #: the Born-Oppenheimer approximation the potential energy surface depends on
    #: the nuclear charge, not the mass, so this changes no chemistry and no
    #: electronic structure - but the fastest X-H stretch (O-H ~3400 cm^-1, i.e. a
    #: ~10 fs period) becomes ~14 fs, which is what lets an AIMD run use a 1.0 fs
    #: time step instead of 0.5 fs (half the SCF cycles for the same trajectory).
    #: The result is the *deuterated* system: static structure (RDF, coordination)
    #: is essentially unchanged, dynamics (diffusion, spectra, rates) are not.
    h_to_d: bool = False

    # MD coupling constants (fs)
    thermostat_timecon: float = 100.0
    barostat_timecon: float = 500.0
    #: ``&MOTION/&MD/&THERMOSTAT REGION``: how many thermostats CP2K applies.
    #: ``GLOBAL`` (CP2K's own default) is one thermostat for the whole system,
    #: ``MOLECULE`` one per molecule kind - which is what makes the per-region
    #: temperature print below worth looking at - and ``MASSIVE`` one per degree
    #: of freedom (``input_cp2k_thermostats.F``, ``THERMOSTAT REGION``).
    thermostat_region: str = "GLOBAL"

    # ---- what an MD run prints while it runs --------------------------------
    #: ``&MOTION/&MD/&PRINT/&TEMP_KIND`` (with the keyword ``&MD TEMP_KIND``):
    #: CP2K computes the temperature of every atomic kind separately and writes
    #: it to ``<project>-1.temp`` - the way to see whether the two halves of a
    #: mixture are actually at the same temperature (``input_cp2k_md.F``:
    #: "Controls the output of the temperature computed separately for each
    #: kind"; the extension comes from ``md_energies.F``).
    md_temp_kind_print: bool = False
    md_temp_kind_every: int = 10
    #: ``&MOTION/&MD/&PRINT/&ENERGY``: the ``<project>-1.ener`` file, one line per
    #: step: step / time / Kin / **Temp[K]** / Pot / Cons Qty / UsedTime.  CP2K
    #: already writes it at ``PRINT_LEVEL >= LOW`` (the tool's default is MEDIUM),
    #: so this switch is how the *interval* and the file name are set - and how
    #: the temperature history is kept when the interval would otherwise be
    #: every single step.
    md_energy_print: bool = False
    md_energy_every: int = 1
    md_energy_file: str = ""
    #: ``&MOTION/&MD/&THERMOSTAT/&PRINT/&TEMPERATURE``: the temperature of every
    #: thermostat region, one ``target actual`` column pair per region
    #: (``input_cp2k_thermostats.F``).  With ``REGION GLOBAL`` that is the global
    #: temperature; with ``MOLECULE`` it is one row per molecule kind.
    thermostat_temp_print: bool = False
    thermostat_temp_every: int = 10

    # electronic structure analysis
    kpath: str | None = None
    lattice: str | None = None
    npoints: int = 40
    kpoint_grid: tuple[int, int, int] | None = None
    kpoint_scheme: str | None = None
    #: target spacing (angstrom) for the *automatic* Monkhorst-Pack grid: the
    #: grid is the smallest one whose reciprocal spacing is at most this.  Used
    #: only when ``kpoint_grid`` is not given.
    kpoint_spacing: float | None = None
    #: ``&KPOINTS KPOINT x y z w`` lines: an explicit (GENERAL) k-point list
    kpoint_points: list[tuple[float, float, float, float]] = field(default_factory=list)
    #: ``&KPOINTS UNITS``: how the explicit points are given
    kpoint_units: str = "B_VECTOR"           # B_VECTOR | CART_ANGSTROM | CART_BOHR
    #: ``&KPOINTS GAMMA_CENTERED T``: shift the MP/MacDonald mesh onto Gamma
    kpoint_gamma_centered: bool = False
    #: ``&KPOINTS FULL_GRID T``: use the full mesh instead of the symmetry
    #: reduced one (needed when every k-point must be present, e.g. for
    #: Wannier/transport; costs proportionally more)
    kpoint_full_grid: bool = False
    #: ``&KPOINTS SYMMETRY`` - the tool writes ``T`` (CP2K's own default is F)
    kpoint_symmetry: bool = True
    #: ``&KPOINTS WAVEFUNCTIONS`` - the tool writes REAL (CP2K's default COMPLEX)
    kpoint_wavefunctions: str = "REAL"       # REAL | COMPLEX
    #: ``&KPOINTS VERBOSE T``: list the k-points in the output
    kpoint_verbose: bool = False
    pdos_ldos: list[list[int]] = field(default_factory=list)
    pdos_nlumo: int | None = None
    pdos_broaden: float | None = None        # FWHM in eV; None = no &CURVE block
    pdos_broaden_type: str = "GAUSSIAN"      # 2026.2: GAUSSIAN/LORENTZIAN/PSEUDO_VOIGT

    # nudged elastic band
    band_type: str = "CI-NEB"
    replicas: int = 0
    replica_files: list[str] = field(default_factory=list)
    k_spring: float = 0.05

    # output
    print_level: str = "MEDIUM"
    walltime: int | None = None
    print_forces: bool = True
    print_mulliken: bool = False
    print_dipole: bool = True
    #: ``&DFT/&PRINT/&MO_MOLDEN``: molecular orbitals in Molden format, for a
    #: viewer (Molden, Avogadro, Jmol, ...).  CP2K only writes it when the run
    #: has **no k-points** (``qs_scf_post_gpw.F`` guards the call with
    #: ``IF (.NOT. do_kpoints)``), so the validator says so.
    print_molden: bool = False
    molden_file: str | None = None           # FILENAME (None = CP2K's "MOS")
    #: NLUMO: 0 = occupied only, -1 = every available virtual, N = the first N
    molden_nlumo: int = 0
    #: UNIT for the coordinates/cell *in the Molden file*; CP2K's own default is
    #: BOHR, the tool writes ANGSTROM unless asked otherwise
    molden_unit: str = "ANGSTROM"            # ANGSTROM | BOHR
    molden_gto_kind: str = "SPHERICAL"       # SPHERICAL | CARTESIAN
    molden_write_pseudo: bool = False
    molden_write_cell: bool = False
    #: ``&VIBRATIONAL_ANALYSIS/&PRINT/&MOLDEN_VIB``: the normal modes in Molden
    #: format (freq task)
    molden_vib: bool = True
    trajectory_stride: int = 10
    #: ``&DFT/&PRINT/&MO_CUBES``: HOMO/LUMO (or any orbital) as a Gaussian cube,
    #: for VMD/VESTA/gOpenMol - the quickest way to *see* a frontier orbital
    print_mo_cubes: bool = False
    mo_cubes_nhomo: int = 1
    mo_cubes_nlumo: int = 0
    #: cube grid stride ("2 2 2" is CP2K's default; 1 1 1 is the full grid)
    mo_cubes_stride: str | None = None
    mo_cubes_filename: str | None = None
    #: ``&DFT/&PRINT/&TOT_DENSITY_CUBE``: the total electron density
    print_density_cube: bool = False
    density_cube_stride: str | None = None
    density_cube_filename: str | None = None
    #: ``&MOTION/&CONSTRAINT/&FIXED_ATOMS``: freeze atoms (LIST 1 3 5..8),
    #: honoured by GEO_OPT/CELL_OPT and MD
    fixed_atoms: str | None = None
    fixed_atoms_components: str = "XYZ"      # X | Y | Z | XY | XZ | YZ | XYZ


@dataclass
class BuildResult:
    sections: list[Section]
    setup: Setup
    notes: list[str]
    run_type: str
    warnings: list[str] = field(default_factory=list)
    #: findings the generator is *sure* make CP2K abort (no coordinates at all,
    #: for instance) - the interface shows them as errors, not as warnings
    errors: list[str] = field(default_factory=list)
    #: validator diagnostic codes this build has already reported itself.  Some
    #: findings are visible to both sides (the generator knows a force-field
    #: route has no ``PARM_FILE_NAME`` *and* the validator has a rule for it), and
    #: the same sentence twice - once as "警告", once as "错误" - is worse than
    #: either one alone.  The merged report drops the validator's copy.
    covered_codes: set[str] = field(default_factory=set)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def suggest_cutoff(basis_names: list[str]) -> float:
    best = 400.0
    for name in basis_names:
        upper = name.upper()
        for family, value in CUTOFF_BY_FAMILY:
            if family in upper:
                best = max(best, value)
                break
    return best


def suggest_kpoint_grid(cell: Cell | None, target_spacing: float = 20.0
                        ) -> tuple[int, int, int]:
    """A Γ-centred grid with roughly one k-point every ``target_spacing`` angstrom."""
    if cell is None:
        return (1, 1, 1)
    lengths = cell.lengths()
    grid = []
    for length in lengths:
        n = int(round(target_spacing / max(length, 1e-3)))
        grid.append(max(1, min(8, n)))
    return tuple(grid)  # type: ignore[return-value]


def normalise_dispersion(value: str | None) -> str | None:
    if not value:
        return None
    return DISPERSION_ALIASES.get(value.lower().strip(), value.upper())


def structure_stem(spec: Spec) -> str | None:
    """A file-name-friendly stem from the structure's source file."""
    if spec.structure is None or not spec.structure.source:
        return None
    import re
    from pathlib import Path

    stem = Path(spec.structure.source).stem
    clean = re.sub(r"[^A-Za-z0-9_.+-]", "_", stem)
    return clean or None


def _project_name(spec: Spec) -> str:
    """``PROJECT_NAME``: an explicit name, else ``cp2k``.

    A *stable* default is what makes restart files inheritable: CP2K looks for
    ``<PROJECT_NAME>-RESTART.wfn`` and ``<PROJECT_NAME>-1.restart``, so keeping
    the name fixed across runs means this run picks up the previous one (this is
    also the convention in the group templates, where the name is ``cp2k`` for
    every system).  Use ``--project`` / the GUI field to run several systems in
    one directory without them sharing restart files.
    """
    if spec.project:
        return spec.project
    return "cp2k"


def _target_kind(spec: Spec, structure: Structure | None) -> str:
    if spec.basis_target != "auto":
        return spec.basis_target
    if structure is not None and structure.is_periodic():
        return "solid"
    return "molecule"


def prepare_structure(structure: Structure | None, spec: Spec) -> Structure | None:
    """Apply ``vacuum`` / ``cell`` / ``periodic`` from the spec to a structure.

    This runs inside :func:`build`, so the library API and the CLI behave
    identically.
    """
    if structure is None:
        # A file-based force field takes its atoms *and* its box from files, so
        # no structure file is needed at all - but the box still has to be
        # written, and that comes from the &CELL the user typed.  An empty
        # structure carrying just the cell is all ``fist_subsys_section`` wants
        # (it writes neither &COORD nor &KIND for those routes).
        if spec.cell is None or not (is_classical(spec) and mm_route(spec).conn_file):
            return None
        return Structure(cell=spec.cell, periodic=spec.periodic or "XYZ",
                         comment="cell only: the atoms come from COORD_FILE_NAME")
    structure = structure.copy()      # never edit the caller's object
    # A force field read from files brings its own coordinates *and* box: padding
    # it with vacuum would quietly replace the cell the topology was built for.
    file_force_field = is_classical(spec) and mm_route(spec).conn_file
    if spec.vacuum and not file_force_field:
        if structure.cell is None or not structure.is_periodic():
            structure.cell = structure.new_box(spec.vacuum)
            structure.center_in_cell()
            structure.periodic = "NONE"
    if spec.cell is not None:
        structure.cell = spec.cell
        structure.periodic = structure.periodic or "XYZ"
    if spec.periodic:
        structure.periodic = spec.periodic
    if structure.cell is not None and structure.periodic is None:
        structure.periodic = "XYZ"
    return structure


# --------------------------------------------------------------------------- #
# sections
# --------------------------------------------------------------------------- #
def global_section(spec: Spec, run_type: str) -> Section:
    section = Section("GLOBAL", comment="global run settings")
    section.set("PROJECT_NAME", _project_name(spec))
    section.set("RUN_TYPE", run_type,
                comment=f"see {MANUAL}CP2K_INPUT/GLOBAL/RUN_TYPE.html")
    section.set("PRINT_LEVEL", spec.print_level.upper())
    if spec.walltime:
        section.set("WALLTIME", int(spec.walltime))
    if spec.seed is not None:
        section.set("SEED", int(spec.seed),
                    comment=("seed of the global random number generator, so that MD "
                             "initial velocities are reproducible"))
    return section


def dft_section(spec: Spec, setup: Setup, structure: Structure | None,
                elements: list[str], force_diagonalization: bool = False,
                ) -> Section:
    dft = Section("DFT", comment="Quickstep electronic structure")

    for basis_file in setup.basis_files:
        dft.append_keyword("BASIS_SET_FILE_NAME", basis_file)
    for potential_file in setup.potential_files:
        dft.append_keyword("POTENTIAL_FILE_NAME", potential_file)
    if not setup.basis_files:
        dft.set("BASIS_SET_FILE_NAME", "BASIS_MOLOPT")
    if not setup.potential_files:
        dft.set("POTENTIAL_FILE_NAME", "GTH_POTENTIALS")

    if spec.charge:
        dft.set("CHARGE", int(spec.charge),
                comment="total charge of the system")
    uks = spec.uks if spec.uks is not None else spec.multiplicity > 1
    if uks:
        dft.set("UKS", True, comment="unrestricted Kohn-Sham (spin polarised)")
        dft.set("MULTIPLICITY", int(spec.multiplicity))
    elif spec.multiplicity != 1:
        dft.set("MULTIPLICITY", int(spec.multiplicity))

    # ---- multigrid ----
    cutoff = spec.cutoff or suggest_cutoff([a.basis for a in setup.assignments.values()])
    suggested = suggest_cutoff([a.basis for a in setup.assignments.values()])
    mgrid = Section("MGRID", comment=(
        f"plane-wave cutoff of the finest grid; {suggested:g} Ry is the usual "
        f"starting point for this basis set. Converge it with "
        f"`cp2kbuild scan --cutoffs ...` - "
        f"{MANUAL}methods/dft/cutoff.html"))
    mgrid.set("CUTOFF", float(cutoff), unit="Ry")
    mgrid.set("REL_CUTOFF", float(spec.rel_cutoff), unit="Ry")
    if spec.ngrids:
        mgrid.set("NGRIDS", int(spec.ngrids))
    dft.add(mgrid)

    qs = Section("QS")
    qs.set("METHOD", spec.qs_method.upper())
    qs.set("EPS_DEFAULT", float(spec.eps_default))
    extrapolation = (spec.extrapolation or "").upper()
    if extrapolation:
        qs.set("EXTRAPOLATION", extrapolation)
    else:
        qs.set("EXTRAPOLATION", "ASPC" if run_type_needs_extrapolation(spec)
               else "USE_GUESS")
    dft.add(qs)

    dft.add(scf_section(spec, structure, setup, force_diagonalization))

    # ---- Poisson solver ----
    # &POISSON PERIODIC and &CELL PERIODIC are two different settings and CP2K
    # wants them consistent; the solver itself is only valid for some
    # periodicities (see POISSON_SOLVERS).  For a 3-D periodic cell the default
    # (PERIODIC) is right, so nothing is written unless the user asks for a
    # specific solver.
    if structure is not None and structure.cell is not None:
        periodicity = (structure.periodic or "XYZ").upper()
        if periodicity != "XYZ" or spec.poisson_solver:
            dft.add(poisson_section(periodicity, structure, spec.poisson_solver))

    xc = Section("XC")
    functional_block = Section("XC_FUNCTIONAL", spec.functional.upper())
    xc.add(functional_block)
    dispersion = normalise_dispersion(spec.dispersion)
    if dispersion:
        vdw = Section("VDW_POTENTIAL")
        vdw.set("POTENTIAL_TYPE", "PAIR_POTENTIAL")
        pair = Section("PAIR_POTENTIAL")
        pair.set("TYPE", dispersion)
        if dispersion.startswith("DFTD") or dispersion.startswith("DFT-D"):
            pair.set("PARAMETER_FILE_NAME", "dftd3.dat"
                     if dispersion != "DFTD2" else "dftd3.dat")
        pair.set("REFERENCE_FUNCTIONAL", spec.functional.upper())
        vdw.add(pair)
        xc.add(vdw)
    dft.add(xc)

    return dft


def run_type_needs_extrapolation(spec: Spec) -> bool:
    return spec.task in ("md", "geo_opt", "cell_opt", "neb")


def poisson_section(periodicity: str, structure: Structure | None,
                    solver: str | None = None) -> Section:
    """A Poisson solver that matches the cell periodicity.

    ``MT`` (Martyna-Tuckerman) is the right choice for the isolated molecule in
    a box that ``--vacuum`` produces; ``WAVELET`` is cheaper for very large
    cells.  Both need the density to be confined inside the cell.

    ``solver`` overrides the automatic choice; it is the caller's job (and the
    validator's) to keep it compatible with ``periodicity`` - see
    :data:`POISSON_SOLVERS`.
    """
    section = Section("POISSON", comment=(
        "explicit Poisson solver choice; without this section CP2K uses "
        "POISSON_SOLVER PERIODIC, which only fits a 3-D periodic cell"
        if periodicity == "XYZ" else
        "&POISSON PERIODIC must match the cell periodicity; the default 3-D "
        "periodic solver would be inconsistent here"))
    section.set("PERIODIC", periodicity)
    atoms = structure.n_atoms() if structure else 0
    chosen = (solver or ("MT" if atoms <= 200 else "WAVELET")).upper()
    section.set("POISSON_SOLVER", chosen,
                comment=("MT decouples the periodic images; it needs the density to be "
                         "confined inside the cell, so keep --vacuum at 8 A or more"
                         if chosen == "MT" else
                         "WAVELET is cheaper than MT for large cells"
                         if chosen == "WAVELET" else
                         poisson_solver_note(chosen, periodicity)))
    return section


#: Which Poisson solver CP2K accepts for which periodicity.  Straight from the
#: ``enum_desc`` of ``POISSON_SOLVER`` in ``input_cp2k_dft.F`` (2026.2):
#: PERIODIC is 3-D only, MT is 0-D/2-D, MULTIPOLE is 0-D only, ANALYTIC covers
#: 0-D/1-D/2-D, WAVELET and IMPLICIT cover everything.
POISSON_SOLVERS: dict[str, tuple[str, ...]] = {
    "XYZ": ("PERIODIC", "WAVELET", "IMPLICIT"),
    "XY": ("WAVELET", "ANALYTIC", "MT", "IMPLICIT"),
    "XZ": ("WAVELET", "ANALYTIC", "MT", "IMPLICIT"),
    "YZ": ("WAVELET", "ANALYTIC", "MT", "IMPLICIT"),
    "X": ("WAVELET", "ANALYTIC", "IMPLICIT"),
    "Y": ("WAVELET", "ANALYTIC", "IMPLICIT"),
    "Z": ("WAVELET", "ANALYTIC", "IMPLICIT"),
    "NONE": ("MT", "WAVELET", "ANALYTIC", "MULTIPOLE", "IMPLICIT"),
}
"""Periodicity -> solvers the manual says are available for it."""

POISSON_SOLVER_WHY = {
    "PERIODIC": "only available for fully (3D) periodic systems",
    "ANALYTIC": "0D/1D/2D periodic solutions via analytical Green's functions "
                "(slow convergence)",
    "MT": "Martyna-Tuckerman decoupling, 0D and 2D only; wrong if the cell is "
          "smaller than twice the cluster size",
    "MULTIPOLE": "fits the total charge with one gaussian per atom, cluster "
                 "(0D) systems only",
    "WAVELET": "0D/2D/3D; needs the density to vanish on the cell faces",
    "IMPLICIT": "0D/1D/2D/3D",
}


def poisson_solvers_for(periodicity: str) -> tuple[str, ...]:
    """Solvers that are valid for a cell periodicity (``XYZ``, ``NONE``, ...)."""
    return POISSON_SOLVERS.get((periodicity or "XYZ").upper(),
                               POISSON_SOLVERS["XYZ"])


def poisson_solver_note(solver: str, periodicity: str) -> str:
    """One line explaining why a solver fits (or does not fit) a periodicity."""
    solver = solver.upper()
    why = POISSON_SOLVER_WHY.get(solver, "")
    if solver in poisson_solvers_for(periodicity):
        return f"{solver}: {why}"
    valid = ", ".join(poisson_solvers_for(periodicity))
    return (f"{solver} is NOT available for a {periodicity}-periodic cell "
            f"({why}); use one of {valid}")


def _restart_notes(spec: Spec) -> list[str]:
    """Say which files a restart reads, since none of them is in the input."""
    notes: list[str] = []
    project = _project_name(spec)
    if is_classical(spec):
        # a classical run has no wave function: the only restart file that
        # matters is &MOTION/&PRINT/&RESTART, read back by &EXT_RESTART
        if spec.motion_restart_print:
            notes.append(f"restart       : 本次会写 {project}-1.restart，"
                         "用 --restart <该文件> 就能接着算")
        else:
            notes.append("restart       : 没有写 &MOTION/&PRINT/&RESTART，"
                         "这次算完不会留下能续算的文件")
        if spec.ext_restart_file:
            notes.append(f"restart       : &EXT_RESTART 从 {spec.ext_restart_file} 续算"
                         + _take_note(spec))
        return notes
    guess = (spec.scf_guess or "RESTART").upper()
    if guess in ("RESTART", "HISTORY_RESTART"):
        source = spec.wfn_restart_file or f"{project}-RESTART.wfn（k 点用 .kp）"
        notes.append(f"restart       : SCF 初始波函数来自 {source}")
        notes.append("restart       : 文件不存在时 CP2K 自己回退到 ATOMIC 初猜"
                     "（只打一条 warning，不会退出）")
    if spec.wfn_restart_print:
        notes.append(f"restart       : 本次算完会写 {project}-RESTART.wfn，"
                     "下次同名运行直接接着算")
    if spec.ext_restart_file:
        notes.append(f"restart       : &EXT_RESTART 从 {spec.ext_restart_file} 续算"
                     + _take_note(spec))
    return notes


def _take_note(spec: Spec) -> str:
    """How the ``&EXT_RESTART`` block reads that file, in one sentence."""
    take = spec.ext_restart_take
    if take:
        names = "、".join(EXT_RESTART_TAKES[k][0] for k in take
                          if k in EXT_RESTART_TAKES)
        return (f"（接力：只取 {names}，写 `RESTART_DEFAULT F` + 这几项 `T`，"
                "没点到的那些用本次输入里的值）")
    return ("（RESTART_DEFAULT T：位置/速度/计数器都以该文件为准）"
            if spec.ext_restart_default else
            "（只取你显式打开的那些项）")


def restart_summary(path: str) -> tuple[str, set[str]]:
    """Read a CP2K ``-1.restart`` file and say what can be taken from it.

    CP2K writes the restart file as a complete input, so this only has to look at
    which sections are in it: coordinates, velocities, a cell, the MD or
    optimiser counters.  Chaining tasks by hand is where people guess wrong
    (``RESTART_VEL T`` on a geometry optimisation's file, or taking the step
    counter into a fresh MD run), so the tool reads the file and reports.

    Returns ``(text, keys)`` where ``keys`` are the short names of
    :data:`EXT_RESTART_TAKES` that the file can actually supply.
    """
    from .parser import read_input

    try:
        result = read_input(path)
    except OSError as exc:
        return f"读不到这个文件：{exc.strerror}", set()
    top = {s.name.upper(): s for s in result.sections}
    global_block = top.get("GLOBAL")
    run_type = str(global_block.get("RUN_TYPE", "?")).upper() if global_block else "?"
    subsys = None
    for candidate in result.sections:
        if candidate.name.upper() == "FORCE_EVAL":
            subsys = candidate.subsection("SUBSYS")
            break
    motion = top.get("MOTION")
    found: set[str] = set()
    bits: list[str] = []
    if subsys is not None:
        coord = subsys.subsection("COORD")
        if coord is not None:
            found.add("pos")
            bits.append(f"坐标 {len(coord.keywords)} 个原子")
        if subsys.subsection("VELOCITY") is not None:
            found.add("vel")
            bits.append("速度")
        if subsys.subsection("CELL") is not None:
            found.add("cell")
            bits.append("晶胞")
        if subsys.subsection("SHELL") is not None or subsys.subsection("CORE") is not None:
            found.update({"shell_pos", "core_pos"})
            bits.append("shell/core")
    if motion is not None:
        for name, key in (("MD", "counters"), ("GEO_OPT", "counters"),
                          ("CELL_OPT", "counters"), ("BAND", "counters")):
            block = motion.subsection(name)
            if block is None:
                continue
            if name == "MD":
                found.add("counters")
                if block.subsection("THERMOSTAT") is not None:
                    found.add("thermostat")
                    bits.append("恒温器状态")
                if block.subsection("BAROSTAT") is not None:
                    found.add("barostat")
                    bits.append("压浴状态")
                bits.append(f"MD 步数计数器（到 {block.get('STEP_START_VAL', '?')} 步）"
                            if block.get("STEP_START_VAL") is not None else "MD 段")
            else:
                found.add("counters")
                bits.append(f"{name} 迭代计数器")
        if motion.subsection("CONSTRAINT") is not None:
            found.add("constraint")
            bits.append("约束")
    text = f"{Path(path).name}：由 {run_type} 写" + (
        "（" + "、".join(bits) + "）" if bits else "（没看出可接力的内容）")
    return text, found


def ext_restart_section(spec: Spec) -> Section | None:
    """The top-level ``&EXT_RESTART`` block that continues a previous run."""
    if not spec.ext_restart_file:
        return None
    take = spec.ext_restart_take
    block = Section("EXT_RESTART", comment=(
        "continue a previous MD / geometry optimisation instead of starting over"
        if take is None else
        "接力：只从上一次的 restart 文件里取 "
        + "、".join(EXT_RESTART_TAKES[k][0] for k in take if k in EXT_RESTART_TAKES)
        + "，其余一概不取（没点到的那些用本次输入里的值）"))
    block.set("RESTART_FILE_NAME", mm_path_for(spec, spec.ext_restart_file))
    # keep heating/cooling going across the restart instead of snapping back to
    # the input's TEMPERATURE (input_cp2k_check.F:646 reads this from the
    # restart section and moves MOTION%MD%TEMPERATURE over from the old file)
    if spec.restart_temperature_annealing:
        block.set("RESTART_TEMPERATURE_ANNEALING", True, comment=(
            "接力时沿用 restart 里存下的目标温度，温度斜坡接着走"
            "（否则会退回本输入里的 TEMPERATURE 重新开始）"))
    if take is None:
        block.set("RESTART_DEFAULT", bool(spec.ext_restart_default))
        return block
    # RESTART_DEFAULT F turns every RESTART_* keyword off; the ones named here
    # are switched back on.  handle_defaults_restart (input_cp2k_check.F:1148)
    # is what applies the default to the keywords the user did not write.
    block.set("RESTART_DEFAULT", False, comment=(
        "先全部关掉，下面只打开需要的几项"))
    for key in take:
        info = EXT_RESTART_TAKES.get(key)
        if info is None:
            continue
        name, why = info
        block.set(f"RESTART_{name}", True, comment=why)
    return block


#: Short name -> (``RESTART_<name>`` suffix, what it takes).  The names are
#: CP2K's own logical ``RESTART_*`` keywords (``motion/input_cp2k_restarts.F``);
#: every one the user does not write inherits ``RESTART_DEFAULT``.
#:
#: The second element is the comment written next to the switch, so it says what
#: the item buys *in this workflow*: a restart loaded as the structure already
#: brings positions and the cell (they end up in ``&COORD``/``&CELL``), so the
#: ones that matter are what an inline input cannot carry - velocities, step
#: counters and the thermostat/barostat history.
EXT_RESTART_TAKES: dict[str, tuple[str, str]] = {
    "vel": ("VEL", "velocities：内联输入给不了这个（restart 里的 &VELOCITY），"
                    "MD 接着跑就取它"),
    "pos": ("POS", "atomic positions（CP2K 的 restart 通常已经把坐标内联进 &COORD，"
                    "所以只在没继承坐标时才需要）"),
    "cell": ("CELL", "cell（同样通常已经随 &CELL 继承；变胞优化/NPT 才会改它）"),
    "counters": ("COUNTERS", "step/iteration counters（想让新任务从旧的步数接着数才取；"
                             "普通接力**不要**取，否则轨迹编号和平均值都从中间开始）"),
    "thermostat": ("THERMOSTAT", "thermostat state（接着控温才需要）"),
    "barostat": ("BAROSTAT", "barostat state（接着控压才需要）"),
    "barostat_thermostat": ("BAROSTAT_THERMOSTAT", "barostat 的恒温器状态"),
    "constraint": ("CONSTRAINT", "约束/限制（SHAKE、collective variables）"),
    "averages": ("AVERAGES", "MD 平均量（接着累加平均才需要）"),
    "randomg": ("RANDOMG", "随机数发生器状态（严格复现才需要）"),
    "metadynamics": ("METADYNAMICS", "metadynamics 的 hills"),
    "shell_pos": ("SHELL_POS", "shell 位置（极化力场）"),
    "shell_vel": ("SHELL_VELOCITY", "shell 速度"),
    "core_pos": ("CORE_POS", "core 位置"),
    "core_vel": ("CORE_VELOCITY", "core 速度"),
    "temperature_annealing": ("TEMPERATURE_ANNEALING", "退火进度"),
}


def ot_supports_kpoints() -> bool:
    """Whether CP2K's OT can run with k-points.  It cannot.

    ``qs_scf_initialization.F:826`` aborts with *"OT diagonalization not
    possible with kpoint calculations"* (and the same file refuses Block Krylov
    at line 835), so a ``&KPOINTS`` section and ``&OT`` in one input is a hard
    error, not a bad-but-runnable combination.  Everything that decides the
    route goes through :func:`effective_route`, which therefore has to know.
    """
    return False


def effective_route(spec: Spec, force_diagonalization: bool = False) -> str:
    """Which SCF route a spec actually uses: ``"OT"`` or ``"DIAGONALIZATION"``.

    One source of truth for :func:`scf_section` *and* for the graphical
    interface, which needs to know the route to show the matching knobs.
    """
    route = (spec.scf or "auto").upper()
    if route != "AUTO":
        return route
    if force_diagonalization or writes_kpoints(spec):
        # k-points need DIAGONALIZATION: CP2K aborts on OT + &KPOINTS
        # (qs_scf_initialization.F), and band/PDOS always take that route
        return "DIAGONALIZATION"
    if spec.smearing:
        # smearing means fractional occupations, which OT cannot do without
        # the extra rotation variables (see &OT ROTATION)
        return "DIAGONALIZATION"
    return "OT"


def _ot_uses_energy_gap(preconditioner: str) -> bool:
    """Only two preconditioners look at ENERGY_GAP (see the manual)."""
    return preconditioner.upper() in ("FULL_ALL", "FULL_SINGLE_INVERSE")


def scf_section(spec: Spec, structure: Structure | None, setup: Setup,
                force_diagonalization: bool = False) -> Section:
    route = effective_route(spec, force_diagonalization)

    eps_scf = spec.eps_scf
    if eps_scf is None:
        eps_scf = 1.0e-6 if spec.task in ("energy", "band", "pdos") else 1.0e-6
    if spec.task == "freq":
        eps_scf = min(eps_scf, 1.0e-7)

    scf = Section("SCF")
    guess = (spec.scf_guess or "RESTART").upper()
    if guess == "RESTART":
        scf.set("SCF_GUESS", "RESTART", comment=(
            "previous run's wave function; CP2K falls back to an ATOMIC guess "
            "by itself when the file is missing"))
    else:
        scf.set("SCF_GUESS", guess)
    scf.set("EPS_SCF", float(eps_scf))
    scf.set("MAX_SCF", int(spec.max_scf))
    if spec.ignore_convergence_failure:
        # keep going (and still print the energy) when the SCF hits MAX_SCF:
        # fine for a relative comparison, dangerous for production numbers
        scf.set("IGNORE_CONVERGENCE_FAILURE", True)
    if spec.level_shift is not None and route != "OT":
        # NB: no ``[a.u.]`` annotation - CP2K's unit parser does not know that
        # spelling and aborts while reading the input ("Unknown unit: a.u.",
        # common/cp_units.F).  Omitting the annotation means CP2K uses the unit
        # the keyword itself declares, which is the internal one - exactly what
        # the number here is.
        scf.set("LEVEL_SHIFT", float(spec.level_shift), comment=(
            "in a.u. (internal units); diagonalisation only "
            "(qs_scf_diagonalization.F); it kicks in from the second SCF step or "
            "with a CORE guess"))

    outer_max = int(spec.outer_max_scf if spec.outer_max_scf is not None else 20)
    outer_eps = float(spec.outer_eps_scf if spec.outer_eps_scf is not None else eps_scf)

    if route.startswith("OT"):
        scf.comment = ("orbital transformation: fast and robust for insulators; "
                       f"{MANUAL}CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT.html")
        ot = Section("OT")
        preconditioner = spec.ot_preconditioner.upper()
        ot.set("MINIMIZER", spec.ot_minimizer.upper())
        ot.set("PRECONDITIONER", preconditioner)
        if spec.ot_algorithm.upper() != "STRICT":
            ot.set("ALGORITHM", spec.ot_algorithm.upper())
        if spec.ot_linesearch.upper() != "2PNT":
            ot.set("LINESEARCH", spec.ot_linesearch.upper())
        if spec.ot_stepsize is not None:
            ot.set("STEPSIZE", float(spec.ot_stepsize))
        if spec.ot_energy_gap is not None:
            # in a.u. - and again *not* annotated: CP2K rejects "[a.u.]"
            ot.set("ENERGY_GAP", float(spec.ot_energy_gap), comment=(
                "in a.u.; HOMO-LUMO estimate used by the preconditioner"
                if _ot_uses_energy_gap(preconditioner) else
                "in a.u.; not used by this PRECONDITIONER (only FULL_ALL and "
                "FULL_SINGLE_INVERSE look at it)"))
        rotation = spec.ot_rotation
        if rotation is None and spec.smearing:
            # fractional occupations make the energy non-invariant under a
            # rotation of the occupied subspace, so OT needs the extra variables
            rotation = True
        if rotation is not None:
            ot.set("ROTATION", bool(rotation), comment=(
                "needed for fractional occupations (SMEAR) / non-singlet "
                "restricted calculations" if rotation else None))
        if spec.ot_safe_diis is not None:
            ot.set("SAFE_DIIS", bool(spec.ot_safe_diis))
        scf.add(ot)
        outer = Section("OUTER_SCF")
        outer.set("EPS_SCF", outer_eps)
        outer.set("MAX_SCF", outer_max)
        if spec.outer_optimizer:
            outer.set("OPTIMIZER", spec.outer_optimizer.upper())
        scf.add(outer)
    else:
        scf.comment = ("diagonalisation route: needed for k-points, smearing and "
                       "unoccupied states")
        added = spec.added_mos
        # a Molden file with virtual orbitals needs somewhere to put them:
        # OT generates them after the SCF, the diagonalisation route only has
        # what ADDED_MOS asks for (and silently writes none without it)
        wants_virtuals = spec.print_molden and int(spec.molden_nlumo or 0) != 0
        if added is None and (spec.smearing or spec.task in ("band", "pdos")
                              or wants_virtuals):
            added = _default_added_mos(structure)
        if added:
            scf.set("ADDED_MOS", int(added))
        diag = Section("DIAGONALIZATION")
        diag.set("ALGORITHM", spec.diag_algorithm.upper())
        scf.add(diag)
        if spec.smearing:
            _add_smear(scf, spec)
        else:
            mixing = Section("MIXING")
            mixing.set("METHOD", spec.mixing_method.upper())
            mixing.set("ALPHA", float(spec.mixing_alpha))
            mixing.set("NBUFFER", int(spec.mixing_nbuffer))
            if spec.mixing_beta is not None:
                mixing.set("BETA", float(spec.mixing_beta))
            scf.add(mixing)
        # the outer loop is CP2K's own (default MAX_SCF 50); write it only when
        # the user asks for something specific, so default output is untouched
        if spec.outer_max_scf is not None or spec.outer_eps_scf is not None \
                or spec.outer_optimizer:
            outer = Section("OUTER_SCF")
            outer.set("EPS_SCF", outer_eps)
            outer.set("MAX_SCF", outer_max)
            if spec.outer_optimizer:
                outer.set("OPTIMIZER", spec.outer_optimizer.upper())
            scf.add(outer)

    # SMEAR belongs to &SCF, not to the diagonalisation route: writing it only
    # there silently dropped the setting when the route was forced to OT
    if spec.smearing and route.startswith("OT") and scf.subsection("SMEAR") is None:
        _add_smear(scf, spec)
        # CP2K needs ADDED_MOS whenever SMEAR is on, OT or not - the abort is
        # "Extra MOs (ADDED_MOS) are required for smearing" (qs_environment.F),
        # with no OT guard, so the OT route needs the count too
        if scf.get("ADDED_MOS") is None:
            scf.set("ADDED_MOS", int(spec.added_mos or _default_added_mos(structure)),
                    comment=("SMEAR 打开时 CP2K 要求 ADDED_MOS > 0，"
                             "OT 路线也一样（`Extra MOs (ADDED_MOS) are required "
                             "for smearing`）"))

    restart_print = _wfn_restart_print(spec, route)
    if restart_print is not None:
        # careful: Section.add() returns the *child*, so chain it explicitly
        scf_print = Section("PRINT")
        scf_print.add(restart_print)
        scf.add(scf_print)
    return scf


def _add_smear(scf: Section, spec: Spec) -> None:
    """``&SCF/&SMEAR``: fractional occupations for metals."""
    smear = Section("SMEAR")
    smear.set("METHOD", spec.smear_method.upper())
    smear.set("ELECTRONIC_TEMPERATURE", float(spec.electronic_temperature), unit="K")
    scf.add(smear)


#: task -> the ``&EACH`` level its steps are counted at
WFN_EACH_LEVELS = {"md": "MD", "geo_opt": "GEO_OPT", "cell_opt": "CELL_OPT"}

#: the levels that make sense for the wave-function restart file, most specific
#: first (used when reading an existing input back)
WFN_EACH_CHOICES = ("MD", "GEO_OPT", "CELL_OPT", "QS_SCF")


def wfn_each_level(spec: Spec) -> str:
    """Which iteration level the wave-function restart interval counts.

    ``&EACH`` is evaluated against *every* active iteration level, so the level
    decides what "every N steps" means: the MD/GEO_OPT/CELL_OPT levels count the
    steps of the run, ``QS_SCF`` counts the SCF iterations of a single force
    evaluation (the right thing for a single-point run, the wrong one for MD).
    """
    if spec.wfn_restart_level:
        return spec.wfn_restart_level.upper()
    return WFN_EACH_LEVELS.get(spec.task, "QS_SCF")


def _wfn_restart_print(spec: Spec, route: str) -> Section | None:
    """``&SCF/&PRINT/&RESTART`` - the file that ``SCF_GUESS RESTART`` reads.

    Written by default: without it the next run has nothing to restart from,
    which is exactly the pairing behind ``SCF_GUESS RESTART``.

    ``&EACH`` and ``ADD_LAST`` are written out explicitly because they are two
    independent settings: the tool's default is "0" plus ``ADD_LAST NUMERIC``,
    i.e. one dump at the end of the run/SCF.  CP2K's own default would be every
    20 steps.

    The *level* of the interval follows the task (see :func:`wfn_each_level`):
    an MD run counts MD steps, an optimisation its optimisation steps, and only a
    single-point run counts SCF iterations.
    """
    if not spec.wfn_restart_print:
        return None
    level = wfn_each_level(spec)
    kpoints = writes_kpoints(spec)
    extension = ".kp" if kpoints else ".wfn"
    guess = (spec.scf_guess or "RESTART").upper()
    note = (f"the {_project_name(spec)}-RESTART{extension} file "
            + ("that SCF_GUESS RESTART reads back" if guess == "RESTART"
               else f"this run writes for the next one (it starts from "
                    f"SCF_GUESS {guess})"))
    if kpoints:
        note += " (k-point runs use .kp, not .wfn)"
    restart = Section("RESTART", "ON", comment=note)
    if level == "QS_SCF":
        each_comment = ("QS_SCF 计数：每多少个 **SCF 迭代**写一次（0 = 迭代过程中不写，"
                        "只靠 ADD_LAST）。它数的是同一次力计算内部的迭代，"
                        "MD / 几何优化想按步写请改成 MD / GEO_OPT / CELL_OPT")
    else:
        each_comment = (f"{level} 计数：每多少个 **{level} 步**写一次"
                        f"（0 = 跑的过程中不写，只靠 ADD_LAST 在最后一步写）")
    each = Section("EACH", comment=each_comment)
    each.set(level, int(spec.wfn_restart_every
                        if spec.wfn_restart_every is not None else 0))
    restart.add(each)
    restart.set("ADD_LAST", "NUMERIC" if spec.wfn_restart_add_last else "NO",
                comment=f"one dump at the last {level} step"
                if spec.wfn_restart_add_last else
                (f"with {level} {spec.wfn_restart_every} and ADD_LAST NO the file "
                 f"is written only every N {level} steps" if spec.wfn_restart_every
                 else f"with {level} 0 and ADD_LAST NO this file is never written"))
    if spec.wfn_backup_copies is not None:
        restart.set("BACKUP_COPIES", int(spec.wfn_backup_copies))
    return restart


def _default_added_mos(structure: Structure | None) -> int:
    """Enough empty orbitals to describe a few conduction bands."""
    if structure is None:
        return 20
    n = structure.n_atoms()
    if n <= 20:
        return 10
    if n <= 100:
        return 20
    if n <= 300:
        return 40
    return 60


def subsys_section(spec: Spec, setup: Setup, structure: Structure | None,
                   elements: list[str], include_coords: bool = True) -> Section:
    subsys = Section("SUBSYS")
    cell = None
    if structure is not None:
        cell = cell_section(structure, comment="simulation cell")
    if cell is not None:
        subsys.add(cell)
    if structure is not None and include_coords and spec.task != "neb":
        subsys.add(coord_section(structure, scaled=spec.scaled_coords,
                             comment=("atomic coordinates [fractional]"
                                      if spec.scaled_coords
                                      else "atomic coordinates [angstrom]")))
    for kind in setup.kind_sections(h_to_d=spec.h_to_d):
        subsys.add(kind)
    # metadynamics CVs live in &SUBSYS, and ``&METAVAR COLVAR n`` counts them in
    # exactly this order.  On the PLUMED route the CVs come from the PLUMED input
    # file, so writing them here would only create sections nothing reads
    # (`metadyn.problems` says so for the library route, where no form warns).
    if (spec.task == "md" and spec.metadyn is not None and spec.metadyn.active
            and not spec.metadyn.use_plumed):
        for colvar in cv_blocks(spec.metadyn):
            subsys.add(colvar)
    return subsys


def _posix(path: str | None) -> str | None:
    """File names go into the input with forward slashes: they work on both
    Windows and Linux, and CP2K treats a backslash as an escape."""
    if not path:
        return None
    return str(path).replace("\\", "/")


def _note(section: Section, text: str | None) -> None:
    """Append a line to a section's comment block."""
    if not text:
        return
    section.comment = "\n".join(part for part in (section.comment, text) if part)


def _set_or_note(section: Section, keyword: str, value: str | None,
                 missing: str, **options) -> None:
    """Write a file keyword, or say in the comment why it is absent.

    The keyword is *not* written when there is no path: a stand-in such as
    ``<fill this in>`` would look like a real file name to the validator (and to
    anyone reading the input), while CP2K would abort on it anyway.
    """
    if value:
        section.set(keyword, value, **options)
    else:
        _note(section, f"{missing}：这一项必须填上，CP2K 才能找到文件")


# --------------------------------------------------------------------------- #
# classical force fields: FORCE_EVAL METHOD FIST
# --------------------------------------------------------------------------- #
def fist_force_eval_section(spec: Spec, structure: Structure | None,
                            elements: list[str]) -> Section:
    """``&FORCE_EVAL`` for a classical (force-field) calculation.

    There is no ``&DFT`` block here and there must not be one: ``METHOD FIST``
    computes no electrons, so basis sets, pseudopotentials, cutoffs, SCF and OT
    settings are all meaningless.  CP2K aborts outright if a ``&DFT`` section is
    present next to ``METHOD FIST``.
    """
    force_eval = Section("FORCE_EVAL", comment=(
        "classical force-field calculation: METHOD FIST evaluates no electrons, "
        "so there is no &DFT/&QS/&SCF block here"))
    force_eval.set("METHOD", "FIST",
                   comment=f"see {MANUAL}CP2K_INPUT/FORCE_EVAL.html")
    if fist_stress_tensor(spec):
        force_eval.set("STRESS_TENSOR", "ANALYTICAL", comment=(
            "needed for a cell that is allowed to change (NPT / CELL_OPT); "
            "CP2K's default is NONE"))

    force_eval.add(mm_section(spec, structure, elements))

    fe_print = Section("PRINT")
    if spec.print_forces:
        fe_print.add(Section("FORCES", "ON"))
    if len(fe_print.sections):
        force_eval.add(fe_print)

    force_eval.add(fist_subsys_section(spec, structure, elements))
    return force_eval


def fist_stress_tensor(spec: Spec) -> bool:
    """Whether an analytical stress tensor is required by this run."""
    if spec.mm_stress_tensor is not None:
        return bool(spec.mm_stress_tensor)
    if spec.task == "cell_opt":
        return True
    if spec.task == "md":
        return spec.ensemble.upper() in NPT_ENSEMBLES
    return False


#: Ensembles that let the cell change, so they need a stress tensor.
#: HYDROSTATICSHOCK is *not* here: the manual lists it, but no CP2K integrator
#: implements the constant it maps to (velocity_verlet_control.F has no case for
#: it and aborts on the first step).
NPT_ENSEMBLES = ("NPT_I", "NPT_F", "NPT_IA", "NPE_I", "NPE_F", "MSST",
                 "MSST_DAMPED")


def mm_section(spec: Spec, structure: Structure | None,
               elements: list[str]) -> Section:
    mm = Section("MM", comment=(
        "classical force field (no electrons are computed); "
        f"{MANUAL}CP2K_INPUT/FORCE_EVAL/MM.html"))
    mm.add(mm_forcefield_section(spec, structure, elements))
    mm.add(mm_poisson_section(spec, structure))
    if spec.mm_verlet_skin is not None:
        neighbours = Section("NEIGHBOR_LISTS")
        neighbours.set("VERLET_SKIN", float(spec.mm_verlet_skin), unit="angstrom")
        mm.add(neighbours)
    return mm


def mm_forcefield_section(spec: Spec, structure: Structure | None,
                          elements: list[str]) -> Section:
    route = mm_route(spec)
    forcefield = Section("FORCEFIELD", comment=route.note or None)
    forcefield.set("PARMTYPE", route.parmtype, comment=(
        f"CP2K's PARMTYPE for {route.key}: {route.label}"))

    if route.parm_file:
        _set_or_note(forcefield, "PARM_FILE_NAME", mm_path_for(spec, spec.mm_parm_file),
                     f"{route.key} 路线缺 PARM_FILE_NAME（力场参数文件）")
    # NB: a PARM_FILE_NAME left over from another route is *not* written for
    # PARMTYPE OFF - the parameters of those routes live in the input, and a
    # stale path would only be a file CP2K has no reason to open.

    # ---- 1-4 scaling ----------------------------------------------------- #
    # CP2K's own defaults (VDW 1.0 / EI 0.0) are right for neither AMBER nor
    # CHARMM: EI_SCALE14 0.0 silently removes every 1-4 electrostatic
    # interaction.  The route supplies the value its force field expects.
    vdw = spec.mm_vdw_scale14 if spec.mm_vdw_scale14 is not None else route.vdw_scale14
    ei = spec.mm_ei_scale14 if spec.mm_ei_scale14 is not None else route.ei_scale14
    if vdw is not None:
        forcefield.set("VDW_SCALE14", float(vdw), comment=route.scale_note or None)
    elif route.scale_note:
        forcefield.set("EI_SCALE14", 0.0, comment=(
            "占位：CP2K 默认 EI_SCALE14 0.0，会把 1-4 静电全部去掉。" + route.scale_note))
    if ei is not None:
        forcefield.set("EI_SCALE14", float(ei))
    if spec.mm_shift_cutoff is not None:
        forcefield.set("SHIFT_CUTOFF", bool(spec.mm_shift_cutoff), comment=(
            "shift the non-bonded interactions so that they are continuous at "
            "the cutoff (CP2K's default is T)"))
    if spec.mm_multiple_potential is not None:
        forcefield.set("MULTIPLE_POTENTIAL", bool(spec.mm_multiple_potential))
    if spec.mm_ignore_missing_params is not None:
        forcefield.set("IGNORE_MISSING_CRITICAL_PARAMS",
                       bool(spec.mm_ignore_missing_params), comment=(
                           "runs as if the terms with missing parameters were zero - "
                           "only for debugging a parameter set"))
    if spec.mm_spline_emax is not None:
        spline = Section("SPLINE")
        spline.set("EMAX_SPLINE", float(spec.mm_spline_emax), unit="hartree")
        forcefield.add(spline)

    # ---- force-field terms ----------------------------------------------- #
    if route.key == "IN_INPUT":
        # the file *is* the force field, so nothing is generated next to it
        blocks, problem = _forcefield_from_file(spec)
        if blocks:
            forcefield.sections.extend(blocks)
        else:
            forcefield.sections.extend(_charge_blocks(spec, elements))
            forcefield.sections.extend(_lennard_jones_blocks(spec, elements))
        if problem:
            forcefield.comment = "\n".join(
                part for part in (forcefield.comment, problem) if part)
    elif route.key == "PAIR":
        forcefield.sections.extend(_charge_blocks(spec, elements))
        forcefield.sections.extend(_lennard_jones_blocks(spec, elements))
    return forcefield


def _forcefield_from_file(spec: Spec) -> tuple[list[Section], str | None]:
    """Contents of ``--mm-ff-file``, ready to sit inside ``&FORCEFIELD``.

    The file may contain the force-field terms only, or a whole
    ``&FORCEFIELD`` / ``&MM`` block - all three spellings are accepted because
    that is what people copy out of the manual and out of CP2K's own test suite.
    Returns the blocks plus a message when the file could not be read.
    """
    from .parser import read_input

    if not spec.mm_ff_file:
        return [], ("还没有力场条款：用 --mm-ff-file 指定一个装着 "
                    "&CHARGE/&BOND/&BEND/&NONBONDED 的文件，"
                    "或直接在下面把它们写出来")
    try:
        result = read_input(spec.mm_ff_file)
    except OSError as error:
        return [], (f"读不到力场文件 {_posix(spec.mm_ff_file)}（{error.strerror}）："
                    "把 &CHARGE/&BOND/&BEND/&NONBONDED 条款补在这里，"
                    "或修好 --mm-ff-file 的路径")
    blocks: list[Section] = []
    for root in result.sections:
        if root.name == "MM":
            forcefield = root.subsection("FORCEFIELD")
            blocks.extend(forcefield.sections if forcefield else [])
        elif root.name == "FORCEFIELD":
            blocks.extend(root.sections)
        else:
            blocks.append(root)
    if not blocks:
        return [], (f"{_posix(spec.mm_ff_file)} 里没有解析到任何力场条款（"
                    "&BOND/&BEND/&CHARGE/&NONBONDED ...）")
    return blocks, None


def _charge_blocks(spec: Spec, elements: list[str]) -> list[Section]:
    """``&CHARGE`` entries; ``(0.0)`` for every element when none are given.

    CP2K expects one ``&CHARGE`` per atomic kind: with none at all the atoms get
    no charge, which is fine for a pure LJ system (CP2K's own argon examples write
    an explicit 0.0 for exactly that reason).
    """
    charges = list(spec.mm_charges)
    if not charges:
        charges = [(element, 0.0) for element in dict.fromkeys(elements)]
    blocks: list[Section] = []
    for atom, charge in charges:
        block = Section("CHARGE")
        block.set("ATOM", atom)
        block.set("CHARGE", float(charge))
        blocks.append(block)
    return blocks


def _lennard_jones_blocks(spec: Spec, elements: list[str]) -> list[Section]:
    """A ``&NONBONDED`` section with one ``&LENNARD-JONES`` per pair.

    ``EPSILON`` is in K and ``SIGMA`` in angstrom - CP2K's declared units for
    these keywords.  Pairs the user did not list are mixed with the
    Lorentz-Berthelot rules from the per-element table.
    """
    if spec.mm_lj:
        pairs = list(spec.mm_lj)
    elif spec.mm_element_lj:
        pairs = lorentz_berthelot({k: (float(v[0]), float(v[1]))
                                   for k, v in spec.mm_element_lj.items()})
    else:
        pairs = [MmLennardJones(list(pair)) for pair in _element_pairs(elements)]

    nonbonded = Section("NONBONDED", comment=(
        "LJ parameters: EPSILON in K, SIGMA and RCUT in angstrom"
        if spec.mm_lj or spec.mm_element_lj else
        "还差 ε/σ：这里只列出了元素对，EPSILON/SIGMA 需要你按力场补上，"
        "否则 LJ 吸引项等于 0"))
    for pair in pairs:
        block = Section("LENNARD-JONES")
        block.set("ATOMS", list(pair.atoms))
        if pair.epsilon is not None:
            block.set("EPSILON", float(pair.epsilon), unit="K")
        if pair.sigma is not None:
            block.set("SIGMA", float(pair.sigma), unit="angstrom")
        if pair.rcut is not None:
            block.set("RCUT", float(pair.rcut), unit="angstrom")
        nonbonded.add(block)
    return [nonbonded]


def _element_pairs(elements: list[str]) -> list[tuple[str, str]]:
    unique = list(dict.fromkeys(elements))
    return [(first, second)
            for index, first in enumerate(unique)
            for second in unique[index:]]


def suggest_ewald_gmax(structure: Structure | Cell | None,
                       alpha_angstrom: float | None = None) -> tuple[int, int, int]:
    """A GMAX for ``EWALD_TYPE EWALD``/``SPME`` when the user gave none.

    GMAX has **no default in CP2K**: ``ewald_environment_types.F`` says so in a
    comment ("we have no defaults for gmax, gmax is only needed for ewald and
    spme") and then *demands* the value, which aborts the run with

        Value requested, but no value set getting value from keyword GMAX
        of section EWALD                      input/input_section_types.F:1132

    so the tool has to supply one.  The estimate is CP2K's own (the formula its
    tight-binding Ewald reader uses): with ``cutoff = 45 * alpha``,

        gmax_i = 2 * floor(sqrt(2 * cutoff) * |a_i| / (2*pi)) + 1

    in atomic units, i.e. the number of reciprocal lattice vectors that fit into
    the plane-wave sphere of that cutoff.  An odd value keeps the mesh centred.
    """
    if structure is None:
        return (25, 25, 25)
    cell = structure if isinstance(structure, Cell) else structure.cell
    if cell is None:
        return (25, 25, 25)
    alpha = float(alpha_angstrom) if alpha_angstrom else 0.35   # CP2K's default
    bohr = 0.5291772109
    alpha_au = alpha * bohr                 # angstrom^-1 -> bohr^-1
    cutoff = 45.0 * alpha_au                # Ry, as in ewald_environment_types.F
    grid = []
    for length in cell.lengths():
        length_au = float(length) / bohr
        count = 2 * int(math.floor(math.sqrt(2.0 * cutoff) * length_au
                                   / (2.0 * math.pi))) + 1
        grid.append(max(3, count))
    return (grid[0], grid[1], grid[2])


def mm_poisson_section(spec: Spec, structure: Structure | None) -> Section:
    """``&MM/&POISSON/&EWALD``: the classical electrostatics.

    ``EWALD_TYPE NONE`` is the honest answer for an isolated system in a box -
    SPME/PME need a periodic cell to interpolate on a mesh, so a non-periodic
    cell must not get one.
    """
    periodic = structure is not None and structure.is_periodic()
    kind = (spec.mm_ewald or ("SPME" if periodic else "NONE")).upper()
    if not periodic and kind in ("SPME", "PME"):
        kind = "NONE"
    poisson = Section("POISSON")
    ewald = Section("EWALD", comment=(
        "SPME is the smooth particle mesh Ewald CP2K recommends"
        if kind == "SPME" else
        "EWALD_TYPE NONE keeps only the real-space Coulomb sum: right for a "
        "neutral system with no charges, and right for an isolated molecule - "
        "wrong for a periodic solution"
        if kind == "NONE" else None))
    ewald.set("EWALD_TYPE", kind)
    if kind != "NONE":
        alpha = spec.mm_ewald_alpha
        if alpha is not None:
            ewald.set("ALPHA", float(alpha), unit="angstrom^-1")
        if kind in ("EWALD", "SPME"):
            # mandatory for these two: CP2K has no default and aborts without it
            gmax = int(spec.mm_ewald_gmax) if spec.mm_ewald_gmax \
                else suggest_ewald_gmax(structure, alpha)
            if isinstance(gmax, tuple):
                ewald.set("GMAX", list(gmax), comment=(
                    "G-space max. Miller index; CP2K has no default for it, "
                    "the tool estimated it from the cell and ALPHA "
                    "(ewald_environment_types.F)"))
            else:
                ewald.set("GMAX", int(gmax), comment=(
                    "G-space max. Miller index; CP2K has no default for it"))
        elif spec.mm_ewald_gmax:
            ewald.set("GMAX", int(spec.mm_ewald_gmax))
        if spec.mm_o_spline is not None and kind in ("SPME", "PME"):
            ewald.set("O_SPLINE", int(spec.mm_o_spline), comment=(
                "interpolation order of the mesh; 6 is fast and accurate"))
        if spec.mm_rcut is not None:
            ewald.set("RCUT", float(spec.mm_rcut), unit="angstrom")
    poisson.add(ewald)
    return poisson


def fist_subsys_section(spec: Spec, structure: Structure | None,
                        elements: list[str]) -> Section:
    """``&SUBSYS`` for FIST: cell, topology and either ``&COORD`` or a file."""
    route = mm_route(spec)
    subsys = Section("SUBSYS")
    if structure is not None:
        cell = cell_section(structure, comment="simulation cell")
        if cell is not None:
            subsys.add(cell)
    subsys.add(fist_topology_section(spec, structure))
    # A coordinate *file* carries its own coordinates, and CP2K would let a
    # ``&COORD`` block *overwrite* it again (topology.F calls read_atoms_input with
    # overwrite=.TRUE. and warns "Overwriting coordinates..."), so the two are
    # exclusive: with a file, the file wins and nothing else is written; without
    # one the atoms are inlined - the shape CP2K's own restart files use.
    if route.conn_file and spec.mm_coord_file:
        return subsys
    if not route.conn_file and spec.task == "neb":
        return subsys
    if structure is not None and structure.atoms:
        subsys.add(coord_section(structure, scaled=spec.scaled_coords,
                                 comment="atomic coordinates [angstrom]",
                                 preserve_labels=coordinates_are_inline(spec)))
    return subsys


def fist_topology_section(spec: Spec, structure: Structure | None) -> Section:
    route = mm_route(spec)
    topology = Section("TOPOLOGY", comment=(
        f"{MANUAL}CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY.html"))
    if spec.mm_molecules:
        # several molecules, each with its own topology file (mixed systems)
        topology.set("CONNECTIVITY", "MOL_SET", comment=(
            "混合体系：每种分子一个 &MOLECULE，各自指向自己的拓扑文件"))
    elif route.conn_file:
        topology.set("CONNECTIVITY", route.connectivity)
    else:
        topology.set("CONNECTIVITY", route.connectivity, comment=(
            "GENERATE 按距离判断成键；这是 CP2K 的默认值"))

    coord_format = coord_format_for(spec, route)
    # CP2K's own restart files are the reference here: when the run did *not* use
    # a CP2K coordinate file, ``input_restart_force_eval.F`` **unsets**
    # COORD_FILE_NAME/COORD_FILE_FORMAT and inlines the current coordinates into
    # ``&COORD`` ("Remove COORD_FILE_NAME and COORD_FILE_FORMAT keywords from
    # restart").  So a relay never needs the inpcrd again - the tool writes the
    # same shape whenever it knows the atoms but was not given a coordinate file.
    if route.conn_file:
        if not spec.mm_molecules:
            _set_or_note(topology, "CONN_FILE_NAME",
                         mm_path_for(spec, conn_file_for(spec, route)),
                         f"{route.key} 路线缺 CONN_FILE_NAME（拓扑文件）"
                         + ("；参数文件给了的话会自动用它"
                            if route.same_file else ""))
        if spec.mm_coord_file:
            topology.set("COORDINATE", coord_format, comment=(
                "CRD 是 AMBER 的坐标格式（inpcrd / restart）"
                if coord_format == "CRD" else None))
            _set_or_note(topology, "COORD_FILE_NAME",
                         mm_path_for(spec, spec.mm_coord_file),
                         "缺 COORD_FILE_NAME（坐标文件）")
        elif spec.structure is None:
            topology.set("COORDINATE", coord_format)
            _set_or_note(topology, "COORD_FILE_NAME", None,
                         "缺 COORD_FILE_NAME（坐标文件）：没有坐标文件时"
                         "至少要给一个结构文件，工具才能把坐标写成 &COORD")
        else:
            topology.comment = (topology.comment + "；" if topology.comment else "") + (
                "坐标写在 &SUBSYS/&COORD 里（没有 COORD_FILE_NAME）："
                "CP2K 的 restart 就是这么做的，接力时不需要 inpcrd")
    if spec.mm_molecules:
        mol_set = Section("MOL_SET")
        for molecule in spec.mm_molecules:
            block = Section("MOLECULE")
            block.set("CONNECTIVITY", (molecule.conn_format or "AMBER").upper())
            _set_or_note(block, "CONN_FILE_NAME", mm_path_for(spec, molecule.conn_file),
                         "这个 &MOLECULE 缺 CONN_FILE_NAME")
            block.set("NMOL", int(molecule.nmol), comment=(
                "this many copies of the molecule are built from the single "
                "coordinate file"))
            mol_set.add(block)
        topology.add(mol_set)
    return topology


def force_eval_section(spec: Spec, setup: Setup, structure: Structure | None,
                       elements: list[str]) -> Section:
    if is_classical(spec):
        return fist_force_eval_section(spec, structure, elements)
    force_eval = Section("FORCE_EVAL")
    force_eval.set("METHOD", "QUICKSTEP")
    # The cell of these tasks is integrated from the virial, and CP2K aborts
    # without a stress tensor ("Virial evaluation not requested for this run",
    # md_run.F) - the FIST route already wrote it, this one did not, so a
    # generated NPT/NPE AIMD input could not even start.
    if spec.task == "cell_opt" or (spec.task == "md"
                                   and spec.ensemble.upper() in NPT_ENSEMBLES):
        force_eval.set("STRESS_TENSOR", "ANALYTICAL", comment=(
            "晶胞会变的任务必须有应力张量；CP2K 没有它会 abort"
            "（`Virial evaluation not requested for this run`）"))

    force_diagonalization = spec.task == "band"
    dft = dft_section(spec, setup, structure, elements, force_diagonalization)

    # ---- print blocks ----
    # NB: a print key is enabled with ON/OFF/SILENT/LOW/MEDIUM/HIGH/DEBUG - its
    # section parameter is an *enumeration* (cp_print_key_section_create), so a
    # bare ``T`` is rejected by CP2K with "invalid value for enumeration:T".
    dft_print = Section("PRINT")
    if spec.print_mulliken and not (structure and structure.is_periodic()):
        dft_print.add(Section("MULLIKEN", "ON"))
    if spec.print_dipole and not (structure and structure.is_periodic()):
        dft_print.add(Section("MOMENTS", "ON", comment=(
            "electric dipole moment; meaningless for a fully periodic system, so it "
            "is only requested for isolated systems")))
    if spec.task in ("band",):
        dft_print.add(band_structure_print(spec, structure))
    if spec.task == "pdos":
        # 2026.2 nests PDOS under a new &DOS parent; in 2025.1 &DOS is only a
        # file-format print key and the projected DOS still sits directly under
        # &PRINT, so probe the subsection rather than the section itself
        dft_print.add(pdos_print(
            spec, _has_section("FORCE_EVAL/DFT/PRINT/DOS/PDOS")))
    if spec.print_molden:
        dft_print.add(molden_print(spec))
    if spec.print_mo_cubes:
        dft_print.add(mo_cubes_print(spec))
    if spec.print_density_cube:
        dft_print.add(density_cube_print(spec))
    if len(dft_print.sections):
        dft.add(dft_print)

    if writes_kpoints(spec):
        dft.add(kpoints_section(spec, structure))

    force_eval.add(dft)

    fe_print = Section("PRINT")
    if spec.print_forces:
        fe_print.add(Section("FORCES", "ON"))
    if spec.task == "cell_opt":
        fe_print.add(Section("STRESS_TENSOR", "ON"))
    if len(fe_print.sections):
        force_eval.add(fe_print)

    force_eval.add(subsys_section(spec, setup, structure, elements,
                                  include_coords=True))
    return force_eval


#: ``&KPOINTS UNITS`` values, from ``input_cp2k_kpoints.F``.
KPOINT_UNITS = ("B_VECTOR", "CART_ANGSTROM", "CART_BOHR")
#: ``&KPOINTS WAVEFUNCTIONS``: REAL is what a viewer/band structure wants, but a
#: system with spin-orbit coupling needs COMPLEX (CP2K's own default).
KPOINT_WAVEFUNCTIONS = ("REAL", "COMPLEX")


def writes_kpoints(spec: Spec) -> bool:
    """Whether the input needs a ``&KPOINTS`` block at all.

    band and PDOS always need one; every other task only when k-points were
    actually asked for (grid, spacing, explicit points, a scheme, ...).  Without
    any of them the section is left out and CP2K uses its own default - a single
    Gamma point - which is what a molecule or a large cell wants.

    This is also what decides whether the SCF restart file is ``.kp`` or
    ``.wfn``, so the two can never disagree.
    """
    if spec.task in ("band", "pdos"):
        return True
    return bool(spec.kpoint_grid or spec.kpoint_scheme or spec.kpoint_points
                or spec.kpoint_spacing or spec.kpoint_full_grid
                or spec.kpoint_gamma_centered or spec.kpoint_verbose
                or (spec.kpoint_wavefunctions or "REAL").upper() != "REAL"
                or not spec.kpoint_symmetry)


def kpoints_section(spec: Spec, structure: Structure | None) -> Section:
    """``&DFT/&KPOINTS``: Brillouin-zone sampling.

    The shape of the section is CP2K's (``input_cp2k_kpoints.F``): ``SCHEME``
    takes the method *and* its numbers (``MONKHORST-PACK 4 4 4``), or
    ``GENERAL`` with one ``KPOINT x y z w`` line per point - which is how CP2K's
    own regression inputs provide explicit k-point lists.
    """
    section = Section("KPOINTS", comment="Brillouin-zone sampling for the SCF density")
    points = list(spec.kpoint_points or ())
    # CP2K reads the ``KPOINT`` lines only under ``SCHEME GENERAL``, so an
    # explicit list beats a scheme left over in the combo: writing
    # ``SCHEME MONKHORST-PACK`` *and* ``KPOINT`` lines would be a contradiction
    # CP2K cannot read as intended.
    first = (spec.kpoint_scheme or "").split()[:1]
    forced_general = bool(points) and first not in ([], ["GENERAL"])
    if forced_general:
        scheme = "GENERAL"
    elif spec.kpoint_scheme:
        scheme = spec.kpoint_scheme
    elif points:
        scheme = "GENERAL"
    else:
        spacing = spec.kpoint_spacing or 20.0
        grid = spec.kpoint_grid or suggest_kpoint_grid(
            structure.cell if structure else None, spacing)
        if grid == (1, 1, 1):
            scheme = "GAMMA"
        else:
            scheme = "MONKHORST-PACK " + " ".join(str(n) for n in grid)
    tokens = scheme.split()
    # The manual (FORCE_EVAL/DFT/KPOINTS/SCHEME): "For MONKHORST-PACK the number
    # of k points in all 3 dimensions has to be supplied along with the keyword."
    # A bare scheme would be a broken input, so fill the three numbers in from the
    # grid box / spacing estimate - this also covers the command line, where
    # --kpoint-scheme is a raw string.
    forced_numbers = (tokens[0].upper() in ("MONKHORST-PACK", "MACDONALD")
                      and len(tokens) < 4)
    if forced_numbers:
        grid = spec.kpoint_grid or suggest_kpoint_grid(
            structure.cell if structure else None, spec.kpoint_spacing or 20.0)
        tokens = [tokens[0]] + [str(n) for n in grid]
    note = None
    if forced_general:
        note = "the explicit KPOINT list below needs SCHEME GENERAL"
    elif forced_numbers:
        note = ("MONKHORST-PACK/MACDONALD need the mesh numbers; "
                "estimated from the cell unless a grid is given")
    section.set("SCHEME", [tokens[0]] + [_maybe_int(t) for t in tokens[1:]],
                comment=note)
    units = (spec.kpoint_units or "B_VECTOR").upper()
    if units not in KPOINT_UNITS:
        units = "B_VECTOR"
    for index, point in enumerate(points):
        coords = [float(v) for v in point[:3]]
        weight = float(point[3]) if len(point) > 3 and point[3] is not None else 1.0
        section.append_keyword("KPOINT", *[round(c, 8) for c in coords], weight,
                               comment=("explicit k-point (SCHEME GENERAL)"
                                        if index == 0 else None))
    if points and units != "B_VECTOR":
        section.set("UNITS", units)
    if spec.kpoint_gamma_centered:
        section.set("GAMMA_CENTERED", True, comment=(
            "shift the Monkhorst-Pack/MacDonald mesh so that it contains Gamma"))
    if spec.kpoint_full_grid:
        section.set("FULL_GRID", True, comment=(
            "use the whole mesh instead of the symmetry-reduced one: every "
            "k-point is present, and the cost grows accordingly"))
    section.set("SYMMETRY", bool(spec.kpoint_symmetry), comment=(
        "reduce the mesh with the crystal symmetry" if spec.kpoint_symmetry
        else "no symmetry reduction: CP2K uses every point of the mesh"))
    wavefunctions = (spec.kpoint_wavefunctions or "REAL").upper()
    if wavefunctions not in KPOINT_WAVEFUNCTIONS:
        wavefunctions = "REAL"
    section.set("WAVEFUNCTIONS", wavefunctions, comment=(
        "real wavefunctions are enough unless the k-point set needs phases "
        "(spin-orbit coupling); CP2K's own default is COMPLEX"))
    if spec.kpoint_verbose:
        section.set("VERBOSE", True, comment="list the k-points in the output")
    return section


def _maybe_int(token: str):
    try:
        return int(token)
    except ValueError:
        return token


def band_structure_print(spec: Spec, structure: Structure | None) -> Section:
    block = Section("BAND_STRUCTURE", comment=(
        "non-self-consistent band structure along a high-symmetry path"))
    block.set("FILE_NAME", f"{_project_name(spec)}.bs")
    block.set("ADDED_MOS", int(spec.added_mos or _default_added_mos(structure)))

    cell = structure.cell if structure else None
    kpath = build_kpath(spec.kpath, cell, spec.lattice)
    counts = distribute_points(kpath, spec.npoints)
    for segment, count in zip(kpath.segments, counts):
        kpoint_set = Section("KPOINT_SET")
        kpoint_set.set("NPOINTS", int(count))
        for label, coords in segment:
            kpoint_set.append_keyword("SPECIAL_POINT", label,
                                      [round(c, 8) for c in coords])
        kpoint_set.set("UNITS", "B_VECTOR")
        block.add(kpoint_set)
    return block


def pdos_print(spec: Spec, use_dos_parent: bool) -> Section:
    """Projected density of states, ready to be added to ``&DFT &PRINT``.

    CP2K 2026.2 moved the DOS print keys under a new ``&DOS`` parent and moved
    ``NLUMO`` up to it, so the shape of this block depends on the version.  The
    database in use decides which one is emitted.
    """
    if use_dos_parent:
        dos = Section("DOS")
        if spec.pdos_nlumo:
            dos.set("NLUMO", int(spec.pdos_nlumo))
        dos.add(Section("PDOS", comment="total projected DOS; add COMPONENTS to split it"))
        for selection in spec.pdos_ldos:
            ldos = Section("LDOS")
            ldos.set("LIST", [int(v) for v in selection])
            dos.add(ldos)
        # smoothing the curve is a 2026.2 feature: only emit it when the manual
        # database of the targeted version knows the block
        if spec.pdos_broaden and _has_section("FORCE_EVAL/DFT/PRINT/DOS/CURVE/BROADEN"):
            curve = Section("CURVE")
            broaden = Section("BROADEN", comment=(
                "smooths the plotted curve only; it does not change any result"))
            broaden.set("TYPE", spec.pdos_broaden_type.upper())
            broaden.set("WIDTH", float(spec.pdos_broaden), unit="eV")
            curve.add(broaden)
            dos.add(curve)
        return dos

    block = Section("PDOS", comment="projected density of states")
    if spec.pdos_nlumo:
        block.set("NLUMO", int(spec.pdos_nlumo))
    for selection in spec.pdos_ldos:
        ldos = Section("LDOS")
        ldos.set("LIST", [int(v) for v in selection])
        block.add(ldos)
    return block


def molden_print(spec: Spec) -> Section:
    """``&DFT/&PRINT/&MO_MOLDEN``: the orbitals in Molden format.

    The keywords are the ones ``input_cp2k_print_dft.F`` declares:
    ``FILENAME`` (CP2K appends the project name and a ``.molden`` extension),
    ``NLUMO``, ``UNIT``, ``GTO_KIND``, ``WRITE_PSEUDO`` and ``WRITE_CELL``.
    """
    block = Section("MO_MOLDEN", "ON", comment=(
        "molecular orbitals in Molden format, for a viewer (Molden/Avogadro/Jmol); "
        "CP2K only writes this when the run has no k-points - "
        f"{MANUAL}CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN.html"))
    if spec.molden_file:
        block.set("FILENAME", spec.molden_file)
    nlumo = int(spec.molden_nlumo or 0)
    block.set("NLUMO", nlumo, comment=(
        "only the occupied orbitals" if nlumo == 0 else
        "every virtual orbital the calculation can produce (OT generates them "
        "after the SCF; on the diagonalisation route cp2kbuild adds ADDED_MOS)"
        if nlumo < 0 else
        f"the first {nlumo} virtual orbitals"))
    unit = (spec.molden_unit or "ANGSTROM").upper()
    block.set("UNIT", unit, comment=(
        "coordinates/cell inside the Molden file; CP2K's own default is BOHR, "
        "the file states its unit either way"))
    if (spec.molden_gto_kind or "SPHERICAL").upper() != "SPHERICAL":
        block.set("GTO_KIND", spec.molden_gto_kind.upper())
    if spec.molden_write_pseudo:
        block.set("WRITE_PSEUDO", True, comment=(
            "include the [Pseudo] block (pseudopotential data)"))
    if spec.molden_write_cell:
        block.set("WRITE_CELL", True, comment="include the [Cell] block")
    return block


def mo_cubes_print(spec: Spec) -> Section:
    """``&DFT/&PRINT/&MO_CUBES``: frontier orbitals as Gaussian cube files.

    ``NHOMO``/``NLUMO`` are *counts*: ``NHOMO 1`` is the HOMO, ``NLUMO 1`` adds
    the LUMO.  The cube grid is thinned with ``STRIDE`` (CP2K's default is
    ``2 2 2``; ``1 1 1`` is the full grid).
    """
    block = Section("MO_CUBES", "ON", comment=(
        "orbital cubes for a viewer (VMD/VESTA/gOpenMol); NHOMO/NLUMO count "
        "orbitals around the HOMO-LUMO gap"))
    block.set("NHOMO", int(spec.mo_cubes_nhomo))
    block.set("NLUMO", int(spec.mo_cubes_nlumo))
    if spec.mo_cubes_filename:
        block.set("FILENAME", spec.mo_cubes_filename)
    if spec.mo_cubes_stride:
        block.set("STRIDE", _stride(spec.mo_cubes_stride))
    return block


def density_cube_print(spec: Spec) -> Section:
    """``&DFT/&PRINT/&TOT_DENSITY_CUBE``: the total electron density."""
    block = Section("TOT_DENSITY_CUBE", "ON", comment=(
        "total electron density as a cube file"))
    if spec.density_cube_filename:
        block.set("FILENAME", spec.density_cube_filename)
    if spec.density_cube_stride:
        block.set("STRIDE", _stride(spec.density_cube_stride))
    return block


def _stride(text: str) -> list[int]:
    """``"2 2 2"`` / ``"2,2,2"`` -> ``[2, 2, 2]`` (CP2K wants three integers)."""
    numbers = [int(float(tok)) for tok in str(text).replace(",", " ").split()]
    while len(numbers) < 3:
        numbers.append(numbers[-1] if numbers else 1)
    return numbers[:3]


def _has_section(path: str) -> bool:
    """Whether the manual database in use knows this section.

    Lets a template adapt to the CP2K version being targeted instead of
    hardcoding one release's input layout.
    """
    from .manualdb import load_db

    db = load_db()
    return bool(db is not None and db.has_section(path))


def motion_section(spec: Spec, structure: Structure | None) -> Section | None:
    if spec.task not in ("geo_opt", "cell_opt", "md", "neb"):
        return None
    motion = Section("MOTION")
    if spec.task == "geo_opt":
        geo = Section("GEO_OPT")
        geo.set("OPTIMIZER", spec.optimizer.upper())
        geo.set("MAX_ITER", int(spec.max_iter))
        # convergence thresholds in a.u. - no ``[a.u.]`` annotation, because
        # CP2K's unit parser rejects that spelling (see cp2kbuild/units.py)
        geo.set("MAX_FORCE", 4.5e-4, comment="convergence threshold in a.u.")
        geo.set("RMS_FORCE", 3.0e-4, comment="convergence threshold in a.u.")
        motion.add(geo)
    elif spec.task == "cell_opt":
        cell_opt = Section("CELL_OPT")
        cell_opt.set("OPTIMIZER", "LBFGS" if spec.optimizer.upper() == "BFGS"
                     else spec.optimizer.upper())
        cell_opt.set("MAX_ITER", int(spec.max_iter))
        cell_opt.set("EXTERNAL_PRESSURE", float(spec.external_pressure), unit="bar")
        cell_opt.set("KEEP_ANGLES", bool(spec.keep_angles))
        motion.add(cell_opt)
        motion.add(_motion_print(spec, periodic=True))
        return motion
    elif spec.task == "md":
        motion.add(md_section(spec, structure))
    elif spec.task == "neb":
        motion.add(band_section(spec))
    if spec.task == "md" and spec.metadyn is not None:
        free_energy = free_energy_section(spec.metadyn)
        if free_energy is not None:
            motion.add(free_energy)
    constraint = constraint_section(spec)
    if constraint is not None:
        motion.add(constraint)
    if spec.task in ("geo_opt", "cell_opt", "md"):
        changing_cell = spec.task == "cell_opt" or (
            spec.task == "md" and spec.ensemble.upper() in NPT_ENSEMBLES)
        motion.add(_motion_print(spec, periodic=changing_cell))
    return motion


#: ``&FIXED_ATOMS COMPONENTS_TO_FIX`` values (``input_cp2k_motion.F``).
FIXED_ATOM_COMPONENTS = ("X", "Y", "Z", "XY", "XZ", "YZ", "XYZ")


def constraint_section(spec: Spec) -> Section | None:
    """``&MOTION/&CONSTRAINT/&FIXED_ATOMS``: freeze atoms.

    The usual surface/slab setup: let the top layers relax while the bottom
    layers stay put (and, with ``COMPONENTS_TO_FIX Z``, also a fixed height).
    ``LIST`` is CP2K's own list syntax (``1 3 5..8``), passed through as written
    so ranges keep working.
    """
    if not spec.fixed_atoms or not str(spec.fixed_atoms).strip():
        return None
    constraint = Section("CONSTRAINT")
    fixed = Section("FIXED_ATOMS")
    fixed.set("LIST", str(spec.fixed_atoms).strip())
    components = (spec.fixed_atoms_components or "XYZ").upper()
    if components not in FIXED_ATOM_COMPONENTS:
        components = "XYZ"
    fixed.set("COMPONENTS_TO_FIX", components)
    constraint.add(fixed)
    return constraint


#: Ensembles whose particle motion is thermostatted: CP2K aborts when
#: ``&MD ANNEALING`` (velocity rescaling) is combined with one of them
#: (``simpar_methods.F``: "These two methods to control the temperature act one
#: against the other").
THERMOSTATTED_ENSEMBLES = ("NVT", "NPT_I", "NPT_IA", "NPT_F")
#: Ensembles whose *cell* has a thermostat - ``ANNEALING_CELL`` is only applied
#: for the NPE ones (``integrator.F``), so everywhere else it does nothing.
CELL_THERMOSTATTED_ENSEMBLES = ("NPT_I", "NPT_F", "NPT_IA", "MSST", "MSST_DAMPED")
#: Ensembles that implement cell-velocity annealing at all.
CELL_ANNEALING_ENSEMBLES = ("NPE_I", "NPE_F")
#: ... and the ones that can rescale particle velocities: everything without a
#: particle thermostat *and* without Langevin's own coupling.
VELOCITY_ANNEALING_ENSEMBLES = ("NVE", "NPE_I", "NPE_F", "MSST", "MSST_DAMPED",
                                "ISOKIN", "HYDROSTATICSHOCK")


@dataclass
class Ramp:
    """A heating/cooling schedule worked out from start / end / number of steps.

    CP2K's own annealing is **geometric**: the factor is applied once per MD
    step, so the target temperature follows ``T_n = T0 * factor**n``.  The factor
    that lands on ``T_end`` after ``steps`` steps is therefore
    ``(T_end/T0)**(1/steps)`` for the temperature ramp and
    ``(T_end/T0)**(1/(2*steps))`` for the velocity rescaling (velocities are
    scaled by ``factor``, and ``T`` goes with ``v**2``).
    """

    start: float
    end: float
    steps: int
    timestep: float           # fs
    factor: float
    #: "temperature" (``&MD TEMPERATURE_ANNEALING``, Langevin) or "velocity"
    #: (``&MD ANNEALING``, ensembles without a particle thermostat)
    mechanism: str

    @property
    def duration(self) -> float:             # ps
        return self.steps * self.timestep / 1000.0

    @property
    def rate(self) -> float:                 # K/ps (signed: >0 heating)
        return (self.end - self.start) / self.duration if self.duration else 0.0

    @property
    def per_step(self) -> float:             # K per MD step, at the start
        return abs(self.factor - 1.0) * self.start

    def table(self, rows: int = 6) -> list[tuple[float, float]]:
        """``(ps, K)`` along the schedule, for the preview/CLI note."""
        out = []
        for i in range(rows + 1):
            step = round(self.steps * i / rows)
            temperature = (self.start * self.factor ** step
                           if self.mechanism == "temperature"
                           else self.start * (self.factor ** 2) ** step)
            out.append((step * self.timestep / 1000.0, temperature))
        return out


def annealing_mechanism(ensemble: str) -> str | None:
    """Which of CP2K's two annealing mechanisms this ensemble can use.

    ``temperature`` (Langevin) moves the thermostat's setpoint - the physically
    meaningful way to heat/cool under a thermostat.  ``velocity`` rescales the
    atoms' velocities and needs an ensemble without a particle thermostat.
    ``None`` means: neither works (NVT/NPT with the standard thermostats).
    """
    name = (ensemble or "").upper()
    if name == "LANGEVIN":
        return "temperature"
    if name in THERMOSTATTED_ENSEMBLES:
        return None
    return "velocity"


def plan_ramp(start: float, end: float, steps: int, timestep: float,
              ensemble: str) -> Ramp:
    """Work out the factor for "go from ``start`` K to ``end`` K in ``steps``"."""
    if steps <= 0:
        raise ValueError("升降温需要 STEPS > 0（一步都没有就无所谓斜率）")
    if start <= 0.0 or end <= 0.0:
        raise ValueError("温度必须是正数（开氏温标）")
    mechanism = annealing_mechanism(ensemble)
    if mechanism is None:
        raise ValueError(
            f"ENSEMBLE {ensemble} 不能做升降温：CP2K 自己的两个办法都不适用于它——"
            "`&MD TEMPERATURE_ANNEALING` 只在 LANGEVIN 系综下生效"
            "（它移动的是 Langevin 的目标温度），而 `&MD ANNEALING`（缩放速度）"
            "在 NVT/NPT 下会被 CP2K 直接中止（\"the thermostat ... act one against "
            "the other\"）。要做升降温请把系综换成 LANGEVIN；"
            "或者留在 NVE/NPE 用速度缩放那种（粗一些）。")
    exponent = 1.0 / steps if mechanism == "temperature" else 1.0 / (2 * steps)
    # rounded to 12 decimals: the GUI's factor box and the CLI have to end up with
    # the *same* double, otherwise "GUI == CLI, byte for byte" fails on the last
    # digits.  The rounding costs nothing: with 12 decimals the accumulated error
    # over 10^7 steps is still below a microkelvin.
    return Ramp(start=float(start), end=float(end), steps=int(steps),
                timestep=float(timestep),
                factor=round((float(end) / float(start)) ** exponent, 12),
                mechanism=mechanism)


def _heating_keywords(spec: Spec, md: Section, ensemble: str) -> None:
    """Write the heating/cooling keywords (and Langevin's own friction).

    Each keyword is only written when *this* ensemble can use it, which is what
    the coupling table promises the user:

    * ``&MD ANNEALING`` (velocity rescaling) with a particle thermostat makes
      CP2K abort, and the Langevin ensemble controls the temperature by itself;
    * ``&MD ANNEALING_CELL`` is implemented for NPE_I/NPE_F only;
    * ``&MD TEMPERATURE_ANNEALING`` is applied by the Langevin integrator only.

    A value that would not take effect is therefore dropped here rather than
    written out - the user is told by the coupling warning (CLI) and by the
    greyed-out row (GUI), and the validator reports it again for inputs that were
    written by hand.
    """
    if spec.annealing is not None and ensemble in VELOCITY_ANNEALING_ENSEMBLES:
        md.set("ANNEALING", float(spec.annealing), comment=(
            "每步把**速度**乘这个因子：温度大约按它的平方变化"
            "（<1 降温、>1 升温）。这不是恒温器，是直接加减动能，"
            "所以只能配没有粒子恒温器的系综（NVE/NPE_*）"))
    if spec.annealing_cell is not None and ensemble in CELL_ANNEALING_ENSEMBLES:
        md.set("ANNEALING_CELL", float(spec.annealing_cell), comment=(
            "同上，作用在晶胞速度上（只有 NPE_I/NPE_F 会用它）"))
    if spec.temperature_annealing is not None and ensemble == "LANGEVIN":
        md.set("TEMPERATURE_ANNEALING", float(spec.temperature_annealing),
               comment=("每步把 Langevin 的**目标温度**乘这个因子"
                        "（<1 降温、>1 升温）；只在 LANGEVIN 系综下生效"))
    if ensemble == "LANGEVIN":
        langevin = Section("LANGEVIN")
        langevin.set("GAMMA", float(spec.langevin_gamma), unit="fs^-1", comment=(
            "Langevin 的摩擦系数：温度和噪声都由它决定（CP2K 默认 0 = 既没有"
            "摩擦也没有噪声，等于 NVE）。0.01 fs^-1 = 10 ps^-1，耦合时间约 100 fs"))
        md.add(langevin)


def md_section(spec: Spec, structure: Structure | None) -> Section:
    md = Section("MD")
    ensemble = spec.ensemble.upper()
    md.set("ENSEMBLE", ensemble)
    md.set("STEPS", int(spec.steps))
    md.set("TIMESTEP", float(spec.timestep), unit="fs")
    # TEMPERATURE is read by *every* ensemble: the manual says it is used "to
    # initialize the velocities with init and pos restart, and in the NPT/NVT
    # simulations".  CP2K's own argon NVE test sets it for exactly that reason,
    # so leaving it out of an NVE run would silently give 300 K start velocities.
    md.set("TEMPERATURE", float(spec.temperature), unit="K", comment=(
        "initial velocities are drawn for this temperature; NVT/NPT keep it as "
        "the target temperature too; with annealing it is the starting point"))
    _heating_keywords(spec, md, ensemble)
    if spec.md_temp_kind_print:
        # the *keyword* is what makes CP2K compute it at all: with only the print
        # key on, md_energies.F just warns "The print_key MD%PRINT%TEMP_KIND has
        # been activated but the calculation of the temperature per kind has not
        # been requested. Please switch on the keyword MD%TEMP_KIND." - and
        # writes nothing.  The tool always writes both.
        md.set("TEMP_KIND", True, comment=(
            "compute the temperature of each atomic kind separately "
            f"({_project_name(spec)}-1.temp)"))
    # NB: no TEMP_TOL.  It is the *obsolescent* velocity-rescaling escape hatch
    # and CP2K's own default is 0 (off): "The maximum accepted deviation of the
    # (global) temperature ... before a rescaling of the velocities is performed.
    # If it is 0 no rescaling is performed. NOTE: This keyword is obsolescent;
    # Using a CSVR thermostat with a short timeconstant is recommended as a
    # better alternative." (src/motion/input_cp2k_md.F).  Writing 50 K here - as
    # this tool used to - switched that coarse rescaling back on top of the
    # thermostat; temperature control belongs to the thermostat alone.
    #
    # The LANGEVIN ensemble is the exception to the &THERMOSTAT rule below: its
    # friction and random force *are* the thermostat (SUBROUTINE langevin in
    # src/motion/integrator.F never touches the &THERMOSTAT machinery, and not
    # one of CP2K's own LANGEVIN test inputs writes the block), so it gets
    # &MD/&LANGEVIN GAMMA instead - see _heating_keywords.
    if ensemble not in ("NVE", "LANGEVIN") and spec.thermostat.upper() != "NONE":
        kind = spec.thermostat.upper()
        thermostat = Section("THERMOSTAT")
        thermostat.set("TYPE", kind)
        region = thermostat_region_of(spec)
        if region != "GLOBAL":
            thermostat.set("REGION", region, comment=THERMOSTAT_REGION_NOTES[region])
        if kind in ("NOSE", "CSVR"):
            block = Section(kind)
            block.set("TIMECON", float(spec.thermostat_timecon), unit="fs")
            thermostat.add(block)
        elif kind == "GLE":
            thermostat.add(Section("GLE", comment=(
                "GLE needs the S and A matrices here; see "
                f"{MANUAL}CP2K_INPUT/MOTION/MD/THERMOSTAT/GLE.html")))
        elif kind == "AD_LANGEVIN":
            thermostat.add(Section("AD_LANGEVIN"))
        if spec.thermostat_temp_print:
            printer = Section("PRINT")
            printer.add(_temperature_print(
                spec, spec.thermostat_temp_every, region=region))
            thermostat.add(printer)
        md.add(thermostat)

    if ensemble in NPT_ENSEMBLES:
        barostat = Section("BAROSTAT")
        barostat.set("PRESSURE", float(spec.pressure), unit="bar")
        barostat.set("TIMECON", float(spec.barostat_timecon), unit="fs")
        md.add(barostat)
    printer = md_print_section(spec)
    if printer is not None:
        md.add(printer)
    return md


#: ``&MOTION/&MD/&THERMOSTAT REGION`` values the tool offers, with the sentence
#: written next to the keyword.  CP2K's own sixth value (``NONE``, no thermostat
#: at all) is spelled ``--thermostat NONE`` here, and ``DEFINED``/``THERMAL``
#: both need ``&DEFINE_REGION`` subsections the tool does not generate.
THERMOSTAT_REGION_NOTES = {
    "MOLECULE": ("one thermostat per molecule kind - a separate heat bath for "
                 "each component of a mixture"),
    "MASSIVE": "one thermostat per degree of freedom (massive Nose-Hoover)",
}


def thermostat_region_of(spec: Spec) -> str:
    """``REGION`` as it will be written: unknown values fall back to GLOBAL."""
    region = (spec.thermostat_region or "GLOBAL").upper()
    return region if region in ("GLOBAL", "MOLECULE", "MASSIVE") else "GLOBAL"


def _every_section(level: str, every: int) -> Section:
    """``&EACH <level> <n>``: how often a print key writes."""
    each = Section("EACH")
    each.set(level, int(every))
    return each


def _temperature_print(spec: Spec, every: int, region: str = "GLOBAL") -> Section:
    """``&THERMOSTAT/&PRINT/&TEMPERATURE``: the temperature per region.

    ``thermostat_utils.F:print_thermostat_status`` writes
    ``<project>-1.<label>.temp`` with a "# Temperature Total and per Region"
    header; the label of the ordinary particle thermostat is ``PARTICLES``
    (``thermostat_methods.F``).
    """
    what = ("temperature of every thermostat region, one target+actual column "
            "pair per region"
            if region != "GLOBAL" else
            "temperature of the thermostat region (REGION GLOBAL = the whole "
            "system; the same number the -1.ener file carries in its Temp[K] "
            "column)")
    temperature = Section("TEMPERATURE", "ON", comment=(
        f"{what} ({_project_name(spec)}-1.PARTICLES.temp)"))
    # ``&EACH`` is always written, even for 1: the interval then round-trips
    # through `importinp` (which reads it back) instead of depending on a default
    temperature.add(_every_section("MD", int(every)))
    return temperature


def md_print_section(spec: Spec) -> Section | None:
    """``&MOTION/&MD/&PRINT``: what an MD run writes *while* it runs.

    Two files matter here and both are about temperature:

    * ``&ENERGY`` -> ``<project>-1.ener``, whose ``Temp[K]`` column is the
      temperature history (CP2K writes it from ``PRINT_LEVEL LOW`` on, so this
      is where its interval and name are set);
    * ``&TEMP_KIND`` -> ``<project>-1.temp``, the temperature *per atomic kind*
      (the extension is ``.temp``, not ``.tempkind``: ``md_energies.F`` passes
      ``extension=".temp"``), which is how a mixture can be seen to equilibrate.
      It only produces numbers together with ``&MD TEMP_KIND T``, so the two are
      written as one switch here.

    Nothing is written when neither is asked for: CP2K's own defaults then apply
    (``ENERGY`` at ``PRINT_LEVEL``, ``TEMP_KIND`` off).
    """
    prints: list[Section] = []
    if spec.md_energy_print:
        energy = Section("ENERGY", "ON", comment=(
            f"the {_project_name(spec)}-1.ener file: step / time / "
            "Kin / Temp[K] / Pot / Cons Qty / UsedTime, one line per write"))
        energy.add(_every_section("MD", int(spec.md_energy_every)))
        if spec.md_energy_file and str(spec.md_energy_file).strip():
            energy.set("FILENAME", str(spec.md_energy_file).strip())
        prints.append(energy)
    if spec.md_temp_kind_print:
        temp_kind = Section("TEMP_KIND", "ON", comment=(
            "temperature of each atomic kind, separately "
            f"({_project_name(spec)}-1.temp); needs MD%TEMP_KIND T above, which "
            "cp2kbuild writes with it"))
        temp_kind.add(_every_section("MD", int(spec.md_temp_kind_every)))
        prints.append(temp_kind)
    if not prints and spec.task != "md":
        return None
    printing = Section("PRINT")
    if spec.task == "md":
        # A stop at the walltime (or an EXIT_MD file) writes the restart and the
        # trajectory of the final step only with FORCE_LAST - its default is F, so
        # a job killed at the walltime lost the last <=20 steps of its restart
        # (md_run.F / md_output; the manual: "Print the output and restart file if
        # walltime is reached or if an external EXIT command is given").
        printing.set("FORCE_LAST", True, comment=(
            "墙钟到点或被 EXIT_MD 停下时，把最后一步的 restart/轨迹也写出来"
            "（CP2K 默认是 F，会丢掉最后 ≤20 步的落点）"))
    for block in prints:
        printing.add(block)
    return printing


def _motion_print(spec: Spec, periodic: bool) -> Section:
    print_section = Section("PRINT")
    # the switch is authoritative: an interval on its own must not force the
    # file back on after the user switched it off
    if spec.motion_restart_print:
        restart = Section("RESTART", "ON", comment=(
            f"run restart file {_project_name(spec)}-1.restart: what &EXT_RESTART "
            "reads back"))
        level = spec.task.upper() if spec.task != "md" else "MD"
        each = Section("EACH")
        # ``&EACH`` and ``ADD_LAST`` are two independent settings: the first says
        # how often during the run, the second whether the *last* iteration is
        # written as well (0 + ADD_LAST NUMERIC = one file at the end)
        each.set(level, int(spec.restart_every if spec.restart_every is not None else 20))
        restart.add(each)
        restart.set("ADD_LAST", "NUMERIC" if spec.restart_add_last else "NO",
                    comment="write the last iteration as well"
                    if spec.restart_add_last else
                    (f"with {level} {spec.restart_every} and ADD_LAST NO the file is "
                     "written only every N steps" if spec.restart_every else
                     "with no interval and no ADD_LAST this file is never written"))
        if spec.restart_backups is not None:
            restart.set("BACKUP_COPIES", int(spec.restart_backups))
        print_section.add(restart)
    trajectory = Section("TRAJECTORY", "ON")
    each = Section("EACH")
    each.set(spec.task.upper() if spec.task != "md" else "MD",
             int(spec.trajectory_stride))
    trajectory.add(each)
    print_section.add(trajectory)
    print_section.add(Section("VELOCITIES", "ON"))
    if periodic:
        print_section.add(Section("CELL", "ON"))
    return print_section


def band_section(spec: Spec) -> Section:
    band = Section("BAND", comment="nudged elastic band")
    band_type = spec.band_type.upper()
    band.set("BAND_TYPE", band_type)
    band.set("NUMBER_OF_REPLICA", int(spec.replicas or len(spec.replica_files)))
    band.set("K_SPRING", float(spec.k_spring))
    band.set("ROTATE_FRAMES", True)
    # only CI-NEB has a dedicated sub-block; every other type is configured
    # through the shared convergence control
    if band_type == "CI-NEB":
        cineb = Section("CI_NEB")
        cineb.set("NSTEPS_IT", 5)
        band.add(cineb)
    convergence = Section("CONVERGENCE_CONTROL")
    convergence.set("MAX_FORCE", 1.0e-3)
    convergence.set("RMS_FORCE", 5.0e-4)
    band.add(convergence)
    if spec.replica_files:
        for path in spec.replica_files:
            replica = Section("REPLICA")
            # forward slashes keep the file portable between Windows and Linux
            replica.set("COORD_FILE_NAME", str(path).replace("\\", "/"))
            band.add(replica)
    else:
        band.add(Section("REPLICA", comment=(
            "one &REPLICA block per image, each with COORD_FILE_NAME; "
            f"{MANUAL}CP2K_INPUT/MOTION/BAND/REPLICA.html")))
    return band


def vibrational_section(spec: Spec, structure: Structure | None) -> Section:
    section = Section("VIBRATIONAL_ANALYSIS", comment=(
        "harmonic frequencies by finite differences of the forces"))
    if spec.intensities and structure is not None and not structure.is_periodic():
        section.set("INTENSITIES", True,
                    comment="IR intensities from the Born charges / dipoles")
    if spec.thermochemistry:
        section.set("THERMOCHEMISTRY", True)
    print_section = Section("PRINT")
    if spec.molden_vib:
        print_section.add(Section("MOLDEN_VIB", "ON", comment=(
            "normal modes in Molden format, so the vibrations can be animated "
            "in a viewer")))
    section.add(print_section)
    return section


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #
def build(spec: Spec, index: DataIndex) -> BuildResult:
    """Build a complete input for ``spec``."""
    from .structures import read_structure

    structure = prepare_structure(spec.structure, spec)
    if structure is None and spec.task == "neb" and spec.replica_files:
        # an NEB input still needs a cell and the KIND blocks; take them from
        # the first image when no separate structure was given
        try:
            structure = prepare_structure(read_structure(spec.replica_files[0]), spec)
        except Exception:  # noqa: BLE001 - the image may not exist yet
            structure = None
    elements = list(structure.elements) if structure else []
    if spec.task == "neb" and not elements and spec.replica_files:
        elements = _elements_from_replicas(spec.replica_files)

    target = _target_kind(spec, structure)
    classical = is_classical(spec)
    if classical:
        # a force field needs no basis set and no pseudopotential: asking the data
        # index for them would only produce "element not covered" noise
        setup = Setup()
    else:
        setup = choose_assignments(
            elements, index,
            functional=spec.functional,
            target=target,
            basis_override=spec.basis,
            potential_override=spec.potential,
            basis_file_override=spec.basis_file,
            potential_file_override=spec.potential_file,
        )
    if spec.data_path_style == "absolute" and index.directory:
        setup.basis_files = [str(index.directory / f).replace("\\", "/")
                             for f in setup.basis_files]
        setup.potential_files = [str(index.directory / f).replace("\\", "/")
                                 for f in setup.potential_files]

    run_type = RUN_TYPE_OF.get(spec.task, "ENERGY")
    sections = [global_section(spec, run_type)]
    ext_restart = ext_restart_section(spec)
    if ext_restart is not None:
        sections.append(ext_restart)
    force_eval = force_eval_section(spec, setup, structure, elements)
    if spec.wfn_restart_file and not classical:
        dft_block = force_eval.subsection("DFT")
        if dft_block is not None:
            dft_block.set("WFN_RESTART_FILE_NAME", spec.wfn_restart_file)
    sections.append(force_eval)
    motion = motion_section(spec, structure)
    if motion is not None:
        sections.append(motion)
    if spec.task == "freq":
        sections.append(vibrational_section(spec, structure))

    notes = _header_notes(spec, setup, structure, elements, run_type)
    notes.extend(_restart_notes(spec))
    if spec.metadyn is not None and spec.metadyn.use_plumed and spec.task == "md":
        # the two ways a PLUMED run dies before it starts (metadynamics.F:166 and
        # metadynamics.F:331 - both cp_abort, so they are worth a header line)
        notes.append(
            "plumed        : 这条路线要一个**编译时带 PLUMED2** 的 CP2K，否则它直接"
            "中止（\"Requested to use plumed for metadynamics, but cp2k was not "
            "compiled with plumed support.\"）；运行时还要能找到 PLUMED 库"
            "（否则 \"NO PLUMED IN YOUR LD_LIBRARY_PATH?\"）。"
            f"CV/丘陵/输出文件都在 {spec.metadyn.plumed_file} 里")
    warnings = list(setup.problems)
    errors: list[str] = []
    covered: set[str] = set()
    classical_errors, classical_warnings, classical_covered = _classical_findings(
        spec, elements, structure)
    errors.extend(classical_errors)
    warnings.extend(classical_warnings)
    covered |= classical_covered
    warnings.extend(coordinate_source_warnings(spec, structure))
    if spec.metadyn is not None:
        natoms = len(structure.atoms) if structure is not None else None
        if spec.task != "md":
            if spec.metadyn.enabled:
                # Enabling the block and then switching the task is easy to do (the
                # block only exists on the MD page, so once the task is something
                # else it is not even on screen) - and CP2K would silently ignore
                # the sections.  Say what is dropped *and* how to make it stop.
                warnings.append(
                    f"元动力学只在 MD 任务里生效：这是 {spec.task}，所以不写 "
                    "&SUBSYS/&COLVAR，也不写 &MOTION/&FREE_ENERGY/&METADYN"
                    "（CP2K 在别的 RUN_TYPE 下根本不读这两段）。"
                    "要跑元动力学就把任务改成 MD；这次不需要的话，"
                    "在 MD 页把「启用元动力学」取消勾选，这条提醒就不会再出现"
                    f"（metadynamics only applies to an MD run）")
        else:
            # the MD temperature is passed in because well-tempered metadynamics
            # takes its reference temperature from `&MD TEMPERATURE` (md_run.F:400),
            # not from `&METADYN TEMPERATURE`
            warnings.extend(metadyn_problems(spec.metadyn, natoms, spec.temperature))
    if spec.h_to_d:
        # the GUI greys the control out (cp2kbuild/couplings.py); these two are
        # for the library route, where nobody sees the form
        if classical:
            warnings.append(
                "氘代（h_to_d）对经典力场（METHOD FIST）不起作用：原子质量来自拓扑"
                "文件（AMBER prmtop 的 %FLAG MASS、CHARMM 的 PSF），不是 &KIND ——"
                "要改质量得改那里")
        elif elements and "H" not in {element.upper() for element in elements}:
            warnings.append(
                "氘代（h_to_d）：体系里没有氢，什么都不会变")
    if spec.task == "neb" and not _has_cell(structure):
        warnings.append(
            "NEB 没有晶胞：每个镜像都需要一个模拟盒子。"
            "用 --vacuum <Å>（或 --cell）让输入里出现 &CELL 段"
            "（不然每张图像都会落在 CP2K 默认的 1 Å 立方体里）")
    return BuildResult(sections, setup, notes, run_type, warnings=warnings,
                       errors=errors, covered_codes=covered)


def _classical_findings(spec: Spec, elements: list[str],
                        structure: Structure | None = None
                        ) -> tuple[list[str], list[str], set[str]]:
    """What a FIST input is still missing: ``(errors, warnings, covered)``.

    The split matters: a force-field run with **no coordinates at all** (no
    ``COORD_FILE_NAME`` and no structure to inline) is not a "check this" - CP2K
    aborts on it ("No atomic coordinates have been found!"), so it is an error.
    The same goes for the three things a file-based route cannot run without: a
    ``PARM_FILE_NAME``, a ``CONN_FILE_NAME`` and a ``&CELL``.

    ``covered`` carries the validator codes these findings already answer for, so
    the merged report does not print the same sentence twice at two different
    levels (that is exactly what happened: `警告 AMBER: no PARM_FILE_NAME` next to
    `错误 PARMTYPE AMBER ... PARM_FILE_NAME is not set`).
    """
    if not is_classical(spec):
        return [], [], set()
    route = mm_route(spec)
    errors: list[str] = []
    out: list[str] = []
    covered: set[str] = set()
    if route.conn_file and not _has_cell(structure):
        # No &CELL at all.  CP2K only falls back to the coordinate file when
        # CELL_FILE_NAME/ABC/A,B,C are all absent, and only CIF/CP2K/PDB/EXTXYZ
        # are parseable there (cell_methods.F "case 4"); everything else - the
        # AMBER inpcrd above all - hits cp_abort, and the box inside an inpcrd is
        # ignored even when it is there.  So: an error, unless the named
        # coordinate file is one CP2K would read a cell from.
        cell_format = coord_format_for(spec, route)
        covered.add("periodic-without-cell")
        if not spec.mm_coord_file:
            errors.append(
                f"{route.key}：没有 &CELL，也没有坐标文件 —— CP2K 先看 "
                "CELL_FILE_NAME/ABC/A,B,C，都没有就退回坐标文件，而这一格是空的，"
                "于是中止（cell_methods.F 的 case 4：\"COORD_FILE_NAME is not set, "
                "so no cell information is available!\"）。"
                "填「4. 体系」的「晶胞覆盖」（命令行 --cell a,b,c）")
        elif cell_format in CELL_FROM_COORD_FORMATS:
            out.append(
                f"{route.key}：没有 &CELL，CP2K 会去坐标文件（{cell_format}）里找盒子"
                "（cell_methods.F 的 case 4 只对 "
                + "/".join(CELL_FROM_COORD_FORMATS) + " 这么做），文件里没有盒子就中止："
                "\"No meaningful cell information is read from parser!\"。"
                "把「4. 体系」的「晶胞覆盖」填上（命令行 --cell a,b,c）就不用赌这一步")
        else:
            errors.append(
                f"{route.key}：没有 &CELL，而坐标格式是 {cell_format} —— 它不在 CP2K "
                "会去读盒子的那四种里（"
                + "/".join(CELL_FROM_COORD_FORMATS) + "），坐标文件这条路走不通，"
                "直接中止（cell_methods.F 的 case 4：\"COORD_FILE_FORMAT is not set "
                "to one of the implemented CELL_FILE_FORMAT options and cannot be "
                "parsed for cell information!\"）"
                + ("；AMBER 的 inpcrd 连文件里的 BOX 都会被忽略"
                   "（topology_amber.F 明说了 \"BOX information found in CRD file. "
                   "They will be ignored.\"）" if cell_format == "CRD" else "")
                + "。填「4. 体系」的「晶胞覆盖」（命令行 --cell a,b,c）")
    if spec.task in ("band", "pdos"):
        out.append(
            f"{spec.task} 要的是本征值，而经典力场没有电子结构："
            "能带和 PDOS 得用 METHOD QUICKSTEP")
    if route.parm_file and not spec.mm_parm_file:
        # CP2K: force_fields_input.F, "Force Field Parameter's filename is empty!
        # Please check your input file." - a certain abort, so an error (the
        # validator's `mm-parmtype-without-file` rule says the same thing; this
        # one names the control and the flag).
        errors.append(
            f"{route.key}：PARMTYPE {route.parmtype} 的力场要从文件里读，但 "
            "PARM_FILE_NAME 是空的 —— CP2K 直接中止（force_fields_input.F："
            "\"Force Field Parameter's filename is empty! Please check your input "
            f"file.\"）。在「3. 经典力场（METHOD FIST）」的「参数文件」里选一个"
            f"（命令行 --mm-parm-file <file>）")
        covered.add("mm-parmtype-without-file")
    if route.conn_file and not conn_file_for(spec, route) and not spec.mm_molecules:
        # Empty CONN_FILE_NAME reaches the file parser as an empty name, which
        # aborts in cp_files.F ("The specified OLD file <> cannot be opened").
        errors.append(
            f"{route.key}：CONNECTIVITY {route.connectivity} 的连接性要从文件里读，"
            "但 CONN_FILE_NAME 是空的 —— CP2K 拿一个空文件名去开文件，"
            "直接中止（cp_files.F：\"The specified OLD file <> cannot be opened. "
            "It does not exist.\"）。填「3. 经典力场」的「拓扑文件」"
            "（命令行 --mm-conn-file <file>；多组分体系用 --mm-molecule）"
            + ("（AMBER/GROMOS 下填了参数文件就会自动用它）"
               if route.same_file else ""))
        covered.add("mm-connectivity-without-file")
    if route.conn_file and not spec.mm_coord_file and spec.structure is None:
        # **error**: this input has no atoms anywhere - CP2K stops with
        # "No atomic coordinates have been found!" (topology.F)
        errors.append(
            f"{route.key}：既没有坐标文件（COORD_FILE_NAME），也没有结构可以写成 "
            "`&COORD`，所以这份输入里**一个原子都没有**——CP2K 会直接中止"
            "（topology.F 的 coordinate_control: \"No atomic coordinates have been "
            "found!\"）。两条路：填「坐标文件」（AMBER 用 inpcrd/CRD），"
            "或者载入一个结构（比如上一轮的 -1.restart，工具会把坐标写成 &COORD）")
    if spec.mm_molecules and not spec.mm_coord_file:
        out.append("CONNECTIVITY MOL_SET 用一个坐标文件拼出多个分子："
                   "请填「坐标文件」（命令行 --mm-coord-file）")
    if route.key == "PAIR" and not (spec.mm_lj or spec.mm_element_lj):
        out.append("对势路线没有 ε/σ：每个 &LENNARD-JONES 段两个都缺，"
                   "吸引力就是零。用 --lj 元素:ε:σ（ε 单位 K，σ 单位 Å）")
    if route.key == "IN_INPUT" and not spec.mm_ff_file:
        out.append("参数写在输入里的路线却没有力场文件："
                   "--mm-ff-file <file> 提供 &BOND/&BEND/&CHARGE 这些条款")
    for label, path in _classical_files(spec):
        if path and not Path(path).exists():
            out.append(f"{label} 在这里不存在：{path}")
    for element in dict.fromkeys(elements):
        if element not in MM_KNOWN_ELEMENTS and route.key in ("IN_INPUT", "PAIR"):
            out.append(f"{element!r} 不是已知的元素符号；"
                       "FIST 的力场是按元素找原子的")
    return errors, out, covered


def _classical_files(spec: Spec) -> list[tuple[str, str | None]]:
    """Every file a FIST input points at, so the build can check it is there."""
    route = mm_route(spec)
    files: list[tuple[str, str | None]] = []
    if route.parm_file:
        files.append(("力场参数文件", spec.mm_parm_file))
    if spec.mm_molecules:
        for index, molecule in enumerate(spec.mm_molecules, start=1):
            files.append((f"第 {index} 个组分的拓扑文件", molecule.conn_file))
    elif route.conn_file:
        files.append(("拓扑文件", conn_file_for(spec, route)))
    if route.conn_file or spec.mm_molecules:
        files.append(("坐标文件", spec.mm_coord_file))
    if spec.mm_ff_file:
        files.append(("写在输入里的力场条款文件", spec.mm_ff_file))
    return files


#: Element symbols the classical routes can address.  CP2K maps AMBER/PSF atom
#: names onto elements with its own table, so anything outside this list would
#: only be correct with an explicit &KIND mapping.
MM_KNOWN_ELEMENTS = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn
Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La
Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po
At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg
Cn Nh Fl Mc Lv Ts Og
""".split())


def _elements_from_replicas(files: list[str]) -> list[str]:
    from .structures import read_structure

    if not files:
        return []
    try:
        return read_structure(files[0]).elements
    except Exception:  # noqa: BLE001 - the file may not exist yet
        return []


def _has_cell(structure: Structure | None) -> bool:
    return structure is not None and structure.cell is not None


def _header_notes(spec: Spec, setup: Setup, structure: Structure | None,
                  elements: list[str], run_type: str) -> list[str]:
    notes: list[str] = []
    classical = is_classical(spec)
    label = "aimd" if spec.task == "md" else spec.task
    if spec.task == "md":
        notes.append(f"task          : "
                     + ("classical md" if classical else label)
                     + (f"  (RUN_TYPE MD, METHOD {spec.method.upper()}: "
                        "the force field is evaluated every step, no SCF)"
                        if classical else
                        "  (RUN_TYPE MD, METHOD QUICKSTEP: Born-Oppenheimer, "
                        "DFT forces every step)"))
    else:
        notes.append(f"task          : {label}  (RUN_TYPE {run_type}"
                     + (", METHOD FIST)" if classical else ")"))
    if structure is not None:
        periodic = (structure.periodic or "NONE").upper()
        cell = structure.cell
        if cell is not None and structure.n_atoms() == 0:
            a, b, c = cell.lengths()
            notes.append("system        : 原子来自坐标文件（本工具不读它，只写 "
                         "COORD_FILE_NAME）——用 `cp2kbuild mmcheck` 核对拓扑与坐标")
            notes.append(f"cell          : a={a:.4f} b={b:.4f} c={c:.4f} A，"
                         f"PERIODIC {periodic}")
        elif cell is not None:
            a, b, c = cell.lengths()
            alpha, beta, gamma = cell.angles()
            notes.append(
                f"system        : {structure.formula()}  "
                f"({structure.n_atoms()} atoms), PERIODIC {periodic}")
            notes.append(
                f"cell          : a={a:.4f} b={b:.4f} c={c:.4f} A, "
                f"angles {alpha:.2f}/{beta:.2f}/{gamma:.2f} deg")
            notes.append(f"cell volume   : {cell.volume():.2f} A^3")
        else:
            notes.append(
                f"system        : {structure.formula()}  "
                f"({structure.n_atoms()} atoms), no &CELL")
            notes.append(
                "cell          : CP2K would assume PERIODIC XYZ with an undefined "
                "cell; for an isolated molecule pass --vacuum 12 (or --cell) so "
                "that a real box and &POISSON PERIODIC NONE are written")
    if classical:
        route = mm_route(spec)
        notes.append(f"force field   : PARMTYPE {route.parmtype}  ({route.label})")
        # only report the files this route actually reads: switching routes in
        # the GUI keeps the old paths in the boxes, and they are ignored
        if route.parm_file and spec.mm_parm_file:
            notes.append(f"parameters    : {mm_path_for(spec, spec.mm_parm_file)}")
        if spec.mm_molecules:
            for molecule in spec.mm_molecules:
                notes.append(f"molecule      : NMOL {molecule.nmol} x "
                             f"{molecule.conn_format} {mm_path_for(spec, molecule.conn_file)}")
            notes.append("topology      : CONNECTIVITY MOL_SET builds every "
                         "molecule from the one coordinate file, in order")
        elif route.conn_file and conn_file_for(spec, route):
            inherited = ("（与参数文件同一个文件）"
                         if not spec.mm_conn_file and route.same_file else "")
            notes.append(f"topology      : CONNECTIVITY {route.connectivity}  "
                         f"{mm_path_for(spec, conn_file_for(spec, route))}{inherited}")
        if route.conn_file:
            if spec.mm_coord_file:
                notes.append(f"coordinates   : {coord_format_for(spec, route)}"
                             f"  {mm_path_for(spec, spec.mm_coord_file)}")
            elif coordinates_are_inline(spec):
                source = Path(spec.structure.source).name if spec.structure \
                    and spec.structure.source else "结构文件"
                notes.append(
                    f"coordinates   : 写在输入里的 `&COORD`（{spec.structure.n_atoms()} "
                    f"个原子，来自 {source}）——CP2K 的 restart 就是这么做的，"
                    "接力不需要 inpcrd")
                notes.append(
                    "atom names    : "
                    + ("AMBER 原子类型名（CA3/OH2…），元素是按名称推断的；"
                       "FIST 不需要元素，CP2K 从拓扑文件读原子"
                       if reads_as_cp2k_input(spec.structure.source) else
                       "按结构文件里的名字读入"))
        elif spec.mm_ff_file:
            notes.append(f"force-field terms written into the input, taken from "
                         f"{mm_path_for(spec, spec.mm_ff_file)}")
        vdw = spec.mm_vdw_scale14 if spec.mm_vdw_scale14 is not None else route.vdw_scale14
        ei = spec.mm_ei_scale14 if spec.mm_ei_scale14 is not None else route.ei_scale14
        if vdw is not None or ei is not None:
            notes.append(f"1-4 scaling   : VDW_SCALE14 {vdw} / EI_SCALE14 {ei}"
                         + (f"  ({route.scale_note})" if route.scale_note else ""))
        if spec.mm_element_lj and not spec.mm_lj:
            pairs = ", ".join(
                f"{p.atoms[0]}-{p.atoms[1]} {p.epsilon:.4g}K/{p.sigma:.4g}A"
                for p in lorentz_berthelot(spec.mm_element_lj))
            notes.append(f"LJ (mixed)    : {pairs}")
        return notes
    if elements:
        assigned = ", ".join(
            f"{el}:{a.basis}/{a.potential}" for el, a in setup.assignments.items())
        notes.append(f"basis/psp     : {assigned}")
    dispersion = normalise_dispersion(spec.dispersion)
    notes.append(f"functional    : {spec.functional.upper()}"
                 + (f" + {dispersion}" if dispersion else ""))
    if not dispersion and spec.task in ("energy", "geo_opt", "cell_opt", "md"):
        notes.append("dispersion    : none. Add `--dispersion d3bj` for molecular "
                     "crystals, adsorption energies and layered materials.")
    if setup.basis_files:
        notes.append(f"data files    : {', '.join(setup.basis_files)} + "
                     f"{', '.join(setup.potential_files)}")
        notes.append("run hint      : make sure CP2K finds them, e.g. "
                     "`export CP2K_DATA_DIR=<cp2k>/data`")
    for note in setup.notes:
        notes.append(f"note          : {note}")
    return notes
