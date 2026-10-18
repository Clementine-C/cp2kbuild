"""Structure readers and geometry helpers.

Supports the formats people actually have at hand: XYZ (plain and extended),
PDB, VASP POSCAR/CONTCAR, CIF (with symmetry operators applied) and the
``&COORD`` block of an existing CP2K input file - including a previous run's
``<PROJECT>-1.restart``, which is a complete input and therefore carries both
the coordinates and the cell (that is what makes task relays easy).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

ELEMENTS = (
    "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni "
    "Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe "
    "Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au "
    "Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf "
    "Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split()

ELEMENT_SET = set(ELEMENTS)
_CANONICAL = {e.lower(): e for e in ELEMENTS}

MASSES = {
    "H": 1.008, "He": 4.0026, "Li": 6.94, "Be": 9.0122, "B": 10.81, "C": 12.011,
    "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180, "Na": 22.990,
    "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974, "S": 32.06,
    "Cl": 35.45, "Ar": 39.948, "K": 39.098, "Ca": 40.078, "Sc": 44.956,
    "Ti": 47.867, "V": 50.942, "Cr": 51.996, "Mn": 54.938, "Fe": 55.845,
    "Co": 58.933, "Ni": 58.693, "Cu": 63.546, "Zn": 65.38, "Ga": 69.723,
    "Ge": 72.630, "As": 74.922, "Se": 78.971, "Br": 79.904, "Kr": 83.798,
    "Rb": 85.468, "Sr": 87.62, "Y": 88.906, "Zr": 91.224, "Nb": 92.906,
    "Mo": 95.95, "Ru": 101.07, "Rh": 102.91, "Pd": 106.42, "Ag": 107.87,
    "Cd": 112.41, "In": 114.82, "Sn": 118.71, "Sb": 121.76, "Te": 127.60,
    "I": 126.90, "Xe": 131.29, "Cs": 132.91, "Ba": 137.33, "La": 138.91,
    "Ce": 140.12, "Pr": 140.91, "Nd": 144.24, "Sm": 150.36, "Eu": 151.96,
    "Gd": 157.25, "Tb": 158.93, "Dy": 162.50, "Ho": 164.93, "Er": 167.26,
    "Tm": 168.93, "Yb": 173.05, "Lu": 174.97, "Hf": 178.49, "Ta": 180.95,
    "W": 183.84, "Re": 186.21, "Os": 190.23, "Ir": 192.22, "Pt": 195.08,
    "Au": 196.97, "Hg": 200.59, "Tl": 204.38, "Pb": 207.2, "Bi": 208.98,
    "Th": 232.04, "U": 238.03,
    "default": 1.0,
}


def canonical_element(token: str) -> str | None:
    """``'c'``, ``'C'``, ``'CA'`` -> ``'C'``/``'Ca'``; ``None`` if not an element."""
    if not token:
        return None
    clean = re.sub(r"[^A-Za-z]", "", token)
    if clean in ELEMENT_SET:
        return clean
    lowered = clean.lower()
    if lowered in _CANONICAL:
        return _CANONICAL[lowered]
    return None


#: Common AMBER atom-type names -> element.
#:
#: A CP2K ``&COORD`` block written by an **AMBER** run carries the *atom names of
#: the topology* - ``CA3``, ``OH2``, ``HO3``, ``NT1`` … - and those are not
#: element symbols at all: ``CA3`` is an aromatic carbon (not calcium), ``HO3``
#: is a hydroxyl hydrogen (not holmium).  Reading them as elements both invents
#: wrong elements *and* drops every name the element table does not know, which
#: is how a 179-atom restart became a 52-atom "Ca6BC21O21Ho3" molecule.
#:
#: The longest prefix wins, so the three-letter ion names are matched first.
AMBER_ATOM_TYPES: dict[str, str] = {
    # monatomic ions carry their charge in the AMBER name
    "NA+": "Na", "K+": "K", "LI+": "Li", "RB+": "Rb", "CS+": "Cs",
    "MG2+": "Mg", "CA2+": "Ca", "ZN2+": "Zn", "FE2+": "Fe", "FE3+": "Fe",
    "MN2+": "Mn", "CU+": "Cu", "CU2+": "Cu", "CL-": "Cl", "BR-": "Br",
    "F-": "F", "I-": "I",
    # the families (longest prefix wins, so CA3 -> CA -> carbon)
    "CA": "C", "CT": "C", "CM": "C", "CK": "C", "CW": "C", "CB": "C",
    "CN": "C", "CQ": "C", "CR": "C", "CV": "C", "CX": "C", "CO": "C", "C*": "C",
    "OH": "O", "OS": "O", "OW": "O", "O2": "O", "OP": "O",
    "HO": "H", "HS": "H", "HC": "H", "HA": "H", "HN": "H", "HP": "H", "HW": "H",
    "NT": "N", "N2": "N", "N3": "N", "NA": "N", "NB": "N", "NC": "N", "ND": "N",
    "NE": "N", "NH": "N", "NO": "N", "N*": "N",
    "SH": "S", "SM": "S", "SS": "S", "S*": "S",
    "CL": "Cl", "BR": "Br",
}


def element_from_label(label: str, amber: bool = False) -> str | None:
    """The element behind a ``&COORD`` label, or ``None``.

    CP2K writes the **kind name** in the first column, and a kind name is a
    chemistry-free string: for a force-field run it is the atom name from the
    topology.  So the resolution is layered, most reliable first:

    1. the label *is* an element symbol written the way elements are written
       (``Ca``, ``Cl``, ``C``) - this is what our own inputs and any DFT run use;
    2. inside an AMBER input, the atom-type table above (``CA3`` -> C);
    3. otherwise the leading letters (``OH2`` -> O, ``HC1`` -> H).
    """
    text = (label or "").strip()
    if not text:
        return None
    if text in ELEMENT_SET:
        return text
    if amber:
        cleaned = re.sub(r"[^A-Za-z0-9*+-]", "", text).upper()
        # longest match first: the whole name (an ion, "NA+") beats a prefix
        for size in range(len(cleaned), 0, -1):
            if cleaned[:size] in AMBER_ATOM_TYPES:
                return AMBER_ATOM_TYPES[cleaned[:size]]
    if text.capitalize() in ELEMENT_SET:
        return text.capitalize()
    letters = re.sub(r"[^A-Za-z]", "", text)
    for size in (2, 1):
        found = canonical_element(letters[:size])
        if found:
            return found
    return None


def mass_of(symbol: str) -> float:
    return MASSES.get(symbol, MASSES["default"])


@dataclass
class Atom:
    symbol: str
    x: float
    y: float
    z: float
    label: str | None = None

    @property
    def position(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)


@dataclass
class Cell:
    """Lattice vectors in angstrom."""

    a: tuple[float, float, float]
    b: tuple[float, float, float]
    c: tuple[float, float, float]

    @classmethod
    def from_lengths_angles(cls, a: float, b: float, c: float,
                            alpha: float = 90.0, beta: float = 90.0,
                            gamma: float = 90.0) -> "Cell":
        """Standard crystallographic -> Cartesian conversion (a along x)."""
        ar, br, gr = (math.radians(v) for v in (alpha, beta, gamma))
        va = (a, 0.0, 0.0)
        vb = (b * math.cos(gr), b * math.sin(gr), 0.0)
        cx = c * math.cos(br)
        cy = c * (math.cos(ar) - math.cos(br) * math.cos(gr)) / math.sin(gr)
        cz2 = c * c - cx * cx - cy * cy
        cz = math.sqrt(cz2) if cz2 > 0 else 0.0
        return cls(va, vb, (cx, cy, cz))

    @classmethod
    def cubic(cls, a: float) -> "Cell":
        return cls((a, 0.0, 0.0), (0.0, a, 0.0), (0.0, 0.0, a))

    @classmethod
    def orthorhombic(cls, a: float, b: float, c: float) -> "Cell":
        return cls((a, 0.0, 0.0), (0.0, b, 0.0), (0.0, 0.0, c))

    def lengths(self) -> tuple[float, float, float]:
        return tuple(_norm(v) for v in (self.a, self.b, self.c))  # type: ignore[return-value]

    def angles(self) -> tuple[float, float, float]:
        return (math.degrees(_angle(self.b, self.c)),
                math.degrees(_angle(self.a, self.c)),
                math.degrees(_angle(self.a, self.b)))

    def volume(self) -> float:
        return abs(_dot(self.a, _cross(self.b, self.c)))

    def is_orthogonal(self, tol: float = 1e-6) -> bool:
        return all(abs(_dot(u, v)) < tol
                   for u, v in ((self.a, self.b), (self.a, self.c), (self.b, self.c)))

    def to_cartesian(self, frac: Sequence[float]) -> tuple[float, float, float]:
        return tuple(  # type: ignore[return-value]
            frac[0] * self.a[i] + frac[1] * self.b[i] + frac[2] * self.c[i]
            for i in range(3)
        )

    def to_fractional(self, cart: Sequence[float]) -> tuple[float, float, float]:
        matrix = [self.a, self.b, self.c]
        return _solve3(matrix, cart)

    def abc_keyword(self) -> list[float]:
        """The three lattice lengths, for CP2K's ``ABC`` keyword."""
        return list(self.lengths())

    def as_matrix(self) -> list[list[float]]:
        return [list(self.a), list(self.b), list(self.c)]

    def replicate(self, na: int = 1, nb: int = 1, nc: int = 1) -> "Cell":
        return Cell(tuple(v * na for v in self.a),      # type: ignore[arg-type]
                    tuple(v * nb for v in self.b),      # type: ignore[arg-type]
                    tuple(v * nc for v in self.c))      # type: ignore[arg-type]


