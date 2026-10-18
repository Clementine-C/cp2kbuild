"""Command line interface: ``cp2kbuild <command>``."""

from __future__ import annotations

import argparse
import itertools
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__
from .cn_glossary import KEYWORDS, TERMS, is_chinese, lookup as cn_lookup
from .cn_glossary import search as cn_search, split_key, terms_in as cn_terms_in
from .datafiles import DataIndex, discover_data_dir
from .manualdb import load_db
from .model import Section, render_document
from .parser import read_input
from .paths import is_frozen, read_template_text, write_template_text, write_text_lf
from .cutoffkit import (DEFAULT_CUTOFFS, DEFAULT_FIXED_REL_CUTOFF,
                        DEFAULT_REL_CUTOFFS, build_cutoff_study)
from .recipes import RUN_TYPE_OF as RUN_TYPES
from .recipes import (MM_ROUTES, TASKS, BuildResult, MmLennardJones, MmMolecule,
                      Spec, build, effective_route, is_classical,
                      lennard_jones_pairs, mm_route, structure_stem)
from .recipes import EXT_RESTART_TAKES
from .recipes import prepare_structure as apply_structure_options
from .structures import Cell, Structure, parse_cell_text, parse_numbers, read_structure
from .validate import Validator, summarise

EPILOG = """\
examples:
  # geometry optimisation of a molecule, gas phase, with D3(BJ)
  cp2kbuild new --task geo_opt --structure mol.xyz --vacuum 12 \\
      --functional PBE --dispersion d3bj --out mol_geo.inp

  # AIMD of a periodic slab at 400 K (Born-Oppenheimer: DFT forces each step)
  cp2kbuild new --task md --structure slab.xyz --ensemble NPT --temperature 400 \\
      --steps 20000 --out slab_aimd.inp

  # the official CUTOFF/REL_CUTOFF convergence scripts, from an input you already
  # have: template.inp + cutoff_inputs/run/analyse.sh + rel_cutoff_*.sh
  cp2kbuild scan --input system.inp --outdir cutoff_test

  # a staged temperature program: hold 300 K, heat to 500 K, hold, cool to 300 K
  # (one input per segment + run_program.sh, chained through &EXT_RESTART)
  cp2kbuild ramp --structure slab.xyz --ensemble LANGEVIN \\
      --segments "300:5,300-500:10,500:5,500-300:20" --outdir temperature_program

  # band structure along Gamma-X-M-Gamma
  cp2kbuild new --task band --structure si.cif --kpath "GAMMA-X-M-GAMMA" \\
      --npoints 60 --out si_band.inp

  # check an input somebody else wrote
  cp2kbuild validate legacy.inp
"""

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _warn_restart(spec: Spec) -> None:
    """Say so when a restart setting cannot have the effect the user expects."""
    if spec.ext_restart_file and spec.task in ("energy", "freq", "band", "pdos"):
        print(f"WARN  --restart 对 RUN_TYPE {RUN_TYPES.get(spec.task, spec.task)} "
              "没有意义：EXT_RESTART 续的是带迭代步的计算（MD / GEO_OPT / "
              "CELL_OPT），单点或频率会照常从头算。", file=sys.stderr)
    if (spec.scf_guess or "").upper() in ("RESTART", "HISTORY_RESTART") \
            and not spec.wfn_restart_file and not spec.wfn_restart_print:
        print("WARN  --scf-guess RESTART 但既没给 --wfn-restart-file 也没开 "
              "&PRINT/&RESTART：没有文件可读，每次都会回退成 ATOMIC 初猜。",
              file=sys.stderr)


