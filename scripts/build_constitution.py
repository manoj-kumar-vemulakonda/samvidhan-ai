"""
Turn the official Constitution of India PDF into data/constitution.json.

You don't need to run this - data/constitution.json is already in the project.
Run it only to rebuild the data (for example when a new edition comes out):

    .venv\\Scripts\\python.exe scripts\\build_constitution.py

What it does:
1. Downloads the official English PDF from the Legislative Department
   (unless data/raw/constitution-of-india-en.pdf is already there).
2. Reads every line of text together with its font size and position.
   - Main text is 13-14 pt. Tiny 9 pt numbers are amendment markers -> removed.
   - Footnotes (amendment history) sit at the bottom of each page -> removed.
   - Page headers and page numbers -> removed.
3. Splits the text at Article headings like "21. Protection of life ...—",
   keeping track of which Part and Chapter each Article belongs to.
4. Marks repealed Articles (e.g. Article 31) as "omitted".
5. Cross-checks the result against an independent open dataset and prints a report.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import statistics
import sys
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path

import pymupdf

PROJECT_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_DIR / "data" / "raw"
PDF_PATH = RAW_DIR / "constitution-of-india-en.pdf"
OUT_PATH = PROJECT_DIR / "data" / "constitution.json"

OFFICIAL_PAGE = "https://legislative.gov.in/constitution-of-india"
OFFICIAL_PDF = "https://www.legislative.gov.in/static/uploads/2025/08/b9a933fb88c0a5afba254256bc55a3f0.pdf"
CROSSCHECK_URL = (
    "https://raw.githubusercontent.com/civictech-India/constitution-of-india/main/constitution_of_india.json"
)

# ---- Layout facts about this PDF (found by inspecting it) --------------------
MARKER_MAX_SIZE = 10.5     # amendment marker numbers are 9-10 pt
SMALL_MAX_SIZE = 12.8      # footnotes are 11-12.5 pt (body text is 13-14 pt)
HEADER_BOTTOM_Y = 96       # page header lines sit above this (they end at about y=81)
PAGE_CENTER_X = 308        # horizontal centre of the text column
BODY_LEFT_X = 100          # body lines start at x=80; indented paragraphs at ~117+
PARAGRAPH_GAP = 19.0       # vertical gap that means "new paragraph"

# "21.", "[21A.", "*[239A.", "[371-I." (the Goa Article is printed with a hyphen)
ARTICLE_HEADING = re.compile(r"^[*]?\s*\[?\s*(\d{1,3}-?[A-Z]{0,3})\s*\.(\s*\S|\s*$)")
CLAUSE_START = re.compile(r"^(\(\s*[0-9a-zA-Z]{1,5}\s*\)|Provided|Explanation|\*\s*\*)")
PART_LINE = re.compile(r"^PART\s+([IVXL]+[A-C]?)\b")
CHAPTER_LINE = re.compile(r"^CHAPTER\s+([IVX]+)\s*\.?\s*[—–-]\s*(.*)$")
PART_HEADER = re.compile(r"^\(Part\s+([IVXL]+[A-C]?)\s*\.?\s*[—–-]\s*(.+?)\)?$")
# "21. Title.—Text"   (newer Articles like 330A use ".-" instead of the long dash)
TITLE_SPLIT = re.compile(
    r"^(\d{1,3}-?[A-Z]{0,3})\s*\.\s*(.+?)\s*(?:[.]?\s*[.]?\s*[—–]|\.\s*-(?=\s|\())\s*(.*)$", re.DOTALL
)

SMALL_WORDS = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with"}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def download(url: str, path: Path) -> None:
    print(f"Downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(request, timeout=120) as response:
        path.write_bytes(response.read())


def article_sort_key(article_id: str) -> tuple[int, str]:
    """Order Articles naturally: 21 < 21A < 22, and 239A < 239AA < 239B."""
    match = re.match(r"(\d+)([A-Z]*)", article_id)
    return (int(match.group(1)), match.group(2)) if match else (0, article_id)


def title_case(text: str) -> str:
    """'THE UNION AND ITS TERRITORY' -> 'The Union and its Territory'."""
    words = text.lower().split()
    out = []
    for i, word in enumerate(words):
        if i > 0 and word in SMALL_WORDS:
            out.append(word)
        else:
            out.append(word[:1].upper() + word[1:])
    return " ".join(out)


def clean(text: str) -> str:
    """Tidy extracted text: drop amendment brackets and odd symbols, fix spacing."""
    text = text.replace("", "").replace("[", "").replace("]", "")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" ([,.;:)])", r"\1", text)
    text = re.sub(r"\( ", "(", text)
    text = re.sub(r" ?\n ?", "\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Step 1: read lines (with sizes and positions) from each page
# ---------------------------------------------------------------------------
def join_spans(spans: list[dict]) -> str:
    """Join text pieces left to right, adding a space where the PDF leaves a visible gap."""
    text, previous_x1 = "", None
    for span in spans:
        piece = span["text"]
        if previous_x1 is not None and span["bbox"][0] - previous_x1 > 1.5:
            if not text.endswith(" ") and not piece.startswith(" "):
                text += " "
        text += piece
        previous_x1 = span["bbox"][2]
    return re.sub(r"\s+", " ", text).strip()


class Line:
    """One visual line of a page (all text pieces on the same row, left to right)."""

    def __init__(self, spans: list[dict]):
        spans = sorted(spans, key=lambda s: s["bbox"][0])
        kept = [s for s in spans if s["size"] >= MARKER_MAX_SIZE]  # without tiny markers
        self.spans = kept  # the pieces, left to right (tables need their positions)
        # Tiny footnote markers on this line ("2", "*"): used to attach court-ruling footnotes
        self.markers: set[str] = set()
        for s in spans:
            t = s["text"].strip()
            if s["size"] < MARKER_MAX_SIZE and t:
                if re.fullmatch(r"[\d\s\[\]]+", t):
                    self.markers.update(re.findall(r"\d{1,2}", t))
                elif len(t) == 1 and not t.isalnum():
                    self.markers.add(t)
        # Tiny text left of the main text. Sometimes the PDF prints a paragraph number inside the
        # same tiny piece as its marker ("21." = marker 2 + paragraph "1.").
        first_x = kept[0]["bbox"][0] if kept else 0
        self.lead_small = "".join(
            s["text"] for s in spans if s["size"] < MARKER_MAX_SIZE and s["bbox"][2] <= first_x + 1
        ).strip()
        main = kept or spans
        self.x0 = min(s["bbox"][0] for s in spans)
        self.x1 = max(s["bbox"][2] for s in spans)
        self.y0 = min(s["bbox"][1] for s in main)  # markers sit higher, so ignore them
        self.full_text = join_spans(spans)  # including tiny markers and small caps
        self.text = join_spans(kept)
        chars: Counter = Counter()
        for s in spans:
            chars[round(s["size"], 1)] += len(s["text"].strip())
        self.size = chars.most_common(1)[0][0] if chars else 0
        # Is the first span that contains a digit bold? (Article numbers are bold.)
        first_number = next((s for s in kept if re.search(r"\d", s["text"])), None)
        self.number_is_bold = bool(first_number and first_number["flags"] & 16)
        if self.text.startswith("*") and not re.match(r"^\*\s*\*", self.text):
            self.markers.add("*")  # "*7. Bar of jurisdiction..." points to a "*" footnote

    @property
    def is_centered(self) -> bool:
        width = self.x1 - self.x0
        center = (self.x0 + self.x1) / 2
        return self.x0 > BODY_LEFT_X + 30 and abs(center - PAGE_CENTER_X) < 22 and width < 360

    def __repr__(self) -> str:
        return f"<{self.size} x={self.x0:.0f} y={self.y0:.0f}> {self.text}"


def page_lines(page) -> list[Line]:
    """
    All lines of one page in reading order.

    The PDF doesn't always store text top-to-bottom, and one visual row can be
    split into several pieces. So: sort pieces by their vertical middle and
    merge pieces that sit on the same row.
    """

    def middle(spans: list[dict]) -> float:
        main = [s for s in spans if s["size"] >= MARKER_MAX_SIZE] or spans
        return sum((s["bbox"][1] + s["bbox"][3]) / 2 for s in main) / len(main)

    pieces = []
    for block in page.get_text("dict")["blocks"]:
        for raw in block.get("lines", []):
            spans = [s for s in raw["spans"] if s["text"].strip()]
            if spans:
                pieces.append((middle(spans), spans))
    pieces.sort(key=lambda p: p[0])

    rows: list[list] = []  # [vertical middle, spans]
    for mid, spans in pieces:
        if rows and abs(mid - rows[-1][0]) < 4:
            rows[-1][1].extend(spans)
        else:
            rows.append([mid, list(spans)])
    return [Line(spans) for _, spans in rows]


def is_footnote_start(line: Line) -> bool:
    return (
        line.size <= SMALL_MAX_SIZE
        and line.x0 < BODY_LEFT_X
        and bool(re.match(r"^(\d{1,2}\s*\.||\*)", line.full_text))
    )


COURT_NOTE = re.compile(r"struck down|declared invalid|unconstitutional", re.IGNORECASE)


def page_footnotes(page) -> dict[str, str]:
    """The footnotes at the bottom of a page, by marker: {"2": "Subs. by the ...", "*": "..."}."""
    notes: dict[str, str] = {}
    key = None
    for line in page_lines(page):
        if line.y0 < HEADER_BOTTOM_Y:
            continue
        if is_footnote_start(line):
            match = re.match(r"^\s*(?:(\d{1,2})\s*\.|(\*)|([^\w\s]))\s*", line.full_text)
            key = (match.group(1) or match.group(2) or match.group(3)) if match else None
            if key:
                notes[key] = line.full_text[match.end():].strip()
        elif key and line.size <= SMALL_MAX_SIZE:
            # "Advocates-" + "on-Record": a line ending in a hyphen continues without a space
            notes[key] += ("" if notes[key].endswith("-") else " ") + line.full_text.strip()
    return {k: re.sub(r"\s+", " ", v) for k, v in notes.items()}


def court_notes(page) -> dict[str, str]:
    """Only the footnotes that report a court ruling, e.g. 'struck down by the Supreme Court in ...'."""
    return {k: v for k, v in page_footnotes(page).items() if COURT_NOTE.search(v)}


def body_lines(page) -> tuple[list[Line], str | None]:
    """Remove header, page number and footnotes. Also return the page's Part header, if any."""
    part_header = None
    kept: list[Line] = []
    for line in page_lines(page):
        if line.y0 < HEADER_BOTTOM_Y:
            if PART_HEADER.match(line.full_text):
                part_header = line.full_text
            continue
        if re.fullmatch(r"\d{1,3}", line.text) and line.y0 > 700:
            continue  # page number at the bottom
        if is_footnote_start(line):
            break  # everything from here down is footnotes
        if line.text:
            kept.append(line)
    return kept, part_header


