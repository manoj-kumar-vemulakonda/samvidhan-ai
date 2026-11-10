"""
Dates in Indian legal documents: "12.11.2026", "12/11/26", "12th November, 2026",
"Nov 12, 2026", "2026-11-12"... and the phrases around them that mean a hearing
("next date of hearing", "list on", "adjourned to", "put up on") or a deadline
("reply within four weeks").

Used when no AI is available (the keyword-based plan) and to double-check AI dates.
Day-first is assumed (Indian style): 05/06/2026 = 5 June 2026.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_PATTERN = re.compile(
    r"(?P<iso>\b(?P<iy>20\d\d|19\d\d)-(?P<im>\d{1,2})-(?P<id>\d{1,2})\b)"
    r"|(?P<num>\b(?P<d>\d{1,2})\s?[./-]\s?(?P<m>\d{1,2})\s?[./-]\s?(?P<y>\d{4}|\d{2})\b)"
    r"|(?P<dmy>\b(?P<d2>\d{1,2})(?:st|nd|rd|th)?(?:\s+day)?(?:\s+of)?[\s.,-]+" + _MONTH.replace("(", "(?P<m2>", 1)
    + r"[\s.,'-]+(?P<y2>\d{4})\b)"
    r"|(?P<mdy>\b" + _MONTH.replace("(", "(?P<m3>", 1) + r"[\s.]+(?P<d3>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y3>\d{4})\b)",
    re.IGNORECASE,
)
TIME_PATTERN = re.compile(
    r"\b(?P<h>[01]?\d|2[0-3])(?:[:.](?P<min>[0-5]\d))?\s*(?P<ampm>a\.?\s?m\.?|p\.?\s?m\.?)(?![a-z])"
    r"|\b(?P<h24>[01]\d|2[0-3]):(?P<min24>[0-5]\d)\b",
    re.IGNORECASE,
)
HEARING_WORDS = re.compile(
    r"next date(?: of hearing)?|n\.?\s?d\.?\s?o\.?\s?h|date of hearing|list(?:ed)?\s+(?:it\s+|the matter\s+|the case\s+)?(?:again\s+)?(?:on|for)|"
    r"re-?list(?:ed)?|put up(?: again)?(?: on| for)?|adjourn(?:ed)?(?: to| till| for)?|fixed for|posted (?:to|for|on)|"
    r"come up (?:on|for)|renotif(?:y|ied)|to be heard on|hearing on|appear (?:on|before)|stands? over to|"
    r"for (?:arguments|evidence|framing of charges?|orders?|judg(?:e)?ment|cross[- ]examination|compliance|"
    r"consideration|appearance|hearing)(?: on)?",
    re.IGNORECASE,
)
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "fifteen": 15, "thirty": 30, "sixty": 60, "ninety": 90}
WITHIN = re.compile(
    r"within (?:a period of )?(?P<n>\d{1,3}|" + "|".join(NUMBER_WORDS) + r")\s*(?:\(\w+\)\s*)?(?P<unit>days?|weeks?|months?)",
    re.IGNORECASE,
)


def _make_date(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000 if year < 70 else 1900
    try:
        found = date(year, month, day)
    except ValueError:
        return None
    return found if 1950 <= found.year <= 2100 else None


def _from_match(match: re.Match) -> date | None:
    if match.group("iso"):
        return _make_date(int(match.group("iy")), int(match.group("im")), int(match.group("id")))
    if match.group("num"):
        return _make_date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
    if match.group("dmy"):
        return _make_date(int(match.group("y2")), MONTHS[match.group("m2").lower()[:3]], int(match.group("d2")))
    if match.group("mdy"):
        return _make_date(int(match.group("y3")), MONTHS[match.group("m3").lower()[:3]], int(match.group("d3")))
    return None


def find_dates(text: str) -> list[tuple[date, int, int, str]]:
    """Every date in the text: (date, start, end, the matched words)."""
    found = []
    for match in DATE_PATTERN.finditer(text or ""):
        value = _from_match(match)
        if value:
            found.append((value, match.start(), match.end(), match.group(0)))
    return found


def parse_date(text: str) -> date | None:
    dates = find_dates(text)
    return dates[0][0] if dates else None


def iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def find_time(text: str) -> str:
    """'10:30 AM' / '2 p.m.' / '14:00' -> 'HH:MM' (24-hour), or ''."""
    match = TIME_PATTERN.search(text or "")
    if not match:
        return ""
    if match.group("h24"):
        return f"{int(match.group('h24')):02d}:{match.group('min24')}"
    hour = int(match.group("h"))
    minute = int(match.group("min") or 0)
    if hour > 12:
        return ""
    if match.group("ampm").lower().startswith("p") and hour != 12:
        hour += 12
    if match.group("ampm").lower().startswith("a") and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}"


def clean_quote(text: str, start: int, end: int, width: int = 90) -> str:
    snippet = text[max(0, start - width): min(len(text), end + width)]
    return re.sub(r"\s+", " ", snippet).strip()


def hearing_dates(text: str) -> list[dict]:
    """Dates that come right after hearing words: 'next date of hearing: 12.11.2026', 'list on 3 Dec 2026'."""
    events = []
    dates = find_dates(text)
    for words in HEARING_WORDS.finditer(text or ""):
        for value, start, end, matched in dates:
            if words.end() <= start <= words.end() + 90:
                after = text[end:end + 40]
                events.append({
                    "date": value.isoformat(),
                    "time": find_time(after) or find_time(text[words.start():end]),
                    "quote": clean_quote(text, words.start(), end),
                    "kind": "hearing",
                    "hint": words.group(0),
                })
                break
    unique: dict[str, dict] = {}
    for event in events:
        unique.setdefault(event["date"], event)
    return list(unique.values())


def deadline_dates(text: str, from_date: date | None) -> list[dict]:
    """'reply be filed within four weeks' + the order's date -> a deadline."""
    if not from_date:
        return []
    events = []
    for match in WITHIN.finditer(text or ""):
        raw = match.group("n").lower()
        amount = int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw, 0)
        unit = match.group("unit").lower()
        if not amount:
            continue
        if unit.startswith("day"):
            due = from_date + timedelta(days=amount)
        elif unit.startswith("week"):
            due = from_date + timedelta(weeks=amount)
        else:
            month = from_date.month - 1 + amount
            year = from_date.year + month // 12
            day = min(from_date.day, 28)
            due = date(year, month % 12 + 1, day)
        sentence_start = max(text.rfind(".", 0, match.start()), text.rfind("\n", 0, match.start())) + 1
        what = re.sub(r"\s+", " ", text[sentence_start:match.start()]).strip()[-80:]
        events.append({
            "date": due.isoformat(),
            "time": "",
            "quote": clean_quote(text, match.start(), match.end()),
            "kind": "deadline",
            "hint": what or "deadline",
        })
    return events


def first_date(text: str, limit: int = 1500) -> date | None:
    """The first date in the opening part of a document - often the date of the document itself."""
    dated = re.search(r"\b(?:dated|date|dt\.?)\s*[:\-]?\s*", (text or "")[:limit], re.IGNORECASE)
    if dated:
        found = find_dates(text[dated.end(): dated.end() + 40])
        if found:
            return found[0][0]
    found = find_dates((text or "")[:limit])
    return found[0][0] if found else None