def _target_version() -> str:
    """Which CP2K version the bundled manual database describes."""
    from .manualdb import current_version

    return current_version()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cp2kbuild",
        description="Write and check CP2K input files, set up the "
                    "CUTOFF/REL_CUTOFF convergence scripts, and write a staged "
                    "MD temperature program (hold / heat / cool).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version",
                        version=f"cp2kbuild {__version__} "
                                f"(CP2K {_target_version()} manual)")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # ---- new ---------------------------------------------------------- #
    new = sub.add_parser("new", help="generate an input file from a structure",
                         formatter_class=argparse.RawDescriptionHelpFormatter,
                         epilog=EPILOG)
    add_common(new)
    new.add_argument("--task", default="energy", choices=list(TASKS),
                     help="what to compute (default: energy); 'md' is AIMD "
                          "(Born-Oppenheimer MD, DFT forces at every step)")
    new.add_argument("-o", "--out", default=None,
                     help="output file (default: <structure>.inp; the PROJECT_NAME "
                          "inside it is a separate thing and defaults to cp2k)")
    new.add_argument("--stdout", action="store_true", help="print instead of writing")

    # ---- scan --------------------------------------------------------- #
    scan = sub.add_parser(
        "scan",
        help="write CP2K's own CUTOFF/REL_CUTOFF convergence script set",
        description="Generate template.inp plus the six shell scripts of CP2K's "
                    "tutorial 'How to Converge the CUTOFF and REL_CUTOFF': "
                    "cutoff_inputs.sh / cutoff_run.sh / cutoff_analyse.sh and the "
                    "same three for REL_CUTOFF.  Everything after that (running "
                    "them, reading the outputs) happens on the cluster.  "
                    "template.inp comes either from an existing input (--input) or "
                    "from the same options `new` takes (--structure ...); in both "
                    "cases RUN_TYPE is set to ENERGY, because that is what a grid "
                    "study compares.  The values swept are --cutoffs (step 1, with "
                    "--rel-cutoff held fixed) and --rel-cutoffs (step 2, with "
                    "--fixed-cutoff held fixed); the &MGRID numbers in the template "
                    "itself are replaced by the placeholders.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common(scan)
    scan.add_argument("--input", default=None, metavar="INP",
                      help="an existing input to base the study on: it is copied to "
                           "template.inp verbatim (CUTOFF/REL_CUTOFF become the "
                           "placeholders LT_cutoff / LT_rel_cutoff, RUN_TYPE becomes "
                           "ENERGY).  Without it, the input is built from the same "
                           "options `new` takes")
    scan.add_argument("--outdir", default="cutoff_test", help="output directory")
    scan.add_argument("--cutoffs", default=None,
                      help=f"comma-separated CUTOFF values in Ry "
                           f"(default: {', '.join(str(v) for v in DEFAULT_CUTOFFS)})")
    scan.add_argument("--rel-cutoffs", default=None,
                      help="comma-separated REL_CUTOFF values in Ry for the second "
                           f"step (default: "
                           f"{', '.join(str(v) for v in DEFAULT_REL_CUTOFFS)})")
    # --rel-cutoff (add_common) is the value held fixed in step 1: 60 Ry by
    # default, which is exactly what the tutorial starts from.
    scan.add_argument("--fixed-cutoff", type=float, default=None, metavar="RY",
                      dest="fixed_cutoff",
                      help="the CUTOFF held fixed in step 2 - the value step 1 "
                           "produced. Without it the second-step scripts refuse "
                           "to run until you fill it in.")
    # add_common's --cutoff means "&MGRID CUTOFF in the input", which here is the
    # number the placeholders replace - say so instead of letting it look useful.
    # The restart chain is dropped for the same reason: a grid scan may not read
    # a previous run's restart (the file is not shipped with the test, and CP2K
    # aborts on a missing one before the first SCF step).
    _IGNORED_BY_SCAN = {
        "cutoff": "the template's CUTOFF becomes the LT_cutoff placeholder, so use "
                  "--cutoffs (step 1) and --fixed-cutoff (step 2)",
        "ext_restart": "a grid scan is a series of independent single point runs; "
                       "chaining them through &EXT_RESTART would make every point "
                       "read (and abort on) a previous run's restart file",
        "restart_take": "only meaningful together with --restart, which scan does "
                        "not use",
        "wfn_restart_file": "the points start from SCF_GUESS ATOMIC, so a wave "
                            "function file is not read",
    }
    for action in scan._actions:
        if action.dest in _IGNORED_BY_SCAN:
            action.help = ("ignored by this command: "
                           + _IGNORED_BY_SCAN[action.dest])

    # ---- ramp --------------------------------------------------------- #
    rmp = sub.add_parser(
        "ramp",
        help="write a staged MD temperature program (hold / heat / cool)",
        description="CP2K has no time-dependent temperature: one &MD run can only "
                    "do a single geometric ramp (per-step factor), so a program "
                    "like 'hold 300 K, heat to 500 K, hold, heat to 600 K, hold, "
                    "cool to 300 K' becomes a chain of runs, each continuing the "
                    "previous one through &EXT_RESTART.  This writes one input per "
                    "segment plus run_program.sh (serial - the stages depend on "
                    "each other) and program.ssv (the planned schedule).  "
                    "The program decides every stage's temperature and length, so "
                    "`--sequence`/`--steps` are not used here.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common(rmp)
    rmp.add_argument("--program", default=None, metavar="FILE",
                     help="the program text: one segment per line, e.g. "
                          "'hold 300 5ps' / 'ramp 300 500 10ps' (# starts a comment)")
    rmp.add_argument("--segments", default=None, metavar="LIST",
                     help="the same thing on one line: '300:5,300-500:10,500:5' "
                          "(T:dur holds, T1-T2:dur ramps; dur defaults to ps and "
                          "accepts ps/fs/ns/steps)")
    rmp.add_argument("--outdir", default="temperature_program",
                     help="where the stage inputs and run_program.sh go")
    rmp.add_argument("--mpi-procs", type=int, default=64, metavar="N",
                     help="mpirun -np N for every stage (default 64)")
    rmp.add_argument("--omp-threads", type=int, default=2, metavar="N",
                     help="OMP_NUM_THREADS for every stage (default 2)")
    rmp.add_argument("--cp2k-bin", default="cp2k.psmp", metavar="EXE",
                     help="the CP2K executable the driver runs (default cp2k.psmp; "
                          "cp2k.popt/cp2k.ssmp/... are fine too)")
    rmp.add_argument("--mpirun", dest="mpirun_bin", default="mpirun", metavar="EXE",
                     help="the launcher (default mpirun).  Give an empty string "
                          "(`--mpirun \"\"`) to run one process without mpirun")
    rmp.add_argument("--mpirun-extra", default="", metavar="ARGS",
                     help="extra words for mpirun, e.g. `--bind-to none` (OpenMPI "
                          "only; MPICH/Intel MPI refuse it)")
    rmp.add_argument("--no-resume", dest="resume", action="store_false",
                     help="do not skip stages that already finished: run the whole "
                          "program from the first stage again")
    # The program decides every stage's temperature and length, and the
    # temperature curve - so the single-run heating/cooling options cannot reach
    # it.  Say that in the help instead of letting them look useful.
    _IGNORED_BY_RAMP = {
        "temperature_ramp": "each segment of the program is a ramp or a hold; a "
                            "single-run ramp is what `new --temperature-ramp` is for",
        "temperature_annealing": "the factor of every stage is worked out from the "
                                 "program (put a segment in it instead)",
        "annealing": "the factor of every stage is worked out from the program "
                     "(put a segment in it instead)",
        "restart_temperature_annealing": "every stage writes its own starting "
                                         "temperature, so nothing has to be carried "
                                         "over from the restart file",
        "steps": "the length of every stage is the segment's duration",
        "temperature": "the temperature of every stage is the segment's start",
    }
    for action in rmp._actions:
        if action.dest in _IGNORED_BY_RAMP:
            action.help = ("not used by this command: "
                           + _IGNORED_BY_RAMP[action.dest])

    mmc = sub.add_parser(
        "mmcheck",
        help="check a classical-MD coordinate file against its topology file(s)",
        description="CP2K assigns the coordinates of a classical run by position: "
                    "every &MOLECULE contributes NMOL copies of its topology's "
                    "atoms, in order.  This checks a coordinate file - a packmol "
                    "PDB, say - against that list before the job runs.")
    mmc.add_argument("--mol", action="append", default=[],
                     metavar="[FMT:]FILE:NMOL",
                     help="one &MOL_SET/&MOLECULE entry, e.g. "
                          "'AMBER:mol1.prmtop:100' (repeatable)")
    mmc.add_argument("--conn-file", default=None, metavar="FILE",
                     help="the single-topology case: no &MOL_SET, one prmtop/PSF")
    mmc.add_argument("--coord", default=None, metavar="FILE",
                     help="the coordinate file: the PDB/inpcrd/XYZ/G96 that goes "
                          "into COORD_FILE_NAME")
    mmc.add_argument("--coord-format", default=None,
                     choices=["PDB", "CRD", "XYZ", "G96"],
                     help="override the format guessed from the file suffix")
    mmc.add_argument("--json", action="store_true", help="machine readable output")

    # ---- validate ----------------------------------------------------- #
    val = sub.add_parser("validate", help="check an existing CP2K input")
    val.add_argument("files", nargs="+", help="input file(s) to check")
    val.add_argument("--data-dir", default=None,
                     help="CP2K data directory for basis/potential checks")
    val.add_argument("--json", action="store_true", help="machine readable output")
    val.add_argument("--strict", action="store_true",
                     help="treat warnings as errors (non-zero exit)")
    val.add_argument("--no-schema", action="store_true",
                     help="skip manual-database checks")

    # ---- info --------------------------------------------------------- #
    info = sub.add_parser("info", help="inspect a CP2K data directory")
    info.add_argument("--data-dir", default=None)
    info.add_argument("--elements", default=None,
                      help="comma-separated elements, e.g. H,C,O,Fe")
    info.add_argument("--kind", default="all", choices=["all", "basis", "potential"],
                      help="only list this kind of data file (default: both)")
    info.add_argument("--target", default="solid",
                      choices=["solid", "molecule", "cheap", "accurate"])
    info.add_argument("--functional", default="PBE")

    # ---- keyword ------------------------------------------------------ #
    kw = sub.add_parser("keyword", help="offline search of the CP2K manual")
    kw.add_argument("term", nargs="?", help="keyword name or fragment (中文也可以)")
    kw.add_argument("--section", default=None,
                    help="restrict to a section path, e.g. FORCE_EVAL/DFT/SCF")
    kw.add_argument("--limit", type=int, default=20)
    kw.add_argument("--all", action="store_true",
                    help="list every keyword of --section")
    kw.add_argument("--cn", action="store_true",
                    help="only show entries that have a Chinese note")
    kw.add_argument("--glossary", action="store_true",
                    help="print the whole English-Chinese terminology list")

    # ---- db ----------------------------------------------------------- #
    db = sub.add_parser("db", help="inspect or rebuild the manual database")
    db.add_argument("--stats", action="store_true",
                    help="print the database inventory (this is what `db` does "
                         "on its own; only --rebuild changes it)")
    db.add_argument("--rebuild", action="store_true",
                    help="crawl the manual again (network required)")
    db.add_argument("--branch", default="cp2k-2025_1-branch")
    db.add_argument("--src", default=None, help="CP2K src/ directory (repeatability)")
    db.add_argument("--workers", type=int, default=8)

    return parser


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-s", "--structure", default=None,
                        help="structure file: xyz, pdb, poscar, cif, a CP2K input, "
                             "or a previous run's <PROJECT>-1.restart (it is a "
                             "complete input, so it carries the coordinates and "
                             "the cell - handy for a relay)")
    parser.add_argument("--data-dir", default=None,
                        help="CP2K data directory (basis sets and potentials)")
    parser.add_argument("--project", default=None,
                        help="PROJECT_NAME (default: cp2k). CP2K names its restart "
                             "files after it, so a fixed name is what makes a second "
                             "run inherit the first one's .wfn / -1.restart; change it "
                             "when several systems share one directory")
    parser.add_argument("--charge", type=int, default=0, help="total charge")
    parser.add_argument("--multiplicity", type=int, default=1,
                        help="spin multiplicity; >1 switches on UKS")
    parser.add_argument("--uks", action="store_true", default=None,
                        help="force unrestricted Kohn-Sham")
    parser.add_argument("--periodic", default=None,
                        choices=["XYZ", "XY", "XZ", "YZ", "X", "Y", "Z", "NONE"],
                        help="cell periodicity")
    parser.add_argument("--vacuum", type=float, default=None,
                        help="pad the molecule with this much vacuum (angstrom)")
    parser.add_argument("--cell", default=None,
                        help="override the cell: a,b,c or a,b,c,alpha,beta,gamma")
    parser.add_argument("--functional", default="PBE",
                        help="exchange-correlation functional (default: PBE)")
    parser.add_argument("--dispersion", default=None,
                        help="dispersion correction: d3, d3bj, d2, rvv10 or none")
    parser.add_argument("--cutoff", type=float, default=None,
                        help="plane-wave cutoff in Ry (default: from the basis set)")
    parser.add_argument("--rel-cutoff", type=float, default=60.0, help="REL_CUTOFF in Ry")
    parser.add_argument("--ngrids", type=int, default=None)
    parser.add_argument("--qs-method", default="GPW", choices=["GPW", "GAPW"],
                        help="Quickstep method (GAPW for all-electron)")
    parser.add_argument("--basis", default=None, help="force a basis set name")
    parser.add_argument("--potential", default=None, help="force a potential name")
    parser.add_argument("--basis-file", default=None, help="force BASIS_SET_FILE_NAME")
    parser.add_argument("--potential-file", default=None,
                        help="force POTENTIAL_FILE_NAME")
    parser.add_argument("--basis-target", default="auto",
                        choices=["auto", "solid", "molecule", "cheap", "accurate"],
                        help="which basis-set family to prefer")
    parser.add_argument("--data-path-style", default="name",
                        choices=["name", "absolute"],
                        help="how to write file names in the input")
    parser.add_argument("--scf", default="auto",
                        choices=["auto", "OT", "DIAGONALIZATION"],
                        help="SCF route (default: OT, diagonalisation for k-points)")
    parser.add_argument("--eps-scf", type=float, default=None)
    parser.add_argument("--max-scf", type=int, default=30)
    parser.add_argument("--outer-max-scf", type=int, default=None,
                        metavar="N",
                        help="&SCF/&OUTER_SCF MAX_SCF (outer loops); 0 switches the "
                             "outer loop off. Default: 20 on the OT route, CP2K's "
                             "own default otherwise")
    parser.add_argument("--outer-eps-scf", type=float, default=None,
                        help="&OUTER_SCF EPS_SCF (default: same as EPS_SCF)")
    parser.add_argument("--added-mos", type=int, default=None,
                        help="number of empty orbitals")
    parser.add_argument("--smearing", action="store_true", default=None,
                        help="Fermi-Dirac smearing (metals)")
    parser.add_argument("--electronic-temperature", type=float, default=300.0,
                        help="smearing temperature in K")
    parser.add_argument("--scf-guess", default="RESTART",
                        choices=["ATOMIC", "RESTART", "HISTORY_RESTART", "RANDOM",
                                 "CORE", "MOPAC", "EHT", "SPARSE", "NONE"],
                        help="initial wavefunction (default: RESTART, i.e. reuse the "
                             "previous run; CP2K itself falls back to ATOMIC when "
                             "the file is missing)")
    parser.add_argument("--wfn-restart-file", default=None, metavar="FILE",
                        help="WFN_RESTART_FILE_NAME for --scf-guess RESTART "
                             "(default: <PROJECT>-RESTART.wfn)")
    parser.add_argument("--no-wfn-print", dest="wfn_restart_print",
                        action="store_false", default=True,
                        help="do not write &SCF/&PRINT/&RESTART (then the next run "
                             "has no <PROJECT>-RESTART.wfn to restart from)")
    parser.add_argument("--wfn-restart-every", type=int, default=0, metavar="N",
                        help="&SCF/&PRINT/&RESTART/&EACH: write the wave function every "
                             "N steps (default 0 = not during the run, only via "
                             "ADD_LAST).  Which iteration level counts is set by "
                             "--wfn-restart-level")
    parser.add_argument("--wfn-restart-level", default=None,
                        choices=["QS_SCF", "MD", "GEO_OPT", "CELL_OPT"],
                        help="&SCF/&PRINT/&RESTART/&EACH/<LEVEL>: which iteration level "
                             "the interval counts.  Default: by task (MD for an MD run, "
                             "GEO_OPT/CELL_OPT for the optimisers, QS_SCF for a single "
                             "point).  QS_SCF counts the SCF iterations *inside one* "
                             "force evaluation")
    parser.add_argument("--no-wfn-add-last", dest="wfn_restart_add_last",
                        action="store_false", default=True,
                        help="&SCF/&PRINT/&RESTART ADD_LAST NO: do not write at the last "
                             "iteration either (with --wfn-restart-every 0 nothing is "
                             "written at all)")
    parser.add_argument("--wfn-backup-copies", type=int, default=None, metavar="N",
                        help="&SCF/&PRINT/&RESTART BACKUP_COPIES (keep N old files)")
    parser.add_argument("--restart", dest="ext_restart", default=None, metavar="FILE",
                        help="continue a previous run: &EXT_RESTART / "
                             "RESTART_FILE_NAME (MD, geo_opt, cell_opt)")
    parser.add_argument("--restart-take", default=None, metavar="LIST",
                        help="接力：只从 --restart 那个文件里取这几样，其余一概不取。"
                             "逗号分隔，可选 " + ", ".join(sorted(EXT_RESTART_TAKES))
                             + "；例如 geo_opt 之后跑 MD 用 `--restart-take pos`"
                               "（只要坐标，不继承优化器的步数计数器，也不去找"
                               "几何优化根本没有的速度）。默认（不给）取全部："
                               "RESTART_DEFAULT T")
    parser.add_argument("--no-restart-default", dest="restart_default",
                        action="store_false", default=True,
                        help="&EXT_RESTART RESTART_DEFAULT F: only take the items "
                             "you switch on explicitly")
    parser.add_argument("--no-motion-restart", dest="motion_restart_print",
                        action="store_false", default=True,
                        help="do not write &MOTION/&PRINT/&RESTART "
                             "(the <PROJECT>-1.restart file)")
    parser.add_argument("--restart-every", type=int, default=20, metavar="STEPS",
                        help="&MOTION/&PRINT/&RESTART/&EACH: write the run restart file "
                             "every N steps (default 20, like CP2K; 0 = not during the run)")
    parser.add_argument("--no-restart-add-last", dest="restart_add_last",
                        action="store_false", default=True,
                        help="&MOTION/&PRINT/&RESTART ADD_LAST NO: do not write at the "
                             "last iteration either")
    parser.add_argument("--restart-backups", type=int, default=None, metavar="N",
                        help="&MOTION/&PRINT/&RESTART BACKUP_COPIES")
    parser.add_argument("--ignore-convergence-failure", action="store_true",
                        help="keep going when the SCF hits MAX_SCF "
                             "(fine for relative comparisons, not for production)")
    # ---- SCF route details (OT and the diagonalisation route) ---------- #
    parser.add_argument("--ot-minimizer", default="DIIS",
                        choices=["SD", "CG", "DIIS", "BROYDEN"],
                        help="&OT MINIMIZER (CP2K default: CG; the tool writes DIIS)")
    parser.add_argument("--ot-preconditioner", default="FULL_SINGLE_INVERSE",
                        choices=["FULL_ALL", "FULL_SINGLE_INVERSE", "FULL_SINGLE",
                                 "FULL_KINETIC", "FULL_S_INVERSE", "NONE"],
                        help="&OT PRECONDITIONER (CP2K default: FULL_KINETIC)")
    parser.add_argument("--ot-algorithm", default="STRICT", choices=["STRICT", "IRAC"],
                        help="&OT ALGORITHM (IRAC = implicit rotational alignment)")
    parser.add_argument("--ot-linesearch", default="2PNT",
                        choices=["ADAPT", "NONE", "2PNT", "3PNT", "GOLD"],
                        help="&OT LINESEARCH")
    parser.add_argument("--ot-stepsize", type=float, default=None,
                        help="&OT STEPSIZE (negative/omitted = CP2K decides)")
    parser.add_argument("--ot-energy-gap", type=float, default=None, metavar="HARTREE",
                        help="&OT ENERGY_GAP: HOMO-LUMO estimate; only the FULL_ALL and "
                             "FULL_SINGLE_INVERSE preconditioners use it")
    parser.add_argument("--ot-rotation", dest="ot_rotation", action="store_true",
                        default=None,
                        help="&OT ROTATION T: needed for fractional occupations (SMEAR)")
    parser.add_argument("--no-ot-rotation", dest="ot_rotation", action="store_false",
                        help="&OT ROTATION F")
    parser.add_argument("--ot-safe-diis", dest="ot_safe_diis", action="store_true",
                        default=None, help="&OT SAFE_DIIS T (CP2K default)")
    parser.add_argument("--no-ot-safe-diis", dest="ot_safe_diis", action="store_false",
                        help="&OT SAFE_DIIS F: accept DIIS steps even when they point "
                             "away from the minimum")
    parser.add_argument("--outer-optimizer", default=None,
                        choices=["SD", "DIIS", "NONE", "BISECT", "BROYDEN", "NEWTON",
                                 "SECANT", "NEWTON_LS"],
                        help="&SCF/&OUTER_SCF OPTIMIZER (omitted = CP2K default NONE)")
    parser.add_argument("--diag-algorithm", default="STANDARD",
                        choices=["STANDARD", "OT", "LANCZOS", "DAVIDSON", "FILTER_MATRIX"],
                        help="&DIAGONALIZATION ALGORITHM")
    parser.add_argument("--level-shift", type=float, default=None, metavar="HARTREE",
                        help="&SCF LEVEL_SHIFT: diagonalisation route only")
    parser.add_argument("--mixing-method", default="BROYDEN_MIXING",
                        choices=["NONE", "DIRECT_P_MIXING", "KERKER_MIXING",
                                 "PULAY_MIXING", "BROYDEN_MIXING",
                                 "MODIFIED_BROYDEN_MIXING", "MULTISECANT_MIXING",
                                 "NEW_PULAY_MIXING"],
                        help="&SCF/&MIXING METHOD (CP2K default: DIRECT_P_MIXING)")
    parser.add_argument("--smear-method", default="FERMI_DIRAC",
                        choices=["FERMI_DIRAC", "ENERGY_WINDOW", "LIST", "GAUSSIAN",
                                 "METHFESSEL_PAXTON", "MARZARI_VANDERBILT"],
                        help="&SCF/&SMEAR METHOD (CP2K default: GAUSSIAN)")
    parser.add_argument("--poisson-solver", default=None,
                        choices=["PERIODIC", "ANALYTIC", "MT", "MULTIPOLE", "WAVELET",
                                 "IMPLICIT"],
                        help="&DFT/&POISSON POISSON_SOLVER. Omitted = the tool picks "
                             "(nothing for a 3-D periodic cell, MT/WAVELET otherwise); "
                             "the solver has to be valid for the cell periodicity")
    parser.add_argument("--mixing-alpha", type=float, default=0.4,
                        help="SCF &MIXING ALPHA (default: 0.4)")
    parser.add_argument("--mixing-nbuffer", type=int, default=8,
                        help="SCF &MIXING NBUFFER (default: 8)")
    parser.add_argument("--mixing-beta", type=float, default=None,
                        help="SCF &MIXING BETA (default: not written)")
    parser.add_argument("--scaled-coords", action="store_true",
                        help="write &COORD SCALED (fractional coordinates; "
                             "needs a cell)")
    parser.add_argument("--thermostat-timecon", type=float, default=100.0,
                        help="&THERMOSTAT/&NOSE|&CSVR TIMECON in fs (default: 100)")
    parser.add_argument("--barostat-timecon", type=float, default=500.0,
                        help="&BAROSTAT TIMECON in fs (default: 500)")
    parser.add_argument("--method", default="QUICKSTEP",
                        choices=["QUICKSTEP", "FIST", "quickspeed", "fist", "classical"],
                        help="FORCE_EVAL METHOD: QUICKSTEP (DFT, the default) or FIST "
                             "(classical force field). With FIST the MD task is a "
                             "classical MD and no &DFT block is written at all")
    parser.add_argument("--mm-route", default="AMBER",
                        choices=["AMBER", "CHARMM", "GROMOS96", "GROMOS87",
                                 "IN_INPUT", "PAIR"],
                        help="which classical force field: AMBER (prmtop + inpcrd), "
                             "CHARMM (PSF + .pot), GROMOS96/87, IN_INPUT (parameters "
                             "written in the input) or PAIR (Lennard-Jones only)")
    parser.add_argument("--mm-path-style", default="name",
                        choices=["name", "auto", "absolute"],
                        help="how to spell the topology/parameter/coordinate file "
                             "names. name (default) writes just the file name - the "
                             "usual 'copy everything next to the input' cluster "
                             "layout; auto writes the bare name only when the file "
                             "really is in the directory the input goes to; absolute "
                             "keeps the full path")
    parser.add_argument("--mm-parm-file", default=None, metavar="FILE",
                        help="&MM/&FORCEFIELD PARM_FILE_NAME (required by every route "
                             "that reads a parameter file)")
    parser.add_argument("--mm-conn-file", default=None, metavar="FILE",
                        help="&TOPOLOGY CONN_FILE_NAME: the topology (prmtop/PSF/.top)")
    parser.add_argument("--mm-coord-file", default=None, metavar="FILE",
                        help="&TOPOLOGY COORD_FILE_NAME: the coordinates (inpcrd/CRD/"
                             "PDB/G96). Not needed for the in-input routes, which use "
                             "&COORD")
    parser.add_argument("--mm-coord-format", default=None,
                        choices=["CRD", "PDB", "XYZ", "G96", "CIF", "XTL", "CP2K"],
                        help="&TOPOLOGY COORDINATE: CRD is AMBER's own coordinate "
                             "format (inpcrd). Default: whatever the route uses")
    parser.add_argument("--mm-molecule", action="append", default=[], metavar="FMT:FILE:NMOL",
                        help="mixed system: one &MOL_SET/&MOLECULE entry, e.g. "
                             "'AMBER:mol1.prmtop:100'. Repeat it for each component")
    parser.add_argument("--mm-ff-file", default=None, metavar="FILE",
                        help="IN_INPUT route: a file holding the &FORCEFIELD terms "
                             "(&CHARGE/&BOND/&BEND/&NONBONDED); a whole &FORCEFIELD or "
                             "&MM block in it is also accepted")
    parser.add_argument("--lj", action="append", default=[], metavar="ELEM:EPS:SIGMA",
                        help="IN_INPUT/PAIR route: a &LENNARD-JONES pair, EPSILON in K "
                             "and SIGMA in angstrom, e.g. 'Ar:119.8:3.405'")
    parser.add_argument("--lj-pair", action="append", default=[], metavar="E1:E2:EPS:SIGMA",
                        help="IN_INPUT/PAIR route: an explicit cross pair, when the "
                             "Lorentz-Berthelot mixing rule is not what you want")
    parser.add_argument("--lj-rcut", type=float, default=None, metavar="A",
                        help="&LENNARD-JONES RCUT in angstrom (default: CP2K's 10)")
    parser.add_argument("--mm-charge", action="append", default=[], metavar="ELEM:Q",
                        help="IN_INPUT/PAIR route: &CHARGE for one element, e.g. 'O:-0.8476'")
    parser.add_argument("--mm-vdw-scale14", type=float, default=None, metavar="F",
                        help="&FORCEFIELD VDW_SCALE14 (default: 0.5 for AMBER, 1.0 for "
                             "CHARMM; CP2K's own default is 1.0)")
    parser.add_argument("--mm-ei-scale14", type=float, default=None, metavar="F",
                        help="&FORCEFIELD EI_SCALE14 (default: 0.8333 for AMBER, 1.0 for "
                             "CHARMM). CP2K's own default is 0.0, which removes every "
                             "1-4 electrostatic term")
    parser.add_argument("--no-shift-cutoff", dest="mm_shift_cutoff",
                        action="store_false", default=None,
                        help="&FORCEFIELD SHIFT_CUTOFF F (CP2K's default is T)")
    parser.add_argument("--mm-multiple-potential", action="store_true", default=None,
                        help="&FORCEFIELD MULTIPLE_POTENTIAL T")
    parser.add_argument("--mm-ignore-missing-params", action="store_true", default=None,
                        help="&FORCEFIELD IGNORE_MISSING_CRITICAL_PARAMS T: run as if the "
                             "terms with missing parameters were zero")
    parser.add_argument("--mm-spline-emax", type=float, default=None, metavar="HARTREE",
                        help="&FORCEFIELD/&SPLINE EMAX_SPLINE")
    parser.add_argument("--mm-ewald", default=None,
                        choices=["SPME", "PME", "EWALD", "NONE"],
                        help="&MM/&POISSON/&EWALD EWALD_TYPE (default: SPME for a "
                             "periodic cell, NONE otherwise)")
    parser.add_argument("--mm-ewald-alpha", type=float, default=None,
                        help="&EWALD ALPHA (default: CP2K derives it from EWALD_ACCURACY)")
    parser.add_argument("--mm-ewald-gmax", type=int, default=None,
                        help="&EWALD GMAX (number of grid points)")
    parser.add_argument("--mm-o-spline", type=int, default=None,
                        help="&EWALD O_SPLINE: SPME/PME interpolation order")
    parser.add_argument("--mm-rcut", type=float, default=None, metavar="A",
                        help="&EWALD RCUT: real-space cutoff of the Ewald sum")
    parser.add_argument("--mm-verlet-skin", type=float, default=None, metavar="A",
                        help="&MM/&NEIGHBOR_LISTS VERLET_SKIN")
    parser.add_argument("--mm-stress-tensor", dest="mm_stress_tensor",
                        action="store_true", default=None,
                        help="&FORCE_EVAL STRESS_TENSOR ANALYTICAL even when the cell "
                             "does not change")
    parser.add_argument("--no-mm-stress-tensor", dest="mm_stress_tensor",
                        action="store_false",
                        help="do not write STRESS_TENSOR (default: written for CELL_OPT "
                             "and the NPT ensembles)")
    parser.add_argument("--ensemble", default="NVT",
                        choices=["NVE", "NVT", "NPT_I", "NPT_F", "NPT_IA", "NPE_I",
                                 "NPE_F", "LANGEVIN", "MSST", "ISOKIN"],
                        help="MD ensemble")
    parser.add_argument("--temperature", type=float, default=300.0, help="temperature in K")
    parser.add_argument("--timestep", type=float, default=None, metavar="FS",
                        help="MD timestep in fs (default: 0.5 for AIMD, 1.0 for a "
                             "classical force field)")
    parser.add_argument("--steps", type=int, default=5000, help="number of MD steps")
    parser.add_argument("--thermostat", default="NOSE",
                        choices=["NOSE", "CSVR", "GLE", "AD_LANGEVIN", "NONE"])
    # ---- heating / cooling ------------------------------------------------ #
    parser.add_argument("--temperature-ramp", default=None, metavar="FROM:TO",
                        help="heat/cool: go from FROM K to TO K over --steps steps, "
                             "e.g. `--temperature-ramp 300:600`.  The factor CP2K "
                             "needs is worked out for you ((TO/FROM)^(1/steps)) and "
                             "the resulting schedule is printed.  LANGEVIN moves the "
                             "thermostat setpoint (recommended; the tool then also "
                             "writes &LANGEVIN GAMMA), NVE/NPE_* rescale velocities "
                             "(&MD ANNEALING).  NVT/NPT cannot do either - CP2K "
                             "aborts or ignores it")
    parser.add_argument("--temperature-annealing", type=float, default=None,
                        metavar="FACTOR",
                        help="&MD TEMPERATURE_ANNEALING: multiply the Langevin target "
                             "temperature by FACTOR after every step (<1 cooling, "
                             ">1 heating).  Only the Langevin ensemble applies it")
    parser.add_argument("--annealing", type=float, default=None, metavar="FACTOR",
                        help="&MD ANNEALING: multiply the velocities by FACTOR after "
                             "every step (temperature follows the square).  Not a "
                             "thermostat - ensembles without a particle thermostat only")
    parser.add_argument("--annealing-cell", type=float, default=None, metavar="FACTOR",
                        help="&MD ANNEALING_CELL: the same for the cell's velocities "
                             "(NPE_I/NPE_F only)")
    parser.add_argument("--langevin-gamma", type=float, default=0.01, metavar="FS-1",
                        help="&MD/&LANGEVIN GAMMA in fs^-1 (default 0.01 = 10 ps^-1). "
                             "CP2K's own default is 0, i.e. no friction and no noise - "
                             "the run is then just NVE and a temperature ramp does "
                             "nothing")
    parser.add_argument("--restart-temperature-annealing", action="store_true",
                        help="&EXT_RESTART/RESTART_TEMPERATURE_ANNEALING T: when "
                             "continuing a run (--restart), keep ramping from the "
                             "target temperature saved in the restart file instead of "
                             "restarting from this input's --temperature")
    # ---- metadynamics ----------------------------------------------------- #
    parser.add_argument("--metadyn", action="store_true",
                        help="&MOTION/&FREE_ENERGY/&METADYN: metadynamics (MD only). "
                             "Add collective variables with --cv and let the bias act "
                             "on them with --metavar (default: every CV)")
    parser.add_argument("--meta-plumed", default=None, metavar="FILE",
                        help="USE_PLUMED T + PLUMED_INPUT_FILE FILE: hand the bias to "
                             "an external PLUMED driver, which then also defines the "
                             "CVs (do not combine with --cv). CP2K only reads the file "
                             "name - CV/hills/walls/output come from that file")
    parser.add_argument("--meta-plumed-draft", default=None, metavar="FILE",
                        help="write a plumed.dat draft from the same --cv/--metavar/"
                             "--meta-* settings: PLUMED syntax, units converted to "
                             "nm/kJ/mol, and a comment wherever something needs your "
                             "decision ('-' prints it to stdout)")
    parser.add_argument("--meta-hill-height", type=float, default=1.0e-3,
                        metavar="HARTREE",
                        help="WW: height of the Gaussian that is spawned. CP2K's own "
                             "default is 0.1, which is huge - the usual start is "
                             "0.5-2 mHartree")
    parser.add_argument("--meta-nt-hills", type=int, default=30, metavar="STEPS",
                        help="NT_HILLS: **MD steps** between two hills (not a number "
                             "of hills; CP2K's own default is 30). A negative value "
                             "spawns nothing and only uses hills carried in by "
                             "RESTART_METADYNAMICS")
    parser.add_argument("--meta-wt", action="store_true",
                        help="WELL_TEMPERED T: well-tempered metadynamics (needs "
                             "--meta-delta-t; the reference temperature is the MD "
                             "temperature, so DELTA_T alone is enough)")
    parser.add_argument("--meta-sigma", type=float, default=None, metavar="SIGMA",
                        help="the default hill width: written as SCALE in each "
                             "&METAVAR that has none of its own (native route) and as "
                             "METAD SIGMA=... in the plumed.dat draft. Without it a "
                             "&METAVAR has CP2K's default SCALE 0, which is read as "
                             "'infinitely wide', i.e. no bias at all. Unit follows the "
                             "CV: angstrom for distances, radians for angles")
    parser.add_argument("--meta-delta-t", type=float, default=None, metavar="K",
                        help="DELTA_T: the well-tempered bias factor (T+DELTA_T)/DELTA_T")
    parser.add_argument("--meta-min-disp", type=float, default=None, metavar="D",
                        help="MIN_DISP: only spawn a hill after the CV moved this far")
    parser.add_argument("--meta-tail-cutoff", type=float, default=None, metavar="C",
                        help="HILL_TAIL_CUTOFF: truncate the Gaussian tails")
    parser.add_argument("--meta-lagrange", action="store_true",
                        help="LAGRANGE T: run the CVs on their own (extended "
                             "Lagrangian) dynamics; give each CV a MASS in --metavar")
    parser.add_argument("--meta-langevin", action="store_true",
                        help="LANGEVIN T: integrate those CVs with a Langevin "
                             "thermostat - the per-CV GAMMA is only read when this is "
                             "on (needs --meta-lagrange)")
    parser.add_argument("--meta-print-every", type=int, default=1, metavar="STEPS",
                        help="&METADYN/&PRINT/&COLVAR|&HILLS &EACH MD n (default 1). "
                             "Native route only: on the PLUMED route CP2K reads only "
                             "the file name, and the hills file comes from plumed.dat")
    parser.add_argument("--no-meta-print", dest="meta_print", action="store_false",
                        default=True,
                        help="do not write the .colvar/.hills files (native route "
                             "only; they are what a continuation and the "
                             "free-energy plot need)")
    parser.add_argument("--plumed-cv", action="append", default=[], metavar="SPEC",
                        help="a PLUMED-native CV, 'label:ACTION:params' with an "
                             "optional ':component=NAME' / ':sigma=NM' / "
                             "':note=TEXT' tail, e.g. "
                             "'cn:COORDINATIONNUMBER:SPECIES=1-100 R_0=0.33 MEAN:"
                             "component=mean'. Lengths are nm (PLUMED's unit) - "
                             "this table is not translated from the CP2K one")
    parser.add_argument("--plumed-wall", action="append", default=[],
                        metavar="SPEC",
                        help="a PLUMED wall, 'ARG:KIND:AT:KAPPA[:EXP]', e.g. "
                             "'cn.mean:UPPER_WALLS:6.0:5000' (AT in the CV's unit, "
                             "KAPPA in kJ/mol/nm²)")
    parser.add_argument("--plumed-line", action="append", default=[],
                        metavar="LINE",
                        help="a raw PLUMED line written into the draft untouched "
                             "(OPES_METAD, PCAVARS, PIV, ... - anything the table "
                             "does not model). Repeatable")
    parser.add_argument("--plumed-free-where", choices=["before", "end"],
                        default="before",
                        help="where the --plumed-line lines go: just before METAD "
                             "(default) or at the end of the file")
    parser.add_argument("--plumed-pace", type=int, default=None, metavar="STEPS",
                        help="PLUMED METAD PACE (hill interval in MD steps)")
    parser.add_argument("--plumed-height", type=float, default=None,
                        metavar="KJMOL",
                        help="PLUMED METAD HEIGHT in **kJ/mol** (not Hartree - the "
                             "draft is written in PLUMED's units)")
    parser.add_argument("--plumed-sigma", type=float, default=None, metavar="NM",
                        help="PLUMED METAD SIGMA in **nm** for the CVs without one "
                             "of their own")
    parser.add_argument("--plumed-biasfactor", type=float, default=None,
                        metavar="G", help="PLUMED METAD BIASFACTOR (>1 turns on "
                                          "well-tempered metadynamics)")
    parser.add_argument("--plumed-temp", type=float, default=None, metavar="K",
                        help="PLUMED METAD TEMP; without it the temperature from "
                             "the generate page is used")
    parser.add_argument("--plumed-units", choices=["nm", "A"], default="nm",
                        help="length unit of the PLUMED file: PLUMED's own nm "
                             "(default) or A, which writes "
                             "'UNITS LENGTH=A TIME=fs ENERGY=kJ/mol' as the first "
                             "line - every length you give is then read as Å, and "
                             "HILLS/COLVAR come out in Å (CP2K's own unit)")
    parser.add_argument("--plumed-label", default=None, metavar="LABEL",
                        help="label of the METAD action (default m); other PLUMED "
                             "lines reference its bias as LABEL.bias")
    parser.add_argument("--plumed-restart", action="store_true",
                        help="add RESTART=YES to METAD: read the existing hills file "
                             "back and continue (PLUMED errors if it is missing). "
                             "CP2K's own RESTART_METADYNAMICS is inert on this route")
    parser.add_argument("--plumed-adaptive", choices=["DIFF", "GEOM"], default=None,
                        help="let PLUMED adapt the hill width (METAD ADAPTIVE=): GEOM "
                             "= cover a Cartesian distance, DIFF = cover a time "
                             "window. SIGMA then holds the single starting value, and "
                             "sum_hills needs the histogram correction")
    parser.add_argument("--plumed-sigma-min", default=None, metavar="S",
                        help="lower bound(s) for the adaptive hill width, one per "
                             "CV: '0.2' or '0.2,0.01' (PLUMED wants one value per "
                             "ARG; a single value is repeated for all of them, and a "
                             "negative number means 'no bound')")
    parser.add_argument("--plumed-sigma-max", default=None, metavar="S",
                        help="upper bound(s) for the adaptive hill width, same "
                             "syntax as --plumed-sigma-min")
    parser.add_argument("--plumed-extra", action="append", default=[],
                        metavar="KEY=VAL",
                        help="a METAD keyword the form does not model, appended to the "
                             "METAD line verbatim (GRID_MIN=..., INTERVAL=..., "
                             "WALKERS_N=...). Repeatable")
    parser.add_argument("--plumed-contact", action="append", default=[],
                        metavar="SPEC",
                        help="generate a contact CV table from molecule blocks, e.g. "
                             "'blocks=19x1,8x20;center=2;center_atoms=1;acceptor=2;"
                             "acceptor_atoms=0,2;r0=1.55;nn=12;mm=24;combine=cv_uu' "
                             "(; separates the fields, the block list keeps its "
                             "commas). Repeatable")
    parser.add_argument("--plumed-import", default=None, metavar="FILE",
                        help="read an existing plumed.dat into the PLUMED-native table; "
                             "the other --plumed-* flags then override what they name. "
                             "What the form cannot place is reported on stderr and "
                             "kept verbatim in the draft")
    parser.add_argument("--plumed-print", dest="plumed_print", action="store_true",
                        default=True,
                        help="write the PRINT ... FILE=COLVAR line (default; useful "
                             "to turn it back on after importing a file without one)")
    parser.add_argument("--plumed-args", default=None, metavar="LABELS",
                        help="comma-separated labels for the METAD ARG list (default: "
                             "the table's 'leaf' scalars - the ones no other action "
                             "points at). A table often holds intermediate quantities "
                             "that are combined into the CV you actually bias, e.g. "
                             "'--plumed-args cv_uu'")
    parser.add_argument("--no-plumed-print", dest="plumed_print",
                        action="store_false", default=True,
                        help="do not write the PRINT ... FILE=COLVAR line")
    parser.add_argument("--cv", action="append", default=[], metavar="SPEC",
                        help="a collective variable, e.g. 'distance:1,2', "
                             "'angle:1,2,3', 'torsion:1,2,3,4' or "
                             "'coordination:O,H,1.2,6,8' (KINDS_FROM,KINDS_TO,R0,NN,ND; "
                             "use '-' to skip one). Repeatable; the order is the order "
                             "&METAVAR COLVAR n counts in. &COMBINE_COLVAR and "
                             "&DISTANCE_FROM_PATH nest further blocks - add those in "
                             "the GUI's 元动力学 page")
    parser.add_argument("--metavar", action="append", default=[], metavar="SPEC",
                        help="how the bias acts on a CV: 'COLVAR[:SCALE=s][:MASS=m]"
                             "[:GAMMA=g][:LAMBDA=l][:WALL=TYPE,POSITION,DIRECTION,K]'. "
                             "COLVAR is the 1-based index of the --cv entry. Default: "
                             "every --cv gets one &METAVAR with SCALE 1")
    parser.add_argument("--pressure", type=float, default=1.0, help="barostat pressure (bar)")
    parser.add_argument("--seed", type=int, default=None, help="RNG seed")
    parser.add_argument("--optimizer", default="BFGS",
                        choices=["BFGS", "LBFGS", "CG"], help="geometry optimiser")
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--keep-angles", action="store_true",
                        help="CELL_OPT: keep the cell angles fixed")
    parser.add_argument("--external-pressure", type=float, default=0.0,
                        help="CELL_OPT: external pressure in bar")
    parser.add_argument("--intensities", action="store_true", default=True,
                        help="IR intensities (freq task)")
    parser.add_argument("--no-intensities", dest="intensities", action="store_false",
                        help="skip the IR intensities (they need an isolated system)")
    parser.add_argument("--no-dipole", dest="print_dipole", action="store_false",
                        default=True,
                        help="do not ask for the dipole moment (&MOMENTS)")
    parser.add_argument("--thermochemistry", action="store_true")
    parser.add_argument("--kpath", default=None,
                        help='band path, e.g. "GAMMA-X-M-GAMMA" or "0,0,0;0.5,0,0"')
    parser.add_argument("--lattice", default=None,
                        choices=["cub", "fcc", "bcc", "hex", "tet", "orc", "generic"],
                        help="override the Bravais lattice used for k-point labels")
    parser.add_argument("--npoints", type=int, default=40,
                        help="total k-points along the band path")
    parser.add_argument("--kpoint-grid", default=None,
                        help="&KPOINTS Monkhorst-Pack grid, e.g. 4,4,4. Works for "
                             "every Quickstep task, not just band/PDOS (default: "
                             "automatic from the cell and --kpoint-spacing)")
    parser.add_argument("--kpoint-spacing", type=float, default=None, metavar="ANG",
                        help="target spacing for the automatic grid in angstrom "
                             "(default 20): the grid is the smallest one whose "
                             "reciprocal spacing stays below it")
    parser.add_argument("--kpoint-scheme", default=None,
                        help="raw CP2K SCHEME string, e.g. 'MONKHORST-PACK 6 6 6', "
                             "'GAMMA' or 'GENERAL'")
    parser.add_argument("--kpoint-point", action="append", default=[],
                        metavar="X,Y,Z[,W]",
                        help="explicit k-point (&KPOINTS KPOINT line, SCHEME GENERAL "
                             "is implied when --kpoint-scheme is not given); repeat "
                             "for several points")
    parser.add_argument("--kpoint-units", default="B_VECTOR",
                        choices=["B_VECTOR", "CART_ANGSTROM", "CART_BOHR"],
                        help="&KPOINTS UNITS for the explicit points")
    parser.add_argument("--kpoint-gamma-centered", action="store_true",
                        help="&KPOINTS GAMMA_CENTERED T: shift the mesh onto Gamma")
    parser.add_argument("--kpoint-full-grid", action="store_true",
                        help="&KPOINTS FULL_GRID T: keep every point of the mesh "
                             "instead of the symmetry-reduced set")
    parser.add_argument("--no-kpoint-symmetry", dest="kpoint_symmetry",
                        action="store_false", default=True,
                        help="&KPOINTS SYMMETRY F (the tool's default is T)")
    parser.add_argument("--kpoint-wavefunctions", default="REAL",
                        choices=["REAL", "COMPLEX"],
                        help="&KPOINTS WAVEFUNCTIONS (CP2K's default is COMPLEX)")
    parser.add_argument("--kpoint-verbose", action="store_true",
                        help="&KPOINTS VERBOSE T: list the k-points in the output")
    parser.add_argument("--mo-cubes", action="store_true",
                        help="&DFT/&PRINT/&MO_CUBES: write orbital cubes")
    parser.add_argument("--mo-cubes-nhomo", type=int, default=1,
                        help="&MO_CUBES NHOMO (default 1 = the HOMO)")
    parser.add_argument("--mo-cubes-nlumo", type=int, default=0,
                        help="&MO_CUBES NLUMO (default 0; 1 adds the LUMO)")
    parser.add_argument("--mo-cubes-stride", default=None, metavar="N N N",
                        help="&MO_CUBES STRIDE (CP2K default 2 2 2; 1 1 1 is the "
                             "full grid)")
    parser.add_argument("--density-cube", action="store_true",
                        help="&DFT/&PRINT/&TOT_DENSITY_CUBE: write the total density")
    parser.add_argument("--density-cube-stride", default=None, metavar="N N N")
    parser.add_argument("--fixed-atoms", default=None, metavar="LIST",
                        help="&MOTION/&CONSTRAINT/&FIXED_ATOMS LIST, CP2K list syntax "
                             "(e.g. '1 3 5..8' or '1..12'); needs a motion task")
    parser.add_argument("--fixed-atoms-components", default="XYZ",
                        choices=["X", "Y", "Z", "XY", "XZ", "YZ", "XYZ"],
                        help="&FIXED_ATOMS COMPONENTS_TO_FIX (default XYZ)")
    parser.add_argument("--eps-default", type=float, default=None,
                        help="&QS EPS_DEFAULT: the master SCF threshold "
                             "(CP2K default 1e-10)")
    parser.add_argument("--extrapolation", default=None,
                        choices=["USE_GUESS", "USE_PREV_P", "USE_PREV_RHO_R",
                                 "USE_PREV_WF", "LINEAR_WF", "LINEAR_P",
                                 "LINEAR_PS", "PS", "ASPC"],
                        help="&QS EXTRAPOLATION (default: the tool uses ASPC while "
                             "MD/geometry steps run, USE_GUESS otherwise)")
    parser.add_argument("--pdos-ldos", action="append", default=[],
                        metavar="LIST",
                        help="PDOS: &LDOS LIST, e.g. --pdos-ldos '1 2 3'")
    parser.add_argument("--pdos-nlumo", type=int, default=None)
    parser.add_argument("--pdos-broaden", type=float, default=None,
                        help="DOS/PDOS broadening FWHM in eV (CP2K 2026.2 &CURVE)")
    parser.add_argument("--pdos-broaden-type", default="GAUSSIAN",
                        choices=["GAUSSIAN", "LORENTZIAN", "PSEUDO_VOIGT"])
    parser.add_argument("--band-type", default="CI-NEB",
                        choices=["CI-NEB", "IT-NEB", "B-NEB", "D-NEB", "SM", "EB"])
    parser.add_argument("--replica", action="append", default=[], metavar="XYZ",
                        help="NEB: one reactant/product/intermediate image file")
    parser.add_argument("--replicas", type=int, default=0,
                        help="NEB: number of images (default: from --replica)")
    parser.add_argument("--k-spring", type=float, default=0.05)
    parser.add_argument("--print-level", default="MEDIUM",
                        choices=["SILENT", "LOW", "MEDIUM", "HIGH", "DEBUG"])
    parser.add_argument("--walltime", type=int, default=None, help="WALLTIME in seconds")
    parser.add_argument("--mulliken", action="store_true")
    parser.add_argument("--no-forces", action="store_true",
                        help="do not print forces")
    parser.add_argument("--molden", action="store_true",
                        help="&DFT/&PRINT/&MO_MOLDEN: write the molecular orbitals in "
                             "Molden format for a viewer (Molden/Avogadro/Jmol). "
                             "CP2K skips this silently when the run uses k-points")
    parser.add_argument("--molden-file", default=None, metavar="NAME",
                        help="&MO_MOLDEN FILENAME (default: CP2K's MOS -> "
                             "<PROJECT>-MOS.molden)")
    parser.add_argument("--molden-nlumo", type=int, default=0, metavar="N",
                        help="&MO_MOLDEN NLUMO: virtual orbitals to include "
                             "(0 = occupied only, the default; -1 = all available). "
                             "OT generates them after the SCF; on the "
                             "diagonalisation route cp2kbuild adds ADDED_MOS")
    parser.add_argument("--molden-unit", default="ANGSTROM",
                        choices=["ANGSTROM", "BOHR"],
                        help="&MO_MOLDEN UNIT: coordinates/cell *inside the Molden "
                             "file* (CP2K's own default is BOHR)")
    parser.add_argument("--molden-gto-kind", default="SPHERICAL",
                        choices=["SPHERICAL", "CARTESIAN"],
                        help="&MO_MOLDEN GTO_KIND (spherical is what viewers expect)")
    parser.add_argument("--molden-write-pseudo", action="store_true",
                        help="&MO_MOLDEN WRITE_PSEUDO T: include the [Pseudo] block")
    parser.add_argument("--molden-write-cell", action="store_true",
                        help="&MO_MOLDEN WRITE_CELL T: include the [Cell] block")
    parser.add_argument("--no-molden-vib", dest="molden_vib",
                        action="store_false", default=True,
                        help="freq: do not write &VIBRATIONAL_ANALYSIS/&PRINT/"
                             "&MOLDEN_VIB (the normal modes in Molden format)")
    parser.add_argument("--trajectory-stride", type=int, default=10)
    parser.add_argument("--no-validate", action="store_true",
                        help="skip the validation pass on the generated input")
    parser.add_argument("--quiet", action="store_true")