def _norm(v: Sequence[float]) -> float:
    return math.sqrt(sum(c * c for c in v))


def _dot(u: Sequence[float], v: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(u, v))


def _cross(u: Sequence[float], v: Sequence[float]) -> tuple[float, float, float]:
    return (u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def _angle(u: Sequence[float], v: Sequence[float]) -> float:
    nu, nv = _norm(u), _norm(v)
    if nu == 0 or nv == 0:
        return 0.0
    cos = max(-1.0, min(1.0, _dot(u, v) / (nu * nv)))
    return math.acos(cos)


def _solve3(matrix: Sequence[Sequence[float]],
            rhs: Sequence[float]) -> tuple[float, float, float]:
    """Solve a 3x3 linear system by Cramer's rule."""
    det = _det3(matrix)
    if abs(det) < 1e-12:
        raise ValueError("singular cell matrix")
    out = []
    for column in range(3):
        replaced = [[rhs[i] if j == column else matrix[i][j] for j in range(3)]
                    for i in range(3)]
        out.append(_det3(replaced) / det)
    return tuple(out)  # type: ignore[return-value]


def _det3(m: Sequence[Sequence[float]]) -> float:
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


@dataclass
class Structure:
    atoms: list[Atom] = field(default_factory=list)
    cell: Cell | None = None
    periodic: str | None = None
    comment: str = ""
    source: str | None = None
    warnings: list[str] = field(default_factory=list)

    # -- basic properties -------------------------------------------------- #
    def copy(self) -> "Structure":
        """An independent copy.

        ``build`` applies vacuum/cell/centring to the structure it is given;
        without a copy that would edit the caller's object (a GUI tab kept the
        vacuum box of a previous preview, so setting the vacuum back to 0 had
        no effect at all).
        """
        return Structure(
            atoms=[Atom(a.symbol, a.x, a.y, a.z, a.label) for a in self.atoms],
            cell=self.cell,
            periodic=self.periodic,
            comment=self.comment,
            source=self.source,
            warnings=list(self.warnings),
        )

    @property
    def elements(self) -> list[str]:
        seen: list[str] = []
        for atom in self.atoms:
            if atom.symbol not in seen:
                seen.append(atom.symbol)
        return seen

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for atom in self.atoms:
            out[atom.symbol] = out.get(atom.symbol, 0) + 1
        return out

    def formula(self) -> str:
        counts = self.counts()
        ordered = [e for e in self.elements]
        parts = []
        for element in ordered:
            n = counts[element]
            parts.append(element if n == 1 else f"{element}{n}")
        return "".join(parts)

    def mass(self) -> float:
        return sum(mass_of(a.symbol) for a in self.atoms)

    def n_atoms(self) -> int:
        return len(self.atoms)

    def bounding_box(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if not self.atoms:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        lo = [min(getattr(a, axis) for a in self.atoms) for axis in "xyz"]
        hi = [max(getattr(a, axis) for a in self.atoms) for axis in "xyz"]
        return tuple(lo), tuple(hi)  # type: ignore[return-value]

    def extent(self) -> tuple[float, float, float]:
        lo, hi = self.bounding_box()
        return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])

    def center(self) -> tuple[float, float, float]:
        lo, hi = self.bounding_box()
        return tuple((lo[i] + hi[i]) / 2.0 for i in range(3))  # type: ignore[return-value]

    def is_periodic(self) -> bool:
        return bool(self.periodic) and self.periodic.upper() not in ("NONE", "0", "")

    def vacuum_fraction(self) -> float:
        """Fraction of the cell volume that is empty (crude, molecular-box test)."""
        if not self.cell:
            return 1.0
        volume = self.cell.volume()
        if volume <= 0:
            return 1.0
        lo, hi = self.bounding_box()
        packed = max(hi[0] - lo[0], 1e-6) * max(hi[1] - lo[1], 1e-6) * max(hi[2] - lo[2], 1e-6)
        return max(0.0, 1.0 - packed / volume)

    # -- manipulation ------------------------------------------------------- #
    def translate_to_origin(self) -> "Structure":
        cx, cy, cz = self.center()
        for atom in self.atoms:
            atom.x -= cx
            atom.y -= cy
            atom.z -= cz
        return self

    def wrap_into_cell(self) -> "Structure":
        if not self.cell:
            return self
        for atom in self.atoms:
            frac = self.cell.to_fractional(atom.position)
            frac = [f - math.floor(f) for f in frac]
            atom.x, atom.y, atom.z = self.cell.to_cartesian(frac)
        return self

    def new_box(self, vacuum: float, keep_center: bool = True) -> Cell:
        """An orthorhombic cell around the molecule with ``vacuum`` angstrom padding."""
        lo, hi = self.bounding_box()
        lengths = [max(hi[i] - lo[i], 1e-3) + 2.0 * vacuum for i in range(3)]
        return Cell.orthorhombic(*lengths)

    def center_in_cell(self) -> "Structure":
        if not self.cell:
            return self
        frac = self.cell.to_fractional(self.center())
        shift = [0.5 - f for f in frac]
        for atom in self.atoms:
            atom.x += shift[0] * self.cell.a[0] + shift[1] * self.cell.b[0] + shift[2] * self.cell.c[0]
            atom.y += shift[0] * self.cell.a[1] + shift[1] * self.cell.b[1] + shift[2] * self.cell.c[1]
            atom.z += shift[0] * self.cell.a[2] + shift[1] * self.cell.b[2] + shift[2] * self.cell.c[2]
        return self

    def to_xyz(self, comment: str | None = None) -> str:
        lines = [str(self.n_atoms()), comment or self.comment or self.formula()]
        for atom in self.atoms:
            lines.append(f"{atom.symbol:<3s} {atom.x:16.8f} {atom.y:16.8f} {atom.z:16.8f}")
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# readers
# --------------------------------------------------------------------------- #
_EXTXYZ_CELL = re.compile(r'(?:Lattice|cell)\s*=\s*"([^"]+)"')


