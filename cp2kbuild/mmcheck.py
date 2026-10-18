"""Does this coordinate file match these topology files?

The classical route lets one system be built from several molecules that each
come with their own topology (``&TOPOLOGY &MOL_SET``).  CP2K then assigns the
coordinates **strictly in order**: every ``&MOLECULE`` contributes ``NMOL``
copies of its topology's atoms, and they are matched against the atoms of the
single coordinate file from the first atom on.  Nothing in the input says which
atom belongs to which molecule, and ``read_topology_conn`` in ``topology.F``
aborts as soon as the two counts differ at all::

    IF (SIZE(topology%atom_info%id_molname) /= topology%natoms) &
       CALL cp_abort(... "Number of atoms in connectivity control is larger than
                          the number of atoms in coordinate control.")

So a file built by another tool - packmol, say - is usable exactly when its atoms
come in that order, in that number.  This module reads the topology files and the
coordinate file and says whether they agree, before CP2K gets a chance to abort
on a job that has already been queued.

Everything here is plain text parsing of the real formats:

* **prmtop** (AMBER) - ``%FLAG POINTERS`` (``%FORMAT(10I8)``, first value is
  NATOM) and ``%FLAG ATOM_NAME`` (``%FORMAT(20a4)``);
* **PSF** (CHARMM/XPLOR) - the ``!NATOM`` count and the ``ATOMNAME`` column;
* **PDB** - the atom-name columns and an optional ``CRYST1`` box;
* **inpcrd/CRD** (AMBER) - title line, ``NATOM``, then ``6F12.7`` coordinates,
  optionally velocities and a box;
* **XYZ** - the element column.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Coordinate formats CP2K accepts for ``&TOPOLOGY COORD_FILE_FORMAT`` that this
#: module can read back (``input_cp2k_subsys.F``).
READABLE_COORDS = ("PDB", "CRD", "XYZ", "G96")

SUFFIX_FORMAT = {
    ".pdb": "PDB", ".ent": "PDB",
    ".crd": "CRD", ".inpcrd": "CRD", ".rst": "CRD", ".restart": "CRD",
    ".xyz": "XYZ",
    ".g96": "G96",
}


@dataclass
class TopologyInfo:
    """One topology file: how many atoms it describes, and their names."""

    source: str
    kind: str
    natom: int
    names: list[str] = field(default_factory=list)


@dataclass
class CoordinateInfo:
    """One coordinate file: how many atoms it holds, and what they are called."""

    source: str
    kind: str
    natom: int
    names: list[str] = field(default_factory=list)
    box: tuple[float, float, float] | None = None
    velocities: bool = False
    #: how the file itself groups its atoms (PDB residue changes / TER records).
    #: Only a hint: packmol writes every molecule as residue ``UNL`` and renumbers
    #: from 1, so a block boundary is not necessarily a molecule boundary.
    blocks: list[int] = field(default_factory=list)


@dataclass
class Component:
    """A molecule size the coordinate file's own block structure implies."""

    natom: int
    count: int
    first_atom: int
    runs: int = 1

    @property
    def grouped(self) -> bool:
        """Whether every copy of this size sits in one contiguous run.

        ``&MOL_SET`` lists the molecules one kind after another, so a file whose
        blocks interleave (A, B, A) cannot be described by one ``&MOLECULE`` per
        kind - the order would be wrong.
        """
        return self.runs == 1


