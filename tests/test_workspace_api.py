"""
The workspace web API, end to end, with AI switched off (no cost, works offline):
case -> upload -> keyword plan -> approve -> documents, calendar, reminders, chat, tools.
"""

import json
import time
from datetime import date, timedelta

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import assistant, config, store
from app.main import app
from app.retrieval import Sources


@pytest.fixture
def client(workspace, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_ENGINE", "off")
    with TestClient(app) as test_client:
        yield test_client


def pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=11)
    return document.tobytes()


def sse_events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


def wait_for(fn, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("timed out")


def test_full_case_flow(client):
    created = client.post("/api/cases", json={"title": "New case"}).json()
    case_id, chat_id = created["case"]["id"], created["chats"][0]["id"]
    soon = (date.today() + timedelta(days=3)).strftime("%d.%m.%Y")
    order = pdf_bytes(f"ORDER\n02.11.2026\nState v. Ramesh Kumar\nPut up for arguments on {soon} at 11:00 AM.")
    fir = pdf_bytes("FIRST INFORMATION REPORT\nFIR No. 345/2026 P.S. Saket\nSections 318(4) BNS\nDate: 20.10.2026")
    response = client.post(f"/api/chats/{chat_id}/uploads", files=[
        ("uploads", ("order.pdf", order, "application/pdf")),
        ("uploads", ("fir.pdf", fir, "application/pdf")),
        ("uploads", ("virus.exe", b"MZ", "application/octet-stream")),
    ])
    assert response.status_code == 200
    assert [u["original_name"] for u in response.json()["uploads"]] == ["order.pdf", "fir.pdf"]
    assert response.json()["skipped"][0]["name"] == "virus.exe"

    def plan_ready():
        messages = client.get(f"/api/chats/{chat_id}").json()["messages"]
        plans = [m for m in messages if m["role"] == "plan" and m.get("plan") and m["plan"]["status"] == "draft"]
        return plans[0]["plan"] if plans else None

    plan = wait_for(plan_ready)
    assert plan["made_by"] == "rules"
    assert {d["doc_type"] for d in plan["data"]["documents"]} == {"Order", "FIR"}
    applied = client.post(f"/api/plans/{plan['id']}/apply").json()
    assert applied["plan"]["status"] == "applied"

    documents = client.get(f"/api/cases/{case_id}/documents").json()
    folders = {f["name"] for f in documents["folders"]}
    assert {"03 Orders & Judgments", "04 Police Records"} <= folders
    first = documents["folders"][0]["documents"][0]
    assert client.get(f"/api/documents/{first['id']}/file").status_code == 200
    assert client.get(f"/api/documents/{first['id']}").json()["page_texts"]

    agenda = client.get("/api/agenda").json()
    assert agenda["next_hearing"]["case_id"] == case_id and agenda["next_hearing"]["time"] == "11:00"
    assert client.get("/api/calendar.ics").text.count("BEGIN:VEVENT") == 1
    assert client.get(f"/api/cases/{case_id}/timeline").json()["items"]

    # Search-only answer in the case chat: the case's own pages come back as sources
    answer = client.post(f"/api/chats/{chat_id}/ask", json={"question": "When are the arguments listed?"})
    events = dict(sse_events(answer.text))
    assert events["done"]["mode"] == "search_only"
    assert events["sources"]["documents"], "the case documents should be searched"

    # Rename / move a document: the file on disk moves too
    moved = client.patch(f"/api/documents/{first['id']}", json={"folder": "10 Important", "filename": "Renamed"}).json()
    assert moved["folder"] == "10 Important" and moved["filename"] == "Renamed.pdf"
    assert client.get(f"/api/documents/{first['id']}/file").status_code == 200

    # Exports and deleting
    assert client.get(f"/api/cases/{case_id}/data-room.zip").content[:2] == b"PK"
    assert client.get(f"/api/cases/{case_id}/index.xlsx").content[:2] == b"PK"
    assert client.delete(f"/api/cases/{case_id}").json()["deleted"]
    assert client.get(f"/api/cases/{case_id}").status_code == 404


def test_general_chat_files_and_make_case(client):
    chat = client.post("/api/chats", json={"title": "New chat"}).json()
    note = pdf_bytes("LEGAL NOTICE\nDate: 28.10.2026\nUnder instructions from my client, refund Rs. 8,50,000 within 15 days.")
    client.post(f"/api/chats/{chat['id']}/uploads", files=[("uploads", ("notice.pdf", note, "application/pdf"))])
    wait_for(lambda: client.get(f"/api/chats/{chat['id']}").json()["documents"])
    made = client.post(f"/api/chats/{chat['id']}/make-case", json={"title": "Malhotra v. Kumar"}).json()
    case_chat = made["chats"][0]["id"]
    plan = wait_for(lambda: next((m["plan"] for m in client.get(f"/api/chats/{case_chat}").json()["messages"]
                                  if m["role"] == "plan" and m.get("plan") and m["plan"]["status"] == "draft"), None))
    assert plan["data"]["documents"][0]["doc_type"] == "Legal notice"


def test_calendar_and_notifications(client):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    event = client.post("/api/events", json={"title": "Arguments", "kind": "hearing", "date": tomorrow, "time": "10:30"}).json()
    assert client.post("/api/events", json={"title": "Bad", "date": "2026-02-31"}).status_code == 422
    assert client.patch(f"/api/events/{event['id']}", json={"status": "done"}).json()["status"] == "done"
    test = client.post("/api/notifications/test").json()
    assert test["unread"] == 1
    assert client.post("/api/notifications/read", json={}).json()["unread"] == 0
    assert client.delete(f"/api/events/{event['id']}").json()["deleted"]


def test_tools(client):
    assert len(client.get("/api/skills").json()) >= 10
    converted = client.get("/api/convert", params={"q": "IPC 420"}).json()
    assert converted["mappings"][0]["section_id"] == "BNS:318"
    assert client.get("/api/convert", params={"q": "498A"}).json()["mappings"][0]["section_id"] == "BNS:85"
    assert client.get("/api/laws/sections/BNS:103").json()["title"] == "Punishment for murder"
    assert client.get("/api/laws").json()["acts"] >= 100
    settings = client.put("/api/settings", json={"name": "Priya Sharma", "answer_language": "tamil"}).json()
    assert settings["display_name"] == "Adv. Priya Sharma" and settings["answer_language"] == "tamil"
    info = client.get("/api/cnr/DLND020047882015").json()
    assert info["valid"] and info["state"] == "Delhi" and info["year"] == 2015
    # A workflow with AI off: the Skill's own steps become the plan, waiting for approval
    chat = client.post("/api/chats", json={"title": "x"}).json()
    run = client.post("/api/runs", json={"chat_id": chat["id"], "skill": "rti-application", "inputs": {}}).json()["run"]
    run = wait_for(lambda: (r := client.get(f"/api/runs/{run['id']}").json())["status"] == "waiting" and r)
    assert len(run["plan"]["steps"]) >= 3


def test_a_task_can_start_straight_away(client):
    chat = client.post("/api/chats", json={"title": "x"}).json()
    started = client.post("/api/runs", json={"chat_id": chat["id"], "skill": "rti-application", "inputs": {},
                                             "review_steps": False}).json()
    run = started["run"]  # no planning call: the task's own steps, ready to run at once
    assert run["status"] == "waiting" and run["plan"]["made_by"] == "skill" and len(run["plan"]["steps"]) >= 3
    assert started["message"]["data"]["title"] == "Draft an RTI application"
    # AI is off in these tests, so running it says so plainly
    events = sse_events(client.post(f"/api/runs/{run['id']}/execute", json={"steps": run["plan"]["steps"]}).text)
    assert events[-1][0] == "error" and "a task needs" in events[-1][1]["message"]


def test_the_deep_button_reaches_the_model(client, monkeypatch):
    from app import llm

    seen = []

    async def fake_stream(system, prompt, tier=llm.MAIN):
        seen.append((tier, prompt))
        yield {"type": "delta", "text": "An answer."}
        yield {"type": "done", "text": "An answer.", "model": "fake", "cost": None, "stop_reason": "end_turn"}

    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "stream", fake_stream)
    chat = client.post("/api/chats", json={"title": "x"}).json()
    for deep in (False, True):
        events = sse_events(client.post(f"/api/chats/{chat['id']}/ask", json={"question": "Is cheating bailable?", "deep": deep}).text)
        assert events[-1][0] == "done" and events[-1][1]["message"]["data"]["deep"] is deep
    assert [tier for tier, _ in seen] == [llm.MAIN, llm.DEEP]
    assert isinstance(seen[0][1], list) and seen[0][1][-1].endswith("<question>\nIs cheating bailable?\n</question>")


