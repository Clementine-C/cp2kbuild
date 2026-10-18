"""Does CP2K require &END for a section written on one line with a parameter?

Scan CP2K's own regression inputs: find every "&SECTION param" line and check
whether the following significant line is "&END ...".
"""
import re
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
TESTS = Path(r"E:\desktop\cp2k\cp2k-2025.1\tests")

SECTION = re.compile(r"^&([A-Za-z0-9_]+)\s+\S")
END = re.compile(r"^&END\b", re.I)

total = 0
without_end = Counter()
with_end = 0
mismatched_end = Counter()
examples = {}

for path in TESTS.glob("**/*.inp"):
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        continue
    cleaned = []
    for raw in lines:
        line = raw.split("#", 1)[0].split("!", 1)[0].strip()
        cleaned.append(line)
    for i, line in enumerate(cleaned):
        if not SECTION.match(line) or END.match(line):
            continue
        total += 1
        name = SECTION.match(line).group(1).upper()
        nxt = ""
        for j in range(i + 1, min(i + 6, len(cleaned))):
            if cleaned[j]:
                nxt = cleaned[j]
                break
        if END.match(nxt):
            with_end += 1
            closed = re.match(r"&END\s*([A-Za-z0-9_]+)", nxt, re.I)
            if closed and closed.group(1).upper() != name:
                mismatched_end[name] += 1
                examples.setdefault(
                    f"mismatch:{name}",
                    f"{path.name}: {line!r} -> {nxt!r}")
        else:
            without_end[name] += 1
            examples.setdefault(name, f"{path.name}: {line!r} -> {nxt!r}")

print(f"one-line parameterised sections found: {total}")
print(f"  followed by an &END line: {with_end}")
print(f"  NOT followed by &END    : {total - with_end}")
print("\n&END whose name does not match the section just opened "
      "(the signature of an unclosed one-line section):")
for name, count in mismatched_end.most_common(15):
    print(f"  {count:5d}  &{name:24s} e.g. {examples['mismatch:' + name][:120]}")
