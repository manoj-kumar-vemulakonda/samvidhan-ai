"""
Turn a PDF of an Act, a set of court rules or a practice handbook into citable provisions.

Used for the official books in the library (scripts/build_official.py) and for rule books a lawyer
adds from the Library page (their own High Court's rules, a Civil Rules of Practice, ...).

    lines = pdf_lines("SCR2013.pdf")                       # text lines with font size, bold, page
    provisions = parse(lines, numbering="orders")         # [{number, ref, title, text, page, ...}]

How it works
  * Text comes from the PDF's text layer; scanned pages are read with OCR (RapidOCR).
  * Footnotes (smaller font), footnote markers (superscript), page numbers, running heads and the
    India Code watermark are dropped.
  * Structure headings: ORDER / CHAPTER / PART, and THE FIRST SCHEDULE ... .
  * A provision starts at a line like "11. Rejection of plaint.—" - in bold where the PDF has bold
    headings (India Code), otherwise at the left margin. Numbers must keep increasing (11, 11A, 12...),
    which removes the table of contents, state amendments that repeat a rule, and stray numbers.
  * If no structure can be found, every page becomes one provision ("p. 12"), so the book is still
    searchable and every answer still points at a page of the official PDF.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
ORDINALS = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6, "SEVENTH": 7, "EIGHTH": 8,
            "NINTH": 9, "TENTH": 10, "ELEVENTH": 11, "TWELFTH": 12}

HEADING = re.compile(r"^(?P<pre>[“\"‘']|\d{1,2}\s*\[|\*{1,3}\s*\[|\[)?\s*(?P<kind>ORDER|CHAPTER|PART)\s*[-–—]?\s*"
                     r"(?P<num>[IVXLC]{1,7}(?:[-–\s]?[A-Z])?|\d{1,3}[A-Z]?|[A-Z])\b\]?\.?\s*(?:[-–—:.]\s*)?(?P<rest>.*)$")
# "Chapter I" alone on its line (some books do not print headings in capitals)
HEADING_ALONE = re.compile(r"^(?P<pre>)(?P<kind>Order|Chapter|Part)\s*[-–—]?\s*(?P<num>[IVXLC]{1,7}(?:-[A-Z])?|\d{1,3}[A-Z]?)"
                           r"\.?(?P<rest>)$")
SCHEDULE = re.compile(r"^\[?\s*THE\s+(?:(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH)\s+)?SCHEDULE\b",
                      re.I)
# "SCHEDULE III", "1[SCHEDULE II" (with its note number), "1[SCHEDULE — I", "SCHEDULE 1" (the Stamp Act), alone on
# the line, in capitals
SCHEDULE_NUMBERED = re.compile(r"^(?:\d{1,2}\s*)?\[?\s*SCHEDULE\s*[—–-]?\s*((?=[IVX])X{0,3}(?:IX|IV|V?I{0,3})|[1-9]\d?)\s*\.?\]?\s*$")
# "1[THE FIRST SCHEDULE", "1THE SECOND SCHEDULE": a Schedule substituted by an amending Act, its note number in front;
# "1[2[THE FIRST SCHEDULE]]" (Motor Vehicles Act: substituted, then renumbered)
SCHEDULE_NOTED = re.compile(r"^\d{1,2}\s*\[?\s*(?:\d{1,2}\s*\[\s*)?THE\s+(?:(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|"
                            r"EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH)\s+)?SCHEDULE\s*\.?\]{0,2}\s*$")
# India Code prints the Bill's Statement of Objects and Reasons after the Act (and its Schedules): not law
OBJECTS_AND_REASONS = re.compile(r"^STATEMENT\s+OF\s+OBJECTS\s+AND\s+REASONS[A-Z]{0,2}\s*\.?$")   # IBC: "REASONSES"
APPENDIX = re.compile(r"^APPENDIX\b", re.I)
# "12. ...", "1[12. ...", "*[12. ..." - and a section inserted, then substituted: "1[2[10A. ...", "5[ 6[15QA. ..."
PROVISION = re.compile(r"^(?:\*{1,3})?\s*(?:(?:\d{1,2}\s*)?\[\s*){0,2}(?:Rule\s+)?(\d{1,4}(?:\s?-?[A-Z]{1,3})?)\s*\.\s*(\S.*)?$")
TITLE_END = re.compile(r"^(.{2,220}?[^\s(])\s*[.:]?\s*[—–]+\s*|^(.{2,220}?)\.\s*-{1,2}\s*(?=[A-Z(\[“\"\d])|^(.{2,220}?)\s*:-\s*")
# "2. The North Eastern Areas (Reorganisation) Act, 1971 (81 of 1971)." - the list of amending Acts, not a section
AMENDING_ACT = re.compile(r"^\d{1,3}\.\s+The\s.{3,200}?\b(?:Act|Ordinance|Regulation|Order)\b[^—]*\(\s*\d+\s*of\s*\d{4}\s*\)\.?\s*$")
WATERMARK = re.compile(r"^\s*IndiaCode\s*$|^\s*INDIA\s*CODE\s*$", re.I)
PAGE_NUMBER = re.compile(r"^\s*[-–(]?\s*\d{1,4}\s*[-–)]?\s*$")


@dataclass
class Line:
    text: str
    page: int                  # 1-based page of the PDF
    size: float = 0.0          # font size (0 when the text came from OCR)
    bold: bool = False
    x: float = 0.0
    y: float = 0.0


# ---------------------------------------------------------------------------
# Reading the PDF
# ---------------------------------------------------------------------------
def roman_to_int(text: str) -> int | None:
    text = (text or "").upper().strip()
    if not text or not re.fullmatch(r"[IVXLC]+", text):
        return None
    total, previous = 0, 0
    for char in reversed(text):
        value = ROMAN[char]
        total = total - value if value < previous else total + value
        previous = max(previous, value)
    return total


def int_to_roman(number: int) -> str:
    table = [(100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for value, letters in table:
        while number >= value:
            out += letters
            number -= value
    return out


def _page_lines(page, page_number: int) -> list[Line]:
    found: list[Line] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            size = max(s["size"] for s in spans)
            if size >= 20:                                    # watermark ("IndiaCode") / decorations
                continue
            body_size = max(s["size"] for s in spans if not (s["flags"] & 1)) if any(not (s["flags"] & 1) for s in spans) else size
            # Superscripts are footnote markers ("1[Provided ...", "310. Return of plaint" = footnote 3 + rule 10)
            text = "".join(s["text"] for s in line["spans"]
                           if not (s["flags"] & 1 and s["size"] < body_size - 0.5 and re.fullmatch(r"\s*[\d*†‡]+\s*", s["text"])))
            text = re.sub(r"\s+", " ", text).strip().replace("―", "—")
            if not text:
                continue
            head = "".join(s["text"] for s in spans)[:60]
            bold = any(("Bold" in s["font"] or s["flags"] & 16) for s in spans if s["text"].strip()
                       and head.find(s["text"].strip()[:10]) < 40)
            found.append(Line(text, page_number, round(body_size, 1), bool(bold), round(line["bbox"][0], 1), round(line["bbox"][1], 1)))
    return found


OCR_DPI = 160   # typed gazette pages read well at 160 dpi, about twice as fast as 220


def _ocr_page(page, page_number: int) -> list[Line]:
    """OCR one scanned page (RapidOCR) -> lines in reading order."""
    from app.extract import ocr_png

    png = page.get_pixmap(dpi=OCR_DPI).tobytes("png")
    text, _confidence = ocr_png(png, upright=True)
    return [Line(re.sub(r"\s+", " ", t).strip(), page_number) for t in text.splitlines() if t.strip()]


def needs_ocr(text: str) -> bool:
    """A text layer that is missing or unreadable (old scans with a garbage OCR layer)."""
    letters = re.findall(r"[A-Za-z]", text)
    if len(text.strip()) < 80 or len(letters) < 40:
        return True
    words = re.findall(r"[A-Za-z]{2,}", text)
    junk = sum(1 for w in words if not re.search(r"[aeiouyAEIOUY]", w)) + text.count("~") * 2
    return junk > max(8, len(words) * 0.18)


def pdf_lines(path: str | Path, ocr: str = "auto", progress=None, first_page: int = 1, last_page: int | None = None,
              cache: str | Path | None = None, notes: str = "size") -> list[Line]:
    """Every text line of the PDF. ocr: 'auto' (only pages without usable text), 'always' or 'never'.
    cache: a JSON file where OCR results are kept, so a scanned book is only read once.
    notes: how footnotes are found - see clean_lines."""
    import json

    import pymupdf

    doc = pymupdf.open(str(path))
    saved: dict = {}
    if cache and Path(cache).exists():
        try:
            saved = json.loads(Path(cache).read_text(encoding="utf-8"))
        except ValueError:
            saved = {}
    changed = False
    lines: list[Line] = []
    last = min(last_page or doc.page_count, doc.page_count)
    for index in range(first_page - 1, last):
        page = doc[index]
        text = page.get_text()
        if ocr == "always" or (ocr == "auto" and needs_ocr(text)):
            key = f"{index + 1}@{OCR_DPI}"
            if key in saved:
                lines += [Line(t, index + 1) for t in saved[key]]
            else:
                try:
                    found = _ocr_page(page, index + 1)
                    saved[key] = [line.text for line in found]
                    changed = True
                    lines += found
                except Exception:
                    lines += _page_lines(page, index + 1)
                if cache and changed and (index + 1) % 5 == 0:      # keep progress if the run is stopped
                    Path(cache).write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        else:
            lines += _page_lines(page, index + 1)
        if progress:
            progress(index + 1, last)
    if cache and changed:
        Path(cache).write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    return clean_lines(lines, notes)


def body_size(lines: list[Line]) -> float:
    sizes = Counter()
    for line in lines:
        if line.size:
            sizes[line.size] += len(line.text)
    return sizes.most_common(1)[0][0] if sizes else 0.0


FOOTNOTE_START = re.compile(r"^(\d{1,3})\.\s+\S")
# The words amendment notes are made of: "Subs. by Act 46 of 1999, s. 5", "Ins. by s. 22, ibid.", "the A.O. 1950"
NOTE_WORDS = re.compile(r"\b(?:Subs|Ins|Omitted|Added|Rep|Renumbered|Inserted|Substituted|Repealed|ibid|w\.e\.f|"
                        r"supra|infra|See)\b|\bAct\s+\d+\s+of\s+\d{4}|\bA\.\s?O\.", re.I)


def footnote_block(page: list[Line], body: float = 0.0) -> int | None:
    """Where the numbered notes at the foot of a page begin ("1. Subs. by Act 3 of 1921...", "2. Ins. by...").

    Returns the index in `page` of the "1." line, or None. Every line from there to the end of the page
    must be the next note number at the same left edge, or a note's next line (indented, or in another
    type size than `body`, the usual size, when given), and most notes must use the words of notes - so a
    numbered list in the law (a Schedule's items "1. The arbitrator is an employee ...") is not mistaken
    for notes."""
    for i, line in enumerate(page):
        start = FOOTNOTE_START.match(line.text.strip())
        if not start or start.group(1) != "1" or line.bold:
            continue
        expected, ok, notes = 2, True, [line.text]
        for later in page[i + 1:]:
            number = FOOTNOTE_START.match(later.text.strip())
            other_size = bool(body and later.size and abs(later.size - body) >= 0.9)
            if later.bold and not other_size:
                ok = False          # a bold heading in the usual size: the law goes on (bold in note type is a
                                    # note's own emphasis - the dates in the IBC's commencement note)
            elif number and int(number.group(1)) == expected and abs(later.x - line.x) < 3:
                expected += 1
                notes.append(later.text)
            elif later.x <= line.x + 3 and not other_size:
                ok = False
            else:
                notes[-1] += " " + later.text            # a note's next line
            if not ok:
                break
        if ok and sum(1 for note in notes if NOTE_WORDS.search(note)) >= 0.6 * len(notes):
            return i
    return None


ILLUSTRATION = re.compile(r"^\s*Illustrations?\s*[.:—–-]?\s*$", re.I)
# The first words of an amendment note: "3. Subs. by Act 46 of 1999, s. 5 ...", "1. Ins. by ..."
AMENDMENT_NOTE = re.compile(r"^\d{1,3}\.\s*(?:Subs|Ins|Omitted|Added|Rep|Renumbered|Inserted|Substituted|"
                            r"The\s+words|Certain\s+words|Vide)\b", re.I)


def clean_lines(lines: list[Line], notes: str = "size") -> list[Line]:
    """Drop footnotes, page numbers, watermarks and running heads/feet.

    notes="size" (court rules and handbooks): a line printed smaller than the usual size is a footnote.
    notes="shape" (India Code Acts, scripts/officialise_acts.py): India Code prints some of the law itself
    in small type - every Illustration in the Contract and Succession Acts, whole pages of the CPC - and
    some notes in the usual type, so size alone would throw law away and keep notes:
      * the numbered block of amendment notes at the foot of a page ("1. Subs. by Act 3 of 1921 ...",
        "2. Ins. by ...") is dropped whatever its size;
      * small lines after an "Illustration(s)" label are kept, up to the next line in the usual size;
      * on a page where most of the law is small, nothing else is dropped for being small;
      * "Illustrations", printed on many pages, is not taken for a running head.
    "shape" is checked against the text the library already has before an Act is replaced; the court-rule
    books keep "size" until each is checked the same way."""
    if not lines:
        return []
    shape = notes == "shape"
    size = body_size(lines)
    pages = {line.page for line in lines}
    # Running heads: the same short text on many pages ("THE GAZETTE OF INDIA : EXTRAORDINARY")
    counts = Counter(re.sub(r"\d+", "#", line.text.strip().upper()) for line in lines if len(line.text) < 90)
    kept_words = r"^(\(|PROVIDED|EXPLANATION|ILLUSTRATION)" if shape else r"^(\(|PROVIDED|EXPLANATION)"
    repeated = {text for text, n in counts.items() if len(pages) >= 6 and n >= max(4, len(pages) * 0.3)
                and not HEADING.match(text) and not re.match(kept_words, text)}

    def noise(line: Line) -> bool:
        text = line.text.strip()
        return bool(WATERMARK.match(text) or PAGE_NUMBER.match(text) or re.sub(r"\d+", "#", text.upper()) in repeated)

    def small(line: Line) -> bool:
        return bool(size and line.size and line.size < size - 1.2)

    small_pages: set[int] = set()                  # pages where most of the law is in small type
    note_ids: set[int] = set()                     # id() of the lines in each page's block of numbered notes
    if shape and size:
        by_page: dict[int, list[Line]] = {}
        for line in lines:
            # a short note ("2. Ins. by s. 27, ibid.") can repeat as often as a running head: it stays in
            # its page's list, or the gap in the numbering would hide the whole block
            if not noise(line) or (FOOTNOTE_START.match(line.text.strip()) and not PAGE_NUMBER.match(line.text.strip())):
                by_page.setdefault(line.page, []).append(line)
        blocks = {page: footnote_block(page_lines, size) for page, page_lines in by_page.items()}
        for page, page_lines in by_page.items():
            block = blocks[page]
            law = page_lines[:block] if block is not None else page_lines
            if block is not None:
                note_ids |= {id(line) for line in page_lines[block:]}
            sizes = Counter()
            for line in law:
                if line.size:
                    sizes[line.size] += len(line.text)
            if sum(sizes.values()) >= 500 and sizes.most_common(1)[0][0] < size - 1.2:
                small_pages.add(page)
        if small_pages:
            # In such a book, pages printed partly in small type are read the same way - but only where
            # the notes were found by their shape, so nothing small is kept unless the notes are known
            small_pages |= {page for page, block in blocks.items()
                            if block is not None and any(small(line) for line in by_page[page][:block])}

    kept = []
    illustrating = False                           # inside the small-type Illustrations under a section
    paused_on = None                               # ... but at the notes at the foot of this page
    for line in lines:
        if noise(line):
            continue
        text = line.text.strip()
        if shape:
            if ILLUSTRATION.match(text):
                illustrating, paused_on = True, None
            elif not small(line):
                illustrating = False
            elif illustrating and paused_on != line.page and (id(line) in note_ids or AMENDMENT_NOTE.match(text)):
                paused_on = line.page              # the Illustrations go on overleaf, after the notes
        if id(line) in note_ids:
            continue                                              # footnote, found by its shape
        if line.page not in small_pages and small(line) and not (illustrating and paused_on != line.page):
            continue                                              # footnote, found by its size
        if LIGATURE.search(line.text):
            line = replace(line, text=fix_ligatures(line.text))
        kept.append(line)
    return kept


LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}
LIGATURE = re.compile("[ﬀﬁﬂﬃﬄ]")


def fix_ligatures(text: str) -> str:
    """'Deﬁ nitions' -> 'Definitions': gazette fonts print 'fi' as one character, often with a stray space after it."""
    return re.sub(r"([ﬀﬁﬂﬃﬄ]) ?(?=[a-z])|([ﬀﬁﬂﬃﬄ])", lambda m: LIGATURES[m.group(1) or m.group(2)], text)


# ---------------------------------------------------------------------------
# Finding provisions
# ---------------------------------------------------------------------------
def number_key(number: str) -> tuple[int, str]:
    match = re.match(r"(\d+)\s*-?\s*([A-Z]*)", number.upper())
    return (int(match.group(1)), match.group(2)) if match else (0, "")


def clean_number(number: str) -> str:
    return re.sub(r"[\s\-]", "", number.upper())


def heading_number(token: str, kind: str = "") -> tuple[int, str] | None:
    """'VII' -> (7, ''), 'XXXII-A' -> (32, 'A'), '12' -> (12, ''), '5A' -> (5, 'A'); 'PART B' -> (2, 'B*')."""
    token = token.strip().upper()
    if kind == "part" and re.fullmatch(r"[A-Z]", token) and token not in "IVX":
        return ord(token) - 64, token + "*"          # lettered Part ("Part B"); '*' marks a letter
    match = re.fullmatch(r"([IVXLC]+)[-–\s]?([A-Z]?)", token)
    if match and roman_to_int(match.group(1)):
        return roman_to_int(match.group(1)), match.group(2)
    match = re.fullmatch(r"(\d+)([A-Z]?)", token)
    if match:
        return int(match.group(1)), match.group(2)
    return None


def split_title(rest: str) -> tuple[str, str]:
    """'Rejection of plaint.—The plaint shall ...' -> ('Rejection of plaint', 'The plaint shall ...')."""
    match = TITLE_END.match(rest)
    if match:
        title = next(g for g in match.groups() if g)
        return title.strip(" .[]"), rest[match.end():].strip()
    # Court rules often write "Caveat. A caveat shall be valid ..." - a short first sentence is the title
    match = re.match(r"^([A-Z][^.;:]{2,70})\.\s+([A-Z(\[“\"].*)$", rest)
    if match and len(match.group(1).split()) <= 9:
        return match.group(1).strip(), match.group(2).strip()
    return "", rest.strip()


def strip_markers(text: str) -> str:
    """Remove footnote markers printed in line with the text: '1[(e) where ...]' -> '[(e) where ...]'."""
    return re.sub(r"(?<![\w\]\)])\d{1,2}\s?\[(?=\S)", "[", text or "")


def join_text(lines: list[str]) -> str:
    """Join PDF lines into paragraphs: keep breaks before (a), (1), Provided, Explanation, Illustration."""
    out = ""
    for text in lines:
        text = text.strip()
        if not text:
            continue
        if not out:
            out = text
        elif out.endswith("-") and not out.endswith(" -") and re.match(r"[a-z]", text):
            out = out[:-1] + text
        elif re.match(r"^(\(\w{1,6}\)|\[?\(\w{1,6}\)|Provided|Explanation|Illustration|Note|STATE AMENDMENT|\d+\.\s)", text):
            out += "\n" + text
        else:
            out += " " + text
    return out.strip()


@dataclass
class Group:
    kind: str                 # "order" | "chapter" | "part" | "schedule"
    number: int
    suffix: str
    title: str
    page: int


def parse(lines: list[Line], numbering: str = "sections", bold: bool | None = None, only_schedule: bool = False,
          stop: str | None = None, start: str | None = None, schedules: str = "whole",
          allow_restart: bool = True, title_headings: bool = False, numbered_schedules: bool = False,
          _skip: frozenset[int] = frozenset()) -> list[dict]:
    """
    lines -> provisions.
      numbering: "sections" (s. 12), "orders" (O. VII r. 11), "chapters" (Ch. IV r. 3), "rules" (r. 12),
                 "paras" (Ch. IV para 3)
      bold:      True = provisions start only at bold numbers (India Code); None = decide from the PDF
      only_schedule: keep only what comes after THE FIRST SCHEDULE heading (the CPC's Orders)
      stop / start: regex of a heading line where the useful part ends / begins
      schedules: "whole" = each Schedule becomes one provision; "skip" = ignore schedules
      numbered_schedules: also take "SCHEDULE III" (no "THE") and "1[THE FIRST SCHEDULE" (a note number in front)
                 for a Schedule heading (sections only)
    """
    candidates = [line for line in lines if PROVISION.match(line.text)]
    if bold is None:
        # bold headings when a good share of numbered lines are bold (a plain table of contents lists them all
        # once more); a few bold lines in a plain book do not count
        bold_starts = sum(1 for line in candidates if line.bold)
        bold = bold_starts >= 5 and bold_starts >= 0.4 * len(candidates)
    left = _left_margin(lines, candidates)
    provisions: list[dict] = []
    current: dict | None = None
    body: list[str] = []
    group: Group | None = None          # current ORDER / CHAPTER
    part: Group | None = None
    schedule: Group | None = None
    pending_title_for: Group | None = None
    in_schedule = False
    started = start is None
    stopped = False
    last_key: tuple[int, str] | None = None
    schedule_parts: list[dict] = []
    used_groups: set[tuple] = set()          # ORDER / CHAPTER numbers that already have rules
    body_seen = False
    restart_ok = False
    latest: Group | None = None
    starts = 0                    # provisions found so far
    first_line = -1               # where the first one began
    lower_after_first = 0         # numbered lines after it with a LOWER number: it may be a stray contents line

    def flush() -> None:
        nonlocal current, body
        if current is not None:
            text = strip_markers(join_text(body))
            if numbering == "sections" and not current.get("_titled") and current.get("kind") == "section":
                title, text = wrapped_title(current.get("title") or "", text)
                current["title"] = title
            current["text"] = text
            if in_schedule and schedules == "whole" and current.get("kind") == "schedule":
                schedule_parts.append(current)
            else:
                provisions.append(current)
        current, body = None, []

    for index, line in enumerate(lines):
        text = line.text.strip()
        next_text = lines[index + 1].text if index + 1 < len(lines) else ""
        if not started:
            if re.search(start, text, re.I):
                started = True
            continue
        if stop and re.search(stop, text, re.I) and (provisions or current):
            stopped = True
        if stopped:
            break
        if OBJECTS_AND_REASONS.match(text) and (provisions or current):
            break                   # the Bill's reasons, printed after the Act: they were glued to its last section
        # Title line that follows "ORDER VII" / "CHAPTER IV" headings
        if pending_title_for is not None:
            footnote = re.match(r"^\d*\s*\[?(Subs|Ins|Substituted|Inserted|Added|Amended|Omitted|Vide)\b", text, re.I)
            if len(text) < 160 and not PROVISION.match(text) and not HEADING.match(text) and not SCHEDULE.match(text) \
                    and not footnote:
                if not pending_title_for.title:
                    pending_title_for.title = re.sub(r"^(?:\d{1,2}|\*{1,3})?\[|\]$", "", text).strip(" .")
                    continue
            pending_title_for = None
        heading = (HEADING.match(text) or (title_headings and line.bold and HEADING_ALONE.match(text))) \
            if len(text) < 140 else None
        if heading and heading.group("pre") and heading.group("pre") in "“\"‘'":
            heading = None                     # "ORDER XV-A ... quoted inside a State Amendment - not a real heading
        if heading and (heading.group("rest") == "" or heading.group("rest").isupper() or len(heading.group("rest")) < 90):
            kind = heading.group("kind").lower()
            parsed = heading_number(heading.group("num"), kind)
            if parsed and (kind != "part" or numbering != "orders"):
                suffix = parsed[1]
                if kind in ("order", "chapter") and (kind, parsed[0], suffix) in used_groups:
                    suffix = (suffix + "C") if numbering == "orders" else (suffix + "2")   # a second Order XI (commercial suits)
                g = Group(kind, parsed[0], suffix, heading.group("rest").strip(" .-—"), line.page)
                latest = g
                restart_ok = True                  # numbering may start again at 1 under a new heading
                if kind == "part" and numbering == "sections":
                    part = g
                    group = g
                elif kind == "part" and numbering == "rules":
                    part = g
                else:
                    group = g
                    if numbering in ("orders", "chapters", "paras"):
                        flush()
                        last_key = None
                pending_title_for = g
                continue
        sched = SCHEDULE.match(text) if len(text) < 80 else None
        if sched and numbering == "sections" and text.lstrip("[ 0123456789")[:1].islower():
            # "the Schedule I." wrapped from the line above - not a heading (NI Act s. 2). Acts only for now:
            # court-rule books were checked with the old reading (two would gain rules, two would change).
            sched = None
        # "SCHEDULE III" alone on its line (the Succession Act's style). Only when asked (officialise_acts.py,
        # whose checks refuse a bad reading): in the court-fees Acts it split most Schedules off the last
        # section, but the Kerala one lists "SCHEDULE I" in its contents and every section came out empty.
        numbered_sched = SCHEDULE_NUMBERED.match(text) if numbered_schedules and numbering == "sections" and not sched else None
        if numbered_schedules and numbering == "sections" and not sched and not numbered_sched:
            sched = SCHEDULE_NOTED.match(text)        # "1[THE FIRST SCHEDULE" (RBI, UAPA, IT Act): only when asked too
        if (sched or numbered_sched) and (provisions or current or (only_schedule and body_seen)):
            flush()
            if sched:
                ordinal = ORDINALS.get((sched.group(1) or "").upper(), 0)
                title = "The " + ((sched.group(1) or "").title() + " Schedule" if sched.group(1) else "Schedule")
            else:
                written = numbered_sched.group(1)
                ordinal = int(written) if written.isdigit() else roman_to_int(written)
                title = f"Schedule {int_to_roman(ordinal)}"
            schedule = Group("schedule", ordinal, "", "", line.page)
            in_schedule = True
            if only_schedule:
                group, last_key = None, None
            if schedules == "whole" and not only_schedule:
                current = {"kind": "schedule", "number": f"SCH{ordinal or ''}",
                           "ref": f"Sch. {int_to_roman(ordinal)}" if ordinal else "Sch.", "title": title,
                           "chapter": "", "chapter_title": "", "page": line.page, "status": "in_force"}
                pending_title_for = None
            continue
        if APPENDIX.match(text) and text.isupper() and len(text) < 40 and only_schedule and in_schedule:
            flush()
            break
        if in_schedule and schedules == "whole" and not only_schedule:
            body.append(text)
            continue
        if in_schedule and schedules == "skip" and not only_schedule:
            continue
        match = PROVISION.match(text)
        is_start = False
        if match:
            looks_right = (line.bold if bold else (not line.x or abs(line.x - left[line.page % 2]) <= 28)) \
                and not AMENDING_ACT.match(text)
            separator = r"[—–]|:-|\.-|\s-\s"          # "Title.—", "Title :-", "Title.-", "Title - "
            title_goes_on = bool(re.search(separator, next_text)) and not PROVISION.match(next_text)
            if looks_right and bold and last_key is None and not re.search(separator, text) and not title_goes_on \
                    and not re.search(r"(?i)omitted|repealed", text):
                looks_right = False       # a bold line in the table of contents, not the first section
            body_seen = body_seen or looks_right      # past the table of contents
            raw = clean_number(match.group(1))
            # A footnote digit glued to the number ("310." = footnote 3 + rule 10): try without it - but only
            # when that gives exactly the next rule (otherwise "97." could be misread as rule 7)
            for number in (raw, raw[1:], raw[2:]) if looks_right and index not in _skip else ():
                if not number or not number[0].isdigit():
                    continue
                key = number_key(number)
                if number != raw and not bold and not (last_key and (key == (last_key[0] + 1, "") or
                                                                     (key[0] == last_key[0] and key[1] > last_key[1]))):
                    continue                  # (bold India Code headings keep the lenient rule: footnote digits are common there)
                if _plausible(key, last_key, numbering, group) or \
                        (allow_restart and restart_ok and key[0] <= 2 and last_key is not None):
                    is_start = True
                    break
        if is_start and only_schedule and (not in_schedule or (numbering == "orders" and group is None)):
            is_start = False                          # e.g. the CPC's sections, when only its Orders are wanted
        if match and not is_start and starts == 1 and looks_right and last_key and raw[:1].isdigit() \
                and number_key(raw)[0] < last_key[0]:
            lower_after_first += 1
        if is_start:
            starts += 1
            if starts == 1:
                first_line = index
            flush()
            last_key = key
            restart_ok = False
            rest = match.group(2) or ""
            title, first = split_title(rest)
            titled = bool(title)                      # a real rule heading ("Caveat.—"), not a list item
            if not title and not bold and len(rest) < 100 and rest.endswith("."):
                title, first = rest.strip(" ."), ""
            ref, pid = _reference(numbering, number, group)
            if group:
                used_groups.add((group.kind, group.number, group.suffix))
            status = "omitted" if re.match(r"^\[?\s*(omitted|repealed|repeal)\b", (title or first).strip(), re.I) else "in_force"
            scope = latest or group                  # the most recent heading: a Part or a Chapter
            current = {"kind": "rule" if numbering != "sections" else "section", "number": pid, "ref": ref,
                       "title": title or _first_words(first), "page": line.page, "status": status,
                       "chapter": _chapter_label(group or part), "chapter_title": (group or part).title if (group or part) else "",
                       "_scope": (_scope_id(scope), _scope_label(scope)) if scope else ("", ""), "_titled": titled}
            body = [first] if first else []
            continue
        if current is not None:
            body.append(text)
    flush()
    if schedules == "whole":
        provisions += schedule_parts
    counts = Counter(p["number"] for p in provisions)
    repeated = {n for n, c in counts.items() if c > 1}
    if numbering == "rules" and repeated:
        # Numbering starts again in each Part / Chapter: make the ids unique ("Pt. B r. 3", "Ch. IV r. 3").
        # If only a few numbers repeat (a table inside a book numbered straight through), only those get a prefix.
        everywhere = sum(counts[n] for n in repeated) > 0.3 * len(provisions)
        for p in provisions:
            scope_id, scope_label = p.get("_scope") or ("", "")
            if scope_id and p.get("kind") == "rule" and (everywhere or p["number"] in repeated):
                p["number"] = scope_id + p["number"]
                p["ref"] = f"{scope_label} {p['ref']}"
    for p in provisions:
        p.pop("_scope", None)
    found = _dedupe(provisions)
    if lower_after_first >= 2 and first_line >= 0 and len(_skip) < 4:
        # "21-A." in the table of contents was taken as the first section, so sections 5, 7, 10 ... after it
        # looked like going backwards: read again without that line and keep whichever finds more
        again = parse(lines, numbering=numbering, bold=bold, only_schedule=only_schedule, stop=stop, start=start,
                      schedules=schedules, allow_restart=allow_restart, title_headings=title_headings,
                      numbered_schedules=numbered_schedules, _skip=_skip | {first_line})
        if len(again) > len(found):
            return again
    return found


def _scope_id(group: Group) -> str:
    letter = group.suffix.endswith("*")
    if group.kind == "part":
        return "PT" + (group.suffix[:-1] if letter else str(group.number) + group.suffix)
    return "CH" + str(group.number) + group.suffix


def _scope_label(group: Group) -> str:
    if group.kind == "part":
        return "Pt. " + (group.suffix[:-1] if group.suffix.endswith("*") else int_to_roman(group.number) + group.suffix)
    return "Ch. " + int_to_roman(group.number) + group.suffix


def _left_margin(lines: list[Line], candidates: list[Line] | None = None) -> dict[int, float]:
    """Where numbered rules start, for odd and even pages (printed books shift the margin). {0: x, 1: x}.
    Footnotes and page furniture often sit further left, so numbered lines decide."""
    margins: dict[int, float] = {}
    for parity in (0, 1):
        numbered = Counter(round(line.x / 4) * 4 for line in (candidates or []) if line.x and line.page % 2 == parity)
        if numbered and sum(numbered.values()) >= 3:
            margins[parity] = numbered.most_common(1)[0][0]
    if len(margins) < 2:
        xs = Counter(round(line.x / 4) * 4 for line in lines if line.x)
        fallback = min((x for x, n in xs.items() if n >= max(5, len(lines) * 0.02)), default=0.0)
        for parity in (0, 1):
            margins.setdefault(parity, margins.get(1 - parity, fallback))
    return margins


def _plausible(key: tuple[int, str], last: tuple[int, str] | None, numbering: str, group: Group | None) -> bool:
    if last is None:
        return key[0] <= 3 or numbering in ("sections",) or group is None
    if key <= last:
        return False
    jump = 30 if numbering == "sections" else 15
    return key[0] - last[0] <= jump


# A heading that ends on a later line: "Proof of Aadhaar number necessary for receipt of certain subsidies, benefits
# and services," / "etc.—The Central Government ...". Words up to a full stop and a dash, no colon or new sentence.
WRAPPED_HEADING = re.compile(r"^([A-Z][^—–\n:;]{2,200}?[^\s(])\s*\.\s*[—–]+\s*")     # the longest seen: 182


def wrapped_title(title: str, text: str) -> tuple[str, str]:
    """A section heading too long for one line: the reader found no '.—' on the heading's line, took its first words
    for the title and kept the heading in the text (614 sections, 42 in the BNS). When the text starts with those
    words and the heading ends with '.—' a line or two later, the whole heading is the title and the text starts
    after it."""
    start = title.rstrip(".").rstrip()[:40]
    heading = WRAPPED_HEADING.match(text or "")
    if not start or not heading or not text.startswith(start):
        return title, text
    words = re.sub(r"\s+", " ", heading.group(1)).strip()
    if re.search(r"\.\s+[A-Z(]|!|\bChapter\b", words) or re.match(r"(?i)(explanation|illustration|provided)\b", words):
        return title, text                # "... shall be void. Explanation.—": a sentence, not a heading (or OCR noise)
    return words, text[heading.end():].lstrip()


def _first_words(text: str, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= limit:
        return text.rstrip(".")
    cut = text.rfind(" ", 0, limit)
    return text[:cut if cut > 30 else limit].rstrip(",;:") + "..."


def _chapter_label(group: Group | None) -> str:
    if not group:
        return ""
    if group.suffix.endswith("*"):
        return f"{group.kind.title()} {group.suffix[:-1]}"
    if group.kind == "order" and group.suffix.endswith("C"):
        letter = group.suffix[:-1]
        return f"Order {int_to_roman(group.number)}{('-' + letter) if letter else ''} (Commercial)"
    return f"{group.kind.title()} {int_to_roman(group.number)}{('-' + group.suffix) if group.suffix else ''}"


def _reference(numbering: str, number: str, group: Group | None) -> tuple[str, str]:
    """('O. VII r. 11', 'O7R11') - how it is cited, and the id part."""
    if numbering == "orders" and group:
        letter = group.suffix[:-1] if group.suffix.endswith("C") else group.suffix
        roman = int_to_roman(group.number) + (f"-{letter}" if letter else "")
        if group.suffix.endswith("C"):
            roman += " (Commercial)"        # the separate Order XI for commercial suits
        return f"O. {roman} r. {number}", f"O{group.number}{group.suffix}R{number}"
    if numbering in ("chapters", "paras") and group:
        if group.kind == "part" or group.suffix.endswith("*"):
            scope_label, scope_id = _scope_label(group), _scope_id(group)
        else:
            scope_label = "Ch. " + int_to_roman(group.number) + (f"-{group.suffix}" if group.suffix else "")
            scope_id = f"CH{group.number}{group.suffix}"
        if numbering == "paras":
            return f"{scope_label} para {number}", f"{scope_id}P{number}"
        return f"{scope_label} r. {number}", f"{scope_id}R{number}"
    if numbering in ("rules", "chapters", "orders", "paras"):
        return f"r. {number}", f"R{number}"
    return f"s. {number}", number


def _dedupe(provisions: list[dict]) -> list[dict]:
    """Same id twice (a rule repeated in a state amendment): keep the longer text, append the other."""
    by_id: dict[str, dict] = {}
    for provision in provisions:
        pid = provision["number"]
        if pid in by_id:
            by_id[pid]["text"] += "\n" + provision["text"]
        else:
            by_id[pid] = provision
    return list(by_id.values())


def page_provisions(lines: list[Line], min_chars: int = 40) -> list[dict]:
    """Fallback: one provision per page ('p. 12')."""
    pages: dict[int, list[str]] = {}
    chapters: dict[int, tuple[str, str]] = {}
    current = ("", "")
    pending = False
    for line in lines:
        pages.setdefault(line.page, []).append(line.text)
        heading = HEADING.match(line.text.strip()) if len(line.text) < 80 else None
        if heading and heading.group("kind").upper() == "CHAPTER" and not heading.group("pre"):
            parsed = heading_number(heading.group("num"))
            if parsed:
                current = (f"Chapter {int_to_roman(parsed[0])}{parsed[1]}", heading.group("rest").strip(" .-—"))
                pending = not current[1]
                chapters.setdefault(line.page, current)
                continue
        if pending and len(line.text) < 100:            # the chapter's title on the next line
            current, pending = (current[0], line.text.strip(" .")), False
            chapters[line.page] = current
        chapters.setdefault(line.page, current)
    out = []
    for page, texts in sorted(pages.items()):
        text = join_text(texts)
        if len(text) < min_chars:
            continue
        chapter, chapter_title = chapters.get(page, ("", ""))
        out.append({"kind": "page", "number": f"P{page}", "ref": f"p. {page}", "title": _first_words(text, 80),
                    "chapter": chapter, "chapter_title": chapter_title.title() if chapter_title.isupper() else chapter_title,
                    "text": text, "page": page, "status": "in_force"})
    return out


def parse_or_pages(lines: list[Line], numbering: str = "rules", only: bool = False, **options) -> tuple[list[dict], str]:
    """Try the given numbering, then the others (unless only=True); fall back to pages. Returns (provisions, how)."""
    total = sum(len(line.text) for line in lines)
    tried = [numbering] + ([] if only else [n for n in ("orders", "chapters", "rules", "sections") if n != numbering])
    candidates: list[tuple[list[dict], str]] = []
    for scheme in tried:
        # "rules" is also read without restarts: books numbered straight through (rule 95, 96, 97 ...)
        for allow_restart in ((True, False) if scheme == "rules" else (True,)):
            try:
                found = parse(lines, numbering=scheme, allow_restart=allow_restart, **options)
            except Exception:
                continue
            covered = sum(len(p["text"]) for p in found)
            enough = len(found) >= 5 or (len(found) >= 1 and total < 15000 and covered >= total * 0.4)   # short Acts
            if enough and covered >= total * 0.4:
                candidates.append((found, scheme))
    best, best_how = [], "pages"
    if candidates:
        def titled(found):            # rules with a real heading ("Caveat.—") - list items have none
            return sum(1 for p in found if p.get("_titled"))

        def ratio(found):            # share of entries that are real rules (the rest: table rows, list items)
            return titled(found) / max(1, len(found))

        requested = [c for c in candidates if c[1] == numbering]
        if requested:
            # The book's own numbering wins. For "rules", choose between "restarts in every chapter" and
            # "numbered straight through": the second when it finds about as many real rules with much less noise.
            best, best_how = requested[0]
            for other in requested[1:]:
                if titled(other[0]) >= titled(best) / 1.15 and ratio(other[0]) > ratio(best) + 0.1:
                    best, best_how = other
        else:
            best, best_how = max(candidates, key=lambda c: len(c[0]))
    if best:
        average = sum(len(p["text"]) for p in best) / len(best)
        pages = page_provisions(lines)
        if average > 5000 and len(pages) > 2 * len(best):
            return pages, "pages"           # a few giant 'rules' = the numbering was not really found
        return best, best_how
    return page_provisions(lines), "pages"


# ---------------------------------------------------------------------------
# Tables (Limitation Act Schedule, BNSS / CrPC First Schedule)
# ---------------------------------------------------------------------------
def _page_words(page) -> list[tuple[float, float, str, float]]:
    """Every word on the page as (x, y, text, font size) - words, not lines, so that two table cells
    printed close together are never glued into one line."""
    words = []
    for block in page.get_text("rawdict")["blocks"]:
        for line in block.get("lines", []):
            y = line["bbox"][1]
            current, x0, last_x1, size = "", None, None, 0.0
            for span in line["spans"]:
                if span["size"] >= 20:
                    continue
                if span["flags"] & 1 and span["size"] < 9:          # superscript footnote marker
                    continue
                for char in span["chars"]:
                    c = char["c"]
                    gap = char["bbox"][0] - last_x1 if last_x1 is not None else 0
                    if c.isspace() or (current and gap > 1.5):
                        if current:
                            words.append((x0, y, current, size, last_x1))
                        current, x0 = "", None
                    if not c.isspace():
                        if not current:
                            x0, size = char["bbox"][0], span["size"]
                        current += c
                    last_x1 = char["bbox"][2]
            if current:
                words.append((x0, y, current, size, last_x1))
    return words


def _gutters(words: list[tuple], digit_spans: list[tuple[float, float]]) -> list[float] | None:
    """Column boundaries for a table page. digit_spans are the x extents of the header digits 1..n.
    Text in a column is left-aligned, so a column's left edge is the most common word start between
    its header digit and the one before (justified lines spread their other words everywhere)."""
    if not words:
        return None
    # Count only words that begin a cell line: first on their visual line, or after a clear gap.
    # ("for" in "Imprisonment for ..." starts at the same x on every row but follows a normal space.)
    starts: Counter = Counter()
    ordered = sorted(words, key=lambda w: (w[1], w[0]))
    lines: list[list] = []
    for w in ordered:
        if lines and abs(lines[-1][0][1] - w[1]) < 2.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    for line in lines:
        previous_end = None
        for w in sorted(line, key=lambda w: w[0]):
            if previous_end is None or w[0] - previous_end >= 4:
                starts[round(w[0])] += 1
            previous_end = w[4]
    boundaries = []
    for (_a0, a1), (b0, _b1) in zip(digit_spans, digit_spans[1:]):
        window = [(n, x) for x, n in starts.items() if a1 < x <= b0 + 2]
        if not window:
            return None
        column_start = max(window, key=lambda pair: (pair[0], -pair[1]))[1]
        boundaries.append(column_start - 0.5)
    return boundaries


def _column_starts(page_lines: list[Line], digit_xs: list[float]) -> list[float]:
    """Column left edges for a table page. The header digits may be centred over their columns, so each
    column starts at the most common line start at or left of its digit (and right of the previous one)."""
    starts = Counter(round(line.x) for line in page_lines)
    columns: list[float] = []
    previous = -1.0
    for i, digit_x in enumerate(digit_xs):
        candidates = [(n, x) for x, n in starts.items() if previous + 8 < x <= digit_x + 2 and n >= 2]
        best = max(candidates, key=lambda c: (c[0], c[1]))[1] if candidates else digit_x
        columns.append(best)
        previous = best
    return columns


def table_rows(path: str | Path, first_page: int, last_page: int, columns: list[float], row_start: str,
               start_text: str | None = None, stop_text: str | None = None, group_pattern: str | None = None,
               major_pattern: str | None = None, header_digits: bool = False) -> list[dict]:
    """
    Read a ruled table where each column starts at a known x position. With header_digits=True the
    positions are taken from each page's column-number row ("1 2 3 4 5 6"), which the official PDFs
    repeat at the top of every page.
    Returns rows: {"cells": [str, ...], "group": "PART I.—SUITS RELATING TO ACCOUNTS", "page": n}.
    """
    import pymupdf

    doc = pymupdf.open(str(path))
    rows: list[dict] = []
    group_text = major_text = ""
    started = start_text is None
    size = None
    columns = list(columns)
    for index in range(first_page - 1, min(last_page, doc.page_count)):
        page = doc[index]
        height = page.rect.height
        page_lines = _page_lines(page, index + 1)
        if size is None:
            size = body_size(page_lines)
        top, bottom = 0.0, height
        events: list[tuple[float, str, str]] = []           # (y, "group"/"major", text)
        skip_ys: list[float] = []
        stop_here = False
        for line in sorted(page_lines, key=lambda l: l.y):
            text = line.text.strip()
            if not started:
                if re.search(start_text, text):
                    started, top = True, line.y + 2
                continue
            if stop_text and re.search(stop_text, text):
                bottom, stop_here = line.y - 1, True
                break
            if group_pattern and re.search(group_pattern, text):
                kind = "major" if major_pattern and re.search(major_pattern, text) else "group"
                events.append((line.y, kind, text))
                skip_ys.append(line.y)
            elif PAGE_NUMBER.match(text) and (line.y < 50 or line.y > height * 0.88):
                skip_ys.append(line.y)
        if not started:
            continue
        words = [w for w in _page_words(page) if top <= w[1] < bottom]
        words = [w for w in words if not (size and w[3] < size - 1.6)]                 # footnotes
        words = [w for w in words if not any(abs(w[1] - y) < 2 for y in skip_ys)]
        words.sort(key=lambda w: (round(w[1]), w[0]))
        if header_digits:
            # the "1 2 3 4 5 6" row: about the same y, the digits 1..n from left to right
            singles = [w for w in words if re.fullmatch(r"\d", w[2])]
            for one in (w for w in singles if w[2] == "1"):
                digits = sorted((w for w in singles if abs(w[1] - one[1]) < 5), key=lambda w: w[0])
                if [w[2] for w in digits] == [str(i + 1) for i in range(len(columns))]:
                    body_words = [w for w in words if w[1] > digits[0][1] + 3]
                    boundaries = _gutters(body_words, [(w[0], w[4]) for w in digits])
                    if boundaries:
                        columns = [0.0] + [b + 3 for b in boundaries]
                    else:
                        body_lines = [l for l in page_lines if l.y > digits[0][1] + 3 and l.size >= (size or 0) - 1.6]
                        columns = [c - 2 for c in _column_starts(body_lines, [w[0] for w in digits])]
                    words = [w for w in words if w not in digits]
                    break
        event_index = 0
        events.sort()
        for x, y, text, _size, _x1 in words:
            while event_index < len(events) and events[event_index][0] <= y:
                _, kind, heading = events[event_index]
                if kind == "major":
                    major_text = group_text = heading
                else:
                    group_text = f"{major_text} / {heading}" if major_text else heading
                event_index += 1
            column = max((i for i, cx in enumerate(columns) if x >= cx - 3), default=0)
            # first word of its line in the first column (a word further right on the same line doesn't count)
            first_in_cell = not rows or not any(abs(e[1] - y) < 3 and e[4] == index + 1 and e[0] == 0 and e[2] < x
                                                for e in rows[-1]["_lines"])
            if column == 0 and first_in_cell and re.match(row_start, text):
                new_row = {"cells": [""] * len(columns), "group": group_text, "page": index + 1, "_lines": []}
                if rows:   # cells of this row printed a hair higher than its number belong here, not to the row above
                    previous = rows[-1]["_lines"]
                    moved = [entry for entry in previous if entry[4] == index + 1 and entry[1] >= y - 3]
                    rows[-1]["_lines"] = [entry for entry in previous if entry not in moved]
                    new_row["_lines"] = moved
                rows.append(new_row)
            if not rows:
                continue
            rows[-1]["_lines"].append((column, y, x, text, index + 1))
        if stop_here:
            break
    for row in rows:
        by_column: dict[int, list] = {}
        for column, y, x, text, page in sorted(row.pop("_lines"), key=lambda e: (e[4], e[1], e[2])):
            by_column.setdefault(column, []).append((page, y, x, text))
        for column, parts in by_column.items():
            # Pieces printed on the same visual line (y within 3 points) are read left to right
            visual: list[list] = []
            for part in parts:
                if visual and visual[-1][0][0] == part[0] and abs(visual[-1][0][1] - part[1]) < 3:
                    visual[-1].append(part)
                else:
                    visual.append([part])
            texts = [" ".join(p[3] for p in sorted(group, key=lambda p: p[2])) for group in visual]
            cell = join_text(texts)
            row["cells"][column] = re.sub(r"(^|(?<=[\s(]))\d{1,2}\s?\[", "[", cell)   # footnote markers "1[When]"
    return rows
