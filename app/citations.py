"""
Finding references to the Constitution in text.

Two jobs:
1. In the USER'S QUESTION, spot exact references like "Article 21A",
   "art. 19(1)(a)", "Articles 14 and 21", "अनुच्छेद 21" or "Entry 97 of List I",
   so those provisions can be fetched directly instead of searched for.
2. In CLAUDE'S ANSWER, find every citation like [Article 22(2)] or
   [Seventh Schedule, List II, Entry 2] and check it against the provisions we
   actually gave Claude. A citation to something that was NOT in the sources is a
   warning sign (the model may be guessing), so the web page shows it in red.

Schedule pieces have ids like these (see scripts/build_constitution.py):
    S7-II-2  = Seventh Schedule, List II, Entry 2     S10-P2  = Tenth Schedule, Paragraph 2
    S3-III   = Third Schedule, Form III               S2-A    = Second Schedule, Part A
    S1-I     = First Schedule, Part I                 S8      = Eighth Schedule (the whole of it)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------
# The word that introduces a reference: Article / Articles / Art. / अनुच्छेद (Hindi)
_KEYWORD = re.compile(r"(?:\barticles?\b|\barts?\b\.?|अनुच्छेद)", re.IGNORECASE)

# One Article number, e.g. 21, 21A, 243ZH, 239AA, optionally followed by
# clauses like (1)(a) which we keep for display but ignore when matching.
_NUMBER = re.compile(r"\s*(\d{1,3}[A-Za-z]{0,3})\b((?:\s*\([0-9a-zA-Z]{1,5}\))*)")

# Words that join a list of numbers: "14, 15 and 16", "21 & 22", "19/21"
_JOINER = re.compile(r"\s*(?:,|\band\b|&|\bor\b|/|\bto\b)\s*", re.IGNORECASE)

# Anything in square brackets on one line, e.g. [Article 21] or [Preamble]
_BRACKET = re.compile(r"\[([^\[\]\n]{1,120})\]")


def normalize_article_id(raw: str) -> str:
    """'21a' -> '21A', ' 370 ' -> '370'. Clause parts like (1)(a) are not part of the id."""
    return raw.strip().upper()


def find_article_refs(text: str, known_ids: set[str] | None = None) -> list[str]:
    """
    Return the Article ids mentioned in `text`, in order, without duplicates.

    If `known_ids` is given, anything that is not a real Article id is dropped
    (so "art 2023" or "1st" never turn into fake Articles).
    """
    found: list[str] = []
    for keyword in _KEYWORD.finditer(text):
        pos = keyword.end()
        # Read a list of numbers after the keyword: "Articles 14, 15 and 16"
        while True:
            number = _NUMBER.match(text, pos)
            if not number:
                break
            article_id = normalize_article_id(number.group(1))
            if known_ids is None or article_id in known_ids:
                if article_id not in found:
                    found.append(article_id)
            pos = number.end()
            joiner = _JOINER.match(text, pos)
            if not joiner:
                break
            pos = joiner.end()
    return found


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
            "ninth", "tenth", "eleventh", "twelfth"]
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
_ORDINAL_SCHEDULE = re.compile(r"\b(" + "|".join(ORDINALS) + r"|\d{1,2}(?:st|nd|rd|th))\s+schedule\b", re.I)
_SCHEDULE_NUMBER = re.compile(r"\bschedule\s+(\d{1,2}|xii|xi|x|ix|viii|vii|vi|v|iv|iii|ii|i)\b", re.I)
_LIST = re.compile(r"\blist\s*[-–]?\s*(iii|ii|i|[123])\b|\b(union|state|concurrent)\s+list\b", re.I)
_ENTRY = re.compile(r"\b(?:entry|entries|item)\s+(\d{1,3}[a-z]{0,2})\b", re.I)
_PARAGRAPH = re.compile(r"\b(?:paragraph|para)\.?\s+(\d{1,2}[a-z]{0,2})\b", re.I)
_FORM = re.compile(r"\bform\s+(viii|vii|vi|iv|v|iii|ii|i)\b", re.I)
_PART = re.compile(r"\bpart\s+([a-e]|ii|i)\b", re.I)
_LIST_NAMES = {"union": 1, "state": 2, "concurrent": 3}


def schedule_number(text: str) -> int | None:
    """'the Seventh Schedule' / '7th schedule' / 'Schedule VII' -> 7."""
    match = _ORDINAL_SCHEDULE.search(text)
    if match:
        word = match.group(1).lower()
        return ORDINALS.index(word) + 1 if word in ORDINALS else int(re.match(r"\d+", word).group())
    match = _SCHEDULE_NUMBER.search(text)
    if match:
        value = match.group(1).upper()
        return int(value) if value.isdigit() else ROMAN.index(value) + 1
    return None


def _list_number(text: str) -> int | None:
    match = _LIST.search(text)
    if not match:
        return None
    if match.group(2):
        return _LIST_NAMES[match.group(2).lower()]
    value = match.group(1).upper()
    return int(value) if value.isdigit() else ROMAN.index(value) + 1


def schedule_id(text: str) -> str | None:
    """
    Turn ONE reference into an id (the order of the pieces doesn't matter):
        'Seventh Schedule, List II, Entry 2' -> 'S7-II-2'    'List I, Entry 97' -> 'S7-I-97'
        'Tenth Schedule, Paragraph 2'        -> 'S10-P2'     'Eighth Schedule'  -> 'S8'
    Returns None if the text doesn't refer to a Schedule.
    """
    number = schedule_number(text)
    list_no, entry = _list_number(text), _ENTRY.search(text)
    if list_no and entry and number in (None, 7):
        return f"S7-{ROMAN[list_no - 1]}-{entry.group(1).upper()}"
    if number is None or not 1 <= number <= 12:
        return None
    paragraph = _PARAGRAPH.search(text)
    if paragraph and number in (5, 6, 10):
        return f"S{number}-P{paragraph.group(1).upper()}"
    form = _FORM.search(text)
    if form and number == 3:
        return f"S3-{form.group(1).upper()}"
    part = _PART.search(text)
    if part and number in (1, 2):
        return f"S{number}-{part.group(1).upper()}"
    return f"S{number}"


def label_for_id(provision_id: str) -> str:
    """The readable name for an id: '21' -> 'Article 21', 'S7-II-2' -> 'Seventh Schedule, List II, Entry 2'."""
    if provision_id == "Preamble":
        return "Preamble"
    match = re.fullmatch(r"S(\d{1,2})(?:-(.+))?", provision_id)
    if not match or not 1 <= int(match.group(1)) <= 12:
        return f"Article {provision_id}"
    number, rest = int(match.group(1)), match.group(2) or ""
    name = f"{ORDINALS[number - 1].title()} Schedule"
    if number == 7 and "-" in rest:
        list_roman, entry = rest.split("-", 1)
        return f"{name}, List {list_roman}, Entry {entry}"
    if rest.startswith("P") and number in (5, 6, 10):
        return f"{name}, Paragraph {rest[1:]}"
    if rest and number == 3:
        return f"{name}, Form {rest}"
    if rest:
        return f"{name}, Part {rest}"
    return name


def mentioned_schedules(text: str) -> list[int]:
    """Schedule numbers named anywhere in a question: 'Is police in the seventh schedule?' -> [7]."""
    found: list[int] = []
    for match in re.finditer(r"\b\w+\s+schedule\b|\bschedule\s+\w+\b", text, re.I):
        number = schedule_number(match.group(0))
        if number and 1 <= number <= 12 and number not in found:
            found.append(number)
    return found


# Exact Schedule references inside a question, e.g. "Entry 97 of List I", "List II entry 5",
# "entry 2 of the state list", "paragraph 2 of the Tenth Schedule", "Tenth Schedule para 2"
_LIST_WORD = r"(?:list\s*[-–]?\s*(?:iii|ii|i|[123])|(?:union|state|concurrent)\s+list)"
_ENTRY_WORD = r"(?:entry|item)\s+\d{1,3}[a-z]{0,2}"
_SCHEDULE_WORD = r"(?:(?:" + "|".join(ORDINALS) + r"|\d{1,2}(?:st|nd|rd|th))\s+schedule|schedule\s+(?:\d{1,2}|[ivx]{1,4}))"
_QUESTION_REFS = re.compile(
    rf"{_ENTRY_WORD}\s+(?:of\s+|in\s+)?(?:the\s+)?{_LIST_WORD}"
    rf"|{_LIST_WORD}\s*,?\s*(?:,\s*)?{_ENTRY_WORD}"
    rf"|(?:paragraph|para)\.?\s+\d{{1,2}}[a-z]{{0,2}}\s+(?:of\s+|in\s+)?(?:the\s+)?{_SCHEDULE_WORD}"
    rf"|{_SCHEDULE_WORD}\s*,?\s*(?:paragraph|para|form|part)\.?\s+[0-9a-z]{{1,4}}"
    rf"|(?:form|part)\s+[a-z]{{1,4}}\s+(?:of\s+|in\s+)?(?:the\s+)?{_SCHEDULE_WORD}"
    rf"|{_SCHEDULE_WORD}",
    re.IGNORECASE,
)


def find_schedule_refs(text: str, known_ids: set[str]) -> list[str]:
    """Schedule pieces named exactly in a question (only ids that really exist)."""
    found: list[str] = []
    for match in _QUESTION_REFS.finditer(text):
        piece_id = schedule_id(match.group(0))
        if piece_id and piece_id in known_ids and piece_id not in found:
            found.append(piece_id)
    return found


# ---------------------------------------------------------------------------
# Checking the citations in an answer
# ---------------------------------------------------------------------------
@dataclass
class CitationCheck:
    """Result of checking an answer's citations against its sources."""

    cited: list[str] = field(default_factory=list)       # every provision cited (ids)
    verified: list[str] = field(default_factory=list)    # cited AND in the sources
    unverified: list[str] = field(default_factory=list)  # cited but NOT in the sources
    labels: dict[str, str] = field(default_factory=dict)  # id -> readable name, for the page

    @property
    def all_verified(self) -> bool:
        return not self.unverified

    def to_dict(self) -> dict:
        return {
            "cited": self.cited,
            "verified": self.verified,
            "unverified": self.unverified,
            "labels": self.labels,
            "all_verified": self.all_verified,
        }


# A court case cited like [Puttaswamy (2017)]: a name followed by a year in brackets
_CASE_LIKE = re.compile(r"\((?:1[89]|20)\d{2}\)\s*$")
UNKNOWN_CASE = "case?"  # id prefix for a cited case that isn't in the case library


def find_citations(answer: str, case_library=None) -> list[str]:
    """
    Return the ids cited in square brackets, in order:
    [Article 19(1)(a)] -> '19', [Preamble] -> 'Preamble', [Seventh Schedule, List II, Entry 2] -> 'S7-II-2',
    [Puttaswamy (2017)] -> 'case-puttaswamy-2017' (with the case library from app/cases.py).
    A case that isn't in the library gets the id 'case?<what was written>'.
    """
    cited: list[str] = []
    for bracket in _BRACKET.finditer(answer):
        inner = bracket.group(1).strip()
        if inner.lower() == "preamble":
            ids = ["Preamble"]
        elif _KEYWORD.match(inner):
            ids = find_article_refs(inner)
        elif re.search(r"\bschedule\b|\blist\b.*\bentry\b|\bentry\b.*\blist\b", inner, re.I):
            piece_id = schedule_id(inner)
            ids = [piece_id] if piece_id else []
        elif case_library is not None and case_library.find_by_name(inner):
            ids = [case_library.find_by_name(inner).id]
        elif _CASE_LIKE.search(inner):
            ids = [UNKNOWN_CASE + inner]
        else:
            continue  # some other bracket, e.g. [sic] - not a citation
        for provision_id in ids:
            if provision_id not in cited:
                cited.append(provision_id)
    return cited


def check_citations(answer: str, source_ids: list[str] | set[str], case_library=None) -> CitationCheck:
    """Compare what Claude cited with what we actually gave it (Articles, Schedules and case notes)."""
    allowed = set(source_ids)
    cited = find_citations(answer, case_library)
    labels = {}
    for cited_id in cited:
        if cited_id.startswith(UNKNOWN_CASE):
            labels[cited_id] = cited_id[len(UNKNOWN_CASE):]
        elif cited_id.startswith("case-") and case_library is not None:
            labels[cited_id] = case_library.get(cited_id).label
        else:
            labels[cited_id] = label_for_id(cited_id)
    return CitationCheck(
        cited=cited,
        verified=[a for a in cited if a in allowed],
        unverified=[a for a in cited if a not in allowed],
        labels=labels,
    )