# --------------------------------------------------------------------------- #
# shared set-up
# --------------------------------------------------------------------------- #
def parse_floats(text: str | None) -> list[float]:
    return parse_numbers(text)


def parse_cell(text: str | None) -> Cell | None:
    try:
        return parse_cell_text(text)
    except ValueError as exc:
        raise SystemExit(f"--cell: {exc}") from exc


def prepare_structure(args) -> Structure | None:
    """Read the structure file; ``build`` applies vacuum/cell/periodic itself."""
    if not args.structure:
        return None
    return read_structure(args.structure)


def _warn_unused_mixing(spec: Spec, args) -> None:
    """Say so when &MIXING will not be written, instead of silently ignoring.

    ``&MIXING`` only appears on the plain diagonalisation route: the orbital
    transformation does not use it, and the smearing branch leaves CP2K's own
    default in place on purpose (metals usually want a smaller ALPHA than the
    0.4 the tool writes for insulators).
    """
    changed = (args.mixing_alpha != 0.4 or args.mixing_nbuffer != 8
               or args.mixing_beta is not None
               or args.mixing_method != "BROYDEN_MIXING")
    if not changed:
        return
    if is_classical(spec):
        print("WARN  --mixing-* 不会生效：METHOD FIST 是经典力场，没有 SCF 需要混合。",
              file=sys.stderr)
        return
    route = effective_route(spec)
    if route == "DIAGONALIZATION" and not spec.smearing:
        return
    reason = ("SCF 走的是 OT（或系统判定为 OT）" if route != "DIAGONALIZATION"
              else "开了 SMEAR（smearing 路线不写 &MIXING，用 CP2K 自己的默认混合）")
    print(f"WARN  --mixing-* 不会生效：{reason}。", file=sys.stderr)


