"""
Replace an Act's open-dataset text with the official India Code text - only when the official PDF is read well.

    .venv\\Scripts\\python.exe scripts\\officialise_acts.py --check          compare only, change nothing
    .venv\\Scripts\\python.exe scripts\\officialise_acts.py                  the Acts lawyers use most (PRIORITY)
    .venv\\Scripts\\python.exe scripts\\officialise_acts.py NIA HMA          just these
    .venv\\Scripts\\python.exe scripts\\officialise_acts.py --all            every Act still from the dataset
    .venv\\Scripts\\python.exe scripts\\officialise_acts.py --redo NIA       read an Act made official before again

Why: the first 101 Acts came from an open dataset (scripts/build_laws.py). Its text is mostly right, but the model
test found a wrong amendment footnote that two of three AI models repeated. India Code's PDFs are the official text,
with correct footnotes ("Subs. by Act 55 of 2002, s. 7 ...") and page numbers, so a citation can open the PDF page.

For each Act: download the PDF once (data/raw/official/<CODE>.pdf), read its sections, and compare with the text
the library has now. The Act is replaced only if
  * at least 98% of its section numbers are found (citations, the IPC <-> BNS table and the topic hints use them);
    the few not found keep their old text, marked "from_dataset";
  * at least 85% of the section titles agree (otherwise the PDF was probably misread);
  * there are not many extra "sections" (a sign of misreading);
  * the texts are there and about as long as before (not just the table of contents);
  * no Illustrations go missing (India Code prints them small, like footnotes);
  * the sections point at pages of the Act itself, not of its table of contents.
The PDF is read with rulebook's notes="shape": footnotes are found by their shape, not their size.
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
from scripts.official_books import BOOKS  # noqa: E402

DATASET = "Open dataset"
# The Acts lawyers cite most, first
PRIORITY = ["BNS", "BNSS", "BSA", "IPC", "CRPC", "IEA", "CPC", "NIA", "LIMITATION", "CONTRACT", "TPA", "SRA", "HMA",
            "DV", "CONSUMER", "ARBITRATION", "POCSO", "NDPS", "SCST", "MVA", "HSA", "SUCCESSION", "RTI", "ITACT"]
MIN_COVERAGE, MIN_TITLES, MAX_EXTRA = 0.98, 0.85, 0.05
MAX_EMPTY, MIN_LENGTH, MAX_MUCH_SHORTER = 0.02, 0.7, 0.10   # the text must really be there, about as long as before
MAX_LOST_ILLUSTRATIONS = 0.05
ILLUSTRATED = re.compile(r"\bIllustrations?\b")
# The label on a line of its own - not a note such as "1. The Illustrations omitted by Act 20 of 1929, s. 11."
ILLUSTRATION_LABEL = re.compile(r"(?m)^\s*Illustrations?\s*[.:—–-]?\s*$")
BODY_START = r"^\s*ACT\s+NO\.?\s*\d+\s+OF\s+\d{4}"           # where an India Code PDF's Act begins (after its contents)
MAX_SCANNED_PAGES = 30   # an Act whose PDF is a scan needs hours of OCR: left for scripts/build_official.py
# Real sections that India Code's own list of sections skips (checked by hand: the body has them, inserted by a
# central Act, not a State amendment) - kept although the list doesn't name them
UNLISTED = {"DPA": {"8A"}}      # Dowry Prohibition Act s. 8A, inserted by Act 43 of 1986
# Acts whose PDF passes the checks but reads badly in a way they can't see (checked by hand)
HOLD_BACK = {
    "STAMP": "India Code prints the States' substituted Schedules after the Act; one of them would be read as the "
             "Act's own Schedule I, and Schedule I itself (printed \"SCHEDULE 1\") stays glued to s. 79, as in the dataset",
}


def illustration_words(text: str) -> list[str]:
    """The first words of each Illustration block in a text, to look for in another reading."""
    found = []
    for label in ILLUSTRATION_LABEL.finditer(text):
        words = re.sub(r"\W+", " ", text[label.end():label.end() + 200]).lower().split()
        if len(words) >= 6:
            found.append(" ".join(words[:8]))
    return found


def contents_numbers(lines) -> set[str]:
    """The section numbers in the PDF's own list of sections (before 'ACT NO. .. OF ....'). State amendments,
    which India Code prints under the sections they change, are never in this list."""
    body = next((i for i, line in enumerate(lines) if re.search(BODY_START, line.text, re.I)), None)
    if not body:
        return set()
    found = set()
    for line in lines[:body]:
        # "22E. Award ...", "22E.Award ...", "125A . Penalty ...", "45-IA. ...", "1[53S. ...", "49-I Conditions ..."
        match = re.match(r"^\s*(?:\d{1,2}\s*\[|\[)?\s*(\d{1,4}\s?-?\s?[A-Z]{0,4})\s*\.\s*\S", line.text) \
            or re.match(r"^\s*(\d{1,4}\s?-\s?[A-Z]{1,4})\s+[A-Z]", line.text)
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


# "[Substituted by Act 18 of 2023 ... for sections 15 to 17 ...]": the range became other sections (EPA s. 17)
MERGED = re.compile(r"\bsubstituted\s+by\s+Act\b[^\]]{0,80}?\bfor\s+sections\b", re.I)


def is_gone(section: dict) -> bool:
    """A section the Act no longer has ([Repealed.], [Omitted.], a range substituted by other sections): the PDF
    may list a range of them on one line, or still name one in its list of sections."""
    text = section.get("text") or ""
    return section.get("status") == "omitted" or bool(REPEALED.match(section.get("title") or "")) \
        or (len(text) < 160 and bool(re.search(r"\b(repealed|omitted)\b", text, re.I))) \
        or (len(text) < 300 and bool(MERGED.search(text)))


def number_key(number: str) -> str:
    """The dataset writes a few numbers as '21A.': the same section as '21A'. India Code (and the dataset, now and
    then) writes an I or O after a hyphen, '45-I', so it isn't read as 1 or 0: the same section as '45I', which is
    how the reader and the library (laws.norm_number) spell it."""
    return re.sub(r"[\s.\-–]+", "", str(number or "")).upper()


def opening_words(text: str, count: int = 20) -> str:
    """The first words of a section's law, after its heading ("3. Constitution of Central Boards.—"): enough
    to find that text elsewhere; "" when the text is too short to tell."""
    text = text or ""
    dash = re.search(r"[—–]", text[:250])
    words = re.sub(r"\W+", " ", text[dash.end():] if dash else text).lower().split()
    return " ".join(words[:count]) if len(words) >= count else ""


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
    # the dataset glued the Bill's Statement of Objects and Reasons to some last sections: not law, not counted
    law = {n: (old_by[n]["text"] or "").split("STATEMENT OF OBJECTS AND REASONS")[0] for n in common}
    ratios = sorted(len(new_by[n]["text"]) / len(law[n]) for n in common if len(law[n]) > 50)
    # A section that swells (Stamp Act s. 79, "[Repealed.]", took in Schedule I and the States' Schedules,
    # printed "SCHEDULE 1"): something the reader didn't see as a heading was glued to it
    before = {number_key(s["number"]): (s["text"] or "").split("STATEMENT OF OBJECTS AND REASONS")[0] for s in old}
    swollen = [n for n in new_by if n in before and not n.startswith("SCH")      # repealed ones too (s. 79 was)
               and len(new_by[n]["text"] or "") > 5 * len(before[n]) + 10000]
    if swollen:
        problems.append(f"s. {', '.join(swollen[:4])} became much longer (a Schedule or other text glued to it)")
    median = ratios[len(ratios) // 2] if ratios else 0.0
    much_shorter = sum(1 for r in ratios if r < 0.5) / len(ratios) if ratios else 0.0
    if len(empty) > max(1, MAX_EMPTY * len(common)):
        problems.append(f"{len(empty)} sections have no text (e.g. s. {', '.join(empty[:5])}) - probably the table of contents")
    if median < MIN_LENGTH or much_shorter > MAX_MUCH_SHORTER:
        problems.append(f"the texts are too short (typically {median:.0%} of the old length; {much_shorter:.0%} under half)")
    # Illustrations are law too (and often printed small, like footnotes): none may go missing, nor their
    # label. An old text can hold another section's Illustrations (the dataset ran TPA ss. 114-137
    # together): those count as found when their words are in a labelled section of the new reading.
    everything = re.sub(r"\W+", " ", " ".join(s["text"] or "" for s in new if ILLUSTRATED.search(s["text"] or ""))).lower()
    illustrated = [n for n in common if ILLUSTRATION_LABEL.search(old_by[n]["text"] or "")]
    def found_elsewhere(text: str) -> bool:
        blocks = illustration_words(text)
        return bool(blocks) and all(words in everything for words in blocks)

    lost = [n for n in illustrated if not ILLUSTRATED.search(new_by[n]["text"] or "")
            and not found_elsewhere(old_by[n]["text"])]
    if len(lost) > max(1, MAX_LOST_ILLUSTRATIONS * len(illustrated)):
        problems.append(f"the Illustrations of {len(lost)} of {len(illustrated)} sections are missing (e.g. s. {', '.join(lost[:5])})")
    old_all = {number_key(s["number"]): s for s in old}
    # A section the reading missed may still be in it, inside the section before (its heading not read at all,
    # e.g. a misprinted number). Keeping the old text too would give the law twice: refused.
    all_text = re.sub(r"\W+", " ", " ".join(s["text"] or "" for s in new)).lower()
    inside = [n for n in missing if not is_gone(old_all[n]) and (words := opening_words(old_all[n]["text"]))
              and words in all_text]
    if inside:
        problems.append(f"the text of s. {', '.join(inside[:4])} is inside another section (its heading was not read)")
    details = ([f"+ s. {n} {new_by[n]['title'][:50]}" for n in extra]
               + [f"= s. {n} {old_all[n]['title'][:50]} (not in the PDF: old text kept)" for n in missing])
    return {"coverage": coverage, "titles": titles, "missing": missing, "extra": extra, "duplicates": duplicates,
            "problems": problems, "details": details, "median_length": median,
            "score": (coverage + titles + min(1.0, median) - 2 * len(empty) / max(1, len(common)) - much_shorter
                      - len(extra) / max(1, len(old_by)) - duplicates / max(1, len(old_by)))}


def split_glued(new: list[dict], old: list[dict], missing: list[str], lines=None) -> list[str]:
    """A heading the reader could not see ("60 Application ..." without its dot, "37ZA." printed for 378ZA, a
    heading that lost its number) leaves that section's text at the end of the section before. Its title from
    the library, followed by the ".—" that ends a heading, shows where it begins: it is cut off there and gets
    its official text. Returns the numbers split off (no longer missing)."""
    order = [number_key(s["number"]) for s in old]
    found = []
    for number in missing:
        old_section = next(s for s in old if number_key(s["number"]) == number)
        title = re.sub(r"\s+", " ", old_section.get("title") or "").strip(" .[]")
        if len(title) < 10 or is_gone(old_section):
            continue
        keys = [number_key(s["number"]) for s in new]
        anchor = next((n for n in reversed(order[:order.index(number)]) if n in keys), None)
        if anchor is None:
            continue
        at = keys.index(anchor)
        before = new[at]
        words = r"\s+".join(re.escape(word) for word in title.split())
        # the last word may end differently ("insufficient ground" in the dataset, "grounds" in the PDF: CPC s. 95)
        heading = re.search(r"(?:^|(?<=[.:;\])—]))\s*((?:\d{1,2}\s*)?\[?\s*(?:\d{1,4}[A-Z]{0,4}\s*\.?\s*)?" + words
                            + r"[a-z]{0,3})\s*\.?\s*[—–]+\s*", before["text"], re.M)
        if not heading or heading.start(1) < 20:
            continue
        page = before.get("page")
        start = re.sub(r"\s+", " ", heading.group(1))[:40]
        for line in lines or ():
            if page and line.page >= page and start[-25:] in line.text:
                page = line.page
                break
        new.insert(at + 1, {**before, "number": number, "ref": f"s. {number}", "title": title,
                            "text": before["text"][heading.end():].strip(), "status": "in_force", "page": page})
        before["text"] = before["text"][:heading.start(1)].rstrip()
        found.append(number)
    return found


def merge(official_sections: list[dict], old: list[dict], missing: list[str], old_is_official: bool = False) -> list[dict]:
    """The official sections in their order; each section the PDF reading missed keeps its old text, in its place.
    old_is_official (--redo): the old text came from an earlier reading of the PDF, so it is "from_dataset"
    only where it already was."""
    out = []
    seen = set()
    for s in official_sections:
        if number_key(s["number"]) in seen:          # read twice: keep the first
            continue
        seen.add(number_key(s["number"]))
        out.append(s)
    order = [number_key(s["number"]) for s in old]
    for number in missing:
        old_section = next(s for s in old if number_key(s["number"]) == number)
        kept = {**old_section, "number": number}
        if not old_is_official:
            kept["from_dataset"] = True
        before = order[:order.index(number)]
        anchor = next((n for n in reversed(before) if n in seen), None)
        position = next((i for i, s in enumerate(out) if number_key(s["number"]) == anchor), -1) + 1 if anchor else 0
        out.insert(position, kept)
        seen.add(number)
    return out


def officialise(code: str, check_only: bool, redo: bool = False) -> tuple[str, dict | None]:
    if code in HOLD_BACK:
        return f"KEPT the dataset text: {HOLD_BACK[code]}", None
    path = LAWS / "acts" / f"{code}.json"
    act = json.loads(path.read_text(encoding="utf-8"))
    again = False
    if not act.get("source", "").startswith(DATASET):
        # --redo: read again an Act this script made official before (after the reader improved).
        # Books that scripts/build_official.py makes are left to it - its next run would undo the change.
        if not (redo and act.get("source") == INDIA_CODE and act.get("parsed_as") == "sections"
                and code not in {book["code"] for book in BOOKS}):
            return "already official", None
        again = True
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
    # notes="shape": India Code prints Illustrations (and some whole pages) in small type - see rulebook.clean_lines
    lines = rulebook.pdf_lines(pdf, ocr="auto", cache=official.RAW / f"{code}.ocr.json", notes="shape")
    # Read it the ways the reader knows (bold headings or not) and keep the reading that matches the sections
    # the library already has best; schedules added before (CPC Orders ...) are not read again
    best = None
    listed = contents_numbers(lines)
    known = {number_key(s["number"]) for s in old}
    use_list = len(listed & known) >= 0.8 * len(known)        # the list was read well enough to rely on
    # The page where the Act itself begins ("ACT NO. 26 OF 1881"): a section said to be on an earlier page
    # was taken from the list of sections, and its link would open the contents instead of the section
    body_page = next((line.page for line in lines if re.search(BODY_START, line.text, re.I)), 0)
    for start in (BODY_START, None):                          # on a tie, the reading that starts at the Act
        for bold in (None, False, True):
            try:
                provisions = rulebook.parse(lines, numbering="sections", bold=bold, start=start, numbered_schedules=True)
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
                    if key in known or key in listed or key in UNLISTED.get(code, ()) \
                            or (key.startswith("SCH") and "SCHEDULE" in listed):
                        keep.append(s)
                    else:
                        left_out.append(f"- s. {s['number']} {s['title'][:45]} (not in the Act's list of sections: left out)")
                new = keep
            result = compare(old, new)
            split = split_glued(new, old, result["missing"], lines)
            if split:                 # sections whose heading the reader missed, cut off the section before
                result = compare(old, new)
            result["details"] += left_out + [f"~ s. {n} cut off the end of the section before (its heading was not read)"
                                             for n in split]
            in_contents = sum(1 for s in new if s.get("page") and s["page"] < body_page) / max(1, len(new))
            result["score"] -= in_contents
            if in_contents > 0.1:
                result["problems"].append(f"{in_contents:.0%} of the sections point at pages before the Act begins "
                                          f"(p. {body_page}) - read from the list of sections")
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
              "sections": merge(new, old, result["missing"], old_is_official=again) + added}
    still_dataset = [number_key(s["number"]) for s in record["sections"] if s.get("from_dataset")]
    record.pop("dataset_sections_kept", None)
    if still_dataset:
        record["dataset_sections_kept"] = still_dataset
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return "replaced: " + summary, record


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Replace dataset Act texts with the official India Code text.")
    parser.add_argument("codes", nargs="*", help="only these Acts (default: the most used ones)")
    parser.add_argument("--all", action="store_true", help="every Act still from the dataset")
    parser.add_argument("--check", action="store_true", help="compare only; change nothing")
    parser.add_argument("--redo", action="store_true",
                        help="also read again the named Acts this script made official before (after a reader fix)")
    args = parser.parse_args()
    index = json.loads((LAWS / "index.json").read_text(encoding="utf-8"))
    dataset = [a["code"] for a in index["acts"] if a.get("source", "").startswith(DATASET)]
    codes = [c.upper() for c in args.codes] or (dataset if args.all else [c for c in PRIORITY if c in dataset])
    records, lines = {}, []
    for code in codes:
        started = time.time()
        try:
            outcome, record = officialise(code, args.check, args.redo)
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
