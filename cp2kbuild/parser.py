"""A forgiving reader for CP2K input files.

It is *not* a full CP2K parser: the preprocessor (``@IF``/``@SET``/``@INCLUDE``)
is reported but not executed.  Everything else - nested sections, section
parameters, repeated keywords, quoted strings, ``&`` continuations, comments and
unit annotations - is handled, which is what a linter needs.
"""

from __future__ import annotations

import re
from pathlib import Path

from .model import Keyword, Section

RE_SECTION = re.compile(
    r"^&([A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*)\b(.*)$")
RE_END = re.compile(r"^&END\b\s*([A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*)?", re.I)
RE_UNIT = re.compile(r"^\[([^\]]+)\]$")


def _strip_comment(line: str) -> str:
    """Remove a ``#`` comment or a whitespace-delimited ``!`` comment.

    CP2K's own regression inputs use both, e.g. ``CUTOFF 200  ! 320``.  A ``!``
    that is glued to a token is left alone so file names survive.
    """
    quote: str | None = None
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "#":
            return line[:index]
        elif char == "!" and (index == 0 or line[index - 1].isspace()):
            return line[:index]
    return line


def _tokenise(text: str) -> list[str]:
    """Split one logical line into tokens the way CP2K's reader does.

    CP2K's tokenizer also treats ``,``, ``;`` and ``=`` as separators (a leading
    one is skipped), so ``FILENAME =RESTART`` has the value ``RESTART`` - not
    ``"=RESTART"`` as this reader used to report - and
    ``ROKS_PARAMETERS 1/3,2/3; 1/3,1/3`` is six numbers instead of one string.
    A ``:`` is deliberately **not** a separator: CP2K accepts ``WALLTIME
    12:00:00``.
    """
    tokens: list[str] = []
    current = ""
    quote: str | None = None
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
            else:
                current += char
        elif char in "\"'":
            quote = char
        elif char.isspace() or char in ",;=":
            if char == "=" and index == 0:
                continue                      # CP2K skips a leading separator
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


def _coerce(token: str):
    # A single letter stays text: "F" is fluorine, not ``False`` - CP2K's own
    # ``ELEMENT F`` and ``&KIND F`` inputs came out as booleans, and a bare "T"
    # has no boolean meaning either (the section-parameter reader already keeps
    # both for exactly this reason).
    if len(token) > 1:
        lowered = token.lower()
        if lowered in ("true", ".true.", "yes", "on"):
            return True
        if lowered in ("false", ".false.", "no", "off"):
            return False
    try:
        if re.fullmatch(r"[-+]?\d+", token):
            return int(token)
        return float(token)
    except ValueError:
        return token


def _coerce_parameter(token: str):
    """Section parameters keep bare ``T``/``F`` as text.

    ``&KIND F`` is fluorine while ``&TRAJECTORY T`` is a logical, so a
    single-letter token must not be turned into a boolean here.
    """
    lowered = token.lower()
    if lowered == ".true.":
        return True
    if lowered == ".false.":
        return False
    if len(token) > 1:
        if lowered in ("true", "yes", "on"):
            return True
        if lowered in ("false", "no", "off"):
            return False
    if re.fullmatch(r"[-+]?\d+", token):
        return int(token)
    try:
        return float(token)
    except ValueError:
        return token


class ParseResult:
    def __init__(self) -> None:
        self.sections: list[Section] = []
        self.directives: list[tuple[int, str]] = []
        self.warnings: list[str] = []
        self.max_depth = 0