def _mm_charge_args(entries: list[str]) -> list[tuple[str, float]]:
    """``--mm-charge ELEM:Q`` -> ``[("O", -0.8476), ...]``."""
    out: list[tuple[str, float]] = []
    for entry in entries or []:
        element, _, charge = entry.partition(":")
        if not element or not charge:
            raise SystemExit(f"--mm-charge expects ELEM:Q, got {entry!r}")
        try:
            out.append((element, float(charge)))
        except ValueError as exc:
            raise SystemExit(f"--mm-charge {entry!r}: {exc}") from exc
    return out


#: ``&TOPOLOGY CONNECTIVITY`` values that may prefix a ``--mm-molecule`` entry.
MM_CONN_FORMATS = ("AMBER", "PSF", "UPSF", "G96", "G87", "GENERATE", "OFF",
                   "USER", "MOL_SET")


def _mm_molecule_args(entries: list[str]) -> list[MmMolecule]:
    """``--mm-molecule [FMT:]FILE:NMOL`` -> :class:`MmMolecule` list.

    The format prefix is optional (AMBER is assumed).  The split is done from
    the right so that a Windows path such as ``C:\\data\\mol.prmtop`` survives.
    """
    out: list[MmMolecule] = []
    for entry in entries or []:
        text = entry
        fmt = "AMBER"
        head, _, rest = text.partition(":")
        if rest and head.upper() in MM_CONN_FORMATS:
            fmt, text = head.upper(), rest
        path, _, count = text.rpartition(":")
        if not path or not count:
            raise SystemExit("--mm-molecule expects [FMT:]FILE:NMOL "
                             f"(e.g. AMBER:mol1.prmtop:100), got {entry!r}")
        try:
            nmol = int(count)
        except ValueError as exc:
            raise SystemExit(f"--mm-molecule {entry!r}: NMOL must be an integer") from exc
        out.append(MmMolecule(path, fmt, nmol))
    return out


