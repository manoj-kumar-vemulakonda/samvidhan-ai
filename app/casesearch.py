"""
Searching YOUR documents - always inside one case (or one general chat), never across cases.

Two methods, merged with reciprocal rank fusion (like the law search):
  * keywords: SQLite FTS5 over every page (exact words, names, numbers like "FIR 123/2026")
  * meaning:  ChromaDB embeddings of every page, stored in workspace/index (same small model
              as the law search, runs on your PC)

The case filter is applied INSIDE both searches (a WHERE clause and a metadata filter), so a
question in one case can never pull pages from another case.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from app import config, db

CHUNK_CHARS = 900
CANDIDATES = 30
RRF_K = 60
_lock = threading.Lock()
_collection = None

STOPWORDS = set("""a an the and or but if of to in on at by for with from as is are was were be been it its this that
these those i me my we our you your he she his her they them their what which who whom when where why how can could do
does did shall should will would may might must not no any all some there here than then so such into about under over
also case document documents file files tell show give please""".split())


def _collection_handle():
    """The workspace's vector collection (created on first use)."""
    global _collection
    with _lock:
        if _collection is None:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

            ONNXMiniLM_L6_V2.DOWNLOAD_PATH = config.MODEL_CACHE_DIR / "all-MiniLM-L6-v2"
            client = chromadb.PersistentClient(path=str(config.WORKSPACE_DIR / "index"),
                                               settings=Settings(anonymized_telemetry=False))
            _collection = client.get_or_create_collection(
                "case_pages", embedding_function=ONNXMiniLM_L6_V2(), metadata={"hnsw:space": "cosine"})
        return _collection


def reset() -> None:
    """Forget the open collection (tests switch workspaces)."""
    global _collection
    with _lock:
        _collection = None


def chunks_of(page_text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", page_text or "").strip()
    if not text:
        return []
    pieces, current = [], ""
    for paragraph in re.split(r"\n+", text):
        if current and len(current) + len(paragraph) > CHUNK_CHARS:
            pieces.append(current)
            current = paragraph
        else:
            current = f"{current}\n{paragraph}" if current else paragraph
        while len(current) > CHUNK_CHARS * 1.5:
            pieces.append(current[:CHUNK_CHARS])
            current = current[CHUNK_CHARS:]
    if current:
        pieces.append(current)
    return pieces


def index_document(document: dict, pages: list[str]) -> None:
    ids, texts, metas = [], [], []
    header = f"{document.get('doc_type', '')}: {document.get('title', '')}"
    for page_number, text in enumerate(pages, start=1):
        for index, piece in enumerate(chunks_of(text)):
            ids.append(f"doc{document['id']}-p{page_number}-{index}")
            texts.append(f"{header}\n{piece}")
            metas.append({"document_id": int(document["id"]), "page": page_number,
                          "case_id": int(document.get("case_id") or -1), "chat_id": int(document.get("chat_id") or -1)})
    if not ids:
        return
    collection = _collection_handle()
    for start in range(0, len(ids), 64):
        collection.upsert(ids=ids[start:start + 64], documents=texts[start:start + 64], metadatas=metas[start:start + 64])


def remove_document(document_id: int) -> None:
    try:
        _collection_handle().delete(where={"document_id": int(document_id)})
    except Exception:
        pass


def move_chat_to_case(chat_id: int, case_id: int) -> None:
    collection = _collection_handle()
    found = collection.get(where={"chat_id": int(chat_id)}, include=["metadatas"])
    if found["ids"]:
        metas = [{**m, "case_id": int(case_id), "chat_id": -1} for m in found["metadatas"]]
        collection.update(ids=found["ids"], metadatas=metas)


@dataclass
class PageHit:
    document_id: int
    page: int
    score: float
    text: str
    title: str = ""
    doc_type: str = ""
    doc_date: str = ""
    filename: str = ""
    folder: str = ""

    @property
    def cite(self) -> str:
        return f"Doc {self.document_id}, p. {self.page}"

    def to_dict(self) -> dict:
        snippet = re.sub(r"\s+", " ", self.text).strip()
        return {
            "kind": "document", "id": f"doc-{self.document_id}-p{self.page}", "document_id": self.document_id,
            "page": self.page, "title": self.title, "doc_type": self.doc_type, "date": self.doc_date,
            "filename": self.filename, "folder": self.folder, "cite": self.cite, "score": round(self.score, 4),
            "snippet": snippet[:300] + ("..." if len(snippet) > 300 else ""),
        }


def _fts_query(question: str) -> str:
    words = [w for w in re.findall(r"[\wऀ-ॿ]+", question.lower()) if w not in STOPWORDS and len(w) > 1]
    numbers = re.findall(r"\d+/\d+", question)
    terms = [f'"{w}"' for w in dict.fromkeys(words + numbers)][:20]
    return " OR ".join(terms)


def search(question: str, case_id: int | None = None, chat_id: int | None = None, k: int = 6,
           use_vectors: bool = True) -> list[PageHit]:
    """The best pages for a question, from one case (case_id) or one general chat (chat_id)."""
    if case_id is None and chat_id is None:
        return []
    scope_sql, scope_value = ("case_id", case_id) if case_id is not None else ("chat_id", chat_id)
    ranked: dict[tuple[int, int], float] = {}

    query = _fts_query(question)
    if query:
        try:
            keyword_rows = db.rows(
                f"SELECT document_id, page FROM page_search WHERE page_search MATCH ? AND {scope_sql} = ?"
                " ORDER BY bm25(page_search) LIMIT ?", (query, scope_value, CANDIDATES))
        except Exception:
            keyword_rows = []
        for rank, row in enumerate(keyword_rows, start=1):
            key = (int(row["document_id"]), int(row["page"]))
            ranked[key] = ranked.get(key, 0.0) + 1.0 / (RRF_K + rank)

    if use_vectors:
        try:
            collection = _collection_handle()
            where = {scope_sql: int(scope_value)}
            count = len(collection.get(where=where, limit=CANDIDATES, include=[])["ids"])
            if count:
                found = collection.query(query_texts=[question], n_results=min(CANDIDATES, count), where=where,
                                         include=["metadatas"])
                seen: set = set()
                rank = 0
                for meta in found["metadatas"][0]:
                    key = (int(meta["document_id"]), int(meta["page"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    rank += 1
                    ranked[key] = ranked.get(key, 0.0) + 1.0 / (RRF_K + rank)
        except Exception:
            pass

    best = sorted(ranked.items(), key=lambda item: item[1], reverse=True)[:k]
    hits = []
    for (document_id, page), score in best:
        row = db.one(
            "SELECT d.title, d.doc_type, d.doc_date, d.filename, d.folder, d.case_id, d.chat_id, p.text FROM documents d"
            " JOIN document_pages p ON p.document_id = d.id WHERE d.id = ? AND p.page = ?", (document_id, page))
        if not row:
            continue
        # Belt and braces: never return a page from outside the requested case / chat
        if case_id is not None and row["case_id"] != case_id:
            continue
        if case_id is None and row["chat_id"] != chat_id:
            continue
        hits.append(PageHit(document_id, page, score, row["text"], row["title"], row["doc_type"], row["doc_date"],
                            row["filename"], row["folder"]))
    return hits
