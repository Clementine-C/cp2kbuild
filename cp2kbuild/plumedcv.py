"""The PLUMED-native side of the metadynamics page.

CP2K's own CVs (``&COLVAR``) and PLUMED's actions are *not* the same language:
PLUMED speaks nm/kJ/mol, calls its coordination-number exponents ``NN``/``MM``
instead of ``NN``/``ND``, makes ``R_0`` compulsory, has multi-component
("multicolvar") actions whose scalars are referenced as ``label.mean`` and so on.
Translating a CP2K table into that therefore always loses something, which is why
this module carries a **second, PLUMED-native model**:

* the CV table stores the action and its keyword text **verbatim** - the tool
  never rewrites a PLUMED keyword, so it cannot invent an invalid line;
* the per-action table below says what the manual says: which module the action
  lives in, which keywords are compulsory, whether it produces a scalar or a set
  of components, and what a typical parameter line looks like.  Everything in
  :data:`PLUMED_ACTIONS` was checked against the PLUMED 2.9 user manual;
* a multi-component action needs a *component* (``mean``, ``min``,
  ``morethan``, ...) to become a scalar that ``METAD ARG=`` can use, and the
  renderer writes that reference for you;
* ``free_lines`` is the escape hatch: anything the table does not model (PCAVARS,
  PIV, OPES, ...) goes into the file untouched.

Lengths in this model are **nm** by default - PLUMED's own unit, which is what the
file needs - or **Å** when the setup's ``units`` says so, in which case the file
gets an explicit ``UNITS LENGTH=A TIME=fs ENERGY=kJ/mol`` line and nothing is
converted (what is typed in the chosen unit is written in the chosen unit).

Two more pieces live here because they are the same decision:

* :func:`read` / :func:`read_file` read a hand-written ``plumed.dat`` **back** into
  this model, so a file can be inspected or edited in the form instead of by hand.
  Everything the form does not model survives verbatim, so the round trip is safe.
* :func:`contact_cvs` builds the one CV shape that is tedious to write by hand: a
  coordination number per molecule pair (4-CPBA + 20 urea -> 20 lines + a
  ``COMBINE``), from a compact molecule-block description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from .plumed import HARTREE_TO_KJMOL, header_lines, plumed_input

#: kJ/mol per Hartree is PLUMED's energy unit, so the METAD height is given to the
#: form in kJ/mol directly - but a CP2K user thinks in Hartree, so the GUI shows
#: both and this factor is what connects them
KJMOL_PER_HARTREE = HARTREE_TO_KJMOL

#: what a multi-component action can be reduced to (the manual's component table);
#: ``min``/``max``/``lowest``/``highest``/``altmin`` only exist for some of them,
#: which is why the field is editable text rather than a fixed enum
COMPONENTS = ("mean", "sum", "min", "max", "lessthan", "morethan", "between",
              "lowest", "highest", "altmin", "vmean", "moment-2")

#: components that only exist together with a value (``MORE_THAN={RATIONAL
#: R_0=4}``): writing the bare keyword would be an error, so the table has to
#: carry the value and the form asks for it instead of inventing one
VALUED_COMPONENTS = ("lessthan", "morethan", "between")

#: the length units a generated file can declare.  ``nm`` is PLUMED's own
#: default; ``A`` writes an explicit ``UNITS`` line, which is what a CP2K user
#: expects (CP2K's own lengths are Å)
UNITS_LENGTHS = ("nm", "A")

#: ``METAD ADAPTIVE``: how PLUMED may change the hill width.  Empty = fixed.
ADAPTIVE_CHOICES = ("", "DIFF", "GEOM")

#: the actions the form knows *with their manual facts*.  ``params`` is only an
#: example (it becomes the placeholder); ``required`` are substrings the manual
#: marks as compulsory; ``kind`` says whether the label is a scalar (usable as
#: ``METAD ARG=``), a set of components, a group or a virtual atom.
PLUMED_ACTIONS: dict[str, dict[str, Any]] = {
    "COMBINE": dict(
        module="function", kind="scalar",
        # COEFFICIENTS 手册写成 compulsory(default=1.0)，省略就是 1，不算缺
        required=("ARG=", "PERIODIC="),
        params="ARG=a,b,c COEFFICIENTS=1,1,1 PERIODIC=NO",
        note="线性组合：把**多个标量**相加/加权相加用这个（系数和 ARG 一一对应，"
             "默认 1.0）。注意 `SUM` 不是这个用途——它只吃一个向量"),
    "SUM": dict(
        module="function", kind="scalar", required=("ARG=", "PERIODIC="),
        params="ARG=one_vector_action PERIODIC=NO",
        note="把一个**向量/矩阵/格点**的所有元素相加（参数只能给一个这样的对象，"
             "比如 `DISTANCES` 或 `COORDINATIONNUMBER` 的标签）。"
             "**要给多个标量求和请用 `COMBINE`**：写成 `SUM ARG=a,b,c` 会被 "
             "PLUMED 拒绝（源码里写死只允许一个输入）"),
    "MEAN": dict(
        module="function", kind="scalar", required=("ARG=", "PERIODIC="),
        params="ARG=one_vector_action PERIODIC=NO",
        note="向量/矩阵元素的算术平均（同样是**一个**输入对象；"
             "多分量动作的平均也可以直接写 `标签.mean`）"),
    "DISTANCE": dict(
        module="colvar", kind="scalar", required=("ATOMS=",),
        params="ATOMS=1,2",
        note="两原子距离（nm）。周期体系默认按最小镜像算，孤立体系加 NOPBC"),
    "ANGLE": dict(
        module="colvar", kind="scalar", required=("ATOMS=",), params="ATOMS=1,2,3",
        note="三点夹角（弧度），中间那个原子是顶点"),
    "TORSION": dict(
        module="colvar", kind="scalar", required=("ATOMS=",),
        params="ATOMS=1,2,3,4", note="四点二面角，范围 -π..π"),
    "COORDINATION": dict(
        module="colvar", kind="scalar", required=("GROUPA=", "R_0="),
        params="GROUPA=1-10 GROUPB=20-100 R_0=0.3 NN=6 MM=12",
        note="配位数（求和，不取平均）。**R_0 是必填项**（nm），"
             "NN/MM 是切换函数的分子/分母指数；等价写法 "
             "SWITCH={RATIONAL R_0=0.3 NN=6 MM=12}"),
    "COORDINATIONNUMBER": dict(
        module="multicolvar", kind="components", required=(),
        params="SPECIES=1-100 R_0=0.3 MEAN",
        note="每个原子的配位数（一组值）→ 用「分量」取标量，"
             "例如 MEAN / MORE_THAN={RATIONAL R_0=4}；"
             "常见写法 SPECIES=1-100 R_0=0.3 或 GROUPA=1 GROUPB=2,3"),
    "DISTANCES": dict(
        module="multicolvar", kind="components", required=(),
        params="GROUPA=1-10 GROUPB=11-20 MIN={BETA=500.}",
        note="一组距离（两两/跨组/列出若干对）→ 用「分量」取标量："
             "MIN={BETA=500.} / MEAN / LESS_THAN={RATIONAL R_0=0.1}；"
             "三种写法：ATOMS1=3,5 ATOMS2=1,2、GROUP=1-3、GROUPA=… GROUPB=…"),
    "CONTACT_MATRIX": dict(
        module="adjmat（**默认不编译**）", kind="components",
        required=("ATOMS=",),
        params="ATOMS=1-100 SWITCH={RATIONAL R_0=0.3 NN=6 MM=12}",
        note="接触矩阵 a_ij = σ(r_ij)。它是「容器」不是标量：要用 "
             "COLUMNSUMS/ROWSUMS 之类的动作再取分量。"
             "**adjmat 模块默认关闭**（要 ./configure --enable-modules=adjmat），"
             "否则 PLUMED 会报 unknown action"),
    "COLUMNSUMS": dict(
        module="adjmat（**默认不编译**）", kind="components",
        required=("MATRIX=",), params="MATRIX=mat MEAN",
        note="接触矩阵的列和（每个原子的接触数）→ 用「分量」取 MEAN 等；"
             "引用写成 label.mean（旧写法 label.* 也行）"),
    "ROWSUMS": dict(
        module="adjmat（**默认不编译**）", kind="components",
        required=("MATRIX=",), params="MATRIX=mat MEAN",
        note="接触矩阵的行和，用法同 COLUMNSUMS"),
    "AROUND": dict(
        module="multicolvar", kind="components", required=("DATA=", "SIGMA="),
        params="DATA=c1 ATOM=1 SIGMA=0.1 XLOWER=-2.0 XUPPER=2.0 MEAN",
        note="某个原子附近一小块区域内的平均（核密度加权）："
             "DATA 是基础多分量动作、ATOM 是参考原子、SIGMA 是核宽度（nm），"
             "边界 XLOWER/XUPPER/… 为 0 表示取整个盒子"),
    "CAVITY": dict(
        module="multicolvar", kind="components",
        required=("DATA=", "ATOMS=", "SIGMA="),
        params="DATA=d1 ATOMS=1,4,5,11 SIGMA=0.1 MEAN",
        note="四个原子围出的盒子里的平均（空腔/通道）：ATOMS 正好写 4 个原子，"
             "DATA 通常是 DENSITY SPECIES=… 或 COORDINATIONNUMBER…"),
    "DENSITY": dict(
        # SPECIES 或 GROUPA/GROUPB 都可以，这里不做必填判定
        module="multicolvar", kind="components", required=(),
        params="SPECIES=1-100",
        note="一组原子的密度（常给 CAVITY/AROUND 当 DATA 用）"),
    "GROUP": dict(
        module="colvar", kind="group", required=("ATOMS=",), params="ATOMS=1-10",
        note="给一组原子起个名字，后面的动作可以直接引用这个标签"),
    "CENTER": dict(
        module="colvar", kind="atom", required=("ATOMS=",), params="ATOMS=1-10",
        note="几何中心（虚拟原子），可以被 DISTANCE/AROUND 当原子用"),
    "COM": dict(
        module="colvar", kind="atom", required=("ATOMS=",), params="ATOMS=1-10",
        note="质心（虚拟原子，按质量加权）"),
    "FIXEDATOM": dict(
        module="colvar", kind="atom", required=("AT=",), params="AT=0.0,0.0,0.0",
        note="空间中固定的一个点（nm），用来量「到某个位置的距离」"),
    "MATHEVAL": dict(
        module="function", kind="scalar", required=("ARG=", "FUNC="),
        params="ARG=a,b PERIODIC=NO FUNC=x+y",
        note="任意数学表达式：ARG 里按顺序对应 x、y、z…；"
             "周期量要写 PERIODIC=YES 并给出周期"),
    "CUSTOM": dict(
        module="function", kind="scalar", required=("ARG=", "FUNC="),
        params="ARG=a PERIODIC=NO VAR=x FUNC=x*x",
        note="自定义函数（和 MATHEVAL 类似，但要显式给 VAR）"),
    "RMSD": dict(
        module="colvar", kind="scalar", required=("REFERENCE=",),
        params="REFERENCE=ref.pdb TYPE=OPTIMAL",
        note="和参考构型的 RMSD（nm）：参考文件里的原子顺序必须和体系一致"),
    "PATHMSD": dict(
        module="colvar", kind="scalar", required=("REFERENCE=",),
        params="REFERENCE=refs.pdb LAMBDA=0.5 NEIGH_STRIDE=4 NEIGH_SIZE=8",
        note="路径 CV：参考构型要拼成一个文件（PLUMED 的 pdb 多模型格式）"),
}

#: actions that exist in a module PLUMED does not compile by default
OPTIONAL_MODULES = {"adjmat": "CONTACT_MATRIX COLUMNSUMS ROWSUMS"}

_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_LABEL_DEF_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*:")
_REF_KEYS = ("ARG=", "DATA=", "MATRIX=", "ATOM=", "GROUPA=", "GROUPB=",
             "SPECIES=", "SPECIESA=", "SPECIESB=", "GROUP=")


@dataclass(frozen=True)
class PlumedCv:
    """One line of the PLUMED CV table: ``label: ACTION params``."""

    label: str = ""
    action: str = "DISTANCE"
    #: the keywords, written into the file **verbatim** (nm / radians)
    params: str = ""
    #: for a multi-component action: which scalar to use (mean/min/morethan/...)
    component: str = ""
    #: this CV's hill width in nm; empty = use the METAD line's SIGMA
    sigma: float | None = None
    #: free comment, written above the line
    note: str = ""

    @property
    def info(self) -> dict[str, Any]:
        return PLUMED_ACTIONS.get(self.action.upper(), {})

    @property
    def kind(self) -> str:
        return self.info.get("kind", "scalar")

    @property
    def is_scalar(self) -> bool:
        """True when ``METAD ARG=`` can name this CV (directly or by component)."""
        if self.kind == "scalar":
            return True
        return self.kind == "components" and bool(self.component.strip())

    def arg(self) -> str:
        """How another action refers to this CV."""
        if self.kind == "components" and self.component.strip():
            return f"{self.label}.{self.component.strip()}"
        return self.label

    def has_component(self) -> bool:
        """Whether the verbatim parameters already carry the reduction keyword."""
        name = self.component.strip().lower()
        if not name:
            return False
        for key, _value in parse_params(self.params):
            if re.sub(r"\d+$", "", key).lower() == name:
                return True
        return False

    def line(self) -> str:
        """The action line itself.

        A multi-component action only *has* the scalar ``label.mean`` when the
        reduction keyword stands on that action line, so the component the form
        keeps beside the parameters is written here.  When the verbatim
        parameters already contain it nothing is added - the line stays exactly
        what was typed.  Components that need a value (``MORE_THAN={RATIONAL
        R_0=4}``) are never invented: the check asks for the value instead.
        """
        parts: list[str] = []
        params = self.params.strip()
        if params:
            parts.append(params)
        name = self.component.strip()
        if name and self.kind == "components" and not self.has_component() \
                and name.lower() not in VALUED_COMPONENTS:
            parts.append(name.upper())
        text = " ".join(parts)
        return f"{self.label}: {self.action.upper()}" + (f" {text}" if text else "")

    def copy(self) -> "PlumedCv":
        return replace(self)


@dataclass(frozen=True)
class PlumedWall:
    """``UPPER_WALLS``/``LOWER_WALLS`` in PLUMED's own units."""

    arg: str = ""
    kind: str = "UPPER_WALLS"
    at: float | None = None
    kappa: float | None = None          # kJ/mol/nm²
    exp: float | None = None            # optional exponential wall
    offset: float | None = None
    #: keywords the form does not model, written verbatim at the end of the line
    #: (that is how a ``plumed.dat`` read back in keeps ``EXP``-less extras)
    extra: tuple[str, ...] = ()

    def line(self) -> str:
        parts = [self.kind.upper(), f"ARG={self.arg}"]
        if self.at is not None:
            parts.append(f"AT={_fmt(self.at)}")
        if self.kappa is not None:
            parts.append(f"KAPPA={_fmt(self.kappa)}")
        if self.exp is not None:
            parts.append(f"EXP={_fmt(self.exp)}")
        if self.offset is not None:
            parts.append(f"OFFSET={_fmt(self.offset)}")
        parts.extend(self.extra)
        return " ".join(parts)