def _mm_lj_args(per_element: list[str], pairs: list[str],
                default_rcut: float | None) -> tuple[list, dict]:
    """``--lj ELEM:EPS:SIGMA`` and ``--lj-pair E1:E2:EPS:SIGMA``."""
    table: dict[str, tuple[float, float]] = {}
    for entry in per_element or []:
        parts = entry.split(":")
        if len(parts) != 3:
            raise SystemExit(f"--lj expects ELEM:EPSILON:SIGMA, got {entry!r}")
        element, epsilon, sigma = parts
        try:
            table[element] = (float(epsilon), float(sigma))
        except ValueError as exc:
            raise SystemExit(f"--lj {entry!r}: {exc}") from exc
    explicit = []
    for entry in pairs or []:
        parts = entry.split(":")
        if len(parts) != 4:
            raise SystemExit(f"--lj-pair expects E1:E2:EPSILON:SIGMA, got {entry!r}")
        first, second, epsilon, sigma = parts
        try:
            explicit.append(MmLennardJones([first, second], float(epsilon),
                                           float(sigma)))
        except ValueError as exc:
            raise SystemExit(f"--lj-pair {entry!r}: {exc}") from exc
    return lennard_jones_pairs(explicit, table, default_rcut), table


def _restart_take(text: str | None) -> tuple[str, ...] | None:
    """``--restart-take pos,vel`` -> the tuple of short names (``None`` = all)."""
    from .recipes import EXT_RESTART_TAKES

    if not text:
        return None
    if text.strip().lower() in ("all", "全部"):
        return None
    keys: list[str] = []
    for raw in text.replace(";", ",").split(","):
        key = raw.strip().lower().replace("-", "_")
        if not key:
            continue
        if key in ("none", "无"):
            return ()
        if key not in EXT_RESTART_TAKES:
            raise SystemExit(
                f"--restart-take: 不认识的量 {raw.strip()!r}；可用的是 "
                + ", ".join(sorted(EXT_RESTART_TAKES)))
        keys.append(key)
    return tuple(dict.fromkeys(keys))


def _kpoint_points_args(values: list[str]) -> list[tuple[float, float, float, float]]:
    """``--kpoint-point x,y,z[,w]`` -> the ``KPOINT`` lines' numbers."""
    out: list[tuple[float, float, float, float]] = []
    for raw in values:
        numbers = parse_floats(raw)
        if len(numbers) not in (3, 4):
            raise SystemExit(f"--kpoint-point expects x,y,z or x,y,z,w (got {raw!r})")
        coords = [float(v) for v in numbers[:3]]
        weight = float(numbers[3]) if len(numbers) == 4 else 1.0
        out.append((coords[0], coords[1], coords[2], weight))
    return out


def _cv_number(text: str, entry: str, what: str, cast):
    """One number out of a ``--cv`` field, with a message that quotes the entry."""
    try:
        return cast(text)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"--cv {entry!r}: {what} 不是数字（{text!r}）") from exc


def _cv_args(entries: list[str]) -> tuple:
    """``--cv distance:1,2`` -> a :class:`~cp2kbuild.metadyn.Cv` tuple.

    Only the CVs whose whole definition fits on one line are accepted here.  A
    combined CV and a path/RMSD CV carry nested (``&COMBINE_COLVAR``) or repeated
    (``&FRAME``) sub-blocks, which the 元动力学 GUI page edits as a table - a
    one-line CLI syntax for them would be a puzzle, not a shortcut.
    """
    from .metadyn import Cv

    out = []
    for entry in entries or []:
        kind, _, rest = entry.partition(":")
        kind = kind.strip().lower()
        fields = [field.strip() for field in rest.split(",")] if rest else []
        if kind in ("distance", "angle", "torsion"):
            atoms = tuple(_cv_number(field, entry, "原子序号", int)
                          for field in fields if field not in ("", "-"))
            out.append(Cv(kind=kind, atoms=atoms))
            continue
        if kind == "coordination":
            if len(fields) < 2 or not fields[0] or not fields[1]:
                raise SystemExit(f"--cv {entry!r}: coordination 要写成 "
                                 "coordination:KINDS_FROM,KINDS_TO[,R0,NN,ND]")
            optional = (fields + ["", "", ""])[2:5]
            out.append(Cv(
                kind="coordination",
                kinds_from=fields[0], kinds_to=fields[1],
                r0=(_cv_number(optional[0], entry, "R0", float)
                    if optional[0] not in ("", "-") else None),
                nn=(_cv_number(optional[1], entry, "NN", int)
                    if optional[1] not in ("", "-") else None),
                nd=(_cv_number(optional[2], entry, "ND", int)
                    if optional[2] not in ("", "-") else None)))
            continue
        raise SystemExit(f"--cv {entry!r}: 不认识的类型 {kind!r}"
                         "（可选 distance、angle、torsion、coordination）")
    return tuple(out)


#: the wall types ``--metavar ... WALL=`` accepts, and how many numbers follow
def _metavar_args(entries: list[str], ncvs: int) -> tuple:
    """``--metavar '1:SCALE=0.5:WALL=QUADRATIC,2.4,WALL_MINUS,0.5'``."""
    from .metadyn import Metavar, Wall

    out = []
    for entry in entries or []:
        head, _, rest = entry.partition(":")
        if not head.strip():
            raise SystemExit(f"--metavar {entry!r}: 要写成 COLVAR[:SCALE=s]...")
        index = _cv_number(head.strip(), entry, "COLVAR", int)
        values: dict = {}
        wall = None
        for item in (piece for piece in rest.split(":") if piece.strip()):
            key, _, text = item.partition("=")
            key = key.strip().upper()
            text = text.strip()
            if not text:
                raise SystemExit(f"--metavar {entry!r}: {key} 没有值")
            if key in ("SCALE", "MASS", "GAMMA", "LAMBDA"):
                values[key.lower()] = _cv_number(text, entry, key, float)
                continue
            if key == "WALL":
                parts = [piece.strip() for piece in text.split(",")]
                wall_type = (parts[0] or "QUADRATIC").upper()
                if wall_type == "NONE":
                    continue
                wall = Wall(
                    type=wall_type,
                    position=(_cv_number(parts[1], entry, "WALL 位置", float)
                              if len(parts) > 1 and parts[1] else None),
                    direction=((parts[2] or "WALL_PLUS").upper()
                               if len(parts) > 2 else "WALL_PLUS"),
                    k=(_cv_number(parts[3], entry, "WALL K", float)
                       if len(parts) > 3 and parts[3] else None))
                continue
            raise SystemExit(f"--metavar {entry!r}: 不认识的项 {key}（可选 "
                             "SCALE、MASS、GAMMA、LAMBDA、WALL）")
        if ncvs and not 1 <= index <= ncvs:
            raise SystemExit(f"--metavar {entry!r}: COLVAR {index} 超出范围"
                             f"（一共 {ncvs} 个 --cv）")
        out.append(Metavar(colvar=index, wall=wall, **values))
    return tuple(out)


def _plumed_cv_args(entries) -> tuple:
    """``--plumed-cv label:ACTION:params[:k=v ...]`` -> PlumedCv objects."""
    from .plumedcv import PlumedCv

    out = []
    for entry in entries:
        parts = str(entry).split(":")
        if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
            raise SystemExit(f"--plumed-cv {entry!r}: 至少要写 'label:ACTION'，"
                             "参数跟在第三个字段里")
        label, action = parts[0].strip(), parts[1].strip()
        params = parts[2].strip() if len(parts) > 2 else ""
        options = {"component": "", "note": ""}
        sigma = None
        for extra in parts[3:]:
            key, sign, value = extra.partition("=")
            key = key.strip().lower()
            if not sign or key not in ("component", "sigma", "note"):
                raise SystemExit(f"--plumed-cv {entry!r}: 第四段之后只能是 "
                                 "component= / sigma= / note=")
            if key == "sigma":
                try:
                    sigma = float(value)
                except ValueError:
                    raise SystemExit(f"--plumed-cv {entry!r}: sigma 要是数字"
                                     "（nm）") from None
            else:
                options[key] = value.strip()
        out.append(PlumedCv(label=label, action=action.upper(), params=params,
                            component=options["component"], sigma=sigma,
                            note=options["note"]))
    return tuple(out)


def _plumed_wall_args(entries) -> tuple:
    """``--plumed-wall ARG:KIND:AT:KAPPA[:EXP]`` -> PlumedWall objects."""
    from .plumedcv import PlumedWall

    out = []
    for entry in entries:
        parts = [part.strip() for part in str(entry).split(":")]
        if len(parts) < 4:
            raise SystemExit(f"--plumed-wall {entry!r}: 要写 "
                             "'ARG:KIND:AT:KAPPA[:EXP]'，例如 "
                             "'cn.mean:UPPER_WALLS:6.0:5000'")
        arg, kind, at, kappa = parts[:4]
        try:
            values = [float(part) for part in parts[4:5]]
        except ValueError:
            raise SystemExit(f"--plumed-wall {entry!r}: EXP 要是数字") from None
        try:
            out.append(PlumedWall(arg=arg, kind=kind.upper(), at=float(at),
                                  kappa=float(kappa),
                                  exp=values[0] if values else None))
        except ValueError:
            raise SystemExit(f"--plumed-wall {entry!r}: AT/KAPPA 要是数字") from None
    return tuple(out)


def _plumed_setup_from_args(args, structure=None):
    """The PLUMED-native table, when any ``--plumed-*`` flag was given.

    ``structure`` is optional and only makes the generated contact CVs better: it
    puts element symbols into the comments, checks the block sizes against the
    atom count and warns when ``R_0`` reaches across half the box.
    """
    from dataclasses import replace

    from .plumedcv import PlumedBias, PlumedSetup, contact_cvs, read_file

    entries = list(getattr(args, "plumed_cv", []) or [])
    walls = list(getattr(args, "plumed_wall", []) or [])
    lines = list(getattr(args, "plumed_line", []) or [])
    contacts = list(getattr(args, "plumed_contact", []) or [])
    imported = getattr(args, "plumed_import", None)
    bias_flags = {"--plumed-pace", "--plumed-height", "--plumed-sigma",
                  "--plumed-biasfactor", "--plumed-temp", "--plumed-args",
                  "--no-plumed-print", "--plumed-print", "--plumed-free-where",
                  "--plumed-units", "--plumed-label", "--plumed-restart",
                  "--plumed-adaptive", "--plumed-sigma-min", "--plumed-sigma-max",
                  "--plumed-extra", "--plumed-import", "--plumed-contact"}
    given = set(getattr(args, "_given", set()) or set())
    if not (entries or walls or lines or contacts or imported
            or (bias_flags & given)):
        return None

    base = PlumedSetup(units="nm")
    if imported:
        try:
            base, notes = read_file(str(imported))
        except OSError as error:
            raise SystemExit(f"--plumed-import {imported}: {error}") from None
        for note in notes:
            print(f"note: --plumed-import {imported}: {note}", file=sys.stderr)

    units = (base.units if imported and "--plumed-units" not in given
             else str(getattr(args, "plumed_units", "nm") or "nm"))
    cvs = list(base.cvs) + list(_plumed_cv_args(entries))
    for entry in contacts:
        rows, contact_notes = contact_cvs(_contact_spec(entry), structure=structure,
                                          units=units)
        cvs.extend(rows)
        for note in contact_notes:
            print(f"note: --plumed-contact: {note}", file=sys.stderr)

    bias = base.bias
    changes: dict = {}
    if getattr(args, "plumed_pace", None) is not None:
        changes["pace"] = int(args.plumed_pace)
    if getattr(args, "plumed_height", None) is not None:
        changes["height"] = float(args.plumed_height)
    if getattr(args, "plumed_sigma", None) is not None:
        changes["sigma"] = float(args.plumed_sigma)
    if getattr(args, "plumed_biasfactor", None) is not None:
        changes["biasfactor"] = float(args.plumed_biasfactor)
    if getattr(args, "plumed_temp", None) is not None:
        changes["temperature"] = float(args.plumed_temp)
    if getattr(args, "plumed_label", None):
        changes["label"] = str(args.plumed_label)
    if getattr(args, "plumed_restart", False):
        changes["restart"] = True
    if getattr(args, "plumed_adaptive", None):
        changes["adaptive"] = str(args.plumed_adaptive)
    if getattr(args, "plumed_sigma_min", None) is not None:
        changes["sigma_min"] = str(args.plumed_sigma_min)
    if getattr(args, "plumed_sigma_max", None) is not None:
        changes["sigma_max"] = str(args.plumed_sigma_max)
    extra = list(base.bias.extra) + [str(item) for item in
                                     (getattr(args, "plumed_extra", []) or [])]
    if extra:
        changes["extra"] = tuple(extra)
    if getattr(args, "plumed_args", None):
        changes["args"] = tuple(part.strip()
                                 for part in str(args.plumed_args).replace(",", " ").split()
                                 if part.strip())
    if not getattr(args, "plumed_print", True):
        changes["print_colvar"] = False
    elif imported and "--plumed-print" in given:
        changes["print_colvar"] = True
    if changes:
        bias = replace(bias, **changes)

    where = str(getattr(args, "plumed_free_where", "before"))
    if imported and not lines and "--plumed-free-where" not in given:
        where = base.free_where
    return PlumedSetup(cvs=tuple(cvs),
                       walls=tuple(base.walls) + _plumed_wall_args(walls),
                       bias=bias,
                       free_lines=tuple(base.free_lines)
                       + tuple(str(line) for line in lines),
                       free_where=where, units=units)


