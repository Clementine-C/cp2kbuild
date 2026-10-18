"""Read the CP2K input-reference database and answer questions about it.

The database is produced by ``tools/build_db.py`` from the official manual plus
the Fortran input definitions.  It lets the builder check its own output and
lets ``cp2kbuild keyword`` work fully offline with the manual's own wording.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DEFAULT_DB = Path(__file__).with_name("data") / "manual_db_2025_1.json"

ROOT = "CP2K_INPUT"

_GH_NOISE = re.compile(r"\[\s*Edit on GitHub\s*\]")


def _clean(text: str | None) -> str | None:
    """Drop the manual's own 'Edit on GitHub' breadcrumbs from descriptions."""
    if not text:
        return text
    cleaned = _GH_NOISE.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip() or None


def _normalise(path: str | list[str] | tuple[str, ...]) -> str:
    if isinstance(path, (list, tuple)):
        parts = [str(p) for p in path]
    else:
        parts = [p for p in str(path).replace("&", "").split("/") if p]
    if parts and parts[0].upper() == ROOT:
        parts = parts[1:]
    return "/".join(p.upper() for p in parts)


@dataclass
class KeywordInfo:
    name: str
    path: str
    type: str | None
    default: str | None
    unit: str | None
    aliases: list[str]
    lone_keyword: str | None
    usage: str | None
    valid_values: list[dict]
    description: str | None
    url: str
    deprecated: bool = False

    @property
    def values(self) -> list[str]:
        return [v["value"] for v in self.valid_values]


@dataclass
class SectionInfo:
    name: str
    path: str
    description: str | None
    url: str
    repeatable: bool
    subsections: list[str]
    keywords: list[str]
    source: str | None

    @property
    def repeatable_known(self) -> bool:
        return self.repeatable


