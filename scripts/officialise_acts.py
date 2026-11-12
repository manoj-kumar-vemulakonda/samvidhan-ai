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
UNLISTED = {
    "DPA": {"8A"},                     # Dowry Prohibition Act s. 8A, inserted by Act 43 of 1986
    "COMPANIES": {"393A", "418A"},     # inserted by Act 29 of 2020, ss. 55 and 59
    "IBC": {"67B", "67C"},             # inserted by Act 6 of 2026, s. 48
}
# Acts whose PDF passes the checks but reads badly in a way they can't see (checked by hand): {code: why}.
# Empty since v3.8: the Stamp Act was here until "SCHEDULE 1" was read as a heading and the States' Schedules
# printed after it became a provision of their own (split_state_amendments).
HOLD_BACK: dict[str, str] = {}


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
    # The States' amendments printed under a section don't count: they are not a Schedule (Stamp Act s. 47 holds
    # 36,000 characters of the States' own s. 47A)
    before = {number_key(s["number"]): (s["text"] or "").split("STATEMENT OF OBJECTS AND REASONS")[0] for s in old}
    swollen = [n for n in new_by if n in before and not n.startswith("SCH")      # repealed ones too (s. 79 was)
               and len(STATE_AMENDMENT.split(new_by[n]["text"] or "")[0]) > 5 * len(before[n]) + 10000]
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


def correct_misprints(lines, old: list[dict]) -> list[tuple[str, str]]:
    """India Code's Water Act PDF prints ss. 3, 13 and 31 with the number of the section before ("2. Constitution
    of Central Boards.—" right after "2. Definitions"). The reader takes such a heading for the same section again
    (after a Chapter heading: numbering that starts again) and the section's heading is lost. A heading in the
    Act's body whose title is not its number's title in the library, but that of the section just before or after
    it - the section that comes next after the last heading read - is given that section's number. (In sequence
    only: rule "1. Definitions" in one of the CPC's Orders is not s. 2.) The lines are corrected in place;
    returns (printed, corrected) pairs."""
    body = next((i for i, line in enumerate(lines) if re.search(BODY_START, line.text, re.I)), None)
    if body is None:
        return []
    order = [number_key(s["number"]) for s in old]
    by_key = {number_key(s["number"]): s for s in old}
    fixed = []
    last = None                                   # the last section heading read, as the library numbers it
    for line in lines[body:]:
        text = line.text.strip()
        match = rulebook.PROVISION.match(text)
        printed = number_key(match.group(1)) if match else ""
        if printed not in by_key:
            continue
        title = rulebook.split_title(match.group(2) or "")[0]
        if not title:
            continue
        if titles_agree(by_key[printed].get("title"), title):
            last = printed
            continue
        if last is None:
            continue
        following = next((n for n in order[order.index(last) + 1:] if not is_gone(by_key[n])), None)
        if following is None or abs(order.index(following) - order.index(printed)) > 1:
            continue
        wanted = norm_title(by_key[following].get("title"))
        if wanted and SequenceMatcher(None, wanted, norm_title(title)).ratio() >= 0.9:
            line.text = text[:match.start(1)] + str(by_key[following]["number"]).rstrip(".") + text[match.end(1):]
            fixed.append((match.group(1), following))
            last = following
    return fixed


def as_printed(section: dict) -> str:
    """A section read from the PDF written out again with its heading: '47A. Instruments under-valued ....—(1) ...'
    (a heading without a title gave its first words as the title: those are the start of the text)."""
    title, text = (section.get("title") or "").strip(), (section.get("text") or "").strip()
    if not title or text.startswith(title):
        return f"{section['number']}. {text}"
    return f"{section['number']}. {title}.—{text}"


def number_pattern(number: str) -> str:
    """'9A' as India Code may print it: '9A', '9 A', '9-A'."""
    match = re.match(r"(\d+)([A-Z]*)$", number_key(number))
    if not match:
        return re.escape(str(number))
    return match.group(1) + (r"\s*-?\s*" + match.group(2) if match.group(2) else "")


