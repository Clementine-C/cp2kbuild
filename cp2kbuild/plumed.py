"""Render the same CV/hill settings as a ``plumed.dat`` draft.

The PLUMED route means CP2K reads *only* the file name out of ``&METADYN``
(``metadynamics_utils.F:129``: "Check if using plumed. If so, only get the file
name and read nothing else"), so everything that describes the bias - the CVs, the
hills, the walls, the output files - lives in the PLUMED input file.  This module
turns the settings the form/CLI already collect into that language.

**Units.**  CP2K runs in atomic units and tells PLUMED the conversion factors
(``metadynamics.F:174-176``: ``setMDEnergyUnits=kjmol``,
``setMDLengthUnits=angstrom*0.1``, ``setMDTimeUnits=picoseconds``), so a
``plumed.dat`` has to be written in *PLUMED's* units: **nm**, **kJ/mol**, ps.  The
conversions below are applied for you - and CP2K's own test file agrees
(``tests/Fist/regtest-plumed2/water.plumedinp``: ``HEIGHT=5.251`` kJ/mol is exactly
2 mHartree).

**What is generated and what is not** - the honest list:

===========================  ==================================================
``DISTANCE``/``ANGLE``/      one-to-one with PLUMED's own CVs
``TORSION``
``COORDINATION``             ``KINDS_FROM``/``KINDS_TO`` are expanded into atom
                             groups, ``R_0`` is converted to nm and CP2K's
                             ``ND`` becomes PLUMED's ``MM``; CP2K *divides* the
                             number of contacts by the number of "from" atoms
                             (``colvar_methods.F:4312``) while PLUMED adds them
                             up, so a ``COMBINE COEFFICIENTS=1/N`` wrapper makes
                             the two routes agree
``&COMBINE_COLVAR``          the child CVs are written so they can be used as
                             ``ARG``s, and the expression is carried over as a
                             comment - PLUMED's ``COMBINE``/``MATHEVAL`` grammar
                             is not CP2K's, so translating it silently would be a
                             guess
``&DISTANCE_FROM_PATH``/     PLUMED's ``PATHMSD``/``RMSD`` wants the reference
``&RMSD``                    frames concatenated in one file; a commented
                             placeholder names the files you gave
``&WALL``                    ``UPPER_WALLS``/``LOWER_WALLS`` (quadratic); a
                             reflective/Gaussian wall has no direct equivalent
``SCALE``                    *is* PLUMED's ``SIGMA`` (the width of the hill along
                             that CV) - same quantity, converted to nm for the
                             length kinds
``WELL_TEMPERED``            ``BIASFACTOR=(T+ΔT)/ΔT`` with the **MD** temperature:
                             CP2K's own ``WTGAMMA`` keyword uses the other
                             convention (``1+ΔT/T``) and is not what PLUMED calls
                             a bias factor
``LAGRANGE``/``MASS``/       no equivalent: PLUMED integrates its own bias, so
``GAMMA``                    these are ignored
===========================  ==================================================
"""

from __future__ import annotations

from typing import Any

from .metadyn import CV_LABELS, Cv, Metadyn, Metavar, Wall, is_length

#: Hartree -> kJ/mol (PLUMED's energy unit, see the module docstring)
HARTREE_TO_KJMOL = 2625.4996394798254
#: Angstrom -> nm (PLUMED's length unit - what the *form* values are in)
ANGSTROM_TO_NM = 0.1
#: Bohr -> nm: CP2K's internal length, which is what its `&COLVAR` values and the
#: wall constants are really expressed in (``cp_units`` internal = bohr), so any
#: conversion of a *derived* quantity has to use this and not the angstrom one.
BOHR_TO_NM = 0.0529177249253
#: what a wall's ``K`` becomes: Hartree/bohr**2 -> kJ/mol/nm**2.  (Using the
#: angstrom factor here would be wrong by 1/0.2800 = 3.57x: K is per *bohr*².)
KAPPA_LENGTH_FACTOR = HARTREE_TO_KJMOL / BOHR_TO_NM ** 2
#: what SIGMA is set to when no width was given anywhere.  PLUMED has **no**
#: default for SIGMA - it is a compulsory keyword - so this is explicitly a
#: placeholder with a TODO in the file, not "PLUMED's default".
DEFAULT_SIGMA = 0.1


