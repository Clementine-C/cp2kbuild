#!/usr/bin/env python
"""Build a machine-readable CP2K input-reference database.

Two sources are combined:

1. The CP2K manual (Sphinx) Input Reference, crawled page by page.  Every
   section page yields its description, subsection list and every keyword with
   type, default, unit, aliases, lone-keyword flag, usage hint, valid enum
   values, description, manual URL and the GitHub source location.

2. The CP2K Fortran sources (``src/input_cp2k_*.F``), scanned for
   ``section_create(..., repeats=.TRUE./.FALSE.)`` so that the database knows
   which sections may appear more than once.

Usage::

    python tools/build_db.py --branch cp2k-2025_1-branch \
        --src E:/desktop/cp2k/cp2k-2025.1/src \
        --out cp2kbuild/data/manual_db_2025_1.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import posixpath
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "cp2kbuild-db/1.0 (+https://manual.cp2k.org)"

RE_H1 = re.compile(r"<h1>([^<]+?)<a class=\"headerlink\"")
RE_FIRST_P = re.compile(r"</h1>\s*<p>(.*?)</p>", re.S)
RE_GH_SRC = re.compile(
    r"https://github\.com/cp2k/cp2k/blob/[^/]+/(src/[A-Za-z0-9_./-]+?)(?:#L(\d+))?\""
)
RE_SUBSEC = re.compile(
    r'<li class="toctree-l\d"><a class="reference internal" href="([^"]+)\.html">([^<]+)</a>'
)
RE_DT = re.compile(r'<dt class="sig sig-object py" id="(CP2K_INPUT(?:\.[A-Za-z0-9_-]+)*)">')
RE_TAG = re.compile(r"<[^>]+>")
RE_UL = re.compile(r'<ul class="simple">(.*?)</ul>', re.S)
RE_LI_ITEM = re.compile(r"<li>(.*?)</li>", re.S)
RE_CODE_VALUE = re.compile(
    r'<code[^>]*>\s*<span class="pre">(.*?)</span>\s*</code>', re.S
)
RE_P = re.compile(r"<p>(.*?)</p>", re.S)
RE_SECTION_LINK = re.compile(
    r'href="(CP2K_INPUT(?:/[A-Za-z0-9_-]+)*?)\.html"'
)
RE_TOCTREE = re.compile(r'class="toctree-wrapper compound"')
RE_HREF = re.compile(r'href="([^"#]+?)\.html"')
RE_SUBSECTION_CAPTION = re.compile(
    r'<span class="caption-text">Subsections</span>\s*</p>\s*<ul>(.*?)</ul>', re.S
)


def declared_subsections(raw: str) -> list[str]:
    """The names under the page's own ``Subsections`` heading.

    Only that caption block counts: every page also carries the manual's
    sidebar toctree, which would otherwise make every section list the whole
    input reference as its subsections.
    """
    match = RE_SUBSECTION_CAPTION.search(raw)
    if not match:
        return []
    names = re.findall(r'<li class="toctree-l\d"><a class="reference internal" '
                       r'href="[^"]*?([A-Za-z0-9_]+)\.html">([^<]+)</a>', match.group(1))
    out: list[str] = []
    for slug, label in names:
        name = strip_tags(label) or slug
        if name and name not in out:
            out.append(name)
    return out


def toctree_children(raw: str, current: str) -> list[str]:
    """Section paths reachable from a page's ``Subsections`` toctree."""
    out: list[str] = []
    base = posixpath.dirname(current)
    for match in RE_TOCTREE.finditer(raw):
        window = raw[match.end():match.end() + 40000]
        for marker in ("<section id=", "<h2"):
            stop = window.find(marker)
            if stop > 0:
                window = window[:stop]
        for href in RE_HREF.findall(window):
            if href.startswith(("http://", "https://")):
                continue
            resolved = posixpath.normpath(posixpath.join(base, href))
            if resolved.startswith("CP2K_INPUT") and ".." not in resolved:
                out.append(resolved)
    return out

LABELS = (
    "lone keyword:",
    "aliases:",
    "usage:",
    "valid values:",
    "mentions:",
    "note:",
    "merged section:",
    "deprecated:",
)


