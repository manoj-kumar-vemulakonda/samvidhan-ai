"""Web API for the law library: Acts, sections, the IPC -> BNS converter, the Constitution and judgments."""

from __future__ import annotations

import re

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from starlette.concurrency import run_in_threadpool

from app import config
from app.routes.common import state

router = APIRouter()


def _laws():
    laws = state.get("laws")
    if laws is None:
        raise HTTPException(status_code=503, detail="The law library is still loading. Try again in a moment.")
    return laws


@router.get("/api/laws")
def law_contents() -> dict:
    laws = _laws()
    return {"categories": laws.library.table_of_contents(), "source": laws.library.source,
            "acts": len(laws.library.acts), "sections": len(laws.library.sections), "index": laws.status}


@router.get("/api/laws/acts/{code}")
def law_act(code: str) -> dict:
    act = _laws().library.act(code.upper())
    if not act:
        raise HTTPException(status_code=404, detail=f"No Act with the code {code}.")
    parts = [{**p, "pdf_url": f"/api/laws/books/{act.code}/pdf?file={p['pdf']}"} for p in act.official_parts if p.get("pdf")]
    return {**act.to_dict(with_sections=True), "pdf_url": _pdf_url(act), "official_parts": parts}


def _pdf_url(act, section=None) -> str:
    """Link to the official PDF of a book (at the provision's page), or '' if there is none."""
    file = (section.pdf if section is not None and section.pdf else "") or act.pdf
    if not file:
        return ""
    url = f"/api/laws/books/{act.code}/pdf?file={file}"
    page = section.page if section is not None else None
    return url + (f"#page={page}" if page else "")


@router.get("/api/laws/sections/{section_id}")
def law_section(section_id: str) -> dict:
    lib = _laws().library
    section = lib.get(section_id)
    if not section:
        raise HTTPException(status_code=404, detail=f"{section_id} is not in the library.")
    act = lib.act(section.act)
    classification = []
    for row_id in lib.classification_of(section):
        row = lib.sections[row_id]
        classification.append({"id": row_id, "label": row.label, "text": row.text})
    part = next((p for p in act.official_parts if section.pdf and p.get("pdf") == section.pdf), None)
    return {**section.to_dict(), "related": lib.related(section), "act_note": act.note, "act_status": act.status,
            "source": part["source"] if part else act.source, "book_kind": act.kind, "court": act.court,
            "jurisdiction": act.jurisdiction, "as_on": (part or {}).get("as_on") or act.as_on,
            "official_url": (part or {}).get("url") or act.url, "pdf_url": _pdf_url(act, section),
            "classification": classification}


@router.get("/api/laws/books/{code}/pdf")
def law_pdf(code: str, file: str = Query("", max_length=120)):
    """The official PDF of a book in the library (or of a rule book the lawyer added)."""
    from fastapi.responses import FileResponse

    from app.laws import user_library_dir

    act = _laws().library.act(code.upper())
    if not act:
        raise HTTPException(status_code=404, detail=f"No book with the code {code}.")
    allowed = {act.pdf} | {p.get("pdf") for p in act.official_parts} | {s.pdf for s in act.sections if s.pdf}
    name = file or act.pdf
    if not name or name not in allowed or not re.fullmatch(r"[\w.\- ()]+\.pdf", name):
        raise HTTPException(status_code=404, detail="This book has no PDF in the library.")
    folder = user_library_dir() if act.user else config.PROJECT_DIR / "data" / "raw" / "official"
    path = folder / name
    if not path.exists():
        raise HTTPException(status_code=404, detail="The PDF file is missing; run scripts/build_official.py again.")
    return FileResponse(path, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.get("/api/laws/courts")
def law_courts() -> dict:
    """Every court in India: the rule books in the library for it, what is missing, and its website."""
    import json as _json

    path = config.LAWS_DIR / "courts.json"
    data = _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"courts": []}
    lib = _laws().library
    mine = [a.to_dict() for a in lib.acts.values() if a.user]
    for court in data.get("courts", []):
        court["books"] = [b for b in court.get("books", []) if b["code"] in lib.acts]
        court["books"] += [{"code": a["code"], "name": a["name"], "kind": a["kind"], "count": a["sections_count"], "user": True}
                           for a in mine if court["name"].lower() in (a.get("court") or "").lower()]
    from app import rulebooks
    return {**data, "my_books": mine, "jobs": rulebooks.jobs(), "inbox": str(rulebooks.inbox_dir())}


def _swap_engine(search) -> None:
    state["laws"] = search


