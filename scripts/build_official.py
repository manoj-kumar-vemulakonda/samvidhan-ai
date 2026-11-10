"""
Add official texts to the law library (data/laws/), straight from the Government and court websites:

  * the CPC's Orders and Rules (First Schedule)              -> CPC O. VII r. 11
  * the BNSS First Schedule (bailable / cognizable / court)   -> BNSS Sch. I (BNS s. 103)
  * the Limitation Act's Schedule (periods of limitation)     -> Limitation Act Art. 54
  * about 45 more central Acts that courts use daily (General Clauses, Court-fees, Gram Nyayalayas, ...)
  * court rules: Supreme Court Rules 2013 and Handbook, High Court rules, district-court / Gram Nyayalaya
    rules, State civil-courts and court-fees Acts

Every PDF is kept in data/raw/official/ and every provision remembers its page, so the app can open the
official PDF at the right page. Run after scripts/build_laws.py (which builds the first 101 Acts):

    .venv\\Scripts\\python.exe scripts\\build_official.py            # download what is missing, rebuild
    .venv\\Scripts\\python.exe scripts\\build_official.py --offline  # only use PDFs already downloaded
    .venv\\Scripts\\python.exe scripts\\build_official.py SCR2013    # just one book
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app import rulebook  # noqa: E402
from scripts import official  # noqa: E402
from scripts.official_books import BOOKS, COURTS  # noqa: E402

LAWS = PROJECT / "data" / "laws"
INDIA_CODE = "India Code (indiacode.gov.in), official text published by the Legislative Department"
MAX_OCR_PAGES = 220     # a bigger scanned book takes hours of OCR: build it on its own (python scripts/build_official.py CODE)


def as_on(path: Path) -> str:
    """'As on the 1st July, 2026' from the first page of an India Code PDF."""
    import pymupdf

    try:
        text = pymupdf.open(str(path))[0].get_text()
    except Exception:
        return ""
    match = re.search(r"\[?\s*As on (?:the\s+)?([^\]\n]{4,40})\]?", text)
    return match.group(1).strip() if match else ""


def act_record(book: dict, provisions: list[dict], path: Path | None, how: str) -> dict:
    info = official.source_info(book["code"]) if path else {}
    source = book.get("source") or (INDIA_CODE if book.get("indiacode") or book.get("uuid") else book.get("publisher", ""))
    return {
        "code": book["code"], "cite": book["cite"], "name": book["name"], "short": book.get("short") or book["cite"],
        "year": book.get("year") or 0, "category": book["category"], "status": book.get("status", "in_force"),
        "replaced_by": book.get("replaced_by"), "replaces": book.get("replaces"), "note": book.get("note", ""),
        "aliases": sorted(set(a.lower() for a in book.get("aliases", []))), "source": source,
        "kind": book.get("kind", "act"), "court": book.get("court", ""), "jurisdiction": book.get("jurisdiction", "Central"),
        "pdf": path.name if path else "", "url": info.get("page") or info.get("url") or book.get("url", ""),
        "as_on": (as_on(path) if path and (book.get("indiacode") or book.get("uuid")) else book.get("as_on", "")),
        "parsed_as": how, "sections": provisions,
    }


def to_sections(provisions: list[dict], pdf: str = "", official_part: bool = False) -> list[dict]:
    out = []
    for p in provisions:
        out.append({
            "number": p["number"], "ref": p["ref"], "kind": p.get("kind", "section"), "title": p["title"][:200],
            "chapter": p.get("chapter", ""), "chapter_title": p.get("chapter_title", ""), "text": p["text"],
            "status": p.get("status", "in_force"), "page": p.get("page"),
            **({"pdf": pdf} if pdf else {}), **({"official_part": True} if official_part else {}),
        })
    return out


# ---------------------------------------------------------------------------
# Schedules added to Acts that are already in the library
# ---------------------------------------------------------------------------
def cpc_orders(path: Path) -> list[dict]:
    lines = rulebook.pdf_lines(path, ocr="never")
    orders = rulebook.parse(lines, numbering="orders", bold=True, only_schedule=True)
    for p in orders:
        p["kind"] = "rule"
        p["chapter_title"] = p["chapter_title"].title() if p["chapter_title"].isupper() else p["chapter_title"]
    return orders


def limitation_articles(path: Path) -> list[dict]:
    import pymupdf

    pages = pymupdf.open(str(path)).page_count
    start = next(i for i in range(pages) if "PERIODS OF LIMITATION" in pymupdf.open(str(path))[i].get_text()) + 1
    rows = rulebook.table_rows(path, start, pages, columns=[70, 270, 345], row_start=r"^\d{1,3}\.$|^\d{1,3}\.\S",
                               start_text=r"PERIODS OF LIMITATION", stop_text=r"STATEMENT OF OBJECTS",
                               group_pattern=r"^(PART [IVX]+\.?\s*[—–-]|[A-Z]+ DIVISION\s*[—–-])",
                               major_pattern=r"DIVISION")
    articles: list[dict] = []
    for row in rows:
        description, period, start_from = row["cells"]
        match = re.match(r"^\[?(\d{1,3})\.\s*(.*)$", description, re.S)
        if not match:
            continue
        number = int(match.group(1))
        if articles and number != int(articles[-1]["number"][3:]) + 1:
            articles[-1]["text"] += "\n" + description
            continue
        words = re.sub(r"\s+", " ", match.group(2)).strip()
        group = row["group"].replace("—", " — ").replace("  ", " ")
        division = group.split(" / ")[0].title() if group else ""
        articles.append({
            "number": f"ART{number}", "ref": f"Art. {number}", "kind": "article",
            "title": rulebook._first_words(words, 110),
            "chapter": "Schedule", "chapter_title": group.title(),
            "text": (f"Article {number} of the Schedule ({division}).\nDescription: {words}\n"
                     f"Period of limitation: {period.strip()}\nTime from which period begins to run: {start_from.strip()}"),
            "page": row["page"], "status": "omitted" if re.search(r"omitted|repealed", words[:40], re.I) else "in_force",
        })
    return articles


BAILABLE = re.compile(r"non-?\s?bailable|bailable|according as", re.I)


def bnss_first_schedule(path: Path) -> list[dict]:
    import pymupdf

    doc = pymupdf.open(str(path))
    first = next(i for i in range(150, doc.page_count) if "CLASSIFICATION OF OFFENCES" in doc[i].get_text()) + 1
    rows = rulebook.table_rows(path, first, doc.page_count, columns=[70, 105, 222, 291, 369, 456],
                               row_start=r"^\d{1,3}(\(\w{1,3}\))*$",
                               start_text=r"OFFENCES UNDER THE BHARATIYA NYAYA SANHITA",
                               stop_text=r"^II\.?\s*[—–-]+\s*CLASSIFICATION OF OFFENCES AGAINST OTHER LAWS",
                               header_digits=True)
    entries: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        section, offence, punishment, cognizable, bailable, court = (re.sub(r"\s+", " ", c).strip() for c in row["cells"])
        section = re.sub(r"\s+", "", section)
        if not re.match(r"^\d", section) or section in seen:
            continue
        seen.add(section)
        entries.append({
            "number": f"FS_{section}", "ref": f"Sch. I (BNS s. {section})", "kind": "entry",
            "title": rulebook._first_words(offence, 110) or f"BNS s. {section}",
            "chapter": "First Schedule", "chapter_title": "Classification of offences - I. Offences under the Bharatiya Nyaya Sanhita",
            "text": (f"First Schedule, classification of the offence under BNS section {section}.\n"
                     f"Offence: {offence}\nPunishment: {punishment}\nCognizable or non-cognizable: {cognizable}\n"
                     f"Bailable or non-bailable: {bailable}\nBy what court triable: {court}"),
            "page": row["page"], "status": "in_force",
        })
    # Part II - offences under other laws (by length of punishment)
    part_two = next((i for i in range(first - 1, doc.page_count) if "AGAINST OTHER LAWS" in doc[i].get_text()), None)
    if part_two is not None:
        text = doc[part_two].get_text()
        text = text[text.find("II.") if "II." in text else 0:]
        text = text.split("THE SECOND SCHEDULE")[0]
        entries.append({
            "number": "FS_OTHER", "ref": "Sch. I Part II", "kind": "entry",
            "title": "Classification of offences against other laws",
            "chapter": "First Schedule", "chapter_title": "Classification of offences - II. Offences against other laws",
            "text": rulebook.join_text([t for t in text.splitlines() if t.strip() and t.strip() != "IndiaCode"]),
            "page": part_two + 1, "status": "in_force",
        })
    return entries


def augment(code: str, parts: list[dict], pdf_name: str, label: str) -> int:
    """Replace the official parts of an existing Act's JSON (idempotent)."""
    path = LAWS / "acts" / f"{code}.json"
    act = json.loads(path.read_text(encoding="utf-8"))
    act["sections"] = [s for s in act["sections"] if not s.get("official_part")] + to_sections(parts, pdf_name, True)
    extra = [p for p in act.get("official_parts", []) if p.get("label") != label]
    info = official.source_info(code if (official.RAW / f"{code}.pdf").exists() else pdf_name.rsplit(".", 1)[0])
    extra.append({"label": label, "pdf": pdf_name, "url": info.get("page") or info.get("url", ""), "count": len(parts),
                  "source": INDIA_CODE, "as_on": as_on(official.RAW / pdf_name)})
    act["official_parts"] = extra
    path.write_text(json.dumps(act, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(parts)


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------
def delhi_rules_orders_parts() -> list[dict]:
    """Every chapter PDF of the Delhi 'High Court Rules and Orders' (Volumes I-V) from the court's website."""
    from urllib.parse import urljoin

    parts, seen = [], set()
    for page in range(0, 20):
        response = official._get(f"https://delhihighcourt.nic.in/web/court-rules?page={page}")
        rows = re.findall(r"<tr\b.*?</tr>", response.text, re.S | re.I)
        found = 0
        for row in rows:
            link = re.search(r'href=["\']([^"\']+\.pdf)["\']', row)
            text = re.sub(r"<[^>]+>|&nbsp;|\s+", " ", row).strip()
            match = re.search(r"Volume\s+([IVX]+)\s+chapter\s+(\d+)\s*:\s*(.*?)\s+Download", text, re.I)
            if not link or not match:
                continue
            url = urljoin(str(response.url), link.group(1))
            if url in seen:
                continue
            seen.add(url)
            found += 1
            parts.append({"url": url, "volume": match.group(1).upper(), "chapter": int(match.group(2)),
                          "label": f"Vol. {match.group(1).upper()} Ch. {match.group(2)}",
                          "title": match.group(3).strip().capitalize()})
        if not found:
            break
    if not parts:
        raise RuntimeError("no chapters found on delhihighcourt.nic.in/web/court-rules")
    return parts


def build_parts_book(book: dict, offline: bool) -> tuple[list[dict], Path, str]:
    """A book published as many PDFs (chapters / notifications): each part parsed on its own, ids prefixed."""
    code = book["code"]
    meta = official.source_info(code)
    if offline or (meta.get("parts") and (official.RAW / f"{code}.pdf").exists()):
        if not meta.get("parts"):
            raise RuntimeError("not downloaded yet (run without --offline)")
        parts = meta["parts"]
        path = official.RAW / f"{code}.pdf"
    else:
        if book.get("delhi_rules_orders"):
            wanted = delhi_rules_orders_parts()
        elif book.get("kerala_parts"):
            wanted = [{"kerala_file": f, "label": f"Amendment {i}", "title": ""} for i, f in enumerate(book["kerala_parts"], 1)]
        else:
            wanted = [part if isinstance(part, dict) else {"url": part, "label": f"Notification {i}", "title": ""}
                      for i, part in enumerate(book["parts"], 1)]
        path, parts = official.fetch_parts(code, wanted)
    provisions: list[dict] = []
    for i, part in enumerate(parts, 1):
        lines = rulebook.pdf_lines(part["file"], ocr=book.get("ocr", "auto"), cache=str(part["file"])[:-4] + ".ocr.json")
        if book.get("pages"):
            found, how = rulebook.page_provisions(lines), "pages"
        else:
            found, how = rulebook.parse_or_pages(lines, numbering=book.get("numbering", "rules"))
        prefix = (f"V{part['volume']}C{part['chapter']}" if part.get("volume") else part.get("prefix") or f"N{i}")
        same_chapter = f"Ch. {rulebook.int_to_roman(part['chapter'])} " if part.get("chapter") else None
        for p in found:
            p["number"] = f"{prefix}{p['number']}"
            ref = p["ref"]
            if same_chapter and ref.startswith(same_chapter):       # "Vol. I Ch. 1 Ch. I r. 2" -> "Vol. I Ch. 1 r. 2"
                ref = ref[len(same_chapter):]
            p["ref"] = f"{part['label']} {ref}"
            p["page"] = (p.get("page") or 1) + part["first_page"] - 1
            p["chapter"] = part["label"]
            p["chapter_title"] = part.get("title") or p.get("chapter_title", "")
            provisions.append(p)
    return provisions, path, "parts"


def build_book(book: dict, offline: bool, wanted_alone: bool = False) -> dict:
    code = book["code"]
    if book.get("parts") or book.get("delhi_rules_orders") or book.get("kerala_parts"):
        provisions, path, how = build_parts_book(book, offline)
        return act_record(book, to_sections(provisions), path, how)
    path = official.RAW / f"{code}.pdf"
    if not path.exists():
        if offline:
            raise RuntimeError("not downloaded yet (run without --offline)")
        path = official.fetch(code, indiacode=book.get("indiacode"), url=book.get("url"), state=book.get("state", "CENTRAL"),
                              uuid=book.get("uuid"), pick=book.get("pick"), page_url=book.get("page_url"),
                              kerala_file=book.get("kerala_file"))
    if book.get("ocr", "auto") == "auto" and not (official.RAW / f"{code}.ocr.json").exists() and not wanted_alone:
        import pymupdf

        with pymupdf.open(str(path)) as doc:
            scanned = sum(1 for page in doc if rulebook.needs_ocr(page.get_text()))
        if scanned > MAX_OCR_PAGES:
            raise RuntimeError(f"{scanned} scanned pages need OCR (hours on a laptop) - deferred; run "
                               f"'scripts/build_official.py {code}' on its own, or add it from the Library page")
    lines = rulebook.pdf_lines(path, ocr=book.get("ocr", "auto"), first_page=book.get("first_page", 1),
                               last_page=book.get("last_page"), cache=official.RAW / f"{code}.ocr.json")
    numbering = book.get("numbering", "sections")
    options = {k: book[k] for k in ("bold", "start", "stop", "schedules", "title_headings") if k in book}
    if book.get("pages"):
        provisions, how = rulebook.page_provisions(lines), "pages"
    else:
        provisions, how = rulebook.parse_or_pages(lines, numbering=numbering, only=book.get("kind", "act") == "act", **options)
    if not provisions:
        raise RuntimeError("no text could be read")
    return act_record(book, to_sections(provisions), path, how)


def write_index(records: dict[str, dict]) -> None:
    """Merge the official books into data/laws/index.json (entries for other Acts are kept)."""
    index_path = LAWS / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    by_code = {a["code"]: a for a in index["acts"]}
    for code, record in records.items():
        entry = {k: v for k, v in record.items() if k != "sections"}
        entry["sections"] = len(record["sections"])
        by_code[code] = entry
    for code in list(by_code):           # an Act whose JSON file is gone is dropped from the index
        if not (LAWS / "acts" / f"{code}.json").exists():
            del by_code[code]
    for code, entry in by_code.items():  # refresh counts of augmented Acts
        data = json.loads((LAWS / "acts" / f"{code}.json").read_text(encoding="utf-8"))
        entry["sections"] = len(data["sections"])
        if data.get("official_parts"):
            entry["official_parts"] = data["official_parts"]
    index["acts"] = sorted(by_code.values(), key=lambda a: (a["category"], a["name"]))
    index["official_source"] = INDIA_CODE
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")


def write_courts(results: dict[str, str]) -> None:
    """data/laws/courts.json - for each court: the rule books in the library and the official site."""
    index = json.loads((LAWS / "index.json").read_text(encoding="utf-8"))
    by_court: dict[str, list] = {}
    for act in index["acts"]:
        if act.get("court"):
            for court in [c.strip() for c in act["court"].split(";")]:
                by_court.setdefault(court, []).append({"code": act["code"], "name": act["name"], "kind": act.get("kind", "act"),
                                                       "count": act["sections"]})
    courts = []
    for court in COURTS:
        entry = dict(court)
        entry["books"] = by_court.get(court["name"], [])
        covered = {c for b in BOOKS if b.get("covers") and court["name"] in b.get("court", "")
                   and (LAWS / "acts" / f"{b['code']}.json").exists() for c in b["covers"]}
        entry["missing"] = [m for m in court.get("main_rules", []) if m not in covered]
        courts.append(entry)
    (LAWS / "courts.json").write_text(json.dumps({"courts": courts, "updated": time.strftime("%Y-%m-%d"),
                                                  "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Add official Acts, schedules and court rules to the library.")
    parser.add_argument("codes", nargs="*", help="only these books (default: all)")
    parser.add_argument("--offline", action="store_true", help="use only PDFs already in data/raw/official")
    parser.add_argument("--no-index", action="store_true", help="only write the books (another run writes the index)")
    args = parser.parse_args()
    wanted = set(c.upper() for c in args.codes)
    results: dict[str, str] = {}
    records: dict[str, dict] = {}

    # 1. Schedules of Acts already in the library
    schedule_jobs = [
        ("CPC", "The Code of Civil Procedure, 1908", "First Schedule - Orders and Rules", cpc_orders),
        ("LIMITATION", "The Limitation Act, 1963", "The Schedule - periods of limitation", limitation_articles),
        ("BNSS", "The Bharatiya Nagarik Suraksha Sanhita, 2023", "First Schedule - classification of offences", bnss_first_schedule),
    ]
    for code, title, label, parse in schedule_jobs:
        if wanted and code not in wanted:
            continue
        started = time.time()
        try:
            path = official.RAW / f"{code}.pdf"
            if not path.exists():
                if args.offline:
                    raise RuntimeError("not downloaded yet")
                path = official.fetch(code, indiacode=title)
            count = augment(code, parse(path), path.name, label)
            results[code] = f"ok: {label}, {count} provisions"
        except Exception as error:
            results[code] = f"FAILED: {error}"
            traceback.print_exc()
        print(f"{code:16} {results[code]}  ({time.time() - started:.0f}s)")

    # 2. Whole books (Acts, rules, handbooks)
    for book in BOOKS:
        if wanted and book["code"] not in wanted:
            continue
        started = time.time()
        try:
            record = build_book(book, args.offline, wanted_alone=len(wanted) == 1)
            (LAWS / "acts" / f"{book['code']}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            records[book["code"]] = record
            results[book["code"]] = f"ok: {len(record['sections'])} provisions ({record['parsed_as']})"
        except Exception as error:
            results[book["code"]] = f"FAILED: {error}"
        print(f"{book['code']:16} {results[book['code']]}  ({time.time() - started:.0f}s)")

    if not args.no_index:
        write_index(records)
        write_courts(results)
    failed = [c for c, r in results.items() if r.startswith("FAILED")]
    print(f"\nDone: {len(results) - len(failed)} ok, {len(failed)} failed{': ' + ', '.join(failed) if failed else ''}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