def read_xyz(text: str, source: str | None = None) -> Structure:
    lines = text.splitlines()
    if not lines:
        raise ValueError("empty XYZ file")
    try:
        count = int(lines[0].split()[0])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"first line of XYZ is not an atom count: {lines[0]!r}") from exc
    comment = lines[1] if len(lines) > 1 else ""
    atoms: list[Atom] = []
    for line in lines[2:2 + count]:
        parts = line.split()
        if len(parts) < 4:
            continue
        symbol = canonical_element(parts[0])
        if symbol is None:
            raise ValueError(f"unknown element {parts[0]!r} in XYZ")
        atoms.append(Atom(symbol, float(parts[1]), float(parts[2]), float(parts[3])))
    if len(atoms) != count:
        raise ValueError(f"XYZ declared {count} atoms but {len(atoms)} were parsed")

    cell = None
    match = _EXTXYZ_CELL.search(comment)
    if match:
        values = [float(v) for v in match.group(1).split()]
        if len(values) == 9:
            cell = Cell(tuple(values[0:3]), tuple(values[3:6]), tuple(values[6:9]))  # type: ignore[arg-type]
        elif len(values) == 3:
            cell = Cell.orthorhombic(*values)
    return Structure(atoms, cell, "XYZ" if cell else None, comment, source)