def page_of(text: str, page, lines) -> int | None:
    """The page where these words (a heading) are printed, from the page given on."""
    start = re.sub(r"\s+", " ", text).strip()[-25:]
    for line in lines or ():
        if page and line.page >= page and start and start in line.text:
            return line.page
    return page


def fill_titles(new: list[dict], old: list[dict]) -> list[str]:
    """A heading whose number stands on a line of its own ("33." and below it "Transfer conditional on ...—")
    leaves the section without a title and its text starting with it. The title is taken back out of the text
    when it is the library's title for that section - or, where the library has none (an earlier reading, --redo),
    when it is printed as a heading: words, a full stop and a dash ("Appeals.—"), no colon before it."""
    old_by = {number_key(s["number"]): s for s in old}
    filled = []
    for s in new:
        if (s.get("title") or "").strip() or str(s["number"]).upper().startswith("SCH"):
            continue
        heading = re.match(r"^([A-Z][^—–\n:;]{2,200}?)\s*\.\s*[—–]+\s*", s["text"] or "")
        known = (old_by.get(number_key(s["number"])) or {}).get("title") or ""
        if heading and (titles_agree(known, heading.group(1)) if known.strip() else True):
            s["title"], s["text"] = heading.group(1).strip(" ."), s["text"][heading.end():].lstrip()
            filled.append(number_key(s["number"]))
    return filled


def state_section_first(new: list[dict], old: list[dict], lines=None) -> list[str]:
    """A State amendment printed under a section may insert a section of its own, numbered like the central one
    that follows: Karnataka's "9A. Power of State Government to consolidate duties ..." is printed before the
    Stamp Act's own "9A. Instruments chargeable with duty for transactions in stock exchanges ...", Orissa's
    "77A. Fractions of five naye paise ..." before "77A. Saving as to certain stamps". The reader takes the
    State's heading and the central section ends up inside its text. When a section's title is not the library's
    title for that number, but "<number>. <the library's title>.—" is a heading further on in its text, the
    section starts there; the State's section goes back to the end of the section before, among its State
    amendments. Returns the numbers put right."""
    old_by = {number_key(s["number"]): s for s in old}
    fixed = []
    for at, s in enumerate(new):
        known = old_by.get(number_key(s["number"]))
        if at == 0 or not known or is_gone(known) or str(s["number"]).upper().startswith("SCH") \
                or titles_agree(known.get("title"), s.get("title")):
            continue
        title = re.sub(r"\s+", " ", known.get("title") or "").strip(" .[]")
        if len(title) < 10:
            continue
        words = r"\s+".join(re.escape(word) for word in title.split())
        heading = re.search(r"(?:\d{1,2}\s*)?\[?\s*" + number_pattern(s["number"]) + r"\s*\.\s*(" + words
                            + r"[a-z]{0,3})\s*\.?\s*[—–]+\s*", s["text"] or "")
        if not heading or heading.start() < 20:
            continue
        before = new[at - 1]
        before["text"] = (before["text"].rstrip() + "\n" + f"{s['number']}. {s['title']}.—"
                          + s["text"][:heading.start()].strip())
        s["page"] = page_of(heading.group(0), s.get("page"), lines)
        s["title"], s["text"] = heading.group(1), s["text"][heading.end():].strip()
        fixed.append(number_key(s["number"]))
    return fixed


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


# India Code's heading over the States' amendments: a line of its own in the PDF, the start of a line in the text
STATE_AMENDMENT_LINE = re.compile(r"^\s*STATE\s+AMENDMENTS?\s*$")
STATE_AMENDMENT = re.compile(r"(?m)^\s*STATE\s+AMENDMENTS?\b\s*")
# a central Schedule printed after the States' ones: "... s. 2] SCHEDULE II.—[Enactments repealed.] Rep. by ..."
LATER_SCHEDULE = re.compile(r"(?:^|(?<=[\].]))\s*SCHEDULE\s+([IVX]{1,4})\s*\.?\s*[—–]+\s*(\S.*)", re.S | re.M)
STATE_PART_MIN = 20000


