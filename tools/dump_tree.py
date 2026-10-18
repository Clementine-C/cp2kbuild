"""Show the parsed section tree of a CP2K input, to debug linter false positives."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from cp2kbuild.parser import read_input  # noqa: E402

for name in sys.argv[1:]:
    path = Path(name)
    if not path.is_file():
        print(f"!! missing {path}")
        continue
    parsed = read_input(path)
    print("=" * 78)
    print(path.name)
    print("=" * 78)
    for section in parsed.sections:
        for spath, node in section.walk():
            depth = spath.count("/")
            head = f"{'  ' * depth}&{node.name}"
            if node.parameter is not None:
                head += f" {node.parameter}"
            print(head)
            for keyword in node.keywords:
                values = " ".join(str(v) for v in keyword.values)
                print(f"{'  ' * (depth + 1)}{keyword.name} {values}")
