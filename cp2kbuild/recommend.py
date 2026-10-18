"""Pick consistent basis sets and pseudopotentials for a set of elements.

The rules encoded here are the ones that bite CP2K newcomers hardest:

* a basis set's valence (the ``-qN`` suffix) must match the pseudopotential's
  valence for the same element, otherwise CP2K stops in the setup phase.  The
  potential is therefore chosen *after* the basis set and *by valence*, never by
  name length - ``GTH-PBE-q8`` is easy to pick for iron by accident, while the
  matching basis set ``DZVP-MOLOPT-SR-GTH`` is q16;
* condensed-phase simulations want the short-range (``-SR``) MOLOPT family,
  gas-phase molecules want the diffuse one;
* elements outside the MOLOPT coverage (lanthanides, actinides, some 4d/5d)
  need a different family or a per-element fallback;
* auxiliary basis sets from the RI/ADMM files must never end up behind
  ``BASIS_SET``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .datafiles import DataIndex

# --------------------------------------------------------------------------- #
# conventions
# --------------------------------------------------------------------------- #
# CP2K ignores these files in favour of the ones listed earlier when the same
# basis set name is present in several of them.
BASIS_FILE_ORDER = [
    "BASIS_MOLOPT",
    "BASIS_MOLOPT_UCL",
    "BASIS_MOLOPT_PSI",
    "BASIS_MOLOPT_UZH",
    "BASIS_MOLOPT_LnPP2",
    "BASIS_MOLOPT_LnPP1",
    "BASIS_MOLOPT_AcPP1",
    "BASIS_pob",
    "BASIS_SET",
    "GTH_BASIS_SETS",
    "ALL_BASIS_SETS",
    "BASIS_MINBAS",
    "EMSL_BASIS_SETS",
]

POTENTIAL_FILE_ORDER = [
    "GTH_POTENTIALS",
    "POTENTIAL",
    "POTENTIAL_UZH",
    "GTH_SOC_POTENTIALS",
    "ALL_POTENTIALS",
    "ECP_POTENTIALS",
]

# files whose contents are *not* orbital basis sets
AUXILIARY_BASIS_FILES = {
    "BASIS_ADMM", "BASIS_ADMM_ae", "BASIS_ADMM_MOLOPT", "BASIS_ADMM_UZH",
    "BASIS_RI_MOLOPT", "BASIS_RI_cc-TZ", "BASIS_LRIGPW_AUXMOLOPT",
    "BASIS_def2_QZVP_RI_ALL", "HFX_BASIS", "BASIS_PERIODIC_GW",
}

AUXILIARY_NAME_TOKENS = ("FIT", "ADMM", "AUX", "RI_", "CIFIT")

#: Deuterium's atomic mass (u).  Replacing H by D is not a chemical change: the
#: Born-Oppenheimer potential energy surface depends on the nuclear charge, not
#: on the mass, so the element (and with it the basis set and pseudopotential)
#: stays H and only the dynamics change - the X-H stretch period goes from
#: ~10 fs to ~14 fs, which is why 1.0 fs instead of 0.5 fs becomes the usual MD
#: time step.  CP2K itself writes ``&KIND H / MASS 2`` for ND3 in
#: ``tests/QS/regtest-gpw-4/ND3_meta_welltemp.inp``.
DEUTERIUM_MASS = 2.0141017781

# functional -> the GTH parameterisation whose name is conventionally used
FUNCTIONAL_POTENTIAL = {
    "PADE": "GTH-PADE",
    "LDA": "GTH-PADE",
    "PZ": "GTH-PADE",
    "VWN": "GTH-PADE",
    "PBE": "GTH-PBE",
    "REVPBE": "GTH-PBE",
    "PBESOL": "GTH-PBE",
    "RPBE": "GTH-PBE",
    "BLYP": "GTH-BLYP",
    "BP": "GTH-BP",
    "BP86": "GTH-BP",
    "HCTH120": "GTH-HCTH120",
    "HCTH407": "GTH-HCTH407",
    "OLYP": "GTH-OLYP",
    "PBE0": "GTH-PBE",
    "B3LYP": "GTH-BLYP",
    "HSE06": "GTH-PBE",
    "PBEH": "GTH-PBE",
    "M06L": "GTH-PBE",
    "TPSS": "GTH-PBE",
    "SCAN": "GTH-PBE",
    "WB97X": "GTH-PBE",
    "NONE": "GTH-PBE",
}

BASIS_PREFERENCES = {
    # condensed phase / metals: short-range MOLOPT keeps the grid cheap
    "solid": [
        "DZVP-MOLOPT-SR-GTH",
        "TZV2P-MOLOPT-SR-GTH",
        "TZVP-MOLOPT-SR-GTH",
        "SZV-MOLOPT-SR-GTH",
        "DZVP-MOLOPT-GTH",
        "TZV2P-MOLOPT-GTH",
        "TZVP-MOLOPT-GTH",
        "SZV-MOLOPT-GTH",
    ],
    # isolated molecules / clusters: diffuse MOLOPT
    "molecule": [
        "DZVP-MOLOPT-GTH",
        "TZV2P-MOLOPT-GTH",
        "TZVP-MOLOPT-GTH",
        "SZV-MOLOPT-GTH",
        "DZVP-MOLOPT-SR-GTH",
        "TZV2P-MOLOPT-SR-GTH",
        "TZVP-MOLOPT-SR-GTH",
        "SZV-MOLOPT-SR-GTH",
    ],
    "cheap": [
        "SZV-MOLOPT-SR-GTH",
        "SZV-MOLOPT-GTH",
        "DZVP-MOLOPT-SR-GTH",
        "DZVP-MOLOPT-GTH",
    ],
    "accurate": [
        "TZV2PX-MOLOPT-GTH",
        "TZV2P-MOLOPT-GTH",
        "TZV2P-MOLOPT-SR-GTH",
        "TZVP-MOLOPT-GTH",
        "TZVP-MOLOPT-SR-GTH",
    ],
}

FAMILY_FALLBACKS = ("MOLOPT", "pob", "GTH", "def2", "ccGRB")


@dataclass
class KindAssignment:
    element: str
    basis: str
    potential: str
    basis_file: str | None = None
    potential_file: str | None = None
    basis_valence: int | None = None
    potential_valence: int | None = None

    @property
    def consistent(self) -> bool:
        if self.basis_valence is None or self.potential_valence is None:
            return True
        return self.basis_valence == self.potential_valence


@dataclass
class Setup:
    assignments: dict[str, KindAssignment] = field(default_factory=dict)
    basis_files: list[str] = field(default_factory=list)
    potential_files: list[str] = field(default_factory=list)
    uniform_basis: bool = True
    notes: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def kind_sections(self, h_to_d: bool = False) -> list:
        """One ``&KIND`` per element; ``h_to_d`` gives hydrogen deuterium's mass.

        Deuterium is **not** a different element in CP2K - it is the same element
        with another nuclear mass, which is exactly what the Born-Oppenheimer
        approximation asks for (the PES depends on the nuclear charge, not the
        mass).  So the kind keeps ``ELEMENT H`` and therefore the same basis set
        and pseudopotential, and only ``MASS`` changes.  CP2K's own tests do the
        same thing, e.g. ``tests/QS/regtest-gpw-4/ND3_meta_welltemp.inp``
        (``&KIND H / MASS 2``).
        """
        from .model import Section

        out = []
        for element, assignment in self.assignments.items():
            comment = f"{element}: {assignment.basis}"
            if assignment.basis_valence is not None:
                comment += f" (q{assignment.basis_valence})"
            comment += f" + {assignment.potential}"
            section = Section("KIND", element, comment=comment)
            section.set("ELEMENT", element)
            section.set("BASIS_SET", assignment.basis)
            section.set("POTENTIAL", assignment.potential)
            if h_to_d and element.upper() == "H":
                section.set("MASS", DEUTERIUM_MASS, comment=(
                    "deuterium (H -> D): same element, same basis set and "
                    "pseudopotential, only the nuclear mass changes.  The X-H "
                    "stretch period grows by sqrt(2) (~10 fs -> ~14 fs), so 1.0 fs "
                    "instead of 0.5 fs is the usual MD time step"))
            out.append(section)
        return out


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _file_rank(name: str, order: list[str]) -> tuple[float, str]:
    # A versioned data file (``POTENTIAL_UZH_2026.2``) belongs to the same family
    # as its unversioned base.  CP2K ships both and the numbered one is the newer
    # data, so it must win instead of falling into the "unknown file" bucket.
    base = re.sub(r"_\d{4}(\.\d+)?$", "", name)
    try:
        return (float(order.index(name)), name)
    except ValueError:
        if base != name:
            try:
                return (order.index(base) - 0.5, name)
            except ValueError:
                pass
        return (float(len(order)), name)


def _looks_auxiliary(name: str) -> bool:
    upper = name.upper().replace("-", "_")
    return any(token in upper for token in AUXILIARY_NAME_TOKENS)


def usable_basis_names(index: DataIndex, element: str) -> list[str]:
    """Orbital basis names available for an element, auxiliaries removed."""
    out: list[str] = []
    for entry in index.entries:
        if entry.kind != "basis" or entry.element != element:
            continue
        if entry.file in AUXILIARY_BASIS_FILES:
            continue
        for candidate in entry.names:
            if _looks_auxiliary(candidate) or candidate in out:
                continue
            out.append(candidate)
    return out


def _pick_file(index: DataIndex, kind: str, name: str, element: str,
               order: list[str]) -> str | None:
    candidates = {
        entry.file
        for entry in index.entries
        if entry.kind == kind and entry.element == element
        and any(n.upper() == name.upper() for n in entry.names)
    }
    if not candidates:
        return None
    return sorted(candidates, key=lambda f: _file_rank(f, order))[0]


def _any_potential(index: DataIndex, element: str) -> tuple[str, int | None] | None:
    """Some *real* GTH potential for ``element``, when nothing matched.

    Writing the bare functional stem instead (``POTENTIAL GTH-PBE``) produces a
    name that many elements simply do not have (U, Bh, ...) - CP2K then refuses
    to start, and the user only finds out on the cluster.
    """
    names = [n for n in index.names_for("potential", element)
             if "GTH" in n.upper() and "-SOC" not in n.upper()]
    if not names:
        return None
    picked = sorted(names, key=lambda n: (len(n), n))[0]
    return picked, index.valence("potential", picked, element)


def _pick_potential(index: DataIndex, element: str, valence: int | None,
                    stem: str, override: str | None = None,
                    notes: list[str] | None = None) -> tuple[str, int | None] | None:
    """The potential for ``element`` whose valence matches the basis set.

    ``notes`` collects the choices that used to be silent: a potential from a
    different functional family, or a name-length guess because the basis set
    carries no electron count.
    """
    if override:
        return override, index.valence("potential", override, element)

    available = index.names_for("potential", element)
    if not available:
        return None

    def is_gth(name: str) -> bool:
        return name.upper().startswith("GTH-") and "-SOC" not in name.upper()

    families = [
        [n for n in available if is_gth(n) and n.upper().startswith(stem.upper() + "-Q")],
        [n for n in available if is_gth(n)],
        [n for n in available if "GTH" in n.upper() and "-SOC" not in n.upper()],
    ]
    for position, family in enumerate(families):
        if not family:
            continue
        if valence is not None:
            matching = [n for n in family
                        if index.valence("potential", n, element) == valence]
            if matching:
                picked = sorted(matching, key=lambda n: (len(n), n))[0]
                if position and notes is not None:
                    notes.append(
                        f"{element} 没有 {stem} 家族的赝势：改用 {picked}"
                        f"（价电子数对得上，但这是另一个交换关联参数化，请确认）")
                return picked, valence
            continue
        # the basis set carries no electron count (``DZV-GTH-PADE``,
        # ``pob-TZVP-rev2``, ...): say so instead of guessing silently - and
        # report the valence of the entry that is actually taken.  Reporting
        # ``family[0]``'s valence made a q8 potential look like a q16 one, so the
        # mismatch check could never fire.
        picked = sorted(family, key=lambda n: (len(n), n))[0]
        if notes is not None:
            notes.append(
                f"{element}: 基组没有 `-qN` 价电子数，赝势无法按价电子匹配，"
                f"按名字取了 {picked}（价电子 "
                f"{index.valence('potential', picked, element)}）——请核对")
        return picked, index.valence("potential", picked, element)
    return None


def _basis_candidates(index: DataIndex, elements: list[str], target: str) -> list[str]:
    """Ordered basis-set names to try, best first, preferring uniform coverage."""
    preferred = list(BASIS_PREFERENCES.get(target, BASIS_PREFERENCES["solid"]))
    ordered: list[str] = []

    def push(name: str) -> None:
        if name not in ordered:
            ordered.append(name)

    for name in preferred:
        push(name)

    # per-element availability, used to extend the list with anything plausible
    scores: dict[str, int] = {}
    coverage: dict[str, int] = {}
    for element in elements:
        for name in usable_basis_names(index, element):
            coverage[name] = coverage.get(name, 0) + 1
            scores.setdefault(name, _basis_score(name))

    extras = sorted(
        (n for n in coverage if n not in ordered),
        key=lambda n: (-coverage[n], -scores[n], len(n), n),
    )
    for name in extras:
        push(name)
    return ordered


def _basis_score(name: str) -> int:
    upper = name.upper()
    score = 0
    for token, weight in (("MOLOPT", 6), ("DZVP", 5), ("TZVP", 4), ("TZV2P", 4),
                          ("SZV", 3), ("SR", 2), ("GTH", 1), ("POB", 1)):
        if token in upper:
            score += weight
    return score


# --------------------------------------------------------------------------- #
# the decision
# --------------------------------------------------------------------------- #
def choose_assignments(
    elements: list[str],
    index: DataIndex,
    functional: str = "PBE",
    target: str = "solid",
    basis_override: str | None = None,
    potential_override: str | None = None,
    basis_file_override: str | None = None,
    potential_file_override: str | None = None,
) -> Setup:
    """Choose a basis set and pseudopotential for every element."""
    setup = Setup()
    elements = list(elements)
    functional_key = (functional or "PBE").upper().replace("-", "").replace("_", "")
    stem = FUNCTIONAL_POTENTIAL.get(functional_key, "GTH-PBE")

    if index.directory is None:
        setup.problems.append(
            "没有 CP2K 数据目录：基组和赝势无法核对。"
            "用 --data-dir <cp2k>/data（或设 CP2K_DATA_DIR）指给它"
        )
        for element in elements:
            setup.assignments[element] = KindAssignment(
                element, basis_override or "DZVP-MOLOPT-SR-GTH",
                potential_override or f"{stem}-q?", None, None)
        setup.basis_files = [basis_file_override or "BASIS_MOLOPT"]
        setup.potential_files = [potential_file_override or "GTH_POTENTIALS"]
        return setup

    if not elements:
        setup.problems.append("没有给元素：选不了基组")
        setup.basis_files = [basis_file_override or "BASIS_MOLOPT"]
        setup.potential_files = [potential_file_override or "GTH_POTENTIALS"]
        return setup

    candidates = [basis_override] if basis_override else _basis_candidates(
        index, elements, target)

    chosen_basis: str | None = None
    chosen_valences: dict[str, int | None] = {}
    chosen_potentials: dict[str, tuple[str, int | None]] = {}

    for name in candidates:
        if not name:
            continue
        valences = _valences_for(index, name, elements)
        if valences is None:
            continue
        potentials: dict[str, tuple[str, int | None]] = {}
        missing: list[str] = []
        for element in elements:
            picked = _pick_potential(index, element, valences[element], stem,
                                     potential_override, setup.problems)
            if picked is None or (valences[element] is not None
                                  and picked[1] is not None
                                  and picked[1] != valences[element]
                                  and not potential_override):
                missing.append(element)
                continue
            potentials[element] = picked
        if missing and not potential_override:
            continue
        chosen_basis = name
        chosen_valences = valences
        chosen_potentials = potentials
        if missing:
            setup.problems.append(
                f"没有和 {name} 价电子数匹配的 {stem} 赝势："
                f"{', '.join(missing)}")
        break

    if chosen_basis is None:
        # nothing covers every element: choose per element
        setup.uniform_basis = False
        setup.notes.append(
            "没有一个基组能覆盖这个体系的全部元素：改成逐元素各选一个")
        chosen_valences = {}
        chosen_potentials = {}
        per_element: dict[str, str] = {}
        for element in elements:
            picked_name = None
            for name in candidates:
                if name is None:
                    continue
                valence = index.valence("basis", name, element)
                if valence is None and basis_override is None:
                    continue
                potential = _pick_potential(index, element, valence, stem,
                                            potential_override, setup.problems)
                if potential is None and not potential_override:
                    continue
                if valence is not None and potential is not None \
                        and potential[1] is not None and potential[1] != valence \
                        and not potential_override:
                    continue
                picked_name = name
                chosen_valences[element] = valence
                chosen_potentials[element] = (potential
                                              or _any_potential(index, element)
                                              or (stem, None))
                break
            if picked_name is None:
                # an explicit --basis is the user's choice: never substitute a
                # different basis set for it (the fallback below used to replace
                # it with the first "usable" name without saying so)
                if basis_override:
                    setup.problems.append(
                        f"基组 {basis_override!r} 是显式指定的：没有为 {element} 找到"
                        "可核对的赝势，保留你的基组，请自己确认 POTENTIAL 一行")
                    picked_name = basis_override
                else:
                    fallback = usable_basis_names(index, element)
                    picked_name = fallback[0] if fallback else "DZVP-MOLOPT-SR-GTH"
                    setup.problems.append(
                        f"{element} 没有可核对的「基组 + 赝势」组合；"
                        f"先用占位的 {picked_name!r}")
                chosen_valences[element] = index.valence("basis", picked_name, element)
                chosen_potentials[element] = (_any_potential(index, element)
                                              or (stem, None))
            per_element[element] = picked_name
        chosen_basis = None
        for element, name in per_element.items():
            _record(setup, index, element, name, chosen_valences.get(element),
                    chosen_potentials.get(element, (stem, None)),
                    basis_file_override, potential_file_override)
        _finish(setup, index)
        return setup

    for element in elements:
        _record(setup, index, element, chosen_basis, chosen_valences.get(element),
                chosen_potentials.get(element, (stem, None)),
                basis_file_override, potential_file_override)
    _finish(setup, index)
    return setup


def _valences_for(index: DataIndex, basis: str, elements: list[str]
                  ) -> dict[str, int | None] | None:
    """Valence of ``basis`` for every element, or ``None`` if one is missing."""
    out: dict[str, int | None] = {}
    for element in elements:
        valence = index.valence("basis", basis, element)
        if valence is None:
            available = {n.upper() for n in usable_basis_names(index, element)}
            if basis.upper() not in available:
                return None
        out[element] = valence
    return out


def _record(setup: Setup, index: DataIndex, element: str, basis: str,
            basis_valence: int | None, potential: tuple[str, int | None],
            basis_file_override: str | None,
            potential_file_override: str | None) -> None:
    potential_name, potential_valence = potential
    basis_file = basis_file_override or _pick_file(
        index, "basis", basis, element, BASIS_FILE_ORDER)
    potential_file = potential_file_override or _pick_file(
        index, "potential", potential_name, element, POTENTIAL_FILE_ORDER)

    if basis_file is None and not basis_file_override:
        available = usable_basis_names(index, element)
        setup.problems.append(
            f"{element} 没有可用的基组 {basis!r}"
            + (f"；可用的有：{', '.join(available[:10])}" if available else ""))
    if potential_file is None and not potential_file_override:
        available = index.names_for("potential", element)
        setup.problems.append(
            f"{element} 没有可用的赝势 {potential_name!r}"
            + (f"；可用的有：{', '.join(available[:10])}" if available else ""))
    if basis_valence is not None and potential_valence is not None \
            and basis_valence != potential_valence:
        setup.problems.append(
            f"{element} 的价电子数对不上：基组 {basis} 是 q{basis_valence}，"
            f"而赝势 {potential_name} 是 q{potential_valence}")

    setup.assignments[element] = KindAssignment(
        element, basis, potential_name, basis_file, potential_file,
        basis_valence, potential_valence)


def _finish(setup: Setup, index: DataIndex) -> None:
    setup.basis_files = sorted(
        {a.basis_file for a in setup.assignments.values() if a.basis_file},
        key=lambda f: _file_rank(f, BASIS_FILE_ORDER))
    setup.potential_files = sorted(
        {a.potential_file for a in setup.assignments.values() if a.potential_file},
        key=lambda f: _file_rank(f, POTENTIAL_FILE_ORDER))
    if not setup.basis_files:
        setup.basis_files = ["BASIS_MOLOPT"]
    if not setup.potential_files:
        setup.potential_files = ["GTH_POTENTIALS"]
