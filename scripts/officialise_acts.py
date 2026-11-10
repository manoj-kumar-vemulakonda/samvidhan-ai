"""
Replace an Act's open-dataset text with the official India Code text - only when the official PDF is read well.

    .venv\\Scripts\\python.exe scripts\\officialise_acts.py --check          compare only, change nothing
    .venv\\Scripts\\python.exe scripts\\officialise_acts.py                  the Acts lawyers use most (PRIORITY)
    .venv\\Scripts\\python.exe scripts\\officialise_acts.py NIA HMA          just these
    .venv\\Scripts\\python.exe scripts\\officialise_acts.py --all            every Act still from the dataset

Why: the first 101 Acts came from an open dataset (scripts/build_laws.py). Its text is mostly right, but the model
test found a wrong amendment footnote that two of three AI models repeated. India Code's PDFs are the official text,
with correct footnotes ("Subs. by Act 55 of 2002, s. 7 ...") and page numbers, so a citation can open the PDF page.

For each Act: download the PDF once (data/raw/official/<CODE>.pdf), read its sections, and compare with the text
the library has now. The Act is replaced only if
  * at least 98% of its section numbers are found (citations, the IPC <-> BNS table and the topic hints use them);
    the few not found keep their old text, marked "from_dataset";
  * at least 85% of the section titles agree (otherwise the PDF was probably misread);
  * there are not many extra "sections" (a sign of misreading).
Schedules added before (CPC Orders, Limitation Articles, BNSS First Schedule) are kept as they are.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from app import rulebook  # noqa: E402
from scripts import official  # noqa: E402
from scripts.build_official import INDIA_CODE, LAWS, as_on, to_sections, write_index  # noqa: E402

DATASET = "Open dataset"
# The Acts lawyers cite most, first
PRIORITY = ["BNS", "BNSS", "BSA", "IPC", "CRPC", "IEA", "CPC", "NIA", "LIMITATION", "CONTRACT", "TPA", "SRA", "HMA",
            "DV", "CONSUMER", "ARBITRATION", "POCSO", "NDPS", "SCST", "MVA", "HSA", "SUCCESSION", "RTI", "ITACT"]
MIN_COVERAGE, MIN_TITLES, MAX_EXTRA = 0.98, 0.85, 0.05
MAX_EMPTY, MIN_LENGTH, MAX_MUCH_SHORTER = 0.02, 0.7, 0.10   # the text must really be there, about as long as before
BODY_START = r"^\s*ACT\s+NO\.?\s*\d+\s+OF\s+\d{4}"           # where an India Code PDF's Act begins (after its contents)
MAX_SCANNED_PAGES = 30   # an Act whose PDF is a scan needs hours of OCR: left for scripts/build_official.py


def contents_numbers(lines) -> set[str]:
    """The section numbers in the PDF's own list of sections (before 'ACT NO. .. OF ....'). State amendments,
    which India Code prints under the sections they change, are never in this list."""
    body = next((i for i, line in enumerate(lines) if re.search(BODY_START, line.text, re.I)), None)
    if not body:
        return set()
    found = set()
    for line in lines[:body]:
        match = re.match(r"^\s*(\d{1,4}[A-Z]{0,4})\.\s+\S", line.text)
        if match:
            found.add(number_key(match.group(1)))
        if re.search(r"\bSCHEDULE\b", line.text, re.I):
            found.add("SCHEDULE")
    return found


def plain_title(name: str) -> str:
    """'The Negotiable Instruments Act, 1881' and 'Negotiable Instruments Act, 1881' are the same Act."""
    text = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower().replace("&", "and"))
    return re.sub(r"^the\s+", "", re.sub(r"\s+", " ", text).strip())


def norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (title or "").lower())).strip()


def titles_agree(a: str, b: str) -> bool:
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return False
    return a in b or b in a or SequenceMatcher(None, a, b).ratio() >= 0.75


REPEALED = re.compile(r"^\W*(repealed|omitted|rep\.|\[?\s*rep(?:ealed)?\b)", re.I)


def is_gone(section: dict) -> bool:
    """A section the Act no longer has ([Repealed.], [Omitted.]): the PDF may list a range of them on one line."""
    return section.get("status") == "omitted" or bool(REPEALED.match(section.get("title") or "")) \
        or (len(section.get("text") or "") < 160 and bool(re.search(r"\b(repealed|omitted)\b", section.get("text") or "", re.I)))


def number_key(number: str) -> str:
    """The dataset writes a few numbers as '21A.': the same section as '21A'."""
    return re.sub(r"[\s.]+$", "", str(number or "")).upper()


def compare(old: list[dict], new: list[dict]) -> dict:
    """How well the official reading matches the sections the library has now (repealed sections don't count)."""
    old_by = {number_key(s["number"]): s for s in old if not is_gone(s)}
    gone = [number_key(s["number"]) for s in old if is_gone(s)]
    new_by: dict[str, dict] = {}
    duplicates = 0
    for s in new:
        if number_key(s["number"]) in new_by:
            duplicates += 1
        new_by.setdefault(number_key(s["number"]), s)
    common = [n for n in old_by if n in new_by]
    agree = sum(1 for n in common if titles_agree(old_by[n]["title"], new_by[n]["title"]))
    missing = [n for n in old_by if n not in new_by] + [n for n in gone if n not in new_by]
    extra = [n for n in new_by if n not in old_by and n not in gone]
    coverage = len(common) / len(old_by) if old_by else 0.0
    titles = agree / len(common) if common else 0.0
    # When every known section is read with the right title, extra ones are real sections the dataset lacked
    # (e.g. Arbitration Act s. 29A, 43A-43M added in 2015-2019); otherwise they are a sign of misreading.
    extra_allowed = len(old_by) if (coverage >= 0.99 and titles >= 0.9) else max(5, MAX_EXTRA * len(old_by))
    problems = []
    if coverage < MIN_COVERAGE:
        problems.append(f"only {coverage:.0%} of the sections found (missing e.g. {', '.join(missing[:6])})")
    if titles < MIN_TITLES:
        bad = [n for n in common if not titles_agree(old_by[n]["title"], new_by[n]["title"])][:3]
        problems.append(f"titles agree for only {titles:.0%} (e.g. s. {bad[0]}: '{old_by[bad[0]]['title'][:40]}' vs "
                        f"'{new_by[bad[0]]['title'][:40]}')" if bad else f"titles agree for only {titles:.0%}")
    if len(extra) > extra_allowed:
        problems.append(f"{len(extra)} extra sections (e.g. {', '.join(extra[:6])})")
    if duplicates > max(3, 0.02 * len(old_by)):
        problems.append(f"{duplicates} section numbers read twice")
    # The text itself: a reading that matched only the table of contents has every number and title but no text
    empty = [n for n in common if len((new_by[n]["text"] or "").strip()) < 20 and len(old_by[n]["text"] or "") >= 20]
    ratios = sorted(len(new_by[n]["text"]) / len(old_by[n]["text"]) for n in common if len(old_by[n]["text"] or "") > 50)
    median = ratios[len(ratios) // 2] if ratios else 0.0
    much_shorter = sum(1 for r in ratios if r < 0.5) / len(ratios) if ratios else 0.0
    if len(empty) > max(1, MAX_EMPTY * len(common)):
        problems.append(f"{len(empty)} sections have no text (e.g. s. {', '.join(empty[:5])}) - probably the table of contents")
    if median < MIN_LENGTH or much_shorter > MAX_MUCH_SHORTER:
        problems.append(f"the texts are too short (typically {median:.0%} of the old length; {much_shorter:.0%} under half)")
    old_all = {number_key(s["number"]): s for s in old}
    details = ([f"+ s. {n} {new_by[n]['title'][:50]}" for n in extra]
               + [f"= s. {n} {old_all[n]['title'][:50]} (not in the PDF: old text kept)" for n in missing])
    return {"coverage": coverage, "titles": titles, "missing": missing, "extra": extra, "duplicates": duplicates,
            "problems": problems, "details": details, "median_length": median,
            "score": (coverage + titles + min(1.0, median) - 2 * len(empty) / max(1, len(common)) - much_shorter
                      - len(extra) / max(1, len(old_by)) - duplicates / max(1, len(old_by)))}


def merge(official_sections: list[dict], old: list[dict], missing: list[str]) -> list[dict]:
    """The official sections in their order; each section the PDF reading missed keeps its old text, in its place."""
    out = []
    seen = set()
    for s in official_sections:
        if number_key(s["number"]) in seen:          # read twice: keep the first
            continue
        seen.add(number_key(s["number"]))
        out.append(s)
    order = [number_key(s["number"]) for s in old]
    for number in missing:
        kept = {**next(s for s in old if number_key(s["number"]) == number), "number": number, "from_dataset": True}
        before = order[:order.index(number)]
        anchor = next((n for n in reversed(before) if n in seen), None)
        position = next((i for i, s in enumerate(out) if number_key(s["number"]) == anchor), -1) + 1 if anchor else 0
        out.insert(position, kept)
        seen.add(number)
    return out


def officialise(code: str, check_only: bool) -> tuple[str, dict | None]:
    path = LAWS / "acts" / f"{code}.json"
    act = json.loads(path.read_text(encoding="utf-8"))
    if not act.get("source", "").startswith(DATASET):
        return "already official", None
    old = [s for s in act["sections"] if not s.get("official_part")]
    added = [s for s in act["sections"] if s.get("official_part")]          # schedules added before: kept
    name = act["name"]
    titles = [name if name.lower().startswith("the ") else f"The {name}", name]
    pdf, last = None, None
    for title in titles:
        try:
            pdf = official.fetch(code, indiacode=title)
            break
        except Exception as error:   # try the next spelling of the title
            last = error
    if pdf is None:
        return f"not found on India Code ({last})", None
    # India Code sometimes has the Act without its PDF (repealed codes); then the search may offer another
    # document with a similar title (a notification, an amending Act). Only the Act itself will do.
    found_title = official.source_info(code).get("indiacode_title", "")
    if found_title and plain_title(found_title) != plain_title(name):
        for leftover in (pdf, official.RAW / f"{code}.json"):
            leftover.unlink(missing_ok=True)
        return f"no official PDF of the Act itself on India Code (only '{found_title[:70]}')", None
    import pymupdf

    with pymupdf.open(str(pdf)) as doc:
        scanned = sum(1 for page in doc if rulebook.needs_ocr(page.get_text()))
    if scanned > MAX_SCANNED_PAGES and not (official.RAW / f"{code}.ocr.json").exists():
        return f"skipped: the PDF is a scan ({scanned} pages need OCR)", None
    lines = rulebook.pdf_lines(pdf, ocr="auto", cache=official.RAW / f"{code}.ocr.json")
    # Read it the ways the reader knows (bold headings or not) and keep the reading that matches the sections
    # the library already has best; schedules added before (CPC Orders ...) are not read again
    best = None
    listed = contents_numbers(lines)
    known = {number_key(s["number"]) for s in old}
    use_list = len(listed & known) >= 0.8 * len(known)        # the list was read well enough to rely on
    for start in (None, BODY_START):
        for bold in (None, False, True):
            try:
                provisions = rulebook.parse(lines, numbering="sections", bold=bold, start=start)
            except Exception:
                continue
            if not provisions:
                continue
            new = to_sections(provisions, pdf.name)
            if added:
                new = [s for s in new if not s["number"].upper().startswith("SCH")]
            # A "section" the Act's own list doesn't name is a State amendment (or a misreading): left out
            left_out = []
            if use_list:
                keep = []
                for s in new:
                    key = number_key(s["number"])
                    if key in known or key in listed or (key.startswith("SCH") and "SCHEDULE" in listed):
                        keep.append(s)
                    else:
                        left_out.append(f"- s. {s['number']} {s['title'][:45]} (not in the Act's list of sections: left out)")
                new = keep
            result = compare(old, new)
            result["details"] += left_out
            if best is None or result["score"] > best[1]["score"]:
                best = (new, result)
    if best is None:
        return "KEPT the dataset text: the PDF could not be read as sections", None
    new, result = best
    how = "sections"
    summary = (f"{len(old)} -> {len(new)} sections, {result['coverage']:.0%} found, titles {result['titles']:.0%}, "
               f"text {result['median_length']:.0%} of the old length"
               + (f", {len(result['missing'])} kept from the dataset" if result["missing"] else "")
               + (f", {len(result['extra'])} new" if result["extra"] else ""))
    if result["problems"]:
        return "KEPT the dataset text: " + "; ".join(result["problems"]), None
    if check_only:
        shown = result["details"][:12] + ([f"... and {len(result['details']) - 12} more"] if len(result["details"]) > 12 else [])
        return "would replace: " + summary + "".join(f"\n{'':15}{line}" for line in shown), None
    for s in new:                     # the dataset knew some sections were omitted; the PDF may only say "[Omitted]"
        old_section = next((o for o in old if number_key(o["number"]) == number_key(s["number"])), None)
        if old_section and old_section.get("status") == "omitted" and len(s["text"]) < 120:
            s["status"] = "omitted"
    info = official.source_info(code)
    record = {**{k: v for k, v in act.items() if k not in ("sections",)},
              "source": INDIA_CODE, "kind": "act", "court": act.get("court", ""), "jurisdiction": act.get("jurisdiction", "Central"),
              "pdf": pdf.name, "url": info.get("page") or info.get("url", ""), "as_on": as_on(pdf), "parsed_as": how,
              "sections": merge(new, old, result["missing"]) + added}
    if result["missing"]:
        record["dataset_sections_kept"] = result["missing"]
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return "replaced: " + summary, record


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Replace dataset Act texts with the official India Code text.")
    parser.add_argument("codes", nargs="*", help="only these Acts (default: the most used ones)")
    parser.add_argument("--all", action="store_true", help="every Act still from the dataset")
    parser.add_argument("--check", action="store_true", help="compare only; change nothing")
    args = parser.parse_args()
    index = json.loads((LAWS / "index.json").read_text(encoding="utf-8"))
    dataset = [a["code"] for a in index["acts"] if a.get("source", "").startswith(DATASET)]
    codes = [c.upper() for c in args.codes] or (dataset if args.all else [c for c in PRIORITY if c in dataset])
    records, lines = {}, []
    for code in codes:
        started = time.time()
        try:
            outcome, record = officialise(code, args.check)
        except Exception as error:
            outcome, record = f"FAILED: {error}", None
        if record:
            records[code] = record
        line = f"{code:12} {outcome}  ({time.time() - started:.0f}s)"
        lines.append(line)
        print(line, flush=True)
    if records:
        write_index(records)
    replaced = sum(1 for line in lines if " replaced:" in line)
    print(f"\n{replaced} replaced, {len(lines) - replaced} unchanged." + (" (check only)" if args.check else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