def parse_input(text: str) -> ParseResult:
    """Parse CP2K input text into :class:`~cp2kbuild.model.Section` objects."""
    result = ParseResult()
    if text.startswith("\ufeff"):        # files saved by Windows editors
        text = text[1:]
    roots: list[Section] = []
    stack: list[Section] = []

    logical: list[tuple[int, str]] = []
    buffer = ""
    buffer_line = 0
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.split(None, 1)[0].upper() == "DESCRIBE":
            # CP2K's input parser has a `DESCRIBE [level] [keyword ...]`
            # directive that prints the documentation of the current section and
            # carries on (input/input_parsing.F:209).  It is not a keyword of the
            # section, so the schema check used to call it an unknown keyword -
            # and CP2K's own regression suite uses it.
            result.directives.append((number, stripped))
            continue
        if stripped.startswith("@"):
            # a preprocessor line must not swallow a pending continuation: the
            # value it would supply lives in the included file, not on the next
            # line (CP2K's own generated inputs write "TIMECON \" + "@INCLUDE x")
            if buffer:
                logical.append((buffer_line, buffer))
                buffer = ""
            result.directives.append((number, stripped))
            continue
        if not buffer:
            buffer_line = number
        if stripped.endswith("\\") and not stripped.startswith("&"):
            # CP2K accepts a backslash as a line-continuation character as well
            buffer += stripped[:-1] + " "
            continue
        if stripped.endswith("&") and not stripped.startswith("&"):
            buffer += stripped[:-1] + " "
            continue
        buffer += line
        logical.append((buffer_line, buffer))
        buffer = ""
    if buffer:
        logical.append((buffer_line, buffer))

    for number, line in logical:
        stripped = line.strip()
        if not stripped:
            continue

        if RE_END.match(stripped):
            end = RE_END.match(stripped)
            target = (end.group(1) or "").upper()
            if not target:
                if stack:
                    stack.pop()
                else:
                    result.warnings.append(f"第 {number} 行：&END 前面没有打开的段")
                continue
            # tolerate the abbreviated style where a parameter-only section is
            # not closed, e.g. "&XC_FUNCTIONAL PBE" followed by "&END XC"
            while stack and stack[-1].name.upper() != target:
                top = stack[-1]
                if top.keywords or top.sections:
                    break
                stack.pop()
                result.warnings.append(
                    f"第 {number} 行：&{top.name} 被 &END {target} 隐式关闭了")
            if stack and stack[-1].name.upper() == target:
                stack.pop()
            elif stack:
                result.warnings.append(
                    f"第 {number} 行：&END {target} 和当前打开的 "
                    f"&{stack[-1].name} 对不上")
                stack.pop()
            else:
                result.warnings.append(
                    f"第 {number} 行：&END {target} 前面没有打开的段")
            continue

        match = RE_SECTION.match(stripped)
        if match:
            name = match.group(1).upper()
            rest = match.group(2).strip()
            parameter = None
            if rest:
                tokens = _tokenise(rest)
                if len(tokens) == 1:
                    parameter = _coerce_parameter(tokens[0])
                elif tokens:
                    parameter = [_coerce_parameter(t) for t in tokens]
            section = Section(name, parameter)
            if stack:
                stack[-1].add(section)
            else:
                roots.append(section)
            stack.append(section)
            result.max_depth = max(result.max_depth, len(stack))
            continue

        if not stack:
            result.warnings.append(
                f"第 {number} 行：关键字出现在任何段之外：{stripped[:60]!r}")
            continue

        tokens = _tokenise(stripped)
        if not tokens:
            continue
        # CP2K tolerates the "NAME:" spelling used in its own documentation
        name = tokens[0].upper().rstrip(":")
        if not name:
            continue
        values: list = []
        unit = None
        index = 1
        # CP2K also accepts "KEYWORD=value" without spaces
        if "=" in name and len(tokens) == 1:
            head, _, tail = name.partition("=")
            if head and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", head) and tail:
                name = head.upper()
                tokens = [name, tail]
                index = 1
        if index < len(tokens):
            unit_match = RE_UNIT.match(tokens[index])
            if unit_match:
                unit = unit_match.group(1)
                index += 1
        for token in tokens[index:]:
            # a unit annotation may also appear after the first value:
            # "K 0.1616657105 [bohr^-1] 1.2580547909"
            unit_match = RE_UNIT.match(token)
            if unit_match:
                unit = unit or unit_match.group(1)
                continue
            values.append(_coerce(token))
        stack[-1].keywords.append(Keyword(name, values, unit))

    result.sections = roots
    return result


def read_input(path: str | Path) -> ParseResult:
    file = Path(path)
    return parse_input(file.read_text(encoding="utf-8-sig", errors="ignore"))