#: what ``--plumed-contact`` accepts; the left side is the spelling on the command
#: line, the right side the field of :class:`cp2kbuild.plumedcv.ContactSpec`
_CONTACT_FIELDS = {
    "blocks": "blocks",
    "center": "center_block", "center_block": "center_block",
    "center_atoms": "center_offsets", "center_offsets": "center_offsets",
    "acceptor": "acceptor_block", "acceptor_block": "acceptor_block",
    "acceptor_atoms": "acceptor_offsets", "acceptor_offsets": "acceptor_offsets",
    "exclude": "exclude_same_molecule", "same_molecule": "exclude_same_molecule",
    "r0": "r0", "nn": "nn", "mm": "mm",
    "prefix": "prefix", "start": "start",
    "combine": "combine_label", "combine_label": "combine_label",
    "nopbc": "nopbc",
}

#: fields of ContactSpec that are numbers / switches
_CONTACT_INTS = ("center_block", "nn", "mm", "start")
_CONTACT_BOOLS = ("exclude_same_molecule", "nopbc")


def _contact_spec(entry) -> "object":
    """``--plumed-contact 'blocks=8x20;center=1;...'`` -> ContactSpec."""
    from .plumedcv import ContactSpec

    values: dict = {}
    for chunk in str(entry).split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, sign, value = chunk.partition("=")
        if not sign:
            raise SystemExit(f"--plumed-contact {entry!r}: 每一项要写成 key=value，"
                             "各项之间用 ; 分隔（分块里的逗号保留，"
                             "例如 blocks=19x1,8x20）")
        name = _CONTACT_FIELDS.get(key.strip().lower().replace("-", "_"))
        if name is None:
            raise SystemExit(f"--plumed-contact {entry!r}: 不认识的项 {key!r}"
                             "（可用：" + "、".join(sorted(_CONTACT_FIELDS)) + "）")
        value = value.strip()
        if name in _CONTACT_INTS:
            if not value.isdigit():
                raise SystemExit(f"--plumed-contact {entry!r}: {key} 要是整数")
            values[name] = int(value)
        elif name == "r0":
            try:
                values[name] = float(value)
            except ValueError:
                raise SystemExit(f"--plumed-contact {entry!r}: r0 要是数字") from None
        elif name in _CONTACT_BOOLS:
            values[name] = value.lower() in ("1", "y", "yes", "true", "t", "on")
        else:
            values[name] = value
    try:
        return ContactSpec(**values)
    except TypeError as error:
        raise SystemExit(f"--plumed-contact {entry!r}: {error}") from None


def _metadyn_from_args(args, structure=None):
    """``--metadyn``/``--meta-*``/``--cv``/``--metavar`` -> Metadyn (or None)."""
    from .metadyn import Metadyn

    entries = list(getattr(args, "cv", []) or [])
    plumed = getattr(args, "meta_plumed", None)
    # any of these flags (even at its default value) means "metadynamics please":
    # `--meta-nt-hills 50` on its own must not be silently dropped
    given = set(getattr(args, "_given", set()) or set())
    flags = {"--metadyn", "--meta-plumed", "--meta-hill-height", "--meta-nt-hills",
             "--meta-wt", "--meta-delta-t", "--meta-min-disp", "--meta-tail-cutoff",
             "--meta-lagrange", "--meta-langevin", "--meta-print-every",
             "--no-meta-print", "--cv", "--metavar", "--plumed-cv",
             "--plumed-wall", "--plumed-line", "--plumed-pace", "--plumed-height",
             "--plumed-sigma", "--plumed-biasfactor", "--plumed-temp",
             "--plumed-args", "--plumed-free-where", "--no-plumed-print",
             "--plumed-units", "--plumed-label", "--plumed-restart",
             "--plumed-adaptive", "--plumed-sigma-min", "--plumed-sigma-max",
             "--plumed-extra", "--plumed-contact", "--plumed-import"}
    wanted = bool(flags & given) or bool(entries) \
        or bool(getattr(args, "metavar", []) or []) \
        or bool(getattr(args, "plumed_cv", []) or [])
    if not wanted:
        return None
    cvs = _cv_args(entries)
    metavars = _metavar_args(getattr(args, "metavar", []) or [], len(cvs))
    if not metavars and cvs:
        # the ordinary case: every CV gets its own bias term, unscaled
        from .metadyn import Metavar

        metavars = tuple(Metavar(colvar=index)
                         for index in range(1, len(cvs) + 1))
    if plumed and cvs and not getattr(args, "meta_plumed_draft", None):
        # the CVs are not written into the CP2K input on this route - they are the
        # input of the plumed.dat draft, so say where they went
        print("note: --cv/--metavar 在 PLUMED 路线下不会写进 CP2K 输入（那边只有"
              "USE_PLUMED + 文件名），它们会变成 plumed.dat 的内容；"
              "加 --meta-plumed-draft FILE（或 '-'）把草稿写出来", file=sys.stderr)
    if getattr(args, "meta_wt", False) and getattr(args, "meta_delta_t", None) is None:
        raise SystemExit("error: --meta-wt（井温元动力学）要同时给 --meta-delta-t")
    if (getattr(args, "plumed_cv", None) or getattr(args, "plumed_wall", None)
            or getattr(args, "plumed_line", None)
            or getattr(args, "plumed_contact", None)
            or getattr(args, "plumed_import", None)) and not plumed:
        print("note: --plumed-cv/--plumed-wall/--plumed-line/--plumed-contact/"
              "--plumed-import 是 PLUMED 专用表的内容，"
              "只有在 PLUMED 路线（--meta-plumed FILE）下才会写进那个文件",
              file=sys.stderr)
    if getattr(args, "meta_langevin", False) and not getattr(args, "meta_lagrange",
                                                             False):
        raise SystemExit("error: --meta-langevin 要配 --meta-lagrange："
                         "Langevin 积分的是扩展拉格朗日的那些 CV")
    printing = bool(getattr(args, "meta_print", True))
    return Metadyn(
        enabled=True,
        use_plumed=bool(plumed),
        plumed_file=str(plumed) if plumed else "plumed.dat",
        do_hills=True,
        nt_hills=int(getattr(args, "meta_nt_hills", 50)),
        hill_height=float(getattr(args, "meta_hill_height", 1.0e-3)),
        well_tempered=bool(getattr(args, "meta_wt", False)),
        # `&METADYN TEMPERATURE` is the CV thermostat of the extended Lagrangian
        # scheme (md_run.F:400 uses the *MD* temperature for well-tempered), so it
        # is only written when LAGRANGE is on - otherwise it would be an inert
        # keyword that only invites the misreading that it is the WT reference
        temperature=(float(getattr(args, "temperature", 300.0))
                     if getattr(args, "meta_lagrange", False) else None),
        delta_t=getattr(args, "meta_delta_t", None),
        min_disp=getattr(args, "meta_min_disp", None),
        hill_tail_cutoff=getattr(args, "meta_tail_cutoff", None),
        lagrange=bool(getattr(args, "meta_lagrange", False)),
        lagrange_langevin=bool(getattr(args, "meta_langevin", False)),
        sigma=getattr(args, "meta_sigma", None),
        metavars=metavars,
        cvs=cvs,
        print_colvar=printing, print_hills=printing,
        print_every=int(getattr(args, "meta_print_every", 1)),
        plumed_setup=_plumed_setup_from_args(args, structure),
    )


def spec_from_args(args) -> Spec:
    structure = prepare_structure(args)
    grid = None
    if getattr(args, "kpoint_grid", None):
        values = [int(v) for v in parse_floats(args.kpoint_grid)]
        if len(values) != 3:
            raise SystemExit("--kpoint-grid expects three integers")
        grid = tuple(values)
    method = "FIST" if str(args.method).upper() in ("FIST", "CLASSICAL") else "QUICKSTEP"
    mm_lj, mm_element_lj = _mm_lj_args(getattr(args, "lj", []),
                                       getattr(args, "lj_pair", []),
                                       getattr(args, "lj_rcut", None))
    timestep = args.timestep
    if timestep is None:
        timestep = 1.0 if method == "FIST" else 0.5
    spec = Spec(
        task=args.task,
        method=method,
        mm_route=args.mm_route,
        mm_path_style=args.mm_path_style,
        mm_parm_file=args.mm_parm_file,
        mm_conn_file=args.mm_conn_file,
        mm_coord_file=args.mm_coord_file,
        mm_coord_format=args.mm_coord_format,
        mm_molecules=_mm_molecule_args(getattr(args, "mm_molecule", [])),
        mm_ff_file=args.mm_ff_file,
        mm_charges=_mm_charge_args(getattr(args, "mm_charge", [])),
        mm_lj=mm_lj,
        mm_element_lj=mm_element_lj,
        mm_vdw_scale14=args.mm_vdw_scale14,
        mm_ei_scale14=args.mm_ei_scale14,
        mm_shift_cutoff=args.mm_shift_cutoff,
        mm_multiple_potential=args.mm_multiple_potential,
        mm_ignore_missing_params=args.mm_ignore_missing_params,
        mm_ewald=args.mm_ewald,
        mm_ewald_alpha=args.mm_ewald_alpha,
        mm_ewald_gmax=args.mm_ewald_gmax,
        mm_o_spline=args.mm_o_spline,
        mm_rcut=args.mm_rcut,
        mm_spline_emax=args.mm_spline_emax,
        mm_verlet_skin=args.mm_verlet_skin,
        mm_stress_tensor=args.mm_stress_tensor,
        project=args.project,
        structure=structure,
        data_dir=args.data_dir,
        charge=args.charge,
        multiplicity=args.multiplicity,
        uks=args.uks,
        periodic=args.periodic,
        vacuum=args.vacuum,
        cell=parse_cell(args.cell),
        functional=args.functional,
        dispersion=args.dispersion,
        cutoff=args.cutoff,
        rel_cutoff=args.rel_cutoff,
        ngrids=args.ngrids,
        qs_method=args.qs_method,
        basis=args.basis,
        potential=args.potential,
        basis_file=args.basis_file,
        potential_file=args.potential_file,
        basis_target=args.basis_target,
        data_path_style=args.data_path_style,
        scf=args.scf,
        eps_scf=args.eps_scf,
        max_scf=args.max_scf,
        outer_max_scf=args.outer_max_scf,
        outer_eps_scf=args.outer_eps_scf,
        added_mos=args.added_mos,
        smearing=args.smearing,
        electronic_temperature=args.electronic_temperature,
        smear_method=args.smear_method,
        ot_minimizer=args.ot_minimizer,
        ot_preconditioner=args.ot_preconditioner,
        ot_algorithm=args.ot_algorithm,
        ot_linesearch=args.ot_linesearch,
        ot_stepsize=args.ot_stepsize,
        ot_energy_gap=args.ot_energy_gap,
        ot_rotation=args.ot_rotation,
        ot_safe_diis=args.ot_safe_diis,
        outer_optimizer=args.outer_optimizer,
        diag_algorithm=args.diag_algorithm,
        level_shift=args.level_shift,
        mixing_method=args.mixing_method,
        poisson_solver=args.poisson_solver,
        ignore_convergence_failure=args.ignore_convergence_failure,
        scf_guess=args.scf_guess,
        wfn_restart_file=args.wfn_restart_file,
        wfn_restart_print=args.wfn_restart_print,
        wfn_restart_every=args.wfn_restart_every,
        wfn_restart_level=args.wfn_restart_level,
        wfn_restart_add_last=args.wfn_restart_add_last,
        wfn_backup_copies=args.wfn_backup_copies,
        ext_restart_file=args.ext_restart,
        ext_restart_take=_restart_take(args.restart_take),
        ext_restart_default=args.restart_default,
        motion_restart_print=args.motion_restart_print,
        restart_every=args.restart_every,
        restart_add_last=args.restart_add_last,
        restart_backups=args.restart_backups,
        mixing_alpha=args.mixing_alpha,
        mixing_nbuffer=args.mixing_nbuffer,
        mixing_beta=args.mixing_beta,
        scaled_coords=args.scaled_coords,
        thermostat_timecon=args.thermostat_timecon,
        barostat_timecon=args.barostat_timecon,
        ensemble=args.ensemble,
        temperature=args.temperature,
        timestep=timestep,
        steps=args.steps,
        thermostat=args.thermostat,
        pressure=args.pressure,
        seed=args.seed,
        optimizer=args.optimizer,
        max_iter=args.max_iter,
        keep_angles=args.keep_angles,
        external_pressure=args.external_pressure,
        intensities=args.intensities,
        thermochemistry=args.thermochemistry,
        kpath=args.kpath,
        lattice=args.lattice,
        npoints=args.npoints,
        kpoint_grid=grid,
        kpoint_scheme=args.kpoint_scheme,
        kpoint_spacing=args.kpoint_spacing,
        kpoint_points=_kpoint_points_args(getattr(args, "kpoint_point", [])),
        kpoint_units=args.kpoint_units,
        kpoint_gamma_centered=args.kpoint_gamma_centered,
        kpoint_full_grid=args.kpoint_full_grid,
        kpoint_symmetry=args.kpoint_symmetry,
        kpoint_wavefunctions=args.kpoint_wavefunctions,
        kpoint_verbose=args.kpoint_verbose,
        print_mo_cubes=args.mo_cubes,
        mo_cubes_nhomo=args.mo_cubes_nhomo,
        mo_cubes_nlumo=args.mo_cubes_nlumo,
        mo_cubes_stride=args.mo_cubes_stride,
        print_density_cube=args.density_cube,
        density_cube_stride=args.density_cube_stride,
        fixed_atoms=args.fixed_atoms,
        fixed_atoms_components=args.fixed_atoms_components,
        extrapolation=args.extrapolation,
        pdos_nlumo=args.pdos_nlumo,
        pdos_broaden=args.pdos_broaden,
        pdos_broaden_type=args.pdos_broaden_type,
        band_type=args.band_type,
        replicas=args.replicas,
        replica_files=list(args.replica),
        k_spring=args.k_spring,
        print_level=args.print_level,
        walltime=args.walltime,
        print_molden=args.molden,
        molden_file=args.molden_file,
        molden_nlumo=args.molden_nlumo,
        molden_unit=args.molden_unit,
        molden_gto_kind=args.molden_gto_kind,
        molden_write_pseudo=args.molden_write_pseudo,
        molden_write_cell=args.molden_write_cell,
        molden_vib=args.molden_vib,
        print_forces=not args.no_forces,
        print_mulliken=args.mulliken,
        print_dipole=args.print_dipole,
        trajectory_stride=args.trajectory_stride,
        annealing=args.annealing,
        annealing_cell=args.annealing_cell,
        temperature_annealing=args.temperature_annealing,
        langevin_gamma=args.langevin_gamma,
        restart_temperature_annealing=args.restart_temperature_annealing,
    )
    if getattr(args, "temperature_ramp", None):
        _apply_temperature_ramp(spec, args.temperature_ramp)
    spec.metadyn = _metadyn_from_args(args, structure)
    if getattr(args, "eps_default", None) is not None:
        spec.eps_default = float(args.eps_default)
    for entry in getattr(args, "pdos_ldos", []) or []:
        spec.pdos_ldos.append([int(v) for v in parse_floats(entry)])
    _warn_unused_mixing(spec, args)
    _warn_restart(spec)
    if not getattr(args, "_skip_coupling_warnings", False):
        # `scan` builds the template itself and forces RUN_TYPE ENERGY, so the
        # "this option cannot take effect" warnings would only repeat what its
        # own note says
        _warn_ignored_settings(spec, args)
    return spec