# ---------------------------------------------------------------------------
# Step 2: walk through the lines and cut them into Articles
# ---------------------------------------------------------------------------
def attach_court_notes(page_marks: list[tuple[dict, set]], notes: dict[str, str]) -> None:
    """
    Give each Article the court-ruling footnotes whose marker appears in its text on this page.
    Example: the 99th Amendment footnotes on Article 124A say it was struck down in 2015.
    """
    for record, markers in page_marks:
        for key in sorted(markers & notes.keys()):
            if notes[key] not in record.setdefault("notes", []):
                record["notes"].append(notes[key])


def is_article_heading(line: Line) -> str | None:
    match = ARTICLE_HEADING.match(line.text)
    if match and line.number_is_bold and line.x0 < 160:
        return match.group(1).replace("-", "")
    return None


def parse(pdf) -> tuple[dict, list[dict], dict[str, str], int]:
    """Returns the Preamble, the Articles, Part titles from page headers, and the page where the Schedules start."""
    # Find the first page of the main text (the Preamble) and stop at the First Schedule.
    start = next(
        i for i in range(pdf.page_count)
        if any(l.text.replace(" ", "").upper() == "PREAMBLE" or l.full_text == "PREAMBLE"
               for l in page_lines(pdf[i]) if l.is_centered)
    )
    print(f"Main text starts on page {start + 1}")

    preamble_lines: list[str] = []
    articles: list[dict] = []
    part_titles_from_headers: dict[str, Counter] = {}
    current: dict | None = None
    part_id, part_title_lines, chapter = "", [], ""
    collecting = "preamble"  # preamble -> part_title -> chapter_title -> article
    last_key = (0, "")
    previous: Line | None = None

    def finish_current() -> None:
        if current:
            articles.append(current)

    for page_index in range(start, pdf.page_count):
        lines, header = body_lines(pdf[page_index])
        if header:
            h = PART_HEADER.match(header)
            part_titles_from_headers.setdefault(h.group(1), Counter())[h.group(2).strip(" .)")] += 1

        # (Article, footnote markers) for every line of this page - see attach_court_notes below
        page_marks: list[tuple[dict, set]] = []

        for n, line in enumerate(lines):
            plain = line.text.replace("[", "").replace("]", "").strip()
            upper_plain = re.sub(r"[^A-Za-z]", "", plain)

            # The end of the Articles: the First Schedule begins after Article 395
            if "FIRST SCHEDULE" in plain.upper() and line.is_centered and last_key >= (395, ""):
                finish_current()
                return {"text": preamble_lines}, articles, {
                    k: c.most_common(1)[0][0] for k, c in part_titles_from_headers.items()
                }, page_index

            # Title lines on the Preamble page
            if collecting == "preamble" and line.is_centered and plain.upper() in {
                "THE CONSTITUTION OF INDIA", "PREAMBLE", "P REAMBLE"
            }:
                continue

            part_match = PART_LINE.match(plain)
            if part_match and line.is_centered:
                finish_current()
                current = None
                part_id, part_title_lines, chapter = part_match.group(1), [], ""
                collecting = "part_title"
                previous = line
                continue

            chapter_match = CHAPTER_LINE.match(plain)
            if chapter_match and line.is_centered:
                finish_current()
                current = None
                chapter = f"Chapter {chapter_match.group(1)} - {title_case(chapter_match.group(2))}"
                collecting = "chapter_title"
                previous = line
                continue

            article_id = is_article_heading(line)
            if article_id and collecting != "preamble":
                key = article_sort_key(article_id)
                if key > last_key:
                    finish_current()
                    last_key = key
                    current = {
                        "id": article_id,
                        "part": part_id,
                        "part_title_raw": " ".join(part_title_lines),
                        "chapter": chapter,
                        "lines": [plain],
                        # "[" minus "]" in this Article's text: inserted text is printed in
                        # brackets, and one bracket can span several Articles (124A to 124C)
                        "brackets": line.text.count("[") - line.text.count("]"),
                    }
                    page_marks.append((current, line.markers))
                    collecting = "article"
                    previous = line
                    continue
                print(f"  note: ignored out-of-order heading {article_id} on page {page_index + 1}")

            # Centered upper-case lines right after PART / CHAPTER lines are their titles
            if collecting == "part_title" and line.is_centered and upper_plain.isupper():
                part_title_lines.append(plain)
                previous = line
                continue
            if collecting == "chapter_title" and line.is_centered and upper_plain.isupper():
                chapter += " " + title_case(plain)
                previous = line
                continue

            # A centered sub-heading (like "Right to Equality") just before an Article: skip it
            next_line = lines[n + 1] if n + 1 < len(lines) else None
            if (
                line.is_centered
                and not re.search(r"[.,;:—–-]$", plain)
                and next_line is not None
                and (is_article_heading(next_line) or next_line.is_centered)
            ):
                previous = line
                continue

            if collecting == "preamble":
                new_paragraph = previous is not None and line.y0 - previous.y0 > PARAGRAPH_GAP
                preamble_lines.append(("\n" if new_paragraph else " ") + plain)
                previous = line
                continue

            if current is None:
                previous = line
                continue  # stray text between headings (e.g. "* * *" for an omitted Part)

            # Normal body text of the current Article: decide whether this starts a new paragraph
            same_page = previous is not None and line.y0 > previous.y0
            gap = line.y0 - previous.y0 if same_page else 0
            new_paragraph = bool(CLAUSE_START.match(plain)) or gap > PARAGRAPH_GAP or (
                line.x0 > BODY_LEFT_X and current["lines"] and re.search(r"[.:;—]$", current["lines"][-1])
            )
            current["lines"].append(("\n" if new_paragraph else "") + plain)
            current["brackets"] += line.text.count("[") - line.text.count("]")
            page_marks.append((current, line.markers))
            previous = line

        attach_court_notes(page_marks, court_notes(pdf[page_index]))

    finish_current()
    headers = {k: c.most_common(1)[0][0] for k, c in part_titles_from_headers.items()}
    return {"text": preamble_lines}, articles, headers, pdf.page_count


