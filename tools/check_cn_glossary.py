"""Check the Chinese layer against the manual database.

Two things can silently rot:

* a curated note keyed to a section/keyword that does not exist (the note would
  simply never show up);
* a glossary term that appears nowhere in the manual (it would never highlight).

Usage::

    python tools/check_cn_glossary.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from cp2kbuild import cn_glossary  # noqa: E402
from cp2kbuild.manualdb import load_db  # noqa: E402


def main() -> int:
    db = load_db()
    if db is None:
        print("no manual database")
        return 2

    problems: list[str] = []

    print(f"中文说明 {len(cn_glossary.KEYWORDS)} 条，术语 {len(cn_glossary.TERMS)} 条")

    missing_section: list[str] = []
    missing_keyword: list[str] = []
    for key in cn_glossary.KEYWORDS:
        section, keyword = cn_glossary.split_key(key)
        if not keyword:                       # section level note
            if not db.has_section(section):
                missing_section.append(key)
            continue
        if not db.has_section(section):
            missing_section.append(f"{key}")
            continue
        # the key may name either a keyword or a print-key section
        # (&MOMENTS, &FORCES, &XC_FUNCTIONAL are sections)
        if not db.has_keyword(section, keyword) and not db.has_section(key):
            missing_keyword.append(f"{key}")

    print(f"\n引用的 section 不存在（{len(missing_section)}）：")
    for item in missing_section:
        print("   ", item)
    print(f"\n引用的关键字不存在（{len(missing_keyword)}）：")
    for item in missing_keyword:
        print("   ", item)
    problems += missing_section + missing_keyword

    # glossary usefulness: does each term occur somewhere in the manual?
    # Scanning record by record instead of building one giant string: joining the
    # whole manual (26 MB of descriptions) and lower-casing it can run out of
    # memory, and this finds exactly the same thing.
    print("\n扫描手册文本以检查术语是否真的会出现……")
    pending = {term: term.lower() for term in cn_glossary.TERMS}
    found: set[str] = set()

    def scan(text: str | None) -> None:
        if not text or not pending:
            return
        lowered = text.lower()
        for term, needle in list(pending.items()):
            if needle in lowered:
                found.add(term)
                del pending[term]

    for record in db._keywords.values():
        scan(record.get("description"))
        scan(record.get("usage"))
        if not pending:
            break
    for record in db._sections.values():
        scan(record.get("description"))
        if not pending:
            break

    unused = list(pending)
    print(f"手册中找不到的术语（{len(unused)}/{len(cn_glossary.TERMS)}）：")
    for term in unused:
        print("   ", term)
    # a handful of misses is fine (they are still useful while reading), but a
    # large fraction would mean the glossary was written blind
    if len(unused) > len(cn_glossary.TERMS) * 0.25:
        problems.append(f"too many unused glossary terms: {len(unused)}")

    # lookup must behave
    checks = [
        ("FORCE_EVAL/DFT/MGRID", "CUTOFF", True),
        ("MOTION/MD", "ENSEMBLE", True),
        (None, "CUTOFF", True),          # bare name with a single definition
        ("MOTION/GEO_OPT", "OPTIMIZER", True),
        ("NOPE/NOPE", "NOPE", False),
    ]
    print("\n查找行为：")
    for section, keyword, expected in checks:
        found = cn_glossary.lookup(section, keyword) is not None
        flag = "ok  " if found == expected else "FAIL"
        if found != expected:
            problems.append(f"lookup({section!r},{keyword!r}) -> {found}")
        print(f"  {flag} lookup({section}, {keyword}) -> {found}")

    print("\n中文搜索：")
    for query, expect_in in [("截断能", "CUTOFF"), ("系综", "ENSEMBLE"),
                             ("赝势", "POTENTIAL"), ("过渡态", "BAND_TYPE"),
                             ("红外", "INTENSITIES")]:
        hits = cn_glossary.search(query)
        keys = [key for key, _ in hits]
        ok = any(expect_in in key for key in keys)
        print(f"  {'ok  ' if ok else 'FAIL'} 「{query}」 -> {len(hits)} 条，"
              f"含 {expect_in}: {ok}")
        if not ok:
            problems.append(f"chinese search {query!r} did not find {expect_in}")

    print("\n术语抽取示例：")
    info = db.keyword("FORCE_EVAL/DFT/MGRID", "CUTOFF")
    for term, cn in cn_glossary.terms_in(info.description or ""):
        print(f"  {term} → {cn}")

    print()
    if problems:
        print(f"{len(problems)} 个问题")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
