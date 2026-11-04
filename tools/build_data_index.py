"""Write the *names* of a CP2K ``data/`` directory into a small JSON index.

Why this exists: validating a generated input needs to know **which basis set and
which pseudopotential exist for which element, and with which valence** - that is
all :class:`cp2kbuild.datafiles.DataIndex` ever reads out of CP2K's ``data/``.
The parameters themselves (the 77 MB of numbers) are CP2K's business at run time,
so shipping them would be both pointless and licence-wise awkward (CP2K is GPL,
this tool is MIT).  The *facts* - names, element coverage, valence - are 767 KB.

``cp2kbuild/data/index_<version>.json`` is what this script writes; the tool then
uses a real ``data/`` directory when one is given (``--data-dir`` /
``$CP2K_DATA_DIR`` / a discovered install) and falls back to this index otherwise.

    python tools/build_data_index.py --data E:/desktop/cp2k/cp2k-2026.2/data
    python tools/build_data_index.py --data ../cp2k/data --version 2026.2 --out x.json

The output is deterministic: the same ``data/`` directory produces byte-identical
JSON, so re-running it is a no-op in git.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cp2kbuild.datafiles import DataIndex          # noqa: E402

VERSION_RE = re.compile(r"cp2k-(\d+)[._](\d+)")


def guess_version(directory: Path, explicit: str | None) -> str:
    """``cp2k-2026.2`` (or ``cp2k-2026_2``) in the path -> ``2026.2``."""
    if explicit:
        return explicit
    for part in reversed(directory.parts):
        found = VERSION_RE.search(part.lower())
        if found:
            return f"{found.group(1)}.{found.group(2)}"
    return "unknown"


def build(directory: Path, version: str) -> dict:
    index = DataIndex(str(directory))
    if not index.entries:
        raise SystemExit(
            f"{directory} 里没解析出任何基组或赝势：确认这是 CP2K 的 data 目录"
            "（里面有 BASIS_MOLOPT / GTH_POTENTIALS 这些文件）")
    entries: dict[str, list] = {"basis": [], "potential": []}
    for entry in index.entries:
        # [file, name, element, valence, aliases] - the aliases matter: CP2K's own
        # files list ``GTH-PBE-q4 GTH-PBE`` and an input may write either spelling
        entries[entry.kind].append(
            [Path(entry.file).name, entry.name, entry.element, entry.valence,
             list(entry.aliases)])
    for kind in entries:
        entries[kind].sort(key=lambda row: (row[0], row[2], row[1]))
    return {
        "format": 1,
        "cp2k": version,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "note": "CP2K data/ 里基组与赝势的**名单**（文件、名字、元素、价电子数），"
                "不含任何参数数值。由 tools/build_data_index.py 生成。",
        "files": {"basis": sorted(index.basis_files),
                  "potential": sorted(index.potential_files)},
        "entries": entries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, help="CP2K 的 data 目录")
    parser.add_argument("--version", default=None,
                        help="CP2K 版本（默认从目录名猜：cp2k-2026.2 -> 2026.2）")
    parser.add_argument("--out", default=None,
                        help="输出文件（默认 cp2kbuild/data/index_<版本>.json）")
    args = parser.parse_args()

    directory = Path(args.data)
    if not directory.is_dir():
        raise SystemExit(f"不是目录：{directory}")
    version = guess_version(directory, args.version)
    payload = build(directory, version)
    out = Path(args.out) if args.out else (
        Path(__file__).resolve().parent.parent / "cp2kbuild" / "data"
        / f"index_{version.replace('.', '_')}.json")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                      sort_keys=True) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text.encode("utf-8"))

    counts = {kind: len(rows) for kind, rows in payload["entries"].items()}
    print(f"wrote {out}  ({out.stat().st_size / 1024:.0f} KB)")
    print(f"  CP2K {version}: {counts['basis']} 个基组条目 / "
          f"{counts['potential']} 个赝势条目；"
          f"{len(payload['files']['basis'])} 个基组文件、"
          f"{len(payload['files']['potential'])} 个赝势文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