class ManualDB:
    """Query wrapper around the generated JSON database."""

    def __init__(self, path: str | Path | None = None):
        location = Path(path) if path else _default_location()
        if not location or not Path(location).is_file():
            raise FileNotFoundError(
                "CP2K manual database not found. Build it with:\n"
                "  python tools/build_db.py --src <cp2k>/src --out cp2kbuild/data/manual_db_2025_1.json"
            )
        payload = json.loads(Path(location).read_text(encoding="utf-8"))
        self.location = Path(location)
        self.manual = payload.get("manual", "https://manual.cp2k.org/")
        self.branch = payload.get("branch")
        self.generated = payload.get("generated_utc")
        self._sections: dict[str, dict] = payload["sections"]
        self._keywords: dict[str, dict] = payload["keywords"]
        self._repeatable_keywords: set[str] = {
            name.upper()
            for name, flag in (payload.get("repeatable_keywords") or {}).items()
            if flag
        }
        # subsections the manual renders as inline anchors instead of pages
        # (the ~150 built-in XC functionals are the main case)
        self._extra_subsections: dict[str, set[str]] = {
            path.upper(): {str(n).upper() for n in names}
            for path, names in (payload.get("extra_subsections") or {}).items()
        }
        # section names that exist in CP2K's own input definitions, used when the
        # manual spells a section differently (NONBONDED/LENNARD vs LENNARD-JONES)
        self._source_section_names: set[str] = {
            str(n).upper() for n in (payload.get("section_names") or [])
        }
        # keyword names declared in CP2K's sources, which the manual may omit
        self._source_keyword_names: set[str] = {
            str(n).upper() for n in (payload.get("keyword_names") or [])
        }

    # -- sections ---------------------------------------------------------- #
    def section(self, path: str | list[str]) -> SectionInfo | None:
        key = f"{ROOT}/{_normalise(path)}" if _normalise(path) else ROOT
        record = self._sections.get(key)
        if record is None:
            return None
        return SectionInfo(
            name=record["name"],
            path=key,
            description=_clean(record.get("description")),
            url=record.get("url", ""),
            repeatable=bool(record.get("repeatable")),
            subsections=record.get("subsections", []),
            keywords=sorted(set(record.get("keywords", []))),
            source=record.get("source"),
        )

    def has_section(self, path: str | list[str]) -> bool:
        return self.section(path) is not None

    def accepts_subsection(self, parent_path: str | list[str], name: str) -> bool:
        """Whether a dynamically listed subsection is valid under the parent."""
        normalised = _normalise(parent_path)
        key = ROOT if not normalised else f"{ROOT}/{normalised}"
        return name.upper() in self._extra_subsections.get(key, set())

    def extra_subsections(self, parent_path: str | list[str]) -> list[str]:
        normalised = _normalise(parent_path)
        key = ROOT if not normalised else f"{ROOT}/{normalised}"
        return sorted(self._extra_subsections.get(key, set()))

    def is_source_section(self, name: str) -> bool:
        """Whether the name is a section in CP2K's own input definitions."""
        return name.upper() in self._source_section_names

    def is_source_keyword(self, name: str) -> bool:
        """Whether the name is a keyword in CP2K's own input definitions."""
        return name.upper() in self._source_keyword_names

    def subsections(self, path: str | list[str]) -> list[str]:
        found = self.section(path)
        return found.subsections if found else []

    def section_url(self, path: str | list[str]) -> str:
        found = self.section(path)
        if found:
            return found.url
        normalised = _normalise(path)
        return f"{self.manual}CP2K_INPUT.html" if not normalised else \
            f"{self.manual}{ROOT}/{normalised}.html"

    # -- keywords ---------------------------------------------------------- #
    def keyword(self, section_path: str | list[str], name: str) -> KeywordInfo | None:
        # ids in the database use dots as separators: CP2K_INPUT.GLOBAL.RUN_TYPE
        normalised = _normalise(section_path)
        prefix = ROOT if not normalised else f"{ROOT}.{normalised.replace('/', '.')}"
        key = f"{prefix}.{name.upper()}"
        record = self._keywords.get(key)
        if record is None:
            return None
        return KeywordInfo(
            name=record["name"],
            path=record.get("section", ""),
            type=record.get("type"),
            default=record.get("default"),
            unit=record.get("unit"),
            aliases=record.get("aliases", []) or [],
            lone_keyword=record.get("lone_keyword"),
            usage=_clean(record.get("usage")),
            valid_values=record.get("valid_values", []) or [],
            description=_clean(record.get("description")),
            url=record.get("url", ""),
            deprecated=bool(record.get("deprecated")),
        )

    def has_keyword(self, section_path: str | list[str], name: str) -> bool:
        return self.keyword(section_path, name) is not None

    def keyword_repeatable(self, section_path: str | list[str], name: str) -> bool | None:
        """``True``/``False`` when the CP2K sources say so, ``None`` when unknown.

        CP2K rejects a keyword that is given twice unless it was declared with
        ``repeats=.TRUE.`` (``SPECIAL_POINT``, ``KPOINT``, ...).
        """
        upper = name.upper()
        if upper in self._repeatable_keywords:
            return True
        if self.keyword(section_path, name) is None:
            return None
        # the keyword exists but no declaration was found: unknown, so stay quiet
        return None

    def keywords(self, section_path: str | list[str]) -> dict[str, KeywordInfo]:
        found = self.section(section_path)
        if not found:
            return {}
        out: dict[str, KeywordInfo] = {}
        for name in found.keywords:
            info = self.keyword(section_path, name)
            if info:
                out[name] = info
        return out

    def default_of(self, section_path: str | list[str], name: str) -> str | None:
        info = self.keyword(section_path, name)
        return info.default if info else None

    def valid_values(self, section_path: str | list[str], name: str) -> list[str]:
        info = self.keyword(section_path, name)
        return info.values if info else []

    # -- search ------------------------------------------------------------ #
    def search(self, needle: str, limit: int = 40) -> list[KeywordInfo]:
        """Free-text search across keyword names and descriptions.

        Results are ordered by name length and then by how shallow the section
        is, so the everyday keyword wins over an obscure namesake
        (``FORCE_EVAL/DFT/MGRID/CUTOFF`` over ``TEST/ERI_MME_TEST/ERI_MME/CUTOFF``).
        """
        pattern = needle.upper()
        hits: list[KeywordInfo] = []
        for record in self._keywords.values():
            haystack = " ".join([
                record["name"],
                record.get("description") or "",
                record.get("usage") or "",
            ]).upper()
            if pattern in haystack and pattern in record["name"].upper():
                info = self.keyword(record.get("section", ""), record["name"])
                if info:
                    hits.append(info)
        hits.sort(key=lambda k: (len(k.name), k.path.count("/"), k.path, k.name))
        return hits[:limit]

    def sections_matching(self, needle: str) -> list[str]:
        pattern = needle.upper()
        return sorted(p for p in self._sections if pattern in p.upper())

    # -- introspection ----------------------------------------------------- #
    def stats(self) -> dict:
        return {
            "location": str(self.location),
            "manual": self.manual,
            "branch": self.branch,
            "generated": self.generated,
            "sections": len(self._sections),
            "keywords": len(self._keywords),
        }


