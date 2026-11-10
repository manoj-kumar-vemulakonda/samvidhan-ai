"""
Landmark Supreme Court judgments, as short case notes the AI can cite.

The words of the Constitution often don't show how the Supreme Court has read them:
the right to privacy (Article 21), the "basic structure" limit on amendments
(Article 368), the 50% ceiling on reservations (Article 16)... So data/cases.json
holds short notes on landmark judgments, written for this app. The search finds the
notes that fit a question, Claude may cite them like [Puttaswamy (2017)], and
app/citations.py checks those citations just like Article citations.

The notes are summaries, not the judgments themselves - the web page links to the
full text so you can read the real thing.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.citations import label_for_id
from app.config import CASES_FILE


@dataclass(frozen=True)
class Case:
    id: str                  # "case-puttaswamy-2017" (always starts with "case-")
    name: str                # "Justice K.S. Puttaswamy (Retd.) v. Union of India"
    short: str               # how it is cited: "Puttaswamy (2017)"
    year: int
    bench: int | None        # number of judges, if known
    citation: str | None     # law report, e.g. "(2017) 10 SCC 1" (only where well established)
    articles: tuple[str, ...]  # provisions it is about: ("21", "19", "14"), or Schedule ids like "S10-P7"
    aliases: tuple[str, ...]   # words that mean the question names this case: ("puttaswamy",)
    topics: str              # keywords that help the search
    held: str                # what the Court decided, in plain words
    later: str               # what happened afterwards (overruled, a new law...), may be empty

    @property
    def label(self) -> str:
        return self.short

    @property
    def link(self) -> str:
        """A search for the full judgment on Indian Kanoon (a free database of Indian court judgments)."""
        return "https://indiankanoon.org/search/?formInput=" + urllib.parse.quote(f"{self.name} {self.year}")

    @property
    def search_text(self) -> str:
        """What the search engine reads for this case."""
        return f"{self.name} ({self.year}) - {self.short}\n{self.topics}\n{self.held}\n{self.later}".strip()

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": "case", "label": self.label, "name": self.name, "year": self.year,
            "bench": self.bench, "citation": self.citation, "articles": list(self.articles),
            "about": [{"id": a, "label": label_for_id(a)} for a in self.articles],
            "topics": self.topics, "held": self.held, "later": self.later, "link": self.link,
        }


def normalize_case_name(text: str) -> str:
    """'Puttaswamy (2017)' and 'Puttaswamy, 2017' -> 'puttaswamy 2017' (for matching citations)."""
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


class CaseLibrary:
    """All case notes, with lookups by id and by citation name."""

    def __init__(self, data: dict):
        self.about: str = data.get("about", "")
        self.cases: list[Case] = [
            Case(**{**raw, "articles": tuple(raw["articles"]), "aliases": tuple(raw.get("aliases", []))})
            for raw in data["cases"]
        ]
        self.by_id: dict[str, Case] = {c.id: c for c in self.cases}
        # Both "Puttaswamy (2017)" and the full name find the case
        self.by_name: dict[str, str] = {}
        for c in self.cases:
            self.by_name[normalize_case_name(c.short)] = c.id
            self.by_name[normalize_case_name(f"{c.name} ({c.year})")] = c.id

    def get(self, case_id: str) -> Case | None:
        return self.by_id.get(case_id.strip().lower())

    def find_by_name(self, citation: str) -> Case | None:
        """'[Puttaswamy (2017)]' (the text inside the brackets) -> the case, if it is in the library."""
        case_id = self.by_name.get(normalize_case_name(citation))
        return self.by_id.get(case_id) if case_id else None

    def named_in(self, question: str) -> list[Case]:
        """Cases the question mentions by name, e.g. 'What did Kesavananda decide?'."""
        text = question.lower()
        return [c for c in self.cases if any(re.search(rf"\b{re.escape(a)}", text) for a in c.aliases)]


@lru_cache(maxsize=1)
def load_cases(path: Path = CASES_FILE) -> CaseLibrary:
    """Read data/cases.json once (an empty library if the file is missing)."""
    if not path.exists():
        return CaseLibrary({"cases": []})
    with open(path, encoding="utf-8") as f:
        return CaseLibrary(json.load(f))