def _pdb_element(name_field: str, element_field: str | None) -> str | None:
    """The element of one PDB ``ATOM``/``HETATM`` record.

    The element column is missing in most older files (``gly.pdb`` stops at
    column 54), so the atom-name field has to be decoded the way the PDB format
    defines it:

    * two-letter elements are written **left-justified** (``CA  `` is calcium);
    * a name that starts with a blank carries a one-letter element
      (`` CA `` is the alpha carbon of a protein, **not** calcium - reading it as
      calcium both invents an element and drops the ten hydrogens named
      ``1HH3``/``CH3``/``2HA``, because those names are not element symbols);
    * a leading digit is a hydrogen numbering (``1HH3`` -> H).
    """
    symbol = canonical_element(element_field or "")
    if symbol:
        return symbol
    stripped = (name_field or "").strip()
    if not stripped:
        return None
    core = stripped.lstrip("0123456789")
    if not core:
        return None
    if name_field[:1].strip():                       # left-justified name
        two = canonical_element(core[:2])
        if two and len(core.strip()) > 1:
            return two
    one = canonical_element(core[0])
    if one:
        return one
    return canonical_element(core[:2])


def read_pdb(text: str, source: str | None = None) -> Structure:
    atoms: list[Atom] = []
    unnamed: list[str] = []
    cell = None
    for line in text.splitlines():
        record = line[:6].strip().upper()
        if record in ("ATOM", "HETATM"):
            name = line[12:16].strip()
            element = line[76:78].strip() if len(line) >= 78 else None
            symbol = _pdb_element(line[12:16], element)
            if symbol is None:
                # never drop the atom: an unresolved name keeps the record and is
                # reported (dropping it silently turns 19 atoms into 9)
                unnamed.append(name or line[12:16])
                symbol = name or "X"
            atoms.append(Atom(symbol, float(line[30:38]), float(line[38:46]),
                              float(line[46:54]), name))
        elif record == "CRYST1":
            a, b, c = (float(line[6:15]), float(line[15:24]), float(line[24:33]))
            alpha, beta, gamma = (float(line[33:40]), float(line[40:47]), float(line[47:54]))
            cell = Cell.from_lengths_angles(a, b, c, alpha, beta, gamma)
    if not atoms:
        raise ValueError("no ATOM/HETATM records found in PDB")
    structure = Structure(atoms, cell, "XYZ" if cell else None, "from PDB", source)
    if unnamed:
        shown = "、".join(dict.fromkeys(unnamed))
        structure.warnings.append(
            f"有 {len(unnamed)} 个原子名既不是元素符号、又没有元素列可以兜底"
            f"（{shown}）——用这个结构之前先核对元素归属")
    return structure


def read_poscar(text: str, source: str | None = None) -> Structure:
    lines = text.splitlines()
    if len(lines) < 8:
        raise ValueError("POSCAR too short")
    comment = lines[0].strip()
    scale = float(lines[1].split()[0])
    vectors = []
    for i in range(2, 5):
        vals = [float(v) for v in lines[i].split()[:3]]
        vectors.append(tuple(v * scale for v in vals))
    cell = Cell(*vectors)  # type: ignore[arg-type]

    index = 5
    tokens = lines[index].split()
    if all(canonical_element(t) for t in tokens):
        symbols = [canonical_element(t) for t in tokens]  # type: ignore[list-item]
        index += 1
        counts = [int(v) for v in lines[index].split()]
    else:
        counts = [int(v) for v in tokens]
        symbols = None
    index += 1

    if lines[index].strip()[:1] in ("S", "s"):
        index += 1
    mode = lines[index].strip()[:1].lower()
    index += 1
    direct = mode in ("d", "")

    if symbols is None:
        symbols = ["Xx"] * len(counts)
        raise ValueError("POSCAR without element symbols (VASP 4 format) is not supported")

    atoms_with_elements = []
    for symbol, count in zip(symbols, counts):
        atoms_with_elements.extend([symbol] * count)

    atoms: list[Atom] = []
    for i, symbol in enumerate(atoms_with_elements):
        parts = [float(v) for v in lines[index + i].split()[:3]]
        if direct:
            x, y, z = cell.to_cartesian(parts)
        else:
            x, y, z = (parts[0] * scale, parts[1] * scale, parts[2] * scale)
        atoms.append(Atom(symbol, x, y, z))
    return Structure(atoms, cell, "XYZ", comment, source)


#: Characters a CIF symmetry operator may contain.  The comma has to be in here:
#: the identity operator is literally ``x,y,z``, so leaving it out made the guard
#: reject the reader's own fallback operator (every fractional CIF raised).
_SYMOP_OK = re.compile(r"^[xyz0-9+\-*/()., ^]+$")


def _apply_symop(op: str, frac: Sequence[float]) -> tuple[float, float, float]:
    cleaned = op.replace(" ", "").lower()
    if not _SYMOP_OK.match(cleaned):
        raise ValueError(f"refusing to evaluate symmetry operator {op!r}")
    # CIF writes implicit products ("2x", "1/2x"); Python needs the operator
    cleaned = re.sub(r"(\d)\s*([xyz])", r"\1*\2", cleaned)
    parts = cleaned.split(",")
    if len(parts) != 3:
        raise ValueError(f"bad symmetry operator {op!r}")
    env = {"x": frac[0], "y": frac[1], "z": frac[2]}
    return tuple(  # type: ignore[return-value]
        float(eval(p, {"__builtins__": {}}, env))  # noqa: S307 - charset-validated
        for p in parts
    )


def _symop_from_row(row: str) -> str | None:
    """The symmetry operator inside one CIF row, or ``None``.

    A row is either the operator alone (``x,y,z``) or an index in front of it
    (``1  x,y,z``); quotes have already been removed by :func:`_split_cif_row`.
    """
    for token in reversed(_split_cif_row(row)):
        if "," in token and re.fullmatch(r"[xyzXYZ0-9+\-*/()., ]+", token):
            return token.strip()
    return None