def detect_components(coordinate: CoordinateInfo) -> tuple[list[Component],
                                                           list[str]]:
    """Guess the system's composition from the coordinate file's block structure.

    A PDB delimits molecules with its residue / chain columns, so the block sizes
    are the molecule sizes and the number of equal-sized blocks is ``NMOL``:
    packmol output therefore carries everything ``&MOL_SET`` needs except the
    topology file names.

    It stays a *suggestion* on purpose.  packmol renumbers every molecule as
    ``UNL`` starting from 1 and only the chain letter (or a TER record) separates
    them, so two molecules can share one block, and one molecule split across two
    (a ``TER`` inside a residue) would look like two.  ``mmcheck`` against the
    real topologies is the authority; this just fills the blanks.
    """
    notes: list[str] = []
    blocks = coordinate.blocks
    if not blocks or coordinate.kind != "PDB":
        return [], notes

    order: list[int] = []
    by_size: dict[int, Component] = {}
    position = 0
    for size in blocks:
        if size in by_size:
            by_size[size].count += 1
        else:
            by_size[size] = Component(size, 1, position + 1)
            order.append(size)
        position += size

    # "contiguous" is about the *collapsed* sequence: 20 blocks of the same size
    # in a row are one run, while A, B, A would be two runs of A and cannot be
    # written as one &MOLECULE per kind
    collapsed = [size for index, size in enumerate(blocks)
                 if index == 0 or size != blocks[index - 1]]
    for size, component in by_size.items():
        component.runs = collapsed.count(size)

    components = [by_size[size] for size in order]
    ungrouped = [c for c in components if not c.grouped]
    if ungrouped:
        notes.append(
            "同一大小的段不连续（"
            + ", ".join(f"{c.natom} 原子 ×{c.count} 分成 {c.runs} 段" for c in ungrouped)
            + "）：`&MOL_SET` 是按种类依次列出的，这种交错顺序它表达不了——"
            "要么改 packmol 输入让同种分子连续，要么生成整个体系合并的拓扑")
    return components, notes


def suggest_molecules(components: list[Component],
                      connectivity: str = "AMBER") -> list[str]:
    """Ready-to-paste ``--mol`` entries (with the file name left blank)."""
    return [f"--mol {connectivity}:<{c.natom} 原子的拓扑文件>:{c.count}"
            for c in components]


@dataclass
class BlockCheck:
    """One ``&MOLECULE`` block against its slice of the coordinate file."""

    source: str
    nmol: int
    natom: int
    first_atom: int
    name_mismatches: int = 0
    samples: list[str] = field(default_factory=list)


@dataclass
class MmCheck:
    expected_atoms: int
    coordinate: CoordinateInfo | None
    blocks: list[BlockCheck] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: what the coordinate file's own block structure implies (a suggestion for
    #: the ``&MOL_SET`` table, not a fact)
    components: list["Component"] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def text(self) -> str:
        lines: list[str] = []
        if self.coordinate is not None:
            lines.append(f"坐标文件 : {self.coordinate.source} "
                         f"（{self.coordinate.kind}，{self.coordinate.natom} 个原子）")
            if self.coordinate.velocities:
                lines.append("          文件里带速度：CP2K 会把它们读成初始速度")
            if self.coordinate.box is not None:
                a, b, c = self.coordinate.box
                lines.append(f"          文件里的盒子 {a:.4f} × {b:.4f} × {c:.4f} Å —— "
                             "CP2K 会忽略它，请用 &CELL 给出盒子")
        if self.components:
            summary = " + ".join(f"{c.natom} 原子 ×{c.count}"
                                 for c in self.components)
            lines.append(f"按分段推测: {summary}"
                         f"（{sum(c.natom * c.count for c in self.components)} 个原子）")
            for entry in suggest_molecules(self.components):
                lines.append(f"          {entry}")
        for block in self.blocks:
            detail = (f"第 {block.first_atom} 个原子起，"
                      f"{block.nmol} × {block.natom} = {block.nmol * block.natom} 个")
            lines.append(f"&MOLECULE: {Path(block.source).name}  {detail}")
            for sample in block.samples:
                lines.append(f"          {sample}")
        lines.append(f"合计需要 : {self.expected_atoms} 个原子")
        for note in self.notes:
            lines.append(f"注意     : {note}")
        for problem in self.problems:
            lines.append(f"错误     : {problem}")
        if self.ok:
            lines.append("结论     : 对得上，CP2K 可以按这个顺序读")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# readers