def _fmt(value: float) -> str:
    """A number PLUMED reads without surprises (``1e-05`` -> ``0.00001``)."""
    if value != value:                                  # NaN
        return "0"
    text = f"{value:.6g}"
    return text


def _elements(structure: Any) -> list[str]:
    """The per-atom element symbols, 1-based indexing done by the caller."""
    if structure is None:
        return []
    return [str(atom.symbol).strip().upper() for atom in structure.atoms]


def _atoms_of_kinds(kinds: str, elements: list[str]) -> list[int]:
    """``"O"`` -> the 1-based indices of every oxygen (``[]`` when unknown)."""
    wanted = {token.strip().upper() for token in kinds.replace(",", " ").split()
              if token.strip()}
    if not wanted or not elements:
        return []
    return [index for index, symbol in enumerate(elements, start=1)
            if symbol in wanted]


def _atom_list(indices: tuple[int, ...] | list[int]) -> str:
    return ",".join(str(int(index)) for index in indices)


def _cv_definition(cv: Cv, label: str, elements: list[str],
                   non_periodic: bool) -> tuple[list[str], str | None, list[str]]:
    """``(lines, arg-or-None, notes)`` for one CV."""
    notes: list[str] = []
    kind = cv.kind.lower()
    if kind in ("distance", "angle", "torsion"):
        directive = {"distance": "DISTANCE", "angle": "ANGLE",
                     "torsion": "TORSION"}[kind]
        suffix = " NOPBC" if non_periodic and kind == "distance" else ""
        return ([f"{label}: {directive} ATOMS={_atom_list(cv.atoms)}{suffix}"],
                label, notes)
    if kind == "coordination":
        groups = []
        group_sizes: dict[str, int] = {}
        for names, explicit, side in ((cv.kinds_from, cv.atoms_from, "GROUPA"),
                                      (cv.kinds_to, cv.atoms_to, "GROUPB")):
            indices = list(explicit) or _atoms_of_kinds(names, elements)
            group_sizes[side] = len(indices)
            if not indices:
                groups.append(f"{side}=?")
                notes.append(
                    f"{label}: {side} 没能确定原子（KINDS {names!r} 需要结构文件才能按"
                    "元素展开），请自己填原子序号"
                    + ("；GROUPA 不止一个原子时还要再除以中心原子数"
                       "（CP2K 的配位数是平均值，源码 colvar_methods.F:4312）"
                       if side == "GROUPA" else ""))
            else:
                groups.append(f"{side}={_atom_list(indices)}")
        parameters = []
        if cv.r0 is not None:
            parameters.append(f"R_0={_fmt(cv.r0 * ANGSTROM_TO_NM)}")
        else:
            notes.append(f"{label}: PLUMED 的 R_0 是**必填**项（CP2K 那边有默认 3.0 "
                         "bohr ≈ 1.59 Å），请补一个")
        if cv.nn is not None:
            parameters.append(f"NN={int(cv.nn)}")
        if cv.nd is not None:
            # CP2K calls it ND (EXPON_DENOMINATOR), PLUMED calls the same exponent MM
            parameters.append(f"MM={int(cv.nd)}")
        lines = [f"{label}: COORDINATION " + " ".join(groups + parameters)]
        if cv.r0 is not None:
            notes.append(f"{label}: R_0 {_fmt(cv.r0)} Å → {_fmt(cv.r0 * ANGSTROM_TO_NM)} nm")
        # CP2K's coordination number is the *average* over the "from" atoms
        # (`colvar%ss = ncoord*inv_n_atoms_from`, colvar_methods.F:4312); PLUMED's
        # is the plain sum over the pairs.  With more than one central atom the two
        # definitions differ by that factor, so divide it back out.
        arg = label
        from_count = group_sizes.get("GROUPA", 0)
        if from_count > 1:
            arg = f"{label}n"
            lines.append(
                f"{arg}: COMBINE ARG={label} PERIODIC=NO "
                f"COEFFICIENTS={_fmt(1.0 / from_count)}")
            lines.append(
                f"#   （CP2K 的配位数会除以中心原子数 {from_count}"
                "（colvar_methods.F:4312），PLUMED 的是求和，"
                "这一层 COMBINE 把两者对齐）")
            notes.append(f"{label}: 已按 CP2K 的 1/{from_count} 归一化"
                         f"（绿字变量 {arg} 才是 CP2K 的配位数）")
        return (lines, arg, notes)
    if kind == "combine":
        lines: list[str] = []
        for position, child in enumerate(cv.children, start=1):
            child_label = f"{label}c{position}"
            child_lines, _arg, child_notes = _cv_definition(
                child, child_label, elements, non_periodic)
            lines.extend(child_lines)
            notes.extend(child_notes)
        args = ",".join(f"{label}c{position}"
                        for position in range(1, len(cv.children) + 1))
        expression = (cv.function or "").strip() or "?"
        # the children ARE usable as ARGs; the combination itself is the gap
        lines.append(f"# 第 {label} 个 CV 是组合 CV：PLUMED 的 COMBINE/MATHEVAL 语法"
                     "和 CP2K 的 FUNCTION 不一样，")
        lines.append(f"# 所以表达式留给你（上面的子 CV 可以直接当 ARG 用）：")
        lines.append(f"#   {label}: COMBINE ARG={args} PERIODIC=NO COEFFICIENTS=1,1"
                     "   # 线性组合")
        lines.append(f"#   {label}: MATHEVAL ARG={args} PERIODIC=NO FUNC=..."
                     f"   # CP2K 原式：{expression}")
        if cv.parameters or cv.values:
            pairs = ", ".join(f"{name}={value}"
                              for name, value in zip(cv.parameters, cv.values))
            lines.append(f"#   （CP2K 的 PARAMETERS/VALUES：{pairs or '—'}）")
        notes.append(f"{label}: 组合 CV 的表达式没有翻译，请在 plumed.dat 里补上")
        return (lines, None, notes)
    if kind in ("path", "rmsd"):
        directive = "PATHMSD" if kind == "path" else "RMSD"
        lines = [f"# 第 {label} 个 CV（{CV_LABELS.get(kind, kind)}，"
                 f"{len(cv.frames)} 个参考构型）：PLUMED 用 {directive}，"
                 "要把所有参考构型拼成一个文件"]
        lines.append("# 参考构型：" + ("、".join(cv.frames) or "（没给）"))
        if kind == "path":
            lines.append(f"#   {label}: PATHMSD REFERENCE=refs.pdb LAMBDA="
                         f"{_fmt(cv.lam) if cv.lam is not None else '5'} "
                         "NEIGH_STRIDE=4 NEIGH_SIZE=8")
        else:
            lines.append(f"#   {label}: RMSD REFERENCE=ref.pdb TYPE=OPTIMAL")
        notes.append(f"{label}: 路径/RMSD CV 需要手写（拼好的参考构型文件）")
        return (lines, None, notes)
    notes.append(f"{label}: 不认识的 CV 类型 {cv.kind!r}")
    return ([f"# {label}: 不认识的 CV 类型 {cv.kind!r}"], None, notes)


