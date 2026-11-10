"""Shared pieces for the route files: the loaded engines and small helpers."""

from __future__ import annotations

import json

from fastapi import HTTPException

from app import store

state: dict = {}   # filled at start-up by app/main.py: "engine" (Constitution), "laws" (Acts search)


def sse(event: str, data: dict) -> str:
    """One Server-Sent Event: the browser reads these one by one as they arrive."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def need_case(case_id: int) -> dict:
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(status_code=404, detail="That case was not found.")
    return case


def need_chat(chat_id: int) -> dict:
    chat = store.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="That chat was not found.")
    return chat


def need_document(document_id: int) -> dict:
    document = store.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="That document was not found.")
    return document