def split_state_amendments(new: list[dict], lines=None) -> list[str]:
    """India Code prints the States' amendments of a Schedule after it: for the Stamp Act 260 pages of State
    Schedules (Assam, Himachal Pradesh, Orissa ...), one of them a substituted "SCHEDULE I". Left in the Schedule,
    a State's rate would be cited as the Act's own Schedule I. When they are longer than the Schedule itself (and
    long), they become a provision of their own, "State amendments to Schedule I"; a central Schedule printed after
    them (Stamp Act Schedule II, "[Enactments repealed.]") gets its own too. Returns what was split off."""
    done = []
    for s in list(new):
        number = str(s["number"]).upper()
        if not number.startswith("SCH") or number.endswith("STATES"):
            continue
        mark = STATE_AMENDMENT.search(s["text"] or "")
        if not mark:
            continue
        own, states = s["text"][:mark.start()].rstrip(), s["text"][mark.end():].strip()
        if len(states) < STATE_PART_MIN or len(states) < len(own):
            continue
        at = new.index(s)
        later = None
        for found in LATER_SCHEDULE.finditer(states):
            later = found                      # the last one, if it is at the very end
        if later and len(states) - later.start() > 2000:
            later = None
        roman = s["ref"].replace("Sch.", "").strip()
        page = next((line.page for line in lines or () if s.get("page") and line.page >= s["page"]
                     and STATE_AMENDMENT_LINE.match(line.text)), s.get("page"))
        part = {**s, "number": f"{number}STATES", "ref": f"Sch. {roman}, State amendments".replace("Sch. ,", "Sch.,"),
                "title": f"State amendments to {s['title']}", "text": "STATE AMENDMENT\n" + states[:later.start()].strip()
                if later else "STATE AMENDMENT\n" + states, "page": page}
        s["text"] = own
        new.insert(at + 1, part)
        done.append(f"{s['ref']}: the States' amendments printed after it are a provision of their own "
                    f"({len(part['text']):,} characters, from p. {page})")
        if later:
            label = later.group(1)
            ordinal = rulebook.roman_to_int(label)
            if ordinal and not any(str(x["number"]).upper() == f"SCH{ordinal}" for x in new):
                last_page = max((line.page for line in lines or ()), default=page)
                new.insert(at + 2, {**s, "number": f"SCH{ordinal}", "ref": f"Sch. {label}", "title": f"Schedule {label}",
                                    "text": later.group(2).strip(), "page": last_page,
                                    "status": "omitted" if re.search(r"(?i)\brep(?:ealed)?\b", later.group(2)[:120])
                                    else s.get("status", "in_force")})
                done.append(f"Sch. {label}, printed after the States' amendments, is a provision of its own")
    return done


OMITTED_SCHEDULE = re.compile(r"\[\s*THE\s+(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH)\s+"
                              r"SCHEDULE\s*\.?\s*[—–]+(.{0,400}?\b(?:Omitted\b|Repealed\b|Rep\.).{0,300})\]\s*$", re.S)