@dataclass(frozen=True)
class PlumedBias:
    """The ``METAD``/``PRINT`` lines."""

    #: the label of the bias action; other lines reference ``<label>.bias``, so a
    #: hand-written file keeps its own name instead of being renamed to ``m``
    label: str = "m"
    pace: int = 500
    #: kJ/mol; empty = the run uses ``TAU`` instead (HEIGHT is then not written)
    height: float | None = 1.2
    #: default hill width for the CVs that have no σ of their own (nm)
    sigma: float = 0.1
    temperature: float | None = None    # empty = the MD temperature
    biasfactor: float | None = None     # empty = plain metadynamics
    hills_file: str = "HILLS"
    print_file: str = "COLVAR"
    print_stride: int = 1
    print_colvar: bool = True
    #: which labels the bias acts on, **in that order** (σ follows the same order).
    #: Empty = the *leaf* scalars of the table - the ones no other action points
    #: at.  That default matters: a table for a real CV is normally a chain of
    #: per-molecule intermediates plus one combined CV, and biasing all 21 would
    #: give a 21-dimensional bias instead of the one CV that was meant.
    args: tuple[str, ...] = ()
    #: ``METAD RESTART``: read the existing ``hills_file`` back and continue
    restart: bool = False
    #: ``METAD ADAPTIVE``: let PLUMED change the hill width (``DIFF`` = cover a
    #: time window, ``GEOM`` = cover a Cartesian distance); empty = fixed width
    adaptive: str = ""
    #: bounds for the adaptive width.  PLUMED wants **one value per ARG** here
    #: (``SIGMA_MIN=0.2,0.1``) and errors when the count does not match, so a
    #: single value is repeated for every CV on the way out.  A negative number
    #: means "no bound".
    sigma_min: str = ""
    sigma_max: str = ""
    #: METAD keywords the form does not model, written verbatim at the end of the
    #: METAD line (``GRID_MIN``, ``INTERVAL``, ``TAU``, ``WALKERS_N``, ...)
    extra: tuple[str, ...] = ()


@dataclass
class PlumedSetup:
    """Everything the PLUMED route's own table holds."""

    cvs: tuple[PlumedCv, ...] = ()
    walls: tuple[PlumedWall, ...] = ()
    bias: PlumedBias = field(default_factory=PlumedBias)
    #: raw PLUMED lines written into the file untouched
    free_lines: tuple[str, ...] = ()
    #: where the free lines go: ``before`` (just before METAD) or ``end``
    free_where: str = "before"
    #: the length unit the file declares: PLUMED's own ``nm``, or ``A`` which
    #: writes ``UNITS LENGTH=A TIME=fs ENERGY=kJ/mol`` first so that every length
    #: in the file (R_0, SIGMA, AT, ...) is read as Å - and written as Å, which is
    #: what a CP2K user expects.  The numbers themselves are never converted: what
    #: is typed in the chosen unit is written in the chosen unit.
    units: str = "nm"

    def copy(self) -> "PlumedSetup":
        return replace(self, cvs=tuple(self.cvs), walls=tuple(self.walls),
                       free_lines=tuple(self.free_lines))

    @property
    def active(self) -> bool:
        return bool(self.cvs or self.walls or self.free_lines)

    @property
    def scalars(self) -> tuple[PlumedCv, ...]:
        return tuple(cv for cv in self.cvs if cv.is_scalar)

    def referenced_labels(self) -> set[str]:
        """Labels some other action points at (``ARG=``/``DATA=``/``ATOMS=label``).

        Both the CV table and the free lines count, so a combination written by
        hand in the free-lines box also hides its inputs from the bias.
        """
        found: set[str] = set()
        for cv in self.cvs:
            found.update(_refs_of(cv.params))
        for line in self.free_lines:
            _label, _action, params = split_line(line)
            found.update(_refs_of(params))
        return found

    def leaves(self) -> tuple[PlumedCv, ...]:
        """The scalar CVs nothing else references - the natural ``METAD ARG=``.

        A CV table for a real system is usually a chain: twenty per-molecule
        coordination numbers, combined into the one CV that matters.  Biasing
        *every* scalar would give a 21-dimensional bias, so the default is the
        leaf of the chain.  When every scalar is referenced (or the table is a
        single CV) the whole scalar list is returned, exactly as before.
        """
        referenced = self.referenced_labels()
        out = tuple(cv for cv in self.scalars if cv.label not in referenced)
        return out or self.scalars

    def chosen(self) -> tuple[PlumedCv, ...]:
        """The CVs the bias acts on: ``bias.args`` when given, else the leaves."""
        if not self.bias.args:
            return self.leaves()
        by_arg = {cv.arg(): cv for cv in self.scalars}
        by_label = {cv.label: cv for cv in self.scalars}
        out = []
        for name in self.bias.args:
            found = by_arg.get(name) or by_label.get(name.split(".")[0])
            if found is not None and found not in out:
                out.append(found)
        return tuple(out)

    def args(self) -> list[str]:
        """The ``METAD ARG=`` list."""
        return [cv.arg() for cv in self.chosen()]

    def sigma_of(self, cv: PlumedCv) -> float:
        return cv.sigma if cv.sigma is not None else self.bias.sigma

    @property
    def length_unit(self) -> str:
        """``Å`` or ``nm`` - what the file's lengths are, for labels and messages."""
        return "Å" if self.units.strip().upper() in ("A", "ANGSTROM", "ANGSTROMS") \
            else "nm"


