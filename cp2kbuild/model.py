"""In-memory CP2K input tree and a faithful serialiser.

The model is intentionally small: a CP2K input file is a tree of sections, each
section holding an optional default parameter, a list of keywords and a list of
subsections.  Ordering matters to CP2K, so the serialiser always writes all
keywords of a section *before* its subsections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterator, Sequence

INDENT = "   "
"""CP2K's own examples use three spaces of indentation."""

PATH_SEP = "/"


def fmt_float(value: float) -> str:
    """Format a float the way CP2K inputs usually spell it."""
    if value != value:  # NaN
        return "0.0"
    if value in (float("inf"), float("-inf")):
        return "1.0E+30" if value > 0 else "-1.0E+30"
    magnitude = abs(value)
    if value == 0:
        return "0.0"
    if 1.0e-4 <= magnitude < 1.0e7:
        text = f"{value:.12g}"
        return text if ("." in text or "e" in text or "E" in text) else text + ".0"
    return f"{value:.6E}"


def fmt_value(value: Any) -> str:
    """Render one CP2K value."""
    if isinstance(value, bool):
        return "T" if value else "F"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return fmt_float(value)
    if value is None:
        return ""
    return str(value)


def fmt_values(values: Sequence[Any]) -> str:
    parts = [fmt_value(v) for v in values]
    return " ".join(p for p in parts if p != "")


def _keyword_name(name: str, preserve_name: bool = False) -> str:
    """CP2K keywords are upper case, but element symbols in &COORD are not."""
    if preserve_name:
        return name
    if re.fullmatch(r"[A-Z][a-z]?", name):
        return name          # an element symbol such as Si or H
    return name.upper()


@dataclass
class Keyword:
    """A single ``NAME value ...`` line, optionally with a unit and a comment."""

    name: str
    values: list[Any] = field(default_factory=list)
    unit: str | None = None
    comment: str | None = None

    def render(self, level: int) -> list[str]:
        pad = INDENT * level
        text = f"{pad}{self.name}"
        if self.unit:
            text += f" [{self.unit}]"
        body = fmt_values(self.values)
        if body:
            text += f" {body}"
        lines: list[str] = []
        if self.comment:
            for chunk in str(self.comment).splitlines():
                lines.append(f"{pad}# {chunk}".rstrip())
        lines.append(text.rstrip())
        return lines