@router.post("/api/laws/books")
async def add_rule_book(book: UploadFile | None = File(None), text: str = Form(""), name: str = Form(""), cite: str = Form(""),
                        court: str = Form(""), jurisdiction: str = Form(""), numbering: str = Form("auto")) -> dict:
    """Add a rule book: any court's rules as a PDF, or their text pasted in. It is read in the background;
    watch /api/stream or /api/laws/courts."""
    from app import rulebooks

    if not name.strip() and not cite.strip():
        raise HTTPException(status_code=400, detail="Give the rule book a name, e.g. 'Rules of the High Court of Kerala, 1971'.")
    try:
        if book is not None and book.filename:
            content = await book.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
            if len(content) > config.MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(status_code=413, detail=f"The PDF is larger than {config.MAX_UPLOAD_MB} MB.")
            code = rulebooks.start(content, book.filename or "rules.pdf", name or cite, cite or name, court, jurisdiction,
                                   numbering, on_done=_swap_engine)
        elif text.strip():
            code = rulebooks.start_text(text[:3_000_000], name or cite, cite or name, court, jurisdiction, numbering,
                                        on_done=_swap_engine)
        else:
            raise HTTPException(status_code=400, detail="Choose a PDF, or paste the text of the rules.")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"code": code, "status": "reading"}


@router.delete("/api/laws/books/{code}")
def remove_rule_book(code: str) -> dict:
    from app import rulebooks

    if not rulebooks.remove(code.upper(), on_done=_swap_engine):
        raise HTTPException(status_code=404, detail="Only rule books you added yourself can be removed.")
    return {"removed": code.upper()}


@router.get("/api/laws/search")
async def law_search(q: str = Query(..., min_length=1, max_length=500), k: int = Query(10, ge=1, le=30)) -> dict:
    laws = _laws()
    act_hits = await run_in_threadpool(laws.search, q, k)
    constitution_hits = await run_in_threadpool(state["engine"].search, q, 4)
    return {"query": q, "acts": [h.to_dict() for h in act_hits], "constitution": [h.to_dict() for h in constitution_hits]}


CONVERT_REF = re.compile(r"^\s*([A-Za-z.\s]+?)\s*(?:s\.|sec\.?|section)?\s*(\d{1,4}\s?-?[A-Za-z]{0,3})\s*$", re.I)


@router.get("/api/convert")
def convert(q: str = Query(..., min_length=1, max_length=80)) -> dict:
    """'IPC 420', '498A', 'CrPC 438', 'BNS 103', '65B Evidence Act' -> the matching section(s) in the other law."""
    lib = _laws().library
    text = q.strip()
    refs = lib.find_refs(text)
    candidates = [r for r in refs if r.split(":")[0] in ("IPC", "CRPC", "IEA", "BNS", "BNSS", "BSA")]
    if not candidates:
        match = CONVERT_REF.match(text)
        number_only = re.fullmatch(r"\s*(\d{1,4}\s?-?[A-Za-z]{0,3})\s*", text)
        if match:
            code = lib.find_act(match.group(1)) or {"crpc": "CRPC", "ipc": "IPC", "iea": "IEA"}.get(match.group(1).strip().lower().replace(".", ""))
            if code:
                candidates = [f"{code}:{match.group(2)}"]
        elif number_only:
            candidates = [f"IPC:{number_only.group(1)}"]   # a bare number usually means an IPC section
    if not candidates:
        raise HTTPException(status_code=400, detail="Type an old or new section, e.g. 'IPC 420', 'CrPC 438', 'BNS 103' or '65B Evidence Act'.")
    code, number = candidates[0].split(":", 1)
    if code not in ("IPC", "CRPC", "IEA", "BNS", "BNSS", "BSA"):
        raise HTTPException(status_code=400, detail="The converter works between IPC/CrPC/Evidence Act and BNS/BNSS/BSA.")
    result = lib.convert(code, number)
    for mapping in result["mappings"]:
        section = lib.get(mapping["section_id"]) if mapping.get("section_id") else None
        mapping["text"] = section.text if section else ""
    source = lib.get(f"{code}:{number}")
    result["from_text"] = source.text if source else ""
    return result


# ---- the Constitution (kept from versions 1 and 2) ---------------------------
@router.get("/api/parts")
def parts() -> list[dict]:
    return state["engine"].constitution.table_of_contents()


@router.get("/api/articles/{article_id}")
def article(article_id: str) -> dict:
    found = state["engine"].constitution.get(article_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{article_id} was not found.")
    return found.to_dict()


@router.get("/api/search")
async def constitution_search(q: str = Query(..., min_length=1, max_length=config.MAX_QUESTION_CHARS),
                              k: int = Query(config.SEARCH_RESULTS, ge=1, le=20)) -> dict:
    hits = await run_in_threadpool(state["engine"].search, q, k)
    return {"query": q, "results": [h.to_dict() for h in hits]}


# ---- landmark judgments ------------------------------------------------------
@router.get("/api/judgments")
def judgments() -> dict:
    library = state["engine"].cases
    return {"about": library.about, "cases": [c.to_dict() for c in library.cases]}


@router.get("/api/judgments/{case_id}")
def judgment(case_id: str) -> dict:
    found = state["engine"].cases.get(case_id)
    if found is None:
        raise HTTPException(status_code=404, detail=f"{case_id} is not in the judgment library.")
    return found.to_dict()