# --------------------------------------------------------------------------- #
def _amber_blocks(lines: list[str]):
    """Yield ``(flag, format, [data lines])`` for an AMBER prmtop."""
    flag = None
    data: list[str] = []
    for line in lines:
        if line.startswith("%FLAG"):
            if flag is not None:
                yield flag, data
            flag = line.split(None, 1)[1].strip() if len(line.split(None, 1)) > 1 else ""
            data = []
        elif line.startswith("%FORMAT") or line.startswith("%VERSION"):
            continue
        elif flag is not None:
            data.append(line.rstrip("\n"))
    if flag is not None:
        yield flag, data


def read_prmtop(path: str | Path) -> TopologyInfo:
    """NATOM and ATOM_NAME out of an AMBER prmtop (topology) file."""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    natom = 0
    names: list[str] = []
    for flag, data in _amber_blocks(lines):
        if flag == "POINTERS" and data:
            # %FORMAT(10I8): the very first value is NATOM
            first = data[0][:8].strip()
            if first.lstrip("-").isdigit():
                natom = int(first)
        elif flag == "ATOM_NAME":
            # %FORMAT(20a4): 20 fields of four characters per line
            for line in data:
                for start in range(0, len(line), 4):
                    name = line[start:start + 4].strip()
                    if name:
                        names.append(name)
    if not natom:
        natom = len(names)
    return TopologyInfo(str(path), "prmtop", natom, names[:natom])


def read_psf(path: str | Path) -> TopologyInfo:
    """NATOM and the atom-name column out of a CHARMM/XPLOR PSF.

    The ``!NATOM`` rows are ``ID SEGID RESID RESNAME ATOMNAME TYPE CHARGE MASS``,
    so the name is field 5 (index 4) and the CHARMM type is field 6 - reading the
    wrong one gives ``HC1`` where the file says ``HH31``.
    """
    lines = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    natom = 0
    names: list[str] = []
    for index, line in enumerate(lines):
        if line.strip().endswith("!NATOM"):
            head = line.split("!")[0].split()
            if head and head[0].lstrip("-").isdigit():
                natom = int(head[0])
            names = []
            for entry in lines[index + 1:index + 1 + natom]:
                fields = entry.split()
                if len(fields) >= 5:
                    names.append(fields[4])
            break
    return TopologyInfo(str(path), "PSF", natom or len(names), names)


def read_topology(path: str | Path) -> TopologyInfo:
    """Read a prmtop or a PSF, by suffix then by content."""
    text = Path(path).read_text(encoding="utf-8", errors="ignore")[:4096]
    if "%FLAG" in text or "%VERSION" in text:
        return read_prmtop(path)
    if "!NATOM" in text:
        return read_psf(path)
    raise ValueError(f"{path}: 既不是 prmtop（没有 %FLAG）也不是 PSF（没有 !NATOM）")


def read_pdb(path: str | Path) -> CoordinateInfo:
    text = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    names: list[str] = []
    blocks: list[int] = []
    box = None
    current: str | None = None
    for line in text:
        record = line[:6].strip().upper()
        if record in ("ATOM", "HETATM"):
            # chain + residue number + residue name + insertion code: a change in
            # any of them starts a new block
            key = (line[21:22] + line[22:27] + line[17:20]).strip()
            if key != current:
                blocks.append(0)
                current = key
            blocks[-1] += 1
            names.append(line[12:16].strip())
        elif record == "TER" and blocks:
            current = None                 # an explicit break, even inside a residue
        elif record == "CRYST1":
            try:
                box = (float(line[6:15]), float(line[15:24]), float(line[24:33]))
            except ValueError:
                box = None
    return CoordinateInfo(str(path), "PDB", len(names), names, box, False, blocks)


