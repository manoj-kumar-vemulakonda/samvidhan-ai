"""
Gather the sources for one chat question - the R in RAG, for the whole workspace:

  * your case's own documents (only that case - or only that general chat's files)
  * sections of the Acts (BNS, BNSS, CPC, NI Act ...)
  * the Constitution (Articles and Schedules)
  * landmark-judgment notes

Each source gets a short citation tag that the answer must use, e.g. [Doc 4, p. 2],
[BNS s. 103], [Article 21], [Puttaswamy (2017)] - and every tag is checked afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app import casesearch, config, store
from app.laws import LawHit, LawSearch
from app.search import CaseHit, Hit, SearchEngine

CONSTITUTION_WORDS = re.compile(
    r"\b(constitution|constitutional|article\s*\d|art\.\s*\d|fundamental rights?|writ|habeas|mandamus|certiorari|"
    r"parliament|president|governor|preamble|schedule|amendment|union list|state list|concurrent list|"
    r"directive principles|basic structure|article\s+(?:14|19|21|22|32|226))\b|अनुच्छेद|संविधान", re.I)
MAX_DOC_PAGES = 6
MAX_SOURCE_CHARS = 5000


@dataclass
class Sources:
    query: str
    documents: list[casesearch.PageHit] = field(default_factory=list)
    acts: list[LawHit] = field(default_factory=list)
    constitution: list[Hit] = field(default_factory=list)
    judgments: list[CaseHit] = field(default_factory=list)
    case: dict | None = None
    case_index: list[dict] = field(default_factory=list)   # every document of the case (titles only)

    def ids(self) -> set[str]:
        """Every citation the answer may use, normalised."""
        found = {f"doc:{d.document_id}" for d in self.documents}
        found |= {f"doc:{d['id']}" for d in self.case_index}
        found |= {h.section.id for h in self.acts}
        found |= {h.article.id for h in self.constitution}
        found |= {c.case.id for c in self.judgments}
        return found

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "documents": [d.to_dict() for d in self.documents],
            "acts": [h.to_dict() for h in self.acts],
            "constitution": [h.to_dict() for h in self.constitution],
            "judgments": [c.to_dict() for c in self.judgments],
        }


def gather(query: str, constitution_engine: SearchEngine, law_engine: LawSearch | None,
           case_id: int | None = None, chat_id: int | None = None) -> Sources:
    sources = Sources(query=query)
    if case_id is not None:
        sources.case = store.get_case(case_id)
        sources.case_index = store.list_documents(case_id=case_id)
        sources.documents = casesearch.search(query, case_id=case_id, k=MAX_DOC_PAGES)
    elif chat_id is not None:
        sources.case_index = store.list_documents(chat_id=chat_id)
        if sources.case_index:
            sources.documents = casesearch.search(query, chat_id=chat_id, k=MAX_DOC_PAGES)

    wants_constitution = bool(CONSTITUTION_WORDS.search(query))
    # The court this case is in, and the courts the lawyer practises in: their rule books come first
    courts: list[str] = []
    if sources.case and sources.case.get("court"):
        courts.append(sources.case["court"])
    practice = store.get_settings().get("courts") or []
    courts += [c.strip() for c in (practice.split(",") if isinstance(practice, str) else practice) if c and c.strip()]
    act_hits = law_engine.search(query, k=8, courts=courts) if law_engine else []
    named_acts = law_engine.library.mentioned_acts(query) if law_engine else []
    constitution_hits = constitution_engine.search(query, k=6)
    if "LIMITATION" in named_acts and not re.search(r"constitution", query, re.I):
        # "Article 54 of the Limitation Act" is an Article of the Act's Schedule, not of the Constitution
        constitution_hits = [h for h in constitution_hits if h.reason != "exact reference"]
    # In a case whose own documents answer the question ("what did the court direct?"),
    # only clearly relevant law is added - loosely related sections would just be noise.
    about_the_file = bool(sources.documents)

    if wants_constitution and not named_acts:
        keep_constitution, keep_acts = 5, 3
    elif named_acts:
        keep_constitution, keep_acts = 1, 7
    else:
        keep_constitution, keep_acts = 2, 6
    exact_acts = [h for h in act_hits if h.reason != "search"]
    bar = 0.5 if about_the_file else 0.36

    def relevant(hit: LawHit) -> bool:
        if hit.similarity is None:  # the meaning index isn't ready: trust the keyword ranking
            return not about_the_file and (hit.keyword_rank or 99) <= 4
        return hit.similarity >= bar

    searched = [h for h in act_hits if h.reason == "search" and relevant(h)]
    sources.acts = (exact_acts + searched)[:max(keep_acts, len(exact_acts))]

    chosen = []
    for hit in constitution_hits:
        if about_the_file and not wants_constitution and hit.reason != "exact reference":
            continue
        strong = hit.reason in ("exact reference",) or (hit.similarity or 0) >= 0.5
        if wants_constitution or strong or (hit.reason == "topic match" and not named_acts):
            chosen.append(hit)
    sources.constitution = chosen[:keep_constitution]
    if sources.constitution:
        sources.judgments = constitution_engine.search_cases(query, sources.constitution, k=2 if not wants_constitution else 3)
    return sources


# ---------------------------------------------------------------------------
# Turning sources into the prompt
# ---------------------------------------------------------------------------
def _trim(text: str, limit: int = MAX_SOURCE_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "\n[... rest of the text omitted ...]"


def case_profile(case: dict | None, index: list[dict]) -> str:
    if not case and not index:
        return ""
    lines = []
    if case:
        details = [f"Title: {case['title']}"]
        for key, label in (("client", "Client"), ("side", "We act for"), ("opponent", "Other side"), ("court", "Court"),
                           ("case_type", "Type"), ("case_number", "Case number"), ("cnr", "CNR"), ("stage", "Stage")):
            if case.get(key):
                details.append(f"{label}: {case[key]}")
        upcoming = store.upcoming_events(365, case["id"])[:5]
        if upcoming:
            details.append("Upcoming dates: " + "; ".join(
                f"{e['date']}{' ' + e['time'] if e['time'] else ''} {e['kind']}: {e['title']}" for e in upcoming))
        if case.get("notes"):
            details.append(f"Lawyer's notes: {case['notes'][:1500]}")
        lines.append("<case>\n" + "\n".join(details) + "\n</case>")
    if index:
        rows = [f"[Doc {d['id']}] {d['title']} ({d['doc_type'] or 'document'}, {d['doc_date'] or 'no date'}, "
                f"{d['pages']} p.) - {d['summary'][:160]}" for d in index[:120]]
        lines.append("<case_file_index>\n" + "\n".join(rows) + "\n</case_file_index>")
    return "\n".join(lines)


def sources_block(sources: Sources) -> str:
    parts = []
    for hit in sources.documents:
        parts.append(
            f'<source cite_as="[Doc {hit.document_id}, p. {hit.page}]" kind="case document" '
            f'document="{hit.title}" type="{hit.doc_type}" date="{hit.doc_date}">\n{_trim(hit.text, 3500)}\n</source>')
    for hit in sources.acts:
        s = hit.section
        status = "REPEALED - applies only to old cases (see note)" if s.act_status == "repealed" else "in force"
        if s.status == "omitted":
            status = "this section is omitted/repealed"
        related = "; ".join(f"{m['label']} ({m['status'].lower()})" for m in hit.related if m.get("label"))
        extra = f' equivalent="{related}"' if related else ""
        text = "\n".join(hit.chunks) if len("\n".join(hit.chunks)) < len(s.text) and len(s.text) > MAX_SOURCE_CHARS else s.text
        from app.laws import library as law_library
        book = law_library().acts.get(s.act)
        kind = "statute"
        if book and book.kind != "act":
            kind = f'court {book.kind}" court="{book.court or "-"}" applies_in="{book.jurisdiction}'
        parts.append(f'<source cite_as="[{s.label}]" kind="{kind}" book="{s.act_name}" title="{s.title}" '
                     f'status="{status}"{extra}>\n{_trim(text)}\n</source>')
    for hit in sources.constitution:
        a = hit.article
        status = "OMITTED (no longer in force)" if a.status == "omitted" else "in force"
        notes = ""
        if a.notes:
            notes = "\n<court_note>" + " ".join(a.notes) + "</court_note>"
        text = "\n".join(hit.chunks) if len(a.text) > MAX_SOURCE_CHARS else a.text
        parts.append(f'<source cite_as="[{a.cite or a.label}]" kind="constitution" title="{a.title}" '
                     f'status="{status}">\n{_trim(text)}{notes}\n</source>')
    for hit in sources.judgments:
        c = hit.case
        parts.append(f'<source cite_as="[{c.label}]" kind="judgment note" name="{c.name}" year="{c.year}">\n'
                     f"Held: {c.held}\n{('Later: ' + c.later) if c.later else ''}\n</source>")
    return "<sources>\n" + "\n\n".join(parts) + "\n</sources>" if parts else "<sources>\n(none found)\n</sources>"


def law_only_ids(sources: Sources) -> list[str]:
    return [h.section.id for h in sources.acts] + [h.article.id for h in sources.constitution]