def _fmt(value: float) -> str:
    """A number PLUMED reads without surprises."""
    text = f"{value:.6g}"
    return "0" if text == "-0" else text


def _values(text: str) -> list[str]:
    """``"0.2, 0.1"`` -> ``["0.2", "0.1"]``."""
    return [part for part in text.replace(",", " ").split() if part]


def _spread(text: str, count: int) -> str:
    """One value per ARG: PLUMED errors when ``SIGMA_MIN`` has the wrong count.

    A single value is therefore repeated for every collective variable - which is
    what "the bound is 0.2" means for a 2D bias as well.
    """
    parts = _values(text)
    if len(parts) == 1 and count > 1:
        parts = parts * count
    return ",".join(parts)


def _at(text: str, index: int) -> float | None:
    """The ``index``-th value of a keyword list (one value counts for all of them)."""
    values = _values(text)
    if not values:
        return None
    if index < len(values):
        return _num(values[index])
    return _num(values[0]) if len(values) == 1 else None


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #
def parse_params(text: str) -> list[tuple[str, str]]:
    """Split a PLUMED keyword list into ``(KEY, value)`` pairs.

    Whitespace inside braces belongs to the value (``SWITCH={RATIONAL R_0=0.3
    NN=6 MM=12}`` is *one* keyword), and a ``#`` starts a comment.  Numbered
    keywords (``ATOMS1=``, ``LESS_THAN1=``) keep their number in the key.
    """
    text = text.split("#", 1)[0]
    pairs: list[tuple[str, str]] = []
    depth = 0
    current = ""
    for char in text:
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
        if char.isspace() and depth == 0:
            if current:
                pairs.append(_pair(current))
                current = ""
            continue
        current += char
    if current:
        pairs.append(_pair(current))
    return [pair for pair in pairs if pair[0]]


def _pair(token: str) -> tuple[str, str]:
    key, sign, value = token.partition("=")
    return ((key.strip().upper(), value.strip()) if sign
            else (token.strip().upper(), ""))


def _keys(pairs: list[tuple[str, str]]) -> set[str]:
    """The keywords given, so ``ATOMS1`` also satisfies a need for ``ATOMS``."""
    found = set()
    for key, _value in pairs:
        found.add(key)
        stripped = re.sub(r"\d+$", "", key)
        found.add(stripped)
    return found


def _defined_labels(setup: PlumedSetup) -> set[str]:
    labels = {cv.label for cv in setup.cvs if cv.label}
    for line in setup.free_lines:
        found = _LABEL_DEF_RE.match(line)
        if found:
            labels.add(found.group(1))
    return labels


def _tokens(value: str) -> list[str]:
    return [token.strip() for token in value.replace("{", " ").replace("}", " ")
            .replace(",", " ").split() if token.strip()]


def _is_atom_spec(token: str) -> bool:
    return bool(re.fullmatch(r"\d+(-\d+)?", token))


_NUMBER_RE = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eEdD][-+]?\d+)?$")


def _is_number(token: str) -> bool:
    """True for a plain number (``0.3``, ``-2.0``, ``1e-3``) - which is no label."""
    return bool(_NUMBER_RE.match(token))


def split_line(line: str) -> tuple[str, str, str]:
    """``"c1: COORDINATION GROUPA=1"`` -> ``("c1", "COORDINATION", "GROUPA=1")``.

    A line without a ``label:`` prefix comes back with an empty label and the
    whole text as the action - that is what a bare ``PRINT``/``UNITS`` line is.
    """
    found = _LABEL_DEF_RE.match(line)
    if found:
        rest = line[found.end():].strip()
        action, _sep, params = rest.partition(" ")
        return found.group(1), action, params.strip()
    text = line.strip()
    action, _sep, params = text.partition(" ")
    return "", action, params.strip()


def _refs_of(text: str) -> set[str]:
    """Every label a keyword text references (``ARG=a,b`` -> ``{a, b}``)."""
    found: set[str] = set()
    for key, value in parse_params(text):
        base = re.sub(r"\d+$", "", key)
        if f"{base}=" not in _REF_KEYS:
            continue
        for token in _tokens(value):
            if _is_atom_spec(token) or _is_number(token):
                continue
            found.add(token.split(".")[0])
    return found


