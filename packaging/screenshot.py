"""Render the GUI to PNG files so its layout can be inspected.

Uses the native Qt platform plugin by default because the ``offscreen`` plugin
has no font directory on some installations and would render blank text; set
``QT_QPA_PLATFORM=offscreen`` explicitly if no display is available.

Usage::

    python packaging/screenshot.py [output_dir]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def pump(milliseconds: int) -> None:
    """Run the event loop for a while so background work can finish."""
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "packaging" / "shots"
    out.mkdir(parents=True, exist_ok=True)

    from cp2kbuild.gui import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1280, 820)
    window.show()

    for _ in range(60):                       # wait for the loader thread
        pump(100)
        if window.tabs.isEnabled():
            break
    print("tabs enabled:", window.tabs.isEnabled())

    example = ROOT / "examples" / "water.xyz"
    silicon = ROOT / "examples" / "silicon.xyz"

    def shot(name: str) -> None:
        pump(250)
        path = out / f"{name}.png"
        window.grab().save(str(path))
        print("wrote", path)

    # --- generate tab, several tasks ---
    window.tabs.setCurrentWidget(window.generate)
    window.generate.load_from_path(str(example))
    for task in ("energy", "geo_opt", "cell_opt", "md", "freq", "band", "pdos", "neb"):
        index = window.generate.task_combo.findData(task)
        window.generate.task_combo.setCurrentIndex(index)
        if task == "band":
            window.generate.load_from_path(str(silicon))
        elif task in ("energy", "pdos"):
            window.generate.load_from_path(str(example))
        shot(f"generate_{task}")

    # the metadynamics block is on the MD page: shoot it folded (what you see
    # first) and open with a well-tempered setup, plus the PLUMED route
    from cp2kbuild.metadyn import Cv, Metavar, Wall

    index = window.generate.task_combo.findData("md")
    window.generate.task_combo.setCurrentIndex(index)
    meta = window.generate.metadyn_panel
    meta.set_expanded(False)
    shot("metadyn_folded")
    meta.set_expanded(True)
    meta.w_enable.setChecked(True)
    meta._cvs = [Cv(kind="distance", atoms=(1, 2)),
                 Cv(kind="coordination", kinds_from="O", kinds_to="H", r0=1.2,
                    nn=6, nd=8)]
    meta._metavars = [Metavar(colvar=1, scale=0.5,
                              wall=Wall(position=2.4, type="QUADRATIC",
                                        direction="WALL_MINUS", k=0.5)),
                      Metavar(colvar=2)]
    meta.w_wt.setChecked(True)
    meta.w_delta_t.setValue(3000.0)
    meta.w_min_disp.setText("0.05")
    meta.w_tail.setText("3.0")
    meta._refresh()
    shot("metadyn_open")
    meta.w_route.setCurrentIndex(1)
    meta.w_plumed.setText("water.plumedinp")
    meta.w_sigma.setText("0.03")
    meta._refresh()
    window.generate.refresh()
    shot("metadyn_plumed")
    # the preview's second page: the plumed.dat that the same settings produce
    window.generate.preview_page.setCurrentIndex(1)
    shot("metadyn_plumed_draft")
    window.generate.preview_page.setCurrentIndex(0)
    # ... and the PLUMED-native table: its own CVs (nm, multicolvar components),
    # its own METAD numbers and the free-line escape hatch
    from cp2kbuild.plumedcv import PlumedCv, PlumedWall

    meta._plumed_cvs = [
        PlumedCv(label="cn", action="COORDINATIONNUMBER",
                 params="SPECIES=1-100 R_0=0.33 MEAN", component="mean", sigma=0.3),
        PlumedCv(label="nn", action="AROUND",
                 params="DATA=cn ATOM=1 SIGMA=0.1 XLOWER=-2.0 XUPPER=2.0 MEAN",
                 component="mean"),
        PlumedCv(label="d", action="DISTANCES",
                 params="GROUPA=1-10 GROUPB=11-20 MIN={BETA=500.}", component="min"),
    ]
    meta._plumed_walls = [PlumedWall(arg="cn.mean", kind="UPPER_WALLS", at=6.0,
                                     kappa=5000.0)]
    meta.w_p_biasfactor.setText("10")
    meta.w_p_temp.setText("330")
    meta.w_free.setPlainText("opes: OPES_METAD ARG=cn.mean PACE=500 BARRIER=40")
    meta._refresh()
    window.generate.refresh()
    shot("metadyn_plumed_native")
    window.generate.preview_page.setCurrentIndex(1)
    shot("metadyn_plumed_native_draft")
    window.generate.preview_page.setCurrentIndex(0)
    # ... and the controls that came later: Å switch, adaptive width, restart,
    # METAD extras, custom bias label - plus the draft they produce
    meta.w_p_units.setCurrentIndex(max(meta.w_p_units.findData("A"), 0))
    meta.w_p_adaptive.setCurrentIndex(max(meta.w_p_adaptive.findData("GEOM"), 0))
    meta.w_p_sigma.setValue(0.4)
    meta.w_p_sigma_min.setText("0.2")
    meta.w_p_sigma_max.setText("0.8")
    meta.w_p_restart.setChecked(True)
    meta.w_p_label.setText("bias")
    meta.w_p_extra.setText("GRID_MIN=-1 GRID_MAX=20 CALC_RCT")
    meta.w_p_args.setText("cn.mean")
    meta._refresh()
    window.generate.refresh()
    shot("metadyn_plumed_advanced")
    window.generate.preview_page.setCurrentIndex(1)
    shot("metadyn_plumed_advanced_draft")
    window.generate.preview_page.setCurrentIndex(0)
    meta.w_p_units.setCurrentIndex(0)
    meta.w_p_adaptive.setCurrentIndex(0)
    meta.w_p_sigma_min.clear()
    meta.w_p_sigma_max.clear()
    meta.w_p_restart.setChecked(False)
    meta.w_p_label.setText("m")
    meta.w_p_extra.clear()
    meta.w_p_args.clear()
    meta._plumed_cvs = []
    meta._plumed_walls = []
    meta.w_free.clear()
    meta.w_p_biasfactor.clear()
    meta.w_p_temp.clear()
    meta.w_sigma.clear()
    meta.w_route.setCurrentIndex(0)
    meta.w_enable.setChecked(False)
    meta.set_expanded(False)
    meta._refresh()

    # a generation-time problem must show up as a row in the 校验结果 table
    meta.set_expanded(True)
    meta.w_enable.setChecked(True)
    meta._cvs = []
    meta._metavars = []
    meta._refresh()
    window.generate.refresh()
    shot("diagnostics_build_warning")
    meta.w_enable.setChecked(False)
    meta.set_expanded(False)
    meta._refresh()

    # the two dialogs (the CV one lost its type combo once - it is a row like the
    # others, so it has to stay visible)
    from cp2kbuild.gui import CvDialog, MetavarDialog
    from cp2kbuild.metadyn import Metavar as _Metavar

    for name, dialog in (
            ("dialog_cv_coordination",
             CvDialog(Cv(kind="coordination", kinds_from="O", kinds_to="H",
                         r0=1.2, nn=6, nd=8))),
            ("dialog_metavar_wall",
             MetavarDialog(_Metavar(colvar=1, scale=0.5,
                                    wall=Wall(position=2.4, type="QUADRATIC",
                                              direction="WALL_MINUS", k=0.5)), 2))):
        dialog.resize(dialog.sizeHint())
        dialog.show()
        pump(350)
        path = out / f"{name}.png"
        dialog.grab().save(str(path))
        print("wrote", path)
        dialog.close()

    # the other blocks that fold away, so their look can be checked too
    for name, box in (("fold_md_prints", window.generate.md_print_box),
                      ("fold_scf_details", window.generate.scf_details_box),
                      ("fold_molden", window.generate.molden_box),
                      ("fold_restart", window.generate.restart_box)):
        box.set_expanded(True)
        shot(name)
        box.set_expanded(False)

    # the convergence tab: fill it as a user would, then show real results
    window.tabs.setCurrentWidget(window.scan_tab)
    scan = window.scan_tab
    scan.silent = True
    scan.w_source.setCurrentIndex(scan.w_source.findData("structure"))
    scan.w_cutoffs.setText("300,400,500,600,700")
    scan.w_rel_cutoffs.setText("60")
    scan.w_scan_outdir.setText(str(out / "scan_demo"))
    shot("converge_setup")

    demo = ROOT / "examples" / "convergence_demo"
    if demo.is_dir():
        scan.w_converge_dir.setText(str(demo))
        scan.analyse()
        shot("converge_report")

    window.tabs.setCurrentWidget(window.validate_tab)
    broken = out / "broken.inp"
    broken.write_text(
        "&GLOBAL\n   PROJECT_NAME broken\n   RUN_TYPE GEO_OPT\n&END GLOBAL\n"
        "&FORCE_EVAL\n   METHOD Quickstep\n   &DFT\n"
        "      BASIS_SET_FILE_NAME BASIS_MOLOPT\n"
        "      POTENTIAL_FILE_NAME GTH_POTENTIALS\n"
        "      &MGRID\n         CUTOFF 300\n         REL_CUTOFF 500\n      &END MGRID\n"
        "      &XC\n         &XC_FUNCTIONAL PBE\n         &END XC_FUNCTIONAL\n      &END XC\n"
        "   &END DFT\n   &SUBSYS\n      &CELL\n         ABC 20 20 20\n"
        "         PERIODIC NONE\n      &END CELL\n      &COORD\n"
        "         Fe 0 0 0\n      &END COORD\n      &KIND Fe\n         ELEMENT Fe\n"
        "         BASIS_SET DZVP-MOLOPT-SR-GTH\n         POTENTIAL GTH-PBE-q8\n"
        "      &END KIND\n   &END SUBSYS\n&END FORCE_EVAL\n"
        "&MOTION\n   &GEO_OPT\n      OPTIMIZR BFGS\n   &END GEO_OPT\n&END MOTION\n",
        encoding="utf-8")
    window.validate_tab.files.addItem(str(broken))
    window.validate_tab.run_checks()
    shot("validate")

    window.tabs.setCurrentWidget(window.keyword_tab)
    window.keyword_tab.search.setText("cutoff")
    window.keyword_tab.do_search()
    shot("keyword")

    window.tabs.setCurrentWidget(window.data_tab)
    window.data_tab.elements.setText("H, C, O, Fe, Pt")
    window.data_tab.recommend()
    shot("data")

    return 0


if __name__ == "__main__":
    sys.exit(main())