def test_citation_check_covers_documents_sections_and_articles(workspace, law_library, engine):
    case = store.create_case({"title": "X"})
    document = store.add_document({"case_id": case["id"], "title": "Order", "doc_type": "Order"}, ["text"])
    sources = Sources(query="q", case_index=[document])
    from app.laws import LawSearch

    sources.acts = LawSearch(law_library, build_vectors=False).search("u/s 302 IPC", k=2)
    answer = (f"The court listed it [Doc {document['id']}, p. 1]. Murder [IPC s. 302] is now [BNS s. 103]. "
              "Also [BNS s. 318] and [Article 21]. But [Doc 999, p. 2] and [XYZ Act s. 5] and [Foo v. Bar (2020)]. "
              "Fill in [CLIENT NAME].")
    report = assistant.check_answer(answer, sources, law_library, engine.cases)
    status = {c["text"]: c["status"] for c in report["citations"]}
    assert status[f"Doc {document['id']}, p. 1"] == "verified"
    assert status["IPC s. 302"] == "verified" and status["BNS s. 103"] == "verified"
    assert status["BNS s. 318"] == "library" and status["Article 21"] == "library"
    assert status["Doc 999, p. 2"] == "unknown" and status["XYZ Act s. 5"] == "outside"
    assert status["Foo v. Bar (2020)"] == "unknown"
    assert "CLIENT NAME" not in status
    assert report["all_verified"] is False
