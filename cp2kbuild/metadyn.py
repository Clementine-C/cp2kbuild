"""Metadynamics: the collective variables and the ``&METADYN`` block.

CP2K splits a metadynamics run over two places, and this module generates both:

* ``&FORCE_EVAL/&SUBSYS/&COLVAR`` (one block per collective variable) - the CV
  *definitions*: distance, angle, torsion, coordination number, a combination of
  other CVs, a distance (or RMSD) from a path of reference frames, or plain RMSD
  against one set of frames;
* ``&MOTION/&FREE_ENERGY/&METADYN`` - how the bias is grown: the hill height
  (``WW``), how often a hill is spawned (``NT_HILLS``, an **MD step interval**,
  not a number of hills), well-tempered metadynamics (``WELL_TEMPERED`` with
  ``DELTA_T``), the per-CV ``&METAVAR`` that points at a CV by its **1-based
  index**, gives it a hill width, a mass/friction (extended Lagrangian) and a
  wall around it, plus the ``&PRINT`` files worth having.

Two things about ``&METAVAR`` are easy to get wrong, and both are verified against
the source rather than the manual's wording:

* ``SCALE`` (alias ``WIDTH``) is **the width σ of the Gaussian hill in that
  direction**, not a factor applied to the CV: ``V = WW · Σ_j Π_k
  exp(-½·((s_k-s_k(t_j))/SCALE_k)²)`` (``input_cp2k_free_energy.F`` and
  ``metadynamics.F:1023-1031``, which reads it into ``metavar%delta_s``).  Its
  unit is CP2K's internal one (bohr for a length), and the **default 0 is
  special-cased as "infinitely wide"** - that dimension then contributes no force
  at all, so a native run whose ``&METAVAR`` has no ``SCALE`` does nothing.
* ``&METADYN TEMPERATURE`` is the temperature of the *collective variables* in the
  extended-Lagrangian/Langevin scheme (the manual says so, and ``md_run.F:400``
  confirms it).  Well-tempered metadynamics takes its reference temperature from
  the **MD temperature** (``simpar%temp_ext``), not from this keyword.

The mistakes that are easy to make and *valid* CP2K are collected by
:func:`problems` and surface as build warnings - ``DO_HILLS F`` (CP2K's default!)
in a run whose whole point is to spawn hills, a ``&METAVAR COLVAR n`` pointing at
a CV that does not exist, well-tempered without ``DELTA_T``, ``GAMMA`` without
``LANGEVIN``, a wall type that does not take the keyword it was given, and so on.

``USE_PLUMED T`` is the other route: CP2K then hands the bias to an external
PLUMED driver and the CVs come from the PLUMED input file, so the two routes are
alternatives rather than additions (the generator drops the ``&COLVAR`` blocks,
and the CLI refuses the combination outright).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .model import Section

#: the CV kinds this module can write, in the order the GUI offers them
CV_KINDS = ("distance", "angle", "torsion", "coordination", "combine", "path",
            "rmsd")

#: what the tool writes for ``NT_HILLS`` / ``WW`` unless told otherwise.  CP2K's
#: own default for NT_HILLS is 30, but for WW it is 0.1 Hartree - two orders of
#: magnitude above the usual 0.5-2 mHartree - so the tool has its own.
NT_HILLS_DEFAULT = 30
HILL_HEIGHT_DEFAULT = 1.0e-3

#: how many atom indices each simple kind needs (``None`` = a free list)
CV_ATOM_COUNT = {"distance": 2, "angle": 3, "torsion": 4}

#: the kinds whose value is a **length**, i.e. whose numbers are in bohr inside
#: CP2K (``&COLVAR`` distances and the RMSD-ish path CVs both come out of the
#: internal coordinates).  The GUI asks for angstroms on these and the generated
#: input carries ``[angstrom]``, so CP2K converts; angles are radians (CP2K's and
#: PLUMED's alike), coordination numbers are dimensionless, and a combination is
#: whatever its expression makes it.
LENGTH_KINDS = ("distance", "path", "rmsd")


def is_length(kind: str) -> bool:
    """True for the CV kinds whose value and parameters are lengths."""
    return (kind or "").lower() in LENGTH_KINDS

#: human labels, for the GUI/CLI and for error messages
CV_LABELS = {
    "distance": "距离 &DISTANCE",
    "angle": "键角 &ANGLE",
    "torsion": "二面角 &TORSION",
    "coordination": "配位数 &COORDINATION",
    "combine": "组合 &COMBINE_COLVAR",
    "path": "路径距离 &DISTANCE_FROM_PATH",
    "rmsd": "RMSD &RMSD",
}


@dataclass(frozen=True)
class Cv:
    """One ``&COLVAR`` block.

    Only the fields of the chosen ``kind`` are written; the rest stay at their
    defaults so a generated input never carries a keyword the user did not ask
    for.
    """

    kind: str = "distance"
    #: atom indices, 1-based as CP2K counts them; distance/angle/torsion use
    #: 2/3/4 of them, ``path`` uses the whole list
    atoms: tuple[int, ...] = ()
    #: &COORDINATION: element names (or explicit atom lists)
    kinds_from: str = ""
    kinds_to: str = ""
    atoms_from: tuple[int, ...] = ()
    atoms_to: tuple[int, ...] = ()
    r0: float | None = None
    nn: int | None = None
    nd: int | None = None
    #: &COMBINE_COLVAR: the expression over its local variables (CV1, CV2, ...)
    function: str = ""
    variables: tuple[str, ...] = ()
    parameters: tuple[str, ...] = ()
    values: tuple[float, ...] = ()
    children: tuple["Cv", ...] = ()
    #: &DISTANCE_FROM_PATH: the reference frames and how to measure against them
    frames: tuple[str, ...] = ()
    rmsd: bool = False
    distances_rmsd: bool = False
    align_frames: bool = False
    lam: float | None = None
    subset_type: str = ""

    @property
    def label(self) -> str:
        """The kind as the GUI/CLI spells it."""
        return CV_LABELS.get(self.kind, self.kind)

    def summary(self) -> str:
        """One line describing the CV, for the GUI table and for messages."""
        if self.kind in ("distance", "angle", "torsion"):
            return " ".join(str(atom) for atom in self.atoms) or "（没给原子序号）"
        if self.kind == "coordination":
            parts = []
            if self.kinds_from or self.kinds_to:
                parts.append(f"{self.kinds_from or '?'} → {self.kinds_to or '?'}")
            if self.atoms_from or self.atoms_to:
                parts.append(" ".join(str(a) for a in self.atoms_from)
                             + " → " + " ".join(str(a) for a in self.atoms_to))
            for name, value in (("R0", self.r0), ("NN", self.nn), ("ND", self.nd)):
                if value is not None:
                    parts.append(f"{name} {value:g}")
            return "，".join(parts) or "（没给定义）"
        if self.kind == "combine":
            return f"{self.function or '（没写 FUNCTION）'}（{len(self.children)} 个子 CV）"
        if self.kind in ("path", "rmsd"):
            parts = []
            if self.atoms:
                parts.append(" ".join(str(atom) for atom in self.atoms))
            parts.append(f"{len(self.frames)} 个参考构型")
            if self.kind == "path":
                if self.rmsd:
                    parts.append("RMSD")
                if self.distances_rmsd:
                    parts.append("DISTANCES_RMSD")
                if self.lam is not None:
                    parts.append(f"LAMBDA {self.lam:g}")
            if self.align_frames:
                parts.append("先对齐参考构型")
            if self.subset_type:
                parts.append(self.subset_type)
            return "，".join(parts)
        return self.kind


#: the wall types CP2K knows (``NONE`` = no wall at all, so it is not listed)
WALL_TYPES = ("REFLECTIVE", "QUADRATIC", "QUARTIC", "GAUSSIAN")

#: which keywords each wall type actually accepts, and which it needs
_WALL_KEYWORDS = {
    "REFLECTIVE": (("DIRECTION",), ()),
    "QUADRATIC": (("DIRECTION", "K"), ("K",)),
    "QUARTIC": (("DIRECTION", "K"), ("K",)),
    "GAUSSIAN": (("SIGMA", "WW"), ("SIGMA", "WW")),
}


@dataclass(frozen=True)
class Wall:
    """``&METAVAR/&WALL``: a restraint that keeps the CV inside a range.

    The keywords are per type: a reflective wall only takes ``DIRECTION``, the
    quadratic/quartic ones take ``DIRECTION`` and ``K``, and a Gaussian wall takes
    ``SIGMA`` and ``WW`` (no direction at all).  Writing the wrong one is a CP2K
    input error, so :func:`_metavar_section` filters rather than emitting blindly.
    """

    position: float | None = None
    type: str = "QUADRATIC"
    direction: str = "WALL_PLUS"
    k: float | None = None
    sigma: float | None = None
    ww: float | None = None


@dataclass(frozen=True)
class Metavar:
    """``&METAVAR``: which CV the bias acts on, and how strongly."""

    colvar: int = 1                 # 1-based index into Metadyn.cvs
    #: SCALE (alias WIDTH): the width σ of the hill along this CV, in the CV's own
    #: unit (angstrom in the GUI/form, bohr in the file - see ``is_length``).
    #: ``None``/0 is CP2K's "infinitely wide", i.e. no bias along this CV.
    scale: float | None = None
    wall: Wall | None = None
    #: extended-Lagrangian scheme (``&METADYN LAGRANGE T``)
    mass: float | None = None
    gamma: float | None = None
    lam: float | None = None        # LAMBDA


@dataclass
class Metadyn:
    """Everything the ``&METADYN`` block and its ``&COLVAR`` blocks need."""

    enabled: bool = False
    #: USE_PLUMED: the bias comes from an external PLUMED input file instead
    use_plumed: bool = False
    plumed_file: str = "plumed.dat"
    #: DO_HILLS: CP2K's default is F, i.e. no hill is ever spawned
    do_hills: bool = True
    #: NT_HILLS: MD steps between two hills (despite the name).  CP2K's own
    #: default is 30; a **negative** value means "spawn nothing, only use the
    #: hills carried in from a restart file" (manual: `NT_HILLS`).
    nt_hills: int = NT_HILLS_DEFAULT
    #: WW: the height of the Gaussian that is spawned
    hill_height: float = HILL_HEIGHT_DEFAULT
    #: WELL_TEMPERED + DELTA_T (the temperature *difference*); its reference
    #: temperature is the MD one, so DELTA_T alone is what CP2K asks for
    well_tempered: bool = False
    #: ``&METADYN TEMPERATURE``: the temperature the *collective variables* are
    #: thermostatted to in the extended-Lagrangian/Langevin scheme.  It is **not**
    #: the well-tempered reference temperature (that is the MD temperature).
    temperature: float | None = None
    delta_t: float | None = None
    min_disp: float | None = None
    hill_tail_cutoff: float | None = None
    #: LAGRANGE: run the CVs on their own (extended Lagrangian) dynamics
    lagrange: bool = False
    #: LANGEVIN: integrate those CVs with a Langevin thermostat - without it the
    #: per-CV ``GAMMA`` is never read
    lagrange_langevin: bool = False
    metavars: tuple[Metavar, ...] = ()
    #: the CV definitions, in the order ``&METAVAR COLVAR n`` counts them
    cvs: tuple[Cv, ...] = ()
    #: &PRINT/&COLVAR and &PRINT/&HILLS (the files a post-mortem needs)
    print_colvar: bool = True
    print_hills: bool = True
    print_every: int = 1
    #: the default hill width σ for the CVs whose ``&METAVAR`` has no SCALE of
    #: its own.  It is the *same quantity* as ``SCALE`` and as PLUMED's ``SIGMA``:
    #: the native route writes it as ``SCALE`` (so the "SCALE 0 = infinitely wide"
    #: trap cannot happen), the legacy PLUMED draft writes it as ``SIGMA=``.  The
    #: unit follows the CV it applies to (angstrom for the length kinds, radians
    #: for angles, plain numbers for coordination).
    sigma: float | None = None
    #: the PLUMED-native table (:class:`cp2kbuild.plumedcv.PlumedSetup`).  PLUMED's
    #: CV language is not CP2K's, so when this has content the draft is rendered
    #: from *it* instead of translating the CP2K table - the two are alternatives,
    #: not layers.  It is annotated as a string to keep this module free of an
    #: import from the PLUMED side (which imports this one).
    plumed_setup: "PlumedSetup | None" = None

    def copy(self) -> "Metadyn":
        return replace(self, metavars=tuple(self.metavars), cvs=tuple(self.cvs))

    @property
    def active(self) -> bool:
        """True when this run really is a metadynamics run."""
        return self.enabled and (self.use_plumed or bool(self.cvs))

    def __str__(self) -> str:
        """One short line, for the "读参数" list in the GUI."""
        if self.use_plumed:
            return f"PLUMED 驱动（{self.plumed_file}）"
        if not self.enabled or not self.cvs:
            return "开了元动力学，但没有集体变量"
        flavors = []
        if self.well_tempered:
            flavors.append("井温")
        if self.lagrange and self.lagrange_langevin:
            flavors.append("扩展拉格朗日+Langevin")
        elif self.lagrange:
            flavors.append("扩展拉格朗日")
        if self.do_hills:
            hills = (f"每 {self.nt_hills} 步一个丘陵，高 {self.hill_height:g}"
                     if self.nt_hills > 0 else
                     f"NT_HILLS {self.nt_hills}：不撒新丘陵，只用接力进来的那些")
        else:
            hills = "DO_HILLS F（不撒丘陵）"
        return (f"{'、'.join(flavors) + '元动力学' if flavors else '元动力学'}："
                f"{len(self.cvs)} 个 CV、{len(self.metavars)} 个 &METAVAR，{hills}")


# --------------------------------------------------------------------------- #
# &FORCE_EVAL/&SUBSYS/&COLVAR
# --------------------------------------------------------------------------- #
def cv_section(cv: Cv) -> Section:
    """One ``&COLVAR`` block holding a single CV (CP2K allows one per block)."""
    colvar = Section("COLVAR")
    kind = cv.kind.lower()
    if kind == "distance":
        block = Section("DISTANCE")
        if cv.atoms:
            block.set("ATOMS", [int(a) for a in cv.atoms])
        colvar.add(block)
    elif kind == "angle":
        block = Section("ANGLE")
        if cv.atoms:
            block.set("ATOMS", [int(a) for a in cv.atoms])
        colvar.add(block)
    elif kind == "torsion":
        block = Section("TORSION")
        if cv.atoms:
            block.set("ATOMS", [int(a) for a in cv.atoms])
        colvar.add(block)
    elif kind == "coordination":
        block = Section("COORDINATION", comment=(
            "配位数 CV：KINDS_FROM 是中心原子、KINDS_TO 是被数的原子；"
            "R0 是切换距离，NN/ND 是切换函数的陡度"))
        if cv.kinds_from:
            block.set("KINDS_FROM", cv.kinds_from.upper())
        if cv.kinds_to:
            block.set("KINDS_TO", cv.kinds_to.upper())
        if cv.atoms_from:
            block.set("ATOMS_FROM", [int(a) for a in cv.atoms_from])
        if cv.atoms_to:
            block.set("ATOMS_TO", [int(a) for a in cv.atoms_to])
        if cv.r0 is not None:
            block.set("R0", float(cv.r0), unit="angstrom")
        if cv.nn is not None:
            block.set("NN", int(cv.nn))
        if cv.nd is not None:
            block.set("ND", int(cv.nd))
        colvar.add(block)
    elif kind == "combine":
        block = Section("COMBINE_COLVAR", comment=(
            "组合 CV：FUNCTION 里的变量名按顺序对应下面嵌套的 &COLVAR 块"
            "（CV1、CV2 …）"))
        if cv.function:
            block.set("FUNCTION", cv.function)
        if cv.variables:
            block.set("VARIABLES", list(cv.variables))
        if cv.parameters:
            block.set("PARAMETERS", list(cv.parameters))
        if cv.values:
            block.set("VALUES", [float(v) for v in cv.values])
        for child in cv.children:
            block.add(_nested_cv(child))
        colvar.add(block)
    elif kind == "path":
        block = Section("DISTANCE_FROM_PATH", comment=(
            "路径 CV：和 &FRAME 给出的参考构型比距离（RMSD 需要 DISTANCES_RMSD T）"))
        if cv.atoms:
            block.set("ATOMS", [int(a) for a in cv.atoms])
        if cv.align_frames:
            block.set("ALIGN_FRAMES", True)
        if cv.rmsd:
            block.set("RMSD", True)
        if cv.distances_rmsd:
            block.set("DISTANCES_RMSD", True)
        if cv.subset_type:
            block.set("SUBSET_TYPE", cv.subset_type.upper())
        if cv.lam is not None:
            block.set("LAMBDA", float(cv.lam))
        for frame in cv.frames:
            frame_section = Section("FRAME")
            frame_section.set("COORD_FILE_NAME", str(frame))
            block.add(frame_section)
        colvar.add(block)
    elif kind == "rmsd":
        block = Section("RMSD", comment=(
            "RMSD CV：和 &FRAME 给出的参考构型比 RMSD"
            "（SUBSET_TYPE WEIGHT_LIST 时用 WEIGHTS 给每个原子加权，本工具不生成"
            "权重表）"))
        if cv.atoms:
            block.set("ATOMS", [int(a) for a in cv.atoms])
        if cv.align_frames:
            block.set("ALIGN_FRAMES", True)
        if cv.subset_type:
            block.set("SUBSET_TYPE", cv.subset_type.upper())
        for frame in cv.frames:
            frame_section = Section("FRAME")
            frame_section.set("COORD_FILE_NAME", str(frame))
            block.add(frame_section)
        colvar.add(block)
    else:
        raise ValueError(f"unknown collective-variable kind {cv.kind!r}")
    return colvar


def _nested_cv(cv: Cv) -> Section:
    """A CV inside ``&COMBINE_COLVAR``: the same ``&COLVAR`` block as at top level."""
    return cv_section(cv)


def cv_blocks(metadyn: Metadyn) -> list[Section]:
    """One ``&COLVAR`` section per CV, ready to be added to ``&SUBSYS``.

    The order is the order ``&METAVAR COLVAR n`` counts in, so callers must not
    sort or reorder them.
    """
    return [cv_section(cv) for cv in metadyn.cvs]


# --------------------------------------------------------------------------- #
# &MOTION/&FREE_ENERGY/&METADYN
# --------------------------------------------------------------------------- #
def free_energy_section(metadyn: Metadyn) -> Section | None:
    """``&FREE_ENERGY`` with ``METHOD METADYN`` - or ``None`` when inactive."""
    if not metadyn.active:
        return None
    free_energy = Section("FREE_ENERGY")
    free_energy.set("METHOD", "METADYN", comment="自由能方法：元动力学")
    metadyn_block = metadyn_section(metadyn)
    if metadyn_block is not None:
        free_energy.add(metadyn_block)
    return free_energy


def metadyn_section(metadyn: Metadyn) -> Section | None:
    """The ``&METADYN`` block itself."""
    if not metadyn.active:
        return None
    block = Section("METADYN")
    if metadyn.use_plumed:
        block.set("USE_PLUMED", True, comment=(
            "把偏置交给外部 PLUMED 驱动：CV 和丘陵都写在 PLUMED 的输入文件里，"
            "本段其它参数不生效"))
        block.set("PLUMED_INPUT_FILE", metadyn.plumed_file or "plumed.dat")
        # Nothing else is read on this route: metadynamics_utils.F:129 says
        # "Check if using plumed. If so, only get the file name and read nothing
        # else", so DO_HILLS/NT_HILLS/WW/&METAVAR *and* the &PRINT keys below are
        # inert - the hills and colvar files are whatever PLUMED writes
        # (`FILE=HILLS` in plumed.dat), not `-1.hills`.
        return block
    if metadyn.lagrange:
        block.set("LAGRANGE", True, comment=(
            "扩展拉格朗日方案：CV 有自己的质量/摩擦（见各 &METAVAR）"))
        if metadyn.lagrange_langevin:
            block.set("LANGEVIN", True, comment=(
                "CV 自己用 Langevin 积分（各 &METAVAR 的 GAMMA 只有开了它才被读）"))
    block.set("DO_HILLS", bool(metadyn.do_hills), comment=(
        "是否真的撒丘陵（CP2K 默认 F，不写就等于只有约束墙、没有偏置）"))
    if metadyn.do_hills:
        block.set("NT_HILLS", int(metadyn.nt_hills), comment=(
            "两次撒丘陵之间的 **MD 步数**（名字里的 NT 容易误读成“丘陵个数”）"))
        block.set("WW", float(metadyn.hill_height), comment="高斯丘陵的高度")
    if metadyn.well_tempered:
        block.set("WELL_TEMPERED", True, comment="井温元动力学（WT-MetaD）")
        # `&METADYN TEMPERATURE` is the *CV* thermostat target of the extended
        # Lagrangian scheme, not the well-tempered reference temperature (that is
        # the MD temperature, md_run.F:400) - so it is only written when the
        # extended Lagrangian is actually on
        if metadyn.temperature is not None and metadyn.lagrange:
            block.set("TEMPERATURE", float(metadyn.temperature), unit="K",
                      comment="扩展拉格朗日/Langevin 方案里 CV 自己的恒温温度"
                              "（井温的参考温度不是它，而是 &MD 的 TEMPERATURE）")
        if metadyn.delta_t is not None:
            block.set("DELTA_T", float(metadyn.delta_t), unit="K", comment=(
                "井温的 ΔT：CP2K 用 &MD TEMPERATURE（simpar%temp_ext）当参考温度 T，"
                "偏置因子 (T+ΔT)/ΔT 由两者决定"))
    if metadyn.min_disp is not None:
        block.set("MIN_DISP", float(metadyn.min_disp), comment=(
            "CV 至少要移动这么多才放新的丘陵（滤掉噪声）；单位是 CV 自己的内部单位"
            "（长度类 = bohr），CP2K 这个关键字没有单位注解"))
    if metadyn.hill_tail_cutoff is not None:
        block.set("HILL_TAIL_CUTOFF", float(metadyn.hill_tail_cutoff), comment=(
            "把高斯尾部更快地压到 0（>0 才生效），单位是 SCALE 的倍数"))
    for metavar in metadyn.metavars:
        block.add(_metavar_section(metavar, metadyn.cvs, metadyn.sigma))
    _add_hill_print(block, metadyn)
    return block


def _metavar_section(metavar: Metavar, cvs: tuple[Cv, ...] = (),
                     default_sigma: float | None = None) -> Section:
    section = Section("METAVAR", comment=(
        f"第 {metavar.colvar} 个 &COLVAR（按 &SUBSYS 里出现的顺序数）作为这个"
        "偏置的自变量"))
    section.set("COLVAR", int(metavar.colvar))
    kind = (cvs[metavar.colvar - 1].kind
            if 0 < metavar.colvar <= len(cvs) else "")
    length = is_length(kind)
    # SCALE is the hill's width along this CV.  Without it CP2K's default 0 is
    # read as "infinitely wide", which silently switches the whole bias off, so
    # the panel-level default (if the user set one) is written out explicitly.
    scale = metavar.scale
    if scale is None and default_sigma:
        scale = default_sigma
    if scale is not None:
        section.set("SCALE", float(scale),
                    unit="angstrom" if length else None, comment=(
                        "SCALE（别名 WIDTH）= 这一维高斯的宽度 σ，不是 CV 的缩放因子；"
                        "V = WW·Σ Π exp(-½·((s-s0)/SCALE)²)。"
                        "CP2K 默认 0 被特判成“无限宽”，那一维就不加力了"
                        + ("（长度类 CV，单位 Å，CP2K 按 [angstrom] 换算）"
                           if length else "")))
    if metavar.mass is not None:
        section.set("MASS", float(metavar.mass))
    if metavar.gamma is not None:
        section.set("GAMMA", float(metavar.gamma))
    if metavar.lam is not None:
        section.set("LAMBDA", float(metavar.lam))
    if metavar.wall is not None:
        wall_type = (metavar.wall.type or "").upper()
        if wall_type and wall_type != "NONE":
            wall = Section("WALL", comment="约束墙：把 CV 挡在一个范围内")
            if metavar.wall.position is not None:
                wall.set("POSITION", float(metavar.wall.position),
                         unit="angstrom" if length else None,
                         comment=("墙所在的 CV 值"
                                  + ("（长度类 CV，单位 Å）" if length else "")))
            wall.set("TYPE", wall_type)
            wall.add(_wall_detail(metavar.wall, wall_type, length))
            section.add(wall)
    return section


def _wall_detail(wall: Wall, wall_type: str, length: bool = False) -> Section:
    """The nested ``&QUADRATIC``/``&QUARTIC``/``&GAUSSIAN``/``&REFLECTIVE`` block."""
    detail = Section(wall_type)
    allowed = _WALL_KEYWORDS.get(wall_type, ((), ()))[0]
    if "DIRECTION" in allowed:
        detail.set("DIRECTION", (wall.direction or "WALL_PLUS").upper())
    if "K" in allowed and wall.k is not None:
        detail.set("K", float(wall.k), unit="hartree",
                   comment="墙的力常数：K*(CV-POS)^2，单位 Hartree/CV单位²")
    if "SIGMA" in allowed and wall.sigma is not None:
        detail.set("SIGMA", float(wall.sigma),
                   unit="angstrom" if length else None,
                   comment="高斯墙的宽度 σ（CP2K 源码里指数带 ½：WW·exp(-½·((CV-POS)/σ)²)）")
    if "WW" in allowed and wall.ww is not None:
        detail.set("WW", float(wall.ww), unit="hartree", comment="高斯墙的高度")
    return detail


def _add_hill_print(block: Section, metadyn: Metadyn) -> None:
    """``&PRINT/&COLVAR`` and ``&PRINT/&HILLS``: the files a post-mortem needs."""
    if not (metadyn.print_colvar or metadyn.print_hills):
        return
    printing = Section("PRINT")
    for name, wanted, note in (
            ("COLVAR", metadyn.print_colvar,
             "CV 随时间的变化（-1.colvar）"),
            ("HILLS", metadyn.print_hills,
             "撒过的丘陵（-1.hills）：接着跑和画自由能面都要它")):
        if not wanted:
            continue
        key = Section(name, "ON", comment=note)
        each = Section("EACH")
        each.set("MD", int(metadyn.print_every))
        key.add(each)
        printing.add(key)
    if printing.sections:
        block.add(printing)


# --------------------------------------------------------------------------- #
# checks the section schema cannot make
# --------------------------------------------------------------------------- #
def _wall_problems(wall: Wall | None, index: int) -> list[str]:
    """Checks for one ``&WALL`` (the per-type keywords catch invalid CP2K input)."""
    if wall is None:
        return []
    wall_type = (wall.type or "").upper()
    if wall_type in ("", "NONE"):
        return []
    if wall_type not in WALL_TYPES:
        return [f"第 {index} 个 &METAVAR 的 &WALL TYPE {wall.type} 不是 CP2K 认识的"
                f"类型（可选 {'/'.join(WALL_TYPES)}，或 NONE 表示不加墙）"]
    out: list[str] = []
    if wall.position is None:
        out.append(f"第 {index} 个 &METAVAR 的 &WALL 没写 POSITION："
                   "墙的位置就是 CV 的那个阈值")
    values = {"K": wall.k, "SIGMA": wall.sigma, "WW": wall.ww}
    for name in _WALL_KEYWORDS[wall_type][1]:
        if values.get(name) is None:
            out.append(f"第 {index} 个 &METAVAR 的 {wall_type} 墙需要 {name}，"
                       "手册里这一项没有默认值，必须显式给")
    return out


def problems(metadyn: Metadyn, natoms: int | None = None,
             temperature: float | None = None) -> list[str]:
    """Physical-consistency problems in a metadynamics setup.

    The section/keyword schema is the validator's job; these are the mistakes
    that are *valid* CP2K but almost certainly not what was meant.

    ``temperature`` is the **MD** temperature (``&MD TEMPERATURE``): it is what
    well-tempered metadynamics uses as its reference temperature
    (``md_run.F:400``), and CP2K divides by it, so 0 is fatal there.
    """
    out: list[str] = []
    if not metadyn.enabled:
        return out
    if metadyn.use_plumed:
        # CP2K reads *only* PLUMED_INPUT_FILE from this section ("only get the file
        # name and read nothing else", metadynamics_utils.F:129), so the CV table is
        # not CP2K's business here.  What *is* worth checking is the PLUMED-native
        # table, if the form filled one in (imported lazily: cp2kbuild.plumedcv
        # imports this module).
        from .plumedcv import problems as plumed_problems

        if metadyn.plumed_setup is not None and metadyn.plumed_setup.active:
            out.extend(plumed_problems(metadyn.plumed_setup, natoms, temperature))
        elif metadyn.cvs:
            out.append("PLUMED 专用表是空的，草稿会按「把 CP2K 的 CV 表翻译过去」"
                       "生成：两种语言的单位/默认值/分量规则并不一致，"
                       "翻译出来的初稿请逐条核对（界面里可以改用 PLUMED 专用表）")
        return out
    if not metadyn.cvs:
        out.append("开了元动力学但没有定义任何集体变量：CP2K 会没有可偏置的自由度")
    if not metadyn.do_hills:
        out.append("DO_HILLS F（CP2K 的默认）：不会撒任何丘陵，这个运行不会产生偏置")
        if (metadyn.hill_height != HILL_HEIGHT_DEFAULT
                or metadyn.nt_hills not in (NT_HILLS_DEFAULT, -1)):
            # a negative NT_HILLS means "no new hills" as well, so that is not a
            # contradiction; a positive interval or a height is
            out.append(
                f"DO_HILLS F，但还留着丘陵参数（NT_HILLS {metadyn.nt_hills}、"
                f"WW {metadyn.hill_height:g}）：生成的输入里不会写这两项，"
                "CP2K 读了也不用——要撒丘陵就把 DO_HILLS 打开，"
                "要「只用接力带进来的丘陵」就把 NT_HILLS 写成负数"
                "（配合 &EXT_RESTART RESTART_METADYNAMICS T）")
    if metadyn.well_tempered:
        if not metadyn.delta_t:
            out.append("井温元动力学要写 DELTA_T > 0：CP2K 只认 DELTA_T（或 WTGAMMA），"
                       "参考温度取的是 &MD 的 TEMPERATURE，偏置因子 (T+ΔT)/ΔT 由"
                       "两者决定")
        if temperature is None:
            out.append("井温元动力学算不出参考温度 T（&MD TEMPERATURE）："
                       "CP2K 会直接拿 &MD 的温度去除（md_run.F:414），"
                       "温度是 0 时这里会算出无穷大")
        elif temperature <= 0:
            out.append(f"&MD TEMPERATURE 是 {temperature:g} K：井温元动力学要拿它当"
                       "参考温度做除法（md_run.F:414），必须是正数")
        if metadyn.temperature is not None and metadyn.lagrange is False:
            out.append("写了 &METADYN TEMPERATURE，但没开 LAGRANGE：这个关键字是给"
                       "扩展拉格朗日/Langevin 的 CV 恒温器用的，井温的参考温度是"
                       "&MD 的 TEMPERATURE，这里写它不起作用")
    for index, metavar in enumerate(metadyn.metavars, start=1):
        if not 1 <= metavar.colvar <= len(metadyn.cvs):
            out.append(f"第 {index} 个 &METAVAR 指向 COLVAR {metavar.colvar}，"
                       f"但只定义了 {len(metadyn.cvs)} 个 &COLVAR")
        if metavar.scale is None and not metadyn.sigma:
            out.append(
                f"第 {index} 个 &METAVAR 没给 SCALE，界面上也没填默认丘陵宽度："
                "SCALE 就是这一维高斯的宽度 σ，CP2K 的默认值 0 被特判成“无限宽”"
                "（metadynamics.F:1023-1031），这一维不会产生任何偏置力——"
                "整段 &METADYN 等于没有偏置")
        elif metavar.scale == 0:
            out.append(f"第 {index} 个 &METAVAR 的 SCALE 是 0：CP2K 把它特判成"
                       "“无限宽”（metadynamics.F:1023-1031），这一维不加任何偏置力。"
                       "要多个一维偏置叠加时才这么写，否则请给一个正的宽度"
                       "（长度类 CV 常见 0.1-0.3 Å）")
        out.extend(_wall_problems(metavar.wall, index))
    if metadyn.sigma:
        # one number cannot be a width in angstroms and a width in radians at the
        # same time: name the classes it is being reused across
        classes: list[str] = []
        for metavar in metadyn.metavars:
            if metavar.scale is not None or not 0 < metavar.colvar <= len(metadyn.cvs):
                continue
            kind = metadyn.cvs[metavar.colvar - 1].kind
            name = ("长度（Å）" if is_length(kind)
                    else "角度（rad）" if kind in ("angle", "torsion") else "纯数")
            if name not in classes:
                classes.append(name)
        if len(classes) > 1:
            out.append("默认丘陵宽度 σ 是同一个数字，却要用在"
                       f"{'、'.join(classes)}的 CV 上（单位随 CV 变）："
                       "请给不同类的 CV 在 &METAVAR 那一栏单独填 SCALE")
    if metadyn.lagrange and not any(m.mass for m in metadyn.metavars):
        out.append("LAGRANGE T 但所有 &METAVAR 都没给 MASS：扩展拉格朗日方案里"
                   "CV 需要质量")
    if metadyn.lagrange_langevin and not metadyn.lagrange:
        out.append("开了 &METADYN LANGEVIN，但没有 LAGRANGE T：Langevin 积分的是"
                   "扩展拉格朗日的那些 CV，没有它们这个开关没有对象")
    if metadyn.lagrange and not metadyn.lagrange_langevin \
            and any(m.gamma for m in metadyn.metavars):
        out.append("给了 &METAVAR GAMMA，但没有 &METADYN LANGEVIN T："
                   "GAMMA 是 CV 的 Langevin 摩擦，不写 LANGEVIN T 时 CP2K 不读它")
    if natoms is not None:
        for index, cv in enumerate(metadyn.cvs, start=1):
            for atom in cv.atoms + cv.atoms_from + cv.atoms_to:
                if not 1 <= atom <= natoms:
                    out.append(f"第 {index} 个 &COLVAR 的原子序号 {atom} 超出体系"
                               f"范围（1..{natoms}）")
                    break
    out.extend(_cv_problems(metadyn.cvs))
    return out


def _cv_problems(cvs: tuple[Cv, ...]) -> list[str]:
    """Checks that only need the CV itself (a missing definition, a wrong count)."""
    out: list[str] = []
    for index, cv in enumerate(cvs, start=1):
        label = f"第 {index} 个 &COLVAR（{cv.label}）"
        needed = CV_ATOM_COUNT.get(cv.kind)
        if needed is not None and len(cv.atoms) != needed:
            out.append(f"{label} 要 {needed} 个原子序号，现在给了 {len(cv.atoms)} 个")
        if cv.kind == "coordination":
            if not (cv.kinds_from or cv.atoms_from):
                out.append(f"{label} 没给 KINDS_FROM / ATOMS_FROM："
                           "配位数不知道数谁周围的原子")
            if not (cv.kinds_to or cv.atoms_to):
                out.append(f"{label} 没给 KINDS_TO / ATOMS_TO："
                           "配位数不知道数的是哪类原子")
        if cv.kind == "combine":
            if not cv.function.strip():
                out.append(f"{label} 没写 FUNCTION：组合 CV 的值由这个表达式给出")
            if not cv.children:
                out.append(f"{label} 没有嵌套的子 &COLVAR：FUNCTION 里的 CV1、CV2… "
                           "没有对应的 CV")
        if cv.kind in ("path", "rmsd") and not cv.frames:
            out.append(f"{label} 没有 &FRAME：这个 CV 就是靠和参考构型比出来的")
    return out