def join_lines(lines: list[str]) -> str:
    """Join PDF lines into text: a line that starts with '\\n' begins a new paragraph."""
    text = ""
    for piece in lines:
        if piece.startswith("\n"):
            text += "\n" + piece[1:]
        elif text.endswith(("-", "—")) or not text:
            text += piece
        else:
            text += " " + piece
    return clean(text)


def finalise(preamble: dict, raw_articles: list[dict], header_titles: dict[str, str]) -> tuple[list, list]:
    articles = [
        {
            "id": "Preamble",
            "title": "Preamble",
            "part": "",
            "part_title": "Preamble",
            "chapter": "",
            "status": "in_force",
            "text": clean("".join(preamble["text"])),
            "notes": [],
        }
    ]
    parts: list[dict] = [{"id": "", "title": "Preamble", "status": "in_force", "articles": ["Preamble"]}]

    # A struck-down insertion ("Ins. by the Constitution (Ninety-ninth Amendment) Act ... struck
    # down") has one footnote marker, but its bracket can cover the next Articles too (124A-124C).
    for i, raw in enumerate(raw_articles):
        inserted_notes = [note for note in raw.get("notes", []) if note.startswith("Ins.")]
        balance = raw.get("brackets", 0)
        for following in raw_articles[i + 1:]:
            if not inserted_notes or balance <= 0:
                break
            for note in inserted_notes:
                if note not in following.setdefault("notes", []):
                    following["notes"].append(note)
            balance += following.get("brackets", 0)

    for raw in raw_articles:
        full = join_lines(raw["lines"]).lstrip("* ")
        match = TITLE_SPLIT.match(full)
        if match:
            title = re.sub(r"\s+", " ", match.group(2)).strip(" .")
            body = match.group(3).strip()
        else:  # no "—" after the title: use the first sentence as the title
            title, _, body = full.partition(". ")
            title = re.sub(r"^\d{1,3}-?[A-Z]{0,3}\.\s*", "", title).strip(" .")
        # Repealed Articles keep only a note like "Omitted by the Constitution (...) Act"
        is_note = len(body) < 400 and re.search(r"\b(subs|omitted|rep)\b\.?.*Amendment\)\s*Act", body, re.I)
        status = "omitted" if re.match(r"^(Omitted|Rep\.|Repealed)", body) or is_note else "in_force"

        # Part title: the page headers have nice casing ("Fundamental Rights"); fall back
        # to the heading under "PART ..." when a header is missing or garbled.
        from_heading = title_case(raw["part_title_raw"])
        from_header = header_titles.get(raw["part"], "")
        if from_header.isupper():
            from_header = title_case(from_header)
        part_title = from_header if len(from_header) >= 0.6 * len(from_heading) else from_heading
        articles.append(
            {
                "id": raw["id"],
                "title": title,
                "part": raw["part"],
                "part_title": part_title,
                "chapter": raw["chapter"].strip(),
                "status": status,
                "text": body or full,
                "notes": raw.get("notes", []),  # official footnotes about court rulings
            }
        )
        if not parts or parts[-1]["id"] != raw["part"]:
            parts.append({"id": raw["part"], "title": part_title, "status": "in_force", "articles": []})
        parts[-1]["articles"].append(raw["id"])

    by_id = {a["id"]: a for a in articles}
    for part in parts:
        if part["articles"] and all(by_id[a]["status"] == "omitted" for a in part["articles"]):
            part["status"] = "omitted"
    return articles, parts