def problems(setup: PlumedSetup, natoms: int | None = None,
             temperature: float | None = None) -> list[str]:
    """What is wrong or missing in a PLUMED-route setup.

    These are the mistakes that are valid text but almost certainly not what was
    meant: a label that does not exist, a compulsory keyword the manual marks as
    required, a multi-component action used as if it were a scalar, a reference to
    an action living in a module PLUMED does not build by default, and so on.
    """
    out: list[str] = []
    if not setup.active:
        return out
    known: set[str] = set()
    for index, cv in enumerate(setup.cvs, start=1):
        where = f"第 {index} 个 PLUMED CV"
        label = cv.label.strip()
        action = cv.action.strip().upper()
        if not label:
            out.append(f"{where} 没有标签：PLUMED 的每一行都要 `标签: 动作`")
        elif not _LABEL_RE.match(label):
            out.append(f"{where} 的标签 {label!r} 不是合法的 PLUMED 标签"
                       "（字母/下划线开头，后面可以是字母、数字、下划线）")
        elif label in known:
            out.append(f"{where} 的标签 {label!r} 和前面某一行重复："
                       "PLUMED 会用最后一个，前面的引用会指错")
        if action not in PLUMED_ACTIONS:
            out.append(f"{where} 的动作 {cv.action!r} 不在工具认识的清单里"
                       "（可以照手册自己写参数，或用「自由行」）")
        info = PLUMED_ACTIONS.get(action, {})
        params = cv.params.strip()
        pairs = parse_params(params)
        given = _keys(pairs)
        for key, value in pairs:
            # `SWITCH={RATIONAL R_0=0.3 NN=6 MM=12}` *is* the R_0/NN/MM/D_0
            # keywords (PLUMED hands the whole block to the switching function),
            # so what is inside the braces counts as given - otherwise every
            # file that uses the documented equivalent spelling was reported as
            # missing R_0.
            if re.sub(r"\d+$", "", key) in _SWITCH_KEYS and "{" in value:
                given |= _keys([_pair(token) for token in _tokens(value)])
        if not params and info.get("required"):
            out.append(f"{where}（{action}）的参数是空的：手册里 "
                       f"{'、'.join(info['required'])} 是必须给的")
        for needed in info.get("required", ()):
            if needed.rstrip("=").upper() not in given:
                out.append(f"{where}（{action}）没写 {needed.rstrip('=')}："
                           "手册把它列为必填")
        if info.get("kind") == "components" and not cv.component.strip():
            out.append(f"{where}（{action}）是多分量动作，不能直接当标量用："
                       "请在「分量」里选一个（MEAN/MIN/MORE_THAN…），"
                       "这样 METAD 的 ARG 才能写 label.分量")
        if info.get("kind") == "components" and cv.component.strip() \
                and cv.component.strip().lower() in VALUED_COMPONENTS \
                and not cv.has_component():
            out.append(f"{where}（{action}）的分量 {cv.component.strip().upper()} "
                       "必须带一个值（例如 MORE_THAN={RATIONAL R_0=4}、"
                       "LESS_THAN={RATIONAL R_0=0.1}）：光写关键字 PLUMED 会报错，"
                       "请把值写进「参数」")
        if action in OPTIONAL_MODULES["adjmat"].split():
            out.append(f"{where}（{action}）在 **adjmat** 模块里，"
                       "PLUMED 默认不编译它（要 ./configure "
                       "--enable-modules=adjmat），否则会报 unknown action")
        if action in ("SUM", "MEAN"):
            # PLUMED's SUM/MEAN take exactly one vector/matrix/grid argument and
            # abort on anything else (src/function/Sum.cpp), so a comma list of
            # scalars - the natural mistake - has to be caught here
            for key, value in pairs:
                if re.sub(r"\d+$", "", key) == "ARG" and "," in value:
                    out.append(f"{where}（{action}）的 ARG 给了多个值"
                               f"（{value[:40]}）：{action} 只接受**一个**向量/矩阵"
                               "（例如某个多分量动作的标签），PLUMED 会直接报错。"
                               "把多个标量相加要用 COMBINE ARG=… COEFFICIENTS=…")
        # references: a label used as `ARG=`/`DATA=`/`MATRIX=`/... must exist
        for key, value in pairs:
            base = re.sub(r"\d+$", "", key)
            if f"{base}=" not in _REF_KEYS:
                continue
            for token in _tokens(value):
                if _is_atom_spec(token) or _is_number(token) \
                        or token.upper() in ("NOPBC", "NO"):
                    continue
                if token.split(".")[0] not in known:
                    out.append(f"{where}（{action}）的 {base} 引用了 {token!r}，"
                               "但前面没有定义过这个标签")
        # atom indices, when the system size is known
        if natoms is not None:
            for key, value in pairs:
                base = re.sub(r"\d+$", "", key)
                if base not in ("ATOMS", "GROUPA", "GROUPB", "GROUP", "SPECIES",
                                "SPECIESA", "SPECIESB", "ATOM", "AT"):
                    continue
                for token in _tokens(value):
                    if not _is_atom_spec(token) or not token[0].isdigit():
                        continue
                    for atom in _atoms(token):
                        if not 1 <= atom <= natoms:
                            out.append(f"{where}（{action}）的 {base} 里原子序号 "
                                       f"{atom} 超出体系范围（1..{natoms}）")
                            break
        if label:
            known.add(label)
    for index, wall in enumerate(setup.walls, start=1):
        where = f"第 {index} 道 PLUMED 墙"
        if wall.kind.upper() not in ("UPPER_WALLS", "LOWER_WALLS"):
            out.append(f"{where} 的类型 {wall.kind!r} 不认识"
                       "（PLUMED 里是 UPPER_WALLS / LOWER_WALLS）")
        if not wall.arg:
            out.append(f"{where} 没给 ARG：墙要作用在哪个 CV 上")
        elif wall.arg not in {cv.arg() for cv in setup.cvs}:
            out.append(f"{where} 的 ARG={wall.arg} 不是本表里的标量 CV")
        if wall.at is None:
            out.append(f"{where} 没给 AT：墙放在哪个 CV 值上（{setup.length_unit}/弧度）")
        if wall.kappa is None:
            out.append(f"{where} 没给 KAPPA：PLUMED 的墙是二次势，"
                       "没有力常数就没有意义（kJ/mol/nm²）")
    if setup.cvs:
        if not setup.args():
            out.append("没有一个 CV 能当 METAD 的 ARG（多分量动作要先选「分量」，"
                       "GROUP/虚拟原子不能直接进 ARG）")
        known_args = {cv.arg() for cv in setup.scalars}
        known_labels = {cv.label for cv in setup.scalars}
        unknown = [name for name in setup.bias.args
                   if name not in known_args and name not in known_labels]
        if unknown:
            out.append(f"「偏置哪些 CV」里的 {'、'.join(unknown[:4])} 找不到对应的标量"
                       " CV（拼错了？还是那个 CV 不是标量——多分量动作要写 "
                       "label.分量，GROUP/虚拟原子不能进 ARG）")
        if setup.bias.pace <= 0:
            out.append(f"METAD 的 PACE 是 {setup.bias.pace}：它是撒丘陵的间隔"
                       "（MD 步），必须是正数")
        extra_keys = _keys(parse_params(" ".join(setup.bias.extra)))
        if setup.bias.height is None:
            # the one legal way to leave HEIGHT out: a TAU-based schedule
            if not ({"TAU"} <= extra_keys
                    and ({"BIASFACTOR", "DAMPFACTOR"} & extra_keys)):
                out.append("METAD 没有 HEIGHT：手册把它列为必填"
                           "（只有同时给了 TAU 和 BIASFACTOR/DAMPFACTOR 才能省）")
        elif setup.bias.height <= 0:
            out.append(f"METAD 的 HEIGHT 是 {setup.bias.height:g} kJ/mol："
                       "高度要不是正数就没有偏置")
        adaptive = setup.bias.adaptive.strip().upper()
        if adaptive and adaptive not in ADAPTIVE_CHOICES:
            out.append(f"METAD 的 ADAPTIVE={setup.bias.adaptive} 不是 PLUMED 认的值"
                       "（只有 DIFF 或 GEOM；写别的它直接报 "
                       "I do not know this type of adaptive scheme）")
        # SIGMA is one value under ADAPTIVE, but SIGMA_MIN/SIGMA_MAX are one
        # value *per ARG* (MetaD.cpp: "the number of SIGMA_MIN values be the
        # same of the number of the arguments")
        bound_counts = {name: len(_values(text))
                        for name, text in (("SIGMA_MIN", setup.bias.sigma_min),
                                           ("SIGMA_MAX", setup.bias.sigma_max))
                        if text.strip()}
        arg_count = len(setup.chosen())
        grid = {"GRID_MIN", "GRID_MAX", "GRID_BIN", "GRID_SPACING"} & extra_keys
        if adaptive in ADAPTIVE_CHOICES[1:]:
            if setup.bias.sigma <= 0:
                out.append("用了 ADAPTIVE 就必须给一个正的 SIGMA："
                           + ("GEOM 时它是一段笛卡尔距离，单位跟 UNITS 的长度单位走"
                              if adaptive == "GEOM" else
                              "DIFF 时它是**时间**（多少步），不是长度"))
            elif adaptive == "DIFF" and abs(setup.bias.sigma - round(setup.bias.sigma)) \
                    > 1e-9:
                out.append(f"ADAPTIVE=DIFF 的 SIGMA 必须是**整数**（它是要覆盖的步数），"
                           f"现在是 {setup.bias.sigma:g}：PLUMED 会报 "
                           "the sigma must be an integer which is the number of "
                           "time steps")
            per_cv = [cv.label for cv in setup.chosen() if cv.sigma is not None]
            if per_cv:
                out.append("用了 ADAPTIVE 时 SIGMA 只能给**一个**数，表格里 "
                           + "、".join(per_cv[:4]) + " 单独设的 σ 会被忽略"
                           "（宽度由 PLUMED 自己算）")
            for name, count in sorted(bound_counts.items()):
                if count not in (1, arg_count):
                    out.append(f"{name} 给了 {count} 个值，但 METAD 有 {arg_count} "
                               "个 CV：PLUMED 要求个数一致（或只给一个，工具会替你"
                               "重复到每一个 CV）")
            bad = [name for name, text in (("SIGMA_MIN", setup.bias.sigma_min),
                                           ("SIGMA_MAX", setup.bias.sigma_max))
                   if text.strip() and len(_values(text)) != len(text.replace(",", " ").split())]
            if bad:
                out.append("、".join(bad) + " 里有不是数字的东西：这里只能填数字，"
                           "多个值用逗号分开")
            for index in range(max(bound_counts.get("SIGMA_MIN", 0),
                                   bound_counts.get("SIGMA_MAX", 0))):
                low = _at(setup.bias.sigma_min, index)
                high = _at(setup.bias.sigma_max, index)
                if low is not None and high is not None and low > high:
                    out.append(f"SIGMA_MIN={_fmt(low)} 比 SIGMA_MAX={_fmt(high)} 还大："
                               "自适应宽度的上下限写反了")
                    break
            if not bound_counts:
                if grid:
                    out.append("用了 ADAPTIVE 又把偏置存在网格上（GRID_*），"
                               "但没给 SIGMA_MIN：PLUMED 手册/源码要求这时必须给"
                               "（When using ADAPTIVE Gaussians on a grid SIGMA_MIN "
                               "must be specified），否则它会直接报错")
                else:
                    out.append("用了 ADAPTIVE 但没设 SIGMA_MIN/SIGMA_MAX：丘陵宽度会"
                               "无界地漂移，多数体系该给一个上限（负值表示不设限）")
            out.append("自适应丘陵跑完做后处理时，sum_hills 必须加直方图修正"
                       "（plumed sum_hills --histo），否则重建的自由能是错的"
                       "——PLUMED 手册对 ADAPTIVE 有明确要求")
        elif bound_counts:
            # without ADAPTIVE these keywords are not read at all, and PLUMED
            # ends its input check with "cannot understand the following words"
            out.append("没开 ADAPTIVE 却写了 "
                       + "、".join(sorted(bound_counts))
                       + "：PLUMED 在没看到 ADAPTIVE 时根本不读这两个词，"
                       "会在启动时报 cannot understand the following words —— "
                       "要么打开 ADAPTIVE，要么把它们清空（生成的草稿里不会写它们）")
        if setup.bias.sigma <= 0 and all(cv.sigma is None for cv in setup.scalars) \
                and adaptive not in ADAPTIVE_CHOICES[1:]:
            out.append("METAD 的 SIGMA（丘陵宽度）不是正数：PLUMED 里 SIGMA 是"
                       "必填项，而且它决定丘陵盖多宽（单位跟 CV 走）")
        if setup.bias.restart and not setup.bias.hills_file.strip():
            out.append("勾了 RESTART 却没有 FILE：PLUMED 不知道从哪个丘陵文件接着撒")
        if setup.bias.biasfactor is not None:
            if setup.bias.biasfactor <= 1:
                out.append(f"BIASFACTOR 是 {setup.bias.biasfactor:g}："
                           "井温元动力学要求它大于 1（等于 1 就是不偏置）")
            effective = (setup.bias.temperature if setup.bias.temperature
                         is not None else temperature)
            if effective is None:
                out.append("井温元动力学（BIASFACTOR）还要 TEMP："
                           "在 METAD 那行填一个体系温度，"
                           "或让「生成输入」页的温度填进来")
            elif (setup.bias.temperature is not None and temperature is not None
                    and abs(setup.bias.temperature - temperature) > 1.0):
                # PLUMED scales the hill height with *its* TEMP, and CP2K does not
                # pass the MD temperature to PLUMED (metadynamics.F only sends the
                # unit conversions), so a mismatch silently biases the wrong T
                out.append(
                    f"METAD 的 TEMP={setup.bias.temperature:g} 和 &MD TEMPERATURE"
                    f" {temperature:g} 不一致：PLUMED 用前者做井温缩放"
                    f"（ΔT = TEMP·(BIASFACTOR−1)），"
                    "而体系实际跑在后者。CP2K 不会把 MD 温度告诉 PLUMED"
                    "（metadynamics.F 只传单位换算），所以这里必须自己写对")
    # A CV that nothing references and that the bias does not act on is dead
    # weight.  For this kind of table (20 intermediates + one combined CV) it
    # usually means the combined line forgot one of them.  For a GROUP it usually
    # means a leftover definition: the file was rewritten to use another group
    # (CV 表里那两行还留着旧的分组), which is harmless but pointless.
    referenced = setup.referenced_labels()
    biased = set(setup.args())
    for index, cv in enumerate(setup.cvs, start=1):
        if not cv.label or cv.label in referenced or cv.label in biased \
                or cv.arg() in biased:
            continue
        how = ("GROUP/虚拟原子只是给一组原子起个名字（不花时间也不报错）"
               if cv.kind in ("group", "atoms") else "")
        out.append(
            f"第 {index} 个 PLUMED CV（{cv.label}）没有被任何动作引用，也不在"
            f" METAD 的 ARG 里：它不会影响这次运行"
            + (f"——{how}；" if how else "；")
            + f"要用它就在别的动作里写 ATOMS=/GROUPA=/GROUPB={cv.label}，"
            "不用可以删掉这一行（留着也不会报错）")
    for index, line in enumerate(setup.free_lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        found = _LABEL_DEF_RE.match(line)
        if found and found.group(1) in {cv.label for cv in setup.cvs}:
            out.append(f"自由行第 {index} 行定义的标签 {found.group(1)!r} "
                       "和 CV 表里的标签重复：PLUMED 会用最后一个")
    return out


def _atoms(spec: str) -> list[int]:
    """``"1-3"`` -> ``[1, 2, 3]`` (only used for the range check)."""
    if "-" in spec:
        low, _, high = spec.partition("-")
        if low.isdigit() and high.isdigit():
            return list(range(int(low), int(high) + 1))
        return []
    return [int(spec)] if spec.isdigit() else []


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
def draft(metadyn: Any, structure: Any = None, temperature: float | None = None,
          version: str = "") -> str:
    """The ``plumed.dat`` text for a metadynamics setup - one decision point.

    When the PLUMED-native table has content it *is* the draft (nothing is
    translated); otherwise the CP2K CV/``&METAVAR`` tables are translated as
    before.  Both the GUI preview and ``--meta-plumed-draft`` go through here, so
    they cannot disagree.
    """
    setup = getattr(metadyn, "plumed_setup", None)
    if setup is not None and setup.active:
        return render(setup, structure, temperature, version)
    return plumed_input(metadyn, structure, temperature, version)


def render(setup: PlumedSetup, structure: Any = None,
           temperature: float | None = None, version: str = "") -> str:
    """The ``plumed.dat`` text this PLUMED-native setup describes."""
    periodic = True
    if structure is not None:
        periodic = bool(getattr(structure, "is_periodic", lambda: True)())
    lines = header_lines(version, structure, non_periodic=not periodic,
                         units=setup.units)
    lines.append(f"# 这一份是按 PLUMED 自己的语法写的（长度 {setup.length_unit}、"
                 "能量 kJ/mol），没有从 CP2K 的 &COLVAR/&METAVAR 翻译。")
    lines.append("")
    if setup.length_unit == "Å":
        # UNITS is read while the file is parsed, so it has to stand before the
        # first action: everything below is then Å, nothing needs converting.
        # The unit strings are case sensitive in PLUMED (src/tools/Units.cpp
        # compares them literally, and the manual says so), so "kj/mol" must not
        # be written "kJ/mol" - that one aborts the run at startup.
        lines.append("UNITS LENGTH=A TIME=fs ENERGY=kj/mol")
        lines.append("# 上面这行把整个文件的长度单位声明成 Å（PLUMED 默认是 nm）："
                     "下面所有 R_0/SIGMA/AT… 都按 Å 读，"
                     "HILLS/COLVAR 也按 Å 写，时间列按 fs 写。")
        lines.append("#   注意单位串**大小写敏感**：必须是 `kj/mol`（写成 `kJ/mol` "
                     "PLUMED 会直接报错）。")
        lines.append("")

    if setup.cvs:
        lines.append("# ---- 集体变量 ----")
        for cv in setup.cvs:
            if cv.note.strip():
                lines.append(f"# {cv.note.strip()}")
            lines.append(cv.line())
            if cv.kind == "components" and cv.component.strip():
                lines.append(f"#   标量引用：{cv.arg()}（METAD 的 ARG 用这个名字）")
            elif cv.kind == "components":
                lines.append("#   这是多分量动作：请在界面上给它选一个「分量」"
                             "（MEAN/MIN/MORE_THAN…）")
        lines.append("")

    if setup.walls:
        lines.append("# ---- 约束墙（PLUMED 自己的单位）----")
        for wall in setup.walls:
            lines.append(wall.line())
        lines.append("")

    if setup.free_lines and setup.free_where != "end":
        lines.append("# ---- 自由行（你自己写的，工具不检查）----")
        lines.extend(setup.free_lines)
        lines.append("")

    args = setup.args()
    if args:
        chosen = setup.chosen()
        adaptive = setup.bias.adaptive.strip().upper()
        if adaptive not in ADAPTIVE_CHOICES:
            adaptive = ""
        if adaptive:
            # with an adaptive width PLUMED computes the σ itself, so SIGMA is
            # the single starting value (a distance for GEOM, a time for DIFF)
            sigma_text = _fmt(setup.bias.sigma)
        else:
            sigma_text = ",".join(_fmt(setup.sigma_of(cv)) for cv in chosen)
        metad = [f"METAD ARG={','.join(args)}", f"SIGMA={sigma_text}"]
        if setup.bias.height is not None:
            metad.append(f"HEIGHT={_fmt(setup.bias.height)}")
        metad.append(f"PACE={int(setup.bias.pace)}")
        effective = setup.bias.temperature if setup.bias.temperature is not None \
            else temperature
        if effective is not None:
            metad.append(f"TEMP={_fmt(effective)}")
        else:
            lines.append("# METAD 还需要 TEMP=体系温度")
        if setup.bias.biasfactor is not None:
            metad.append(f"BIASFACTOR={_fmt(setup.bias.biasfactor)}")
            if effective is not None:
                # the convention, spelled out: PLUMED scales the height as
                # exp(-V/(k_B*T*(gamma-1))), i.e. DELTA_T = T*(gamma-1)
                lines.append(
                    f"# 井温：TEMP {_fmt(effective)} K、BIASFACTOR "
                    f"{_fmt(setup.bias.biasfactor)}（= 1 + ΔT/T，和 CP2K 的 WTGAMMA "
                    f"同约定）→ ΔT = {_fmt(effective * (setup.bias.biasfactor - 1.0))} K")
        if adaptive:
            metad.append(f"ADAPTIVE={adaptive}")
            if setup.bias.sigma_min.strip():
                metad.append(f"SIGMA_MIN={_spread(setup.bias.sigma_min, len(args))}")
            if setup.bias.sigma_max.strip():
                metad.append(f"SIGMA_MAX={_spread(setup.bias.sigma_max, len(args))}")
        metad.append(f"FILE={setup.bias.hills_file or 'HILLS'}")
        if setup.bias.restart:
            metad.append("RESTART=YES")
        metad.extend(setup.bias.extra)
        if not setup.bias.args:
            hidden = [cv.label for cv in setup.scalars
                      if cv.label not in {leaf.label for leaf in chosen}]
            lines.append("# 偏置哪些 CV：界面没指定，工具取「叶子」标量"
                         + (f"——{len(hidden)} 个被别的动作引用的中间量没有进 ARG"
                            if hidden else "")
                         + "。要改就改这一行，或在界面上填「偏置哪些 CV」。")
        if adaptive:
            lines.append("# ADAPTIVE：让 PLUMED 自己调整丘陵宽度——"
                         + ("GEOM 是「覆盖一段笛卡尔距离」"
                            if adaptive == "GEOM" else "DIFF 是「覆盖一段时间」")
                         + f"，所以 SIGMA 只给一个数"
                         + ("（长度，跟随 UNITS）" if adaptive == "GEOM"
                            else "（步数，不是长度）"))
            lines.append("#   事后用 sum_hills 重建自由能必须加直方图修正"
                         "（plumed sum_hills --histo），否则曲线是错的。")
        if setup.bias.restart:
            lines.append(f"# RESTART=YES：PLUMED 先把 {setup.bias.hills_file or 'HILLS'} "
                         "里的旧丘陵读回来再继续撒（文件不存在它会直接报错）；"
                         "换 TEMP/BIASFACTOR 续跑也支持，旧丘陵按新值重新缩放。")
            lines.append("#   CP2K 的 &EXT_RESTART/RESTART_METADYNAMICS 是给 CP2K 自带"
                         "元动力学的，这条路线只认这个关键字。")
        lines.append("# ---- 偏置 ----")
        lines.append(f"{setup.bias.label or 'm'}: " + " ".join(metad))
        if setup.bias.print_colvar:
            label = setup.bias.label or "m"
            printed = list(args) + [f"{label}.bias"]
            lines.append(f"PRINT ARG={','.join(printed)} "
                         f"FILE={setup.bias.print_file or 'COLVAR'} "
                         f"STRIDE={int(setup.bias.print_stride)}")
        else:
            lines.append("# 不写 COLVAR（界面上关掉了）")
        lines.append("")

    if setup.free_lines and setup.free_where == "end":
        lines.append("# ---- 自由行（你自己写的，工具不检查）----")
        lines.extend(setup.free_lines)
        lines.append("")

    notes = problems(setup, temperature=temperature)
    if notes:
        lines.append("# ---- 要你自己确认的地方 ----")
        for note in notes:
            lines.append(f"# * {note}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# reading a hand-written plumed.dat back in
# --------------------------------------------------------------------------- #
#: METAD keywords the form has a field for; everything else is kept verbatim
_METAD_KEYS = ("ARG", "SIGMA", "HEIGHT", "PACE", "TEMP", "BIASFACTOR", "FILE",
               "RESTART", "ADAPTIVE", "SIGMA_MIN", "SIGMA_MAX")

#: wall keywords the form has a field for
_WALL_KEYS = ("ARG", "AT", "KAPPA", "EXP", "OFFSET")

#: PRINT keywords the form has a field for (anything else keeps the whole line)
_PRINT_KEYS = ("ARG", "FILE", "STRIDE")

#: the unit strings PLUMED accepts for ``UNITS`` - **case sensitive**, straight
#: from src/tools/Units.cpp (``setEnergy`` compares the string literally, so
#: "kJ/mol" is not "kj/mol" and aborts the run)
_UNIT_SPELLINGS = {"ENERGY": ("kj/mol", "j/mol", "kcal/mol", "eV", "Ha"),
                   "TIME": ("ps", "fs", "ns", "atomic"),
                   "LENGTH": ("nm", "A", "um", "Bohr")}

#: keywords whose value is a switching function given as a ``{...}`` block.  The
#: parameters *inside* that block stand in for the action's own compulsory
#: keywords - ``Coordination.cpp`` says it in the keyword's own help: "When this
#: keyword is present you no longer need the NN, MM, D_0 and R_0 keywords".
_SWITCH_KEYS = ("SWITCH", "LESS_THAN", "MORE_THAN", "BETWEEN")


def _num(text: Any) -> float | None:
    """A PLUMED number as a float, or None when it is something else (``pi``)."""
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def _pairs_by_key(params: str,
                  known: tuple[str, ...]) -> tuple[dict[str, str], list[str]]:
    """Split a keyword text into the keys the form models and the raw rest.

    ``ATOMS1=`` counts as ``ATOMS`` (that is how PLUMED numbers repeated
    keywords), and a keyword the form does not model comes back as ``KEY=VALUE``
    text so that it can be written out again unchanged.
    """
    seen: dict[str, str] = {}
    kept: list[str] = []
    for key, value in parse_params(params):
        base = re.sub(r"\d+$", "", key)
        if base in known:
            seen.setdefault(base, value)
        else:
            kept.append(f"{key}={value}" if value else key)
    return seen, kept


def _read_metad(label: str, params: str, notes: list[str]) -> dict[str, Any]:
    """The METAD line as :class:`PlumedBias` fields (unknown keywords verbatim)."""
    seen, kept = _pairs_by_key(params, _METAD_KEYS)
    if kept:
        notes.append("METAD 行上有工具没建模的关键字，已原样保留："
                     + "、".join(item.split("=", 1)[0] for item in kept))

    def number(key: str, *, keep: bool) -> float | None:
        """A modelled numeric keyword; ``keep`` = put the raw text back if odd."""
        text = seen.get(key)
        if text is None:
            return None
        value = _num(text)
        if value is None:
            if keep:
                kept.append(f"{key}={text}")
            notes.append(f"METAD 的 {key}={text} 不是数字：导入后按"
                         + ("空值处理" if keep else "默认值处理") + "，请手工核对")
        return value

    # a file without a PRINT line must not grow one on the way out
    out: dict[str, Any] = {"label": label or "m", "print_colvar": False}
    arg = seen.get("ARG", "")
    out["args"] = tuple(part.strip() for part in arg.replace(" ", "").split(",")
                        if part.strip())
    if not arg:
        notes.append("METAD 行没有 ARG：导入后按表格里的「叶子」标量填，请核对")
    if "SIGMA" in seen:
        # SIGMA is one value **per ARG** (MetaD.cpp: "number of arguments does not
        # match number of SIGMA parameters" is an error), so a 2D bias legitimately
        # writes "SIGMA=0.15,0.5".  A list is handed back as per-CV widths (that is
        # where the form keeps them, and where the GUI can edit them); a single
        # value is the default for every CV.
        values = _values(seen["SIGMA"])
        numbers = [_num(part) for part in values]
        if len(numbers) == 1 and numbers[0] is not None:
            out["sigma"] = numbers[0]
            if len(out["args"]) > 1:
                # one number for several CVs: PLUMED itself refuses that line, and
                # the tool writes the expanded form - so say what happened instead
                # of quietly "fixing" the file
                notes.append(
                    f"METAD 的 SIGMA 只给了一个值（{seen['SIGMA']}）但有 "
                    f"{len(out['args'])} 个 ARG：PLUMED 会直接报 "
                    "\"number of arguments does not match number of SIGMA "
                    "parameters\"。工具按「每个 CV 都用这个宽度」处理，"
                    "写成 " + ",".join([seen["SIGMA"]] * len(out["args"])))
        elif numbers and len(numbers) == len(out["args"]) \
                and all(value is not None for value in numbers):
            out["sigma_per_arg"] = tuple(numbers)       # placed onto the CVs
        else:
            notes.append(
                f"METAD 的 SIGMA={seen['SIGMA']} 不是数字、或者个数和 ARG "
                f"（{len(out['args'])} 个）对不上：PLUMED 会拒绝这一行，"
                "导入后请自己核对宽度")
            kept.append(f"SIGMA={seen['SIGMA']}")
    else:
        notes.append("METAD 行没有 SIGMA（手册把它列为必填）：导入后用了默认宽度，"
                     "请核对")
    out["height"] = number("HEIGHT", keep=True)      # None = no HEIGHT keyword
    pace = number("PACE", keep=False)
    if pace is not None:
        out["pace"] = int(round(pace))
    temp = number("TEMP", keep=True)
    if temp is not None:
        out["temperature"] = temp
    biasfactor = number("BIASFACTOR", keep=True)
    if biasfactor is not None:
        out["biasfactor"] = biasfactor
    sigma_min = seen.get("SIGMA_MIN")
    if sigma_min is not None:
        if _values(sigma_min) and len(_values(sigma_min)) == \
                len(sigma_min.replace(",", " ").split()):
            out["sigma_min"] = sigma_min
        else:
            kept.append(f"SIGMA_MIN={sigma_min}")
            notes.append(f"METAD 的 SIGMA_MIN={sigma_min} 不是数字列表：已原样保留")
    sigma_max = seen.get("SIGMA_MAX")
    if sigma_max is not None:
        if _values(sigma_max) and len(_values(sigma_max)) == \
                len(sigma_max.replace(",", " ").split()):
            out["sigma_max"] = sigma_max
        else:
            kept.append(f"SIGMA_MAX={sigma_max}")
            notes.append(f"METAD 的 SIGMA_MAX={sigma_max} 不是数字列表：已原样保留")
    if "FILE" in seen:
        out["hills_file"] = seen["FILE"] or "HILLS"
    if "RESTART" in seen:
        flag = seen["RESTART"].strip().upper()
        if flag in ("YES", "TRUE", "T", "1", "ON"):
            out["restart"] = True
        elif flag in ("NO", "FALSE", "F", "0", "OFF"):
            out["restart"] = False
        else:
            kept.append(f"RESTART={seen['RESTART']}")
            notes.append(f"METAD 的 RESTART={seen['RESTART']} 不是开关值"
                         "（YES/NO/AUTO）：已原样保留，界面上的勾选框留空")
    if "ADAPTIVE" in seen:
        mode = seen["ADAPTIVE"].strip().upper()
        if mode in ADAPTIVE_CHOICES[1:]:
            out["adaptive"] = mode
        else:
            kept.append(f"ADAPTIVE={seen['ADAPTIVE']}")
            notes.append(f"METAD 的 ADAPTIVE={seen['ADAPTIVE']} 不是 DIFF/GEOM："
                         "已原样保留，界面上的下拉框留空")
    out["extra"] = tuple(kept)
    return out


def _read_wall(kind: str, params: str, notes: list[str]) -> PlumedWall:
    """A ``UPPER_WALLS``/``LOWER_WALLS`` line as a :class:`PlumedWall`."""
    seen, kept = _pairs_by_key(params, _WALL_KEYS)

    def number(key: str) -> float | None:
        text = seen.get(key)
        if text is None:
            return None
        value = _num(text)
        if value is None:
            kept.append(f"{key}={text}")
            notes.append(f"{kind} 的 {key}={text} 不是数字（pi 之类）：已原样保留")
        return value

    return PlumedWall(arg=seen.get("ARG", ""), kind=kind.upper(),
                      at=number("AT"), kappa=number("KAPPA"),
                      exp=number("EXP"), offset=number("OFFSET"),
                      extra=tuple(kept))


def _detect_component(action: str, params: str) -> str:
    """Which reduction keyword a multi-component line already carries."""
    if PLUMED_ACTIONS.get(action.upper(), {}).get("kind") != "components":
        return ""
    found = ""
    for key, _value in parse_params(params):
        name = re.sub(r"\d+$", "", key).lower()
        if name in COMPONENTS:
            found = name
    return found


def _drop_elision(params: str, notes: list[str], where: str) -> str:
    """Remove a stray ``...`` from a keyword text.

    In PLUMED ``...`` is the continuation marker (see
    :func:`_fold_continuations`), never a keyword.  If one survives into the
    parameters the tool would write it back at the end of the line, where PLUMED
    reads it as "this action continues below" and swallows whatever follows - so
    it is dropped here with a note instead.
    """
    if "..." not in params:
        return params
    tokens = params.split()
    kept = [token for token in tokens if token != "..."]
    if len(kept) != len(tokens):
        notes.append(f"{where} 行里有一个单独的 `...`：它是 PLUMED 的续行标记、"
                     "不是关键字，留着它后面的内容都会被当成这一行的续行，"
                     "所以没有写回去")
    return " ".join(kept)


def _fold_continuations(text: str, notes: list[str]) -> list[str]:
    """Fold PLUMED's ``...`` continuation lines into one line per action.

    The manual ("Continuation lines") defines the syntax::

        DISTANCES ...
          LABEL=dist
          # comments and blank lines are allowed inside
          ATOMS1=1,300
          ATOMS2=1,400 ATOMS3=1,500
        ...

    The opening line may carry keywords before the ``...``, and the closing
    ``...`` may name the action again (``... DISTANCES``), which PLUMED checks.
    Folding first is what makes the rest of this reader work on that style: it
    used to see ``COORDINATION ...``, take the ``...`` for the parameters and
    keep every keyword that followed as an unrelated free line - so a
    hand-written multi-line file came back with its CVs emptied out and its
    ``METAD`` reduced to defaults.
    """
    out: list[str] = []
    collecting: list[str] | None = None
    opening = ""
    name = ""
    label = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if collecting is not None:
            if stripped.startswith("..."):
                tail = stripped[3:].strip()
                if tail:
                    first = tail.split()[0]
                    if name and first.upper() not in (name.upper(), label.upper()):
                        notes.append(
                            f"续行的结尾写的是 `... {first}`，而这一段开头是 "
                            f"`{opening}`：PLUMED 会检查两者是否一致（不一致直接"
                            f"报错），要么写 `...`，要么写 `... {name}`")
                out.append(" ".join(collecting))
                collecting = None
                continue
            if not stripped or stripped.startswith("#"):
                continue                 # blank lines and comments are allowed
            collecting.append(stripped)
            continue
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        if stripped.endswith("...") and not stripped.startswith("..."):
            head = stripped[:-3].rstrip()
            if not head:
                continue
            # the opening line may be a label, the action and keywords before the
            # dots - only the dots are dropped
            label, name, _params = split_line(head)
            opening = head
            collecting = [head]
            continue
        if stripped.startswith("..."):
            notes.append("文件里有一个多出来的 `...`（前面没有以 `...` 结尾的行），"
                         "已忽略：PLUMED 读到它会当成续行的结尾")
            continue
        out.append(line)
    if collecting is not None:
        notes.append(f"这一段 `{opening}` 的续行没有用 `...` 收尾："
                     "PLUMED 会把后面的内容一直当成它的续行（或者直接报错）。"
                     "已按「这一段就是一行」处理，请核对")
        out.append(" ".join(collecting))
    return out


def read(text: str) -> tuple[PlumedSetup, list[str]]:
    """Read a ``plumed.dat`` back into the form's model.

    Returns the setup plus the notes about everything that could not be placed.
    The contract: what the form models is taken over (CV lines, walls, ``UNITS``,
    the ``METAD``/``PRINT`` pair), everything else is kept **verbatim** - inside
    the parameters, in ``extra``, or as a free line - so a file that came out of
    the form survives the round trip, and a hand-written file keeps every line
    the form does not understand.
    """
    cvs: list[PlumedCv] = []
    walls: list[PlumedWall] = []
    bias_kwargs: dict[str, Any] = {}
    metad_args: tuple[str, ...] = ()
    bias_label = "m"
    units = "nm"
    free_before: list[str] = []
    free_after: list[str] = []
    notes: list[str] = []
    seen_metad = False

    for raw in _fold_continuations(text, notes):
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        label, action, params = split_line(line)
        params = _drop_elision(params, notes, label or action.upper())
        upper = action.upper()

        if upper == "UNITS":
            lengths = ""
            for key, value in parse_params(params):
                base = re.sub(r"\d+$", "", key)
                if base == "LENGTH":
                    lengths = value.strip()
                elif base in ("ENERGY", "TIME") and value.strip() not in \
                        _UNIT_SPELLINGS[base]:
                    # the strings are case sensitive (src/tools/Units.cpp compares
                    # them literally and the manual says so): "kJ/mol" aborts
                    if _num(value.strip()) is None:
                        notes.append(
                            f"UNITS 行里的 {base}={value.strip()} 不是 PLUMED 认的写法"
                            f"（大小写敏感，只能是 {'/'.join(_UNIT_SPELLINGS[base])}）"
                            "：PLUMED 会在启动时直接报错。工具生成的草稿会写成规范写法")
            if lengths.upper().startswith("A"):
                units = "A"
            elif lengths.upper().startswith("N") or _num(lengths) is not None:
                units = "nm"
            else:
                notes.append("UNITS 行里没有工具认识的 LENGTH=…：导入后按 nm 处理，"
                             "请核对此行：" + stripped)
            continue

        if upper == "METAD":
            if seen_metad:
                notes.append("文件里有不止一行 METAD：只导入了第一行，"
                             "其余当作自由行保留")
                free_after.append(stripped)
                continue
            seen_metad = True
            bias_label = label or "m"
            bias_kwargs = _read_metad(bias_label, params, notes)
            metad_args = tuple(bias_kwargs.get("args", ()))
            per_arg = bias_kwargs.pop("sigma_per_arg", None)
            if per_arg:
                # one width per ARG: put each onto the CV it belongs to, which is
                # where the table (and the GUI) keeps a per-CV σ
                placed: list[float] = []
                for name, value in zip(metad_args, per_arg):
                    found = next((cv for cv in cvs
                                  if cv.arg() == name
                                  or cv.label == name.split(".")[0]), None)
                    if found is not None:
                        cvs[cvs.index(found)] = replace(found, sigma=value)
                        placed.append(value)
                # `SIGMA=0.15,0.15` is not "two widths", it is "the width is 0.15":
                # lifting a uniform list into the field keeps the "默认 SIGMA" box
                # showing the number that is really in the file (and writes the very
                # same `SIGMA=0.15,0.15` back out).
                if len(placed) == len(metad_args) and len(set(placed)) == 1:
                    bias_kwargs["sigma"] = placed[0]
                    for name in metad_args:
                        found = next((cv for cv in cvs
                                      if cv.arg() == name
                                      or cv.label == name.split(".")[0]), None)
                        if found is not None:
                            cvs[cvs.index(found)] = replace(found, sigma=None)
            continue

        if upper in ("UPPER_WALLS", "LOWER_WALLS"):
            walls.append(_read_wall(upper, params, notes))
            continue

        if upper == "PRINT" and seen_metad:
            seen_print, kept = _pairs_by_key(params, _PRINT_KEYS)
            names = [item.strip() for item in seen_print.get("ARG", "").split(",")
                     if item.strip()]
            wanted = {*metad_args, f"{bias_label}.bias"}
            if not kept and metad_args and set(names) == wanted:
                bias_kwargs["print_colvar"] = True
                bias_kwargs["print_file"] = seen_print.get("FILE") or "COLVAR"
                stride = _num(seen_print.get("STRIDE") or 1)
                if stride is not None:
                    bias_kwargs["print_stride"] = int(round(stride))
                continue
            notes.append("这行 PRINT 不是 METAD 自己的输出（ARG 对不上，或带了工具"
                         "没建模的关键字），已作为自由行原样保留；"
                         "界面上的「写 COLVAR」不要重复打开")
            free_after.append(stripped)
            continue

        # a labelled action line: before METAD it is a CV, after METAD PLUMED
        # would not accept it as CV input anyway, so it keeps its place
        if label and not seen_metad:
            cvs.append(PlumedCv(label=label, action=upper, params=params,
                                component=_detect_component(upper, params)))
            continue
        if label:
            notes.append(f"标签 {label!r} 的动作在 METAD 之后，PLUMED 不允许 METAD "
                         "引用它：已作为自由行原样保留")
        free_after.append(stripped)

    if free_before and free_after:
        # one free-lines box can only hold one position; a `<bias>.bias`
        # reference is what breaks when it moves up, so that case goes last
        end = any(re.search(rf"\b{re.escape(bias_label)}\.", item)
                  for item in free_after)
        notes.append("文件里 METAD 之前和之后都有工具不建模的内容：只有一个「自由行」"
                     f"框，已统一放到{'末尾' if end else ' METAD 之前'}，"
                     "请手工核对顺序")
        where = "end" if end else "before"
    elif free_after:
        where = "end"
    else:
        where = "before"

    bias = PlumedBias(**bias_kwargs) if bias_kwargs else PlumedBias(print_colvar=True)
    setup = PlumedSetup(cvs=tuple(cvs), walls=tuple(walls), bias=bias,
                        free_lines=tuple(free_before + free_after),
                        free_where=where, units=units)
    if not setup.active:
        notes.append("这个文件里没有工具认识的 CV、墙或自由行——是不是选错了文件？")
    # a free line is written back exactly as it was typed, so a `...` inside one
    # would go out as a continuation marker (PLUMED then reads the rest of the
    # file as part of that line) - say so instead of writing it silently
    for item in setup.free_lines:
        if "..." in item.split():
            notes.append("自由行里有单独的 `...`：" + item.strip()
                         + " —— 它是 PLUMED 的续行标记，请手工确认这一行")
            break
    return setup, notes


def read_file(path: str) -> tuple[PlumedSetup, list[str]]:
    """Read a ``plumed.dat`` from disk (UTF-8 first, GBK as a fallback)."""
    with open(path, "rb") as handle:
        raw = handle.read()
    encoding_used = "utf-8"
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            text = raw.decode(encoding)
            encoding_used = encoding
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin-1")
        encoding_used = "latin-1"
    setup, notes = read(text)
    if encoding_used not in ("utf-8", "utf-8-sig"):
        notes.insert(0, f"文件不是 UTF-8（按 {encoding_used} 读的）："
                        "里面的非 ASCII 注释可能已经变形")
    if not text.strip():
        notes.insert(0, "文件是空的")
    return setup, notes


# --------------------------------------------------------------------------- #
# generating a "molecule contact" CV table
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ContactSpec:
    """A contact CV between molecule types: one coordination number per pair.

    ``blocks`` describes the system as the molecule types it is made of, in atom
    order: ``"19x1,8x20"`` is one 19-atom molecule followed by twenty 8-atom ones
    - the 4-CPBA + 20 urea system.  ``center_offsets``/``acceptor_offsets`` are
    atom positions *inside* a block (0-based); an empty acceptor list means every
    atom of the acceptor block.  The result is the usual shape of a reactive CV:
    one ``COORDINATION`` line per molecule (paired with the acceptors of all the
    *other* molecules) and, optionally, one ``COMBINE`` line that adds them up.
    """

    blocks: str = "19x1,8x20"
    #: 1-based index into the block list
    center_block: int = 2
    #: 0-based atoms inside the center block
    center_offsets: str = "1"
    acceptor_block: int = 2
    #: 0-based atoms inside the acceptor block; empty = the whole block
    acceptor_offsets: str = "0,2"
    exclude_same_molecule: bool = True
    r0: float = 1.55
    nn: int = 12
    mm: int = 24
    prefix: str = "c"
    start: int = 1
    #: empty = no COMBINE row
    combine_label: str = "cv_uu"
    #: add NOPBC (an isolated system; PLUMED would otherwise use the cell)
    nopbc: bool = False


def parse_blocks(spec: str) -> list[tuple[int, int]]:
    """``"19x1,8x20"`` -> ``[(19, 1), (8, 20)]`` (atoms per block, how many)."""
    out: list[tuple[int, int]] = []
    text = spec.replace("*", "x").replace(":", "x").replace(";", ",")
    for part in text.split(","):
        part = part.strip().lower()
        if not part:
            continue
        count, _sep, number = part.partition("x")
        if not count.strip().isdigit():
            raise ValueError(f"看不懂的分块写法：{part!r}（应该像 19x1,8x20）")
        atoms = int(count)
        copies = int(number) if number.strip().isdigit() else 1
        if atoms <= 0 or copies <= 0:
            raise ValueError(f"分块里的数字要正数：{part!r}")
        out.append((atoms, copies))
    if not out:
        raise ValueError("分块是空的：至少要写一个分子的原子数，例如 8x20")
    return out


def _offset_list(text: str) -> list[int]:
    """``"0,2"`` -> ``[0, 2]`` (anything that is not a number is ignored)."""
    return [int(part) for part in re.split(r"[,;\s]+", text.strip()) if part.isdigit()]


def _block_atoms(blocks: list[tuple[int, int]]) -> list[tuple[int, int, int]]:
    """``(block index, copy index, first atom)`` per molecule, 1-based atoms."""
    out: list[tuple[int, int, int]] = []
    first = 1
    for index, (atoms, copies) in enumerate(blocks, start=1):
        for copy in range(1, copies + 1):
            out.append((index, copy, first))
            first += atoms
    return out


def contact_cvs(spec: ContactSpec, structure: Any = None, natoms: int | None = None,
                units: str = "nm") -> tuple[list[PlumedCv], list[str]]:
    """The table rows a contact spec describes, plus the notes to read.

    ``units`` is the length unit ``R_0`` is written in (it has to match the file's
    ``UNITS`` line); it only matters for the min-image note.  A spec that cannot
    describe a CV at all (a block index that does not exist, an offset outside its
    block) raises ``ValueError`` - the caller shows that text.
    """
    blocks = parse_blocks(spec.blocks)
    total = sum(atoms * copies for atoms, copies in blocks)
    if natoms is None and structure is not None:
        getter = getattr(structure, "n_atoms", None)
        natoms = getter() if callable(getter) else None
    notes: list[str] = []
    if natoms is not None and total != natoms:
        notes.append(f"分块加起来是 {total} 个原子、体系有 {natoms} 个："
                     "原子序号会整体错位，请先把分块改对")

    def block_of(index: int, what: str) -> tuple[int, int]:
        if not 1 <= index <= len(blocks):
            raise ValueError(f"{what}写的是第 {index} 块，但分块只有 {len(blocks)} 块"
                             f"（{spec.blocks}）")
        return blocks[index - 1]

    center_block = int(spec.center_block)
    acceptor_block = int(spec.acceptor_block)
    atoms_per_center, _n = block_of(center_block, "中心块")
    atoms_per_acceptor, _n = block_of(acceptor_block, "受体块")
    center_offsets = _offset_list(spec.center_offsets)
    acceptor_offsets = _offset_list(spec.acceptor_offsets)
    if not center_offsets:
        raise ValueError("中心原子的偏移是空的：写它在分子里是第几个原子（0 起算）")
    for offset in center_offsets:
        if offset >= atoms_per_center:
            raise ValueError(f"中心偏移 {offset} 超出了块大小"
                             f"（{atoms_per_center} 个原子）")
    for offset in acceptor_offsets:
        if offset >= atoms_per_acceptor:
            raise ValueError(f"受体偏移 {offset} 超出了块大小"
                             f"（{atoms_per_acceptor} 个原子）")
    if not acceptor_offsets:
        acceptor_offsets = list(range(atoms_per_acceptor))

    where = _block_atoms(blocks)
    elements = [str(getattr(atom, "symbol", "?")).strip()
                for atom in getattr(structure, "atoms", [])] if structure else []

    def symbol(atom: int) -> str:
        return f"{elements[atom - 1]}{atom}" if 0 < atom <= len(elements) else str(atom)

    def name(block: int, copy: int) -> str:
        return f"块{block}-{copy}" if len(blocks) > 1 else f"第 {copy} 个分子"

    centers = [item for item in where if item[0] == center_block]
    acceptors = [item for item in where if item[0] == acceptor_block]
    same_block = center_block == acceptor_block
    rows: list[PlumedCv] = []
    index = int(spec.start)
    for block, copy, first in centers:
        group_b: list[int] = []
        for _other_block, other_copy, other_first in acceptors:
            if spec.exclude_same_molecule and same_block and other_copy == copy:
                continue
            group_b.extend(other_first + offset for offset in acceptor_offsets)
        if not group_b:
            notes.append(f"{name(block, copy)} 没有可配对的受体原子（同一分子被排除，"
                         "而它又是块里唯一的一个）——这一行没有生成")
            continue
        for offset in center_offsets:
            atom = first + offset
            params = (f"GROUPA={atom} GROUPB={','.join(str(item) for item in group_b)}"
                      f" R_0={_fmt(spec.r0)} NN={int(spec.nn)} MM={int(spec.mm)}")
            if spec.nopbc:
                params += " NOPBC"
            rows.append(PlumedCv(
                label=f"{spec.prefix}{index}", action="COORDINATION", params=params,
                note=f"{name(block, copy)} 的 {symbol(atom)}"
                     f"（与 {len(group_b)} 个受体原子配对）"))
            index += 1
    if not rows:
        raise ValueError("这个分块/偏移组合生不出任何 CV：请检查中心块和受体块")
    combine_label = spec.combine_label.strip()
    if combine_label and len(rows) > 1:
        rows.append(PlumedCv(
            label=combine_label, action="COMBINE",
            params=f"ARG={','.join(row.label for row in rows)} "
                   f"COEFFICIENTS={','.join('1' for _ in rows)} PERIODIC=NO",
            note=f"把上面 {len(rows)} 个配位数加起来 = 一个 CV"))
    made = len(rows) - (1 if combine_label and len(rows) > 1 else 0)
    notes.append(f"生成了 {made} 个配位数（原子按 {spec.blocks} 从 1 起顺序编号；"
                 "注释里写了每个中心原子是谁，请照着体系核对一遍）")

    cell = getattr(structure, "cell", None) if structure is not None else None
    checker = getattr(structure, "is_periodic", None) if structure is not None else None
    periodic = bool(checker()) if callable(checker) else False
    if periodic and cell is not None and not spec.nopbc:
        r0_angstrom = spec.r0 if str(units).strip().upper().startswith("A") \
            else spec.r0 * 10.0
        half = 0.5 * min(cell.lengths())
        if r0_angstrom > half:
            notes.append(f"R_0={spec.r0:g} 比半个盒子（{half:.2f} Å）还大："
                         "周期体系里 PLUMED 按最小镜像算距离，这么宽的开关会把"
                         "「从另一侧绕过来」的原子也算成接触")
    return rows, notes
