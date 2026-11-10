"""
Web API for the workspace: cases, chats, messages, file uploads, organisation plans,
documents - and asking questions in a chat (streamed).
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import assistant, config, exports, files, intake, llm, organizer, retrieval, store
from app.bus import bus
from app.language import english_query
from app.routes.common import SSE_HEADERS, need_case, need_chat, need_document, sse, state

router = APIRouter()


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------
class CaseFields(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    client: str | None = None
    opponent: str | None = None
    side: str | None = None
    court: str | None = None
    case_type: str | None = None
    case_number: str | None = None
    cnr: str | None = None
    stage: str | None = None
    status: Literal["active", "closed"] | None = None
    notes: str | None = Field(default=None, max_length=5000)


@router.get("/api/cases")
def list_cases(status: str | None = None, q: str = "") -> list[dict]:
    return store.list_cases(status, q)


@router.post("/api/cases")
def create_case(fields: CaseFields) -> dict:
    case = store.create_case(fields.model_dump(exclude_none=True))
    bus.publish("cases", {"case_id": case["id"]})
    return {"case": case, "chats": store.list_chats(case["id"])}


@router.get("/api/cases/{case_id}")
def get_case(case_id: int) -> dict:
    case = need_case(case_id)
    return {
        "case": case,
        "chats": store.list_chats(case_id),
        "upcoming": store.upcoming_events(365, case_id),
        "documents": len(store.list_documents(case_id=case_id)),
    }


@router.patch("/api/cases/{case_id}")
def update_case(case_id: int, fields: CaseFields) -> dict:
    need_case(case_id)
    changes = fields.model_dump(exclude_none=True)
    if "title" in changes:
        organizer._rename_case_folder(store.get_case(case_id), changes["title"])
    case = store.update_case(case_id, changes)
    bus.publish("cases", {"case_id": case_id})
    return case


@router.delete("/api/cases/{case_id}")
def delete_case(case_id: int) -> dict:
    need_case(case_id)
    for document in store.list_documents(case_id=case_id):
        organizer._unindex_document(document["id"])
    store.delete_case(case_id)
    bus.publish("cases", {"case_id": case_id, "deleted": True})
    bus.publish("calendar", {})
    return {"deleted": True, "note": "The case folder was moved to workspace\\_deleted (not erased)."}


@router.post("/api/cases/{case_id}/open-folder")
def open_folder(case_id: int) -> dict:
    case = need_case(case_id)
    return {"opened": files.open_in_explorer(files.case_dir(case)), "path": str(files.case_dir(case))}


@router.get("/api/cases/{case_id}/documents")
def case_documents(case_id: int) -> dict:
    case = need_case(case_id)
    documents = store.list_documents(case_id=case_id)
    folders: dict[str, list] = {}
    for document in documents:
        folders.setdefault(document["folder"] or "Other", []).append(document)
    return {"case": case, "folders": [{"name": name, "documents": docs} for name, docs in sorted(folders.items())],
            "count": len(documents), "pending_uploads": store.pending_uploads(case_id),
            "path": str(files.case_dir(case))}


@router.get("/api/cases/{case_id}/timeline")
def case_timeline(case_id: int) -> dict:
    """Every dated document and calendar entry of the case, in date order (the chronology)."""
    need_case(case_id)
    items = []
    for document in store.list_documents(case_id=case_id):
        if document["doc_date"]:
            items.append({"date": document["doc_date"], "kind": "document", "title": document["title"],
                          "type": document["doc_type"], "document_id": document["id"], "summary": document["summary"]})
    for event in store.list_events("1900-01-01", "2999-12-31", case_id):
        items.append({"date": event["date"], "time": event["time"], "kind": event["kind"], "title": event["title"],
                      "event_id": event["id"], "status": event["status"], "document_id": event.get("document_id")})
    items.sort(key=lambda i: (i["date"], i.get("time") or ""))
    return {"items": items}


@router.get("/api/cases/{case_id}/data-room.zip")
def data_room(case_id: int, originals: bool = False) -> Response:
    case = need_case(case_id)
    content = exports.data_room_zip(case, include_originals=originals)
    name = files.safe_name(f"{case['title']} - data room", 80) + ".zip"
    return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/cases/{case_id}/index.xlsx")
def case_index(case_id: int) -> Response:
    case = need_case(case_id)
    content = exports.index_xlsx(case, store.list_documents(case_id=case_id))
    name = files.safe_name(f"{case['title']} - index", 80) + ".xlsx"
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/api/cnr/{cnr}")
def explain_cnr(cnr: str) -> dict:
    return store.describe_cnr(cnr)


# ---------------------------------------------------------------------------
# Chats and messages
# ---------------------------------------------------------------------------
class NewChat(BaseModel):
    case_id: int | None = None
    title: str = Field(default="New chat", max_length=120)


@router.get("/api/chats")
def list_chats(case_id: int | None = None) -> list[dict]:
    return store.list_chats(case_id) if case_id else store.list_chats(general=True)


@router.post("/api/chats")
def create_chat(request: NewChat) -> dict:
    if request.case_id:
        need_case(request.case_id)
    chat = store.create_chat(request.case_id, request.title or "New chat")
    bus.publish("chats", {"case_id": request.case_id})
    return chat


def _expand(message: dict) -> dict:
    """Attach the live plan / workflow run to messages that show one."""
    data = message.get("data") or {}
    if message["role"] == "plan" and data.get("plan_id"):
        message["plan"] = store.get_plan(data["plan_id"])
    if message["role"] == "workflow" and data.get("run_id"):
        message["run"] = store.get_run(data["run_id"])
    if message["role"] == "files" and data.get("batch"):
        message["uploads"] = store.batch_uploads(data["batch"])
    return message


@router.get("/api/chats/{chat_id}")
def get_chat(chat_id: int) -> dict:
    chat = need_chat(chat_id)
    case = store.get_case(chat["case_id"]) if chat["case_id"] else None
    return {"chat": chat, "case": case, "messages": [_expand(m) for m in store.list_messages(chat_id)],
            "documents": store.list_documents(chat_id=chat_id) if not case else []}


class ChatRename(BaseModel):
    title: str = Field(max_length=120)


@router.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: int, request: ChatRename) -> dict:
    need_chat(chat_id)
    chat = store.rename_chat(chat_id, request.title)
    bus.publish("chats", {"case_id": chat["case_id"]})
    return chat


@router.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int) -> dict:
    chat = need_chat(chat_id)
    if chat["case_id"] and len(store.list_chats(chat["case_id"])) <= 1:
        raise HTTPException(status_code=400, detail="A case keeps at least one chat.")
    for document in store.list_documents(chat_id=chat_id):
        organizer._unindex_document(document["id"])
    store.delete_chat(chat_id)
    bus.publish("chats", {"case_id": chat["case_id"]})
    return {"deleted": True}


class MakeCase(BaseModel):
    title: str = Field(default="", max_length=200)


@router.post("/api/chats/{chat_id}/make-case")
async def make_case(chat_id: int, request: MakeCase) -> dict:
    chat = need_chat(chat_id)
    if chat["case_id"]:
        raise HTTPException(status_code=400, detail="This chat already belongs to a case.")
    if not store.list_documents(chat_id=chat_id):
        raise HTTPException(status_code=400, detail="There are no files in this chat yet.")
    case = await run_in_threadpool(organizer.make_case_from_chat, chat_id, request.title)
    bus.publish("cases", {"case_id": case["id"]})
    return {"case": case, "chats": store.list_chats(case["id"])}


@router.get("/api/messages/{message_id}/docx")
def message_docx(message_id: int, save: bool = False, title: str = "") -> Response:
    message = store.get_message(message_id)
    if not message or message["role"] not in ("assistant", "workflow"):
        raise HTTPException(status_code=404, detail="That answer was not found.")
    chat = store.get_chat(message["chat_id"])
    case = store.get_case(chat["case_id"]) if chat and chat["case_id"] else None
    heading = title.strip() or message["data"].get("title") or "Research note"
    content = exports.to_docx(heading, message["content"], store.get_settings(), case)
    saved = None
    if save and case:
        saved = exports.save_draft_to_case(case, heading, content)
        bus.publish("documents", {"case_id": case["id"]})
    name = files.safe_name(heading, 80) + ".docx"
    headers = {"Content-Disposition": f'attachment; filename="{name}"'}
    if saved:
        headers["X-Saved-To"] = saved["folder"] + "/" + saved["filename"]
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers=headers)


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
@router.post("/api/chats/{chat_id}/uploads")
async def upload_files(chat_id: int, uploads: list[UploadFile] = File(...)) -> dict:
    chat = need_chat(chat_id)
    case = store.get_case(chat["case_id"]) if chat["case_id"] else None
    if len(uploads) > config.MAX_FILES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"Please upload at most {config.MAX_FILES_PER_UPLOAD} files at once.")
    batch = uuid.uuid4().hex[:12]
    folder = files.originals_dir(case, chat_id)
    saved, skipped = [], []
    for upload in uploads:
        name = upload.filename or "file"
        kind = files.file_kind(name)
        if not kind:
            skipped.append({"name": name, "reason": "This kind of file can't be read (use PDF, Word .docx, images or text)."})
            continue
        try:
            path, size, digest = await run_in_threadpool(files.save_upload, upload.file, folder, name,
                                                         config.MAX_UPLOAD_MB * 1024 * 1024)
        except ValueError as error:
            skipped.append({"name": name, "reason": str(error)})
            continue
        duplicate = store.find_duplicate_upload(digest, case["id"] if case else None, batch)
        record = store.add_upload(case["id"] if case else None, chat_id, batch, name, files.relative(path), size, digest, kind)
        if duplicate:
            record["duplicate_of"] = duplicate["original_name"]
        saved.append(record)
    if not saved:
        raise HTTPException(status_code=400, detail=skipped[0]["reason"] if skipped else "No files received.")
    message = store.add_message(chat_id, "files", "", {"batch": batch, "skipped": skipped,
                                                       "duplicates": [u["original_name"] for u in saved if u.get("duplicate_of")]})
    bus.publish("message", {"chat_id": chat_id, "message": _expand(message)})
    organizer.start_batch(batch)
    return {"batch": batch, "uploads": saved, "skipped": skipped, "message": _expand(message)}


# ---------------------------------------------------------------------------
# The home-screen drop box: a pile of files from one case or many (app/intake.py)
# ---------------------------------------------------------------------------
@router.post("/api/intake")
async def intake_upload(uploads: list[UploadFile] = File(...)) -> dict:
    if len(uploads) > config.MAX_FILES_PER_UPLOAD:
        raise HTTPException(status_code=400, detail=f"Please drop at most {config.MAX_FILES_PER_UPLOAD} files at once.")
    record = store.create_intake()
    folder = intake.folder(record["id"])
    batch = intake.batch_name(record["id"])
    saved, skipped = [], []
    for upload in uploads:
        name = upload.filename or "file"
        kind = files.file_kind(name)
        if not kind:
            skipped.append({"name": name, "reason": "This kind of file can't be read (use PDF, Word .docx, images or text)."})
            continue
        try:
            path, size, digest = await run_in_threadpool(files.save_upload, upload.file, folder, name,
                                                         config.MAX_UPLOAD_MB * 1024 * 1024)
        except ValueError as error:
            skipped.append({"name": name, "reason": str(error)})
            continue
        saved.append(store.add_upload(None, None, batch, name, files.relative(path), size, digest, kind))
    if not saved:
        store.update_intake(record["id"], status="discarded")
        raise HTTPException(status_code=400, detail=skipped[0]["reason"] if skipped else "No files received.")
    data = {"files": [{"id": u["id"], "name": u["original_name"], "status": u["status"], "pages": 0, "error": ""}
                      for u in saved], "skipped": skipped, "groups": [], "notes": []}
    record = store.update_intake(record["id"], data=data, progress=f"Reading {len(saved)} file{'s' if len(saved) != 1 else ''}…")
    intake.start(record["id"])
    return record


@router.get("/api/intake")
def intake_list() -> list[dict]:
    return store.open_intakes()


def _intake_call(function, *args):
    try:
        return function(*args)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.get("/api/intake/{intake_id}")
def intake_get(intake_id: int) -> dict:
    found = store.get_intake(intake_id)
    if not found:
        raise HTTPException(status_code=404, detail="This upload was not found.")
    return found


class IntakeMove(BaseModel):
    upload_id: int
    to: str = Field(min_length=1, max_length=20)        # a group key ("g2") or "new"


@router.post("/api/intake/{intake_id}/move")
async def intake_move(intake_id: int, request: IntakeMove) -> dict:
    return await run_in_threadpool(_intake_call, intake.move_file, intake_id, request.upload_id, request.to)


class IntakeApply(BaseModel):
    groups: list[dict] = Field(default_factory=list)


@router.post("/api/intake/{intake_id}/apply")
async def intake_apply(intake_id: int, request: IntakeApply | None = None) -> dict:
    return await run_in_threadpool(_intake_call, intake.apply, intake_id, request.model_dump() if request else None)


@router.post("/api/intake/{intake_id}/discard")
def intake_discard(intake_id: int) -> dict:
    return _intake_call(intake.discard, intake_id)


@router.post("/api/intake/{intake_id}/close")
def intake_close(intake_id: int) -> dict:
    return _intake_call(intake.close, intake_id)


@router.get("/api/uploads/{upload_id}/file")
def upload_file(upload_id: int) -> FileResponse:
    upload = store.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return FileResponse(files.resolve(upload["path"]), filename=upload["original_name"],
                        content_disposition_type="inline")


@router.get("/api/uploads/{upload_id}/pages")
def upload_pages(upload_id: int) -> dict:
    upload = store.get_upload(upload_id)
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return {"upload": upload, "pages": store.upload_pages(upload_id)}


# ---------------------------------------------------------------------------
# Organisation plans
# ---------------------------------------------------------------------------
class PlanEdits(BaseModel):
    documents: list[dict] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    case: dict = Field(default_factory=dict)


class PlanRevision(BaseModel):
    instruction: str = Field(min_length=2, max_length=2000)
    current: PlanEdits | None = None


def _need_plan(plan_id: int) -> dict:
    plan = store.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    return plan


@router.get("/api/plans/{plan_id}")
def get_plan(plan_id: int) -> dict:
    return _need_plan(plan_id)


@router.put("/api/plans/{plan_id}")
async def save_plan(plan_id: int, edits: PlanEdits) -> dict:
    _need_plan(plan_id)
    try:
        return await run_in_threadpool(organizer.save_plan_edits, plan_id, edits.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/api/plans/{plan_id}/revise")
async def revise_plan(plan_id: int, request: PlanRevision) -> dict:
    _need_plan(plan_id)
    try:
        if request.current:
            await run_in_threadpool(organizer.save_plan_edits, plan_id, request.current.model_dump())
        return await run_in_threadpool(organizer.revise_plan, plan_id, request.instruction)
    except llm.AIUnavailable as error:
        raise HTTPException(status_code=400, detail=str(error))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error))


@router.post("/api/plans/{plan_id}/apply")
async def apply_plan(plan_id: int, edits: PlanEdits | None = None) -> dict:
    _need_plan(plan_id)
    try:
        return await run_in_threadpool(organizer.apply_plan, plan_id, edits.model_dump() if edits else None)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/api/plans/{plan_id}/discard")
def discard_plan(plan_id: int) -> dict:
    _need_plan(plan_id)
    try:
        return organizer.discard_plan(plan_id)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))


@router.post("/api/plans/{plan_id}/retry")
async def retry_plan(plan_id: int) -> dict:
    """Make a fresh plan for the same files (e.g. after the AI was unavailable)."""
    plan = _need_plan(plan_id)
    if plan["status"] not in ("draft", "failed"):
        raise HTTPException(status_code=400, detail="This plan was already used.")
    store.update_plan(plan_id, status="discarded")
    new_plan = await run_in_threadpool(organizer.make_plan, plan["batch"])
    return new_plan or {}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class DocumentEdit(BaseModel):
    title: str | None = Field(default=None, max_length=160)
    doc_type: str | None = Field(default=None, max_length=60)
    doc_date: str | None = None
    summary: str | None = Field(default=None, max_length=500)
    folder: str | None = Field(default=None, max_length=60)
    filename: str | None = Field(default=None, max_length=120)


@router.get("/api/documents/{document_id}")
def get_document(document_id: int) -> dict:
    document = need_document(document_id)
    return {**document, "page_texts": store.document_pages(document_id),
            "events": [e for e in store.list_events("1900-01-01", "2999-12-31", document.get("case_id"))
                       if e.get("document_id") == document_id] if document.get("case_id") else []}


@router.get("/api/documents/{document_id}/file")
def document_file(document_id: int) -> FileResponse:
    document = need_document(document_id)
    path = files.resolve(document["path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="The file is missing from the case folder.")
    return FileResponse(path, filename=document["filename"] or path.name, content_disposition_type="inline")


@router.patch("/api/documents/{document_id}")
def edit_document(document_id: int, edit: DocumentEdit) -> dict:
    document = need_document(document_id)
    changes = edit.model_dump(exclude_none=True)
    if "doc_date" in changes:
        changes["doc_date"] = organizer.iso_or_empty(changes["doc_date"])
    # Moving / renaming the actual file on disk (case documents only)
    if document.get("case_id") and ("folder" in changes or "filename" in changes):
        case = store.get_case(document["case_id"])
        source = files.resolve(document["path"])
        folder = files.safe_folder(changes.get("folder") or document["folder"])
        name = files.safe_name(changes.get("filename") or document["filename"])
        if not name.lower().endswith(source.suffix.lower()):
            name += source.suffix
        target = files.case_dir(case) / folder / name
        if target != source:
            target = files.unique_path(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            changes.update({"folder": target.parent.name, "filename": target.name, "path": files.relative(target)})
    updated = store.update_document(document_id, changes)
    bus.publish("documents", {"case_id": document.get("case_id")})
    return updated


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: int) -> dict:
    document = need_document(document_id)
    if document.get("case_id"):
        source = files.resolve(document["path"])
        if source.exists():
            bin_dir = files.resolve("_deleted")
            bin_dir.mkdir(parents=True, exist_ok=True)
            source.replace(files.unique_path(bin_dir / source.name))
    organizer._unindex_document(document_id)
    store.delete_document(document_id)
    bus.publish("documents", {"case_id": document.get("case_id")})
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Asking a question in a chat (streamed)
# ---------------------------------------------------------------------------
class ChatQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    style: Literal["simple", "detailed"] | None = None
    deep: bool = False  # the "Deep" button: the strongest model, thinking longer


@router.post("/api/chats/{chat_id}/ask")
async def ask_in_chat(chat_id: int, request: ChatQuestion) -> StreamingResponse:
    chat = need_chat(chat_id)
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Please type a question.")
    settings = store.get_settings()
    style = request.style or settings.get("style", "detailed")
    history = store.chat_history(chat_id)
    user_message = store.add_message(chat_id, "user", question)
    if not chat["case_id"] and chat["title"] in ("New chat", ""):
        chat = store.rename_chat(chat_id, assistant.chat_title(question))
        bus.publish("chats", {"case_id": None})

    search_query = question
    if history and len(question.split()) < 8:
        search_query = f"{history[-1]['question']} {question}"
    search_query, language, translation = await english_query(search_query)
    sources = await run_in_threadpool(
        retrieval.gather, search_query, state["engine"], state.get("laws"),
        chat["case_id"], None if chat["case_id"] else chat_id)
    reply_language = assistant.answer_language(question, language, settings)
    sources_data = {**sources.to_dict(), "language": language, "translation": translation}

    async def events():
        yield sse("start", {"user_message": user_message, "chat": chat})
        yield sse("sources", {**sources_data, "ai_enabled": llm.available()})
        if not llm.available():
            message = store.add_message(chat_id, "assistant", "", {"sources": sources_data, "mode": "search_only"})
            yield sse("done", {"mode": "search_only", "message": message})
            return
        system = assistant.system_prompt(settings, style, reply_language)
        prompt = assistant.prompt_parts(question, sources, history)
        pieces: list[str] = []
        final: dict | None = None
        failure = ""
        saved = False
        try:
            async for event in llm.stream(system, prompt, tier=llm.DEEP if request.deep else llm.MAIN):
                if event["type"] == "delta":
                    pieces.append(event["text"])
                    yield sse("delta", {"text": event["text"]})
                elif event["type"] == "done":
                    final = event
                else:
                    failure = event.get("message", "Something went wrong.")
            answer = "".join(pieces)
            library = state["laws"].library if state.get("laws") else None
            report = assistant.check_answer(answer, sources, library, state["engine"].cases)
            data = {"sources": sources_data, "citations": report, "style": style, "language": reply_language,
                    "model": (final or {}).get("model"), "cost": (final or {}).get("cost"), "error": failure or None,
                    "stop_reason": (final or {}).get("stop_reason"), "deep": request.deep}
            message = store.add_message(chat_id, "assistant", answer, data)
            saved = True
            if failure and not answer:
                yield sse("error", {"message": failure, "message_record": message})
            else:
                yield sse("done", {"mode": "ai", "message": message, "citations": report,
                                   "model": data["model"], "cost": data["cost"]})
        finally:
            if not saved and pieces:  # stopped half-way: keep what was written
                store.add_message(chat_id, "assistant", "".join(pieces) + "\n\n*(stopped)*",
                                  {"sources": sources_data, "stopped": True})

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)