def strip_tags(value: str) -> str:
    """Turn a fragment of HTML into single-spaced plain text."""
    text = RE_TAG.sub(" ", value)
    text = html.unescape(text)
    text = text.replace("\uf0c1", " ")  # the pilcrow link glyph
    return re.sub(r"\s+", " ", text).strip()


def balanced_span(text: str, start: int, open_tag: str, close_tag: str
                  ) -> tuple[int, int] | None:
    """Span of the balanced ``open_tag ... close_tag`` block at/after ``start``.

    The manual nests ``<ul>`` inside ``<li>`` (enum values with their own
    sub-lists), so a non-greedy regex would cut the list short.
    """
    begin = text.find(open_tag, start)
    if begin < 0:
        return None
    depth = 0
    index = begin
    while index < len(text):
        if text.startswith(open_tag, index):
            depth += 1
            index += len(open_tag)
        elif text.startswith(close_tag, index):
            depth -= 1
            index += len(close_tag)
            if depth == 0:
                return begin, index
        else:
            index += 1
    return None


def top_level_items(block: str, tag: str = "li") -> list[str]:
    """Every top-level ``<li>...</li>`` of a list block, nesting preserved."""
    items: list[str] = []
    open_tag, close_tag = f"<{tag}", f"</{tag}>"
    index = 0
    while True:
        span = balanced_span(block, index, open_tag, close_tag)
        if span is None:
            break
        items.append(block[span[0]:span[1]])
        index = span[1]
    return items


def fetch(url: str, retries: int = 4, timeout: int = 90) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read().decode("utf-8", "ignore")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:  # noqa: PERF203
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")


# --------------------------------------------------------------------------- #
# keyword / section page parsing
# --------------------------------------------------------------------------- #
def split_type_default(spec: str) -> tuple[str | None, str | None, str | None]:
    """``'real = 2.80000000E+002 [Ry]'`` -> ``('real', '2.8E+002', 'Ry')``.

    The 2026 manuals annotate array keywords as ``real[ ]``; that must not end
    up as the type name, otherwise the type checks would stop matching.
    """
    if not spec:
        return None, None, None
    kind, _, default = spec.partition("=")
    kind = re.sub(r"\[\s*\]?\s*$", "", kind.strip()).strip() or None
    default = default.strip() or None
    unit = None
    if default:
        m = re.search(r"\[([^\]]*)\]\s*$", default)
        if m:
            unit = m.group(1).strip() or None
            default = default[: m.start()].strip() or None
    return kind, default, unit


def parse_dd(dd: str) -> dict:
    """Extract the structured fields from a keyword ``<dd>`` block."""
    out: dict = {
        "aliases": [],
        "lone_keyword": None,
        "usage": None,
        "valid_values": [],
        "mentions": [],
        "description": None,
        "deprecated": False,
    }

    marker = re.search(r"<strong>Valid values:</strong>", dd)
    if marker:
        span = balanced_span(dd, marker.end(), "<ul", "</ul>")
        if span:
            start, end = span
            block = dd[start:end]
            for item in top_level_items(block):
                code = RE_CODE_VALUE.search(item)
                if not code:
                    continue
                value = strip_tags(code.group(1))
                description = item[code.end():]
                # an entry may carry its own sub-list or a table of formulae
                nested = balanced_span(description, 0, "<ul", "</ul>")
                while nested:
                    description = description[:nested[0]] + " " + description[nested[1]:]
                    nested = balanced_span(description, 0, "<ul", "</ul>")
                description = re.sub(r"<table.*?</table>", " ", description, flags=re.S)
                description = re.sub(r"</?p>", " ", description)
                description = re.sub(r"</?li>", " ", description)
                out["valid_values"].append(
                    {"value": value, "description": strip_tags(description) or None}
                )
            dd = dd[:start] + " " + dd[end:]

    text = strip_tags(dd)

    m = re.search(r"Lone keyword:\s*([A-Za-z_]+)", text)
    if m:
        out["lone_keyword"] = m.group(1)

    m = re.search(r"Aliases:\s*([^\n]+?)(?=\s*(?:Usage:|Valid values:|Mentions:|$))", text)
    if m:
        out["aliases"] = [
            a.strip() for a in re.split(r"[,\s]+", m.group(1)) if a.strip()
        ]

    m = re.search(r"Usage:\s*(.+?)(?=\s*(?:Valid values:|Mentions:|Lone keyword:|Aliases:|Note:|$))", text)
    if m:
        out["usage"] = m.group(1).strip()

    for label, url in re.findall(
        r'<a class="reference (?:internal|external)" href="([^"]+)"[^>]*>(.*?)</a>', dd
    ):
        label_text = strip_tags(url)
        if label_text and "Edit on GitHub" not in label_text:
            out["mentions"].append({"title": label_text, "href": label})

    out["deprecated"] = bool(re.search(r"\bdeprecated\b", text, re.I))

    # description: paragraph(s) that are not one of the labelled fields
    for para in RE_P.findall(dd):
        if "<strong>" in para:
            continue
        plain = strip_tags(para)
        if not plain:
            continue
        low = plain.lower()
        if any(low.startswith(lbl) for lbl in LABELS):
            continue
        if re.fullmatch(r"\[Edit on GitHub\]?", plain):
            continue
        out["description"] = (
            plain if out["description"] is None else f"{out['description']} {plain}"
        )

    return out