def read_cif(text: str, source: str | None = None) -> Structure:
    cell_params: dict[str, float] = {}
    symops: list[str] = []
    atoms_frac: list[tuple[str, tuple[float, float, float]]] = []
    atoms_cart: list[tuple[str, tuple[float, float, float]]] = []
    unresolved: list[str] = []

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index]
        line = raw.strip()
        index += 1
        if line.startswith("_cell_"):
            parts = line.split()
            if len(parts) >= 2:
                value = parts[1].split("(")[0]
                try:
                    cell_params[parts[0].lower()] = float(value)
                except ValueError:
                    pass
        elif line.lower().startswith(("_symmetry_equiv_pos_as_xyz",
                                      "_space_group_symop_operation_xyz")):
            inline = line.split(None, 1)
            if len(inline) == 2:
                found = _symop_from_row(inline[1])
                if found:
                    symops.append(found)
            # ... and the operators listed right after it, one per line
            while index < len(lines):
                found = _symop_from_row(lines[index])
                if found is None:
                    break
                symops.append(found)
                index += 1
        elif line.lower().startswith("loop_"):
            headers: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("_"):
                headers.append(lines[index].strip().lower())
                index += 1
            if not headers:
                continue
            if any("_symmetry_equiv_pos_as_xyz" in h
                   or "_space_group_symop_operation_xyz" in h for h in headers):
                # the standard CIF form: a loop_ of operator rows (optionally with
                # a site id in front).  Without this branch the operators were
                # read as a *header* and then thrown away, so a real CIF silently
                # came out with the identity only - or, before the comma was
                # allowed, with an exception.
                while index < len(lines):
                    row = lines[index].strip()
                    if not row or row.startswith(("_", "loop_", "data_", "#")):
                        break
                    index += 1
                    found = _symop_from_row(row)
                    if found:
                        symops.append(found)
            elif any("_atom_site_fract_x" in h or "_atom_site_cartn_x" in h
                     for h in headers):
                while index < len(lines):
                    row = lines[index].strip()
                    if not row or row.startswith(("_", "loop_", "data_", "#")):
                        break
                    tokens = _split_cif_row(row)
                    index += 1
                    if len(tokens) < len(headers):
                        continue
                    record = dict(zip(headers, tokens))
                    fract = ("_atom_site_fract_x", "_atom_site_fract_y",
                             "_atom_site_fract_z")
                    cart = ("_atom_site_cartn_x", "_atom_site_cartn_y",
                            "_atom_site_cartn_z")
                    has_fract = any(key in record for key in fract)
                    if not has_fract and not any(key in record for key in cart):
                        # a loop that shares the "_atom_site_" prefix but carries
                        # no position (aniso_U, occupancy, ...): not an atom row
                        continue
                    label = (record.get("_atom_site_type_symbol")
                             or record.get("_atom_site_label", ""))
                    symbol = (canonical_element(label)
                              or element_from_label(record.get("_atom_site_label", "")))
                    if symbol is None:
                        # keep the row: dropping an atom silently shrinks the
                        # system, and the caller gets told instead
                        symbol = label.strip() or "X"
                        unresolved.append(label.strip() or "?")
                    if has_fract:
                        values = [_cif_number(record.get(key)) for key in fract]
                        if any(value is None for value in values):
                            raise ValueError(
                                f"CIF atom row {label!r} has no usable fractional "
                                f"coordinates ({' '.join(str(record.get(k)) for k in fract)})")
                        atoms_frac.append((symbol, tuple(values)))  # type: ignore[arg-type]
                    else:
                        values = [_cif_number(record.get(key)) for key in cart]
                        if any(value is None for value in values):
                            raise ValueError(
                                f"CIF atom row {label!r} has no usable cartesian "
                                f"coordinates ({' '.join(str(record.get(k)) for k in cart)})")
                        atoms_cart.append((symbol, tuple(values)))  # type: ignore[arg-type]
            else:
                while index < len(lines):
                    row = lines[index].strip()
                    if not row or row.startswith(("_", "loop_", "data_", "#")):
                        break
                    index += 1

    try:
        cell = Cell.from_lengths_angles(
            cell_params["_cell_length_a"], cell_params["_cell_length_b"],
            cell_params["_cell_length_c"], cell_params.get("_cell_angle_alpha", 90.0),
            cell_params.get("_cell_angle_beta", 90.0),
            cell_params.get("_cell_angle_gamma", 90.0),
        )
    except KeyError as exc:
        raise ValueError(f"CIF is missing a cell parameter: {exc}") from exc

    atoms: list[Atom] = []
    if atoms_frac:
        ops = symops or ["x,y,z"]
        seen: set[tuple[str, int, int, int]] = set()
        for symbol, frac in atoms_frac:
            for op in ops:
                shifted = _apply_symop(op, frac)
                wrapped = tuple(f - math.floor(f) for f in shifted)
                key = (symbol, *(round(f * 1000) for f in wrapped))
                if key in seen:
                    continue
                seen.add(key)
                x, y, z = cell.to_cartesian(wrapped)
                atoms.append(Atom(symbol, x, y, z))
    else:
        for symbol, cart in atoms_cart:
            atoms.append(Atom(symbol, *cart))

    if not atoms:
        raise ValueError("CIF contains no usable _atom_site_ records")

    structure = Structure(atoms, cell, "XYZ", "from CIF", source)
    if symops:
        structure.warnings.append(
            f"已按 CIF 里的 {len(symops)} 个对称操作展开")
    if unresolved:
        shown = "、".join(dict.fromkeys(unresolved))
        structure.warnings.append(
            f"CIF 里有 {len(unresolved)} 个原子标签不是元素符号"
            f"（{shown}）——核对一下元素归属")
    return structure


