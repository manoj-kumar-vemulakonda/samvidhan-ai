"""
The Acts library: about 100 central Acts (BNS, BNSS, BSA, IPC, CrPC, CPC, Contract Act,
NI Act, Limitation Act, Companies Act, ...) - built by scripts/build_laws.py into data/laws/.

  * Look up a section:        library.get("BNS:103")
  * Find references in text:  "u/s 302 IPC", "Section 138 of the NI Act", "धारा 103 बीएनएस"
  * Old -> new converter:     IPC 420 -> BNS 318(4); CrPC 438 -> BNSS 482; IEA 65B -> BSA 63
  * Search:                   exact references + keywords (BM25) + meaning (ChromaDB), merged

Run it directly to try a search or build the index:
    .venv\\Scripts\\python.exe -m app.laws "punishment for cheating"
    .venv\\Scripts\\python.exe -m app.laws --build
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache

from rank_bm25 import BM25Okapi

from app import config
from app.search import RRF_K, tokenize

CHUNK_CHARS = 900
CANDIDATES = 50
OLD_CODES = {"IPC": "BNS", "CRPC": "BNSS", "IEA": "BSA"}
NEW_CODES = {v: k for k, v in OLD_CODES.items()}
CASE_SENSITIVE_ALIASES = {"IT Act": "ITACT", "POSH": "POSH", "PC Act": "PCA", "MV Act": "MVA", "DV Act": "DV", "JJ Act": "JJ"}
RISKY_ALIASES = {"it act", "posh", "pc act", "ica", "cpa", "sra", "hma", "tpa", "bsa", "rti", "mva", "nia", "iea", "jj act",
                 "dv act", "mv act"}
OLD_LAW_WORDS = re.compile(r"\b(ipc|crpc|cr\.?p\.?c|evidence act|indian penal code|before (?:1 )?july 2024|old law|pending case)\b", re.I)

# Everyday legal words -> the sections they usually mean (each one checked against the
# section titles in tests/test_laws.py). Like TOPIC_HINTS for the Constitution.
LEGAL_HINTS: dict[str, list[str]] = {
    "anticipatory bail": ["BNSS:482"], "regular bail": ["BNSS:480", "BNSS:483"], "default bail": ["BNSS:187"],
    "bail": ["BNSS:480", "BNSS:478", "BNSS:483"], "bailable": ["BNSS:478"], "non-bailable": ["BNSS:480"],
    "undertrial": ["BNSS:479"], "remand": ["BNSS:187"], "police custody": ["BNSS:187", "BNSS:58"],
    "arrest without warrant": ["BNSS:35"], "grounds of arrest": ["BNSS:47"], "24 hours": ["BNSS:58"],
    "fir": ["BNSS:173"], "first information report": ["BNSS:173"], "zero fir": ["BNSS:173"],
    "quash": ["BNSS:528"], "quashing": ["BNSS:528"], "inherent powers": ["BNSS:528", "CPC:151"],
    "charge sheet": ["BNSS:193"], "chargesheet": ["BNSS:193"], "plea bargaining": ["BNSS:290"],
    "proclaimed offender": ["BNSS:84"], "absconding": ["BNSS:84"], "mercy petition": ["BNSS:472"],
    "victim compensation": ["BNSS:396"], "maintenance": ["BNSS:144", "HAMA:18", "HMA:24", "HMA:25"],
    "cheque bounce": ["NIA:138", "NIA:142"], "dishonour of cheque": ["NIA:138", "NIA:142"],
    "cheque dishonour": ["NIA:138", "NIA:142"], "cheque bounced": ["NIA:138", "NIA:142"],
    "bounced cheque": ["NIA:138", "NIA:142"], "dishonoured cheque": ["NIA:138", "NIA:142"],
    "cheque was dishonoured": ["NIA:138", "NIA:142"], "cheque was returned": ["NIA:138", "NIA:142"],
    "cheque returned": ["NIA:138", "NIA:142"], "returned unpaid": ["NIA:138", "NIA:142"],
    "murder": ["BNS:103", "BNS:101"], "attempt to murder": ["BNS:109"], "rape": ["BNS:64", "BNS:63"],
    "cruelty": ["BNS:85", "BNS:86"], "498a": ["BNS:85", "BNS:86"], "dowry death": ["BNS:80", "BSA:118"],
    "dowry": ["DPA:3", "DPA:4", "BNS:80"], "stalking": ["BNS:78"], "cheating": ["BNS:318"], "fraud": ["BNS:318"],
    "criminal breach of trust": ["BNS:316"], "forgery": ["BNS:336"], "hurt": ["BNS:115"],
    "grievous hurt": ["BNS:117"], "kidnapping": ["BNS:137"], "extortion": ["BNS:308"],
    "criminal intimidation": ["BNS:351"], "threat": ["BNS:351"], "sedition": ["BNS:152"],
    "organised crime": ["BNS:111"], "terrorist act": ["BNS:113"], "snatching": ["BNS:304"], "theft": ["BNS:303"],
    "defamation": ["BNS:356"], "outrage modesty": ["BNS:74"], "molestation": ["BNS:74"],
    "sexual harassment": ["BNS:75", "POSH:3"], "false promise of marriage": ["BNS:69"],
    "electronic evidence": ["BSA:63"], "electronic record": ["BSA:63"], "65b": ["BSA:63"], "dying declaration": ["BSA:26"],
    "confession": ["BSA:23"], "burden of proof": ["BSA:104"],
    "divorce": ["HMA:13"], "mutual consent": ["HMA:13B"], "restitution of conjugal rights": ["HMA:9"],
    "alimony": ["HMA:25"], "domestic violence": ["DV:12", "DV:18", "DV:19", "DV:20"], "protection order": ["DV:18"],
    "residence order": ["DV:19"], "specific performance": ["SRA:10"], "injunction": ["SRA:38", "SRA:39"],
    "jurisdiction of civil court": ["CPC:9"], "notice to government": ["CPC:80"], "section 80 notice": ["CPC:80"],
    "first appeal": ["CPC:96"], "second appeal": ["CPC:100"], "review petition": ["CPC:114"], "civil revision": ["CPC:115"],
    "caveat": ["CPC:148A"], "limitation": ["LIMITATION:3", "LIMITATION:5"], "condonation of delay": ["LIMITATION:5"],
    "delay condonation": ["LIMITATION:5"], "acknowledgment of debt": ["LIMITATION:18"],
    "consumer complaint": ["CONSUMER:35", "CONSUMER:34"], "consumer forum": ["CONSUMER:34", "CONSUMER:35"],
    "arbitration agreement": ["ARBITRATION:7"], "appoint arbitrator": ["ARBITRATION:11"],
    "appointment of arbitrator": ["ARBITRATION:11"], "set aside award": ["ARBITRATION:34"],
    "setting aside award": ["ARBITRATION:34"], "interim measures": ["ARBITRATION:9"],
    "rti application": ["RTI:6"], "right to information": ["RTI:6", "RTI:7", "RTI:8"], "first appeal rti": ["RTI:19"],
    "breach of contract": ["CONTRACT:73", "CONTRACT:74"], "damages": ["CONTRACT:73"], "liquidated damages": ["CONTRACT:74"],
    "lease": ["TPA:105", "TPA:106"], "tenancy": ["TPA:105", "TPA:106"], "notice to quit": ["TPA:106"],
    "coparcenary": ["HSA:6"], "daughter's share": ["HSA:6"], "senior citizen maintenance": ["MWPSC:4", "MWPSC:23"],
    "cyber crime": ["ITACT:66", "ITACT:43"], "obscene": ["ITACT:67"], "pocso": ["POCSO:4", "POCSO:6", "POCSO:19"],
    "ndps bail": ["NDPS:37"], "commercial quantity": ["NDPS:37"], "atrocities": ["SCST:3", "SCST:18"],
    "bribe": ["PCA:7"], "sanction for prosecution": ["PCA:19"], "pre-institution mediation": ["COMMCOURTS:12A"],
    "insolvency": ["IBC:7", "IBC:9"], "operational creditor": ["IBC:9"], "financial creditor": ["IBC:7"],
    "contempt of court": ["CONTEMPT:12", "CONTEMPT:2"],
}
# Court procedure (CPC Orders, Limitation Articles, court Acts) - titles checked in tests/test_rules.py
LEGAL_HINTS.update({
    "rejection of plaint": ["CPC:O7R11"], "reject the plaint": ["CPC:O7R11"], "plaint rejected": ["CPC:O7R11"],
    "plaint be rejected": ["CPC:O7R11"], "reject plaint": ["CPC:O7R11"],
    "specific performance": ["SRA:10", "LIMITATION:ART54"], "time barred": ["LIMITATION:3", "LIMITATION:5"],
    "time-barred": ["LIMITATION:3", "LIMITATION:5"], "barred by limitation": ["LIMITATION:3", "LIMITATION:5"],
    "temporary injunction": ["CPC:O39R1", "CPC:O39R2"], "interim injunction": ["CPC:O39R1", "CPC:O39R2"],
    "breach of injunction": ["CPC:O39R2A"], "amendment of pleadings": ["CPC:O6R17"], "amend the plaint": ["CPC:O6R17"],
    "written statement": ["CPC:O8R1"], "ex parte decree": ["CPC:O9R13", "LIMITATION:ART123"],
    "set aside ex parte": ["CPC:O9R13", "LIMITATION:ART123"], "review of judgment": ["CPC:O47R1", "CPC:114"],
    "appeal against order": ["CPC:O43R1"], "local commission": ["CPC:O26R9"], "appointment of receiver": ["CPC:O40R1"],
    "attachment before judgment": ["CPC:O38R5"], "legal heirs": ["CPC:O22R3", "CPC:O22R4"],
    "abatement": ["CPC:O22R3", "CPC:O22R4"], "withdrawal of suit": ["CPC:O23R1"], "compromise of suit": ["CPC:O23R3"],
    "indigent person": ["CPC:O33R1"], "pauper suit": ["CPC:O33R1"], "impleadment": ["CPC:O1R10"],
    "judgment on admission": ["CPC:O12R6"], "framing of issues": ["CPC:O14R1"], "adjournment": ["CPC:O17R1"],
    "additional evidence": ["CPC:O41R27"], "summary suit": ["CPC:O37R2", "CPC:O37R3"],
    "leave to defend": ["CPC:O37R3", "LIMITATION:ART118"], "summary judgment": ["CPC:O13AR3"],
    "case management hearing": ["CPC:O15AR1"], "substituted service": ["CPC:O5R20"],
    "obstruction to possession": ["CPC:O21R97"], "next friend": ["CPC:O32R1"],
    "execution of decree": ["LIMITATION:ART136"], "money lent": ["LIMITATION:ART19"],
    "recovery of money": ["LIMITATION:ART19", "CPC:O37R2"], "adverse possession": ["LIMITATION:ART65"],
    "suit for possession": ["LIMITATION:ART65", "LIMITATION:ART64"], "suit for declaration": ["LIMITATION:ART58"],
    "cancellation of sale deed": ["LIMITATION:ART59"], "limitation for specific performance": ["LIMITATION:ART54"],
    "gram nyayalaya": ["GRAMNYAYALAYA:3", "GRAMNYAYALAYA:9", "GRAMNYAYALAYA:11"], "mobile court": ["GRAMNYAYALAYA:9"],
    "court fee": ["COURTFEES:7", "COURTFEES:6", "SUITSVALUATION:8"], "court fees": ["COURTFEES:7", "COURTFEES:6"],
    "computation of time": ["GENCLAUSES:10", "GENCLAUSES:9"], "probation": ["PROBATION:4", "PROBATION:3"],
    "first offender": ["PROBATION:3", "PROBATION:4"], "mediation": ["MEDIATION:5"], "notary": ["NOTARIES:8"],
    "natural guardian": ["HMGA:6"], "dissolution of muslim marriage": ["DMMA:2"],
    "eviction from public premises": ["PUBLICPREMISES:5"],
})
HINT_WEIGHT = 2.0


@dataclass
class Section:
    """One citable provision: a section of an Act, a rule of court rules, an Order-rule of the CPC,
    an Article of the Limitation Act Schedule, an entry of the BNSS First Schedule, a page of a handbook."""
    id: str
    act: str
    number: str
    title: str
    chapter: str
    chapter_title: str
    text: str
    status: str
    act_cite: str
    act_name: str
    act_status: str
    ref: str = ""              # "O. VII r. 11", "Art. 54", "Ch. IV r. 3" (empty for an ordinary section)
    kind: str = "section"      # section / rule / article / entry / schedule / page
    page: int | None = None    # page of the official PDF
    pdf: str = ""              # which PDF (when it differs from the book's)

    @property
    def label(self) -> str:
        """How it is cited: 'BNS s. 103', 'CPC O. VII r. 11', 'Limitation Act Art. 54'."""
        return f"{self.act_cite} {self.ref}" if self.ref else f"{self.act_cite} s. {self.number}"

    def to_dict(self, include_text: bool = True) -> dict:
        data = {
            "id": self.id, "kind": self.kind, "act": self.act, "number": self.number, "title": self.title,
            "label": self.label, "cite": self.label, "act_name": self.act_name, "act_status": self.act_status,
            "chapter": self.chapter, "chapter_title": self.chapter_title, "status": self.status, "page": self.page,
            "location": f"{self.act_name}" + (f", {self.chapter}" if self.chapter and not self.chapter[:1].isdigit()
                                               else f", Chapter {self.chapter}" if self.chapter else "")
                        + (f" - {self.chapter_title}" if self.chapter_title else ""),
        }
        if include_text:
            data["text"] = self.text
        return data


@dataclass
class Act:
    code: str
    cite: str
    name: str
    short: str
    year: int
    category: str
    status: str
    replaced_by: str | None
    replaces: str | None
    note: str
    aliases: list[str]
    source: str
    sections: list[Section] = field(default_factory=list)
    kind: str = "act"          # act / rules / handbook
    court: str = ""            # "Delhi High Court; District courts"
    jurisdiction: str = "Central"
    pdf: str = ""              # official PDF file name (data/raw/official or the workspace library)
    url: str = ""              # where it was published
    as_on: str = ""
    official_parts: list = field(default_factory=list)
    user: bool = False         # added by the lawyer from the Library page

    def to_dict(self, with_sections: bool = False) -> dict:
        data = {
            "code": self.code, "cite": self.cite, "name": self.name, "short": self.short, "year": self.year,
            "category": self.category, "status": self.status, "replaced_by": self.replaced_by,
            "replaces": self.replaces, "note": self.note, "sections_count": len(self.sections), "source": self.source,
            "kind": self.kind, "court": self.court, "jurisdiction": self.jurisdiction, "has_pdf": bool(self.pdf),
            "url": self.url, "as_on": self.as_on, "official_parts": self.official_parts, "user": self.user,
        }
        if with_sections:
            data["sections"] = [{"id": s.id, "number": s.number, "title": s.title, "chapter": s.chapter,
                                 "chapter_title": s.chapter_title, "status": s.status, "label": s.label,
                                 "ref": s.ref or f"s. {s.number}", "kind": s.kind, "page": s.page}
                                for s in self.sections]
        return data


def alias_key(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r",?\s*\b(1[89]\d\d|20\d\d)\b", " ", text)
    text = re.sub(r"[().,/'\-]", " ", text)
    text = re.sub(r"^\s*the\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def norm_number(number: str) -> str:
    return re.sub(r"[\s\-–.]", "", str(number or "")).upper()


def user_library_dir():
    """Rule books the lawyer added from the Library page (kept with their workspace, not in data/)."""
    return config.WORKSPACE_DIR / "library"


class LawLibrary:
    def __init__(self) -> None:
        index = json.loads((config.LAWS_DIR / "index.json").read_text(encoding="utf-8"))
        self.source = index.get("source", "")
        self.acts: dict[str, Act] = {}
        self.sections: dict[str, Section] = {}
        files = [(config.LAWS_DIR / "acts" / f"{meta['code']}.json", False) for meta in index["acts"]]
        folder = user_library_dir()
        if folder.exists():
            files += [(path, True) for path in sorted(folder.glob("*.json"))]
        for path, user in files:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if raw["code"] in self.acts:
                continue
            act = Act(raw["code"], raw["cite"], raw["name"], raw.get("short") or raw["cite"], raw.get("year") or 0,
                      raw["category"], raw.get("status", "in_force"), raw.get("replaced_by"), raw.get("replaces"),
                      raw.get("note", ""), raw.get("aliases", []), raw.get("source", ""),
                      kind=raw.get("kind", "act"), court=raw.get("court", ""), jurisdiction=raw.get("jurisdiction", "Central"),
                      pdf=raw.get("pdf", ""), url=raw.get("url", ""), as_on=raw.get("as_on", ""),
                      official_parts=raw.get("official_parts", []), user=user)
            for s in raw["sections"]:
                number = norm_number(s["number"])
                section_id = f"{act.code}:{number}"
                if section_id in self.sections:
                    continue
                section = Section(section_id, act.code, number, s["title"], s.get("chapter", ""),
                                  s.get("chapter_title", ""), s["text"], s.get("status", "in_force"), act.cite,
                                  act.name, act.status, ref=s.get("ref", ""), kind=s.get("kind", "section"),
                                  page=s.get("page"), pdf=s.get("pdf", ""))
                act.sections.append(section)
                self.sections[section_id] = section
            self.acts[act.code] = act
        # BNS section -> its rows in the BNSS First Schedule (bailable? cognizable? which court?)
        self.classification: dict[str, list[str]] = {}
        for section_id in self.sections:
            if section_id.startswith("BNSS:FS_") and section_id != "BNSS:FS_OTHER":
                base = re.match(r"\d+", section_id[len("BNSS:FS_"):])
                if base:
                    self.classification.setdefault(base.group(0), []).append(section_id)
        concordance_file = config.LAWS_DIR / "concordance.json"
        self.concordance = json.loads(concordance_file.read_text(encoding="utf-8")) if concordance_file.exists() else {}
        # Act names / abbreviations -> act code
        self.alias_map: dict[str, str] = {}
        for act in self.acts.values():
            for alias in [act.cite, act.short, act.name, *act.aliases]:
                key = alias_key(alias)
                if key and key not in RISKY_ALIASES and len(key) >= 3:
                    self.alias_map.setdefault(key, act.code)
                elif key in RISKY_ALIASES:
                    self.alias_map.setdefault(key, act.code)
        def words_pattern(alias: str) -> str:
            # "ni act" also matches "N.I. Act", "NI-Act"
            return r"[\s.\-]*".join(re.escape(word) for word in alias.split(" "))

        pattern_parts = sorted((k for k in self.alias_map if k not in RISKY_ALIASES), key=len, reverse=True)
        self._alias_re = re.compile(
            r"(?<![\wऀ-ॿ])(" + "|".join(words_pattern(k) for k in pattern_parts)
            + r")(?![\wऀ-ॿ])", re.IGNORECASE)
        risky = sorted(RISKY_ALIASES & set(self.alias_map), key=len, reverse=True)
        # Short abbreviations that are also ordinary words only count in CAPITALS ("BSA", "RTI", "TPA")
        self._risky_re = re.compile(r"(?<![\w])(" + "|".join(words_pattern(k.upper()) for k in risky)
                                    + r")(?![\w])") if risky else None
        self._case_re = re.compile(r"(?<![\w])(" + "|".join(re.escape(k) for k in CASE_SENSITIVE_ALIASES) + r")(?![\w])")

    # ---- lookups -----------------------------------------------------------
    def get(self, section_id: str) -> Section | None:
        if ":" not in str(section_id):
            return None
        code, number = section_id.split(":", 1)
        return self.sections.get(f"{code.upper()}:{norm_number(number)}")

    def section(self, code: str, number: str) -> Section | None:
        return self.sections.get(f"{code.upper()}:{norm_number(number)}")

    def act(self, code: str) -> Act | None:
        return self.acts.get(code)

    def find_act(self, name: str) -> str | None:
        """'Negotiable Instruments Act' / 'NI Act' / 'ipc' -> act code."""
        key = alias_key(name)
        if key in self.alias_map:
            return self.alias_map[key]
        upper = str(name or "").strip()
        if upper in CASE_SENSITIVE_ALIASES:
            return CASE_SENSITIVE_ALIASES[upper]
        return None

    def table_of_contents(self) -> list[dict]:
        groups: dict[str, list] = {}
        for act in sorted(self.acts.values(), key=lambda a: (a.category, a.name)):
            groups.setdefault(act.category, []).append(act.to_dict())
        order = ["Criminal law", "Civil procedure and property", "Courts and procedure", "Court rules - Supreme Court",
                 "Court rules - High Courts", "District courts, Gram Nyayalayas and court fees", "My rule books",
                 "Contracts, commerce and banking", "Family law",
                 "Constitutional and public law", "Companies, finance and securities", "Tax", "Labour and employment",
                 "Cyber, data and IP", "Environment", "Regulatory", "Other central Acts"]
        return [{"category": c, "acts": groups[c]} for c in sorted(groups, key=lambda c: order.index(c) if c in order else 99)]

    # ---- references in text ---------------------------------------------------
    def act_mentions(self, text: str) -> list[tuple[str, int, int]]:
        """(act code, start, end) for every act named in the text."""
        found = []
        for match in self._alias_re.finditer(text or ""):
            code = self.alias_map.get(alias_key(match.group(1)))
            if code:
                found.append((code, match.start(), match.end()))
        if self._risky_re:
            for match in self._risky_re.finditer(text or ""):
                code = self.alias_map.get(alias_key(match.group(1)))
                if code:
                    found.append((code, match.start(), match.end()))
        for match in self._case_re.finditer(text or ""):
            found.append((CASE_SENSITIVE_ALIASES[match.group(1)], match.start(), match.end()))
        found.sort(key=lambda f: f[1])
        return found

    def mentioned_acts(self, text: str) -> list[str]:
        codes = []
        for code, _, _ in self.act_mentions(text):
            if code not in codes:
                codes.append(code)
        return codes

    SECTION_WORD = r"(?:sections?|secs?\.?|ss?\.|u/ss?\.?|under section|धारा|धाराओं|अनुभाग)"
    NUMBER = r"\d{1,4}\s?-?[A-Z]{0,3}(?:\s?\(\s?\w{1,4}\s?\))*"
    NUMBER_LIST = NUMBER + r"(?:\s*(?:,|and|&|/|or|r/w|read with|तथा|और)\s*" + NUMBER + r")*"

    # ---- rules: "Order VII Rule 11", "O. 39 rr. 1 & 2", "Article 54", "Chapter IV Rule 3" ---------------
    ORDER_RULE = re.compile(r"(?<![\w])(?:order|o\.?)\s*([IVXLC]{1,7}|\d{1,2})\s*(?:[-–]\s*)?([A-Z](?![a-z]))?"
                            r"(?:\s*\(\s*(commercial)\s*\))?[\s,]*(?:rules?|rr?\.?)\s*"
                            r"(\d{1,3}[A-Z]?(?:\s*(?:,|and|&|to|-|–)\s*\d{1,3}[A-Z]?)*)", re.I)
    CHAPTER_RULE = re.compile(r"(?<![\w])(?:chapter|ch\.?)\s*([IVXLC]{1,7}|\d{1,2})\s*([A-Z](?![a-z]))?[\s,]*"
                              r"(?:rules?|rr?\.?|para(?:graph)?\.?)\s*(\d{1,3}[A-Z]?)", re.I)
    RULE_OF_CHAPTER = re.compile(r"(?<![\w])(?:rules?|r\.)\s*(\d{1,3}[A-Z]?)\s*(?:of|,)\s*(?:the\s+)?(?:chapter|ch\.?)\s*"
                                 r"([IVXLC]{1,7}|\d{1,2})\b", re.I)
    BARE_RULE = re.compile(r"(?<![\w])(?:rules?|r\.)\s*(\d{1,3}[A-Z]?)\b", re.I)
    ARTICLE = re.compile(r"(?<![\w])(?:article|art\.?)\s*(\d{1,3})\b", re.I)

    def _books_with(self, prefix: str) -> list[str]:
        if not hasattr(self, "_prefix_books"):
            self._prefix_books: dict[str, list[str]] = {}
        if prefix not in self._prefix_books:
            self._prefix_books[prefix] = [code for code, act in self.acts.items()
                                          if any(s.number.startswith(prefix) and s.number[len(prefix):len(prefix) + 1].isdigit()
                                                 for s in act.sections[:2000])]
        return self._prefix_books[prefix]

    @staticmethod
    def _expand(numbers: str) -> list[str]:
        """'1 and 2' -> ['1', '2']; '1 to 3' -> ['1', '2', '3']."""
        out: list[str] = []
        parts = re.split(r"\s*(?:,|and|&)\s*", numbers.strip())
        for part in parts:
            span = re.match(r"(\d+)\s*(?:to|-|–)\s*(\d+)$", part)
            if span and int(span.group(2)) - int(span.group(1)) < 12:
                out += [str(n) for n in range(int(span.group(1)), int(span.group(2)) + 1)]
            elif part:
                out.append(part.upper().replace(" ", ""))
        return out

    @staticmethod
    def _num(token: str) -> int:
        token = token.strip().upper()
        if token.isdigit():
            return int(token)
        from app.rulebook import roman_to_int
        return roman_to_int(token) or 0

    def _nearest(self, mentions: list[tuple[str, int, int]], position: int, candidates: list[str], default: str | None):
        """The candidate book named closest to a reference (books named after it count too)."""
        best = None
        for code, start, end in mentions:
            if code in candidates:
                distance = abs(start - position)
                if best is None or distance < best[0]:
                    best = (distance, code)
        return best[1] if best else default

    def rule_refs(self, text: str) -> tuple[list[str], list[tuple[int, int]]]:
        """Ids of rules / Order-rules / Articles named in the text, and the spans they used."""
        text = text or ""
        mentions = self.act_mentions(text)
        named = {c for c, _, _ in mentions}
        found: list[str] = []
        spans: list[tuple[int, int]] = []

        def add(section_id: str) -> None:
            if section_id in self.sections and section_id not in found:
                found.append(section_id)

        order_books = self._books_with("O")
        for m in self.ORDER_RULE.finditer(text):
            code = self._nearest(mentions, m.start(), order_books, "CPC" if "CPC" in self.acts else None)
            if not code:
                continue
            order = self._num(m.group(1))
            letter = (m.group(2) or "").upper()
            suffix = letter + ("C" if m.group(3) else "")
            for rule in self._expand(m.group(4)):
                add(f"{code}:O{order}{suffix}R{rule}")
            spans.append(m.span())
        chapter_books = [c for c in self._books_with("CH") if c in named]
        for pattern in (self.CHAPTER_RULE, self.RULE_OF_CHAPTER):
            for m in pattern.finditer(text):
                if any(a <= m.start() < b for a, b in spans):
                    continue
                if pattern is self.CHAPTER_RULE:
                    chapter, letter, rule = self._num(m.group(1)), (m.group(2) or "").upper(), m.group(3).upper()
                else:
                    rule, chapter, letter = m.group(1).upper(), self._num(m.group(2)), ""
                code = self._nearest(mentions, m.start(), chapter_books, None)
                if code:
                    before = len(found)
                    add(f"{code}:CH{chapter}{letter}R{rule}")
                    add(f"{code}:CH{chapter}{letter}P{rule}")
                    if len(found) > before:
                        spans.append(m.span())
        if "LIMITATION" in named:
            for m in self.ARTICLE.finditer(text):
                before = len(found)
                add(f"LIMITATION:ART{m.group(1)}")
                if len(found) > before:
                    spans.append(m.span())
        rule_books = [c for c in named if self.acts[c].kind in ("rules", "handbook")]
        for m in self.BARE_RULE.finditer(text):
            if any(a <= m.start() < b for a, b in spans) or not rule_books:
                continue
            code = self._nearest(mentions, m.start(), rule_books, None)
            if not code:
                continue
            rule = m.group(1).upper()
            exact = f"{code}:R{rule}"
            if exact in self.sections:
                add(exact)
                spans.append(m.span())
            else:   # numbering starts again in each chapter: only a unique match counts
                matches = [s.id for s in self.acts[code].sections if s.number.endswith("R" + rule)]
                if len(matches) == 1:
                    add(matches[0])
                    spans.append(m.span())
        return found, spans

    def resolve_citation(self, text: str) -> tuple[str | None, str | None]:
        """A citation tag -> (book code, provision id). 'CPC O. VII r. 11' -> ('CPC', 'CPC:O7R11');
        a known book with an unknown provision -> (code, None); not a book in the library -> (None, None)."""
        text = (text or "").strip()
        mentions = [m for m in self.act_mentions(text) if m[1] <= 2]
        if not mentions:
            return None, None
        code, _, end = max(mentions, key=lambda m: m[2] - m[1])
        rest = text[end:].strip(" ,")
        if not rest:
            return None, None
        key = re.sub(r"[^a-z0-9()]", "", rest.lower())
        act = self.acts[code]
        if not hasattr(self, "_ref_index"):
            self._ref_index: dict[str, dict[str, str]] = {}
        if code not in self._ref_index:
            self._ref_index[code] = {re.sub(r"[^a-z0-9()]", "", (s.ref or f"s. {s.number}").lower()): s.id
                                     for s in act.sections}
        if key in self._ref_index[code]:
            return code, self._ref_index[code][key]
        found, _ = self.rule_refs(f"{act.cite} {rest}")
        found = [f for f in found if f.startswith(code + ":")]
        if found:
            return code, found[0]
        schedule = re.search(r"sch(?:edule)?\.?\s*(?:i|1|first)\b.*?(\d{1,3}(?:\s*\(\s*\w{1,3}\s*\))*)", rest, re.I)
        if schedule and code == "BNSS":
            number = re.sub(r"\s+", "", schedule.group(1))
            for candidate in (f"BNSS:FS_{number}", *self.classification.get(re.match(r"\d+", number).group(0), [])):
                if candidate in self.sections:
                    return code, candidate
        page = re.match(r"p(?:age)?\.?\s*(\d{1,4})$", rest, re.I)
        if page and f"{code}:P{page.group(1)}" in self.sections:
            return code, f"{code}:P{page.group(1)}"
        section = re.match(r"(?:ss?\.|sec(?:tion)?\.?)\s*(\d{1,4}[A-Z]{0,3})", rest, re.I)
        if section:
            hit = self.section(code, section.group(1))
            return code, hit.id if hit else None
        return code, None

    def find_refs(self, text: str) -> list[str]:
        """Section ids mentioned in the text, e.g. 'u/s 302, 307 IPC' -> ['IPC:302', 'IPC:307'];
        'Order VII Rule 11 CPC' -> ['CPC:O7R11']."""
        text = text or ""
        rule_ids, spans = self.rule_refs(text)
        for start, end in spans:        # "Order 7 Rule 11 CPC" must not also read as "section 11 CPC"
            text = text[:start] + " " * (end - start) + text[end:]
        found: list[str] = list(rule_ids)

        def add(code: str, numbers: str) -> None:
            # "318(4)" is section 318: the (4) is a sub-section, not "section 4"
            numbers = re.sub(r"\(\s*\w{1,4}\s*\)", " ", numbers)
            for raw in re.findall(r"\d{1,4}\s?-?[A-Za-z]{0,3}", numbers):
                number = norm_number(raw)
                base = re.match(r"\d+", number).group(0)
                section = self.sections.get(code + ":" + number) or self.sections.get(code + ":" + base)
                if section and section.id not in found:
                    found.append(section.id)

        mentions = self.act_mentions(text)
        for code, start, end in mentions:
            before = text[max(0, start - 70):start]
            after = text[end:end + 40]
            # "section 302 of the IPC", "u/s 138 NI Act", "302 IPC"
            match = re.search(self.SECTION_WORD + r"\s*(" + self.NUMBER_LIST + r")\s*(?:of\s+(?:the\s+)?|,\s*)?$",
                              before, re.IGNORECASE)
            if match:
                add(code, match.group(1))
            else:
                bare = re.search(r"(?<![\w/])(" + self.NUMBER + r")\s*(?:of\s+(?:the\s+)?)?$", before, re.IGNORECASE)
                if bare:
                    add(code, bare.group(1))
            # "IPC 302", "BNS section 103", "NI Act, s. 138"
            match = re.match(r"\s*,?\s*(?:" + self.SECTION_WORD + r")?\s*(" + self.NUMBER_LIST + r")", after, re.IGNORECASE)
            if match and re.match(r"\s*,?\s*(?:" + self.SECTION_WORD + r"\s*)?\d", after, re.IGNORECASE):
                add(code, match.group(1))
        # "section 138" with exactly one act named somewhere in the question
        codes = {c for c, _, _ in mentions}
        if len(codes) == 1:
            code = next(iter(codes))
            for match in re.finditer(self.SECTION_WORD + r"\s*(" + self.NUMBER_LIST + ")", text, re.IGNORECASE):
                add(code, match.group(1))
        return found

    # ---- old <-> new criminal laws ----------------------------------------------
    def convert(self, code: str, number: str) -> dict:
        """IPC/CrPC/IEA section -> BNS/BNSS/BSA (or the other way round)."""
        code = code.upper()
        number = norm_number(number)
        if code in OLD_CODES:
            target = OLD_CODES[code]
        elif code in NEW_CODES:
            target = NEW_CODES[code]
        else:
            return {"from": f"{code}:{number}", "mappings": [], "error": "Only IPC, CrPC, Evidence Act, BNS, BNSS and BSA can be converted."}
        table = self.concordance.get(f"{code}>{target}", {})
        entries = table.get(number) or table.get(re.match(r"\d+", number).group(0) if re.match(r"\d+", number) else "", [])
        if not entries and "(" not in number:
            # Some sections are mapped sub-section by sub-section: BNS 318 = 318(1) ... 318(4) -> IPC 415 ... 420
            entries = [entry for key, rows in table.items() if key.startswith(number + "(") for entry in rows]
        source = self.sections.get(f"{code}:{number}")
        mappings = []
        for entry in entries:
            target_id = f"{target}:{entry['section']}" if entry.get("section") else None
            target_section = self.sections.get(target_id) if target_id else None
            mappings.append({
                "status": entry.get("status", ""), "section_id": target_id if target_section else None,
                "label": f"{self.acts[target].cite} s. {entry.get('sub') or entry.get('section')}" if entry.get("section") else None,
                "title": entry.get("title") or (target_section.title if target_section else ""),
                "statement": entry.get("statement", ""), "note": entry.get("note", ""),
            })
        return {
            "from": source.to_dict() if source else {"id": f"{code}:{number}", "label": f"{self.acts[code].cite} s. {number}"},
            "to_act": self.acts[target].to_dict(),
            "mappings": mappings,
        }

    def classification_of(self, section: Section) -> list[str]:
        """BNS s. 318 -> its rows in the BNSS First Schedule ('BNSS:FS_318(2)', 'BNSS:FS_318(4)', ...)."""
        if section.act != "BNS":
            return []
        base = re.match(r"\d+", section.number)
        rows = self.classification.get(base.group(0), []) if base else []
        exact = [r for r in rows if r == f"BNSS:FS_{section.number}"]
        return exact or rows[:4]

    def related(self, section: Section) -> list[dict]:
        """For a BNS/BNSS/BSA section: the old IPC/CrPC/IEA section(s), and vice versa."""
        if section.act not in OLD_CODES and section.act not in NEW_CODES:
            return []
        result = self.convert(section.act, section.number)
        return [m for m in result.get("mappings", []) if m.get("label")]


@lru_cache(maxsize=1)
def library() -> LawLibrary:
    return LawLibrary()


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
@dataclass
class LawChunk:
    id: str
    section_id: str
    position: int
    header: str
    text: str


@dataclass
class LawHit:
    section: Section
    score: float
    reason: str
    vector_rank: int | None = None
    keyword_rank: int | None = None
    similarity: float | None = None
    chunks: list[str] = field(default_factory=list)
    related: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        snippet = self.chunks[0] if self.chunks else self.section.text
        return {
            **self.section.to_dict(include_text=False), "score": round(self.score, 4), "reason": self.reason,
            "vector_rank": self.vector_rank, "keyword_rank": self.keyword_rank,
            "similarity": None if self.similarity is None else round(self.similarity, 3),
            "snippet": snippet[:300] + ("..." if len(snippet) > 300 else ""),
            "related": self.related,
        }


def make_chunks(section: Section) -> list[LawChunk]:
    header = f"{section.label} - {section.title} | {section.act_name}"
    if section.chapter_title:
        header += f" | Chapter {section.chapter} {section.chapter_title}"
    if section.act_status == "repealed":
        header += " | repealed (old law)"
    paragraphs = [p.strip() for p in re.split(r"\n+", section.text) if p.strip()]
    pieces, current = [], ""
    for paragraph in paragraphs:
        while len(paragraph) > CHUNK_CHARS * 1.6:
            cut = paragraph.rfind(" ", 0, CHUNK_CHARS)
            cut = cut if cut > 200 else CHUNK_CHARS
            if current:
                pieces.append(current)
                current = ""
            pieces.append(paragraph[:cut])
            paragraph = paragraph[cut:].strip()
        if current and len(current) + len(paragraph) + 1 > CHUNK_CHARS:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n{paragraph}" if current else paragraph
    if current or not pieces:
        pieces.append(current or section.title)
    return [LawChunk(f"{section.id}#{i}", section.id, i, header, piece) for i, piece in enumerate(pieces)]


class LawSearch:
    """Search across every Act. The meaning index is built in the background if missing."""

    def __init__(self, lib: LawLibrary | None = None, build_vectors: bool = True, background: bool = True):
        self.library = lib or library()
        self.chunks: list[LawChunk] = [c for s in self.library.sections.values() for c in make_chunks(s)]
        self.chunk_by_id = {c.id: c for c in self.chunks}
        self.chunks_by_section: dict[str, list[LawChunk]] = {}
        for chunk in self.chunks:
            self.chunks_by_section.setdefault(chunk.section_id, []).append(chunk)
        self.bm25 = BM25Okapi([tokenize(f"{c.header} {c.header} {c.text}") for c in self.chunks])
        self.collection = None
        self.status = "keywords only"
        self.progress = 0.0
        if build_vectors:
            if background:
                threading.Thread(target=self._open_vectors, daemon=True, name="law-index").start()
            else:
                self._open_vectors()

    @staticmethod
    def _vector_id(chunk: LawChunk) -> str:
        """Id in the meaning index: changes when the text changes, so only new / edited pieces are embedded."""
        digest = hashlib.sha1(f"{chunk.header}\n{chunk.text}".encode("utf-8")).hexdigest()[:10]
        return f"{chunk.id}|{digest}"

    COLLECTION = "law_pieces"

    def _open_vectors(self) -> None:
        """Open the meaning index and bring it up to date: embed only new pieces, drop removed ones.
        (The first time, pieces already embedded in an older 'acts_*' index are copied, not recomputed.)"""
        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

            ONNXMiniLM_L6_V2.DOWNLOAD_PATH = config.MODEL_CACHE_DIR / "all-MiniLM-L6-v2"
            client = chromadb.PersistentClient(path=str(config.INDEX_DIR), settings=Settings(anonymized_telemetry=False))
            embedder = ONNXMiniLM_L6_V2()
            collection = client.get_or_create_collection(self.COLLECTION, embedding_function=embedder,
                                                         metadata={"hnsw:space": "cosine"})
            wanted = {self._vector_id(c): c for c in self.chunks}
            self.vector_to_chunk = {vid: c.id for vid, c in wanted.items()}
            have = set(collection.get(include=[])["ids"]) if collection.count() else set()
            self.status = "updating"
            started = time.time()
            # Copy embeddings from an old index built before this one existed
            for old in [c if isinstance(c, str) else c.name for c in client.list_collections()]:
                if not old.startswith("acts_"):
                    continue
                old_collection = client.get_collection(old)
                total = old_collection.count()
                for offset in range(0, total, 2000):
                    got = old_collection.get(include=["embeddings", "documents", "metadatas"], limit=2000, offset=offset)
                    ids, vectors, documents, metadatas = [], [], [], []
                    for oid, vector, document, meta in zip(got["ids"], got["embeddings"], got["documents"], got["metadatas"]):
                        vid = f"{oid}|{hashlib.sha1(document.encode('utf-8')).hexdigest()[:10]}"
                        if vid in wanted and vid not in have:
                            ids.append(vid), vectors.append(vector), documents.append(document), metadatas.append(meta)
                    if ids:
                        collection.add(ids=ids, embeddings=vectors, documents=documents, metadatas=metadatas)
                        have.update(ids)
                client.delete_collection(old)
            stale = [vid for vid in have if vid not in wanted]
            for start in range(0, len(stale), 2000):
                collection.delete(ids=stale[start:start + 2000])
            todo = [(vid, c) for vid, c in wanted.items() if vid not in have]
            done = len(wanted) - len(todo)
            self.progress = done / max(1, len(wanted))
            if todo:
                self.collection = collection          # searchable while the new pieces are added
                self.status = "building"
            batch = 128
            for start in range(0, len(todo), batch):
                part = todo[start:start + batch]
                collection.add(ids=[vid for vid, _ in part], documents=[f"{c.header}\n{c.text}" for _, c in part],
                               metadatas=[{"section": c.section_id} for _, c in part])
                self.progress = (done + start + len(part)) / len(wanted)
            self.collection, self.status, self.progress = collection, "ready", 1.0
            if todo or stale:
                print(f"Law library meaning index updated: {len(todo)} new, {len(stale)} removed "
                      f"({len(wanted)} pieces, {time.time() - started:.0f} s).")
        except Exception as error:  # keyword search still works
            self.status = f"keywords only ({error})"

    def hints(self, query: str) -> list[str]:
        text = (query or "").lower()
        found: list[str] = []
        taken: list[tuple[int, int]] = []
        # Longest phrases first; "bail" inside "anticipatory bail" doesn't count again
        for phrase in sorted(LEGAL_HINTS, key=len, reverse=True):
            spans = [(m.start(), m.end()) for m in re.finditer(rf"(?<![\w]){re.escape(phrase)}(?![\w])", text)]
            spans = [s for s in spans if not any(a <= s[0] and s[1] <= b for a, b in taken)]
            if not spans:
                continue
            taken += spans
            for section_id in LEGAL_HINTS[phrase]:
                if section_id in self.library.sections and section_id not in found:
                    found.append(section_id)
        return found

    def search(self, query: str, k: int = 6, acts: list[str] | None = None, courts: list[str] | None = None) -> list[LawHit]:
        """courts: the lawyer's / the case's courts ("Delhi High Court") - their rule books rank higher."""
        query = (query or "").strip()
        if not query:
            return []
        lib = self.library
        exact = lib.find_refs(query)
        mentioned = acts or lib.mentioned_acts(query)
        wants_old = bool(OLD_LAW_WORDS.search(query))
        court_words = [c.lower() for c in (courts or []) if c and len(c) > 3]

        fused: dict[str, float] = {}
        vector_ranks: dict[str, int] = {}
        keyword_ranks: dict[str, int] = {}
        similarity: dict[str, float] = {}
        matched_chunks: set[str] = set()

        tokens = tokenize(query)
        if tokens:
            scores = self.bm25.get_scores(tokens)
            best = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:CANDIDATES * 2]
            for i in best:
                if scores[i] <= 0:
                    break
                chunk = self.chunks[i]
                matched_chunks.add(chunk.id)
                if chunk.section_id not in keyword_ranks:
                    keyword_ranks[chunk.section_id] = len(keyword_ranks) + 1
        if self.collection is not None:
            try:
                result = self.collection.query(query_texts=[query], n_results=CANDIDATES, include=["distances", "metadatas"])
                for vector_id, distance in zip(result["ids"][0], result["distances"][0]):
                    chunk_id = vector_id.split("|", 1)[0]
                    chunk = self.chunk_by_id.get(chunk_id)
                    if not chunk:
                        continue
                    matched_chunks.add(chunk_id)
                    similarity[chunk.section_id] = max(similarity.get(chunk.section_id, 0.0), 1 - distance)
                    if chunk.section_id not in vector_ranks:
                        vector_ranks[chunk.section_id] = len(vector_ranks) + 1
            except Exception:
                pass
        for ranks in (vector_ranks, keyword_ranks):
            for section_id, rank in ranks.items():
                fused[section_id] = fused.get(section_id, 0.0) + 1.0 / (RRF_K + rank)
        hints = self.hints(query)
        for rank, section_id in enumerate(hints, start=1):
            fused[section_id] = fused.get(section_id, 0.0) + HINT_WEIGHT / (RRF_K + rank)
        for section_id in list(fused):
            section = lib.sections[section_id]
            if mentioned and section.act in mentioned:
                fused[section_id] *= 2.0            # the question names this Act
            elif section.act_status == "repealed" and not wants_old:
                fused[section_id] *= 0.55           # prefer the law in force today
            if section.status == "omitted":
                fused[section_id] *= 0.3
            book = lib.acts[section.act]
            if book.kind != "act":
                states = [s.strip().lower() for s in book.jurisdiction.split(";") if s.strip() and s.strip() != "Central"]
                own_court = any(word in book.court.lower() or book.court.lower() in word for word in court_words)
                own_state = any(state in word for state in states for word in court_words)   # "Saket Court, New Delhi"
                if own_court or own_state:
                    fused[section_id] *= 1.8        # the rules of the lawyer's own court / State
                elif book.court and "district" not in book.court.lower() and section.act not in mentioned:
                    fused[section_id] *= 0.6        # another High Court's rules: only when nothing better
        # Exact references first; then, for old criminal-law sections, their new equivalents (and back);
        # for BNS sections, their row in the BNSS First Schedule (bailable? cognizable? which court?)
        order: list[str] = []
        for section_id in exact:
            order.append(section_id)
            for mapping in lib.related(lib.sections[section_id]):
                if mapping.get("section_id") and mapping["section_id"] not in order and mapping["section_id"] not in exact:
                    order.append(mapping["section_id"])
        for section_id in list(order):
            for row in lib.classification_of(lib.sections[section_id]):
                if row not in order:
                    order.append(row)
        for position, section_id in enumerate(order):
            fused[section_id] = 1.0 - position * 0.001
        ranked = sorted(fused, key=lambda s: fused[s], reverse=True)
        limit = max(k, min(len(order), 10))
        hits = []
        for section_id in ranked[:limit]:
            section = lib.sections[section_id]
            pieces = self.chunks_by_section.get(section_id, [])
            matching = [c for c in pieces if c.id in matched_chunks] or pieces
            hits.append(LawHit(
                section=section, score=fused[section_id],
                reason="exact reference" if section_id in exact else
                ("bail / cognizance table" if section_id.startswith("BNSS:FS_") and section_id in order
                 else "old/new equivalent" if section_id in order else "topic match" if section_id in hints else "search"),
                vector_rank=vector_ranks.get(section_id), keyword_rank=keyword_ranks.get(section_id),
                similarity=similarity.get(section_id),
                chunks=[c.text for c in sorted(matching, key=lambda c: c.position)],
                related=lib.related(section),
            ))
        return hits


_shared: dict = {}
_shared_lock = threading.Lock()


def shared_search(build_vectors: bool = True) -> LawSearch:
    """One LawSearch for the whole app (building the keyword index takes a few seconds)."""
    with _shared_lock:
        if "search" not in _shared:
            _shared["search"] = LawSearch(library(), build_vectors=build_vectors, background=True)
            _shared["vectors"] = build_vectors
        return _shared["search"]


def reload_library() -> LawSearch:
    """After a rule book is added or removed: read the library again; the meaning index only embeds what is new."""
    import os

    with _shared_lock:
        build_vectors = _shared.get("vectors", os.getenv("LAW_VECTORS", "1") != "0")
        library.cache_clear()
        _shared["search"] = LawSearch(library(), build_vectors=build_vectors, background=True)
        _shared["vectors"] = build_vectors
        return _shared["search"]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Search the Acts library, or build its index.")
    parser.add_argument("query", nargs="*")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    lib = library()
    print(f"{len(lib.acts)} Acts, {len(lib.sections)} sections.")
    engine = LawSearch(lib, build_vectors=True, background=False)
    print(f"Meaning index: {engine.status}")
    if args.build and not args.query:
        return 0
    question = " ".join(args.query) or "punishment for cheating"
    for i, hit in enumerate(engine.search(question, k=8), 1):
        extra = "; ".join(m["label"] for m in hit.related if m.get("label"))
        print(f"{i}. {hit.section.label}: {hit.section.title}  [{hit.reason}; score {hit.score:.4f}]"
              + (f"  <-> {extra}" if extra else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
