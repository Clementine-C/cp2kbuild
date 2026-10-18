"""Index a CP2K ``data/`` directory: basis sets, pseudopotentials and coverage.

CP2K's basis-set and potential files are flat text databases whose entry lines
look like::

    H  DZVP-MOLOPT-GTH  DZVP-MOLOPT-GTH-q1        # basis set: element name alias
    O  GTH-PBE-q6  GTH-PBE                        # potential: element name alias

Knowing exactly which basis sets and potentials exist for which elements, and
whether their valences match, removes the single most common source of CP2K
start-up errors.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .structures import ELEMENT_SET, canonical_element

BASIS_FILE_PATTERNS = (
    "BASIS_SET",
    "BASIS_MOLOPT",
    "BASIS_MOLOPT_*",
    "BASIS_pob",
    "BASIS_MINBAS",
    "BASIS_MINIX",
    "BASIS_ccGRB_UZH",
    "BASIS_PERIODIC_GW",
    "BASIS_RI_*",
    "BASIS_ADMM*",
    "BASIS_ZIJLSTRA",
    "GTH_BASIS_SETS",
    "EMSL_BASIS_SETS",
    "ALL_BASIS_SETS",
    "HFX_BASIS",
)

POTENTIAL_FILE_PATTERNS = (
    "POTENTIAL",
    "POTENTIAL_UZH*",
    "GTH_POTENTIALS",
    "GTH_SOC_POTENTIALS",
    "ALL_POTENTIALS",
    "ECP_POTENTIALS",
    "ECP_POTENTIALS_*",
    "NLCC_POTENTIALS",
    "HF_POTENTIALS",
    "LnPP1_POTENTIALS",
    "LnPP2_POTENTIALS",
    "AcPP1_POTENTIALS",
    # The companions of BASIS_MOLOPT_AcPP2 / MPC bases: without them the index
    # had the basis set but not its matching pseudopotentials, so a legitimate
    # pair (`U DZVP-MOLOPT-GTH-q11` from BASIS_MOLOPT_AcPP2 + `U GTH-PBE-q11`
    # from AcPP2_POTENTIALS) fell into the "no verified pair" placeholder path.
    "AcPP2_POTENTIALS",
    "MPC_POTENTIALS",
)

RE_ENTRY = re.compile(r"^([A-Za-z]{1,2})\s+(\S+)(.*)$")
RE_VALENCE = re.compile(r"-q(\d+)\b")


@dataclass(frozen=True)
class DataEntry:
    kind: str            # "basis" or "potential"
    file: str            # file the entry lives in
    element: str
    name: str            # the value to put behind BASIS_SET / POTENTIAL
    aliases: tuple[str, ...]
    valence: int | None

    @property
    def names(self) -> tuple[str, ...]:
        """Primary name plus every alias, e.g. GTH-PADE-q4 / GTH-PADE / GTH-LDA."""
        return (self.name, *self.aliases)


def _valence_of(*tokens: str | None) -> int | None:
    for token in tokens:
        if not token:
            continue
        match = RE_VALENCE.search(token)
        if match:
            return int(match.group(1))
    return None


def parse_data_file(path: Path, kind: str) -> list[DataEntry]:
    """Parse one CP2K basis-set or potential database file."""
    entries: list[DataEntry] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return entries

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("@"):
            continue
        match = RE_ENTRY.match(line)
        if not match:
            continue
        element, name, rest = match.group(1), match.group(2), match.group(3)
        # some databases (EMSL_BASIS_SETS) spell elements in capitals
        element = canonical_element(element) or element
        if element not in ELEMENT_SET:
            continue
        # exponent lines and contraction lines never look like this, but guard
        # against a numeric "element"
        if name.replace(".", "").replace("-", "").replace("+", "").isdigit():
            continue
        aliases = tuple(rest.split()) if rest else ()
        entries.append(
            DataEntry(kind, path.name, element, name, aliases,
                      _valence_of(name, *aliases))
        )
    return entries


class DataIndex:
    """All basis sets and potentials found in a CP2K data directory."""

    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory) if directory else None
        self.entries: list[DataEntry] = []
        self.basis_files: list[str] = []
        self.potential_files: list[str] = []
        self.warnings: list[str] = []
        if self.directory:
            self._scan(self.directory)

    # -- construction ------------------------------------------------------ #
    def _scan(self, directory: Path) -> None:
        if not directory.is_dir():
            self.warnings.append(f"data directory not found: {directory}")
            return
        basis_paths = _collect(directory, BASIS_FILE_PATTERNS, exclude_prefixes=("MM_",))
        potential_paths = _collect(directory, POTENTIAL_FILE_PATTERNS,
                                   exclude_prefixes=("MM_",))
        for path in basis_paths:
            found = parse_data_file(path, "basis")
            if found:
                self.basis_files.append(path.name)
                self.entries.extend(found)
        for path in potential_paths:
            found = parse_data_file(path, "potential")
            if found:
                self.potential_files.append(path.name)
                self.entries.extend(found)
        if not self.entries:
            self.warnings.append(f"在 {directory} 里没解析出任何基组或赝势")

    # -- queries ----------------------------------------------------------- #
    def _kind(self, kind: str) -> list[DataEntry]:
        return [e for e in self.entries if e.kind == kind]

    def elements_available(self, kind: str, name: str) -> set[str]:
        """Which elements a given basis set / potential name covers."""
        target = name.upper()
        out: set[str] = set()
        for entry in self._kind(kind):
            if any(n.upper() == target for n in entry.names):
                out.add(entry.element)
        return out

    def covers(self, kind: str, name: str, elements: list[str]) -> list[str]:
        """Elements from ``elements`` that ``name`` does *not* provide."""
        available = self.elements_available(kind, name)
        return [e for e in elements if e not in available]

    def find(self, kind: str, name: str, element: str) -> DataEntry | None:
        target = name.upper()
        for entry in self._kind(kind):
            if entry.element != element:
                continue
            if any(n.upper() == target for n in entry.names):
                return entry
        return None

    def has_entry(self, kind: str, name: str, element: str) -> bool:
        """Whether the name exists for the element (it may carry no ``-qN``)."""
        return self.find(kind, name, element) is not None

    def valence(self, kind: str, name: str, element: str) -> int | None:
        entry = self.find(kind, name, element)
        return entry.valence if entry else None

    def names_for(self, kind: str, element: str) -> list[str]:
        out: list[str] = []
        for entry in self._kind(kind):
            if entry.element != element:
                continue
            for candidate in entry.names:
                if candidate not in out:
                    out.append(candidate)
        return out

    def family(self, kind: str, stem: str) -> list[str]:
        """All primary names that contain ``stem`` (case insensitive)."""
        out: list[str] = []
        needle = stem.upper()
        for entry in self._kind(kind):
            if needle in entry.name.upper() and entry.name not in out:
                out.append(entry.name)
        return out

    def file_for(self, kind: str, name: str, element: str | None = None) -> str | None:
        target = name.upper()
        for entry in self._kind(kind):
            if element and entry.element != element:
                continue
            if any(n.upper() == target for n in entry.names):
                return entry.file
        return None

    def summary(self) -> dict:
        basis_names = sorted({e.name for e in self._kind("basis")})
        potential_names = sorted({e.name for e in self._kind("potential")})
        return {
            "directory": str(self.directory) if self.directory else None,
            "basis_files": sorted(set(self.basis_files)),
            "potential_files": sorted(set(self.potential_files)),
            "basis_entries": len(self._kind("basis")),
            "potential_entries": len(self._kind("potential")),
            "distinct_basis_names": len(basis_names),
            "distinct_potential_names": len(potential_names),
            "elements_with_basis": len({e.element for e in self._kind("basis")}),
            "elements_with_potential": len({e.element for e in self._kind("potential")}),
        }


def _collect(directory: Path, patterns: tuple[str, ...],
             exclude_prefixes: tuple[str, ...] = ()) -> list[Path]:
    seen: dict[str, Path] = {}
    for pattern in patterns:
        for path in sorted(directory.glob(pattern)):
            if not path.is_file():
                continue
            if any(path.name.startswith(p) for p in exclude_prefixes):
                continue
            seen.setdefault(path.name, path)
    return list(seen.values())


# --------------------------------------------------------------------------- #
# discovering a data directory
# --------------------------------------------------------------------------- #
CANDIDATE_SUFFIXES = (
    "data",
    "share/cp2k/data",
    "lib/cp2k/data",
    "cp2k/data",
)

# source checkouts collected under one folder, newest version first
SOURCE_TREES = (r"E:/desktop/cp2k", r"E:/cp2k", r"D:/cp2k", r"C:/cp2k")


def _source_checkouts() -> list[Path]:
    """CP2K source trees such as ``E:/desktop/cp2k/cp2k-2026.2``, newest first."""
    found: list[Path] = []
    for root in SOURCE_TREES:
        folder = Path(root)
        if not folder.is_dir():
            continue
        try:
            found.extend(sorted(
                (child for child in folder.iterdir()
                 if child.is_dir() and child.name.lower().startswith("cp2k-")),
                key=lambda path: path.name, reverse=True))
        except OSError:
            continue
        found.append(folder)
    return found


def discover_data_dir(explicit: str | Path | None = None,
                      extra_roots: tuple[str | Path, ...] = ()) -> Path | None:
    """Find a CP2K ``data/`` directory.

    Order: the explicit argument, ``$CP2K_DATA_DIR``, then a scan of likely
    install roots - including every ``cp2k-*`` source checkout, newest version
    first.
    """
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            candidate = candidate.parent
        if candidate.is_dir():
            return candidate
        # the stored path is gone: fall through to auto-detection rather than
        # silently scanning nothing

    env = os.environ.get("CP2K_DATA_DIR")
    if env and Path(env).is_dir():
        return Path(env)

    roots: list[Path] = [Path(p) for p in extra_roots if p]
    for variable in ("CP2K_ROOT", "CP2K_DIR", "CP2K_HOME"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value))
    roots.extend(_source_checkouts())
    roots.extend([
        Path("C:/cp2k"),
        Path("C:/Program Files/cp2k"),
        Path("/usr/local/cp2k"),
        Path("/opt/cp2k"),
        Path.home() / "cp2k",
    ])

    for root in roots:
        if not root.exists():
            continue
        if root.name.lower() == "data" and (root / "BASIS_MOLOPT").exists():
            return root
        for suffix in CANDIDATE_SUFFIXES:
            candidate = root / suffix
            if (candidate / "BASIS_MOLOPT").exists() or (candidate / "GTH_POTENTIALS").exists():
                return candidate
    return None


@lru_cache(maxsize=8)
def load_index(directory: str | None) -> DataIndex:
    """Cached ``DataIndex`` so repeated lookups stay cheap."""
    return DataIndex(directory)