def split_omitted_schedules(new: list[dict]) -> list[str]:
    """An omitted Schedule printed as a note at the end of the one before: the Motor Vehicles Act's First Schedule
    ends "[THE SECOND SCHEDULE.—[Schedule for compensation for third party fatal accidents/injury cases claims]
    Omitted by the Motor Vehicles (Amendment) Act, 2019 ...]". Lawyers still cite it: it becomes a Schedule of its
    own, marked omitted."""
    done = []
    for s in list(new):
        if not str(s["number"]).upper().startswith("SCH"):
            continue
        found = OMITTED_SCHEDULE.search(s["text"] or "")
        if not found or len(s["text"]) - found.start() > 800:
            continue
        ordinal = rulebook.ORDINALS[found.group(1).upper()]
        if any(str(x["number"]).upper() == f"SCH{ordinal}" for x in new):
            continue
        s["text"] = s["text"][:found.start()].rstrip()
        roman = rulebook.int_to_roman(ordinal)
        new.insert(new.index(s) + 1, {**s, "number": f"SCH{ordinal}", "ref": f"Sch. {roman}",
                                      "title": f"The {found.group(1).title()} Schedule", "status": "omitted",
                                      "text": found.group(2).strip()})
        done.append(f"Sch. {roman}, omitted, printed at the end of {s['ref']}, is a provision of its own")
    return done


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
        scans = [page.number + 1 for page in doc if rulebook.needs_ocr(page.get_text())]
    ocr, pictures = "auto", ""
    if len(scans) > MAX_SCANNED_PAGES and not (official.RAW / f"{code}.ocr.json").exists():
        # The Motor Vehicles Act's 51 scanned pages are its First Schedule: pictures of the road signs, nothing OCR
        # can read. When every scanned page comes after the first Schedule heading, no section is on them: the
        # Act is read without OCR (the Schedule keeps its heading and text pages; the PDF page shows the pictures)
        plain = rulebook.pdf_lines(pdf, ocr="never", notes="shape")
        body = next((i for i, line in enumerate(plain) if re.search(BODY_START, line.text, re.I)), None)
        schedules = next((line.page for line in plain[body or 0:] if body is not None and (
            rulebook.SCHEDULE.match(line.text.strip()) or rulebook.SCHEDULE_NUMBERED.match(line.text.strip())
            or rulebook.SCHEDULE_NOTED.match(line.text.strip()))), None)
        if not schedules or min(scans) < schedules:
            return f"skipped: the PDF is a scan ({len(scans)} pages need OCR)", None
        ocr, pictures = "never", f"pp. {min(scans)}-{max(scans)}"
    # notes="shape": India Code prints Illustrations (and some whole pages) in small type - see rulebook.clean_lines
    lines = rulebook.pdf_lines(pdf, ocr=ocr, cache=official.RAW / f"{code}.ocr.json", notes="shape")
    misprints = correct_misprints(lines, old)          # "2. Constitution of Central Boards" for s. 3 (Water Act)
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
            # A "section" the Act's own list doesn't name is a State amendment (or a misreading): not a section of
            # the Act. Its text goes back to the end of the section before, where India Code prints it - among that
            # section's State amendments (Stamp Act s. 47: the States' own "47-A. Instruments under-valued ...")
            left_out = []
            if use_list:
                keep = []
                for s in new:
                    key = number_key(s["number"])
                    if key in known or key in listed or key in UNLISTED.get(code, ()) \
                            or (key.startswith("SCH") and "SCHEDULE" in listed):
                        keep.append(s)
                    elif keep:
                        keep[-1]["text"] = keep[-1]["text"].rstrip() + "\n" + as_printed(s)
                        left_out.append(f"- s. {s['number']} {s['title'][:45]} (not in the Act's list of sections: "
                                        f"kept in the text of s. {keep[-1]['number']})")
                    else:
                        left_out.append(f"- s. {s['number']} {s['title'][:45]} (not in the Act's list of sections: left out)")
                new = keep
            states = split_state_amendments(new, lines)      # the Stamp Act's 260 pages of State Schedules
            states += split_omitted_schedules(new)            # the Motor Vehicles Act's Second Schedule
            titled = fill_titles(new, old)                    # "33." on a line of its own, the title below it
            state_first = state_section_first(new, old, lines)   # Karnataka's s. 9A before the Stamp Act's own
            result = compare(old, new)
            split = split_glued(new, old, result["missing"], lines)
            if split:                 # sections whose heading the reader missed, cut off the section before
                result = compare(old, new)
            result["details"] += left_out + [f"~ s. {n} cut off the end of the section before (its heading was not read)"
                                             for n in split] \
                + [f"~ s. {right} printed as s. {printed} in the PDF (number corrected)" for printed, right in misprints] \
                + [f"~ {line}" for line in states] \
                + [f"~ s. {n}: its title was read from the start of its text" for n in titled] \
                + [f"~ s. {n}: a State's own s. {n}, printed before it, went back among the State amendments of the "
                   f"section before" for n in state_first]
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
               + (f", {len(result['extra'])} new" if result["extra"] else "")
               + (f", the scanned {pictures} (pictures) not read" if pictures else ""))
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
    record.pop("scanned_pages_not_read", None)
    if pictures:
        record["scanned_pages_not_read"] = pictures
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
