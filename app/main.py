"""
The web server. It does three things:
  1. Serves the web page (the files in app/static/).
  2. Answers the page's requests on /api/... (see the files in app/routes/).
  3. Runs the background helpers: the reminder service and the law-library index.

start.bat runs it with:  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import webbrowser
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import config, db, reminders
from app.answer import stream_answer
from app.bus import bus
from app.language import english_query
from app.routes import calendar, library, tools, workspace
from app.routes.common import SSE_HEADERS, sse, state
from app.search import SearchEngine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load the Constitution, the Acts library and the workspace database once, at start-up.
    db.init()
    bus.attach(asyncio.get_running_loop())
    state["engine"] = SearchEngine(build_if_missing=True)
    try:
        from app.laws import shared_search

        # The meaning index of the Acts is built in the background the first time (~20 minutes);
        # until then the law search uses exact references and keywords.
        state["laws"] = await run_in_threadpool(shared_search, os.getenv("LAW_VECTORS", "1") != "0")
    except FileNotFoundError:
        state["laws"] = None
        print("  (The Acts library is missing: run scripts\\build_laws.py)")
    reminder_task = None
    if os.getenv("REMINDERS", "1") != "0":
        reminder_task = asyncio.create_task(reminders.run_forever())
    # Rule-book inbox: PDFs saved into <workspace>/library/inbox are added to the library automatically
    stop_inbox = threading.Event()
    if os.getenv("INBOX", "1") != "0":
        from app import rulebooks

        rulebooks.inbox_dir().mkdir(parents=True, exist_ok=True)
        threading.Thread(target=rulebooks.watch_inbox, args=(stop_inbox, lambda search: state.__setitem__("laws", search)),
                         daemon=True, name="rulebook-inbox").start()
    mode = f"AI answers ON - {config.engine_label()}" if config.ai_enabled() else "search-only"
    print(f"\n  {config.APP_NAME} is ready: http://127.0.0.1:8000   [{mode}]\n  Your work is saved in {config.WORKSPACE_DIR}\n")
    if os.getenv("OPEN_BROWSER") == "1":
        threading.Timer(1.0, lambda: webbrowser.open("http://127.0.0.1:8000")).start()
    yield
    stop_inbox.set()
    if reminder_task:
        reminder_task.cancel()


app = FastAPI(title=config.APP_NAME, lifespan=lifespan)
app.include_router(tools.router)
app.include_router(workspace.router)
app.include_router(calendar.router)
app.include_router(library.router)


# ---------------------------------------------------------------------------
# The original one-question endpoint (Constitution only, no chat history in the
# database). Kept for compatibility; the workspace uses /api/chats/{id}/ask.
# ---------------------------------------------------------------------------
class HistoryTurn(BaseModel):
    question: str = ""
    answer: str = ""


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=config.MAX_QUESTION_CHARS)
    style: Literal["simple", "detailed"] = "simple"
    history: list[HistoryTurn] = Field(default_factory=list, max_length=10)


@app.post("/api/ask")
async def ask(request: AskRequest) -> StreamingResponse:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Please type a question.")
    history = [turn.model_dump() for turn in request.history][-2:]
    search_query = question
    if history and len(question.split()) < 8:
        search_query = f"{history[-1]['question']} {question}"
    search_query, language, translation = await english_query(search_query)
    engine = state["engine"]
    hits = await run_in_threadpool(engine.search, search_query, config.TOP_K)
    case_hits = await run_in_threadpool(engine.search_cases, search_query, hits)

    async def events():
        yield sse("sources", {
            "query": search_query, "language": language, "translation": translation,
            "ai_enabled": config.ai_enabled(), "sources": [h.to_dict() for h in hits],
            "cases": [c.to_dict() for c in case_hits],
        })
        if not config.ai_enabled():
            yield sse("done", {"type": "done", "mode": "search_only"})
            return
        async for event in stream_answer(question, hits, request.style, history, cases=case_hits):
            yield sse(event["type"], event)

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


# The web page itself (index.html, styles.css, js/...). Mounted last so /api wins.
app.mount("/", StaticFiles(directory=config.STATIC_DIR, html=True), name="static")

__all__ = ["app", "json"]