# ---------------------------------------------------------------------------
# Step 2b: the Schedules (they follow Article 395)
# ---------------------------------------------------------------------------
ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth",
            "Ninth", "Tenth", "Eleventh", "Twelfth"]
SCHEDULE_HEADING = re.compile(r"^\[?\s*(" + "|".join(o.upper() for o in ORDINALS) + r")\s+SCHEDULE\b")
SCHEDULE_REFS = re.compile(r"^[\[(]\s*(Articles?\s+.+?)\s*[\])]$")       # "(Article 246)"
# The number at the start of an item: "1.", "2A.", "[25.]", or "[33* * *" (an omitted entry)
NUMBERED = re.compile(r"^[\[\s*]*(\d{1,3}[A-Z]{0,2})[\]\s]*(?:\.[\]\s]*|(?=\*))")
ONLY_STARS = re.compile(r"[\s*\[\]]+")   # "* * * * *": something omitted by an amendment
LEADER_DOTS = re.compile(r"\s*(?:\.{4,}|…+)\s*")                       # "Assam ........ 7"
SCHEDULE_PARAGRAPH_GAP = 23.0  # Schedules are printed with slightly looser line spacing
TABLE_COLUMN_X = 186           # First Schedule: State names left of this, territories right of it
OMITTED_NOTE = "* * * (Omitted by a constitutional amendment - no longer in force.)"

