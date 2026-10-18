"""Turn an existing CP2K input into a family of convergence-test inputs.

A hand-written input is usually not a plain ``&SECTION`` tree: it uses the CP2K
preprocessor (``@SET`` / ``@IF`` / ``@INCLUDE``) and leaves placeholders such as
``LT_cutoff`` for a shell script to replace::

    @SET CUTOFF LT_cutoff

The usual way to sweep a parameter is then a ``sed`` loop that rewrites the
``@SET`` value and copies the ``.inc`` files into one directory per value.  This
module does the same thing but without throwing the file away: the template is
kept **verbatim** - comments, tabs, ``@IF`` blocks and all - and only the value of
the swept keyword is replaced, so nothing else about the input can change by
accident.

The substitution is deliberately not clever.  In order of preference:

1. ``@SET <KEYWORD> <token>``  - the token is replaced (the usual convention, and
   it keeps ``${KEYWORD}`` working inside the include files)
2. ``<KEYWORD> <token>``       - a plain keyword line is replaced
3. a placeholder anywhere      - ``LT_<keyword>``, ``${KEYWORD}``, ``@KEYWORD@``
   or ``__KEYWORD__``

If none of these matches, the case is refused: silently producing N identical
files would look like a convergence test that says "nothing changes".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: file names that travel with a template (coordinates, include files)
SIDE_SUFFIXES = (".inc", ".xyz", ".pdb", ".cif", ".dat")

RE_INCLUDE = re.compile(r"^\s*@INCLUDE\s+([^\s#!]+)", re.M | re.I)


@dataclass
class Substitution:
    keyword: str
    strategy: str
    occurrences: int

    def describe(self) -> str:
        return {
            "set": f"@SET {self.keyword} 的值",
            "literal": f"{self.keyword} 那一行的值",
            "placeholder": f"{self.keyword} 的占位符",
        }.get(self.strategy, self.strategy)


def _set_pattern(keyword: str) -> re.Pattern:
    return re.compile(rf"^([ \t]*@SET[ \t]+{re.escape(keyword)}[ \t]+)(\S+)(.*)$",
                      re.M | re.I)


def _literal_pattern(keyword: str) -> re.Pattern:
    # the name must be followed by whitespace, so CUTOFF_RADIUS and
    # CUTOFF_CONV_INFO are left alone
    #
    # A ``[unit]`` annotation may stand between the keyword and its value
    # (``CUTOFF [Ry] 400.0``, which is what cp2kbuild itself writes).  The
    # annotation is not the value: replacing *it* would leave ``CUTOFF LT_cutoff
    # 400.0`` in the template and ``CUTOFF 100 400.0`` after ``sed`` - two values
    # on one line.  So the annotation is captured separately and put back.
    return re.compile(
        rf"^([ \t]*)({re.escape(keyword)})([ \t]+)(\[[^\]]*\][ \t]+)?(\S+)(.*)$",
        re.M | re.I)


def _literal_sub(keyword: str, rendered: str):
    """The replacement for :func:`_literal_pattern`, unit annotation and all."""

    def repl(match: re.Match) -> str:
        annotated = match.group(4)
        head = f"{match.group(1)}{match.group(2)}{match.group(3)}"
        if annotated is None:
            return f"{head}{rendered}{match.group(6)}"
        from .units import is_valid
        if is_valid(annotated.strip().rstrip()):
            # a real unit annotation: keep it, put the new value behind it
            return f"{head}{annotated}{rendered}{match.group(6)}"
        # a bracket that only *looks* like a unit (a character keyword's value,
        # ``ELECTRON_CONFIGURATION [Rn] 6d1 7s2``) - that token is the value
        return f"{head}{rendered} {match.group(5)}{match.group(6)}"

    return repl


def _placeholder_pattern(keyword: str) -> re.Pattern:
    name = re.escape(keyword)
    return re.compile(
        rf"(?<![A-Za-z0-9_])(?:LT_{name}|\$\{{{name}\}}|@{name}@|__{name}__)"
        rf"(?![A-Za-z0-9_])",
        re.I)


def _format(value) -> str:
    if isinstance(value, bool):
        return "T" if value else "F"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def detect(text: str, keyword: str) -> str | None:
    """Which substitution rule applies to ``keyword`` in ``text`` (or None)."""
    if _set_pattern(keyword).search(text):
        return "set"
    if _literal_pattern(keyword).search(text):
        return "literal"
    if _placeholder_pattern(keyword).search(text):
        return "placeholder"
    return None


def replace(text: str, keyword: str, value) -> tuple[str, Substitution | None]:
    """Replace the value of ``keyword``; ``None`` when there is nothing to replace."""
    rendered = _format(value)
    pattern = _set_pattern(keyword)
    matches = pattern.findall(text)
    if matches:
        return pattern.sub(lambda m: f"{m.group(1)}{rendered}{m.group(3)}", text), \
            Substitution(keyword, "set", len(matches))

    pattern = _literal_pattern(keyword)
    matches = pattern.findall(text)
    if matches:
        return pattern.sub(_literal_sub(keyword, rendered), text), \
            Substitution(keyword, "literal", len(matches))

    pattern = _placeholder_pattern(keyword)
    matches = pattern.findall(text)
    if matches:
        # a lambda, not the raw string: re.sub treats a *replacement string* as
        # a template, so a value with a backslash (a Windows path) or "\\1"
        # either injected a newline/tab or raised "invalid group reference"
        return pattern.sub(lambda m: rendered, text), \
            Substitution(keyword, "placeholder", len(matches))

    return text, None


def replace_named(text: str, name: str, value) -> tuple[str, Substitution | None]:
    """``--set NAME=VALUE``: try ``@SET NAME`` first, then a bare placeholder."""
    replaced, info = replace(text, name, value)
    if info is not None:
        return replaced, info
    # NAME may itself be a placeholder token such as LT_rtype
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    matches = pattern.findall(text)
    if matches:
        return pattern.sub(lambda m: _format(value), text), \
            Substitution(name, "placeholder", len(matches))
    return text, None


def side_files(template: Path) -> list[Path]:
    """Files that must sit next to every generated input.

    The ``@INCLUDE`` targets named by the template (followed recursively) plus
    any coordinates/include files in the same directory - the same set the usual
    shell scripts copy with ``find . -name "*.inc" -o -name "*.xyz"``.
    """
    found: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved in seen or not path.is_file():
            return
        seen.add(resolved)
        found.append(path)

    def walk(path: Path, depth: int = 0) -> None:
        if depth > 6:
            return
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return
        for name in RE_INCLUDE.findall(text):
            target = path.parent / name.strip("\"'")
            if target.is_file():
                add(target)
                walk(target, depth + 1)

    walk(template)
    for candidate in sorted(template.parent.iterdir()):
        if candidate == template or not candidate.is_file():
            continue
        if candidate.suffix.lower() in SIDE_SUFFIXES:
            add(candidate)
    return found


def directory_name(label: str, value, units: bool = True) -> str:
    """``cutoff_450Ry`` - the naming the usual convergence scripts use."""
    text = _format(value)
    if units and label.upper() in {"CUTOFF", "REL_CUTOFF"}:
        return f"{label.lower()}_{text}Ry"
    return f"{label.lower()}_{text}"