def read_inpcrd(path: str | Path) -> CoordinateInfo:
    """AMBER inpcrd/restart: title, NATOM, 6F12.7 coordinates [, velocities][, box].

    The title line is optional - CP2K's own reader checks whether the first line
    already looks like ``NATOM`` before consuming it, and some files in CP2K's
    test suite really do start straight with the count (``dmpoh.rst``).

    Velocities and the box are told apart the way CP2K's ``read_coordinate_crd``
    does it: after the coordinates, a *full block* of ``3*natom`` more numbers is
    read as velocities, and a single further line is the box.  A file that has
    coordinates and only a box therefore trips CP2K's "no velocity information"
    warning and the box is dropped - which is worth saying out loud, because the
    box has to come from ``&CELL`` anyway.
    """
    raw = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    index = 0
    while index < len(raw) and not raw[index].strip():
        index += 1
    if index < len(raw):
        head = raw[index].split()
        if not (head and head[0].lstrip("-").isdigit()):
            index += 1                      # that was the title
    if index >= len(raw):
        raise ValueError(f"{path}: 没有 NATOM 行，不像 inpcrd")
    fields = raw[index].split()
    natom = int(fields[0])

    def numbers(line: str) -> list[float]:
        out: list[float] = []
        for start in range(0, len(line), 12):
            token = line[start:start + 12].strip()
            if token:
                try:
                    out.append(float(token))
                except ValueError:
                    pass
        return out

    body = [line for line in raw[index + 1:] if line.strip()]
    coord_lines = (natom + 1) // 2
    rest = body[coord_lines:]
    velocities = False
    box = None
    if len(rest) >= coord_lines and len(rest) > 1:
        velocities = True
        rest = rest[coord_lines:]
    if rest:
        values = numbers(rest[0])
        if len(values) >= 6:
            box = (values[0], values[1], values[2])
    return CoordinateInfo(str(path), "CRD", natom, [], box, velocities)


def read_xyz(path: str | Path) -> CoordinateInfo:
    lines = [line for line in Path(path).read_text(
        encoding="utf-8", errors="ignore").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"{path}: 空文件")
    try:
        natom = int(lines[0].split()[0])
    except (ValueError, IndexError) as exc:
        raise ValueError(f"{path}: 第一行不是原子数") from exc
    names = [line.split()[0] for line in lines[2:2 + natom]]
    return CoordinateInfo(str(path), "XYZ", natom, names)


def read_g96(path: str | Path) -> CoordinateInfo:
    """GROMOS-96 block format: the ``POSITION`` block, coordinates in **nm**.

    The blocks are ``TITLE`` / ``POSITION`` / ``BOX`` / ``END``; each position row
    is ``resid resname atomname seq x y z`` and GROMOS writes x, y, z in
    nanometres, so they are multiplied by 10 to get angstrom.  A file with only a
    ``POSITIONRED`` (reduced) block is refused by name rather than guessed at:
    the atom order would have to come from the topology, which this reader does
    not have.
    """
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    block = ""
    names: list[str] = []
    box_lines: list[list[float]] = []
    has_velocity = False
    reduced_only = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        upper = line.upper()
        if upper in ("TITLE", "POSITION", "POSITIONRED", "VELOCITY", "VELOCITYRED",
                     "BOX", "END", "POSRES", "REFPOSITION", "TIMESTEP", "PBC"):
            block = upper
            if block == "POSITIONRED":
                reduced_only = True
            continue
        if block == "POSITION":
            parts = line.split()
            if len(parts) >= 6:
                names.append(parts[2])
        elif block in ("VELOCITY", "VELOCITYRED"):
            has_velocity = has_velocity or len(line.split()) >= 3
        elif block == "BOX":
            parts = line.split()
            if len(parts) >= 3:
                box_lines.append([float(v) * 10.0 for v in parts[:3]])
    if not names:
        if reduced_only:
            raise ValueError(f"{path}: 只有 POSITIONRED（约化坐标）块，认不出原子")
        raise ValueError(f"{path}: G96 里没有 POSITION 块（认不出坐标）")
    box: tuple[float, float, float] | None = None
    if len(box_lines) == 1:
        # gmx2gromos writes an orthogonal box as a single line of diagonal
        # lengths; a triclinic box is three lines of box vectors
        box = (box_lines[0][0], box_lines[0][1], box_lines[0][2])
    elif len(box_lines) == 3:
        box = (box_lines[0][0], box_lines[1][1], box_lines[2][2])
    elif box_lines:
        raise ValueError(f"{path}: BOX 块既不是 1 行也不是 3 行，无法确定晶胞")
    return CoordinateInfo(str(path), "G96", len(names), names, box, has_velocity)


