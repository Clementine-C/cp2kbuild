"""PySide6 desktop front-end for cp2kbuild.

The window has four tabs:

* **生成输入** - pick a structure, choose a task, watch the finished input file
  and the validation report update live as parameters change;
* **校验输入** - lint existing CP2K inputs against the manual database;
* **手册查询** - offline lookup of any keyword, with defaults, enum values and
  the manual link;
* **基组/赝势** - scan a CP2K ``data/`` directory and show which basis set and
  pseudopotential would be chosen for which element.

Generation and validation are fast enough (tens of milliseconds) to run on every
keystroke, so the preview is always the file that would be written.
"""

from __future__ import annotations

import html
import re
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QFontDatabase,
                           QIcon, QKeySequence, QPainter, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
                               QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit,
                               QListWidget, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter,
                               QStackedWidget, QStatusBar, QStyledItemDelegate,
                               QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser,
                               QToolButton, QVBoxLayout, QWidget)

from . import __version__
from .cn_glossary import TERMS, is_chinese, lookup as cn_lookup, search as cn_search
from .cn_glossary import split_key as cn_split_key, terms_in as cn_terms_in
from .datafiles import DataIndex, discover_data_dir
from .helptext import HELP, Help
from .importinp import import_input
from .manualdb import ManualDB, load_db
from .metadyn import (CV_ATOM_COUNT, CV_KINDS, CV_LABELS, Cv, Metadyn, Metavar,
                      Wall, problems as metadyn_problems)
from .model import render_document
from .parser import read_input
from .paths import write_text_lf
from .couplings import applicability, poisson_solver_warning
from .cutoffkit import (DEFAULT_CUTOFFS, DEFAULT_FIXED_REL_CUTOFF,
                        DEFAULT_REL_CUTOFFS)
from .recipes import (COORD_SUFFIX_FORMAT, EXT_RESTART_TAKES, MM_ROUTES, TASKS,
                      BuildResult,
                      MmLennardJones, MmMolecule, Spec, build, effective_route,
                      is_classical, lennard_jones_pairs, mm_route,
                      poisson_solver_note, poisson_solvers_for, prepare_structure,
                      reads_as_cp2k_input, restart_summary, suggest_kpoint_grid)
from .structures import Structure, parse_cell_text, read_structure
from .validate import Diagnostic, Validator

#: Initial width of the parameter panel.  Narrower than this and the rows start
#: to feel cramped; wider and the preview loses the space it needs.
THIN_FORM_WIDTH = 700

LEVEL_COLORS = {
    "error": QColor("#c0392b"),
    "warning": QColor("#b9770e"),
    "info": QColor("#5d6d7e"),
}
LEVEL_LABELS = {"error": "错误", "warning": "警告", "info": "提示"}

TASK_LABELS = {
    "energy": "单点能 ENERGY",
    "geo_opt": "几何优化 GEO_OPT",
    "cell_opt": "晶胞优化 CELL_OPT",
    "md": "分子动力学 MD",
    "freq": "频率/红外 VIBRATIONAL_ANALYSIS",
    "band": "能带 BAND",
    "pdos": "态密度 PDOS",
    "neb": "过渡态 NEB",
}

#: ``FORCE_EVAL METHOD``.  The label changes with the task because "MD" means two
#: completely different runs: Born-Oppenheimer AIMD on the Quickstep route and a
#: force-field MD on the FIST route.
METHOD_LABELS = [
    ("DFT / Quickstep（&DFT，AIMD 也是这条）", "QUICKSTEP"),
    ("经典力场 FIST（&MM，不涉及电子）", "FIST"),
]

#: Rows of the "mixed system" table: one &MOL_SET/&MOLECULE per component.
MM_CONN_FORMATS = ["AMBER", "PSF", "UPSF", "G96", "G87", "GENERATE", "OFF"]
MM_EWALD_TYPES = ["SPME", "PME", "EWALD", "NONE"]
#: the coordinate-format combo: an automatic entry first, then CP2K's values
MM_COORD_FORMATS = [("自动（按文件后缀）", None), "CRD", "PDB", "XYZ", "G96",
                    "CIF", "XTL", "CP2K"]
#: how the force-field file names are spelled in the generated input
MM_PATH_STYLES = [("只写文件名（都在一个文件夹里）", "name"),
                  ("自动（同目录才缩写成文件名）", "auto"),
                  ("写完整路径", "absolute")]

#: ``&EXT_RESTART`` presets.  ``None`` = take everything (CP2K's
#: ``RESTART_DEFAULT T``); a tuple writes ``RESTART_DEFAULT F`` plus the named
#: ``RESTART_*`` switches.
#:
#: Ordered by how much they are actually needed now that a restart loaded as the
#: *structure* already brings the coordinates and the cell (the tool writes them
#: as ``&COORD``/``&CELL``): the interesting entries are the ones an inline input
#: cannot carry - **velocities**, **step counters**, **thermostat/barostat
#: state** - so those come first and ``pos``/``cell`` are marked as usually
#: redundant.  The labels stay short; the line under the combo explains each case.
RESTART_TAKES = [
    ("只取速度（MD → MD 接力，最常用）", ("vel",)),
    ("速度 + 热浴/压浴（接着控温控压）",
     ("vel", "thermostat", "barostat")),
    ("只取步数计数器（接着编号）", ("counters",)),
    ("全部（CP2K 默认，连计数器一起搬）", None),
    ("只取坐标（坐标通常已继承，多半用不上）", ("pos",)),
    ("坐标 + 晶胞（盒子和坐标通常也已继承）", ("pos", "cell")),
]

FUNCTIONALS = ["PBE", "BLYP", "BP", "PADE", "PBE0", "B3LYP", "HSE06", "HCTH120",
               "OLYP", "revPBE", "RPBE", "PBEsol", "TPSS", "M06L", "SCAN", "PBEH"]

DISPERSIONS = [("不使用", None), ("DFT-D3", "d3"), ("DFT-D3(BJ)", "d3bj"),
               ("DFT-D2", "d2"), ("rVV10", "rvv10")]

ENSEMBLES = ["NVE", "NVT", "NPT_I", "NPT_F", "NPT_IA", "NPE_I", "NPE_F",
             "LANGEVIN", "MSST", "ISOKIN"]
THERMOSTATS = ["NOSE", "CSVR", "GLE", "AD_LANGEVIN", "NONE"]
#: ``&MD/&THERMOSTAT REGION``: how many thermostats (and how many temperature
#: columns the thermostat print key produces).  ``DEFINED`` and ``THERMAL`` need
#: ``&DEFINE_REGION``/``&THERMAL_REGION`` subsections the tool does not build.
THERMOSTAT_REGIONS = [
    ("整个体系（一个热浴）", "GLOBAL"),
    ("每个分子（每种 molecule kind 一个热浴）", "MOLECULE"),
    ("每个自由度（MASSIVE）", "MASSIVE"),
]
PERIODIC_CHOICES = [("自动", None), ("XYZ", "XYZ"), ("XY", "XY"), ("XZ", "XZ"),
                    ("YZ", "YZ"), ("X", "X"), ("Y", "Y"), ("Z", "Z"), ("NONE", "NONE")]
BASIS_TARGETS = [("自动", "auto"), ("周期性/固体", "solid"), ("孤立分子", "molecule"),
                 ("省算力 SZV", "cheap"), ("高精度 TZV2P", "accurate")]
SCF_ROUTES = [("自动", "auto"), ("OT（绝缘体，快）", "OT"),
              ("DIAGONALIZATION（金属/k点）", "DIAGONALIZATION")]
#: ``&QS EXTRAPOLATION``: the first entry is the tool's own automatic choice.
EXTRAPOLATIONS = [("自动（MD/优化里用 ASPC）", None), ("ASPC", "ASPC"),
                  ("USE_GUESS（不外推）", "USE_GUESS"),
                  ("USE_PREV_P", "USE_PREV_P"), ("USE_PREV_WF", "USE_PREV_WF"),
                  ("LINEAR_P", "LINEAR_P"), ("LINEAR_PS", "LINEAR_PS"),
                  ("PS", "PS")]
#: ``&KPOINTS SCHEME`` choices: the grid ones take their numbers from the grid
#: field, ``GENERAL`` from the explicit k-point table, ``自定义`` from the text.
KPOINT_SCHEMES = [("自动（按网格/间距）", None), ("GAMMA", "GAMMA"),
                  ("MONKHORST-PACK", "MONKHORST-PACK"),
                  ("MACDONALD", "MACDONALD"), ("GENERAL（显式 k 点）", "GENERAL"),
                  ("自定义文本", "custom")]
KPOINT_UNIT_CHOICES = ["B_VECTOR", "CART_ANGSTROM", "CART_BOHR"]
KPOINT_WAVEFUNCTION_CHOICES = ["REAL", "COMPLEX"]
BAND_TYPES = ["CI-NEB", "IT-NEB", "B-NEB", "D-NEB", "SM", "EB"]
LATTICES = [("自动判断", None), ("简单立方 cub", "cub"), ("面心立方 fcc", "fcc"),
            ("体心立方 bcc", "bcc"), ("六方 hex", "hex"), ("四方 tet", "tet"),
            ("正交 orc", "orc"), ("其他", "generic")]

# SCF route details.  The lists come from the manual database (2026.2) and
# ``tools/selftest.py`` checks them against it, so a version bump cannot leave
# a stale value in a combo box.
OT_MINIMIZERS = ["DIIS", "CG", "SD", "BROYDEN"]
OT_PRECONDITIONERS = ["FULL_SINGLE_INVERSE", "FULL_ALL", "FULL_SINGLE",
                      "FULL_KINETIC", "FULL_S_INVERSE", "NONE"]
OT_ALGORITHMS = ["STRICT", "IRAC"]
OT_LINESEARCHES = ["2PNT", "3PNT", "GOLD", "ADAPT", "NONE"]
DIAG_ALGORITHMS = ["STANDARD", "DAVIDSON", "FILTER_MATRIX", "LANCZOS", "OT"]
MIXING_METHODS = ["BROYDEN_MIXING", "PULAY_MIXING", "MODIFIED_BROYDEN_MIXING",
                  "NEW_PULAY_MIXING", "MULTISECANT_MIXING", "KERKER_MIXING",
                  "DIRECT_P_MIXING", "NONE"]
SMEAR_METHODS = ["FERMI_DIRAC", "METHFESSEL_PAXTON", "MARZARI_VANDERBILT",
                 "ENERGY_WINDOW", "GAUSSIAN", "LIST"]
OUTER_OPTIMIZERS = [("跟随 CP2K 默认（不写）", None), ("DIIS", "DIIS"),
                    ("BROYDEN", "BROYDEN"), ("SD", "SD"), ("BISECT", "BISECT"),
                    ("NEWTON", "NEWTON"), ("SECANT", "SECANT"),
                    ("NEWTON_LS", "NEWTON_LS"), ("NONE", "NONE")]


def _target_version() -> str:
    """Which CP2K version the manual database in use describes."""
    from .manualdb import current_version

    try:
        return current_version()
    except Exception:  # noqa: BLE001 - never break the window over a label
        return "unknown"


def _optional_float(text: str, widget=None) -> float | None:
    """Parse a field that may be left empty; mark it red when it is nonsense."""
    stripped = (text or "").strip()
    if not stripped:
        if widget is not None:
            widget.setStyleSheet("")
        return None
    try:
        value = float(stripped.replace("D", "E").replace("d", "e"))
    except ValueError:
        if widget is not None:
            widget.setStyleSheet("border:1px solid #c0392b;")
        return None
    if widget is not None:
        widget.setStyleSheet("")
    return value


def _optional_int(text: str) -> int | None:
    """Parse an optional whole number; empty or nonsense means "not set"."""
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        return int(float(stripped.replace("D", "E").replace("d", "e")))
    except ValueError:
        return None


def _optional_step(spin: "QSpinBox") -> int | None:
    """A spin box whose minimum (-1) means "leave CP2K's own default alone".

    ``0`` is a *meaningful* value for ``&EACH`` (only the last iteration), so it
    cannot double as the "not set" marker.
    """
    value = spin.value()
    return None if value < 0 else int(value)


def tristate_combo() -> QComboBox:
    """A logical keyword that can also be left at CP2K's own default.

    ``T``/``F`` are *values*; "do not write the line at all" is a third state,
    and for these switches it is not the same thing as F.
    """
    combo = QComboBox()
    combo.addItem("跟随 CP2K 默认（不写）", None)
    combo.addItem("T", True)
    combo.addItem("F", False)
    return combo


def tristate_value(combo: QComboBox) -> bool | None:
    return combo.currentData()


def mono_font() -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
    font.setPointSize(max(9, font.pointSize()))
    return font


def make_icon() -> QIcon:
    """Draw the application icon in code so no external file is needed."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#1f4e79"))
        radius = size * 0.22
        painter.drawRoundedRect(1, 1, size - 2, size - 2, radius, radius)
        painter.setPen(QColor("#ffffff"))
        font = QFont()
        font.setBold(True)
        font.setPixelSize(int(size * 0.46))
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "CP")
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def field_label(text: str, tooltip: str = "") -> QLabel:
    label = QLabel(text)
    if tooltip:
        label.setToolTip(tooltip)
    return label


#: A light stylesheet for the whole window.  It only touches chrome - group-box
#: frames and titles, the tab bar, table headers, the status bar and a little
#: input padding - so the layout logic, the control set and the generated text
#: are exactly what they were.  The accent is a soft blue-grey, which leaves the
#: blue "?" badges as the one thing that reads as "click me".
_WINDOW_STYLE = """
QGroupBox {
    border: 1px solid #d9e0e8;
    border-radius: 6px;
    margin-top: 16px;
    padding: 12px 10px 8px 10px;
    background: #fcfdfe;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #2b5680;
    font-weight: 600;
}
QGroupBox QGroupBox {
    background: #ffffff;
    border-color: #e4e9f0;
    margin-top: 14px;
}
QTabWidget::pane {
    border: 1px solid #d9e0e8;
    border-radius: 4px;
    top: -1px;
}
QTabBar::tab {
    padding: 6px 14px;
    margin-right: 3px;
    border: 1px solid transparent;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}
QTabBar::tab:selected {
    background: #ffffff;
    border-color: #d9e0e8;
    font-weight: 600;
}
QTabBar::tab:hover:!selected {
    background: #eef3f8;
}
QHeaderView::section {
    background: #f1f5f9;
    padding: 5px 8px;
    border: 0px;
    border-right: 1px solid #e2e8f0;
    border-bottom: 1px solid #d9e0e8;
}
QStatusBar {
    background: #f6f9fc;
}
QStatusBar::item {
    border: 0px;
}
QPushButton {
    padding: 5px 12px;
}
QSplitter::handle {
    background: #e6ecf3;
}
"""


class SortItem(QTableWidgetItem):
    """A cell that sorts like a spreadsheet: numbers by value, not by text.

    ``QTableWidget`` compares the display text, so "q16" would come before "q8"
    and "100 Ry" before "90 Ry".  An explicit sort key fixes that.
    """

    def __init__(self, text: str, key=None):
        super().__init__(text)
        self.sort_key = key

    def __lt__(self, other):  # noqa: D105 - Qt sorts with this
        if isinstance(other, SortItem) and self.sort_key is not None \
                and other.sort_key is not None:
            try:
                return self.sort_key < other.sort_key
            except TypeError:
                pass
        # NB: do *not* call super().__lt__.  PySide6 routes the C++
        # ``operator<`` back into this Python override, so delegating to it
        # recurses until the interpreter gives up ("maximum recursion depth
        # exceeded" the moment a table is sorted).
        if isinstance(other, QTableWidgetItem):
            mine = self.data(Qt.DisplayRole)
            theirs = other.data(Qt.DisplayRole)
            try:
                return mine < theirs
            except TypeError:
                return str(mine) < str(theirs)
        return NotImplemented


class ComboDelegate(QStyledItemDelegate):
    """A cell that is edited through a combo box of fixed choices.

    Used for the ``&MOLECULE`` connectivity column, where CP2K only accepts the
    ``CONN_FILE_FORMAT`` values - typing one of those by hand is exactly the kind
    of mistake a list prevents.
    """

    def __init__(self, choices: list[str], parent=None):
        super().__init__(parent)
        self.choices = list(choices)

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = QComboBox(parent)
        editor.addItems(self.choices)
        return editor

    def setEditorData(self, editor, index):  # noqa: N802
        value = str(index.data(Qt.EditRole) or "").upper()
        position = editor.findText(value)
        editor.setCurrentIndex(position if position >= 0 else 0)

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.currentText(), Qt.EditRole)


class ExcelTable(QTableWidget):
    """A table that behaves like a spreadsheet.

    * every column can be **dragged** to whatever width you like (Qt's default
      here is to force the width to the contents, which cannot be dragged);
    * double-clicking a column boundary auto-fits that column, and the header's
      context menu offers 自适应列宽 / 重置列宽;
    * clicking a header sorts (numbers by value);
    * Ctrl+C copies the selection as tab-separated text, ready to paste into
      Excel or a report;
    * the widths you chose are remembered between sessions.
    """

    def __init__(self, headers: list[str], widths: list[int] | None = None,
                 key: str | None = None, settings: QSettings | None = None,
                 sortable: bool = True, editable: bool = False, parent=None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(headers)
        self._default_widths = widths or [140] * len(headers)
        self._settings_key = f"table/{key}/widths" if key else None
        self._settings = settings
        self._restoring = False

        self.verticalHeader().setVisible(False)
        # read-only is the default (a report table); the classical force-field
        # tables are the ones the user types into
        self.setEditTriggers(
            (QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
             | QAbstractItemView.SelectedClicked | QAbstractItemView.AnyKeyPressed)
            if editable else QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setShowGrid(True)
        self.setWordWrap(False)
        # an editable table must not reorder itself under the cursor - and for the
        # &MOL_SET table the row order *is* meaningful (it has to match the order
        # of the molecules in the single coordinate file)
        self.setSortingEnabled(sortable and not editable)

        head = self.horizontalHeader()
        head.setSectionsMovable(False)
        head.setHighlightSections(False)
        # Interactive is what makes the column boundaries draggable
        for column in range(len(headers)):
            head.setSectionResizeMode(column, QHeaderView.Interactive)
        head.setContextMenuPolicy(Qt.CustomContextMenu)
        head.customContextMenuRequested.connect(self._header_menu)

        # A table is a scrolling container: it must not demand the width of all its
        # columns from the panel around it.  Qt's default minimum size hint *is* that
        # sum, which made the parameter panel 950 px wide (the PLUMED CV table wants
        # 720) and put a horizontal scrollbar under the whole form.  With Ignored the
        # parent decides the width and the table scrolls sideways by itself.
        policy = self.sizePolicy()
        policy.setHorizontalPolicy(QSizePolicy.Ignored)
        self.setSizePolicy(policy)
        self.setMinimumWidth(260)

        # applying the default widths must not count as "the user chose this",
        # otherwise it overwrites the remembered widths before they are read
        self._restoring = True
        for column, width in enumerate(self._default_widths):
            self.setColumnWidth(column, width)
        self._restore_widths()
        self._restoring = False
        head.sectionResized.connect(self._on_section_resized)

    # -- widths ------------------------------------------------------------ #
    def minimumSizeHint(self) -> QSize:
        """A table scrolls its own columns, so they set no minimum for its parent.

        Qt's ``QTableView`` reports the sum of the column widths here, and a layout
        takes that seriously: the PLUMED CV table (110+190+330+90) then made the
        whole parameter panel 950 px wide and pushed a horizontal scrollbar under
        the form.  What the panel needs is a usable table, not a fully visible one -
        the table gets its own scrollbar instead.
        """
        hint = super().minimumSizeHint()
        return QSize(260, hint.height())

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        return QSize(min(max(hint.width(), 260), 520), hint.height())

    def _restore_widths(self) -> None:
        if not (self._settings and self._settings_key):
            return
        stored = self._settings.value(self._settings_key)
        if not stored:
            return
        try:
            widths = [int(value) for value in stored]
        except (TypeError, ValueError):
            return
        if len(widths) != self.columnCount():
            return
        for column, width in enumerate(widths):
            if width > 10:
                self.setColumnWidth(column, width)

    def _save_widths(self) -> None:
        if not (self._settings and self._settings_key) or self._restoring:
            return
        self._settings.setValue(self._settings_key,
                                [self.columnWidth(c) for c in range(self.columnCount())])

    def _on_section_resized(self, *_args) -> None:
        self._save_widths()

    def autofit(self) -> None:
        """Fit every column to its contents, like double-clicking a boundary."""
        head = self.horizontalHeader()
        for column in range(self.columnCount()):
            head.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        for column in range(self.columnCount()):
            head.setSectionResizeMode(column, QHeaderView.Interactive)
        self._save_widths()

    def reset_widths(self) -> None:
        for column, width in enumerate(self._default_widths):
            self.setColumnWidth(column, width)
        self._save_widths()

    def _header_menu(self, position) -> None:
        menu = QMenu(self)
        menu.addAction("自适应列宽", self.autofit)
        menu.addAction("重置列宽", self.reset_widths)
        menu.addSeparator()
        menu.addAction("复制选中内容", self.copy_selection)
        menu.exec(self.horizontalHeader().mapToGlobal(position))

    # -- content ----------------------------------------------------------- #
    def set_row(self, row: int, values: list, keys: list | None = None) -> None:
        """Fill a row; ``keys`` gives the sort key of each cell when it differs."""
        for column, value in enumerate(values):
            key = keys[column] if keys else None
            self.setItem(row, column, SortItem(str(value), key))

    def add_row(self, values: list, keys: list | None = None) -> int:
        """Append a row.

        Sorting is switched off while the row is filled: with it on, Qt moves
        the half-filled row as soon as the first cell lands, so the remaining
        cells go into a row that has already travelled (and show up as empty).
        """
        sorting = self.isSortingEnabled()
        if sorting:
            self.setSortingEnabled(False)
        row = self.rowCount()
        self.insertRow(row)
        self.set_row(row, values, keys)
        if sorting:
            self.setSortingEnabled(True)
        return row

    # -- clipboard --------------------------------------------------------- #
    def copy_selection(self) -> None:
        ranges = self.selectedRanges()
        if not ranges:
            return
        area = ranges[0]
        lines = []
        for row in range(area.topRow(), area.bottomRow() + 1):
            cells = []
            for column in range(area.leftColumn(), area.rightColumn() + 1):
                item = self.item(row, column)
                cells.append(item.text() if item is not None else "")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.matches(QKeySequence.Copy):
            self.copy_selection()
            return
        super().keyPressEvent(event)


class HelpBadge(QLabel):
    """A small "?" that explains a parameter on hover.

    Hovering shows the guidance text (and which manual keyword it maps to);
    clicking opens that page in the browser when the parameter has one.
    """

    def __init__(self, help_entry: Help, parent=None):
        super().__init__("?", parent)
        self.help_entry = help_entry
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignCenter)
        self.setToolTip(help_entry.tooltip())
        self.setStyleSheet(
            "QLabel {"
            "  background:#d6e4f0; color:#1f4e79;"
            "  border:1px solid #7fa8cc; border-radius:8px;"
            "  font-size:11px; font-weight:bold;"
            "}")
        if help_entry.url:
            self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.help_entry.url:
            QDesktopServices.openUrl(self.help_entry.url)
        super().mousePressEvent(event)


def help_badge(key: str | None) -> HelpBadge | None:
    entry = HELP.get(key) if key else None
    return HelpBadge(entry) if entry else None


#: the diagnostic code the generator's own warnings are shown under (they are not
#: schema or physics findings, so they get a code of their own)
BUILD_WARNING_CODE = "build-warning"

#: ... and the one for the findings the generator considers an **error** (no
#: coordinates at all, for instance: CP2K aborts on that input)
BUILD_ERROR_CODE = "build-error"


def build_warnings(result: BuildResult) -> list[Diagnostic]:
    """``BuildResult.warnings``/``errors`` as diagnostic rows for 校验结果.

    ``build()`` reports the things the *generator* knows are wrong or useless (a
    metadynamics block on a non-MD task, ``h_to_d`` on a system without hydrogen,
    ...).  They used to be a tooltip on the summary only - so a run whose only
    problem was one of them showed "1 项生成期问题" above an empty table.

    The two levels are kept apart: what the generator itself knows makes CP2K
    abort is an error row, everything else a warning row.
    """
    rows = [Diagnostic(level="warning", code=BUILD_WARNING_CODE, message=text)
            for text in getattr(result, "warnings", [])]
    rows += [Diagnostic(level="error", code=BUILD_ERROR_CODE, message=text)
             for text in getattr(result, "errors", [])]
    return rows


def without_covered(diagnostics: list[Diagnostic], result: BuildResult
                    ) -> list[Diagnostic]:
    """Drop the validator rows the generator has already answered for.

    Some findings are visible from both sides - the generator knows a
    force-field route has no ``PARM_FILE_NAME`` *and* the validator has a rule
    for it.  Printing both made the same sentence appear twice at two different
    levels (``警告 AMBER: no PARM_FILE_NAME`` right next to
    ``错误 PARMTYPE AMBER ... PARM_FILE_NAME is not set``), which reads like the
    tool disagreeing with itself.  :attr:`BuildResult.covered_codes` names those
    rules, and the generator's own row - the one that names the control to fill
    in - is the one that stays.
    """
    covered = getattr(result, "covered_codes", None)
    if not covered:
        return list(diagnostics)
    return [item for item in diagnostics if item.code not in covered]


def form_row(form: QFormLayout, key: str | None, label: str,
             widget: QWidget, badge: "HelpBadge | None" = None) -> QWidget:
    """Add a form row; a "?" badge appears next to the label when help exists."""
    entry = HELP.get(key) if key else None
    if entry is None:
        form.addRow(label, widget)
        return widget
    holder = QWidget()
    box = QHBoxLayout(holder)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(5)
    box.addWidget(QLabel(label))
    badge = badge or HelpBadge(entry)
    box.addWidget(badge)
    box.addStretch(1)
    form.addRow(holder, widget)
    # The guidance lives on the "?" only: a tooltip on the control itself would
    # cover the rest of the form while you are trying to type in it.
    return widget


def check_row(form: QFormLayout, key: str | None, widget: QCheckBox,
              badge: "HelpBadge | None" = None) -> QWidget:
    """Add a checkbox row with a trailing "?" badge."""
    entry = HELP.get(key) if key else None
    if entry is None:
        form.addRow(widget)
        return widget
    holder = QWidget()
    box = QHBoxLayout(holder)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(5)
    box.addWidget(widget)
    box.addWidget(badge or HelpBadge(entry))
    box.addStretch(1)
    form.addRow(holder)
    return widget


def section_label(text: str) -> QLabel:
    """A small heading that splits one group into sub-sections."""
    label = QLabel(text)
    label.setStyleSheet("color:#34495e; font-weight:600;")
    return label


def hint_label() -> QLabel:
    """A grey, wrapping one-liner under a row; updated live."""
    label = QLabel()
    label.setWordWrap(True)
    label.setStyleSheet("color:#5d6d7e;")
    return label


def set_hint(label: QLabel, text: str, level: str = "info") -> None:
    label.setText(text)
    label.setStyleSheet("color:#b9770e;" if level == "warning" else "color:#5d6d7e;")


def label_with_help(text: str, key: str | None) -> QWidget:
    """A standalone label carrying a "?" badge, for rows without a control."""
    entry = HELP.get(key) if key else None
    holder = QWidget()
    box = QHBoxLayout(holder)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(5)
    label = QLabel(text)
    label.setWordWrap(True)
    box.addWidget(label)
    if entry is not None:
        box.addWidget(HelpBadge(entry))
    box.addStretch(1)
    return holder


def file_field_row(form: QFormLayout, key: str | None, label: str,
                   placeholder: str, on_browse) -> tuple[QLineEdit, QWidget]:
    """A path field with a "浏览…" button, as one form row.

    Returns ``(field, holder)``: the coupling table greys the *holder* so that
    the button is disabled together with the field, and ``_row_badges`` finds the
    row's "?" through it.
    """
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    button = QToolButton()
    button.setText("浏览…")
    button.clicked.connect(on_browse)
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(field, 1)
    row.addWidget(button)
    form_row(form, key, label, holder)
    return field, holder


class CollapsibleBox(QGroupBox):
    """A group box whose body folds away - for long blocks that are rarely used.

    The title stays visible (that is the one-line header); the arrow button on the
    first row folds the body.  The state is remembered per ``key``, so a block the
    user keeps open stays open across sessions - and a collapsed block takes no
    vertical space, which is the whole point on a form this long.

    Put the block's controls into ``body``::

        box = CollapsibleBox("6. 续算", key="restart", settings=settings)
        form = QFormLayout(box.body)
    """

    def __init__(self, title: str, key: str | None = None,
                 settings: QSettings | None = None, expanded: bool = False,
                 parent=None):
        super().__init__(title, parent)
        self._settings_key = f"fold/{key}" if key else None
        self._settings = settings
        self.body = QWidget(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.toggle = QToolButton(self)
        self.toggle.setCheckable(True)
        self.toggle.setAutoRaise(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        # no tooltip on the button itself: the guidance in this interface lives on
        # the row's "?" badge only (a tooltip here would cover the block)
        self.toggle.clicked.connect(self._on_toggle)
        header = QWidget(self)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(self.toggle)
        header_row.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 6)
        outer.setSpacing(4)
        outer.addWidget(header)
        outer.addWidget(self.body)
        if self._settings is not None and self._settings_key:
            stored = self._settings.value(self._settings_key)
            if stored is not None:
                expanded = str(stored).strip().lower() in ("true", "1", "yes", "on")
        self.set_expanded(expanded, save=False)

    def set_expanded(self, expanded: bool, save: bool = True) -> None:
        expanded = bool(expanded)
        self.toggle.setChecked(expanded)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.setText("收起" if expanded else "展开")
        self.body.setVisible(expanded)
        if save and self._settings is not None and self._settings_key:
            self._settings.setValue(self._settings_key,
                                    "true" if expanded else "false")

    def is_expanded(self) -> bool:
        return bool(self.toggle.isChecked())

    def expand(self) -> None:
        self.set_expanded(True)

    def _on_toggle(self) -> None:
        self.set_expanded(self.toggle.isChecked())


# --------------------------------------------------------------------------- #
# backend loading
# --------------------------------------------------------------------------- #
class LoaderThread(QThread):
    """Builds the data index and loads the manual database off the GUI thread."""

    ready = Signal(object, object, str)

    def __init__(self, data_dir: str | None, parent=None):
        super().__init__(parent)
        self.data_dir = data_dir

    def run(self) -> None:  # noqa: D102
        try:
            resolved = discover_data_dir(self.data_dir)
            index = DataIndex(resolved) if resolved else DataIndex(None)
            db = load_db()
            self.ready.emit(index, db, "")
        except Exception as exc:  # noqa: BLE001 - report anything to the GUI
            self.ready.emit(None, None, f"{exc}\n{traceback.format_exc()}")


# --------------------------------------------------------------------------- #
# generate tab
# --------------------------------------------------------------------------- #
class GenerateTab(QWidget):
    """Structure + task + parameters, with a live preview."""

    status = Signal(str)

    def __init__(self, settings: QSettings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.index: DataIndex | None = None
        self.db: ManualDB | None = None
        self.structure: Structure | None = None
        self.structure_error: str | None = None
        self.text = ""
        self._badge_cache: dict[QWidget, HelpBadge] = {}
        self._badge_base: dict[HelpBadge, str] = {}

        self.setAcceptDrops(True)
        self._build_ui()

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.refresh)
        self._connect_live_updates()
        self._route_changed()          # show the block of the active route
        self._task_changed()           # ... and the page of the active task
        # (the task pages are shown/hidden, so the first one has to be shown here:
        #  a combo signal only fires when the index actually changes)

    # -- construction ------------------------------------------------------ #
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal, self)

        # ---- left: form ----
        form_host = QWidget()
        form = QVBoxLayout(form_host)
        form.setContentsMargins(10, 10, 10, 10)
        form.setSpacing(8)

        form.addWidget(self._structure_group())
        form.addWidget(self._task_group())
        self.classical_box = self._classical_group()
        form.addWidget(self.classical_box)
        self.method_box = self._method_group()
        form.addWidget(self.method_box)
        self.kpoint_box = self._kpoint_group()
        form.addWidget(self.kpoint_box)
        # the route summary stays *outside* the foldable details block: which SCF
        # route this run takes is worth seeing at a glance
        self.route_summary = QWidget()
        summary_row = QHBoxLayout(self.route_summary)
        summary_row.setContentsMargins(0, 0, 0, 0)
        self.route_label = QLabel()
        self.route_label.setWordWrap(True)
        summary_row.addWidget(self.route_label, 1)
        summary_row.addWidget(HelpBadge(HELP["route_summary"]))
        form.addWidget(self.route_summary)
        self.route_box = self._route_group()
        form.addWidget(self.route_box)
        form.addWidget(self._system_group())
        form.addWidget(self._output_group())
        form.addWidget(self._restart_group())
        self.classical_box.setVisible(False)

        buttons = QHBoxLayout()
        # one button for both files: the *preview page* decides what gets saved
        # (CP2K input or plumed.dat + its default file name), so the label says
        # "保存" and not "保存为 .inp"
        self.save_button = QPushButton("保存")
        self.save_button.clicked.connect(self.save)
        self.copy_button = QPushButton("复制")
        self.copy_button.clicked.connect(self.copy)
        reset = QPushButton("恢复默认")
        reset.clicked.connect(self.reset_defaults)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.copy_button)
        buttons.addWidget(reset)
        buttons.addWidget(HelpBadge(HELP["save_button"]))
        buttons.addStretch(1)
        # The three actions belong to the *panel*, not to the form: they stay put
        # while the (long) form scrolls.  Inside the scroll area they sat at the
        # very bottom, so on a short window you had to scroll all the way down to
        # save what you had just typed.
        self.form_footer = QWidget()
        footer_layout = QHBoxLayout(self.form_footer)
        footer_layout.setContentsMargins(10, 6, 10, 8)
        footer_layout.addLayout(buttons)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        scroll.setFrameShape(QFrame.NoFrame)
        # the form is long, so vertical scrolling is expected; sideways scrolling
        # is not - the rows are made narrow enough to fit (see _narrow_widgets)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.form_host = form_host
        self.form_scroll = scroll      # "jump to this parameter" scrolls to it

        # ---- left: the scrolling form with a fixed action bar under it ----
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(scroll, 1)
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setFrameShadow(QFrame.Sunken)
        left_layout.addWidget(separator)
        left_layout.addWidget(self.form_footer)
        self.form_column = left
        splitter.addWidget(left)

        # ---- right: preview + diagnostics ----
        right = QSplitter(Qt.Vertical)

        preview_box = QGroupBox("生成的文件（实时预览）")
        preview_layout = QVBoxLayout(preview_box)
        preview_header = QHBoxLayout()
        self.preview_hint = QLabel("改动左侧任意控件后这里会自动重算")
        self.preview_hint.setStyleSheet("color:#5d6d7e;")
        preview_header.addWidget(self.preview_hint)
        preview_header.addStretch(1)
        # the PLUMED route produces two files: the CP2K input *and* plumed.dat, so
        # the preview gets a second page (shown only while that route is active)
        self.preview_page = QComboBox()
        self.preview_page.addItem("CP2K 输入", 0)
        self.preview_page.addItem("plumed.dat", 1)
        self.preview_page.currentIndexChanged.connect(self._preview_page_changed)
        preview_header.addWidget(self.preview_page)
        self.preview_page.setVisible(False)
        preview_header.addWidget(HelpBadge(HELP["preview"]))
        preview_layout.addLayout(preview_header)
        self.preview_stack = QStackedWidget()
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(mono_font())
        self.preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.preview_stack.addWidget(self.preview)
        self.plumed_preview = QPlainTextEdit()
        self.plumed_preview.setReadOnly(True)
        self.plumed_preview.setFont(mono_font())
        self.plumed_preview.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.preview_stack.addWidget(self.plumed_preview)
        preview_layout.addWidget(self.preview_stack)
        right.addWidget(preview_box)

        diag_box = QGroupBox("校验结果")
        diag_layout = QVBoxLayout(diag_box)
        summary_row = QHBoxLayout()
        self.summary = QLabel("等待生成…")
        self.summary.setTextFormat(Qt.RichText)
        summary_row.addWidget(self.summary, 1)
        summary_row.addWidget(HelpBadge(HELP["diagnostics"]))
        diag_layout.addLayout(summary_row)
        self.diagnostics = ExcelTable(
            ["级别", "类型", "说明"], widths=[60, 190, 520], key="diagnostics",
            settings=self.settings, sortable=False)
        diag_layout.addWidget(self.diagnostics)
        right.addWidget(diag_box)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.form_splitter = splitter
        self._narrow_widgets(form_host)
        # A fixed 700 px was 10 px short of the panel's own minimum (the vertical
        # scrollbar eats into the viewport), so the horizontal scrollbar was there
        # from the start and the right-hand end of every row was hidden.  Ask the
        # form how wide it actually wants to be instead of guessing.
        self.form_splitter.setSizes([self._needed_form_width(), 760])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    def _needed_form_width(self) -> int:
        """How wide the parameter panel has to be to avoid sideways scrolling.

        The scroll area's viewport is narrower than the panel by the vertical
        scrollbar, and the content must also fit its own margins - both are easy
        to forget and both show up as "the end of every row is cut off".
        """
        scrollbar = self.form_scroll.verticalScrollBar().sizeHint().width()
        need = self.form_host.minimumSizeHint().width() + scrollbar + 4
        return max(THIN_FORM_WIDTH, min(need, 900))

    @staticmethod
    def _narrow_widgets(host: QWidget) -> None:
        """Keep the parameter panel from needing horizontal scrolling.

        A ``QComboBox`` is as wide as its longest entry by default (``MODIFIED
        BROYDEN_MIXING`` is wide!) and a ``QAbstractSpinBox`` as wide as its
        largest value plus suffix.  In a panel that holds six stacked groups
        those widths add up to more than any sane window, and the panel ends up
        with a horizontal scrollbar - so the right-hand part of every row is
        hidden until you scroll sideways.  Both can be capped without losing
        anything: the popup still shows the full text, and the field column
        still stretches into whatever width the panel actually has.
        """
        for combo in host.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(
                QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            # minimumContentsLength caps the *hint*, not the minimum: a combo
            # whose items are whole sentences would still force the panel wide.
            # The popup always shows the full text, and the "?" explains it.
            if combo.minimumSizeHint().width() > 320:
                combo.setMaximumWidth(320)
        for spin in (host.findChildren(QSpinBox)
                     + host.findChildren(QDoubleSpinBox)):
            # a ceiling, and a floor taken from the text it actually shows: a spin
            # whose special value is a sentence ("CP2K 默认（1 份）") was squeezed
            # until the last characters were elided
            cap = 170
            if spin.minimumSizeHint().width() > cap:
                spin.setMaximumWidth(cap)
            wanted = spin.fontMetrics().horizontalAdvance(spin.text()) + 40
            if spin.minimumWidth() < wanted <= cap:
                spin.setMinimumWidth(wanted)
        # Text widgets that the row layouts happily squeeze below the width their
        # own label needs: a checkbox then elides the last character ("…（&MO_CU")
        # and a small button loses its "…".  Their rows have room, so give them the
        # width of the text they actually show.
        for check in host.findChildren(QCheckBox):
            # full text is the ideal, but one 500 px label ("接力时接着这个斜坡走
            # （RESTART_TEMPERATURE_ANNEALING）") sets the width of the whole panel:
            # cap it, the longest ones elide with "…" and their "?" says the rest
            check.setMinimumWidth(min(check.sizeHint().width(), 420))
        for button in host.findChildren(QToolButton):
            button.setMinimumWidth(button.sizeHint().width())
        for combo in host.findChildren(QComboBox):
            needed = (combo.fontMetrics().horizontalAdvance(combo.currentText()) + 42)
            combo.setMinimumWidth(min(200, max(combo.minimumWidth(), needed)))
        for line in host.findChildren(QLineEdit):
            line.setMinimumWidth(60)

    def _structure_group(self) -> QGroupBox:
        box = QGroupBox("1. 结构")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        self.structure_edit = QLineEdit()
        self.structure_edit.setPlaceholderText(
            "xyz / pdb / POSCAR / cif / .inp / <项目名>-1.restart（也可以把文件拖进来）")
        self.structure_edit.editingFinished.connect(self.load_structure)
        browse = QToolButton()
        browse.setText("浏览…")
        browse.clicked.connect(self.browse_structure)
        # an .inp / -1.restart also carries the *parameters*: this reads them back
        # into the form, so what you see matches the file you pointed at
        self.w_import = QToolButton()
        self.w_import.setText("读参数")
        self.w_import.setEnabled(False)
        self.w_import.setToolTip("")
        self.w_import.clicked.connect(self.import_parameters)
        row.addWidget(self.structure_edit, 1)
        row.addWidget(HelpBadge(HELP["structure"]))
        row.addWidget(self.w_import)
        row.addWidget(HelpBadge(HELP["import_params"]))
        row.addWidget(browse)
        layout.addLayout(row)

        self.structure_info = QLabel("尚未选择结构文件")
        self.structure_info.setWordWrap(True)
        self.structure_info.setStyleSheet("color:#5d6d7e;")
        layout.addWidget(self.structure_info)

        # what 读参数 did: one link per parameter, and clicking it scrolls the
        # form to that control
        self.import_info = QLabel()
        self.import_info.setTextFormat(Qt.RichText)
        self.import_info.setWordWrap(True)
        self.import_info.setVisible(False)
        self.import_info.linkActivated.connect(self._jump_to_field)
        layout.addWidget(self.import_info)
        self._importable: Path | None = None

        kind_row = QHBoxLayout()
        kind_title = QLabel("基组与赝势（自动选择）")
        kind_title.setStyleSheet("color:#5d6d7e;")
        kind_row.addWidget(kind_title)
        kind_row.addWidget(HelpBadge(HELP["kind_table"]))
        kind_row.addStretch(1)
        # the basis-set table only exists on the Quickstep route, so it lives in
        # its own container that the FIST page can hide
        self.kind_box = QWidget()
        kind_layout = QVBoxLayout(self.kind_box)
        kind_layout.setContentsMargins(0, 0, 0, 0)
        kind_layout.addLayout(kind_row)
        layout.addWidget(self.kind_box)

        self.kind_table = QTableWidget(0, 4)
        self.kind_table.setHorizontalHeaderLabels(["元素", "基组", "价", "赝势"])
        self.kind_table.verticalHeader().setVisible(False)
        self.kind_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.kind_table.setMaximumHeight(130)
        kind_head = self.kind_table.horizontalHeader()
        for column in range(self.kind_table.columnCount()):
            kind_head.setSectionResizeMode(column, QHeaderView.Interactive)
        self.kind_table.setVisible(False)
        kind_layout.addWidget(self.kind_table)

        # ---- deuterium: H -> D is a mass change, and it buys a 1 fs time step -- #
        h_row = QHBoxLayout()
        h_row.setContentsMargins(0, 0, 0, 0)
        self.w_h_to_d = QCheckBox("氘代（H→D：氢用氘的质量 2.0141 u）")
        self.w_h_to_d.toggled.connect(self._h_to_d_changed)
        h_row.addWidget(self.w_h_to_d)
        h_row.addWidget(HelpBadge(HELP["h_to_d"]))
        h_row.addStretch(1)
        kind_layout.addLayout(h_row)
        self.h_to_d_hint = hint_label()
        kind_layout.addWidget(self.h_to_d_hint)
        self._h_to_d_changed(False)      # say what the switch would do
        return box

    def _h_to_d_changed(self, on: bool) -> None:
        """Say what deuterating does - and that it is a real isotope, not a knob.

        The point of H -> D in AIMD is the time step (the X-H stretch period goes
        from ~10 fs to ~14 fs), so the hint names the file line that changes and
        the time step it makes affordable; the "?" carries the physics.
        """
        if not hasattr(self, "h_to_d_hint"):
            return
        elements = {str(e).upper()
                    for e in getattr(getattr(self, "structure", None), "elements", [])}
        if not on:
            set_hint(self.h_to_d_hint,
                     "不写 `MASS`，氢用元素默认质量 1.008 u"
                     "（要跑 1 fs 的 AIMD 就勾上：X–H 伸缩周期 ~10 fs → ~14 fs）。")
        elif not elements:
            set_hint(self.h_to_d_hint,
                     "会把 `&KIND H` 写成 `MASS 2.0141017781`；"
                     "先载入结构文件才能看出体系里有没有氢。")
        elif "H" not in elements:
            set_hint(self.h_to_d_hint,
                     "⚠ 当前体系里没有氢，写不写这个质量都没有区别。",
                     level="warning")
        else:
            set_hint(self.h_to_d_hint,
                     "会写 `&KIND H / MASS 2.0141017781`（元素、基组、赝势都不变）："
                     "X–H 伸缩周期 ~10 → ~14 fs，MD 的 TIMESTEP 可以从 0.5 fs 提到 "
                     "1.0 fs（同样时长省一半机时）。结果是**重同位素**体系：静态结构"
                     "几乎不变，扩散/谱/速率会变。")
        self._apply_couplings()
        self.schedule()

    def _task_group(self) -> QGroupBox:
        box = QGroupBox("2. 任务与计算方法")
        layout = QVBoxLayout(box)

        self.w_method = QComboBox()
        for label, value in METHOD_LABELS:
            self.w_method.addItem(label, value)
        self.w_method.currentIndexChanged.connect(self._method_changed)
        self.method_form = QFormLayout()
        self.method_form.setLabelAlignment(Qt.AlignRight)
        form_row(self.method_form, "method", "计算方法", self.w_method)
        layout.addLayout(self.method_form)

        self.task_combo = QComboBox()
        for task in TASKS:
            self.task_combo.addItem(TASK_LABELS.get(task, task), task)
        self.task_combo.currentIndexChanged.connect(self._task_changed)
        task_row = QHBoxLayout()
        task_row.addWidget(self.task_combo, 1)
        task_row.addWidget(HelpBadge(HELP["task"]))
        layout.addLayout(task_row)

        # The task pages are **shown/hidden** rather than stacked.  A
        # QStackedWidget is sized by the tallest page it holds - the MD page is
        # ~2100 px (temperature program table, print keys, 元动力学) versus ~70 px
        # for a single point calculation - so every other task sat on top of a
        # ~2000 px blank area, and Qt does not re-query the stack's size hint when
        # the page changes.  A hidden widget, on the other hand, is left out of the
        # layout, so the panel is exactly as long as the task actually needs.
        self.task_host = QWidget()
        self.task_host_layout = QVBoxLayout(self.task_host)
        self.task_host_layout.setContentsMargins(0, 0, 0, 0)
        self.task_pages: dict[str, QWidget] = {}
        for task in TASKS:
            page = self._make_task_page(task)
            self.task_pages[task] = page
            self.task_host_layout.addWidget(page)
            page.setVisible(False)
        layout.addWidget(self.task_host)
        return box

    # -- task-specific pages ---------------------------------------------- #
    def _make_task_page(self, task: str) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 4, 0, 0)
        form.setLabelAlignment(Qt.AlignRight)

        if task == "energy":
            self.w_print_forces = QCheckBox("打印受力")
            self.w_print_forces.setChecked(True)
            self.w_print_mulliken = QCheckBox("Mulliken 电荷布居")
            self.w_print_dipole = QCheckBox("偶极矩（仅非周期体系）")
            self.w_print_dipole.setChecked(True)
            check_row(form, "print_forces", self.w_print_forces)
            check_row(form, "print_mulliken", self.w_print_mulliken)
            check_row(form, "print_dipole", self.w_print_dipole)

        elif task == "geo_opt":
            self.w_geo_optimizer = QComboBox()
            self.w_geo_optimizer.addItems(["BFGS", "LBFGS", "CG"])
            self.w_geo_max_iter = QSpinBox()
            self.w_geo_max_iter.setRange(1, 100000)
            self.w_geo_max_iter.setValue(200)
            form_row(form, "geo_optimizer", "优化器", self.w_geo_optimizer)
            form_row(form, "geo_max_iter", "最大步数", self.w_geo_max_iter)

        elif task == "cell_opt":
            self.w_cell_optimizer = QComboBox()
            self.w_cell_optimizer.addItems(["BFGS", "LBFGS", "CG"])
            self.w_cell_max_iter = QSpinBox()
            self.w_cell_max_iter.setRange(1, 100000)
            self.w_cell_max_iter.setValue(200)
            self.w_pressure = QDoubleSpinBox()
            self.w_pressure.setRange(-1000.0, 1000.0)
            self.w_pressure.setDecimals(3)
            self.w_pressure.setSuffix(" bar")
            self.w_keep_angles = QCheckBox("固定晶胞角度")
            form_row(form, "cell_optimizer", "优化器", self.w_cell_optimizer)
            form_row(form, "cell_max_iter", "最大步数", self.w_cell_max_iter)
            form_row(form, "cell_pressure", "外压", self.w_pressure)
            check_row(form, "keep_angles", self.w_keep_angles)

        elif task == "md":
            # the same page serves AIMD and classical MD: name the one the
            # current method gives (see _method_changed)
            self.md_kind_hint = label_with_help("", "aimd_or_classical")
            form.addRow(self.md_kind_hint)
            self.w_ensemble = QComboBox()
            self.w_ensemble.addItems(ENSEMBLES)
            self.w_ensemble.setCurrentText("NVT")
            self.w_thermostat = QComboBox()
            self.w_thermostat.addItems(THERMOSTATS)
            self.w_temperature = QDoubleSpinBox()
            self.w_temperature.setRange(1.0, 10000.0)
            self.w_temperature.setValue(300.0)
            self.w_temperature.setSuffix(" K")
            # the relay hint quotes this temperature ("velocities restarted at
            # 300 K"), so keep it in step while it is edited
            self.w_temperature.valueChanged.connect(self._temperature_changed)
            self.w_timestep = QDoubleSpinBox()
            self.w_timestep.setRange(0.05, 10.0)
            self.w_timestep.setSingleStep(0.1)
            self.w_timestep.setValue(0.5)
            self.w_timestep.setSuffix(" fs")
            self.w_steps = QSpinBox()
            self.w_steps.setRange(1, 100000000)
            self.w_steps.setValue(5000)
            self.w_md_pressure = QDoubleSpinBox()
            self.w_md_pressure.setRange(-1000.0, 100000.0)
            self.w_md_pressure.setValue(1.0)
            self.w_md_pressure.setSuffix(" bar")
            self.w_seed = QSpinBox()
            self.w_seed.setRange(0, 2_000_000_000)
            self.w_seed.setSpecialValueText("不设置")
            self.w_thermostat_timecon = QDoubleSpinBox()
            self.w_thermostat_timecon.setRange(1.0, 100000.0)
            self.w_thermostat_timecon.setValue(100.0)
            self.w_thermostat_timecon.setSuffix(" fs")
            self.w_barostat_timecon = QDoubleSpinBox()
            self.w_barostat_timecon.setRange(1.0, 100000.0)
            self.w_barostat_timecon.setValue(500.0)
            self.w_barostat_timecon.setSuffix(" fs")
            self.w_trajectory_stride = QSpinBox()
            self.w_trajectory_stride.setRange(1, 1000000)
            self.w_trajectory_stride.setValue(10)
            self.w_trajectory_stride.setSuffix(" 步")
            # ---- one thermostat or one per region ---- #
            self.w_thermostat_region = QComboBox()
            for label, value in THERMOSTAT_REGIONS:
                self.w_thermostat_region.addItem(label, value)
            # ---- what the run writes while it runs (temperature / energy) ---- #
            self.w_md_temp_kind = QCheckBox(
                "每个原子种类的温度（&MD TEMP_KIND + &PRINT）")
            self.w_md_temp_kind_every = QSpinBox()
            self.w_md_temp_kind_every.setRange(1, 1000000)
            self.w_md_temp_kind_every.setValue(10)
            self.w_md_temp_kind_every.setSuffix(" 步")
            self.w_md_energy = QCheckBox("能量文件 -1.ener（含 Temp[K] 列）")
            self.w_md_energy_every = QSpinBox()
            self.w_md_energy_every.setRange(1, 1000000)
            self.w_md_energy_every.setValue(1)
            self.w_md_energy_every.setSuffix(" 步")
            self.w_md_energy_file = QLineEdit()
            self.w_md_energy_file.setPlaceholderText(
                "留空 = <项目名>-1.ener；填名字 = <项目名>-名字-1.ener")
            self.w_thermostat_temp = QCheckBox(
                "热浴区域温度（&THERMOSTAT/&PRINT/&TEMPERATURE）")
            self.w_thermostat_temp_every = QSpinBox()
            self.w_thermostat_temp_every.setRange(1, 1000000)
            self.w_thermostat_temp_every.setValue(10)
            self.w_thermostat_temp_every.setSuffix(" 步")
            # ---- heating / cooling (CP2K's own &MD annealing keywords) ---- #
            self.w_ramp = QCheckBox("升温 / 降温到这个温度")
            self.w_ramp_target = QDoubleSpinBox()
            self.w_ramp_target.setRange(1.0, 10000.0)
            self.w_ramp_target.setValue(600.0)
            self.w_ramp_target.setSuffix(" K")
            self.w_temperature_annealing = QDoubleSpinBox()
            self.w_temperature_annealing.setRange(0.0, 0.0)      # set below
            self.w_temperature_annealing.setDecimals(12)
            self.w_temperature_annealing.setSingleStep(0.01)
            self.w_annealing = QDoubleSpinBox()
            self.w_annealing.setRange(0.0, 0.0)
            self.w_annealing.setDecimals(12)
            self.w_annealing.setSingleStep(0.01)
            self.w_annealing_cell = QDoubleSpinBox()
            self.w_annealing_cell.setRange(0.0, 0.0)
            self.w_annealing_cell.setDecimals(12)
            self.w_annealing_cell.setSingleStep(0.01)
            for box, top in ((self.w_temperature_annealing, 10.0),
                             (self.w_annealing, 10.0),
                             (self.w_annealing_cell, 10.0)):
                # 0 = "do not write the keyword"; CP2K's own default is 1.0
                box.setRange(0.0, top)
                box.setSpecialValueText("不写")
            self.w_langevin_gamma = QDoubleSpinBox()
            self.w_langevin_gamma.setRange(0.0, 1.0)
            self.w_langevin_gamma.setDecimals(6)
            self.w_langevin_gamma.setSingleStep(0.001)
            self.w_langevin_gamma.setValue(0.01)
            self.w_langevin_gamma.setSuffix(" fs⁻¹")
            self.w_restart_temperature_annealing = QCheckBox(
                "接力时接着这个斜坡走（RESTART_TEMPERATURE_ANNEALING）")
            self.ramp_hint = hint_label()
            form_row(form, "ensemble", "系综", self.w_ensemble)
            form_row(form, "thermostat", "恒温器", self.w_thermostat)
            form_row(form, "thermostat_region", "热浴区域", self.w_thermostat_region)
            form_row(form, "temperature", "温度", self.w_temperature)
            form_row(form, "timestep", "时间步长", self.w_timestep)
            form_row(form, "steps", "步数", self.w_steps)
            form_row(form, "md_pressure", "压力", self.w_md_pressure)
            form_row(form, "thermostat_timecon", "恒温器耦合", self.w_thermostat_timecon)
            form_row(form, "barostat_timecon", "恒压器耦合", self.w_barostat_timecon)
            form_row(form, "trajectory_stride", "轨迹间隔", self.w_trajectory_stride)
            form_row(form, "seed", "随机数种子", self.w_seed)
            form.addRow(section_label(
                "升温 / 降温（单段：只影响「保存」的那一份输入）"))
            row = QHBoxLayout()
            row.addWidget(self.w_ramp)
            row.addWidget(self.w_ramp_target)
            row.addWidget(help_badge("md_ramp"))
            row.addStretch(1)
            form.addRow(row)
            form_row(form, "temperature_annealing", "目标温度因子", self.w_temperature_annealing)
            form_row(form, "annealing", "速度缩放因子", self.w_annealing)
            form_row(form, "annealing_cell", "晶胞速度因子", self.w_annealing_cell)
            form_row(form, "langevin_gamma", "Langevin 摩擦", self.w_langevin_gamma)
            check_row(form, "restart_temperature_annealing",
                      self.w_restart_temperature_annealing)
            form.addRow(self.ramp_hint)
            # The multi-stage program is a whole workflow of its own (one input per
            # stage plus a driver script), and it is the rare half of the two
            # heating/cooling routes - so it folds away like the other long blocks
            # and stays that way across sessions.
            self.temperature_program_box = CollapsibleBox(
                "温度程序（多段：保持 / 升温 / 降温）",
                key="temperature_program", settings=self.settings)
            program_form = QFormLayout(self.temperature_program_box.body)
            program_form.setContentsMargins(0, 4, 0, 0)
            program_form.addRow(section_label(
                "每段一个输入 + run_program.sh（多段的升温 / 降温走这里）"))
            self.w_program_table = QTableWidget(0, 3)
            self.w_program_table.setHorizontalHeaderLabels(
                ["起始温度 K", "目标温度 K", "时长 ps"])
            self.w_program_table.verticalHeader().setVisible(False)
            self.w_program_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.w_program_table.setMinimumHeight(110)
            program_row = QWidget()
            program_box = QHBoxLayout(program_row)
            program_box.setContentsMargins(0, 0, 0, 0)
            self.w_program_add = QPushButton("加一段")
            self.w_program_remove = QPushButton("删掉选中")
            badge = help_badge("temperature_program")
            for widget in (self.w_program_add, self.w_program_remove):
                program_box.addWidget(widget)
            if badge is not None:
                program_box.addWidget(badge)
            program_box.addStretch(1)
            program_form.addRow(self.w_program_table)
            program_form.addRow(program_row)
            # how the driver launches every stage (the machine's half, not CP2K's)
            launch_row = QWidget()
            launch_box = QHBoxLayout(launch_row)
            launch_box.setContentsMargins(0, 0, 0, 0)
            self.w_program_procs = QSpinBox()
            self.w_program_procs.setRange(1, 4096)
            self.w_program_procs.setValue(64)
            self.w_program_procs.setPrefix("-np ")
            self.w_program_omp = QSpinBox()
            self.w_program_omp.setRange(1, 256)
            self.w_program_omp.setValue(2)
            self.w_program_omp.setPrefix("OMP ")
            self.w_program_bin = QComboBox()
            self.w_program_bin.setEditable(True)
            self.w_program_bin.addItems(["cp2k.psmp", "cp2k.popt", "cp2k.ssmp",
                                         "cp2k.sopt"])
            self.w_program_mpirun = QLineEdit("mpirun")
            self.w_program_mpirun.setMaximumWidth(120)
            launch_box.addWidget(QLabel("每段"))
            launch_box.addWidget(self.w_program_mpirun)
            launch_box.addWidget(self.w_program_bin, 1)
            launch_box.addWidget(self.w_program_procs)
            launch_box.addWidget(self.w_program_omp)
            launch_badge = help_badge("program_launch")
            if launch_badge is not None:
                launch_box.addWidget(launch_badge)
            launch_box.addStretch(1)
            program_form.addRow(launch_row)
            self.w_program_resume = QCheckBox("跳过已跑完的段（断点续跑）")
            self.w_program_resume.setChecked(True)
            resume_row = QWidget()
            resume_box = QHBoxLayout(resume_row)
            resume_box.setContentsMargins(0, 0, 0, 0)
            resume_box.addWidget(self.w_program_resume)
            resume_badge = help_badge("program_resume")
            if resume_badge is not None:
                resume_box.addWidget(resume_badge)
            resume_box.addStretch(1)
            program_form.addRow(resume_row)
            out_row = QWidget()
            out_box = QHBoxLayout(out_row)
            out_box.setContentsMargins(0, 0, 0, 0)
            self.w_program_outdir = QLineEdit()
            self.w_program_outdir.setPlaceholderText(
                "每段一个 .inp + run_program.sh（留空 = 生成时问）")
            self.w_program_browse = QToolButton()
            self.w_program_browse.setText("浏览…")
            self.w_program_open = QPushButton("打开目录")
            self.w_program_open.setEnabled(False)
            self.w_program_button = QPushButton("生成温度程序")
            out_box.addWidget(QLabel("输出目录"))
            out_box.addWidget(self.w_program_outdir, 1)
            out_box.addWidget(self.w_program_browse)
            out_box.addWidget(self.w_program_open)
            out_box.addWidget(self.w_program_button)
            program_form.addRow(out_row)
            self.program_hint = hint_label()
            program_form.addRow(self.program_hint)
            form.addRow(self.temperature_program_box)
            self.w_program_add.clicked.connect(self._program_add_row)
            self.w_program_remove.clicked.connect(self._program_remove_row)
            self.w_program_button.clicked.connect(self.run_program)
            self.w_program_browse.clicked.connect(self.browse_program_outdir)
            self.w_program_open.clicked.connect(self.open_program_outdir)
            # 「打开目录」只在目录真的存在时可用（生成完就能一键打开）
            self.w_program_outdir.textChanged.connect(self._program_outdir_changed)
            self._program_outdir_changed()
            # remember the machine's launch settings (see closeEvent)
            self.w_program_outdir.setText(
                str(self.settings.value("program_outdir", "") or ""))
            self.w_program_mpirun.setText(
                str(self.settings.value("program_mpirun", "mpirun") or ""))
            self.w_program_bin.setCurrentText(
                str(self.settings.value("program_bin", "cp2k.psmp") or "cp2k.psmp"))
            self.w_program_procs.setValue(
                int(self.settings.value("program_procs", 64) or 64))
            self.w_program_omp.setValue(
                int(self.settings.value("program_omp", 2) or 2))
            self.w_program_resume.setChecked(
                str(self.settings.value("program_resume", "true")).lower()
                not in ("false", "0", "no"))
            # the table's meaning depends on the ensemble and the timestep
            self.w_ensemble.currentTextChanged.connect(self._program_changed)
            self.w_timestep.valueChanged.connect(self._program_changed)
            self._program_add_row(300.0, 5.0)                  # 保持 300 K 5 ps
            self._program_add_row(300.0, 10.0, target=600.0)   # 300 → 600 K 10 ps
            self._program_changed()
            self.md_print_box = CollapsibleBox(
                "运行中的温度 / 能量打印（写文件）", key="md_prints",
                settings=self.settings)
            md_print_form = QFormLayout(self.md_print_box.body)
            md_print_form.setContentsMargins(0, 4, 0, 0)
            check_row(md_print_form, "md_temp_kind_print", self.w_md_temp_kind)
            form_row(md_print_form, "md_temp_kind_every", "温度间隔",
                     self.w_md_temp_kind_every)
            check_row(md_print_form, "md_energy_print", self.w_md_energy)
            form_row(md_print_form, "md_energy_every", "能量间隔",
                     self.w_md_energy_every)
            form_row(md_print_form, "md_energy_file", "能量文件名",
                     self.w_md_energy_file)
            check_row(md_print_form, "thermostat_temp_print", self.w_thermostat_temp)
            form_row(md_print_form, "thermostat_temp_every", "热浴温度间隔",
                     self.w_thermostat_temp_every)
            self.md_print_hint = hint_label()
            md_print_form.addRow(self.md_print_hint)
            form.addRow(self.md_print_box)
            for widget in (self.w_md_temp_kind, self.w_md_energy,
                           self.w_thermostat_temp):
                widget.toggled.connect(self._md_print_changed)
            self.w_thermostat_region.currentIndexChanged.connect(
                self._md_print_changed)
            self.w_ensemble.currentTextChanged.connect(self._ensemble_changed)
            # the thermostat decides too: with NONE there is no &THERMOSTAT, so
            # the region/TIMECON/temperature-print rows have to grey out with it
            self.w_thermostat.currentTextChanged.connect(self._ensemble_changed)
            # the ramp is a helper in front of the two raw factors: it recomputes
            # them whenever one of its inputs moves
            self.w_ramp.toggled.connect(self._ramp_changed)
            for widget in (self.w_ramp_target, self.w_steps, self.w_timestep,
                           self.w_temperature):
                widget.valueChanged.connect(self._ramp_changed)
            for widget in (self.w_temperature_annealing, self.w_annealing,
                           self.w_annealing_cell):
                widget.valueChanged.connect(self._refresh_ramp_hint_suffix)
            self.w_ensemble.currentTextChanged.connect(self._ramp_changed)
            self.w_langevin_gamma.valueChanged.connect(self._ramp_changed)
            self._ensemble_changed("NVT")
            self._ramp_changed()
            self._md_print_changed()
            # 元动力学 sits on the MD page - those two sections are only read for
            # RUN_TYPE MD - in a block that is folded away until it is wanted
            self.metadyn_panel = MetadynPanel(self, self.settings)
            form.addRow(self.metadyn_panel)

        elif task == "freq":
            self.w_intensities = QCheckBox("红外强度（非周期体系）")
            self.w_intensities.setChecked(True)
            self.w_thermochemistry = QCheckBox("热化学量")
            check_row(form, "intensities", self.w_intensities)
            check_row(form, "thermochemistry", self.w_thermochemistry)
            note = QLabel("CP2K 2025.1 没有 &RAMAN 段：拉曼强度需要另外的后处理，"
                          "这个模板只给红外。")
            note.setWordWrap(True)
            note.setStyleSheet("color:#b9770e;")
            form.addRow(note)

        elif task == "band":
            self.w_lattice = QComboBox()
            for label, value in LATTICES:
                self.w_lattice.addItem(label, value)
            self.w_kpath = QLineEdit()
            self.w_kpath.setPlaceholderText("留空用该晶格类型的默认路径，例如 GAMMA-X-M-GAMMA")
            self.w_npoints = QSpinBox()
            self.w_npoints.setRange(2, 5000)
            self.w_npoints.setValue(40)
            # the SCF k-grid lives in the shared &KPOINTS group, so it is
            # available for every Quickstep task and not only for band
            form_row(form, "lattice", "晶格类型", self.w_lattice)
            form_row(form, "kpath", "k 点路径", self.w_kpath)
            form_row(form, "npoints", "路径总点数", self.w_npoints)

        elif task == "pdos":
            self.w_pdos_nlumo = QSpinBox()
            self.w_pdos_nlumo.setRange(0, 100000)
            self.w_pdos_nlumo.setSpecialValueText("默认")
            self.w_pdos_ldos = QLineEdit()
            self.w_pdos_ldos.setPlaceholderText("如 1 2 3，可留空")
            self.w_pdos_broaden = QDoubleSpinBox()
            self.w_pdos_broaden.setRange(0.0, 5.0)
            self.w_pdos_broaden.setDecimals(3)
            self.w_pdos_broaden.setSingleStep(0.01)
            self.w_pdos_broaden.setSpecialValueText("不展宽")
            self.w_pdos_broaden.setSuffix(" eV")
            self.w_pdos_broaden_type = QComboBox()
            self.w_pdos_broaden_type.addItems(["GAUSSIAN", "LORENTZIAN", "PSEUDO_VOIGT"])
            form_row(form, "pdos_nlumo", "NLUMO", self.w_pdos_nlumo)
            form_row(form, "pdos_ldos", "LDOS 列表", self.w_pdos_ldos)
            form_row(form, "pdos_broaden", "展宽 FWHM", self.w_pdos_broaden)
            form_row(form, "pdos_broaden_type", "展宽函数", self.w_pdos_broaden_type)

        elif task == "neb":
            self.w_band_type = QComboBox()
            self.w_band_type.addItems(BAND_TYPES)
            self.w_k_spring = QDoubleSpinBox()
            self.w_k_spring.setRange(0.0, 100.0)
            self.w_k_spring.setDecimals(4)
            self.w_k_spring.setSingleStep(0.01)
            self.w_k_spring.setValue(0.05)
            form_row(form, "band_type", "NEB 类型", self.w_band_type)
            form_row(form, "k_spring", "弹簧常数", self.w_k_spring)

            self.w_replicas = QListWidget()
            self.w_replicas.setMaximumHeight(110)
            self.w_replicas.setSelectionMode(QAbstractItemView.ExtendedSelection)
            buttons = QHBoxLayout()
            add = QPushButton("添加镜像…")
            add.clicked.connect(self.add_replicas)
            remove = QPushButton("移除选中")
            remove.clicked.connect(self.remove_replicas)
            buttons.addWidget(add)
            buttons.addWidget(remove)
            holder = QWidget()
            holder_layout = QVBoxLayout(holder)
            holder_layout.setContentsMargins(0, 0, 0, 0)
            holder_layout.addWidget(self.w_replicas)
            holder_layout.addLayout(buttons)
            form_row(form, "replicas", "镜像文件", holder)
            note = QLabel("按顺序给出 5 个以上镜像（反应物→产物）。NEB 需要晶胞，"
                          "请在上面填「真空层」或「晶胞」。")
            note.setWordWrap(True)
            note.setStyleSheet("color:#5d6d7e;")
            form.addRow(note)

        return page

    def _route_group(self) -> QGroupBox:
        """The knobs of the two SCF routes, shown only when they apply.

        OT and diagonalisation take *different* sub-parameters, and several of
        them only matter for one preconditioner/minimizer.  Rather than dumping
        everything (and letting people set values that are then ignored), the
        two blocks are shown/hidden live and the coupled fields are greyed out,
        with the reason on the row's "?".
        """
        box = CollapsibleBox("3.2 SCF 路线细节（OT / 对角化）", key="scf_details",
                             settings=self.settings)
        self.scf_details_box = box
        layout = QVBoxLayout(box.body)

        # ---- OT ----
        self.ot_box = QGroupBox("OT（轨道变换）")
        ot_form = QFormLayout(self.ot_box)
        ot_form.setLabelAlignment(Qt.AlignRight)
        form_row(ot_form, "ot_minimizer", "MINIMIZER", self.w_ot_minimizer)
        form_row(ot_form, "ot_preconditioner", "PRECONDITIONER", self.w_ot_preconditioner)
        form_row(ot_form, "ot_algorithm", "ALGORITHM", self.w_ot_algorithm)
        form_row(ot_form, "ot_linesearch", "LINESEARCH", self.w_ot_linesearch)
        form_row(ot_form, "ot_stepsize", "STEPSIZE", self.w_ot_stepsize)
        self.energy_gap_badge = HelpBadge(HELP["ot_energy_gap"])
        form_row(ot_form, "ot_energy_gap", "ENERGY_GAP", self.w_ot_energy_gap,
                 badge=self.energy_gap_badge)
        self.safe_diis_badge = HelpBadge(HELP["ot_safe_diis"])
        form_row(ot_form, "ot_safe_diis", "SAFE_DIIS", self.w_ot_safe_diis,
                 badge=self.safe_diis_badge)
        form_row(ot_form, "ot_rotation", "ROTATION", self.w_ot_rotation)
        form_row(ot_form, "outer_optimizer", "外层 OPTIMIZER", self.w_outer_optimizer)
        layout.addWidget(self.ot_box)

        # ---- diagonalisation ----
        self.diag_box = QGroupBox("对角化 DIAGONALIZATION")
        diag_form = QFormLayout(self.diag_box)
        diag_form.setLabelAlignment(Qt.AlignRight)
        form_row(diag_form, "diag_algorithm", "ALGORITHM", self.w_diag_algorithm)
        form_row(diag_form, "level_shift", "LEVEL_SHIFT", self.w_level_shift)
        layout.addWidget(self.diag_box)

        # reacting to the route *and* to the sub-parameters (smearing goes
        # through _smearing_changed, which calls this as well); the greying out
        # itself comes from the coupling table
        self.w_scf.currentIndexChanged.connect(self._route_changed)
        self.w_scf.currentIndexChanged.connect(self._scf_route_changed)
        self.w_ot_preconditioner.currentIndexChanged.connect(self._apply_couplings)
        self.w_ot_minimizer.currentIndexChanged.connect(self._apply_couplings)
        self.w_ot_linesearch.currentIndexChanged.connect(self._apply_couplings)
        return box

    def _effective_route(self) -> str:
        """Which route the current widget state would actually take.

        Goes through :func:`cp2kbuild.recipes.effective_route` (the same function
        the generator uses), so "auto" resolves exactly the way the written input
        will - including the k-point case: k-points force DIAGONALIZATION.
        """
        grid: tuple[int, int, int] | None = None
        spacing: float | None = None
        if hasattr(self, "kpoint_box") and self.kpoint_box.isChecked():
            if self.w_kgrid_auto.isChecked():
                spacing = self.w_kpoint_spacing.value()
            else:
                grid = self._kpoint_grid_from_field()
        spec = Spec(task=self.task_combo.currentData(),
                    scf=self.w_scf.currentData() or "auto",
                    smearing=True if self.w_smearing.isChecked() else None,
                    kpoint_grid=grid,
                    kpoint_spacing=spacing,
                    kpoint_points=(self._kpoint_points()
                                   if hasattr(self, "kpoint_box")
                                   and self.kpoint_box.isChecked() else []))
        return effective_route(spec)

    def _kpoint_grid_from_field(self) -> tuple[int, int, int] | None:
        try:
            numbers = [int(float(v)) for v in
                       self.w_kgrid.text().replace(",", " ").split()]
        except ValueError:
            return None
        return tuple(numbers) if len(numbers) == 3 else None

    def _route_changed(self) -> None:
        """Show the block of the active route; greying out is table-driven."""
        # METHOD FIST writes no &DFT/&SCF at all, so there is no SCF route to
        # name.  The OT/DIAGONALIZATION blocks are inside ``route_box`` (hidden by
        # _method_changed), but this summary line deliberately sits *outside* it -
        # so it kept claiming "当前路线：OT" on the classical page.
        classical = (self.w_method.currentData()
                     or "QUICKSTEP").upper() in ("FIST", "CLASSICAL")
        route = self._effective_route()
        uses_ot = route.startswith("OT")
        automatic = (self.w_scf.currentData() or "auto") == "auto"
        smearing = self.w_smearing.isChecked()

        self.ot_box.setVisible(uses_ot and not classical)
        self.diag_box.setVisible(not uses_ot and not classical)
        if classical:
            self.route_label.setText(
                "<b>当前路线：经典力场（METHOD FIST）</b>——不写 &DFT/&SCF，"
                "所以 OT / 对角化在这里不适用（那一块也收起来了）")
        elif automatic:
            self.route_label.setText(
                f"<b>当前路线：{route}</b>（「自动」按任务和 SMEAR 现场判定）")
        else:
            self.route_label.setText(f"<b>当前路线：{route}</b>")

        # the mixing row keeps its own summary note (the table only greys it)
        if classical:
            self.mixing_badge.setToolTip(HELP["mixing"].tooltip())
        else:
            self.mixing_badge.setToolTip(HELP["mixing"].tooltip() + (
                "\n\n当前走 OT：`&MIXING` 不会被写进输入文件。"
                if uses_ot else
                "\n\n当前开了 SMEAR：`&MIXING` 不会被写进输入文件，"
                "CP2K 会用自己默认的混合参数。" if smearing else ""))
        self._apply_couplings()
        # the k-point group explains itself relative to this route
        self._update_kpoint_hint()
        self.schedule()

    # ------------------------------------------------------------------ #
    # coupling table -> the widgets
    # ------------------------------------------------------------------ #
    def _coupling_widgets(self) -> dict[str, QWidget]:
        """Spec field -> the control that sets it."""
        widgets = {
            # method + classical force field
            "method": self.w_method,
            "mm_route": self.w_mm_route,
            "mm_parm_file": self.h_mm_parm,
            "mm_conn_file": self.h_mm_conn,
            "mm_coord_file": self.h_mm_coord,
            "mm_coord_format": self.w_mm_coord_format,
            "mm_molecules": self.mm_molecules_box,
            "mm_ff_file": self.h_mm_ff,
            "mm_lj": self.w_mm_table,
            "mm_element_lj": self.w_mm_table,
            "mm_charges": self.w_mm_charges,
            "mm_vdw_scale14": self.mm_scale14_holder,
            "mm_ei_scale14": self.mm_scale14_holder,
            "mm_ewald": self.w_mm_ewald,
            "mm_ewald_gmax": self.w_mm_ewald_gmax,
            "mm_ewald_alpha": self.w_mm_ewald_alpha,
            "mm_o_spline": self.w_mm_o_spline,
            "mm_rcut": self.w_mm_rcut,
            "mm_verlet_skin": self.w_mm_verlet_skin,
            "mm_spline_emax": self.w_mm_spline_emax,
            "mm_shift_cutoff": self.w_mm_shift_cutoff,
            "mm_multiple_potential": self.w_mm_multiple,
            "mm_ignore_missing_params": self.w_mm_ignore_missing,
            "mm_stress_tensor": self.w_mm_stress,
            # SCF route
            "ot_minimizer": self.w_ot_minimizer,
            "ot_preconditioner": self.w_ot_preconditioner,
            "ot_algorithm": self.w_ot_algorithm,
            "ot_linesearch": self.w_ot_linesearch,
            "ot_stepsize": self.w_ot_stepsize,
            "ot_energy_gap": self.w_ot_energy_gap,
            "ot_safe_diis": self.w_ot_safe_diis,
            "ot_rotation": self.w_ot_rotation,
            "outer_optimizer": self.w_outer_optimizer,
            "diag_algorithm": self.w_diag_algorithm,
            "level_shift": self.w_level_shift,
            "added_mos": self.w_added_mos,
            "smear_method": self.w_smear_method,
            "electronic_temperature": self.w_electronic_temperature,
            "mixing_method": self.w_mixing_method,
            "mixing_alpha": self.w_mixing_alpha,
            "mixing_nbuffer": self.w_mixing_nbuffer,
            "mixing_beta": self.w_mixing_beta,
            "scf_guess": self.w_scf_guess,
            "wfn_restart_print": self.w_wfn_print,
            "charge": self.w_charge,
            "multiplicity": self.w_multiplicity,
            "uks": self.w_force_uks,
            # Molden
            "print_molden": self.w_molden,
            "molden_file": self.w_molden_file,
            "molden_nlumo": self.w_molden_nlumo,
            "molden_unit": self.w_molden_unit,
            "molden_gto_kind": self.w_molden_gto_kind,
            "molden_write_pseudo": self.w_molden_pseudo,
            "molden_write_cell": self.w_molden_cell,
            "molden_vib": self.w_molden_vib,
            # restarting
            "wfn_restart_file": self.w_wfn_file,
            "wfn_restart_every": self.w_wfn_every,
            "wfn_restart_level": self.w_wfn_level,
            "wfn_restart_add_last": self.w_wfn_add_last,
            "wfn_backup_copies": self.w_wfn_backups,
            "restart_every": self.w_restart_every,
            "restart_add_last": self.w_restart_add_last,
            "restart_backups": self.w_restart_backups,
            "ext_restart_file": self.w_ext_restart,
            "restart_default": self.w_restart_default,
            "ext_restart_take": self.w_restart_take,
            # cell / periodicity
            "vacuum": self.w_vacuum,
            "scaled_coords": self.w_scaled_coords,
            "h_to_d": self.w_h_to_d,
            "kpoint_grid": self.w_kgrid,
            "kpoint_scheme": self.w_kpoint_scheme,
            "kpoint_points": self.w_kpoint_points,
            "poisson_solver": self.w_poisson,
            "fixed_atoms": self.w_fixed_atoms,
            # per task
            "optimizer": self.w_geo_optimizer,
            "max_iter": self.w_geo_max_iter,
            "ensemble": self.w_ensemble,
            "thermostat": self.w_thermostat,
            "temperature": self.w_temperature,
            "timestep": self.w_timestep,
            "steps": self.w_steps,
            "thermostat_timecon": self.w_thermostat_timecon,
            "barostat_timecon": self.w_barostat_timecon,
            "trajectory_stride": self.w_trajectory_stride,
            "thermostat_region": self.w_thermostat_region,
            "md_temp_kind_print": self.w_md_temp_kind,
            "md_temp_kind_every": self.w_md_temp_kind_every,
            "md_energy_print": self.w_md_energy,
            "md_energy_every": self.w_md_energy_every,
            "md_energy_file": self.w_md_energy_file,
            "thermostat_temp_print": self.w_thermostat_temp,
            "thermostat_temp_every": self.w_thermostat_temp_every,
            "temperature_annealing": self.w_temperature_annealing,
            "annealing": self.w_annealing,
            "annealing_cell": self.w_annealing_cell,
            "langevin_gamma": self.w_langevin_gamma,
            "restart_temperature_annealing":
                self.w_restart_temperature_annealing,
            "intensities": self.w_intensities,
            "thermochemistry": self.w_thermochemistry,
            "lattice": self.w_lattice,
            "kpath": self.w_kpath,
            "npoints": self.w_npoints,
            "pdos_nlumo": self.w_pdos_nlumo,
            "pdos_ldos": self.w_pdos_ldos,
            "pdos_broaden": self.w_pdos_broaden,
            "pdos_broaden_type": self.w_pdos_broaden_type,
            "band_type": self.w_band_type,
            "k_spring": self.w_k_spring,
            "replicas": self.w_replicas,
        }
        if hasattr(self, "w_print_forces"):
            widgets["print_forces"] = self.w_print_forces
            widgets["print_mulliken"] = self.w_print_mulliken
            widgets["print_dipole"] = self.w_print_dipole
        if hasattr(self, "w_pressure"):
            widgets["external_pressure"] = self.w_pressure
            widgets["keep_angles"] = self.w_keep_angles
            widgets["max_iter"] = (
                self.w_cell_max_iter
                if self.task_combo.currentData() == "cell_opt" else self.w_geo_max_iter)
        if hasattr(self, "w_md_pressure"):
            widgets["pressure"] = self.w_md_pressure
        return widgets

    def _row_badges(self) -> dict[QWidget, HelpBadge]:
        """field widget -> the "?" badge of its row, found in the layouts."""
        found: dict[QWidget, HelpBadge] = {}
        for form in self.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                field_item = form.itemAt(row, QFormLayout.FieldRole)
                label_item = form.itemAt(row, QFormLayout.LabelRole)
                if field_item is None or label_item is None:
                    continue
                widget = field_item.widget()
                label = label_item.widget()
                if widget is None or label is None:
                    continue
                badge = label.findChild(HelpBadge)
                if badge is not None:
                    found[widget] = badge
        return found

    def _apply_couplings(self) -> None:
        """Grey out every control whose parameter cannot take effect.

        The conditions (and their evidence) live in ``cp2kbuild.couplings``, so
        the interface, the command line warnings and the self-test cannot drift
        apart.  The reason goes on that row's "?" - never on the control itself.
        """
        try:
            spec = self.collect_spec()
            # the *effective* structure: vacuum/cell/periodic applied, so a rule
            # about cells (SCALED, k-points, Poisson) sees what will be written
            structure = prepare_structure(spec.structure.copy(), spec) \
                if spec.structure is not None else None
        except Exception:      # noqa: BLE001 - half-typed values must not break the form
            return
        inactive = applicability(spec, structure)
        if not self._badge_cache:
            self._badge_cache = self._row_badges()
            self._badge_base = {badge: badge.toolTip()
                                for badge in self._badge_cache.values()}
        for field, widget in self._coupling_widgets().items():
            rule = inactive.get(field)
            widget.setEnabled(rule is None)
            badge = self._badge_cache.get(widget)
            if badge is None:
                continue
            base = self._badge_base.get(badge, badge.toolTip())
            if rule is None:
                badge.setToolTip(base)
            else:
                badge.setToolTip(
                    base + f"\n\n现在用不上：{rule.reason(spec, structure)}"
                           f"\n依据：{rule.evidence}")
        # One statement the table cannot express, because it depends on another
        # control's *value*: a take-list is written as ``RESTART_DEFAULT F``
        # (cp2kbuild/recipes.py:ext_restart_section), so switching it back on
        # would contradict the very list the user just picked.
        if hasattr(self, "w_restart_default"):
            self.w_restart_default.setEnabled(
                bool(self.w_ext_restart.text().strip())
                and self.w_restart_take.currentData() is None)
        # Same kind of statement, for the k-point mesh: the table decides whether
        # k-points apply at all, but *which* of the two boxes takes part is the
        # 「按间距自动」 toggle's business.  Without this the manual mesh looks
        # editable while the generator keeps using the spacing estimate (the
        # "enabled" state is read from the table, not from the widget, so this
        # cannot latch itself off).
        if hasattr(self, "w_kgrid_auto"):
            applies = "kpoint_grid" not in inactive
            auto = self.w_kgrid_auto.isChecked()
            self.w_kgrid.setEnabled(applies and not auto)
            self.w_kpoint_spacing.setEnabled(applies and auto)

    def _smearing_changed(self, on: bool) -> None:
        """Smearing decides the route (and therefore what applies)."""
        self._route_changed()

    def _update_scaled_enabled(self) -> None:
        """SCALED needs a cell, so an unset cell clears the box."""
        if not hasattr(self, "w_scaled_coords"):
            return
        has_cell = self.structure is not None and self.structure.cell is not None
        if not has_cell and not self.w_cell.text().strip():
            self.w_scaled_coords.setChecked(False)
        self._apply_couplings()

    def _ensemble_changed(self, text: str) -> None:
        if not hasattr(self, "w_thermostat"):
            return
        self._apply_couplings()
        # the relay hint names the ensemble ("NVT -> speed restarted at 300 K",
        # "NPT -> the barostat starts from zero"), so it has to follow this
        if hasattr(self, "restart_take_hint"):
            self._restart_changed()

    def _temperature_changed(self, value: float) -> None:
        """The relay hint quotes the temperature the velocities restart at."""
        if hasattr(self, "restart_take_hint"):
            self._restart_changed()

    def _ramp_changed(self, *_args) -> None:
        """Turn "150 → 600 K" into the factor CP2K actually wants.

        ``&MD`` has no "target temperature" keyword - heating/cooling is a factor
        applied once per step, so the interesting number is
        ``(T_end/T_start)**(1/steps)`` (or its square root for the velocity
        rescaling, where the temperature follows the square).  The factor is
        written into the raw spin box, so the input always contains exactly what
        the field says, and the same helper is used by ``--temperature-ramp``.
        """
        if not hasattr(self, "ramp_hint"):
            return
        from .recipes import annealing_mechanism, plan_ramp

        ensemble = self.w_ensemble.currentText()
        mechanism = annealing_mechanism(ensemble)
        usable = mechanism is not None
        # note the intent *before* unchecking: that is what the warning is about
        blocked = bool(self.w_ramp.isChecked() and not usable)
        self.w_ramp.setEnabled(usable)
        if not usable:
            self.w_ramp.setChecked(False)
        self.w_ramp_target.setEnabled(usable and self.w_ramp.isChecked())
        if blocked:
            # the factors were the ramp's; it cannot work in this ensemble, and a
            # left-over ANNEALING would abort the run (NVT/NPT)
            for box in (self.w_temperature_annealing, self.w_annealing):
                if box.value():
                    box.setValue(0.0)
            self.ramp_hint.setText(
                f"⚠ ENSEMBLE {ensemble} 不能升降温：`TEMPERATURE_ANNEALING` 只在 "
                "LANGEVIN 下生效，而 `ANNEALING`（缩放速度）与 NVT/NPT 的恒温器"
                "同时开会被 CP2K 直接中止（两个控温手段互相打架）。"
                "要受控升降温就把系综换成 LANGEVIN；NVE/NPE 只能用速度缩放那种。")
        elif self.w_ramp.isChecked():
            try:
                ramp = plan_ramp(self.w_temperature.value(),
                                 self.w_ramp_target.value(),
                                 self.w_steps.value(), self.w_timestep.value(),
                                 ensemble)
            except ValueError as exc:
                self.ramp_hint.setText(f"⚠ {exc}")
                return
            box = (self.w_temperature_annealing if ramp.mechanism == "temperature"
                   else self.w_annealing)
            # only one of the two can be right for this ensemble: leaving the
            # other one set would write a keyword CP2K ignores (or aborts on)
            for other in (self.w_temperature_annealing, self.w_annealing):
                if other is not box and other.value():
                    other.setValue(0.0)
            box.setValue(ramp.factor)
            self.ramp_hint.setText(
                ("升温" if ramp.end > ramp.start else "降温")
                + f" {ramp.start:g} → {ramp.end:g} K，{ramp.duration:.2f} ps，"
                + f"约 {abs(ramp.rate):.1f} K/ps：写 `&MD "
                + ("TEMPERATURE_ANNEALING" if ramp.mechanism == "temperature"
                   else "ANNEALING")
                + f" {ramp.factor:.12g}`；温度按几何斜率走（"
                + " → ".join(f"{temperature:.0f} K"
                             for _, temperature in ramp.table(4))
                + "）"
                + ("" if ramp.mechanism == "temperature" else
                   "。速度缩放不是恒温器：温度只是跟着变，速度分布会被拉偏"))
            if self.w_langevin_gamma.value() <= 0.0 and \
                    ramp.mechanism == "temperature":
                self.ramp_hint.setText(
                    self.ramp_hint.text()
                    + " ⚠ LANGEVIN 的摩擦是 0（CP2K 默认）：没有控温，斜坡不会生效")
        else:
            self.ramp_hint.setText(
                "不做升降温：两个因子留 0（= 不写这两个关键字），"
                "温度由恒温器（NVT/NPT）或 LANGEVIN 的摩擦维持。"
                "要升降温就勾上左边的框并填目标温度。")
        self._refresh_ramp_hint_suffix()

    def _refresh_ramp_hint_suffix(self) -> None:
        """The raw factors can also be typed in by hand - say what they mean."""
        if not hasattr(self, "ramp_hint") or self.w_ramp.isChecked():
            return
        for box, name, note in (
                (self.w_temperature_annealing, "TEMPERATURE_ANNEALING",
                 "每步把 Langevin 的目标温度乘它（只在 LANGEVIN 下生效）"),
                (self.w_annealing, "ANNEALING",
                 "每步把速度乘它，温度按它的平方走（没有粒子恒温器的系综）"),
                (self.w_annealing_cell, "ANNEALING_CELL",
                 "同上，作用在晶胞速度（只有 NPE_I/NPE_F）")):
            if box.value() > 0.0:
                self.ramp_hint.setText(
                    f"手填 `&MD {name} {box.value():.10f}`：{note}")
                return

    def _program_cells(self, row: int) -> list:
        return [self.w_program_table.cellWidget(row, column)
                for column in range(3)]

    def _program_last_target(self) -> float:
        """Where the next segment should start: the previous one's target."""
        rows = self.w_program_table.rowCount()
        if rows == 0:
            return self.w_temperature.value()
        cell = self.w_program_table.cellWidget(rows - 1, 1)
        return cell.value() if cell is not None else self.w_temperature.value()

    def _program_add_row(self, start: float | None = None,
                         duration: float | None = None,
                         target: float | None = None, *_args) -> None:
        """Append a segment; its start is the previous segment's target."""
        table = self.w_program_table
        row = table.rowCount()
        table.insertRow(row)
        begin = self._program_last_target() if start is None else float(start)
        values = (begin,
                  begin if target is None else float(target),
                  float(duration) if duration is not None else 5.0)
        for column, value in enumerate(values):
            box = QDoubleSpinBox()
            box.setRange(1.0, 10000.0)
            box.setDecimals(2)
            box.setSuffix(" K" if column < 2 else " ps")
            box.setValue(value)
            box.valueChanged.connect(self._program_changed)
            table.setCellWidget(row, column, box)
        self._program_changed()

    def _program_remove_row(self, *_args) -> None:
        rows = sorted({index.row() for index in
                       self.w_program_table.selectedIndexes()}, reverse=True)
        if not rows and self.w_program_table.rowCount():
            rows = [self.w_program_table.rowCount() - 1]
        for row in rows:
            self.w_program_table.removeRow(row)
        self._program_changed()

    def _program_rows(self) -> list[tuple[float, float, float]]:
        rows = []
        for row in range(self.w_program_table.rowCount()):
            cells = self._program_cells(row)
            if any(cell is None for cell in cells):
                continue
            rows.append((cells[0].value(), cells[1].value(), cells[2].value()))
        return rows

    def _program_changed(self, *_args) -> None:
        """Say what the table would do - CP2K cannot, so the stages matter."""
        if not hasattr(self, "program_hint"):
            return
        from .recipes import annealing_mechanism
        from .tempprogram import (parse_program, plan_program, program_notes,
                                  program_text)

        rows = self._program_rows()
        if not rows:
            self.program_hint.setText(
                "还没有段：点「加一段」加一行（起始温度默认接上一段的终点）。")
            return
        ensemble = self.w_ensemble.currentText()
        if annealing_mechanism(ensemble) is None and \
                any(abs(end - start) > 1e-9 for start, end, _ in rows):
            self.program_hint.setText(
                f"⚠ ENSEMBLE {ensemble} 不能升降温（见上）：把表里的变化段改成"
                "保持段，或者把系综换成 LANGEVIN（受控升降温）/ NVE（速度缩放）。")
            return
        try:
            text = program_text(rows)
            stages = plan_program(parse_program(text, timestep=self.w_timestep.value()),
                                  timestep=self.w_timestep.value(),
                                  ensemble=ensemble)
        except ValueError as exc:
            self.program_hint.setText(f"⚠ {exc}")
            return
        total = sum(stage.steps * self.w_timestep.value() / 1000.0
                    for stage in stages)
        kinds = "，".join(
            ("保持" if stage.kind == "hold" else
             ("升温" if stage.end > stage.start else "降温"))
            + f" {stage.start:g}→{stage.end:g} K {stage.steps * self.w_timestep.value() / 1000.0:.2f} ps"
            for stage in stages)
        notes = program_notes(stages, timestep=self.w_timestep.value(),
                              ensemble=ensemble)
        single = self._single_run_ramp_in_use()
        self.program_hint.setText(
            f"{len(stages)} 段，共 {total:.2f} ps（timestep "
            f"{self.w_timestep.value():g} fs）：{kinds}"
            + ("　⚠ " + notes[0] if notes else "")
            + ("" if not single else
               "　（上面「升温 / 降温」那一栏的因子不进程序：每段自己算）"))

    def _single_run_ramp_in_use(self) -> bool:
        """Is the single-run heating/cooling row set (and therefore not used here)?"""
        if not hasattr(self, "w_ramp"):
            return False
        return (self.w_ramp.isChecked()
                or self.w_temperature_annealing.value() > 0.0
                or self.w_annealing.value() > 0.0)

    def _program_outdir_changed(self, *_args) -> None:
        """「打开目录」只在目录已经存在时才亮（生成完就能一键打开）。"""
        if not hasattr(self, "w_program_open"):
            return
        target = self.w_program_outdir.text().strip()
        self.w_program_open.setEnabled(bool(target) and Path(target).is_dir())

    def browse_program_outdir(self) -> None:
        """挑输出目录：走系统的目录选择框（Windows 上就是资源管理器）。"""
        name = QFileDialog.getExistingDirectory(
            self, "温度程序写到哪个目录",
            self._start_directory("last_program_dir",
                                  self.w_program_outdir.text().strip()))
        if name:
            self._remember_directory("last_program_dir", str(Path(name) / "x"))
            self.w_program_outdir.setText(name)

    def open_program_outdir(self) -> None:
        """在文件管理器里打开输出目录（生成完看看写了什么）。"""
        target = self.w_program_outdir.text().strip()
        if not target:
            self.program_hint.setText("⚠ 还没有输出目录：先填一个（或用「浏览…」选）。")
            return
        path = Path(target)
        if not path.is_dir():
            self.program_hint.setText(f"⚠ 目录还不存在：{path}（先生成一次）")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def run_program(self) -> int:
        """Write the stage inputs + ``run_program.sh`` into a directory."""
        from .tempprogram import build_from_text, program_text

        if not hasattr(self, "program_hint"):
            return -1
        rows = self._program_rows()
        if not rows:
            self.program_hint.setText("⚠ 温度程序是空的：先加一段。")
            return -1
        outdir = self.w_program_outdir.text().strip()
        if not outdir:
            outdir = QFileDialog.getExistingDirectory(
                self, "温度程序写到哪个目录",
                self._start_directory("last_program_dir", ""))
            if not outdir:
                return -1
            self._remember_directory("last_program_dir",
                                     str(Path(outdir) / "x"))
            self.w_program_outdir.setText(outdir)
        try:
            spec = self.collect_spec()
            # a temperature program is MD: take the MD page's settings even when
            # another task page happens to be the one on screen
            spec.task = "md"
            self._apply_md_spec(spec)
        except Exception as exc:  # noqa: BLE001 - a half-typed field raises here
            # this is a Qt slot: an exception would be swallowed by the event
            # loop and the click would look like it did nothing at all
            self.program_hint.setText("⚠ 生成失败："
                                      f"{type(exc).__name__}: {exc}")
            return -1
        if self.index is None:
            self.program_hint.setText(
                "⚠ 还没有数据目录：温度程序要按「生成输入」页的参数生成每一段，"
                "基组/赝势的价电子匹配需要 CP2K 的 data 目录（见「基组 / 赝势」页）。")
            return -1
        from .tempprogram import RunOptions
        run = RunOptions(
            mpirun_bin=self.w_program_mpirun.text().strip(),
            cp2k_bin=self.w_program_bin.currentText().strip() or "cp2k.psmp",
            mpi_procs=self.w_program_procs.value(),
            omp_threads=self.w_program_omp.value(),
            resume=self.w_program_resume.isChecked())
        try:
            plan = build_from_text(
                spec, self.index, program_text(rows), outdir,
                seed_restart=self.w_ext_restart.text().strip() or None,
                run=run)
        except (ValueError, OSError) as exc:
            self.program_hint.setText(f"⚠ 生成失败：{exc}")
            return -1
        self.last_program = plan
        extra = ("" if not self._single_run_ramp_in_use() else
                 " 上面「升温 / 降温」的因子没有进这些段（每段自己算）；"
                 "要改某一段就改表格。")
        self.program_hint.setText(
            f"写好了 {len(plan.written)} 个文件 → {plan.outdir}："
            f"{len(plan.stages)} 段，共 {plan.total_ps:.2f} ps。"
            f"驱动脚本每段用 {run.mpirun_bin or '（不用 mpirun）'} "
            f"-np {run.mpi_procs} {run.cp2k_bin}（OMP_NUM_THREADS="
            f"{run.omp_threads}），串行跑。"
            "在服务器上 `sh run_program.sh`（后一段读前一段的 restart）。"
            + extra)
        return 0

    def _md_print_changed(self, *_args) -> None:
        """Say which files the MD run will write, and what the defaults are.

        The one counter-intuitive thing here is the ``-1.ener`` file: CP2K writes
        it from ``PRINT_LEVEL LOW`` on, so leaving the box empty does *not* mean
        "no energy file" - it means "CP2K's own default, one line per step".
        """
        if not hasattr(self, "md_print_hint"):
            return
        region = (self.w_thermostat_region.currentData()
                  if hasattr(self, "w_thermostat_region") else "GLOBAL")
        region_label = {"GLOBAL": "整个体系", "MOLECULE": "每个分子",
                        "MASSIVE": "每个自由度"}.get(region, region)
        bits: list[str] = []
        if self.w_md_temp_kind.isChecked():
            bits.append(f"每 {self.w_md_temp_kind_every.value()} 步写 "
                        "`<项目名>-1.temp`（每个原子种类的温度）")
        if self.w_md_energy.isChecked():
            every = self.w_md_energy_every.value()
            name = self.w_md_energy_file.text().strip()
            target = (f"`{name}`" if name else "`<项目名>-1.ener`")
            bits.append(f"每 {every} 步写 {target}（含 Temp[K] 列）")
        else:
            bits.append("不写 `&ENERGY`：CP2K 自己那份 `-1.ener` 仍然会写"
                        "（每步一行，PRINT_LEVEL ≥ LOW）")
        if self.w_thermostat_temp.isChecked():
            bits.append(f"每 {self.w_thermostat_temp_every.value()} 步写 "
                        "`<项目名>-1.PARTICLES.temp`"
                        f"（热浴区域是「{region_label}」："
                        + ("就是全局温度，和 -1.ener 的 Temp[K] 列重复"
                           if region == "GLOBAL" else
                           "每个区域一列，能看出哪个组分被控到了多少")
                        + "）")
        set_hint(self.md_print_hint, "；".join(bits) + "。")
        self._apply_couplings()
        self.schedule()

    def _classical_group(self) -> QGroupBox:
        """``&MM``/``&FORCEFIELD``: the classical (FIST) force-field page.

        Only visible on the FIST route, because none of it exists without
        ``METHOD FIST`` - and conversely every control on the DFT pages is
        meaningless there (CP2K computes no electrons at all).
        """
        box = QGroupBox("3. 经典力场（METHOD FIST）")
        layout = QVBoxLayout(box)

        self.mm_hint = hint_label()
        layout.addWidget(self.mm_hint)

        basics = QFormLayout()
        basics.setLabelAlignment(Qt.AlignRight)

        self.w_mm_route = QComboBox()
        for route in MM_ROUTES:
            self.w_mm_route.addItem(route.label, route.key)
        self.w_mm_route.currentIndexChanged.connect(self._mm_route_changed)
        form_row(basics, "mm_route", "力场路线", self.w_mm_route)

        self.w_mm_parm, self.h_mm_parm = file_field_row(
            basics, "mm_parm_file", "参数文件",
            "prmtop / .pot / .top：力场的参数", self._browse_mm_parm)
        self.w_mm_conn, self.h_mm_conn = file_field_row(
            basics, "mm_conn_file", "拓扑文件",
            "prmtop / .psf / .top：连接性、电荷、LJ 参数", self._browse_mm_conn)
        # AMBER/GROMOS: the topology file *is* the parameter file (the prmtop
        # holds both), which is why CP2K's own inputs name it twice.  The box
        # saves that, and disables the field so it is clear where it comes from.
        self.w_mm_same_file = QCheckBox("同参数文件")
        self.w_mm_same_file.setToolTip("")
        self.w_mm_same_file.toggled.connect(self._mm_same_file_changed)
        self.h_mm_conn.layout().addWidget(self.w_mm_same_file)
        self.w_mm_coord, self.h_mm_coord = file_field_row(
            basics, "mm_coord_file", "坐标文件",
            "inpcrd / .crd / .pdb / .g96", self._browse_mm_coord)
        self.w_mm_coord_format = QComboBox()
        for entry in MM_COORD_FORMATS:
            if isinstance(entry, tuple):
                self.w_mm_coord_format.addItem(entry[0], entry[1])
            else:
                self.w_mm_coord_format.addItem(entry, entry)
        form_row(basics, "mm_coord_format", "坐标格式", self.w_mm_coord_format)

        self.w_mm_path_style = QComboBox()
        for label, value in MM_PATH_STYLES:
            self.w_mm_path_style.addItem(label, value)
        form_row(basics, "mm_path_style", "文件路径写法", self.w_mm_path_style)

        # NB: VDW_SCALE14 / EI_SCALE14 are *numbers*, not logicals (the manual
        # lists them as reals, CP2K's own defaults are 1.0 / 0.0), so there is no
        # "T/F" for them - only "follow the route's recommendation" (empty) or a
        # value.  A leftover tristate combo used to sit in this row and did
        # nothing; "F" would have read as "no 1-4 scaling" while the run kept the
        # force field's 0.5 / 0.8333.
        self.mm_scale14_label = QLabel("1-4 缩放 VDW / EI")
        self.w_mm_vdw14 = QLineEdit()
        self.w_mm_vdw14.setPlaceholderText("跟随力场默认")
        self.w_mm_vdw14.setFixedWidth(90)
        self.w_mm_ei14 = QLineEdit()
        self.w_mm_ei14.setPlaceholderText("跟随力场默认")
        self.w_mm_ei14.setFixedWidth(110)
        scale_row = QHBoxLayout()
        scale_row.setContentsMargins(0, 0, 0, 0)
        scale_row.addWidget(self.w_mm_vdw14)
        scale_row.addWidget(self.w_mm_ei14, 1)
        scale_holder = QWidget()
        scale_holder.setLayout(scale_row)
        self.mm_scale14_holder = scale_holder
        scale_label = QWidget()
        scale_label_box = QHBoxLayout(scale_label)
        scale_label_box.setContentsMargins(0, 0, 0, 0)
        scale_label_box.setSpacing(5)
        scale_label_box.addWidget(QLabel("1-4 缩放 VDW/EI"))
        scale_label_box.addWidget(HelpBadge(HELP["mm_vdw_scale14"]))
        scale_label_box.addWidget(HelpBadge(HELP["mm_ei_scale14"]))
        scale_label_box.addStretch(1)
        basics.addRow(scale_label, scale_holder)
        layout.addLayout(basics)

        # ---- one topology file per component (&MOL_SET) --------------------- #
        self.mm_molecules_box = CollapsibleBox(
            "组分表：每个组分一个拓扑文件（&MOL_SET）", key="mm_molecules",
            settings=self.settings)
        mol_layout = QVBoxLayout(self.mm_molecules_box.body)
        mol_title = QHBoxLayout()
        mol_title.addWidget(HelpBadge(HELP["mm_molecules"]))
        mol_title.addStretch(1)
        mol_layout.addLayout(mol_title)
        mol_hint = hint_label()
        mol_layout.addWidget(mol_hint)
        self.w_mm_molecules = ExcelTable(
            ["连接性", "拓扑文件", "NMOL"], widths=[90, 340, 70],
            key="mm_molecules", settings=self.settings,
            sortable=False, editable=True)
        self.w_mm_molecules.setMinimumHeight(96)
        self.w_mm_molecules.setItemDelegateForColumn(
            0, ComboDelegate(MM_CONN_FORMATS, self.w_mm_molecules))
        mol_layout.addWidget(self.w_mm_molecules)
        mol_buttons = QHBoxLayout()
        add_mol = QPushButton("浏览添加分子…")
        add_mol.setToolTip("")
        add_mol.clicked.connect(self._browse_molecule)
        add_empty = QPushButton("加空行")
        add_empty.clicked.connect(self._add_molecule)
        del_mol = QPushButton("删除选中")
        del_mol.clicked.connect(self._remove_molecule)
        mol_buttons.addWidget(add_mol)
        mol_buttons.addWidget(add_empty)
        mol_buttons.addWidget(del_mol)
        check_mol = QPushButton("检查坐标与拓扑是否对得上")
        check_mol.setToolTip("")
        check_mol.clicked.connect(self._check_mixture)
        mol_buttons.addWidget(check_mol)
        detect_mol = QPushButton("按坐标文件识别组分")
        detect_mol.setToolTip("")
        detect_mol.clicked.connect(self._detect_components_clicked)
        mol_buttons.addWidget(detect_mol)
        mol_buttons.addStretch(1)
        mol_layout.addLayout(mol_buttons)
        self.mm_molecules_hint = mol_hint
        layout.addWidget(self.mm_molecules_box)

        # ---- in-input force field and pair potentials ---------------------- #
        self.mm_terms_box = CollapsibleBox("写在输入里的力场条款", key="mm_terms",
                                          settings=self.settings)
        terms = QVBoxLayout(self.mm_terms_box.body)
        self.mm_terms_hint = hint_label()
        terms.addWidget(self.mm_terms_hint)

        terms_form = QFormLayout()
        terms_form.setLabelAlignment(Qt.AlignRight)
        self.w_mm_ff, self.h_mm_ff = file_field_row(
            terms_form, "mm_ff_file", "力场文件",
            "&CHARGE/&BOND/&BEND/&NONBONDED 写在里面（整段 &FORCEFIELD 也行）",
            self._browse_mm_ff)
        terms.addLayout(terms_form)

        self.w_mm_table = ExcelTable(
            ["元素 / 元素对", "ε (K)", "σ (Å)"], widths=[150, 110, 110],
            key="mm_lj", settings=self.settings, sortable=False, editable=True)
        self.w_mm_table.setMinimumHeight(110)
        lj_title = QHBoxLayout()
        lj_title.addWidget(QLabel("ε/σ 表（单元素行按 Lorentz-Berthelot 混合）"))
        lj_title.addWidget(HelpBadge(HELP["mm_lj"]))
        lj_title.addWidget(HelpBadge(HELP["mm_element_lj"]))
        lj_title.addStretch(1)
        terms.addLayout(lj_title)
        terms.addWidget(self.w_mm_table)
        lj_buttons = QHBoxLayout()
        lj_fill = QPushButton("按结构补全元素")
        lj_fill.clicked.connect(self._fill_mm_elements)
        lj_add = QPushButton("加一行")
        lj_add.clicked.connect(lambda: self._add_mm_row(["", "", ""]))
        lj_del = QPushButton("删除选中")
        lj_del.clicked.connect(lambda: self._remove_mm_row(self.w_mm_table))
        lj_buttons.addWidget(lj_fill)
        lj_buttons.addWidget(lj_add)
        lj_buttons.addWidget(lj_del)
        lj_buttons.addWidget(QLabel("RCUT"))
        self.w_mm_lj_rcut = QDoubleSpinBox()
        self.w_mm_lj_rcut.setRange(0.0, 100.0)
        self.w_mm_lj_rcut.setDecimals(2)
        self.w_mm_lj_rcut.setSingleStep(0.5)
        self.w_mm_lj_rcut.setSuffix(" Å")
        self.w_mm_lj_rcut.setValue(10.0)
        self.w_mm_lj_rcut.setToolTip("")
        lj_buttons.addWidget(self.w_mm_lj_rcut)
        lj_buttons.addWidget(HelpBadge(HELP["mm_lj_rcut"]))
        lj_buttons.addStretch(1)
        terms.addLayout(lj_buttons)

        self.w_mm_charges = ExcelTable(["元素", "电荷 e"], widths=[80, 110],
                                       key="mm_charges", settings=self.settings,
                                       sortable=False, editable=True)
        self.w_mm_charges.setMinimumHeight(90)
        terms.addWidget(QLabel("&CHARGE（留空则写 0.0）"))
        terms.addWidget(self.w_mm_charges)
        charge_buttons = QHBoxLayout()
        ch_fill = QPushButton("按结构补全元素")
        ch_fill.clicked.connect(self._fill_mm_charges)
        ch_add = QPushButton("加一行")
        ch_add.clicked.connect(lambda: self._add_mm_row(["", ""], self.w_mm_charges))
        ch_del = QPushButton("删除选中")
        ch_del.clicked.connect(lambda: self._remove_mm_row(self.w_mm_charges))
        for button in (ch_fill, ch_add, ch_del):
            charge_buttons.addWidget(button)
        charge_buttons.addStretch(1)
        terms.addLayout(charge_buttons)
        layout.addWidget(self.mm_terms_box)

        # ---- electrostatics and the odd knobs ------------------------------ #
        self.mm_advanced_box = CollapsibleBox("经典静电与高级选项（可选）",
                                             key="mm_advanced",
                                             settings=self.settings)
        advanced = QFormLayout(self.mm_advanced_box.body)
        advanced.setLabelAlignment(Qt.AlignRight)
        self.w_mm_ewald = QComboBox()
        self.w_mm_ewald.addItem("自动（周期 SPME / 非周期 NONE）", None)
        for kind in MM_EWALD_TYPES:
            self.w_mm_ewald.addItem(kind, kind)
        self.w_mm_ewald_gmax = QSpinBox()
        self.w_mm_ewald_gmax.setRange(0, 400)
        self.w_mm_ewald_gmax.setSpecialValueText("自动")
        self.w_mm_ewald_alpha = QLineEdit()
        self.w_mm_ewald_alpha.setPlaceholderText("留空 = CP2K 按 EWALD_ACCURACY 推")
        self.w_mm_o_spline = QSpinBox()
        self.w_mm_o_spline.setRange(0, 20)
        self.w_mm_o_spline.setSpecialValueText("自动")
        self.w_mm_rcut = QLineEdit()
        self.w_mm_rcut.setPlaceholderText("留空 = 自动")
        self.w_mm_verlet_skin = QLineEdit()
        self.w_mm_verlet_skin.setPlaceholderText("留空 = CP2K 默认 1.0 Å")
        self.w_mm_spline_emax = QLineEdit()
        self.w_mm_spline_emax.setPlaceholderText("如 1.0（Ha）；留空不写 &SPLINE")
        self.w_mm_shift_cutoff = tristate_combo()
        self.w_mm_multiple = QCheckBox("MULTIPLE_POTENTIAL（多个势叠加）")
        self.w_mm_ignore_missing = QCheckBox("缺参数也继续（只当调试用）")
        self.w_mm_stress = tristate_combo()
        form_row(advanced, "mm_ewald", "EWALD_TYPE", self.w_mm_ewald)
        form_row(advanced, "mm_ewald_gmax", "GMAX", self.w_mm_ewald_gmax)
        form_row(advanced, "mm_ewald_alpha", "ALPHA", self.w_mm_ewald_alpha)
        form_row(advanced, "mm_o_spline", "O_SPLINE", self.w_mm_o_spline)
        form_row(advanced, "mm_rcut", "Ewald RCUT", self.w_mm_rcut)
        form_row(advanced, "mm_verlet_skin", "VERLET_SKIN", self.w_mm_verlet_skin)
        form_row(advanced, "mm_spline_emax", "SPLINE EMAX", self.w_mm_spline_emax)
        form_row(advanced, "mm_shift_cutoff", "SHIFT_CUTOFF", self.w_mm_shift_cutoff)
        check_row(advanced, "mm_multiple_potential", self.w_mm_multiple)
        check_row(advanced, "mm_ignore_missing_params", self.w_mm_ignore_missing)
        form_row(advanced, "mm_stress_tensor", "STRESS_TENSOR", self.w_mm_stress)
        layout.addWidget(self.mm_advanced_box)

        for widget in (self.w_mm_route, self.w_mm_parm, self.w_mm_conn,
                       self.w_mm_coord, self.w_mm_coord_format):
            if isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self.schedule)
            else:
                widget.textChanged.connect(self.schedule)
        return box

    # -- listeners used by the classical page ----------------------------- #
    def _browse_mm_parm(self) -> None:
        self._browse_into(
            self.w_mm_parm, "选择力场参数文件",
            "力场参数 (*.prmtop *.parm7 *.top *.pot *.prm *.par *.atop);;所有文件 (*)",
            "last_mm_parm_dir")

    def _browse_mm_conn(self) -> None:
        self._browse_into(
            self.w_mm_conn, "选择拓扑文件",
            "拓扑 (*.prmtop *.parm7 *.psf *.top *.gtop *.atop);;所有文件 (*)",
            "last_mm_conn_dir")

    def _browse_mm_coord(self) -> None:
        self._browse_into(
            self.w_mm_coord, "选择坐标文件",
            "坐标 (*.inpcrd *.crd *.rst *.pdb *.g96 *.xyz);;所有文件 (*)",
            "last_mm_coord_dir")

    def _browse_mm_ff(self) -> None:
        self._browse_into(
            self.w_mm_ff, "选择力场条款文件",
            "CP2K 输入片段 (*.inc *.inp *.txt *.ff);;所有文件 (*)",
            "last_mm_ff_dir")

    def _start_directory(self, key: str | None = None,
                         hint: str = "") -> str:
        """Where a file dialog should open.

        Every browse dialog remembers the directory it ended in, both under its
        own key and under a shared ``last_dir``, so the *next* dialog - whichever
        field it belongs to - opens where the last one left off.  For a classical
        run that is what you want anyway: topology, parameters and coordinates
        almost always live in the same folder.
        """
        if key:
            remembered = self.settings.value(key, "")
            if remembered and Path(str(remembered)).is_dir():
                return str(remembered)
        shared = self.settings.value("last_dir", "")
        if shared and Path(str(shared)).is_dir():
            return str(shared)
        if hint:
            parent = Path(hint).parent
            if str(parent) and parent.is_dir():
                return str(parent)
        return str(Path.home())

    def _remember_directory(self, key: str | None, chosen: str) -> None:
        parent = str(Path(chosen).parent)
        if key:
            self.settings.setValue(key, parent)
        # the shared one is what makes a *different* field open in the same place
        self.settings.setValue("last_dir", parent)

    def _browse_into(self, field: QLineEdit, title: str, filters: str,
                     key: str | None = None) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, title, self._start_directory(key, field.text().strip()), filters)
        if path:
            self._remember_directory(key, path)
            field.setText(path)

    def _add_mm_row(self, values: list, table: ExcelTable | None = None) -> None:
        table = table or self.w_mm_table
        table.add_row(values)

    def _remove_mm_row(self, table: ExcelTable) -> None:
        rows = sorted({index.row() for index in table.selectedIndexes()},
                      reverse=True)
        if not rows and table.rowCount():
            rows = [table.rowCount() - 1]
        for row in rows:
            table.removeRow(row)
        self.schedule()

    def _add_molecule(self) -> None:
        self.w_mm_molecules.add_row(["AMBER", "", "1"])
        self.schedule()

    def _browse_molecule(self) -> None:
        """Pick a topology file and append it as one ``&MOLECULE`` row.

        The connectivity format is guessed from the file name; it stays editable
        in the table (prmtop files are often called ``.top``, so the suffix is a
        hint, not the truth).
        """
        hint = (self.w_mm_conn.text().strip() or self.w_mm_parm.text().strip())
        names, _ = QFileDialog.getOpenFileNames(
            self, "选择每种分子的拓扑文件（可多选）",
            self._start_directory("last_mm_molecule_dir", hint),
            "拓扑 (*.prmtop *.parm7 *.psf *.top *.gtop *.atop);;所有文件 (*)")
        if not names:
            return
        self._remember_directory("last_mm_molecule_dir", names[0])
        suffixes = {"psf": "PSF", "prmtop": "AMBER", "parm7": "AMBER",
                    "gtop": "G96", "top": "AMBER", "atop": "AMBER"}
        for name in names:
            suffix = Path(name).suffix.lstrip(".").lower()
            fmt = suffixes.get(suffix, "AMBER")
            self.w_mm_molecules.add_row([fmt, name, "1"])
        self.schedule()

    def _remove_molecule(self) -> None:
        self._remove_mm_row(self.w_mm_molecules)

    def _mixture_fingerprint(self) -> tuple:
        """The inputs the mixture check reads, so a stale report can be spotted.

        Comparing this against the fingerprint stored with the report is more
        reliable than clearing the report on every widget signal: ``refresh()``
        itself can emit signals (rebuilding the Poisson combo, for instance),
        which would wipe a result the user had just asked for.
        """
        return (tuple((m.conn_file, m.conn_format, m.nmol)
                      for m in self._mm_molecules()),
                self.w_mm_conn.text().strip(),
                self.w_mm_coord.text().strip(),
                self.w_mm_coord_format.currentText())

    def _detect_components_clicked(self) -> None:
        """Re-run the composition detection, even if the table already has rows."""
        self.w_mm_molecules.setRowCount(0)
        self._mm_detected_from = None
        if self._detect_components(force=True) is None:
            set_hint(self.mm_molecules_hint,
                     "先在上面选一个坐标文件（PDB 才带残基分段），再识别。", "warning")
            return
        self._mm_visibility()
    def _check_mixture(self) -> None:
        """Check the coordinate file against the topology file(s), and say so.

        CP2K assigns the coordinates *by position*, so a file built by another
        tool (packmol) is usable exactly when the atom count and the molecule
        order agree with the ``&MOLECULE`` list.  That is checkable here, from
        the files themselves, before the job is queued.
        """
        from .mmcheck import check_mixture, check_single

        molecules = self._mm_molecules()
        coordinate = self.w_mm_coord.text().strip()
        target = self.mm_molecules_hint
        if not coordinate:
            set_hint(target, "先在「坐标文件」里选一个文件（packmol 的 PDB 也行："
                             "把「坐标格式」改成 PDB），再检查。", "warning")
            return
        if not molecules:
            conn = self.w_mm_conn.text().strip()
            if not conn:
                set_hint(target, "先在上面填「拓扑文件」，或在表里加一种分子，再检查。",
                         "warning")
                return
            report = check_single(conn, coordinate, self._coord_format())
        else:
            report = check_mixture(molecules, coordinate,
                                   self._coord_format())
        self.schedule()
        text = report.text().replace("\n", "；")
        # the hint is re-rendered by _mm_visibility on every refresh, so the
        # result is cached together with the inputs it was computed from
        self.mixture_result = (self._mixture_fingerprint(),
                              ("✅ " if report.ok else "⚠ ") + text,
                              "info" if report.ok else "warning")
        self._mm_visibility()

    def _fill_mm_elements(self) -> None:
        """One ε/σ row per element of the loaded structure."""
        known = {self._cell_text(self.w_mm_table, row, 0)
                 for row in range(self.w_mm_table.rowCount())}
        for element in self._element_list():
            if element not in known:
                self.w_mm_table.add_row([element, "", ""])
        self.schedule()

    def _fill_mm_charges(self) -> None:
        known = {self._cell_text(self.w_mm_charges, row, 0)
                 for row in range(self.w_mm_charges.rowCount())}
        for element in self._element_list():
            if element not in known:
                self.w_mm_charges.add_row([element, ""])
        self.schedule()

    def _element_list(self) -> list[str]:
        if self.structure is None:
            return []
        return list(dict.fromkeys(self.structure.elements))

    def _mm_same_file_changed(self, on: bool) -> None:
        """「拓扑文件同参数文件」：勾上就用参数文件那一个路径。

        The generated input then carries ``PARM_FILE_NAME`` only and the build
        inherits it for ``CONN_FILE_NAME`` (``recipes.conn_file_for``), so the
        result is byte-identical to the command line without ``--mm-conn-file``.
        """
        if on:
            self.w_mm_conn.clear()
        self.w_mm_conn.setPlaceholderText(
            "与参数文件相同（勾上右边那个框）" if on else
            "prmtop / .psf / .top：连接性、电荷、LJ 参数")
        self.w_mm_conn.setEnabled(not on)
        self._mm_visibility()
        self._apply_couplings()
        self.schedule()

    def _mm_route_changed(self) -> None:
        """Refill the 1-4 scaling with what the chosen route expects."""
        route = mm_route(self.collect_spec_safe())
        # AMBER/GROMOS name one file twice; CHARMM has a PSF *and* a .pot
        self.w_mm_same_file.setChecked(route.same_file)
        self.w_mm_conn.setEnabled(not self.w_mm_same_file.isChecked())
        if not self.w_mm_vdw14.text().strip() and not self.w_mm_ei14.text().strip():
            self.w_mm_vdw14.setPlaceholderText(
                "跟随力场默认" if route.vdw_scale14 is None
                else f"跟随力场默认 {route.vdw_scale14:g}")
            self.w_mm_ei14.setPlaceholderText(
                "跟随力场默认" if route.ei_scale14 is None
                else f"跟随力场默认 {route.ei_scale14:g}")
        self._mm_visibility()
        self._apply_couplings()
        self.schedule()

    def _detect_components(self, force: bool = False) -> str | None:
        """Fill the ``&MOL_SET`` table from the coordinate file's own blocks.

        A packmol PDB numbers every molecule as residue ``UNL`` and separates them
        with the chain column, so its block sizes *are* the molecule sizes and the
        number of equal-sized blocks *is* ``NMOL``.  The topology file names are
        the one thing a coordinate file cannot tell us, so those cells are left
        for the user - and the format column gets the route's connectivity.

        Re-runs whenever the coordinate file (or its format) changes, carrying
        over any topology path already typed, so dropping another PDB does not
        leave a stale table behind.  ``force`` re-runs it for the button.
        """
        from .mmcheck import detect_components, read_coordinates

        coordinate = self.w_mm_coord.text().strip()
        if not coordinate:
            return None
        fingerprint = (coordinate, self._coord_format())
        if not force and fingerprint == getattr(self, "_mm_detected_from", None):
            return None
        previous = [self._cell_text(self.w_mm_molecules, row, 1)
                    for row in range(self.w_mm_molecules.rowCount())]
        self._mm_detected_from = fingerprint

        def remember(text: str | None) -> str | None:
            """Cache the note so the next refresh can render it again."""
            if text:
                self._mm_detect_note = (fingerprint, text)
            return text

        try:
            info = read_coordinates(coordinate, self._coord_format())
        except (OSError, ValueError) as exc:
            return remember(f"读不出这个坐标文件，没有自动填表：{exc}")
        components, notes = detect_components(info)
        typed = [path for path in previous if path]
        if len(components) < 2 and not force:
            if typed:
                # never throw away a topology path the user typed: keep the rows
                # and say that they no longer describe this coordinate file
                return remember(
                    "⚠ 这个坐标文件里"
                    + (f"只看出一种分子（{components[0].natom} 原子 ×"
                       f"{components[0].count}）" if components
                       else "看不出分子分段")
                    + f"，但表里还有你填过拓扑文件的 {len(typed)} 行——"
                    "它们是按上一个坐标文件识别的，和现在这个对不上。"
                    "点「按坐标文件识别组分」重来；"
                    "如果整个体系有一个合并好的拓扑，清空这张表并填上面那格。")
            self.w_mm_molecules.setRowCount(0)
            if not components:
                return remember("这个坐标文件里看不出分子分段（XYZ 没有残基信息，"
                                "CRD 也没有），没有自动填表。")
            first = components[0]
            return remember(
                f"坐标文件里只看出一种分子（{first.natom} 原子 ×{first.count}），"
                "所以没有自动填表：整个体系合并好的拓扑用上面的「拓扑文件」就行；"
                f"如果手上是**单分子**的拓扑要复制 {first.count} 份，"
                "点「按坐标文件识别组分」把表填出来。")
        connectivity = mm_route(self.collect_spec_safe()).connectivity
        carried = 0
        self.w_mm_molecules.setRowCount(0)
        for index, component in enumerate(components):
            path = previous[index] if index < len(previous) else ""
            carried += bool(path)
            self.w_mm_molecules.add_row([connectivity, path, str(component.count)])
        summary = " + ".join(f"{c.natom} 原子 ×{c.count}" for c in components)
        note = (f"按坐标文件的分段自动填了 {len(components)} 行：{summary}"
                f"（{info.natom} 个原子）。这是从残基/链号读出来的**推测**："
                "拓扑文件那一列还要你填，填完点「检查坐标与拓扑是否对得上」核对")
        if carried:
            note += f"（已保留你填过的 {carried} 个拓扑文件路径）"
        if notes:
            note += "\n⚠ " + "；".join(notes)
        self.schedule()
        return remember(note)

    def coordinates_inline(self) -> bool:
        """Whether the loaded structure is a CP2K input (``&COORD`` comes with it)."""
        source = getattr(self.structure, "source", None)
        return reads_as_cp2k_input(source)

    def _clear_mm_coord_for_inline(self) -> bool:
        """Empty ``COORD_FILE_NAME`` after a CP2K input/restart was loaded.

        The field stays *usable* - it is the user's call whether the coordinates
        come from a file - but a path left over from a previous structure must not
        survive: CP2K's restart is not a coordinate file (it is a CP2K input with
        a ``&COORD`` block), so ``COORD_FILE_NAME cp2k-1.restart`` would make CP2K
        parse that input as an AMBER CRD file.  So it is cleared, with a hint that
        says why and how to put a real coordinate file back.
        """
        if not self.coordinates_inline():
            return False
        current = self.w_mm_coord.text().strip()
        if not current:
            return False
        ours = getattr(self, "_mm_autofilled", None)
        # clear what we filled in ourselves, and anything that *is* a CP2K input
        if not (reads_as_cp2k_input(current) or (ours and current == ours[0])):
            return False
        self._mm_filling = True
        try:
            self.w_mm_coord.clear()
            self.w_mm_coord_format.setCurrentIndex(0)
        finally:
            self._mm_filling = False
        self._mm_autofilled = None
        self._mm_detected_from = None
        return True

    def _coord_format(self) -> str | None:
        """The coordinate format to hand to ``mmcheck``: explicit, else by suffix."""
        chosen = self.w_mm_coord_format.currentData()
        if chosen:
            return str(chosen)
        suffix = Path(self.w_mm_coord.text().strip()).suffix.lower()
        return COORD_SUFFIX_FORMAT.get(suffix)

    def _autofill_mm_coord(self) -> str | None:
        """Point ``COORD_FILE_NAME`` at the loaded structure file, once.

        Dragging a packmol PDB into the window loads it as the **structure** -
        which on the DFT route becomes an inline ``&COORD``.  For a coordinate
        *file* (pdb/inpcrd/xyz/…) the very same file has to be named again as the
        coordinate file, otherwise CP2K has no positions; the format is left on
        "自动（按文件后缀）".

        A **CP2K input or restart is not a coordinate file**: those bring their
        own ``&COORD`` (CP2K's restart writer even removes ``COORD_FILE_NAME``),
        so nothing is filled in and the tool writes ``&COORD`` itself - naming the
        restart as ``COORD_FILE_NAME`` would make CP2K parse an input as an AMBER
        CRD file.

        Loading *another* structure replaces a path this method put there (that is
        what makes a second drag-and-drop work) but never one the user typed.
        """
        source = getattr(self.structure, "source", None)
        if not source or Path(source).suffix.lower() not in COORD_SUFFIX_FORMAT:
            return None
        if reads_as_cp2k_input(source):
            # coordinates come inline: leave COORD_FILE_NAME empty on purpose
            self._mm_autofilled = None
            return None
        current = self.w_mm_coord.text().strip()
        ours = getattr(self, "_mm_autofilled", None)
        if current and current != str(source):
            # a path we did not put there is the user's choice: leave it alone
            if not (ours and current == ours[0]):
                return None
        if current == str(source):
            return None                      # already pointing at this structure
        self._mm_filling = True
        try:
            self.w_mm_coord.setText(str(source))
        finally:
            self._mm_filling = False
        # remember it, so the note survives the refresh that follows: the note
        # is only meaningful while the field still holds what we put there
        self._mm_autofilled = (str(source),
                              f"坐标文件已自动填成刚载入的结构文件（COORDINATE "
                              f"{COORD_SUFFIX_FORMAT[Path(source).suffix.lower()]}）。"
                              "**填了就以这个文件为准**（这时不写 `&COORD`）；"
                              "清空这一格的话，工具会把结构坐标写成输入里的 "
                              "`&COORD`。如果那个文件不是你要用的坐标文件，改掉它。")
        return COORD_SUFFIX_FORMAT[Path(source).suffix.lower()]

    def _mm_visibility(self) -> None:
        """Show only the controls the chosen route actually uses."""
        spec = self.collect_spec_safe()
        route = mm_route(spec)
        file_route = bool(route.conn_file)
        # the autofill only makes sense on the classical route: on the DFT route
        # the whole &MM page is hidden and the structure becomes an inline &COORD
        detect_note = None
        if file_route and is_classical(spec):
            self._clear_mm_coord_for_inline()
            filled = self._autofill_mm_coord()
            detect_note = self._detect_components()
        else:
            filled = None
        for field, visible in (
            (self.h_mm_parm, route.parm_file),
            (self.h_mm_conn, file_route and not spec.mm_molecules),
            (self.h_mm_coord, file_route),
            (self.w_mm_coord_format, file_route),
            (self.mm_molecules_box, file_route),
            (self.mm_terms_box, not file_route),
            (self.h_mm_ff, route.key == "IN_INPUT"),
        ):
            field.setVisible(visible)
        # the field is greyed while it is inherited, but its row (and the check
        # box that switches it back on) stays usable
        self.w_mm_conn.setEnabled(not self.w_mm_same_file.isChecked())
        # ε/σ and charges are generated for both in-input routes, the file routes
        # take them from the topology
        self.w_mm_table.setEnabled(not file_route)
        self.w_mm_charges.setEnabled(not file_route)
        text = {
            "AMBER": "AMBER 路线：prmtop 里同时给出连接性、电荷和 LJ 参数，"
                     "坐标用 inpcrd（COORDINATE CRD）。",
            "CHARMM": "CHARMM 路线：PSF 给连接性，.pot/.prm 给参数，坐标用 PDB 或 CRD。",
            "GROMOS96": "GROMOS96 路线：.top/.gtop 拓扑 + .g96 坐标。",
            "GROMOS87": "GROMOS87 路线：老格式拓扑 + GROMOS96 坐标。",
            "IN_INPUT": "参数直接写在输入里：给一个力场条款文件，"
                        "或在下面的 ε/σ 表里填对势。",
            "PAIR": "纯对势：只要 ε/σ（LJ）和电荷，适合稀有气体这类体系。",
        }.get(route.key, "")
        if file_route:
            text += ("\n第 1 节的「结构」可以留空——这条路线的原子和坐标都来自文件；"
                     "但盒子必须自己给（下面「4. 体系」的晶胞覆盖）：CP2K 的晶胞优先级是 "
                     "CELL_FILE_NAME > ABC > A/B/C > COORD_FILE_NAME，"
                     "工具总会写 `&CELL`，所以坐标文件里的盒子（连 AMBER inpcrd 自带的）"
                     "都用不上。")
        if file_route and self.coordinates_inline():
            text += ("\n**坐标已经随结构文件读进来了**（CP2K 的 restart/`.inp` 带着 "
                     "`&COORD`，CP2K 自己写 restart 时也会去掉 `COORD_FILE_NAME`），"
                     "所以「坐标文件」已经清空、这一格空着就照常写 `&COORD`，"
                     "**不需要再准备 inpcrd**。要用坐标文件就把它填上——"
                     "填了就以文件为准（那时不再写 `&COORD`，而 `&CELL` 仍然来自载入的"
                     "结构；两份不是同一套体系时工具会核对原子数/盒子并警告）。")
        set_hint(self.mm_hint, text)
        autofilled = getattr(self, "_mm_autofilled", None)
        if filled or (autofilled
                      and self.w_mm_coord.text().strip() == autofilled[0]):
            set_hint(self.mm_hint, autofilled[1] + "\n" + text)
        if file_route:
            text = ("一个体系可以由几个组分拼成，`&MOL_SET` 就是「**每个组分一个拓扑文件**」："
                    "每行一种分子的拓扑 + 复制份数 NMOL，坐标来自同一个坐标文件。"
                    "**只有一种分子也能用这张表**——比如手上是 1 个水分子的 prmtop、"
                    "盒子里有 200 个水，就填一行 NMOL 200；"
                    "只有当你有一个**整个体系合并好的拓扑**时才不用它（填上面那格）。\n"
                    "行序要和坐标文件里分子的先后一致。")
            detected = getattr(self, "_mm_detect_note", None)
            if detected and detected[0] == (self.w_mm_coord.text().strip(),
                                            self._coord_format()):
                text = detected[1] + "\n" + text
            result = getattr(self, "mixture_result", None)
            if result and result[0] == self._mixture_fingerprint():
                set_hint(self.mm_molecules_hint, text + "\n" + result[1], result[2])
            else:
                set_hint(self.mm_molecules_hint, text)
        else:
            set_hint(self.mm_terms_hint,
                     "路线用 `CONNECTIVITY GENERATE` 按距离判断成键，"
                     "所以只需要电荷和 ε/σ；键长键角项来自「力场文件」。")

    def collect_spec_safe(self) -> Spec:
        """``collect_spec`` for reactive code: half-typed values must not raise."""
        try:
            return self.collect_spec()
        except Exception:      # noqa: BLE001 - a half-typed field is not an error
            return Spec(task=self.task_combo.currentData())

    def _update_md_kind_hint(self, classical: bool) -> None:
        """Say which kind of MD this page is configuring right now."""
        holder = getattr(self, "md_kind_hint", None)
        if holder is None:
            return
        label = holder.findChild(QLabel)
        if label is None:
            return
        label.setText(
            "**经典 MD（`METHOD FIST`，力场）**：力来自 `&MM` 力场，不看电子；"
            "系综、温度、步数、时间步都在这一页设。想跑 AIMD 就在上面"
            "「计算方法」换回 Quickstep。"
            if classical else
            "**AIMD（从头算分子动力学，`METHOD QUICKSTEP`）**：每一步都由 DFT "
            "现算力。经典 MD（FIST/力场）在上面「计算方法」里选 FIST——"
            "力场路线下这一页的系综/温度/步数照样是 MD 的设置。")

    def _method_changed(self) -> None:
        """Switch between the Quickstep and the classical force-field pages.

        The two are mutually exclusive in CP2K: ``METHOD FIST`` writes no ``&DFT``
        block at all, so the DFT groups are hidden rather than greyed out.
        """
        classical = self.w_method.currentData() == "FIST"
        for box in (self.method_box, self.route_box):
            box.setVisible(not classical)
        # k-points are a Quickstep concept: &KPOINTS lives under &DFT
        if hasattr(self, "kpoint_box"):
            self.kpoint_box.setVisible(not classical)
        if hasattr(self, "kind_box"):
            self.kind_box.setVisible(not classical)
        if hasattr(self, "classical_box"):
            self.classical_box.setVisible(classical)
        # band structure and PDOS are eigenvalues: a force field has none
        model = self.task_combo.model()
        current = self.task_combo.currentData()
        for index in range(self.task_combo.count()):
            data = self.task_combo.itemData(index)
            allowed = not (classical and data in ("band", "pdos"))
            item = model.item(index) if hasattr(model, "item") else None
            if item is not None:
                item.setEnabled(allowed)
        if classical and current in ("band", "pdos"):
            self.task_combo.setCurrentIndex(self.task_combo.findData("energy"))
        # "MD" is two different runs: name the one the current method gives
        index = self.task_combo.findData("md")
        if index >= 0:
            self.task_combo.setItemText(
                index, "经典分子动力学 MD（力场）" if classical
                else "从头算分子动力学 AIMD")
        # a force field is integrated with a longer step than AIMD (0.5 fs),
        # but only touch the box while it still holds the other route's default
        if classical and abs(self.w_timestep.value() - 0.5) < 1e-9:
            self.w_timestep.setValue(1.0)
        elif not classical and abs(self.w_timestep.value() - 1.0) < 1e-9:
            self.w_timestep.setValue(0.5)
        self._update_md_kind_hint(classical)
        if hasattr(self, "restart_hint"):
            self._restart_changed()
        if classical and not self.w_mm_vdw14.text().strip() \
                and not self.w_mm_ei14.text().strip():
            self._mm_route_changed()
            self._route_changed()
            return
        self._mm_visibility()
        # the OT / 对角化 blocks *and* the summary line above them follow the
        # method too, so recompute them here (_route_changed ends with
        # _apply_couplings + schedule, which used to be the last two calls)
        self._route_changed()


    def _kpoint_group(self) -> QGroupBox:
        """``&DFT/&KPOINTS``: Brillouin-zone sampling (its own group, so it is
        reachable for *every* Quickstep task, not only band/PDOS)."""
        box = QGroupBox("3.1 布里渊区采样（&KPOINTS）")
        box.setCheckable(True)
        box.setChecked(False)
        box.toggled.connect(self._kpoints_toggled)
        # one-off explanation shown under the group after a route/k-point switch
        self._kpoint_note: str | None = None
        form = QFormLayout(box)

        self.w_kpoint_gamma_centered = QCheckBox(
            "GAMMA_CENTERED（把网格平移到含 Γ 点）")
        self.w_kpoint_full_grid = QCheckBox(
            "FULL_GRID（用整个网格，不做对称约化）")
        self.w_kpoint_symmetry = QCheckBox("SYMMETRY（按对称性约化 k 点）")
        self.w_kpoint_symmetry.setChecked(True)
        self.w_kpoint_verbose = QCheckBox("VERBOSE（在输出里列出 k 点）")
        self.w_kpoint_wavefunctions = QComboBox()
        self.w_kpoint_wavefunctions.addItems(KPOINT_WAVEFUNCTION_CHOICES)
        for widget in (self.w_kpoint_gamma_centered, self.w_kpoint_full_grid,
                       self.w_kpoint_symmetry, self.w_kpoint_verbose):
            widget.toggled.connect(self._update_kpoint_hint)
        self.w_kpoint_wavefunctions.currentIndexChanged.connect(
            self._update_kpoint_hint)

        grid_row = QHBoxLayout()
        grid_row.setContentsMargins(0, 0, 0, 0)
        self.w_kgrid_auto = QCheckBox("按间距自动")
        self.w_kgrid_auto.setChecked(True)
        self.w_kpoint_spacing = QDoubleSpinBox()
        self.w_kpoint_spacing.setRange(2.0, 200.0)
        self.w_kpoint_spacing.setValue(20.0)
        self.w_kpoint_spacing.setSuffix(" Å")
        self.w_kpoint_spacing.setToolTip("")
        self.w_kgrid = QLineEdit()
        self.w_kgrid.setPlaceholderText("4 4 4")
        self.w_kgrid.setEnabled(False)
        self.w_kgrid_auto.toggled.connect(
            lambda on: (self.w_kgrid.setEnabled(not on),
                        self.w_kpoint_spacing.setEnabled(on)))
        grid_row.addWidget(self.w_kgrid_auto)
        grid_row.addWidget(self.w_kpoint_spacing)
        grid_row.addWidget(self.w_kgrid, 1)
        grid_holder = QWidget()
        grid_holder.setLayout(grid_row)
        grid_label = QWidget()
        grid_label_box = QHBoxLayout(grid_label)
        grid_label_box.setContentsMargins(0, 0, 0, 0)
        grid_label_box.setSpacing(5)
        grid_label_box.addWidget(QLabel("k 网格"))
        grid_label_box.addWidget(HelpBadge(HELP["kgrid"]))
        grid_label_box.addWidget(HelpBadge(HELP["kpoint_spacing"]))
        grid_label_box.addStretch(1)
        form.addRow(grid_label, grid_holder)

        scheme_row = QHBoxLayout()
        scheme_row.setContentsMargins(0, 0, 0, 0)
        self.w_kpoint_scheme = QComboBox()
        for label, value in KPOINT_SCHEMES:
            self.w_kpoint_scheme.addItem(label, value)
        self.w_kpoint_scheme_text = QLineEdit()
        self.w_kpoint_scheme_text.setPlaceholderText(
            "自定义 SCHEME，如 MONKHORST-PACK 6 6 6")
        self.w_kpoint_scheme_text.setVisible(False)
        self.w_kpoint_scheme.currentIndexChanged.connect(
            self._kpoint_scheme_changed)
        scheme_row.addWidget(self.w_kpoint_scheme, 1)
        scheme_row.addWidget(self.w_kpoint_scheme_text, 1)
        scheme_holder = QWidget()
        scheme_holder.setLayout(scheme_row)
        form_row(form, "kpoint_scheme", "SCHEME", scheme_holder)

        self.w_kpoint_points = ExcelTable(
            ["kx", "ky", "kz", "权重"], widths=[90, 90, 90, 70],
            key="kpoint_points", settings=self.settings, sortable=False,
            editable=True)
        self.w_kpoint_points.setMinimumHeight(84)
        points_title = QHBoxLayout()
        points_title.addWidget(QLabel("显式 k 点（SCHEME GENERAL）"))
        points_title.addWidget(HelpBadge(HELP["kpoint_points"]))
        points_title.addStretch(1)
        form.addRow(points_title)
        form.addRow(self.w_kpoint_points)
        points_buttons = QHBoxLayout()
        add_point = QPushButton("加一行")
        add_point.clicked.connect(
            lambda: self.w_kpoint_points.add_row(["0.0", "0.0", "0.0", "1.0"]))
        del_point = QPushButton("删除选中")
        del_point.clicked.connect(
            lambda: self._remove_mm_row(self.w_kpoint_points))
        clear_points = QPushButton("清空")
        clear_points.clicked.connect(self.w_kpoint_points.clear)
        points_buttons.addWidget(add_point)
        points_buttons.addWidget(del_point)
        points_buttons.addWidget(clear_points)
        points_buttons.addWidget(QLabel("UNITS"))
        self.w_kpoint_units = QComboBox()
        self.w_kpoint_units.addItems(KPOINT_UNIT_CHOICES)
        points_buttons.addWidget(self.w_kpoint_units)
        points_buttons.addStretch(1)
        form.addRow(points_buttons)

        check_row(form, "kpoint_gamma_centered", self.w_kpoint_gamma_centered)
        check_row(form, "kpoint_full_grid", self.w_kpoint_full_grid)
        check_row(form, "kpoint_symmetry", self.w_kpoint_symmetry)
        check_row(form, "kpoint_verbose", self.w_kpoint_verbose)
        form_row(form, "kpoint_wavefunctions", "波函数形式",
                 self.w_kpoint_wavefunctions)
        self.kpoint_hint = hint_label()
        form.addRow(self.kpoint_hint)
        return box

    def _kpoints_toggled(self, on: bool) -> None:
        """Enabling the group is what writes ``&KPOINTS`` at all.

        The two settings are mutually exclusive in CP2K: k-points need the
        **diagonalisation** route, and ``&OT`` + ``&KPOINTS`` makes CP2K abort
        (``OT diagonalization not possible with kpoint calculations``,
        ``qs_scf_initialization.F``).  So switching one on swings the other:
        turning k-points on with an explicit OT route moves the route to
        DIAGONALIZATION and says so, and choosing OT later switches k-points
        back off (see ``_scf_route_changed``).
        """
        if on and self.w_scf.currentData() == "OT":
            self._set_widget_value(self.w_scf, "DIAGONALIZATION")
            self._kpoint_note = ("已把 SCF 路线从 **OT** 改成 **DIAGONALIZATION**："
                                 "CP2K 的 OT 不能算 k 点（会直接中止）。")
        # the route label, the OT/diagonalisation boxes, the coupling greying and
        # this group's own hint all follow from the route, so one call updates all
        self._route_changed()
        self.schedule()

    def _scf_route_changed(self) -> None:
        """Choosing OT while k-points are on: k-points have to go."""
        self._kpoint_note = None
        if self.w_scf.currentData() != "OT":
            return
        if hasattr(self, "kpoint_box") and self.kpoint_box.isChecked():
            self.kpoint_box.blockSignals(True)
            self.kpoint_box.setChecked(False)
            self.kpoint_box.blockSignals(False)
            self._kpoint_note = (
                "已关掉 k 点：OT 路线不能用 k 点（CP2K 会中止）。"
                "要在布里渊区采样，请把 SCF 路线换成 DIAGONALIZATION 再勾上。")
        self._update_kpoint_hint()
        self._apply_couplings()
        self.schedule()

    def _kpoint_scheme_changed(self) -> None:
        scheme = self.w_kpoint_scheme.currentData()
        custom = scheme == "custom"
        self.w_kpoint_scheme_text.setVisible(custom)
        if scheme in ("MONKHORST-PACK", "MACDONALD"):
            # these two take their three numbers from the grid box, so the
            # spacing estimate must not be the thing that decides the mesh
            self.w_kgrid_auto.setChecked(False)
        self._update_kpoint_hint()

    def _update_kpoint_hint(self) -> None:
        """Say what the current k-point settings will actually produce."""
        if not hasattr(self, "kpoint_hint"):
            return
        note = getattr(self, "_kpoint_note", None)
        ot = (self.w_scf.currentData() or "auto") == "OT"
        if hasattr(self, "kpoint_box") and not self.kpoint_box.isChecked():
            if ot:
                set_hint(self.kpoint_hint,
                         "当前 SCF 路线是 **OT**，而 OT 不能算 k 点"
                         "（CP2K 会直接中止），所以这里不能再勾。"
                         "要做布里渊区采样：把上面的 SCF 路线改成 "
                         "**DIAGONALIZATION**（或留「自动」）再勾上。",
                         level="warning")
                return
            set_hint(self.kpoint_hint,
                     (note + "\n" if note else "")
                     + "未启用：CP2K 用 Γ 点一个 k 点（不写 `&KPOINTS` 段），"
                     "分子、大盒子和 Γ 点绝缘体都该这样。周期性金属/小带隙要网格时才勾上。",
                     level="warning" if note else None)
            return
        scheme = self.w_kpoint_scheme.currentData()
        prefix = (note + "\n") if note else ""
        warn = "warning" if note else None
        points = self._kpoint_points()
        if points and scheme not in (None, "GENERAL"):
            # the writer makes the explicit list win (CP2K only reads KPOINT
            # lines under SCHEME GENERAL), so say so instead of describing a
            # scheme that will not be the one written
            set_hint(self.kpoint_hint,
                     prefix
                     + f"⚠ SCHEME 选的是 {scheme}，但「显式 k 点」表里有 "
                     f"{len(points)} 个点：写出来会是 `SCHEME GENERAL` + "
                     "`KPOINT x y z w`（CP2K 只在 GENERAL 下读 KPOINT 行）。"
                     "不想要这些点就把表格清空。", level="warning")
            return
        if scheme == "GENERAL" or (scheme is None and points):
            text = (f"显式 k 点：{len(points)} 个（写 `&KPOINTS SCHEME GENERAL` + "
                    "`KPOINT x y z w`）。")
            if self.w_kpoint_symmetry.isChecked():
                text += "⚠ SYMMETRY 还开着，CP2K 会做对称约化；要每个点都算就关掉它。"
            set_hint(self.kpoint_hint, prefix + text,
                     level="warning" if self.w_kpoint_symmetry.isChecked() else warn)
            return
        if scheme is None and self.w_kgrid_auto.isChecked() \
                and self.structure is not None and self.structure.cell is not None:
            grid = suggest_kpoint_grid(self.structure.cell,
                                       self.w_kpoint_spacing.value())
            if grid == (1, 1, 1):
                text = (f"按间距 {self.w_kpoint_spacing.value():g} Å 算出来是 "
                        "1×1×1 → 只写 `SCHEME GAMMA`：这个盒子够大，Γ 点就够。")
            else:
                text = (f"按间距 {self.w_kpoint_spacing.value():g} Å → "
                        f"`SCHEME MONKHORST-PACK {grid[0]} {grid[1]} {grid[2]}`"
                        f"（共 {grid[0] * grid[1] * grid[2]} 个点，对称约化后更少）。")
            if self.w_kpoint_full_grid.isChecked():
                text += "FULL_GRID 打开：不做对称约化，这么多点全部要算。"
            set_hint(self.kpoint_hint, prefix + text, level=warn)
            return
        if scheme is None and not self.w_kgrid_auto.isChecked():
            set_hint(self.kpoint_hint,
                     prefix
                     + f"用手填的网格 {self.w_kgrid.text().strip() or '（还没填）'}"
                     "写 `SCHEME MONKHORST-PACK`。", level=warn)
            return
        if scheme in ("MONKHORST-PACK", "MACDONALD"):
            numbers = self.w_kgrid.text().replace(",", " ").split()
            if len(numbers) != 3:
                # a scheme without its three integers is not a valid CP2K input,
                # so say what is written instead of pretending the empty box is
                # a value
                estimate = suggest_kpoint_grid(
                    self.structure.cell if self.structure is not None else None,
                    self.w_kpoint_spacing.value())
                set_hint(self.kpoint_hint,
                         prefix + f"⚠ SCHEME 选了 {scheme}，但网格框里没有三个数字："
                         f"写出来会按间距 {self.w_kpoint_spacing.value():g} Å 估算成 "
                         f"`SCHEME {scheme} "
                         f"{' '.join(str(n) for n in estimate)}`"
                         "（想自己定就往网格框里填三个整数）。", level="warning")
                return
            text = f"`SCHEME {scheme} {' '.join(numbers)}`"
            try:
                values = [int(float(v)) for v in numbers]
            except ValueError:
                values = []
            if len(values) == 3:
                text += (f"：共 {values[0] * values[1] * values[2]} 个点"
                         + ("，对称约化后更少。" if self.w_kpoint_symmetry.isChecked()
                            else "，SYMMETRY 关着所以全都要算。"))
            set_hint(self.kpoint_hint, prefix + text, level=warn)
            return
        set_hint(self.kpoint_hint, prefix + f"SCHEME {scheme or '自动'}。", level=warn)

    def _kpoint_points(self) -> list[tuple[float, float, float, float]]:
        out: list[tuple[float, float, float, float]] = []
        for row in range(self.w_kpoint_points.rowCount()):
            values = []
            for column in range(4):
                text = self._cell_text(self.w_kpoint_points, row, column)
                try:
                    values.append(float(text) if text.strip() else None)
                except ValueError:
                    values.append(None)
            if values[0] is None or values[1] is None or values[2] is None:
                continue
            out.append((values[0], values[1], values[2],
                        values[3] if values[3] is not None else 1.0))
        return out

    def _method_group(self) -> QGroupBox:
        box = QGroupBox("3. 泛函与精度（METHOD QUICKSTEP）")
        form = QFormLayout(box)

        self.w_functional = QComboBox()
        self.w_functional.setEditable(True)
        self.w_functional.addItems(FUNCTIONALS)
        self.w_dispersion = QComboBox()
        for label, value in DISPERSIONS:
            self.w_dispersion.addItem(label, value)

        cutoff_row = QHBoxLayout()
        self.w_cutoff_auto = QCheckBox("自动")
        self.w_cutoff_auto.setChecked(True)
        self.w_cutoff = QDoubleSpinBox()
        self.w_cutoff.setRange(50.0, 5000.0)
        self.w_cutoff.setDecimals(0)
        self.w_cutoff.setSingleStep(50)
        self.w_cutoff.setValue(400.0)
        self.w_cutoff.setSuffix(" Ry")
        self.w_cutoff.setEnabled(False)
        self.w_cutoff_auto.toggled.connect(lambda on: self.w_cutoff.setEnabled(not on))
        cutoff_row.addWidget(self.w_cutoff_auto)
        cutoff_row.addWidget(self.w_cutoff, 1)
        cutoff_holder = QWidget()
        cutoff_holder.setLayout(cutoff_row)

        self.w_rel_cutoff = QDoubleSpinBox()
        self.w_rel_cutoff.setRange(10.0, 1000.0)
        self.w_rel_cutoff.setValue(60.0)
        self.w_rel_cutoff.setSuffix(" Ry")
        self.w_basis_target = QComboBox()
        for label, value in BASIS_TARGETS:
            self.w_basis_target.addItem(label, value)
        self.w_scf = QComboBox()
        for label, value in SCF_ROUTES:
            self.w_scf.addItem(label, value)
        self.w_eps_scf = QLineEdit()
        self.w_eps_scf.setPlaceholderText("留空自动（1e-6；freq 用 1e-7）")
        self.w_outer_max_scf = QSpinBox()
        self.w_outer_max_scf.setRange(-1, 1000)
        self.w_outer_max_scf.setValue(-1)
        self.w_outer_max_scf.setSpecialValueText("自动")
        self.w_outer_max_scf.setSuffix(" 次")
        self.w_outer_eps_scf = QLineEdit()
        self.w_outer_eps_scf.setPlaceholderText("留空 = 与 EPS_SCF 相同")
        self.w_max_scf = QSpinBox()
        self.w_max_scf.setRange(1, 2000)
        self.w_max_scf.setValue(30)
        self.w_qs_method = QComboBox()
        self.w_qs_method.addItems(["GPW", "GAPW"])
        self.w_eps_default = QLineEdit()
        self.w_eps_default.setPlaceholderText("留空 = CP2K 默认 1e-10（全部 SCF 阈值随它缩放）")
        self.w_extrapolation = QComboBox()
        for label, value in EXTRAPOLATIONS:
            self.w_extrapolation.addItem(label, value)
        self.w_ngrids = QSpinBox()
        self.w_ngrids.setRange(0, 8)
        self.w_ngrids.setSpecialValueText("不写（CP2K 默认 4）")
        self.w_added_mos = QSpinBox()
        self.w_added_mos.setRange(0, 500)
        self.w_added_mos.setSpecialValueText("自动")
        self.w_scf_guess = QComboBox()
        self.w_scf_guess.addItems(["RESTART", "ATOMIC", "HISTORY_RESTART", "RANDOM",
                                   "CORE", "SPARSE", "MOPAC", "EHT", "NONE"])
        self.w_scf_guess.setCurrentText("RESTART")
        self.w_wfn_file = QLineEdit()
        self.w_wfn_file.setPlaceholderText(
            "留空 = 用 <项目名>-RESTART.wfn（上一次算完写的）")
        self.w_scf_guess.currentTextChanged.connect(self._scf_guess_changed)
        self.w_smearing = QCheckBox("SMEAR（金属 / 小带隙）")
        self.w_electronic_temperature = QDoubleSpinBox()
        self.w_electronic_temperature.setRange(50.0, 20000.0)
        self.w_electronic_temperature.setSingleStep(100.0)
        self.w_electronic_temperature.setValue(300.0)
        self.w_electronic_temperature.setSuffix(" K")
        self.w_electronic_temperature.setEnabled(False)
        self.w_smearing.toggled.connect(self._smearing_changed)
        self.w_ignore_convergence = QCheckBox("SCF 没收敛也继续")
        self.w_mixing_alpha = QDoubleSpinBox()
        self.w_mixing_alpha.setRange(0.0, 1.0)
        self.w_mixing_alpha.setSingleStep(0.05)
        self.w_mixing_alpha.setDecimals(2)
        self.w_mixing_alpha.setValue(0.4)
        self.w_mixing_nbuffer = QSpinBox()
        self.w_mixing_nbuffer.setRange(0, 50)
        self.w_mixing_nbuffer.setValue(8)
        self.w_mixing_beta = QLineEdit()
        self.w_mixing_beta.setPlaceholderText("留空不写（CP2K 默认）")
        self.w_mixing_method = QComboBox()
        self.w_mixing_method.addItems(MIXING_METHODS)
        self.w_smear_method = QComboBox()
        self.w_smear_method.addItems(SMEAR_METHODS)
        # ---- SCF route details (OT / diagonalisation) ----
        self.w_ot_minimizer = QComboBox()
        self.w_ot_minimizer.addItems(OT_MINIMIZERS)
        self.w_ot_preconditioner = QComboBox()
        self.w_ot_preconditioner.addItems(OT_PRECONDITIONERS)
        self.w_ot_algorithm = QComboBox()
        self.w_ot_algorithm.addItems(OT_ALGORITHMS)
        self.w_ot_linesearch = QComboBox()
        self.w_ot_linesearch.addItems(OT_LINESEARCHES)
        self.w_ot_stepsize = QLineEdit()
        self.w_ot_stepsize.setPlaceholderText("留空/负数 = CP2K 按预条件子自己选")
        self.w_ot_energy_gap = QLineEdit()
        self.w_ot_energy_gap.setPlaceholderText("留空 = CP2K 自己选（单位 Ha）")
        self.w_ot_safe_diis = tristate_combo()
        self.w_ot_rotation = tristate_combo()
        self.w_outer_optimizer = QComboBox()
        for label, value in OUTER_OPTIMIZERS:
            self.w_outer_optimizer.addItem(label, value)
        self.w_diag_algorithm = QComboBox()
        self.w_diag_algorithm.addItems(DIAG_ALGORITHMS)
        self.w_level_shift = QLineEdit()
        self.w_level_shift.setPlaceholderText("留空不写（0 = 不平移），单位 Ha")

        self.w_basis = QLineEdit()
        self.w_basis.setPlaceholderText("留空自动选择；也可强制如 DZVP-MOLOPT-SR-GTH")
        self.w_basis_file = QLineEdit()
        self.w_basis_file.setPlaceholderText("留空自动")
        self.w_potential_file = QLineEdit()
        self.w_potential_file.setPlaceholderText("留空自动")

        form_row(form, "functional", "交换关联泛函", self.w_functional)
        form_row(form, "dispersion", "色散校正", self.w_dispersion)
        form_row(form, "cutoff", "平面波截断", cutoff_holder)
        form_row(form, "rel_cutoff", "REL_CUTOFF", self.w_rel_cutoff)
        form_row(form, "basis_target", "基组偏好", self.w_basis_target)
        form_row(form, "scf", "SCF 路线", self.w_scf)
        form_row(form, "eps_scf", "EPS_SCF", self.w_eps_scf)
        form_row(form, "max_scf", "内层迭代 MAX_SCF", self.w_max_scf)
        outer_row = QHBoxLayout()
        outer_row.setContentsMargins(0, 0, 0, 0)
        outer_row.addWidget(self.w_outer_max_scf)
        outer_row.addWidget(self.w_outer_eps_scf, 1)
        outer_holder = QWidget()
        outer_holder.setLayout(outer_row)
        # two help entries on this row (the count and the threshold), so build the
        # label by hand instead of going through form_row
        outer_label = QWidget()
        outer_label_box = QHBoxLayout(outer_label)
        outer_label_box.setContentsMargins(0, 0, 0, 0)
        outer_label_box.setSpacing(5)
        outer_label_box.addWidget(QLabel("外层 SCF"))
        outer_label_box.addWidget(HelpBadge(HELP["outer_max_scf"]))
        outer_label_box.addWidget(HelpBadge(HELP["outer_eps_scf"]))
        outer_label_box.addStretch(1)
        form.addRow(outer_label, outer_holder)
        form_row(form, "qs_method", "Quickstep 方法（&QS）", self.w_qs_method)
        form_row(form, "ngrids", "NGRIDS", self.w_ngrids)
        eps_default_row = QHBoxLayout()
        eps_default_row.setContentsMargins(0, 0, 0, 0)
        eps_default_row.addWidget(self.w_eps_default, 1)
        eps_default_row.addSpacing(8)
        eps_default_row.addWidget(QLabel("外推"))
        eps_default_row.addWidget(self.w_extrapolation)
        eps_default_holder = QWidget()
        eps_default_holder.setLayout(eps_default_row)
        eps_label = QWidget()
        eps_label_box = QHBoxLayout(eps_label)
        eps_label_box.setContentsMargins(0, 0, 0, 0)
        eps_label_box.setSpacing(5)
        eps_label_box.addWidget(QLabel("EPS_DEFAULT"))
        eps_label_box.addWidget(HelpBadge(HELP["eps_default"]))
        eps_label_box.addWidget(HelpBadge(HELP["extrapolation"]))
        eps_label_box.addStretch(1)
        form.addRow(eps_label, eps_default_holder)
        self.added_mos_badge = HelpBadge(HELP["added_mos"])
        form_row(form, "added_mos", "空轨道数", self.w_added_mos,
                 badge=self.added_mos_badge)
        guess_row = QHBoxLayout()
        guess_row.setContentsMargins(0, 0, 0, 0)
        guess_row.addWidget(self.w_scf_guess)
        guess_row.addWidget(self.w_wfn_file, 1)
        guess_holder = QWidget()
        guess_holder.setLayout(guess_row)
        form_row(form, "scf_guess", "初始波函数 / 文件", guess_holder)
        check_row(form, "smearing", self.w_smearing)
        form_row(form, "electronic_temperature", "电子温度",
                 self.w_electronic_temperature)
        form_row(form, "smear_method", "展宽方法", self.w_smear_method)
        check_row(form, "ignore_convergence", self.w_ignore_convergence)

        # two rows: four controls on one line could not fit into the panel
        # without pushing a horizontal scrollbar onto the whole form
        mixing_row = QHBoxLayout()
        mixing_row.setContentsMargins(0, 0, 0, 0)
        mixing_row.addWidget(self.w_mixing_method, 1)
        mixing_row.addSpacing(8)
        mixing_row.addWidget(QLabel("α"))
        mixing_row.addWidget(self.w_mixing_alpha)
        mixing_holder = QWidget()
        mixing_holder.setLayout(mixing_row)
        self.mixing_badge = HelpBadge(HELP["mixing"])
        # two entries on this row (the coupling rules and the mixing method), so
        # build the label by hand instead of going through form_row
        mixing_label = QWidget()
        mixing_label_box = QHBoxLayout(mixing_label)
        mixing_label_box.setContentsMargins(0, 0, 0, 0)
        mixing_label_box.setSpacing(5)
        mixing_label_box.addWidget(QLabel("混合（方法/α）"))
        mixing_label_box.addWidget(self.mixing_badge)
        mixing_label_box.addWidget(HelpBadge(HELP["mixing_method"]))
        mixing_label_box.addStretch(1)
        form.addRow(mixing_label, mixing_holder)

        mixing_row2 = QHBoxLayout()
        mixing_row2.setContentsMargins(0, 0, 0, 0)
        mixing_row2.addWidget(QLabel("缓冲"))
        mixing_row2.addWidget(self.w_mixing_nbuffer)
        mixing_row2.addSpacing(8)
        mixing_row2.addWidget(QLabel("β"))
        mixing_row2.addWidget(self.w_mixing_beta, 1)
        mixing_holder2 = QWidget()
        mixing_holder2.setLayout(mixing_row2)
        form.addRow("", mixing_holder2)

        form_row(form, "basis", "强制基组", self.w_basis)
        form_row(form, "basis_file", "基组文件", self.w_basis_file)
        form_row(form, "potential_file", "赝势文件", self.w_potential_file)
        return box

    def _system_group(self) -> QGroupBox:
        box = QGroupBox("4. 体系")
        form = QFormLayout(box)

        self.w_charge = QSpinBox()
        self.w_charge.setRange(-50, 50)
        self.w_multiplicity = QSpinBox()
        self.w_multiplicity.setRange(1, 20)
        self.w_force_uks = QCheckBox("强制 UKS")
        self.w_vacuum = QDoubleSpinBox()
        self.w_vacuum.setRange(0.0, 100.0)
        self.w_vacuum.setValue(12.0)
        self.w_vacuum.setSuffix(" Å")
        self.w_periodic = QComboBox()
        for label, value in PERIODIC_CHOICES:
            self.w_periodic.addItem(label, value)
        self.w_cell = QLineEdit()
        self.w_cell.setPlaceholderText("覆盖晶胞：a,b,c 或 a,b,c,α,β,γ")
        self.w_cell.editingFinished.connect(self._update_scaled_enabled)
        self.w_scaled_coords = QCheckBox("分数坐标（SCALED）")
        self.w_scaled_coords.setEnabled(False)
        # &MOTION/&CONSTRAINT/&FIXED_ATOMS: the slab/surface workhorse
        self.w_fixed_atoms = QLineEdit()
        self.w_fixed_atoms.setPlaceholderText(
            "留空 = 都不固定；CP2K 列表写法，如 1 3 5..8 或 1..12")
        self.w_fixed_atoms_components = QComboBox()
        self.w_fixed_atoms_components.addItems(
            ["XYZ", "X", "Y", "Z", "XY", "XZ", "YZ"])
        # The Poisson solver is only valid for some periodicities, so the combo
        # is rebuilt from the effective periodicity (see _poisson_changed).
        self.w_poisson = QComboBox()
        self.poisson_hint = hint_label()
        self._poisson_updating = False

        form_row(form, "charge", "总电荷", self.w_charge)
        form_row(form, "multiplicity", "自旋多重度", self.w_multiplicity)
        check_row(form, "force_uks", self.w_force_uks)
        form_row(form, "vacuum", "真空层", self.w_vacuum)
        form_row(form, "periodic", "周期性", self.w_periodic)
        form_row(form, "cell", "晶胞覆盖", self.w_cell)
        check_row(form, "scaled_coords", self.w_scaled_coords)
        form_row(form, "poisson_solver", "POISSON 求解器", self.w_poisson)
        form.addRow(self.poisson_hint)
        fixed_row = QHBoxLayout()
        fixed_row.setContentsMargins(0, 0, 0, 0)
        fixed_row.addWidget(self.w_fixed_atoms, 1)
        fixed_row.addWidget(QLabel("方向"))
        fixed_row.addWidget(self.w_fixed_atoms_components)
        fixed_holder = QWidget()
        fixed_holder.setLayout(fixed_row)
        form_row(form, "fixed_atoms", "固定原子（GEO_OPT/MD）", fixed_holder)

        # the solver list follows the cell periodicity, live
        self.w_periodic.currentIndexChanged.connect(self._poisson_changed)
        self.w_vacuum.valueChanged.connect(self._poisson_changed)
        self.w_cell.textChanged.connect(self._poisson_changed)
        self.w_poisson.currentIndexChanged.connect(self._poisson_changed)
        return box

    def _effective_periodicity(self) -> str:
        """The periodicity the generated input will really have.

        Mirrors ``recipes.prepare_structure`` without touching the loaded
        structure: an explicit choice wins, then ``--vacuum`` on a cell-less
        system, then the structure's own cell - and with no cell at all CP2K
        assumes ``PERIODIC XYZ``.
        """
        chosen = self.w_periodic.currentData()
        if chosen:
            return str(chosen).upper()
        if self.structure is None:
            return "XYZ"
        if self.w_vacuum.value() and (self.structure.cell is None
                                      or not self.structure.is_periodic()):
            return "NONE"
        if self._cell_from_field() is not None:
            return str(self.structure.periodic or "XYZ").upper()
        if self.structure.cell is None:
            return "XYZ"
        return str(self.structure.periodic or "XYZ").upper()

    def _cell_from_field(self):
        text = self.w_cell.text().strip()
        if not text:
            return None
        try:
            return parse_cell_text(text)
        except (ValueError, TypeError):
            # The box is half-typed ("30,30,3", "abc").  This runs from
            # ``w_cell.textChanged``, so raising would be swallowed by the event
            # loop and leave the Poisson control describing the old cell; treat it
            # as "no cell typed yet" and fall back to the structure's own cell.
            return None

    def _poisson_changed(self) -> None:
        """Offer only the solvers the manual allows for this periodicity."""
        if self._poisson_updating:
            return
        periodicity = self._effective_periodicity()
        allowed = poisson_solvers_for(periodicity)
        previous = self.w_poisson.currentData()

        self._poisson_updating = True
        # NB: blockSignals as well as the flag.  Rebuilding the list emits
        # currentIndexChanged, which is also wired to _schedule_signal - and
        # refresh() calls this method, so without blocking the two would keep
        # re-scheduling each other and the preview would regenerate for ever
        # (which is what "the preview never settles / seems not to refresh" was).
        blocked = self.w_poisson.blockSignals(True)
        try:
            self.w_poisson.clear()
            if periodicity == "XYZ":
                self.w_poisson.addItem("自动（不写 &POISSON，CP2K 用 PERIODIC）", None)
            else:
                self.w_poisson.addItem("自动（MT；>200 原子用 WAVELET）", None)
            for solver in allowed:
                self.w_poisson.addItem(solver, solver)
            index = self.w_poisson.findData(previous) if previous else 0
            self.w_poisson.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self.w_poisson.blockSignals(blocked)
            self._poisson_updating = False

        picked = self.w_poisson.currentData()
        if picked is None:
            set_hint(self.poisson_hint,
                     f"当前晶胞是 {periodicity}："
                     + ("不写 `&POISSON`，CP2K 默认的 PERIODIC 正好合适。"
                        if periodicity == "XYZ" else
                        f"自动写 `&POISSON PERIODIC {periodicity}` + "
                        "`MT`（原子数 > 200 时换 `WAVELET`）。"))
        else:
            note = poisson_solver_note(picked, periodicity)
            ok = picked in allowed
            set_hint(self.poisson_hint,
                     f"{periodicity} 周期 + `POISSON_SOLVER {picked}`：{note}",
                     level="info" if ok else "warning")

    def _restart_group(self) -> QGroupBox:
        """Restarting: where this run continues *from* (``&EXT_RESTART``).

        What the run *writes* for the next one (``<PROJECT>-RESTART.wfn`` and
        ``<PROJECT>-1.restart``) lives in section 5 「输出」, next to the print
        level, the Molden file and the cube files: those are output settings,
        and mixing them with "where do I continue from" made this group look
        like it had two unrelated halves.

        Only a continuation needs it, so the block is folded away by default.
        """
        box = CollapsibleBox("6. 续算（&EXT_RESTART）", key="restart",
                             settings=self.settings)
        self.restart_box = box
        form = QFormLayout(box.body)
        form.setLabelAlignment(Qt.AlignRight)

        restart_row = QHBoxLayout()
        restart_row.setContentsMargins(0, 0, 0, 0)
        self.w_ext_restart = QLineEdit()
        self.w_ext_restart.setPlaceholderText(
            "<项目名>-1.restart（留空 = 从头算）")
        restart_browse = QToolButton()
        restart_browse.setText("…")
        restart_browse.clicked.connect(self.browse_restart_file)
        self.w_use_found = QToolButton()
        self.w_use_found.setText("用它续算")
        self.w_use_found.setEnabled(False)
        self.w_use_found.setToolTip("")          # guidance lives on the "?"
        self.w_use_found.clicked.connect(self.use_found_restart)
        # A restart file *contains* the coordinates, so a relay does not have to
        # start from a separate structure file: this reads the system out of it
        # (the input still needs &COORD - CP2K only overrides it at run time).
        self.w_use_coords = QToolButton()
        self.w_use_coords.setText("读它的坐标")
        self.w_use_coords.setEnabled(False)
        self.w_use_coords.setToolTip("")
        self.w_use_coords.clicked.connect(self.use_restart_coords)
        restart_row.addWidget(self.w_ext_restart, 1)
        restart_row.addWidget(self.w_use_coords)
        restart_row.addWidget(self.w_use_found)
        restart_row.addWidget(restart_browse)
        restart_holder = QWidget()
        restart_holder.setLayout(restart_row)
        form_row(form, "ext_restart", "从上次续算", restart_holder)

        self.w_restart_default = QCheckBox("位置/速度/计数器都以该文件为准")
        # **Off by default.**  It used to start ticked (because 「取哪些量」 starts at
        # 「全部」), which made the whole 续算 section look switched on - a user read
        # that as "dragging a restart file in turns continuation on".  With the file
        # field empty nothing is continued either way; picking 「全部」 for a file
        # that *is* named ticks it again (`_restart_take_changed`).
        self.w_restart_default.setChecked(False)
        check_row(form, "restart_default", self.w_restart_default)

        # ---- what to take from that file (task chaining) ------------------ #
        form.addRow(section_label("从那个文件里取什么（任务接力）"))
        self.w_restart_take = QComboBox()
        for label, take in RESTART_TAKES:
            self.w_restart_take.addItem(label, list(take) if take else None)
        # the presets are ordered useful-first, but the *initial* state has to
        # agree with the 「都以该文件为准」 box below (checked) - that is 「全部」,
        # "carry on as if nothing happened".  A preset is a deliberate choice, so
        # it is selected by *data* (reordering RESTART_TAKES cannot break this).
        self.select_take(None)
        self.w_restart_take.currentIndexChanged.connect(self._restart_take_changed)
        form_row(form, "restart_take", "取哪些量", self.w_restart_take)
        self.restart_take_hint = hint_label()
        # say what the current option means even before a file is named: the
        # question "which of these do I want?" is asked *before* filling it in
        set_hint(self.restart_take_hint,
                 self._take_hint(self.w_restart_take.currentData()))
        form.addRow(self.restart_take_hint)

        self.restart_hint = hint_label()
        form.addRow(self.restart_hint)

        self.w_ext_restart.textChanged.connect(self._ext_restart_named)
        return box

    def _ext_restart_named(self, text: str) -> None:
        """React to the file field changing (naming it, or clearing it again).

        Naming a file while 「取哪些量」 is at 「全部」 means *continue as it was*
        (``RESTART_DEFAULT T``), so that one tick happens here - and only here, so
        a box the user clears afterwards stays clear through any refresh.  Clearing
        the field takes the tick away again: with nothing named there is nothing to
        continue, and a ticked box made the whole section look switched on.
        """
        if not text.strip():
            self.w_restart_default.setChecked(False)
        elif self.w_restart_take.currentData() is None:
            self.w_restart_default.setChecked(True)
        self._restart_changed()

    def _write_back_group(self, form: QFormLayout) -> None:
        """The two files this run writes **for the next one** (section 5).

        Both are *output* files: ``<PROJECT>-RESTART.wfn`` (the wave function,
        read back by ``SCF_GUESS RESTART``) and ``<PROJECT>-1.restart`` (the
        whole run, read back by ``&EXT_RESTART``).  They used to sit next to the
        ``&EXT_RESTART`` controls, which mixed "what I write" with "what I read"
        - and ``PRINT_LEVEL``/``WALLTIME``/Molden/cubes are output settings too.
        """
        form.addRow(section_label("给下次算准备（写）"))
        self.w_wfn_print = QCheckBox("波函数文件")
        self.w_wfn_print.setChecked(True)
        self.w_wfn_every = QSpinBox()
        self.w_wfn_every.setRange(0, 100000)
        self.w_wfn_every.setValue(0)
        self.w_wfn_every.setSpecialValueText("不按步写")
        self.w_wfn_every.setSuffix(" 步")
        # "every N steps" means different things at different iteration levels:
        # QS_SCF counts the SCF iterations *inside one* force evaluation, so it is
        # the right level for a single point and the wrong one for an MD run.
        self.w_wfn_level = QComboBox()
        for level_label, level_value in (("自动（按任务）", None),
                                         ("按 SCF 步（QS_SCF）", "QS_SCF"),
                                         ("按 MD 步（MD）", "MD"),
                                         ("按几何优化步（GEO_OPT）", "GEO_OPT"),
                                         ("按晶胞优化步（CELL_OPT）", "CELL_OPT")):
            self.w_wfn_level.addItem(level_label, level_value)
        self.w_wfn_add_last = QCheckBox("最后一步也写")
        self.w_wfn_add_last.setChecked(True)
        self.w_wfn_backups = QSpinBox()
        self.w_wfn_backups.setRange(-1, 99)
        self.w_wfn_backups.setValue(-1)
        self.w_wfn_backups.setSpecialValueText("CP2K 默认（1 份）")
        self.w_wfn_backups.setSuffix(" 份")
        check_row(form, "wfn_print", self.w_wfn_print)
        form.addRow(self._each_row(self.w_wfn_every, self.w_wfn_add_last,
                                   self.w_wfn_backups, "wfn_every", "add_last",
                                   level=self.w_wfn_level, level_help="wfn_level"))

        self.w_motion_restart = QCheckBox("重启文件")
        self.w_motion_restart.setChecked(True)
        self.w_restart_every = QSpinBox()
        self.w_restart_every.setRange(0, 1000000)
        self.w_restart_every.setValue(20)
        self.w_restart_every.setSpecialValueText("不按步写")
        self.w_restart_every.setSuffix(" 步")
        self.w_restart_add_last = QCheckBox("最后一步也写")
        self.w_restart_add_last.setChecked(True)
        self.w_restart_backups = QSpinBox()
        self.w_restart_backups.setRange(-1, 99)
        self.w_restart_backups.setValue(-1)
        self.w_restart_backups.setSpecialValueText("CP2K 默认（1 份）")
        self.w_restart_backups.setSuffix(" 份")
        check_row(form, "motion_restart", self.w_motion_restart)
        form.addRow(self._each_row(self.w_restart_every, self.w_restart_add_last,
                                   self.w_restart_backups, "restart_every",
                                   "add_last"))

        self.restart_files_hint = hint_label()
        form.addRow(self.restart_files_hint)

        self.w_wfn_print.toggled.connect(self._restart_changed)
        self.w_motion_restart.toggled.connect(self._restart_changed)
        if hasattr(self, "w_ext_restart"):
            # section 6 already exists (it is built after 5): let it fill the hint
            self._restart_changed()

    def _each_row(self, every: QWidget, add_last: QWidget, backups: QWidget,
                  every_help: str, add_last_help: str,
                  level: QWidget | None = None,
                  level_help: str | None = None) -> QWidget:
        """``&EACH`` and ``ADD_LAST`` are two settings, so they get two controls.

        ``每 N 步`` says how often *during* the run; ``最后一步也写`` is
        ``ADD_LAST NUMERIC`` and only adds the last iteration.  With ``0`` and
        the box ticked you get exactly one dump at the end - the usual choice
        for a wave function restart file.  ``level`` is the ``&EACH`` iteration
        level (only the wave-function file has a choice to make).

        They are laid out on **two lines** (how often / what happens at the end),
        not one: all nine widgets in a single row wanted 650 px, which was wider
        than the whole parameter panel and pushed a horizontal scrollbar into it
        (the right end of every row then sat out of sight).
        """
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(2)

        frequency = QHBoxLayout()
        frequency.setContentsMargins(0, 0, 0, 0)
        frequency.setSpacing(4)
        frequency.addWidget(QLabel("每"))
        frequency.addWidget(every)
        if level is not None:
            frequency.addWidget(level)
            if level_help:
                frequency.addWidget(HelpBadge(HELP[level_help]))
        frequency.addWidget(HelpBadge(HELP[every_help]))
        frequency.addStretch(1)
        box.addLayout(frequency)

        tail = QHBoxLayout()
        tail.setContentsMargins(0, 0, 0, 0)
        tail.setSpacing(4)
        tail.addWidget(add_last)
        tail.addWidget(HelpBadge(HELP[add_last_help]))
        tail.addSpacing(12)
        tail.addWidget(QLabel("备份"))
        tail.addWidget(backups)
        tail.addWidget(QLabel("份"))
        tail.addStretch(1)
        box.addLayout(tail)
        return holder

    def use_found_restart(self) -> None:
        """Continue the run whose restart file was found next to the input.

        "用它续算" means *carry on as if nothing happened*, which is the 「全部」
        preset (``RESTART_DEFAULT T``): a take-list preset left over from an
        earlier experiment would silently narrow that down, so it is reset here
        instead of leaving the checked box disagreeing with the combo.
        """
        found = self._restart_files_found().get("run")
        if found is not None:
            self.w_ext_restart.setText(str(found))
            self.select_take(None)
            self.w_restart_default.setChecked(True)

    def use_restart_coords(self) -> None:
        """Take the system (atoms + cell) out of the restart file.

        CP2K's restart file is a complete input, so it carries the coordinates
        and the cell of that run.  Reading them here means a relay can start
        from the restart file alone - and it keeps ``&COORD`` in the generated
        input, which is what CP2K falls back to when the file is missing.
        """
        path = self._ext_restart_path()
        if path is None:
            return
        self.load_from_path(str(path))
        if self.structure_error:
            set_hint(self.restart_hint,
                     f"读不出 {path.name} 里的坐标：{self.structure_error}",
                     level="warning")
            return
        structure = self.structure
        if structure is None:
            return
        # the coordinates are inlined now, so this *is* the system: &EXT_RESTART
        # only matters if the take-list names something an inline input cannot
        # carry (velocity / counters / thermostat state), not for pos or cell
        take = (self.w_restart_take.currentData()
                if hasattr(self, "w_restart_take") else None)
        takes_pos = take is None or "pos" in take
        set_hint(self.restart_hint,
                 f"已从 {path.name} 读出 {structure.n_atoms()} 个原子"
                 + ("和它的晶胞" if structure.cell is not None
                    else "（这个文件里没有晶胞）")
                 + "：输入里会写上 `&COORD`/`&CELL`，这就是本次要算的体系。"
                 + ("（「取哪些量」里还点着 `RESTART_POS`，运行时它会再覆盖一次"
                    "同样的坐标，没有坏处也只是多余。）" if takes_pos
                    else "（「取哪些量」没取 `RESTART_POS`，跑起来用的就是这份坐标。）"))

    def select_take(self, names) -> None:
        """Select a take-list preset by its *data* (``None`` = 全部).

        The presets are ordered by usefulness, so index 0 is not "全部" any more -
        callers that mean a specific combination have to say so.
        """
        want = list(names) if names else None
        for index in range(self.w_restart_take.count()):
            if self.w_restart_take.itemData(index) == want:
                self.w_restart_take.setCurrentIndex(index)
                return

    def _take_hint(self, take) -> str:
        """Say what the chosen take-list means *for the run as configured*.

        The order of business is "what does this option buy me here?": with the
        coordinates and cell already inherited from the restart, only velocities,
        step counters and the thermostat/barostat history are left - so the hint
        starts from what the option *adds*, then states the consequences for this
        task and ensemble ("step numbering from 0", "velocities redrawn at 330 K",
        "barostat starts from zero").
        """
        if take is None:
            return ("取全部：`RESTART_DEFAULT T`——**同一套参数、同一个任务**"
                    "「像没中断过一样」接着跑。"
                    "注意它把步数计数器也一起搬过来，用**换任务**会出怪现象"
                    "（比如 MD 的编号从上一轮中间开始）。")
        names = "、".join(EXT_RESTART_TAKES[k][0] for k in take
                          if k in EXT_RESTART_TAKES)
        task = self.task_combo.currentData()
        ensemble_w = getattr(self, "w_ensemble", None)
        ensemble = ((ensemble_w.currentText() if ensemble_w is not None else "")
                    or "").upper()
        buys: list[str] = []
        if "vel" in take:
            buys.append("把 restart 里的 `&VELOCITY` 接上（这是内联输入给不了的）")
        if "counters" in take:
            buys.append("步数/迭代编号接着上一轮数")
        if "thermostat" in take:
            buys.append("恒温器状态接着用")
        if "barostat" in take:
            buys.append("压浴状态接着用")
        if "pos" in take or "cell" in take:
            what = "坐标" if "pos" in take and "cell" not in take else (
                "晶胞" if "cell" in take and "pos" not in take else "坐标 + 晶胞")
            buys.append(f"{what}——这**通常已经随结构文件继承了**，多半是多余的")
        head = f"只取 {names}：写 `RESTART_DEFAULT F` + 这几项 `T`。"
        if buys:
            head += "作用：" + "；".join(buys) + "。"
        note: list[str] = []
        if task == "md":
            if "counters" not in take:
                note.append("步数从第 0 步起")
            if "vel" not in take:
                temp = getattr(self, "w_temperature", None)
                value = f"{temp.value():g} K" if temp is not None else "设定温度"
                note.append(f"速度按 {value} 重新起"
                            + ("（NVE 没有恒温器，它决定整段轨迹）"
                               if ensemble == "NVE" else ""))
            if ensemble.startswith(("NPT", "NPE")) and "barostat" not in take:
                note.append("压浴从零开始（不取就没有压力历史）")
        elif task in ("geo_opt", "cell_opt") and "counters" not in take:
            note.append("迭代号从第 1 次起数")
        if not note:
            return head
        where = f"本次是 {ensemble}：" if task == "md" and ensemble else "本次："
        return head + where + "、".join(note) + "。"

    def _restart_take_changed(self) -> None:
        """The take-list decides whether the RESTART_DEFAULT box makes sense."""
        if not hasattr(self, "restart_take_hint"):
            return
        take = self.w_restart_take.currentData()
        if take is None:
            # "全部" *is* RESTART_DEFAULT T - but only tick it once a file is
            # actually named: with the field empty there is nothing to continue,
            # and a pre-ticked box made the section look switched on.
            self.w_restart_default.setChecked(bool(self.w_ext_restart.text().strip()))
        else:
            self.w_restart_default.setChecked(False)
        self._restart_changed()

    def _restart_changed(self) -> None:
        """Keep the restart block consistent while you edit it."""
        has_file = bool(self.w_ext_restart.text().strip())
        take = self.w_restart_take.currentData()
        self._apply_couplings()          # the &EACH / ADD_LAST rows follow their switch
        if hasattr(self, "restart_take_hint"):
            set_hint(self.restart_take_hint, self._take_hint(take))

        # what the *previous* run left in this directory (inherited by name)
        found = self._restart_files_found()
        self.w_use_found.setEnabled("run" in found)

        single_point = self.task_combo.currentData() in ("energy", "freq", "band", "pdos")
        if not has_file:
            # with the coordinates already inlined there is nothing left for the
            # restart file to add unless velocities/counters/thermostat are wanted
            if self.coordinates_inline():
                set_hint(self.restart_hint, self._inline_restart_note())
            else:
                set_hint(self.restart_hint,
                         "只有带迭代步的计算能续（MD / GEO_OPT / CELL_OPT）。")
        elif single_point:
            set_hint(self.restart_hint,
                     f"⚠ 当前任务是「{TASK_LABELS.get(self.task_combo.currentData(), '')}」："
                     "它没有迭代步，`&EXT_RESTART` 会被 CP2K 忽略。", level="warning")
        elif take:
            names = "、".join(EXT_RESTART_TAKES[k][0] for k in take
                             if k in EXT_RESTART_TAKES)
            set_hint(self.restart_hint,
                     f"只从这个文件里取 {names}，其余一概不取。")
        else:
            if self.w_restart_default.isChecked():
                set_hint(self.restart_hint,
                         f"续算文件里的坐标、速度和步数计数器会覆盖本输入里的值")
            else:
                # 「全部」 without RESTART_DEFAULT takes nothing at all - the &
                # EXT_RESTART block would just sit there
                set_hint(self.restart_hint,
                         "「取哪些量」是「全部」，但没勾上面那一项：`&EXT_RESTART` "
                         "只会写 `RESTART_DEFAULT F`，**什么都不取**（等于没续算）。"
                         "要么勾上它，要么在「取哪些量」里点名要什么。",
                         level="warning")

        project = self.project_name()
        written = []
        if self.w_wfn_print.isChecked():
            written.append(f"{project}-RESTART.wfn")
        if self.w_motion_restart.isChecked():
            written.append(f"{project}-1.restart")
        inherited = []
        if "wfn" in found:
            inherited.append(f"{found['wfn'].name}（SCF 初猜会继承它）")
        if "wfn_kp" in found:
            inherited.append(f"{found['wfn_kp'].name}（k 点初猜会继承它）")
        if "run" in found:
            inherited.append(f"{found['run'].name}（可以「用它续算」）")
        text = "会写成：" + "、".join(written) if written \
            else "两个文件都不写（下次只能从头猜波函数）"
        if inherited:
            text += "｜这个目录里已经有：" + "、".join(inherited)
        set_hint(self.restart_files_hint, text)

        # ---- what is actually *in* the file we were pointed at ------------ #
        # CP2K writes the restart file as a complete input, so the tool can say
        # what the previous run left behind and whether the take-list matches -
        # taking velocity from a geometry optimisation's file is the classic
        # silent mistake when chaining tasks by hand
        summary, available = self._ext_restart_summary() if has_file else ("", set())
        # the file carries the atoms, so a relay can start from it alone
        self.w_use_coords.setEnabled(has_file and "pos" in available)
        if summary:
            wanted = set(take or ())
            missing = [k for k in wanted
                       if k not in available and k in EXT_RESTART_TAKES]
            if missing:
                names = "、".join(EXT_RESTART_TAKES[k][0] for k in missing)
                set_hint(self.restart_files_hint,
                         text + f"｜⚠ 这个文件里**没有** {names}，"
                                "取了也没用（CP2K 会用本次输入里的值）",
                         level="warning")
            else:
                set_hint(self.restart_files_hint, text + "｜" + summary)

    def _ext_restart_path(self) -> Path | None:
        """The file in 「从上次续算」, if it is there.

        A bare name means "next to the input" (that is how the cluster sees it),
        so a relative path is first tried against the output directory.
        """
        raw = self.w_ext_restart.text().strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            out = self.w_out.text().strip()
            base = Path(out).parent if out else Path.cwd()
            candidate = base / path
            path = candidate if candidate.exists() else path
        return path if path.exists() else None

    def _ext_restart_summary(self) -> tuple[str, set[str]]:
        """Describe the file in 「从上次续算」, if it is readable."""
        path = self._ext_restart_path()
        if path is None:
            return "", set()
        try:
            return restart_summary(str(path))
        except Exception:      # noqa: BLE001 - a weird file is not a crash
            return "", set()

    def browse_restart_file(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "选择上一次的重启文件",
            self._start_directory("last_restart_dir",
                                  self.w_ext_restart.text().strip()),
            "CP2K 重启文件 (*.restart *.wfn *.kp);;所有文件 (*)")
        if name:
            self._remember_directory("last_restart_dir", name)
            self.w_ext_restart.setText(name)

    def _scf_guess_changed(self, text: str) -> None:
        """The file field only matters when the guess comes from a file."""
        needs_file = text.upper() in ("RESTART", "HISTORY_RESTART")
        self.w_wfn_file.setEnabled(needs_file)
        if not needs_file:
            self.w_wfn_file.clear()

    def _output_group(self) -> QGroupBox:
        box = QGroupBox("5. 输出")
        form = QFormLayout(box)

        self.w_project = QLineEdit()
        self.w_project.setPlaceholderText("留空 = cp2k（同一个目录里换体系时改掉它）")
        self.w_print_level = QComboBox()
        self.w_print_level.addItems(["SILENT", "LOW", "MEDIUM", "HIGH", "DEBUG"])
        self.w_print_level.setCurrentText("MEDIUM")
        self.w_walltime = QSpinBox()
        self.w_walltime.setRange(0, 100_000_000)
        self.w_walltime.setSpecialValueText("不设置")
        self.w_walltime.setSuffix(" s")

        out_row = QHBoxLayout()
        self.w_out = QLineEdit()
        self.w_out.setPlaceholderText("保存路径（留空则用项目名 .inp）")
        out_browse = QToolButton()
        out_browse.setText("…")
        out_browse.clicked.connect(self.browse_output)
        out_row.addWidget(self.w_out, 1)
        out_row.addWidget(out_browse)
        out_holder = QWidget()
        out_holder.setLayout(out_row)

        form_row(form, "project", "项目名", self.w_project)
        form_row(form, "print_level", "打印级别", self.w_print_level)
        form_row(form, "walltime", "WALLTIME", self.w_walltime)
        form_row(form, "out", "输出文件", out_holder)
        self._write_back_group(form)

        # ---- cube files ------------------------------------------------------ #
        self.w_mo_cubes = QCheckBox()
        self.w_mo_cubes_nhomo = QSpinBox()
        self.w_mo_cubes_nhomo.setRange(0, 100)
        self.w_mo_cubes_nhomo.setValue(1)
        self.w_mo_cubes_nhomo.setSuffix(" 条")
        self.w_mo_cubes_nlumo = QSpinBox()
        self.w_mo_cubes_nlumo.setRange(0, 100)
        self.w_mo_cubes_nlumo.setValue(0)
        self.w_mo_cubes_nlumo.setSuffix(" 条")
        self.w_mo_cubes_stride = QLineEdit()
        self.w_mo_cubes_stride.setPlaceholderText("留空 = CP2K 默认 2 2 2（1 1 1 = 全网格）")
        self.w_density_cube = QCheckBox()

        # ---- Molden (folded away: most runs never write it) ------------------ #
        self.molden_box = CollapsibleBox("Molden 文件（看轨道用）", key="molden",
                                        settings=self.settings)
        molden_form = QFormLayout(self.molden_box.body)
        molden_form.setContentsMargins(0, 4, 0, 0)
        molden_form.setLabelAlignment(Qt.AlignRight)
        form.addRow(self.molden_box)
        self.w_molden = QCheckBox("写分子轨道（&MO_MOLDEN）")
        self.w_molden.setChecked(False)
        self.w_molden.toggled.connect(self._molden_changed)
        check_row(molden_form, "print_molden", self.w_molden)

        self.w_molden_file = QLineEdit()
        self.w_molden_file.setPlaceholderText("留空 = CP2K 的 MOS → <项目名>-MOS.molden")
        form_row(molden_form, "molden_file", "文件名", self.w_molden_file)

        self.w_molden_nlumo = QSpinBox()
        self.w_molden_nlumo.setRange(-1, 2000)
        self.w_molden_nlumo.setValue(0)
        self.w_molden_nlumo.setSpecialValueText("只写占据轨道")
        self.w_molden_nlumo.setSuffix(" 条空轨道")
        form_row(molden_form, "molden_nlumo", "空轨道数 NLUMO", self.w_molden_nlumo)

        self.w_molden_unit = QComboBox()
        self.w_molden_unit.addItems(["ANGSTROM", "BOHR"])
        form_row(molden_form, "molden_unit", "文件里的单位", self.w_molden_unit)

        self.w_molden_gto_kind = QComboBox()
        self.w_molden_gto_kind.addItems(["SPHERICAL", "CARTESIAN"])
        form_row(molden_form, "molden_gto_kind", "GTO 形式", self.w_molden_gto_kind)

        self.w_molden_pseudo = QCheckBox("带上 [Pseudo] 块（赝势数据）")
        self.w_molden_cell = QCheckBox("带上 [Cell] 块（晶胞）")
        check_row(molden_form, "molden_write_pseudo", self.w_molden_pseudo)
        check_row(molden_form, "molden_write_cell", self.w_molden_cell)

        self.molden_hint = hint_label()
        molden_form.addRow(self.molden_hint)
        self.molden_controls = [self.w_molden_file, self.w_molden_nlumo,
                                self.w_molden_unit, self.w_molden_gto_kind,
                                self.w_molden_pseudo, self.w_molden_cell]

        # the freq task has its own Molden file: the normal modes
        self.w_molden_vib = QCheckBox("频率计算也写振动模式（&MOLDEN_VIB）")
        self.w_molden_vib.setChecked(True)
        check_row(molden_form, "molden_vib", self.w_molden_vib)

        # ---- cube files: the quickest way to *see* an orbital or a density --- #
        self.cube_box = CollapsibleBox("立方体文件（给 VMD / VESTA / gOpenMol 看）",
                                      key="cubes", settings=self.settings)
        cube_form = QFormLayout(self.cube_box.body)
        cube_form.setContentsMargins(0, 4, 0, 0)
        cube_form.setLabelAlignment(Qt.AlignRight)
        form.addRow(self.cube_box)
        self.w_mo_cubes.setText("写轨道立方体（&MO_CUBES）")
        check_row(cube_form, "print_mo_cubes", self.w_mo_cubes)
        cube_row = QHBoxLayout()
        cube_row.setContentsMargins(0, 0, 0, 0)
        cube_row.addWidget(QLabel("NHOMO"))
        cube_row.addWidget(self.w_mo_cubes_nhomo)
        cube_row.addSpacing(6)
        cube_row.addWidget(QLabel("NLUMO"))
        cube_row.addWidget(self.w_mo_cubes_nlumo)
        cube_row.addSpacing(6)
        cube_row.addWidget(QLabel("STRIDE"))
        cube_row.addWidget(self.w_mo_cubes_stride, 1)
        cube_holder = QWidget()
        cube_holder.setLayout(cube_row)
        form_row(cube_form, "mo_cubes_nhomo", "轨道范围", cube_holder)
        self.w_density_cube.setText("写总电子密度（&TOT_DENSITY_CUBE）")
        check_row(cube_form, "print_density_cube", self.w_density_cube)
        self.w_density_cube_stride = QLineEdit()
        self.w_density_cube_stride.setPlaceholderText("留空 = CP2K 默认 2 2 2")
        form_row(cube_form, "density_cube_stride", "密度 STRIDE",
                 self.w_density_cube_stride)
        self.molden_controls.extend([self.w_mo_cubes_nhomo, self.w_mo_cubes_nlumo,
                                     self.w_mo_cubes_stride])
        self.cube_controls = [self.w_mo_cubes_nhomo, self.w_mo_cubes_nlumo,
                              self.w_mo_cubes_stride, self.w_density_cube_stride]
        for widget in (self.w_mo_cubes, self.w_density_cube):
            widget.toggled.connect(self._cubes_changed)
        self._cubes_changed(False)

        self._molden_changed(False)
        return box

    def _cubes_changed(self, on: bool) -> None:
        """The cube detail rows only matter while their switch is on."""
        for widget in getattr(self, "cube_controls", []):
            widget.setEnabled(self.w_mo_cubes.isChecked()
                              or self.w_density_cube.isChecked())
        self.w_mo_cubes_stride.setEnabled(self.w_mo_cubes.isChecked())
        self._apply_couplings()

    def _molden_changed(self, on: bool) -> None:
        """Explain the Molden file; the greying itself is table-driven.

        ``molden_file``/``molden_nlumo``/... only apply while ``print_molden``
        holds, which is a rule in ``cp2kbuild.couplings`` (same pattern as the
        wave-function restart keys), so ``_apply_couplings`` greys them out and
        the row's "?" carries the reason.
        """
        if not hasattr(self, "molden_hint"):
            return
        if not on:
            set_hint(self.molden_hint,
                     "不写 Molden 文件。要拿轨道去 Molden/Avogadro/Jmol 里看就勾上。")
        else:
            nlumo = self.w_molden_nlumo.value()
            text = ("会写成 <项目名>-"
                    + (self.w_molden_file.text().strip() or "MOS")
                    + ".molden。")
            if nlumo == 0:
                text += " NLUMO 0 = 只有占据轨道；要看 LUMO 就填条数或 -1（全部可用）。"
            if self.task_combo.currentData() in ("band", "pdos"):
                set_hint(self.molden_hint,
                         text + " ⚠ 当前任务是带 k 点的，CP2K **不会**写这个文件"
                                "（Molden 格式不带 k 点）——它被包在 "
                                "`IF (.NOT. do_kpoints)` 里，而且不会提示。",
                         "warning")
            else:
                set_hint(self.molden_hint,
                         text + " CP2K 只在没有 k 点的计算里写它。")
        self._apply_couplings()
        self.schedule()

    # -- interaction ------------------------------------------------------- #
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if urls:
            self.structure_edit.setText(urls[0].toLocalFile())
            self.load_structure()

    def _connect_live_updates(self) -> None:
        """Re-generate the preview shortly after *any* form widget changes.

        Only the left-hand form is walked: the preview and the diagnostics
        table live on the right, so they can never feed back into the builder.
        ``structure_edit`` is skipped on purpose - a structure file is re-read
        on ``editingFinished`` (which schedules on its own), and refreshing on
        every intermediate keystroke would rebuild against the old structure.
        """
        for widget in self.form_host.findChildren(QWidget):
            if widget is self.structure_edit:
                continue
            if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                widget.valueChanged.connect(self._schedule_signal)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._schedule_signal)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._schedule_signal)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._schedule_signal)
        # the classical ε/σ and &MOL_SET tables are edited cell by cell
        for table in self.form_host.findChildren(ExcelTable):
            table.cellChanged.connect(self._schedule_signal)

    def _schedule_signal(self, *_args) -> None:
        """Slot for widget signals that carry a value we do not care about."""
        if getattr(self, "_mm_filling", False):
            # we are filling the coordinate file from the structure: that is not
            # a user edit, so do not schedule a refresh for it
            return
        self.schedule()

    def _task_changed(self) -> None:
        task = self.task_combo.currentData()
        # exactly one task page is visible; the others are hidden, so they do not
        # take part in the layout (see _task_group)
        for name, page in self.task_pages.items():
            page.setVisible(name == task)
        current = self.task_pages[task]
        current.updateGeometry()
        self.task_host.updateGeometry()
        # run the host's layout now: a page that was hidden while its content shrank
        # keeps the old (taller) height until Qt re-runs it, and the user sees that
        # as a blank area under the newly selected task
        layout = self.task_host.layout()
        if layout is not None:
            layout.activate()
        # the task itself can decide the route (band/pdos need DIAGONALIZATION)
        self._route_changed()
        self._mm_visibility()
        self._update_md_kind_hint(self.w_method.currentData() == "FIST")
        if hasattr(self, "w_molden"):
            self._molden_changed(self.w_molden.isChecked())
        self.schedule()

    def schedule(self) -> None:
        """Queue a re-generation; the header says so while it is pending."""
        if hasattr(self, "preview_hint"):
            self.preview_hint.setText("正在重算…")
            self.preview_hint.setStyleSheet("color:#b9770e;")
        if not hasattr(self, "timer"):
            # called from _build_ui (before the timer exists): the form is still
            # being assembled, so there is nothing to re-generate yet
            return
        self.timer.start()

    def browse_structure(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "选择结构文件",
            self._start_directory("last_structure_dir",
                                  self.structure_edit.text().strip()),
            "结构文件 (*.xyz *.pdb *.ent *.cif *.vasp *.poscar *.contcar "
            "*.inp *.restart);;所有文件 (*)")
        if name:
            self._remember_directory("last_structure_dir", name)
            self.structure_edit.setText(name)
            self.load_structure()

    def browse_output(self) -> None:
        default = self.w_out.text() or str(
            Path(self._start_directory("last_output_dir")) / f"{self.project_name()}.inp")
        name, _ = QFileDialog.getSaveFileName(self, "保存输入文件", default,
                                              "CP2K 输入 (*.inp);;所有文件 (*)")
        if name:
            self._remember_directory("last_output_dir", name)
            self.w_out.setText(name)

    def add_replicas(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(
            self, "选择镜像文件（按反应物→产物顺序）",
            self._start_directory("last_structure_dir"),
            "结构文件 (*.xyz *.pdb *.cif *.vasp *.poscar);;所有文件 (*)")
        for name in names:
            self.w_replicas.addItem(name)
        if names:
            self._remember_directory("last_structure_dir", names[0])
            self.schedule()

    def remove_replicas(self) -> None:
        for item in self.w_replicas.selectedItems():
            self.w_replicas.takeItem(self.w_replicas.row(item))
        self.schedule()

    def _structure_from_own_restart(self, path: str) -> tuple[Any, str, str] | None:
        """A CP2K input without ``&COORD``: fall back to the restart it writes.

        A force-field input keeps its atoms in ``COORD_FILE_NAME``, so the input
        itself carries none - and the file it points at (an AMBER ``inpcrd``) has
        no element symbols either, because CP2K takes those from the prmtop.  The
        run it describes, however, writes ``<PROJECT>-1.restart`` right next to it,
        and *that* is a complete input with ``&COORD``/``&CELL`` inlined: the same
        system, ready to be used as the structure.

        Returns ``(structure, path_to_use, why)``, or ``None`` when there is no
        such restart - then the caller shows the full explanation instead.
        """
        from .cli import restart_files_in

        file = Path(path)
        if file.suffix.lower() not in (".inp", ".restart"):
            return None
        try:
            text = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        if "&COORD" in text.upper():
            return None                     # it does have atoms: another problem
        project = re.search(r"(?mi)^\s*PROJECT_NAME\s+(\S+)", text)
        name = project.group(1) if project else file.stem
        found = restart_files_in(file.parent, name).get("run")
        if found is None:
            return None
        try:
            structure = read_structure(found)
        except Exception:                   # noqa: BLE001 - not a usable restart
            return None
        why = (f"{file.name} 里没有 &COORD（原子来自外部文件），所以改读它自己会写的 "
               f"{found.name}——那里面有 &COORD/&CELL。"
               "不是你要的体系就把这一格换成别的文件，或留空走力场文件路线")
        return structure, str(found), why

    def _cell_from_coordinate_file(self) -> str | None:
        """The box inside the named coordinate file, as a ``a,b,c`` string.

        Returns ``None`` when there is no coordinate file, it cannot be read, or it
        carries no box (an XYZ has none, and a PDB written without ``CRYST1``).
        """
        path = self.w_mm_coord.text().strip()
        if not path:
            return None
        from .mmcheck import box_of

        box = box_of(path, self._coord_format())
        if not box:
            return None
        return ",".join(f"{value:.6g}" for value in box)

    def _sibling_cell(self, path: str) -> tuple[str, str] | None:
        """A cell sitting next to the coordinate file under the same stem.

        ``mixture.inpcrd`` usually has ``mixture.pdb`` (``CRYST1``) and/or a CP2K
        restart from the same system beside it - the same-name check keeps this
        from turning into "search the folder and guess".
        """
        from .mmcheck import box_of
        from .structures import read_structure

        file = Path(path)
        if not file.parent.is_dir():
            return None
        for suffix in (".pdb", ".g96", ".cif", ".restart", ".inp"):
            candidate = file.with_suffix(suffix)
            if not candidate.is_file():
                continue
            box = box_of(candidate) if suffix in (".pdb", ".g96") else None
            if box:
                return candidate.name, ",".join(f"{value:.6g}" for value in box)
            try:
                structure = read_structure(candidate)
            except Exception:               # noqa: BLE001 - any file may be there
                continue
            if structure.cell is not None:
                return (candidate.name,
                        ",".join(f"{value:.6g}" for value in structure.cell.lengths()))
        return None

    def _set_cell_from_file(self, text: str) -> None:
        """Fill 「晶胞覆盖」 from the coordinate file (provenance kept for the note)."""
        if self.w_cell.text().strip():
            return                          # never overwrite what the user typed
        self.w_cell.setText(text)
        self._cell_from_file = text

    def load_structure(self) -> None:
        path = self.structure_edit.text().strip()
        self.structure = None
        self.structure_error = None
        self._structure_recovered = None
        if not path:
            self.structure_info.setText("尚未选择结构文件")
            self.structure_info.setStyleSheet("color:#5d6d7e;")
            self.kind_table.setVisible(False)
            self.schedule()
            return
        try:
            self.structure = read_structure(path)
        except Exception as exc:  # noqa: BLE001 - the user may pick anything
            recovered = self._structure_from_own_restart(path)
            if recovered is None:
                self.structure_error = str(exc)
                self.structure_info.setText(f"读取失败：{exc}")
                self.structure_info.setStyleSheet("color:#c0392b;")
                self.kind_table.setVisible(False)
                # ... but it may still be a perfectly good *parameter* source: a
                # FIST input without &COORD holds the whole setup (force field,
                # files, and the &CELL the form needs).  So 读参数 stays available.
                self._importable = self._find_importable(path)
                self.w_import.setEnabled(self._importable is not None)
                if self._importable is not None:
                    set_hint(self.import_info,
                             "这个文件读不出结构（见上一行），但它里面有参数："
                             "点「读参数」把力场、拓扑/坐标文件和晶胞读进来——"
                             "力场路线本来就不需要结构文件。", "warning")
                    self.import_info.setVisible(True)
                else:
                    self.import_info.setVisible(False)
                self.schedule()
                return
            # the file has no atoms, but the run it describes does: keep the
            # system instead of dead-ending on "wrong slot for that file"
            self.structure, chosen, why = recovered
            self.structure_edit.setText(chosen)
            self._structure_recovered = why
            path = chosen

        structure = self.structure
        parts = [f"{structure.formula()} · {structure.n_atoms()} 个原子"]
        if structure.cell is not None:
            a, b, c = structure.cell.lengths()
            alpha, beta, gamma = structure.cell.angles()
            parts.append(f"晶胞 {a:.3f}×{b:.3f}×{c:.3f} Å")
            if max(abs(alpha - 90), abs(beta - 90), abs(gamma - 90)) > 0.01:
                parts.append(f"角 {alpha:.1f}/{beta:.1f}/{gamma:.1f}°")
            parts.append(f"V={structure.cell.volume():.1f} Å³")
        else:
            parts.append("无晶胞")
        parts.append(f"PERIODIC {structure.periodic or '未定'}")
        if not self.w_vacuum.value() and structure.cell is None:
            parts.append("提示：孤立体系建议给一个真空层")
        # a restart file is a complete input, so this was read out of its
        # &COORD/&CELL - say so, because "which构型 is this?" is the question
        # that matters when the file came from a long run
        if Path(path).suffix.lower() == ".restart":
            parts.append(f"（restart 文件：读的是它里面的 &COORD/&CELL）")
        recovered = getattr(self, "_structure_recovered", None)
        if recovered:
            parts.append(recovered)
        self.structure_info.setText(" · ".join(parts))
        self.structure_info.setStyleSheet(
            "color:#b9770e;" if recovered else "color:#1e8449;")
        self._update_scaled_enabled()
        # the deuterium hint says whether the system has any hydrogen at all
        if hasattr(self, "w_h_to_d"):
            self._h_to_d_changed(self.w_h_to_d.isChecked())
        # an .inp / -1.restart also carries parameters, so offer to read them
        self._importable = self._find_importable(path)
        self.w_import.setEnabled(self._importable is not None)
        if self._importable is None:
            self.import_info.setVisible(False)
        if Path(path).suffix.lower() == ".restart":
            self._adopt_restart_file(path)
        self.schedule()

    def _find_importable(self, path: str) -> Path | None:
        """The file itself, when it is a CP2K input with readable settings."""
        try:
            report = import_input(path)
        except Exception:      # noqa: BLE001 - anything unreadable is simply not it
            return None
        return Path(path) if report.settings else None

    def _inline_restart_note(self) -> str:
        """Why ``&EXT_RESTART`` is not needed when the structure brought the coords."""
        return ("坐标和晶胞已经随结构文件读进本次输入（`&COORD`/`&CELL`），"
                "所以 **`&EXT_RESTART` 用不上**——同一份坐标再取一次没有意义。"
                "只有还要**速度**（MD 接力）、**步数计数器**或**恒温器/压浴状态**时，"
                "才把「从上次续算」填上，并在「取哪些量」里至少选上 vel/counters。")

    def _adopt_restart_file(self, path: str) -> None:
        """A CP2K restart read as the structure: say why ``&EXT_RESTART`` is moot.

        The restart *is* a complete input, so the new input gets the coordinates
        (``&COORD``) and the cell from it - and ``&EXT_RESTART`` taking only
        ``pos``/``cell`` would then just repeat what is already there.  So nothing
        is filled in; the note explains when the file *is* worth naming:

        * **velocities** - an MD restart carries ``&VELOCITY`` (which the input
          does not inline), so a MD → MD continuation needs it;
        * **step counters** - to keep numbering where the previous run stopped;
        * **thermostat/barostat state** - to keep the previous control history.

        For a geometry optimisation → MD relay there is nothing in that list, and
        the rebuilt input stands on its own.
        """
        if not hasattr(self, "w_ext_restart"):
            return
        note = self._inline_restart_note()
        self.structure_info.setText(f"{self.structure_info.text()} · {note}")
        if not self.w_ext_restart.text().strip():
            set_hint(self.restart_hint, note)

    # ------------------------------------------------------------------ #
    # reading the parameters back out of the file (「读参数」)
    # ------------------------------------------------------------------ #
    def _import_widgets(self) -> dict[str, QWidget]:
        """Import field -> the control that sets it (also the jump target)."""
        widgets = dict(self._coupling_widgets())
        widgets.update({
            "task": self.task_combo,
            "project": self.w_project,
            "print_level": self.w_print_level,
            "walltime": self.w_walltime,
            "functional": self.w_functional,
            "dispersion": self.w_dispersion,
            "cutoff": self.w_cutoff,
            "rel_cutoff": self.w_rel_cutoff,
            "scf": self.w_scf,
            "eps_scf": self.w_eps_scf,
            "max_scf": self.w_max_scf,
            "outer_eps_scf": self.w_outer_eps_scf,
            "outer_max_scf": self.w_outer_max_scf,
            "qs_method": self.w_qs_method,
            "ngrids": self.w_ngrids,
            "eps_default": self.w_eps_default,
            "extrapolation": self.w_extrapolation,
            "kpoint_scheme": self.w_kpoint_scheme,
            "kpoint_gamma_centered": self.w_kpoint_gamma_centered,
            "kpoint_full_grid": self.w_kpoint_full_grid,
            "kpoint_symmetry": self.w_kpoint_symmetry,
            "kpoint_verbose": self.w_kpoint_verbose,
            "kpoint_wavefunctions": self.w_kpoint_wavefunctions,
            "kpoint_units": self.w_kpoint_units,
            "kpoint_points": self.w_kpoint_points,
            "print_mo_cubes": self.w_mo_cubes,
            "mo_cubes_nhomo": self.w_mo_cubes_nhomo,
            "mo_cubes_nlumo": self.w_mo_cubes_nlumo,
            "mo_cubes_stride": self.w_mo_cubes_stride,
            "print_density_cube": self.w_density_cube,
            "density_cube_stride": self.w_density_cube_stride,
            "fixed_atoms": self.w_fixed_atoms,
            "fixed_atoms_components": self.w_fixed_atoms_components,
            "mm_lj": self.w_mm_table,
            "mm_charges": self.w_mm_charges,
            "mm_molecules": self.w_mm_molecules,
            # &SUBSYS/&CELL: the box a force-field input keeps even when its atoms
            # come from COORD_FILE_NAME (and the form needs it for FIST)
            "cell": self.w_cell,
            "periodic": self.w_periodic,
        })
        return widgets

    def _set_widget_value(self, widget: QWidget, value) -> bool:
        """Put one imported value into one control; False = the form cannot hold it."""
        if isinstance(widget, QComboBox):
            wanted = list(value) if isinstance(value, (tuple, list)) else value
            for i in range(widget.count()):
                data = widget.itemData(i)
                if isinstance(data, list) and isinstance(wanted, list):
                    if data == wanted:
                        widget.setCurrentIndex(i)
                        return True
                elif data == wanted:
                    widget.setCurrentIndex(i)
                    return True
            text = str(value)
            index = widget.findText(text, Qt.MatchFixedString)
            if index < 0:
                index = widget.findText(text, Qt.MatchContains)
            if index >= 0:
                widget.setCurrentIndex(index)
                return True
            return False
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
            return True
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(float(value) if isinstance(widget, QDoubleSpinBox)
                            else int(value))
            return True
        if isinstance(widget, QLineEdit):
            widget.setText("" if value is None else str(value))
            return True
        return False

    def _apply_import_tables(self, report) -> tuple[list, list]:
        """The three tables need reshaping: the form's columns are its own."""
        applied, skipped = [], []
        rows = report.get("mm_lj")
        if rows:
            self.w_mm_table.setRowCount(0)
            rcut = None
            for first, second, eps, sigma, cut in rows:
                label = first if first == second else f"{first} {second}"
                self.w_mm_table.add_row(
                    [label, "" if eps is None else f"{eps:g}",
                     "" if sigma is None else f"{sigma:g}"])
                rcut = cut if cut is not None else rcut
            if rcut is not None:
                self.w_mm_lj_rcut.setValue(float(rcut))
            applied.append(f"ε/σ 表（{len(rows)} 行）")
        rows = report.get("mm_charges")
        if rows:
            self.w_mm_charges.setRowCount(0)
            for element, charge in rows:
                self.w_mm_charges.add_row(
                    [str(element), "" if charge is None else f"{charge:g}"])
            applied.append(f"&CHARGE（{len(rows)} 行）")
        rows = report.get("mm_molecules")
        if rows:
            self.w_mm_molecules.setRowCount(0)
            for conn_file, fmt, nmol in rows:
                self.w_mm_molecules.add_row(
                    [str(fmt or "AMBER"), str(conn_file), str(nmol or 1)])
            applied.append(f"组分表（{len(rows)} 行）")
        return applied, skipped

    def _apply_import_scheme(self, text: str) -> None:
        """``MONKHORST-PACK 4 4 4`` -> the combo *and* the grid field."""
        tokens = text.split()
        name = tokens[0].upper() if tokens else ""
        self.kpoint_box.setChecked(True)
        if name in ("MONKHORST-PACK", "MACDONALD", "GAMMA", "GENERAL"):
            self._set_widget_value(self.w_kpoint_scheme, name)
            if len(tokens) > 1:
                self.w_kgrid.setText(" ".join(tokens[1:]))
                self.w_kgrid_auto.setChecked(False)
            elif name in ("MONKHORST-PACK", "MACDONALD"):
                self.w_kgrid_auto.setChecked(False)
            return
        if name:
            self._set_widget_value(self.w_kpoint_scheme, "custom")
            self.w_kpoint_scheme_text.setText(text)

    def _apply_import_points(self, rows: list) -> None:
        self.kpoint_box.setChecked(True)
        self.w_kpoint_points.setRowCount(0)
        for row in rows:
            self.w_kpoint_points.add_row([f"{float(v):g}" for v in row])

    def import_parameters(self) -> None:
        """Read the settings out of the structure file into the form."""
        if self._importable is None:
            return
        report = import_input(self._importable)
        widgets = self._import_widgets()
        # the &EACH level follows the task unless the file says otherwise, so start
        # from "自动": a level pinned by an earlier import must not leak into this
        # one (the file only reports a level when it contradicts its own task)
        self.w_wfn_level.setCurrentIndex(0)
        applied, skipped = [], []
        # task/method/route first: they decide which pages and rows exist
        order = {"task": 0, "method": 1, "mm_route": 2, "scf": 3}
        for setting in sorted(report.settings,
                              key=lambda s: order.get(s.field, 9)):
            # two settings are not a single widget: the k-point scheme carries
            # its own numbers, and the explicit points are a table
            if setting.field == "kpoint_scheme":
                self._apply_import_scheme(str(setting.value))
                applied.append(setting)
                continue
            if setting.field == "kpoint_points":
                self._apply_import_points(setting.value)
                applied.append(setting)
                continue
            if setting.field == "metadyn":
                # the MD page's 元动力学 block holds these tables
                panel = getattr(self, "metadyn_panel", None)
                if panel is None:
                    skipped.append(f"{setting.label}（没有元动力学块）")
                else:
                    panel.import_metadyn(setting.value)
                    applied.append(setting)
                continue
            widget = widgets.get(setting.field)
            if widget is None:
                skipped.append(f"{setting.label}（界面里没有对应控件）")
                continue
            if setting.field == "cutoff":
                self.w_cutoff_auto.setChecked(False)
            if self._set_widget_value(widget, setting.value):
                applied.append(setting)
            else:
                skipped.append(f"{setting.label}={setting.text()}")
        table_applied, _ = self._apply_import_tables(report)
        self._show_import(report, applied, table_applied, skipped)
        if self.structure_error:
            # the file could not be the *structure*, but its parameters are in the
            # form now - and a force-field file route needs no structure at all, so
            # the old failure must not keep the preview switched off
            self.structure_error = None
            self.structure_info.setText(
                "结构这一格的文件读不出坐标；参数已经读进表单"
                "（力场文件路线只要拓扑/坐标文件 + 晶胞，不需要结构文件）")
            self.structure_info.setStyleSheet("color:#b9770e;")
        self.refresh()

    def _show_import(self, report, applied: list, tables: list[str],
                     skipped: list[str]) -> None:
        links = "、".join(
            f'<a href="{html.escape(s.field)}" style="color:#1a5276;'
            f'text-decoration:none;">{html.escape(s.label)} '
            f'<b>{html.escape(s.text())}</b></a>' for s in applied)
        parts = [f"<b>{html.escape(report.path.name)}</b> 里的参数已读进表单："]
        parts.append(links if links else "（没有能对上的参数）")
        if tables:
            parts.append("、".join(html.escape(t) for t in tables))
        text = "　".join(parts)
        text += "<br><span style='color:#5d6d7e;'>点参数名可以跳到那个控件。</span>"
        if skipped:
            text += ("<br><span style='color:#7d6608;'>没对上："
                     + html.escape("、".join(skipped)) + "</span>")
        if report.unread:
            text += ("<br><span style='color:#7d6608;'>这个文件里工具没读的："
                     + html.escape("、".join(report.unread)) + "</span>")
        self.import_info.setText(text)
        self.import_info.setVisible(True)

    def _jump_to_field(self, field: str) -> None:
        """Scroll the form to a control and flash it."""
        if field == "metadyn":
            panel = getattr(self, "metadyn_panel", None)
            if panel is not None:
                panel.flash()
            return
        widget = self._import_widgets().get(field)
        if widget is None:
            return
        self.form_scroll.ensureWidgetVisible(widget, 40, 60)
        widget.setFocus(Qt.OtherFocusReason)
        previous = widget.styleSheet()
        widget.setStyleSheet("background-color:#fff3cd;")
        QTimer.singleShot(1400, lambda: widget.setStyleSheet(previous))

    def project_name(self) -> str:
        """PROJECT_NAME: the field, else ``cp2k``.

        A fixed default is what lets a second run inherit the first one's
        restart files (``cp2k-RESTART.wfn`` / ``cp2k-1.restart``) - the same
        convention the group templates use.  Change it when several systems
        share one directory.
        """
        return self.w_project.text().strip() or "cp2k"

    def output_directory(self) -> Path:
        """Where the input file (and hence CP2K's restart files) will live."""
        target = self.w_out.text().strip()
        if target:
            parent = Path(target).parent
            if str(parent):
                return parent
        return Path.cwd()

    def _restart_files_found(self) -> dict[str, Path]:
        from .cli import restart_files_in

        return restart_files_in(self.output_directory(), self.project_name())

    def reset_defaults(self) -> None:
        """Put every *parameter* back to what a bare CLI run would use.

        The structure file and the chosen task are kept - they are the user's
        context, not a parameter.  Every widget ``collect_spec`` reads has to
        appear here, otherwise this button silently restores an old default:
        that is exactly how ``SCF_GUESS`` kept coming back as ATOMIC after the
        default became RESTART.  ``gui_selftest`` now pins this button against
        the CLI defaults, so the two can no longer drift apart.
        """
        # ---- structure-related (the file itself is kept) ----
        self.w_vacuum.setValue(12.0)
        self.w_periodic.setCurrentIndex(0)
        self.w_cell.clear()
        self.w_scaled_coords.setChecked(False)
        self.w_h_to_d.setChecked(False)
        self._poisson_changed()          # rebuild the solver list for this cell

        # ---- method: back to the DFT Quickstep route ----
        self.w_method.setCurrentIndex(0)
        self._reset_mm_defaults()

        # ---- method / accuracy ----
        self.w_functional.setCurrentText("PBE")
        self.w_dispersion.setCurrentIndex(0)
        self.w_cutoff_auto.setChecked(True)
        self.w_cutoff.setValue(400.0)
        self.w_rel_cutoff.setValue(60.0)
        self.w_eps_default.clear()
        self.w_extrapolation.setCurrentIndex(0)
        # ---- Brillouin-zone sampling: off again (= Gamma, no &KPOINTS) ----
        self.kpoint_box.setChecked(False)
        self.w_kgrid_auto.setChecked(True)
        self.w_kpoint_spacing.setValue(20.0)
        self.w_kgrid.clear()
        self.w_kpoint_scheme.setCurrentIndex(0)
        self.w_kpoint_scheme_text.clear()
        self.w_kpoint_points.setRowCount(0)
        self.w_kpoint_units.setCurrentText("B_VECTOR")
        self.w_kpoint_gamma_centered.setChecked(False)
        self.w_kpoint_full_grid.setChecked(False)
        self.w_kpoint_symmetry.setChecked(True)
        self.w_kpoint_verbose.setChecked(False)
        self.w_kpoint_wavefunctions.setCurrentText("REAL")
        # ---- cube files ----
        self.w_mo_cubes.setChecked(False)
        self.w_mo_cubes_nhomo.setValue(1)
        self.w_mo_cubes_nlumo.setValue(0)
        self.w_mo_cubes_stride.clear()
        self.w_density_cube.setChecked(False)
        self.w_density_cube_stride.clear()
        # ---- constraints ----
        self.w_fixed_atoms.clear()
        self.w_fixed_atoms_components.setCurrentText("XYZ")
        self.w_basis_target.setCurrentIndex(0)
        self.w_basis.clear()
        self.w_basis_file.clear()
        self.w_potential_file.clear()
        self.w_scf.setCurrentIndex(0)
        self.w_eps_scf.clear()
        self.w_max_scf.setValue(30)
        self.w_outer_max_scf.setValue(-1)
        self.w_outer_eps_scf.clear()
        self.w_qs_method.setCurrentText("GPW")
        self.w_ngrids.setValue(0)
        self.w_added_mos.setValue(0)
        self.w_smearing.setChecked(False)
        self.w_electronic_temperature.setValue(300.0)
        self.w_ignore_convergence.setChecked(False)
        self.w_mixing_alpha.setValue(0.4)
        self.w_mixing_nbuffer.setValue(8)
        self.w_mixing_beta.clear()
        self.w_mixing_method.setCurrentText("BROYDEN_MIXING")
        self.w_smear_method.setCurrentText("FERMI_DIRAC")

        # ---- SCF route details ----
        self.w_ot_minimizer.setCurrentText("DIIS")
        self.w_ot_preconditioner.setCurrentText("FULL_SINGLE_INVERSE")
        self.w_ot_algorithm.setCurrentText("STRICT")
        self.w_ot_linesearch.setCurrentText("2PNT")
        self.w_ot_stepsize.clear()
        self.w_ot_energy_gap.clear()
        self.w_ot_safe_diis.setCurrentIndex(0)
        self.w_ot_rotation.setCurrentIndex(0)
        self.w_outer_optimizer.setCurrentIndex(0)
        self.w_diag_algorithm.setCurrentText("STANDARD")
        self.w_level_shift.clear()

        # ---- system ----
        self.w_charge.setValue(0)
        self.w_multiplicity.setValue(1)
        self.w_force_uks.setChecked(False)

        # ---- output ----
        self.w_project.clear()
        self.w_print_level.setCurrentText("MEDIUM")
        self.w_walltime.setValue(0)
        self.w_molden.setChecked(False)
        self.w_molden_file.clear()
        self.w_molden_nlumo.setValue(0)
        self.w_molden_unit.setCurrentText("ANGSTROM")
        self.w_molden_gto_kind.setCurrentText("SPHERICAL")
        self.w_molden_pseudo.setChecked(False)
        self.w_molden_cell.setChecked(False)
        self.w_molden_vib.setChecked(True)
        self._molden_changed(False)

        # ---- restart / continuation ----
        self.w_scf_guess.setCurrentText("RESTART")
        self.w_wfn_file.clear()
        self.w_wfn_print.setChecked(True)
        self.w_wfn_every.setValue(0)
        self.w_wfn_level.setCurrentIndex(0)
        self.w_wfn_add_last.setChecked(True)
        self.w_wfn_backups.setValue(-1)
        self.w_ext_restart.clear()
        self.w_restart_default.setChecked(True)
        self.w_restart_default.setEnabled(True)
        self.select_take(None)          # back to "全部" by *data*, not by index
        self.w_motion_restart.setChecked(True)
        self.w_restart_every.setValue(20)
        self.w_restart_add_last.setChecked(True)
        self.w_restart_backups.setValue(-1)

        # ---- task pages (the widgets are shared between tasks) ----
        self.w_print_forces.setChecked(True)
        self.w_print_mulliken.setChecked(False)
        self.w_print_dipole.setChecked(True)
        self.w_geo_optimizer.setCurrentText("BFGS")
        self.w_geo_max_iter.setValue(200)
        self.w_cell_optimizer.setCurrentText("BFGS")
        self.w_cell_max_iter.setValue(200)
        self.w_pressure.setValue(0.0)
        self.w_keep_angles.setChecked(False)
        self.w_ensemble.setCurrentText("NVT")
        self.w_thermostat.setCurrentText("NOSE")
        self.w_temperature.setValue(300.0)
        self.w_timestep.setValue(0.5)
        self.w_steps.setValue(5000)
        self.w_md_pressure.setValue(1.0)
        self.w_seed.setValue(0)
        self.w_thermostat_timecon.setValue(100.0)
        self.w_barostat_timecon.setValue(500.0)
        self.w_trajectory_stride.setValue(10)
        self.w_thermostat_region.setCurrentIndex(0)        # GLOBAL
        self.w_md_temp_kind.setChecked(False)
        self.w_md_temp_kind_every.setValue(10)
        self.w_md_energy.setChecked(False)
        self.w_md_energy_every.setValue(1)
        self.w_md_energy_file.clear()
        self.w_thermostat_temp.setChecked(False)
        self.w_thermostat_temp_every.setValue(10)
        self.w_ramp.setChecked(False)
        self.w_ramp_target.setValue(600.0)
        self.w_temperature_annealing.setValue(0.0)
        self.w_annealing.setValue(0.0)
        self.w_annealing_cell.setValue(0.0)
        self.w_langevin_gamma.setValue(0.01)
        self.w_restart_temperature_annealing.setChecked(False)
        if hasattr(self, "ramp_hint"):
            self._ramp_changed()
        if hasattr(self, "w_program_table"):
            while self.w_program_table.rowCount():
                self.w_program_table.removeRow(0)
            self._program_add_row(300.0, 5.0)
            self._program_add_row(300.0, 10.0, target=600.0)
            self.w_program_outdir.clear()
            self.w_program_mpirun.setText("mpirun")
            self.w_program_bin.setCurrentText("cp2k.psmp")
            self.w_program_procs.setValue(64)
            self.w_program_omp.setValue(2)
            self.w_program_resume.setChecked(True)
        self.w_intensities.setChecked(True)
        self.w_thermochemistry.setChecked(False)
        self.w_lattice.setCurrentIndex(0)
        self.w_kpath.clear()
        self.w_npoints.setValue(40)
        self.w_kgrid.clear()
        self.w_pdos_nlumo.setValue(0)
        self.w_pdos_ldos.clear()
        self.w_pdos_broaden.setValue(0.0)
        self.w_pdos_broaden_type.setCurrentText("GAUSSIAN")
        self.w_band_type.setCurrentText(BAND_TYPES[0])
        self.w_k_spring.setValue(0.05)
        # the NEB image list is kept: like the structure file it says *which*
        # system, not how to compute it
        self.schedule()

    def _reset_mm_defaults(self) -> None:
        """The classical page, back to a bare ``--method fist`` run.

        The file paths are *kept*: like the structure they say which system this
        is, and retyping a prmtop path is exactly the sort of thing this button
        is not for.
        """
        self.w_mm_route.setCurrentIndex(0)          # AMBER
        self.w_mm_same_file.setChecked(True)        # AMBER: prmtop 同时是拓扑
        self.w_mm_coord_format.setCurrentIndex(0)
        self.w_mm_path_style.setCurrentIndex(0)
        # let the composition be recognised again from the current coordinate file
        self._mm_detected_from = None
        self.w_mm_vdw14.clear()
        self.w_mm_ei14.clear()
        self.w_mm_ff.clear()
        self.w_mm_molecules.setRowCount(0)
        self.w_mm_table.setRowCount(0)
        self.w_mm_charges.setRowCount(0)
        self.w_mm_ewald.setCurrentIndex(0)
        self.w_mm_ewald_gmax.setValue(0)
        self.w_mm_ewald_alpha.clear()
        self.w_mm_o_spline.setValue(0)
        self.w_mm_rcut.clear()
        self.w_mm_verlet_skin.clear()
        self.w_mm_spline_emax.clear()
        self.w_mm_shift_cutoff.setCurrentIndex(0)
        self.w_mm_multiple.setChecked(False)
        self.w_mm_ignore_missing.setChecked(False)
        self.w_mm_stress.setCurrentIndex(0)

    # -- spec + build ------------------------------------------------------ #
    # -- classical tables -> Spec ----------------------------------------- #
    @staticmethod
    def _cell_text(table: ExcelTable, row: int, column: int) -> str:
        item = table.item(row, column)
        return item.text().strip() if item is not None else ""

    @staticmethod
    def _cell_float(table: ExcelTable, row: int, column: int) -> float | None:
        text = GenerateTab._cell_text(table, row, column)
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None

    def _mm_molecules(self) -> list[MmMolecule]:
        """One ``&MOL_SET/&MOLECULE`` per row.

        A row without a topology file is still emitted: the generated input then
        carries an empty ``&MOLECULE`` with a comment saying what is missing, and
        the validator names that row ("&MOLECULE 2 has no CONN_FILE_NAME") instead
        of the far vaguer "CONNECTIVITY AMBER reads a topology file".  Only a row
        with nothing in it at all is dropped.
        """
        out: list[MmMolecule] = []
        for row in range(self.w_mm_molecules.rowCount()):
            fmt = self._cell_text(self.w_mm_molecules, row, 0).upper()
            path = self._cell_text(self.w_mm_molecules, row, 1)
            count = self._cell_text(self.w_mm_molecules, row, 2)
            if not (fmt or path or count):
                continue                      # a fully empty row adds nothing
            try:
                nmol = int(float(count)) if count else 1
            except ValueError:
                nmol = 1
            out.append(MmMolecule(path or None, fmt or "AMBER", max(1, nmol)))
        return out

    def _mm_charges(self) -> list[tuple[str, float]]:
        out: list[tuple[str, float]] = []
        for row in range(self.w_mm_charges.rowCount()):
            element = self._cell_text(self.w_mm_charges, row, 0)
            value = self._cell_float(self.w_mm_charges, row, 1)
            if element and value is not None:
                out.append((element, value))
        return out

    def _mm_lj(self) -> list[MmLennardJones]:
        """Explicit element pairs (``O-H``) plus the mixed ones, in CLI order."""
        explicit: list[MmLennardJones] = []
        for row in range(self.w_mm_table.rowCount()):
            atoms = self._cell_text(self.w_mm_table, row, 0).replace(
                "-", " ").replace("/", " ").split()
            if len(atoms) < 2:
                continue                     # a scalar row goes through mixing
            explicit.append(MmLennardJones(
                atoms[:2],
                self._cell_float(self.w_mm_table, row, 1),
                self._cell_float(self.w_mm_table, row, 2)))
        return lennard_jones_pairs(explicit, self._mm_element_lj(),
                                   self.w_mm_lj_rcut.value() or None)

    def _mm_element_lj(self) -> dict[str, tuple[float, float]]:
        """Per-element ε/σ; the writer mixes the pairs with Lorentz-Berthelot."""
        table: dict[str, tuple[float, float]] = {}
        for row in range(self.w_mm_table.rowCount()):
            element = self._cell_text(self.w_mm_table, row, 0)
            if not element or " " in element or "-" in element or "/" in element:
                continue                     # a pair row, handled by _mm_lj
            epsilon = self._cell_float(self.w_mm_table, row, 1)
            sigma = self._cell_float(self.w_mm_table, row, 2)
            if epsilon is None or sigma is None:
                continue
            table[element] = (epsilon, sigma)
        return table

    def collect_spec(self) -> Spec:
        task = self.task_combo.currentData()
        cell_text = self.w_cell.text().strip()
        cell = parse_cell_text(cell_text) if cell_text else None
        basis_file = self.w_basis_file.text().strip() or None
        potential_file = self.w_potential_file.text().strip() or None
        # EPS_DEFAULT scales every SCF threshold, so 0 (or a negative number)
        # cannot converge: fall back to CP2K's own 1e-10 **visibly**, instead of
        # quietly turning the typed 0 into 1e-10 with no explanation
        eps_default = _optional_float(self.w_eps_default.text(), self.w_eps_default)
        if eps_default is not None and eps_default <= 0:
            self.w_eps_default.setStyleSheet("border:1px solid #c0392b;")
            self.w_eps_default.setToolTip(
                "EPS_DEFAULT 必须大于 0：它按比例缩放所有 SCF 阈值，"
                "填 0 等于要求 SCF 收敛到 0。这里按 CP2K 默认的 1e-10 写。")
            eps_default = None

        spec = Spec(
            task=task,
            method=self.w_method.currentData() or "QUICKSTEP",
            mm_route=self.w_mm_route.currentData() or "AMBER",
            mm_parm_file=self.w_mm_parm.text().strip() or None,
            # when the topology file is the parameter file, leave it unset: the
            # build inherits it (recipes.conn_file_for), exactly like the CLI
            mm_conn_file=(None if self.w_mm_same_file.isChecked()
                          else self.w_mm_conn.text().strip() or None),
            # the coordinate file stays in charge when one is given: the build
            # only falls back to &COORD when this is empty.  It is *cleared*
            # when a CP2K input/restart is loaded (that file is not a coordinate
            # file - CP2K's own restart inlines &COORD and drops COORD_FILE_NAME)
            mm_coord_file=self.w_mm_coord.text().strip() or None,
            mm_coord_format=self.w_mm_coord_format.currentData() or None,
            mm_path_style=self.w_mm_path_style.currentData() or "name",
            # "auto" compares each file against the directory the input goes to
            output_dir=str(self.output_directory()),
            mm_molecules=self._mm_molecules(),
            mm_ff_file=self.w_mm_ff.text().strip() or None,
            mm_charges=self._mm_charges(),
            mm_lj=self._mm_lj(),
            mm_element_lj=self._mm_element_lj(),
            mm_vdw_scale14=_optional_float(self.w_mm_vdw14.text(),
                                           self.w_mm_vdw14),
            mm_ei_scale14=_optional_float(self.w_mm_ei14.text(),
                                          self.w_mm_ei14),
            mm_ewald=self.w_mm_ewald.currentData(),
            mm_ewald_gmax=self.w_mm_ewald_gmax.value() or None,
            mm_ewald_alpha=_optional_float(self.w_mm_ewald_alpha.text(),
                                           self.w_mm_ewald_alpha),
            mm_o_spline=self.w_mm_o_spline.value() or None,
            mm_rcut=_optional_float(self.w_mm_rcut.text(), self.w_mm_rcut),
            mm_verlet_skin=_optional_float(self.w_mm_verlet_skin.text(),
                                           self.w_mm_verlet_skin),
            mm_spline_emax=_optional_float(self.w_mm_spline_emax.text(),
                                           self.w_mm_spline_emax),
            mm_shift_cutoff=tristate_value(self.w_mm_shift_cutoff),
            mm_multiple_potential=(True if self.w_mm_multiple.isChecked()
                                   else None),
            mm_ignore_missing_params=(True if self.w_mm_ignore_missing.isChecked()
                                      else None),
            mm_stress_tensor=tristate_value(self.w_mm_stress),
            project=self.w_project.text().strip() or None,
            structure=self.structure,
            charge=self.w_charge.value(),
            multiplicity=self.w_multiplicity.value(),
            uks=True if self.w_force_uks.isChecked() else None,
            periodic=self.w_periodic.currentData(),
            vacuum=self.w_vacuum.value() or None,
            cell=cell,
            functional=self.w_functional.currentText().strip().upper(),
            dispersion=self.w_dispersion.currentData(),
            cutoff=None if self.w_cutoff_auto.isChecked() else self.w_cutoff.value(),
            rel_cutoff=self.w_rel_cutoff.value(),
            ngrids=self.w_ngrids.value() or None,
            qs_method=self.w_qs_method.currentText(),
            eps_default=(eps_default if eps_default is not None else 1e-10),
            extrapolation=self.w_extrapolation.currentData(),
            eps_scf=_optional_float(self.w_eps_scf.text(), self.w_eps_scf),
            max_scf=self.w_max_scf.value(),
            outer_max_scf=(self.w_outer_max_scf.value()
                           if self.w_outer_max_scf.value() >= 0 else None),
            outer_eps_scf=_optional_float(self.w_outer_eps_scf.text(),
                                          self.w_outer_eps_scf),
            added_mos=self.w_added_mos.value() or None,
            scf_guess=self.w_scf_guess.currentText(),
            wfn_restart_file=self.w_wfn_file.text().strip() or None,
            wfn_restart_print=self.w_wfn_print.isChecked(),
            wfn_restart_every=self.w_wfn_every.value(),
            wfn_restart_level=self.w_wfn_level.currentData(),
            wfn_restart_add_last=self.w_wfn_add_last.isChecked(),
            wfn_backup_copies=_optional_step(self.w_wfn_backups),
            ext_restart_file=self.w_ext_restart.text().strip() or None,
            ext_restart_default=self.w_restart_default.isChecked(),
            ext_restart_take=(tuple(self.w_restart_take.currentData())
                              if self.w_restart_take.currentData() else None),
            motion_restart_print=self.w_motion_restart.isChecked(),
            restart_every=self.w_restart_every.value(),
            restart_add_last=self.w_restart_add_last.isChecked(),
            restart_backups=_optional_step(self.w_restart_backups),
            smearing=True if self.w_smearing.isChecked() else None,
            electronic_temperature=self.w_electronic_temperature.value(),
            smear_method=self.w_smear_method.currentText(),
            ot_minimizer=self.w_ot_minimizer.currentText(),
            ot_preconditioner=self.w_ot_preconditioner.currentText(),
            ot_algorithm=self.w_ot_algorithm.currentText(),
            ot_linesearch=self.w_ot_linesearch.currentText(),
            ot_stepsize=_optional_float(self.w_ot_stepsize.text(),
                                        self.w_ot_stepsize),
            ot_energy_gap=_optional_float(self.w_ot_energy_gap.text(),
                                          self.w_ot_energy_gap),
            ot_rotation=tristate_value(self.w_ot_rotation),
            ot_safe_diis=tristate_value(self.w_ot_safe_diis),
            outer_optimizer=self.w_outer_optimizer.currentData(),
            diag_algorithm=self.w_diag_algorithm.currentText(),
            level_shift=_optional_float(self.w_level_shift.text(),
                                        self.w_level_shift),
            mixing_method=self.w_mixing_method.currentText(),
            ignore_convergence_failure=self.w_ignore_convergence.isChecked(),
            mixing_alpha=self.w_mixing_alpha.value(),
            mixing_nbuffer=self.w_mixing_nbuffer.value(),
            mixing_beta=_optional_float(self.w_mixing_beta.text(),
                                        self.w_mixing_beta),
            scaled_coords=self.w_scaled_coords.isChecked(),
            h_to_d=self.w_h_to_d.isChecked(),
            poisson_solver=self.w_poisson.currentData(),
            basis=self.w_basis.text().strip() or None,
            basis_file=basis_file,
            potential_file=potential_file,
            basis_target=self.w_basis_target.currentData() or "auto",
            scf=self.w_scf.currentData() or "auto",
            print_level=self.w_print_level.currentText(),
            walltime=self.w_walltime.value() or None,
            print_molden=self.w_molden.isChecked(),
            molden_file=self.w_molden_file.text().strip() or None,
            molden_nlumo=self.w_molden_nlumo.value(),
            molden_unit=self.w_molden_unit.currentText(),
            molden_gto_kind=self.w_molden_gto_kind.currentText(),
            molden_write_pseudo=self.w_molden_pseudo.isChecked(),
            molden_write_cell=self.w_molden_cell.isChecked(),
            molden_vib=self.w_molden_vib.isChecked(),
            print_mo_cubes=self.w_mo_cubes.isChecked(),
            mo_cubes_nhomo=self.w_mo_cubes_nhomo.value(),
            mo_cubes_nlumo=self.w_mo_cubes_nlumo.value(),
            mo_cubes_stride=self.w_mo_cubes_stride.text().strip() or None,
            print_density_cube=self.w_density_cube.isChecked(),
            density_cube_stride=self.w_density_cube_stride.text().strip() or None,
        )
        spec.fixed_atoms = self.w_fixed_atoms.text().strip() or None
        spec.fixed_atoms_components = self.w_fixed_atoms_components.currentText()
        # ---- metadynamics: the MD page owns this block ---------------------- #
        # It is part of that page now, so there is nothing to hand over: the
        # Spec just asks the block (which is None while the box is unticked, and
        # build() drops the sections for a non-MD task).
        if getattr(self, "metadyn_panel", None) is not None:
            spec.metadyn = self.metadyn_panel.metadyn()

        # ---- &KPOINTS: the same controls for every Quickstep task ---------- #
        # The group box is checkable: unchecked means "no &KPOINTS section at
        # all", which is CP2K's own default (a single Gamma point) and keeps the
        # generated file identical to what the command line produces without
        # any k-point flag.
        if self.kpoint_box.isChecked():
            scheme = self.w_kpoint_scheme.currentData()
            spec.kpoint_points = self._kpoint_points()
            spec.kpoint_units = self.w_kpoint_units.currentText()
            spec.kpoint_gamma_centered = self.w_kpoint_gamma_centered.isChecked()
            spec.kpoint_full_grid = self.w_kpoint_full_grid.isChecked()
            spec.kpoint_symmetry = self.w_kpoint_symmetry.isChecked()
            spec.kpoint_verbose = self.w_kpoint_verbose.isChecked()
            spec.kpoint_wavefunctions = self.w_kpoint_wavefunctions.currentText()
            if scheme == "custom":
                spec.kpoint_scheme = (
                    self.w_kpoint_scheme_text.text().strip() or None)
            elif scheme in ("GAMMA", "GENERAL"):
                spec.kpoint_scheme = scheme
            elif scheme in ("MONKHORST-PACK", "MACDONALD"):
                grid = self.w_kgrid.text().replace(",", " ").split()
                # never write the scheme without its three integers: CP2K needs
                # them, and the box is empty whenever 「按间距自动」 is on (or was
                # just cleared) - then the spacing box estimates the mesh
                spec.kpoint_scheme = (" ".join([scheme] + grid) if grid else None)
            else:
                spec.kpoint_scheme = None
            if not self.w_kgrid_auto.isChecked():
                grid = [int(float(v)) for v in
                        self.w_kgrid.text().replace(",", " ").split()]
                if len(grid) == 3:
                    spec.kpoint_grid = tuple(grid)
                    spec.kpoint_spacing = None
                else:
                    # no usable mesh in the box: estimate it from the spacing
                    # instead of dropping the whole &KPOINTS section (which is
                    # what an empty grid plus "no spacing" would do)
                    spec.kpoint_grid = None
                    spec.kpoint_spacing = self.w_kpoint_spacing.value()
            else:
                spec.kpoint_grid = None
                spec.kpoint_spacing = self.w_kpoint_spacing.value()

        if task == "energy":
            spec.print_forces = self.w_print_forces.isChecked()
            spec.print_mulliken = self.w_print_mulliken.isChecked()
            spec.print_dipole = self.w_print_dipole.isChecked()
        elif task == "geo_opt":
            spec.optimizer = self.w_geo_optimizer.currentText()
            spec.max_iter = self.w_geo_max_iter.value()
        elif task == "cell_opt":
            spec.optimizer = self.w_cell_optimizer.currentText()
            spec.max_iter = self.w_cell_max_iter.value()
            spec.external_pressure = self.w_pressure.value()
            spec.keep_angles = self.w_keep_angles.isChecked()
        elif task == "md":
            self._apply_md_spec(spec)
        elif task == "freq":
            spec.intensities = self.w_intensities.isChecked()
            spec.thermochemistry = self.w_thermochemistry.isChecked()
        elif task == "band":
            spec.lattice = self.w_lattice.currentData()
            spec.kpath = self.w_kpath.text().strip() or None
            spec.npoints = self.w_npoints.value()
        elif task == "pdos":
            spec.pdos_nlumo = self.w_pdos_nlumo.value() or None
            ldos = [int(float(v)) for v in self.w_pdos_ldos.text().replace(",", " ").split()]
            spec.pdos_ldos = [ldos] if ldos else []
            spec.pdos_broaden = self.w_pdos_broaden.value() or None
            spec.pdos_broaden_type = self.w_pdos_broaden_type.currentText()
        elif task == "neb":
            spec.band_type = self.w_band_type.currentText()
            spec.k_spring = self.w_k_spring.value()
            spec.replica_files = [self.w_replicas.item(i).text()
                                  for i in range(self.w_replicas.count())]
            spec.replicas = len(spec.replica_files)
        return spec

    def _apply_md_spec(self, spec: Spec) -> None:
        """The MD page's values, whatever task page is currently shown.

        ``collect_spec`` uses it for the MD task, and the temperature program uses
        it too: a program is MD by definition, and the user may well be looking at
        another task page when they press the button - the MD page's settings are
        what the stage inputs have to be built from.
        """
        spec.ensemble = self.w_ensemble.currentText()
        spec.thermostat = self.w_thermostat.currentText()
        spec.temperature = self.w_temperature.value()
        spec.timestep = self.w_timestep.value()
        spec.steps = self.w_steps.value()
        spec.pressure = self.w_md_pressure.value()
        spec.seed = self.w_seed.value() or None
        spec.thermostat_timecon = self.w_thermostat_timecon.value()
        spec.barostat_timecon = self.w_barostat_timecon.value()
        spec.trajectory_stride = self.w_trajectory_stride.value()
        spec.thermostat_region = self.w_thermostat_region.currentData()
        spec.md_temp_kind_print = self.w_md_temp_kind.isChecked()
        spec.md_temp_kind_every = self.w_md_temp_kind_every.value()
        spec.md_energy_print = self.w_md_energy.isChecked()
        spec.md_energy_every = self.w_md_energy_every.value()
        spec.md_energy_file = self.w_md_energy_file.text().strip()
        spec.thermostat_temp_print = self.w_thermostat_temp.isChecked()
        spec.thermostat_temp_every = self.w_thermostat_temp_every.value()
        spec.temperature_annealing = (
            self.w_temperature_annealing.value() or None)
        spec.annealing = self.w_annealing.value() or None
        spec.annealing_cell = self.w_annealing_cell.value() or None
        spec.langevin_gamma = self.w_langevin_gamma.value()
        spec.restart_temperature_annealing = \
            self.w_restart_temperature_annealing.isChecked()

    def _set_preview(self, text: str) -> None:
        """Replace the preview text without losing where the user was reading."""
        bar = self.preview.verticalScrollBar()
        offset = bar.value()
        self.preview.setPlainText(text)
        if offset:
            bar.setValue(min(offset, bar.maximum()))

    # -- the preview's second page (the PLUMED route) ---------------------- #
    def _preview_page_changed(self, _index: int) -> None:
        self.preview_stack.setCurrentIndex(max(0, self.preview_page.currentData()
                                               or 0))

    def _active_preview(self) -> QPlainTextEdit:
        """The page the user is looking at (save/copy act on that one)."""
        if (self.preview_page.isVisible()
                and self.preview_page.currentData() == 1):
            return self.plumed_preview
        return self.preview

    def _set_plumed_preview(self, text: str) -> None:
        """Fill the plumed.dat page; hide the page switch when there is none."""
        bar = self.plumed_preview.verticalScrollBar()
        offset = bar.value()
        self.plumed_preview.setPlainText(text)
        if offset:
            bar.setValue(min(offset, bar.maximum()))
        self.preview_page.setVisible(bool(text))
        if not text:
            self.preview_page.setCurrentIndex(0)
            self.preview_stack.setCurrentIndex(0)

    def refresh(self) -> None:
        if self.index is None:
            return
        if hasattr(self, "preview_hint"):
            # a heartbeat, so "does the preview follow my edits?" is answerable at
            # a glance instead of by comparing two long files by eye
            from datetime import datetime

            self.preview_hint.setText(
                f"已刷新 {datetime.now().strftime('%H:%M:%S')}"
                "（改动左侧控件会自动重算）")
            self.preview_hint.setStyleSheet("color:#1e8449;")
        # which rows of the classical page apply depends on what is *in* its
        # tables (a filled &MOL_SET replaces the top-level topology file), so the
        # page is re-laid-out on every refresh - it is cheap and it keeps the
        # form honest after any edit, programmatic or not
        self._mm_visibility()
        self._update_kpoint_hint()      # the k-point table feeds this too
        if self.structure_error:
            # clear the text so "保存" can never write a stale file that no
            # longer matches what the preview shows
            self.text = ""
            self._set_preview(f"# 结构文件读取失败\n# {self.structure_error}")
            self.summary.setText("结构文件读取失败")
            self._fill_diagnostics([])
            self.kind_table.setVisible(False)
            return
        if self.structure is None and self.task_combo.currentData() != "neb":
            # a file-based force field reads its atoms *and* its box from files,
            # so no structure file is needed - only the &CELL has to be typed in
            probe = self.collect_spec_safe()
            ready = (is_classical(probe) and mm_route(probe).conn_file
                     and probe.cell is not None)
            if not ready and is_classical(probe) and mm_route(probe).conn_file:
                # the box may already be sitting in the coordinate file (AMBER's
                # inpcrd/restart ends with it, PDB has CRYST1): CP2K would read it
                # when the input has no &CELL at all, so filling &CELL with it is
                # the same thing - and it beats asking for numbers that are in the
                # file the user just named
                box = self._cell_from_coordinate_file()
                if box is not None:
                    self._set_cell_from_file(box)
                    self.schedule()
                    return
            if not ready:
                self.text = ""
                restart_path = self._ext_restart_path()
                _, restart_has = self._ext_restart_summary()
                if is_classical(probe) and mm_route(probe).conn_file:
                    # the coordinate file may simply have no box (an XYZ never
                    # does): say so instead of leaving the user guessing why the
                    # file they just named did not fill anything in
                    named = self.w_mm_coord.text().strip()
                    sibling = self._sibling_cell(named) if named else None
                    self._set_preview(
                        "# 还差一个晶胞：力场路线下原子和坐标来自 COORD_FILE_NAME，\n"
                        "# 但 CP2K 只在输入里**没有** &CELL 时才会去读坐标文件里的盒子；\n"
                        "# 工具总会写显式的 &CELL，所以请在「4. 体系」的「晶胞覆盖」里填 "
                        "a,b,c（或 a,b,c,α,β,γ）。\n"
                        + (f"#\n# 已经看过 {Path(named).name} 了：它里面没有盒子"
                           "（XYZ 没有，PDB 要有 CRYST1，inpcrd/g96 才有 box 行）。\n"
                           if named else "")
                        + (f"# 同名的 {sibling[0]} 里有晶胞：{sibling[1]}"
                           "——数字就在上面，填进「晶胞覆盖」即可。\n"
                           if sibling else "")
                        + ("#\n# 接力文件里有晶胞的话，点「从上次续算」右边的"
                           "「读它的坐标」就能一起读进来。"
                           if restart_path is not None and "cell" in restart_has else ""))
                    self.summary.setText("等待晶胞")
                elif restart_path is not None and "pos" in restart_has:
                    # the classic confusion: "I gave it a restart file, why is
                    # there no preview?" - &EXT_RESTART only replaces the
                    # coordinates *at run time*, the input still needs the atoms
                    self._set_preview(
                        f"# 已指定接力文件 {restart_path.name}，但输入里**仍然要有原子**：\n"
                        "# `&EXT_RESTART` 只在 CP2K **运行时**用文件里的坐标覆盖，"
                        "输入自己得定义体系。\n"
                        "#\n"
                        f"# 这个文件里有坐标"
                        + ("和晶胞" if "cell" in restart_has else "")
                        + "，点「从上次续算」右边的\n"
                        "# 「读它的坐标」就能把体系读进来——或者自己选一个结构文件；\n"
                        "# FIST 路线也可以用「坐标文件」+「晶胞」。")
                    self.summary.setText("等待结构文件（可从接力文件读）")
                else:
                    self._set_preview("# 请先选择结构文件")
                    self.summary.setText("等待结构文件")
                self._fill_diagnostics([])
                self.kind_table.setVisible(False)
                return

        try:
            spec = self.collect_spec()
            result = build(spec, self.index)
        except Exception as exc:  # noqa: BLE001 - surface anything to the user
            self.text = ""
            self._set_preview(f"# 生成失败\n# {exc}")
            self.summary.setText(f"<span style='color:#c0392b;'>生成失败：{exc}</span>")
            self._fill_diagnostics([])
            self.kind_table.setVisible(False)
            return

        # say where an auto-filled cell came from (the note goes into the file's
        # own header, so it travels with the input)
        provenance = getattr(self, "_cell_from_file", None)
        if provenance and self.w_mm_coord.text().strip() \
                and self.w_cell.text().strip() == provenance:
            result.notes = list(result.notes) + [
                f"cell          : 晶胞取自坐标文件 {Path(self.w_mm_coord.text().strip()).name} "
                f"里的盒子（{provenance}）——CP2K 只在输入里没有 &CELL 时才自己去读它，"
                "工具把它写成显式的 &CELL；不是这个盒子就改「4. 体系」的「晶胞覆盖」"]
        self.text = render_document(
            result.sections,
            title=f"CP2K input generated by cp2kbuild {__version__}",
            notes=result.notes)
        self._set_preview(self.text)
        self._fill_kinds(result)
        diagnostics = Validator(self.db, self.index).run(result.sections)
        # The generator's own warnings (BuildResult.warnings) are problems too, so
        # they go into the *same* table: before this they only lived in the
        # summary's tooltip, which made "1 项生成期问题" look like an empty report.
        # What the generator already spoke about is not repeated (see
        # `without_covered`): one problem, one row, one level.
        diagnostics = without_covered(list(diagnostics), result) \
            + build_warnings(result)
        self._fill_diagnostics(diagnostics)
        self._show_summary(diagnostics, result)
        # the PLUMED route's other output: plumed.dat, from the very same CV/hill
        # settings (the block on the MD page explains what is converted and what
        # is left as a comment)
        if hasattr(self, "metadyn_panel"):
            self._set_plumed_preview(self.metadyn_panel.plumed_draft(spec))
        # the restart hints depend on the project name and the task, so refresh
        # them here (this must not call schedule(), it would loop)
        self._restart_changed()
        self._poisson_changed()
        self._apply_couplings()      # grey out whatever cannot take effect
        self.status.emit(f"已生成 {len(self.text.splitlines())} 行")

    def _fill_kinds(self, result: BuildResult) -> None:
        assignments = list(result.setup.assignments.values())
        if not assignments:
            self.kind_table.setVisible(False)
            return
        self.kind_table.setRowCount(len(assignments))
        for row, assignment in enumerate(assignments):
            values = [
                assignment.element,
                f"{assignment.basis}  ({assignment.basis_file})",
                f"q{assignment.basis_valence}" if assignment.basis_valence else "?",
                f"{assignment.potential}  ({assignment.potential_file})",
            ]
            for column, value in enumerate(values):
                self.kind_table.setItem(row, column, QTableWidgetItem(value))
        self.kind_table.setVisible(True)

    def _fill_diagnostics(self, diagnostics) -> None:
        self.diagnostics.setRowCount(len(diagnostics))
        for row, item in enumerate(diagnostics):
            level = QTableWidgetItem(LEVEL_LABELS[item.level])
            level.setForeground(LEVEL_COLORS[item.level])
            code = QTableWidgetItem(item.code)
            message = QTableWidgetItem(item.message)
            tooltip_parts = [item.message]
            if item.path:
                tooltip_parts.append(f"位置：{item.path}")
            if item.hint:
                tooltip_parts.append(f"建议：{item.hint}")
            if item.url:
                tooltip_parts.append(item.url)
            message.setToolTip("\n".join(tooltip_parts))
            self.diagnostics.setItem(row, 0, level)
            self.diagnostics.setItem(row, 1, code)
            self.diagnostics.setItem(row, 2, message)
            if item.url:
                font = message.font()
                font.setUnderline(True)
                message.setFont(font)
                message.setForeground(LEVEL_COLORS[item.level])
        self.diagnostics.resizeRowsToContents()

    def _show_summary(self, diagnostics, result: BuildResult) -> None:
        errors = sum(1 for d in diagnostics if d.level == "error")
        warnings = sum(1 for d in diagnostics if d.level == "warning")
        color = "#c0392b" if errors else ("#b9770e" if warnings else "#1e8449")
        text = (f"<span style='color:{color};font-weight:bold;'>"
                f"{errors} 错误 / {warnings} 警告</span>")
        # The generator's own findings are rows in the *same* table (codes
        # build-error / build-warning), so they are counted above - counting them a
        # second time as "生成期问题" made the headline read "0 错误 / 0 警告" above a
        # table that plainly showed a warning.  What is still worth saying is *where*
        # those rows came from.
        from_generator = sum(1 for d in diagnostics
                             if d.code in (BUILD_ERROR_CODE, BUILD_WARNING_CODE))
        problems = list(getattr(result, "errors", [])) \
            + list(getattr(result, "warnings", []))
        if from_generator:
            text += (f" &nbsp;<span style='color:#7d6608;'>· "
                     f"其中 {from_generator} 项来自生成器</span>")
        if problems:
            self.summary.setToolTip("\n".join(problems))
        else:
            self.summary.setToolTip("")
        if not diagnostics:
            text += " &nbsp;<span style='color:#1e8449;'>— 没有问题</span>"
        self.summary.setText(text)

    # -- output ------------------------------------------------------------ #
    def save(self) -> None:
        """Ask where to save **every** time; only the starting point is inherited.

        This used to skip the dialog once 「输出文件」 had a value, so the second
        click silently overwrote that file (the field was filled by the first
        save).  Now every click raises the dialog, pre-filled with the file that
        was saved last time - so Enter still overwrites *deliberately*, while a
        new name/location is one edit away.  The dialog also remembers the
        directory under the shared ``last_dir``, like every other browse dialog.

        Which *file* it is follows the preview page: the CP2K input, or the
        ``plumed.dat`` of the PLUMED route.
        """
        page = self._active_preview()
        if page is self.plumed_preview:
            self._save_plumed()
            return
        if not self.text:
            QMessageBox.information(self, "还没有内容", "请先生成一个输入文件。")
            return
        typed = self.w_out.text().strip()
        start = typed or str(Path(self._start_directory("last_output_dir"))
                             / f"{self.project_name()}.inp")
        name, _ = QFileDialog.getSaveFileName(self, "保存输入文件", start,
                                              "CP2K 输入 (*.inp);;所有文件 (*)")
        if not name:
            return
        if name != self.w_out.text().strip():
            # Where the file goes decides how it is written: with 「文件路径写法
            # =自动」 the classical-file names are shortened relative to the output
            # directory.  Writing the text generated for the *old* directory would
            # save a file full of absolute Windows paths while the preview shows
            # bare names - so regenerate for the chosen target first.
            self.w_out.setText(name)
            if self.index is not None:
                self.refresh()
        if not self.text:
            QMessageBox.warning(self, "保存失败",
                                "换到新目录后重新生成失败了，什么都没写。")
            return
        try:
            # LF, not the platform default: this file is going to a Linux cluster
            write_text_lf(name, self.text)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._remember_directory("last_output_dir", name)
        self.status.emit(f"已保存到 {name}")

    def _save_plumed(self) -> None:
        """Save the ``plumed.dat`` page (the PLUMED route's second file)."""
        text = self.plumed_preview.toPlainText()
        if not text:
            QMessageBox.information(self, "还没有内容", "PLUMED 路线才会生成 plumed.dat。")
            return
        typed = self.w_out.text().strip()
        base = Path(typed).parent if typed else self._start_directory("last_output_dir")
        start = str(Path(base) / "plumed.dat")
        name, _ = QFileDialog.getSaveFileName(
            self, "保存 plumed.dat", start,
            "PLUMED 输入 (*.dat);;所有文件 (*)")
        if not name:
            return
        try:
            write_text_lf(name, text)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._remember_directory("last_output_dir", name)
        self.status.emit(f"已保存到 {name}（记得把它和输入文件放在一起）")

    def copy(self) -> None:
        text = self._active_preview().toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.status.emit("已复制到剪贴板")

    def set_backend(self, index: DataIndex, db: ManualDB | None) -> None:
        self.index = index
        self.db = db
        self.schedule()

    def load_from_path(self, path: str) -> None:
        self.structure_edit.setText(path)
        self.load_structure()


# --------------------------------------------------------------------------- #
# 元动力学（metadynamics）
# --------------------------------------------------------------------------- #
#: what the CV dialog shows for each atom field, so the hint matches the kind
CV_ATOM_HINTS = {
    "distance": "两个原子，如 1 2",
    "angle": "三个原子（第二行是顶点），如 1 2 3",
    "torsion": "四个原子，如 1 2 3 4",
    "coordination": "也可只写元素名（见下面的 KINDS_FROM/KINDS_TO）",
    "combine": "组合 CV 本身不直接要原子：原子写在嵌套的子 CV 里",
    "path": "参与比较的原子；留空表示全部原子",
    "rmsd": "参与比较的原子；留空表示全部原子",
}


def _atom_text(atoms) -> str:
    return " ".join(str(atom) for atom in atoms)


def _parse_int_list(text: str) -> tuple[int, ...]:
    """``"1 2 3"`` or ``"1,2,3"`` -> ``(1, 2, 3)`` (empty/garbage is ignored)."""
    out: list[int] = []
    for token in str(text).replace(",", " ").split():
        try:
            out.append(int(float(token)))
        except ValueError:
            continue
    return tuple(out)


class CvDialog(QDialog):
    """Add or edit one collective variable (a ``&COLVAR`` block)."""

    def __init__(self, cv: "Cv | None" = None, parent=None,
                 allow_combine: bool = True):
        super().__init__(parent)
        from .metadyn import CV_KINDS

        self.cv = cv
        self._children: list[Cv] = list(cv.children) if cv is not None else []
        self._rows: dict[str, tuple[QWidget, QWidget]] = {}
        self.setWindowTitle("集体变量" if cv is None else "编辑集体变量")
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)
        top = QFormLayout()
        outer.addLayout(top)

        #: the *rows* that carry a badge.  Every row of a CV shares one help topic,
        #: so attaching the badge per row showed the same "?" several times over;
        #: one badge on the type combo is enough (its text covers the fields too).
        keep_rows = {"kind"}

        def row(name: str, key: str | None, label: str, widget: QWidget) -> QWidget:
            holder = label_with_help(label, key if name in keep_rows else None)
            top.addRow(holder, widget)
            self._rows[name] = (holder, widget)
            return widget

        self._kinds = [kind for kind in CV_KINDS
                       if allow_combine or kind != "combine"]
        self.w_kind = QComboBox()
        for kind in self._kinds:
            self.w_kind.addItem(CV_LABELS[kind], kind)
        self.w_kind.currentIndexChanged.connect(self._update_rows)
        row("kind", "meta_cv", "类型", self.w_kind)

        self.w_atoms = QLineEdit()
        row("atoms", "meta_cv", "原子序号（1 起算）", self.w_atoms)
        self.w_atoms_hint = hint_label()
        outer.addWidget(self.w_atoms_hint)

        self.w_kinds_from = QLineEdit()
        self.w_kinds_from.setPlaceholderText("中心原子的元素名，如 O")
        row("kinds_from", "meta_cv", "KINDS_FROM", self.w_kinds_from)
        self.w_kinds_to = QLineEdit()
        self.w_kinds_to.setPlaceholderText("被数的原子，如 H")
        row("kinds_to", "meta_cv", "KINDS_TO", self.w_kinds_to)
        self.w_atoms_from = QLineEdit()
        self.w_atoms_from.setPlaceholderText("或直接给原子序号，如 1 2")
        row("atoms_from", "meta_cv", "ATOMS_FROM", self.w_atoms_from)
        self.w_atoms_to = QLineEdit()
        self.w_atoms_to.setPlaceholderText("或直接给原子序号，如 3 4 5")
        row("atoms_to", "meta_cv", "ATOMS_TO", self.w_atoms_to)
        self.w_r0 = QLineEdit()
        self.w_r0.setPlaceholderText("切换半径，Å（留空 = CP2K 默认 3.0 bohr ≈ 1.59 Å）")
        row("r0", "meta_cv", "R0", self.w_r0)
        self.w_nn = QLineEdit()
        self.w_nn.setPlaceholderText("分子指数（留空 = CP2K 6 / PLUMED 也是 6）")
        row("nn", "meta_cv", "NN", self.w_nn)
        self.w_nd = QLineEdit()
        self.w_nd.setPlaceholderText("分母指数（留空 = 12；PLUMED 里叫 MM）")
        row("nd", "meta_cv", "ND", self.w_nd)

        self.w_function = QLineEdit()
        self.w_function.setPlaceholderText("如 (CV1^P+CV2^P)^(1./P) 或 CV1-CV2")
        row("function", "meta_cv", "FUNCTION", self.w_function)
        self.w_variables = QLineEdit()
        self.w_variables.setPlaceholderText("按顺序写，如 CV1 CV2")
        row("variables", "meta_cv", "VARIABLES", self.w_variables)
        self.w_parameters = QLineEdit()
        self.w_parameters.setPlaceholderText("表达式里的常数名，如 P")
        row("parameters", "meta_cv", "PARAMETERS", self.w_parameters)
        self.w_values = QLineEdit()
        self.w_values.setPlaceholderText("与 PARAMETERS 一一对应，如 4.0")
        row("values", "meta_cv", "VALUES", self.w_values)

        self.w_frames = QListWidget()
        self.w_frames.setMaximumHeight(96)
        self.w_frames.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.w_frames.setEditTriggers(QAbstractItemView.DoubleClicked
                                      | QAbstractItemView.EditKeyPressed)
        row("frames", "meta_cv", "&FRAME 参考构型", self.w_frames)
        frame_buttons = QHBoxLayout()
        add_frame = QPushButton("添加文件…")
        add_frame.clicked.connect(self._add_frames)
        del_frame = QPushButton("删除选中")
        del_frame.clicked.connect(
            lambda: _remove_rows(self.w_frames))
        frame_buttons.addWidget(add_frame)
        frame_buttons.addWidget(del_frame)
        frame_buttons.addStretch(1)
        outer.addLayout(frame_buttons)

        self.w_align = QCheckBox("先把参考构型叠合到当前构型（ALIGN_FRAMES）")
        check_row(top, None, self.w_align)
        self._rows["align"] = (self.w_align, self.w_align)
        self.w_rmsd = QCheckBox("用 RMSD 而不是距离（RMSD T）")
        check_row(top, None, self.w_rmsd)
        self._rows["rmsd"] = (self.w_rmsd, self.w_rmsd)
        self.w_dist_rmsd = QCheckBox("对原子对距离做 RMSD（DISTANCES_RMSD T）")
        check_row(top, None, self.w_dist_rmsd)
        self._rows["dist_rmsd"] = (self.w_dist_rmsd, self.w_dist_rmsd)
        self.w_subset = QComboBox()
        for label, value in (("全部原子（ALL，默认）", "ALL"),
                             ("只算 ATOMS 列出的那些（LIST）", "LIST"),
                             ("按 WEIGHTS 加权（WEIGHT_LIST，本工具不生成权重表）",
                              "WEIGHT_LIST")):
            self.w_subset.addItem(label, value)
        row("subset", "meta_cv", "SUBSET_TYPE", self.w_subset)
        self.w_lam = QLineEdit()
        self.w_lam.setPlaceholderText("路径积分的宽度参数（留空 = CP2K 默认 5）")
        row("lam", "meta_cv", "LAMBDA", self.w_lam)

        self.w_children = ExcelTable(["子 CV", "定义"], widths=[210, 300],
                                     sortable=False)
        self.w_children.setMinimumHeight(84)
        row("children", "meta_cv", "子 &COLVAR", self.w_children)
        child_buttons = QHBoxLayout()
        add_child = QPushButton("加子 CV…")
        add_child.clicked.connect(self._add_child)
        edit_child = QPushButton("编辑…")
        edit_child.clicked.connect(self._edit_child)
        del_child = QPushButton("删除选中")
        del_child.clicked.connect(lambda: self._remove_child())
        child_buttons.addWidget(add_child)
        child_buttons.addWidget(edit_child)
        child_buttons.addWidget(del_child)
        child_buttons.addStretch(1)
        outer.addLayout(child_buttons)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._fill(cv)
        self._update_rows()

    # -- helpers ---------------------------------------------------------- #
    def _fill(self, cv: "Cv | None") -> None:
        if cv is None:
            return
        index = self.w_kind.findData(cv.kind)
        if index >= 0:
            self.w_kind.setCurrentIndex(index)
        self.w_atoms.setText(_atom_text(cv.atoms))
        self.w_kinds_from.setText(cv.kinds_from)
        self.w_kinds_to.setText(cv.kinds_to)
        self.w_atoms_from.setText(_atom_text(cv.atoms_from))
        self.w_atoms_to.setText(_atom_text(cv.atoms_to))
        self.w_r0.setText("" if cv.r0 is None else f"{cv.r0:g}")
        self.w_nn.setText("" if cv.nn is None else str(cv.nn))
        self.w_nd.setText("" if cv.nd is None else str(cv.nd))
        self.w_function.setText(cv.function)
        self.w_variables.setText(" ".join(cv.variables))
        self.w_parameters.setText(" ".join(cv.parameters))
        self.w_values.setText(" ".join(f"{value:g}" for value in cv.values))
        for frame in cv.frames:
            self.w_frames.addItem(frame)
        self.w_align.setChecked(cv.align_frames)
        self.w_rmsd.setChecked(cv.rmsd)
        self.w_dist_rmsd.setChecked(cv.distances_rmsd)
        index = self.w_subset.findData(cv.subset_type or "ALL")
        self.w_subset.setCurrentIndex(max(index, 0))
        self.w_lam.setText("" if cv.lam is None else f"{cv.lam:g}")
        self._refresh_children()

    def _refresh_children(self) -> None:
        self.w_children.setRowCount(0)
        for child in self._children:
            self.w_children.add_row([child.label, child.summary()])

    def _add_child(self) -> None:
        dialog = CvDialog(None, self, allow_combine=False)
        if dialog.exec() == QDialog.Accepted:
            self._children.append(dialog.value())
            self._refresh_children()

    def _edit_child(self) -> None:
        row = self.w_children.currentRow()
        if not 0 <= row < len(self._children):
            return
        dialog = CvDialog(self._children[row], self, allow_combine=False)
        if dialog.exec() == QDialog.Accepted:
            self._children[row] = dialog.value()
            self._refresh_children()

    def _remove_child(self) -> None:
        row = self.w_children.currentRow()
        if 0 <= row < len(self._children):
            del self._children[row]
            self._refresh_children()

    def _add_frames(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择参考构型（&FRAME）", "",
            "结构文件 (*.xyz *.pdb *.inp *.cif *.poscar *.vasp);;所有文件 (*)")
        for path in files:
            self.w_frames.addItem(path)

    def _update_rows(self) -> None:
        kind = self.w_kind.currentData()
        wanted = {
            "distance": {"atoms"},
            "angle": {"atoms"},
            "torsion": {"atoms"},
            "coordination": {"atoms", "kinds_from", "kinds_to", "atoms_from",
                             "atoms_to", "r0", "nn", "nd"},
            "combine": {"function", "variables", "parameters", "values",
                        "children"},
            "path": {"atoms", "frames", "align", "rmsd", "dist_rmsd", "subset",
                     "lam"},
            "rmsd": {"atoms", "frames", "align", "subset"},
        }.get(kind, set())
        for name, (holder, widget) in self._rows.items():
            # the type combo itself is one of the rows - it must stay visible, or
            # the dialog opens with no way to pick the kind at all
            visible = name == "kind" or name in wanted
            holder.setVisible(visible)
            widget.setVisible(visible)
        self.w_atoms_hint.setVisible("atoms" in wanted)
        needs = CV_ATOM_COUNT.get(kind)
        if needs:
            self.w_atoms_hint.setText(
                f"{kind}：需要 {needs} 个原子序号（{CV_ATOM_HINTS[kind]}）")
        else:
            self.w_atoms_hint.setText(CV_ATOM_HINTS.get(kind, ""))
        self.adjustSize()

    # -- result ----------------------------------------------------------- #
    def value(self) -> "Cv":
        from .metadyn import Cv

        kind = self.w_kind.currentData()
        return Cv(
            kind=kind,
            atoms=_parse_int_list(self.w_atoms.text()),
            kinds_from=self.w_kinds_from.text().strip(),
            kinds_to=self.w_kinds_to.text().strip(),
            atoms_from=_parse_int_list(self.w_atoms_from.text()),
            atoms_to=_parse_int_list(self.w_atoms_to.text()),
            r0=_optional_float(self.w_r0.text(), self.w_r0),
            nn=_optional_int(self.w_nn.text()),
            nd=_optional_int(self.w_nd.text()),
            function=self.w_function.text().strip(),
            variables=tuple(self.w_variables.text().split()),
            parameters=tuple(self.w_parameters.text().split()),
            values=tuple(value for value in
                         (_optional_float(token, self.w_values)
                          for token in self.w_values.text().replace(",", " ").split())
                         if value is not None),
            children=tuple(self._children),
            frames=tuple(self.w_frames.item(i).text()
                         for i in range(self.w_frames.count())),
            rmsd=self.w_rmsd.isChecked(),
            distances_rmsd=self.w_dist_rmsd.isChecked(),
            align_frames=self.w_align.isChecked(),
            lam=_optional_float(self.w_lam.text(), self.w_lam),
            subset_type=(self.w_subset.currentData()
                         if self.w_subset.currentData() != "ALL" else ""),
        )


class MetavarDialog(QDialog):
    """Add or edit one ``&METAVAR`` (which CV the bias acts on, and the wall)."""

    WALLS = (("不加墙（NONE）", "NONE"), ("二次墙 QUADRATIC", "QUADRATIC"),
             ("四次墙 QUARTIC", "QUARTIC"), ("反弹墙 REFLECTIVE", "REFLECTIVE"),
             ("高斯墙 GAUSSIAN", "GAUSSIAN"))

    def __init__(self, metavar: "Metavar | None" = None, ncvs: int = 1, parent=None,
                 kinds: tuple[str, ...] = ()):
        super().__init__(parent)
        from .metadyn import Metavar

        self.setWindowTitle("偏置项（&METAVAR）")
        self.setMinimumWidth(540)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)
        self._rows: dict[str, tuple[QWidget, QWidget]] = {}
        #: the CV kinds, so the dialog can say which unit the numbers are in
        self._kinds = tuple(kinds)

        #: two help topics here (the bias term, and the wall), so at most one badge
        #: each - the other rows share them and would repeat the same "?" several
        #: times over
        keep_rows = {"colvar", "wall_type"}

        def row(name: str, key: str | None, label: str, widget: QWidget) -> QWidget:
            holder = label_with_help(label, key if name in keep_rows else None)
            form.addRow(holder, widget)
            self._rows[name] = (holder, widget)
            return widget

        self.w_colvar = QSpinBox()
        self.w_colvar.setRange(1, max(1, ncvs))
        self.w_colvar.setValue(1)
        row("colvar", "meta_metavar", "COLVAR（第几个 CV）", self.w_colvar)
        self.w_colvar_hint = QLabel("")
        self.w_colvar_hint.setWordWrap(True)
        self.w_colvar_hint.setStyleSheet("color:#5d6d7e;")
        outer.addWidget(self.w_colvar_hint)
        self.w_colvar.valueChanged.connect(self._update_units)

        self.w_scale = QLineEdit()
        row("scale", "meta_metavar", "SCALE（丘陵宽度 σ）", self.w_scale)
        self.w_mass = QLineEdit()
        self.w_mass.setPlaceholderText("扩展拉格朗日才需要")
        row("mass", "meta_metavar", "MASS", self.w_mass)
        self.w_gamma = QLineEdit()
        self.w_gamma.setPlaceholderText("需要 &METADYN LANGEVIN T")
        row("gamma", "meta_metavar", "GAMMA", self.w_gamma)
        self.w_lam = QLineEdit()
        self.w_lam.setPlaceholderText("扩展拉格朗日的耦合强度")
        row("lam", "meta_metavar", "LAMBDA", self.w_lam)

        self.w_wall_type = QComboBox()
        for label, value in self.WALLS:
            self.w_wall_type.addItem(label, value)
        self.w_wall_type.currentIndexChanged.connect(self._update_wall)
        row("wall_type", "meta_wall", "墙 TYPE", self.w_wall_type)
        self.w_position = QLineEdit()
        row("position", "meta_wall", "墙 POSITION", self.w_position)
        self.w_direction = QComboBox()
        self.w_direction.addItem("WALL_PLUS：挡住 CV 变大", "WALL_PLUS")
        self.w_direction.addItem("WALL_MINUS：挡住 CV 变小", "WALL_MINUS")
        row("direction", "meta_wall", "墙 DIRECTION", self.w_direction)
        self.w_k = QLineEdit()
        self.w_k.setPlaceholderText("力常数 K，Hartree / CV单位²")
        row("k", "meta_wall", "墙 K", self.w_k)
        self.w_sigma = QLineEdit()
        row("sigma", "meta_wall", "墙 SIGMA（高斯宽度）", self.w_sigma)
        self.w_ww = QLineEdit()
        self.w_ww.setPlaceholderText("高斯墙的高度 WW，Hartree")
        row("ww", "meta_wall", "墙 WW", self.w_ww)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        if metavar is not None:
            self.w_colvar.setValue(max(1, min(metavar.colvar, max(1, ncvs))))
            self.w_scale.setText("" if metavar.scale is None else f"{metavar.scale:g}")
            self.w_mass.setText("" if metavar.mass is None else f"{metavar.mass:g}")
            self.w_gamma.setText("" if metavar.gamma is None else f"{metavar.gamma:g}")
            self.w_lam.setText("" if metavar.lam is None else f"{metavar.lam:g}")
            wall = metavar.wall
            if wall is not None:
                index = self.w_wall_type.findData(wall.type or "QUADRATIC")
                self.w_wall_type.setCurrentIndex(max(index, 0))
                self.w_position.setText(
                    "" if wall.position is None else f"{wall.position:g}")
                self.w_direction.setCurrentIndex(
                    max(self.w_direction.findData(wall.direction), 0))
                self.w_k.setText("" if wall.k is None else f"{wall.k:g}")
                self.w_sigma.setText("" if wall.sigma is None else f"{wall.sigma:g}")
                self.w_ww.setText("" if wall.ww is None else f"{wall.ww:g}")
        self._update_units()
        self._update_wall()

    def _update_units(self) -> None:
        """Say which unit SCALE/POSITION/墙 SIGMA are in for the selected CV.

        CP2K declares all three as ``internal_cp2k``, so the number is bohr for a
        length CV (the generated file therefore carries ``[angstrom]`` and asks for
        angstroms here), radians for an angle and dimensionless for a coordination
        number.  Getting this wrong is silent, so the dialog states it outright.
        """
        from .metadyn import CV_LABELS, is_length

        index = self.w_colvar.value() - 1
        kind = self._kinds[index] if 0 <= index < len(self._kinds) else ""
        if is_length(kind):
            unit, where = "Å", "长度类 CV，文件里写成 [angstrom]，CP2K 自己换算"
        elif kind in ("angle", "torsion"):
            unit = "rad"
            where = "角度类 CV 用弧度（CP2K 内部单位就是 rad）"
        elif kind == "coordination":
            unit = ""
            where = "配位数是无量纲的纯数，没有单位"
        elif kind == "combine":
            unit = ""
            where = "组合 CV 的单位取决于它的表达式，请自己确认"
        else:
            unit = ""
            where = "这个 CV 的单位请自己确认"
        label = CV_LABELS.get(kind, kind or "？")
        total = len(self._kinds)
        text = (f"CV 表里一共 {total} 行，COLVAR 就是行号（1 起算）。"
                f"当前第 {index + 1} 行是「{label}」：{where}。")
        if unit:
            text += f"下面几栏都按 {unit} 填。"
        self.w_colvar_hint.setText(text)
        suffix = f"（{unit}）" if unit else ""
        lead = "丘陵宽度 σ，长度类用 Å" if is_length(kind) else "丘陵宽度 σ"
        self.w_scale.setPlaceholderText(f"{lead}{suffix}；留空 = 用面板上的默认值")
        self.w_position.setPlaceholderText(f"墙所在的 CV 值{suffix}，如 2.4")
        self.w_sigma.setPlaceholderText(f"高斯墙的宽度 σ{suffix}")

    def _update_wall(self) -> None:
        """Each wall type accepts different keywords (manual: &WALL/&<TYPE>)."""
        wall_type = self.w_wall_type.currentData()
        shows = {
            "NONE": set(),
            "REFLECTIVE": {"position", "direction"},
            "QUADRATIC": {"position", "direction", "k"},
            "QUARTIC": {"position", "direction", "k"},
            "GAUSSIAN": {"position", "sigma", "ww"},
        }.get(wall_type, set())
        for name in ("position", "direction", "k", "sigma", "ww"):
            holder, widget = self._rows[name]
            visible = name in shows
            holder.setVisible(visible)
            widget.setVisible(visible)
        self.adjustSize()

    def value(self) -> "Metavar":
        from .metadyn import Metavar, Wall

        wall = None
        wall_type = self.w_wall_type.currentData()
        if wall_type != "NONE":
            wall = Wall(
                position=_optional_float(self.w_position.text(), self.w_position),
                type=wall_type,
                direction=self.w_direction.currentData() or "WALL_PLUS",
                k=_optional_float(self.w_k.text(), self.w_k),
                sigma=_optional_float(self.w_sigma.text(), self.w_sigma),
                ww=_optional_float(self.w_ww.text(), self.w_ww))
        return Metavar(colvar=self.w_colvar.value(),
                       scale=_optional_float(self.w_scale.text(), self.w_scale),
                       mass=_optional_float(self.w_mass.text(), self.w_mass),
                       gamma=_optional_float(self.w_gamma.text(), self.w_gamma),
                       lam=_optional_float(self.w_lam.text(), self.w_lam),
                       wall=wall)


class PlumedCvDialog(QDialog):
    """Add or edit one PLUMED-native CV (``label: ACTION params``).

    The parameter line is free text on purpose: the tool never rewrites a PLUMED
    keyword, so it cannot invent an invalid one.  What the dialog *does* know is
    what the manual says about the action (which module it lives in, what is
    compulsory, whether it is a multi-component action), and it shows that.
    """

    def __init__(self, cv: "PlumedCv | None" = None, parent=None,
                 units: str = "nm"):
        super().__init__(parent)
        from .plumedcv import COMPONENTS, PLUMED_ACTIONS, PlumedCv

        self.setWindowTitle("PLUMED 集体变量")
        self.setMinimumWidth(620)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)
        self._rows: dict[str, tuple[QWidget, QWidget]] = {}
        keep_rows = {"action", "params"}

        def row(name: str, key: str | None, label: str, widget: QWidget) -> QWidget:
            holder = label_with_help(label, key if name in keep_rows else None)
            form.addRow(holder, widget)
            self._rows[name] = (holder, widget)
            return widget

        self.w_label = QLineEdit()
        self.w_label.setPlaceholderText("如 cn（字母/下划线开头，后面的行用它引用）")
        row("label", "plumed_cv", "标签", self.w_label)

        self.w_action = QComboBox()
        for name, info in PLUMED_ACTIONS.items():
            module = info.get("module", "")
            self.w_action.addItem(f"{name}（{module}）" if module else name, name)
        self.w_action.currentIndexChanged.connect(self._action_changed)
        row("action", "plumed_cv", "动作", self.w_action)

        self.w_params = QLineEdit()
        row("params", "plumed_cv", "参数", self.w_params)

        self.w_component = QComboBox()
        self.w_component.setEditable(True)
        self.w_component.addItem("（不用：这是一个标量）", "")
        for name in COMPONENTS:
            self.w_component.addItem(name.upper(), name)
        self.w_component.setCurrentIndex(0)
        row("component", "plumed_multicolvar", "分量", self.w_component)

        self.w_sigma = QLineEdit()
        self.w_sigma.setPlaceholderText(
            "留空 = 用 METAD 那行的默认"
            f"（{'Å' if str(units).upper().startswith('A') else 'nm'}）")
        row("sigma", "plumed_cv", "该 CV 的 SIGMA", self.w_sigma)

        self.w_note = QLineEdit()
        self.w_note.setPlaceholderText("可选：写在文件里的注释")
        row("note", "plumed_cv", "备注", self.w_note)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#5d6d7e;")
        outer.addWidget(self.hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        if cv is not None:
            self.w_label.setText(cv.label)
            index = self.w_action.findData(cv.action.upper())
            self.w_action.setCurrentIndex(max(index, 0))
            self.w_params.setText(cv.params)
            self.w_component.setCurrentText(cv.component)
            self.w_sigma.setText("" if cv.sigma is None else f"{cv.sigma:g}")
            self.w_note.setText(cv.note)
        self._action_changed()

    def _action_changed(self) -> None:
        from .plumedcv import PLUMED_ACTIONS

        action = self.w_action.currentData() or "DISTANCE"
        info = PLUMED_ACTIONS.get(action, {})
        self.w_params.setPlaceholderText("例如 " + str(info.get("params", "")))
        is_multicolvar = info.get("kind") == "components"
        for name in ("component",):
            holder, widget = self._rows[name]
            holder.setVisible(is_multicolvar)
            widget.setVisible(is_multicolvar)
        module = info.get("module", "")
        text = str(info.get("note", ""))
        if module:
            text = f"PLUMED 模块：{module}。" + text
        self.hint.setText(text)
        self.adjustSize()

    def value(self) -> "PlumedCv":
        from .plumedcv import PlumedCv

        return PlumedCv(
            label=self.w_label.text().strip(),
            action=(self.w_action.currentData() or "DISTANCE"),
            params=self.w_params.text().strip(),
            component=self.w_component.currentText().strip(),
            sigma=_optional_float(self.w_sigma.text(), self.w_sigma),
            note=self.w_note.text().strip())


class PlumedWallDialog(QDialog):
    """Add or edit one PLUMED wall (``UPPER_WALLS``/``LOWER_WALLS``)."""

    def __init__(self, wall: "PlumedWall | None" = None, args: tuple[str, ...] = (),
                 parent=None, units: str = "nm"):
        super().__init__(parent)
        from .plumedcv import PlumedWall

        self.setWindowTitle("PLUMED 约束墙")
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)

        self.w_arg = QComboBox()
        self.w_arg.setEditable(True)
        for name in args:
            self.w_arg.addItem(name)
        form.addRow(label_with_help("作用在哪个 CV（ARG）", "plumed_wall"), self.w_arg)

        self.w_kind = QComboBox()
        self.w_kind.addItem("UPPER_WALLS：挡住 CV 变大", "UPPER_WALLS")
        self.w_kind.addItem("LOWER_WALLS：挡住 CV 变小", "LOWER_WALLS")
        form.addRow(label_with_help("类型", "plumed_wall"), self.w_kind)

        length = "Å" if str(units).upper().startswith("A") else "nm"
        self.w_at = QLineEdit()
        self.w_at.setPlaceholderText(f"墙的位置 AT，CV 自己的单位（距离是 {length}）")
        form.addRow(QLabel("AT"), self.w_at)
        self.w_kappa = QLineEdit()
        self.w_kappa.setPlaceholderText(
            f"力常数 KAPPA，kJ/mol/{length}²（距离类），如 5000")
        form.addRow(QLabel("KAPPA"), self.w_kappa)
        self.w_exp = QLineEdit()
        self.w_exp.setPlaceholderText("留空 = 普通二次墙；给了 EXP 就是指数墙")
        form.addRow(label_with_help("EXP（可选）", "plumed_wall"), self.w_exp)
        self.w_extra = QLineEdit()
        self.w_extra.setPlaceholderText("这一行上工具没建模的关键字，原样追加")
        form.addRow(QLabel("其他关键字"), self.w_extra)

        hint = QLabel("PLUMED 的墙是二次势 KAPPA·(CV-AT)²，没有 CP2K 那种"
                      "反射/高斯墙；上下各一道就加两条。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5d6d7e;")
        outer.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        if wall is not None:
            self.w_arg.setCurrentText(wall.arg)
            self.w_kind.setCurrentIndex(max(self.w_kind.findData(wall.kind), 0))
            self.w_at.setText("" if wall.at is None else f"{wall.at:g}")
            self.w_kappa.setText("" if wall.kappa is None else f"{wall.kappa:g}")
            self.w_exp.setText("" if wall.exp is None else f"{wall.exp:g}")
            self.w_extra.setText(" ".join(wall.extra))

    def value(self) -> "PlumedWall":
        from .plumedcv import PlumedWall

        return PlumedWall(
            arg=self.w_arg.currentText().strip(),
            kind=self.w_kind.currentData() or "UPPER_WALLS",
            at=_optional_float(self.w_at.text(), self.w_at),
            kappa=_optional_float(self.w_kappa.text(), self.w_kappa),
            exp=_optional_float(self.w_exp.text(), self.w_exp),
            extra=tuple(self.w_extra.text().split()))


class PlumedContactDialog(QDialog):
    """Batch-generate the ``COORDINATION`` table of a molecule-contact CV.

    Writing 20 ``COORDINATION`` lines by hand (each with the 38 acceptor atoms of
    all the *other* molecules) is the tedious part of this kind of CV, and one
    wrong index is invisible.  This dialog describes the system as molecule
    blocks instead ("19x1,8x20" = 4-CPBA + 20 urea), which is how the user already
    thinks about it, and shows the first generated line as it goes.
    """

    def __init__(self, structure=None, units: str = "nm", parent=None):
        super().__init__(parent)
        from .plumedcv import ContactSpec, contact_cvs

        self.structure = structure
        self.units = units
        self.setWindowTitle("接触型 CV 生成器（分子分块 → 配位数表）")
        self.setMinimumWidth(680)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)
        self._spec = ContactSpec
        self._build = contact_cvs

        self.w_blocks = QLineEdit(ContactSpec().blocks)
        self.w_blocks.setPlaceholderText("每块的「原子数x个数」，按原子顺序用逗号分开")
        form.addRow(label_with_help("体系分块", "plumed_contact"), self.w_blocks)

        self.w_center_block = QSpinBox()
        self.w_center_block.setRange(1, 99)
        self.w_center_block.setValue(ContactSpec().center_block)
        self.w_center_offsets = QLineEdit(ContactSpec().center_offsets)
        self.w_center_offsets.setPlaceholderText("块内第几个原子，0 起算，可多个")
        row = QHBoxLayout()
        row.addWidget(QLabel("第"))
        row.addWidget(self.w_center_block)
        row.addWidget(QLabel("块，原子偏移"))
        row.addWidget(self.w_center_offsets, 1)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(label_with_help("中心原子", "plumed_contact"), holder)

        self.w_acceptor_block = QSpinBox()
        self.w_acceptor_block.setRange(1, 99)
        self.w_acceptor_block.setValue(ContactSpec().acceptor_block)
        self.w_acceptor_offsets = QLineEdit(ContactSpec().acceptor_offsets)
        self.w_acceptor_offsets.setPlaceholderText("留空 = 整块所有原子都算受体")
        row = QHBoxLayout()
        row.addWidget(QLabel("第"))
        row.addWidget(self.w_acceptor_block)
        row.addWidget(QLabel("块，原子偏移"))
        row.addWidget(self.w_acceptor_offsets, 1)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(label_with_help("受体原子", "plumed_contact"), holder)

        self.w_exclude = QCheckBox("排除同一个分子里的受体原子（分子间 CV 一般要勾）")
        self.w_exclude.setChecked(True)
        form.addRow(self.w_exclude)
        self.w_nopbc = QCheckBox("加 NOPBC（孤立/非周期体系；周期体系不要勾）")
        form.addRow(self.w_nopbc)

        self.w_r0 = QLineEdit(f"{ContactSpec().r0:g}")
        self.w_nn = QSpinBox()
        self.w_nn.setRange(1, 200)
        self.w_nn.setValue(ContactSpec().nn)
        self.w_mm = QSpinBox()
        self.w_mm.setRange(1, 400)
        self.w_mm.setValue(ContactSpec().mm)
        row = QHBoxLayout()
        row.addWidget(QLabel("R_0"))
        row.addWidget(self.w_r0)
        row.addWidget(QLabel("切换函数指数 NN"))
        row.addWidget(self.w_nn)
        row.addWidget(QLabel("MM"))
        row.addWidget(self.w_mm)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(label_with_help("切换函数", "plumed_contact"), holder)

        self.w_prefix = QLineEdit(ContactSpec().prefix)
        self.w_start = QSpinBox()
        self.w_start.setRange(0, 10000)
        self.w_start.setValue(ContactSpec().start)
        self.w_combine = QLineEdit(ContactSpec().combine_label)
        self.w_combine.setPlaceholderText("留空 = 不生成合计数")
        row = QHBoxLayout()
        row.addWidget(QLabel("标签前缀"))
        row.addWidget(self.w_prefix)
        row.addWidget(QLabel("从第几个开始"))
        row.addWidget(self.w_start)
        row.addWidget(QLabel("合计 CV 的标签"))
        row.addWidget(self.w_combine, 1)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(label_with_help("命名", "plumed_contact"), holder)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        self.preview.setFont(mono_font())
        self.preview.setStyleSheet("color:#34495e;")
        outer.addWidget(self.preview)
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet("color:#b9770e;")
        outer.addWidget(self.notes)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        for widget in (self.w_blocks, self.w_center_offsets, self.w_acceptor_offsets,
                       self.w_r0, self.w_prefix, self.w_combine):
            widget.textChanged.connect(self._update_preview)
        for spin in (self.w_center_block, self.w_acceptor_block, self.w_nn,
                     self.w_mm, self.w_start):
            spin.valueChanged.connect(self._update_preview)
        for box in (self.w_exclude, self.w_nopbc):
            box.toggled.connect(self._update_preview)
        self._update_preview()

    def spec(self) -> "object":
        """The :class:`~cp2kbuild.plumedcv.ContactSpec` the fields describe."""
        return self._spec(
            blocks=self.w_blocks.text().strip(),
            center_block=self.w_center_block.value(),
            center_offsets=self.w_center_offsets.text().strip(),
            acceptor_block=self.w_acceptor_block.value(),
            acceptor_offsets=self.w_acceptor_offsets.text().strip(),
            exclude_same_molecule=self.w_exclude.isChecked(),
            r0=_optional_float(self.w_r0.text(), self.w_r0) or 0.0,
            nn=self.w_nn.value(), mm=self.w_mm.value(),
            prefix=self.w_prefix.text().strip() or "c",
            start=self.w_start.value(),
            combine_label=self.w_combine.text().strip(),
            nopbc=self.w_nopbc.isChecked())

    def value(self) -> "object":
        """Kept so the dialog follows the same ``value()`` convention as the rest."""
        return self.spec()

    def result_rows(self) -> tuple[list, list[str]]:
        """``(rows, notes)`` for the current fields; raises ``ValueError``."""
        return self._build(self.spec(), structure=self.structure, units=self.units)

    def _update_preview(self, *_args) -> None:
        unit = "Å" if str(self.units).upper().startswith("A") else "nm"
        try:
            rows, notes = self.result_rows()
        except ValueError as error:
            self.preview.setText(f"现在这样生成不出来：{error}")
            self.preview.setStyleSheet("color:#c0392b;")
            self.notes.setText("")
            return
        self.preview.setStyleSheet("color:#34495e;")
        made = len(rows) - (1 if self.w_combine.text().strip() and len(rows) > 1 else 0)
        head = rows[0].line()
        self.preview.setText(f"会生成 {made} 个配位数"
                             + ("（末尾再加一行 COMBINE 求和）"
                                if self.w_combine.text().strip() else "")
                             + f"（R_0 单位 {unit}）：\n{head[:150]}…")
        self.notes.setText("\n".join(f"• {note}" for note in notes))


def _remove_rows(table) -> None:
    """Delete every selected row (works for ``QListWidget`` and ``QTableWidget``)."""
    rows = sorted({index.row() for index in table.selectedIndexes()}, reverse=True)
    if isinstance(table, QListWidget):
        for row in rows:
            table.takeItem(row)
        return
    for row in rows:
        table.removeRow(row)


class MetadynPanel(CollapsibleBox):
    """元动力学：`&SUBSYS/&COLVAR` 的定义 + `&MOTION/&FREE_ENERGY/&METADYN`。

    It lives on the **MD page** (that is the only task where CP2K reads those two
    sections) and is folded away by default: the block is long, and most MD runs
    do not use it.  The controls feed the same :class:`~cp2kbuild.recipes.Spec` the
    rest of the page builds, so the preview on the right is the file that would be
    written - the CLI flags and this block produce byte-identical inputs
    (``packaging/gui_selftest.py`` checks that).
    """

    status = Signal(str)

    def __init__(self, generate: "GenerateTab", settings: QSettings, parent=None):
        super().__init__("元动力学（metadynamics）：&COLVAR + &METADYN",
                         key="metadyn", settings=settings, expanded=False,
                         parent=parent)
        self.generate = generate
        self.settings = settings
        self._cvs = []
        self._metavars = []
        #: the PLUMED-native table: its own CVs/walls/bias, in PLUMED's units.  The
        #: two tables are alternatives - whichever the route selects is the one the
        #: draft is built from (cp2kbuild.plumedcv.draft decides).
        self._plumed_cvs: list = []
        self._plumed_walls: list = []
        #: headless tests set this so a modal box cannot block the run (the text
        #: still lands in :attr:`last_notes`)
        self.silent = False
        #: the last ``(title, notes, body)`` an import or a generator produced
        self.last_notes: tuple[str, list[str], str] | None = None
        self._build_ui()
        # The remembered fold state (QSettings) must not reopen a block whose
        # feature is switched off: 元动力学 未启用 时它就是收起的，不管上次是不是
        # 开着。 ``save=False`` 保住了那个偏好——一旦勾上「启用元动力学」它照样会
        # 展开（`_enable_toggled`），下次也还是开着的。
        if not self.w_enable.isChecked():
            self.set_expanded(False, save=False)
        generate.metadyn_panel = self

    # -- construction ------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self.body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._enable_group())
        layout.addWidget(self._hills_group())
        layout.addWidget(self._cv_group())
        layout.addWidget(self._metavar_group())
        layout.addWidget(self._print_group())
        layout.addWidget(self._plumed_cv_group())
        layout.addWidget(self._plumed_bias_group())
        layout.addWidget(self._plumed_free_group())
        self._refresh()

    def _enable_group(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.w_enable = QCheckBox("启用元动力学（&FREE_ENERGY/&METADYN）")
        self.w_enable.toggled.connect(self._enable_toggled)
        holder = QHBoxLayout()
        holder.addWidget(self.w_enable)
        holder.addWidget(HelpBadge(HELP["metadyn"]))
        holder.addStretch(1)
        layout.addLayout(holder)
        self.w_route = QComboBox()
        self.w_route.addItem("CP2K 原生 &METADYN（CV 和丘陵都由 CP2K 算）", "cp2k")
        self.w_route.addItem("PLUMED 驱动（USE_PLUMED T，CV 写在 plumed.dat 里）",
                             "plumed")
        self.w_route.currentIndexChanged.connect(self._route_changed)
        row = QHBoxLayout()
        row.addWidget(QLabel("路线"))
        row.addWidget(HelpBadge(HELP["metadyn_route"]))
        row.addWidget(self.w_route, 1)
        layout.addLayout(row)
        self.w_plumed = QLineEdit()
        self.w_plumed.setPlaceholderText("PLUMED 的输入文件（相对作业目录）")
        self.w_plumed.setText("plumed.dat")
        self.w_plumed.textChanged.connect(self._refresh)
        plumed_row = QHBoxLayout()
        plumed_row.addWidget(QLabel("PLUMED 输入文件"))
        plumed_row.addWidget(HelpBadge(HELP["meta_plumed_file"]))
        plumed_row.addWidget(self.w_plumed, 1)
        self.plumed_row = QWidget()
        self.plumed_row.setLayout(plumed_row)
        layout.addWidget(self.plumed_row)
        self.enable_hint = hint_label()
        layout.addWidget(self.enable_hint)
        note = QHBoxLayout()
        note.addWidget(QLabel("这两段会写进右侧的完整预览（&COLVAR / &FREE_ENERGY）"))
        note.addWidget(HelpBadge(HELP["meta_preview"]))
        note.addStretch(1)
        layout.addLayout(note)
        return box

    def _hills_group(self) -> QWidget:
        box = QWidget()
        self.hills_box = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("丘陵怎么长（&METADYN）"))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        self.w_hills = QCheckBox("撒丘陵（DO_HILLS T）")
        self.w_hills.setChecked(True)
        self.w_hills.toggled.connect(self._refresh)
        check_row(form, "meta_do_hills", self.w_hills)
        self.w_height = QDoubleSpinBox()
        self.w_height.setDecimals(6)
        self.w_height.setRange(0.0, 1.0)
        self.w_height.setSingleStep(0.0001)
        self.w_height.setValue(0.001)
        self.w_height.setSuffix(" Hartree")
        self.w_height.setToolTip("")
        self.w_height.valueChanged.connect(self._refresh)
        form_row(form, "meta_hill_height", "丘陵高度 WW", self.w_height)
        self.w_sigma = QLineEdit()
        self.w_sigma.setPlaceholderText("留空 = 不给默认值；长度类 CV 用 Å，角度用 rad")
        self.w_sigma.textChanged.connect(self._refresh)
        form_row(form, "meta_sigma", "默认丘陵宽度 σ（= SCALE / SIGMA）", self.w_sigma)
        self.w_nt_hills = QSpinBox()
        self.w_nt_hills.setRange(-1000000, 10000000)
        self.w_nt_hills.setValue(30)
        self.w_nt_hills.setSuffix(" 步")
        self.w_nt_hills.setToolTip("")
        self.w_nt_hills.valueChanged.connect(self._refresh)
        form_row(form, "meta_nt_hills", "两次丘陵之间的 MD 步数 NT_HILLS",
                 self.w_nt_hills)
        self.w_wt = QCheckBox("井温元动力学（WELL_TEMPERED T）")
        self.w_wt.toggled.connect(self._refresh)
        check_row(form, "meta_wt", self.w_wt)
        self.w_follow_temp = QCheckBox("用「生成输入」页的模拟温度")
        self.w_follow_temp.setChecked(True)
        self.w_follow_temp.toggled.connect(self._refresh)
        check_row(form, "meta_wt", self.w_follow_temp)
        self.w_temp = QDoubleSpinBox()
        self.w_temp.setRange(0.0, 100000.0)
        self.w_temp.setDecimals(2)
        self.w_temp.setValue(300.0)
        self.w_temp.setSuffix(" K")
        self.w_temp.setToolTip("")
        self.w_temp.valueChanged.connect(self._refresh)
        form_row(form, "meta_wt", "CV 恒温温度 TEMPERATURE", self.w_temp)
        self.w_delta_t = QDoubleSpinBox()
        self.w_delta_t.setRange(0.0, 1000000.0)
        self.w_delta_t.setDecimals(1)
        self.w_delta_t.setValue(3000.0)
        self.w_delta_t.setSuffix(" K")
        self.w_delta_t.setToolTip("")
        self.w_delta_t.valueChanged.connect(self._refresh)
        form_row(form, "meta_delta_t", "DELTA_T", self.w_delta_t)
        self.w_min_disp = QLineEdit()
        self.w_min_disp.setPlaceholderText("留空 = 不启用；单位是 CP2K 内部单位（长度=bohr）")
        self.w_min_disp.textChanged.connect(self._refresh)
        form_row(form, "meta_min_disp", "MIN_DISP", self.w_min_disp)
        self.w_tail = QLineEdit()
        self.w_tail.setPlaceholderText("留空 = 不截断；倍数是 SCALE 的倍数")
        self.w_tail.textChanged.connect(self._refresh)
        form_row(form, "meta_tail_cutoff", "HILL_TAIL_CUTOFF", self.w_tail)
        self.w_lagrange = QCheckBox("扩展拉格朗日（LAGRANGE T）")
        self.w_lagrange.toggled.connect(self._refresh)
        check_row(form, "meta_lagrange", self.w_lagrange)
        self.w_langevin = QCheckBox("CV 自己用 Langevin 积分（LANGEVIN T）")
        self.w_langevin.toggled.connect(self._refresh)
        check_row(form, "meta_lagrange", self.w_langevin)
        return box

    def _cv_group(self) -> QWidget:
        box = QWidget()
        self.cv_box = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("集体变量（&SUBSYS/&COLVAR）"))
        title = QHBoxLayout()
        title.addWidget(QLabel("CV 列表（行序 = &METAVAR COLVAR 的序号）"))
        title.addWidget(HelpBadge(HELP["meta_cv"]))
        title.addWidget(HelpBadge(HELP["meta_cv_kind"]))
        title.addStretch(1)
        layout.addLayout(title)
        self.w_cv_table = ExcelTable(["类型", "定义"], widths=[210, 330],
                                     key="meta_cv", settings=self.settings,
                                     sortable=False)
        self.w_cv_table.setMinimumHeight(120)
        self.w_cv_table.doubleClicked.connect(lambda _index: self.edit_cv())
        layout.addWidget(self.w_cv_table)
        buttons = QHBoxLayout()
        for label, slot in (("加 CV…", self.add_cv), ("编辑…", self.edit_cv),
                            ("删除选中", self.remove_cv),
                            ("上移", lambda: self.move_cv(-1)),
                            ("下移", lambda: self.move_cv(1)),
                            ("清空", self.clear_cvs)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return box

    def _metavar_group(self) -> QWidget:
        box = QWidget()
        self.metavar_box = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("偏置作用在哪些 CV 上（&METADYN/&METAVAR）"))
        title = QHBoxLayout()
        title.addWidget(QLabel("一个 CV 一个 &METAVAR；一个都没有时 CP2K 没有任何偏置项"))
        title.addWidget(HelpBadge(HELP["meta_metavar"]))
        title.addWidget(HelpBadge(HELP["meta_wall"]))
        title.addStretch(1)
        layout.addLayout(title)
        self.w_metavar_table = ExcelTable(
            ["COLVAR", "SCALE", "MASS", "GAMMA", "LAMBDA", "墙"],
            widths=[80, 80, 80, 80, 90, 220], key="meta_metavar",
            settings=self.settings, sortable=False)
        self.w_metavar_table.setMinimumHeight(120)
        self.w_metavar_table.doubleClicked.connect(lambda _index: self.edit_metavar())
        layout.addWidget(self.w_metavar_table)
        buttons = QHBoxLayout()
        for label, slot in (("加 &METAVAR…", self.add_metavar),
                            ("编辑…", self.edit_metavar),
                            ("删除选中", self.remove_metavar),
                            ("每个 CV 一个（自动）", self.autofill_metavars),
                            ("清空", self.clear_metavars)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return box

    # -- the PLUMED-native table ------------------------------------------- #
    def _plumed_cv_group(self) -> QWidget:
        """PLUMED's own CV table (only shown on the PLUMED route)."""
        box = QWidget()
        self.plumed_cv_box = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("PLUMED 集体变量（写在 plumed.dat 里）"))
        title = QHBoxLayout()
        # a long, non-wrapping label here made the whole panel 950 px wide (and so
        # pushed a horizontal scrollbar into it): keep the line short, the rest
        # goes into the wrapping hint under the table
        grammar = QLabel("按 PLUMED 自己的语法写（键角/二面角用弧度），长度用")
        grammar.setWordWrap(True)
        title.addWidget(grammar)
        self.w_p_units = QComboBox()
        self.w_p_units.addItem("nm（PLUMED 默认）", "nm")
        self.w_p_units.addItem("Å（文件里写 UNITS LENGTH=A）", "A")
        self.w_p_units.currentIndexChanged.connect(self._refresh)
        self.w_p_units.currentIndexChanged.connect(lambda _i: self._refresh_units())
        title.addWidget(self.w_p_units)
        title.addWidget(HelpBadge(HELP["plumed_units"]))
        title.addWidget(HelpBadge(HELP["plumed_cv"]))
        title.addWidget(HelpBadge(HELP["plumed_multicolvar"]))
        title.addStretch(1)
        layout.addLayout(title)
        self.w_plumed_table = ExcelTable(
            ["标签", "动作", "参数", "分量", "该 CV 的 σ"],
            widths=[110, 180, 300, 80, 90], key="plumed_cv",
            settings=self.settings, sortable=False)
        self.w_plumed_table.setMinimumHeight(120)
        self.w_plumed_table.doubleClicked.connect(lambda _index: self.edit_plumed_cv())
        layout.addWidget(self.w_plumed_table)
        hint = QLabel("行序 = 文件里的行序；后面的行可以直接引用前面定义的标签"
                      "（ARG=/DATA=/MATRIX=…）。GROUP / 虚拟原子不是标量，"
                      "不能直接进 METAD 的 ARG。多分量动作（COORDINATIONNUMBER、"
                      "DISTANCES…）要选「分量」，工具会把那个关键字补到动作行上。"
                      "**每个 CV 可以有自己的丘陵宽度 σ**（PLUMED 的 SIGMA 是"
                      "每个 ARG 一个值）：双击那一行填「该 CV 的 SIGMA」，"
                      "留空就用下面 METAD 的默认 σ。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5d6d7e;")
        layout.addWidget(hint)
        buttons = QHBoxLayout()
        for label, slot in (("加 PLUMED CV…", self.add_plumed_cv),
                            ("编辑…", self.edit_plumed_cv),
                            ("删除选中", self.remove_plumed_cv),
                            ("上移", lambda: self.move_plumed_cv(-1)),
                            ("下移", lambda: self.move_plumed_cv(1)),
                            ("清空", self.clear_plumed_cvs)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        # seven buttons in one row wanted 600 px - wider than the panel, which put a
        # horizontal scrollbar under the whole form.  Two rows instead.
        builders = QHBoxLayout()
        for label, slot in (("把选中的相加…", self.combine_selected_plumed_cvs),
                            ("接触型 CV 生成器…", lambda: self.generate_contact_cvs()),
                            ("读回 plumed.dat…", lambda: self.import_plumed_file())):
            button = QPushButton(label)
            button.clicked.connect(slot)
            builders.addWidget(button)
        builders.addWidget(HelpBadge(HELP["plumed_contact"]))
        builders.addWidget(HelpBadge(HELP["plumed_import"]))
        builders.addStretch(1)
        layout.addLayout(builders)
        note = QLabel("生成器按「分子分块 + 块内原子」批量写配位数；"
                      "导入把已有文件读回这张表，读不出来的行原样留在文件里。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#5d6d7e;")
        layout.addWidget(note)
        return box

    def _plumed_bias_group(self) -> QWidget:
        """``METAD``/``PRINT`` and the walls, in PLUMED's units."""
        box = QWidget()
        self.plumed_bias_box = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("PLUMED 偏置（METAD）与约束墙"))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        self.w_p_pace = QSpinBox()
        self.w_p_pace.setRange(1, 10000000)
        self.w_p_pace.setValue(500)
        self.w_p_pace.setSuffix(" 步")
        self.w_p_pace.valueChanged.connect(self._refresh)
        form_row(form, "plumed_metad", "PACE（每多少步一个丘陵）", self.w_p_pace)
        self.w_p_height = QDoubleSpinBox()
        self.w_p_height.setDecimals(4)
        self.w_p_height.setRange(0.0, 100000.0)
        self.w_p_height.setValue(1.2)
        self.w_p_height.setSuffix(" kJ/mol")
        self.w_p_height.valueChanged.connect(self._refresh)
        form_row(form, "plumed_metad", "HEIGHT（丘陵高度）", self.w_p_height)
        self.w_p_sigma = QDoubleSpinBox()
        self.w_p_sigma.setDecimals(4)
        self.w_p_sigma.setRange(0.0, 1000.0)
        self.w_p_sigma.setSingleStep(0.01)
        self.w_p_sigma.setValue(0.1)
        self.w_p_sigma.setSuffix(" nm")
        self.w_p_sigma.valueChanged.connect(self._refresh)
        self.p_sigma_row = form_row(form, "plumed_metad", "默认 SIGMA（丘陵宽度）",
                                    self.w_p_sigma)
        self.sigma_note = QLabel("")
        self.sigma_note.setWordWrap(True)
        self.sigma_note.setStyleSheet("color:#7d6608;")
        self.sigma_note.setVisible(False)
        form.addRow(self.sigma_note)
        self.w_p_adaptive = QComboBox()
        self.w_p_adaptive.addItem("不用（固定宽度）", "")
        self.w_p_adaptive.addItem("DIFF（覆盖一段时间）", "DIFF")
        self.w_p_adaptive.addItem("GEOM（覆盖一段距离）", "GEOM")
        self.w_p_adaptive.currentIndexChanged.connect(self._refresh)
        self.w_p_adaptive.currentIndexChanged.connect(lambda _i: self._refresh_units())
        form_row(form, "plumed_adaptive", "ADAPTIVE", self.w_p_adaptive)
        self.w_p_sigma_min = QLineEdit()
        self.w_p_sigma_min.setPlaceholderText("留空 = 不设下限（自适应宽度的最小 σ）")
        self.w_p_sigma_min.textChanged.connect(self._refresh)
        form_row(form, "plumed_adaptive", "SIGMA_MIN", self.w_p_sigma_min)
        self.w_p_sigma_max = QLineEdit()
        self.w_p_sigma_max.setPlaceholderText("留空 = 不设上限（建议给一个，"
                                              "否则宽度会无界地漂）")
        self.w_p_sigma_max.textChanged.connect(self._refresh)
        form_row(form, "plumed_adaptive", "SIGMA_MAX", self.w_p_sigma_max)
        self.w_p_temp = QLineEdit()
        self.w_p_temp.setPlaceholderText("留空 = 用「生成输入」页的模拟温度")
        self.w_p_temp.textChanged.connect(self._refresh)
        form_row(form, "plumed_metad", "TEMP（体系温度 K）", self.w_p_temp)
        self.w_p_biasfactor = QLineEdit()
        self.w_p_biasfactor.setPlaceholderText("留空 = 普通元动力学；>1 才是井温")
        self.w_p_biasfactor.textChanged.connect(self._refresh)
        form_row(form, "plumed_metad", "BIASFACTOR（井温 γ）", self.w_p_biasfactor)
        self.w_p_restart = QCheckBox("接着上次的丘陵撒（RESTART=YES）")
        self.w_p_restart.toggled.connect(self._refresh)
        check_row(form, "plumed_restart", self.w_p_restart)
        self.w_p_args = QLineEdit()
        self.w_p_args.setPlaceholderText(
            "留空 = 表格里没有被别的动作引用的标量（叶子节点），如 20 个中间量 + "
            "cv_uu 时只有 cv_uu")
        self.w_p_args.textChanged.connect(self._refresh)
        form_row(form, "plumed_args", "偏置哪些 CV（METAD ARG=）", self.w_p_args)
        self.w_p_extra = QLineEdit()
        self.w_p_extra.setPlaceholderText(
            "METAD 行上工具没建模的关键字，原样追加，如 GRID_MIN=-1 GRID_MAX=20 "
            "CALC_RCT")
        self.w_p_extra.textChanged.connect(self._refresh)
        form_row(form, "plumed_extra", "METAD 其他关键字", self.w_p_extra)
        self.w_p_label = QLineEdit("m")
        self.w_p_label.setPlaceholderText("METAD 的标签（默认 m，别的行写 m.bias）")
        self.w_p_label.textChanged.connect(self._refresh)
        form_row(form, "plumed_label", "偏置动作的标签", self.w_p_label)
        self.w_p_hills = QLineEdit("HILLS")
        self.w_p_hills.textChanged.connect(self._refresh)
        form_row(form, "plumed_metad", "丘陵文件 FILE", self.w_p_hills)
        self.w_p_print = QCheckBox("写 COLVAR（PRINT 那一行）")
        self.w_p_print.setChecked(True)
        self.w_p_print.toggled.connect(self._refresh)
        check_row(form, "plumed_metad", self.w_p_print)
        self.w_p_colvar = QLineEdit("COLVAR")
        self.w_p_colvar.textChanged.connect(self._refresh)
        form_row(form, "plumed_metad", "COLVAR 文件名", self.w_p_colvar)
        self.w_p_stride = QSpinBox()
        self.w_p_stride.setRange(1, 1000000)
        self.w_p_stride.setValue(1)
        self.w_p_stride.setSuffix(" 步")
        self.w_p_stride.valueChanged.connect(self._refresh)
        form_row(form, "plumed_metad", "每多少步写一次", self.w_p_stride)

        walls_title = QHBoxLayout()
        walls_title.addWidget(QLabel("约束墙"))
        walls_title.addWidget(HelpBadge(HELP["plumed_wall"]))
        walls_title.addStretch(1)
        layout.addLayout(walls_title)
        self.w_plumed_wall_table = ExcelTable(
            ["ARG", "类型", "AT", "KAPPA", "EXP"],
            widths=[140, 130, 90, 110, 90], key="plumed_wall",
            settings=self.settings, sortable=False)
        self.w_plumed_wall_table.doubleClicked.connect(
            lambda _index: self.edit_plumed_wall())
        layout.addWidget(self.w_plumed_wall_table)
        buttons = QHBoxLayout()
        for label, slot in (("加墙…", self.add_plumed_wall),
                            ("编辑…", self.edit_plumed_wall),
                            ("删除选中", self.remove_plumed_wall),
                            ("清空", self.clear_plumed_walls)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return box

    def _plumed_free_group(self) -> QWidget:
        """Raw PLUMED lines: the escape hatch for everything not modelled here."""
        box = CollapsibleBox("自由行（OPES / PCAVARS / PIV / 任何手册里的写法）",
                             key="plumed_free", settings=self.settings,
                             expanded=False)
        self.plumed_free = box
        layout = QVBoxLayout(box.body)
        layout.setContentsMargins(0, 0, 0, 0)
        self.w_free_where = QComboBox()
        self.w_free_where.addItem("METAD 之前", "before")
        self.w_free_where.addItem("文件末尾", "end")
        self.w_free_where.currentIndexChanged.connect(self._refresh)
        row = QHBoxLayout()
        row.addWidget(QLabel("原样写进 plumed.dat，插在"))
        row.addWidget(HelpBadge(HELP["plumed_free"]))
        row.addWidget(self.w_free_where)
        row.addWidget(QLabel("（工具不检查这些行）"))
        row.addStretch(1)
        layout.addLayout(row)
        self.w_free = QPlainTextEdit()
        self.w_free.setFont(mono_font())
        self.w_free.setPlaceholderText(
            "例如：\nopes: OPES_METAD ARG=cn.mean PACE=500 BARRIER=40\n"
            "或  pca: PCAVARS REFERENCE=ref.pdb TYPE=OPTIMAL")
        self.w_free.setMaximumHeight(120)
        self.w_free.textChanged.connect(self._refresh)
        layout.addWidget(self.w_free)
        return box

    # -- PLUMED table edits ------------------------------------------------ #
    def add_plumed_cv(self) -> None:
        dialog = PlumedCvDialog(None, self,
                                units=self.w_p_units.currentData() or "nm")
        if dialog.exec() == QDialog.Accepted:
            self._plumed_cvs.append(dialog.value())
            self._refresh()

    def edit_plumed_cv(self) -> None:
        row = self.w_plumed_table.currentRow()
        if not 0 <= row < len(self._plumed_cvs):
            return
        dialog = PlumedCvDialog(self._plumed_cvs[row], self,
                                units=self.w_p_units.currentData() or "nm")
        if dialog.exec() == QDialog.Accepted:
            self._plumed_cvs[row] = dialog.value()
            self._refresh()

    def remove_plumed_cv(self) -> None:
        rows = sorted({index.row()
                       for index in self.w_plumed_table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            if 0 <= row < len(self._plumed_cvs):
                del self._plumed_cvs[row]
        if rows:
            self._refresh()

    def move_plumed_cv(self, step: int) -> None:
        row = self.w_plumed_table.currentRow()
        target = row + step
        if not (0 <= row < len(self._plumed_cvs)
                and 0 <= target < len(self._plumed_cvs)):
            return
        self._plumed_cvs[row], self._plumed_cvs[target] = \
            self._plumed_cvs[target], self._plumed_cvs[row]
        self._refresh()
        self.w_plumed_table.setCurrentCell(target, 0)

    def clear_plumed_cvs(self) -> None:
        self._plumed_cvs = []
        self._refresh()

    def add_plumed_wall(self) -> None:
        from .plumedcv import PlumedSetup

        dialog = PlumedWallDialog(None, tuple(self.plumed_setup().args()), self,
                                  units=self.w_p_units.currentData() or "nm")
        if dialog.exec() == QDialog.Accepted:
            self._plumed_walls.append(dialog.value())
            self._refresh()

    def edit_plumed_wall(self) -> None:
        row = self.w_plumed_wall_table.currentRow()
        if not 0 <= row < len(self._plumed_walls):
            return
        dialog = PlumedWallDialog(self._plumed_walls[row],
                                  tuple(self.plumed_setup().args()), self,
                                  units=self.w_p_units.currentData() or "nm")
        if dialog.exec() == QDialog.Accepted:
            self._plumed_walls[row] = dialog.value()
            self._refresh()

    def remove_plumed_wall(self) -> None:
        rows = sorted({index.row()
                       for index in self.w_plumed_wall_table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            if 0 <= row < len(self._plumed_walls):
                del self._plumed_walls[row]
        if rows:
            self._refresh()

    def clear_plumed_walls(self) -> None:
        self._plumed_walls = []
        self._refresh()

    def _structure(self):
        """The structure the 「生成输入」page currently holds (None while empty)."""
        try:
            spec = self.generate.collect_spec()
        except Exception:       # noqa: BLE001 - a half-typed form must not break this
            return None
        return getattr(spec, "structure", None)

    def _show_notes(self, title: str, notes: list[str], body: str = "") -> None:
        """Report what was added and what the tool could not place."""
        self.last_notes = (title, list(notes), body)
        if self.silent or (not notes and not body):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(body or title)
        if notes:
            box.setDetailedText("\n".join(f"• {note}" for note in notes))
        box.exec()

    def combine_selected_plumed_cvs(self) -> None:
        """Add one ``COMBINE`` row that adds up the selected CVs.

        The natural way to build the CV you bias out of 20 per-molecule
        coordination numbers - and the place where ``SUM ARG=a,b,c`` gets typed by
        mistake, which PLUMED rejects outright (its ``SUM`` takes one vector).
        """
        rows = sorted({index.row()
                       for index in self.w_plumed_table.selectedIndexes()})
        self._combine_plumed_rows(rows)

    def _combine_plumed_rows(self, rows: list[int], label: str | None = None) -> str:
        """The testable core of the button; returns the label it added (or "")."""
        from .plumedcv import PlumedCv

        picked = [self._plumed_cvs[row] for row in rows
                  if 0 <= row < len(self._plumed_cvs)]
        if len(picked) < 2:
            if not self.silent:
                QMessageBox.information(self, "先选中几行",
                                        "在 PLUMED 集体变量表里选中两行以上"
                                        "（Ctrl/Shift 多选），再来相加。")
            return ""
        skipped = [cv.label for cv in picked if not cv.is_scalar]
        usable = [cv for cv in picked if cv.is_scalar]
        if len(usable) < 2:
            if not self.silent:
                QMessageBox.warning(self, "这些行不能相加",
                                    "选中的行里没有两个标量：多分量动作要先选「分量」，"
                                    "GROUP/虚拟原子不能进 ARG。")
            return ""
        if label is None:
            label, ok = QInputDialog.getText(self, "合计 CV 的标签",
                                             "给这个和起个标签（METAD 就偏置它）：",
                                             text="cv")
            if not ok:
                return ""
        label = (label or "").strip()
        if not label:
            return ""
        if any(cv.label == label for cv in self._plumed_cvs):
            if not self.silent:
                QMessageBox.warning(self, "标签重复",
                                    f"表里已经有 {label} 了，换个名字。")
            return ""
        names = [cv.label for cv in usable]
        row = PlumedCv(
            label=label, action="COMBINE",
            params=f"ARG={','.join(names)} "
                   f"COEFFICIENTS={','.join('1' for _ in names)} PERIODIC=NO",
            note=f"把上面 {len(names)} 个 CV 加起来（SUM 只吃一个向量，"
                 "多个标量相加要用 COMBINE）")
        position = max(rows) + 1
        self._plumed_cvs.insert(min(position, len(self._plumed_cvs)), row)
        self._refresh()
        notes = ([f"{name} 不是标量，没有进这一行" for name in skipped]
                 + [f"偏置哪些 CV 留空时，工具会选「叶子」标量，"
                    f"所以 METAD 的 ARG 会是 {label}"])
        self._show_notes("已相加", notes,
                         f"加了 {label}: COMBINE（{len(names)} 项）。")
        return label

    def generate_contact_cvs(self) -> None:
        """Fill the CV table from the molecule-block description."""
        from .plumedcv import contact_cvs

        structure = self._structure()
        units = self.w_p_units.currentData() or "nm"
        dialog = PlumedContactDialog(structure, units, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            rows, notes = contact_cvs(dialog.spec(), structure=structure, units=units)
        except ValueError as error:
            if not self.silent:
                QMessageBox.warning(self, "生成不了", str(error))
            return
        self._plumed_cvs.extend(rows)
        self._refresh()
        self._show_notes("接触型 CV 生成器", notes,
                         f"已往 PLUMED 集体变量表里加了 {len(rows)} 行"
                         f"（R_0 单位 {units}）。")

    def import_plumed_file(self, path: str | None = None) -> list[str]:
        """Read a ``plumed.dat`` back into the table; returns the notes."""
        from .plumedcv import read_file

        if path is None:
            path, _filter = QFileDialog.getOpenFileName(
                self, "读回 plumed.dat", "",
                "PLUMED 输入 (*.dat *.plumed *.inp);;所有文件 (*)")
        if not path:
            return []
        try:
            setup, notes = read_file(path)
        except OSError as error:
            if not self.silent:
                QMessageBox.warning(self, "读不了这个文件", str(error))
            return []
        self.set_plumed_setup(setup)
        self._refresh()
        self._show_notes(
            "读回 plumed.dat", notes,
            f"已读入 {len(setup.cvs)} 个 CV、{len(setup.walls)} 道墙、"
            f"{len(setup.free_lines)} 行自由行（长度单位 "
            f"{setup.length_unit}）。工具没建模的东西留在原处，"
            "生成的草稿里还会看到它们。")
        return notes

    def _refresh_plumed_table(self) -> None:
        self.w_plumed_table.setRowCount(0)
        for cv in self._plumed_cvs:
            # the width column: PLUMED's SIGMA is one value **per ARG**, so each CV
            # can carry its own - "默认" means this row inherits the METAD default
            width = f"{cv.sigma:g}" if cv.sigma is not None else "默认"
            self.w_plumed_table.add_row([
                cv.label or "（没标签）", cv.action.upper(), cv.params,
                cv.component or ("？" if cv.kind == "components" else ""), width])
        self.w_plumed_wall_table.setRowCount(0)
        for wall in self._plumed_walls:
            self.w_plumed_wall_table.add_row([
                wall.arg, wall.kind.upper(),
                "" if wall.at is None else f"{wall.at:g}",
                "" if wall.kappa is None else f"{wall.kappa:g}",
                "" if wall.exp is None else f"{wall.exp:g}"])

    def _refresh_sigma_note(self) -> None:
        """Say whether the 「默认 SIGMA」 field is what actually goes into the file.

        The default is only used by the CVs that carry no σ of their own, so a file
        that gives every CV its own width (``METAD ARG=a,b SIGMA=0.15,0.5``) makes
        this field do nothing at all - which used to look like the edit was lost.
        """
        if not hasattr(self, "sigma_note"):
            return
        chosen = self.plumed_setup().chosen()
        own = [(cv.label or "（没标签）", cv.sigma) for cv in chosen
               if cv.sigma is not None]
        adaptive = (self.w_p_adaptive.currentData() or "") in ("DIFF", "GEOM")
        if adaptive and own:
            self.sigma_note.setText(
                "ADAPTIVE 打开时 SIGMA 只能给一个数：表格里 "
                + "、".join(f"{label} σ={value:g}" for label, value in own[:4])
                + " 会被忽略（宽度由 PLUMED 自己算）")
        elif own and len(own) == len(chosen):
            self.sigma_note.setText(
                f"表格里 {len(own)} 个 CV 各自设了 σ（"
                + "、".join(f"{label} {value:g}" for label, value in own[:4])
                + ("…" if len(own) > 4 else "")
                + "）：上面这一格现在用不上，改它不会影响文件；"
                "要让某个 CV 用默认值，就把那一行的 σ 清空")
        elif own:
            self.sigma_note.setText(
                f"表格里 {len(own)} 个 CV 自己设了 σ（"
                + "、".join(f"{label} {value:g}" for label, value in own[:4])
                + ("…" if len(own) > 4 else "")
                + f"），其余 {len(chosen) - len(own)} 个用上面这一格；"
                "改这一格只影响后者")
        else:
            self.sigma_note.setText("")
        self.sigma_note.setVisible(bool(self.sigma_note.text()))

    def _print_group(self) -> QWidget:
        box = QWidget()
        self.print_box = box
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(section_label("输出文件（&METADYN/&PRINT）"))
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        self.w_print_colvar = QCheckBox("写 CV 随时间的变化（-1.colvar）")
        self.w_print_colvar.setChecked(True)
        self.w_print_colvar.toggled.connect(self._refresh)
        check_row(form, "meta_print", self.w_print_colvar)
        self.w_print_hills = QCheckBox("写撒过的丘陵（-1.hills，接力与自由能面需要）")
        self.w_print_hills.setChecked(True)
        self.w_print_hills.toggled.connect(self._refresh)
        check_row(form, "meta_print", self.w_print_hills)
        self.w_print_every = QSpinBox()
        self.w_print_every.setRange(1, 1000000)
        self.w_print_every.setValue(1)
        self.w_print_every.setSuffix(" 步")
        self.w_print_every.setToolTip("")
        self.w_print_every.valueChanged.connect(self._refresh)
        form_row(form, "meta_print_every", "每多少 MD 步写一行（&EACH MD）",
                 self.w_print_every)
        return box

    # -- the tab's own state ---------------------------------------------- #
    def set_backend(self, index, db) -> None:
        """Kept for the window's uniform ``set_backend`` loop; nothing to load."""
        self.index = index
        self.db = db

    def _route_changed(self, *_args) -> None:
        self._refresh()

    def metadyn(self) -> "Metadyn | None":
        """The ``Spec.metadyn`` value this page describes (None = switched off)."""
        if not self.w_enable.isChecked():
            return None
        plumed = self.w_route.currentData() == "plumed"
        temperature = None
        if self.w_wt.isChecked() and self.w_lagrange.isChecked():
            # `&METADYN TEMPERATURE` thermostats the CVs of the extended-Lagrangian
            # scheme; with LAGRANGE off nothing reads it (well-tempered takes its
            # reference temperature from the MD page), so it is not written at all
            temperature = (self.generate.w_temperature.value()
                           if self.w_follow_temp.isChecked()
                           else self.w_temp.value())
        return Metadyn(
            enabled=True,
            use_plumed=plumed,
            plumed_file=self.w_plumed.text().strip() or "plumed.dat",
            do_hills=self.w_hills.isChecked(),
            nt_hills=self.w_nt_hills.value(),
            hill_height=self.w_height.value(),
            well_tempered=self.w_wt.isChecked(),
            temperature=temperature,
            delta_t=(self.w_delta_t.value() if self.w_wt.isChecked() else None),
            min_disp=_optional_float(self.w_min_disp.text(), self.w_min_disp),
            hill_tail_cutoff=_optional_float(self.w_tail.text(), self.w_tail),
            lagrange=self.w_lagrange.isChecked(),
            lagrange_langevin=self.w_langevin.isChecked(),
            metavars=tuple(self._metavars),
            cvs=tuple(self._cvs),
            print_colvar=self.w_print_colvar.isChecked(),
            print_hills=self.w_print_hills.isChecked(),
            print_every=self.w_print_every.value(),
            sigma=_optional_float(self.w_sigma.text(), self.w_sigma),
            plumed_setup=self.plumed_setup(),
        )

    def plumed_setup(self) -> "PlumedSetup":
        """The PLUMED-native table as it stands (always built, route-agnostic).

        It is kept even on the native route so that switching routes cannot lose
        what was typed; ``cp2kbuild.plumedcv.draft`` decides whether it or the
        translated CP2K table becomes the file.
        """
        from .plumedcv import PlumedBias, PlumedSetup

        bias = PlumedBias(
            label=self.w_p_label.text().strip() or "m",
            pace=self.w_p_pace.value(),
            height=self.w_p_height.value(),
            sigma=self.w_p_sigma.value(),
            temperature=_optional_float(self.w_p_temp.text(), self.w_p_temp),
            biasfactor=_optional_float(self.w_p_biasfactor.text(),
                                       self.w_p_biasfactor),
            hills_file=self.w_p_hills.text().strip() or "HILLS",
            print_file=self.w_p_colvar.text().strip() or "COLVAR",
            print_stride=self.w_p_stride.value(),
            print_colvar=self.w_p_print.isChecked(),
            restart=self.w_p_restart.isChecked(),
            adaptive=self.w_p_adaptive.currentData() or "",
            sigma_min=self.w_p_sigma_min.text().strip(),
            sigma_max=self.w_p_sigma_max.text().strip(),
            extra=tuple(self.w_p_extra.text().split()),
            args=tuple(part.strip() for part in
                       self.w_p_args.text().replace(',', ' ').split()
                       if part.strip()))
        return PlumedSetup(
            cvs=tuple(self._plumed_cvs), walls=tuple(self._plumed_walls),
            bias=bias,
            free_lines=tuple(self.w_free.toPlainText().splitlines()),
            free_where=self.w_free_where.currentData() or "before",
            units=self.w_p_units.currentData() or "nm")

    def plumed_draft(self, spec=None) -> str:
        """The ``plumed.dat`` this block describes (empty when not on that route).

        Only the PLUMED route has one: on the native route the CV table becomes
        ``&COLVAR`` blocks in the CP2K input instead.
        """
        metadyn = self.metadyn()
        if metadyn is None or not metadyn.use_plumed:
            return ""
        if spec is None:
            try:
                spec = self.generate.collect_spec()
            except Exception:       # noqa: BLE001 - a half-typed form
                spec = None
        from .plumedcv import draft

        return draft(metadyn,
                     getattr(spec, "structure", None),
                     temperature=getattr(spec, "temperature", None),
                     version=__version__)

    # -- CV table --------------------------------------------------------- #
    def _refresh_cv_table(self) -> None:
        self.w_cv_table.setRowCount(0)
        for cv in self._cvs:
            self.w_cv_table.add_row([cv.label, cv.summary()])

    def _refresh_metavar_table(self) -> None:
        self.w_metavar_table.setRowCount(0)
        for metavar in self._metavars:
            wall = metavar.wall
            if wall is None or not wall.type or wall.type.upper() == "NONE":
                wall_text = "—"
            else:
                wall_text = wall.type + (f" @ {wall.position:g}"
                                         if wall.position is not None else "")
            self.w_metavar_table.add_row([
                str(metavar.colvar),
                "" if metavar.scale is None else f"{metavar.scale:g}",
                "" if metavar.mass is None else f"{metavar.mass:g}",
                "" if metavar.gamma is None else f"{metavar.gamma:g}",
                "" if metavar.lam is None else f"{metavar.lam:g}",
                wall_text])

    def add_cv(self) -> None:
        dialog = CvDialog(None, self)
        if dialog.exec() == QDialog.Accepted:
            self._cvs.append(dialog.value())
            self._refresh()

    def edit_cv(self) -> None:
        row = self.w_cv_table.currentRow()
        if not 0 <= row < len(self._cvs):
            return
        dialog = CvDialog(self._cvs[row], self)
        if dialog.exec() == QDialog.Accepted:
            self._cvs[row] = dialog.value()
            self._refresh()

    def remove_cv(self) -> None:
        rows = sorted({index.row() for index in self.w_cv_table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            if 0 <= row < len(self._cvs):
                del self._cvs[row]
        if rows:
            self._refresh()

    def move_cv(self, step: int) -> None:
        row = self.w_cv_table.currentRow()
        target = row + step
        if not (0 <= row < len(self._cvs) and 0 <= target < len(self._cvs)):
            return
        self._cvs[row], self._cvs[target] = self._cvs[target], self._cvs[row]
        self._refresh()
        self.w_cv_table.setCurrentCell(target, 0)

    def clear_cvs(self) -> None:
        self._cvs = []
        self._refresh()

    # -- METAVAR table ---------------------------------------------------- #
    def _cv_kinds(self) -> tuple[str, ...]:
        """The kind of every CV row, so the &METAVAR dialog can name the unit."""
        return tuple(cv.kind for cv in self._cvs)

    def add_metavar(self) -> None:
        dialog = MetavarDialog(None, len(self._cvs), self, kinds=self._cv_kinds())
        if dialog.exec() == QDialog.Accepted:
            self._metavars.append(dialog.value())
            self._refresh()

    def edit_metavar(self) -> None:
        row = self.w_metavar_table.currentRow()
        if not 0 <= row < len(self._metavars):
            return
        dialog = MetavarDialog(self._metavars[row], len(self._cvs), self,
                               kinds=self._cv_kinds())
        if dialog.exec() == QDialog.Accepted:
            self._metavars[row] = dialog.value()
            self._refresh()

    def remove_metavar(self) -> None:
        rows = sorted({index.row()
                       for index in self.w_metavar_table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            if 0 <= row < len(self._metavars):
                del self._metavars[row]
        if rows:
            self._refresh()

    def clear_metavars(self) -> None:
        self._metavars = []
        self._refresh()

    def autofill_metavars(self) -> None:
        """One SCALE-1 ``&METAVAR`` per CV - the ordinary metadynamics setup."""
        self._metavars = [Metavar(colvar=index)
                          for index in range(1, len(self._cvs) + 1)]
        self._refresh()

    # -- import ----------------------------------------------------------- #
    def import_metadyn(self, metadyn: "Metadyn") -> None:
        """Put what 「读参数」 found in a file into this page."""
        self.w_enable.setChecked(bool(metadyn.enabled))
        index = self.w_route.findData("plumed" if metadyn.use_plumed else "cp2k")
        self.w_route.setCurrentIndex(max(index, 0))
        self.w_plumed.setText(metadyn.plumed_file or "plumed.dat")
        self.w_hills.setChecked(bool(metadyn.do_hills))
        self.w_nt_hills.setValue(int(metadyn.nt_hills))
        self.w_height.setValue(float(metadyn.hill_height))
        self.w_wt.setChecked(bool(metadyn.well_tempered))
        temperature = metadyn.temperature
        follows = (temperature is None
                   or abs(temperature - self.generate.w_temperature.value()) < 1e-6)
        self.w_follow_temp.setChecked(follows)
        self.w_temp.setValue(float(temperature if temperature is not None else 300.0))
        self.w_delta_t.setValue(float(metadyn.delta_t or 3000.0))
        self.w_min_disp.setText("" if metadyn.min_disp is None
                                else f"{metadyn.min_disp:g}")
        self.w_tail.setText("" if metadyn.hill_tail_cutoff is None
                            else f"{metadyn.hill_tail_cutoff:g}")
        self.w_lagrange.setChecked(bool(metadyn.lagrange))
        self.w_langevin.setChecked(bool(metadyn.lagrange_langevin))
        self._cvs = list(metadyn.cvs)
        self._metavars = list(metadyn.metavars)
        self.w_print_colvar.setChecked(bool(metadyn.print_colvar))
        self.w_print_hills.setChecked(bool(metadyn.print_hills))
        self.w_print_every.setValue(max(1, int(metadyn.print_every)))
        self.w_sigma.setText("" if metadyn.sigma is None else f"{metadyn.sigma:g}")
        setup = getattr(metadyn, "plumed_setup", None)
        if setup is not None:
            self.set_plumed_setup(setup)
        self._refresh()

    def set_plumed_setup(self, setup) -> None:
        """Load a PLUMED-native table into the widgets."""
        self._plumed_cvs = list(setup.cvs)
        self._plumed_walls = list(setup.walls)
        bias = setup.bias
        self.w_p_label.setText(bias.label or "m")
        self.w_p_pace.setValue(max(1, int(bias.pace)))
        # a TAU-based METAD line has no HEIGHT; the spin box then shows its minimum
        self.w_p_height.setValue(float(bias.height if bias.height is not None else 0.0))
        self.w_p_sigma.setValue(float(bias.sigma))
        self.w_p_temp.setText("" if bias.temperature is None
                              else f"{bias.temperature:g}")
        self.w_p_biasfactor.setText("" if bias.biasfactor is None
                                    else f"{bias.biasfactor:g}")
        self.w_p_restart.setChecked(bool(bias.restart))
        index = self.w_p_adaptive.findData(bias.adaptive or "")
        self.w_p_adaptive.setCurrentIndex(max(index, 0))
        self.w_p_sigma_min.setText(bias.sigma_min)
        self.w_p_sigma_max.setText(bias.sigma_max)
        self.w_p_extra.setText(" ".join(bias.extra))
        self.w_p_args.setText(",".join(bias.args))
        self.w_p_hills.setText(bias.hills_file or "HILLS")
        self.w_p_colvar.setText(bias.print_file or "COLVAR")
        self.w_p_stride.setValue(max(1, int(bias.print_stride)))
        self.w_p_print.setChecked(bool(bias.print_colvar))
        self.w_free.setPlainText("\n".join(setup.free_lines))
        index = self.w_free_where.findData(setup.free_where or "before")
        self.w_free_where.setCurrentIndex(max(index, 0))
        index = self.w_p_units.findData(setup.units or "nm")
        self.w_p_units.setCurrentIndex(max(index, 0))
        self._refresh_units()

    def _refresh_units(self) -> None:
        """Show which unit the two width fields are in (nm, Å, or DIFF's steps)."""
        unit = self.w_p_units.currentData() or "nm"
        length = "Å" if unit == "A" else "nm"
        adaptive = self.w_p_adaptive.currentData() or ""
        if adaptive == "DIFF":
            width = "步（DIFF 是时间，不是长度）"
        elif adaptive == "GEOM":
            width = f"{length}（GEOM 是一段笛卡尔距离）"
        else:
            width = length
        self.w_p_sigma.setSuffix(f" {width}" if adaptive else f" {length}")
        for widget in (self.w_p_sigma_min, self.w_p_sigma_max):
            widget.setEnabled(bool(adaptive))
        self.w_p_sigma_min.setPlaceholderText(
            f"σ 下限（{width}），每个 CV 一个、逗号分开；只给一个就全用这个值" if adaptive
            else "只在 ADAPTIVE 打开时有意义")
        self.w_p_sigma_max.setPlaceholderText(
            f"σ 上限（{width}）；建议给一个，否则宽度会无界地漂" if adaptive
            else "只在 ADAPTIVE 打开时有意义")

    # -- redraw ----------------------------------------------------------- #
    def _problems(self) -> list[str]:
        try:
            spec = self.generate.collect_spec()
        except Exception:       # noqa: BLE001 - a half-typed form must not break this
            return []
        structure = spec.structure
        natoms = len(structure.atoms) if structure is not None else None
        metadyn = self.metadyn()
        if metadyn is None:
            return []
        try:
            temperature = self.generate.collect_spec().temperature
        except Exception:       # noqa: BLE001
            temperature = None
        return metadyn_problems(metadyn, natoms, temperature)

    def _enable_toggled(self, on: bool) -> None:
        """Switching it on opens the block: the controls are what you came for."""
        if on:
            self.set_expanded(True)
        self._refresh()

    def _refresh(self, *_args) -> None:
        """Re-draw this block and ask 「生成输入」to rebuild the preview."""
        self._redraw()
        self.generate.schedule()

    def _redraw(self) -> None:
        """Re-draw the tables, the control states and the hint line."""
        if not self.w_enable.isChecked() and self.is_expanded():
            # whichever way it got opened (a remembered fold state, a spec that
            # used to have metadynamics, …), an unchecked block goes back to
            # collapsed - without overwriting the remembered preference
            self.set_expanded(False, save=False)
        plumed = self.w_route.currentData() == "plumed"
        self.plumed_row.setVisible(plumed)
        # Two control sets, one per route.  They are *not* translations of each
        # other: PLUMED's language (nm, R_0 compulsory, multicolvar components) is
        # its own, so the native table and the PLUMED table are alternatives and the
        # one belonging to the other route is hidden rather than greyed.
        for widget in (self.hills_box, self.cv_box, self.metavar_box,
                       self.print_box):
            widget.setVisible(not plumed)
        for widget in (self.plumed_cv_box, self.plumed_bias_box,
                       self.plumed_free):
            widget.setVisible(plumed)
        self.w_height.setEnabled(True)
        self.w_nt_hills.setEnabled(True)
        # &METADYN TEMPERATURE is the extended-Lagrangian CV thermostat, so it
        # is greyed out until LAGRANGE is on; the well-tempered reference
        # temperature is the MD one (md_run.F:400)
        self.w_temp.setEnabled(self.w_wt.isChecked()
                               and not self.w_follow_temp.isChecked()
                               and self.w_lagrange.isChecked())
        self.w_follow_temp.setEnabled(self.w_wt.isChecked()
                                      and self.w_lagrange.isChecked())
        self.w_delta_t.setEnabled(self.w_wt.isChecked())
        self.w_print_every.setEnabled(self.w_print_colvar.isChecked()
                                      or self.w_print_hills.isChecked())
        self._refresh_cv_table()
        self._refresh_metavar_table()
        self._refresh_plumed_table()
        self._refresh_units()
        self._refresh_sigma_note()

        metadyn = self.metadyn()
        if metadyn is None:
            set_hint(self.enable_hint,
                     "没启用：输入文件里不会有 &METADYN/&COLVAR。"
                     "这一段只在 MD 任务里生效，所以它就在 MD 页上。")
            return
        problems = self._problems()
        if problems:
            set_hint(self.enable_hint, "；".join(problems), "warning")
        elif plumed:
            setup = self.plumed_setup()
            unit = setup.length_unit
            units_note = (f"长度单位 {unit}"
                          + ("（文件第一行写 UNITS LENGTH=A …）"
                             if unit == "Å" else "（PLUMED 默认）"))
            if setup.active:
                set_hint(self.enable_hint,
                         "PLUMED 路线：CP2K 只从 &METADYN 读一个文件名"
                         "（metadynamics_utils.F：\"only get the file name and read "
                         "nothing else\"），CV/丘陵/墙/输出都写进 "
                         f"{metadyn.plumed_file} —— 右边预览切到「plumed.dat」那一页"
                         "就是它。上面这张表**按 PLUMED 自己的语法**写"
                         f"（{units_note}、能量 kJ/mol，不做任何换算），"
                         "所以 CP2K 的 CV 表在这条路线上不显示、也不参与。"
                         "另需一个**编译时带 PLUMED 的 CP2K**，否则它会直接中止。")
            else:
                set_hint(self.enable_hint,
                         "PLUMED 路线：CP2K 只从 &METADYN 读一个文件名"
                         "（metadynamics_utils.F），所以上面 CP2K 那套控件不显示。"
                         "现在 PLUMED 专用表是空的，草稿会退回到「把 CP2K 的 CV 表"
                         "翻译过去」的老做法（单位、默认值、分量规则都要你自己核对）；"
                         "填了「PLUMED 集体变量」之后，工具就按那份写，不再翻译。")
        else:
            set_hint(self.enable_hint,
                     f"{metadyn}（写成什么看右侧预览。注意两点：CP2K 的 SCALE 就是"
                     "这一维高斯的宽度 σ、默认 0 等于“无限宽”也就是没有偏置，"
                     "所以每个 &METAVAR 都要有宽度——上面填了默认 σ 就会写进去；"
                     "井温的参考温度是「生成输入」页的 &MD TEMPERATURE）")

    def flash(self) -> None:
        """Open the block and blink it, for the 「读参数」 link.

        Only when the feature is on: an expanded block whose 启用元动力学 is
        unchecked shows controls that do nothing, and :meth:`_redraw` would
        collapse it again at the next refresh anyway.
        """
        if self.w_enable.isChecked():
            self.set_expanded(True)
        self.generate.form_scroll.ensureWidgetVisible(self, 40, 60)
        self.body.setStyleSheet("background-color:#fff3cd;")
        QTimer.singleShot(1400, lambda: self.body.setStyleSheet(""))


# --------------------------------------------------------------------------- #
# validate tab
# --------------------------------------------------------------------------- #
class ValidateTab(QWidget):
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.index: DataIndex | None = None
        self.db: ManualDB | None = None
        self.setAcceptDrops(True)

        layout = QVBoxLayout(self)
        # the same frame as the other tabs (Qt's default 9 px margin made tabs 0/1
        # and tabs 2-4 look like two different applications)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.addWidget(QLabel("待校验的输入文件"))
        header.addWidget(HelpBadge(HELP["validate_files"]))
        header.addStretch(1)
        layout.addLayout(header)

        top = QHBoxLayout()
        self.files = QListWidget()
        self.files.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.files.setMaximumHeight(130)
        side = QVBoxLayout()
        add = QPushButton("添加文件…")
        add.clicked.connect(self.add_files)
        remove = QPushButton("移除选中")
        remove.clicked.connect(self.remove_selected)
        clear = QPushButton("清空")
        clear.clicked.connect(self.files.clear)
        self.strict = QCheckBox("警告也算失败")
        strict_holder = QWidget()
        strict_box = QHBoxLayout(strict_holder)
        strict_box.setContentsMargins(0, 0, 0, 0)
        strict_box.setSpacing(5)
        strict_box.addWidget(self.strict)
        strict_box.addWidget(HelpBadge(HELP["validate_strict"]))
        strict_box.addStretch(1)
        run = QPushButton("开始校验")
        run.setDefault(True)
        run.clicked.connect(self.run_checks)
        side.addWidget(add)
        side.addWidget(remove)
        side.addWidget(clear)
        side.addWidget(strict_holder)
        side.addWidget(run)
        side.addStretch(1)
        top.addWidget(self.files, 1)
        top.addLayout(side)
        layout.addLayout(top)

        self.summary = QLabel("把 .inp 加进来，然后点「开始校验」。")
        self.summary.setTextFormat(Qt.RichText)
        layout.addWidget(self.summary)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["文件", "级别", "类型", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

    def add_files(self) -> None:
        # the dialog-directory helpers (_start_directory/_remember_directory)
        # live on the generate tab, which is the one holding the QSettings and
        # the shared "last_dir" memory - this tab has neither
        owner = getattr(self.window(), "generate", None)
        start = owner._start_directory("last_validate_dir") if owner else ""
        names, _ = QFileDialog.getOpenFileNames(
            self, "选择 CP2K 输入文件", start,
            "CP2K 输入 (*.inp *.restart);;所有文件 (*)")
        if names and owner is not None:
            owner._remember_directory("last_validate_dir", names[0])
        for name in names:
            self.files.addItem(name)

    def remove_selected(self) -> None:
        for item in self.files.selectedItems():
            self.files.takeItem(self.files.row(item))

    def set_backend(self, index: DataIndex, db: ManualDB | None) -> None:
        self.index = index
        self.db = db

    def dropEvent(self, event) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            self.files.addItem(url.toLocalFile())

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def run_checks(self) -> None:
        if self.index is None:
            return
        paths = [self.files.item(i).text() for i in range(self.files.count())]
        if not paths:
            self.summary.setText("还没有添加文件。")
            return
        rows: list[tuple[str, object]] = []
        errors = warnings = 0
        notes = 0
        for path in paths:
            name = Path(path).name
            try:
                parsed = read_input(path)
            except Exception as exc:  # noqa: BLE001
                self.summary.setText(f"<span style='color:#c0392b;'>"
                                     f"无法读取 {name}：{exc}</span>")
                continue
            if self.db is None:
                rows.append((name, Diagnostic(
                    "warning", "no-database",
                    "手册数据库不可用，只做了物理一致性检查", path=None)))
            for item in Validator(self.db, self.index).run(parsed.sections,
                                                          parsed.directives):
                rows.append((name, item))
                errors += item.level == "error"
                warnings += item.level == "warning"
                notes += item.level == "info"
            for warning in parsed.warnings:
                rows.append((name, Diagnostic("warning", "parse", warning)))
                warnings += 1

        severity = {"error": 0, "warning": 1, "info": 2}
        rows.sort(key=lambda pair: (severity[pair[1].level], pair[0], pair[1].code))
        self.table.setRowCount(len(rows))
        for row, (name, item) in enumerate(rows):
            cells = [
                QTableWidgetItem(name),
                QTableWidgetItem(LEVEL_LABELS[item.level]),
                QTableWidgetItem(item.code),
                QTableWidgetItem(item.message),
            ]
            cells[1].setForeground(LEVEL_COLORS[item.level])
            hint = []
            if item.path:
                hint.append(f"位置：{item.path}")
            if item.hint:
                hint.append(f"建议：{item.hint}")
            if item.url:
                hint.append(item.url)
            if hint:
                cells[3].setToolTip("\n".join(hint))
            for column, cell in enumerate(cells):
                self.table.setItem(row, column, cell)
        self.table.resizeRowsToContents()

        color = "#c0392b" if errors else ("#b9770e" if warnings else "#1e8449")
        self.summary.setText(
            f"<span style='color:{color};font-weight:bold;'>"
            f"{len(paths)} 个文件：{errors} 错误 / {warnings} 警告 / {notes} 提示</span>")
        self.status.emit(f"校验完成：{errors} 错误 / {warnings} 警告")


# --------------------------------------------------------------------------- #
# keyword tab
# --------------------------------------------------------------------------- #
class KeywordTab(QWidget):
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db: ManualDB | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("中英文都可以：cutoff / 截断能 / 系综 / 赝势")
        self.search.returnPressed.connect(self.do_search)
        self.section = QLineEdit()
        self.section.setPlaceholderText("限定 section，如 FORCE_EVAL/DFT/SCF（可留空）")
        go = QPushButton("搜索")
        go.clicked.connect(self.do_search)
        list_all = QPushButton("列出该 section 的全部关键字")
        list_all.clicked.connect(self.list_section)
        glossary = QPushButton("术语表")
        glossary.clicked.connect(self.show_glossary)
        top.addWidget(self.search, 2)
        top.addWidget(HelpBadge(HELP["keyword_search"]))
        top.addWidget(self.section, 2)
        top.addWidget(HelpBadge(HELP["keyword_section"]))
        top.addWidget(go)
        top.addWidget(list_all)
        top.addWidget(glossary)
        layout.addLayout(top)

        note = QLabel(
            "手册正文是英文的。中文部分（蓝色块里的「中文说明」）是 cp2kbuild "
            "自己写的高频关键字解释，<b>不是手册译文</b>；没有中文说明的词条会显示"
            "英文原文，并把常见术语标出来。搜索框里输入中文也能找到对应关键字。")
        note.setTextFormat(Qt.RichText)
        note.setWordWrap(True)
        note.setStyleSheet("color:#5d6d7e;")
        layout.addWidget(note)

        splitter = QSplitter(Qt.Horizontal)
        self.results = QListWidget()
        self.results.currentRowChanged.connect(self.show_selected)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        splitter.addWidget(self.results)
        splitter.addWidget(self.detail)
        splitter.setSizes([320, 700])
        layout.addWidget(splitter, 1)

        self._hits: list = []

    def set_backend(self, index: DataIndex, db: ManualDB | None) -> None:
        self.db = db
        if db is None:
            self.detail.setHtml("<p>手册数据库不可用。</p>")

    def do_search(self) -> None:
        if self.db is None:
            return
        term = self.search.text().strip()
        section = self.section.text().strip()
        self.results.clear()
        self._hits = []
        if not term:
            self.detail.setHtml(
                "<p>请输入搜索词。<b>中英文都可以</b>："
                "例如 <code>cutoff</code> 或 <code>截断能</code>。</p>"
                "<p>点「术语表」可以看全部常用术语的中文对照。</p>")
            return

        if is_chinese(term):
            # Chinese query: search the curated layer, and show备注 for the
            # English keywords it points at
            for key, entry in cn_search(term, 60):
                self._hits.append(("cn", (key, entry)))
        elif section:
            hit = self.db.keyword(section, term)
            if hit:
                self._hits.append(("en", hit))
        else:
            hits = self.db.search(term, 100)
            # entries with a curated Chinese note are the ones people usually
            # want; among those, shallower sections come first
            hits.sort(key=lambda hit: (
                0 if cn_lookup(hit.path, hit.name) else 1,
                hit.path.count("/"),
                len(hit.name),
                hit.name))
            for hit in hits:
                self._hits.append(("en", hit))

        for kind, payload in self._hits:
            if kind == "cn":
                key, entry = payload
                sec, kw = cn_split_key(key)
                self.results.addItem(f"{entry.name}  ·  {kw}  ·  {sec.split('/')[-1]}")
            else:
                self.results.addItem(f"{payload.name}   ·   {payload.path.split('/')[-1]}")

        if self._hits:
            self.results.setCurrentRow(0)
        else:
            self.detail.setHtml(f"<p>没有找到匹配「{term}」的内容。</p>")
        self.status.emit(f"找到 {len(self._hits)} 条结果")

    def show_glossary(self) -> None:
        rows = "".join(
            f"<tr><td><code>{term}</code></td><td>{cn}</td></tr>"
            for term, cn in sorted(TERMS.items()))
        self.detail.setHtml(
            "<h3>术语表</h3>"
            "<p>手册正文是英文的。下面这些术语会在你查看关键字详情时自动"
            "标注出来，方便对着原文读。</p>"
            f"<table border='0' cellpadding='4'>{rows}</table>")

    def _cn_html(self, section: str, keyword: str | None) -> str:
        entry = cn_lookup(section, keyword)
        if entry is None:
            return ""
        return (
            "<div style='background:#eef4fa; border-left:3px solid #1f4e79;"
            " padding:6px 10px;'>"
            f"<b>【中文说明】{entry.name}</b> "
            "<span style='color:#7b8794;font-size:small'>"
            "（cp2kbuild 自己的说明，不是手册译文）</span><br>"
            f"{entry.text.replace(chr(10), '<br>')}"
            "</div>")

    def list_section(self) -> None:
        if self.db is None:
            return
        path = self.section.text().strip() or self.search.text().strip()
        info = self.db.section(path)
        if info is None:
            matches = self.db.sections_matching(path)
            html = ["<p>没有这个 section。可能是：</p><ul>"]
            html += [f"<li>{m}</li>" for m in matches[:30]]
            html.append("</ul>")
            self.detail.setHtml("".join(html))
            return
        keywords = self.db.keywords(path)
        rows = []
        for name, kw in sorted(keywords.items()):
            default = f" = {kw.default}" if kw.default is not None else ""
            unit = f" [{kw.unit}]" if kw.unit else ""
            entry = cn_lookup(path, name)
            mark = (f"<br><span style='color:#1f4e79;font-size:small'>"
                    f"中文：{entry.name}</span>" if entry else "")
            rows.append(f"<tr><td><b>{name}</b>{mark}</td>"
                        f"<td>{kw.type or '?'}{default}{unit}</td>"
                        f"<td>{kw.description or ''}</td></tr>")
        html = [
            self._cn_html(path, None),
            f"<h3>&amp;{info.name}</h3>",
            f"<p>{info.description or ''}</p>",
            f"<p>手册：<a href='{info.url}'>{info.url}</a></p>",
        ]
        if info.subsections:
            html.append(f"<p>子 section：{', '.join(info.subsections)}</p>")
        html.append("<table border='0' cellpadding='4'>"
                    "<tr><th align='left'>关键字</th><th align='left'>类型/默认值</th>"
                    "<th align='left'>说明</th></tr>")
        html.extend(rows)
        html.append("</table>")
        self.detail.setHtml("".join(html))

    def show_selected(self, row: int) -> None:
        if not (0 <= row < len(self._hits)):
            return
        kind, payload = self._hits[row]
        if kind == "cn":
            key, entry = payload
            section, keyword = cn_split_key(key)
            info = self.db.keyword(section, keyword) if self.db else None
            if info is None and self.db is not None and self.db.has_section(key):
                self.detail.setHtml(self._cn_html(section, keyword) + self._section_html(key))
                return
        else:
            info = payload
            section, keyword = info.path, info.name

        parts = [self._cn_html(section, keyword)]
        parts.append(f"<h3>{info.name}</h3>")
        parts.append(f"<p><b>section</b>: {info.path}</p>")
        if info.type:
            default = f" = {info.default}" if info.default is not None else ""
            unit = f" [{info.unit}]" if info.unit else ""
            parts.append(f"<p><b>类型</b>: {info.type}{default}{unit}</p>")
        if info.aliases:
            parts.append(f"<p><b>别名</b>: {', '.join(info.aliases)}</p>")
        if info.lone_keyword:
            parts.append(f"<p><b>单独使用</b>: {info.lone_keyword}</p>")
        if info.usage:
            parts.append(f"<p><b>用法</b>: <code>{info.usage}</code></p>")
        if info.valid_values:
            parts.append("<p><b>合法取值</b>:</p><ul>")
            for value in info.valid_values:
                description = f" — {value['description']}" if value.get("description") else ""
                parts.append(f"<li><code>{value['value']}</code>{description}</li>")
            parts.append("</ul>")
        if info.description:
            parts.append("<p><b>手册原文</b>:<br>"
                         f"<span style='color:#333;'>{info.description}</span></p>")
            terms = cn_terms_in(info.description)
            if terms:
                rows = "".join(f"<tr><td><code>{t}</code></td><td>{c}</td></tr>"
                               for t, c in terms)
                parts.append("<p><b>术语对照</b>（原文里出现的词）:</p>"
                             f"<table border='0' cellpadding='3'>{rows}</table>")
        parts.append(f"<p><a href='{info.url}'>{info.url}</a></p>")
        self.detail.setHtml("".join(parts))

    def _section_html(self, path: str) -> str:
        info = self.db.section(path) if self.db else None
        if info is None:
            return ""
        rows = "".join(
            f"<tr><td><b>{name}</b></td><td>{kw.type or '?'}</td>"
            f"<td>{kw.description or ''}</td></tr>"
            for name, kw in sorted(self.db.keywords(path).items()))
        return (f"<h3>&amp;{info.name}</h3><p>{info.description or ''}</p>"
                f"<p>手册：<a href='{info.url}'>{info.url}</a></p>"
                f"<table border='0' cellpadding='4'>{rows}</table>")


# --------------------------------------------------------------------------- #
# data / basis tab
# --------------------------------------------------------------------------- #
class DataTab(QWidget):
    status = Signal(str)

    def __init__(self, settings: QSettings | None = None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.index: DataIndex | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        row = QHBoxLayout()
        self.data_dir = QLineEdit()
        self.data_dir.setPlaceholderText("CP2K 的 data 目录，例如 E:\\cp2k\\data")
        browse = QPushButton("浏览…")
        browse.clicked.connect(self.browse)
        scan = QPushButton("扫描")
        scan.clicked.connect(self.scan)
        row.addWidget(QLabel("数据目录"))
        row.addWidget(HelpBadge(HELP["data_dir"]))
        row.addWidget(self.data_dir, 1)
        row.addWidget(browse)
        row.addWidget(scan)
        layout.addLayout(row)

        self.summary = QLabel("还没有扫描。")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        box = QGroupBox("推荐基组与赝势（按价电子数匹配）")
        box_layout = QVBoxLayout(box)
        controls = QHBoxLayout()
        self.elements = QLineEdit("H, C, N, O")
        self.elements.setPlaceholderText("元素，逗号分隔")
        self.target = QComboBox()
        for label, value in BASIS_TARGETS:
            self.target.addItem(label, value)
        self.functional = QComboBox()
        self.functional.setEditable(True)
        self.functional.addItems(FUNCTIONALS)
        go = QPushButton("推荐")
        go.clicked.connect(self.recommend)
        fit = QPushButton("列宽自适应")
        fit.clicked.connect(lambda: self.table.autofit())
        controls.addWidget(QLabel("元素"))
        controls.addWidget(HelpBadge(HELP["data_elements"]))
        controls.addWidget(self.elements, 2)
        controls.addWidget(QLabel("偏好"))
        controls.addWidget(HelpBadge(HELP["data_target"]))
        controls.addWidget(self.target, 1)
        controls.addWidget(QLabel("泛函"))
        controls.addWidget(HelpBadge(HELP["functional"]))
        controls.addWidget(self.functional, 1)
        controls.addWidget(go)
        controls.addWidget(fit)
        box_layout.addLayout(controls)

        self.table = ExcelTable(
            ["元素", "基组", "价", "基组文件", "赝势", "赝势文件"],
            widths=[70, 210, 50, 190, 210, 190], key="data_basis",
            settings=getattr(self, "settings", None))
        box_layout.addWidget(self.table)
        self.problems = QLabel("")
        self.problems.setWordWrap(True)
        box_layout.addWidget(self.problems)
        layout.addWidget(box, 1)

    def browse(self) -> None:
        name = QFileDialog.getExistingDirectory(self, "选择 CP2K data 目录",
                                                self.data_dir.text() or str(Path.home()))
        if name:
            self.data_dir.setText(name)
            self.scan()

    def set_backend(self, index: DataIndex, db: ManualDB | None) -> None:
        self.index = index
        if index is not None and index.directory:
            self.data_dir.setText(str(index.directory))
            self.scan()

    def scan(self) -> None:
        path = self.data_dir.text().strip() or None
        self.status.emit("正在扫描数据目录…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.index = DataIndex(path)
        finally:
            QApplication.restoreOverrideCursor()
        summary = self.index.summary()
        if summary.get("directory") is None:
            self.summary.setText("没有找到数据目录。请手动指定 CP2K 的 data 目录。")
            return
        self.summary.setText(
            f"<b>{summary['directory']}</b><br>"
            f"基组文件 {len(summary['basis_files'])} 个，条目 {summary['basis_entries']} 条，"
            f"覆盖 {summary['elements_with_basis']} 种元素；"
            f"赝势文件 {len(summary['potential_files'])} 个，条目 "
            f"{summary['potential_entries']} 条，覆盖 {summary['elements_with_potential']} 种元素。<br>"
            f"基组名 {summary['distinct_basis_names']} 个，赝势名 "
            f"{summary['distinct_potential_names']} 个。")
        self.status.emit("数据目录扫描完成")

    def recommend(self) -> None:
        if self.index is None or self.index.directory is None:
            self.summary.setText("请先扫描一个数据目录。")
            return
        from .recommend import choose_assignments

        elements = [e.strip() for e in self.elements.text().replace(";", ",").split(",")
                    if e.strip()]
        setup = choose_assignments(
            elements, self.index,
            functional=self.functional.currentText().strip() or "PBE",
            target=self.target.currentData() or "solid")
        assignments = list(setup.assignments.values())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for assignment in assignments:
            valence = assignment.basis_valence
            self.table.add_row(
                [
                    assignment.element,
                    assignment.basis,
                    f"q{valence}" if valence else "?",
                    assignment.basis_file or "?",
                    assignment.potential,
                    assignment.potential_file or "?",
                ],
                keys=[assignment.element, assignment.basis,
                      valence if valence else -1,
                      assignment.basis_file or "", assignment.potential,
                      assignment.potential_file or ""])
        self.table.setSortingEnabled(True)
        self.table.resizeRowsToContents()
        messages = []
        if setup.problems:
            messages.append("<span style='color:#c0392b;'><b>问题</b>："
                            + "；".join(setup.problems) + "</span>")
        if setup.notes:
            messages.append("<span style='color:#b9770e;'>"
                            + "；".join(setup.notes) + "</span>")
        self.problems.setText("<br>".join(messages))
        self.status.emit(f"为 {len(assignments)} 种元素给出了建议")


class ScanTab(QWidget):
    """Write CP2K's own CUTOFF/REL_CUTOFF convergence script set.

    The tool's job ends where the cluster's begins: from an input you already
    have it writes ``template.inp`` plus the six scripts of CP2K's manual page
    *How to Converge the CUTOFF and REL_CUTOFF* - ``cutoff_inputs.sh`` /
    ``cutoff_run.sh`` / ``cutoff_analyse.sh`` and the same three for
    ``REL_CUTOFF``.  Running them and reading the results is done on the server
    (the ``*_analyse.sh`` scripts write the ``.ssv`` tables).

    The text shown here is what ``cp2kbuild scan`` prints, byte for byte: both
    call :func:`cp2kbuild.cutoffkit.build_cutoff_study`.
    """

    status = Signal(str)

    def __init__(self, generate: "GenerateTab", settings: QSettings, parent=None):
        super().__init__(parent)
        self.generate = generate
        self.settings = settings
        #: headless tests set this so a modal dialog cannot block the run; the
        #: messages are still recorded and shown in the log below
        self.silent = False
        self.messages: list[tuple[str, str, str]] = []
        self.last_plan = None
        self._build_ui()

    def _notify(self, kind: str, title: str, text: str) -> None:
        self.messages.append((kind, title, text))
        if self.silent:
            return
        {"info": QMessageBox.information, "warn": QMessageBox.warning,
         "error": QMessageBox.critical}[kind](self, title, text)

    # -- construction ------------------------------------------------------ #
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal, self)

        form_host = QWidget()
        form = QVBoxLayout(form_host)
        form.setContentsMargins(10, 10, 10, 10)
        form.setSpacing(8)
        form.addWidget(self._source_group())
        form.addWidget(self._range_group())
        form.addWidget(self._output_group())
        form.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        scroll.setFrameShape(QFrame.NoFrame)
        splitter.addWidget(scroll)

        result_box = QGroupBox("生成结果")
        result_layout = QVBoxLayout(result_box)
        result_header = QHBoxLayout()
        self.scan_summary = QLabel("还没有生成。")
        result_header.addWidget(self.scan_summary, 1)
        result_header.addWidget(HelpBadge(HELP["scan_result"]))
        result_layout.addLayout(result_header)
        self.scan_log = QPlainTextEdit()
        self.scan_log.setReadOnly(True)
        self.scan_log.setFont(mono_font())
        self.scan_log.setLineWrapMode(QPlainTextEdit.NoWrap)
        result_layout.addWidget(self.scan_log)

        self.howto = QLabel()
        self.howto.setTextFormat(Qt.PlainText)
        self.howto.setWordWrap(True)
        self.howto.setStyleSheet("color:#1a5276;")
        self.howto.setText(
            "在服务器上（把整个目录拷过去）：\n"
            "  sh cutoff_inputs.sh && sh cutoff_run.sh && sh cutoff_analyse.sh\n"
            "  # 看 cutoff_data.ssv 定下 CUTOFF，填进三个 rel_cutoff_*.sh 的 cutoff=\n"
            "  sh rel_cutoff_inputs.sh && sh rel_cutoff_run.sh && "
            "sh rel_cutoff_analyse.sh")
        result_layout.addWidget(self.howto)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(result_box)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 620])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)
        self.w_scan_outdir.setText(str(self.settings.value("scan_outdir", "") or ""))
        # closeEvent writes this, so read it back: otherwise the 「已有输入」
        # channel is empty again on every start
        self.w_source_file.setText(
            str(self.settings.value("scan_source_file", "") or ""))
        self._source_changed()

    def _source_group(self) -> QGroupBox:
        box = QGroupBox("1. 用哪份输入")
        layout = QVBoxLayout(box)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("来源"))
        source_row.addWidget(HelpBadge(HELP["scan_source"]))
        self.w_source = QComboBox()
        self.w_source.addItem("用「生成输入」页此刻的参数", "structure")
        self.w_source.addItem("已有输入（.inp，原样保留）", "input")
        self.w_source.currentIndexChanged.connect(self._source_changed)
        source_row.addWidget(self.w_source, 1)
        layout.addLayout(source_row)

        row = QHBoxLayout()
        self.w_source_file = QLineEdit()
        self.w_source_file.setPlaceholderText(
            "已有的 .inp（会原样复制成 template.inp，只换 CUTOFF/REL_CUTOFF 与 RUN_TYPE）")
        self.w_source_file.textChanged.connect(self._source_changed)
        self.w_source_browse = QToolButton()
        self.w_source_browse.setText("浏览…")
        self.w_source_browse.clicked.connect(self.browse_source)
        self.w_source_label = QLabel("输入")
        row.addWidget(self.w_source_label)
        row.addWidget(self.w_source_file, 1)
        row.addWidget(self.w_source_browse)
        layout.addLayout(row)

        self.source_hint = QLabel()
        self.source_hint.setWordWrap(True)
        self.source_hint.setStyleSheet("color:#555;")
        layout.addWidget(self.source_hint)
        return box

    def _range_group(self) -> QGroupBox:
        box = QGroupBox("2. 扫描范围（官方教程的默认值）")
        layout = QVBoxLayout(box)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.w_cutoffs = QLineEdit(",".join(f"{v:g}" for v in DEFAULT_CUTOFFS))
        self.w_cutoffs.setPlaceholderText("第 1 步：CUTOFF 的取值（Ry）")
        form_row(form, "scan_cutoffs", "第 1 步 CUTOFF (Ry)", self.w_cutoffs)

        self.w_rel_cutoff = QDoubleSpinBox()
        self.w_rel_cutoff.setRange(1.0, 100000.0)
        self.w_rel_cutoff.setDecimals(1)
        self.w_rel_cutoff.setValue(DEFAULT_FIXED_REL_CUTOFF)
        self.w_rel_cutoff.setSuffix(" Ry")
        form_row(form, "scan_fixed_rel_cutoff", "固定的 REL_CUTOFF",
                 self.w_rel_cutoff)

        self.w_rel_cutoffs = QLineEdit(",".join(f"{v:g}" for v in DEFAULT_REL_CUTOFFS))
        self.w_rel_cutoffs.setPlaceholderText("第 2 步：REL_CUTOFF 的取值（Ry）")
        form_row(form, "scan_rel_cutoffs", "第 2 步 REL_CUTOFF (Ry)",
                 self.w_rel_cutoffs)

        self.w_fixed_cutoff = QLineEdit()
        self.w_fixed_cutoff.setPlaceholderText(
            "第 1 步定下来的 CUTOFF；留空则脚本里先写 CHANGE_ME（改之前拒绝跑）")
        form_row(form, "scan_fixed_cutoff", "固定的 CUTOFF (Ry)",
                 self.w_fixed_cutoff)

        layout.addLayout(form)
        hint = hint_label()
        set_hint(hint, "两步是耦合的：第 1 步固定 REL_CUTOFF 扫 CUTOFF，"
                       "第 2 步**在第 1 步定下来的 CUTOFF 上**扫 REL_CUTOFF。"
                       "第 2 步那个数留空没关系，生成的脚本会拒绝运行直到你填上。")
        layout.addWidget(hint)
        return box

    def _output_group(self) -> QGroupBox:
        box = QGroupBox("3. 生成")
        layout = QVBoxLayout(box)

        row = QHBoxLayout()
        row.addWidget(QLabel("输出目录"))
        row.addWidget(HelpBadge(HELP["scan_outdir"]))
        self.w_scan_outdir = QLineEdit()
        self.w_scan_outdir.setPlaceholderText("例如 E:\\work\\cutoff_test")
        browse = QToolButton()
        browse.setText("浏览…")
        browse.clicked.connect(self.browse_outdir)
        row.addWidget(self.w_scan_outdir, 1)
        row.addWidget(browse)
        layout.addLayout(row)

        self.scan_button = QPushButton("生成 template.inp + 6 个脚本")
        self.scan_button.setDefault(True)
        self.scan_button.clicked.connect(self.run_scan)
        layout.addWidget(self.scan_button)
        return box

    # -- state ------------------------------------------------------------- #
    def set_backend(self, index: DataIndex | None, db: ManualDB | None) -> None:
        self.index = index

    def _source_changed(self) -> None:
        structure_mode = self.w_source.currentData() == "structure"
        self.w_source_file.setVisible(not structure_mode)
        self.w_source_browse.setVisible(not structure_mode)
        self.w_source_label.setVisible(not structure_mode)
        if structure_mode:
            task = self.generate.task_combo.currentData()
            label = TASK_LABELS.get(task, task)
            note = ("" if task == "energy" else
                    f"（当前是「{label}」，但网格扫描是单点能运行，"
                    "template.inp 里的 RUN_TYPE 会被固定成 ENERGY）")
            self.source_hint.setText(
                "按「生成输入」页此刻的参数生成 template.inp：结构、泛函、色散、"
                "基组/赝势都照那一页，RUN_TYPE 固定成 ENERGY，"
                "并按官方教程写 MAX_SCF 1 + IGNORE_CONVERGENCE_FAILURE"
                "（每个参数点只跑 1 步 SCF）。" + note)
            return
        path = self.w_source_file.text().strip()
        if not path:
            self.source_hint.setText(
                "选一份现成的输入（.inp）：它会被原样复制成 template.inp，"
                "只把 `&MGRID` 里的 CUTOFF / REL_CUTOFF 换成 `LT_cutoff` / "
                "`LT_rel_cutoff` 占位符，并把 RUN_TYPE 改成 ENERGY"
                "（截断能扫描比的是总能；`@SET RTYPE` 那种写法也认）。")
            return
        suffix = Path(path).suffix.lower()
        if suffix not in (".inp", ".restart"):
            self.source_hint.setText(
                f"⚠ {suffix or '这个文件'} 不像是 CP2K 输入：脚本要 sed 替换 "
                "template.inp 里的占位符，请确认这是一份能直接跑的输入。")
            return
        self.source_hint.setText(
            f"会把 {Path(path).name} 复制成 template.inp（只换那两个值、"
            "RUN_TYPE 改成 ENERGY），"
            "并把 BASIS_SET_FILE_NAME / POTENTIAL_FILE_NAME / @INCLUDE 的文件"
            "一起复制进目录（脚本要 cp 它们）。")

    def browse_source(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self, "选择要跑截断能测试的输入",
            self.generate._start_directory("last_source_dir",
                                           self.w_source_file.text().strip()),
            "CP2K 输入 (*.inp *.restart);;所有文件 (*)")
        if name:
            self.generate._remember_directory("last_source_dir", name)
            self.w_source_file.setText(name)

    def browse_outdir(self) -> None:
        name = QFileDialog.getExistingDirectory(
            self, "选择输出目录",
            self.generate._start_directory("last_scan_outdir",
                                           self.w_scan_outdir.text().strip()))
        if name:
            self.generate._remember_directory("last_scan_outdir",
                                              str(Path(name) / "x"))
            self.w_scan_outdir.setText(name)

    # -- actions ----------------------------------------------------------- #
    def run_scan(self) -> int:
        from .cutoffkit import build_cutoff_study, source_from_input, source_from_spec

        structure_mode = self.w_source.currentData() == "structure"
        source = self.w_source_file.text().strip() if not structure_mode else None
        if not structure_mode and not source:
            self._notify("warn", "请先选输入文件",
                         "「已有输入」这个来源要从一份现成的 .inp 出发。")
            return -1
        outdir = self.w_scan_outdir.text().strip()
        if not outdir:
            self._notify("warn", "请先选输出目录", "生成的 7 个文件写到哪个目录？")
            return -1
        fixed = self.w_fixed_cutoff.text().strip()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if structure_mode:
                if self.index is None:
                    self._notify(
                        "warn", "还没有数据目录",
                        "按「生成输入」页的参数生成需要 CP2K 的 data 目录"
                        "（基组/赝势的价电子匹配要用），请先在「基组 / 赝势」页指定。")
                    return -1
                study_source = source_from_spec(self.generate.collect_spec(),
                                                self.index)
            else:
                study_source = source_from_input(source)
            plan = build_cutoff_study(
                study_source, outdir, cutoffs=self.w_cutoffs.text(),
                rel_cutoffs=self.w_rel_cutoffs.text(),
                fixed_rel_cutoff=self.w_rel_cutoff.value(),
                fixed_cutoff=float(fixed) if fixed else None)
        except (ValueError, OSError) as exc:
            self._notify("error", "生成失败", str(exc))
            self.scan_log.setPlainText(str(exc))
            self.status.emit("生成失败")
            return -1
        finally:
            QApplication.restoreOverrideCursor()

        self.last_plan = plan
        self.scan_log.setPlainText(plan.text())
        self.scan_summary.setText(
            f"生成 {len(plan.written)} 个文件 → <b>{plan.outdir}</b>")
        self.settings.setValue("scan_outdir", str(plan.outdir))
        self.status.emit(f"生成了 {len(plan.written)} 个文件")
        if plan.notes:
            self._notify("info", "生成了，但有几点要说",
                         "\n\n".join(plan.notes))
        return 0


class MainWindow(QMainWindow):
    def __init__(self, data_dir: str | None = None):
        super().__init__()
        self.settings = QSettings("cp2kbuild", "cp2kbuild")
        #: headless smoke tests set this so a modal dialog on the startup path
        #: cannot block the run (a real failure is reported instead)
        self.silent = False
        #: what went wrong while loading the backend ("" = nothing)
        self.backend_error = ""
        self.setWindowTitle(
            f"cp2kbuild {__version__} — CP2K 输入文件工具"
            f"（手册 {_target_version()}）")
        self.setWindowIcon(make_icon())
        # before the tabs are built: the stylesheet changes the size hints every
        # control reports (group-box padding, button padding, tab bar), and the
        # "keep the panel narrow" pass below reads those hints
        self.setStyleSheet(_WINDOW_STYLE)
        # the parameter panel is long and the preview is wide: open at a size
        # where neither needs sideways scrolling, but never larger than the
        # screen (a 1366-wide laptop must not get a window it cannot fit)
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.resize(min(1400, int(area.width() * 0.94)),
                        min(900, int(area.height() * 0.92)))
        else:
            self.resize(1400, 880)

        self.tabs = QTabWidget()
        self.generate = GenerateTab(self.settings)
        self.validate_tab = ValidateTab()
        self.scan_tab = ScanTab(self.generate, self.settings)
        self.keyword_tab = KeywordTab()
        self.data_tab = DataTab(self.settings)
        self.tabs.addTab(self.generate, "生成输入")
        self.tabs.addTab(self.scan_tab, "收敛测试")
        self.tabs.addTab(self.validate_tab, "校验输入")
        self.tabs.addTab(self.keyword_tab, "手册查询")
        self.tabs.addTab(self.data_tab, "基组 / 赝势")
        self.setCentralWidget(self.tabs)

        self.setStatusBar(QStatusBar())
        self.generate.status.connect(self._status)
        self.scan_tab.status.connect(self._status)
        self.validate_tab.status.connect(self._status)
        self.keyword_tab.status.connect(self._status)
        self.data_tab.status.connect(self._status)

        self._add_menu()

        self.tabs.setEnabled(False)
        self._status("正在加载手册数据库与基组索引…")
        self.loader = LoaderThread(data_dir or self.settings.value("data_dir"), self)
        self.loader.ready.connect(self._backend_ready)
        self.loader.start()

    def _add_menu(self) -> None:
        file_menu = self.menuBar().addMenu("文件")
        open_action = QAction("打开结构文件…", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.generate.browse_structure)
        file_menu.addAction(open_action)
        save_action = QAction("保存…（当前预览页）", self)
        save_action.setShortcut(QKeySequence.Save)
        save_action.triggered.connect(self.generate.save)
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.setShortcut(QKeySequence.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("帮助")
        manual = QAction("打开 CP2K 手册", self)
        manual.triggered.connect(lambda: QDesktopServices.openUrl(
            "https://manual.cp2k.org/trunk/"))
        help_menu.addAction(manual)
        about = QAction("关于", self)
        about.triggered.connect(self._about)
        help_menu.addAction(about)

    def _about(self) -> None:
        db = self.keyword_tab.db
        if db is not None:
            stats = db.stats()
            database = (f"{stats['sections']} 个 section / {stats['keywords']} 个关键字"
                        f"（{stats['branch']}）")
        else:
            database = "手册数据库不可用"
        QMessageBox.about(
            self, "关于 cp2kbuild",
            f"<h3>cp2kbuild {__version__}</h3>"
            f"<p>内置手册对应的 CP2K 版本：<b>{_target_version()}</b></p>"
            "<p>CP2K 输入文件的生成、收敛与校验工具。</p>"
            "<p>生成器按价电子数匹配基组与赝势，保证不会出现 q 不匹配。</p>"
            f"<p>校验器内置手册数据库：{database}，"
            "并叠加物理一致性规则。</p>"
            "<p>界面用 PySide6 编写。</p>")

    def _status(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)

    def _backend_ready(self, index, db, error: str) -> None:
        if error:
            # do not leave a dead window behind.  Without the manual database the
            # generator and the validator still work (the physical checks are
            # independent of it), and the 「基组 / 赝势」 page is where another
            # data directory - or a working database - can be pointed at.
            self.backend_error = error
            if not self.silent:
                QMessageBox.critical(self, "加载失败", error)
            self._status("加载失败：手册数据库/基组索引读不出来，生成与校验仍可用"
                         "（只做物理一致性检查）")
            index, db = None, None
        elif index is None:
            self._status("没有找到 CP2K data 目录：请在「基组 / 赝势」页手动指定")
        else:
            directory = index.directory
            self.settings.setValue("data_dir", str(directory) if directory else "")
            self._status(f"数据目录：{directory}")
        tabs = [self.generate, self.scan_tab, self.validate_tab,
                self.keyword_tab, self.data_tab]
        for tab in tabs:
            tab.set_backend(index, db)
        self.tabs.setEnabled(True)
        self.generate.schedule()
        if db is None:
            self._status("手册数据库缺失：只做物理一致性检查")

    def closeEvent(self, event) -> None:  # noqa: N802
        self.settings.setValue("scan_outdir", self.scan_tab.w_scan_outdir.text())
        self.settings.setValue("scan_source_file", self.scan_tab.w_source_file.text())
        generate = self.generate
        if hasattr(generate, "w_program_outdir"):
            # the machine's launch settings are worth remembering: they are the
            # same on every program, and retyping "-np 64 / OMP 2" is pointless
            for key, widget in (("program_outdir", generate.w_program_outdir),
                                ("program_mpirun", generate.w_program_mpirun)):
                self.settings.setValue(key, widget.text())
            self.settings.setValue("program_bin",
                                   generate.w_program_bin.currentText())
            self.settings.setValue("program_procs", generate.w_program_procs.value())
            self.settings.setValue("program_omp", generate.w_program_omp.value())
            self.settings.setValue("program_resume",
                                   generate.w_program_resume.isChecked())
        self.settings.sync()
        # Closing the window while the manual database is still being read would
        # destroy a *running* QThread, and Qt answers that by aborting the process
        # (0xC0000409).  The load takes well under a second, so wait it out - the
        # signal is dropped rather than delivered into a widget being torn down.
        loader = getattr(self, "loader", None)
        if loader is not None and loader.isRunning():
            try:
                loader.ready.disconnect()
            except (RuntimeError, TypeError):
                pass
            loader.wait(10000)
        super().closeEvent(event)


def run_selftest(report_path: str) -> int:
    """Headless smoke test of the real startup path, for packaged builds.

    A windowed executable has no console, so the result is written to a JSON
    file: which manual database was found, which data directory was scanned,
    and what each task template produced.
    """
    import json

    from PySide6.QtCore import QEventLoop, QTimer

    app = QApplication.instance() or QApplication([sys.argv[0]])
    report: dict = {"version": __version__, "tasks": {}, "problems": []}

    try:
        window = MainWindow()
        window.silent = True         # a modal load-failure dialog would hang here
        window.resize(1280, 820)
        window.show()
        for _ in range(120):                     # up to ~12 s for the loader
            loop = QEventLoop()
            QTimer.singleShot(100, loop.quit)
            loop.exec()
            if window.tabs.isEnabled():
                break
        report["tabs_enabled"] = window.tabs.isEnabled()
        if window.backend_error:
            report["problems"].append(
                f"backend: {window.backend_error.splitlines()[0]}")

        db = window.keyword_tab.db
        report["manual_db"] = db.stats() if db is not None else None
        index = window.generate.index
        report["data_directory"] = str(index.directory) if index and index.directory else None
        report["basis_names"] = index.summary()["distinct_basis_names"] if index else 0

        generate = window.generate
        # the examples shipped with the source tree are not part of the bundle,
        # so the smoke test writes its own structures
        import tempfile

        workdir = Path(tempfile.gettempdir()) / "cp2kbuild_selftest"
        workdir.mkdir(parents=True, exist_ok=True)
        water = workdir / "water.xyz"
        water.write_text(
            "3\nwater\n"
            "O   0.000000   0.000000  -0.065587\n"
            "H   0.000000  -0.757136   0.520545\n"
            "H   0.000000   0.757136   0.520545\n", encoding="utf-8")
        silicon = workdir / "silicon.xyz"
        silicon.write_text(
            "8\n"
            'Lattice="5.43 0.0 0.0 0.0 5.43 0.0 0.0 0.0 5.43" silicon\n'
            "Si 0.000000 0.000000 0.000000\n"
            "Si 0.000000 2.715000 2.715000\n"
            "Si 2.715000 0.000000 2.715000\n"
            "Si 2.715000 2.715000 0.000000\n"
            "Si 1.357500 1.357500 1.357500\n"
            "Si 1.357500 4.072500 4.072500\n"
            "Si 4.072500 1.357500 4.072500\n"
            "Si 4.072500 4.072500 1.357500\n", encoding="utf-8")

        for task in TASKS:
            periodic = task in ("cell_opt", "band", "pdos")
            path = silicon if periodic else water
            generate.load_from_path(str(path))
            generate.task_combo.setCurrentIndex(generate.task_combo.findData(task))
            if task == "neb":                    # NEB needs its images
                generate.w_replicas.clear()
                for _ in range(5):
                    generate.w_replicas.addItem(str(water))
            generate.refresh()
            errors = sum(1 for row in range(generate.diagnostics.rowCount())
                         if generate.diagnostics.item(row, 0).text() == "错误")
            report["tasks"][task] = {
                "lines": len(generate.text.splitlines()),
                "errors": errors,
                "kinds": generate.kind_table.rowCount(),
            }

        # the metadynamics block on the MD page must produce the two halves in a
        # frozen build too (it is folded away by default - opening it is not
        # needed for the values, but do it so the layout is exercised as well)
        from .metadyn import Cv, Metavar, Wall

        generate.load_from_path(str(water))
        generate.task_combo.setCurrentIndex(generate.task_combo.findData("md"))
        panel = generate.metadyn_panel
        panel.set_expanded(True)
        panel.w_enable.setChecked(True)
        panel._cvs = [Cv(kind="distance", atoms=(1, 2)),
                      Cv(kind="coordination", kinds_from="O", kinds_to="H",
                         r0=1.2, nn=6, nd=8)]
        panel._metavars = [Metavar(colvar=1, scale=0.5,
                                   wall=Wall(position=2.4, type="QUADRATIC",
                                             direction="WALL_MINUS", k=0.5)),
                           Metavar(colvar=2)]
        panel.w_wt.setChecked(True)
        panel.w_delta_t.setValue(3000.0)
        panel._refresh()
        generate.refresh()
        metadata = generate.text
        report["metadynamics"] = {
            "colvar_blocks": metadata.count("&COLVAR\n"),
            "metadyn": "&METADYN" in metadata,
            "well_tempered": "WELL_TEMPERED T" in metadata,
            "wall": "&QUADRATIC" in metadata,
            "hill_files": metadata.count("&HILLS ON"),
            "errors": sum(1 for row in range(generate.diagnostics.rowCount())
                          if generate.diagnostics.item(row, 0).text() == "错误"),
            "foldable": panel.is_expanded(),
        }
        panel._cvs = []
        panel._metavars = []
        panel.w_wt.setChecked(False)
        panel.w_enable.setChecked(False)
        panel._refresh()

        # ... and the PLUMED route: the same table becomes a plumed.dat draft
        panel.w_enable.setChecked(True)
        panel.w_route.setCurrentIndex(1)
        panel.w_plumed.setText("water.plumedinp")
        panel._cvs = [Cv(kind="distance", atoms=(1, 2)),
                      Cv(kind="coordination", kinds_from="O", kinds_to="H",
                         r0=1.2, nn=6, nd=8)]
        panel._metavars = [Metavar(colvar=1), Metavar(colvar=2)]
        panel.w_sigma.setText("0.03")
        panel._refresh()
        generate.refresh()
        draft = generate.plumed_preview.toPlainText()
        report["plumed_draft"] = {
            "lines": len(draft.splitlines()),
            "distance": "cv1: DISTANCE ATOMS=1,2" in draft,
            "coordination": "COORDINATION GROUPA=1 GROUPB=2,3" in draft,
            # the default σ (0.03) follows the CV's unit: the distance becomes nm
            # (0.003) while the coordination number is dimensionless and stays 0.03
            "metad": "METAD ARG=cv1,cv2" in draft
                     and "SIGMA=0.003,0.03" in draft,
            "page": generate.preview_page.isVisible()
                    and generate.preview_page.count() == 2,
            "plumed_only": "&COLVAR" not in generate.text,
        }
        panel.w_sigma.clear()
        panel._cvs = []
        panel._metavars = []
        panel.w_route.setCurrentIndex(0)
        panel.w_enable.setChecked(False)
        panel._refresh()

        # the preview must refresh by itself: change a widget, pump the event
        # loop, and never call refresh() by hand
        generate.load_from_path(str(silicon))
        generate.task_combo.setCurrentIndex(generate.task_combo.findData("energy"))
        generate.refresh()
        before = generate.preview.toPlainText()
        generate.w_cutoff_auto.setChecked(False)
        generate.w_cutoff.setValue(1234.0)
        loop = QEventLoop()
        QTimer.singleShot(500, loop.quit)
        loop.exec()
        after = generate.preview.toPlainText()
        report["live_preview_refreshed"] = "1234" in after and after != before
        generate.w_cutoff_auto.setChecked(True)   # back to the defaults for
        generate.refresh()                        # the convergence part below

        window.close()
    except Exception as exc:  # noqa: BLE001 - the report must survive
        report["problems"].append(f"{type(exc).__name__}: {exc}")
        report["traceback"] = traceback.format_exc()

    # the cutoff-scripts tab must work in a frozen build too: it reads your input
    # and writes seven files, which is where a missing module or a path problem
    # would show up.  Both template channels are exercised: the default one builds
    # the template out of the generation tab (data index, basis lookup, recipes -
    # the parts a frozen build is most likely to lose), the other takes the file.
    try:
        scan = window.scan_tab
        scan.silent = True
        study_input = workdir / "study.inp"
        study_input.write_text(
            "&GLOBAL\n   RUN_TYPE ENERGY\n   PRINT_LEVEL MEDIUM\n&END GLOBAL\n"
            "&FORCE_EVAL\n   METHOD Quickstep\n   &DFT\n      &MGRID\n"
            "         CUTOFF 400\n         REL_CUTOFF 60\n      &END MGRID\n"
            "      &SCF\n         SCF_GUESS ATOMIC\n         MAX_SCF 1\n"
            "         IGNORE_CONVERGENCE_FAILURE TRUE\n      &END SCF\n"
            "   &END DFT\n&END FORCE_EVAL\n", encoding="utf-8")
        scan.w_cutoffs.setText("50,100")
        scan.w_rel_cutoffs.setText("10,20")
        scan.w_fixed_cutoff.setText("100")

        report["cutoff_scripts"] = 0
        for mode, prefix in (("structure", "cutoff_from_params"),
                             ("input", "cutoff_from_input")):
            for index in range(scan.w_source.count()):
                if scan.w_source.itemData(index) == mode:
                    scan.w_source.setCurrentIndex(index)
                    break
            scan.w_source_file.setText(str(study_input))
            outdir = workdir / f"cutoff_test_{mode}"
            scan.w_scan_outdir.setText(str(outdir))
            written = scan.run_scan()
            template = ""
            if (outdir / "template.inp").is_file():
                template = (outdir / "template.inp").read_text(encoding="utf-8")
            step2 = ""
            if (outdir / "rel_cutoff_inputs.sh").is_file():
                step2 = (outdir / "rel_cutoff_inputs.sh").read_text(encoding="utf-8")
            report[prefix] = {
                "exit": written,
                "files": len(scan.last_plan.written) if scan.last_plan else 0,
                "placeholder": "LT_cutoff" in template,
                "run_type_energy": "RUN_TYPE ENERGY" in template,
                "step2_ready": "cutoff=100" in step2,
            }
            if mode == "input":       # the keys the smoke test has always reported
                report["cutoff_scripts"] = report[prefix]["files"]
                report["cutoff_layout"] = sorted(
                    path.name for path in outdir.iterdir())[:8]
                report["cutoff_template_placeholder"] = report[prefix]["placeholder"]
                report["cutoff_step2_ready"] = report[prefix]["step2_ready"]
    except Exception as exc:  # noqa: BLE001
        report["problems"].append(f"cutoff scripts tab: {exc}")
        report["scan_traceback"] = traceback.format_exc()

    # the temperature program writes a whole stage set - one more place a frozen
    # build could lose a module or a path
    try:
        generate = window.generate
        generate.w_ensemble.setCurrentText("LANGEVIN")
        program_dir = workdir / "temperature_program"
        generate.w_program_outdir.setText(str(program_dir))
        program_code = generate.run_program()
        program_files = sorted(path.name for path in program_dir.iterdir()) \
            if program_dir.is_dir() else []
        report["temperature_program"] = {
            "exit": program_code,
            "files": len(program_files),
            "layout": program_files[:6],
            "stages": len(generate.last_program.stages)
            if getattr(generate, "last_program", None) else 0,
        }
        if program_code != 0 or len(program_files) < 4:
            report["problems"].append(
                f"the temperature program did not write its stage set: "
                f"{program_files}")
    except Exception as exc:  # noqa: BLE001
        report["problems"].append(f"temperature program: {exc}")
        report["program_traceback"] = traceback.format_exc()

    # the Chinese layer must be reachable in a frozen build as well
    try:
        keyword_tab = window.keyword_tab
        keyword_tab.search.setText("截断能")
        keyword_tab.section.clear()
        keyword_tab.do_search()
        report["cn_search_hits"] = keyword_tab.results.count()
        report["cn_first_result"] = (keyword_tab.results.item(0).text()
                                     if keyword_tab.results.count() else "")
        keyword_tab.show_glossary()
        report["glossary_rendered"] = len(keyword_tab.detail.toHtml())
    except Exception as exc:  # noqa: BLE001
        report["problems"].append(f"chinese layer: {exc}")

    for task, stats in report["tasks"].items():
        if stats["errors"] or stats["lines"] < 20 or stats["kinds"] < 1:
            report["problems"].append(
                f"{task}: {stats['errors']} errors, {stats['lines']} lines, "
                f"{stats['kinds']} kinds")
    if not report.get("tabs_enabled"):
        report["problems"].append("the interface never finished loading")
    if not report.get("manual_db"):
        report["problems"].append("the manual database was not found")
    if not report.get("data_directory"):
        report["problems"].append("no CP2K data directory was found")
    if not report.get("cn_search_hits"):
        report["problems"].append("the Chinese search returned nothing")
    if not report.get("glossary_rendered"):
        report["problems"].append("the glossary did not render")
    if report.get("cutoff_scripts") != 7 or not report.get("cutoff_template_placeholder"):
        report["problems"].append(
            "the cutoff tab did not write the 7 files (template.inp + 6 scripts)")
    if not report.get("cutoff_step2_ready"):
        report["problems"].append("the cutoff tab did not fill in step 2's CUTOFF")
    if (report.get("temperature_program") or {}).get("stages") != 2:
        report["problems"].append(
            "the temperature program did not plan its two stages: "
            f"{report.get('temperature_program')}")
    metadynamics = report.get("metadynamics") or {}
    if (metadynamics.get("colvar_blocks") != 2 or not metadynamics.get("metadyn")
            or not metadynamics.get("well_tempered")
            or not metadynamics.get("wall")
            or metadynamics.get("hill_files") != 1
            or not metadynamics.get("foldable")
            or metadynamics.get("errors")):
        report["problems"].append(
            f"the metadynamics block is broken: {metadynamics}")
    draft = report.get("plumed_draft") or {}
    if (not draft.get("distance") or not draft.get("coordination")
            or not draft.get("metad") or not draft.get("page")
            or not draft.get("plumed_only")):
        report["problems"].append(
            f"the plumed.dat draft is broken: {draft}")
    for name in ("cutoff_from_params", "cutoff_from_input"):
        stats = report.get(name) or {}
        if (stats.get("exit") != 0 or stats.get("files") != 7
                or not stats.get("placeholder") or not stats.get("run_type_energy")
                or not stats.get("step2_ready")):
            report["problems"].append(f"the {name} channel is broken: {stats}")

    Path(report_path).write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                 encoding="utf-8")
    return 1 if report["problems"] else 0


def _take_option(argv: list[str], name: str, default: str | None = None
                 ) -> str | None:
    """Remove ``--name value`` (or a bare ``--name``) from ``argv``."""
    if name not in argv:
        return None
    index = argv.index(name)
    if index + 1 < len(argv) and not argv[index + 1].startswith("--"):
        value = argv[index + 1]
        del argv[index:index + 2]
    else:
        value = default
        del argv[index]
    return value


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``cp2kbuild-gui`` and for the packaged executable."""
    argv = list(sys.argv if argv is None else argv)
    data_dir = _take_option(argv, "--data-dir")
    selftest = _take_option(argv, "--selftest", "gui_selftest.json")

    if selftest is not None:
        return run_selftest(selftest)

    app = QApplication(argv)
    app.setApplicationName("cp2kbuild")
    app.setApplicationDisplayName("cp2kbuild")
    app.setOrganizationName("cp2kbuild")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(make_icon())
    window = MainWindow(data_dir)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