def _apply_temperature_ramp(spec: Spec, text: str) -> None:
    """``--temperature-ramp 300:600``: work out and store CP2K's factor.

    Which keyword gets it depends on the ensemble (CP2K has no ensemble-agnostic
    ramp): LANGEVIN moves the thermostat's setpoint, everything without a particle
    thermostat rescales velocities, and NVT/NPT can do neither - that is a hard
    error here rather than a run that quietly does not heat up.
    """
    from .recipes import plan_ramp

    parts = [piece for piece in re.split(r"[:\-–>]+", str(text)) if piece.strip()]
    if len(parts) != 2:
        raise SystemExit(f"error: --temperature-ramp 要写成 FROM:TO（比如 300:600），"
                         f"收到的是 {text!r}")
    try:
        start, end = (float(part) for part in parts)
    except ValueError:
        raise SystemExit(f"error: --temperature-ramp 的两个温度都要是数字：{text!r}")
    try:
        ramp = plan_ramp(start, end, int(spec.steps), float(spec.timestep),
                         spec.ensemble)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}")
    # the input's TEMPERATURE is the starting point either way (CP2K scales it
    # once per step; for a geometric ramp the starting point *is* the first value)
    spec.temperature = start
    if ramp.mechanism == "temperature":
        spec.temperature_annealing = ramp.factor
    else:
        spec.annealing = ramp.factor
    direction = "升温" if end > start else "降温"
    print(f"note: {direction} {start:g} K → {end:g} K，{ramp.steps} 步 × "
          f"{ramp.timestep:g} fs = {ramp.duration:.2f} ps，约 {abs(ramp.rate):.1f} K/ps；"
          f"机制：ENSEMBLE {spec.ensemble} 用 "
          + ("`&MD TEMPERATURE_ANNEALING`（移动 Langevin 的目标温度）"
             if ramp.mechanism == "temperature" else
             "`&MD ANNEALING`（缩放速度，温度大约按它的平方走）")
          + f"；因子 {ramp.factor:.10f}", file=sys.stderr)
    schedule = "，".join(f"{moment:.2f} ps→{temperature:.0f} K"
                         for moment, temperature in ramp.table(5)[1:])
    print(f"note: 温度大致按几何（指数）斜率走：{schedule}"
          + ("（不是严格线性；要严格线性就分段接力）"
             if abs(ramp.factor - 1.0) > 1e-6 else ""), file=sys.stderr)
    if ramp.mechanism == "temperature" and spec.langevin_gamma <= 0.0:
        print("warning: LANGEVIN 的 GAMMA 是 0：没有摩擦也没有噪声，温度不受控，"
              "斜坡也不会生效——给个 --langevin-gamma（比如 0.01）", file=sys.stderr)


def _touched_fields(spec: Spec) -> set[str]:
    """Spec fields whose value differs from the dataclass default.

    That is the closest thing to "the user asked for this": the command line
    writes every option into the Spec, so a value that moved must have come from
    a flag (or from a flag's own default, which is harmless).
    """
    import dataclasses

    touched = set()
    for name, field in Spec.__dataclass_fields__.items():
        default = field.default
        if default is dataclasses.MISSING:
            continue
        if getattr(spec, name, None) != default:
            touched.add(name)
    return touched


def _asked_for(rule, spec: Spec, args) -> bool:
    """Kept for callers that only have one rule; the table does the real work."""
    from .couplings import asked_but_ignored

    return rule in asked_but_ignored(spec, spec.structure,
                                     getattr(args, "_given", set()))


def _warn_ignored_settings(spec: Spec, args) -> None:
    """Say so when a parameter the user set cannot have any effect.

    The conditions live in ``cp2kbuild.couplings`` (one table for the CLI, the
    GUI and the self-test), each with the manual page or source file behind it.
    """
    from .couplings import asked_but_ignored, poisson_solver_warning

    given = getattr(args, "_given", set())
    # the *effective* structure: --vacuum/--cell/--periodic applied, exactly what
    # ``build`` will see.  Without this, "--cell 30,30,30 --mm-ewald SPME" would
    # be reported as having no periodic cell to work with.
    structure = (apply_structure_options(spec.structure, spec)
                 if spec.structure is not None else None)
    for rule in asked_but_ignored(spec, structure, given):
        value = getattr(spec, rule.field, None)
        print(f"WARN  {rule.flag()} {value!r} 不会生效："
              f"{rule.reason(spec, structure)}。（依据：{rule.evidence}）",
              file=sys.stderr)
    poisson = poisson_solver_warning(spec, structure)
    if poisson:
        print(f"WARN  {poisson}。", file=sys.stderr)


def open_index(data_dir: str | None) -> tuple[DataIndex, Path | None]:
    resolved = discover_data_dir(data_dir)
    index = DataIndex(resolved) if resolved else DataIndex(None)
    if resolved is None:
        print("warning: no CP2K data directory found; basis sets and potentials "
              "cannot be verified.\n         pass --data-dir <cp2k>/data or set "
              "CP2K_DATA_DIR.", file=sys.stderr)
    return index, resolved


def validate_sections(sections, index: DataIndex, use_schema: bool = True):
    db = load_db() if use_schema else None
    return Validator(db, index).run(sections)


def report(diagnostics, quiet: bool = False, as_json: bool = False,
           stream=None, extra_errors: int = 0,
           extra_warnings: int = 0) -> tuple[int, int]:
    """Print diagnostics and return ``(errors, warnings)``.

    ``stream`` matters for ``--stdout``: the report must not land in the input
    file that is being piped out, so it goes to stderr instead.

    ``extra_errors``/``extra_warnings`` count the findings the caller printed
    itself — ``new`` writes the generator's own ``warning:``/``error:`` lines before
    calling this — so the tally line cannot say "0 个错误" directly under an
    ``error:`` line.
    """
    stream = stream or sys.stdout
    if as_json:
        print(json.dumps([d.__dict__ for d in diagnostics], indent=2), file=stream)
    elif not quiet:
        for item in diagnostics:
            print(item.render(), file=stream)
    errors = sum(1 for d in diagnostics if d.level == "error")
    warnings = sum(1 for d in diagnostics if d.level == "warning")
    if not quiet:
        print(f"-> {summarise(diagnostics, extra_errors, extra_warnings)}",
              file=stream)
    return errors + extra_errors, warnings + extra_warnings


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_new(args) -> int:
    index, data_dir = open_index(args.data_dir)
    spec = spec_from_args(args)
    # "auto" path style needs to know where the input lands: a force-field file
    # that sits in that directory is written as a bare name, so the input can be
    # copied to a cluster together with its topology/coordinates
    spec.output_dir = str(Path(args.out).parent) if args.out else str(Path.cwd())

    if spec.task == "neb":
        if not spec.replica_files and spec.replicas < 3:
            print("error: the NEB task needs --replica files or --replicas >= 3",
                  file=sys.stderr)
            return 2
        if not spec.replica_files and spec.structure is None:
            print("error: the NEB task needs at least a cell/kind definition; "
                  "pass --structure", file=sys.stderr)
            return 2
    elif spec.structure is None and not (
            is_classical(spec) and mm_route(spec).conn_file and spec.cell is not None):
        # a file-based force field needs no structure file: the atoms come from
        # COORD_FILE_NAME - but the box still has to come from --cell, because
        # CP2K ignores the one inside the coordinate file
        print("error: --structure is required (xyz, pdb, poscar, cif, .inp or a "
              "-1.restart); "
              "for a classical file-based force field you may instead pass "
              "--method fist --mm-coord-file <file> --cell a,b,c", file=sys.stderr)
        return 2

    result = build(spec, index)
    header = [f"CP2K input generated by cp2kbuild {__version__}"]
    text = render_document(result.sections, title=header[0],
                           notes=result.notes)

    if args.stdout:
        print(text, end="")
    else:
        # the *file* name follows the structure (so two systems in one folder do
        # not overwrite each other's input), the PROJECT_NAME defaults to cp2k
        out = Path(args.out) if args.out else Path(
            f"{structure_stem(spec) or _project_of(result, spec)}.inp")
        write_text_lf(out, text)
        if not args.quiet:
            print(f"wrote {out}")
            _restart_note(out, _project_of(result, spec), spec)

    if result.warnings and not args.quiet:
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    # the generator's own *errors* (no coordinates at all, ...): CP2K aborts on
    # those inputs, so they are printed even under --quiet and set the exit code
    for error in getattr(result, "errors", []):
        print(f"error: {error}", file=sys.stderr)

    # the PLUMED route needs a plumed.dat as well; generating a draft of it from
    # the same CV/hill settings is the whole point of --meta-plumed-draft
    draft = getattr(args, "meta_plumed_draft", None)
    if draft:
        from .plumedcv import draft as plumed_draft

        if spec.metadyn is None or not spec.metadyn.use_plumed:
            print("warning: --meta-plumed-draft 需要 --meta-plumed（PLUMED 路线）",
                  file=sys.stderr)
        else:
            structure = apply_structure_options(spec.structure, spec)
            text_plumed = plumed_draft(spec.metadyn, structure,
                                       temperature=spec.temperature,
                                       version=__version__)
            if draft == "-":
                print(text_plumed, end="")
            else:
                write_text_lf(Path(draft), text_plumed)
                if not args.quiet:
                    print(f"wrote {draft}")

    exit_code = 1 if getattr(result, "errors", []) else 0
    if not args.no_validate:
        diagnostics = validate_sections(result.sections, index)
        # one problem, one line: a finding the generator already reported itself
        # (an AMBER route without PARM_FILE_NAME, say) must not come back a second
        # time from the validator - it used to print as `warning:` *and* `error:`.
        covered = getattr(result, "covered_codes", None) or set()
        if covered:
            diagnostics = [item for item in diagnostics
                           if item.code not in covered]
        # with --stdout the input file is the program's output, so the report
        # has to go to stderr to keep `cp2kbuild new --stdout > job.inp` clean
        stream = sys.stderr if args.stdout else sys.stdout
        errors, _ = report(diagnostics, quiet=args.quiet, stream=stream,
                           extra_errors=len(getattr(result, "errors", [])),
                           extra_warnings=len(getattr(result, "warnings", [])))
        exit_code = 1 if errors else exit_code
    return exit_code


def _project_of(result: BuildResult, spec: Spec) -> str:
    for section in result.sections:
        if section.name.upper() == "GLOBAL":
            value = section.get("PROJECT_NAME")
            if value:
                return str(value)
    return "cp2k"


def restart_files_in(directory: Path, project: str) -> dict[str, Path]:
    """Existing restart files that this input would pick up.

    ``SCF_GUESS RESTART`` (the default) reads ``<project>-RESTART.wfn`` (``.kp``
    for k-point runs) and ``&EXT_RESTART`` reads ``<project>-1.restart``; both
    live next to the input file, so a *stable* project name is what makes a
    second run continue the first one.
    """
    found: dict[str, Path] = {}
    for suffix, kind in ((".wfn", "wfn"), (".kp", "wfn_kp"), (".restart", "run")):
        candidate = directory / f"{project}-RESTART{suffix}"
        if candidate.is_file():
            found[kind] = candidate
    run = directory / f"{project}-1.restart"
    if run.is_file():
        found["run"] = run
    return found


def _restart_note(out: Path, project: str, spec: Spec) -> None:
    """Say which restart files this run will read (they are inherited)."""
    found = restart_files_in(out.parent, project)
    if not found:
        return
    if "wfn" in found or "wfn_kp" in found:
        source = found.get("wfn") or found["wfn_kp"]
        print(f"note: {source.name} exists -> SCF_GUESS {spec.scf_guess} reads it "
              "(inherits the previous wave function)", file=sys.stderr)
    if "run" in found and not spec.ext_restart_file:
        print(f"note: {found['run'].name} exists -> add --restart "
              f"{found['run'].name} to continue that run from it", file=sys.stderr)


def cmd_ramp(args) -> int:
    """Write a staged temperature program."""
    from .tempprogram import build_from_text

    if bool(args.program) == bool(args.segments):
        raise SystemExit("error: give exactly one of --program FILE (a schedule "
                         "file) or --segments '300:5,300-500:10,500:5'")
    if args.program:
        try:
            text = Path(args.program).read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"error: 读不了 {args.program}：{exc}") from exc
    else:
        text = args.segments
    if not getattr(args, "structure", None):
        raise SystemExit("error: --structure is required (the system to run the "
                         "program on)")

    index, _ = open_index(args.data_dir)
    # a program is MD by definition, and the temperatures and stage lengths come
    # from the schedule - so say so instead of letting --steps/--temperature look
    # as if they did something
    args.task = "md"
    spec = spec_from_args(args)
    from .tempprogram import RunOptions

    run = RunOptions(mpirun_bin=args.mpirun_bin, cp2k_bin=args.cp2k_bin,
                     mpi_procs=args.mpi_procs, omp_threads=args.omp_threads,
                     mpirun_extra=args.mpirun_extra, resume=args.resume)
    try:
        plan = build_from_text(spec, index, text, args.outdir,
                               compact=bool(args.segments),
                               seed_restart=args.ext_restart, run=run)
    except ValueError as exc:
        raise SystemExit(f"error: {exc}") from exc
    for option, value, default in (("--steps", args.steps, 5000),
                                   ("--temperature", args.temperature, 300.0),
                                   ("--temperature-ramp", args.temperature_ramp, None),
                                   ("--temperature-annealing",
                                    args.temperature_annealing, None),
                                   ("--annealing", args.annealing, None),
                                   ("--restart-temperature-annealing",
                                    args.restart_temperature_annealing, False)):
        if value != default:
            print(f"note: {option} 在这里不起作用：每一段的温度和步数（以及因子）"
                  "都由温度程序自己决定；单段升温请用 `new`", file=sys.stderr)
    print(plan.text(), end="")
    return 0


