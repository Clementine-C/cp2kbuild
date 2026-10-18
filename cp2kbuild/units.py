"""The unit annotations CP2K's input parser accepts.

CP2K resolves a ``[unit]`` annotation *at run time* through ``string_to_unit``
(``common/cp_units.F``).  A string that is not in its table aborts the run before
the first SCF step::

    [ABORT] Unknown unit: a.u.        common/cp_units.F:384

``a.u.`` is the easy mistake - the manual writes units as "a.u." in prose, but
the parser only knows ``AU_F``, ``AU_E``, ``AU_T`` ... (and a bare ``AU`` is
refused on purpose: *"au unit without specifying its kind not accepted"*).  The
safe spelling for an internal (atomic-unit) quantity is **no annotation at all**:
CP2K then uses the unit the keyword itself declares.

The table below is transcribed from the ``CASE`` list of ``string_to_unit``; the
self-test re-checks it against the CP2K source tree when one is available.
"""

from __future__ import annotations

import re

#: Every unit string ``string_to_unit`` knows (upper case, no dots).
ACCEPTED_UNITS: tuple[str, ...] = (
    "AMU", "ANGLE", "ANGSTROM", "ATM",
    "AU_E", "AU_EFIELD", "AU_F", "AU_L", "AU_M", "AU_P", "AU_POT", "AU_T",
    "AU_TEMP",
    "BAR", "BOHR", "DEG", "EFIELD", "ENERGY", "EV", "FORCE", "FS", "GPA",
    "HARTREE", "INTERNAL_CP2K", "JMOL", "JOULE", "J", "K", "K_TEMP", "K_E",
    "KBAR", "KCALMOL", "KEV", "KG", "KJMOL", "LENGTH", "M", "M_E", "MASS",
    "MN", "MNEWTON", "MPA", "N", "NEWTON", "NM", "PA", "PM", "POTENTIAL",
    "PRESSURE", "PS", "RAD", "RY", "S", "TEMPERATURE", "TIME", "VA-1",
    "VOLT_PER_ANGSTROM", "VM-1", "VOLT_PER_M", "VNM-1", "VOLT_PER_NM", "VOLT",
    "WAVENUMBER_E", "WAVENUMBER_T",
)

ACCEPTED_SET = frozenset(ACCEPTED_UNITS)

#: ``AU`` alone is *known* but refused without a kind.
KINDLESS_AU = "AU"

#: The kinds CP2K names in its own error message.
AU_KINDS = ("AU_E", "AU_F", "AU_T", "AU_TEMP", "AU_L", "AU_M", "AU_P", "AU_POT",
            "AU_EFIELD")

# The annotation grammar, from the parsing loop in ``string_to_unit``: a unit
# name, an optional ``^power``, and then more of the same joined by nothing,
# ``*``, ``/`` or spaces - CP2K's own test inputs write ``nm^-2kjmol`` and
# ``angstrom^-2kcalmol``.
_NAMES = "|".join(sorted(ACCEPTED_UNITS + (KINDLESS_AU,), key=len, reverse=True))
_TERM = rf"(?:{_NAMES})(?:\^[-+]?\d+)?"
# terms may be concatenated with nothing at all (CP2K's own tests write
# ``nm^-2kjmol``), or joined by ``*``, ``/`` or spaces
_EXPRESSION = re.compile(rf"^(?:{_TERM})(?:\s*[*/\s]?\s*{_TERM})*$")
_WORDS = re.compile(r"[A-Za-z_]+")


def normalise(unit: str | None) -> str:
    """Upper-case and strip, the way ``string_to_unit`` does."""
    return (unit or "").strip().strip("[]").upper()


def _parts(unit: str) -> list[str]:
    """The unit names inside an annotation, in order."""
    return _WORDS.findall(normalise(unit))


def is_valid(unit: str | None) -> bool:
    return check(unit) is None


def check(unit: str | None) -> str | None:
    """Return an error message for an unacceptable unit, else ``None``.

    ``None`` (no annotation) is always fine - that is the recommended spelling
    for internal quantities, and it is what CP2K's own inputs do.
    """
    if unit is None or not str(unit).strip():
        return None
    token = normalise(unit)
    kinds = _parts(token)
    if KINDLESS_AU in kinds and not any(k.startswith("AU_") for k in kinds):
        return ("CP2K 拒绝不带种类的 `[au]`："
                "`au unit without specifying its kind not accepted, use "
                "(au_e, au_f, au_t, au_temp, au_l, au_m, au_p, au_pot, "
                "au_efield)`（common/cp_units.F）")
    if _EXPRESSION.match(token):
        return None
    unknown = [name for name in kinds if name not in ACCEPTED_SET
               and name != KINDLESS_AU]
    shown = "、".join(f"`{name}`" for name in dict.fromkeys(unknown)) or f"`{token}`"
    return (f"CP2K 的输入解析器不认识单位 {shown}"
            "：`Unknown unit` 会在读输入时就中止（common/cp_units.F:384）。"
            "内部原子单位（a.u.）就**什么都不写**，"
            "CP2K 会用关键字自己声明的单位")


def suggest(unit: str | None) -> list[str]:
    """Close matches from the accepted table, for the hint."""
    token = normalise(unit)
    if not token:
        return []
    stripped = token.rstrip(".")
    out = [u for u in ACCEPTED_UNITS
           if u.startswith(stripped) or stripped.startswith(u)]
    if not out and token == "A.U.":
        out = list(AU_KINDS)
    return out[:6]


def units_in(sections) -> list[tuple[str, str, str]]:
    """``(section path, keyword, unit)`` for every keyword that really has one.

    CP2K only reads ``[...]`` as a unit for keywords that expect a **number**
    (``get_r_val`` in ``input/input_parsing.F``); for a character keyword the
    same brackets are ordinary text - ``ELECTRON_CONFIGURATION [Rn] 6d1 7s2`` is
    CP2K's own spelling and must not be reported as a unit.  So a keyword counts
    only when it carries a numeric value.
    """
    found: list[tuple[str, str, str]] = []

    def numeric(keyword) -> bool:
        for value in getattr(keyword, "values", []):
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                return True
        return False

    def walk(section, path: str) -> None:
        here = f"{path}/{section.name}" if path else section.name
        for keyword in getattr(section, "keywords", []):
            unit = getattr(keyword, "unit", None)
            if unit and numeric(keyword):
                found.append((here, str(keyword.name), str(unit)))
        for child in getattr(section, "sections", []):
            walk(child, here)

    for section in sections:
        walk(section, "")
    return found
