"""Check every section/keyword name cp2kbuild emits against the manual database."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from cp2kbuild.manualdb import load_db  # noqa: E402

CHECKS = [
    # (section, keyword or None to check the section itself)
    ("GLOBAL", "PROJECT_NAME"), ("GLOBAL", "PROJECT"), ("GLOBAL", "RUN_TYPE"),
    ("GLOBAL", "PRINT_LEVEL"), ("GLOBAL", "WALLTIME"),
    ("FORCE_EVAL", "METHOD"),
    ("FORCE_EVAL/PRINT/FORCES", None), ("FORCE_EVAL/PRINT/STRESS_TENSOR", None),
    ("FORCE_EVAL/DFT", "BASIS_SET_FILE_NAME"), ("FORCE_EVAL/DFT", "POTENTIAL_FILE_NAME"),
    ("FORCE_EVAL/DFT", "CHARGE"), ("FORCE_EVAL/DFT", "MULTIPLICITY"),
    ("FORCE_EVAL/DFT", "UKS"), ("FORCE_EVAL/DFT", "LSD"),
    ("FORCE_EVAL/DFT/MGRID", "CUTOFF"), ("FORCE_EVAL/DFT/MGRID", "REL_CUTOFF"),
    ("FORCE_EVAL/DFT/MGRID", "NGRIDS"), ("FORCE_EVAL/DFT/MGRID", "PROGRESSION_FACTOR"),
    ("FORCE_EVAL/DFT/QS", "METHOD"), ("FORCE_EVAL/DFT/QS", "EPS_DEFAULT"),
    ("FORCE_EVAL/DFT/QS", "EXTRAPOLATION"),
    ("FORCE_EVAL/DFT/SCF", "SCF_GUESS"), ("FORCE_EVAL/DFT/SCF", "EPS_SCF"),
    ("FORCE_EVAL/DFT/SCF", "MAX_SCF"), ("FORCE_EVAL/DFT/SCF", "ADDED_MOS"),
    ("FORCE_EVAL/DFT/SCF/SMEAR", "METHOD"),
    ("FORCE_EVAL/DFT/SCF/SMEAR", "ELECTRONIC_TEMPERATURE"),
    ("FORCE_EVAL/DFT/SCF/OT", "MINIMIZER"), ("FORCE_EVAL/DFT/SCF/OT", "PRECONDITIONER"),
    ("FORCE_EVAL/DFT/SCF/OT", "ADDED_MOS"),
    ("FORCE_EVAL/DFT/SCF/OUTER_SCF", "EPS_SCF"),
    ("FORCE_EVAL/DFT/SCF/OUTER_SCF", "MAX_SCF"),
    ("FORCE_EVAL/DFT/SCF/DIAGONALIZATION", "ALGORITHM"),
    ("FORCE_EVAL/DFT/SCF/MIXING", "METHOD"), ("FORCE_EVAL/DFT/SCF/MIXING", "ALPHA"),
    ("FORCE_EVAL/DFT/SCF/MIXING", "NBROYDEN"),
    ("FORCE_EVAL/DFT/XC/XC_FUNCTIONAL", None),
    ("FORCE_EVAL/DFT/XC/VDW_POTENTIAL", "POTENTIAL_TYPE"),
    ("FORCE_EVAL/DFT/XC/VDW_POTENTIAL/PAIR_POTENTIAL", "TYPE"),
    ("FORCE_EVAL/DFT/XC/VDW_POTENTIAL/PAIR_POTENTIAL", "PARAMETER_FILE_NAME"),
    ("FORCE_EVAL/DFT/XC/VDW_POTENTIAL/PAIR_POTENTIAL", "REFERENCE_FUNCTIONAL"),
    ("FORCE_EVAL/DFT/POISSON", "PERIODIC"), ("FORCE_EVAL/DFT/POISSON", "PSOLVER"),
    ("FORCE_EVAL/DFT/KPOINTS", "SCHEME"), ("FORCE_EVAL/DFT/KPOINTS", "SYMMETRY"),
    ("FORCE_EVAL/DFT/KPOINTS", "WAVEFUNCTIONS"), ("FORCE_EVAL/DFT/KPOINTS", "UNITS"),
    ("FORCE_EVAL/DFT/KPOINTS", "KPOINT"),
    ("FORCE_EVAL/DFT/PRINT", None), ("FORCE_EVAL/DFT/PRINT/MULLIKEN", None),
    ("FORCE_EVAL/DFT/PRINT/MOMENTS", None),
    ("FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE", "FILE_NAME"),
    ("FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE", "ADDED_MOS"),
    ("FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET", "NPOINTS"),
    ("FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET", "SPECIAL_POINT"),
    ("FORCE_EVAL/DFT/PRINT/BAND_STRUCTURE/KPOINT_SET", "UNITS"),
    ("FORCE_EVAL/DFT/PRINT/PDOS", "NLUMO"), ("FORCE_EVAL/DFT/PRINT/PDOS/LDOS", "LIST"),
    ("FORCE_EVAL/SUBSYS", None),
    ("FORCE_EVAL/SUBSYS/CELL", "ABC"), ("FORCE_EVAL/SUBSYS/CELL", "A"),
    ("FORCE_EVAL/SUBSYS/CELL", "PERIODIC"), ("FORCE_EVAL/SUBSYS/CELL/CELL_REF", None),
    ("FORCE_EVAL/SUBSYS/COORD", "SCALED"), ("FORCE_EVAL/SUBSYS/KIND", "ELEMENT"),
    ("FORCE_EVAL/SUBSYS/KIND", "BASIS_SET"), ("FORCE_EVAL/SUBSYS/KIND", "POTENTIAL"),
    ("FORCE_EVAL/SUBSYS/RNG_INIT", "SEED"),
    ("FORCE_EVAL/SUBSYS/TOPOLOGY", "COORD_FILE_NAME"),
    ("FORCE_EVAL/SUBSYS/TOPOLOGY", "CONNECTIVITY"),
    ("MOTION/GEO_OPT", "OPTIMIZER"), ("MOTION/GEO_OPT", "MAX_ITER"),
    ("MOTION/GEO_OPT", "MAX_FORCE"), ("MOTION/GEO_OPT", "RMS_FORCE"),
    ("MOTION/CELL_OPT", "OPTIMIZER"), ("MOTION/CELL_OPT", "EXTERNAL_PRESSURE"),
    ("MOTION/CELL_OPT", "KEEP_ANGLES"), ("MOTION/CELL_OPT", "MAX_ITER"),
    ("MOTION/MD", "ENSEMBLE"), ("MOTION/MD", "STEPS"), ("MOTION/MD", "TIMESTEP"),
    ("MOTION/MD", "TEMPERATURE"), ("MOTION/MD", "TEMP_TOL"),
    ("MOTION/MD/THERMOSTAT", "TYPE"), ("MOTION/MD/THERMOSTAT/NOSE", "TIMECON"),
    ("MOTION/MD/THERMOSTAT/CSVR", "TIMECON"),
    ("MOTION/MD/THERMOSTAT/LANGEVIN", "GAMMA"),
    ("MOTION/MD/BAROSTAT", "PRESSURE"), ("MOTION/MD/BAROSTAT", "TIMECON"),
    ("MOTION/PRINT/TRAJECTORY", "_"), ("MOTION/PRINT/TRAJECTORY/EACH", "MD"),
    ("MOTION/PRINT/TRAJECTORY/EACH", "GEO_OPT"),
    ("MOTION/PRINT/TRAJECTORY/EACH", "CELL_OPT"),
    ("MOTION/PRINT/VELOCITIES", "_"), ("MOTION/PRINT/CELL", "_"),
    ("MOTION/BAND", "BAND_TYPE"), ("MOTION/BAND", "NUMBER_OF_REPLICA"),
    ("MOTION/BAND", "K_SPRING"), ("MOTION/BAND", "ROTATE_FRAMES"),
    ("MOTION/BAND/CI_NEB", "NSTEPS_IT"),
    ("MOTION/BAND/CONVERGENCE_CONTROL", "MAX_FORCE"),
    ("MOTION/BAND/CONVERGENCE_CONTROL", "RMS_FORCE"),
    ("MOTION/BAND/REPLICA", "COORD_FILE_NAME"), ("MOTION/BAND/STRING_METHOD", None),
    ("VIBRATIONAL_ANALYSIS", "INTENSITIES"), ("VIBRATIONAL_ANALYSIS", "THERMOCHEMISTRY"),
    ("VIBRATIONAL_ANALYSIS", "FULLY_PERIODIC"),
    ("VIBRATIONAL_ANALYSIS/PRINT/MOLDEN_VIB", None),
    ("VIBRATIONAL_ANALYSIS/MODE_SELECTIVE", None),
]

db = load_db()
if db is None:
    raise SystemExit("no db")
missing_sections = []
missing_keywords = []
for section, keyword in CHECKS:
    if keyword is None:
        if not db.has_section(section):
            missing_sections.append(section)
        continue
    if keyword == "_":     # section existence only
        if not db.has_section(section):
            missing_sections.append(section)
        continue
    if not db.has_section(section):
        missing_sections.append(section)
        continue
    if not db.has_keyword(section, keyword):
        missing_keywords.append(f"{section} :: {keyword}")

print(f"checked {len(CHECKS)} entries against {db.stats()['keywords']} keywords")
print(f"\nMISSING SECTIONS ({len(missing_sections)}):")
for item in missing_sections:
    print("   ", item)
print(f"\nMISSING KEYWORDS ({len(missing_keywords)}):")
for item in missing_keywords:
    print("   ", item)

print("\n--- enum values for the ones that matter ---")
for section, keyword in [
    ("GLOBAL", "RUN_TYPE"), ("FORCE_EVAL/DFT/SCF/OT", "MINIMIZER"),
    ("FORCE_EVAL/DFT/SCF/OT", "PRECONDITIONER"),
    ("FORCE_EVAL/DFT/SCF/SMEAR", "METHOD"),
    ("FORCE_EVAL/DFT/XC/VDW_POTENTIAL", "POTENTIAL_TYPE"),
    ("FORCE_EVAL/DFT/XC/VDW_POTENTIAL/PAIR_POTENTIAL", "TYPE"),
    ("FORCE_EVAL/DFT/POISSON", "PSOLVER"), ("FORCE_EVAL/DFT/QS", "EXTRAPOLATION"),
    ("MOTION/MD", "ENSEMBLE"), ("MOTION/MD/THERMOSTAT", "TYPE"),
    ("MOTION/BAND", "BAND_TYPE"), ("FORCE_EVAL/DFT/KPOINTS", "SCHEME"),
    ("FORCE_EVAL/DFT/KPOINTS", "WAVEFUNCTIONS"),
    ("FORCE_EVAL/SUBSYS/CELL", "PERIODIC"),
    ("MOTION/PRINT/TRAJECTORY/EACH", "MD"),
]:
    info = db.keyword(section, keyword)
    if info:
        values = ", ".join(info.values[:14])
        print(f"  {section}/{keyword}: type={info.type} default={info.default} "
              f"[{values}{' ...' if len(info.values) > 14 else ''}]")
    else:
        print(f"  {section}/{keyword}: (missing)")
