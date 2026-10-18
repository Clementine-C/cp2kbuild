"""Regenerate ``examples/generated`` from the command line interface.

``python tools/make_examples.py`` writes every example with the same code path a
user would take (``python -m cp2kbuild new ...``), so the examples can never
describe an interface that no longer exists.  Run it after changing the CLI.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cp2kbuild.cli import main as cli_main          # noqa: E402

EXAMPLES = ROOT / "examples"
OUT = EXAMPLES / "generated"


def run(*argv: str) -> None:
    code = cli_main(["new", *argv, "--quiet", "--no-validate"])
    if code:
        raise SystemExit(f"cp2kbuild new {' '.join(argv)} -> exit {code}")


def argon_cell(path: Path, n: int = 3, a: float = 5.26) -> None:
    """An fcc argon cluster: the simplest system the pair-potential route fits."""
    basis = [(0.0, 0.0, 0.0), (0.0, 0.5, 0.5), (0.5, 0.0, 0.5), (0.5, 0.5, 0.0)]
    lines = []
    for i in range(n):
        for j in range(n):
            for k in range(n):
                for corner in basis:
                    x = (i + corner[0]) * a
                    y = (j + corner[1]) * a
                    z = (k + corner[2]) * a
                    # fold back into the cell so the box stays a x n
                    if x >= n * a - 1e-9:
                        x -= n * a
                    if y >= n * a - 1e-9:
                        y -= n * a
                    if z >= n * a - 1e-9:
                        z -= n * a
                    lines.append(f"Ar {x:.6f} {y:.6f} {z:.6f}")
    text = f"{len(lines)}\nargon fcc {n}x{n}x{n}, a={a} A\n" + "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def force_field_terms(path: Path) -> None:
    """The &FORCEFIELD contents for the in-input route.

    Water with the SPC/E-like terms CP2K's own ``H2O-32_NPT.inp`` uses: the bond
    and angle are kept rigid by the constraint block in the input, so their force
    constants are zero and only the charges and the LJ terms do any work.
    """
    path.write_text(
        "# &FORCEFIELD contents for the in-input route (PARMTYPE OFF).\n"
        "# Units are CP2K's: EPSILON in K, SIGMA/RCUT in angstrom.\n"
        "&CHARGE\n"
        "  ATOM O\n"
        "  CHARGE -0.8476\n"
        "&END CHARGE\n"
        "&CHARGE\n"
        "  ATOM H\n"
        "  CHARGE 0.4238\n"
        "&END CHARGE\n"
        "&BOND\n"
        "  ATOMS O H\n"
        "  K 0.0\n"
        "  R0 1.0\n"
        "&END BOND\n"
        "&BEND\n"
        "  ATOMS H O H\n"
        "  K 0.0\n"
        "  THETA0 109.47\n"
        "&END BEND\n"
        "&NONBONDED\n"
        "  &LENNARD-JONES\n"
        "    ATOMS O O\n"
        "    EPSILON 78.198\n"
        "    SIGMA 3.166\n"
        "    RCUT 11.4\n"
        "  &END LENNARD-JONES\n"
        "  &LENNARD-JONES\n"
        "    ATOMS O H\n"
        "    EPSILON 0.0\n"
        "    SIGMA 3.6705\n"
        "    RCUT 11.4\n"
        "  &END LENNARD-JONES\n"
        "  &LENNARD-JONES\n"
        "    ATOMS H H\n"
        "    EPSILON 0.0\n"
        "    SIGMA 3.30523\n"
        "    RCUT 11.4\n"
        "  &END LENNARD-JONES\n"
        "&END NONBONDED\n",
        encoding="utf-8", newline="\n")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    water = str(EXAMPLES / "water.xyz")
    silicon = str(EXAMPLES / "silicon.xyz")

    # ---- DFT / Quickstep -------------------------------------------------- #
    run("--task", "energy", "-s", water, "--vacuum", "12",
        "--out", str(OUT / "water_energy.inp"))
    run("--task", "geo_opt", "-s", water, "--vacuum", "12",
        "--out", str(OUT / "water_geo_opt.inp"))
    run("--task", "freq", "-s", water, "--vacuum", "12", "--intensities",
        "--out", str(OUT / "water_ir.inp"))
    run("--task", "md", "-s", water, "--vacuum", "12",
        "--out", str(OUT / "water_md.inp"))
    run("--task", "neb", "-s", water, "--vacuum", "12", "--replicas", "5",
        "--out", str(OUT / "water_neb.inp"))
    run("--task", "cell_opt", "-s", silicon, "--cutoff", "600",
        "--out", str(OUT / "si_cell_opt.inp"))
    run("--task", "band", "-s", silicon, "--lattice", "fcc",
        "--kpath", "GAMMA-X-W-K-GAMMA-L-U-W-L-K|U-X",
        "--npoints", "60", "--out", str(OUT / "si_band.inp"))
    run("--task", "pdos", "-s", silicon, "--pdos-ldos", "1 2 3",
        "--pdos-nlumo", "10", "--kpoint-grid", "4,4,4",
        "--out", str(OUT / "si_pdos.inp"))

    # ---- classical force field (METHOD FIST) ------------------------------ #
    # AMBER: the files are placeholders, so the input is a template to fill in
    run("--task", "md", "-s", water, "--method", "fist", "--mm-route", "AMBER",
        "--mm-parm-file", "system.prmtop", "--mm-conn-file", "system.prmtop",
        "--mm-coord-file", "system.inpcrd", "--cell", "30,30,30",
        "--ensemble", "NVT", "--temperature", "300", "--steps", "100000",
        "--timestep", "1.0", "--mm-ewald-gmax", "64",
        "--out", str(OUT / "fist_amber_nvt.inp"))

    # mixed system: one topology file per molecule, coordinates from one file
    run("--task", "md", "-s", water, "--method", "fist", "--mm-route", "AMBER",
        "--mm-parm-file", "mixture.prmtop", "--mm-coord-file", "mixture.inpcrd",
        "--mm-molecule", "AMBER:water.prmtop:500",
        "--mm-molecule", "AMBER:ion.prmtop:20",
        "--cell", "40,40,40", "--ensemble", "NPT_I", "--temperature", "300",
        "--steps", "500000", "--timestep", "2.0",
        "--out", str(OUT / "fist_mixed_molset.inp"))

    # in-input parameters: self-contained apart from the structure file
    ff = OUT / "fist_water_ff.inc"
    force_field_terms(ff)
    run("--task", "md", "-s", water, "--method", "fist", "--mm-route", "IN_INPUT",
        "--mm-ff-file", "fist_water_ff.inc", "--cell", "9.865,9.865,9.865",
        "--ensemble", "NVT", "--temperature", "300", "--steps", "10000",
        "--timestep", "0.5", "--mm-ewald", "SPME", "--mm-ewald-gmax", "20",
        "--mm-o-spline", "6",
        "--out", str(OUT / "fist_water_in_input.inp"))

    # pair potential: argon, the simplest thing FIST can do
    argon = EXAMPLES / "argon.xyz"
    argon_cell(argon)
    run("--task", "md", "-s", str(argon), "--method", "fist", "--mm-route", "PAIR",
        "--lj", "Ar:119.8:3.405", "--lj-rcut", "8.4", "--mm-ewald", "NONE",
        "--cell", f"{3 * 5.26:.4f},{3 * 5.26:.4f},{3 * 5.26:.4f}",
        "--ensemble", "NVE", "--temperature", "85", "--steps", "10000",
        "--timestep", "5.0",
        "--out", str(OUT / "fist_argon_nve.inp"))

    print(f"wrote {len(list(OUT.glob('*.inp')))} inputs + "
          f"{len(list(OUT.glob('*.inc')))} include to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
