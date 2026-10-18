"""Offscreen check of the classical (FIST) page of the GUI.

Builds the real window, switches to METHOD FIST, fills the file paths and the
tables, and prints what the preview would contain - no display needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from PySide6.QtCore import QEventLoop, QTimer          # noqa: E402
from PySide6.QtWidgets import QApplication             # noqa: E402

from cp2kbuild.gui import MainWindow                   # noqa: E402


def pump(ms: int = 300) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_ready(window: MainWindow, rounds: int = 100) -> None:
    for _ in range(rounds):
        pump(100)
        if window.tabs.isEnabled():
            return
    raise SystemExit("the data index never finished loading")


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    wait_ready(window)
    generate = window.generate

    generate.load_from_path(str(HERE / "examples" / "water.xyz"))
    pump()
    generate.w_method.setCurrentIndex(1)                     # FIST
    pump()
    generate.task_combo.setCurrentIndex(generate.task_combo.findData("md"))
    pump()

    print("classical page visible      :", generate.classical_box.isVisible())
    print("DFT page hidden             :", not generate.method_box.isVisible())
    print("SCF route page hidden       :", not generate.route_box.isVisible())
    print("kind table hidden           :", not generate.kind_box.isVisible())
    print("β band item enabled         :",
          generate.task_combo.model().item(
              generate.task_combo.findData("band")).isEnabled())
    print("task label for md           :", generate.task_combo.currentText()
          if generate.task_combo.currentData() == "md" else "(not md)")
    print("timestep default (classical):", generate.w_timestep.value())

    generate.w_mm_parm.setText("mix.prmtop")
    generate.w_mm_conn.setText("mix.prmtop")
    generate.w_mm_coord.setText("mix.inpcrd")
    generate.w_mm_table.add_row(["O", "78.198", "3.166", "11.4"])
    generate.w_mm_table.add_row(["H", "0.0", "3.30523", "11.4"])
    generate.w_mm_charges.add_row(["O", "-0.8476"])
    generate.w_mm_charges.add_row(["H", "0.4238"])
    generate.w_mm_molecules.add_row(["AMBER", "mol1.prmtop", "100"])
    generate.w_mm_molecules.add_row(["AMBER", "mol2.prmtop", "50"])
    generate.w_mm_ewald.setCurrentIndex(1)                   # SPME
    generate.w_mm_ewald_gmax.setValue(64)
    generate.w_mm_o_spline.setValue(6)
    pump()

    spec = generate.collect_spec()
    print("collected route             :", spec.mm_route)
    print("collected charge            :", spec.charge)
    print("mw scale14 (None=route)     :", spec.mm_vdw_scale14)
    print("molecules                   :", spec.mm_molecules)
    print("element lj                  :", spec.mm_element_lj)
    print("ewald / gmax / o_spline     :", spec.mm_ewald, spec.mm_ewald_gmax,
          spec.mm_o_spline)

    text = generate.preview.toPlainText()
    for line in text.splitlines():
        if line.strip().startswith(("&", "METHOD", "PARMTYPE", "PARM_FILE_NAME",
                                    "VDW_SCALE14", "EI_SCALE14", "EWALD_TYPE",
                                    "GMAX", "O_SPLINE", "CONNECTIVITY",
                                    "CONN_FILE_NAME", "COORDINATE",
                                    "COORD_FILE_NAME", "NMOL", "ENSEMBLE",
                                    "TIMESTEP", "STRESS_TENSOR")):
            print("   ", line.strip())

    # AMBER + a non-periodic box must fall back to EWALD_TYPE NONE
    generate.w_method.setCurrentIndex(1)
    generate.w_mm_route.setCurrentIndex(5)                   # PAIR
    pump()
    print("PAIR: terms box visible     :", generate.mm_terms_box.isVisible(),
          "| molecules box hidden:", not generate.mm_molecules_box.isVisible())
    spec = generate.collect_spec()
    print("PAIR: parm/conn/coord kept  :", spec.mm_parm_file, spec.mm_conn_file,
          spec.mm_coord_file)
    generate.reset_defaults()
    pump()
    spec = generate.collect_spec()
    print("after reset: method/route   :", spec.method, spec.mm_route)
    print("after reset: molecules/lj    :", spec.mm_molecules, spec.mm_lj,
          spec.mm_element_lj)
    print("after reset: method combo   :", generate.w_method.currentIndex(),
          "| DFT page visible:", generate.method_box.isVisible())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