def _wall_lines(metavar: Metavar, label: str, kind: str,
                non_periodic: bool) -> tuple[list[str], list[str]]:
    """``(lines, notes)`` for a ``&WALL``."""
    notes: list[str] = []
    wall: Wall | None = metavar.wall
    if wall is None or not wall.type or wall.type.upper() == "NONE":
        return ([], notes)
    wall_type = wall.type.upper()
    direction = (wall.direction or "WALL_PLUS").upper()
    # WALL_PLUS blocks the CV from growing past POSITION -> a wall above it
    directive = "LOWER_WALLS" if direction == "WALL_MINUS" else "UPPER_WALLS"
    position = ("?" if wall.position is None
                else _fmt(wall.position * ANGSTROM_TO_NM
                          if is_length(kind)
                          else wall.position))
    lines = []
    if wall_type in ("QUADRATIC", "QUARTIC"):
        factor = (KAPPA_LENGTH_FACTOR if is_length(kind)
                  else HARTREE_TO_KJMOL)
        kappa = "?" if wall.k is None else _fmt(wall.k * factor)
        lines.append(f"{directive} ARG={label} AT={position} KAPPA={kappa}")
        lines.append(f"#   （CP2K 的 TYPE {wall_type} / POSITION "
                     f"{_fmt(wall.position) if wall.position is not None else '—'}"
                     f" / K {_fmt(wall.k) if wall.k is not None else '—'}"
                     f" → PLUMED 的 {directive} AT/J/mol/nm² 已换算）")
        if wall_type == "QUARTIC":
            notes.append(f"{label}: PLUMED 的 {directive} 是二次墙，"
                         "CP2K 的四次墙形状不同，形状要自己确认")
    else:
        lines.append(f"# {label}: CP2K 的 {wall_type} 墙在 PLUMED 里没有直译"
                     "（PLUMED 只有二次墙 UPPER_WALLS/LOWER_WALLS）")
        notes.append(f"{label}: {wall_type} 墙没有直译，请自己改写")
    return (lines, notes)


