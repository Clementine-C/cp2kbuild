"""Locating bundled resources, both from source and from a frozen executable.

PyInstaller unpacks ``--add-data`` payloads into a temporary directory that it
exposes as ``sys._MEIPASS``.  Anything that reads a file next to the package
(the manual database, for instance) has to look there first when frozen.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def bundle_root() -> Path:
    """The directory that holds bundled resources."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    return Path(__file__).resolve().parent


def resource_path(relative: str) -> Path:
    """Resolve a resource path such as ``data/manual_db_2025_1.json``."""
    candidate = bundle_root() / relative
    if candidate.exists():
        return candidate
    # next to the package as a fallback (e.g. a hand-edited database)
    return Path(__file__).resolve().parent / relative


def app_dir() -> Path:
    """Directory of the running program, for user-facing config and output."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def write_text_lf(path: "str | Path", text: str, encoding: str = "utf-8") -> Path:
    """Write a generated file with Unix line endings, always.

    Everything this tool writes is meant to end up on a Linux cluster, and
    Windows would otherwise turn every ``\\n`` into ``\\r\\n``: a ``run.sh`` with
    CRLF fails with ``\\r: command not found``, and a CRLF after ``@SET CUTOFF
    450`` can carry the carriage return into the value CP2K reads.  Writing bytes
    sidesteps the platform's newline translation completely.
    """
    target = Path(path)
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    target.write_bytes(normalised.encode(encoding))
    return target


def read_template_text(path: "str | Path", encoding: str = "utf-8-sig") -> str:
    """Read a file byte-for-byte, keeping the line endings it already has.

    ``Path.read_text`` would silently rewrite CRLF to LF on the way in, which
    then makes it impossible to give the file back unchanged.
    """
    return Path(path).read_bytes().decode(encoding)


def write_template_text(path: "str | Path", text: str,
                        encoding: str = "utf-8") -> Path:
    """Write text exactly as given - no newline translation in either direction."""
    target = Path(path)
    target.write_bytes(text.encode(encoding))
    return target
