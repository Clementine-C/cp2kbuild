"""PyInstaller entry point for the cp2kbuild graphical front-end."""

import multiprocessing
import sys


def main() -> int:
    from cp2kbuild.gui import main as gui_main

    return gui_main(sys.argv)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