def manual_root() -> str:
    """Base URL of the manual matching the database that is in use.

    The version is encoded in the database file name (``manual_db_2026_2.json``),
    so switching databases also switches every generated manual link - no edit
    needed anywhere else.
    """
    location = _default_location()
    if location is not None:
        match = re.search(r"manual_db_(\d{4})_(\d+)\.json$", location.name)
        if match:
            return (f"https://manual.cp2k.org/"
                    f"cp2k-{match.group(1)}_{match.group(2)}-branch/")
    return "https://manual.cp2k.org/trunk/"


def cp2k_version(branch: str | None) -> str:
    """``cp2k-2026_2-branch`` -> ``2026.2`` (``trunk`` stays ``trunk``)."""
    if not branch:
        return "unknown"
    match = re.fullmatch(r"cp2k-(\d+)_(\d+)(?:-branch)?", branch.strip())
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return branch.strip()


def database_version(path: str | Path) -> str:
    """The CP2K version a database file describes, read from its head.

    The databases are written with ``sort_keys=True``, so ``branch`` is the very
    first field: this reads a few hundred bytes of a 26 MB file.
    """
    try:
        with Path(path).open("rb") as handle:
            head = handle.read(4096).decode("utf-8", errors="ignore")
    except OSError:
        return "unknown"
    match = re.search(r'"branch"\s*:\s*"([^"]+)"', head)
    return cp2k_version(match.group(1) if match else None)


def loaded_version(db: "ManualDB | None") -> str:
    """The CP2K version of the database that is actually in use."""
    return cp2k_version(getattr(db, "branch", None)) if db is not None else "unknown"


def current_version() -> str:
    """The CP2K version of the database that *would* be loaded, without loading it.

    Both front ends only want the version for a label / ``--version``; reading the
    head of the file instead of parsing 26 MB of JSON keeps that cheap (and keeps
    ``cp2kbuild --help`` working when memory is tight).
    """
    location = _default_location()
    return database_version(location) if location else "unknown"


def _newest(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    # the names carry the version (manual_db_2026_2.json), so the last one sorts
    # to the newest
    candidates = sorted(folder.glob("manual_db_*.json"))
    return candidates[-1] if candidates else None


def _default_location() -> Path | None:
    """Find the manual database, preferring the newest version available.

    Order: the ``CP2KBUILD_DB`` override, a ``data/`` folder next to the running
    executable (which is how a second CP2K version can be tried out without
    rebuilding), then whatever was bundled.
    """
    override = os.environ.get("CP2KBUILD_DB")
    if override and Path(override).is_file():
        return Path(override)

    from .paths import app_dir, bundle_root

    beside_exe = _newest(app_dir() / "data")
    if beside_exe is not None:
        return beside_exe

    bundled = _newest(bundle_root() / "data")
    if bundled is not None:
        return bundled

    if DEFAULT_DB.is_file():
        return DEFAULT_DB
    return _newest(DEFAULT_DB.parent)


@lru_cache(maxsize=4)
def load_db(path: str | None = None) -> ManualDB | None:
    """Cached loader; returns ``None`` when no database has been built yet."""
    try:
        return ManualDB(path)
    except FileNotFoundError:
        return None