def _cif_number(token: str | None) -> float | None:
    """One CIF numeric field -> float, or ``None`` when it carries no value.

    CIF writes the standard uncertainty in parentheses (``0.0986(3)``) and uses
    ``.``/``?`` for "no value".  ``float()`` refuses both, and every atom whose
    coordinates carried an esd used to be dropped on that exception - which is
    how a Hg-P-I structure came out as four mercury atoms.
    """
    if token is None:
        return None
    text = re.sub(r"\(.*?\)", "", str(token).strip())
    if not text or text in (".", "?"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _split_cif_row(row: str) -> list[str]:
    tokens: list[str] = []
    current = ""
    quote: str | None = None
    for char in row:
        if quote:
            if char == quote:
                tokens.append(current)
                current = ""
                quote = None
            else:
                current += char
        elif char in "'\"":
            quote = char
        elif char.isspace():
            if current:
                tokens.append(current)
                current = ""
        else:
            current += char
    if current:
        tokens.append(current)
    return tokens


#: Factors to angstrom for the length units CP2K accepts in ``&COORD``/``&CELL``
#: (``string_to_unit``).  ``BOHR`` and ``AU_L`` are the same length; a bare
#: ``AU`` is refused on purpose by CP2K ("au unit without specifying its kind"),
#: so it is not accepted here either.
_LENGTH_TO_ANGSTROM = {
    "ANGSTROM": 1.0,
    "ANGSTROMS": 1.0,
    "BOHR": 0.529177210903,
    "AU_L": 0.529177210903,
    "NM": 10.0,
    "PM": 0.01,
    "M": 1.0e10,
}

# CP2K evaluates a coordinate field as an expression - its own manual names
# "2/3", "0.3*COS(60)" and "-SQRT(3)/2" - and the trigonometric functions take
# **degrees** there.
_COORD_EXPR = re.compile(r"^[0-9eE+\-*/().^A-Za-z_ ]+$")
_COORD_FUNCS = {
    "SQRT": math.sqrt,
    "COS": lambda v: math.cos(math.radians(v)),
    "SIN": lambda v: math.sin(math.radians(v)),
    "TAN": lambda v: math.tan(math.radians(v)),
    "EXP": math.exp,
    "LOG": math.log,
    "LOG10": math.log10,
}

#: Keywords that may sit inside ``&COORD``/``&CELL`` and are not atom rows.
_COORD_KEYWORDS = ("SCALED", "LEUC", "UNIT", "SCALED_VELOCITIES")


def _length_factor(unit: str, where: str) -> float:
    """Angstrom factor for a ``UNIT`` keyword; loud error for anything else.

    Silently reading bohr columns as angstrom scales a structure by 1.89 - the
    Li2 test input would come out with a 5.35 A bond instead of 2.83 A.
    """
    key = (unit or "").strip().strip("[]").upper()
    if key in _LENGTH_TO_ANGSTROM:
        return _LENGTH_TO_ANGSTROM[key]
    if key == "AU":
        raise ValueError(
            f"{where}: CP2K refuses a bare `au` (it wants the kind, `au_l`)")
    raise ValueError(
        f"{where}: `{unit}` is not a length unit this reader knows "
        f"(knows: {', '.join(sorted(_LENGTH_TO_ANGSTROM))}) - refusing to "
        "guess, since the wrong guess rescales the whole structure")


def _coord_value(token: str, where: str) -> float:
    """One coordinate field -> float, the way CP2K reads it.

    A plain number passes straight through.  Anything else has to be an
    expression CP2K would evaluate; a field that cannot be evaluated raises,
    because the alternative used to be "drop the atom silently" - which shrinks
    the system without saying so.
    """
    text = (token or "").strip()
    try:
        return float(text)
    except ValueError:
        pass
    upper = text.upper()
    if not upper or not _COORD_EXPR.match(text):
        raise ValueError(f"{where}: cannot read the coordinate {token!r}")
    unknown = set(re.findall(r"[A-Za-z_]+", upper)) - set(_COORD_FUNCS) - {"PI"}
    if unknown:
        raise ValueError(
            f"{where}: the coordinate {token!r} uses "
            f"{'/'.join(sorted(unknown))}, which CP2K's coordinate evaluator "
            "does not provide")
    env: dict[str, object] = {"__builtins__": {}, "pi": math.pi}
    env.update({name.lower(): fn for name, fn in _COORD_FUNCS.items()})
    try:
        return float(eval(upper.replace("^", "**"), env))  # noqa: S307
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"{where}: cannot evaluate the coordinate {token!r} ({exc})") from exc


