"""Headless functional test for the GUI.

Two kinds of check, neither of which needs a display:

* **equivalence** - for a set of parameter combinations the text the GUI shows
  must be byte-identical to what the command line interface writes for the same
  settings.  This catches every parameter-mapping mistake (two task pages
  sharing a widget, a default that differs from ``Spec``, ...);
* **layout sanity** - after showing the window every visible widget must have a
  non-zero size, and the panes that matter must hold actual content.

Usage::

    python packaging/gui_selftest.py
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# the check messages carry box-drawing characters and the odd emoji; on a GBK
# console the first one aborts the whole run with UnicodeEncodeError, so the
# streams are made lossy-safe instead of the messages being dumbed down
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox,  # noqa: E402
                               QDoubleSpinBox, QLineEdit, QListWidget,
                               QPlainTextEdit, QPushButton, QSpinBox,
                               QTableWidget, QToolButton, QWidget)

WATER = str(ROOT / "examples" / "water.xyz")
SILICON = str(ROOT / "examples" / "silicon.xyz")
FF_INCLUDE = str(ROOT / "examples" / "generated" / "fist_water_ff.inc")

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"  ok   {message}")
    else:
        print(f"  FAIL {message}")
        failures.append(message)


def pump(milliseconds: int = 200) -> None:
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def cli_text(args: list[str]) -> str:
    """Run the CLI with ``--stdout`` and return what it printed."""
    from cp2kbuild.cli import main as cli_main

    buffer = io.StringIO()
    saved = sys.stdout
    sys.stdout = buffer
    try:
        code = cli_main(args)
    finally:
        sys.stdout = saved
    if code not in (0, 1):        # 1 means the validation found problems
        raise RuntimeError(f"CLI exited with {code}")
    return buffer.getvalue()


def main() -> int:
    from cp2kbuild.gui import MainWindow
    from cp2kbuild.recipes import poisson_solvers_for

    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1280, 820)
    window.show()
    for _ in range(80):
        pump(100)
        if window.tabs.isEnabled():
            break

    generate = window.generate

    def set_task(task: str) -> None:
        generate.task_combo.setCurrentIndex(generate.task_combo.findData(task))

    def set_scf(value: str) -> None:
        """Pick an SCF route ("auto" / "OT" / "DIAGONALIZATION") by its data."""
        for i in range(generate.w_scf.count()):
            if generate.w_scf.itemData(i) == value:
                generate.w_scf.setCurrentIndex(i)
                return
        raise AssertionError(f"no SCF route {value}")

    def set_dispersion(value) -> None:
        for i in range(generate.w_dispersion.count()):
            if generate.w_dispersion.itemData(i) == value:
                generate.w_dispersion.setCurrentIndex(i)
                return

    meta = generate.metadyn_panel

    def reset_metadyn() -> None:
        """The MD page's 元动力学 block back to "off, nothing defined"."""
        meta.w_enable.setChecked(False)
        meta.w_route.setCurrentIndex(0)
        meta.w_plumed.setText("plumed.dat")
        meta.w_hills.setChecked(True)
        meta.w_nt_hills.setValue(30)
        meta.w_height.setValue(0.001)
        meta.w_wt.setChecked(False)
        meta.w_follow_temp.setChecked(True)
        meta.w_temp.setValue(300.0)
        meta.w_delta_t.setValue(3000.0)
        meta.w_min_disp.clear()
        meta.w_tail.clear()
        meta.w_lagrange.setChecked(False)
        meta.w_langevin.setChecked(False)
        meta._cvs = []
        meta._metavars = []
        meta.w_print_colvar.setChecked(True)
        meta.w_print_hills.setChecked(True)
        meta.w_print_every.setValue(1)
        meta.set_expanded(False)
        meta._refresh()

    def reset_gui() -> None:
        """Put every widget back to the values the CLI defaults to."""
        generate.w_method.setCurrentIndex(0)
        generate._reset_mm_defaults()
        generate.w_charge.setValue(0)
        generate.w_multiplicity.setValue(1)
        generate.w_force_uks.setChecked(False)
        generate.w_vacuum.setValue(12.0)
        generate.w_periodic.setCurrentIndex(0)
        generate.w_cell.clear()
        generate.w_cutoff_auto.setChecked(True)
        generate.w_rel_cutoff.setValue(60.0)
        set_dispersion(None)
        generate.w_basis.clear()
        generate.w_basis_file.clear()
        generate.w_potential_file.clear()
        generate.w_functional.setCurrentText("PBE")
        generate.w_print_level.setCurrentText("MEDIUM")
        generate.w_walltime.setValue(0)
        generate.w_scf.setCurrentIndex(0)
        generate.w_basis_target.setCurrentIndex(0)
        generate.w_eps_scf.clear()
        generate.w_max_scf.setValue(30)
        generate.w_outer_max_scf.setValue(-1)
        generate.w_outer_eps_scf.clear()
        generate.w_qs_method.setCurrentText("GPW")
        generate.w_ngrids.setValue(0)
        generate.w_added_mos.setValue(0)
        generate.w_smearing.setChecked(False)
        generate.w_electronic_temperature.setValue(300.0)
        generate.w_ignore_convergence.setChecked(False)
        generate.w_mixing_alpha.setValue(0.4)
        generate.w_mixing_nbuffer.setValue(8)
        generate.w_mixing_beta.clear()
        generate.w_mixing_method.setCurrentText("BROYDEN_MIXING")
        generate.w_smear_method.setCurrentText("FERMI_DIRAC")
        generate.w_ot_minimizer.setCurrentText("DIIS")
        generate.w_ot_preconditioner.setCurrentText("FULL_SINGLE_INVERSE")
        generate.w_ot_algorithm.setCurrentText("STRICT")
        generate.w_ot_linesearch.setCurrentText("2PNT")
        generate.w_ot_stepsize.clear()
        generate.w_ot_energy_gap.clear()
        generate.w_ot_safe_diis.setCurrentIndex(0)
        generate.w_ot_rotation.setCurrentIndex(0)
        generate.w_outer_optimizer.setCurrentIndex(0)
        generate.w_diag_algorithm.setCurrentText("STANDARD")
        generate.w_level_shift.clear()
        generate.w_poisson.setCurrentIndex(0)
        generate.w_scaled_coords.setChecked(False)
        generate.w_h_to_d.setChecked(False)
        generate.w_eps_default.clear()
        generate.w_extrapolation.setCurrentIndex(0)
        generate.kpoint_box.setChecked(False)
        generate.w_kgrid_auto.setChecked(True)
        generate.w_kpoint_spacing.setValue(20.0)
        generate.w_kgrid.clear()
        generate.w_kpoint_scheme.setCurrentIndex(0)
        generate.w_kpoint_scheme_text.clear()
        generate.w_kpoint_points.setRowCount(0)
        generate.w_kpoint_units.setCurrentText("B_VECTOR")
        generate.w_kpoint_gamma_centered.setChecked(False)
        generate.w_kpoint_full_grid.setChecked(False)
        generate.w_kpoint_symmetry.setChecked(True)
        generate.w_kpoint_verbose.setChecked(False)
        generate.w_kpoint_wavefunctions.setCurrentText("REAL")
        generate.w_mo_cubes.setChecked(False)
        generate.w_mo_cubes_nhomo.setValue(1)
        generate.w_mo_cubes_nlumo.setValue(0)
        generate.w_mo_cubes_stride.clear()
        generate.w_density_cube.setChecked(False)
        generate.w_density_cube_stride.clear()
        generate.w_fixed_atoms.clear()
        generate.w_fixed_atoms_components.setCurrentText("XYZ")
        generate.w_thermostat_timecon.setValue(100.0)
        generate.w_barostat_timecon.setValue(500.0)
        generate.w_trajectory_stride.setValue(10)
        generate.w_thermostat_region.setCurrentIndex(0)      # GLOBAL
        generate.w_md_temp_kind.setChecked(False)
        generate.w_md_temp_kind_every.setValue(10)
        generate.w_md_energy.setChecked(False)
        generate.w_md_energy_every.setValue(1)
        generate.w_md_energy_file.clear()
        generate.w_thermostat_temp.setChecked(False)
        generate.w_thermostat_temp_every.setValue(10)
        generate.w_project.clear()
        # restart / continuation widgets (they were never reset before, which
        # let one case leak into the next)
        generate.w_ext_restart.clear()
        generate.w_restart_default.setChecked(True)
        generate.select_take(None)                 # 「全部」by data, not index
        generate.w_restart_every.setValue(20)
        generate.w_restart_add_last.setChecked(True)
        generate.w_restart_backups.setValue(-1)
        generate.w_scf_guess.setCurrentText("RESTART")
        generate.w_wfn_file.clear()
        generate.w_wfn_print.setChecked(True)
        generate.w_wfn_every.setValue(0)
        generate.w_wfn_add_last.setChecked(True)
        generate.w_wfn_backups.setValue(-1)
        generate.w_motion_restart.setChecked(True)
        generate.w_molden.setChecked(False)
        generate.w_molden_file.clear()
        generate.w_molden_nlumo.setValue(0)
        generate.w_molden_unit.setCurrentText("ANGSTROM")
        generate.w_molden_gto_kind.setCurrentText("SPHERICAL")
        generate.w_molden_pseudo.setChecked(False)
        generate.w_molden_cell.setChecked(False)
        generate.w_molden_vib.setChecked(True)
        # the「打印」checkboxes are per-task but shared, so put them back too
        generate.w_print_forces.setChecked(True)
        generate.w_print_mulliken.setChecked(False)
        generate.w_print_dipole.setChecked(True)
        # task specific widgets are shared across cases, so reset them too
        generate.w_pdos_nlumo.setValue(0)
        generate.w_pdos_ldos.clear()
        generate.w_pdos_broaden.setValue(0.0)
        generate.w_pdos_broaden_type.setCurrentText("GAUSSIAN")
        generate.w_geo_optimizer.setCurrentText("BFGS")
        generate.w_geo_max_iter.setValue(200)
        generate.w_cell_optimizer.setCurrentText("BFGS")
        generate.w_cell_max_iter.setValue(200)
        generate.w_pressure.setValue(0.0)
        generate.w_keep_angles.setChecked(False)
        generate.w_ensemble.setCurrentText("NVT")
        generate.w_thermostat.setCurrentText("NOSE")
        generate.w_temperature.setValue(300.0)
        generate.w_timestep.setValue(0.5)
        generate.w_steps.setValue(5000)
        # the heating/cooling rows and the program table
        generate.w_ramp.setChecked(False)
        generate.w_ramp_target.setValue(600.0)
        generate.w_temperature_annealing.setValue(0.0)
        generate.w_annealing.setValue(0.0)
        generate.w_annealing_cell.setValue(0.0)
        generate.w_langevin_gamma.setValue(0.01)
        generate.w_restart_temperature_annealing.setChecked(False)
        while generate.w_program_table.rowCount():
            generate.w_program_table.removeRow(0)
        generate._program_add_row(300.0, 5.0)
        generate._program_add_row(300.0, 10.0, target=600.0)
        generate.w_program_outdir.clear()
        generate.w_md_pressure.setValue(1.0)
        generate.w_seed.setValue(0)
        generate.w_kpath.clear()
        generate.w_npoints.setValue(40)
        generate.w_kgrid.clear()
        generate.w_lattice.setCurrentIndex(0)
        generate.w_replicas.clear()
        # the 元动力学 block is on the MD page but feeds the same Spec: leaving it
        # switched on would leak into every case below
        reset_metadyn()
        # ... and the fold state is remembered in QSettings, so a block a test
        # opened would stay open for the next one
        for box in (generate.md_print_box, generate.scf_details_box,
                    generate.molden_box, generate.cube_box,
                    generate.restart_box, generate.temperature_program_box):
            box.set_expanded(False)

    def gui_text() -> str:
        generate.refresh()
        return generate.text

    def compare(label: str, cli_args: list[str]) -> None:
        expected = cli_text(cli_args)
        actual = gui_text()
        if expected == actual:
            check(True, f"{label}: GUI == CLI")
            return
        check(False, f"{label}: GUI != CLI")
        import difflib

        diff = list(difflib.unified_diff(expected.splitlines(), actual.splitlines(),
                                         "cli", "gui", lineterm=""))
        for line in diff[:40]:
            print("       " + line)

    print("== 等价性：GUI 输出必须和 CLI 完全一致 ==")

    # 0. a freshly opened window, before anything is touched: this is what the
    #    user sees first, so it has to be the CLI defaults already (the widget
    #    defaults used to say SCF_GUESS ATOMIC while the tool said RESTART)
    generate.load_from_path(WATER)
    set_task("energy")
    compare("刚打开窗口（未动任何控件）",
            ["new", "--task", "energy", "--structure", WATER, "--vacuum", "12",
             "--stdout"])

    reset_gui()
    # 1. energy, molecule in vacuum, D3(BJ)
    generate.load_from_path(WATER)
    set_task("energy")
    generate.w_vacuum.setValue(12.0)
    generate.w_dispersion.setCurrentIndex(
        [i for i in range(generate.w_dispersion.count())
         if generate.w_dispersion.itemData(i) == "d3bj"][0])
    compare("energy water+d3bj",
            ["new", "--task", "energy", "--structure", WATER, "--vacuum", "12",
             "--dispersion", "d3bj", "--stdout"])

    reset_gui()
    # 2. geo_opt with a non-default optimiser (the geo/cell widget collision)
    set_task("geo_opt")
    set_dispersion("d3bj")
    generate.w_geo_optimizer.setCurrentText("LBFGS")
    generate.w_geo_max_iter.setValue(50)
    compare("geo_opt LBFGS/50",
            ["new", "--task", "geo_opt", "--structure", WATER, "--vacuum", "12",
             "--dispersion", "d3bj", "--optimizer", "LBFGS", "--max-iter", "50",
             "--stdout"])

    reset_gui()
    # 3. cell_opt with its own optimiser settings (must differ from geo_opt)
    generate.load_from_path(SILICON)
    set_task("cell_opt")
    generate.w_cell_optimizer.setCurrentText("CG")
    generate.w_cell_max_iter.setValue(77)
    generate.w_pressure.setValue(5.0)
    generate.w_keep_angles.setChecked(True)
    compare("cell_opt CG/77/5bar/keep-angles",
            ["new", "--task", "cell_opt", "--structure", SILICON, "--optimizer", "CG",
             "--max-iter", "77", "--external-pressure", "5.0", "--keep-angles",
             "--stdout"])

    # the two task pages must really be independent: set the geo page to LBFGS,
    # then the cell page to CG, and check each kept its own value
    reset_gui()
    generate.load_from_path(WATER)
    set_task("geo_opt")
    generate.w_geo_optimizer.setCurrentText("LBFGS")
    set_task("cell_opt")
    generate.w_cell_optimizer.setCurrentText("CG")
    set_task("geo_opt")
    geo_text = gui_text()
    set_task("cell_opt")
    cell_text = gui_text()
    check("OPTIMIZER LBFGS" in geo_text and "OPTIMIZER CG" in cell_text,
          "geo_opt 与 cell_opt 的优化器参数互相独立")

    reset_gui()
    # 4. MD
    generate.load_from_path(WATER)
    set_task("md")
    generate.w_ensemble.setCurrentText("NVT")
    generate.w_thermostat.setCurrentText("CSVR")
    generate.w_temperature.setValue(330.0)
    generate.w_steps.setValue(20000)
    generate.w_seed.setValue(12345)
    compare("md NVT/CSVR/330K/20000/seed",
            ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
             "--ensemble", "NVT", "--thermostat", "CSVR", "--temperature", "330",
             "--steps", "20000", "--seed", "12345", "--stdout"])

    # 4b. 元动力学: the MD page's foldable block fills the same Spec fields the
    #     CLI flags do, so the bytes have to match - switching it off must change
    #     nothing, and folding it must not touch the input either
    reset_gui()
    generate.load_from_path(WATER)
    set_task("md")
    generate.w_ensemble.setCurrentText("NVT")
    generate.w_thermostat.setCurrentText("CSVR")
    generate.w_temperature.setValue(330.0)
    generate.w_steps.setValue(20000)
    generate.w_seed.setValue(12345)
    compare("md（元动力学关闭）",
            ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
             "--ensemble", "NVT", "--thermostat", "CSVR", "--temperature", "330",
             "--steps", "20000", "--seed", "12345", "--stdout"])

    from cp2kbuild.gui import BUILD_WARNING_CODE, CollapsibleBox
    from cp2kbuild.metadyn import CV_KINDS, Cv, Metavar, Wall

    # it is a block on the MD page, folded away by default; the state is kept in
    # QSettings, so put it back to "collapsed" first and watch it come and go
    meta.set_expanded(False)
    check(not meta.is_expanded() and not meta.body.isVisibleTo(meta)
          and meta.isVisibleTo(generate) and "元动力学" in meta.title(),
          "元动力学是 MD 页上一块默认收起的块（标题行还在）")
    meta.set_expanded(True)
    check(meta.body.isVisibleTo(meta), "展开后内容可见")
    check(str(window.settings.value("fold/metadyn")).lower() == "true",
          f"展开状态记进了 QSettings：{window.settings.value('fold/metadyn')!r}")
    folded_text = gui_text()
    meta.set_expanded(False)
    check(gui_text() == folded_text,
          "收起 / 展开不改变生成的输入（折叠只是显示）")

    META_CLI = ["--metadyn", "--meta-wt", "--meta-delta-t", "3000",
                "--meta-min-disp", "0.05", "--meta-tail-cutoff", "3.0",
                "--meta-sigma", "0.25",
                "--cv", "distance:1,2", "--cv", "coordination:O,H,1.2,6,8",
                "--metavar", "1:SCALE=0.5:WALL=QUADRATIC,2.4,WALL_MINUS,0.5",
                "--metavar", "2"]
    meta.w_enable.setChecked(True)
    check(meta.is_expanded(), "勾上「启用元动力学」会自动展开这一块")

    # 启用但还没定义 CV：这条生成期问题必须是表格里的**一行**，而不是只挂在
    # 摘要的悬浮提示里（那样"1 项生成期问题"下面看着像空的）。刷新是防抖的，
    # 所以要等事件循环跑完再看。
    pump(300)
    gen_table = [(generate.diagnostics.item(r, 0).text(),
                  generate.diagnostics.item(r, 1).text(),
                  generate.diagnostics.item(r, 2).text())
                 for r in range(generate.diagnostics.rowCount())]
    check(sum(1 for _lv, code, _t in gen_table
              if code == BUILD_WARNING_CODE) == 1
          and any("没有定义任何集体变量" in text for _lv, _c, text in gen_table),
          f"生成期问题进了校验结果表：{gen_table}")
    # 真实反馈：「存在警告时为啥上方还是显示 0 错误/0 警告」——生成器的那行以前
    # 被排除在计数之外（只算作"生成期问题"），于是表里有警告、上面写 0 警告。
    check("1 警告" in generate.summary.text()
          and "0 错误" in generate.summary.text()
          and "来自生成器" in generate.summary.text(),
          f"表里有几行，摘要就数几行（生成器那行也算）："
          f"{generate.summary.text()!r}")

    meta.w_wt.setChecked(True)
    meta.w_follow_temp.setChecked(True)
    meta.w_delta_t.setValue(3000.0)
    meta.w_min_disp.setText("0.05")
    meta.w_tail.setText("3.0")
    # the panel default hill width (same as --meta-sigma): it fills the SCALE of
    # every &METAVAR that has none, so the CLI and the GUI produce one file
    meta.w_sigma.setText("0.25")
    meta._cvs = [Cv(kind="distance", atoms=(1, 2)),
                 Cv(kind="coordination", kinds_from="O", kinds_to="H", r0=1.2,
                    nn=6, nd=8)]
    meta._metavars = [Metavar(colvar=1, scale=0.5,
                              wall=Wall(position=2.4, type="QUADRATIC",
                                        direction="WALL_MINUS", k=0.5)),
                      Metavar(colvar=2)]
    meta._refresh()
    compare("md + 井温元动力学（2 个 CV，其中一个带二次墙）",
            ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
             "--ensemble", "NVT", "--thermostat", "CSVR", "--temperature", "330",
             "--steps", "20000", "--seed", "12345", "--stdout", *META_CLI])

    # the tables are read-only summaries: the row order *is* the &METAVAR index,
    # so it must not be sorted away
    check(meta.w_cv_table.rowCount() == 2
          and meta.w_cv_table.item(0, 0).text().startswith("距离")
          and meta.w_cv_table.item(1, 0).text().startswith("配位数"),
          f"CV 表的行序 = CV 定义顺序（{meta.w_cv_table.rowCount()} 行）")
    check(not meta.w_cv_table.isSortingEnabled()
          and not meta.w_metavar_table.isSortingEnabled(),
          "CV / &METAVAR 表不排序（行序就是 COLVAR 序号）")
    check(meta.w_metavar_table.rowCount() == 2
          and meta.w_metavar_table.item(1, 0).text() == "2",
          "&METAVAR 表列出了两个偏置项")
    check("每 30 步一个丘陵" in meta.enable_hint.text(),
          f"说明按 CP2K 的 NT_HILLS 默认值写：{meta.enable_hint.text()}")
    check("&METADYN" in gui_text() and "&COLVAR" in gui_text(),
          "这两段出现在右侧的完整预览里（没有第二份预览了）")

    # On the PLUMED route the CV table is *not* dropped: CP2K only reads the file
    # name (metadynamics_utils.F:129), so the CVs/hills become the plumed.dat draft
    # - which is what the second preview page shows.
    meta.w_route.setCurrentIndex(1)
    meta.w_plumed.setText("water.plumedinp")
    meta._refresh()
    compare("md + PLUMED 驱动",
            ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
             "--ensemble", "NVT", "--thermostat", "CSVR", "--temperature", "330",
             "--steps", "20000", "--seed", "12345", "--stdout",
             "--meta-plumed", "water.plumedinp"])
    # the two routes show *different* control sets: CP2K's own CV table belongs to
    # the native route, and the PLUMED table is its own language (nm, R_0
    # compulsory, multicolvar components), so neither is a translation of the other
    check(not meta.cv_box.isVisibleTo(meta)
          and not meta.metavar_box.isVisibleTo(meta)
          and not meta.print_box.isVisibleTo(meta)
          and not meta.hills_box.isVisibleTo(meta)
          and meta.plumed_cv_box.isVisibleTo(meta)
          and meta.plumed_bias_box.isVisibleTo(meta)
          and meta.plumed_free.isVisibleTo(meta),
          "PLUMED 路线：CP2K 那套控件（CV/&METAVAR/丘陵/打印）隐藏，"
          "换成 PLUMED 专用的一套")
    check(meta.plumed_row.isVisibleTo(meta),
          "PLUMED 路线显示 PLUMED 输入文件名那一行")
    # the preview grew a second page, and it must be exactly what the CLI writes
    draft_cli = cli_text(["new", "--task", "md", "--structure", WATER, "--vacuum",
                          "12", "--ensemble", "NVT", "--thermostat", "CSVR",
                          "--temperature", "330", "--steps", "20000",
                          "--seed", "12345", "--quiet", "--out",
                          str(ROOT / "packaging" / "shots" / "_draft.inp"),
                          *META_CLI, "--meta-plumed", "water.plumedinp",
                          "--meta-plumed-draft", "-"])
    check(generate.preview_page.isVisibleTo(generate)
          and generate.preview_page.count() == 2,
          "预览多了一个「plumed.dat」页")
    same_draft = generate.plumed_preview.toPlainText() == draft_cli
    check(same_draft,
          "plumed.dat 预览与 CLI --meta-plumed-draft 逐字节相同"
          if same_draft else
          f"plumed.dat 预览与 CLI 草稿不同：{draft_cli[:100]!r}")
    check("DISTANCE ATOMS=1,2" in draft_cli and "METAD ARG=cv1,cv2" in draft_cli
          and "HEIGHT=" in draft_cli and "BIASFACTOR=" in draft_cli,
          "草稿是 PLUMED 语法（DISTANCE / METAD / HEIGHT / BIASFACTOR）")
    # SCALE is the same quantity as SIGMA (the hill width), so it must land in the
    # METAD line and *not* become a COMBINE wrapper.  The per-CV SCALE (0.5 Å) wins
    # over the panel default (0.25) for the CV that has one, and the unit follows
    # the CV: the distance becomes nm (0.05), the coordination number is a plain
    # number and stays 0.25
    check("SIGMA=0.05,0.25" in draft_cli and "COMBINE ARG=cv1" not in draft_cli,
          "平原宽度：SCALE 0.5 Å → SIGMA 0.05 nm，配位数用默认 σ 0.25（无量纲），"
          "不再包一层 COMBINE")
    generate.preview_page.setCurrentIndex(1)
    pump(120)
    check(generate._active_preview() is generate.plumed_preview,
          "切到 plumed.dat 页后「保存 / 复制」作用于那一页")
    generate.preview_page.setCurrentIndex(0)
    pump(120)
    # with the CV list cleared there is nothing left to translate, so the hint
    # explains the route itself
    meta._cvs = []
    meta._metavars = []
    meta._refresh()
    pump(250)
    check("只从 &METADYN 读一个文件名" in meta.enable_hint.text(),
          f"提示写明了 CP2K 只读文件名：{meta.enable_hint.text()[:60]}")

    # ---- the PLUMED-native table: the draft comes from *it*, not from a
    # translation of the CP2K table ------------------------------------------ #
    from cp2kbuild.plumedcv import PlumedCv
    from cp2kbuild.plumedcv import draft as plumed_draft

    meta._plumed_cvs = [
        PlumedCv(label="cn", action="COORDINATIONNUMBER",
                 params="SPECIES=1-100 R_0=0.33 MEAN", component="mean",
                 sigma=0.3),
        PlumedCv(label="nn", action="AROUND",
                 params="DATA=cn ATOM=1 SIGMA=0.1 XLOWER=-2.0 XUPPER=2.0 MEAN",
                 component="mean"),
    ]
    meta.w_p_pace.setValue(500)
    meta.w_p_height.setValue(1.2)
    meta.w_p_sigma.setValue(0.1)
    meta.w_p_biasfactor.setText("10")
    meta.w_p_temp.setText("330")
    meta.w_free.setPlainText("opes: OPES_METAD ARG=cn.mean PACE=500 BARRIER=40")
    # the CP2K table still has two CVs - they must *not* leak into the draft
    meta._cvs = [Cv(kind="distance", atoms=(1, 2)),
                 Cv(kind="coordination", kinds_from="O", kinds_to="H", r0=1.2,
                    nn=6, nd=8)]
    meta._metavars = [Metavar(colvar=1, scale=0.5), Metavar(colvar=2)]
    meta._refresh()
    pump(300)
    native_draft = plumed_draft(meta.metadyn(), temperature=330.0)
    check("cn: COORDINATIONNUMBER SPECIES=1-100 R_0=0.33 MEAN" in native_draft
          and "m: METAD ARG=nn.mean SIGMA=0.1 HEIGHT=1.2 PACE=500 "
              "TEMP=330 BIASFACTOR=10 FILE=HILLS" in native_draft
          and "opes: OPES_METAD" in native_draft
          and "cv1: DISTANCE" not in native_draft,
          "PLUMED 专用表接管草稿：动作行/METAD ARG=nn.mean（叶子标量，cn 被 nn 引用"
          "所以不进 ARG）/free 行都在，CP2K 的 CV 表不再参与")
    check(meta.w_plumed_table.rowCount() == 2
          and meta.w_plumed_table.item(0, 0).text() == "cn",
          "PLUMED CV 表列出了两行")
    walls_before = meta.w_plumed_wall_table.rowCount()
    meta._plumed_walls = [__import__("cp2kbuild.plumedcv", fromlist=["PlumedWall"])
                          .PlumedWall(arg="cn.mean", kind="UPPER_WALLS", at=6.0,
                                      kappa=5000.0)]
    meta._refresh()
    pump(120)
    check(meta.w_plumed_wall_table.rowCount() == walls_before + 1,
          "PLUMED 墙表多了一行")
    # the CLI must produce the same file from the same settings
    draft_cli_plumed = cli_text(
        ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
         "--ensemble", "NVT", "--thermostat", "CSVR", "--temperature", "330",
         "--steps", "20000", "--seed", "12345", "--quiet", "--no-validate",
         "--meta-plumed", "water.plumedinp", "--meta-plumed-draft", "-",
         "--plumed-cv", "cn:COORDINATIONNUMBER:SPECIES=1-100 R_0=0.33 MEAN:"
                        "component=mean:sigma=0.3",
         "--plumed-cv", "nn:AROUND:DATA=cn ATOM=1 SIGMA=0.1 XLOWER=-2.0 "
                        "XUPPER=2.0 MEAN:component=mean",
         "--plumed-wall", "cn.mean:UPPER_WALLS:6.0:5000",
         "--plumed-line", "opes: OPES_METAD ARG=cn.mean PACE=500 BARRIER=40",
         "--plumed-pace", "500", "--plumed-height", "1.2",
         "--plumed-sigma", "0.1", "--plumed-biasfactor", "10",
         "--plumed-temp", "330"])
    # `--meta-plumed-draft -` prints the draft *and* the input is suppressed by
    # --stdout-less mode, so take the draft part only
    gui_draft_plumed = meta.plumed_draft()
    draft_cli_plumed = draft_cli_plumed[draft_cli_plumed.find("# plumed.dat"):]
    check(draft_cli_plumed == gui_draft_plumed,
          "PLUMED 专用表：GUI 与 CLI 草稿逐字节相同"
          if draft_cli_plumed == gui_draft_plumed else
          f"GUI/CLI 草稿不同：CLI {draft_cli_plumed[:80]!r} "
          f"GUI {gui_draft_plumed[:80]!r}")

    # ---- the newer PLUMED controls: unit switch, RESTART, adaptive width,
    # METAD extras, bias label, "add the selected up", read a file back -------- #
    meta.w_p_units.setCurrentIndex(max(meta.w_p_units.findData("A"), 0))
    meta.w_p_adaptive.setCurrentIndex(max(meta.w_p_adaptive.findData("GEOM"), 0))
    meta.w_p_sigma.setValue(0.05)
    meta.w_p_sigma_min.setText("0.02")
    meta.w_p_sigma_max.setText("0.1")
    meta.w_p_restart.setChecked(True)
    meta.w_p_label.setText("bias")
    meta.w_p_extra.setText("GRID_MIN=0 GRID_MAX=10")
    meta.w_p_args.setText("cn.mean,nn.mean")
    meta._refresh()
    pump(200)
    rich_draft = meta.plumed_draft()
    set_cli = ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
               "--ensemble", "NVT", "--thermostat", "CSVR", "--temperature", "330",
               "--steps", "20000", "--seed", "12345", "--quiet", "--no-validate",
               "--meta-plumed", "water.plumedinp", "--meta-plumed-draft", "-",
               "--plumed-cv", "cn:COORDINATIONNUMBER:SPECIES=1-100 R_0=0.33 MEAN:"
                              "component=mean:sigma=0.3",
               "--plumed-cv", "nn:AROUND:DATA=cn ATOM=1 SIGMA=0.1 XLOWER=-2.0 "
                              "XUPPER=2.0 MEAN:component=mean",
               "--plumed-wall", "cn.mean:UPPER_WALLS:6.0:5000",
               "--plumed-line", "opes: OPES_METAD ARG=cn.mean PACE=500 BARRIER=40",
               "--plumed-pace", "500", "--plumed-height", "1.2",
               "--plumed-sigma", "0.05", "--plumed-biasfactor", "10",
               "--plumed-temp", "330", "--plumed-units", "A",
               "--plumed-adaptive", "GEOM", "--plumed-sigma-min", "0.02",
               "--plumed-sigma-max", "0.1", "--plumed-restart",
               "--plumed-label", "bias", "--plumed-extra", "GRID_MIN=0",
               "--plumed-extra", "GRID_MAX=10", "--plumed-args", "cn.mean,nn.mean"]
    rich_cli = cli_text(set_cli)
    rich_cli = rich_cli[rich_cli.find("# plumed.dat"):]
    check(rich_cli == rich_draft,
          "单位开关/RESTART/ADAPTIVE/其他关键字/标签：GUI 与 CLI 逐字节相同"
          if rich_cli == rich_draft else
          f"GUI/CLI 不同：CLI {rich_cli[-200:]!r} GUI {rich_draft[-200:]!r}")
    rich_first = next(line for line in rich_draft.splitlines()
                      if line.strip() and not line.strip().startswith("#"))
    check(rich_first == "UNITS LENGTH=A TIME=fs ENERGY=kj/mol"
          and "bias: METAD ARG=cn.mean,nn.mean SIGMA=0.05 HEIGHT=1.2 PACE=500 "
              "TEMP=330 BIASFACTOR=10 ADAPTIVE=GEOM "
              "SIGMA_MIN=0.02,0.02 SIGMA_MAX=0.1,0.1 "
              "FILE=HILLS RESTART=YES GRID_MIN=0 GRID_MAX=10" in rich_draft
          and "PRINT ARG=cn.mean,nn.mean,bias.bias" in rich_draft,
          "Å 开关写在第一个动作行前、METAD 行带上 RESTART/ADAPTIVE/GRID_*、"
          "两维 CV 的 SIGMA_MIN/MAX 各铺成两个值、PRINT 引用的是自定义标签")
    check(meta.w_p_sigma.suffix().startswith(" Å"),
          f"长度单位选 Å 后 SIGMA 的标签跟着变成 Å：{meta.w_p_sigma.suffix()!r}")
    meta.w_p_adaptive.setCurrentIndex(max(meta.w_p_adaptive.findData("DIFF"), 0))
    pump(60)
    check("步" in meta.w_p_sigma.suffix(),
          f"ADAPTIVE=DIFF 时提示 σ 是步数：{meta.w_p_sigma.suffix()!r}")
    meta.w_p_adaptive.setCurrentIndex(max(meta.w_p_adaptive.findData("GEOM"), 0))
    # put it back to the plain setup for the checks below
    meta.w_p_units.setCurrentIndex(max(meta.w_p_units.findData("nm"), 0))
    meta.w_p_restart.setChecked(False)
    meta.w_p_extra.setText("")
    meta.w_p_args.setText("")
    meta.w_p_label.setText("m")
    meta.w_p_sigma.setValue(0.1)
    meta.w_p_sigma_min.setText("")
    meta.w_p_sigma_max.setText("")
    meta.w_p_adaptive.setCurrentIndex(0)
    meta._refresh()
    pump(150)

    # 读回 plumed.dat：按钮背后的那条路（这里直接给路径，绕开文件对话框）
    meta.silent = True
    fixture = os.path.join(ROOT, "examples", "plumed_urea_cn.dat")
    if os.path.exists(fixture):
        notes = meta.import_plumed_file(fixture)
        pump(150)
        imported = meta.plumed_setup()
        check(len(imported.cvs) == 21 and imported.units == "A"
              and imported.bias.label == "metad" and imported.args() == ["cv_uu"]
              and meta.w_plumed_table.rowCount() == 21
              and meta.w_p_units.currentData() == "A"
              and not notes,
              f"读回 examples/plumed_urea_cn.dat：21 个 CV / Å / 标签 metad / "
              f"ARG=cv_uu 都进了控件（notes={notes[:1]}）")
        # 把读回来的东西再写出去：草稿和文件里那些动作行一致
        again = meta.plumed_draft()
        want = [line.strip() for line in
                open(fixture, encoding="utf-8").read().splitlines()
                if line.strip() and not line.strip().startswith("#")]
        got = [line.strip() for line in again.splitlines()
               if line.strip() and not line.strip().startswith("#")]
        same = sum(1 for a, b in zip(want, got) if a == b)
        check(same >= len(want) - 1,
              f"读回后重新生成的草稿与文件一致（{same}/{len(want)} 行动作逐字节相同）")
        # 「把选中的相加」：选前两行，加一行 COMBINE
        label = meta._combine_plumed_rows([0, 1], label="cv_extra")
        pump(120)
        check(label == "cv_extra" and len(meta._plumed_cvs) == 22
              and meta._plumed_cvs[2].action == "COMBINE"
              and "ARG=c1,c2 " in meta._plumed_cvs[2].params
              and "COEFFICIENTS=1,1 PERIODIC=NO" in meta._plumed_cvs[2].params,
              "「把选中的相加」生成了 COMBINE（SUM 只吃一个向量，所以不能用 SUM）")
        check(meta._combine_plumed_rows([0], label="nope") == ""
              and len(meta._plumed_cvs) == 22,
              "只选一行时不生成 COMBINE")
    else:
        check(False, f"缺例子文件 {fixture}")
    # 生成器：分块 → 配位数表（对话框背后的同一条路）
    from cp2kbuild.plumedcv import ContactSpec, contact_cvs
    rows, _notes = contact_cvs(ContactSpec(blocks="19x1,8x20"), natoms=179, units="A")
    check(len(rows) == 21 and rows[0].label == "c1" and rows[-1].label == "cv_uu",
          "接触型 CV 生成器：19x1,8x20 生成 20 个配位数 + 1 行 COMBINE")
    check(meta.w_plumed_table.rowCount() == 22,
          f"相加后表里 22 行：{meta.w_plumed_table.rowCount()}")
    meta._plumed_cvs = []
    meta._plumed_walls = []
    meta.w_p_units.setCurrentIndex(0)
    meta._refresh()

    # 真实反馈：一份手写的 plumed.dat 用的是手册 "Continuation lines" 的写法
    # （`cv: COORDINATION ...` + 一行一个关键字 + `...` 收尾）。读回来必须一个
    # 关键字都不少——旧读法把 `...` 当参数、把后面的关键字当成"自由行"，
    # CV 等于被掏空、METAD 掉回默认值（回写就把人家的设置改没了）。
    import shutil
    ml_dir = ROOT / "examples" / "generated" / "_tmp_plumed_multiline"
    ml_dir.mkdir(parents=True, exist_ok=True)
    ml_path = ml_dir / "plumed.dat"
    ml_path.write_text(
        "UNITS LENGTH=A TIME=fs ENERGY=kj/mol\n"
        "\n"
        "HNCO_C: GROUP ATOMS=102,106,110\n"
        "UREA_N: GROUP ATOMS=20,22,28\n"
        "\n"
        "cv_coord: COORDINATION ...\n"
        "    GROUPA=HNCO_C\n"
        "    GROUPB=UREA_N\n"
        "    SWITCH={RATIONAL R_0=2.30 D_0=0.0 NN=12 MM=24}\n"
        "    NLIST\n"
        "    NL_CUTOFF=3.5\n"
        "    NL_STRIDE=10\n"
        "...\n"
        "\n"
        "metad: METAD ...\n"
        "    ARG=cv_coord\n"
        "    SIGMA=0.15\n"
        "    HEIGHT=1.0\n"
        "    PACE=100\n"
        "    TEMP=900\n"
        "    BIASFACTOR=5\n"
        "    FILE=HILLS\n"
        "...\n"
        "\n"
        "PRINT ARG=cv_coord,metad.bias FILE=COLVAR STRIDE=10\n"
        "\n"
        "FLUSH STRIDE=100\n", encoding="utf-8")
    ml_notes = meta.import_plumed_file(str(ml_path))
    pump(200)
    ml_setup = meta.plumed_setup()
    coord_cv = next((cv for cv in ml_setup.cvs if cv.label == "cv_coord"), None)
    check(not ml_notes and coord_cv is not None
          and all(token in coord_cv.params for token in
                  ("GROUPA=HNCO_C", "GROUPB=UREA_N", "NLIST",
                   "NL_CUTOFF=3.5", "NL_STRIDE=10"))
          and ml_setup.bias.args == ("cv_coord",)
          and ml_setup.bias.pace == 100 and ml_setup.bias.height == 1.0
          and ml_setup.bias.temperature == 900.0
          and ml_setup.bias.biasfactor == 5.0,
          f"续行写法读回：CV 关键字不丢、METAD 的 ARG/PACE/HEIGHT/TEMP/"
          f"BIASFACTOR 全进控件（notes={ml_notes[:1]}）")
    check(meta.w_free.toPlainText().strip() == "FLUSH STRIDE=100"
          and meta.w_free_where.currentData() == "end",
          f"只有 `FLUSH` 落在自由行框里：{meta.w_free.toPlainText().strip()!r}")
    ml_draft = meta.plumed_draft()
    ml_body = [line for line in ml_draft.splitlines()
               if line.strip() and not line.strip().startswith("#")]
    check(all(line.strip() != "..." for line in ml_body)
          and "FLUSH STRIDE=100" in ml_draft
          and any("NL_CUTOFF=3.5" in line for line in ml_body),
          "回写的草稿里没有单独的 `...`（PLUMED 会把它当成续行的开头）")

    # 真实反馈：「PLUMED 能分别给多个 CV 定义 σ，那工具是不是还有点小问题」——
    # 确实有：σ 是每个 ARG 一个值（`SIGMA=0.15,0.5`），可表格里看不见，
    # 「默认 SIGMA」那一格在 CV 各自带 σ 时改了不起任何作用（看着像编辑丢了）。
    ml_path.write_text(
        "UNITS LENGTH=A TIME=fs ENERGY=kj/mol\n"
        "d1: DISTANCE ATOMS=1,2\n"
        "d2: DISTANCE ATOMS=1,3\n"
        "m: METAD ARG=d1,d2 SIGMA=0.15,0.5 HEIGHT=1 PACE=100 TEMP=900 FILE=HILLS\n"
        "PRINT ARG=d1,d2,m.bias FILE=COLVAR STRIDE=10\n", encoding="utf-8")
    meta.import_plumed_file(str(ml_path))
    pump(250)
    widths = [meta.w_plumed_table.item(row, 4).text()
              for row in range(meta.w_plumed_table.rowCount())]
    check(meta.w_plumed_table.columnCount() == 5 and widths == ["0.15", "0.5"],
          f"每个 CV 的 σ 在表里看得见（第 5 列 {widths}）")
    check("用不上" in meta.sigma_note.text()
          and not meta.sigma_note.isHidden(),
          f"CV 各自带 σ 时点明「默认 SIGMA」那一格不起作用："
          f"{meta.sigma_note.text()[:70]}")
    meta.w_p_sigma.setValue(0.9)
    pump(200)
    check("SIGMA=0.15,0.5" in meta.plumed_draft(),
          "改「默认 SIGMA」不会动到 CV 自己的 σ（文件仍是 0.15,0.5）")
    meta.w_p_sigma.setValue(0.1)
    pump(150)
    # ... and the other shape: one width for every CV is *the* default
    ml_path.write_text(
        "UNITS LENGTH=A TIME=fs ENERGY=kj/mol\n"
        "d1: DISTANCE ATOMS=1,2\n"
        "d2: DISTANCE ATOMS=1,3\n"
        "m: METAD ARG=d1,d2 SIGMA=0.15,0.15 HEIGHT=1 PACE=100 TEMP=900 FILE=HILLS\n"
        "PRINT ARG=d1,d2,m.bias FILE=COLVAR STRIDE=10\n", encoding="utf-8")
    meta.import_plumed_file(str(ml_path))
    pump(250)
    widths = [meta.w_plumed_table.item(row, 4).text()
              for row in range(meta.w_plumed_table.rowCount())]
    check(meta.w_p_sigma.value() == 0.15 and widths == ["默认", "默认"]
          and meta.sigma_note.isHidden(),
          f"`SIGMA=0.15,0.15` 就是「宽度 0.15」：面板显示 0.15、表里写「默认」"
          f"（现在 {meta.w_p_sigma.value()} / {widths}）")
    meta.w_p_sigma.setValue(0.9)
    pump(200)
    check("SIGMA=0.9,0.9" in meta.plumed_draft(),
          "这种形状下改「默认 SIGMA」真的会改到文件（不再是空操作）")
    meta.w_p_sigma.setValue(0.1)
    pump(150)
    shutil.rmtree(ml_dir, ignore_errors=True)
    meta._plumed_cvs = []
    meta._plumed_walls = []
    meta.w_free.setPlainText("")
    meta._refresh()

    # put the two CVs back - the checks below look at the table again
    meta._cvs = [Cv(kind="distance", atoms=(1, 2)),
                 Cv(kind="coordination", kinds_from="O", kinds_to="H", r0=1.2,
                    nn=6, nd=8)]
    meta._metavars = [Metavar(colvar=1), Metavar(colvar=2)]
    meta._refresh()

    # a non-MD task: the block lives on the MD page, so it goes away with it
    meta.w_route.setCurrentIndex(0)
    meta._refresh()
    set_task("geo_opt")
    generate.refresh()
    pump(150)
    check(not meta.isVisibleTo(generate),
          "非 MD 任务时整块随 MD 页一起不出现（比置灰更干净）")
    check("&METADYN" not in gui_text() and "&COLVAR" not in gui_text(),
          "非 MD 任务的输入里没有那两段")
    set_task("md")
    generate.refresh()
    pump(150)
    check(meta.isVisibleTo(generate) and meta.w_cv_table.rowCount() == 2,
          "回到 MD 任务后块和里面的内容都还在")

    # the CV dialog's own type combo is one of its rows: hiding it with the rows
    # that do not belong to the current kind made "加 CV" open with no way to pick
    # a type at all
    from cp2kbuild.gui import CvDialog, MetavarDialog

    cv_dialog = CvDialog(None)
    check(cv_dialog._rows["kind"][1].isVisibleTo(cv_dialog)
          and cv_dialog.w_kind.count() == len(CV_KINDS),
          f"「加 CV」对话框一打开就能选类型（{cv_dialog.w_kind.count()} 种）")
    # ... and it shows exactly *one* "?" (every row shares the same help topic, so
    # attaching the badge per row used to print the same "?" several times)
    from cp2kbuild.gui import HelpBadge

    cv_badges = [b.help_entry.text.splitlines()[0][:12]
                 for b in cv_dialog.findChildren(HelpBadge)]
    check(len(cv_badges) == 1,
          f"「加 CV」对话框只有一个「?」（实际 {len(cv_badges)} 个：{cv_badges}）")
    kind_rows = {}
    for kind in CV_KINDS:
        cv_dialog.w_kind.setCurrentIndex(cv_dialog.w_kind.findData(kind))
        kind_rows[kind] = {name for name, (holder, _w) in cv_dialog._rows.items()
                           if holder.isVisibleTo(cv_dialog)}
    check(all("kind" in rows for rows in kind_rows.values()),
          f"每一种类型都能看到类型框（{ {k: sorted(v)[:2] for k, v in kind_rows.items()} }）")
    check(kind_rows["distance"] == {"kind", "atoms"}
          and kind_rows["coordination"] >= {"kind", "atoms", "kinds_from",
                                            "kinds_to", "r0", "nn", "nd"}
          and kind_rows["combine"] >= {"kind", "function", "children"},
          f"切类型时显示的行跟着变：{ {k: sorted(v) for k, v in kind_rows.items()} }")
    cv_dialog.deleteLater()
    wall_dialog = MetavarDialog(None, 2)
    wall_badges = [b.help_entry.text.splitlines()[0][:12]
                   for b in wall_dialog.findChildren(HelpBadge)]
    check(len(wall_badges) == 2,
          f"&METAVAR 对话框是 2 个「?」（偏置 1 个 + 墙 1 个），"
          f"实际 {len(wall_badges)} 个：{wall_badges}")
    wall_rows = {}
    for wall_type in ("NONE", "REFLECTIVE", "QUADRATIC", "GAUSSIAN"):
        wall_dialog.w_wall_type.setCurrentIndex(
            wall_dialog.w_wall_type.findData(wall_type))
        wall_rows[wall_type] = {name for name, (holder, _w)
                               in wall_dialog._rows.items()
                               if holder.isVisibleTo(wall_dialog)}
    check(all({"colvar", "scale", "wall_type"} <= rows
              for rows in wall_rows.values())
          and wall_rows["NONE"] < wall_rows["QUADRATIC"],
          f"&METAVAR 对话框：前几行一直在、墙的细节行按类型显示"
          f"（{ {k: sorted(v) for k, v in wall_rows.items()} }）")
    wall_dialog.deleteLater()

    # the other long-but-rare blocks fold the same way and remember it
    for label, box in (("MD 温度/能量打印", generate.md_print_box),
                       ("3.2 SCF 路线细节", generate.scf_details_box),
                       ("温度程序（多段升温/降温）",
                        generate.temperature_program_box),
                       ("Molden", generate.molden_box),
                       ("立方体", generate.cube_box),
                       ("6. 续算", generate.restart_box)):
        reset_gui()
        check(isinstance(box, CollapsibleBox) and not box.is_expanded()
              and not box.body.isVisibleTo(box),
              f"{label} 是可折叠块且默认收起（{type(box).__name__}）")
        box.set_expanded(True)
        check(box.body.isVisibleTo(box), f"{label} 展开后内容可见")
        box.set_expanded(False)
    reset_gui()

    reset_gui()
    # 5. band
    generate.load_from_path(SILICON)
    set_task("band")
    generate.w_lattice.setCurrentIndex(
        [i for i in range(generate.w_lattice.count())
         if generate.w_lattice.itemData(i) == "fcc"][0])
    generate.w_kpath.setText("GAMMA-X-W-K-GAMMA-L-U-W-L-K|U-X")
    generate.w_npoints.setValue(60)
    compare("band fcc path",
            ["new", "--task", "band", "--structure", SILICON, "--lattice", "fcc",
             "--kpath", "GAMMA-X-W-K-GAMMA-L-U-W-L-K|U-X", "--npoints", "60",
             "--stdout"])

    reset_gui()
    # 6. freq with IR intensities
    generate.load_from_path(WATER)
    set_task("freq")
    generate.w_intensities.setChecked(True)
    compare("freq + intensities",
            ["new", "--task", "freq", "--structure", WATER, "--vacuum", "12",
             "--intensities", "--stdout"])

    reset_gui()
    # 7. pdos
    generate.load_from_path(SILICON)
    set_task("pdos")
    generate.w_pdos_nlumo.setValue(15)
    generate.w_pdos_ldos.setText("1 2 3")
    compare("pdos nlumo/ldos",
            ["new", "--task", "pdos", "--structure", SILICON,
             "--pdos-nlumo", "15", "--pdos-ldos", "1 2 3", "--stdout"])

    # 7b. DOS broadening, a 2026.2 feature
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("pdos")
    generate.w_pdos_broaden.setValue(0.05)
    generate.w_pdos_broaden_type.setCurrentText("LORENTZIAN")
    compare("pdos + broadening",
            ["new", "--task", "pdos", "--structure", SILICON,
             "--pdos-broaden", "0.05", "--pdos-broaden-type", "LORENTZIAN",
             "--stdout"])
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("pdos")
    generate.refresh()
    check("&BROADEN" not in generate.text,
          "不填展宽时不生成 &CURVE/&BROADEN")

    reset_gui()
    # 8. basis / cutoff / scf overrides
    generate.load_from_path(WATER)
    set_task("energy")
    generate.w_cutoff_auto.setChecked(False)
    generate.w_cutoff.setValue(800)
    generate.w_basis.setText("TZV2P-MOLOPT-GTH")
    generate.w_scf.setCurrentIndex(
        [i for i in range(generate.w_scf.count())
         if generate.w_scf.itemData(i) == "DIAGONALIZATION"][0])
    generate.w_functional.setCurrentText("PBE0")
    compare("manual cutoff/basis/scf/functional",
            ["new", "--task", "energy", "--structure", WATER, "--vacuum", "12",
             "--cutoff", "800", "--basis", "TZV2P-MOLOPT-GTH",
             "--scf", "DIAGONALIZATION", "--functional", "PBE0", "--stdout"])

    generate.w_cutoff_auto.setChecked(True)
    generate.w_basis.clear()
    generate.w_scf.setCurrentIndex(0)
    generate.w_functional.setCurrentText("PBE")
    generate.w_dispersion.setCurrentIndex(0)

    # 9. the SCF / grid / mixing knobs that used to be CLI-only
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.w_scf.setCurrentIndex(
        [i for i in range(generate.w_scf.count())
         if generate.w_scf.itemData(i) == "DIAGONALIZATION"][0])
    generate.w_eps_scf.setText("1e-7")
    generate.w_max_scf.setValue(120)
    generate.w_qs_method.setCurrentText("GAPW")
    generate.w_ngrids.setValue(5)
    generate.w_added_mos.setValue(24)
    generate.w_mixing_alpha.setValue(0.15)
    generate.w_mixing_nbuffer.setValue(12)
    generate.w_mixing_beta.setText("1.2")
    compare("SCF/grid/mixing knobs",
            ["new", "--task", "energy", "--structure", SILICON, "--eps-scf", "1e-7",
             "--max-scf", "120", "--qs-method", "GAPW", "--ngrids", "5",
             "--added-mos", "24", "--scf", "DIAGONALIZATION",
             "--mixing-alpha", "0.15", "--mixing-nbuffer", "12",
             "--mixing-beta", "1.2", "--stdout"])

    reset_gui()
    # 9b. the inner / outer SCF iteration counts (SCF_NCYCLES / SCF_OCYCLES)
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.w_max_scf.setValue(1)
    generate.w_outer_max_scf.setValue(0)
    generate.w_ignore_convergence.setChecked(True)
    compare("固定步数：内层 1 / 外层 0",
            ["new", "--task", "energy", "--structure", SILICON, "--max-scf", "1",
             "--outer-max-scf", "0", "--ignore-convergence-failure", "--stdout"])
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.w_max_scf.setValue(50)
    generate.w_outer_max_scf.setValue(10)
    generate.w_outer_eps_scf.setText("1e-7")
    generate.w_scf.setCurrentIndex(
        [i for i in range(generate.w_scf.count())
         if generate.w_scf.itemData(i) == "DIAGONALIZATION"][0])
    compare("对角化 + 外层循环 10/1e-7",
            ["new", "--task", "energy", "--structure", SILICON, "--max-scf", "50",
             "--scf", "DIAGONALIZATION", "--outer-max-scf", "10",
             "--outer-eps-scf", "1e-7", "--stdout"])
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.refresh()
    check("OUTER_SCF" in generate.text and "MAX_SCF 20" in generate.text,
          "默认（自动）下 OT 路线的外层循环仍是 20")

    reset_gui()
    # 10. SCF that is allowed not to converge + fractional coordinates
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.w_max_scf.setValue(1)
    generate.w_ignore_convergence.setChecked(True)
    generate.w_scaled_coords.setChecked(True)
    check(generate.w_scaled_coords.isEnabled(),
          "有晶胞时「分数坐标」可选")
    compare("IGNORE_CONVERGENCE_FAILURE + SCALED",
            ["new", "--task", "energy", "--structure", SILICON, "--max-scf", "1",
             "--ignore-convergence-failure", "--scaled-coords", "--stdout"])

    reset_gui()
    # 11. smearing owns the electronic temperature and mutes the mixing knobs
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.w_smearing.setChecked(True)
    generate.w_electronic_temperature.setValue(800.0)
    check(generate.w_electronic_temperature.isEnabled(),
          "开了 SMEAR 后电子温度可编辑")
    check(not generate.w_mixing_alpha.isEnabled(),
          "开了 SMEAR 后混合参数变灰（&MIXING 不会被写进去）")
    compare("SMEAR + electronic temperature",
            ["new", "--task", "energy", "--structure", SILICON, "--smearing",
             "--electronic-temperature", "800", "--scf", "DIAGONALIZATION",
             "--stdout"])
    generate.w_smearing.setChecked(False)
    check(not generate.w_mixing_alpha.isEnabled(),
          "关掉 SMEAR 后「自动」路线回到 OT，混合参数仍灰（&MIXING 不会被写）")
    set_scf("DIAGONALIZATION")
    check(generate.w_mixing_alpha.isEnabled(),
          "显式选对角化后混合参数恢复可编辑")
    set_scf("auto")

    reset_gui()
    # 12. MD coupling constants and the trajectory stride
    generate.load_from_path(SILICON)
    set_task("md")
    generate.w_ensemble.setCurrentText("NPT_I")
    generate.w_thermostat.setCurrentText("NOSE")
    generate.w_steps.setValue(200)
    generate.w_thermostat_timecon.setValue(250.0)
    generate.w_barostat_timecon.setValue(1000.0)
    generate.w_trajectory_stride.setValue(25)
    compare("MD coupling + trajectory stride",
            ["new", "--task", "md", "--structure", SILICON, "--ensemble", "NPT_I",
             "--thermostat", "NOSE", "--steps", "200", "--thermostat-timecon", "250",
             "--barostat-timecon", "1000", "--trajectory-stride", "25", "--stdout"])

    reset_gui()
    generate.load_from_path(WATER)
    set_task("energy")
    generate._apply_couplings()
    check(generate.w_scaled_coords.isEnabled(),
          "分子 + 真空层会生成 &CELL，所以「分数坐标」可选")
    generate.w_vacuum.setValue(0.0)
    generate.refresh()
    check(not generate.w_scaled_coords.isEnabled(),
          "没有晶胞（真空层 0）时「分数坐标」不可选")

    # ------------------------------------------------------------------ #
    # 温度打印：三种机制各自的控件、联动和生成的输入
    # ------------------------------------------------------------------ #
    print("\n== MD 温度打印（每个 kind / -1.ener / 热浴区域）==")

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("md")
    check(generate.w_thermostat_region.currentData() == "GLOBAL"
          and not generate.w_md_temp_kind.isChecked()
          and not generate.w_md_energy.isChecked()
          and not generate.w_thermostat_temp.isChecked(),
          "三个打印开关默认都是关的（不写多余的段）")
    check("不写 `&ENERGY`" in generate.md_print_hint.text()
          and "仍然会写" in generate.md_print_hint.text(),
          f"默认就说明了 -1.ener 是 CP2K 自己会写的："
          f"{generate.md_print_hint.text()[:50]!r}")
    compare("默认 MD：不写任何温度打印段",
            ["new", "--task", "md", "--structure", SILICON, "--steps", "5000",
             "--stdout"])

    # 1) per-kind temperature: the keyword and the print key go together
    generate.w_md_temp_kind.setChecked(True)
    generate.w_md_temp_kind_every.setValue(25)
    pump(60)
    text = gui_text()
    check("TEMP_KIND T" in text and "&TEMP_KIND ON" in text
          and "MD 25" in text and "cp2k-1.temp" in text,
          "勾「每个原子种类的温度」会同时写 &MD TEMP_KIND 和打印键（每 25 步）")
    check("`<项目名>-1.temp`" in generate.md_print_hint.text(),
          f"说明里写清楚了输出文件：{generate.md_print_hint.text()[:60]!r}")
    generate.w_md_temp_kind.setChecked(False)
    pump(60)

    # 2) the .ener file, with interval and name
    generate.w_md_energy.setChecked(True)
    generate.w_md_energy_every.setValue(50)
    generate.w_md_energy_file.setText("run")
    pump(60)
    text = gui_text()
    check("&ENERGY ON" in text and "FILENAME run" in text and "MD 50" in text,
          "勾「能量文件」写出 &ENERGY ON + FILENAME run + 每 50 步")
    generate.w_md_energy.setChecked(False)
    generate.w_md_energy_file.clear()
    pump(60)

    # 3) thermostat regions: MOLECULE + the per-region temperature
    generate.w_thermostat_region.setCurrentIndex(1)       # MOLECULE
    generate.w_thermostat_temp.setChecked(True)
    generate.w_thermostat_temp_every.setValue(20)
    pump(60)
    text = gui_text()
    check("REGION MOLECULE" in text and "&TEMPERATURE ON" in text
          and "MD 20" in text and "cp2k-1.PARTICLES.temp" in text,
          "「热浴区域=每个分子」+「热浴温度」写出 REGION MOLECULE 和区域温度打印")
    check("每个区域一列" in generate.md_print_hint.text(),
          f"说明跟着区域走：{generate.md_print_hint.text()[-70:]!r}")

    # switching the ensemble to NVE greys the thermostat rows out, because
    # md_section writes no &THERMOSTAT at all then
    generate.w_ensemble.setCurrentText("NVE")
    pump(60)
    check(not generate.w_thermostat_region.isEnabled()
          and not generate.w_thermostat_temp.isEnabled(),
          "NVE 没有恒温器：区域和区域温度打印都变灰")
    generate.w_ensemble.setCurrentText("NVT")
    generate.w_thermostat.setCurrentText("NONE")
    pump(60)
    check(not generate.w_thermostat_temp.isEnabled(),
          "恒温器选 NONE 时热浴温度打印也变灰")
    generate.w_thermostat.setCurrentText("CSVR")
    pump(60)

    # 换任务时这些控件不适用：单点没有温度可言
    set_task("energy")
    pump(60)
    check(not generate.w_md_temp_kind.isEnabled()
          and not generate.w_md_energy.isEnabled(),
          "非 MD 任务时温度打印的两个开关变灰")
    set_task("md")
    pump(60)

    # everything at once still validates clean, and 「恢复默认」 clears it again
    generate.w_md_temp_kind.setChecked(True)
    generate.w_md_energy.setChecked(True)
    generate.w_thermostat_temp.setChecked(True)
    generate.refresh()
    check("0 错误" in generate.summary.text(),
          f"三个打印段一起上也是干净的：{generate.summary.text()[:60]!r}")
    generate.reset_defaults()
    pump(60)
    spec = generate.collect_spec()
    check(not spec.md_temp_kind_print and not spec.md_energy_print
          and not spec.thermostat_temp_print and spec.thermostat_region == "GLOBAL"
          and spec.md_energy_file == "",
          "「恢复默认」把温度打印全清掉，和命令行默认一致")

    # ------------------------------------------------------------------ #
    # 氘代（H→D）：只改核质量 → 元素/基组不变，但能换来 1 fs 的 AIMD 步长
    # ------------------------------------------------------------------ #
    print("\n== 氘代（H→D）==")

    reset_gui()
    generate.load_from_path(WATER)               # 有水就有氢
    set_task("md")
    check(not generate.w_h_to_d.isChecked(), "氘代默认是关的")
    generate.w_h_to_d.setChecked(True)
    generate.w_timestep.setValue(1.0)
    pump(200)
    text = gui_text()
    check("&KIND H" in text and "MASS 2.0141017781" in text,
          "勾上以后 &KIND H 里写出氘的质量")
    check("ELEMENT H" in text and "BASIS_SET" in text,
          "元素仍是 H：基组和赝势跟着氢走，不换元素")
    check("1.0 fs" in generate.h_to_d_hint.text(),
          f"说明里点出步长可以到 1.0 fs：{generate.h_to_d_hint.text()[:48]!r}")
    generate.w_timestep.setValue(0.5)
    pump(200)
    codes = " ".join(generate.diagnostics.item(row, 1).text()
                     for row in range(generate.diagnostics.rowCount())
                     if generate.diagnostics.item(row, 1) is not None)
    check("deuterated-timestep" in codes,
          f"还写 0.5 fs 时提示白白少了一半机时：{codes[:60]!r}")

    generate.load_from_path(SILICON)             # 没有氢
    pump(200)
    check(not generate.w_h_to_d.isEnabled()
          and "没有氢" in generate.h_to_d_hint.text(),
          "体系里没有氢：控件变灰并说明原因")

    generate.load_from_path(WATER)
    set_task("energy")
    pump(200)
    check(not generate.w_h_to_d.isEnabled(),
          "单点能的能量与质量无关：控件变灰")
    set_task("md")
    pump(200)
    check(generate.w_h_to_d.isEnabled(), "回到 MD 又能用")
    generate.w_method.setCurrentIndex(1)         # METHOD FIST
    pump(200)
    check(not generate.kind_box.isVisible(),
          "经典力场的质量来自拓扑文件：整块基组/氘代区域都藏起来")
    generate.w_method.setCurrentIndex(0)
    pump(200)
    generate.w_h_to_d.setChecked(True)
    generate.reset_defaults()
    pump(200)
    check(not generate.collect_spec().h_to_d, "「恢复默认」把氘代关掉")

    reset_gui()
    # 13. restart files that are written for the *next* run
    generate.load_from_path(SILICON)
    set_task("energy")
    check(generate.project_name() == "cp2k",
          f"项目名默认是 cp2k（重启文件才继承得上）：{generate.project_name()!r}")
    check(generate.w_scf_guess.currentText() == "RESTART",
          "初始波函数默认就是 RESTART（CP2K 自己会回退到 ATOMIC）")
    check(generate.w_wfn_print.isChecked() and generate.w_motion_restart.isChecked(),
          "两个重启文件的打印默认都是开的")
    compare("默认：RESTART 初猜 + 写 .wfn",
            ["new", "--task", "energy", "--structure", SILICON, "--stdout"])

    generate.w_scf_guess.setCurrentText("ATOMIC")
    generate.w_wfn_print.setChecked(False)
    compare("ATOMIC 初猜 + 不写 .wfn",
            ["new", "--task", "energy", "--structure", SILICON,
             "--scf-guess", "ATOMIC", "--no-wfn-print", "--stdout"])

    generate.w_wfn_print.setChecked(True)
    generate.w_wfn_every.setValue(0)          # 0 = not during the SCF cycle
    generate.w_wfn_add_last.setChecked(True)  # + ADD_LAST NUMERIC = one dump at the end
    generate.w_wfn_backups.setValue(3)
    # 两个复选框的名字（真实反馈：把「运行状态」改成「重启文件」、「波函数」改成
    # 「波函数文件」）——它们都是**写文件**，名字里点出来
    check(generate.w_wfn_print.text() == "波函数文件"
          and generate.w_motion_restart.text() == "重启文件",
          f"第 5 节的开关叫「波函数文件」/「重启文件」："
          f"{generate.w_wfn_print.text()!r} / {generate.w_motion_restart.text()!r}")
    compare("波函数文件只在最后写一次 + 备份 3 份",
            ["new", "--task", "energy", "--structure", SILICON,
             "--scf-guess", "ATOMIC", "--wfn-restart-every", "0",
             "--wfn-backup-copies", "3", "--stdout"])

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("md")
    generate.w_steps.setValue(200)
    generate.w_restart_every.setValue(50)
    generate.w_restart_backups.setValue(2)
    compare("运行重启文件每 50 步 + 备份 2 份",
            ["new", "--task", "md", "--structure", SILICON, "--steps", "200",
             "--restart-every", "50", "--restart-backups", "2", "--stdout"])

    generate.w_motion_restart.setChecked(False)
    compare("关掉运行重启文件",
            ["new", "--task", "md", "--structure", SILICON, "--steps", "200",
             "--restart-every", "50", "--restart-backups", "2",
             "--no-motion-restart", "--stdout"])
    # ... and the switch has to *do* something: a GUI==CLI comparison would pass
    # even if both ignored it, so count the surviving &RESTART blocks
    generate.refresh()
    check(generate.text.count("&RESTART") == 1,
          "关掉运行重启文件后只剩 SCF 那个 &RESTART（.wfn 的）")
    generate.w_motion_restart.setChecked(True)

    # &EACH and ADD_LAST are two independent settings, with two controls - and the
    # &EACH *level* follows the task: MD for an MD run, GEO_OPT/CELL_OPT for the
    # optimisers, QS_SCF only for a single point (QS_SCF counts the SCF iterations
    # inside one force evaluation, so it is the wrong level for MD)
    generate.w_wfn_every.setValue(0)
    generate.w_wfn_add_last.setChecked(True)
    keep_task = generate.task_combo.currentIndex()
    levels_ok = True
    for task, want in (("energy", "QS_SCF"), ("md", "MD"), ("geo_opt", "GEO_OPT"),
                       ("cell_opt", "CELL_OPT")):
        generate.task_combo.setCurrentIndex(generate.task_combo.findData(task))
        generate.refresh()
        pump(200)
        levels_ok = levels_ok and f"{want} 0" in generate.text
    generate.task_combo.setCurrentIndex(keep_task)
    generate.refresh()
    pump(200)
    check(levels_ok and "ADD_LAST NUMERIC" in generate.text,
          "波函数：每 0 步 + 最后一步 → 任务对应的 &EACH 层级 + ADD_LAST NUMERIC")
    generate.w_wfn_add_last.setChecked(False)
    generate.w_restart_every.setValue(5)
    generate.w_restart_add_last.setChecked(False)
    compare("关掉「最后一步也写」",
            ["new", "--task", "md", "--structure", SILICON, "--steps", "200",
             "--wfn-restart-every", "0", "--no-wfn-add-last",
             "--restart-every", "5", "--restart-backups", "2",
             "--no-restart-add-last", "--stdout"])
    generate.refresh()
    off = [line for line in generate.text.splitlines()
           if line.strip() == "ADD_LAST NO"]
    check(len(off) == 2, f"两个文件都写出了 ADD_LAST NO（实际 {len(off)} 处）")

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("md")
    generate.w_ext_restart.setText("prev-1.restart")
    generate.w_restart_default.setChecked(False)
    compare("EXT_RESTART 只取显式打开的项",
            ["new", "--task", "md", "--structure", SILICON,
             "--restart", "prev-1.restart", "--no-restart-default", "--stdout"])

    # ---- inheritance: what the previous run left in the output directory ----
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("md")
    import tempfile

    inherit_dir = Path(tempfile.mkdtemp(prefix="gui_inherit_"))
    inherit_dir.mkdir(parents=True, exist_ok=True)
    (inherit_dir / "cp2k-RESTART.wfn").write_text("fake", encoding="utf-8")
    (inherit_dir / "cp2k-1.restart").write_text("fake", encoding="utf-8")
    generate.w_out.setText(str(inherit_dir / "cp2k.inp"))
    generate.refresh()
    check(generate.w_use_found.isEnabled(),
          "目录里有 cp2k-1.restart 时「用它续算」可点")
    hint = generate.restart_files_hint.text()
    check("cp2k-RESTART.wfn" in hint and "cp2k-1.restart" in hint,
          f"提示里列出了将会继承的重启文件：{hint!r}")
    generate.use_found_restart()
    check(generate.w_ext_restart.text().endswith("cp2k-1.restart"),
          "一键把 &EXT_RESTART 指到了那个续算文件")
    compare("「用它续算」后的输入",
            ["new", "--task", "md", "--structure", SILICON,
             "--restart", str(inherit_dir / "cp2k-1.restart"), "--stdout"])

    # ------------------------------------------------------------------ #
    # 任务接力: GEO_OPT writes positions, the MD that follows must take only
    # those - RESTART_DEFAULT F plus the two or three RESTART_* that matter
    # ------------------------------------------------------------------ #
    print("\n== 任务接力（&EXT_RESTART 取哪些量）==")

    def set_take(*names: str) -> None:
        want = list(names) or None
        for i in range(generate.w_restart_take.count()):
            if generate.w_restart_take.itemData(i) == want:
                generate.w_restart_take.setCurrentIndex(i)
                pump(60)
                return
        raise AssertionError(f"no take preset {names}")

    reset_gui()
    # the presets are listed useful-first, but "开机默认" is still 「全部」: it has
    # to agree with the 「都以该文件为准」 box, which is checked out of the box
    check(generate.w_restart_take.currentData() is None
          and generate.w_restart_default.isChecked(),
          "「取哪些量」默认还是「全部」，和「都以该文件为准」对得上")
    check("RESTART_DEFAULT T" in generate.restart_take_hint.text(),
          f"这个选项的说明开机就写着：{generate.restart_take_hint.text()[:40]!r}")
    generate.load_from_path(SILICON)
    set_task("md")
    generate.w_ext_restart.setText("1_geo-1.restart")

    set_take("pos")
    text = gui_text()
    check("RESTART_DEFAULT F" in text and "RESTART_POS T" in text,
          "「只取坐标」写成 RESTART_DEFAULT F + RESTART_POS T")
    check("RESTART_VEL" not in text and "RESTART_COUNTERS" not in text,
          "没取速度、也没把步数计数器搬过来")
    check(not generate.w_restart_default.isEnabled()
          and not generate.w_restart_default.isChecked(),
          "选了取哪些量后「都以该文件为准」自动关掉并变灰")
    # "MD" is not specific enough: the hint has to name the ensemble, because
    # whether the velocities/thermostat are taken is what the ensemble feels
    generate.w_ensemble.setCurrentText("NPT_I")
    pump(60)
    set_take("pos")
    hint = generate.restart_take_hint.text()
    check("NPT_I" in hint and "300" in hint and "压浴" in hint,
          f"取哪些量的说明点名了本次系综和后果：{hint!r}")
    generate.w_ensemble.setCurrentText("NVT")
    pump(60)
    compare("接力：GEO_OPT → MD（只取坐标）",
            ["new", "--task", "md", "--structure", SILICON,
             "--restart", "1_geo-1.restart", "--restart-take", "pos", "--stdout"])

    # a MD → MD relay does not need pos/cell again (the restart file is loaded as
    # the structure, so &COORD/&CELL are already in the new input); what an inline
    # input cannot carry is the velocity
    set_take("vel")
    compare("接力：MD → MD（只取速度，坐标已继承）",
            ["new", "--task", "md", "--structure", SILICON,
             "--restart", "1_geo-1.restart", "--restart-take", "vel",
             "--stdout"])

    set_take("pos", "cell")
    compare("接力：CELL_OPT → MD（坐标 + 晶胞）",
            ["new", "--task", "md", "--structure", SILICON,
             "--restart", "1_geo-1.restart", "--restart-take", "pos,cell",
             "--stdout"])

    # the tool reads the file it was pointed at, so "what can I take from it"
    # is answered instead of guessed
    prev = Path(tempfile.mkdtemp(prefix="gui_relay_")) / "1_geo-1.restart"
    prev.write_text(
        "&GLOBAL\n  RUN_TYPE GEO_OPT\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD FIST\n  &SUBSYS\n"
        "    &CELL\n      ABC 24.4 24.4 24.4\n    &END CELL\n"
        "    &COORD\n      C 0 0 0\n      H 1.0 0 0\n    &END COORD\n"
        "  &END SUBSYS\n&END FORCE_EVAL\n"
        "&MOTION\n  &GEO_OPT\n    MAX_ITER 200\n  &END GEO_OPT\n&END MOTION\n",
        encoding="utf-8")
    generate.w_ext_restart.setText(str(prev))
    set_take("pos")
    hint = generate.restart_files_hint.text()
    check("GEO_OPT" in hint and "坐标 2 个原子" in hint,
          f"提示里念出了这个 restart 文件的内容：{hint!r}")
    set_take("vel")
    hint = generate.restart_files_hint.text()
    check("没有" in hint and "VEL" in hint,
          f"取文件中没有的量时给出警告：{hint!r}")

    # ---- a relay can start from the restart file alone ------------------- #
    # CP2K's restart file is a complete input, so it carries coordinates and
    # cell: "I gave it a restart file, why is there no preview?" is answered by
    # reading the system out of it instead of demanding another structure file
    reset_gui()
    set_task("md")
    generate.structure_edit.clear()
    generate.load_structure()
    generate.w_ext_restart.setText(str(prev))
    pump(60)
    check(generate.w_use_coords.isEnabled(),
          "restart 文件里有坐标时「读它的坐标」可点")
    generate.refresh()
    check("仍然要有原子" in generate.preview.toPlainText(),
          f"没选结构文件时说清楚为什么还没有预览："
          f"{generate.preview.toPlainText().splitlines()[:1]}")
    generate.w_use_coords.click()
    pump(120)
    text = gui_text()
    check("&COORD" in text and "24.4000" in text,
          "一键把 restart 文件里的坐标和晶胞读成了体系")
    check("读的是它里面的 &COORD/&CELL" in generate.structure_info.text(),
          f"结构面板标明了体系来自那个 restart 文件："
          f"{generate.structure_info.text()!r}")
    set_take("pos")
    compare("从 restart 文件读出的体系也能和命令行对上",
            ["new", "--task", "md", "--structure", str(prev),
             "--restart", str(prev), "--restart-take", "pos", "--stdout"])

    # a file without &COORD cannot supply the system - the button stays off
    bare = Path(tempfile.mkdtemp(prefix="gui_relay_bare_")) / "2_md-1.restart"
    bare.write_text("&GLOBAL\n  RUN_TYPE MD\n&END GLOBAL\n", encoding="utf-8")
    generate.w_ext_restart.setText(str(bare))
    pump(60)
    check(not generate.w_use_coords.isEnabled(),
          "文件里没有 &COORD 时「读它的坐标」不亮")

    # put the structure back for the cases that follow
    generate.load_from_path(SILICON)
    pump(60)

    # ------------------------------------------------------------------ #
    # 读参数: a CP2K input (or -1.restart) also carries the *settings*, so the
    # form can be brought in line with the file instead of guessing
    # ------------------------------------------------------------------ #
    print("\n== 读参数（把文件里的参数读回表单）==")

    reset_gui()
    set_task("md")
    imported = Path(tempfile.mkdtemp(prefix="gui_import_")) / "prev.inp"
    imported.write_text(
        cli_text(["new", "--task", "md", "--structure", SILICON,
                  "--ensemble", "NPT_I", "--temperature", "450", "--steps",
                  "12345", "--pressure", "2.5", "--project", "imported",
                  "--cutoff", "700", "--rel-cutoff", "80", "--stdout"]),
        encoding="utf-8")
    check(not generate.w_import.isEnabled(), "还没载入输入文件时「读参数」不亮")
    generate.load_from_path(str(imported))
    pump(120)
    check(generate.w_import.isEnabled(), "载入 .inp 后「读参数」可以点")
    generate.w_import.click()
    pump(120)
    check(generate.w_ensemble.currentText() == "NPT_I"
          and generate.w_steps.value() == 12345
          and abs(generate.w_temperature.value() - 450.0) < 1e-9,
          f"MD 的系综/步数/温度读进来了：{generate.w_ensemble.currentText()} / "
          f"{generate.w_steps.value()} / {generate.w_temperature.value()}")
    check(generate.w_project.text() == "imported"
          and abs(generate.w_cutoff.value() - 700.0) < 1e-9
          and not generate.w_cutoff_auto.isChecked(),
          "项目名和 CUTOFF 也读进来了（并且切到手动 CUTOFF）")
    info = generate.import_info.text()
    check("已读进表单" in info and 'href="ensemble"' in info,
          "列出来读了哪些参数，每一项都带跳转链接")
    generate._jump_to_field("ensemble")
    check(generate.w_ensemble.styleSheet() != "",
          "点参数名会滚到并高亮对应控件")
    compare("读参数之后生成的输入 == 同一套参数的命令行",
            ["new", "--task", "md", "--structure", str(imported),
             "--ensemble", "NPT_I", "--temperature", "450", "--steps", "12345",
             "--pressure", "2.5", "--project", "imported", "--cutoff", "700",
             "--rel-cutoff", "80", "--stdout"])

    # a file that is *not* a CP2K input has nothing to read
    generate.load_from_path(WATER)
    pump(120)
    check(not generate.w_import.isEnabled(),
          "xyz 结构文件没有参数可读，「读参数」保持不亮")

    # ------------------------------------------------------------------ #
    # loading a -1.restart as the structure leaves section 6 alone on purpose
    # ------------------------------------------------------------------ #
    print("\n== 读 restart 文件后第 6 节不再自动接上 ==")

    reset_gui()
    set_task("md")
    generate.w_ext_restart.clear()
    generate.load_from_path(str(prev))
    pump(120)
    check(generate.w_ext_restart.text() == "",
          f"续算文件那一行保持为空（坐标/晶胞已经读进来了）："
          f"{generate.w_ext_restart.text()!r}")
    generate.refresh()
    text = generate.preview.toPlainText()
    check(not any(line.strip() == "&EXT_RESTART" for line in text.splitlines()),
          "所以预览里不会再出现多余的 &EXT_RESTART 段")
    check("&COORD" in text, "体系坐标写进了输入（&COORD）")
    check("用不上" in generate.structure_info.text(),
          f"结构面板说明了为什么：{generate.structure_info.text()[-60:]!r}")
    check("速度" in generate.restart_hint.text()
          and "步数计数器" in generate.restart_hint.text(),
          f"并说明什么时候才需要它：{generate.restart_hint.text()[:60]!r}")

    # a file the user typed themselves is never touched
    generate.w_ext_restart.setText("my-own-1.restart")
    generate.load_from_path(str(prev))
    pump(120)
    check(generate.w_ext_restart.text() == "my-own-1.restart",
          "已经填了续算文件时不会被清掉")
    # ... and taking only POS repeats what &COORD already has: the validator says so
    set_take("pos")
    generate.w_ext_restart.setText(str(prev))
    generate.refresh()
    codes = " ".join(generate.diagnostics.item(row, 2).text()
                     for row in range(generate.diagnostics.rowCount())
                     if generate.diagnostics.item(row, 2) is not None)
    check("删掉" in codes or "再读一遍" in codes,
          f"只取 POS 时校验器提示这一节可以删掉：{codes[:70]!r}")

    # put the structure back for the cases that follow
    generate.w_ext_restart.clear()
    generate.load_from_path(SILICON)
    pump(60)

    # ------------------------------------------------------------------ #
    # a CP2K restart as the structure: the coordinates are *in* the file, so the
    # coordinate-file row is out of the way (and &COORD is written instead)
    # ------------------------------------------------------------------ #
    print("\n== 用 restart 当结构：坐标文件控件停用 ==")

    reset_gui()
    set_task("md")
    generate.w_method.setCurrentIndex(1)              # FIST
    pump(200)
    for _i in range(generate.w_mm_route.count()):
        if generate.w_mm_route.itemData(_i) == "AMBER":
            generate.w_mm_route.setCurrentIndex(_i)
    pump(200)
    relay_input = Path(tempfile.mkdtemp(prefix="gui_relay_inline_")) / "cp2k-1.restart"
    relay_input.write_text(
        "&GLOBAL\n  RUN_TYPE GEO_OPT\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD FIST\n  &MM\n    &FORCEFIELD\n"
        "      PARMTYPE AMBER\n      PARM_FILE_NAME mix.prmtop\n"
        "    &END FORCEFIELD\n  &END MM\n  &SUBSYS\n    &CELL\n"
        "      ABC 24.4 24.4 24.4\n      PERIODIC XYZ\n    &END CELL\n"
        "    &COORD\n      CA3 1.0 2.0 3.0\n      OH2 2.0 3.0 4.0\n"
        "      HO3 3.0 4.0 5.0\n    &END COORD\n    &TOPOLOGY\n"
        "      CONN_FILE_NAME mix.prmtop\n      CONN_FILE_FORMAT AMBER\n"
        "    &END TOPOLOGY\n  &END SUBSYS\n&END FORCE_EVAL\n",
        encoding="utf-8")
    generate.w_mm_parm.setText("mix.prmtop")
    generate.w_mm_coord.setText(str(relay_input))    # a stale, wrong value
    generate.load_from_path(str(relay_input))
    pump(250)
    check(generate.structure.n_atoms() == 3,
          f"restart 里的 3 个原子都读进来了（元素按 AMBER 名字推断）："
          f"{[(a.label, a.symbol) for a in generate.structure.atoms]}")
    check(generate.h_mm_coord.isEnabled(),
          "坐标文件控件**保持可用**（要不要用文件由用户决定）")
    check(generate.w_mm_coord.text() == "",
          f"但读进 restart 的坐标后它被清空了：{generate.w_mm_coord.text()!r}")
    check("坐标已经随结构文件读进来了" in generate.mm_hint.text(),
          f"力场页说明写清了原因：{generate.mm_hint.text()[-70:]!r}")
    generate.refresh()
    text = generate.preview.toPlainText()
    check("&COORD" in text and not any(
        line.strip().startswith("COORD_FILE_NAME") for line in text.splitlines()),
        "生成的输入写 &COORD，不写 COORD_FILE_NAME")
    check("CA3" in text and "OH2" in text,
          "CP2K 自己的原子名被保留（和 restart 一致）")
    # ... and the field is still usable: filling it makes the file win again
    generate.w_mm_coord.setText("real.inpcrd")
    pump(200)
    generate.refresh()
    text = generate.preview.toPlainText()
    check(any(line.strip().startswith("COORD_FILE_NAME") for line in text.splitlines())
          and "&COORD" not in text,
          "填上坐标文件后又以文件为准（不写 &COORD）——这一格没有被停用")

    # a plain structure file still fills the coordinate file in: the packmol flow
    reset_gui()
    set_task("md")
    generate.w_method.setCurrentIndex(1)
    pump(200)
    for _i in range(generate.w_mm_route.count()):
        if generate.w_mm_route.itemData(_i) == "AMBER":
            generate.w_mm_route.setCurrentIndex(_i)
    pump(200)
    generate.w_mm_parm.setText("mix.prmtop")
    generate.w_mm_coord.clear()
    generate.load_from_path(WATER)
    pump(250)
    check(generate.h_mm_coord.isEnabled(),
          "普通结构文件（xyz/pdb）下坐标文件控件照常可用")
    check(generate.w_mm_coord.text().endswith("water.xyz"),
          f"并且照旧自动填上（packmol 的用法不变）：{generate.w_mm_coord.text()!r}")
    generate.refresh()
    check("COORD_FILE_NAME" in generate.preview.toPlainText(),
          "这时写的是 COORD_FILE_NAME（坐标来自文件）")

    reset_gui()
    generate.w_mm_coord.clear()
    generate.load_from_path(SILICON)
    pump(60)

    # three tasks in a row: the second one reads what the first wrote, the
    # third one reads the second - only the take-list differs between them
    set_task("geo_opt")
    generate.w_ext_restart.clear()
    compare("第一棒：GEO_OPT 从头算",
            ["new", "--task", "geo_opt", "--structure", SILICON, "--stdout"])
    set_task("md")
    generate.w_ext_restart.setText("1_geo-1.restart")
    generate.w_ensemble.setCurrentText("NVT")
    set_take("pos")
    compare("第二棒：GEO_OPT → NVT",
            ["new", "--task", "md", "--structure", SILICON, "--ensemble", "NVT",
             "--restart", "1_geo-1.restart", "--restart-take", "pos", "--stdout"])

    set_take()
    check("RESTART_DEFAULT T" in gui_text() and "RESTART_POS" not in gui_text(),
          "回到「全部」：只写 RESTART_DEFAULT T")
    check(generate.w_restart_default.isEnabled(),
          "「全部」时「都以该文件为准」又可以改了")

    # 真实反馈：「拖拽进 restart 文件续算好像会默认开启，最好默认关闭」——
    # 那一项以前一开始就是勾着的（因为「取哪些量」默认是「全部」），整段看着像
    # 已经开着。现在：没给文件时不勾也不给改、预览里没有 &EXT_RESTART；
    # 一旦**填上**文件、且是「全部」，它才自动勾上（= RESTART_DEFAULT T），
    # 之后手动取消也能留住（任何刷新都不会把它勾回去）。
    generate.w_ext_restart.clear()
    set_take()
    pump(250)

    def has_ext_restart_block(text: str) -> bool:
        """The **section** (a comment mentioning &EXT_RESTART does not count)."""
        from cp2kbuild.parser import parse_input as _parse_input

        return any(section.name.upper() == "EXT_RESTART"
                   for section in _parse_input(text).sections)

    check(not generate.w_restart_default.isChecked()
          and not generate.w_restart_default.isEnabled()
          and not has_ext_restart_block(gui_text()),
          "没给续算文件时：不勾、不可改、预览里没有 &EXT_RESTART 段")
    generate.w_ext_restart.setText("prev-1.restart")
    pump(250)
    check(generate.w_restart_default.isChecked()
          and has_ext_restart_block(gui_text())
          and "RESTART_DEFAULT T" in gui_text(),
          "填上文件（「全部」）后自动勾上 = RESTART_DEFAULT T")
    generate.w_restart_default.setChecked(False)
    generate.refresh()
    pump(250)
    check(not generate.w_restart_default.isChecked()
          and "RESTART_DEFAULT F" in gui_text(),
          "手动取消后刷新不会把它勾回去（RESTART_DEFAULT F）")
    generate.w_ext_restart.clear()
    generate.refresh()
    pump(200)
    check(not has_ext_restart_block(gui_text()),
          "清空续算文件后 &EXT_RESTART 整段消失")

    # 真实反馈：「拖进 restart，点读参数，从上次续算会填上 true 字段」——CP2K 自己
    # 写的 restart 里 `&EXT_RESTART` 那一块的 RESTART_FILE_NAME 是**空的**，
    # 旧代码把这种"没有值的关键字"读成了布尔 True 再转成字符串 "True"。
    own = ROOT / "examples" / "generated" / "_tmp_own_restart"
    own.mkdir(parents=True, exist_ok=True)
    own_restart = own / "cp2k-1.restart"
    own_restart.write_text(
        "&GLOBAL\n  PROJECT_NAME cp2k\n  RUN_TYPE MD\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD FIST\n  &MM\n    &FORCEFIELD\n      PARMTYPE AMBER\n"
        "      PARM_FILE_NAME \n    &END FORCEFIELD\n  &END MM\n&END FORCE_EVAL\n"
        "&EXT_RESTART\n   RESTART_FILE_NAME \n   RESTART_DEFAULT F\n"
        "   RESTART_VEL T\n&END EXT_RESTART\n"
        "&MOTION\n  &MD\n    ENSEMBLE NPT_I\n    STEPS 100\n  &END MD\n&END MOTION\n",
        encoding="utf-8")
    generate.w_ext_restart.clear()
    generate.load_from_path(str(own_restart))
    pump(500)
    generate.import_parameters()
    pump(700)
    check(generate.w_ext_restart.text() == ""
          and not has_ext_restart_block(gui_text()),
          f"拖入 CP2K 自己写的 restart 再「读参数」：续算不会被填上"
          f"（{generate.w_ext_restart.text()!r}）")
    check("EXT_RESTART" in generate.import_info.text()
          and "没读" in generate.import_info.text(),
          f"「没读」里说明了为什么：{generate.import_info.text()[-90:]}")
    generate.w_ext_restart.clear()
    generate.load_from_path(WATER)
    pump(250)

    # ------------------------------------------------------------------ #
    # Molden output (a DFT-only feature: nothing appears for FIST)
    # ------------------------------------------------------------------ #
    print("\n== Molden 输出 ==")

    reset_gui()
    generate.load_from_path(WATER)
    set_task("energy")
    check(not any(w.isEnabled() for w in generate.molden_controls),
          "没勾「写分子轨道」时下面的细节控件全是灰的")
    generate.w_molden_file.setText("orbitals")      # must not leak into the input
    generate.w_molden.setChecked(True)
    pump(60)
    text = gui_text()
    check("&MO_MOLDEN" in text and "FILENAME orbitals" in text,
          "勾上后 &DFT/&PRINT/&MO_MOLDEN 出现并带上文件名")
    check(any(w.isEnabled() for w in generate.molden_controls),
          "细节控件跟着可以编辑")
    compare("Molden：默认只写占据轨道",
            ["new", "--task", "energy", "--structure", WATER, "--vacuum", "12",
             "--molden", "--molden-file", "orbitals", "--stdout"])

    generate.w_molden_nlumo.setValue(-1)
    generate.w_molden_unit.setCurrentText("BOHR")
    generate.w_molden_gto_kind.setCurrentText("CARTESIAN")
    generate.w_molden_pseudo.setChecked(True)
    generate.w_molden_cell.setChecked(True)
    compare("Molden：全部空轨道 + 赝势/晶胞块",
            ["new", "--task", "energy", "--structure", WATER, "--vacuum", "12",
             "--molden", "--molden-file", "orbitals", "--molden-nlumo", "-1",
             "--molden-unit", "BOHR", "--molden-gto-kind", "CARTESIAN",
             "--molden-write-pseudo", "--molden-write-cell", "--stdout"])

    # virtual orbitals need somewhere to live: the diagonalisation route only
    # writes what ADDED_MOS asks for, so the tool raises it by itself
    set_task("energy")
    set_scf("DIAGONALIZATION")
    check("ADDED_MOS" in gui_text(),
          "对角化路线要空轨道时自动补上 ADDED_MOS")
    compare("Molden：对角化路线自带 ADDED_MOS",
            ["new", "--task", "energy", "--structure", WATER, "--vacuum", "12",
             "--scf", "DIAGONALIZATION", "--molden", "--molden-file", "orbitals",
             "--molden-nlumo", "-1", "--molden-unit", "BOHR",
             "--molden-gto-kind", "CARTESIAN", "--molden-write-pseudo",
             "--molden-write-cell", "--stdout"])

    set_scf("auto")
    set_task("freq")
    generate.refresh()
    check("&MOLDEN_VIB" in generate.text,
          "频率任务额外写 &MOLDEN_VIB（振动模式）")
    generate.w_molden_vib.setChecked(False)
    compare("Molden：频率不写振动模式",
            ["new", "--task", "freq", "--structure", WATER, "--vacuum", "12",
             "--molden", "--molden-file", "orbitals", "--molden-nlumo", "-1",
             "--molden-unit", "BOHR", "--molden-gto-kind", "CARTESIAN",
             "--molden-write-pseudo", "--molden-write-cell",
             "--no-molden-vib", "--stdout"])

    reset_gui()

    # ------------------------------------------------------------------ #
    # the controls that used to be missing: &KPOINTS (reachable for every
    # Quickstep task, not only for band/PDOS), cube files, fixed atoms,
    # EPS_DEFAULT and the extrapolation scheme
    # ------------------------------------------------------------------ #
    print("\n== 布里渊区采样（&KPOINTS）/ 立方体 / 固定原子 ==")

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    check(not generate.kpoint_box.isChecked(),
          "k 点组默认不启用（= Γ 点，不写 &KPOINTS）")
    generate.refresh()
    check("&KPOINTS" not in generate.preview.toPlainText(),
          "不启用时预览里确实没有 &KPOINTS")
    compare("不启用 k 点 == 命令行的默认",
            ["new", "--task", "energy", "--structure", SILICON, "--stdout"])

    generate.kpoint_box.setChecked(True)
    pump(60)
    generate.refresh()
    text = generate.preview.toPlainText()
    check("&KPOINTS" in text and "SCHEME MONKHORST-PACK" in text,
          "启用后按目标间距自动给出网格")
    compare("启用 + 自动网格（20 Å）",
            ["new", "--task", "energy", "--structure", SILICON,
             "--kpoint-spacing", "20", "--stdout"])
    check("MONKHORST-PACK" in generate.kpoint_hint.text()
          or "GAMMA" in generate.kpoint_hint.text(),
          f"说明里给出了将写出的方案：{generate.kpoint_hint.text()!r}")

    generate.w_kpoint_gamma_centered.setChecked(True)
    generate.w_kpoint_full_grid.setChecked(True)
    generate.w_kpoint_verbose.setChecked(True)
    generate.w_kpoint_wavefunctions.setCurrentText("COMPLEX")
    compare("GAMMA_CENTERED + FULL_GRID + VERBOSE + COMPLEX",
            ["new", "--task", "energy", "--structure", SILICON,
             "--kpoint-spacing", "20", "--kpoint-gamma-centered",
             "--kpoint-full-grid", "--kpoint-verbose",
             "--kpoint-wavefunctions", "COMPLEX", "--stdout"])

    generate.w_kgrid_auto.setChecked(False)
    generate.w_kgrid.setText("6 6 6")
    generate._set_widget_value(generate.w_kpoint_scheme, "MONKHORST-PACK")
    pump(60)
    compare("手填网格 6 6 6",
            ["new", "--task", "energy", "--structure", SILICON,
             "--kpoint-grid", "6,6,6", "--kpoint-gamma-centered",
             "--kpoint-full-grid", "--kpoint-verbose",
             "--kpoint-wavefunctions", "COMPLEX", "--stdout"])
    check("6 6 6" in generate.kpoint_hint.text(),
          f"说明里报了当前网格：{generate.kpoint_hint.text()!r}")

    # explicit k-points: SCHEME GENERAL + one KPOINT line per point
    generate.w_kgrid_auto.setChecked(True)
    generate.w_kpoint_full_grid.setChecked(False)
    generate.w_kpoint_gamma_centered.setChecked(False)
    generate.w_kpoint_verbose.setChecked(False)
    generate.w_kpoint_wavefunctions.setCurrentText("REAL")
    generate._set_widget_value(generate.w_kpoint_scheme, "GENERAL")
    generate.w_kpoint_points.setRowCount(0)
    generate.w_kpoint_points.add_row(["0.0", "0.0", "0.0", "1.0"])
    generate.w_kpoint_points.add_row(["0.5", "0.5", "0.5", "2.0"])
    pump(60)
    generate.refresh()
    check("KPOINT 0.0 0.0 0.0 1.0" in generate.preview.toPlainText()
          and "SCHEME GENERAL" in generate.preview.toPlainText(),
          "显式 k 点写成一串 KPOINT 行")
    check("SYMMETRY" in generate.kpoint_hint.text(),
          f"提醒 SYMMETRY 会约化显式 k 点：{generate.kpoint_hint.text()!r}")
    compare("显式 k 点（GENERAL）",
            ["new", "--task", "energy", "--structure", SILICON,
             "--kpoint-point", "0,0,0", "--kpoint-point", "0.5,0.5,0.5,2.0",
             "--stdout"])

    # cubes + fixed atoms + EPS_DEFAULT + extrapolation
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("geo_opt")
    generate.w_mo_cubes.setChecked(True)
    generate.w_mo_cubes_nlumo.setValue(1)
    generate.w_mo_cubes_stride.setText("1 1 1")
    generate.w_density_cube.setChecked(True)
    generate.w_fixed_atoms.setText("1..4")
    generate.w_fixed_atoms_components.setCurrentText("Z")
    generate.w_eps_default.setText("1e-9")
    generate._set_widget_value(generate.w_extrapolation, "USE_GUESS")
    pump(60)
    generate.refresh()
    text = generate.preview.toPlainText()
    check("&MO_CUBES" in text and "NHOMO 1" in text and "STRIDE 1 1 1" in text,
          "轨道立方体写对了")
    check("&TOT_DENSITY_CUBE" in text, "密度立方体写对了")
    check("FIXED_ATOMS" in text and "LIST 1..4" in text
          and "COMPONENTS_TO_FIX Z" in text, "固定原子写对了")
    check("EPS_DEFAULT 1.000000E-09" in text, "EPS_DEFAULT 写进去了")
    compare("立方体 + 固定原子 + EPS_DEFAULT + 外推",
            ["new", "--task", "geo_opt", "--structure", SILICON,
             "--mo-cubes", "--mo-cubes-nlumo", "1",
             "--mo-cubes-stride", "1 1 1", "--density-cube",
             "--fixed-atoms", "1..4", "--fixed-atoms-components", "Z",
             "--eps-default", "1e-9", "--extrapolation", "USE_GUESS",
             "--stdout"])

    # a fixed-atom list on a single point is pointless, and the interface says so
    set_task("energy")
    pump(60)
    check(not generate.w_fixed_atoms.isEnabled(),
          "单点能量下「固定原子」变灰（那条「?」里写着原因）")
    set_task("geo_opt")
    pump(60)
    check(generate.w_fixed_atoms.isEnabled(), "GEO_OPT 下「固定原子」可以编辑")

    reset_gui()

    # ---- k-points are DIAGONALIZATION-only: both directions react -------- #
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    set_scf("auto")
    generate.kpoint_box.setChecked(True)
    pump(60)
    check(generate._effective_route() == "DIAGONALIZATION",
          f"「自动」+ k 点 → 自动判定为对角化（实际 {generate._effective_route()}）")
    check("DIAGONALIZATION" in generate.route_label.text(),
          f"路线说明也跟着变：{generate.route_label.text()!r}")
    check(generate.w_kpoint_spacing.isEnabled()
          and generate.w_kpoint_scheme.isEnabled(),
          "对角化路线下 k 点控件可用")
    check(not generate.w_kgrid.isEnabled(),
          "「按间距自动」勾着时手填网格是灰的（那个值不会被写进去）")
    generate.refresh()
    text = generate.preview.toPlainText()
    check("&DIAGONALIZATION" in text and "&OT" not in text,
          "写出来的是对角化，没有 &OT")

    # picking OT switches the k-point group off (CP2K aborts on that pair)
    set_scf("OT")
    pump(80)
    check(not generate.kpoint_box.isChecked(),
          "选 OT 后 k 点组自动关掉")
    check("OT" in generate.kpoint_hint.text()
          and "DIAGONALIZATION" in generate.kpoint_hint.text(),
          f"说明里写清了原因和出路：{generate.kpoint_hint.text()!r}")
    check(not generate.w_kgrid.isEnabled(),
          "OT 路线下 k 点网格变灰（那条「?」里写着原因）")
    generate.refresh()
    check("&KPOINTS" not in generate.preview.toPlainText(),
          "所以预览里不会出现 &KPOINTS")

    # and the other direction: enabling k-points while OT is selected swings
    # the route back to DIAGONALIZATION instead of building a doomed input
    generate.kpoint_box.setChecked(True)
    pump(80)
    check(generate.w_scf.currentData() == "DIAGONALIZATION",
          f"在 OT 下勾 k 点 → 路线自动改成对角化"
          f"（实际 {generate.w_scf.currentData()}）")
    check("OT" in generate.kpoint_hint.text()
          and "DIAGONALIZATION" in generate.kpoint_hint.text(),
          f"并说明为什么改：{generate.kpoint_hint.text()!r}")
    generate.refresh()
    text = generate.preview.toPlainText()
    check("&KPOINTS" in text and "&OT" not in text,
          "预览里 k 点和对角化同时存在，且没有 &OT")

    # ---- an explicit scheme owns the mesh box, and the mesh box must have
    # ---- numbers whenever MONKHORST-PACK/MACDONALD is written ------------ #
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    set_scf("auto")
    generate.kpoint_box.setChecked(True)
    pump(60)
    generate._set_widget_value(generate.w_kpoint_scheme, "MONKHORST-PACK")
    pump(60)
    check(not generate.w_kgrid_auto.isChecked(),
          "选 MONKHORST-PACK 后「按间距自动」自动取消勾选（数字来自网格框）")
    check(generate.w_kgrid.isEnabled(), "手填网格跟着变成可编辑")
    generate.w_kgrid.setText("6 6 6")
    pump(60)
    generate.refresh()
    check("SCHEME MONKHORST-PACK 6 6 6" in generate.preview.toPlainText(),
          "手填的网格进了 SCHEME 行")

    # a leftover explicit table row must not fight the scheme: CP2K reads the
    # KPOINT lines only under SCHEME GENERAL, so the explicit list wins
    generate.w_kpoint_points.add_row(["0.5", "0.5", "0.5", "2.0"])
    pump(60)
    generate.refresh()
    text = generate.preview.toPlainText()
    check("SCHEME GENERAL" in text and "KPOINT 0.5 0.5 0.5 2.0" in text,
          "SCHEME 与显式 k 点冲突时，写出来的是 SCHEME GENERAL + KPOINT")
    check("GENERAL" in generate.kpoint_hint.text()
          and "⚠" in generate.kpoint_hint.text(),
          f"说明里点出了这个冲突：{generate.kpoint_hint.text()!r}")

    # and the box can be empty: never write a scheme without its three numbers
    # (estimate the mesh from the spacing instead of dropping &KPOINTS)
    generate.w_kpoint_points.setRowCount(0)
    generate.w_kgrid.clear()
    pump(60)
    generate.refresh()
    text = generate.preview.toPlainText()
    scheme_line = next((line.strip() for line in text.splitlines()
                        if line.strip().upper().startswith("SCHEME")), "")
    parts = scheme_line.split()
    check("&KPOINTS" in text and len(parts) >= 3
          and parts[1].upper() in ("MONKHORST-PACK", "MACDONALD"),
          f"网格框空着时按间距估算出完整网格：{scheme_line!r}")
    check("没有三个数字" in generate.kpoint_hint.text(),
          f"说明里讲清了空网格框会发生什么：{generate.kpoint_hint.text()!r}")

    reset_gui()

    # ------------------------------------------------------------------ #
    # the classical force-field page: it replaces the DFT pages, and its own
    # output has to stay byte-identical to the command line
    # ------------------------------------------------------------------ #
    print("\n== 经典力场页（METHOD FIST）==")

    def set_method(value: str) -> None:
        for i in range(generate.w_method.count()):
            if generate.w_method.itemData(i) == value:
                generate.w_method.setCurrentIndex(i)
                pump(60)
                return
        raise AssertionError(f"no method {value}")

    def set_route(value: str) -> None:
        for i in range(generate.w_mm_route.count()):
            if generate.w_mm_route.itemData(i) == value:
                generate.w_mm_route.setCurrentIndex(i)
                pump(60)
                return
        raise AssertionError(f"no MM route {value}")

    def set_mm_table(rows: list[list[str]]) -> None:
        generate.w_mm_table.setRowCount(0)
        for row in rows:
            generate.w_mm_table.add_row(row)
        pump(60)

    reset_gui()
    generate.load_from_path(WATER)
    set_task("md")
    set_method("FIST")
    check(generate.classical_box.isVisible(), "选 FIST 后经典力场页出现")

    # 一个真实问题：「没有 coord 的 inp 为啥不能读？」——力场输入把原子放在
    # COORD_FILE_NAME 里，它自己没有 &COORD；而结构这一格要的是**元素符号**
    # （inpcrd 里也没有）。同一目录里它自己会写的 <PROJECT>-1.restart 有
    # &COORD/&CELL，所以工具改读那个并把原因写在信息行上。
    nocoord = ROOT / "examples" / "generated"
    nocoord.mkdir(parents=True, exist_ok=True)
    with_restart = nocoord / "_tmp_nocoord_with_restart"
    without = nocoord / "_tmp_nocoord_alone"
    for folder in (with_restart, without):
        folder.mkdir(exist_ok=True)
        (folder / "cp2k.inp").write_text(
            "&GLOBAL\n  PROJECT_NAME cp2k\n  RUN_TYPE MD\n&END GLOBAL\n"
            "&FORCE_EVAL\n  METHOD FIST\n  &MM\n    &FORCEFIELD\n"
            "      PARMTYPE AMBER\n      PARM_FILE_NAME mixture.prmtop\n"
            "    &END FORCEFIELD\n  &END MM\n  &SUBSYS\n    &CELL\n"
            "      ABC [angstrom] 20 20 20\n    &END CELL\n    &TOPOLOGY\n"
            "      CONNECTIVITY AMBER\n      CONN_FILE_NAME mixture.prmtop\n"
            "      COORDINATE CRD\n      COORD_FILE_NAME mixture.inpcrd\n"
            "    &END TOPOLOGY\n  &END SUBSYS\n&END FORCE_EVAL\n",
            encoding="utf-8")
    (with_restart / "cp2k-1.restart").write_text(
        "&GLOBAL\n  PROJECT_NAME cp2k\n  RUN_TYPE MD\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD FIST\n  &SUBSYS\n    &CELL\n"
        "      ABC [angstrom] 20 20 20\n    &END CELL\n    &COORD\n"
        "      O 0.0 0.0 0.0\n      H 0.0 0.0 0.9572\n      H 0.757 0.0 0.5865\n"
        "    &END COORD\n  &END SUBSYS\n&END FORCE_EVAL\n", encoding="utf-8")
    generate.load_from_path(str(with_restart / "cp2k.inp"))
    pump(400)
    check(generate.structure is not None and generate.structure_error is None
          and generate.structure.n_atoms() == 3
          and generate.structure_edit.text().endswith("cp2k-1.restart"),
          f"没有 &COORD 的输入：同目录有它自己的 restart 时改读它"
          f"（{generate.structure_edit.text()}）")
    check("没有 &COORD" in generate.structure_info.text()
          and generate.structure_info.styleSheet().endswith("#b9770e;"),
          f"信息行说明了为什么换文件：{generate.structure_info.text()[:60]}")
    generate.load_from_path(str(without / "cp2k.inp"))
    pump(400)
    check(generate.structure is None and bool(generate.structure_error)
          and "没有 &COORD" in generate.structure_info.text()
          and "结构留空" in generate.structure_info.text(),
          f"没有 restart 可接手时给出三条出路："
          f"{generate.structure_info.text()[:80]}")

    # 真实反馈：「指定了 inpcrd 为啥预览不刷新」——力场文件路线必须有 &CELL，
    # 而 CP2K 只在输入里没有 &CELL 时才去读坐标文件里的盒子。所以：
    #   * 文件里有盒子（AMBER inpcrd 的 box 行）→ 工具直接填进「晶胞覆盖」，
    #     预览就此变成真正的输入，并把来源写进文件头部；
    #   * 文件里没有盒子（tleap 不带 box 的 inpcrd、XYZ）→ 明说没有，并指名
    #     同名的 pdb/restart 里有没有现成的晶胞数字。
    cells = ROOT / "examples" / "generated" / "_tmp_cellcase"
    cells.mkdir(parents=True, exist_ok=True)
    # 上一条用例故意留了一个「结构读取失败」，先清干净再测晶胞
    generate.structure_edit.clear()
    generate.load_structure()
    set_route("AMBER")
    generate.w_mm_parm.setText(str(cells / "mix.prmtop"))
    pump(200)
    boxed = cells / "boxed.inpcrd"
    boxed.write_text(
        "boxed\n     3\n"
        "   0.0000000   0.0000000   0.0000000   0.0000000   0.0000000   0.9572000\n"
        "   0.7570000   0.0000000   0.5865000\n"
        "  20.0000000  20.0000000  20.0000000  90.0000000  90.0000000  90.0000000\n",
        encoding="utf-8")
    boxless = cells / "nobox.inpcrd"
    boxless.write_text("nobox\n     3\n"
                       "   0.0000000   0.0000000   0.0000000   0.0000000   0.0000000"
                       "   0.9572000\n   0.7570000   0.0000000   0.5865000\n",
                       encoding="utf-8")
    (cells / "nobox.pdb").write_text(
        "CRYST1   24.510   24.400   24.410  90.00  90.00  90.00 P 1           1\n"
        "ATOM      1  O   UNL A   1       0.000   0.000   0.000  1.00  0.00           O\n"
        "ATOM      2  H   UNL A   1       0.000   0.000   0.957  1.00  0.00           H\n"
        "ATOM      3  H   UNL A   1       0.757   0.000   0.586  1.00  0.00           H\n"
        "END\n", encoding="utf-8")
    generate.w_cell.clear()
    generate._cell_from_file = None
    generate.w_mm_coord.setText(str(boxed))
    pump(800)
    check(generate.w_cell.text() == "20,20,20" and "&CELL" in generate.text
          and "晶胞取自坐标文件" in generate.text,
          f"坐标文件里有盒子 → 自动填「晶胞覆盖」并注明来源"
          f"（{generate.w_cell.text()!r}）")
    generate.w_cell.clear()
    generate._cell_from_file = None
    generate.w_mm_coord.setText(str(boxless))
    pump(800)
    preview = generate.preview.toPlainText()
    check(not generate.w_cell.text() and "等待晶胞" in generate.summary.text()
          and "它里面没有盒子" in preview
          and "同名的 nobox.pdb 里有晶胞：24.51,24.4,24.41" in preview,
          f"坐标文件里没有盒子 → 说明没有、并指名同名的 pdb："
          f"{preview.splitlines()[-2:]}")
    generate.w_mm_coord.clear()
    generate.w_cell.clear()
    generate._cell_from_file = None
    generate.load_from_path(WATER)
    pump(250)

    # 真实反馈：「inp 里的 cell 为啥不能直接读进来」。两个原因都修了：
    #   1) 读参数原先根本不读 &SUBSYS/&CELL（也不列进「没读」清单）；
    #   2) 读不出结构的文件（力场输入没有 &COORD）连「读参数」按钮都是灰的。
    # 现在：载入这种文件 → 「读参数」可用 → 力场/拓扑/坐标/晶胞/周期性全读进来
    # → 力场文件路线的预览直接生成（它本来就不需要结构文件）。
    importable = ROOT / "examples" / "generated" / "_tmp_cellcase" / "ref.inp"
    importable.parent.mkdir(parents=True, exist_ok=True)
    importable.write_text(
        "&GLOBAL\n  PROJECT_NAME ref\n  RUN_TYPE MD\n&END GLOBAL\n"
        "&FORCE_EVAL\n  METHOD FIST\n  &MM\n    &FORCEFIELD\n"
        "      PARMTYPE AMBER\n      PARM_FILE_NAME mixture.prmtop\n"
        "    &END FORCEFIELD\n  &END MM\n  &SUBSYS\n    &CELL\n"
        "      ABC [angstrom] 24.51 24.4 24.41\n      PERIODIC XYZ\n    &END CELL\n"
        "    &TOPOLOGY\n      CONNECTIVITY AMBER\n      CONN_FILE_NAME mixture.prmtop\n"
        "      COORDINATE CRD\n      COORD_FILE_NAME mixture.inpcrd\n    &END TOPOLOGY\n"
        "  &END SUBSYS\n&END FORCE_EVAL\n&MOTION\n  &MD\n    STEPS 200\n"
        "    ENSEMBLE NVT\n    TEMPERATURE 493\n  &END MD\n&END MOTION\n",
        encoding="utf-8")
    generate.structure_edit.clear()
    generate.load_structure()
    generate.w_cell.clear()
    generate._cell_from_file = None
    generate.load_from_path(str(importable))
    pump(400)
    check(generate._importable is not None and generate.w_import.isEnabled(),
          "没有 &COORD 的力场输入：读不出结构，但「读参数」仍然可用")
    generate.import_parameters()
    pump(600)
    check(generate.w_cell.text() == "24.51,24.4,24.41"
          and generate.w_periodic.currentData() == "XYZ"
          and generate.w_method.currentData() == "FIST",
          f"「读参数」把 &CELL/PERIODIC/方法都读进来"
          f"（{generate.w_cell.text()!r} / {generate.w_periodic.currentText()}）")
    check("ABC [angstrom] 24.51 24.4 24.41" in generate.text
          and "COORD_FILE_NAME" in generate.text
          and "请先选择结构文件" not in generate.preview.toPlainText(),
          "读参数后力场文件路线的预览直接生成（不需要结构文件）")
    # 真实反馈：没有坐标这一条要是**错误**（生成期 error 行），不是警告
    generate.w_mm_coord.clear()
    generate.w_mm_conn.clear()
    generate.w_mm_parm.clear()
    generate.w_cell.setText("24.4,24.4,24.4")
    pump(600)
    levels = [(generate.diagnostics.item(row, 0).text(),
               generate.diagnostics.item(row, 1).text(),
               generate.diagnostics.item(row, 2).text())
              for row in range(generate.diagnostics.rowCount())]
    build_errors = [row for row in levels if row[1] == "build-error"]
    check(build_errors and all(row[0] == "错误" for row in build_errors)
          and any("一个原子都没有" in row[2] for row in build_errors),
          f"没有坐标文件也没有结构 → 「错误」行（build-error）：{build_errors}")
    # 真实反馈：同一件事被说了两遍、而且一遍「警告」一遍「错误」。现在生成器
    # 自己报 error，并把它覆盖的校验器 code 记进 covered_codes，合并报告据此
    # 丢掉校验器那一份 —— 一件事一行、一个等级。
    codes = [row[1] for row in levels]
    check(len(build_errors) == 3
          and codes.count("build-error") == 3
          and not any(row[0] == "警告" and row[1] == "build-warning"
                      for row in levels),
          f"三个缺失（参数/拓扑/坐标）各报一次，全是错误、没有重复的警告：{codes}")
    check("mm-parmtype-without-file" not in codes
          and "mm-connectivity-without-file" not in codes
          and "periodic-without-cell" not in codes,
          f"生成器已经说过的校验器规则不再重复出现：{codes}")
    check("错误" in generate.summary.text().split("警告")[0],
          f"摘要把它算进错误数：{generate.summary.text()[:60]}")
    # 这块读进来的东西很多（力场路线、拓扑/坐标文件、晶胞…），后面的用例要求
    # 干净状态，所以按这一节开头的方式重来一遍
    reset_gui()
    generate.load_from_path(WATER)
    set_task("md")
    set_method("FIST")
    set_route("AMBER")
    pump(250)
    check(not generate.method_box.isVisible()
          and not generate.route_box.isVisible()
          and not generate.kind_box.isVisible(),
          "选 FIST 后泛函/SCF 路线/基组页整块收起（CP2K 下 FIST 里没有 &DFT）")
    check(not generate.task_combo.model().item(
        generate.task_combo.findData("band")).isEnabled(),
        "FIST 下「能带」被禁用（力场没有本征值）")
    check("力场" in generate.task_combo.itemText(
        generate.task_combo.findData("md")),
        "MD 的名字随方法变化，不会让人以为是 AIMD")
    check(abs(generate.w_timestep.value() - 1.0) < 1e-9,
          "FIST 的时间步默认 1.0 fs（AIMD 是 0.5）")
    # 真实反馈：「选 FIST 时为什么还是会显示当前路线 OT」。那一行摘要**故意**放在
    # 可折叠的 3.2 外面（一眼能看到走哪条 SCF 路线），于是 _method_changed 把
    # 3.2 收起来以后，它还留着上一次算出来的 OT。
    check("经典力场" in generate.route_label.text()
          and "当前路线：OT" not in generate.route_label.text(),
          f"选 FIST 后「当前路线」不再写着 OT：{generate.route_label.text()[:70]}")
    # isHidden() = 明确被这次判定隐藏（isVisibleTo 会连可折叠块的收起一起算进来）
    check(generate.ot_box.isHidden() and generate.diag_box.isHidden(),
          "选 FIST 后 OT/对角化两块都被判定隐藏（FIST 里没有 &SCF）")
    set_method("QUICKSTEP")
    check("当前路线：OT" in generate.route_label.text()
          and not generate.ot_box.isHidden() and generate.diag_box.isHidden(),
          f"切回 QUICKSTEP 后摘要与 OT 块恢复：{generate.route_label.text()[:50]}")
    set_method("FIST")

    # 1. AMBER, everything from files, NVT, explicit Ewald settings
    #    (the coordinate file is filled automatically from the loaded structure:
    #    that is what a drag-and-drop followed by "FIST" has to do)
    check(generate.w_mm_coord.text() == WATER,
          f"切到 FIST 后坐标文件自动填成刚载入的结构（{generate.w_mm_coord.text()}）")
    check(generate.w_mm_coord_format.currentData() is None
          and generate._coord_format() == "XYZ",
          "坐标格式默认「自动（按文件后缀）」，.xyz 解析成 XYZ")
    check("COORDINATE XYZ" in gui_text(),
          "生成出来的输入里 COORDINATE 是 XYZ（不是 AMBER 路线的 CRD）")

    # 1b. a coordinate file with several molecules fills the &MOL_SET table by
    #     itself: a packmol PDB numbers every molecule as residue UNL, so the
    #     block sizes *are* the molecule sizes and the count is NMOL
    def write_packmol(path, sizes) -> None:
        lines = ["CRYST1   30.000   30.000   30.000  90.00  90.00  90.00 P 1           1"]
        serial = 0
        for block, size in enumerate(sizes):
            for offset in range(size):
                serial += 1
                # PDB is fixed-width: name 12:16, resName 17:20, chain 21,
                # resSeq 22:26, x 30:38 - both readers have to agree on it
                lines.append(f"HETATM{serial:5d}  C{offset:<2d} UNL A{block + 1:4d}    "
                             f"{float(serial):8.3f}   0.000   0.000  1.00  0.00           C")
        lines.append("END")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    mm_pdb = ROOT / "examples" / "generated" / "_tmp_packmol.pdb"
    write_packmol(mm_pdb, [3] + [2] * 2)          # 3 atoms x1 + 2 atoms x2
    generate.w_mm_molecules.setRowCount(0)
    generate.w_mm_coord.clear()
    generate._mm_autofilled = None
    generate._mm_detected_from = None
    generate.load_from_path(str(mm_pdb))          # 拖进来：结构 + 坐标 + 识别
    pump(500)
    counts = [generate.w_mm_molecules.item(r, 2).text()
              for r in range(generate.w_mm_molecules.rowCount())]
    check(counts == ["1", "2"], f"拖入多组分 PDB 后自动填出 &MOL_SET 两行：{counts}")
    check([generate.w_mm_molecules.item(r, 0).text()
           for r in range(generate.w_mm_molecules.rowCount())] == ["AMBER", "AMBER"],
          "连接性列按当前路线的 CONNECTIVITY 预填")
    check(generate.w_mm_molecules.item(0, 1).text() == "",
          "拓扑文件那一列留空（坐标文件给不出文件名）")
    check("3 原子 ×1 + 2 原子 ×2" in generate.mm_molecules_hint.text(),
          "提示里说明这是从残基/链号读出来的推测")

    # dropping *another* coordinate file must refresh the table (it used to keep
    # the first file's composition)
    other_pdb = ROOT / "examples" / "generated" / "_tmp_packmol2.pdb"
    write_packmol(other_pdb, [5] + [4] * 2 + [3] * 3)
    generate.load_from_path(str(other_pdb))
    pump(500)
    counts = [generate.w_mm_molecules.item(r, 2).text()
              for r in range(generate.w_mm_molecules.rowCount())]
    check(counts == ["1", "2", "3"],
          f"再拖另一个 PDB，表格跟着更新：{counts}")
    check("5 原子 ×1" in generate.mm_molecules_hint.text(),
          "提示也跟着换成新的组成")
    # ... and a topology path the user typed survives a re-detection
    generate.w_mm_molecules.item(0, 1).setText("D:/ff/five.prmtop")
    generate.w_mm_coord.clear()
    generate._mm_autofilled = None
    generate._mm_detected_from = None
    generate.load_from_path(str(mm_pdb))
    pump(500)
    check(generate.w_mm_molecules.item(0, 1).text() == "D:/ff/five.prmtop",
          "重新识别时保留已经填好的拓扑文件路径")

    # an empty topology file must be reported as such, not silently dropped
    from cp2kbuild.datafiles import load_index as _load_index
    from cp2kbuild.recipes import build as _build
    from cp2kbuild.validate import Validator as _Validator

    spec = generate.collect_spec()
    check(len(spec.mm_molecules) == 2
          and spec.mm_molecules[0].conn_file == "D:/ff/five.prmtop"
          and spec.mm_molecules[1].conn_file is None,
          "没填拓扑文件的行也照样生成 &MOLECULE（让校验器点名到行）")
    problems = [d.code for d in _Validator(None, None).run(
        _build(spec, _load_index(None)).sections) if d.level == "error"]
    check(problems.count("mm-molecule-without-file") == 1,
          f"只给缺文件的那一行报错（{problems}）")

    # a single-component coordinate file clears the stale table (and says why)
    single_pdb = ROOT / "examples" / "generated" / "_tmp_packmol3.pdb"
    write_packmol(single_pdb, [4] * 6)
    generate.w_mm_molecules.item(0, 1).setText("")
    generate.load_from_path(str(single_pdb))
    pump(500)
    check(generate.w_mm_molecules.rowCount() == 0,
          "单一组分的坐标文件会把旧表清掉（它已经不描述这个文件了）")
    check("只看出一种分子" in generate.mm_molecules_hint.text()
          and "识别组分" in generate.mm_molecules_hint.text(),
          "并说明：合并拓扑用上面那格，单分子拓扑点按钮复制 N 份")
    generate._detect_components_clicked()
    pump(300)
    check([generate.w_mm_molecules.item(r, 2).text()
           for r in range(generate.w_mm_molecules.rowCount())] == ["6"],
          "按钮能把单一组分也填成一行（单分子拓扑复制 N 份）")
    for path in (mm_pdb, other_pdb, single_pdb):
        path.unlink()
    generate.w_mm_molecules.setRowCount(0)
    generate.w_mm_coord.clear()
    generate._mm_autofilled = None
    generate._mm_detected_from = None
    generate.load_from_path(WATER)                # back to the comparison setup
    pump(300)
    check("自动填成" in generate.mm_hint.text(),
          "提示里说明了这次是自动填的，以及力场路线不写 &COORD")

    # 1c. PARM_FILE_NAME and CONN_FILE_NAME are normally the *same* prmtop for
    #     AMBER, so the field is inherited instead of asked for twice
    check(generate.w_mm_same_file.isChecked() and not generate.w_mm_conn.isEnabled(),
          "AMBER 路线默认勾上「拓扑文件同参数文件」，拓扑文件框变灰")
    generate.w_cell.setText("30,30,30")
    generate.w_mm_parm.setText("a.prmtop")
    generate.w_mm_coord.setText("a.inpcrd")
    pump(400)
    text = gui_text()
    check("PARM_FILE_NAME a.prmtop" in text and "CONN_FILE_NAME a.prmtop" in text,
          "勾上时只写一个路径，拓扑那个自动继承")
    check(generate.collect_spec().mm_conn_file is None,
          "Spec 里 mm_conn_file 留空（和命令行的默认行为一致）")
    check("# topology      : CONNECTIVITY AMBER  a.prmtop（与参数文件同一个文件）" in text,
          "输入头部写明拓扑文件是从参数文件继承来的")
    # ... and the CLI produces the same file without --mm-conn-file
    check(generate.text == cli_text(
        ["new", "--task", "md", "--structure", WATER, "--method", "fist",
         "--mm-route", "AMBER", "--mm-parm-file", "a.prmtop",
         "--mm-coord-file", "a.inpcrd", "--cell", "30,30,30", "--timestep", "1.0",
         "--stdout"]),
          "只给 --mm-parm-file 时，GUI 与 CLI 仍然逐字节一致")
    generate.w_mm_same_file.setChecked(False)
    generate.w_mm_conn.setText("b.prmtop")
    pump(400)
    check("CONN_FILE_NAME b.prmtop" in gui_text(),
          "取消勾选后可以给一个不同的拓扑文件")
    generate.w_mm_conn.clear()
    generate.w_mm_same_file.setChecked(True)
    pump(300)

    # 1d. the preview must settle: it used to re-schedule itself for ever
    #     (refresh -> rebuild the Poisson combo -> currentIndexChanged -> schedule)
    pump(800)
    check(generate.preview_hint.text().startswith("已刷新"),
          f"预览会自动重算并且**停下来**：{generate.preview_hint.text()!r}")

    # 1e. a file-based force field needs no structure file: the atoms come from
    #     COORD_FILE_NAME, only the &CELL has to be typed in
    generate.structure_edit.clear()
    generate.load_structure()
    generate.w_cell.clear()
    generate.w_mm_parm.clear()
    generate.w_mm_coord.clear()
    generate._mm_autofilled = None
    generate._mm_detected_from = None
    pump(400)
    check("还差一个晶胞" in generate.preview.toPlainText(),
          "没有结构文件时明确说缺的是晶胞（不是让人去找结构文件）")
    check(generate.summary.text().startswith("等待晶胞"),
          f"摘要写「等待晶胞」：{generate.summary.text()!r}")
    generate.w_cell.setText("24.4,24.4,24.4")
    generate.w_mm_parm.setText("mix.prmtop")
    generate.w_mm_coord.setText("mixture.pdb")
    pump(500)
    text = gui_text()
    check("ABC [angstrom] 24.4" in text and "&COORD" not in text,
          "填好晶胞和文件后就能出预览（有 &CELL、没有 &COORD）")
    check("PARM_FILE_NAME mix.prmtop" in text
          and "CONN_FILE_NAME mix.prmtop" in text,
          "参数/拓扑照常写进去")
    check(generate.text == cli_text(
        ["new", "--task", "md", "--method", "fist", "--mm-route", "AMBER",
         "--mm-parm-file", "mix.prmtop", "--mm-coord-file", "mixture.pdb",
         "--cell", "24.4,24.4,24.4", "--timestep", "1.0", "--stdout"]),
          "没有结构文件的这条路，GUI 与 CLI 也逐字节一致")

    # 1f. the file names are spelled for the machine that *runs* the input: if
    #     the files sit next to the input, only the bare name is written (a
    #     Windows path would not exist on the Linux cluster)
    import tempfile as _tempfile

    run_dir = Path(_tempfile.mkdtemp(prefix="gui_mmpath_"))
    (run_dir / "mol.prmtop").write_text("x", encoding="utf-8")
    (run_dir / "mix.pdb").write_text("x", encoding="utf-8")
    generate.w_out.setText(str(run_dir / "run.inp"))
    generate.w_mm_parm.setText(str(run_dir / "mol.prmtop"))
    generate.w_mm_coord.setText(str(run_dir / "mix.pdb"))
    pump(500)
    text = gui_text()
    check("PARM_FILE_NAME mol.prmtop" in text
          and "COORD_FILE_NAME mix.pdb" in text,
          "输入和文件同目录时只写文件名（不带 E:/... 这种路径）")
    check(str(run_dir).replace("\\", "/") not in text,
          "生成的输入里不出现本机的绝对路径")
    check(generate.w_mm_path_style.currentData() == "name",
          "「文件路径写法」默认是「只写文件名」")
    # 「自动」只在文件真的与输入同目录时才缩写，文件在别处时它保留路径
    for _i in range(generate.w_mm_path_style.count()):
        if generate.w_mm_path_style.itemData(_i) == "auto":
            generate.w_mm_path_style.setCurrentIndex(_i)
    generate.w_out.setText(str(Path(_tempfile.mkdtemp(prefix="gui_other_")) / "run.inp"))
    pump(500)
    check(str(run_dir).replace("\\", "/") in gui_text(),
          "选「自动」且文件在别的目录时保留路径（不会静默改坏）")
    for _i in range(generate.w_mm_path_style.count()):
        if generate.w_mm_path_style.itemData(_i) == "name":
            generate.w_mm_path_style.setCurrentIndex(_i)
    pump(500)
    check("PARM_FILE_NAME mol.prmtop" in gui_text()
          and str(run_dir).replace("\\", "/") not in gui_text(),
          "选回「只写文件名」时又只剩文件名")
    generate.w_mm_path_style.setCurrentIndex(0)
    generate.w_out.clear()
    generate.w_cell.clear()
    generate.w_mm_parm.clear()
    generate.w_mm_coord.clear()
    generate.load_from_path(WATER)
    pump(400)
    generate.w_cell.clear()
    generate.w_mm_parm.clear()
    generate.w_mm_coord.clear()
    generate.load_from_path(WATER)
    pump(400)
    # 6b below flips the route, which must not leave the box checked
    for _i in range(generate.w_mm_route.count()):
        if generate.w_mm_route.itemData(_i) == "CHARMM":
            generate.w_mm_route.setCurrentIndex(_i)
    pump(400)
    check(not generate.w_mm_same_file.isChecked() and generate.w_mm_conn.isEnabled(),
          "切到 CHARMM 路线时自动取消勾选（PSF 和 .pot 是两个文件）")
    for _i in range(generate.w_mm_route.count()):
        if generate.w_mm_route.itemData(_i) == "AMBER":
            generate.w_mm_route.setCurrentIndex(_i)
    pump(300)
    generate.w_cell.setText("30,30,30")
    generate.w_mm_parm.setText("a.prmtop")
    # the box stays ticked: the CLI below gives no --mm-conn-file either
    generate.w_mm_coord.setText("a.inpcrd")
    generate.w_mm_ewald.setCurrentIndex(1)          # SPME
    generate.w_mm_ewald_gmax.setValue(64)
    generate.w_mm_o_spline.setValue(6)
    compare("FIST AMBER NVT（文件路线）",
            ["new", "--task", "md", "--structure", WATER, "--method", "fist",
             "--mm-route", "AMBER", "--mm-parm-file", "a.prmtop",
             "--mm-coord-file", "a.inpcrd",
             "--cell", "30,30,30", "--mm-ewald", "SPME",
             "--mm-ewald-gmax", "64", "--mm-o-spline", "6",
             "--timestep", "1.0", "--stdout"])

    # GMAX has no default in CP2K and EWALD/SPME demand it, so "自动" has to
    # produce a number: the tool estimates it from the cell and ALPHA
    generate.w_mm_ewald_gmax.setValue(0)
    pump(60)
    generate.refresh()
    text = generate.preview.toPlainText()
    gmax_lines = [l.strip() for l in text.splitlines() if l.strip().startswith("GMAX")]
    check(bool(gmax_lines),
          "GMAX 留「自动」时也会写出一个值（CP2K 对 GMAX 没有默认值）")
    check(gmax_lines and gmax_lines[0] == "GMAX 73 73 73",
          f"30 Å 立方 + 默认 ALPHA → {gmax_lines}（α=0.35、公式见 recipes."
          "suggest_ewald_gmax）")
    compare("GMAX 自动 == 命令行的默认估计",
            ["new", "--task", "md", "--structure", WATER, "--method", "fist",
             "--mm-route", "AMBER", "--mm-parm-file", "a.prmtop",
             "--mm-coord-file", "a.inpcrd",
             "--cell", "30,30,30", "--mm-ewald", "SPME",
             "--mm-o-spline", "6", "--timestep", "1.0", "--stdout"])
    generate.w_mm_ewald_gmax.setValue(64)
    pump(60)

    # 2. the 1-4 scaling override
    generate.w_mm_vdw14.setText("0.5")
    generate.w_mm_ei14.setText("0.5")               # PARM91
    compare("FIST 1-4 缩放显式覆盖（PARM91）",
            ["new", "--task", "md", "--structure", WATER, "--method", "fist",
             "--mm-route", "AMBER", "--mm-parm-file", "a.prmtop",
             "--mm-coord-file", "a.inpcrd",
             "--cell", "30,30,30", "--mm-ewald", "SPME",
             "--mm-ewald-gmax", "64", "--mm-o-spline", "6", "--timestep", "1.0",
             "--mm-vdw-scale14", "0.5", "--mm-ei-scale14", "0.5", "--stdout"])
    generate.w_mm_vdw14.clear()
    generate.w_mm_ei14.clear()

    # 3. pair potential with mixing + an explicit RCUT
    reset_gui()
    generate.load_from_path(WATER)
    set_task("md")
    set_method("FIST")
    set_route("PAIR")
    generate.w_vacuum.setValue(0.0)          # the CLI case below gives no vacuum
    generate.w_cell.setText("30,30,30")
    set_mm_table([["O", "78.198", "3.166"], ["H", "0.0", "3.30523"]])
    generate.w_mm_lj_rcut.setValue(11.4)
    compare("FIST 对势路线（ε/σ + Lorentz-Berthelot 混合）",
            ["new", "--task", "md", "--structure", WATER, "--method", "fist",
             "--mm-route", "PAIR", "--lj", "O:78.198:3.166",
             "--lj", "H:0.0:3.30523", "--lj-rcut", "11.4",
             "--cell", "30,30,30", "--timestep", "1.0", "--stdout"])
    check("LENNARD-JONES" in gui_text()
          and ("ATOMS H O" in gui_text() or "ATOMS O H" in gui_text()),
          "对势表里生成了交叉对（O-H）")

    # 4. an explicit cross pair overrides the mixing rule
    set_mm_table([["O", "78.198", "3.166"], ["H", "0.0", "3.30523"],
                  ["O-H", "5.0", "3.5"]])
    explicit = [p for p in generate.collect_spec().mm_lj
                if p.atoms == ["O", "H"]]
    check(len(explicit) == 1 and explicit[0].epsilon == 5.0,
          "显式写的 O-H 对覆盖了混合结果")

    # 5. the mixed system: CONNECTIVITY MOL_SET
    reset_gui()
    generate.load_from_path(WATER)
    set_task("md")
    set_method("FIST")
    generate.w_cell.setText("40,40,40")
    generate.w_mm_parm.setText("mixture.prmtop")
    generate.w_mm_coord.setText("mixture.inpcrd")
    generate.w_mm_molecules.setRowCount(0)
    generate.w_mm_molecules.add_row(["AMBER", "water.prmtop", "500"])
    generate.w_mm_molecules.add_row(["AMBER", "ion.prmtop", "20"])
    pump(60)
    compare("FIST 混合体系（MOL_SET，每分子一个拓扑）",
            ["new", "--task", "md", "--structure", WATER, "--method", "fist",
             "--mm-route", "AMBER", "--mm-parm-file", "mixture.prmtop",
             "--mm-coord-file", "mixture.inpcrd",
             "--mm-molecule", "AMBER:water.prmtop:500",
             "--mm-molecule", "AMBER:ion.prmtop:20",
             "--cell", "40,40,40", "--timestep", "1.0", "--stdout"])
    check("CONNECTIVITY MOL_SET" in gui_text()
          and generate.h_mm_conn.isVisible() is False,
          "MOL_SET 路线下顶层的「拓扑文件」收起（每个 &MOLECULE 自带）")

    # the tables the user types into have to *be* editable, and the molecule
    # order must survive (it has to match the order in the coordinate file)
    from PySide6.QtWidgets import QAbstractItemView, QComboBox

    for name, label in (("w_mm_molecules", "混合体系表"),
                        ("w_mm_table", "ε/σ 表"),
                        ("w_mm_charges", "&CHARGE 表")):
        table = getattr(generate, name)
        check(table.editTriggers() != QAbstractItemView.NoEditTriggers,
              f"{label}可以双击编辑")
        check(not table.isSortingEnabled(),
              f"{label}不排序（否则行序会被点表头改掉）")
    check(isinstance(generate.w_mm_molecules.itemDelegateForColumn(0),
                     type(generate.w_mm_molecules.itemDelegateForColumn(0)))
          and generate.w_mm_molecules.itemDelegateForColumn(0).createEditor(
              generate.w_mm_molecules, None, None).__class__ is QComboBox,
          "「连接性」列是用下拉框选的（取值就是 CP2K 的 CONN_FILE_FORMAT）")
    # ... and typing into a cell really reaches the Spec
    generate.w_mm_molecules.item(0, 2).setText("123")
    pump(200)
    check(generate.collect_spec().mm_molecules[0].nmol == 123,
          "单元格里改的 NMOL 会进到输入文件")
    generate.w_mm_molecules.item(0, 2).setText("500")
    pump(200)

    # 6b. the "check coordinates against topology" button, on real fixtures
    import tempfile
    from pathlib import Path as _Path

    mm_dir = _Path(tempfile.mkdtemp(prefix="gui_mmcheck_"))
    (mm_dir / "mol.prmtop").write_text(
        "%VERSION  VERSION_STAMP = V0001.000\n"
        "%FLAG POINTERS\n%FORMAT(10I8)\n"
        "       2       1       0       0       0       0       0       0       0       0\n"
        "%FLAG ATOM_NAME\n%FORMAT(20a4)\nC1  H1\n", encoding="utf-8")
    (mm_dir / "two.pdb").write_text(
        "ATOM      1  C1  ALA A   1       0.000   0.000   0.000  1.00  0.00           C\n"
        "ATOM      2  H1  ALA A   1       1.000   0.000   0.000  1.00  0.00           H\n"
        "END\n", encoding="utf-8")
    generate.w_mm_molecules.setRowCount(0)
    generate.w_mm_conn.setText(str(mm_dir / "mol.prmtop"))
    generate.w_mm_coord.setText(str(mm_dir / "two.pdb"))
    generate.w_mm_coord_format.setCurrentText("PDB")
    pump(200)
    generate._check_mixture()
    pump(150)
    hint = generate.mm_molecules_hint.text()
    check("✅" in hint and "对得上" in hint,
          f"「检查坐标与拓扑」按钮：对得上时给出 ✅（{hint.splitlines()[-1][:60]}）")
    generate.w_mm_molecules.add_row(["AMBER", str(mm_dir / "mol.prmtop"), "3"])
    pump(250)
    generate._check_mixture()
    pump(150)
    hint = generate.mm_molecules_hint.text()
    check("⚠" in hint and "原子数对不上" in hint,
          "NMOL 填错时按钮报 ⚠ 并说明差多少")
    check(generate.mixture_result is not None
          and generate.w_mm_molecules.item(0, 2).text() == "3"
          and generate.mixture_result[0] == generate._mixture_fingerprint(),
          "检查结果缓存下来、由提示行渲染（不会被刷新覆盖）")
    # ... and it goes away as soon as the inputs change
    generate.w_mm_molecules.item(0, 2).setText("4")
    pump(250)
    check(generate.mixture_result[0] != generate._mixture_fingerprint()
          and "原子数对不上" not in generate.mm_molecules_hint.text(),
          "改过输入后，旧的检查结果不再显示（不会拿旧结论糊弄人）")
    generate.w_mm_molecules.setRowCount(0)
    generate.w_mm_conn.clear()
    generate.w_mm_coord.clear()
    pump(150)

    # 6. the in-input force field, read from the shipped example
    reset_gui()
    generate.load_from_path(WATER)
    set_task("md")
    set_method("FIST")
    set_route("IN_INPUT")
    generate.w_vacuum.setValue(0.0)
    generate.w_cell.setText("9.865,9.865,9.865")
    generate.w_mm_ff.setText(FF_INCLUDE)
    set_mm_table([["O", "78.198", "3.166"]])
    generate.w_mm_charges.setRowCount(0)
    generate.w_mm_charges.add_row(["O", "-0.8476"])
    generate.w_mm_charges.add_row(["H", "0.4238"])
    generate.w_mm_ewald.setCurrentIndex(1)
    generate.w_mm_ewald_gmax.setValue(20)
    pump(60)
    compare("FIST 参数写在输入里（内联力场文件）",
            ["new", "--task", "md", "--structure", WATER, "--method", "fist",
             "--mm-route", "IN_INPUT",
             "--mm-ff-file", FF_INCLUDE,
             "--lj", "O:78.198:3.166",
             "--mm-charge", "O:-0.8476", "--mm-charge", "H:0.4238",
             "--cell", "9.865,9.865,9.865", "--mm-ewald", "SPME",
             "--mm-ewald-gmax", "20", "--timestep", "1.0", "--stdout"])
    text = gui_text()
    check("&BOND" in text and "THETA0" in text and "&LENNARD-JONES" in text,
          "力场文件的内容被展开进 &FORCEFIELD")

    # 7. band/PDOS are refused with a warning on the classical route
    spec = generate.collect_spec()
    spec.task = "band"
    from cp2kbuild.recipes import build as build_inputs
    from cp2kbuild.datafiles import load_index

    index = load_index(None)
    problems = build_inputs(spec, index).warnings
    check(any("本征值" in w for w in problems),
          f"FIST + 能带会被点名拒绝（{str(problems)[:80]}）")

    # 8. reset puts the method back to DFT
    generate.reset_defaults()
    pump(60)
    check(generate.w_method.currentData() == "QUICKSTEP"
          and generate.method_box.isVisible()
          and not generate.classical_box.isVisible(),
          "「恢复默认」把方法切回 DFT 并重新显示泛函页")
    check(generate.collect_spec().mm_lj == []
          and generate.collect_spec().mm_molecules == [],
          "「恢复默认」清空了 ε/σ 表和分子表")

    # ------------------------------------------------------------------ #
    # the SCF route blocks: same text as the CLI, and the coupled fields must
    # switch on/off with the route and the sub-parameters
    # ------------------------------------------------------------------ #
    print("\n== SCF 路线细节（OT / 对角化）==")

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    set_scf("OT")
    generate.w_ot_minimizer.setCurrentText("CG")
    generate.w_ot_preconditioner.setCurrentText("FULL_ALL")
    generate.w_ot_energy_gap.setText("0.002")
    generate.w_ot_linesearch.setCurrentText("GOLD")
    generate.w_ot_stepsize.setText("0.2")
    generate.w_ot_safe_diis.setCurrentIndex(2)      # F
    generate.w_ot_rotation.setCurrentIndex(1)       # T
    generate.w_outer_optimizer.setCurrentText("DIIS")
    compare("OT 子参数（含 SAFE_DIIS F / ROTATION T）",
            ["new", "--task", "energy", "--structure", SILICON, "--scf", "OT",
             "--ot-minimizer", "CG", "--ot-preconditioner", "FULL_ALL",
             "--ot-energy-gap", "0.002", "--ot-linesearch", "GOLD",
             "--ot-stepsize", "0.2", "--no-ot-safe-diis", "--ot-rotation",
             "--outer-optimizer", "DIIS", "--stdout"])

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    set_scf("DIAGONALIZATION")
    generate.w_diag_algorithm.setCurrentText("DAVIDSON")
    generate.w_level_shift.setText("0.1")
    generate.w_mixing_method.setCurrentText("PULAY_MIXING")
    generate.w_mixing_alpha.setValue(0.2)
    generate.w_mixing_nbuffer.setValue(12)
    compare("对角化子参数 + 混合方法",
            ["new", "--task", "energy", "--structure", SILICON,
             "--scf", "DIAGONALIZATION", "--diag-algorithm", "DAVIDSON",
             "--level-shift", "0.1", "--mixing-method", "PULAY_MIXING",
             "--mixing-alpha", "0.2", "--mixing-nbuffer", "12", "--stdout"])

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.w_smearing.setChecked(True)
    generate.w_smear_method.setCurrentText("METHFESSEL_PAXTON")
    generate.w_electronic_temperature.setValue(500.0)
    compare("SMEAR 方法（自动切到对角化）",
            ["new", "--task", "energy", "--structure", SILICON, "--smearing",
             "--smear-method", "METHFESSEL_PAXTON",
             "--electronic-temperature", "500", "--stdout"])

    # ---- the reactive part: show/hide and grey out what does not apply ----
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    set_scf("auto")
    check("OT" in generate.route_label.text(),
          f"路线提示写着实际路线：{generate.route_label.text()!r}")
    # the two sub-boxes live in the foldable 「3.2 SCF 路线细节」 block, so open it
    # and ask *that* what it shows (the route summary stays outside the fold)
    details = generate.scf_details_box
    details.set_expanded(True)
    check(generate.ot_box.isVisibleTo(details)
          and not generate.diag_box.isVisibleTo(details),
          "「自动」+ energy → OT 框在、对角化框不在")
    check(not generate.w_added_mos.isEnabled(),
          "OT 路线下「空轨道数」被灰掉（ADDED_MOS 只在 OT 关掉时被读）")
    check(not generate.w_mixing_alpha.isEnabled(),
          "OT 路线下混合参数被灰掉（&MIXING 不会被写）")

    set_scf("DIAGONALIZATION")
    check(generate.diag_box.isVisibleTo(details)
          and not generate.ot_box.isVisibleTo(details),
          "选 DIAGONALIZATION → 对角化框在、OT 框不在")
    check(generate.w_added_mos.isEnabled() and generate.w_mixing_alpha.isEnabled(),
          "对角化路线下「空轨道数」和混合参数恢复可编辑")
    generate.w_smearing.setChecked(True)
    check(not generate.w_mixing_alpha.isEnabled(),
          "对角化 + SMEAR → 混合参数再次被灰掉")
    generate.w_smearing.setChecked(False)

    set_task("band")
    check(generate.diag_box.isVisibleTo(details),
          "「自动」+ 能带 → 自动判成对角化（要未占据轨道）")
    set_task("energy")
    set_scf("OT")
    generate.w_ot_preconditioner.setCurrentText("FULL_KINETIC")
    check(not generate.w_ot_energy_gap.isEnabled(),
          "PRECONDITIONER=FULL_KINETIC → ENERGY_GAP 灰掉（它不看这个值）")
    generate.w_ot_preconditioner.setCurrentText("FULL_ALL")
    check(generate.w_ot_energy_gap.isEnabled(),
          "PRECONDITIONER=FULL_ALL → ENERGY_GAP 恢复可编辑")
    generate.w_ot_minimizer.setCurrentText("CG")
    check(not generate.w_ot_safe_diis.isEnabled(),
          "MINIMIZER=CG → SAFE_DIIS 灰掉（没有 DIIS 步要过滤）")
    generate.w_ot_minimizer.setCurrentText("DIIS")
    generate.w_ot_linesearch.setCurrentText("NONE")
    check(not generate.w_ot_stepsize.isEnabled(),
          "LINESEARCH=NONE → STEPSIZE 灰掉（不做线搜索）")
    generate.w_ot_linesearch.setCurrentText("2PNT")
    check(generate.w_ot_stepsize.isEnabled(), "LINESEARCH=2PNT → STEPSIZE 可用")

    # every value offered by these combos has to exist in the manual database,
    # otherwise a version bump would leave a value CP2K rejects
    import cp2kbuild.gui as gui_module

    db = window.keyword_tab.db
    if db is not None:
        pairs = [("FORCE_EVAL/DFT/SCF/OT", "MINIMIZER", gui_module.OT_MINIMIZERS),
                 ("FORCE_EVAL/DFT/SCF/OT", "PRECONDITIONER", gui_module.OT_PRECONDITIONERS),
                 ("FORCE_EVAL/DFT/SCF/OT", "ALGORITHM", gui_module.OT_ALGORITHMS),
                 ("FORCE_EVAL/DFT/SCF/OT", "LINESEARCH", gui_module.OT_LINESEARCHES),
                 ("FORCE_EVAL/DFT/SCF/DIAGONALIZATION", "ALGORITHM",
                  gui_module.DIAG_ALGORITHMS),
                 ("FORCE_EVAL/DFT/SCF/MIXING", "METHOD", gui_module.MIXING_METHODS),
                 ("FORCE_EVAL/DFT/SCF/SMEAR", "METHOD", gui_module.SMEAR_METHODS),
                 ("FORCE_EVAL/DFT/SCF/OUTER_SCF", "OPTIMIZER",
                  [v for _, v in gui_module.OUTER_OPTIMIZERS if v])]
        wrong: list[str] = []
        for path, keyword, values in pairs:
            allowed = {v.upper() for v in db.valid_values(path, keyword)}
            wrong += [f"{keyword} {v}" for v in values if v.upper() not in allowed]
        check(not wrong, f"下拉框里的取值全部来自手册（多出来的：{wrong}）")

    # ------------------------------------------------------------------ #
    # Poisson solver: the combo follows the cell periodicity, and an invalid
    # combination must be caught rather than written
    # ------------------------------------------------------------------ #
    print("\n== Poisson 求解器 × 周期性 ==")

    def poisson_items() -> list[str | None]:
        return [generate.w_poisson.itemData(i)
                for i in range(generate.w_poisson.count())]

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    generate._poisson_changed()
    check("PERIODIC" in poisson_items() and "MT" not in poisson_items(),
          f"三维周期：只给 3D 可用的求解器（{poisson_items()}）")
    compare("Poisson 自动（XYZ 不写 &POISSON）",
            ["new", "--task", "energy", "--structure", SILICON, "--stdout"])
    generate.w_poisson.setCurrentIndex(generate.w_poisson.findData("WAVELET"))
    compare("Poisson 显式 WAVELET",
            ["new", "--task", "energy", "--structure", SILICON,
             "--poisson-solver", "WAVELET", "--stdout"])

    reset_gui()                      # back to auto
    generate.load_from_path(WATER)
    set_task("energy")
    generate._poisson_changed()
    items = poisson_items()
    check("MT" in items and "MULTIPOLE" in items and "PERIODIC" not in items,
          f"孤立分子（真空层）：给出 0D 可用的求解器（{items}）")
    compare("Poisson 自动（分子 → MT）",
            ["new", "--task", "energy", "--structure", WATER, "--vacuum", "12",
             "--stdout"])

    generate.w_periodic.setCurrentIndex(2)          # XY
    generate._poisson_changed()
    items = poisson_items()
    check("PERIODIC" not in items and "MULTIPOLE" not in items,
          f"表面板（XY）：PERIODIC/MULTIPOLE 从列表里消失（{items}）")
    generate.w_poisson.setCurrentIndex(generate.w_poisson.findData("MT"))
    compare("Poisson 表面板选 MT",
            ["new", "--task", "energy", "--structure", WATER, "--vacuum", "12",
             "--periodic", "XY", "--poisson-solver", "MT", "--stdout"])
    check("MT" in generate.poisson_hint.text(),
          f"提示里写明了当前组合的依据：{generate.poisson_hint.text()!r}")

    # the invariant behind the reactive list: whatever the periodicity, every
    # offered solver is one the manual allows for it
    offenders: list[str] = []
    for index, periodicity in ((1, "XYZ"), (2, "XY"), (5, "X"), (8, "NONE")):
        generate.w_periodic.setCurrentIndex(index)
        generate._poisson_changed()
        allowed = set(poisson_solvers_for(periodicity))
        offered = {item for item in poisson_items() if item}
        if not offered <= allowed:
            offenders.append(f"{periodicity}: {sorted(offered - allowed)}")
    check(not offenders,
          f"每种周期性下都只提供手册允许的求解器（越界的：{offenders}）")

    # ------------------------------------------------------------------ #
    # the「恢复默认」button must land exactly on the CLI defaults, for every
    # task.  This is the check that would have caught SCF_GUESS quietly going
    # back to ATOMIC (the button had its own copy of the defaults).
    # ------------------------------------------------------------------ #

    print("\n== 「恢复默认」按钮 ==")
    for task, structure in [("energy", WATER), ("geo_opt", WATER),
                            ("cell_opt", SILICON), ("md", WATER), ("freq", WATER),
                            ("band", SILICON), ("pdos", SILICON), ("neb", WATER)]:
        generate.load_from_path(structure)
        set_task(task)
        # move a few knobs off their defaults first, so the reset has work to do
        generate.w_scf_guess.setCurrentText("ATOMIC")
        generate.w_wfn_print.setChecked(False)
        generate.w_motion_restart.setChecked(False)
        generate.w_force_uks.setChecked(True)
        generate.w_charge.setValue(2)
        generate.w_cutoff_auto.setChecked(False)
        generate.w_project.setText("not_the_default")
        replicas: list[str] = []
        if task == "neb":                    # NEB needs its images on both sides
            replicas = [str(ROOT / "examples" / f"water_{i}.xyz") for i in range(1, 6)]
            generate.w_replicas.clear()
            for path in replicas:
                generate.w_replicas.addItem(path)
        generate.reset_defaults()
        args = ["new", "--task", task, "--structure", structure]
        if structure == WATER:
            args += ["--vacuum", "12"]
        for path in replicas:
            args += ["--replica", path]
        args.append("--stdout")
        compare(f"「恢复默认」{task}", args)

    # ------------------------------------------------------------------ #
    # the preview must refresh on its own: nobody calls refresh() here, the
    # debounce timer of the tab has to do it
    # ------------------------------------------------------------------ #
    print("\n== 实时预览：改控件后自动刷新 ==")
    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.refresh()
    pump(400)                                   # drain anything still pending

    def live_compare(label: str, cli_args: list[str]) -> None:
        pump(400)                               # let the 120 ms timer fire
        expected = cli_text(cli_args)
        actual = generate.preview.toPlainText()
        if expected == actual:
            check(True, f"{label}：没调 refresh() 也自动刷新，且 == 命令行")
        else:
            check(False, f"{label}：预览没有随控件自动刷新")

    settled = generate.preview.toPlainText()
    generate.w_cutoff_auto.setChecked(False)
    generate.w_cutoff.setValue(999.0)
    check(generate.preview.toPlainText() == settled,
          "去抖动：计时器没到期之前预览还没重算")
    live_compare("改 cutoff（小数框）",
                 ["new", "--task", "energy", "--structure", SILICON,
                  "--cutoff", "999", "--stdout"])

    generate.w_max_scf.setValue(17)
    live_compare("改 MAX_SCF（整数框）",
                 ["new", "--task", "energy", "--structure", SILICON,
                  "--cutoff", "999", "--max-scf", "17", "--stdout"])

    set_dispersion("d3bj")
    live_compare("改色散校正（下拉框）",
                 ["new", "--task", "energy", "--structure", SILICON,
                  "--cutoff", "999", "--max-scf", "17", "--dispersion", "d3bj",
                  "--stdout"])

    generate.w_print_forces.setChecked(False)
    live_compare("改「打印受力」（勾选框）",
                 ["new", "--task", "energy", "--structure", SILICON,
                  "--cutoff", "999", "--max-scf", "17", "--dispersion", "d3bj",
                  "--no-forces", "--stdout"])

    generate.w_project.setText("live_preview_check")
    live_compare("改工程名（文本框）",
                 ["new", "--task", "energy", "--structure", SILICON,
                  "--cutoff", "999", "--max-scf", "17", "--dispersion", "d3bj",
                  "--no-forces", "--project", "live_preview_check", "--stdout"])

    # the preview and the file written by 「保存」 must always agree
    check(generate.preview.toPlainText() == generate.text,
          "预览文本就是「保存」会写出去的那份文本")
    check(generate.preview_hint.isVisible(),
          "预览区标出了「改动左侧控件会自动重算」")

    # a live refresh must not yank the reader back to the top
    bar = generate.preview.verticalScrollBar()
    if bar.maximum() > 0:
        bar.setValue(bar.maximum() // 2)
        parked = bar.value()
        generate.w_max_scf.setValue(23)
        pump(400)
        check(bar.value() == parked,
              f"自动重算后预览的滚动位置不变（{parked} → {bar.value()}）")
    else:
        check(False, "预览区没有滚动条，没法检查滚动位置是否被重置")

    # ------------------------------------------------------------------ #
    # 升温 / 降温：勾选框算出因子，界面与命令行（--temperature-ramp）必须一致
    # ------------------------------------------------------------------ #
    print("\n== 升温 / 降温（&MD 的退火开关） ==")
    from cp2kbuild.recipes import plan_ramp

    reset_gui()
    generate.load_from_path(WATER)
    set_task("md")
    generate.w_ensemble.setCurrentText("LANGEVIN")
    generate.w_temperature.setValue(300.0)
    generate.w_timestep.setValue(0.5)
    generate.w_steps.setValue(20000)
    generate.w_ramp_target.setValue(600.0)
    ramp = plan_ramp(300.0, 600.0, 20000, 0.5, "LANGEVIN")
    check(generate.w_ramp.isEnabled() and generate.w_ramp_target.isVisible(),
          "LANGEVIN 系综下「升温 / 降温」可用")
    generate.w_ramp.setChecked(True)
    check(generate.w_temperature_annealing.value() == ramp.factor
          and generate.w_annealing.value() == 0.0,
          f"勾上升温后填的是 TEMPERATURE_ANNEALING 因子："
          f"{generate.w_temperature_annealing.value():.12f}")
    check("TEMPERATURE_ANNEALING" in generate.ramp_hint.text()
          and "600 K" in generate.ramp_hint.text()
          and "K/ps" in generate.ramp_hint.text(),
          f"旁边说明写了机制、终点与速率：{generate.ramp_hint.text()[:60]}…")
    live_compare("LANGEVIN 升温 300→600 K",
                 ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
                  "--ensemble", "LANGEVIN", "--temperature-ramp", "300:600",
                  "--steps", "20000", "--timestep", "0.5", "--stdout"])

    generate.w_ensemble.setCurrentText("NVE")
    velocity = plan_ramp(300.0, 600.0, 20000, 0.5, "NVE")
    check(generate.w_ramp.isChecked()
          and generate.w_annealing.value() == velocity.factor
          and generate.w_temperature_annealing.value() == 0.0,
          "换成 NVE 后自动改用 ANNEALING（温度按平方走），另一个因子清零")
    check("不是恒温器" in generate.ramp_hint.text(),
          "NVE 那条路会说明「速度缩放不是恒温器」")
    live_compare("NVE 升温 300→600 K",
                 ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
                  "--ensemble", "NVE", "--temperature-ramp", "300:600",
                  "--steps", "20000", "--timestep", "0.5", "--stdout"])

    generate.w_ensemble.setCurrentText("NVT")
    check(not generate.w_ramp.isChecked() and not generate.w_ramp.isEnabled(),
          "NVT 下勾选框变灰并自动取消（两条机制都不能用）")
    check("不能升降温" in generate.ramp_hint.text()
          and generate.w_temperature_annealing.value() == 0.0
          and generate.w_annealing.value() == 0.0,
          "NVT 下说清为什么不行，并把两个因子清掉（留着会中止运行）")

    # hand-written factors and the Langevin friction are normal knobs too
    generate.w_ensemble.setCurrentText("LANGEVIN")
    generate.w_temperature_annealing.setValue(1.0005)
    generate.w_langevin_gamma.setValue(0.05)
    live_compare("手填 TEMPERATURE_ANNEALING + GAMMA",
                 ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
                  "--ensemble", "LANGEVIN", "--temperature", "300",
                  "--steps", "20000", "--timestep", "0.5",
                  "--temperature-annealing", "1.0005",
                  "--langevin-gamma", "0.05", "--stdout"])

    # ... and the ramp can continue across a restart
    generate.w_ext_restart.setText("prev-1.restart")
    generate.w_restart_temperature_annealing.setChecked(True)
    live_compare("接力时接着斜坡（RESTART_TEMPERATURE_ANNEALING）",
                 ["new", "--task", "md", "--structure", WATER, "--vacuum", "12",
                  "--ensemble", "LANGEVIN", "--temperature", "300",
                  "--steps", "20000", "--timestep", "0.5",
                  "--temperature-annealing", "1.0005",
                  "--langevin-gamma", "0.05", "--restart", "prev-1.restart",
                  "--restart-temperature-annealing", "--stdout"])
    reset_gui()

    # ------------------------------------------------------------------ #
    # 「保存输入文件」：第 n 次都要弹对话框（以前第二次直接覆盖那个文件），
    # 只是对话框的起点继承上一次的路径
    # ------------------------------------------------------------------ #
    print("\n== 保存输入文件：每次都问，路径继承上一次 ==")
    import tempfile as _tempfile

    from cp2kbuild import gui as gui_module

    save_dir = Path(_tempfile.mkdtemp(prefix="gui_save_"))
    calls: list[str] = []
    real_dialog = gui_module.QFileDialog.getSaveFileName
    settings_before = {key: generate.settings.value(key, None)
                       for key in ("last_output_dir", "last_dir")}

    def fake_dialog(parent, title, start, *args, **kwargs):
        calls.append(str(start))
        return (str(save_dir / f"run{len(calls)}.inp"), "CP2K 输入 (*.inp)")

    reset_gui()
    generate.load_from_path(SILICON)
    set_task("energy")
    generate.refresh()
    generate.w_out.clear()
    gui_module.QFileDialog.getSaveFileName = staticmethod(fake_dialog)
    try:
        generate.save()
        first = generate.w_out.text()
        generate.save()
        second = generate.w_out.text()
        generate.save()
        third = generate.w_out.text()
    finally:
        gui_module.QFileDialog.getSaveFileName = real_dialog

    check(len(calls) == 3, f"点三次「保存」弹三次对话框（实际 {len(calls)} 次）")
    check(all(Path(p).is_file() for p in (first, second, third))
          and [Path(p).name for p in (first, second, third)]
          == ["run1.inp", "run2.inp", "run3.inp"],
          "三次都写到了各自选中的文件（不再有静默覆盖）")
    check(calls[1] == first and calls[2] == second,
          f"对话框的起点是上一次保存的路径：{calls}")
    check(generate.text == Path(third).read_text(encoding="utf-8"),
          "写出去的就是预览里那一份（LF 换行）")

    # cancelling the dialog must not write anything and must not move the path
    gui_module.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
    try:
        before = generate.w_out.text()
        generate.save()
    finally:
        gui_module.QFileDialog.getSaveFileName = real_dialog
        for key, value in settings_before.items():
            if value is None:
                generate.settings.remove(key)
            else:
                generate.settings.setValue(key, value)
    check(generate.w_out.text() == before and len(calls) == 3,
          "在对话框里点取消：不写文件、路径也不变")

    # ---- saving *into the directory the classical files live in* must shorten
    # ---- their names on disk too, not only in the preview ----------------- #
    # (the text used to be built for the *old* output directory, so this saved a
    #  file full of Windows paths while the preview showed bare names)
    mm_dir = Path(_tempfile.mkdtemp(prefix="gui_save_mm_"))
    (mm_dir / "mol.prmtop").write_text("x", encoding="utf-8")
    (mm_dir / "mix.pdb").write_text("x", encoding="utf-8")
    reset_gui()
    set_method("FIST")
    set_route("AMBER")
    generate.w_cell.setText("24.4,24.4,24.4")
    generate.w_mm_parm.setText(str(mm_dir / "mol.prmtop"))
    generate.w_mm_coord.setText(str(mm_dir / "mix.pdb"))
    for _i in range(generate.w_mm_path_style.count()):
        if generate.w_mm_path_style.itemData(_i) == "auto":
            generate.w_mm_path_style.setCurrentIndex(_i)
    generate.w_out.clear()          # output dir = cwd: "auto" keeps the full path
    pump(400)
    check(str(mm_dir).replace("\\", "/") in generate.text,
          "保存前：输出目录在别处，所以「自动」写的是绝对路径")
    target = mm_dir / "run.inp"
    gui_module.QFileDialog.getSaveFileName = staticmethod(
        lambda *a, **k: (str(target), "CP2K 输入 (*.inp)"))
    try:
        generate.save()
    finally:
        gui_module.QFileDialog.getSaveFileName = real_dialog
    on_disk = target.read_text(encoding="utf-8")
    check(on_disk == generate.text,
          "存到力场文件所在目录后，磁盘上的文件与预览逐字节一致")
    check("PARM_FILE_NAME mol.prmtop" in on_disk
          and str(mm_dir).replace("\\", "/") not in on_disk,
          "存过去之后「自动」真的把文件名缩成了裸名字")
    generate.w_mm_path_style.setCurrentIndex(0)
    reset_gui()

    # ------------------------------------------------------------------ #
    # ------------------------------------------------------------------ #
    # the cutoff tab writes CP2K's own convergence script set; the text must be
    # exactly what the CLI writes (same code, two front ends)
    # ------------------------------------------------------------------ #
    print("== 收敛测试页：官方 CUTOFF/REL_CUTOFF 脚本 ==")
    import shutil
    import tempfile

    from cp2kbuild.cli import main as cli_main

    scan = window.scan_tab
    # no modal dialogs in a headless run: failures are recorded and shown instead
    scan.silent = True
    work = Path(tempfile.mkdtemp(prefix="gui_cutoff_"))
    # ``side_files`` copies every ``*.inc`` sitting next to the input, so the
    # fixtures that must not see each other get their own directory
    src_dir = work / "src"
    src_dir.mkdir()

    def tree(root: Path) -> dict[str, bytes]:
        return {str(path.relative_to(root)): path.read_bytes()
                for path in sorted(root.rglob("*")) if path.is_file()}

    def compare_tree(label: str, gui_dir: Path, cli_dir: Path) -> None:
        mine, theirs = tree(gui_dir), tree(cli_dir)
        if mine == theirs:
            check(True, f"{label}: 生成的文件与 CLI 逐字节一致（{len(mine)} 个）")
            return
        only_gui = sorted(set(mine) - set(theirs))
        only_cli = sorted(set(theirs) - set(mine))
        differing = sorted(name for name in set(mine) & set(theirs)
                           if mine[name] != theirs[name])
        check(False, f"{label}: 与 CLI 不一致（多 {only_gui[:3]} / 少 {only_cli[:3]} / "
                     f"内容不同 {differing[:3]}）")

    def run_cli(args: list[str]) -> None:
        buffer, saved = io.StringIO(), sys.stdout
        sys.stdout = buffer
        try:
            code = cli_main(args)
        finally:
            sys.stdout = saved
        if code != 0:
            raise RuntimeError(f"CLI {args} exited with {code}")

    def set_source(mode: str) -> None:
        """Pick a template channel by its data value, then refresh the hint."""
        for i in range(scan.w_source.count()):
            if scan.w_source.itemData(i) == mode:
                scan.w_source.setCurrentIndex(i)
                scan._source_changed()
                return
        raise AssertionError(f"no scan source mode {mode!r}")

    # the official defaults are what the tab starts with (the tutorial's own
    # `cutoffs=` / `rel_cutoffs=` lines, ten values each)
    check(scan.w_cutoffs.text() == "50,100,150,200,250,300,350,400,450,500",
          f"第 1 步默认是官方脚本的 CUTOFF 范围：{scan.w_cutoffs.text()}")
    check(scan.w_rel_cutoffs.text() == "10,20,30,40,50,60,70,80,90,100",
          f"第 2 步默认是官方脚本的 REL_CUTOFF 范围：{scan.w_rel_cutoffs.text()}")
    check(scan.w_rel_cutoff.value() == 60.0, "第 1 步固定 REL_CUTOFF = 60 Ry")
    check(not scan.w_fixed_cutoff.text(), "第 2 步的 CUTOFF 默认留空")
    check([scan.w_source.itemData(i) for i in range(scan.w_source.count())]
          == ["structure", "input"],
          "来源下拉框就是两条通道：生成页参数 / 已有输入")
    check(scan.w_source.currentData() == "structure",
          "默认走「用生成输入页此刻的参数」（不用先准备文件）")

    # ---- A. an input that already has the placeholders: byte-identical ------ #
    tutorial = work / "system.inp"
    (work / "force.inc").write_text(
        "&FORCE_EVAL\n   METHOD QuickStep\n   &DFT\n"
        "      BASIS_SET_FILE_NAME  BASIS_MOLOPT\n"
        "      POTENTIAL_FILE_NAME  GTH_POTENTIALS\n      &MGRID\n"
        "         CUTOFF ${CUTOFF}\n         REL_CUTOFF ${REL_CUTOFF}\n"
        "      &END MGRID\n      &SCF\n         SCF_GUESS ATOMIC\n"
        "         MAX_SCF 1\n         IGNORE_CONVERGENCE_FAILURE TRUE\n"
        "      &END SCF\n   &END DFT\n&END FORCE_EVAL\n", encoding="utf-8")
    tutorial_bytes = ("@SET RTYPE ENERGY\n@SET CUTOFF LT_cutoff\n"
                      "@SET REL_CUTOFF LT_rel_cutoff\n&GLOBAL\n"
                      "   RUN_TYPE ${RTYPE}\n&END GLOBAL\n@INCLUDE force.inc\n"
                      ).encode("utf-8")
    tutorial.write_bytes(tutorial_bytes)

    set_source("input")
    scan.w_source_file.setText(str(tutorial))
    scan.w_cutoffs.setText("50,100")
    scan.w_rel_cutoffs.setText("10,20")
    scan.w_fixed_cutoff.setText("100")
    scan.w_scan_outdir.setText(str(work / "gui_a"))
    check(scan.run_scan() == 0, "从一份带占位符的输入生成：不报错")
    check(sorted(path.name for path in (work / "gui_a").iterdir()) ==
          ["cutoff_analyse.sh", "cutoff_inputs.sh", "cutoff_run.sh", "force.inc",
           "rel_cutoff_analyse.sh", "rel_cutoff_inputs.sh", "rel_cutoff_run.sh",
           "template.inp"],
          "生成的是官方那 7 个文件（外加 @INCLUDE 的文件）")
    check((work / "gui_a" / "template.inp").read_bytes() == tutorial_bytes,
          "占位符已经在输入里时 template.inp 与它逐字节相同")
    run_cli(["scan", "--input", str(tutorial), "--outdir", str(work / "cli_a"),
             "--cutoffs", "50,100", "--rel-cutoffs", "10,20",
             "--fixed-cutoff", "100"])
    compare_tree("带占位符的输入", work / "gui_a", work / "cli_a")

    # ---- B. a literal input: the tab plants the placeholders ---------------- #
    # and its RUN_TYPE is forced to ENERGY - a grid scan compares total energies,
    # so it may not inherit a GEO_OPT from the file it was handed
    literal = src_dir / "literal.inp"
    literal.write_text(
        "&GLOBAL\n   RUN_TYPE GEO_OPT\n&END GLOBAL\n&FORCE_EVAL\n"
        "   METHOD Quickstep\n   &DFT\n      &MGRID\n"
        "         CUTOFF [Ry] 400\n"
        "         REL_CUTOFF [Ry] 60\n      &END MGRID\n   &END DFT\n"
        "&END FORCE_EVAL\n", encoding="utf-8")
    set_source("input")
    scan.w_source_file.setText(str(literal))
    scan.w_cutoffs.setText("100")
    scan.w_rel_cutoffs.setText("20")
    scan.w_fixed_cutoff.clear()
    scan.w_scan_outdir.setText(str(work / "gui_b"))
    check(scan.run_scan() == 0, "字面写法的输入也能生成")
    prepared = (work / "gui_b" / "template.inp").read_text(encoding="utf-8")
    check("CUTOFF [Ry] LT_cutoff" in prepared
          and "REL_CUTOFF [Ry] LT_rel_cutoff" in prepared,
          "字面写的 CUTOFF/REL_CUTOFF 被换成占位符（插在 [Ry] 之后）")
    check("RUN_TYPE ENERGY" in prepared and "GEO_OPT" not in prepared,
          "输入里的 RUN_TYPE GEO_OPT 被改成 ENERGY（接口上改不动的东西也改掉了）")
    check("RUN_TYPE 已从 GEO_OPT 改成 ENERGY" in scan.scan_log.toPlainText(),
          "结果里点名了这次改动（用户得知道输入被动过）")
    check("CUTOFF [Ry] LT_cutoff" in prepared,
          "单位标注 [Ry] 与占位符共存：sed 之后只有一个值")
    step2 = (work / "gui_b" / "rel_cutoff_inputs.sh").read_text(encoding="utf-8")
    check("CHANGE_ME" in step2 and "exit 1" in step2,
          "没填第 2 步的 CUTOFF 时脚本里是 CHANGE_ME 且会拒绝运行")
    check("MAX_SCF 1" in scan.scan_log.toPlainText(),
          "输入不是官方那种省机时写法时，结果里提醒一次")
    check("第 2 步的 CUTOFF 还没定" in scan.scan_log.toPlainText(),
          "结果里也说明了第 2 步还缺什么")
    run_cli(["scan", "--input", str(literal), "--outdir", str(work / "cli_b"),
             "--cutoffs", "100", "--rel-cutoffs", "20"])
    compare_tree("字面写法的输入", work / "gui_b", work / "cli_b")

    # ---- C. the guards must reach the user instead of crashing ------------- #
    scan.w_source_file.clear()
    scan.w_scan_outdir.setText(str(work / "gui_c"))
    before = len(scan.messages)
    check(scan.run_scan() == -1 and len(scan.messages) > before,
          "没选输入文件时给出提示而不是崩掉")
    scan.w_source_file.setText(str(literal))
    scan.w_cutoffs.setText("abc")
    scan.w_scan_outdir.setText(str(work / "gui_c"))
    check(scan.run_scan() == -1 and "CUTOFF" in scan.scan_log.toPlainText(),
          f"非数字的 CUTOFF 被拒绝并说明：{scan.scan_log.toPlainText()[:50]}")
    scan.w_cutoffs.setText("100,200")

    # ---- E. the other channel: the tool's own parameters ------------------- #
    # The tab has to be able to build the template out of the generation tab's
    # current parameters; the CLI's `scan` takes the same options, so the bytes
    # have to match for the same settings.
    window.tabs.setCurrentWidget(scan)
    pump(200)
    reset_gui()
    set_task("energy")
    generate.load_from_path(WATER)
    generate.w_vacuum.setValue(12.0)
    set_source("structure")
    set_source("input")
    check(scan.w_source_file.isVisible(), "「已有输入」这一档才显示文件框")
    set_source("structure")
    check(not scan.w_source_file.isVisible()
          and not scan.w_source_browse.isVisible(),
          "「生成输入页参数」这一档把文件框收起来（不用先挑文件）")
    check("RUN_TYPE 固定成 ENERGY" in scan.source_hint.text(),
          f"这一档的说明写清了 RUN_TYPE 会被固定：{scan.source_hint.text()[:40]}…")
    set_task("md")
    set_source("structure")
    check("MD" in scan.source_hint.text()
          and "RUN_TYPE 会被固定成 ENERGY" in scan.source_hint.text(),
          "在 MD 页上也说明：网格扫描会按单点能跑")
    set_task("energy")
    set_source("structure")
    scan.w_cutoffs.setText("100,200")
    scan.w_rel_cutoffs.setText("20")
    scan.w_fixed_cutoff.clear()
    scan.w_scan_outdir.setText(str(work / "gui_e"))
    check(scan.run_scan() == 0, "「生成输入页参数」通道：不报错")
    check(sorted(path.name for path in (work / "gui_e").iterdir()) ==
          ["cutoff_analyse.sh", "cutoff_inputs.sh", "cutoff_run.sh",
           "rel_cutoff_analyse.sh", "rel_cutoff_inputs.sh", "rel_cutoff_run.sh",
           "template.inp"],
          "这条通道生成的同样是那 7 个文件")
    built = (work / "gui_e" / "template.inp").read_text(encoding="utf-8")
    check("RUN_TYPE ENERGY" in built and "&MD" not in built,
          "模板是单点能输入（RUN_TYPE ENERGY，没有 &MD）")
    check("SCF_GUESS ATOMIC" in built and "MAX_SCF 1" in built
          and "IGNORE_CONVERGENCE_FAILURE T" in built
          and "MAX_SCF 0" in built,
          "自动写上官方那套省机时协议（含 &OUTER_SCF MAX_SCF 0）")
    check("PRINT_LEVEL MEDIUM" in built,
          "PRINT_LEVEL 抬到 MEDIUM（否则数不出每个网格层的高斯数）")
    check("CUTOFF [Ry] LT_cutoff" in built
          and "REL_CUTOFF [Ry] LT_rel_cutoff" in built,
          "工具自己写的输入：占位符插在 [Ry] 之后，sed 之后只有一个值")
    run_cli(["scan", "--structure", WATER, "--vacuum", "12",
             "--outdir", str(work / "cli_e"), "--cutoffs", "100,200",
             "--rel-cutoffs", "20"])
    compare_tree("生成输入页参数", work / "gui_e", work / "cli_e")

    # a relay/restart must not become part of a grid study: CP2K aborts on the
    # missing file during input checking, so every point would die before the
    # first SCF step
    generate.w_ext_restart.setText("cp2k-1_40000.restart")
    scan.w_scan_outdir.setText(str(work / "gui_f"))
    check(scan.run_scan() == 0, "填了接力 restart 的生成页也能扫（不是照抄进模板）")
    relayed = (work / "gui_f" / "template.inp").read_text(encoding="utf-8")
    check("EXT_RESTART" not in relayed
          and "cp2k-1_40000.restart" not in relayed,
          "接力设置不会进模板（否则每档都在检查输入时 ABORT）")
    check("接力用的 restart 文件" in scan.scan_log.toPlainText(),
          "结果里说明了接力设置被丢掉（并说清是哪一项）")
    run_cli(["scan", "--structure", WATER, "--vacuum", "12",
             "--restart", "cp2k-1_40000.restart",
             "--outdir", str(work / "cli_f"), "--cutoffs", "100,200",
             "--rel-cutoffs", "20"])
    compare_tree("接力设置", work / "gui_f", work / "cli_f")
    generate.w_ext_restart.clear()

    # back to the file channel: the layout check below expects its widgets shown
    set_source("input")
    scan.w_source_file.setText(str(literal))

    # ---- D. the sources are gone: no scan/converge CLI any more ------------ #
    parser = window.generate.__class__  # keep a reference alive, nothing to do
    from cp2kbuild.cli import build_parser

    subcommands = {action.dest for action in build_parser()._actions
                   if action.dest == "command"}
    choices = next((action.choices for action in build_parser()._actions
                    if getattr(action, "choices", None) and "scan" in action.choices),
                   {})
    check("scan" in choices and "converge" not in choices,
          f"命令行只剩 scan（已无 converge）：{sorted(choices)}")

    shutil.rmtree(work, ignore_errors=True)

    # ------------------------------------------------------------------ #
    # 温度程序：表里的段 → 和 `ramp --program` 逐字节一致
    # ------------------------------------------------------------------ #
    print("\n== 温度程序（保持 / 升温 / 降温） ==")
    import tempfile as _tempfile_prog

    from cp2kbuild import tempprogram

    program_dir = Path(_tempfile_prog.mkdtemp(prefix="gui_program_"))
    generate.w_ensemble.setCurrentText("LANGEVIN")
    generate.w_timestep.setValue(0.5)
    table = generate.w_program_table
    while table.rowCount():
        table.removeRow(0)
    rows = ((300.0, 300.0, 5.0), (300.0, 500.0, 10.0), (500.0, 500.0, 5.0),
            (500.0, 300.0, 20.0))
    for start, target, duration in rows:
        generate._program_add_row(start, duration, target=target)
    check(generate._program_rows() == [tuple(row) for row in rows],
          f"表里 4 段读出来与填进去一致：{generate._program_rows()}")
    check("4 段" in generate.program_hint.text()
          and "40.00 ps" in generate.program_hint.text(),
          f"旁边写明段数与总时长：{generate.program_hint.text()[:60]}…")
    generate.w_program_outdir.setText(str(program_dir / "gui"))
    check(generate.run_program() == 0,
          f"「生成温度程序」写得出文件：{generate.program_hint.text()[:60]}…")
    gui_files = sorted(path.name for path in (program_dir / "gui").iterdir())
    check(gui_files == sorted(
        [f"{stage}.inp" for stage in (
            "stage01_hold_300K", "stage02_heat_300to500K", "stage03_hold_500K",
            "stage04_cool_500to300K")]
        + ["program.ssv", "run_program.sh", "program.txt"]),
          f"一个目录里：每段一个 .inp + 计划表 + 驱动脚本：{gui_files}")
    program_file = program_dir / "program.txt"
    program_file.write_text(tempprogram.program_text(rows), encoding="utf-8")
    run_cli(["ramp", "--structure", WATER, "--vacuum", "12",
             "--ensemble", "LANGEVIN", "--timestep", "0.5",
             "--program", str(program_file),
             "--outdir", str(program_dir / "cli")])
    compare_tree("温度程序", program_dir / "gui", program_dir / "cli")

    # 每段的启动方式（-np / OMP / 可执行 / 续跑）也是控件，且与命令行一致
    check(generate.w_program_procs.value() == 64
          and generate.w_program_omp.value() == 2
          and generate.w_program_bin.currentText() == "cp2k.psmp"
          and generate.w_program_resume.isChecked(),
          "启动控件的默认值就是集群标准：-np 64 / OMP 2 / cp2k.psmp / 断点续跑")
    generate.w_program_procs.setValue(8)
    generate.w_program_omp.setValue(1)
    generate.w_program_bin.setCurrentText("cp2k.popt")
    generate.w_program_mpirun.setText("mpirun")
    generate.w_program_resume.setChecked(False)
    generate.w_program_outdir.setText(str(program_dir / "gui_launch"))
    check(generate.run_program() == 0,
          f"改了启动控件后照样生成：{generate.program_hint.text()[:60]}…")
    launch_script = (program_dir / "gui_launch" / "run_program.sh").read_text(
        encoding="utf-8")
    check("export OMP_NUM_THREADS=1" in launch_script
          and "no_proc=8" in launch_script
          and "cp2k.popt" in launch_script
          and "resume=false" in launch_script,
          "控件真的写进了 run_program.sh（-np 8 / OMP 1 / cp2k.popt / 不续跑）")
    check("串行" in launch_script and "wait" not in launch_script,
          "驱动脚本仍然写明是串行链条（没有并行批处理）")
    run_cli(["ramp", "--structure", WATER, "--vacuum", "12",
             "--ensemble", "LANGEVIN", "--timestep", "0.5",
             "--program", str(program_file),
             "--mpi-procs", "8", "--omp-threads", "1", "--cp2k-bin", "cp2k.popt",
             "--no-resume", "--outdir", str(program_dir / "cli_launch")])
    compare_tree("温度程序（启动参数）", program_dir / "gui_launch",
                 program_dir / "cli_launch")

    # 「浏览…」走系统目录选择框，「打开目录」在文件管理器里打开它
    from cp2kbuild import gui as _gui_module

    picked = program_dir / "picked_by_browse"
    picked.mkdir()
    asked: list[str] = []
    real_dir_dialog = _gui_module.QFileDialog.getExistingDirectory

    def fake_dir_dialog(parent, title, start, *args, **kwargs):
        asked.append(str(start))
        return str(picked)

    _gui_module.QFileDialog.getExistingDirectory = staticmethod(fake_dir_dialog)
    try:
        generate.w_program_outdir.clear()
        check(not generate.w_program_open.isEnabled(),
              "输出目录为空时「打开目录」是灰的")
        generate.w_program_browse.click()
        check(generate.w_program_outdir.text() == str(picked)
              and generate.w_program_open.isEnabled(),
              f"点「浏览…」把选中的目录填进来：{generate.w_program_outdir.text()}")
        generate.w_program_browse.click()
        check(len(asked) == 2 and str(picked) in asked[1],
              f"第二次浏览的起点是上一次的目录：{asked[1]}")
    finally:
        _gui_module.QFileDialog.getExistingDirectory = real_dir_dialog

    opened: list[str] = []
    real_open = _gui_module.QDesktopServices.openUrl
    _gui_module.QDesktopServices.openUrl = staticmethod(
        lambda url: opened.append(url.toLocalFile()))
    try:
        generate.open_program_outdir()
        check([Path(name) for name in opened] == [picked],
              f"「打开目录」在文件管理器里打开那个目录：{opened}")
        generate.w_program_outdir.setText(str(program_dir / "nope"))
        generate.open_program_outdir()
        check(len(opened) == 1 and "还不存在" in generate.program_hint.text(),
              "目录不存在时不打开，并在提示里说明")
    finally:
        _gui_module.QDesktopServices.openUrl = real_open

    # 有变化段时 NVT/NPT 不能做，界面要说清楚（而不是生成一堆跑不动的输入）
    generate.w_ensemble.setCurrentText("NVT")
    check("不能升降温" in generate.program_hint.text(),
          "NVT 下必须先把变化段改掉（提示说清该换成什么）")
    check(generate.run_program() == -1,
          "NVT + 变化段时「生成」直接拒绝并说明原因")

    # 一个只有「保持」段的程序在 NVT 下是合法的
    table = generate.w_program_table
    while table.rowCount():
        table.removeRow(0)
    generate._program_add_row(300.0, 5.0)
    check("1 段" in generate.program_hint.text()
          and "不能升降温" not in generate.program_hint.text(),
          f"NVT 下只有保持段时不报错：{generate.program_hint.text()[:60]}…")
    generate.w_program_outdir.setText(str(program_dir / "gui_nvt"))
    check(generate.run_program() == 0, "NVT + 保持段也能生成")
    reset_gui()

    print("\n== 表格（Excel 式：可拖列宽 / 排序 / 复制 / 记住宽度） ==")
    from PySide6.QtCore import QSettings as _QSettings, Qt as _Qt
    from PySide6.QtWidgets import QHeaderView, QTableWidgetSelectionRange

    from cp2kbuild.gui import ExcelTable

    probe_settings = _QSettings("cp2kbuild-test", "excel-table")
    probe_settings.clear()
    probe = ExcelTable(["元素", "基组", "价"], widths=[70, 200, 50], key="probe",
                       settings=probe_settings)
    modes = {probe.horizontalHeader().sectionResizeMode(c)
             for c in range(probe.columnCount())}
    check(modes == {QHeaderView.Interactive},
          "表格的每一列都是可拖动的（Interactive）")
    probe.add_row(["Fe", "DZVP", "q16"], keys=["Fe", "DZVP", 16])
    probe.add_row(["U", "SZV", "q8"], keys=["U", "SZV", 8])
    probe.add_row(["C", "TZV", "q4"], keys=["C", "TZV", 4])
    check(all(probe.item(r, 0) is not None for r in range(3)),
          "开着排序逐行填充不会丢单元格")
    probe.sortItems(2, _Qt.AscendingOrder)
    order = [probe.item(r, 0).text() for r in range(probe.rowCount())]
    check(order == ["C", "U", "Fe"], f"点表头排序按数值而不是字符串：{order}")
    probe.setColumnWidth(1, 321)
    probe._save_widths()
    probe_settings.sync()
    restored = ExcelTable(["元素", "基组", "价"], widths=[70, 200, 50], key="probe",
                          settings=probe_settings)
    check(restored.columnWidth(1) == 321, "拖过的列宽会被记住")
    restored.reset_widths()
    check(restored.columnWidth(1) == 200, "「重置列宽」回到默认值")
    probe.setRangeSelected(QTableWidgetSelectionRange(0, 0, 0, 2), True)
    probe.copy_selection()
    check(QApplication.clipboard().text().count("\t") == 2,
          "Ctrl+C 复制选中行成 TSV（可以直接粘到 Excel）")
    probe.autofit()
    check(probe.horizontalHeader().sectionResizeMode(1) == QHeaderView.Interactive,
          "自适应列宽之后仍然可以拖动")

    window.tabs.setCurrentWidget(window.data_tab)
    window.data_tab.elements.setText("H, C, O, Fe, Pt")
    window.data_tab.recommend()
    basis_table = window.data_tab.table
    check(basis_table.rowCount() == 5, f"基组页有 {basis_table.rowCount()} 行")
    check({basis_table.horizontalHeader().sectionResizeMode(c)
           for c in range(basis_table.columnCount())} == {QHeaderView.Interactive},
          "基组/赝势表每列都能拖动（以前被内容宽度锁死）")
    check(hasattr(basis_table, "autofit") and hasattr(basis_table, "copy_selection"),
          "基组/赝势表带自适应列宽与复制")
    fit_buttons = [b for b in window.data_tab.findChildren(QPushButton)
                   if "列宽" in b.text()]
    check(bool(fit_buttons) and fit_buttons[0].isVisible(),
          "基组/赝势页有一个看得见的「列宽自适应」按钮")
    check(window.generate.kind_table.horizontalHeader().sectionResizeMode(1)
          == QHeaderView.Interactive, "生成页的基组表列宽也可拖")
    check(not window.generate.diagnostics.isSortingEnabled(),
          "校验结果表默认不排序（保持严重程度顺序）")
    check("2026" in window.windowTitle() or "trunk" in window.windowTitle(),
          f"窗口标题带上内置手册的 CP2K 版本：{window.windowTitle()}")
    # the title and the "about" line were two of the spots damaged by a
    # PowerShell encoding accident while editing this file; assert their shape
    check(window.windowTitle().endswith("）"),
          f"窗口标题以右括号收尾：{window.windowTitle()!r}")
    if window.keyword_tab.db is not None:
        branch = window.keyword_tab.db.stats()["branch"]
        check("；" not in branch, f"手册分支名没被改坏：{branch!r}")
    window.tabs.setCurrentWidget(generate)   # 后面的布局检查要求停在「生成输入」页
    pump(150)

    print("\n== 布局与内容 ==")
    generate.load_from_path(WATER)
    set_task("geo_opt")
    generate.refresh()
    pump(300)

    zero_sized: list[str] = []
    for widget in window.findChildren(QWidget):
        if not widget.isVisible():
            continue
        if widget.width() <= 0 or widget.height() <= 0:
            zero_sized.append(f"{type(widget).__name__}({widget.objectName()})")
    check(not zero_sized,
          f"所有可见控件都有非零尺寸（异常 {len(zero_sized)} 个：{zero_sized[:5]}）")

    check(generate.preview.height() > 200,
          f"预览区高度充足（{generate.preview.height()} px）")
    check(generate.diagnostics.height() > 80,
          f"诊断表高度充足（{generate.diagnostics.height()} px）")
    check(len(generate.preview.toPlainText().splitlines()) > 50,
          "预览里确实有生成出来的输入文件")
    check(generate.kind_table.isVisible() and generate.kind_table.rowCount() >= 2,
          f"基组/赝势表显示了 {generate.kind_table.rowCount()} 个元素")
    check("0 错误" in generate.summary.text(),
          f"校验摘要显示无错误：{generate.summary.text()!r}")

    # the cutoff-scripts tab has to lay out as well as the generation tab
    window.tabs.setCurrentWidget(scan)
    pump(250)
    scan_zero = [f"{type(w).__name__}({w.objectName()})"
                 for w in window.findChildren(QWidget)
                 if w.isVisible() and (w.width() <= 0 or w.height() <= 0)]
    check(not scan_zero,
          f"收敛测试页的可见控件都有非零尺寸（异常 {len(scan_zero)} 个：{scan_zero[:5]}）")
    check(scan.scan_log.height() > 80 and scan.howto.isVisible(),
          f"结果区高度充足（{scan.scan_log.height()} px）")
    check(scan.w_source_file.isVisible() and scan.w_scan_outdir.isVisible(),
          "输入框与输出目录都在页面上")

    # the MD page (which now carries the 元动力学 block) has to lay out as well -
    # both with the block folded away and with it open
    window.tabs.setCurrentWidget(generate)
    set_task("md")
    reset_gui()
    pump(250)
    md_zero = [f"{type(w).__name__}({w.objectName()})"
               for w in window.findChildren(QWidget)
               if w.isVisible() and (w.width() <= 0 or w.height() <= 0)]
    check(not md_zero,
          f"MD 页（含收起的元动力学块）可见控件都有非零尺寸"
          f"（异常 {len(md_zero)} 个：{md_zero[:5]}）")
    check(meta.isVisibleTo(generate) and not meta.is_expanded(),
          "MD 页上元动力学块默认收起、标题行可见")
    check(generate.temperature_program_box.isVisibleTo(generate)
          and not generate.temperature_program_box.is_expanded(),
          "MD 页上温度程序（多段升温/降温）也默认收起、标题行可见")
    meta.set_expanded(True)
    pump(250)
    meta_zero = [f"{type(w).__name__}({w.objectName()})"
                 for w in window.findChildren(QWidget)
                 if w.isVisible() and (w.width() <= 0 or w.height() <= 0)]
    check(not meta_zero,
          f"展开后的元动力学块可见控件都有非零尺寸"
          f"（异常 {len(meta_zero)} 个：{meta_zero[:5]}）")
    check(meta.w_cv_table.height() > 80 and meta.w_metavar_table.height() > 80,
          f"两张表都有高度（{meta.w_cv_table.height()} / "
          f"{meta.w_metavar_table.height()} px）")

    # ---- 左侧参数面板：宽度够用 + 动作按钮固定在下方 ---------------------- #
    # 一个真实反馈：左栏「有时候显示不完全」。原因是面板自身的最小宽度大于竖滚动条
    # 吃剩下的视口（默认 700 px 的栏位差 10 px），横向滚动条一直在，每行右端被藏住；
    # 而 保存/复制/恢复默认 在滚动区内部的最底部，窗口一矮就得滚到底才能点。
    def form_fits(tag: str) -> None:
        need = generate.form_host.minimumSizeHint().width()
        view = generate.form_scroll.viewport().width()
        check(view >= need and not generate.form_scroll.horizontalScrollBar().isVisible(),
              f"{tag}：左栏宽度够（视口 {view} ≥ 内容最小 {need}），没有横向滚动条")

    form_fits("geo_opt 页")
    set_task("md")
    reset_gui()
    pump(250)
    form_fits("MD 页")
    meta.set_expanded(True)
    pump(250)
    form_fits("MD 页 + 元动力学展开")
    generate.temperature_program_box.set_expanded(True)
    pump(250)
    form_fits("MD 页 + 温度程序展开")
    generate.temperature_program_box.set_expanded(False)
    pump(150)
    meta.w_route.setCurrentIndex(1)
    from cp2kbuild.plumedcv import ContactSpec, PlumedWall, contact_cvs
    rows, _notes = contact_cvs(ContactSpec(blocks="19x1,8x20"), natoms=179, units="A")
    meta._plumed_cvs = list(rows)
    meta._plumed_walls = [PlumedWall(arg="cv_uu", kind="UPPER_WALLS", at=5.0,
                                     kappa=1000.0)]
    meta._refresh()
    generate.refresh()
    pump(300)
    form_fits("PLUMED 路线 + 21 行 CV + 墙（表列宽 760 也不撑宽面板）")
    meta._plumed_cvs = []
    meta._plumed_walls = []
    meta.w_route.setCurrentIndex(0)
    pump(150)

    check(not generate.form_scroll.isAncestorOf(generate.save_button)
          and not generate.form_scroll.isAncestorOf(generate.copy_button),
          "保存/复制/恢复默认 在滚动区**外面**（固定在下方的动作栏里）")
    # 一个按钮管两个文件（左边是 CP2K 输入、plumed.dat 是右边预览的第二页），
    # 所以它不该叫「保存为 .inp」
    check(generate.save_button.text() == "保存"
          and ".inp" not in generate.save_button.text(),
          f"动作栏第一个按钮叫「保存」而不是「保存为 .inp」："
          f"{generate.save_button.text()!r}")
    bar = generate.form_footer
    check(bar.isVisibleTo(window) and bar.parent() is generate.form_column
          and bar.y() >= generate.form_scroll.y() + generate.form_scroll.height() - 1,
          f"动作栏贴在左栏底部（y={bar.y()}，滚动区底 "
          f"{generate.form_scroll.y() + generate.form_scroll.height()}）")
    # 滚到底 / 滚到顶，按钮都在同一个位置（它根本不参与滚动）
    before = bar.mapTo(window, bar.rect().topLeft())
    generate.form_scroll.verticalScrollBar().setValue(
        generate.form_scroll.verticalScrollBar().maximum())
    pump(120)
    after = bar.mapTo(window, bar.rect().topLeft())
    generate.form_scroll.verticalScrollBar().setValue(0)
    pump(120)
    check(before == after and generate.save_button.isVisibleTo(window),
          f"滚动表单时动作栏不动（{before.y()} → {after.y()}）")
    # 每行的「&EACH/ADD_LAST」是两行：九个控件挤一行要 650 px，比整个面板还宽
    holder = generate.w_restart_every.parentWidget()
    check(holder.minimumSizeHint().width() < 420
          and holder.height() > generate.w_restart_every.height() * 1.6
          and holder.isAncestorOf(generate.w_restart_backups),
          f"「每 N 步 / 最后一步也写 / 备份」分两行（行宽 "
          f"{holder.minimumSizeHint().width()} px，高 {holder.height()} px）")
    check(meta.w_cv_table.minimumSizeHint().width() <= 300
          and meta.w_plumed_table.minimumSizeHint().width() <= 300,
          "表格不按列宽和撑宽面板（自己的横向滚动条负责看全）")
    # isHidden() 才反映「这次判定是否明确隐藏它」；块收起时 body 被隐藏，
    # 里面的控件 isVisible()/isVisibleTo() 都会是 False
    check(not meta.w_enable.isHidden() and meta.plumed_row.isHidden(),
          "默认（CP2K 路线）显示启用开关、不显示 PLUMED 文件框")
    # 真实反馈：「metaD 未启用时还是默认收起吧」——记住的展开状态不能把没启用的
    # 块重新撑开；勾上启用它会自动展开（原来的行为）
    check(not meta.is_expanded(),
          "未启用元动力学时这一块是收起的（哪怕上次是展开的）")
    meta.set_expanded(True)
    meta._refresh()
    pump(200)
    check(not meta.is_expanded(),
          "未启用却手动展开，任何一次刷新都会收回去")
    meta.w_enable.setChecked(True)
    pump(250)
    check(meta.is_expanded(), "勾上「启用元动力学」会自动展开（原行为不变）")
    meta.w_enable.setChecked(False)
    pump(250)
    check(not meta.is_expanded() and window.settings.value("fold/metadyn") is not None,
          "取消勾选后收起，而且记住的偏好没有被这次收藏覆盖掉")
    check(meta.w_temp.isEnabled() is False,
          "没勾井温时参考温度不可编辑（避免写一个用不上的值）")
    check(generate.preview.height() > 200, "完整预览还在右边（没有第二份预览）")
    meta.set_expanded(False)
    reset_metadyn()

    # 任务页是「显示/隐藏」而不是 QStackedWidget：Qt 会按**最高**的那一页把 stack
    # 撑高，而 MD 页有 ~2100 px（温度程序表 + 打印键 + 元动力学），于是单点能那种
    # 几十 px 的页面下面会挂着一大片空白（而且切页时 Qt 不会重新问 sizeHint）
    blanks = []
    for task in generate.task_pages:
        set_task(task)
        generate.refresh()
        pump(300)
        visible = [name for name, page in generate.task_pages.items()
                   if page.isVisibleTo(generate.task_host)]
        page = generate.task_pages[task]
        slack = page.height() - page.sizeHint().height()
        if slack > 20:
            # give the event loop one more turn before believing it (a loaded
            # machine can take longer than a single pump)
            pump(400)
            slack = page.height() - page.sizeHint().height()
        if visible != [task]:
            blanks.append(f"{task}: 可见的是 {visible}")
        elif slack > 20:
            blanks.append(f"{task}: 下面还有 {slack} px 空白")
    check(not blanks,
          f"每个任务页只显示自己、且没有多余空白（{blanks[:3]}）")
    check(generate.task_pages["md"].sizeHint().height() > 500
          and generate.task_pages["energy"].sizeHint().height() < 200,
          f"MD 页仍然完整（{generate.task_pages['md'].sizeHint().height()} px，"
          f"折叠状态下；展开元动力学会更高）、"
          f"单点能页很短（{generate.task_pages['energy'].sizeHint().height()} px）")
    # 真实反馈：「温度程序也可以做默认折叠隐藏」——量一下它省了多少高度
    # （必须先让 MD 页显示出来：Qt 不会给隐藏的页面重算布局，sizeHint 会是旧值）
    set_task("md")
    generate.refresh()
    pump(300)
    page = generate.task_pages["md"]
    generate.temperature_program_box.set_expanded(False)
    pump(200)
    folded_height = page.sizeHint().height()
    generate.temperature_program_box.set_expanded(True)
    pump(200)
    open_height = page.sizeHint().height()
    generate.temperature_program_box.set_expanded(False)
    pump(150)
    check(open_height - folded_height > 150,
          f"温度程序默认收起：MD 页因此矮 "
          f"{open_height - folded_height} px（{open_height} → {folded_height}）")
    reset_gui()

    # the validate tab must find the deliberate mistakes
    window.tabs.setCurrentWidget(window.validate_tab)
    window.validate_tab.files.clear()
    broken = ROOT / "packaging" / "shots" / "_broken.inp"
    broken.parent.mkdir(parents=True, exist_ok=True)
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
    codes = {window.validate_tab.table.item(r, 2).text()
             for r in range(window.validate_tab.table.rowCount())}
    check("valence-mismatch" in codes, "校验页抓到了基组/赝势价电子不匹配")
    check("unknown-keyword" in codes, "校验页抓到了拼错的关键字")
    check("rel-cutoff-above-cutoff" in codes, "校验页抓到了 REL_CUTOFF > CUTOFF")
    broken.unlink()

    # 「添加文件…」 must actually open the dialog: the directory helpers live on
    # the generate tab, and this used to raise AttributeError before the dialog
    from cp2kbuild import gui as _gui
    original_picker = _gui.QFileDialog.getOpenFileNames
    asked: list[str] = []

    def _fake_picker(_parent, _title, start="", _filters=""):
        asked.append(start)
        return [SILICON], ""

    _gui.QFileDialog.getOpenFileNames = staticmethod(_fake_picker)
    try:
        window.validate_tab.files.clear()
        window.validate_tab.add_files()
    finally:
        _gui.QFileDialog.getOpenFileNames = original_picker
    check(bool(asked), "校验页「添加文件…」真的打开了文件对话框")
    check(window.validate_tab.files.count() == 1,
          f"选中的文件进了列表（{window.validate_tab.files.count()} 个）")
    window.validate_tab.files.clear()

    # keyword tab
    window.tabs.setCurrentWidget(window.keyword_tab)
    window.keyword_tab.search.setText("cutoff")
    window.keyword_tab.do_search()
    check(window.keyword_tab.results.count() > 0,
          f"手册查询返回 {window.keyword_tab.results.count()} 条结果")
    check("CUTOFF" in window.keyword_tab.detail.toHtml().upper(),
          "手册查询详情面板有内容")

    print("\n== 中文层（中文说明 / 术语 / 中文搜索） ==")
    from cp2kbuild.cn_glossary import KEYWORDS, TERMS, lookup
    from cp2kbuild.cn_glossary import terms_in

    keyword_tab = window.keyword_tab
    keyword_tab.section.clear()
    for query, expect in [("截断能", "CUTOFF"), ("系综", "ENSEMBLE"),
                          ("赝势", "POTENTIAL"), ("真空层", "CELL"),
                          ("几何优化", "RUN_TYPE"), ("红外", "INTENSITIES")]:
        keyword_tab.search.setText(query)
        keyword_tab.do_search()
        texts = [keyword_tab.results.item(i).text()
                 for i in range(keyword_tab.results.count())]
        check(any(expect in text for text in texts),
              f"中文搜「{query}」能找到 {expect}（{len(texts)} 条）")

    keyword_tab.search.setText("截断能")
    keyword_tab.do_search()
    html = keyword_tab.detail.toHtml()
    check("中文说明" in html and "不是手册译文" in html,
          "中文查询的详情里有中文说明，并标明不是手册译文")

    keyword_tab.search.setText("cutoff")
    keyword_tab.do_search()
    first = keyword_tab.results.item(0).text() if keyword_tab.results.count() else ""
    check("CUTOFF" in first and "MGRID" in first,
          f"英文搜索首条是常用的那个：{first}")

    keyword_tab.show_glossary()
    glossary_html = keyword_tab.detail.toHtml()
    check("截断能" in glossary_html and "布里渊区" in glossary_html,
          f"术语表渲染了 {len(TERMS)} 条术语")

    manual = keyword_tab.db
    wrong = []
    if manual is not None:
        for key in KEYWORDS:
            group, _, name = key.rpartition("/")
            if not manual.has_section(group):
                wrong.append(f"{key} (section)")
            elif not manual.has_keyword(group, name) and not manual.has_section(key):
                wrong.append(key)
    check(not wrong,
          f"{len(KEYWORDS)} 条中文说明都指向真实的手册位置（错的：{wrong[:3]}）")

    check(lookup("FORCE_EVAL/DFT/MGRID", "CUTOFF") is not None
          and lookup("NOPE/NOPE", "NOPE") is None,
          "中英文 lookup 正常")

    sample = manual.keyword("FORCE_EVAL/DFT/MGRID", "CUTOFF").description
    terms = terms_in(sample)
    check(any(cn == "截断能" for _, cn in terms),
          f"术语能从英文原文里抽出来：{terms}")

    # data tab
    window.tabs.setCurrentWidget(window.data_tab)
    window.data_tab.elements.setText("H, C, O, Fe, Pt")
    window.data_tab.recommend()
    rows = window.data_tab.table.rowCount()
    check(rows == 5, f"基组页为 5 种元素给出建议（实际 {rows}）")
    fe_row = None
    for row in range(rows):
        if window.data_tab.table.item(row, 0).text() == "Fe":
            fe_row = row
    check(fe_row is not None and
          window.data_tab.table.item(fe_row, 2).text() == "q16" and
          window.data_tab.table.item(fe_row, 4).text() == "GTH-PBE-q16",
          "Fe 的基组/赝势价电子数都是 16")

    print("\n== 帮助（“?”悬浮提示） ==")
    from cp2kbuild.gui import HelpBadge
    from cp2kbuild.helptext import HELP

    badges = window.findChildren(HelpBadge)
    check(len(badges) >= 25, f"界面上有 {len(badges)} 个「?」帮助徽标")

    too_short = [b.help_entry.text.splitlines()[0][:16]
                 for b in badges if len(b.toolTip()) < 60]
    check(not too_short, f"每个「?」都有实质内容（过短的：{too_short}）")

    # every help entry must actually be reachable by hovering something
    tooltips = " ".join(w.toolTip() for w in window.findChildren(QWidget) if w.toolTip())
    unreachable = [key for key, entry in HELP.items()
                   if entry.text.splitlines()[0][:12] not in tooltips]
    check(not unreachable, f"每条帮助都挂到了控件或徽标上（漏挂的：{unreachable}）")

    # guidance must not pop up over the form: only the "?" carries a tooltip
    interactive = (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
                   QPushButton, QToolButton, QPlainTextEdit, QListWidget,
                   QTableWidget)
    leaking = []
    for widget in window.findChildren(QWidget):
        if isinstance(widget, HelpBadge) or not isinstance(widget, interactive):
            continue
        if widget.toolTip():
            leaking.append(f"{type(widget).__name__}({widget.toolTip()[:24]})")
    check(not leaking,
          f"输入框/下拉框/按钮上都没有提示（提示只在「?」上）：{leaking[:4]}")

    linked = [b for b in badges if b.help_entry.url]
    check(len(linked) >= 15, f"{len(linked)} 个「?」可以点击打开手册")

    # the manual references inside the help texts must be real, otherwise the
    # click-through would land on a 404
    db = window.keyword_tab.db
    wrong: list[str] = []
    if db is not None:
        for key, entry in HELP.items():
            if not entry.section:
                continue
            if not db.has_section(entry.section):
                wrong.append(f"{key}: section {entry.section}")
            elif entry.keyword and not db.has_keyword(entry.section, entry.keyword):
                wrong.append(f"{key}: {entry.section}/{entry.keyword}")
    check(not wrong, f"帮助里引用的手册位置都真实存在（查不到的：{wrong}）")

    # spot-check that a URL is exactly what the database would produce
    if db is not None:
        entry = HELP["cutoff"]
        expected = db.keyword(entry.section, entry.keyword).url
        check(entry.url == expected, "帮助里的手册链接与数据库生成的一致")

    print()
    if failures:
        print(f"{len(failures)} 项失败：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