def header_lines(version: str = "", structure: Any = None,
                 non_periodic: bool = False, units: str = "nm") -> list[str]:
    """The comment block every generated ``plumed.dat`` starts with.

    Shared by the translation path (:func:`plumed_input`) and the PLUMED-native
    one (:mod:`cp2kbuild.plumedcv`), so the units note cannot drift apart.
    ``units="A"`` is for a file that declares ``UNITS LENGTH=A`` itself: the note
    then has to say Å, or it would contradict the file it introduces.
    """
    lines: list[str] = []
    lines.append("# plumed.dat - written by cp2kbuild"
                 + (f" {version}" if version else "") + "（初稿，请核对）")
    lines.append("#")
    if units.strip().upper().startswith("A"):
        lines.append("# 单位（重要）：下面第一行是 UNITS LENGTH=A TIME=fs ENERGY=kj/mol，")
        lines.append("#   所以这个文件里的距离是 **Å**、能量是 **kJ/mol**"
                     "（单位串大小写敏感，")
        lines.append("#   必须写 kj/mol，写成 kJ/mol 会让 PLUMED 在启动时直接报错）。"
                     "CP2K 跑的是原子单位，")
        lines.append("#   并通过 setMDEnergyUnits=kjmol / setMDLengthUnits=angstrom*0.1")
        lines.append("#   告诉 PLUMED 怎么换算（metadynamics.F:174-176）——"
                     "两边都是 Å，正好一致。")
    else:
        lines.append("# 单位（重要）：PLUMED 用 nm / kJ/mol / ps。CP2K 跑的是原子单位，")
        lines.append("#   并通过 setMDEnergyUnits=kjmol / setMDLengthUnits=angstrom*0.1")
        lines.append("#   告诉 PLUMED 怎么换算（metadynamics.F:174-176），"
                     "所以这个文件里的")
        lines.append("#   距离是 **nm**（CP2K 侧 Å × 0.1）、能量是 **kJ/mol**"
                     "（Hartree × 2625.5）。")
        lines.append("#   想直接用 Å：勾「PLUMED 集体变量」那行右边的单位开关，"
                     "工具会写一行")
        lines.append("#   `UNITS LENGTH=A TIME=fs ENERGY=kj/mol`"
                     "（注意单位串大小写敏感，")
        lines.append("#   必须是 kj/mol，写成 kJ/mol 会让 PLUMED 启动即报错）。")
        lines.append("#   CP2K 自己的测试就是这个规矩"
                     "（tests/Fist/regtest-plumed2/water.plumedinp 里 HEIGHT=5.251 "
                     "kJ/mol = 2 mHartree）。")
    lines.append("# 原子序号：1 起算，顺序 = CP2K 输入里 &COORD 的行序，"
                 "和 CP2K 的 &COLVAR 一致。")
    lines.append("# CP2K 那条路线只从 &METADYN 读一个文件名，"
                 "CV/丘陵/墙/输出全都以这个文件为准。")
    if non_periodic:
        lines.append("# 孤立体系/非周期：PLUMED 会拿到 CP2K 的晶胞，距离类 CV 记得加"
                     " NOPBC。")
    return lines