def parse_section_page(path: str, raw: str, base_url: str) -> tuple[dict, list[dict]]:
    """Parse one manual section page into a section record plus keyword records."""
    name = path.split("/")[-1]

    m = RE_H1.search(raw)
    title = strip_tags(m.group(1)) if m else name

    section_desc = None
    m = RE_FIRST_P.search(raw)
    if m:
        section_desc = strip_tags(m.group(1)) or None

    gh = RE_GH_SRC.search(raw)
    source = f"{gh.group(1)}#L{gh.group(2)}" if gh and gh.group(2) else (gh.group(1) if gh else None)

    subsections = declared_subsections(raw)

    section = {
        "name": name,
        "path": path.split("/"),
        "title": title,
        "description": section_desc,
        "url": f"{base_url}{path}.html",
        "source": source,
        "subsections": subsections,
        "keywords": [],
        "repeatable": None,
    }

    keywords: list[dict] = []
    matches = list(RE_DT.finditer(raw))
    for index, match in enumerate(matches):
        kw_id = match.group(1)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        # never run past the end of the definition list
        stop = raw.find("</dl>", match.end())
        if 0 < stop < end:
            end = stop
        chunk = raw[match.start():end]

        dt_end = chunk.find("</dt>")
        dt = chunk[: dt_end if dt_end > 0 else 200]
        dd = chunk[dt_end + 5:] if dt_end > 0 else ""

        dt_text = strip_tags(dt)
        kw_name = kw_id.split(".")[-1]
        spec = dt_text
        if spec.startswith(kw_name):
            spec = spec[len(kw_name):]
        spec = spec.lstrip(": ").strip()
        kind, default, unit = split_type_default(spec)

        record = {
            "name": kw_name,
            "id": kw_id,
            "section": path,
            "type": kind,
            "default": default,
            "unit": unit,
            "url": f"{base_url}{path}.html#{kw_id}",
        }
        record.update(parse_dd(dd))
        keywords.append(record)
        section["keywords"].append(kw_name)

    return section, keywords


