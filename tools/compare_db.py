"""Compare two manual databases and report what changed between CP2K versions.

Answers the question that matters after upgrading: which sections and keywords
appeared, which went away, whose defaults moved, and which enums gained or lost
values.  Focused on the parts of the input tree a normal user touches, with
``--all`` for the full, noisy list.

Usage::

    python tools/compare_db.py --old cp2kbuild/data/manual_db_2025_1.json \
                              --new cp2kbuild/data/manual_db_2026_2.json
    python tools/compare_db.py --old ... --new ... --section FORCE_EVAL/DFT
    python tools/compare_db.py --old ... --new ... --all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

# the parts of the tree the generator and the linter care about
FOCUS = (
    "CP2K_INPUT/GLOBAL",
    "CP2K_INPUT/FORCE_EVAL",
    "CP2K_INPUT/MOTION",
    "CP2K_INPUT/VIBRATIONAL_ANALYSIS",
)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def section_of(keyword_id: str) -> str:
    return keyword_id.rpartition(".")[0]


def short(keyword_id: str, prefix: str = "CP2K_INPUT.") -> str:
    return keyword_id[len(prefix):] if keyword_id.startswith(prefix) else keyword_id


def expand_prefixes(prefixes: tuple[str, ...]) -> tuple[str, ...]:
    """Accept ``FORCE_EVAL/DFT`` as well as ``CP2K_INPUT/FORCE_EVAL/DFT``.

    Every key in the database carries the ``CP2K_INPUT`` root, so a user who
    copies a path out of the manual URL would otherwise match nothing at all.
    """
    out: list[str] = []
    for prefix in prefixes:
        for candidate in (prefix, f"CP2K_INPUT/{prefix}"):
            if candidate not in out:
                out.append(candidate)
    return tuple(out)


def in_focus(key: str, prefixes: tuple[str, ...]) -> bool:
    """Prefix test that works for both section keys and keyword ids.

    Section keys use ``CP2K_INPUT/FORCE_EVAL`` while keyword ids use
    ``CP2K_INPUT.FORCE_EVAL.DFT...``, so normalise the separators first.
    """
    normalized = key.replace(".", "/")
    return any(normalized == prefix or normalized.startswith(prefix + "/")
               for prefix in prefixes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", required=True)
    parser.add_argument("--new", required=True)
    parser.add_argument("--section", action="append", default=[],
                        help="limit to a section prefix (repeatable)")
    parser.add_argument("--all", action="store_true",
                        help="do not limit to the commonly used subtree")
    parser.add_argument("--limit", type=int, default=60,
                        help="how many entries to list per category")
    args = parser.parse_args()

    old = load(Path(args.old))
    new = load(Path(args.new))
    prefixes = expand_prefixes(tuple(args.section)) if args.section else FOCUS

    def keep(keyword_id: str) -> bool:
        return args.all or not prefixes or in_focus(keyword_id, prefixes)

    old_kw = {k: v for k, v in old["keywords"].items() if keep(k)}
    new_kw = {k: v for k, v in new["keywords"].items() if keep(k)}
    old_sec = {k: v for k, v in old["sections"].items() if keep(k)}
    new_sec = {k: v for k, v in new["sections"].items() if keep(k)}

    # A typo in --section used to look exactly like "this version changed
    # nothing", which is the most dangerous possible answer here.
    if args.section and not (old_kw or new_kw or old_sec or new_sec):
        names = ", ".join(args.section)
        print(f"--section {names} 在两版数据库里都匹配不到任何 section 或关键字。",
              file=sys.stderr)
        print("路径是 CP2K_INPUT 开头的完整路径，例如 FORCE_EVAL/DFT/MGRID；"
              "不确定就用 --all 看全量。", file=sys.stderr)
        return 2

    print(f"旧：{old.get('branch')}  {Path(args.old).name}")
    print(f"     {len(old['sections'])} sections / {len(old['keywords'])} keywords")
    print(f"新：{new.get('branch')}  {Path(args.new).name}")
    print(f"     {len(new['sections'])} sections / {len(new['keywords'])} keywords")
    scope = "全部" if args.all or not prefixes else ", ".join(args.section or prefixes)
    print(f"对比范围：{scope}"
          f"（旧 {len(old_sec)} sections / {len(old_kw)} keywords，"
          f"新 {len(new_sec)} / {len(new_kw)}）")

    added_sec = sorted(set(new_sec) - set(old_sec))
    removed_sec = sorted(set(old_sec) - set(new_sec))
    added = sorted(set(new_kw) - set(old_kw))
    removed = sorted(set(old_kw) - set(new_kw))

    print(f"\n=== 新增 section（{len(added_sec)}）===")
    for key in added_sec[:args.limit]:
        print(f"  + &{key[len('CP2K_INPUT/'):]}")
    if len(added_sec) > args.limit:
        print(f"  ... 还有 {len(added_sec) - args.limit} 个")

    print(f"\n=== 删除 section（{len(removed_sec)}）===")
    for key in removed_sec[:args.limit]:
        print(f"  - &{key[len('CP2K_INPUT/'):]}")
    if len(removed_sec) > args.limit:
        print(f"  ... 还有 {len(removed_sec) - args.limit} 个")

    print(f"\n=== 新增关键字（{len(added)}）===")
    for key in added[:args.limit]:
        record = new_kw[key]
        default = f" = {record.get('default')}" if record.get("default") else ""
        print(f"  + {short(key)}{default}")
    if len(added) > args.limit:
        print(f"  ... 还有 {len(added) - args.limit} 个")

    print(f"\n=== 删除关键字（{len(removed)}）===")
    for key in removed[:args.limit]:
        record = old_kw[key]
        default = f" = {record.get('default')}" if record.get("default") else ""
        print(f"  - {short(key)}{default}")
    if len(removed) > args.limit:
        print(f"  ... 还有 {len(removed) - args.limit} 个")

    # default changes and enum changes only make sense for keywords in both
    common = sorted(set(old_kw) & set(new_kw))
    default_changes = []
    enum_gained = []
    enum_lost = []
    for key in common:
        before, after = old_kw[key], new_kw[key]
        old_default = (before.get("default") or "").strip()
        new_default = (after.get("default") or "").strip()
        if old_default != new_default:
            default_changes.append((key, old_default, new_default))
        old_values = {v["value"] for v in (before.get("valid_values") or [])}
        new_values = {v["value"] for v in (after.get("valid_values") or [])}
        if new_values - old_values:
            enum_gained.append((key, sorted(new_values - old_values)))
        if old_values - new_values:
            enum_lost.append((key, sorted(old_values - new_values)))

    print(f"\n=== 默认值变化（{len(default_changes)}）===")
    for key, before, after in default_changes[:args.limit]:
        print(f"  ~ {short(key)}: {before or '(空)'}  ->  {after or '(空)'}")
    if len(default_changes) > args.limit:
        print(f"  ... 还有 {len(default_changes) - args.limit} 个")

    print(f"\n=== 枚举取值增加（{len(enum_gained)}）===")
    for key, values in enum_gained[:args.limit]:
        shown = ", ".join(values[:6]) + (" ..." if len(values) > 6 else "")
        print(f"  + {short(key)}: {shown}")
    if len(enum_gained) > args.limit:
        print(f"  ... 还有 {len(enum_gained) - args.limit} 个")

    print(f"\n=== 枚举取值减少（{len(enum_lost)}）===")
    for key, values in enum_lost[:args.limit]:
        shown = ", ".join(values[:6]) + (" ..." if len(values) > 6 else "")
        print(f"  - {short(key)}: {shown}")
    if len(enum_lost) > args.limit:
        print(f"  ... 还有 {len(enum_lost) - args.limit} 个")

    print("\n=== 概览 ===")
    print(f"  sections: +{len(added_sec)} / -{len(removed_sec)}")
    print(f"  keywords: +{len(added)} / -{len(removed)}")
    print(f"  默认值变化 {len(default_changes)}，枚举增减 "
          f"+{len(enum_gained)} / -{len(enum_lost)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
