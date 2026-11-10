"""
The Constitution text, loaded from data/constitution.json.

That file is made once by scripts/build_constitution.py from the official PDF.
It holds two kinds of "provisions" (pieces of the Constitution that can be cited):
  - Articles (and the Preamble):  "Article 21", "Preamble"
  - pieces of the 12 Schedules:   "Seventh Schedule, List II, Entry 2", "Tenth Schedule, Paragraph 2"
Both are loaded as the same `Article` record, so search, citations and the web page
treat them alike.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import DATA_FILE


@dataclass(frozen=True)
class Article:
    id: str            # "21", "21A", "Preamble", or for Schedules e.g. "S7-II-2", "S10-P2", "S8"
    title: str         # "Protection of life and personal liberty"
    part: str          # "III" (empty for the Preamble); for Schedules a group id like "S7-II"
    part_title: str    # "Fundamental Rights"; for Schedules e.g. "Seventh Schedule"
    chapter: str       # e.g. "Chapter I - The Executive", or "List II—State List" (may be empty)
    status: str        # "in_force" or "omitted" (repealed by an amendment)
    text: str          # the full text
    kind: str = "article"   # "article" or "schedule"
    cite: str = ""          # how a Schedule piece is cited, e.g. "Seventh Schedule, List II, Entry 2"
    refs: str = ""          # Schedules only: the Articles they belong to, e.g. "Article 246"
    # Official footnotes reporting court rulings, e.g. "This amendment has been struck down by the Supreme Court ..."
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        """How it is cited: 'Article 21', 'Preamble' or 'Seventh Schedule, List II, Entry 2'."""
        if self.cite:
            return self.cite
        return "Preamble" if self.id == "Preamble" else f"Article {self.id}"

    @property
    def is_schedule(self) -> bool:
        return self.kind == "schedule"

    @property
    def location(self) -> str:
        """Where it sits: 'Part III - Fundamental Rights' or 'Seventh Schedule - List II—State List'."""
        if self.is_schedule:
            return f"{self.part_title} - {self.chapter}" if self.chapter else self.part_title
        return f"Part {self.part} - {self.part_title}" if self.part else self.part_title

    @property
    def number(self) -> str:
        """The short number shown in lists: '21A', or for Schedules the last part ('2', 'P2' -> '2', 'III')."""
        if not self.is_schedule:
            return "" if self.id == "Preamble" else self.id
        last = self.cite.split(", ")[-1]
        return last.split(" ")[-1] if " " in last and "Schedule" not in last else ""

    def to_dict(self, include_text: bool = True) -> dict:
        data = asdict(self)
        data["notes"] = list(self.notes)
        data["label"] = self.label
        data["location"] = self.location
        data["number"] = self.number
        if not include_text:
            data.pop("text")
        return data


class Constitution:
    """All Articles and Schedule pieces, with quick lookup by id."""

    def __init__(self, data: dict):
        self.source: dict = data.get("source", {})
        self.articles: list[Article] = [_record(r) for r in data["articles"]]
        self.schedules: list[Article] = [_record(r) for r in data.get("schedules", [])]
        self.provisions: list[Article] = self.articles + self.schedules  # everything searchable
        self.by_id: dict[str, Article] = {a.id: a for a in self.provisions}
        self.parts: list[dict] = data.get("parts", [])

    @property
    def ids(self) -> set[str]:
        return set(self.by_id)

    def get(self, article_id: str) -> Article | None:
        """Find a provision by id: get('21a'), get('preamble') and get('s7-ii-2') all work."""
        key = article_id.strip()
        key = "Preamble" if key.lower() == "preamble" else key.upper()
        return self.by_id.get(key)

    def schedule_pieces(self, number: int) -> list[Article]:
        """All pieces of one Schedule, in order (e.g. 7 -> every entry of Lists I, II and III)."""
        return [a for a in self.schedules if re.match(rf"S{number}(?:-|$)", a.id)]

    def table_of_contents(self) -> list[dict]:
        """Parts and Schedules in order, each with its provisions (without the long texts)."""
        toc = []
        for part in self.parts:
            toc.append(
                {
                    **part,
                    "kind": part.get("kind", "article"),
                    "articles": [
                        self.by_id[a].to_dict(include_text=False)
                        for a in part["articles"]
                        if a in self.by_id
                    ],
                }
            )
        return toc


def _record(raw: dict) -> Article:
    """One JSON record -> Article (lists become tuples, so the record can't be changed by accident)."""
    return Article(**{**raw, "notes": tuple(raw.get("notes", []))})


@lru_cache(maxsize=1)
def load_constitution(path: Path = DATA_FILE) -> Constitution:
    """Read the JSON file once and keep it in memory."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run: .venv\\Scripts\\python.exe scripts\\build_constitution.py"
        )
    with open(path, encoding="utf-8") as f:
        return Constitution(json.load(f))
