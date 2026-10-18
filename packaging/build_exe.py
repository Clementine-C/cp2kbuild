"""Build the standalone executables with PyInstaller.

Produces two files in ``dist/``:

* ``cp2kbuild-<cp2k>.exe``      - console, the command line interface;
* ``cp2kbuild-gui-<cp2k>.exe``  - windowed, the PySide6 front-end.

``<cp2k>`` is the CP2K version the bundled manual database describes (``2026.2``
for ``cp2k-2026_2-branch``).  Keeping it in the name lets two builds - one per
CP2K release - sit side by side without guessing which is which.

The manual database is bundled inside each executable through
``cp2kbuild/paths.py``, which looks for ``sys._MEIPASS`` when frozen.

Usage::

    python packaging/build_exe.py            # both
    python packaging/build_exe.py gui        # only the GUI
    python packaging/build_exe.py --clean    # wipe build/ and dist/ first
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
DATA_DIR = ROOT / "cp2kbuild" / "data"


def newest_database() -> Path:
    """The newest manual database next to the package (``manual_db_<ver>.json``)."""
    candidates = sorted(DATA_DIR.glob("manual_db_*.json"))
    if not candidates:
        raise SystemExit(f"no manual database in {DATA_DIR}\n"
                         f"build one with: python tools/build_db.py --out <path>")
    return candidates[-1]


DATABASE = newest_database()

# Qt modules this application never imports; excluding them keeps the
# executable (and the one-file unpacking step) noticeably smaller
QT_EXCLUDES = [
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtLocation",
    "PySide6.QtNfc", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtStateMachine",
    "PySide6.QtSvgWidgets", "PySide6.QtTest", "PySide6.QtTextToSpeech",
    "PySide6.QtUiTools", "PySide6.QtWebView",
]

COMMON_EXCLUDES = ["tkinter", "unittest", "pydoc", "doctest", "pdb", "PIL",
                   "numpy", "matplotlib", "scipy", "pandas"]


def run(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode != 0:
        raise SystemExit(f"command failed with exit code {result.returncode}")


def pyinstaller_base(name: str, entry: Path, console: bool) -> list[str]:
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", name,
        "--paths", str(ROOT),
        "--distpath", str(DIST),
        "--workpath", str(BUILD / name),
        "--specpath", str(BUILD),
        "--add-data", f"{DATABASE}{';' if sys.platform == 'win32' else ':'}data",
        "--console" if console else "--windowed",
    ]
    icon = PACKAGING / "icon.ico"
    if icon.is_file():
        command += ["--icon", str(icon)]
    for module in COMMON_EXCLUDES:
        command += ["--exclude-module", module]
    if not console:
        for module in QT_EXCLUDES:
            command += ["--exclude-module", module]
    command.append(str(entry))
    return command


def report() -> None:
    print("\n=== dist ===")
    for item in sorted(DIST.glob("*")):
        print(f"  {item.name:24s} {item.stat().st_size / 1e6:8.1f} MB")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", nargs="?", default="all",
                        choices=["all", "gui", "cli"])
    parser.add_argument("--clean", action="store_true",
                        help="remove build/ and dist/ before building")
    parser.add_argument("--skip-icon", action="store_true")
    parser.add_argument("--database", default=None,
                        help="manual database to bundle (default: the newest)")
    args = parser.parse_args()

    global DATABASE
    if args.database:
        DATABASE = Path(args.database)
    if not DATABASE.is_file():
        raise SystemExit(f"manual database missing: {DATABASE}\n"
                         f"build it with: python tools/build_db.py --out {DATABASE}")
    print(f"bundling {DATABASE.name} ({DATABASE.stat().st_size / 1e6:.1f} MB)")

    # the names carry the CP2K version the bundled manual describes
    sys.path.insert(0, str(ROOT))
    from cp2kbuild.manualdb import database_version

    version = database_version(DATABASE)
    suffix = "" if version in ("", "unknown", "trunk") else f"-{version}"
    if suffix:
        print(f"CP2K {version} -> executables named *{suffix}.exe")
    else:
        print(f"!! cannot tell which CP2K version {DATABASE.name} describes; "
              "falling back to plain names")

    if args.clean:
        for folder in (BUILD, DIST):
            shutil.rmtree(folder, ignore_errors=True)
    DIST.mkdir(exist_ok=True)

    if not args.skip_icon:
        # an icon is nice to have, never a reason to fail the build
        try:
            run([sys.executable, str(PACKAGING / "make_icon.py")])
        except SystemExit as exc:
            print(f"!! icon generation failed ({exc}); building without an icon")

    if args.target in ("all", "cli"):
        run(pyinstaller_base(f"cp2kbuild{suffix}", PACKAGING / "cli_entry.py",
                             console=True))
    if args.target in ("all", "gui"):
        run(pyinstaller_base(f"cp2kbuild-gui{suffix}", PACKAGING / "gui_entry.py",
                             console=False))

    report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