def cmd_scan(args) -> int:
    """Write the official CUTOFF/REL_CUTOFF script set."""
    from .cutoffkit import source_from_input, source_from_spec

    if args.input:
        try:
            source = source_from_input(args.input)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        if not getattr(args, "structure", None):
            raise SystemExit(
                "error: give --input FILE (an input you already have) or "
                "--structure FILE plus the options you want, like `new`")
        index, _ = open_index(args.data_dir)
        # a grid study is an energy run (that is what CUTOFF/REL_CUTOFF are
        # converged against), so `scan` has no --task: it is ENERGY, always
        args.task = "energy"
        args._skip_coupling_warnings = True
        spec = spec_from_args(args)
        try:
            source = source_from_spec(spec, index)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    try:
        plan = build_cutoff_study(
            source, args.outdir, cutoffs=args.cutoffs,
            rel_cutoffs=args.rel_cutoffs, fixed_rel_cutoff=args.rel_cutoff,
            fixed_cutoff=args.fixed_cutoff)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.cutoff is not None:
        # `new`'s --cutoff writes &MGRID CUTOFF into the input, but here that
        # number becomes the LT_cutoff placeholder and every point gets its own
        # value from --cutoffs / --fixed-cutoff, so it cannot do anything
        plan.notes.append(
            f"--cutoff {args.cutoff:g} 在这里不起作用：模板里的 CUTOFF 会被换成 "
            "LT_cutoff 占位符，每个参数点的值来自 --cutoffs（第 1 步）或 "
            "--fixed-cutoff（第 2 步固定值）。")
    for option, given in (("--restart", getattr(args, "ext_restart", None)),
                          ("--restart-take", getattr(args, "restart_take", None)),
                          ("--wfn-restart-file",
                           getattr(args, "wfn_restart_file", None))):
        if given:
            plan.notes.append(
                f"{option} {given} 在这里不起作用：网格扫描的每一档都是独立的单点能，"
                "工具不会把 &EXT_RESTART（或 WFN_RESTART_FILE_NAME）写进模板——"
                "否则每档都会去读上一次的 restart 文件，文件不在就在检查输入时 "
                "ABORT（common/cp_files.F:406）。接力请用 `new`。")
    print(plan.text(), end="")
    return 0


def cmd_mmcheck(args) -> int:
    """Check a classical coordinate file against its topology file(s)."""
    from .mmcheck import check_mixture, check_single

    if not args.coord:
        print("error: --coord is required (the file that goes into "
              "COORD_FILE_NAME)", file=sys.stderr)
        return 2
    molecules = _mm_molecule_args(getattr(args, "mol", []))
    if not molecules and not args.conn_file:
        print("error: give either --mol (one per &MOLECULE) or --conn-file "
              "(one topology for the whole system)", file=sys.stderr)
        return 2
    if molecules and args.conn_file:
        print("error: --mol and --conn-file are two different layouts: pick one "
              "(with --mol the input uses CONNECTIVITY MOL_SET)", file=sys.stderr)
        return 2

    if molecules:
        report = check_mixture(molecules, args.coord, args.coord_format)
    else:
        report = check_single(args.conn_file, args.coord, args.coord_format)

    if args.json:
        print(json.dumps({
            "ok": report.ok,
            "expected_atoms": report.expected_atoms,
            "coordinate": report.coordinate.__dict__ if report.coordinate else None,
            "blocks": [b.__dict__ for b in report.blocks],
            "problems": report.problems,
            "notes": report.notes,
        }, ensure_ascii=False, indent=2))
    else:
        print(report.text())
    return 0 if report.ok else 1


def cmd_validate(args) -> int:
    index, _ = open_index(args.data_dir)
    db = None if args.no_schema else load_db()
    if db is None and not args.no_schema:
        print("note: manual database not built; schema checks are skipped.\n"
              "      run `cp2kbuild db --rebuild` to enable them.", file=sys.stderr)

    total_errors = 0
    total_warnings = 0
    payload = {}
    for name in args.files:
        try:
            parsed = read_input(name)
        except OSError as exc:
            print(f"error: cannot read {name}: {exc}", file=sys.stderr)
            total_errors += 1
            continue
        for warning in parsed.warnings:
            print(f"WARN  parse [{name}]: {warning}")
        if parsed.directives:
            print(f"NOTE  parse [{name}]: {len(parsed.directives)} preprocessor "
                  f"directive(s) are not evaluated by this linter")
        diagnostics = Validator(db, index).run(parsed.sections, parsed.directives)
        if args.json:
            payload[name] = [d.__dict__ for d in diagnostics]
        else:
            print(f"\n=== {name} ===")
            for item in diagnostics:
                print(item.render())
            print(f"-> {summarise(diagnostics)}")
        total_errors += sum(1 for d in diagnostics if d.level == "error")
        total_warnings += sum(1 for d in diagnostics if d.level == "warning")

    if args.json:
        print(json.dumps(payload, indent=2))
    if total_errors:
        return 1
    if args.strict and total_warnings:
        return 1
    return 0


def cmd_info(args) -> int:
    index, resolved = open_index(args.data_dir)
    kind = getattr(args, "kind", "all")
    summary = index.summary()
    if kind != "all":
        # the inventory has one set of keys per kind (basis_* / potential_*);
        # "directory" has neither and is always kept
        summary = {key: value for key, value in summary.items()
                   if kind in key or not ("basis" in key or "potential" in key)}
    print("CP2K data directory")
    for key, value in summary.items():
        if isinstance(value, list):
            print(f"  {key}: {', '.join(value) if value else '(none)'}")
        else:
            print(f"  {key}: {value}")

    if args.elements:
        from .recommend import choose_assignments

        elements = [e.strip() for e in args.elements.split(",") if e.strip()]
        print(f"\nrecommendation for {', '.join(elements)} "
              f"({args.target}, {args.functional}):")
        setup = choose_assignments(elements, index, functional=args.functional,
                                   target=args.target)
        for element, assignment in setup.assignments.items():
            valence = f"q{assignment.basis_valence}" if assignment.basis_valence else ""
            print(f"  {element:>3s}  {assignment.basis} {valence}  "
                  f"({assignment.basis_file})  +  {assignment.potential}  "
                  f"({assignment.potential_file})")
        for problem in setup.problems:
            print(f"  problem: {problem}")
        for note in setup.notes:
            print(f"  note: {note}")
        print("\nlines that would be written into the input:")
        for name in setup.basis_files:
            print(f"  BASIS_SET_FILE_NAME  {name}")
        for name in setup.potential_files:
            print(f"  POTENTIAL_FILE_NAME  {name}")
        if kind != "all":
            print(f"  note: --kind {kind} 只管上面的目录清单；一次计算两种文件都要，"
                  "所以这里不按它过滤。")
        return 0

    if args.target != "solid" or args.functional != "PBE":
        print("\nnote: --target/--functional 只在 --elements 时才起作用"
              "（比如 info --elements Fe,O --target accurate）。")
    print("\nfiles present in the data directory:")
    if kind in ("all", "basis"):
        print(f"  basis    ({len(index.basis_files)}): "
              f"{', '.join(index.basis_files[:8])}"
              + (" ..." if len(index.basis_files) > 8 else ""))
    if kind in ("all", "potential"):
        print(f"  potential({len(index.potential_files)}): "
              f"{', '.join(index.potential_files[:8])}"
              + (" ..." if len(index.potential_files) > 8 else ""))
    return 0


def cmd_keyword(args) -> int:
    db = load_db()
    if db is None:
        print("the manual database is missing; build it with "
              "`cp2kbuild db --rebuild`", file=sys.stderr)
        return 2

    if args.glossary:
        print("CP2K 术语对照（英文 → 中文）")
        print("-" * 44)
        for term, cn in sorted(TERMS.items()):
            print(f"  {term:<24s} {cn}")
        print(f"\n共 {len(TERMS)} 条。手册正文是英文的，这些术语会在 GUI 里"
              f"自动标注；{len(KEYWORDS)} 个高频关键字另有中文说明"
              f"（`cp2kbuild keyword --cn <词>`）。")
        return 0

    if args.section and args.all:
        info = db.section(args.section)
        if info is None:
            matches = db.sections_matching(args.section)
            print("section not found. did you mean:")
            for match in matches[:20]:
                print(f"  {match}")
            return 2
        print(f"&{info.name}  ({info.path})")
        if info.description:
            print(f"  {info.description}")
        note = cn_lookup(info.path, None)
        if note:
            print(f"  【中文】{note.name}：{note.text.splitlines()[0]}")
        print(f"  manual: {info.url}")
        if info.subsections:
            print(f"  subsections: {', '.join(info.subsections)}")
        print("  keywords:")
        for name, keyword in sorted(db.keywords(args.section).items()):
            default = f" = {keyword.default}" if keyword.default is not None else ""
            unit = f" [{keyword.unit}]" if keyword.unit else ""
            entry = cn_lookup(args.section, name)
            mark = f"  ← {entry.name}" if entry else ""
            print(f"    {name:<32s} {keyword.type or '?':<8s}{default}{unit}{mark}")
        return 0

    if not args.term:
        print("give a search term (English or Chinese), or use "
              "--section PATH --all / --glossary", file=sys.stderr)
        return 2

    entries: list[tuple] = []
    if is_chinese(args.term):
        for key, cn_entry in cn_search(args.term, args.limit):
            section, keyword = split_key(key)
            entries.append((section, keyword, cn_entry))
    elif args.section:
        info = db.keyword(args.section, args.term)
        if info:
            entries.append((info.path, info.name, cn_lookup(info.path, info.name)))
    else:
        hits = db.search(args.term, args.limit)
        if args.cn:
            hits = [h for h in hits if cn_lookup(h.path, h.name)]
        for hit in hits:
            entries.append((hit.path, hit.name, cn_lookup(hit.path, hit.name)))

    if not entries:
        print(f"no keyword matching {args.term!r}")
        return 1

    for section, keyword, cn_entry in entries:
        info = db.keyword(section, keyword) if keyword else None
        title = f"{keyword}  ({section})" if keyword else section
        print(title)
        if cn_entry is not None:
            print("  【中文说明】" + cn_entry.name +
                  "  —— cp2kbuild 自己写的说明，不是手册译文")
            for line in cn_entry.text.splitlines():
                print(f"    {line}")
        if info is None:
            print()
            continue
        default = f" = {info.default}" if info.default is not None else ""
        unit = f" [{info.unit}]" if info.unit else ""
        print(f"  类型: {info.type or '?'}{default}{unit}")
        if info.aliases:
            print(f"  别名: {', '.join(info.aliases)}")
        if info.usage:
            print(f"  用法: {info.usage}")
        if info.valid_values:
            values = ", ".join(v["value"] for v in info.valid_values[:12])
            more = " ..." if len(info.valid_values) > 12 else ""
            print(f"  取值: {values}{more}")
        if info.description:
            text = re.sub(r"\s+", " ", info.description)
            print(f"  原文: {text[:400]}{'...' if len(text) > 400 else ''}")
            terms = cn_terms_in(info.description)
            if terms:
                print("  术语: " + "; ".join(f"{t}={c}" for t, c in terms[:8]))
        print(f"  {info.url}")
        print()
    return 0


def cmd_db(args) -> int:
    if args.rebuild:
        import subprocess

        script = Path(__file__).resolve().parent.parent / "tools" / "build_db.py"
        match = re.search(r"cp2k-(\d{4})_(\d+)-branch", args.branch)
        version = f"{match.group(1)}_{match.group(2)}" if match else "trunk"
        if not script.is_file():
            if is_frozen():
                print("打包版里没有 tools/build_db.py：爬 CP2K 手册要用源码目录。\n"
                      "请在源码目录里运行 `python tools/build_db.py "
                      f"--branch {args.branch} --out cp2kbuild/data/"
                      f"manual_db_{version}.json`。", file=sys.stderr)
            else:
                print(f"cannot find {script}", file=sys.stderr)
            return 2
        out = Path(__file__).with_name("data") / f"manual_db_{version}.json"
        command = [sys.executable, str(script), "--branch", args.branch,
                   "--out", str(out), "--workers", str(args.workers)]
        if args.src:
            command += ["--src", args.src]
        print("running:", " ".join(command))
        return subprocess.call(command)

    db = load_db()
    if db is None:
        print("no manual database found; build it with `cp2kbuild db --rebuild`")
        return 1
    for key, value in db.stats().items():
        print(f"  {key}: {value}")
    return 0


# --------------------------------------------------------------------------- #
def _configure_output() -> None:
    """Make Chinese output work in a Windows console.

    A frozen executable inherits the console code page (936 on a Chinese
    Windows), which cannot encode the Chinese notes and turns ``print`` into a
    UnicodeEncodeError.  Switching the console to UTF-8 and reconfiguring the
    streams fixes the rendering and ``errors="replace"`` guarantees that a
    failure to do so degrades to '?' instead of a traceback.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001 - no console, or no permission
            pass
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    # remember which options were on the command line: a flag whose value equals
    # the default (--thermostat NOSE, --intensities, ...) is still an ask, and
    # the coupling table needs to know it to warn about a setting with no effect
    raw = list(argv if argv is not None else sys.argv[1:])
    args._given = {token.split("=", 1)[0] for token in raw if token.startswith("--")}
    if not args.command:
        parser.print_help()
        return 0

    handlers = {
        "new": cmd_new,
        "ramp": cmd_ramp,
        "scan": cmd_scan,
        "mmcheck": cmd_mmcheck,
        "validate": cmd_validate,
        "info": cmd_info,
        "keyword": cmd_keyword,
        "db": cmd_db,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return 130
    except (ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