def read_cp2k_coord(text: str, source: str | None = None) -> Structure:
    """Pull the first ``&COORD``/``&CELL`` pair out of a CP2K input file.

    Works for an input written by hand **and** for a ``-1.restart`` file: CP2K
    writes the restart as a complete input, so the coordinates and the cell of
    that run are plain ``&COORD``/``&CELL`` sections here.  That is what lets a
    relay take its system from the restart file instead of a separate structure.
    """
    lines = [line.split("#", 1)[0] for line in text.splitlines()]
    atoms: list[Atom] = []
    cell: Cell | None = None
    scaled = False
    inside = False
    in_cell = False
    coord_factor = 1.0
    cell_factor = 1.0
    cell_abc: list[float] | None = None
    cell_vectors: dict[str, list[float]] = {}
    # An AMBER run names its atoms after the topology (CA3/OH2/HO3), and those
    # names must be translated with the atom-type table rather than the element
    # table - otherwise the elements come out wrong and the unknown names are
    # dropped, which silently shrinks the system.
    amber = bool(re.search(r"PARMTYPE\s+AMBER|CONN(?:ECTIVITY|_FILE_FORMAT)\s+AMBER",
                           text, re.IGNORECASE))

    for number, line in enumerate(lines, start=1):
        token = line.strip()
        if not token:
            continue
        upper = token.upper()
        if upper.startswith("&COORD"):
            inside = True
            in_cell = False
            scaled = False
            coord_factor = 1.0
            continue
        if inside and upper.startswith("&END"):
            inside = False
            continue
        if upper.startswith("&CELL"):
            in_cell = True
            cell_factor = 1.0
            continue
        if in_cell and upper.startswith("&END"):
            in_cell = False
            continue
        # ``SCALED``/``UNIT`` belong to the section they sit in; SCALED's value is
        # optional (``SCALED`` alone means T) and CP2K's own restart files write
        # ``SCALED T`` / ``SCALED .TRUE.`` just as often as the bare keyword.
        head = upper.split()[0]
        if (inside or in_cell) and head in ("SCALED", "LEUC"):
            words = upper.split(None, 1)
            flag = "" if len(words) == 1 else words[1].strip()
            if inside:
                scaled = flag not in ("F", "FALSE", ".FALSE.", "NO", "OFF", "0", "N")
            continue
        if (inside or in_cell) and head == "UNIT":
            words = token.split()
            if len(words) >= 2:
                if inside:
                    coord_factor = _length_factor(words[1], f"line {number} &COORD UNIT")
                else:
                    cell_factor = _length_factor(words[1], f"line {number} &CELL UNIT")
            continue
        if upper.startswith("ABC"):
            parts = token.split()
            values = [float(v) for v in parts[1:] if _is_number(v)]
            if len(values) >= 3:
                cell_abc = [v * cell_factor for v in values[:3]]
            continue
        if upper.startswith(("A ", "B ", "C ")) and not inside:
            key = upper[0]
            values = [float(v) for v in token.split()[1:] if _is_number(v)]
            if len(values) >= 3:
                cell_vectors[key] = [v * cell_factor for v in values[:3]]
            continue
        if inside:
            parts = token.split()
            if len(parts) >= 4:
                label = parts[0]
                symbol = element_from_label(label, amber=amber)
                where = f"&COORD line {number}"
                factor = 1.0 if scaled else coord_factor
                position = tuple(_coord_value(part, where) * factor
                                 for part in parts[1:4])
                # never drop an atom over an unrecognised name: the count is what
                # a force field run hinges on (&MOLECULE / NMOL must match it)
                atoms.append(Atom(symbol or label, *position, label=label))  # type: ignore[arg-type]
            elif not token.startswith(("@", "&")) \
                    and parts[0].upper() not in _COORD_KEYWORDS:
                # fewer than four fields is neither a coordinate row nor a
                # keyword of the section: CP2K would refuse it, so refuse it too
                # instead of quietly shrinking the system
                raise ValueError(
                    f"&COORD line {number}: {token!r} is neither a coordinate row "
                    "(label x y z) nor a keyword")

    if cell_abc:
        cell = Cell.orthorhombic(*cell_abc)
    elif len(cell_vectors) == 3:
        cell = Cell(tuple(cell_vectors["A"]), tuple(cell_vectors["B"]),  # type: ignore[arg-type]
                    tuple(cell_vectors["C"]))  # type: ignore[arg-type]

    if scaled and cell:
        for atom in atoms:
            atom.x, atom.y, atom.z = cell.to_cartesian((atom.x, atom.y, atom.z))
    if not atoms:
        raise ValueError(no_atoms_message(text, source))
    return Structure(atoms, cell, "XYZ" if cell else None, "from CP2K input", source)


def no_atoms_message(text: str, source: str | None = None) -> str:
    """Why a CP2K input without ``&COORD`` cannot be a *structure*, and what to do.

    ``&COORD`` is the only place in a CP2K input that carries atoms, and the tool
    needs one **element symbol per atom** (it writes ``&COORD``/``&KIND``, counts
    the elements, and checks the atom count against the topology).  A force-field
    input that takes its coordinates from ``COORD_FILE_NAME`` has none - and the
    file it points at (an AMBER ``inpcrd``) has no symbols either: CP2K gets those
    from the prmtop.  So this is a "wrong slot for that file" situation, not a
    broken file, and it is worth naming the alternatives.
    """
    name = Path(source).name if source else "这个输入"
    referenced = []
    for keyword in ("COORD_FILE_NAME", "CONN_FILE_NAME", "RESTART_FILE_NAME"):
        found = re.search(rf"(?mi)^\s*{keyword}\s+(\S+)", text)
        if found:
            referenced.append(f"{keyword} {found.group(1)}")
    where = ("（它写着 " + "、".join(referenced) + "）") if referenced else ""
    return (
        f"{name} 里没有 &COORD{where}：CP2K 的输入里只有 &COORD 带原子，"
        "而这个输入的原子在外部文件里。结构这一格要的是**每个原子的元素符号**"
        "（用来写 &COORD/&KIND、数元素、核对原子数）；AMBER 的 inpcrd/CRD 里只有坐标、"
        "元素在 prmtop 里，所以它也不能当结构。三条路："
        "① 力场（METHOD FIST）路线：**结构留空**，只填「拓扑文件」+「坐标文件」；"
        "② 想复用这份输入里的设置：用「读参数」；"
        "③ 想要一份结构：拖同一套体系的 .pdb/.xyz，或 CP2K 自己写的 -1.restart"
        "（里面有 &COORD 和 &CELL）。")


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


READERS = {
    ".xyz": read_xyz,
    ".pdb": read_pdb,
    ".ent": read_pdb,
    ".vasp": read_poscar,
    ".poscar": read_poscar,
    ".contcar": read_poscar,
    ".cif": read_cif,
    ".inp": read_cp2k_coord,
    ".restart": read_cp2k_coord,
}