# How each Schedule is cut into pieces that can be cited on their own
SCHEDULE_LAYOUT = {1: "table", 2: "parts", 3: "forms", 4: "whole", 5: "paragraphs", 6: "paragraphs",
                   7: "lists", 8: "whole", 9: "whole", 10: "paragraphs", 11: "whole", 12: "whole"}
# Short descriptions for the table of contents (the PDF's own headings are inconsistent)
SCHEDULE_TITLES = {
    1: "The States and the Union territories",
    2: "Salaries and allowances of the President, Governors, presiding officers, judges and the CAG",
    3: "Forms of oaths or affirmations",
    4: "Seats in the Council of States (Rajya Sabha)",
    5: "Scheduled Areas and Scheduled Tribes",
    6: "Tribal areas in Assam, Meghalaya, Tripura and Mizoram",
    7: "Union, State and Concurrent Lists",
    8: "Languages",
    9: "Acts and Regulations protected by Article 31B",
    10: "Disqualification on ground of defection (anti-defection law)",
    11: "Powers of Panchayats",
    12: "Powers of Municipalities",
}


class Row:
    """One line of a Schedule, with its position (needed to rebuild paragraphs and tables)."""

    def __init__(self, line: Line, page: int):
        self.text = line.text.strip()
        self.x0, self.y0, self.page = line.x0, line.y0, page
        self.size, self.centered, self.spans = line.size, line.is_centered, line.spans
        self.markers, self.lead_small = line.markers, line.lead_small

    def with_text(self, text: str) -> "Row":
        copy = Row.__new__(Row)
        copy.__dict__.update(self.__dict__, text=text)
        return copy


def schedule_ranges(pdf, first_page: int) -> list[tuple[int, int, int]]:
    """(Schedule number, first page, page after its last) - every Schedule starts on a new page."""
    starts, end = [], pdf.page_count
    for i in range(first_page, pdf.page_count):
        top = [l for l in page_lines(pdf[i]) if HEADER_BOTTOM_Y <= l.y0 < 200 and l.is_centered]
        if any(l.text.strip().startswith("APPENDIX") for l in top):
            end = i
            break
        for line in top:
            match = SCHEDULE_HEADING.match(line.text.strip())
            if match:
                starts.append((ORDINALS.index(match.group(1).title()) + 1, i))
                break
    return [(n, page, starts[k + 1][1] if k + 1 < len(starts) else end) for k, (n, page) in enumerate(starts)]


def rows_to_text(rows: list[Row]) -> str:
    """Join rows into text: numbered items, clauses and big gaps start new paragraphs."""
    pieces: list[str] = []
    previous: Row | None = None
    for row in rows:
        gap = row.y0 - previous.y0 if previous is not None and row.page == previous.page else 0
        starts_item = bool(CLAUSE_START.match(row.text) or NUMBERED.match(row.text))
        if re.match(r"^\*+\s*[a-z]", row.text):
            starts_item = False  # "*** the following ..." - omitted words inside a sentence
        new_paragraph = bool(pieces) and (
            starts_item
            or gap > SCHEDULE_PARAGRAPH_GAP
            or (row.x0 > BODY_LEFT_X and bool(re.search(r"[.:;—]$", pieces[-1])))
        )
        pieces.append(("\n" if new_paragraph else "") + row.text)
        previous = row
    text = re.sub(r"(?<=[^.:;—”])\n(?=[a-z])", " ", join_lines(pieces))  # no break inside a sentence
    return LEADER_DOTS.sub(" - ", text)


