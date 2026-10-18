"""Validation: schema checks against the manual plus physics sanity checks.

Two independent layers are combined:

* **schema** - every section and keyword is looked up in the CP2K manual
  database, enum values are checked against the manual's own value table and
  scalar types are verified;
* **semantics** - the mistakes that actually cost people a day: valence
  mismatches between basis set and pseudopotential, missing ``&KIND`` blocks,
  inconsistent ``RUN_TYPE``, k-points without periodicity, smearing with OT,
  electronic-charge parity against the multiplicity, and so on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .datafiles import DataIndex
from .manualdb import ManualDB, manual_root
from .model import Keyword, Section
from .recipes import POISSON_SOLVER_WHY, poisson_solvers_for
from .structures import ELEMENTS, canonical_element
from .units import units_in as unknown_unit_annotations
from .units import check as unit_problem
from .units import suggest as unit_suggestions

MANUAL = manual_root()
"""Manual base URL, following whichever database version is in use."""

LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}

COORD_SECTIONS = {
    "COORD", "VELOCITY", "CORE_COORD", "CORE_VELOCITY",
    "SHELL_COORD", "SHELL_VELOCITY",
}

TRUE_WORDS = {"t", "true", ".true.", "yes", "on", "y", "1"}
FALSE_WORDS = {"f", "false", ".false.", "no", "off", "n", "0"}

# keywords CP2K still accepts although the current manual no longer documents
# them; the value is the keyword they are equivalent to
LEGACY_ALIASES = {
    "LSD": "UKS",
    "CUTOFF_LIST": "MULTIGRID_CUTOFF",
    "RELATIVE_CUTOFF": "REL_CUTOFF",
}

# values of BASIS_SET / POTENTIAL that switch behaviour instead of naming a
# data-file entry
SPECIAL_KIND_VALUES = {
    "NONE", "ALL", "ALLELECTRON", "UPF", "ECP", "GTH", "SGP", "SAP",
    "ORB", "AUX", "RI", "FIT",
}

# methods that obtain their basis/element data from elsewhere, so a missing
# &KIND block is not an error.  Used only as a fallback: when the manual
# database is available the list is taken from &QS METHOD itself, so a new
# semi-empirical method (PNNL, PDG, ...) needs no code change here.
NO_KIND_METHODS = {"DFTB", "DFTB3", "XTB", "SE", "PM3", "PM6", "PM6-FM", "PM6PDDG",
                   "PM3PDDG", "AM1", "MNDO", "MNDOD", "RM1", "PDDG", "PDG", "PNNL",
                   "DFTB2"}
NO_KIND_FORCE_EVAL = {"FIST", "MM", "EIP", "NNP", "MIXED"}
NO_KIND_PATTERN = re.compile(r"^(PM\d|AM\d|MNDO|RM\d|DFTB|XTB|SE\d*)$")

# the methods that actually use the GPW/GAPW machinery, and therefore OT or
# diagonalisation
AB_INITIO_METHODS = {"GPW", "GAPW", "GAPW_XC", "LRIGPW", "RIGPW", "OFGPW",
                     "QUICKSTEP", "QS"}


def is_ab_initio(db, qs_method: str) -> bool:
    """Whether ``&QS METHOD`` uses basis sets, OT and diagonalisation at all."""
    upper = (qs_method or "").upper()
    if db is not None:
        values = {v.upper() for v in db.valid_values("FORCE_EVAL/DFT/QS", "METHOD")}
        if values and upper in values:
            return upper in AB_INITIO_METHODS
    return upper in AB_INITIO_METHODS or (
        upper not in NO_KIND_METHODS and not NO_KIND_PATTERN.match(upper))

# sections whose parameter can switch them off (&OT OFF, &FORCES OFF, ...)
EXPRESSION = re.compile(r"[-+*/^().\d\s]+")


#: Ensembles whose cell is allowed to change, i.e. the ones that need a stress
#: tensor and a barostat (``enum_c_vals`` of ``MOTION/MD/ENSEMBLE`` in
#: ``src/motion/input_cp2k_md.F``; CP2K has no plain ``NPT``/``ISO`` value).
NPT_ENSEMBLES = frozenset({"NPT_I", "NPT_F", "NPT_IA", "NPE_I", "NPE_F",
                           "MSST", "MSST_DAMPED"})


def _enum_word(value) -> str:
    """An enumeration value as CP2K spells it.

    The input parser turns a bare ``ON``/``OFF``/``YES``/``NO`` into a boolean
    (they are legitimate logical values), so an enumeration such as ``PARMTYPE``
    - whose values include ``OFF`` - comes back as ``False``.  Map it back before
    comparing against CP2K's own value lists.
    """
    if value is True:
        return "ON"
    if value is False:
        return "OFF"
    return str(value).upper()


def _bool_word(value) -> bool:
    """A logical keyword that may have come back as a Python bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lstrip(".").rstrip(".").upper() in (
        "T", "TRUE", "ON", "YES", "1")


def section_enabled(section: Section) -> bool:
    """False when the section parameter switches the block off (&OT OFF)."""
    parameter = section.parameter
    if parameter is None:
        return True
    if isinstance(parameter, list):
        if not parameter:
            return True
        parameter = parameter[0]
    if isinstance(parameter, bool):
        return parameter
    text = str(parameter).strip().lower()
    if text in FALSE_WORDS:
        return False
    return True


def has_preprocessor(value) -> bool:
    return "$" in str(value) or "@" in str(value)


def _is_logical_word(value) -> bool:
    """Whether a value is how CP2K writes a logical (T/F/TRUE/ON/1/...)."""
    return str(value).strip().lower() in (TRUE_WORDS | FALSE_WORDS)


def _is_true(value) -> bool:
    """A logical keyword value that means "on" (``T`` / ``TRUE`` / ``ON`` / ...)."""
    return value is not None and str(value).strip().lower() in TRUE_WORDS


def _is_expression(value) -> bool:
    """CP2K evaluates simple arithmetic in input values (``1/0.129629``)."""
    text = str(value).strip()
    if not text:
        return False
    return bool(EXPRESSION.fullmatch(text)) and any(c in text for c in "+-*/^")


@dataclass
class Diagnostic:
    level: str
    code: str
    message: str
    path: str | None = None
    hint: str | None = None
    url: str | None = None

    def render(self) -> str:
        tag = {"error": "ERROR", "warning": "WARN ", "info": "NOTE "}[self.level]
        where = f" [{self.path}]" if self.path else ""
        text = f"{tag} {self.code}{where}: {self.message}"
        if self.hint:
            text += f"\n        -> {self.hint}"
        if self.url:
            text += f"\n        -> {self.url}"
        return text


def _coordinate_row(keyword) -> bool:
    """Whether one ``&COORD`` line really is ``label x y z``.

    The numbers may be plain or CP2K expressions (``1/3``, ``0*1/3``, ``1./3.``),
    and a row with fewer than three of them is a keyword (``SCALED``, ``UNIT``) or
    something CP2K would refuse.
    """
    if len(keyword.values) < 3:
        return False
    for value in keyword.values[:3]:
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            continue
        if _is_expression(value):
            continue
        return False
    return True


def _coord_file_of(topology) -> Any:
    """``&TOPOLOGY``'s coordinate file, under either spelling.

    The manual name is ``COORD_FILE_NAME``; CP2K's own FIST regression inputs and
    this tool's generator write the alias ``COORD_FILE`` (46 corpus inputs), so a
    check that only knows the long name sees coordinates that are right there.
    """
    if topology is None:
        return None
    return topology.get("COORD_FILE_NAME") or topology.get("COORD_FILE")


def atomic_number(symbol: str) -> int:
    try:
        return ELEMENTS.index(symbol) + 1
    except ValueError:
        return 0