#: coordinate files that hold **no element names**: CP2K reads them through
#: ``&TOPOLOGY COORD_FILE_NAME``, but they cannot be the structure (``&COORD`` needs
#: one symbol per atom, and the elements come from the topology file instead)
COORDINATE_ONLY_SUFFIXES = {
    ".crd": "AMBER 坐标文件（inpcrd / restart）",
    ".inpcrd": "AMBER 坐标文件（inpcrd）",
    ".rst7": "AMBER 坐标文件（rst7）",
    ".rst": "AMBER 坐标文件（rst7）",
}


def read_structure(path: str | Path, fmt: str | None = None) -> Structure:
    """Read a structure file, choosing the reader by extension or ``fmt``."""
    file = Path(path)
    text = file.read_text(encoding="utf-8", errors="ignore")
    extension = f".{(fmt or file.suffix).lstrip('.').lower()}"
    if extension in COORDINATE_ONLY_SUFFIXES:
        # These hold coordinates *without* element names (an AMBER inpcrd numbers
        # the atoms and gets the elements from the prmtop), so they cannot stand in
        # for a structure file - `&COORD` needs a symbol per atom.  Without this the
        # content sniffer picked POSCAR and died on
        # "invalid literal for int() with base 10: '8.9030000'".
        raise ValueError(
            f"{file.name} 是 {COORDINATE_ONLY_SUFFIXES[extension]}，里面只有坐标、"
            "没有元素名，不能当结构文件用（`&COORD` 每个原子都要元素符号）。"
            "做法：结构用同一套体系的 .pdb（或者 CP2K 写的 -1.restart），"
            "再把它填进 `&TOPOLOGY COORD_FILE_NAME`（界面「经典力场」页的坐标文件那一格）")
    reader = READERS.get(extension)
    if reader is None:
        # try hard: sniff the content
        stripped = text.lstrip()
        if stripped.startswith(("data_", "_cell_")) or "_atom_site_" in text:
            reader = read_cif
        elif "&COORD" in text.upper():
            reader = read_cp2k_coord
        elif _looks_like_poscar(text):
            reader = read_poscar
        elif any(line[:6].strip().upper() in ("ATOM", "HETATM") for line in text.splitlines()):
            reader = read_pdb
        else:
            reader = read_xyz
    return reader(text, str(file))


def parse_numbers(text: str | None) -> list[float]:
    """Parse ``"1, 2 3"`` into ``[1.0, 2.0, 3.0]``."""
    if not text:
        return []
    out: list[float] = []
    for token in re.split(r"[,\s]+", str(text).strip()):
        if token:
            out.append(float(token))
    return out


def parse_cell_text(text: str | None) -> "Cell | None":
    """Parse a user supplied cell: ``a,b,c`` or ``a,b,c,alpha,beta,gamma``."""
    values = parse_numbers(text)
    if not values:
        return None
    if len(values) == 3:
        return Cell.orthorhombic(*values)
    if len(values) == 6:
        return Cell.from_lengths_angles(*values)
    if len(values) == 9:
        return Cell(tuple(values[0:3]), tuple(values[3:6]), tuple(values[6:9]))  # type: ignore[arg-type]
    raise ValueError("a cell needs 3 (abc), 6 (abc + angles) or 9 (vectors) numbers")


def _looks_like_poscar(text: str) -> bool:
    lines = text.splitlines()
    if len(lines) < 8:
        return False
    try:
        float(lines[1].split()[0])
    except (ValueError, IndexError):
        return False
    for i in range(2, 5):
        try:
            if len([float(v) for v in lines[i].split()]) < 3:
                return False
        except ValueError:
            return False
    return True


# --------------------------------------------------------------------------- #
# CP2K blocks
# --------------------------------------------------------------------------- #
def coord_section(structure: Structure, scaled: bool = False,
                  comment: str | None = None,
                  preserve_labels: bool = False) -> "Section":
    """Build a ``&COORD`` section from a structure.

    ``preserve_labels`` writes the name the atom had in the source file instead
    of the element symbol.  A CP2K restart from an AMBER run names its atoms after
    the topology (``CA3``, ``OH2``), and copying those names back is both truthful
    and what CP2K itself does - a force-field run reads its parameters from the
    topology and never looks at these names.  On the Quickstep route the labels
    must stay element symbols, because CP2K matches them against ``&KIND``.
    """
    from .model import Section

    def name_of(atom: "Atom") -> str:
        if preserve_labels and atom.label:
            return atom.label
        return atom.symbol

    section = Section("COORD", comment=comment)
    if scaled:
        if structure.cell is None:
            raise ValueError("SCALED coordinates need a cell")
        section.set("SCALED", True)
        for atom in structure.atoms:
            frac = structure.cell.to_fractional(atom.position)
            section.append_keyword(name_of(atom), [round(f, 10) for f in frac],
                                   preserve_name=True)
    else:
        for atom in structure.atoms:
            section.append_keyword(name_of(atom),
                                   [round(atom.x, 8), round(atom.y, 8), round(atom.z, 8)],
                                   preserve_name=True)
    return section


def cell_section(structure: Structure, comment: str | None = None) -> "Section | None":
    """Build a ``&CELL`` section from a structure."""
    from .model import Section

    if structure.cell is None:
        return None
    cell = structure.cell
    section = Section("CELL", comment=comment)
    if cell.is_orthogonal():
        section.set("ABC", [round(v, 8) for v in cell.abc_keyword()], unit="angstrom")
    else:
        section.set("A", [round(v, 8) for v in cell.a], unit="angstrom")
        section.set("B", [round(v, 8) for v in cell.b], unit="angstrom")
        section.set("C", [round(v, 8) for v in cell.c], unit="angstrom")
    periodic = (structure.periodic or "XYZ").upper()
    section.set("PERIODIC", periodic)
    return section
