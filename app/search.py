"""
Steps 1-2 of RAG: find the Articles that best match a question.

Three ways of finding them, combined:
  1. Exact lookup  - "Article 21A" or "art. 19(1)(a)" in the question -> fetch it directly.
  2. Meaning search - ChromaDB turns every piece of text into an embedding (a list of
     384 numbers that captures its meaning). "Can police arrest me?" lands close to
     Article 22 even though the words differ.
  3. Keyword search - BM25 scores pieces that share rare words with the question,
     e.g. "habeas corpus" or "Rajya Sabha", which meaning search can miss.
The rankings from 2 and 3 are merged with "reciprocal rank fusion" (RRF):
an Article ranked high by either method rises to the top.

Run it directly to try it out:
    .venv\\Scripts\\python.exe -m app.search "Can police arrest me without a warrant?"
    .venv\\Scripts\\python.exe -m app.search --build      (build the index; setup.bat does this)
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from dataclasses import dataclass, field

import chromadb
from chromadb.config import Settings
from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
from rank_bm25 import BM25Okapi

from app import config
from app.cases import Case, CaseLibrary, load_cases
from app.citations import find_article_refs, find_schedule_refs, mentioned_schedules
from app.constitution import Article, Constitution, load_constitution

CHUNK_CHARS = 900      # the embedding model reads ~256 tokens (~1,000 characters) at most
CANDIDATES = 40        # how many pieces each search method returns before merging
RRF_K = 60             # standard constant for reciprocal rank fusion

# Keep the embedding model download inside this project (.cache/models), not on drive C:
ONNXMiniLM_L6_V2.DOWNLOAD_PATH = config.MODEL_CACHE_DIR / "all-MiniLM-L6-v2"

# Everyday words whose Articles don't use that word (e.g. the Constitution never says
# "Lok Sabha" - it says "House of the People"; "privacy" comes from how the Supreme Court
# reads Article 21). A small hand-made map like this is called query expansion.
TOPIC_HINTS: dict[str, list[str]] = {
    "privacy": ["21"], "dignity": ["21"], "livelihood": ["21"], "right to life": ["21"],
    "death penalty": ["21", "72"], "capital punishment": ["21", "72"], "health": ["21", "47"],
    "pollution": ["21", "48A"], "environment": ["48A", "51A"],
    "speech": ["19"], "expression": ["19"], "press": ["19"], "newspaper": ["19"], "media": ["19"],
    "protest": ["19"], "internet": ["19", "21"],
    "equality": ["14"], "discrimination": ["15", "14"], "caste": ["15", "17"], "untouchability": ["17"],
    "arrest": ["22", "21"], "police": ["22", "21"], "custody": ["22", "21"], "bail": ["21", "22"],
    "writ": ["32", "226"], "habeas corpus": ["32", "226"], "mandamus": ["32", "226"],
    "pil": ["32", "226"], "public interest litigation": ["32", "226"],
    "religion": ["25", "26"], "conversion": ["25"], "secular": ["Preamble", "25"],
    "property": ["300A", "31"], "land acquisition": ["300A", "31A"],
    "child labour": ["24"], "child labor": ["24"], "trafficking": ["23"], "bonded labour": ["23"],
    "reservation": ["15", "16"], "reservations": ["16", "15"], "quota": ["15", "16"],
    "obc": ["15", "16", "338B"],
    "minority": ["29", "30"], "minorities": ["29", "30"],
    "emergency": ["352", "356", "360"], "president's rule": ["356"], "presidents rule": ["356"],
    "pardon": ["72", "161"], "mercy petition": ["72", "161"],
    "lok sabha": ["81", "83"], "rajya sabha": ["80", "83"], "parliament": ["79"],
    "prime minister": ["74", "75"], "chief minister": ["163", "164"],
    "supreme court": ["124"], "high court": ["214", "226"],
    "gst": ["246A", "279A"], "goods and services tax": ["246A", "279A"],
    "money bill": ["110"], "budget": ["112"],
    "uniform civil code": ["44"], "ucc": ["44"], "legal aid": ["39A"],
    "official language": ["343"], "hindi": ["343", "351"],
    "kashmir": ["370"], "citizenship": ["5", "11"],
    "fundamental duties": ["51A"], "directive principles": ["37"],
    "vote": ["326"], "voting": ["326"], "election commission": ["324"],
    "impeachment": ["61"],
    # The Schedules (S7-II-2 = Seventh Schedule, List II, Entry 2; see app/citations.py)
    "defection": ["S10-P2", "S10-P4", "102", "191"], "anti-defection": ["S10-P2", "S10-P4", "102", "191"],
    "anti defection": ["S10-P2", "S10-P4", "102", "191"], "party switch": ["S10-P2"],
    "languages": ["S8", "343"], "scheduled languages": ["S8", "344"], "eighth schedule": ["S8"],
    "panchayat": ["243G", "S11", "243B"], "panchayats": ["243G", "S11", "243B"], "gram sabha": ["243A"],
    "municipality": ["243W", "S12"], "municipalities": ["243W", "S12"], "municipal": ["243W", "S12"],
    "union list": ["246"], "state list": ["246"], "concurrent list": ["246"], "seventh schedule": ["246"],
    "state subject": ["246"], "central subject": ["246"], "make laws on": ["246"],
    "ninth schedule": ["31B", "S9"], "tribal areas": ["244", "S6-P1"], "autonomous district": ["244", "S6-P1"],
    "scheduled areas": ["244", "S5-P6"], "oath": ["S3-I", "S3-III", "60"],
    "salary of the president": ["S2-A", "59"], "salary of judges": ["S2-D", "125"],
    "judges appointed": ["124", "124A", "217"], "appointment of judges": ["124", "124A", "217"],
    "appoint judges": ["124", "124A", "217"], "collegium": ["124", "124A", "217"],
    "njac": ["124A", "124", "124B"], "judicial appointments": ["124A", "124", "124B"],
}

# Questions about WHO may make laws on a subject are answered by the Seventh Schedule's
# three Lists, so these words make the search prefer Seventh Schedule entries.
LAW_MAKING = re.compile(
    r"\b(?:make|makes|making|pass|passes|enact|frame)\b(?:\s+[\w']+){0,3}?\s+laws?\b"  # "make its own laws"
    r"|\blegislat|\b(?:state|union|central|centre) subject"
    r"|\bconcurrent\b|\b(?:centre|center|union|central government) or (?:the )?states?\b",
    re.IGNORECASE,
)
HINT_WEIGHT = 2.0  # a topic hint counts as much as 2 "first places" in the merged ranking

# How close in meaning (0..1) a case note must be to the question to be used:
CASE_FIT_TOP = 0.28        # ...if it is about one of the 2 best provisions found
CASE_FIT_OTHER = 0.40      # ...if it is about another provision found
CASE_STRONG = 0.45         # ...if it is about none of them

STOPWORDS = set(
    """a an the and or but if of to in on at by for with from as is are was were be been being
    it its this that these those i me my we our you your he she his her they them their what
    which who whom whose when where why how can could do does did shall should will would may
    might must not no yes any all some there here than then so such into about under over also
    article articles art constitution india indian""".split()
)


# ---------------------------------------------------------------------------
# Splitting Articles into pieces ("chunks")
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    id: str           # e.g. "22#1" = second piece of Article 22
    article_id: str
    position: int     # order of the piece within its Article
    header: str       # "Article 22 - Protection against arrest ... | Part III Fundamental Rights"
    text: str         # the piece of Article text

    @property
    def indexed_text(self) -> str:
        """What gets embedded: the header makes every piece self-describing."""
        return f"{self.header}\n{self.text}"


def _split_long_paragraph(paragraph: str) -> list[str]:
    """Cut a very long paragraph at sentence/semicolon boundaries."""
    if len(paragraph) <= CHUNK_CHARS:
        return [paragraph]
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.;:])\s+", paragraph):
        if current and len(current) + len(sentence) + 1 > CHUNK_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def make_chunks(article: Article) -> list[Chunk]:
    """Short provisions -> 1 piece. Long ones -> pieces of whole clauses, ~900 characters each."""
    header = f"{article.label} - {article.title}"
    if article.status == "omitted":
        header += " (omitted, no longer in force)"
    if article.is_schedule:
        header += f" | {article.location}"
        if article.refs:
            header += f" | belongs to {article.refs}"
    else:
        if article.part:
            header += f" | Part {article.part} {article.part_title}"
        if article.chapter:
            header += f" | {article.chapter}"
    if article.notes:
        header += " | struck down or declared invalid in part by the Supreme Court"

    paragraphs = []
    for p in article.text.split("\n"):
        if p.strip():
            paragraphs.extend(_split_long_paragraph(p.strip()))

    pieces, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) + 1 > CHUNK_CHARS:
            pieces.append(current)
            current = p
        else:
            current = f"{current}\n{p}" if current else p
    if current or not pieces:
        pieces.append(current)

    return [
        Chunk(id=f"{article.id}#{i}", article_id=article.id, position=i, header=header, text=piece)
        for i, piece in enumerate(pieces)
    ]


# ---------------------------------------------------------------------------
# Keyword search helpers
# ---------------------------------------------------------------------------
def _stem(word: str) -> str:
    """A tiny stemmer so 'arrested' matches 'arrest' and 'rights' matches 'right'."""
    if word.endswith("ies") and len(word) > 5:
        return word[:-3] + "y"
    for suffix in ("ations", "ation", "ings", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [_stem(w) for w in words if w not in STOPWORDS and len(w) > 1]


def topic_hints(query: str, known_ids: set[str]) -> list[str]:
    """Articles suggested by everyday words in the question (see TOPIC_HINTS)."""
    text = query.lower()
    found: list[str] = []
    for phrase, article_ids in TOPIC_HINTS.items():
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            for article_id in article_ids:
                if article_id in known_ids and article_id not in found:
                    found.append(article_id)
    return found


# ---------------------------------------------------------------------------
# Search results
# ---------------------------------------------------------------------------
@dataclass
class Hit:
    article: Article
    score: float                  # merged score (higher = better)
    reason: str                   # "exact reference" or "search"
    vector_rank: int | None = None    # rank in meaning search (1 = best)
    keyword_rank: int | None = None   # rank in keyword search (1 = best)
    similarity: float | None = None   # 0..1, how close in meaning the best piece was
    chunks: list[str] = field(default_factory=list)  # matching pieces, in Article order

    def to_dict(self) -> dict:
        snippet = self.chunks[0] if self.chunks else self.article.text
        return {
            **self.article.to_dict(include_text=False),
            "score": round(self.score, 4),
            "reason": self.reason,
            "vector_rank": self.vector_rank,
            "keyword_rank": self.keyword_rank,
            "similarity": None if self.similarity is None else round(self.similarity, 3),
            "snippet": snippet[:300] + ("..." if len(snippet) > 300 else ""),
        }


def _rank_articles(chunk_ids: list[str], chunk_by_id: dict[str, Chunk]) -> dict[str, int]:
    """Turn a ranked list of pieces into a ranking of Articles (best piece counts)."""
    ranks: dict[str, int] = {}
    for chunk_id in chunk_ids:
        article_id = chunk_by_id[chunk_id].article_id
        if article_id not in ranks:
            ranks[article_id] = len(ranks) + 1
    return ranks


@dataclass
class CaseHit:
    """A landmark-judgment note found for a question."""

    case: Case
    score: float
    reason: str                       # "named in the question", "about the provisions found" or "search"
    similarity: float | None = None   # 0..1, how close in meaning the note is to the question

    def to_dict(self) -> dict:
        return {
            **self.case.to_dict(),
            "score": round(self.score, 4),
            "reason": self.reason,
            "similarity": None if self.similarity is None else round(self.similarity, 3),
        }


class SearchEngine:
    def __init__(
        self,
        constitution: Constitution | None = None,
        build_if_missing: bool = True,
        cases: CaseLibrary | None = None,
    ):
        self.constitution = constitution or load_constitution()
        self.cases = cases if cases is not None else load_cases()
        # Articles AND the pieces of the Schedules are searched together
        self.chunks: list[Chunk] = [c for a in self.constitution.provisions for c in make_chunks(a)]
        self.chunk_by_id = {c.id: c for c in self.chunks}

        # Keyword index (rebuilt in memory every start - it takes well under a second).
        # The header is included twice so that words in the title count extra.
        self.bm25 = BM25Okapi([tokenize(f"{c.header} {c.header} {c.text}") for c in self.chunks])
        self.case_bm25 = BM25Okapi([tokenize(c.search_text) for c in self.cases.cases]) if self.cases.cases else None

        # Meaning indexes (ChromaDB, saved on disk in the "index" folder)
        self._embedder = ONNXMiniLM_L6_V2()
        self._chroma = chromadb.PersistentClient(
            path=str(config.INDEX_DIR), settings=Settings(anonymized_telemetry=False)
        )
        self.collection = self._open_collection(
            "constitution",
            ids=[c.id for c in self.chunks],
            documents=[c.indexed_text for c in self.chunks],
            metadatas=[{"article": c.article_id} for c in self.chunks],
            build_if_missing=build_if_missing,
        )
        self.case_collection = None
        if self.cases.cases:
            self.case_collection = self._open_collection(
                "cases",
                ids=[c.id for c in self.cases.cases],
                documents=[c.search_text for c in self.cases.cases],
                metadatas=[{"case": c.id} for c in self.cases.cases],
                build_if_missing=build_if_missing,
            )

    # ---- the vector indexes ---------------------------------------------------
    def _open_collection(self, prefix: str, ids: list[str], documents: list[str],
                         metadatas: list[dict], build_if_missing: bool):
        # The collection name contains a fingerprint of the data, so if the text
        # (or the chunking) changes, a fresh index is built automatically.
        digest = hashlib.sha256()
        for piece_id, document in zip(ids, documents):
            digest.update(piece_id.encode())
            digest.update(document.encode("utf-8"))
        name = f"{prefix}_{digest.hexdigest()[:12]}"
        client = self._chroma
        existing = [c if isinstance(c, str) else c.name for c in client.list_collections()]
        if name in existing:
            return client.get_collection(name, embedding_function=self._embedder)
        if not build_if_missing:
            raise RuntimeError("The search index is missing. Run setup.bat (or: python -m app.search --build).")

        print(f"Building the {prefix} search index for {len(ids)} pieces of text (first run only)...")
        started = time.time()
        for old in existing:
            if old.startswith(f"{prefix}_"):  # an outdated index of the same kind
                client.delete_collection(old)
        collection = client.create_collection(
            name, embedding_function=self._embedder, metadata={"hnsw:space": "cosine"}
        )
        batch = 128
        for start in range(0, len(ids), batch):
            end = start + batch
            collection.add(ids=ids[start:end], documents=documents[start:end], metadatas=metadatas[start:end])
            print(f"  indexed {min(end, len(ids))}/{len(ids)}", end="\r")
        print(f"\nSearch index ready in {time.time() - started:.0f} seconds.")
        return collection

    # ---- searching -----------------------------------------------------------
    def search(self, query: str, k: int = config.TOP_K) -> list[Hit]:
        query = query.strip()
        if not query:
            return []

        # 1. Exact references like "Article 21A" or "Entry 97 of List I"
        exact_ids = find_article_refs(query, self.constitution.ids)
        exact_ids += [i for i in find_schedule_refs(query, self.constitution.ids) if i not in exact_ids]

        # 2. Meaning search
        result = self.collection.query(
            query_texts=[query], n_results=CANDIDATES, include=["distances"]
        )
        vector_ids = result["ids"][0]
        similarity_by_chunk = {cid: 1 - d for cid, d in zip(vector_ids, result["distances"][0])}

        # 3. Keyword search
        tokens = tokenize(query)
        keyword_ids: list[str] = []
        if tokens:
            scores = self.bm25.get_scores(tokens)
            best = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:CANDIDATES]
            keyword_ids = [self.chunks[i].id for i in best if scores[i] > 0]

        # Merge the rankings (reciprocal rank fusion): 1 / (60 + rank) from each list
        vector_ranks = _rank_articles(vector_ids, self.chunk_by_id)
        keyword_ranks = _rank_articles(keyword_ids, self.chunk_by_id)
        hint_ids = topic_hints(query, self.constitution.ids)
        if LAW_MAKING.search(query) and "246" not in hint_ids:
            hint_ids.append("246")  # Article 246: the rule that divides law-making powers
        fused: dict[str, float] = {}
        for ranks in (vector_ranks, keyword_ranks):
            for article_id, rank in ranks.items():
                fused[article_id] = fused.get(article_id, 0.0) + 1.0 / (RRF_K + rank)
        for rank, article_id in enumerate(hint_ids, start=1):
            fused[article_id] = fused.get(article_id, 0.0) + HINT_WEIGHT / (RRF_K + rank)

        # A Schedule named in the question ("... in the Seventh Schedule?"): its pieces that
        # the searches found move up, keeping their order among themselves.
        schedules = mentioned_schedules(query)
        if LAW_MAKING.search(query) and 7 not in schedules:
            schedules.append(7)
        for number in schedules:
            pieces = {a.id for a in self.constitution.schedule_pieces(number)}
            found_pieces = [a for a in fused if a in pieces]
            if not found_pieces:  # none found: fall back to the first pieces of that Schedule
                found_pieces = [a.id for a in self.constitution.schedule_pieces(number)][:3]
                for article_id in found_pieces:
                    fused.setdefault(article_id, 0.0)
            for article_id in found_pieces:
                fused[article_id] += HINT_WEIGHT / (RRF_K + 1)

        # An omitted Schedule entry is just "* * *" - it rarely helps, so it moves down
        for article_id in fused:
            piece = self.constitution.by_id[article_id]
            if piece.is_schedule and piece.status == "omitted":
                fused[article_id] *= 0.5

        # Exact references always come first
        for position, article_id in enumerate(exact_ids):
            fused[article_id] = 1.0 - position * 0.001
        ordered = sorted(fused, key=lambda a: fused[a], reverse=True)
        limit = max(k, min(len(exact_ids), 10))

        matched_chunks = set(vector_ids[:CANDIDATES]) | set(keyword_ids)
        hits = []
        for article_id in ordered[:limit]:
            article = self.constitution.by_id[article_id]
            pieces = [c for c in self.chunks if c.article_id == article_id]
            matching = [c for c in pieces if c.id in matched_chunks] or pieces
            similarities = [similarity_by_chunk[c.id] for c in pieces if c.id in similarity_by_chunk]
            hits.append(
                Hit(
                    article=article,
                    score=fused[article_id],
                    reason=(
                        "exact reference" if article_id in exact_ids
                        else "topic match" if article_id in hint_ids
                        else "search"
                    ),
                    vector_rank=vector_ranks.get(article_id),
                    keyword_rank=keyword_ranks.get(article_id),
                    similarity=max(similarities) if similarities else None,
                    chunks=[c.text for c in sorted(matching, key=lambda c: c.position)],
                )
            )
        return hits

    # ---- landmark judgments ---------------------------------------------------
    def search_cases(self, query: str, hits: list[Hit], k: int = config.MAX_CASES) -> list[CaseHit]:
        """
        The case notes that fit a question (at most k). A case is used when:
          - the question names it ("What did Kesavananda decide?"), or
          - it is about one of the provisions found AND fits the question, or
          - it is very close in meaning to the question on its own.
        """
        library = self.cases
        query = query.strip()
        if not query or not library.cases or self.case_collection is None:
            return []
        count = len(library.cases)

        named = {c.id for c in library.named_in(query)}
        result = self.case_collection.query(query_texts=[query], n_results=count, include=["distances"])
        similarity = {cid: 1 - d for cid, d in zip(result["ids"][0], result["distances"][0])}
        vector_rank = {cid: rank for rank, cid in enumerate(result["ids"][0], start=1)}
        keyword_rank: dict[str, int] = {}
        tokens = tokenize(query)
        if tokens:
            scores = self.case_bm25.get_scores(tokens)
            order = [i for i in sorted(range(count), key=lambda i: scores[i], reverse=True) if scores[i] > 0]
            keyword_rank = {library.cases[i].id: rank for rank, i in enumerate(order, start=1)}
        hit_rank = {h.article.id: rank for rank, h in enumerate(hits, start=1)}

        found: list[CaseHit] = []
        for case in library.cases:
            sim = similarity.get(case.id, 0.0)
            linked = [hit_rank[a] for a in case.articles if a in hit_rank]
            if case.id in named:
                reason, score = "named in the question", 1.0
            elif named and sim < CASE_STRONG:
                continue  # the question is about a named case: only add very close ones
            elif linked and sim >= (CASE_FIT_TOP if min(linked) <= 2 else CASE_FIT_OTHER):
                reason = "about the provisions found"
                score = HINT_WEIGHT / (RRF_K + min(linked))
            elif sim >= CASE_STRONG:
                reason, score = "search", 0.0
            else:
                continue
            score += 1.0 / (RRF_K + vector_rank.get(case.id, count)) + 1.0 / (RRF_K + keyword_rank.get(case.id, count))
            found.append(CaseHit(case=case, score=score, reason=reason, similarity=sim))
        found.sort(key=lambda h: h.score, reverse=True)
        return found[:k]


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Build the search index or try a search.")
    parser.add_argument("query", nargs="*", help="a question to search for")
    parser.add_argument("--build", action="store_true", help="build the search index")
    args = parser.parse_args()

    engine = SearchEngine(build_if_missing=True)
    if args.build and not args.query:
        c = engine.constitution
        print(f"Index OK: {len(engine.chunks)} pieces from {len(c.articles)} Articles "
              f"and {len(c.schedules)} Schedule entries.")
        return 0
    question = " ".join(args.query) or "Can police arrest me without a warrant?"
    print(f"Question: {question}\n")
    hits = engine.search(question, k=config.SEARCH_RESULTS)
    for i, hit in enumerate(hits, 1):
        a = hit.article
        print(
            f"{i}. {a.label}: {a.title}  [{hit.reason}; meaning rank {hit.vector_rank}, "
            f"keyword rank {hit.keyword_rank}, score {hit.score:.4f}]"
        )
    for case_hit in engine.search_cases(question, hits[: config.TOP_K]):
        print(f"   case: {case_hit.case.short}  [{case_hit.reason}; similarity {case_hit.similarity:.2f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