def crawl(branch: str, workers: int) -> tuple[dict, dict]:
    base_url = f"https://manual.cp2k.org/{branch}/"

    # breadth-first over the "Subsections" toctrees: the sidebar of the root
    # page is not complete (e.g. DFT/PRINT/BAND_STRUCTURE is missing from it)
    raw_pages: dict[str, str] = {}
    failures: dict[str, str] = {}
    frontier = ["CP2K_INPUT"]
    for round_number in range(1, 12):
        if not frontier:
            break
        print(f"[db] round {round_number}: fetching {len(frontier)} pages", flush=True)
        results: dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch, f"{base_url}{p}.html"): p for p in frontier}
            for future in concurrent.futures.as_completed(futures):
                page = futures[future]
                try:
                    results[page] = future.result()
                except Exception as exc:  # noqa: BLE001
                    failures[page] = str(exc)
                    results[page] = ""
        for page, raw in results.items():
            if raw:
                raw_pages[page] = raw
                failures.pop(page, None)
        discovered: set[str] = set()
        for page, raw in results.items():
            if not raw:
                continue
            for child in toctree_children(raw, page):
                if child not in raw_pages and child not in discovered:
                    discovered.add(child)
        frontier = sorted(discovered)

    # retry transient failures sequentially
    for attempt in range(3):
        if not failures:
            break
        pending = sorted(failures)
        failures = {}
        print(f"[db] retry pass {attempt + 1} for {len(pending)} pages", flush=True)
        for page in pending:
            try:
                raw_pages[page] = fetch(f"{base_url}{page}.html")
            except Exception as exc:  # noqa: BLE001
                failures[page] = str(exc)

    print(f"[db] {len(raw_pages)} section pages fetched, {len(failures)} failed", flush=True)
    for page, message in sorted(failures.items()):
        print(f"    FAILED {page}: {message[:120]}", flush=True)

    sections: dict[str, dict] = {}
    keywords: dict[str, dict] = {}
    for page in sorted(raw_pages):
        section, kws = parse_section_page(page, raw_pages[page], base_url)
        sections[page] = section
        for kw in kws:
            keywords[kw["id"]] = kw
    print(f"[db] parsed {len(keywords)} keywords from {len(sections)} pages", flush=True)

    # root page itself (CP2K_INPUT) has no keywords of its own
    if "CP2K_INPUT" not in sections:
        sections["CP2K_INPUT"] = {
            "name": "CP2K_INPUT",
            "path": ["CP2K_INPUT"],
            "title": "CP2K_INPUT",
            "description": "root of the CP2K input tree",
            "url": f"{base_url}CP2K_INPUT.html",
            "source": None,
            "subsections": sorted(
                p.split("/")[1] for p in sections if p.count("/") == 1 and p != "CP2K_INPUT"
            ),
            "keywords": [],
            "repeatable": False,
        }

    # merge the page-derived and tree-derived subsection lists: neither is
    # complete on its own (deep print sections are absent from the root sidebar)
    for path, section in sections.items():
        depth = path.count("/")
        tree_children = {
            other.split("/")[-1]
            for other in sections
            if other != path and other.startswith(path + "/") and other.count("/") == depth + 1
        }
        merged = sorted(set(section.get("subsections") or []) | tree_children)
        section["subsections"] = merged

    if failures:
        print(f"[db] WARNING {len(failures)} pages never fetched:", flush=True)
        for line in sorted(failures)[:20]:
            print("   ", line, flush=True)

    return sections, keywords


# --------------------------------------------------------------------------- #
# repeatability from the Fortran sources
# --------------------------------------------------------------------------- #
def _strip_fortran_comment(line: str) -> str:
    """Drop a trailing ``!`` comment, ignoring ``!`` inside a string literal.

    Fortran descriptions are full of ``k!`` (factorials) and ``!=``; splitting on
    the first ``!`` truncated the statement, which silently lost the
    ``repeats=.TRUE.`` that came a few continuation lines later.
    """
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char == "!":
            return line[:index]
    return line