class Validator:
    def __init__(self, db: ManualDB | None = None,
                 index: DataIndex | None = None):
        self.db = db
        self.index = index
        self.diagnostics: list[Diagnostic] = []
        self._files_verified = False
        self._unverified_noted = False
        #: preprocessor lines (``@INCLUDE``/``@IF``/``@SET``) of the input being
        #: checked.  They are not executed, so a section they would have supplied
        #: is genuinely absent from the tree - the checks have to say "I cannot
        #: see it" rather than "it is missing".
        self.directives: tuple[str, ...] = ()

    def _data_files_indexed(self, dft: Section | None) -> bool:
        """True when the input's data files are the ones we actually indexed.

        A CP2K test suite ships its own ``BASIS_SET`` next to the input, so a
        name that is absent from <cp2k>/data proves nothing about availability.
        """
        if self.index is None or self.index.directory is None or dft is None:
            return False
        known_basis = {name.upper() for name in self.index.basis_files}
        known_potential = {name.upper() for name in self.index.potential_files}
        declared_basis = _declared_files(dft, "BASIS_SET_FILE_NAME")
        declared_potential = _declared_files(dft, "POTENTIAL_FILE_NAME")
        if not declared_basis and not declared_potential:
            return False
        for name in declared_basis:
            # a relative path ("../GTH_BASIS_SETS") points at a file next to the
            # input, which is not the copy we indexed
            if "/" in name or "\\" in name:
                return False
            if Path(name).name.upper() not in known_basis:
                return False
        for name in declared_potential:
            if "/" in name or "\\" in name:
                return False
            if Path(name).name.upper() not in known_potential:
                return False
        return True

    # -- helpers ----------------------------------------------------------- #
    def _add(self, level: str, code: str, message: str, **kwargs) -> None:
        self.diagnostics.append(Diagnostic(level, code, message, **kwargs))

    def run(self, sections: Sequence[Section],
            directives: Sequence[str] = ()) -> list[Diagnostic]:
        self.diagnostics = []
        self.directives = tuple(directives)
        if self.db is not None:
            for section in sections:
                self._check_section(section, "CP2K_INPUT")
            self._check_repeated_root(sections)
        self._check_units(sections)
        self._check_semantics(sections)
        self.diagnostics.sort(key=lambda d: (LEVEL_ORDER[d.level], d.path or "", d.code))
        return self.diagnostics

    def _check_repeated_root(self, sections: Sequence[Section]) -> None:
        """A repeated *top-level* section: CP2K aborts and used to pass silently.

        ``run()`` hands each root section to ``_check_section`` separately, so the
        sibling count inside that method can never see two ``&GLOBAL`` blocks.
        ``FORCE_EVAL`` is the one root section CP2K allows to repeat.
        """
        counts: dict[str, int] = {}
        for section in sections:
            counts[section.name.upper()] = counts.get(section.name.upper(), 0) + 1
        for name, count in counts.items():
            if count < 2:
                continue
            info = self.db.section(f"CP2K_INPUT/{name}")
            if info is not None and not info.repeatable:
                self._add(
                    "error", "section-not-repeatable",
                    f"&{name} 出现了 {count} 次，而它不可重复：CP2K 读输入时直接中止"
                    "（`Section ... should not repeat`，input/input_parsing.F）",
                    path="CP2K_INPUT",
                )

    def _check_units(self, sections: Sequence[Section]) -> None:
        """Unit annotations CP2K's input parser would refuse.

        ``string_to_unit`` (``common/cp_units.F``) resolves ``[unit]`` while
        reading the input, and an unknown string aborts the run before any
        calculation::

            [ABORT] Unknown unit: a.u.        common/cp_units.F:384

        A bare ``[au]`` is refused too, with a message naming the kinds it
        wants.  Both are worth an error: they are cheap to make by hand (the
        manual writes "a.u." in prose) and cost a whole queue slot.
        """
        for path, keyword, unit in unknown_unit_annotations(sections):
            message = unit_problem(unit)
            if message is None:
                continue
            hint = None
            near = unit_suggestions(unit)
            if near:
                hint = "CP2K 认识的是：" + "、".join(f"`[{u}]`" for u in near) \
                       + "；内部原子单位建议不写标注"
            self._add("error", "unknown-unit", message,
                      path=f"CP2K_INPUT/{path}", hint=hint)

    #: Preprocessor directives that inject sections this linter cannot see:
    #: ``@INCLUDE`` pulls in a file and ``@FFTYPE`` expands a whole
    #: ``&FORCEFIELD`` block out of CP2K's own ``data/`` directory
    #: (``cp_parser_inpp_methods.F``).
    CONTENT_DIRECTIVES = ("@INCLUDE", "@FFTYPE", "@XCTYPE")

    def _included(self) -> bool:
        """Whether the input pulls in sections from somewhere this linter
        cannot read (an ``@INCLUDE``/``@FFTYPE`` expansion).

        The parser records directives as ``(line_number, text)`` pairs.
        """
        for entry in self.directives:
            text = entry[1] if isinstance(entry, tuple) and len(entry) > 1 else entry
            upper = str(text).upper()
            if any(upper.startswith(name) for name in self.CONTENT_DIRECTIVES):
                return True
        return False

    # ------------------------------------------------------------------ #
    # schema layer
    # ------------------------------------------------------------------ #
    def _check_section(self, section: Section, parent_path: str,
                       verified: bool = True) -> None:
        if not verified:
            # inside a region the database does not describe in detail
            for child in section.sections:
                self._check_section(child, f"{parent_path}/{section.name}",
                                    verified=False)
            return
        assert self.db is not None
        candidate = f"{parent_path}/{section.name}"
        known_here = self.db.section(candidate)
        info = known_here or self.db.section(section.name)
        if info is None:
            if self.db.accepts_subsection(parent_path, section.name):
                self._add(
                    "info", "dynamic-subsection",
                    f"&{section.name} 是手册列在 "
                    f"&{parent_path.split('/')[-1]} 里的子段（每个名字一个段，"
                    "手册没有单独的页面）",
                    path=parent_path,
                )
                for child in section.sections:
                    self._check_section(child, candidate, verified=False)
                return
            if section.name.upper() in {s.upper()
                                        for s in self.db.subsections(parent_path)} \
                    or _matches_listed_subsection(self.db.subsections(parent_path),
                                                  section.name):
                # listed as a subsection but the manual has no page of its own
                for child in section.sections:
                    self._check_section(child, candidate, verified=False)
                return
            # CP2K creates a subsection for every switch-like keyword it defines
            # (the XC functional blocks are the classic case: the manual lists
            # BECKE97 as a keyword of XC_FUNCTIONAL, the input writes &BECKE97)
            if self.db.has_keyword(parent_path, section.name):
                self._add(
                    "info", "keyword-as-subsection",
                    f"&{section.name} 手册里记作 "
                    f"&{parent_path.split('/')[-1]} 的关键字（CP2K 会把它读成段，"
                    "所以这种写法有效）",
                    path=parent_path,
                )
                for child in section.sections:
                    self._check_section(child, candidate, verified=False)
                return
            # the manual sometimes spells a section differently from CP2K itself
            # (&LENNARD is documented as LENNARD-JONES)
            if self.db.is_source_section(section.name):
                self._add(
                    "info", "section-not-in-manual",
                    f"&{section.name} 存在于 CP2K 自己的输入定义里，"
                    "手册把它记在别的名字或别的位置下",
                    path=parent_path,
                )
                for child in section.sections:
                    self._check_section(child, candidate, verified=False)
                return
            self._add(
                "error", "unknown-section",
                f"&{section.name} 不在 CP2K 输入手册里",
                path=parent_path,
                hint="检查拼写；手册里有全部段和子段的名字",
            )
            return
        if known_here is None:
            # a section listed as a subsection of the parent is valid even when
            # the manual has no page of its own for it
            if section.name.upper() in {s.upper() for s in self.db.subsections(parent_path)}:
                for child in section.sections:
                    self._check_section(child, candidate, verified=False)
                return
            # a section that lives somewhere else in the tree is a real error,
            # unless the parent simply has no page listing it
            if self.db.has_keyword(parent_path, section.name):
                self._add(
                    "info", "section-from-keyword",
                    f"&{section.name} 是 "
                    f"&{parent_path.split('/')[-1]} 的关键字，这里写成了段",
                    path=parent_path,
                )
                return
            self._add(
                "error", "misplaced-section",
                f"&{section.name} 确实存在，但它不是 "
                f"&{parent_path.split('/')[-1]} 的子段",
                path=parent_path,
                hint=f"这里合法的子段只有："
                     f"{', '.join(self.db.subsections(parent_path)) or '（没有）'}",
                url=info.url,
            )
            return

        path = candidate

        aliases: dict[str, str] = {}
        for name, kw in self.db.keywords(path).items():
            for alias in kw.aliases:
                aliases[alias.upper()] = name

        accepts_default = "DEFAULT_KEYWORD" in {
            k.upper() for k in (self.db.section(path).keywords if self.db.section(path) else [])
        }

        seen: dict[str, Keyword] = {}
        for keyword in section.keywords:
            if self._is_coordinate_line(section, keyword):
                continue
            key = keyword.name.upper()
            kw_info = self.db.keyword(path, key)
            if kw_info is None:
                # CP2K matches dashes and underscores interchangeably in some
                # places (the manual says K_DIMER, inputs write K-DIMER)
                swapped = key.replace("-", "_") if "-" in key else key.replace("_", "-")
                if swapped != key and self.db.has_keyword(path, swapped):
                    self._add("info", "keyword-underscore-dash",
                              f"{key} 在手册里写成 {swapped}（CP2K 把 - 和 _ 当同一个，"
                              "两种写法都收）",
                              path=path,
                              hint=f"想让文件里写的就是手册里的名字，就改成 {swapped}")
                    kw_info = self.db.keyword(path, swapped)
            if kw_info is None and key in aliases:
                target = aliases[key]
                # both spellings are legal, so say that instead of leaving the
                # reader wondering whether something has to be fixed.  The short
                # spelling is the common one: CP2K's own regtest inputs write
                # CONNECTIVITY 842 times and CONN_FILE_FORMAT 33 times.
                self._add(
                    "info", "keyword-alias",
                    f"{key} 是手册里 {target} 的别名（&{section.name} 里两种写法 "
                    "CP2K 都收，不用改）",
                    path=path,
                    hint=f"手册那页的关键字标题是 {target}；"
                         f"`cp2kbuild keyword {target}` 可以直接翻到它",
                )
                kw_info = self.db.keyword(path, target)
            if kw_info is None and key in LEGACY_ALIASES:
                target = LEGACY_ALIASES[key]
                if self.db.has_keyword(path, target):
                    self._add(
                        "info", "legacy-keyword",
                        f"{key} 是 {target} 的旧写法（&{section.name} 里仍然有效，"
                        "手册已经改成新名字）",
                        path=path,
                        hint=f"新输入建议写 {target}；旧文件不用动",
                    )
                    kw_info = self.db.keyword(path, target)
            if kw_info is None:
                if accepts_default:
                    # e.g. &FRAGMENT takes free-form data lines
                    continue
                if key in seen:
                    continue
                if not keyword.values and self.db.has_keyword(path, "SECTION_PARAMETERS"):
                    # a bare token in a section that takes a parameter is more
                    # likely that parameter written on its own line
                    self._add(
                        "warning", "possible-section-parameter",
                        f"{key} 不是 &{section.name} 的关键字",
                        path=path,
                        hint=f"如果它是这个段的段参数，就写在段开头那一行："
                             f"`&{section.name} {key}`",
                        url=self.db.section_url(path),
                    )
                    continue
                if self.db.is_source_keyword(key):
                    # CP2K knows the keyword, the manual page just does not list it
                    self._add(
                        "info", "keyword-not-in-manual",
                        f"{key} 是 CP2K 输入定义里 &{section.name} 的关键字，"
                        "只是手册那一页没列出来",
                        path=path,
                    )
                    continue
                if not keyword.values and keyword.unit is None:
                    # a lone token with no value: a ``@IF``/``@ENDIF`` branch that
                    # CP2K's preprocessor may drop, a placeholder someone left in
                    # (``XXXXXXX``), or a genuine typo.  Without expanding the
                    # preprocessor the schema cannot tell them apart, so this is a
                    # warning rather than an error (CP2K's own regression suite has
                    # such files).
                    self._add(
                        "warning", "unknown-keyword-bare",
                        f"{key} 在 &{section.name} 里既不是关键字、也没有取值："
                        "可能是 @IF/@ENDIF 分支里的占位符，或者关键字拼错了"
                        "（CP2K 真读到它会中止）",
                        path=path,
                        hint="确认它确实该出现在这一段里；拼错的话手册里有正确写法",
                        url=self.db.section_url(path),
                    )
                    continue
                self._add(
                    "error", "unknown-keyword",
                    f"{key} 不是 &{section.name} 的关键字",
                    path=path,
                    hint="用 `cp2kbuild keyword <名字>` 离线搜手册",
                    url=self.db.section_url(path),
                )
                continue
            if key in seen and not self.db.keyword_repeatable(path, key):
                # a warning, not an error: the same keyword legitimately appears
                # once per @IF/@ELSE branch, and this reader does not expand the
                # preprocessor (CP2K's own regression suite is full of that)
                self._add(
                    "warning", "duplicate-keyword",
                    f"{key} 在 &{section.name} 里出现了两次：CP2K 读输入时会中止"
                    "（`Keyword ... should not repeat`）——但若两次分别在 "
                    "@IF/@ELSE 的不同分支里，运行时只会读到其中一个",
                    path=path,
                    hint="真的要写两次就换成可重复的关键字；否则删掉一处",
                )
            seen[key] = keyword
            self._check_keyword(section, keyword, kw_info, path)

        self._check_parameters(section, info, path)

        # repeated subsections that CP2K would reject
        counts: dict[str, int] = {}
        for child in section.sections:
            counts[child.name.upper()] = counts.get(child.name.upper(), 0) + 1
        for name, count in counts.items():
            if count < 2:
                continue
            child_info = self.db.section(f"{path}/{name}")
            if child_info is not None and not child_info.repeatable:
                # same caveat as for keywords: @IF/@ELSE branches look like a
                # repeat to a reader that does not expand the preprocessor
                self._add(
                    "warning", "section-not-repeatable",
                    f"&{name} 在 &{section.name} 里出现了 {count} 次，而它不可重复："
                    "CP2K 读输入时会中止（`Section ... should not repeat`）——"
                    "除非它们分处 @IF/@ELSE 的不同分支",
                    path=path,
                    url=child_info.url,
                )

        for child in section.sections:
            self._check_section(child, path)

    def _is_coordinate_line(self, section: Section, keyword: Keyword) -> bool:
        if section.name.upper() not in COORD_SECTIONS:
            return False
        if len(keyword.values) < 3:
            return False
        return all(isinstance(v, (int, float)) and not isinstance(v, bool)
                   for v in keyword.values[:3])

    def _check_keyword(self, section: Section, keyword: Keyword, info, path: str) -> None:
        assert self.db is not None
        if info.deprecated:
            self._add("warning", "deprecated-keyword",
                      f"{keyword.name} 手册里已标记为过时（deprecated）",
                      path=path, url=info.url)
        if not keyword.values:
            return

        kind = (info.type or "").lower()
        values = keyword.values

        # the value may be decided by the preprocessor at run time
        if any(has_preprocessor(v) for v in values):
            return

        if kind == "logical":
            for value in values:
                if isinstance(value, bool):
                    continue
                if str(value).lower() in TRUE_WORDS | FALSE_WORDS:
                    continue
                if _is_expression(value):
                    continue
                self._add(
                    "error", "bad-logical",
                    f"{keyword.name} 要的是逻辑值（T/F），这里是 {value!r}",
                    path=path, url=info.url,
                )
        elif kind in ("integer", "int"):
            if not all(_looks_int(v) for v in values):
                self._add("error", "bad-integer",
                          f"{keyword.name} 要的是整数，这里是 {_short(values)}",
                          path=path, url=info.url)
        elif kind in ("real", "float"):
            if not all(_looks_real(v) for v in values):
                self._add("error", "bad-real",
                          f"{keyword.name} 要的是数值，这里是 {_short(values)}",
                          path=path, url=info.url)

        # A numeric scalar takes one value and CP2K aborts on an extra one ("found
        # unexpected extra argument", input/input_parsing.F) - but the manual's
        # type cannot tell a scalar from a list: ``NUM_GTO 15 15 10 5``,
        # ``GMAX 25 25 25`` and ``LIST {integer} ..`` are all typed ``integer``.
        # What does separate them is the keyword's own usage example: it shows
        # exactly one number for the scalars (``cutoff 300``) and either several
        # numbers or ``{integer} ..`` for the lists.  So the rule fires only when
        # that example carries a single number.
        if kind in ("integer", "real") and len(values) > 1 \
                and len(re.findall(r"[-+]?\d+(?:\.\d+)?", info.usage or "")) == 1:
            # a warning, not an error: the usage example is a *hint*, and a
            # handful of CP2K's own keywords are lists whose example still shows
            # one number (exotic table rows).  CP2K itself is strict here, so the
            # user should look at it.
            self._add(
                "warning", "too-many-values",
                f"{keyword.name} 看起来只接受一个取值，这里给了 {len(values)} 个："
                f"{_short(values)}；CP2K 遇到多余的取值会中止"
                "（`found unexpected extra argument`）——若它其实是列表关键字，忽略这条",
                path=path, url=info.url,
            )

        if kind == "enum" and info.valid_values and not isinstance(values[0], bool):
            allowed = {v.upper() for v in info.values}
            head = values[0]
            # A logical is left alone: CP2K's own inputs write `.FALSE.` for some
            # enum keywords (CONNECTIVITY, 45 corpus files).  A *number* is never a
            # member (0 corpus files) and CP2K aborts on it - ``ENSEMBLE 5`` used
            # to pass because the check skipped anything that was not a string.
            shown = str(head)
            if shown.upper() not in allowed and not _is_expression(head):
                self._add(
                    "error", "bad-enum",
                    f"{keyword.name} = {head!r} 不是合法的取值",
                    path=path,
                    hint=f"合法取值：{', '.join(info.values[:14])}"
                         + (" ..." if len(info.values) > 14 else ""),
                    url=info.url,
                )

    def _check_parameters(self, section: Section, info, path: str) -> None:
        """Section default parameters, e.g. ``&XC_FUNCTIONAL PBE``."""
        assert self.db is not None
        if section.parameter is None:
            return
        parameter = section.parameter
        if isinstance(parameter, list):
            parameter = parameter[0]
        if not isinstance(parameter, str):
            return

        own = self.db.keyword(path, section.name)
        if own is not None and own.valid_values:
            if parameter.upper() not in {v.upper() for v in own.values}:
                self._add(
                    "error", "bad-section-parameter",
                    f"&{section.name} {parameter} 不是合法的段参数",
                    path=path,
                    hint=f"合法取值：{', '.join(own.values[:14])}",
                    url=own.url,
                )
            return

        # The manual documents a section's own parameter as that section's
        # ``SECTION_PARAMETERS`` entry.  This is what print keys use, and they
        # are *enumerations* (ON OFF SILENT LOW MEDIUM HIGH DEBUG): writing a
        # bare ``T`` makes CP2K abort with "invalid value for enumeration:T",
        # because the input parser maps enum values strictly.
        meta = self.db.keyword(path, "SECTION_PARAMETERS")
        if meta is None or not meta.valid_values:
            return
        if parameter.upper() in {v.upper() for v in meta.values}:
            return
        if _is_logical_word(parameter):
            self._add(
                "error", "boolean-section-parameter",
                f"&{section.name} {parameter}：这里的段参数是**枚举**，不是逻辑值",
                path=path,
                hint="写 ON / OFF（另外几级是 SILENT / LOW / MEDIUM / HIGH / "
                     f"DEBUG）；合法取值：{', '.join(meta.values)}",
                url=meta.url,
            )
            return
        self._add(
            "warning", "bad-section-parameter",
            f"&{section.name} {parameter} 不在手册记的段参数里",
            path=path,
            hint=f"手册记的取值：{', '.join(meta.values[:14])}",
            url=meta.url,
        )

    # ------------------------------------------------------------------ #
    # semantics
    # ------------------------------------------------------------------ #
    def _check_semantics(self, sections: Sequence[Section]) -> None:
        top = {s.name.upper(): s for s in sections}
        global_section = top.get("GLOBAL")
        run_type = "ENERGY"
        if global_section is not None:
            value = global_section.get("RUN_TYPE", "ENERGY")
            run_type = str(value).upper()
            if global_section.keyword("PROJECT_NAME") is None \
                    and global_section.keyword("PROJECT") is None:
                self._add("info", "no-project-name",
                          "&GLOBAL 里没有 PROJECT_NAME",
                          path="CP2K_INPUT/GLOBAL",
                          hint="输出文件和 restart 文件都按这个名字命名"
                               "（CP2K 默认用 cp2k）")
        else:
            self._add("warning", "no-global", "输入里没有 &GLOBAL 段",
                      path="CP2K_INPUT")

        force_evals = [s for s in sections if s.name.upper() == "FORCE_EVAL"]
        if not force_evals:
            self._add("warning", "no-force-eval", "输入里没有 &FORCE_EVAL 段",
                      path="CP2K_INPUT")
            motion = top.get("MOTION")
            self._check_motion_presence(run_type, motion)
            return
        # with MULTIPLE_FORCE_EVALS the interesting one is the Quickstep block
        force_eval = force_evals[0]
        for candidate in force_evals:
            if candidate.subsection("DFT") is not None:
                force_eval = candidate
                break
        dft = force_eval.subsection("DFT")
        subsys = force_eval.subsection("SUBSYS")
        motion = top.get("MOTION")

        method = str(force_eval.get("METHOD", "QUICKSTEP")).upper()
        qs_method = "GPW"
        if dft is not None:
            qs_section = dft.subsection("QS")
            if qs_section is not None:
                qs_method = str(qs_section.get("METHOD", "GPW")).upper()

        self._check_motion_presence(run_type, motion)
        if method in ("FIST", "MM") and dft is not None:
            self._add("error", "method-mismatch",
                      "METHOD FIST（经典力场）的 &FORCE_EVAL 里不能有 &DFT 段",
                      path="CP2K_INPUT/FORCE_EVAL")
        if method in ("QUICKSTEP", "QS") and dft is None:
            self._add("warning", "no-dft",
                      "METHOD QUICKSTEP 的 &FORCE_EVAL 里没有 &DFT 段",
                      path="CP2K_INPUT/FORCE_EVAL")

        needs_kind = is_ab_initio(self.db, qs_method) \
            and method not in NO_KIND_FORCE_EVAL

        self._files_verified = self._data_files_indexed(dft)
        # in a QM/MM run only the atoms listed in &QMMM &QM_KIND are quantum;
        # the rest are force-field atoms and need no &KIND
        required: set[str] | None = None
        qmmm = force_eval.subsection("QMMM")
        if qmmm is not None:
            names = {str(block.parameter).upper()
                     for block in qmmm.subsections("QM_KIND")
                     if isinstance(block.parameter, str)}
            if names:
                required = {canonical_element(name) or name for name in names}
        elements = self._check_subsys(subsys, needs_kind=needs_kind,
                                      required_elements=required,
                                      # on a QM/MM route the MM kinds legitimately
                                      # have neither a basis set nor a potential,
                                      # so a missing one is only a warning there
                                      strict_kinds=qmmm is None)
        if dft is not None:
            self._check_dft(dft, subsys, elements, motion, run_type, qs_method)
        if motion is not None:
            self._check_motion(motion, run_type, dft, subsys, method, force_eval)
        self._check_vibrational(top.get("VIBRATIONAL_ANALYSIS"), run_type)
        self._check_properties(force_eval, dft, run_type)
        self._check_restart(top, dft, run_type)
        self._check_scf_iterations(dft)
        self._check_print_intervals(dft, motion, run_type)
        if method in ("FIST", "MM") and dft is None:
            self._check_fist(force_eval, subsys, motion, run_type)

    # ------------------------------------------------------------------ FIST #
    #: ``PARMTYPE`` values that read their parameters from ``PARM_FILE_NAME``
    #: (``enum_c_vals`` of PARMTYPE in ``input_cp2k_mm.F``; OFF means the
    #: parameters are written in the input itself).
    MM_FILE_PARMTYPES = {"CHM", "G87", "G96", "AMBER"}
    #: ``CONNECTIVITY`` values that read a topology file.  MOL_SET takes one file
    #: per ``&MOLECULE`` instead, GENERATE/OFF/USER need none.
    MM_FILE_CONNECTIVITY = {"PSF", "UPSF", "AMBER", "G87", "G96"}
    #: ``COORDINATE`` value -> the file suffixes that go with it, so a mismatch
    #: (PDB file with ``COORDINATE CRD``) is caught instead of silently producing
    #: a system that fails to read its coordinates
    MM_COORD_SUFFIXES = {
        "PDB": (".pdb", ".ent"),
        "CRD": (".crd", ".inpcrd", ".rst", ".restart", ".restrt"),
        "XYZ": (".xyz",),
        "G96": (".g96",),
        "CIF": (".cif",),
        "XTL": (".xtl",),
    }

    def _check_fist(self, force_eval: Section, subsys: Section | None,
                    motion: Section | None, run_type: str) -> None:
        """Checks for ``METHOD FIST``, where the physics is the force field.

        The classic mistakes are a force field without its parameter file (CP2K
        then has no parameters at all), an AMBER/CHARMM run that inherits CP2K's
        ``EI_SCALE14 0.0`` default and silently loses every 1-4 electrostatic
        term, and SPME on a cell that is not periodic.
        """
        mm = force_eval.subsection("MM")
        if mm is None:
            self._add("info" if self._included() else "error", "fist-without-mm",
                      "METHOD FIST needs a &MM section: that is where the force "
                      "field lives"
                      + ("（输入里有 @INCLUDE/@FFTYPE：它可能就写在被包含的文件里，"
                         "这里只做静态检查）" if self._included() else ""),
                      path="CP2K_INPUT/FORCE_EVAL")
            return
        forcefield = mm.subsection("FORCEFIELD")
        if forcefield is None:
            self._add("info" if self._included() else "error", "fist-without-forcefield",
                      "&MM has no &FORCEFIELD section, so no force field is "
                      "defined"
                      + ("（输入里有 @INCLUDE/@FFTYPE：力场可能由它展开出来）"
                         if self._included() else ""),
                      path="CP2K_INPUT/FORCE_EVAL/MM")
            return
        parmtype = _enum_word(forcefield.get("PARMTYPE", "OFF"))
        parm_file = forcefield.get("PARM_FILE_NAME")
        if parmtype in self.MM_FILE_PARMTYPES and not parm_file:
            self._add("error", "mm-parmtype-without-file",
                      f"PARMTYPE {parmtype} 的力场要从文件里读，"
                      "但 PARM_FILE_NAME 没填 —— CP2K 直接中止"
                      "（force_fields_input.F：\"Force Field Parameter's filename "
                      "is empty! Please check your input file.\"）",
                      path="CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD",
                      hint="填「3. 经典力场」的「参数文件」（--mm-parm-file <file>）；"
                           "PARMTYPE OFF 才是「参数写在输入里」的那种写法")
        if parmtype in ("CHM", "AMBER") and forcefield.get("EI_SCALE14") is None:
            self._add("warning", "mm-ei-scale14-default",
                      f"PARMTYPE {parmtype} 没写 EI_SCALE14：CP2K 的默认值是 "
                      "0.0，也就是把 1-4 静电相互作用整个去掉 "
                      f"({'PARM94 及以后应为 0.8333，PARM91 为 0.5' if parmtype == 'AMBER' else 'CHARMM 不做 1-4 缩放，应为 1.0'})",
                      path="CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD",
                      hint="1-4 项是力场的一部分；只有你的参数集确实这么规定时才留 0.0")

        self._check_mm_topology(subsys, forcefield)
        self._check_mm_parameter_coverage(forcefield, subsys, parmtype)
        self._check_mm_electrostatics(mm, subsys, forcefield, parmtype)
        self._check_fist_motion(motion, run_type, force_eval, subsys)
        self._check_fist_leftovers(force_eval)

    def _check_mm_topology(self, subsys: Section | None, forcefield: Section) -> None:
        topology = subsys.subsection("TOPOLOGY") if subsys is not None else None
        if topology is None:
            self._add("info", "mm-no-topology",
                      "没有 &TOPOLOGY 段：CP2K 会按距离自己算连接性"
                      "（CONNECTIVITY GENERATE）",
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS")
            return
        connectivity = _enum_word(topology.get(
            "CONNECTIVITY", topology.get("CONN_FILE_FORMAT", "GENERATE"))).upper()
        conn_file = topology.get("CONN_FILE_NAME", topology.get("CONN_FILE"))
        coord_file = topology.get("COORD_FILE_NAME", topology.get("COORD_FILE"))
        if connectivity in self.MM_FILE_CONNECTIVITY and not conn_file:
            self._add("error", "mm-connectivity-without-file",
                      f"CONNECTIVITY {connectivity} 要从文件里读拓扑，"
                      "但 CONN_FILE_NAME 没填：CP2K 拿一个空文件名去开文件，"
                      "直接中止（cp_files.F：\"The specified OLD file <> cannot be "
                      "opened. It does not exist.\"）",
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY",
                      hint="填「拓扑文件」（命令行 --mm-conn-file <file>）")
        if connectivity == "MOL_SET":
            mol_set = topology.subsection("MOL_SET")
            molecules = mol_set.subsections("MOLECULE") if mol_set is not None else []
            if not molecules:
                self._add("error", "mm-mol-set-empty",
                          "CONNECTIVITY MOL_SET 需要一个 &MOL_SET，"
                          "每个组分一个 &MOLECULE",
                          path="CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY")
            for index, block in enumerate(molecules, start=1):
                if not block.get("CONN_FILE_NAME") and not block.get("CONN_FILE"):
                    self._add("error", "mm-molecule-without-file",
                              f"&MOLECULE {index} 没有 CONN_FILE_NAME",
                              path="CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY/MOL_SET")
                if block.get("NMOL") is None:
                    self._add("info", "mm-molecule-default-nmol",
                              f"&MOLECULE {index} 没写 NMOL：CP2K 按 1 份算",
                              path="CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY/MOL_SET")
        if connectivity in self.MM_FILE_CONNECTIVITY and not coord_file:
            # without a coordinate file and without &COORD CP2K has no positions
            has_coord = subsys is not None and subsys.subsection("COORD") is not None
            if not has_coord:
                self._add("error", "mm-connectivity-without-coords",
                          f"CONNECTIVITY {connectivity} 既没有 COORD_FILE_NAME，"
                          "也没有 &COORD 段，CP2K 拿不到原子位置"
                          "（topology.F：\"No atomic coordinates have been "
                          "found!\"）",
                          path="CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY",
                          hint="填「坐标文件」（AMBER 用 inpcrd/CRD），"
                               "或者载入一个结构让工具写成 &COORD")
        if connectivity in self.MM_FILE_CONNECTIVITY and coord_file:
            coord_format = _enum_word(topology.get(
                "COORD_FILE_FORMAT", topology.get("COORDINATE", "OFF"))).upper()
            name = str(coord_file).lower()
            expected = next((fmt for fmt, suffixes in self.MM_COORD_SUFFIXES.items()
                             if name.endswith(suffixes)), None)
            if expected and coord_format in self.MM_COORD_SUFFIXES \
                    and coord_format != expected:
                self._add("warning", "mm-coord-format-suspect",
                          f"COORDINATE {coord_format} 与文件后缀对不上："
                          f"{name.rsplit('/', 1)[-1]} 看起来是 {expected}",
                          path="CP2K_INPUT/FORCE_EVAL/SUBSYS/TOPOLOGY",
                          hint=f"CRD 读的是 AMBER 的 inpcrd/restart 定宽卡片格式；"
                               f"PDB 文件要写 COORDINATE PDB（这里应当用 {expected}）")

    def _check_mm_parameter_coverage(self, forcefield: Section,
                                     subsys: Section | None,
                                     parmtype: str) -> None:
        """With ``&MOL_SET``, ``PARM_FILE_NAME`` still has to cover every component.

        ``read_connectivity_amber`` reads each per-molecule topology with
        ``do_forcefield=.FALSE.`` (``topology_amber.F:306``): it supplies the
        connectivity and the charges, nothing else.  The parameters - bond and
        bend constants, torsions, LJ - come from ``&FORCEFIELD PARM_FILE_NAME``
        (``force_fields_ext.F:705``, by atom-type name), and
        ``force_fields_util.F:340-366`` aborts with "Missing critical ForceField
        parameters!" when a bond or bend has none.  So pointing
        ``PARM_FILE_NAME`` at one component's prmtop does not give the other
        component its parameters.

        Only AMBER is checked: for CHARMM the parameter file is a type *library*
        (``.pot``) shared by every topology, which is exactly what CP2K's own
        ``multi_psf.inp`` does.
        """
        if parmtype != "AMBER" or subsys is None:
            return
        topology = subsys.subsection("TOPOLOGY")
        connectivity = _enum_word(topology.get(
            "CONNECTIVITY", topology.get("CONN_FILE_FORMAT", "GENERATE"))).upper() \
            if topology is not None else ""
        if connectivity != "MOL_SET":
            return
        mol_set = topology.subsection("MOL_SET")
        blocks = mol_set.subsections("MOLECULE") if mol_set is not None else []
        shells = {str(block.get("CONN_FILE_NAME", block.get("CONN_FILE")) or "").strip()
                  for block in blocks}
        shells.discard("")
        parm = str(forcefield.get("PARM_FILE_NAME") or "").strip()
        if not parm or len(shells) < 2:
            # nothing to say when the official one-molecule pattern is used
            # (PARM_FILE_NAME == the single per-molecule topology)
            return
        where = ("那就是其中一个组分自己的拓扑"
                 if parm in shells else
                 f"它和任何一个组分拓扑都不是同一个文件")
        self._add(
            "warning" if parm in shells else "info",
            "mm-parm-file-covers-all-components",
            f"PARMTYPE AMBER + CONNECTIVITY MOL_SET：{len(shells)} 个组分各有自己的拓扑，"
            f"而 PARM_FILE_NAME 只有 {Path(parm).name}（{where}）。"
            "AMBER 的**参数只从 PARM_FILE_NAME 来**——每个单体的拓扑是以 "
            "do_forcefield=FALSE 读的，只提供连接性和电荷——而 CP2K 是按原子类型名把参数"
            "套到原子上的，所以这个文件必须覆盖所有组分用到的键/角，否则 "
            "force_fields_util.F 会直接 abort：Missing critical ForceField parameters!",
            path="CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD",
            hint="最省事的是生成一个覆盖整个体系的 prmtop（tleap 里 combine 后 saveAmberParm），"
                 "那时连 &MOL_SET 都不需要；如果两个组分的参数确实同源且项都被覆盖，"
                 "先跑一次 RUN_TYPE ENERGY 确认没有 missing parameters 再开 MD")

    def _check_mm_electrostatics(self, mm: Section, subsys: Section | None,
                                 forcefield: Section, parmtype: str) -> None:
        poisson = mm.subsection("POISSON")
        ewald = poisson.subsection("EWALD") if poisson is not None else None
        kind = str(ewald.get("EWALD_TYPE", "EWALD")).upper() if ewald is not None else None
        if kind in ("EWALD", "SPME") and ewald is not None \
                and ewald.get("GMAX") is None:
            # CP2K has no default for GMAX and demands the value, so the run
            # aborts while setting up FIST:
            #   Value requested, but no value set getting value from keyword
            #   GMAX of section EWALD        input/input_section_types.F:1132
            self._add("error", "mm-ewald-gmax-required",
                      f"EWALD_TYPE {kind} 必须给 GMAX：CP2K 对 GMAX 没有默认值，"
                      "缺了会在 fist_init 阶段直接中止"
                      "（`Value requested, but no value set getting value from "
                      "keyword GMAX of section EWALD`）",
                      path="CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD",
                      hint="写一个 GMAX（例如 25–64，随盒子大小），"
                           "或者用 EWALD_TYPE PME（它用的是 NS_MAX，有默认值）")
        if kind in ("EWALD", "SPME") and ewald is not None \
                and ewald.get("GMAX") is not None:
            gmax = ewald.get("GMAX")
            biggest = max([_as_int(v, 0) for v in (gmax if isinstance(gmax, list)
                                                   else [gmax])] or [0])
            if biggest > 128:
                alpha = _as_float(ewald.get("ALPHA"), 0.35)
                self._add("info", "mm-ewald-gmax-large",
                          f"GMAX {biggest} 的倒空间网格很大（SPME 的开销随它三次方涨）。"
                          f"当前 ALPHA 是 {alpha:g} Å⁻¹：盒子很大时通常把 ALPHA 调小"
                          "（0.2 左右），GMAX 和实空间截断会一起降下来",
                          path="CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD")
        if kind in ("SPME", "PME"):
            cell = subsys.subsection("CELL") if subsys is not None else None
            periodic = str(cell.get("PERIODIC", "XYZ")).upper() if cell is not None else "XYZ"
            if periodic in ("NONE", "OFF") or cell is None:
                self._add("warning", "mm-ewald-needs-periodic",
                          f"EWALD_TYPE {kind} 要在周期网格上做倒空间求和，"
                          "但这个晶胞不是周期的",
                          path="CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD",
                          hint="孤立体系用 EWALD_TYPE NONE；要用 Ewald 就得让晶胞周期")
        elif kind == "NONE":
            charges = [c.get("CHARGE") for c in forcefield.subsections("CHARGE")]
            non_zero = any(_as_float(value, 0.0) for value in charges)
            if non_zero:
                self._add("warning", "mm-ewald-none-with-charges",
                          "EWALD_TYPE NONE 只算实空间的库仑和，"
                          "而这个力场里的电荷不是零",
                          path="CP2K_INPUT/FORCE_EVAL/MM/POISSON/EWALD",
                          hint="中性孤立分子这样写是对的，周期溶液里就不对了"
                               "（那种体系用 SPME）")
        if parmtype == "OFF":
            nonbonded = forcefield.subsection("NONBONDED")
            blocks = nonbonded.subsections("LENNARD-JONES") if nonbonded is not None else []
            for index, block in enumerate(blocks, start=1):
                if block.get("EPSILON") is None or block.get("SIGMA") is None:
                    self._add("warning", "mm-lj-without-parameters",
                              f"&LENNARD-JONES {index} 没写 "
                              + ("EPSILON" if block.get("EPSILON") is None else "SIGMA")
                              + "，这一对原子之间就没有相互作用",
                              path="CP2K_INPUT/FORCE_EVAL/MM/FORCEFIELD/NONBONDED",
                              hint="EPSILON 的单位是 K，SIGMA 是 Å")
                    break

    def _check_fist_motion(self, motion: Section | None, run_type: str,
                           force_eval: Section, subsys: Section | None) -> None:
        md = motion.subsection("MD") if motion is not None else None
        if md is None:
            return
        ensemble = str(md.get("ENSEMBLE", "NVE")).upper()
        stress = str(force_eval.get("STRESS_TENSOR", "NONE")).upper()
        if ensemble in NPT_ENSEMBLES:
            if stress == "NONE":
                self._add("warning", "npt-without-stress-tensor",
                          f"ENSEMBLE {ensemble} 会改晶胞，但 STRESS_TENSOR 是 "
                          "NONE：压浴拿不到压强",
                          path="CP2K_INPUT/FORCE_EVAL",
                          hint="METHOD FIST 默认没有解析应力，要写 "
                               "STRESS_TENSOR ANALYTICAL")
            cell = subsys.subsection("CELL") if subsys is not None else None
            periodic = str(cell.get("PERIODIC", "XYZ")).upper() if cell is not None else "XYZ"
            if periodic in ("NONE", "OFF"):
                self._add("warning", "npt-without-periodicity",
                          f"ENSEMBLE {ensemble} 配 PERIODIC {periodic}："
                          "压浴要有一个周期晶胞才能压缩",
                          path="CP2K_INPUT/FORCE_EVAL/SUBSYS/CELL",
                          hint="去掉真空层、给真实的盒子，或者改跑 NVT/NVE")
        if ensemble == "NVE" and md.subsection("THERMOSTAT") is not None:
            self._add("info", "nve-with-thermostat",
                      "ENSEMBLE NVE 不读 &THERMOSTAT（写了也不起作用）",
                      path="CP2K_INPUT/MOTION/MD")
        timestep = _as_float(md.get("TIMESTEP"), 0.5)
        if timestep > 2.0:
            # a constraint block (&SHAKE / &G3X3 ...) is what makes 2 fs+ safe;
            # this only *adds* the reason to the generic timestep warning
            constraint = motion.subsection("CONSTRAINT")
            if constraint is not None and constraint.sections:
                self._add("info", "mm-large-timestep-constrained",
                          f"TIMESTEP {timestep:g} fs 配 &CONSTRAINT 段："
                          "冻住键长正是长步长安全的原因",
                          path="CP2K_INPUT/MOTION/MD")

    def _check_fist_leftovers(self, force_eval: Section) -> None:
        """DFT-only settings that would be silently ignored by FIST."""
        for name in ("DFT", "PROPERTIES", "EFIELD"):
            block = force_eval.subsection(name)
            if block is not None and section_enabled(block):
                self._add("warning", f"fist-ignores-{name.lower()}",
                          f"&{name} 是 Quickstep 的段，METHOD FIST 下不读",
                          path=f"CP2K_INPUT/FORCE_EVAL/{name}")
        subsys = force_eval.subsection("SUBSYS")
        kinds = subsys.subsections("KIND") if subsys is not None else []
        for kind in kinds:
            for keyword in ("BASIS_SET", "POTENTIAL", "BASIS_SET_FILE_NAME"):
                if kind.get(keyword) is not None:
                    self._add("info", "fist-kind-with-basis",
                              f"&KIND {kind.parameter} 里写了 {keyword}，"
                              "只有 Quickstep 会读它",
                              path="CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND")

    def _check_scf_iterations(self, dft: Section | None) -> None:
        """A fixed number of inner steps only works with the failure flag on.

        CP2K aborts when the SCF has not converged, unless
        IGNORE_CONVERGENCE_FAILURE is set - so the "just do one iteration and
        take the energy" pattern needs both settings.
        """
        scf = dft.subsection("SCF") if dft is not None else None
        if scf is None or not section_enabled(scf):
            return
        max_scf = _as_int(scf.get("MAX_SCF", 50), 50)
        if not 0 < max_scf <= 4:
            return
        ignore = str(scf.get("IGNORE_CONVERGENCE_FAILURE", "F")).upper()
        if ignore.startswith("T"):
            return
        self._add("warning", "scf-max-too-small",
                  f"MAX_SCF 只有 {max_scf} 步，又没开 "
                  "IGNORE_CONVERGENCE_FAILURE：SCF 没收敛时 CP2K 会中止这次运行",
                  path="CP2K_INPUT/FORCE_EVAL/DFT/SCF",
                  hint="如果只要固定步数（比如扫截断能），"
                       "就把 IGNORE_CONVERGENCE_FAILURE T 一起写上")

    def _check_restart(self, top: Section, dft: Section | None,
                       run_type: str) -> None:
        """Two restarts that people mix up: the wavefunction and the whole run.

        Neither is an error - CP2K is happy - but both hide a file dependency
        that is easy to miss when the run is submitted from a fresh directory.
        """
        scf = dft.subsection("SCF") if dft is not None else None
        if scf is not None and section_enabled(scf):
            guess = str(scf.get("SCF_GUESS", "ATOMIC")).upper()
            has_file = dft is not None and bool(dft.get("WFN_RESTART_FILE_NAME"))
            print_block = scf.subsection("PRINT")
            prints = print_block is not None \
                and print_block.subsection("RESTART") is not None
            if guess in ("RESTART", "HISTORY_RESTART") and not has_file and not prints:
                # only worth saying when this input will never write the file:
                # CP2K itself falls back to an atomic guess when it is missing
                self._add("info", "restart-without-file",
                          f"SCF_GUESS {guess}，但既没有 WFN_RESTART_FILE_NAME，"
                          "也没有 &SCF/&PRINT/&RESTART：CP2K 会在工作目录里找 "
                          "<PROJECT>-RESTART.wfn，找不到就退回原子初猜",
                          path="CP2K_INPUT/FORCE_EVAL/DFT/SCF",
                          hint="加 &SCF/&PRINT/&RESTART 让这次运行把文件写出来给下一次用，"
                               "或者把 WFN_RESTART_FILE_NAME 指向已有的文件")

        ext = top.get("EXT_RESTART")
        if ext is None or not section_enabled(ext):
            return
        name = ext.get("RESTART_FILE_NAME")
        if not name:
            self._add("info", "ext-restart-no-file",
                      "&EXT_RESTART 没写 RESTART_FILE_NAME："
                      "CP2K 会去找 <PROJECT>-1.restart",
                      path="CP2K_INPUT/EXT_RESTART")
        if run_type in ("ENERGY", "ENERGY_FORCE", "VIBRATIONAL_ANALYSIS"):
            self._add("warning", "ext-restart-ignored",
                      f"&EXT_RESTART 对 RUN_TYPE {run_type} 没有任何作用："
                      "它续的是带迭代步的计算（MD、GEO_OPT、CELL_OPT）",
                      path="CP2K_INPUT/EXT_RESTART")
        if bool(ext.get("RESTART_DEFAULT", True)):
            self._add("info", "restart-default-overrides",
                      "RESTART_DEFAULT T：坐标、速度、晶胞和步数计数器都以 restart "
                      "文件为准，不用这份输入里的",
                      path="CP2K_INPUT/EXT_RESTART",
                      hint="想保留这里写的坐标就关掉它（RESTART_DEFAULT F）")
        if _bool_word(ext.get("RESTART_TEMPERATURE_ANNEALING", False)):
            # input_cp2k_check.F:646 copies MOTION%MD%TEMPERATURE over from the
            # restart file, then warns unless TEMPERATURE_ANNEALING is explicitly
            # set to something other than 1
            force_eval = top.get("FORCE_EVAL")
            motion = top.get("MOTION")
            md = motion.subsection("MD") if motion is not None else None
            factor = _as_float(md.get("TEMPERATURE_ANNEALING", 1.0), 1.0) \
                if md is not None else 1.0
            if factor in (1.0, 0.0):
                self._add("warning", "restart-annealing-without-ramp",
                          "RESTART_TEMPERATURE_ANNEALING T 但 &MD "
                          "TEMPERATURE_ANNEALING 没给（或者等于 1）："
                          "CP2K 会把输入里的 TEMPERATURE 覆盖成 restart 里存的那个值，"
                          "但温度并不会继续斜坡（它自己也会这么警告）",
                          path="CP2K_INPUT/EXT_RESTART",
                          hint="接力升温/降温要同时给 `&MD TEMPERATURE_ANNEALING <因子>`")
        self._check_ext_restart_redundant(ext, top)

    #: What ``&EXT_RESTART`` can bring that an *inline* input cannot.
    RESTART_TAKE_KEYS = ("RESTART_POS", "RESTART_VEL", "RESTART_CELL",
                         "RESTART_COUNTERS", "RESTART_THERMOSTAT",
                         "RESTART_BAROSTAT", "RESTART_BAROSTAT_THERMOSTAT",
                         "RESTART_CONSTRAINT", "RESTART_AVERAGES",
                         "RESTART_RANDOMG", "RESTART_METADYNAMICS",
                         "RESTART_SHELL_POS", "RESTART_SHELL_VELOCITY",
                         "RESTART_CORE_POS", "RESTART_CORE_VELOCITY",
                         "RESTART_TEMPERATURE_ANNEALING", "RESTART_XC",
                         "RESTART_MP2", "RESTART_LS_SCF")

    def _check_ext_restart_redundant(self, ext: Section, top: dict) -> None:
        """An ``&EXT_RESTART`` that only re-reads what the input already has.

        When the input carries the atoms itself (``&COORD``, e.g. because it was
        rebuilt from a restart file), taking ``POS`` - and ``CELL`` when the cell
        is written too - repeats what is already there.  The file is still worth
        naming for the things an inline input *cannot* carry: velocities, step
        counters, thermostat/barostat state.
        """
        explicit = [key for key in self.RESTART_TAKE_KEYS
                    if _bool_word(ext.get(key, False))]
        if not explicit:
            return                      # RESTART_DEFAULT's behaviour, not a list
        useful = [key for key in explicit
                  if key not in ("RESTART_POS", "RESTART_CELL")]
        if useful:
            return
        force_eval = top.get("FORCE_EVAL")
        subsys = force_eval.subsection("SUBSYS") if force_eval is not None else None
        if subsys is None or subsys.subsection("COORD") is None:
            return                      # without &COORD the file is the only source
        self._add("info", "ext-restart-redundant",
                  "这次输入自己就带着坐标（`&COORD`），"
                  "而 `&EXT_RESTART` 只取了 POS/CELL——等于把同一份东西再读一遍，"
                  "这一段可以删掉",
                  path="CP2K_INPUT/EXT_RESTART",
                  hint="`&EXT_RESTART` 真正能补的是内联输入给不了的东西："
                       "**速度**（MD 接力，`RESTART_VEL`）、**步数计数器**"
                       "（`RESTART_COUNTERS`）、**恒温器/压浴状态**。"
                       "只要这三个都不需要，就把 RESTART_FILE_NAME 留空；"
                       "需要哪一个就显式取它")

    def _check_motion_presence(self, run_type: str, motion: Section | None) -> None:
        required = {"GEO_OPT": "GEO_OPT", "CELL_OPT": "CELL_OPT",
                    "MD": "MD", "BAND": "BAND", "MOLECULAR_DYNAMICS": "MD"}
        wanted = required.get(run_type)
        if wanted and (motion is None or not any(
                section_enabled(s) for s in motion.subsections(wanted))):
            self._add("info", "motion-defaults",
                      f"RUN_TYPE {run_type} 但没有显式的 &MOTION/&{wanted} 段",
                      path="CP2K_INPUT/MOTION",
                      hint=f"CP2K 会用 &{wanted} 的默认设置；"
                           "想要自己的优化器、阈值或轨迹输出就把这一段写出来")
        if run_type in ("ENERGY", "ENERGY_FORCE") and motion is not None:
            for name in ("GEO_OPT", "CELL_OPT", "MD", "BAND"):
                if motion.subsection(name) is not None:
                    self._add("warning", "motion-ignored",
                              f"RUN_TYPE {run_type} 不读 &MOTION/&{name}"
                              "（写了也不起作用）",
                              path="CP2K_INPUT/MOTION",
                              hint=f"要跑它就改 RUN_TYPE {name}")
        # &CONSTRAINT only means something when atoms actually move
        if motion is not None and motion.subsection("CONSTRAINT") is not None \
                and run_type not in ("GEO_OPT", "CELL_OPT", "MD", "BAND",
                                     "MOLECULAR_DYNAMICS"):
            self._add("warning", "constraint-ignored",
                      f"RUN_TYPE {run_type} 不会移动原子，"
                      "&MOTION/&CONSTRAINT（固定原子）没有任何作用",
                      path="CP2K_INPUT/MOTION/CONSTRAINT",
                      hint="要固定原子就得跑 GEO_OPT / CELL_OPT / MD")

    def _check_subsys(self, subsys: Section | None, needs_kind: bool = True,
                      required_elements: set[str] | None = None,
                      strict_kinds: bool = True) -> list[str]:
        if subsys is None:
            self._add("warning", "no-subsys",
                      "&FORCE_EVAL 里没有 &SUBSYS 段",
                      path="CP2K_INPUT/FORCE_EVAL")
            return []
        cell = subsys.subsection("CELL")
        coord = subsys.subsection("COORD")
        topology = subsys.subsection("TOPOLOGY")

        elements: list[str] = []
        labels: set[str] = set()
        coord_atoms = 0
        if coord is not None:
            for keyword in coord.keywords:
                # A coordinate row is "label x y z" - and the three numbers may be
                # written as CP2K expressions (``Si 1/3 2/3 0.063``, ``0*1/3``),
                # which is what four of the official FIST/QS inputs do.
                if _coordinate_row(keyword):
                    coord_atoms += 1
                if len(keyword.values) >= 3 and all(
                        isinstance(v, (int, float)) and not isinstance(v, bool)
                        for v in keyword.values[:3]):
                    labels.add(keyword.name.upper())
                    # &COORD may also list MM atom types of a QM/MM setup
                    # (CTL3, OW, ...), which are not elements and need no &KIND
                    symbol = canonical_element(keyword.name)
                    if symbol is not None and symbol not in elements:
                        elements.append(symbol)
        # An *empty* ``&COORD`` is not coordinates: it reads as zero atoms and
        # CP2K stops right there, so it counts as "no coordinates" below.
        has_coord = coord_atoms > 0

        periodic = "XYZ"
        if cell is not None:
            raw = cell.get("PERIODIC", "XYZ")
            periodic = str(raw).upper()
            has_geometry = any(cell.keyword(k) for k in ("ABC", "A", "B", "C")) \
                or cell.subsection("CELL_REF") is not None
            if not has_geometry and periodic != "NONE":
                # CP2K's default cell is a 1 A cube, so this is only a real
                # problem for a genuinely periodic system
                self._add("warning", "empty-cell",
                          "&CELL 里没有任何几何信息（ABC、A/B/C 或 &CELL_REF）",
                          path="CP2K_INPUT/FORCE_EVAL/SUBSYS/CELL",
                          hint="CP2K 会退回它默认的 1 Å 立方体")
            if periodic not in ("NONE", "0"):
                # ``COORD_FILE`` is the manual's alias of ``COORD_FILE_NAME`` and
                # CP2K's own FIST regtests write the short one (46 inputs in the
                # corpus) - missing it here would report those as coordinate-less.
                topo = _coord_file_of(topology)
                if not has_coord and topo is None:
                    # **No** coordinates at all is an error: CP2K aborts right there
                    # ("No atomic coordinates have been found!" - topology.F, after
                    # coordinate_control has tried every source).  Only an
                    # @INCLUDE/@FFTYPE can still supply them behind our back, and
                    # then it stays a note.
                    self._add("info" if self._included() else "error", "no-coordinates",
                              "&SUBSYS 里既没有 &COORD（或它是空的）也没有 "
                              "&TOPOLOGY/COORD_FILE_NAME：原子从哪来？"
                              + ("（输入里有 @INCLUDE/@FFTYPE：坐标可能写在被包含的文件里，"
                                 "这里只做静态检查）" if self._included() else
                                 "CP2K 会直接中止："
                                 "\"No atomic coordinates have been found!\""
                                 "（topology.F 的 coordinate_control 试完所有来源之后）"),
                              path="CP2K_INPUT/FORCE_EVAL/SUBSYS")
        else:
            if not has_coord:
                topo = _coord_file_of(topology)
                if topo is None:
                    self._add("info" if self._included() else "error", "no-cell-no-coord",
                              "&SUBSYS 既没有 &CELL，也没有 &COORD 或 "
                              "COORD_FILE_NAME：体系完全没有定义"
                              + ("（输入里有 @INCLUDE/@FFTYPE，可能写在被包含的文件里）"
                                 if self._included() else ""),
                              path="CP2K_INPUT/FORCE_EVAL/SUBSYS")

        if periodic != "NONE" and cell is None:
            self._add("error", "periodic-without-cell",
                      f"PERIODIC {periodic} 需要 &CELL：这份输入里没有",
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS",
                      hint="CP2K 只在 CELL_FILE_NAME/ABC/A,B,C 都没有时才去坐标文件里"
                           "找盒子，而那条路只认 CIF/CP2K/PDB/EXTXYZ 四种格式"
                           "（cell_methods.F 的 case 4）——CRD/inpcrd 这类会直接中止。"
                           "填「4. 体系」的「晶胞覆盖」（--cell a,b,c）")

        if has_coord and _coord_file_of(topology) is not None:
            # **Two** sets of coordinates: legal (CP2K reads the file and then lets
            # &COORD overwrite it) but almost never what was meant -> warning
            self._add("warning", "coord-and-file",
                      "同时给了 &COORD 和 &TOPOLOGY COORD_FILE_NAME（两套坐标）",
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS",
                      hint="CP2K 先读坐标文件，再用 &COORD **覆盖**它"
                           "（topology.F 调 read_atoms_input(overwrite=.TRUE.)，"
                           "日志里会打 \"Overwriting coordinates. Active "
                           "coordinates read from &COORD section.\"）——所以真正生效的"
                           "是 &COORD，不是那个文件。两者都留会让人以为读的是文件："
                           "要么删掉 &COORD（用文件），要么删掉 COORD_FILE_NAME")

        # KIND coverage
        kinds: dict[str, Section] = {}
        for kind_section in subsys.subsections("KIND"):
            name = kind_section.parameter
            if isinstance(name, str):
                key = name.upper()
                if key in kinds:
                    self._add("error", "duplicate-kind",
                              f"&KIND {name} 定义了两次",
                              path="CP2K_INPUT/FORCE_EVAL/SUBSYS")
                kinds[key] = kind_section
        if needs_kind:
            defaults_everything = any(
                isinstance(s.parameter, str) and s.parameter.upper() == "DEFAULT"
                for s in subsys.subsections("KIND"))
            if not defaults_everything:
                targets = elements if required_elements is None else \
                    [e for e in elements if e.upper() in required_elements]
                for element in targets:
                    if element.upper() not in kinds and not _kind_by_element(kinds, element):
                        self._add("error", "missing-kind",
                                  f"元素 {element} 出现在 &COORD 里，"
                                  "却没有对应的 &KIND 段",
                                  path="CP2K_INPUT/FORCE_EVAL/SUBSYS")

        for name, kind_section in kinds.items():
            self._check_kind(kind_section, name,
                             in_use=self._kind_in_use(kind_section, name, elements,
                                                      labels, coord),
                             quantum=needs_kind and strict_kinds)
        return elements

    def _kind_in_use(self, kind_section: Section, name: str, elements: list[str],
                     labels: set[str], coord: Section | None) -> bool:
        """Whether a &KIND block is actually referenced.

        CP2K never looks at an unused KIND block, so its basis set and potential
        must not be validated (:file:`H2_MP2_debug.inp` defines an unused
        ``&KIND F`` with a potential that does not exist in the data directory).
        """
        if coord is None:
            return True          # coordinates come from a file: cannot tell
        declared = canonical_element(str(kind_section.get("ELEMENT", name)))
        if name.upper() in labels:
            return True
        if declared is not None and declared in elements:
            return True
        return False

    def _check_kind(self, kind_section: Section, name: str,
                    in_use: bool = True, quantum: bool = True) -> None:
        raw_element = kind_section.get("ELEMENT", name)
        element = canonical_element(str(raw_element))
        basis = kind_section.get("BASIS_SET")
        potential = kind_section.get("POTENTIAL")
        if not in_use:
            self._add("info", "unused-kind",
                      f"&KIND {name} 定义了但没有任何原子用它",
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS",
                      hint="CP2K 根本不看它，所以它的基组和赝势没有被检查")
            return
        # A &KIND's label *is* the element CP2K uses; an explicit ELEMENT
        # overrides it silently (topology_util.F), which is one typo away from
        # turning every H-labelled atom into oxygen.
        label = name.strip()
        if quantum and element is not None and label in ELEMENTS and label != element:
            self._add("error", "kind-element-mismatch",
                      f"&KIND {label} 里写了 ELEMENT {raw_element}：CP2K 会把标着 "
                      f"{label} 的原子按 {element} 处理（topology_util.F）",
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND",
                      hint=f"要么删掉 ELEMENT（标签 {label} 本身就是元素），"
                           f"要么把标签改成 {element}")
        # These two need no data directory, so they come before the "no index"
        # return: a &KIND without POTENTIAL used to be reported as a warning - and
        # not reported at all when no data dir was configured - while CP2K aborts
        # ("No potential type was defined for the atomic kind").  A GHOST kind is
        # exempt: it has no electrons, so it needs neither.
        ghost = _is_true(kind_section.get("GHOST"))
        if ghost:
            self._add("info", "ghost-kind",
                      f"&KIND {name} 是幽灵原子（GHOST T）：没有电子，"
                      "基组/赝势按 CP2K 的规则可以不写",
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND")
        # Reported as a warning even though CP2K aborts for an in-use non-ghost
        # kind: CP2K's own regression suite has plenty of inputs that omit these
        # (preprocessor branches, GAPW soft/hard basis sets), so an error here
        # would be a false alarm on files that demonstrably run.
        if basis is None and not ghost:
            self._add("warning", "kind-without-basis",
                      f"&KIND {name} 没有 BASIS_SET"
                      + ("：CP2K 会中止（`No basis set type was defined for the "
                         "atomic kind`）" if quantum else ""),
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND")
        if potential is None and not ghost:
            self._add("warning", "kind-without-potential",
                      f"&KIND {name} 没有 POTENTIAL"
                      + ("：CP2K 会中止（`No potential type was defined for the "
                         "atomic kind`）" if quantum else ""),
                      path="CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND")
        if basis is None:
            return
        basis_name = _basis_name_of(basis)
        potential_name = _basis_name_of(potential) if potential is not None else None

        if self.index is None or self.index.directory is None:
            return
        if not self._files_verified:
            if not self._unverified_noted:
                self._unverified_noted = True
                self._add(
                    "info", "basis-not-verified",
                    "没有核对基组和赝势：这份输入会去扫描的 CP2K data 目录之外"
                    "读数据文件",
                    path="CP2K_INPUT/FORCE_EVAL/DFT",
                    hint="用 --data-dir <cp2k>/data 指定数据目录，"
                         "或者确认那些额外的数据文件在运行时能找到",
                )
            return
        if element is None:
            # a KIND named after something that is not an element (a molecule
            # name, "DEFAULT", ...) cannot be looked up
            return
        if basis_name.upper() in SPECIAL_KIND_VALUES \
                or (potential_name and potential_name.upper() in SPECIAL_KIND_VALUES):
            # BASIS_SET NONE, POTENTIAL UPF, ... are switch values, not names
            return
        if has_preprocessor(basis_name) or (potential_name
                                            and has_preprocessor(potential_name)):
            return

        if not self.index.has_entry("basis", basis_name, element):
            available = self.index.names_for("basis", element)
            self._add(
                "error", "basis-unavailable",
                f"基组 {basis_name!r} 对 {element} 不存在"
                f"（在 {self.index.directory} 里找不到）",
                path="CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND",
                hint=(f"{element} 可用的基组：{', '.join(available[:12])}"
                      if available else f"这个数据目录里根本没有 {element} 的基组"),
            )
        if potential_name is None:
            return
        if not self.index.has_entry("potential", potential_name, element):
            available = self.index.names_for("potential", element)
            self._add(
                "error", "potential-unavailable",
                f"赝势 {potential_name!r} 对 {element} 不存在",
                path="CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND",
                hint=(f"{element} 可用的赝势：{', '.join(available[:12])}"
                      if available else f"这个数据目录里根本没有 {element} 的赝势"),
            )
            return
        basis_valence = self.index.valence("basis", basis_name, element)
        potential_valence = self.index.valence("potential", potential_name, element)
        if basis_valence is not None and potential_valence is not None \
                and basis_valence != potential_valence:
            self._add(
                "error", "valence-mismatch",
                f"{element}：基组 {basis_name} 是 q{basis_valence}，"
                f"而赝势 {potential_name} 是 q{potential_valence}",
                path="CP2K_INPUT/FORCE_EVAL/SUBSYS/KIND",
                hint="CP2K 会中止（`Mismatch in the number of electrons`）："
                     "基组和赝势要选 -qN 后缀相同的",
            )

    def _count_electrons(self, subsys: Section) -> int | None:
        """Electrons in the system, using pseudopotential valences when known.

        Counting atomic numbers would be wrong for any pseudopotential
        calculation, which is the norm with Quickstep.
        """
        coord = subsys.subsection("COORD")
        if coord is None:
            return None
        kinds: dict[str, tuple[str, str | None]] = {}
        for kind_section in subsys.subsections("KIND"):
            name = kind_section.parameter
            if not isinstance(name, str):
                continue
            declared = kind_section.get("ELEMENT", name)
            element = canonical_element(str(declared)) or str(declared)
            potential = kind_section.get("POTENTIAL")
            if isinstance(potential, list):
                potential = potential[0] if potential else None
            kinds[name.upper()] = (element, str(potential) if potential else None)

        total = 0
        for keyword in coord.keywords:
            if len(keyword.values) < 3:
                continue
            key = keyword.name.upper()
            element, potential = kinds.get(
                key, (canonical_element(keyword.name) or keyword.name, None))
            valence = None
            if potential and self.index is not None and self.index.directory:
                valence = self.index.valence("potential", potential, element)
                if valence is None and "ALLELECTRON" in potential.upper():
                    valence = atomic_number(element)
            if valence is None:
                valence = atomic_number(element)
            if valence <= 0:
                return None
            total += valence
        return total or None

    def _check_dft(self, dft: Section, subsys: Section | None,
                   elements: list[str], motion: Section | None,
                   run_type: str, qs_method: str = "GPW") -> None:
        basis_files = dft.get("BASIS_SET_FILE_NAME")
        potential_file = dft.get("POTENTIAL_FILE_NAME")
        if basis_files is None:
            self._add("warning", "no-basis-file",
                      "&DFT 里没有 BASIS_SET_FILE_NAME（基组库文件）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT",
                      hint="CP2K 的默认值是内置的 `BASIS_SET`：它在数据目录里找这个"
                           "文件（没有就用 CP2K 自己编译进去的那份）。集群上数据目录和"
                           "本地不一致时，把这一行显式写上更稳")
        if potential_file is None:
            self._add("warning", "no-potential-file",
                      "&DFT 里没有 POTENTIAL_FILE_NAME（赝势库文件）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT",
                      hint="CP2K 的默认值是内置的 `POTENTIAL`：它在数据目录里找这个"
                           "文件。集群上数据目录和本地不一致时，把这一行显式写上更稳")

        # charge / multiplicity parity - only meaningful when the spin is pinned
        charge = _as_int(dft.get("CHARGE", 0), 0)
        multiplicity_value = dft.get("MULTIPLICITY")
        multiplicity = _as_int(multiplicity_value, 1)
        uks_flag = dft.get("UKS")
        if uks_flag is None:
            for factor in (dft.subsection("QS"),):
                if factor is not None and factor.get("LSD") is not None:
                    uks_flag = factor.get("LSD")
        if multiplicity > 1 and not uks_flag:
            self._add("info", "multiplicity-without-uks",
                      f"MULTIPLICITY {multiplicity} 但没开 UKS",
                      path="CP2K_INPUT/FORCE_EVAL/DFT",
                      hint="较新的 CP2K 见到 MULTIPLICITY 会自己转成自旋极化；"
                           "老输入都显式写 UKS T")
        if uks_flag and multiplicity == 1 and multiplicity_value is not None:
            self._add("info", "uks-singlet",
                      "UKS 开着而 MULTIPLICITY 是 1（开壳层单重态）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT")
        if multiplicity_value is not None and elements and subsys is not None:
            if not has_preprocessor(multiplicity_value) and not has_preprocessor(charge):
                electrons = self._count_electrons(subsys)
                if electrons:
                    net = electrons - charge
                    if (net - (multiplicity - 1)) % 2 != 0:
                        self._add(
                            "error", "charge-multiplicity-parity",
                            f"{net} 个电子配 multiplicity {multiplicity} 是不可能的"
                            "（电子数的奇偶性和自旋多重度对不上）",
                            path="CP2K_INPUT/FORCE_EVAL/DFT",
                            hint=f"体系有 {electrons} 个电子，CHARGE {charge} → 净 {net} 个；"
                                 "MULTIPLICITY 的奇偶性必须和它一致"
                                 "（CP2K 会中止：`Mismatch in the number of electrons`）",
                        )

        mgrid = dft.subsection("MGRID")
        cutoff = None
        rel_cutoff = None
        if mgrid is not None:
            cutoff = _as_float(mgrid.get("CUTOFF"), None)
            rel_cutoff = _as_float(mgrid.get("REL_CUTOFF"), 40.0)
            if cutoff is not None and rel_cutoff is not None and rel_cutoff > cutoff:
                self._add(
                    "warning", "rel-cutoff-above-cutoff",
                    f"REL_CUTOFF {rel_cutoff:g} 比 CUTOFF {cutoff:g} 还大",
                    path="CP2K_INPUT/FORCE_EVAL/DFT/MGRID",
                    hint="那样最细的一层网格反而要去装一个单位高斯，"
                         "通常不是本意（一般 REL_CUTOFF 用 60 Ry 左右）",
                )
            if cutoff is not None and cutoff <= 0:
                # CP2K asserts cutoff > 0 (pw_grid_info.F) - CPASSERT always aborts
                self._add("error", "cutoff-not-positive",
                          f"CUTOFF {cutoff:g} 不是正数：CP2K 建网格时 CPASSERT 失败并中止",
                          path="CP2K_INPUT/FORCE_EVAL/DFT/MGRID",
                          hint="CUTOFF 是平面波截断能，典型值 400–800 Ry")
            if cutoff is not None and cutoff < 200:
                self._add("warning", "cutoff-low",
                          f"CUTOFF {cutoff:g} Ry 对高斯基组来说偏小",
                          path="CP2K_INPUT/FORCE_EVAL/DFT/MGRID",
                          hint="MOLOPT 系列一般要 400–800 Ry；"
                               "用 `cp2kbuild scan --cutoffs` 把它收敛出来",
                          url=f"{MANUAL}methods/dft/cutoff.html")
        else:
            self._add("info", "no-mgrid",
                      "没有 &MGRID：CUTOFF 用 CP2K 默认的 280 Ry",
                      path="CP2K_INPUT/FORCE_EVAL/DFT")

        scf = dft.subsection("SCF")
        ot_blocks = [block for block in scf.subsections("OT")
                     if section_enabled(block)] if scf is not None else []
        diag_blocks = [block for block in scf.subsections("DIAGONALIZATION")
                       if section_enabled(block)] if scf is not None else []
        # an explicit &DIAGONALIZATION wins, and semi-empirical methods ignore
        # &OT altogether - neither is a wrong SCF route for SMEAR
        if ot_blocks and diag_blocks:
            # CP2K: "Don't activate OT and Diagonaliztion together"
            # (scf_control_types.F) - the section parameters are read
            # independently, so only writing both trips it.  A warning rather than
            # an error, because the usual reason both are visible is an
            # @IF ($OT == T) / @IF ($OT == F) pair, which CP2K resolves before it
            # ever looks at the sections.
            self._add("warning", "ot-and-diagonalization",
                      "&OT 和 &DIAGONALIZATION 同时出现了：CP2K 会中止"
                      "（`Don't activate OT and Diagonaliztion together`）——"
                      "但若两者分处 @IF/@ELSE 的不同分支，运行时只有一个存在",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/SCF",
                      hint="留一个：绝缘体/大体系用 &OT；需要 smearing / 未占据轨道用 "
                           "&DIAGONALIZATION（显式写出来的那个会赢）")
        uses_ot = (bool(ot_blocks) and not diag_blocks
                   and is_ab_initio(self.db, qs_method))
        smear = None
        if scf is not None:
            for block in scf.subsections("SMEAR"):
                if section_enabled(block):
                    smear = block
                    break
        if scf is not None:
            eps_scf = _as_float(scf.get("EPS_SCF"), None)
            if eps_scf is not None and eps_scf > 1e-5:
                self._add("warning", "eps-scf-loose",
                          f"EPS_SCF {eps_scf:g} 偏松",
                          path="CP2K_INPUT/FORCE_EVAL/DFT/SCF",
                          hint="AIMD 里一般用 1e-6；SCF 太松时能量和力都不够稳")
            if smear is not None:
                if uses_ot:
                    rotation = next((ot_block for ot_block in ot_blocks
                                     if section_enabled(ot_block)), None)
                    has_rotation = rotation is not None and \
                        _is_true(rotation.get("ROTATION"))
                    if has_rotation:
                        # OT can describe fractional occupations, but only with
                        # the extra rotation variables - and CP2K's own
                        # regression suite has no such input, so say so
                        self._add("warning", "smear-with-ot",
                                  "SMEAR 配 OT：能跑（开了 ROTATION T，"
                                  "分数占据需要它），但很少见",
                                  path="CP2K_INPUT/FORCE_EVAL/DFT/SCF",
                                  hint="smearing 的标准做法是 DIAGONALIZATION；"
                                       "走 OT 时只有 ROTATION T 开着占据数才会被 smearing")
                    else:
                        self._add("error", "smear-with-ot",
                                  "SMEAR 配 OT 但没写 &OT ROTATION T："
                                  "OT 描述不了分数占据",
                                  path="CP2K_INPUT/FORCE_EVAL/DFT/SCF",
                                  hint="要么改用 DIAGONALIZATION（smearing 的常规路线），"
                                       "要么在 &OT 里加 ROTATION T")
                # CP2K's check has no OT guard: `IF (smear%do_smear) IF
                # (added_mos(1) == 0) CPABORT("Extra MOs (ADDED_MOS) are required
                # for smearing")` (qs_environment.F), and with OT the smeared
                # occupations are exactly what ADDED_MOS is needed for
                if _added_mos(dft) == 0:
                    self._add("error", "smear-without-added-mos",
                              "SMEAR 需要 ADDED_MOS > 0",
                              path="CP2K_INPUT/FORCE_EVAL/DFT/SCF",
                              hint="&SCF / ADDED_MOS 20（空带数）；CP2K 会中止"
                                   "（`Extra MOs (ADDED_MOS) are required for smearing`）")
        kpoints = next((s for s in dft.subsections("KPOINTS") if section_enabled(s)), None)
        if kpoints is not None and subsys is not None:
            cell = subsys.subsection("CELL")
            if cell is None or str(cell.get("PERIODIC", "XYZ")).upper() == "NONE":
                self._add("error", "kpoints-without-periodicity",
                          "&KPOINTS 需要一个周期晶胞",
                          path="CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS",
                          hint="k 点只在三维周期体系里才有意义；"
                               "孤立体系用 Γ 点（不写 &KPOINTS）")
        if kpoints is not None and uses_ot:
            # CP2K aborts: "OT diagonalization not possible with kpoint
            # calculations" (qs_scf_initialization.F:826)
            self._add("error", "ot-with-kpoints",
                      "OT 不能算 k 点：CP2K 会直接中止"
                      "（`qs_scf_initialization.F` 里 `OT diagonalization not "
                      "possible with kpoint calculations`）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/SCF/OT",
                      hint="把 SCF 路线换成 DIAGONALIZATION（工具在「自动」时"
                           "也不会给 k 点选 OT），或者去掉 &KPOINTS 只用 Γ 点")
        self._check_kpoints(kpoints, dft)
        self._check_molden(dft, kpoints, subsys)
        self._check_poisson(dft, subsys)

        # dispersion cross-check
        xc = dft.subsection("XC")
        if xc is not None:
            functional = _functional_of(xc)
            vdw = xc.subsection("VDW_POTENTIAL")
            if vdw is not None and functional:
                pair = vdw.subsection("PAIR_POTENTIAL")
                reference = pair.get("REFERENCE_FUNCTIONAL") if pair else None
                if reference and str(reference).upper() != functional.upper():
                    self._add(
                        "warning", "vdw-reference-mismatch",
                        f"VDW_POTENTIAL 写的是 REFERENCE_FUNCTIONAL {reference}，"
                        f"而这里的泛函是 {functional}",
                        path="CP2K_INPUT/FORCE_EVAL/DFT/XC",
                        hint="D3 的参数是针对特定泛函拟合的，"
                             "参考泛函和实际泛函不一致时校正量没有依据",
                    )

    def _check_motion(self, motion: Section, run_type: str, dft: Section | None,
                      subsys: Section | None = None,
                      method: str = "QUICKSTEP",
                      force_eval: Section | None = None) -> None:
        md = motion.subsection("MD")
        # Only apply the ensemble rules when the run type actually enters &MD:
        # CP2K's own regression suite has files with "#RUN_TYPE MD" commented out
        # and a leftover &MD section (cubane_15.inp), where an ensemble without a
        # thermostat is perfectly fine.
        if md is not None and section_enabled(md) \
                and (run_type or "").upper() in ("MD", "MC", "TMC", "PINT"):
            ensemble = str(md.get("ENSEMBLE", "NVE")).upper()
            thermostat = next((s for s in md.subsections("THERMOSTAT")
                               if section_enabled(s)), None)
            barostat = next((s for s in md.subsections("BAROSTAT")
                             if section_enabled(s)), None)
            barostatted = NPT_ENSEMBLES
            # LANGEVIN is its own thermostat: SUBROUTINE langevin in
            # src/motion/integrator.F never calls the &THERMOSTAT machinery, and
            # none of CP2K's own LANGEVIN inputs writes the block.  What it needs
            # instead is a friction: GAMMA, whose default is 0.
            if ensemble == "LANGEVIN":
                langevin = md.subsection("LANGEVIN")
                gamma = _as_float(langevin.get("GAMMA", 0.0), 0.0) \
                    if langevin is not None else 0.0
                if gamma <= 0.0:
                    self._add("warning", "langevin-without-friction",
                              f"ENSEMBLE LANGEVIN 的 GAMMA 是 {gamma:g}（CP2K 的默认也是 0）："
                              "没有摩擦也没有随机力，等于 NVE——温度既不受控，"
                              "升温/降温（TEMPERATURE_ANNEALING）也不会生效",
                              path="CP2K_INPUT/MOTION/MD/LANGEVIN",
                              hint="常用 0.001–0.01 fs^-1（=1–10 ps^-1）；"
                                   "耦合时间 1/GAMMA 要明显短于你要走的温度斜坡")
                if thermostat is not None:
                    self._add("warning", "langevin-with-thermostat",
                              "ENSEMBLE LANGEVIN 不用 &THERMOSTAT：它的摩擦 + 随机力"
                              "就是恒温器，这个块不会生效",
                              path="CP2K_INPUT/MOTION/MD/THERMOSTAT",
                              hint="把温度控制交给 &LANGEVIN GAMMA（工具生成 Langevin "
                                   "输入时就是这么写的）")
            if ensemble == "HYDROSTATICSHOCK":
                # the manual lists it, but no CP2K integrator implements the
                # constant it maps to (nph_ensemble): velocity_verlet_control.F
                # has no such case and aborts on the first MD step
                self._add("error", "ensemble-not-implemented",
                          "ENSEMBLE HYDROSTATICSHOCK 在 CP2K 里没有积分器：手册列了它，"
                          "但跑到第一步就 abort（`Integrator not implemented`，"
                          "motion/velocity_verlet_control.F）",
                          path="CP2K_INPUT/MOTION/MD",
                          hint="要恒压就用 NPT_I / NPT_F / NPT_IA，或 MSST / MSST_DAMPED")
            # CP2K aborts for these four when the particles have no thermostat
            # ("One constant temperature ensemble has been required, but no
            # thermostat for the particles has been defined",
            # thermostat_methods.F).  NPE_*/MSST/ISOKIN run without one by design,
            # so warning about those was a false alarm.
            if ensemble in ("NVT", "NPT_I", "NPT_F", "NPT_IA") and thermostat is None:
                self._add("error", "md-no-thermostat",
                          f"ENSEMBLE {ensemble} 必须给 &THERMOSTAT：CP2K 直接中止"
                          "（`One constant temperature ensemble has been required, "
                          "but no thermostat for the particles has been defined`）",
                          path="CP2K_INPUT/MOTION/MD",
                          hint="加 &THERMOSTAT / TYPE NOSE（或 CSVR）；"
                               "或者改用 LANGEVIN / NVE")
            elif ensemble in ("NPE_I", "NPE_F") and thermostat is not None:
                self._add("info", "thermostat-ignored",
                          f"ENSEMBLE {ensemble} 不用 &THERMOSTAT（手册写明 no "
                          "thermostat）：这个块不会生效",
                          path="CP2K_INPUT/MOTION/MD/THERMOSTAT")
            # The virial is what NPT/NPE/MSST integrate: CP2K aborts with
            # "Virial evaluation not requested for this run ... STRESS_TENSOR"
            # (md_run.F) when STRESS_TENSOR is NONE, which is the default.  The
            # check for the FIST route lives in _check_fist_motion, so QUICKSTEP
            # is covered here.
            if method.upper() not in ("FIST", "MM") and ensemble in NPT_ENSEMBLES:
                stress = str(force_eval.get("STRESS_TENSOR", "NONE")
                             if force_eval is not None else "NONE").upper()
                if stress in ("", "NONE"):
                    self._add("error", "md-no-stress-tensor",
                              f"ENSEMBLE {ensemble} 需要应力张量，但 STRESS_TENSOR 是 "
                              f"{stress or 'NONE'}（默认值）：CP2K 会 abort"
                              "（`Virial evaluation not requested for this run`）",
                              path="CP2K_INPUT/FORCE_EVAL",
                              hint="在 &FORCE_EVAL 里写 STRESS_TENSOR ANALYTICAL；"
                                   "NPT/MSST 系综都靠它积分晶胞")
            if ensemble in barostatted and barostat is None:
                self._add("warning", "md-no-barostat",
                          f"ENSEMBLE {ensemble} 一般都要配一个 &BAROSTAT 段",
                          path="CP2K_INPUT/MOTION/MD",
                          hint="不写就用 CP2K 的默认压浴参数（PRESSURE 1 bar 之类）；"
                               "想要自己的压强/时间常数就把它写出来")
            timestep = _as_float(md.get("TIMESTEP", 0.5), 0.5)
            steps = _as_int(md.get("STEPS", 0), 0)
            # how large a step is acceptable depends on where the forces come
            # from: classical force fields allow far bigger steps than DFT
            classical = method.upper() in ("FIST", "MM")
            limit = 2.0 if classical else 1.0
            if timestep > limit:
                if classical:
                    self._add("warning", "md-timestep-large-classical",
                              f"TIMESTEP {timestep:g} fs 对经典力场 MD 来说也偏大",
                              path="CP2K_INPUT/MOTION/MD",
                              hint="刚性模型用 2 fs 没问题，但可弯的 X–H 键要 1 fs "
                                   "或更小；先确认你用的水/烷烃模型是哪种")
                else:
                    self._add("warning", "md-timestep-large",
                              f"TIMESTEP {timestep:g} fs 对含轻元素的从头算（AIMD）"
                              "来说偏大",
                              path="CP2K_INPUT/MOTION/MD",
                              hint="有氢的时候通常取 0.5 fs；AIMD 用不了力场那种 2 fs")
            if 0 < steps < 100:
                self._add("info", "md-steps-small",
                          f"STEPS 只有 {steps} 步，做统计太短了",
                          path="CP2K_INPUT/MOTION/MD")
            if ensemble in barostatted:
                self._add("info", "md-npt-cell",
                          f"ENSEMBLE {ensemble} 会改晶胞：确认你给的 &CELL "
                          "在物理上合理（盒子错了整条轨迹都是错的）",
                          path="CP2K_INPUT/MOTION/MD",
                          hint="等压系综从初始盒子出发；把平衡后的盒子当起点，"
                               "或先跑一段 NVT 再开压浴")
            if thermostat is not None:
                kind = str(thermostat.get("TYPE", "NOSE")).upper()
                if kind == "GLE" and thermostat.subsection("GLE") is None:
                    self._add("error", "gle-without-matrices",
                              "THERMOSTAT TYPE GLE 需要一个带 S、A 矩阵的 &GLE 段",
                              path="CP2K_INPUT/MOTION/MD/THERMOSTAT",
                              hint="GLE 的 S/A 矩阵没有默认值：NDIM 和 A_LIST/C_LIST/"
                                   "S_LIST 都得写在 &GLE 段里（手册那一页有例子）")
                if kind in ("NOSE", "CSVR") and thermostat.subsection(kind) is None:
                    self._add("info", "thermostat-defaults",
                              f"THERMOSTAT TYPE {kind} 用默认的时间常数",
                              path="CP2K_INPUT/MOTION/MD/THERMOSTAT")

            self._check_annealing(md, ensemble)
            self._check_md_prints(md, subsys)
            self._check_deuterium(md, subsys, method)

        band = motion.subsection("BAND")
        if band is not None:
            if run_type == "BAND" and subsys is not None \
                    and subsys.subsection("CELL") is None:
                self._add(
                    "error", "neb-without-cell",
                    "NEB 运行没有 &CELL：每个镜像都会落在 CP2K 默认的 1 Å 立方体里",
                    path="CP2K_INPUT/FORCE_EVAL/SUBSYS",
                    hint="给镜像一个真正的盒子，例如 PERIODIC NONE 配 "
                         "&CELL / ABC 20 20 20",
                )
            replicas = len(band.subsections("REPLICA"))
            declared = _as_int(band.get("NUMBER_OF_REPLICA", replicas), replicas)
            band_type = str(band.get("BAND_TYPE", "IT-NEB")).upper()
            if replicas and declared != replicas:
                self._add("warning", "neb-replica-mismatch",
                          f"NUMBER_OF_REPLICA 写的是 {declared}，"
                          f"而 &REPLICA 段有 {replicas} 个",
                          path="CP2K_INPUT/MOTION/BAND",
                          hint="大多数 band 类型都是一个镜像一个 &REPLICA")
            if declared < 3:
                self._add("warning", "neb-too-few-images",
                          f"只有 {declared} 个镜像，画不出有意义的势垒",
                          path="CP2K_INPUT/MOTION/BAND",
                          hint="5–9 个镜像（含两端）通常够看形状；"
                               "要精确势垒就得更多，并做收敛测试")
            for replica in band.subsections("REPLICA"):
                if replica.get("COORD_FILE_NAME") is None \
                        and replica.subsection("COORD") is None:
                    self._add("error", "neb-replica-no-file",
                              "有一个 &REPLICA 段既没有 COORD_FILE_NAME，"
                              "也没有 &COORD 段",
                              path="CP2K_INPUT/MOTION/BAND/REPLICA")

        cell_opt = motion.subsection("CELL_OPT")
        if cell_opt is not None and str(cell_opt.get("OPTIMIZER", "LBFGS")).upper() == "BFGS":
            self._add("info", "cell-opt-bfgs",
                      "CELL_OPT 用 BFGS 很贵；一般选 LBFGS",
                      path="CP2K_INPUT/MOTION/CELL_OPT",
                      hint="晶胞优化的 Hessian 近似用 LBFGS 就够了，"
                           "BFGS 每步都要重建 Hessian")

    def _check_annealing(self, md: Section, ensemble: str) -> None:
        """CP2K's two heating/cooling switches, and what they can be combined with.

        ``&MD ANNEALING`` rescales the velocities, so CP2K *aborts* when a
        particle thermostat is on as well (``simpar_methods.F``: "These two
        methods to control the temperature act one against the other"); the error
        is raised here so it is caught before a job is submitted.
        ``&MD TEMPERATURE_ANNEALING`` moves the Langevin target temperature and
        is applied only by the Langevin integrator, so anywhere else it silently
        does nothing.
        """
        annealing = _as_float(md.get("ANNEALING", 1.0), 1.0)
        annealing_cell = _as_float(md.get("ANNEALING_CELL", 1.0), 1.0)
        temperature_annealing = _as_float(md.get("TEMPERATURE_ANNEALING", 1.0), 1.0)

        if annealing != 1.0:
            if ensemble in ("NVT", "NPT_I", "NPT_IA", "NPT_F"):
                self._add(
                    "error", "annealing-with-thermostat",
                    f"ENSEMBLE {ensemble} 还开了 `&MD ANNEALING {annealing:g}`："
                    "CP2K 会直接中止——恒温器和速度缩放是两个互相打架的控温手段"
                    "（simpar_methods.F: \"These two methods to control the "
                    "temperature act one against the other\"）",
                    path="CP2K_INPUT/MOTION/MD",
                    hint="要么去掉 ANNEALING（让恒温器控温），要么把系综换成 "
                         "NVE/NPE_*（那里只能靠速度缩放）；"
                         "想在恒温器下升降温，用 LANGEVIN + "
                         "`&MD TEMPERATURE_ANNEALING`")
        if annealing_cell != 1.0 and ensemble not in ("NPE_I", "NPE_F"):
            self._add(
                "warning", "annealing-cell-inactive",
                f"`&MD ANNEALING_CELL {annealing_cell:g}` 在 ENSEMBLE {ensemble} 下"
                "不会生效：晶胞速度的退火只在 NPE_I / NPE_F 里实现"
                "（integrator.F 里只有这两个系综做这件事）",
                path="CP2K_INPUT/MOTION/MD")
        if temperature_annealing != 1.0 and ensemble != "LANGEVIN":
            self._add(
                "warning", "temperature-annealing-inactive",
                f"`&MD TEMPERATURE_ANNEALING {temperature_annealing:g}` 在 ENSEMBLE "
                f"{ensemble} 下不生效：那个乘法只写在 Langevin 积分器里"
                "（integrator.F，SUBROUTINE langevin），手册也写着 "
                "\"works only for the Langevin ensemble\"",
                path="CP2K_INPUT/MOTION/MD",
                hint="要做受控的升温/降温就把系综改成 LANGEVIN（并给一个 "
                     "&LANGEVIN GAMMA）；在没有恒温器的系综里只能用 "
                     "`&MD ANNEALING` 直接缩放速度")
        for keyword, value in (("ANNEALING", annealing),
                               ("ANNEALING_CELL", annealing_cell),
                               ("TEMPERATURE_ANNEALING", temperature_annealing)):
            if value == 1.0 and md.get(keyword) is not None:
                self._add("info", "annealing-one",
                          f"{keyword} 写成 1.0 就等于没开（CP2K 的默认值也是 1.0）",
                          path="CP2K_INPUT/MOTION/MD")
        # a usable ramp: the setpoint must move slowly compared with the run
        if temperature_annealing not in (1.0,) and ensemble == "LANGEVIN":
            start = _as_float(md.get("TEMPERATURE", 300.0), 300.0)
            steps = _as_int(md.get("STEPS", 0), 0)
            timestep = _as_float(md.get("TIMESTEP", 0.5), 0.5)
            per_step = abs(temperature_annealing - 1.0) * start
            if per_step > 1.0 and steps > 0:
                end = start * temperature_annealing ** steps
                self._add(
                    "warning", "annealing-too-fast",
                    f"温度斜坡每步移动约 {per_step:.1f} K"
                    f"（{start:g} K → {end:.0f} K，{steps * timestep / 1000.0:.2f} ps，"
                    f"约 {abs(end - start) / (steps * timestep / 1000.0):.0f} K/ps）："
                    "比多数 AIMD 升降温快一个量级，恒温器来不及跟上，"
                    "得到的是非平衡构型",
                    path="CP2K_INPUT/MOTION/MD",
                    hint="每步 0.1 K 量级比较稳（比如 300→600 K 用 ≥5 ps）；"
                         "要更快就先想想是不是真需要")

    def _check_md_prints(self, md: Section, subsys: Section | None) -> None:
        """``&MD/&PRINT``: the temperature and energy keys, and their traps.

        ``md_energies.F`` writes the per-kind temperature only when the
        *keyword* ``MD%TEMP_KIND`` is on, and otherwise just warns::

            The print_key MD%PRINT%TEMP_KIND has been activated but the
            calculation of the temperature per kind has not been requested.
            Please switch on the keyword MD%TEMP_KIND.

        That is an error here (the file the user asked for is never written),
        while the opposite (keyword on, no print key) is only worth an info.
        """
        printing = md.subsection("PRINT")
        temp_kind = printing.subsection("TEMP_KIND") if printing is not None else None
        wanted = temp_kind is not None and section_enabled(temp_kind)
        per_kind = _bool_word(md.get("TEMP_KIND", False))
        if wanted and not per_kind:
            self._add("error", "tempkind-print-without-keyword",
                      "打印项 MD%PRINT%TEMP_KIND 开着，但 &MD 里没有 TEMP_KIND T："
                      "CP2K 只会警告一句，文件根本不写",
                      path="CP2K_INPUT/MOTION/MD/PRINT/TEMP_KIND",
                      hint="在 &MD 里也写上 TEMP_KIND T ——"
                           "工具生成时这两个总是一起写")
        elif per_kind and not wanted:
            self._add("info", "tempkind-keyword-without-print",
                      "&MD 里开了 TEMP_KIND，但没有 MD%PRINT%TEMP_KIND 打印项："
                      "每类原子的温度算出来了却没人写出去",
                      path="CP2K_INPUT/MOTION/MD/TEMP_KIND",
                      hint="要那个文件就加 &MD/&PRINT/&TEMP_KIND")
        if per_kind and subsys is not None:
            kinds = subsys.subsections("KIND")
            if len(kinds) == 1:
                self._add("info", "tempkind-single-kind",
                          "体系只有一种原子，它的温度就是全局温度",
                          path="CP2K_INPUT/MOTION/MD/TEMP_KIND")

        thermostat = md.subsection("THERMOSTAT")
        if thermostat is None or not section_enabled(thermostat):
            return
        thermostat_print = thermostat.subsection("PRINT")
        temperature = (thermostat_print.subsection("TEMPERATURE")
                       if thermostat_print is not None else None)
        if temperature is None or not section_enabled(temperature):
            return
        region = str(thermostat.get("REGION", "GLOBAL")).upper()
        if region == "GLOBAL":
            note = ("with THERMOSTAT REGION GLOBAL this is the global temperature - "
                    "the same number the -1.ener file already carries; REGION "
                    "MOLECULE gives one row per molecule kind")
            self._add("info", "thermostat-temp-print-global", note,
                      path="CP2K_INPUT/MOTION/MD/THERMOSTAT/PRINT/TEMPERATURE")

    def _check_deuterium(self, md: Section, subsys: Section | None,
                         method: str) -> None:
        """``&KIND H MASS``: deuterium and the time step it is meant to buy.

        Replacing H by D is a mass change only (the element, basis set and
        pseudopotential stay), and its point in AIMD is the longer stretch
        period: O-H ~3400 cm^-1 is a ~10 fs period, O-D ~2400 cm^-1 is ~14 fs.
        With 10-20 steps per period, 0.5 fs is required for H and 1.0 fs is
        normal for D - so both mismatches are worth saying out loud.
        """
        hydrogen = _hydrogen_kind_mass(subsys)
        if hydrogen is None:
            return
        timestep = _as_float(md.get("TIMESTEP", 0.5), 0.5)
        deuterated = hydrogen >= 1.5               # D is 2.014, H is 1.00794
        if deuterated and 0 < timestep <= 0.6:
            self._add("info", "deuterated-timestep",
                      f"氢已经氘代了，但 TIMESTEP 还是 {timestep:g} fs："
                      "X–D 伸缩周期约 14 fs，用 1.0 fs 通常没问题，"
                      "步数还能减半",
                      path="CP2K_INPUT/MOTION/MD/TIMESTEP")
        elif (not deuterated and method.upper() not in ("FIST", "MM")
                and timestep >= 1.0):
            self._add("info", "timestep-vs-xh-stretch",
                      f"TIMESTEP {timestep:g} fs 配普通氢已经在临界上了："
                      "X–H 伸缩周期只有约 10 fs。常规做法是 0.5 fs，"
                      "或者氘代（&KIND H MASS 2.0141）",
                      path="CP2K_INPUT/MOTION/MD/TIMESTEP")

    def _check_kpoints(self, kpoints: Section | None, dft: Section) -> None:
        """``&KPOINTS`` details CP2K would otherwise silently ignore.

        ``SCHEME GENERAL`` reads the explicit ``KPOINT`` lines: without any, the
        run has no k-point at all.  A grid given next to ``SCHEME GAMMA`` is
        dropped.  ``FULL_GRID`` costs proportionally more, which is worth saying
        once because it is easy to leave on.
        """
        if kpoints is None:
            return
        raw = kpoints.get("SCHEME", "GAMMA")
        tokens = raw if isinstance(raw, list) else str(raw).split()
        scheme = str(tokens[0]).upper() if tokens else "GAMMA"
        points = kpoints.keywords_of("KPOINT")
        if scheme == "GENERAL" and not points:
            self._add("error", "kpoint-general-without-points",
                      "SCHEME GENERAL 靠 `KPOINT x y z w` 行给出 k 点，"
                      "而这个输入里一条都没有",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS",
                      hint="填上显式 k 点，或把 SCHEME 改回网格"
                           "（GAMMA / MONKHORST-PACK 4 4 4）")
        elif scheme == "GENERAL" and points:
            self._add("info", "kpoint-explicit-list",
                      f"SCHEME GENERAL：显式给了 {len(points)} 个 k 点"
                      "（通常还应该关掉 SYMMETRY，否则 CP2K 会做对称约化）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS")
        if scheme in ("GAMMA", "MONKHORST-PACK", "MACDONALD") \
                and len(tokens) > 1 and scheme == "GAMMA":
            self._add("warning", "kpoint-gamma-with-numbers",
                      f"SCHEME GAMMA 后面的 {tokens[1:]} 不会生效："
                      "Γ 点不需要网格数字（要网格就用 MONKHORST-PACK）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS")
        if section_enabled(kpoints) and _bool_word(kpoints.get("FULL_GRID", False)):
            self._add("info", "kpoint-full-grid",
                      "FULL_GRID T：用整个网格而不做对称约化，"
                      "k 点数和耗时都会成倍增加（Wannier/输运这类需要完整网格时才对）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS")
        if kpoints.get("WAVEFUNCTIONS") is not None \
                and _enum_word(kpoints.get("WAVEFUNCTIONS")) == "COMPLEX" \
                and section_enabled(kpoints):
            self._add("info", "kpoint-complex-wavefunctions",
                      "WAVEFUNCTIONS COMPLEX：波函数按复数列处理（自旋轨道耦合才需要），"
                      "内存和耗时都比 REAL 高",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/KPOINTS")

    def _check_molden(self, dft: Section, kpoints: Section | None,
                      subsys: Section | None) -> None:
        """``&MO_MOLDEN`` is skipped entirely when the run uses k-points.

        ``qs_scf_post_gpw.F`` guards the write with ``IF (.NOT. do_kpoints)``, so
        a k-point calculation produces no Molden file at all - and it says
        nothing about it.  That is worth a warning, because the file simply never
        appears.
        """
        print_section = dft.subsection("PRINT")
        blocks = [b for b in print_section.subsections("MO_MOLDEN")
                  if section_enabled(print_section)] if print_section else []
        block = next((b for b in blocks if section_enabled(b)), None)
        if block is None:
            return
        scheme = str(kpoints.get("SCHEME", "GAMMA")).upper() if kpoints else ""
        if kpoints is not None and not scheme.startswith("GAMMA"):
            self._add("warning", "molden-with-kpoints",
                      "&MO_MOLDEN 在带 k 点的计算里**不会被写出来**："
                      "CP2K 把这一步包在 `IF (.NOT. do_kpoints)` 里，"
                      "而且不会给任何提示（qs_scf_post_gpw.F:2229）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN",
                      hint="Molden 格式本身不带 k 点。要看轨道就换成 Γ 点单胞，"
                           "或者用 &PRINT &CUBES 出实空间 cube")
        nlumo = _as_int(block.get("NLUMO", 0), 0)
        if nlumo == 0:
            self._add("info", "molden-occupied-only",
                      "&MO_MOLDEN NLUMO 0：文件里只有占据轨道（CP2K 的默认值）",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/PRINT/MO_MOLDEN",
                      hint="要看空轨道/LUMO 就设 NLUMO 为需要的条数，或 -1 表示全部可用")

    def _check_poisson(self, dft: Section, subsys: Section | None) -> None:
        """&POISSON and &CELL talk about the same periodicity, and the solver
        has to be one the manual lists for that periodicity."""
        poisson = None
        for block in dft.subsections("POISSON"):
            if section_enabled(block):
                poisson = block
                break
        cell = subsys.subsection("CELL") if subsys is not None else None
        cell_periodic = str(cell.get("PERIODIC", "XYZ")).upper() if cell else "XYZ"
        if poisson is None:
            # CP2K's default solver is 3-D periodic; anything else needs the
            # matching solver, which is what the generator writes automatically
            if cell_periodic not in ("XYZ", "NONE"):
                self._add("warning", "poisson-missing",
                          f"&CELL 是 PERIODIC {cell_periodic}，但没有 &POISSON："
                          "CP2K 默认的泊松求解器是三维周期的",
                          path="CP2K_INPUT/FORCE_EVAL/DFT",
                          hint="补一个 &POISSON：PERIODIC "
                               f"{cell_periodic}，求解器用 WAVELET 之类")
            return

        poisson_periodic = str(poisson.get("PERIODIC", "XYZ")).upper()
        if poisson_periodic != cell_periodic:
            self._add("warning", "poisson-periodic-mismatch",
                      f"&POISSON 写的是 PERIODIC {poisson_periodic}，"
                      f"而 &CELL 是 PERIODIC {cell_periodic}",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/POISSON",
                      hint="手册说这两个通常要一致；几何/近邻表按 &CELL 算，"
                           "静电按 &POISSON 算，不一致时两边说的不是同一个体系")
        solver = poisson.get("POISSON_SOLVER")
        if solver is None:
            # CP2K's own default is PERIODIC, and its consistency check against
            # the periodicity is commented out (pw_poisson_read_parameters), so
            # "&POISSON PERIODIC NONE" without a solver is what a lot of real
            # inputs do - including 238 of CP2K's regression tests.  Only an
            # *explicit* choice can be wrong.
            return
        solver = str(solver).upper()
        valid = poisson_solvers_for(poisson_periodic)
        if solver not in valid:
            self._add("warning", "poisson-solver-mismatch",
                      f"手册说 POISSON_SOLVER {solver} 不适用于 "
                      f"{poisson_periodic} 周期的晶胞",
                      path="CP2K_INPUT/FORCE_EVAL/DFT/POISSON",
                      hint=f"手册里 {solver} 的说明是 "
                           f"{POISSON_SOLVER_WHY.get(solver, '')}；这里可用的是 "
                           f"{', '.join(valid)}。CP2K 自己不做这个检查"
                           "（它的一致性检查被注释掉了），所以能跑起来 ——"
                           "但这个求解器不是为这种晶胞准备的")

    def _check_print_intervals(self, dft: Section | None, motion: Section | None,
                               run_type: str) -> None:
        """``&EACH`` 0 / a level the run never reaches / ``ADD_LAST NO``.

        The two settings are independent: ``&EACH`` is how often *during* the run,
        ``ADD_LAST`` adds the last iteration.  With no interval and no
        last-iteration dump the switch is on but nothing ever appears.

        The *level* matters just as much: ``QS_SCF`` counts the SCF iterations
        inside a single force evaluation, so "every 500 SCF steps" in an MD run
        only fires when one MD step needs 500 SCF iterations - the interval does
        nothing there and ADD_LAST is the only thing that writes the file.  The
        same applies to a ``&EACH MD`` interval on a geometry optimisation.
        """
        expected = {"MD": "MD", "GEO_OPT": "GEO_OPT", "CELL_OPT": "CELL_OPT"}.get(
            (run_type or "").upper(), "QS_SCF")
        scf = dft.subsection("SCF") if dft is not None else None
        blocks = []
        if scf is not None:
            print_block = scf.subsection("PRINT")
            if print_block is not None:
                blocks.append(("the wave function restart file",
                               print_block.subsection("RESTART"),
                               "CP2K_INPUT/FORCE_EVAL/DFT/SCF/PRINT/RESTART",
                               "SCF_GUESS RESTART", expected))
        # a &MOTION/&PRINT/... block in a run that never enters &MOTION (ENERGY,
        # FREQUENCY, ...) is inert, so those are not worth a warning
        if motion is not None and (run_type or "").upper() in (
                "MD", "GEO_OPT", "CELL_OPT"):
            print_block = motion.subsection("PRINT")
            if print_block is not None:
                blocks.append(("the run restart file", print_block.subsection("RESTART"),
                               "CP2K_INPUT/MOTION/PRINT/RESTART", "&EXT_RESTART",
                               expected))
                blocks.append(("轨迹文件", print_block.subsection("TRAJECTORY"),
                               "CP2K_INPUT/MOTION/PRINT/TRAJECTORY", None, expected))
            # metadynamics writes its .colvar/.hills files on MD steps too
            # (`each_iter_names=s2a("MD", "METADYNAMICS")` in
            # input_cp2k_free_energy.F), so an interval counted on the SCF
            # iterations - or the whole block inside a geometry optimisation -
            # would never produce a file
            free_energy = motion.subsection("FREE_ENERGY")
            metadyn = (free_energy.subsection("METADYN")
                       if free_energy is not None else None)
            metadyn_print = (metadyn.subsection("PRINT")
                             if metadyn is not None else None)
            if metadyn_print is not None:
                blocks.append(("元动力学的 COLVAR 文件",
                               metadyn_print.subsection("COLVAR"),
                               "CP2K_INPUT/MOTION/FREE_ENERGY/METADYN/PRINT/COLVAR",
                               None, expected))
                blocks.append(("元动力学的 HILLS 文件",
                               metadyn_print.subsection("HILLS"),
                               "CP2K_INPUT/MOTION/FREE_ENERGY/METADYN/PRINT/HILLS",
                               "接力与画自由能面都要它", expected))
        for label, block, path, consumer, wanted in blocks:
            if block is None or not section_enabled(block):
                continue
            each = block.subsection("EACH")
            present = [level for level in ("MD", "GEO_OPT", "CELL_OPT", "QS_SCF")
                       if each is not None and each.get(level) is not None]
            level = present[0] if present else wanted
            interval = each.get(level) if each is not None else None
            add_last = str(block.get("ADD_LAST", "NUMERIC")).upper()
            every = _as_int(interval, -1) if interval is not None else -1
            if every == 0 and add_last in ("NO", "F", "FALSE", ".FALSE."):
                self._add("warning", "restart-print-never",
                          f"{label}：&EACH {level} 是 0，ADD_LAST 又是 NO，"
                          "这个文件永远不会出现",
                          path=path,
                          hint=(f"{consumer} 到时就没什么可读的；"
                                "给个间隔，或者把 ADD_LAST 打开" if consumer else
                                "给个间隔、或把 ADD_LAST 打开，否则这个文件不会出现"))
            elif present and level != wanted:
                self._add(
                    "warning", "restart-print-level-mismatch",
                    f"{label} 的 &EACH 按 {level} 计数，但这是个 "
                    f"{run_type or '单点'} 计算：这个间隔几乎不会触发"
                    "（真正写文件的是 ADD_LAST）",
                    path=path,
                    hint=(f"想「每 N 步落一次盘」就把 &EACH {level} 改成 &EACH {wanted}"
                          if wanted != "QS_SCF" else
                          f"改成 &EACH {wanted}（SCF 迭代）"),
                )

    def _check_vibrational(self, vibrational: Section | None, run_type: str) -> None:
        if run_type == "VIBRATIONAL_ANALYSIS" and vibrational is None:
            self._add("error", "missing-vibrational",
                      "RUN_TYPE VIBRATIONAL_ANALYSIS 需要 &VIBRATIONAL_ANALYSIS 段",
                      path="CP2K_INPUT",
                      hint="这一段给出要算的频率类型和数值步长，"
                           "不写就没有可算的东西")
        if vibrational is not None and run_type not in (
                "VIBRATIONAL_ANALYSIS", "ENERGY", "GEO_OPT"):
            self._add("info", "vibrational-unused",
                      f"RUN_TYPE {run_type} 下不读 &VIBRATIONAL_ANALYSIS",
                      path="CP2K_INPUT/VIBRATIONAL_ANALYSIS")

    def _check_properties(self, force_eval: Section, dft: Section | None,
                          run_type: str) -> None:
        properties = force_eval.subsection("PROPERTIES")
        if dft is None:
            return
        print_section = dft.subsection("PRINT")
        if print_section is None or not section_enabled(print_section):
            return
        band_block = next((b for b in print_section.subsections("BAND_STRUCTURE")
                           if section_enabled(b)), None)
        pdos_block = next((b for b in print_section.subsections("PDOS")
                           if section_enabled(b)), None)
        if band_block is not None:
            if _added_mos(dft) == 0:
                self._add(
                    "warning", "bandstructure-without-added-mos",
                    "&PRINT/&BAND_STRUCTURE 需要未占据态，"
                    "但哪里都没有 ADDED_MOS",
                    path="CP2K_INPUT/FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE",
                    hint="在 &SCF 里写 ADDED_MOS（走 OT 时写在 &OT 里），"
                         "或者写在 &BAND_STRUCTURE 段自己里面",
                )
        if pdos_block is not None:
            scf = dft.subsection("SCF")
            ot_active = scf is not None and any(
                section_enabled(block) for block in scf.subsections("OT"))
            if ot_active and _added_mos(dft) == 0:
                self._add("info", "pdos-ot-added-mos",
                          "走 OT 时 PDOS 用的是 OT 的本征值；"
                          "ADDED_MOS 会让未占据那部分更准",
                          path="CP2K_INPUT/FORCE_EVAL/DFT/PRINT/PDOS")


def _added_mos(dft: Section) -> int:
    """ADDED_MOS from &SCF or &PRINT/&BAND_STRUCTURE.

    ``&SCF/&OT`` and ``&SCF/&SMEAR`` are *not* holders: CP2K declares ADDED_MOS
    in ``&SCF`` only (input_cp2k_scf.F), so counting a copy placed there both
    hid the "ADDED_MOS is required for smearing" abort and made the misplaced
    keyword look harmless.  Returns ``-1`` when the value is decided by the
    preprocessor, i.e. unknown rather than zero.
    """
    holders: list[Section] = []
    scf = dft.subsection("SCF")
    if scf is not None:
        holders.append(scf)
    print_section = dft.subsection("PRINT")
    if print_section is not None:
        holders.extend(block for block in print_section.subsections("BAND_STRUCTURE")
                       if section_enabled(block))

    values: list[int] = []
    for holder in holders:
        for kw in holder.keywords_of("ADDED_MOS"):
            if any(has_preprocessor(v) for v in kw.values):
                return -1
            values.append(_as_int(kw.values, 0))
    return max(values) if values else 0


def _hydrogen_kind_mass(subsys: Section | None) -> float | None:
    """The ``MASS`` of the hydrogen ``&KIND``, if one is there.

    ``None`` when there is no hydrogen (or no ``&KIND`` at all, which is the
    classical route - there the masses come from the topology file).  1.00794 is
    ordinary hydrogen, 2.0141 is deuterium.
    """
    if subsys is None:
        return None
    for kind in subsys.subsections("KIND"):
        name = str(kind.parameter or "").strip().upper()
        element = str(kind.get("ELEMENT", name) or name).strip().upper()
        if element != "H":
            continue
        return _as_float(kind.get("MASS", 0.0), 0.0)
    return None


def _matches_listed_subsection(listed: Sequence[str], name: str) -> bool:
    """Match ``&LENNARD`` against the manual's ``LENNARD-JONES``.

    The manual occasionally documents a section with a longer, qualified name
    than the one CP2K actually accepts, so a listed name that starts with the
    written name followed by a separator is taken as the same section.  A plain
    prefix (``LENN``) is not accepted.
    """
    upper = name.upper()
    if not upper:
        return False
    for candidate in listed:
        text = candidate.upper()
        if text == upper:
            return True
        for separator in ("-", "_", "."):
            if text.startswith(upper + separator) or upper.startswith(text + separator):
                return True
    return False


def _basis_name_of(value) -> str:
    """Pick the actual name out of ``BASIS_SET`` / ``POTENTIAL`` values.

    These accept a leading role marker, as in ``BASIS_SET ORB pob-TZVP`` or
    ``BASIS_SET AUX cFIT``, so the name is not always the first token.
    """
    values = value if isinstance(value, list) else [value]
    values = [str(v) for v in values]
    if len(values) > 1 and values[0].upper() in ("ORB", "AUX", "RI", "FIT", "ADMM"):
        return values[1]
    return values[0] if values else ""


def _declared_files(section: Section, keyword: str) -> list[str]:
    out: list[str] = []
    for kw in section.keywords_of(keyword):
        for value in kw.values:
            if has_preprocessor(value):
                return ["<preprocessor>"]
            out.append(str(value))
    return out


def _kind_by_element(kinds: dict[str, Section], element: str) -> bool:
    for section in kinds.values():
        declared = section.get("ELEMENT")
        if declared is not None and str(declared).upper() == element.upper():
            return True
    return False


def _functional_of(xc: Section) -> str | None:
    block = xc.subsection("XC_FUNCTIONAL")
    if block is None or block.parameter is None:
        return None
    parameter = block.parameter
    if isinstance(parameter, list):
        parameter = parameter[0]
    return str(parameter).upper()


def _as_float(value, default):
    if value is None:
        return default
    if isinstance(value, list):
        if not value:
            return default
        value = value[0]
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return default


def _as_int(value, default):
    if value is None:
        return default
    if isinstance(value, list):
        if not value:
            return default
        value = value[0]
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(float(str(value)))
    except ValueError:
        return default


def _looks_int(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return float(value).is_integer()
    text = str(value).strip().rstrip(",")
    if re.fullmatch(r"[-+]?\d+", text):
        return True
    # CP2K integer lists accept ranges, e.g. "1..58,65..113"
    parts = [p for p in text.split(",") if p]
    if parts and all(re.fullmatch(r"[-+]?\d+\.\.[-+]?\d+", p) or
                     re.fullmatch(r"[-+]?\d+", p) for p in parts):
        return True
    return _is_expression(text)


def _looks_real(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    text = str(value).strip()
    try:
        float(text)
        return True
    except ValueError:
        # Fortran's double-precision exponent is valid CP2K input - CP2K's own
        # test suite writes EPSILON 0.D0 and SIGMA 1.D0
        if re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)[dD][-+]?\d+", text):
            return True
        return _is_expression(text)


def _is_number(token) -> bool:
    text = str(token).strip()
    if re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)[dD][-+]?\d+", text):
        return True                     # Fortran exponent: 0.D0
    try:
        float(text)
        return True
    except ValueError:
        return False


def _short(values: Iterable) -> str:
    text = ", ".join(repr(v) for v in values)
    return text if len(text) < 60 else text[:57] + "..."


def summarise(diagnostics: Sequence[Diagnostic], extra_errors: int = 0,
              extra_warnings: int = 0) -> str:
    """The one-line tally.

    ``extra_*`` are findings the caller printed itself (``cp2kbuild new`` writes the
    generator's own ``warning:``/``error:`` lines directly): without them the tally
    reads "0 个错误" right underneath an ``error:`` line.
    """
    errors = sum(1 for d in diagnostics if d.level == "error") + extra_errors
    warnings = sum(1 for d in diagnostics if d.level == "warning") + extra_warnings
    notes = sum(1 for d in diagnostics if d.level == "info")
    if not diagnostics and not extra_errors and not extra_warnings:
        return "干净：没有发现问题"
    return f"{errors} 个错误、{warnings} 个警告、{notes} 条提示"
