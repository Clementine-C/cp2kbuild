"""High-symmetry k-point paths for band structures.

Fractional (crystal) coordinates are used, which is exactly what CP2K's
``B_VECTOR`` units expect.  Paths follow the Setyawan-Curtarolo conventions used
by SeeK-path and most electronic-structure codes.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .structures import Cell

# label -> fractional coordinates, per Bravais lattice
LATTICE_LABELS: dict[str, dict[str, tuple[float, float, float]]] = {
    "cub": {
        "GAMMA": (0.0, 0.0, 0.0), "X": (0.0, 0.5, 0.0),
        "M": (0.5, 0.5, 0.0), "R": (0.5, 0.5, 0.5),
    },
    "fcc": {
        "GAMMA": (0.0, 0.0, 0.0), "X": (0.5, 0.0, 0.5), "W": (0.5, 0.25, 0.75),
        "K": (0.375, 0.375, 0.75), "L": (0.5, 0.5, 0.5), "U": (0.625, 0.625, 0.75),
    },
    "bcc": {
        "GAMMA": (0.0, 0.0, 0.0), "H": (0.5, -0.5, 0.5), "N": (0.0, 0.0, 0.5),
        "P": (0.25, 0.25, 0.25),
    },
    "hex": {
        "GAMMA": (0.0, 0.0, 0.0), "M": (0.5, 0.0, 0.0), "K": (1.0 / 3, 1.0 / 3, 0.0),
        "A": (0.0, 0.0, 0.5), "L": (0.5, 0.0, 0.5), "H": (1.0 / 3, 1.0 / 3, 0.5),
    },
    "tet": {
        "GAMMA": (0.0, 0.0, 0.0), "X": (0.0, 0.5, 0.0), "M": (0.5, 0.5, 0.0),
        "Z": (0.0, 0.0, 0.5), "R": (0.0, 0.5, 0.5), "A": (0.5, 0.5, 0.5),
    },
    "orc": {
        "GAMMA": (0.0, 0.0, 0.0), "X": (0.5, 0.0, 0.0), "S": (0.5, 0.5, 0.0),
        "Y": (0.0, 0.5, 0.0), "Z": (0.0, 0.0, 0.5), "U": (0.5, 0.0, 0.5),
        "R": (0.5, 0.5, 0.5), "T": (0.0, 0.5, 0.5),
    },
    "generic": {
        "GAMMA": (0.0, 0.0, 0.0), "X": (0.5, 0.0, 0.0), "Y": (0.0, 0.5, 0.0),
        "Z": (0.0, 0.0, 0.5), "M": (0.5, 0.5, 0.0), "R": (0.5, 0.5, 0.5),
    },
}

# default path per lattice, "|" separates discontinuous segments
LATTICE_PATHS = {
    "cub": "GAMMA-X-M-GAMMA-R-X|M-R",
    "fcc": "GAMMA-X-W-K-GAMMA-L-U-W-L-K|U-X",
    "bcc": "GAMMA-H-N-GAMMA-P-H|P-N",
    "hex": "GAMMA-M-K-GAMMA-A-L-H-A|L-M|K-H",
    "tet": "GAMMA-X-M-GAMMA-Z-R-A-Z",
    "orc": "GAMMA-X-S-Y-GAMMA-Z-U-R-T-Z",
    "generic": "GAMMA-X-M-GAMMA",
}

ALIASES = {
    "G": "GAMMA", "Γ": "GAMMA", "GAMMA": "GAMMA",
}


@dataclass
class KPath:
    lattice: str
    segments: list[list[tuple[str, tuple[float, float, float]]]]

    @property
    def labels(self) -> list[str]:
        return [label for segment in self.segments for label, _ in segment]

    def total_points(self) -> int:
        return sum(max(1, len(segment) - 1) for segment in self.segments)


def detect_lattice(cell: Cell | None, tolerance: float = 0.02) -> str:
    """Classify a cell as one of the Bravais types the k-path table knows.

    ``tolerance`` is **relative** for the lengths (2 % by default) and the angles
    are compared with half a degree.  An absolute 2 Å window used to call
    ``a=4, b=4, c=6`` cubic (a 50 % longer axis) and to file cells with
    gamma = 91.7° under "cub", which then produced a plausible-looking but wrong
    band path.
    """
    if cell is None:
        return "generic"
    a, b, c = cell.lengths()
    alpha, beta, gamma = cell.angles()

    def close(x: float, y: float) -> bool:
        return abs(x - y) <= tolerance * max(abs(x), abs(y))

    def right(x: float) -> bool:
        return abs(x - 90.0) <= 0.5

    equal_lengths = close(a, b) and close(b, c)
    right_angles = right(alpha) and right(beta) and right(gamma)

    if equal_lengths and right_angles:
        return "cub"
    if equal_lengths and abs(alpha - 60.0) <= 0.5 and abs(beta - 60.0) <= 0.5 \
            and abs(gamma - 60.0) <= 0.5:
        return "fcc"          # primitive fcc cell
    if equal_lengths and abs(alpha - 109.4712) <= 0.5 and abs(beta - 109.4712) <= 0.5 \
            and abs(gamma - 109.4712) <= 0.5:
        return "bcc"          # primitive bcc cell
    if close(a, b) and right(alpha) and right(beta) and abs(gamma - 120.0) <= 0.5:
        return "hex"
    if close(a, b) and right_angles:
        return "tet"
    if right_angles:
        return "orc"
    return "generic"


def parse_label(token: str, lattice: str) -> tuple[str, tuple[float, float, float]]:
    """Resolve a label (``GAMMA``, ``G``, ``X``) or an explicit coordinate triple."""
    text = token.strip()
    if not text:
        raise ValueError("empty k-point label")
    numbers = re.findall(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?", text)
    if len(numbers) >= 3 and not re.search(r"[A-Za-z]{2,}", text.replace("GAMMA", "")):
        coords = tuple(float(v) for v in numbers[:3])
        return f"{coords[0]:g},{coords[1]:g},{coords[2]:g}", coords  # type: ignore[return-value]

    key = text.upper().replace("Γ", "GAMMA")
    resolved = ALIASES.get(key, key)
    table = LATTICE_LABELS.get(lattice, LATTICE_LABELS["generic"])
    if resolved in table:
        return resolved, table[resolved]

    # a coordinate triple written as 0,0,0
    parts = [p for p in re.split(r"[,;]", text) if p.strip()]
    if len(parts) == 3:
        try:
            coords = tuple(float(p) for p in parts)
            return text, coords  # type: ignore[return-value]
        except ValueError:
            pass
    raise ValueError(
        f"unknown k-point label {token!r} for a {lattice} lattice; "
        f"known labels: {', '.join(sorted(table))}"
    )


def build_kpath(expression: str | None, cell: Cell | None,
                lattice: str | None = None) -> KPath:
    """Turn a path expression such as ``GAMMA-X-M-GAMMA`` into resolved segments."""
    kind = (lattice or detect_lattice(cell)).lower()
    if kind not in LATTICE_LABELS:
        kind = "generic"
    text = expression or LATTICE_PATHS.get(kind, LATTICE_PATHS["generic"])

    segments: list[list[tuple[str, tuple[float, float, float]]]] = []
    for chunk in text.split("|"):
        points = [p for p in re.split(r"[\s\-–>]+", chunk.strip()) if p]
        if len(points) < 2:
            continue
        segments.append([parse_label(p, kind) for p in points])
    if not segments:
        raise ValueError(f"could not parse k-point path {text!r}")
    return KPath(kind, segments)


def distribute_points(kpath: KPath, total: int) -> list[int]:
    """Split ``total`` sample points over the segments by path length."""
    lengths = []
    for segment in kpath.segments:
        length = 0.0
        for (_, start), (_, end) in zip(segment, segment[1:]):
            length += math.dist(start, end)
        lengths.append(max(length, 1e-9))
    total = max(total, len(kpath.segments))
    raw = [total * length / sum(lengths) for length in lengths]
    counts = [max(2, int(round(value))) for value in raw]
    return counts