def _logical_statements(path: Path):
    """Yield ``(line_number, statement)`` with comments removed and ``&`` joined."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    buffer = ""
    buffer_line = 0
    for number, line in enumerate(text.splitlines(), start=1):
        code = _strip_fortran_comment(line)
        if not code:
            continue
        stripped = code.rstrip()
        if not buffer:
            buffer_line = number
        if stripped.endswith("&"):
            buffer += stripped[:-1] + " "
            continue
        buffer += stripped
        yield buffer_line, buffer
        buffer = ""
    if buffer:
        yield buffer_line, buffer


def refresh_sections(payload: dict, src: Path) -> int:
    """Re-apply the Fortran scan to an existing database; no crawling needed.

    Used by ``--keywords-only`` so a fix to the scan can be rolled into the
    shipped JSON files without re-downloading the manual.
    """
    repeats = fortran_sections(src)
    matched = 0
    for section in payload["sections"].values():
        info = repeats.get(section["name"])
        description = (section.get("description") or "").lower()
        if info:
            section["repeatable"] = info["repeatable"]
            section["repeat_source"] = info["source"]
            matched += 1
        elif "can be repeated" in description and not section.get("repeatable"):
            # the manual page says so in prose
            section["repeatable"] = True
            section["repeat_source"] = "manual caption"
    return matched


def _fortran_files(src: Path):
    """Every Fortran source that may define input sections or keywords."""
    if not src.is_dir():
        return []
    return sorted(src.rglob("*.F"))


def fortran_keyword_names(src: Path) -> list[str]:
    """Every keyword name declared in the Fortran input definitions.

    Guards against manual gaps: ``IONS+CENTERS`` is a real keyword of
    ``&WANNIER_CENTERS`` that the manual page does not list.
    """
    names: set[str] = set()
    for path in _fortran_files(src):
        for _, statement in _logical_statements(path):
            if "keyword_create" not in statement.lower():
                continue
            match = re.search(r'name\s*=\s*"([^"]+)"', statement)
            if match:
                names.add(match.group(1).upper())
    print(f"[db] {len(names)} keyword names collected from Fortran", flush=True)
    return sorted(names)


def fortran_keyword_repeats(src: Path) -> dict[str, bool]:
    """Keyword names declared with ``repeats=.TRUE.`` in the Fortran sources.

    ``SPECIAL_POINT`` and ``KPOINT`` are the obvious members; without this the
    linter would flag legitimate repeated lines as duplicates.
    """
    result: dict[str, bool] = {}
    if not src.is_dir():
        return result
    for path in _fortran_files(src):
        for _, statement in _logical_statements(path):
            if "keyword_create" not in statement.lower():
                continue
            name = re.search(r'name\s*=\s*"([^"]+)"', statement)
            if not name:
                continue
            repeats = re.search(r"repeats\s*=\s*\.(TRUE|FALSE)\.", statement, re.I)
            if repeats and repeats.group(1).upper() == "TRUE":
                result[name.group(1).upper()] = True
    print(f"[db] {len(result)} keywords declare repeats=.TRUE.", flush=True)
    return result


def fortran_sections(src: Path) -> dict[str, dict]:
    """Map section name -> {repeatable, source_file, source_line}."""
    result: dict[str, dict] = {}
    if not src.is_dir():
        print(f"[db] src dir not found, skipping repeats: {src}", flush=True)
        return result

    for path in _fortran_files(src):
        for number, statement in _logical_statements(path):
            lowered = statement.lower()
            if "section_create" not in lowered:
                continue
            m = re.search(r'name\s*=\s*"([^"]+)"', statement)
            if not m:
                continue
            name = m.group(1)
            repeats = None
            r = re.search(r"repeats\s*=\s*\.(TRUE|FALSE)\.", statement, re.I)
            if r:
                repeats = r.group(1).upper() == "TRUE"
            elif "cp_print_key_section_create" in lowered and \
                    "can be repeated" in lowered:
                # print keys say it in prose instead of passing repeats=
                repeats = True
            entry = result.setdefault(
                name, {"repeatable": bool(repeats), "source": f"{path.name}#L{number}"}
            )
            if repeats:
                entry["repeatable"] = True
                entry["source"] = f"{path.name}#L{number}"
    print(f"[db] {len(result)} section definitions scanned from Fortran", flush=True)
    return result


def fetch_dynamic_subsections(payload: dict, base_url: str,
                              workers: int = 4) -> tuple[dict, int]:
    """Harvest subsections the manual renders as anchors instead of pages.

    ``&XC_FUNCTIONAL`` is the important case: its ~150 built-in functionals are
    written as ``&PBE``/``&BECKE97`` blocks but have no page of their own, so the
    page-level crawl cannot see them.
    """
    targets = [path for path in payload["sections"]
               if path.split("/")[-1].endswith("XC_FUNCTIONAL")]
    noise = {"section-parameters", "keywords", "built-in-functionals",
             "subsection-parameters", "subsections"}
    out: dict[str, list[str]] = {}
    patched = 0

    def harvest(path: str) -> tuple[str, list[str]]:
        raw = fetch(f"{base_url}{path}.html")
        # Sphinx lower-cases the doc path and turns both / and _ into -
        slug = re.sub(r"[/_]", "-", path).lower()
        names: set[str] = set()
        for match in re.finditer(r'href="#' + re.escape(slug) + r'-([a-z0-9-]+)"', raw):
            token = match.group(1)
            if token in noise:
                continue
            names.add(token.upper().replace("-", "_"))
        return path, sorted(names)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for path, names in pool.map(harvest, targets):
            if not names:
                continue
            out[path] = names
            keyword_id = f"{path}.SECTION_PARAMETERS".replace("/", ".")
            keyword = payload["keywords"].get(keyword_id)
            if keyword is not None:
                known = {v["value"].upper() for v in keyword.get("valid_values", [])}
                for name in names:
                    if name not in known:
                        keyword.setdefault("valid_values", []).append(
                            {"value": name, "description": "built-in functional"})
                        patched += 1
    print(f"[db] dynamic subsections harvested for {len(out)} pages "
          f"({patched} enum values added)", flush=True)
    return out, patched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", default="cp2k-2025_1-branch")
    parser.add_argument("--src", default=None, help="path to the CP2K src/ directory")
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--keywords-only", action="store_true",
                        help="do not crawl; only refresh keyword/section repeatability "
                             "from --src into the existing --out database")
    parser.add_argument("--dynamic-lists", action="store_true",
                        help="do not crawl; only refresh the dynamically rendered "
                             "subsection lists (XC functionals) in the existing database")
    args = parser.parse_args()

    if args.dynamic_lists:
        out = Path(args.out)
        payload = json.loads(out.read_text(encoding="utf-8"))
        base_url = f"https://manual.cp2k.org/{args.branch}/"
        extra, _ = fetch_dynamic_subsections(payload, base_url, args.workers)
        payload["extra_subsections"] = extra
        out.write_text(json.dumps(payload, indent=1, sort_keys=True,
                                  ensure_ascii=False), encoding="utf-8")
        print(f"[db] rewrote {out}", flush=True)
        return 0

    if args.keywords_only:
        out = Path(args.out)
        payload = json.loads(out.read_text(encoding="utf-8"))
        src = Path(args.src) if args.src else None
        if src is None:
            parser.error("--keywords-only needs --src")
        payload["repeatable_keywords"] = fortran_keyword_repeats(src)
        payload["keyword_names"] = fortran_keyword_names(src)
        repeats = fortran_sections(src)
        payload["section_names"] = sorted(repeats)
        matched = refresh_sections(payload, src)
        print(f"[db] refreshed {matched} section repeatability flags", flush=True)
        out.write_text(json.dumps(payload, indent=1, sort_keys=True,
                                  ensure_ascii=False), encoding="utf-8")
        print(f"[db] rewrote {out}", flush=True)
        return 0

    sections, keywords = crawl(args.branch, args.workers)

    repeats: dict[str, dict] = {}
    if args.src:
        repeats = fortran_sections(Path(args.src))

    matched = refresh_sections(payload={"sections": sections}, src=Path(args.src)) \
        if args.src else 0
    for section in sections.values():
        if section.get("repeatable"):
            continue
        # sections that wrap a repeated keyword (KIND, COORD, KPOINT, ...) are
        # repeatable in practice even when the flag is not found
        if section["name"] in {
            "KIND", "COORD", "KPOINT", "KPOINT_SET", "SPECIAL_POINT", "COLVAR",
            "VELOCITY", "MULTIPOLES", "SHELL_COORD", "CORE_COORD", "JOB",
            "FRAGMENT", "REPLICA", "MM_KIND", "QM_KIND", "LINK", "MM_KIND",
            "RESTRAINT", "EACH", "VARIABLE", "COLLECTIVE", "EACH",
        }:
            section["repeatable"] = True
    print(f"[db] repeatability matched for {matched} sections", flush=True)

    payload = {
        "schema": "cp2kbuild/manual-db/1",
        "manual": f"https://manual.cp2k.org/{args.branch}/",
        "branch": args.branch,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "section_count": len(sections),
        "keyword_count": len(keywords),
        "repeatable_keywords": fortran_keyword_repeats(Path(args.src)) if args.src else {},
        "keyword_names": fortran_keyword_names(Path(args.src)) if args.src else [],
        "section_names": sorted(fortran_sections(Path(args.src))) if args.src else [],
        "sections": sections,
        "keywords": keywords,
    }
    extra, _ = fetch_dynamic_subsections(
        payload, f"https://manual.cp2k.org/{args.branch}/", workers=min(args.workers, 6))
    payload["extra_subsections"] = extra

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    size_mb = out.stat().st_size / 1e6
    print(f"[db] wrote {out} ({size_mb:.1f} MB): "
          f"{len(sections)} sections, {len(keywords)} keywords", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