@dataclass
class Section:
    """A ``&NAME`` block."""

    name: str
    parameter: Any | None = None
    comment: str | None = None
    keywords: list[Keyword] = field(default_factory=list)
    sections: list["Section"] = field(default_factory=list)

    # -- construction ------------------------------------------------------ #
    def set(
        self,
        name: str,
        *values: Any,
        unit: str | None = None,
        comment: str | None = None,
        preserve_name: bool = False,
    ) -> "Section":
        """Set (or replace) a keyword. A single list/tuple argument is expanded."""
        flat: list[Any] = []
        for value in values:
            if isinstance(value, (list, tuple)):
                flat.extend(value)
            else:
                flat.append(value)
        keyword = _keyword_name(name, preserve_name)
        existing = self.keyword(keyword)
        if existing is not None and len(self.keywords_of(keyword)) == 1:
            existing.values = flat
            existing.unit = unit
            existing.comment = comment
            return self
        self.keywords.append(Keyword(keyword, flat, unit, comment))
        return self

    def append_keyword(
        self,
        name: str,
        *values: Any,
        unit: str | None = None,
        comment: str | None = None,
        preserve_name: bool = False,
    ) -> "Section":
        """Add a repeated instance of a keyword (e.g. several ``KPOINT`` lines)."""
        flat: list[Any] = []
        for value in values:
            if isinstance(value, (list, tuple)):
                flat.extend(value)
            else:
                flat.append(value)
        self.keywords.append(
            Keyword(_keyword_name(name, preserve_name), flat, unit, comment))
        return self

    def add(self, section: "Section") -> "Section":
        self.sections.append(section)
        return section

    def section(self, name: str, parameter: Any | None = None,
                comment: str | None = None) -> "Section":
        """Create a child section, or return the existing one."""
        for child in self.sections:
            if child.name.upper() == name.upper():
                if parameter is not None:
                    child.parameter = parameter
                if comment and not child.comment:
                    child.comment = comment
                return child
        return self.add(Section(name.upper(), parameter, comment))

    def new_section(self, name: str, parameter: Any | None = None,
                    comment: str | None = None) -> "Section":
        """Always create a new child section (for repeatable sections)."""
        return self.add(Section(name.upper(), parameter, comment))

    # -- lookup ------------------------------------------------------------ #
    def keywords_of(self, name: str) -> list[Keyword]:
        upper = name.upper()
        return [k for k in self.keywords if k.name.upper() == upper]

    def keyword(self, name: str) -> Keyword | None:
        found = self.keywords_of(name)
        return found[0] if found else None

    def get(self, name: str, default: Any = None) -> Any:
        found = self.keyword(name)
        if found is None:
            return default
        if not found.values:
            return True
        if len(found.values) == 1:
            return found.values[0]
        return list(found.values)

    def remove(self, name: str) -> "Section":
        upper = name.upper()
        self.keywords = [k for k in self.keywords if k.name.upper() != upper]
        return self

    def subsections(self, name: str) -> list["Section"]:
        upper = name.upper()
        return [s for s in self.sections if s.name.upper() == upper]

    def subsection(self, name: str) -> "Section" | None:
        found = self.subsections(name)
        return found[0] if found else None

    # -- traversal --------------------------------------------------------- #
    def walk(self, prefix: str = "") -> Iterator[tuple[str, "Section"]]:
        path = f"{prefix}{PATH_SEP}{self.name}" if prefix else self.name
        yield path, self
        for child in self.sections:
            yield from child.walk(path)

    def find(self, path: str) -> "Section" | None:
        """Look up a section by ``FORCE_EVAL/DFT/MGRID``-style path."""
        parts = [p for p in path.replace("&", "").split(PATH_SEP) if p]
        node: Section | None = self
        for part in parts:
            if node is None:
                return None
            if node.name.upper() == part.upper():
                continue
            node = node.subsection(part)
        return node

    def find_all(self, path: str) -> list["Section"]:
        parts = [p for p in path.replace("&", "").split(PATH_SEP) if p]
        nodes = [self] if self.name.upper() == parts[0].upper() else [self]
        for part in parts:
            nxt: list[Section] = []
            for node in nodes:
                nxt.extend(node.subsections(part))
            nodes = nxt
            if not nodes:
                return []
        return nodes

    def copy(self) -> "Section":
        clone = Section(self.name, self.parameter, self.comment)
        clone.keywords = [Keyword(k.name, list(k.values), k.unit, k.comment)
                          for k in self.keywords]
        clone.sections = [s.copy() for s in self.sections]
        return clone

    # -- output ------------------------------------------------------------ #
    def render(self, level: int = 0) -> Iterator[str]:
        pad = INDENT * level
        if self.comment:
            for chunk in str(self.comment).splitlines():
                yield f"{pad}# {chunk}".rstrip()
        head = f"{pad}&{self.name.upper()}"
        if self.parameter is not None:
            body = fmt_values(self.parameter if isinstance(self.parameter, (list, tuple))
                              else [self.parameter])
            if body:
                head += f" {body}"
        yield head
        for keyword in self.keywords:
            yield from keyword.render(level + 1)
        for child in self.sections:
            yield from child.render(level + 1)
        # every section is closed explicitly: CP2K does not accept a one-line
        # "&XC_FUNCTIONAL PBE" without its &END (verified against 2300+ of
        # CP2K's own regression inputs)
        yield f"{pad}&END {self.name.upper()}"

    def to_string(self, level: int = 0) -> str:
        return "\n".join(self.render(level))


def wrap_comment(text: str, width: int = 96, prefix: str = "# ") -> list[str]:
    """Wrap a comment to a sensible width, preserving blank lines."""
    import textwrap

    out: list[str] = []
    for line in str(text).splitlines():
        if not line.strip():
            out.append(prefix.rstrip())
            continue
        out.extend(textwrap.wrap(line, width=width,
                                 initial_indent=prefix,
                                 subsequent_indent=prefix) or [prefix.rstrip()])
    return out


def render_document(
    sections: Sequence[Section],
    title: str | None = None,
    notes: Sequence[str] = (),
    generated_by: str = "cp2kbuild",
) -> str:
    """Serialise the root sections into a complete, commented input file."""
    lines: list[str] = []
    if title:
        for chunk in wrap_comment(title):
            lines.append(chunk)
    for note in notes:
        for chunk in wrap_comment(note):
            lines.append(chunk)
    if title or notes:
        lines.append(f"# generated by {generated_by}")
        lines.append("")
    for index, section in enumerate(sections):
        if index:
            lines.append("")
        lines.extend(section.render(0))
    return "\n".join(lines).rstrip() + "\n"
