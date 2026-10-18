"""PyInstaller entry point for the cp2kbuild command line interface."""

import multiprocessing
import sys


def main() -> int:
    from cp2kbuild.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