def guess_format(path: str | Path) -> str | None:
    return SUFFIX_FORMAT.get(Path(path).suffix.lower())


def read_coordinates(path: str | Path, kind: str | None = None) -> CoordinateInfo:
    kind = (kind or guess_format(path) or "").upper()
    readers = {"PDB": read_pdb, "CRD": read_inpcrd, "XYZ": read_xyz, "G96": read_g96}
    if kind not in readers:
        raise ValueError(f"{path}: 不认识的坐标格式 {kind!r}"
                         f"（认得出的是 {'/'.join(READABLE_COORDS)}）")
    return readers[kind](path)


def box_of(path: str | Path, kind: str | None = None) -> tuple[float, float, float] | None:
    """The box a coordinate file carries, if it has one (Å), else ``None``.

    AMBER's ``inpcrd``/``restart`` ends with a box line, PDB has ``CRYST1`` and a
    GROMOS ``.g96`` has a ``BOX`` block - it is the *same* box CP2K would read
    through ``TOPOLOGY/COORD_FILE_NAME`` when the input has no ``&CELL`` at all
    (``cell_methods.F``: ``CELL_FILE_NAME`` > ``ABC`` > ``A``/``B``/``C`` >
    ``COORD_FILE_NAME``).  The interface uses it to fill ``&CELL`` instead of
    asking for numbers that are already in the file.
    """
    try:
        info = read_coordinates(path, kind)
    except (OSError, ValueError):
        return None
    return info.box