def short_title(text: str, limit: int = 90) -> str:
    """The first line of an entry, cut to fit in a list: 'Defence of India and every part thereof…'."""
    first = text.split("\n")[0].strip().rstrip(".;:,")
    if len(first) <= limit:
        return first
    return first[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def is_uppercase_heading(row: Row) -> bool:
    letters = re.sub(r"[^A-Za-z]", "", row.text)
    return len(letters) > 2 and letters.isupper()


def split_lists(rows: list[Row]) -> list[dict]:
    """Seventh Schedule: List I/II/III, each with numbered entries."""
    pieces, current, list_no, list_title, last_key = [], None, 0, "", (0, "")
    for row in rows:
        heading = re.match(r"^List\s+(I{1,3})\s*[—–-]\s*(.+)$", row.text)
        if heading and row.centered:
            list_no, list_title, last_key, current = len(heading.group(1)), row.text, (0, ""), None
            continue
        number = NUMBERED.match(row.text)
        if number and list_no and 100 <= row.x0 < 140 and article_sort_key(number.group(1)) > last_key:
            last_key = article_sort_key(number.group(1))
            roman = "I" * list_no
            current = {
                "suffix": f"{roman}-{number.group(1)}",
                "cite": f"List {roman}, Entry {number.group(1)}",
                "group": f"S7-{roman}", "group_title": list_title, "chapter": list_title, "rows": [],
            }
            pieces.append(current)
            rest = row.text[number.end():].strip()
            if rest:
                current["rows"].append(row.with_text(rest))
        elif current is not None:
            current["rows"].append(row)
    for piece in pieces:
        piece["text"] = rows_to_text(piece["rows"])
    return pieces


def paragraph_number(row: Row, last_key: tuple[int, str]) -> tuple[str, Row] | None:
    """If this row starts the next numbered paragraph, return its number (and the row, number first)."""
    if row.x0 <= 105:  # paragraph headings are indented; their continuation lines are not
        return None
    number = NUMBERED.match(row.text)
    if number:
        rest = row.text[number.end():]
        if re.match(r"[\[\s]*([A-Z]|\*)", rest) and article_sort_key(number.group(1)) > last_key:
            return number.group(1), row
        return None
    # The number printed inside the same tiny piece as a footnote marker: "21." = marker 2 + "1.",
    # "214." = marker 2 + "14.". Pick the reading that comes next in order.
    hidden = re.fullmatch(r"\*?\s*(\d{1,4})([A-Z]{0,2})\s*\.", row.lead_small)
    if hidden and re.match(r"[\[\s]*[A-Z]", row.text):
        digits, letters = hidden.groups()
        options = [digits[i:] + letters for i in range(len(digits))]
        options = [o for o in options if last_key < article_sort_key(o) <= (last_key[0] + 3, "ZZ")]
        if options:
            best = min(options, key=article_sort_key)
            return best, row.with_text(f"{best}. {row.text}")
    return None


def split_paragraphs(rows: list[Row], n: int) -> list[dict]:
    """Fifth, Sixth and Tenth Schedules: numbered paragraphs like '2. Disqualification ...—'."""
    pieces, current, last_key = [], None, (0, "")
    part_letter, part_words, in_part_title = "", [], False   # Fifth Schedule: "PART A" + "GENERAL"
    for row in rows:
        part = re.match(r"^PART\s+([A-D])$", row.text)
        if part and row.centered:
            part_letter, part_words, in_part_title = part.group(1), [], True
            continue
        if in_part_title and is_uppercase_heading(row):
            part_words.append(row.text.strip(" *"))
            continue
        in_part_title = False
        found = paragraph_number(row, last_key)
        if found:
            number, row = found
            chapter = f"Part {part_letter} - {title_case(' '.join(part_words))}" if part_letter else ""

            def new_piece(num: str, first_row: Row) -> dict:
                return {"suffix": f"P{num}", "cite": f"Paragraph {num}", "number": num,
                        "group": f"S{n}", "chapter": chapter, "rows": [first_row]}

            # An omitted paragraph is printed as just "* * * * *" (its number is only in a
            # footnote), e.g. paragraph 3 of the Tenth Schedule between paragraphs 2 and 4.
            if (
                current and ONLY_STARS.fullmatch(current["rows"][-1].text)
                and number.isdigit() and current["number"].isdigit()
                and int(number) == int(current["number"]) + 2
            ):
                missing = str(int(number) - 1)
                stars = current["rows"].pop()
                pieces.append(new_piece(missing, stars.with_text(f"{missing}. * * * * *")))
            last_key = article_sort_key(number)
            current = new_piece(number, row)
            pieces.append(current)
        elif current is not None:
            current["rows"].append(row)
    for piece in pieces:
        full = rows_to_text(piece["rows"]).lstrip("* ")
        match = TITLE_SPLIT.match(full)
        if match:
            piece["title"] = re.sub(r"\s+", " ", match.group(2)).strip(" .")
            piece["text"] = match.group(3).strip()
        else:  # an omitted paragraph: "3. * * * * *"
            piece["text"] = NUMBERED.sub("", full, count=1).strip()
    return pieces


def split_parts(rows: list[Row]) -> list[dict]:
    """Second Schedule: Parts A to E, each with a small-capitals title."""
    pieces, current, in_title = [], None, False
    for row in rows:
        part = re.match(r"^\[?\s*PART\s+([A-E])\s*\]?$", row.text)
        if part and row.centered:
            current = {"suffix": part.group(1), "cite": f"Part {part.group(1)}", "group": "S2",
                       "chapter": "", "title_rows": [], "rows": []}
            pieces.append(current)
            in_title = True
            continue
        if current is None:
            continue
        if in_title and is_uppercase_heading(row) and row.size < SMALL_MAX_SIZE:
            current["title_rows"].append(row.text)
            continue
        in_title = False
        current["rows"].append(row)
    for piece in pieces:
        piece["title"] = title_case(re.sub(r"\*+", "", " ".join(piece["title_rows"])).strip())
        piece["text"] = rows_to_text(piece["rows"])
    return pieces


def split_forms(rows: list[Row]) -> list[dict]:
    """Third Schedule: Forms I to VIII (III and VII have parts A and B)."""
    pieces, current, letter = [], None, ""
    for row in rows:
        form = re.match(r"^\[?\s*(VIII|VII|VI|IV|V|III|II|I)\s*\]?$", row.text)
        if form and row.centered:
            current = {"suffix": form.group(1), "cite": f"Form {form.group(1)}", "group": "S3",
                       "chapter": "", "rows": []}
            pieces.append(current)
            continue
        if current is None:
            continue
        if row.centered and row.text in {"A", "B"}:
            letter = row.text
            continue
        if row.centered and row.text.startswith("solemnly affirm") and current["rows"]:
            # The printed form offers two choices, one above the other:
            #   "do swear in the name of God / solemnly affirm"
            last = current["rows"][-1]
            merged = last.text.replace("in the name of God", "in the name of God/solemnly affirm", 1)
            if merged == last.text:
                merged += " (or solemnly affirm)"
            current["rows"][-1] = last.with_text(merged)
            continue
        if letter:
            row, letter = row.with_text(f"({letter}) {row.text}"), ""
        current["rows"].append(row)
    for piece in pieces:
        piece["text"] = re.sub(r"in the name of God\s+solemnly affirm", "in the name of God/solemnly affirm",
                               rows_to_text(piece["rows"]))
        names = re.findall(r"Form of (.+?)\s*:?\s*[—–]", piece["text"].replace("\n", " "))
        title = " / ".join(dict.fromkeys(names)) or short_title(piece["text"])
        piece["title"] = title[:1].upper() + title[1:]
    return pieces


def split_states_table(rows: list[Row]) -> list[dict]:
    """First Schedule: a two-column table (Name | Territories) for the States, then the Union territories."""
    pieces, entry = [], None
    for row in rows:
        section = re.match(r"^(I{1,2})\.\s*THE\s+(STATES|UNION TERRITORIES)", row.text)
        if section:
            title = "The States" if section.group(2) == "STATES" else "The Union territories"
            pieces.append({"suffix": section.group(1), "cite": f"Part {section.group(1)}", "group": "S1",
                           "chapter": "", "title": title, "entries": []})
            entry = None
            continue
        if not pieces or re.match(r"^Name\s+(Territories|Extent)$", row.text) or not re.search(r"[A-Za-z0-9]", row.text):
            continue
        left = join_spans([s for s in row.spans if s["bbox"][0] < TABLE_COLUMN_X])
        right = join_spans([s for s in row.spans if s["bbox"][0] >= TABLE_COLUMN_X])
        number = NUMBERED.match(left)
        if number:
            entry = {"number": number.group(1), "name": [left[number.end():]], "text": [right]}
            pieces[-1]["entries"].append(entry)
        elif entry is not None:
            entry["name"].append(left)
            entry["text"].append(right)
    for piece in pieces:
        piece["text"] = "\n".join(
            f"{e['number']}. {clean(' '.join(e['name']))}: {join_lines([t for t in e['text'] if t])}"
            for e in piece["entries"]
        )
    return pieces


def whole_schedule(rows: list[Row], n: int) -> list[dict]:
    """Short Schedules (and the long list in the Ninth) are kept as one piece."""
    start = next((k for k, r in enumerate(rows) if NUMBERED.match(r.text) or r.text.startswith("To each")), 0)
    kept = [r for r in rows[start:] if r.text != "TABLE"]
    return [{"suffix": "", "cite": "", "group": f"S{n}", "chapter": "",
             "title": SCHEDULE_TITLES[n], "text": rows_to_text(kept), "rows": kept}]


def parse_schedules(pdf, first_page: int) -> tuple[list[dict], list[dict]]:
    """All twelve Schedules, cut into citable pieces, plus their table-of-contents groups."""
    units: list[dict] = []
    groups: dict[str, dict] = {}
    notes_by_page: dict[int, dict[str, str]] = {}

    def notes_for(rows: list[Row]) -> list[str]:
        found: list[str] = []
        for row in rows:
            page_notes = notes_by_page.setdefault(row.page, court_notes(pdf[row.page]))
            for key in sorted(row.markers & page_notes.keys()):
                if page_notes[key] not in found:
                    found.append(page_notes[key])
        return found

    for n, start, end in schedule_ranges(pdf, first_page):
        rows = [Row(line, i) for i in range(start, end) for line in body_lines(pdf[i])[0]]
        # Drop the heading ("SEVENTH SCHEDULE") and read the Articles it belongs to ("(Article 246)")
        refs, head = "", 0
        for k, row in enumerate(rows[:4]):
            if SCHEDULE_HEADING.match(row.text):
                head = k + 1
            refs_match = SCHEDULE_REFS.match(row.text)
            if refs_match:
                refs, head = re.sub(r"\s+\(", "(", refs_match.group(1)), k + 1
                break
        rows = rows[head:]

        layout = SCHEDULE_LAYOUT[n]
        if layout == "lists":
            pieces = split_lists(rows)
        elif layout == "paragraphs":
            pieces = split_paragraphs(rows, n)
        elif layout == "parts":
            pieces = split_parts(rows)
        elif layout == "forms":
            pieces = split_forms(rows)
        elif layout == "table":
            pieces = split_states_table(rows)
        else:
            pieces = whole_schedule(rows, n)

        ordinal = ORDINALS[n - 1]
        for piece in pieces:
            body = clean(piece["text"])
            omitted = not re.sub(r"[\s*.\[\]]", "", body) or bool(re.match(r"^(Omitted|Rep\.)", body))
            unit = {
                "id": f"S{n}" + (f"-{piece['suffix']}" if piece["suffix"] else ""),
                "title": "Omitted" if omitted else (piece.get("title") or short_title(body)),
                "part": piece["group"],
                "part_title": f"{ordinal} Schedule",
                "chapter": piece["chapter"],
                "status": "omitted" if omitted else "in_force",
                "text": OMITTED_NOTE if omitted else body,
                "kind": "schedule",
                "cite": f"{ordinal} Schedule" + (f", {piece['cite']}" if piece["cite"] else ""),
                "refs": refs,
                "notes": notes_for(piece.get("rows", [])),
            }
            units.append(unit)
            group_title = f"{ordinal} Schedule - " + piece.get("group_title", SCHEDULE_TITLES[n])
            group = groups.setdefault(piece["group"], {
                "id": piece["group"], "title": group_title, "status": "in_force",
                "kind": "schedule", "articles": [],
            })
            group["articles"].append(unit["id"])
    return units, list(groups.values())


def edition_info(pdf) -> dict:
    first_pages = " ".join(pdf[i].get_text() for i in range(3))
    as_on = re.search(r"\[As on ([^\]]+)\]", first_pages)
    amendment = re.search(r"up to the\s+(Constitution\s*\([^)]+\)\s*Act,\s*\d{4})", first_pages)
    return {
        "title": "The Constitution of India",
        "as_on": re.sub(r"\s+", " ", as_on.group(1)).strip() if as_on else "",
        "latest_amendment": re.sub(r"\s+", " ", amendment.group(1)).strip() if amendment else "",
        "publisher": "Government of India, Ministry of Law and Justice, Legislative Department",
        "page_url": OFFICIAL_PAGE,
        "pdf_url": OFFICIAL_PDF,
        "built_on": date.today().isoformat(),
    }


# ---------------------------------------------------------------------------
# Step 3: cross-check against an independent copy
# ---------------------------------------------------------------------------
def cross_check(articles: list[dict]) -> None:
    path = RAW_DIR / "civictech_constitution.json"
    try:
        if not path.exists():
            download(CROSSCHECK_URL, path)
        other = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # the check is helpful but optional
        print(f"Cross-check skipped ({error})")
        return

    ours = {a["id"]: a for a in articles}
    theirs = {("Preamble" if str(o["article"]) == "0" else str(o["article"]).upper()): o for o in other}
    missing = sorted(set(theirs) - set(ours), key=article_sort_key)
    extra = sorted(set(ours) - set(theirs), key=article_sort_key)
    print("\n--- Cross-check against civictech-India/constitution-of-india ---")
    print(f"Ours: {len(ours)} entries | theirs: {len(theirs)} entries")
    print(f"In theirs but missing from ours ({len(missing)}): {missing}")
    print(f"Only in ours, e.g. newer amendments ({len(extra)}): {extra}")
    mismatched = []
    for article_id in set(ours) & set(theirs):
        a = ours[article_id]["title"].lower()
        b = str(theirs[article_id]["title"]).lower().strip(" .[]")
        if difflib.SequenceMatcher(None, a, b).ratio() < 0.6:
            mismatched.append((article_id, ours[article_id]["title"], theirs[article_id]["title"]))
    print(f"Titles that differ a lot ({len(mismatched)}):")
    for row in sorted(mismatched, key=lambda r: article_sort_key(r[0]))[:40]:
        print(f"  {row[0]}: ours={row[1]!r} | theirs={row[2]!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdf", type=Path, default=PDF_PATH, help="path to the official PDF")
    parser.add_argument("--no-crosscheck", action="store_true")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if not args.pdf.exists():
        download(OFFICIAL_PDF, args.pdf)

    pdf = pymupdf.open(args.pdf)
    preamble, raw_articles, header_titles, schedules_page = parse(pdf)
    articles, parts = finalise(preamble, raw_articles, header_titles)
    schedules, schedule_groups = parse_schedules(pdf, schedules_page)

    data = {
        "source": edition_info(pdf),
        "parts": parts + schedule_groups,
        "articles": articles,
        "schedules": schedules,
    }
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    omitted = [a["id"] for a in articles if a["status"] == "omitted"]
    lengths = [len(a["text"]) for a in articles]
    print(f"\nEdition: {data['source']['as_on']} | latest amendment: {data['source']['latest_amendment']}")
    print(f"Saved {len(articles)} entries (Preamble + Articles) in {len(parts)} groups to {OUT_PATH}")
    print(f"Omitted Articles ({len(omitted)}): {omitted}")
    print(f"Text length per Article: median {statistics.median(lengths):.0f}, max {max(lengths)} characters")
    odd = [
        (a["id"], a["title"])
        for a in articles
        if not a["title"] or a["title"] == a["id"] or len(a["title"]) > 160 or "\n" in a["title"]
        or not a["text"].strip()
    ]
    print(f"Suspicious titles/texts ({len(odd)}): {odd[:20]}")

    noted = [a["id"] for a in articles if a["notes"]] + [u["id"] for u in schedules if u["notes"]]
    print(f"Provisions with court-ruling notes ({len(noted)}): {noted}")

    print(f"\nSchedules: {len(schedules)} pieces")
    for group in schedule_groups:
        units = [u for u in schedules if u["part"] == group["id"]]
        gone = sum(u["status"] == "omitted" for u in units)
        print(f"  {group['id']:7} {len(units):4} pieces ({gone} omitted), "
              f"{sum(len(u['text']) for u in units):6} characters | {group['title']}")
    if not args.no_crosscheck:
        cross_check(articles)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