def plumed_input(metadyn: Metadyn, structure: Any = None,
                 temperature: float | None = None, version: str = "") -> str:
    """The ``plumed.dat`` text this PLUMED-route setup describes.

    ``structure`` is what turns ``KINDS_FROM``/``KINDS_TO`` into atom numbers, and
    ``temperature`` is the MD temperature: PLUMED's ``METAD`` wants ``TEMP`` (it
    is what well-tempered scaling is computed from), and the form knows it even
    when the metadynamics block's own field is empty.
    """
    elements = _elements(structure)
    # periodicity lives on the Structure (a `&CELL PERIODIC NONE` system needs
    # NOPBC on its distance CVs); no structure at all = assume periodic
    periodic = True
    if structure is not None:
        periodic = bool(getattr(structure, "is_periodic", lambda: True)())
    non_periodic = not periodic

    lines: list[str] = header_lines(version, structure, non_periodic)
    lines.append("")

    notes: list[str] = []
    usable: list[str] = []          # labels that can go into METAD's ARG
    labels: dict[int, str] = {}
    kinds: dict[int, str] = {}
    lines.append("# ---- 集体变量 ----")
    for index, cv in enumerate(metadyn.cvs, start=1):
        label = f"cv{index}"
        cv_lines, arg, cv_notes = _cv_definition(cv, label, elements, non_periodic)
        lines.extend(cv_lines)
        notes.extend(cv_notes)
        if arg is not None:
            usable.append(arg)
            # the walls of this &METAVAR act on the variable the bias uses, so the
            # normalised coordination number (cv1n) has to be named here too
            labels[index] = arg
            kinds[index] = cv.kind
    if not metadyn.cvs:
        lines.append("# （没有定义 CV：请在界面上加，或自己在这里写）")

    # ---- hill widths: SIGMA ---------------------------------------------------- #
    # CP2K's SCALE (= WIDTH) and PLUMED's SIGMA are the *same* quantity - the width
    # of the Gaussian hill along that CV - so a per-&METAVAR SCALE wins and the
    # panel's default fills the rest.  PLUMED wants it in the CV's own unit, so the
    # length kinds get the nm conversion.  SCALE 0 is CP2K's "infinitely wide",
    # i.e. the bias does not depend on that CV at all: the equivalent in PLUMED is
    # to leave the CV out of ARG, which is what happens below.
    sigmas: list[float] = []
    kept: list[str] = []
    placeholders: list[str] = []
    widths: list[str] = []
    default_classes: set[str] = set()
    for index, cv in enumerate(metadyn.cvs, start=1):
        label = labels.get(index)
        if label is None or label not in usable:
            continue        # combine/path/RMSD: no ARG was produced for it
        metavar = next((item for item in metadyn.metavars
                        if item.colvar == index), None)
        scale = metavar.scale if metavar is not None else None
        if scale is None:
            scale = metadyn.sigma
        if scale == 0:
            notes.append(f"cv{index}: SCALE 是 0（CP2K 的“无限宽”= 这个 CV 上不加偏置），"
                         "已经把它从 METAD 的 ARG 里去掉了")
            continue
        if scale is None:
            sigma = DEFAULT_SIGMA
            placeholders.append(label)
        else:
            sigma = scale * ANGSTROM_TO_NM if is_length(cv.kind) else scale
            if scale is not None and metavar is not None and metavar.scale is None:
                default_classes.add(("长度（Å）" if is_length(cv.kind)
                                     else "角度（rad）" if cv.kind in ("angle", "torsion")
                                     else "纯数"))
            if is_length(cv.kind):
                widths.append(f"{label}: σ {_fmt(scale)} Å → {_fmt(sigma)} nm")
        sigmas.append(sigma)
        kept.append(label)
    usable = kept
    if widths:
        lines.append("")
        lines.append("# ---- 丘陵宽度 σ（CP2K 的 SCALE = PLUMED 的 SIGMA）----")
        for text in widths:
            lines.append(f"# {text}")
    if len(default_classes) > 1:
        notes.append("同一个默认丘陵宽度用在了"
                     + "、".join(sorted(default_classes))
                     + "的 CV 上：σ 的单位随 CV 变，请给不同类的 CV 在"
                     " &METAVAR 那一栏单独填 SCALE")

    # ---- walls ----------------------------------------------------------------- #
    wall_lines: list[str] = []
    for metavar in metadyn.metavars:
        if metavar.colvar not in labels:
            continue
        kind = (metadyn.cvs[metavar.colvar - 1].kind
                if 0 < metavar.colvar <= len(metadyn.cvs) else "")
        produced, wall_notes = _wall_lines(metavar, labels[metavar.colvar],
                                           kind, non_periodic)
        if produced:
            wall_lines.extend(produced)
        notes.extend(wall_notes)
    if wall_lines:
        lines.append("")
        lines.append("# ---- 约束墙（&WALL）----")
        lines.extend(wall_lines)

    # ---- the bias -------------------------------------------------------------- #
    lines.append("")
    lines.append("# ---- 偏置 ----")
    if not usable:
        lines.append("# METAD 需要至少一个可用的 ARG：把上面的 CV（或组合 CV 的表达式）"
                     "补好之后再写这一行；下面只给注释，不写一个空 ARG 的 METAD。")
        notes.append("没有一个 CV 能自动转成 PLUMED 的 ARG，METAD 那行要你自己写")
    height = metadyn.hill_height * HARTREE_TO_KJMOL
    if placeholders:
        lines.append(f"# SIGMA 是 PLUMED 的**必填**项（它没有默认值），"
                     f"这里给 {'、'.join(placeholders)} 填了 {DEFAULT_SIGMA} 当占位："
                     "它按 CV 自己的单位（距离是 nm、角度是 rad），请按你的 CV 尺度改")
        notes.append(f"这些 CV 没给丘陵宽度，文件里先用 {DEFAULT_SIGMA} 占位："
                     + "、".join(placeholders))
    # the well-tempered reference temperature is the *system* one: CP2K takes it
    # from simpar%temp_ext (md_run.F:400) and PLUMED's TEMP means the same thing,
    # so the MD temperature comes first and &METADYN TEMPERATURE (the CV
    # thermostat, which is a different quantity) is only a fallback.
    temperature_used = temperature if temperature is not None else metadyn.temperature
    from_md = temperature is not None
    if usable:
        metad = [f"METAD ARG={','.join(usable)}",
                 f"SIGMA={','.join(_fmt(value) for value in sigmas)}",
                 f"HEIGHT={_fmt(height)}",
                 f"PACE={int(metadyn.nt_hills)}"]
        if temperature_used is not None:
            metad.append(f"TEMP={_fmt(temperature_used)}")
        else:
            lines.append("# METAD 还需要 TEMP=体系温度（井温的偏置因子由它算）")
        if metadyn.well_tempered:
            if metadyn.delta_t:
                # PLUMED's bias factor is gamma = 1 + DELTA_T/T: src/bias/MetaD.cpp
                # scales the height as exp(-V/(k_B*T*(biasf-1))), and the height is
                # multiplied by biasf/(biasf-1) on output, so DELTA_T = T*(gamma-1).
                # That is the *same* convention as CP2K's own WTGAMMA
                # (`md_run.F:414`: WTGAMMA = 1 + DELTA_T/T).
                biasfactor = (1.0 + metadyn.delta_t / temperature_used
                              if temperature_used else 0.0)
                metad.append(f"BIASFACTOR={_fmt(biasfactor)}")
                source = "&MD TEMPERATURE" if from_md else "&METADYN TEMPERATURE（退而求其次）"
                lines.append(f"# 井温：{source} {_fmt(temperature_used or 0)} K + DELTA_T "
                             f"{_fmt(metadyn.delta_t)} K → BIASFACTOR={_fmt(biasfactor)}"
                             "（PLUMED 的 γ = 1+ΔT/T，和 CP2K 的 WTGAMMA 同约定；"
                             "MetaD.cpp 里高度按 exp(-V/(k_B·T·(γ−1))) 缩放）")
                if not from_md:
                    notes.append("井温的参考温度：CP2K 用的是 &MD TEMPERATURE，"
                                 "这里退用了 &METADYN TEMPERATURE，请核对是不是同一个值")
            else:
                lines.append("# 井温元动力学还要 DELTA_T（→ PLUMED 的 BIASFACTOR），"
                             "现在没给")
        if metadyn.print_hills:
            metad.append("FILE=HILLS")
        lines.append("m: " + " ".join(metad))
        if metadyn.print_colvar:
            args = list(usable) + ["m.bias"]
            lines.append(f"PRINT ARG={','.join(args)} FILE=COLVAR "
                         f"STRIDE={int(metadyn.print_every)}")
        else:
            lines.append("# 不写 COLVAR（PRINT 那行没生成）")
        if metadyn.print_hills:
            lines.append("# 丘陵文件是 PLUMED 的 FILE=HILLS（默认 HILLS），"
                         "不是 CP2K 的 -1.hills：接着跑/画自由能面都用它")
    if metadyn.lagrange or metadyn.lagrange_langevin:
        lines.append("# CP2K 的 LAGRANGE/MASS/GAMMA 在 PLUMED 里没有等价设置，"
                     "上面没有写（PLUMED 自己积分偏置）")

    if notes:
        lines.append("")
        lines.append("# ---- 要你自己确认的地方 ----")
        for note in notes:
            lines.append(f"# * {note}")
    return "\n".join(lines) + "\n"