# --------------------------------------------------------------------------- #
# the check
# --------------------------------------------------------------------------- #
def check_mixture(molecules, coordinate_path: str | Path,
                  coordinate_format: str | None = None) -> MmCheck:
    """Compare a ``&MOL_SET`` molecule list against one coordinate file.

    ``molecules`` is anything with ``conn_file`` / ``nmol`` attributes (the
    ``MmMolecule`` dataclass), in the order the ``&MOLECULE`` blocks will be
    written - the order *is* the contract.

    The one thing CP2K itself insists on is the **total atom count** (it aborts
    when it differs), so that is the only hard error here.  Atom *names* are
    compared too, but only as a hint: CP2K assigns the coordinates by position
    and derives each element through its own AMBER/CHARMM map, so names that do
    not match are normal - CP2K's own ``multi_psf.inp`` pairs a PSF that says
    ``HC1`` with a PDB that says ``1HH3`` and runs fine.
    """
    try:
        coordinate = read_coordinates(coordinate_path, coordinate_format)
    except (OSError, ValueError) as exc:
        return MmCheck(expected_atoms=0, coordinate=None, problems=[str(exc)])

    result = MmCheck(expected_atoms=0, coordinate=coordinate)
    result.components, component_notes = detect_components(coordinate)
    result.notes.extend(component_notes)

    topologies: list[TopologyInfo] = []
    for number, molecule in enumerate(molecules, start=1):
        if not molecule.conn_file:
            result.problems.append(
                f"第 {number} 个 &MOLECULE 还没填拓扑文件（CONN_FILE_NAME）")
            return result
        try:
            info = read_topology(molecule.conn_file)
        except (OSError, ValueError) as exc:
            result.problems.append(str(exc))
            return result
        topologies.append(info)
        result.expected_atoms += info.natom * int(molecule.nmol)

    if result.expected_atoms != coordinate.natom:
        result.problems.append(
            f"原子数对不上：{len(topologies)} 个拓扑按 NMOL 展开要 "
            f"{result.expected_atoms} 个原子，坐标文件里是 {coordinate.natom} 个"
            f"（相差 {coordinate.natom - result.expected_atoms:+d}）。"
            "CP2K 的 read_topology_conn 是「不相等就 abort」，"
            "所以这个数必须完全相等——先查 NMOL，再查坐标文件是不是这一套体系的")

    # only a PDB carries names that are worth comparing: an XYZ holds element
    # symbols and a CRD/CRD-restart carries no names at all
    compare_names = coordinate.kind == "PDB" and bool(coordinate.names)
    position = 0
    for info, molecule in zip(topologies, molecules):
        block = BlockCheck(info.source, int(molecule.nmol), info.natom,
                           position + 1)
        if compare_names and info.names:
            for copy in range(int(molecule.nmol)):
                start = position + copy * info.natom
                if start + info.natom > len(coordinate.names):
                    break
                for offset, expected in enumerate(info.names):
                    actual = coordinate.names[start + offset]
                    if not _same_name(expected, actual):
                        block.name_mismatches += 1
                        if len(block.samples) < 2:
                            block.samples.append(
                                f"第 {start + offset + 1} 个原子：拓扑写 "
                                f"{expected!r}，坐标写 {actual!r}")
        result.blocks.append(block)
        position += info.natom * int(molecule.nmol)

    if compare_names:
        compared = sum(b.natom * b.nmol for b in result.blocks)
        mismatched = sum(b.name_mismatches for b in result.blocks)
        if mismatched == 0:
            result.notes.append(
                f"原子名 {compared} 个逐个对上：坐标和拓扑来自同一套文件")
        else:
            sample = next(s for b in result.blocks for s in b.samples)
            result.notes.append(
                f"原子名有 {mismatched}/{compared} 处不同（{sample}）。"
                "**这本身不是错**：CP2K 按位置把坐标套到拓扑上，元素用它自己的 "
                "AMBER/CHARMM 映射表推（官方的 multi_psf.inp 就是 PDB 写 1HH3、"
                "PSF 写 HC1）。要保证的是**顺序**，这个从名字看不出来——"
                "先用 STEPS 10 短跑一次，看 &SUBSYS/&PRINT/&TOPOLOGY_INFO 的输出")
    elif coordinate.kind == "XYZ":
        result.notes.append(
            "坐标是 XYZ，只有元素符号：这里只核对了总数，原子顺序要自己看"
            "（CP2K 也是按位置分配的）")
    else:
        result.notes.append(f"{coordinate.kind} 坐标不带原子名，只核对了总数")

    if coordinate.blocks:
        head = ", ".join(str(n) for n in coordinate.blocks[:8])
        if len(coordinate.blocks) > 8:
            head += f", … （共 {len(coordinate.blocks)} 段）"
        result.notes.append(
            f"坐标文件按残基号/TER 分成这些段：{head}。"
            "这只能当线索——packmol 会把每种分子都写成残基 UNL 并重新编号，"
            "所以段边界不一定等于分子边界；CP2K 也完全不看残基，"
            "只按 &MOLECULE 的顺序数原子")

    if coordinate.box is not None:
        a, b, c = coordinate.box
        result.notes.append(
            f"坐标文件里的盒子**会被 CP2K 忽略**（read_coordinate_crd 明确写了 "
            f"\"BOX information ... will be ignored\"）：请在 &CELL 里写 "
            f"ABC {a:.4f} {b:.4f} {c:.4f}")
    return result


def _same_name(expected: str, actual: str) -> bool:
    """AMBER names are 4 characters, PDB names often 3 - compare loosely."""
    left = expected.strip().upper()
    right = actual.strip().upper()
    if left == right:
        return True
    # a PDB may drop the leading digit-free padding, or add a leading digit for
    # hydrogens (1HH1 vs HH1); compare the alphanumeric core
    return left.lstrip("0123456789") == right.lstrip("0123456789")


def check_single(topology_path: str | Path, coordinate_path: str | Path,
                 coordinate_format: str | None = None) -> MmCheck:
    """The one-topology case: no ``&MOL_SET``, coordinates must just match."""

    class _One:
        def __init__(self, path: str) -> None:
            self.conn_file = str(path)
            self.nmol = 1

    return check_mixture([_One(str(topology_path))], coordinate_path,
                         coordinate_format)
