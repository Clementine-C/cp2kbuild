"""Smoke-test every classical (FIST) route: print the input and its warnings.

Run with ``python tools/fist_smoke.py``.  It only checks that the generated text
matches the layout CP2K documents; there is no CP2K binary involved.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cp2kbuild.datafiles import load_index          # noqa: E402
from cp2kbuild.model import render_document         # noqa: E402
from cp2kbuild.recipes import (MmMolecule, Spec, build,  # noqa: E402
                               lorentz_berthelot)
from cp2kbuild.structures import read_structure     # noqa: E402

HERE = Path(__file__).resolve().parents[1]
INDEX = load_index(None)


def show(title: str, spec: Spec) -> None:
    result = build(spec, INDEX)
    print("=" * 78)
    print(f"### {title}")
    print("=" * 78)
    print(render_document(result.sections, notes=result.notes))
    for warning in result.warnings:
        print(f"!! WARNING: {warning}")
    print()


def main() -> int:
    silicon = read_structure(str(HERE / "examples" / "silicon.xyz"))
    water = read_structure(str(HERE / "examples" / "water.xyz"))

    routes = {
        "AMBER": dict(mm_route="AMBER", mm_parm_file="sys.prmtop",
                      mm_conn_file="sys.prmtop", mm_coord_file="sys.inpcrd"),
        "CHARMM": dict(mm_route="CHARMM", mm_parm_file="sys.par",
                       mm_conn_file="sys.psf", mm_coord_file="sys.pdb"),
        "GROMOS96": dict(mm_route="GROMOS96", mm_parm_file="sys.top",
                         mm_conn_file="sys.top", mm_coord_file="sys.g96"),
        "GROMOS87": dict(mm_route="GROMOS87", mm_parm_file="sys.top",
                         mm_conn_file="sys.top", mm_coord_file="sys.g96"),
    }
    for name, extra in routes.items():
        show(f"{name} route, MD NVT",
             Spec(task="md", method="FIST", structure=water, ensemble="NVT",
                  steps=2000, timestep=1.0, **extra))

    show("PAIR route (argon-like, no epsilon given) -> warning",
         Spec(task="md", method="FIST", mm_route="PAIR", structure=silicon,
              ensemble="NVE", steps=100, timestep=5.0))
    show("PAIR route with Lorentz-Berthelot mixing",
         Spec(task="md", method="FIST", mm_route="PAIR", structure=water,
              ensemble="NPT_I", steps=100, timestep=1.0,
              mm_element_lj={"O": (78.198, 3.166), "H": (0.0, 3.30523)}))
    print("mixed pairs:", [(p.atoms, round(p.epsilon or 0, 4), round(p.sigma or 0, 4))
                           for p in lorentz_berthelot({"O": (78.198, 3.166),
                                                       "H": (0.0, 3.30523)})])

    mixed = Spec(task="md", method="FIST", mm_route="AMBER", structure=water,
                 ensemble="NVT", steps=1000, timestep=1.0,
                 mm_parm_file="mix.prmtop", mm_coord_file="mix.inpcrd",
                 mm_molecules=[MmMolecule("mol1.prmtop", "AMBER", 100),
                               MmMolecule("mol2.prmtop", "AMBER", 50)])
    show("mixed system: CONNECTIVITY MOL_SET with two molecules", mixed)

    show("classical energy with an in-input force field (file missing)",
         Spec(task="energy", method="FIST", mm_route="IN_INPUT",
              structure=water, mm_ff_file="does_not_exist.inc"))
    show("band structure + FIST -> refused with a warning",
         Spec(task="band", method="FIST", mm_route="AMBER", structure=silicon,
              mm_parm_file="a.prmtop", mm_conn_file="a.prmtop",
              mm_coord_file="a.inpcrd"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
