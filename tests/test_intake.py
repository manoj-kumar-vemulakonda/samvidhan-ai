"""
The home-screen drop box: a pile of files from several cases is sorted into cases, each with its filing plan,
and "Approve all" creates the cases, files the documents and fills the calendar. AI off (no cost), plus the
AI path with a fake answer.
"""

import time
from datetime import date, timedelta

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import config, intake, llm, store
from app.main import app

SOON = (date.today() + timedelta(days=5)).strftime("%d.%m.%Y")
FIR = "FIRST INFORMATION REPORT\nFIR No. 345/2026 P.S. Saket\nSections 318(4) BNS\nDate: 20.10.2026"
ORDER = (f"ORDER\n02.11.2026\nState v. Ramesh Kumar\nBail Application No. 1987/2026 in FIR No. 345/2026\n"
         f"Put up for arguments on {SOON} at 11:00 AM.")
BAIL = "IN THE COURT OF THE SESSIONS JUDGE, SAKET\nBail Application No. 1987/2026\nState v. Ramesh Kumar\nApplication for regular bail."
NOTICE = "COMPLAINT\nComplaint Case No. 55/2026\nPriya Traders v. Sunil Gupta\nCheque No. 001122 for Rs. 2,00,000 was dishonoured."
RECEIPT = "Received with thanks a sum of rupees five thousand only."


def pdf_bytes(text: str) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=11)
    return document.tobytes()


def wait_for(fn, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(0.3)
    raise AssertionError("timed out")


def fake_uploads(texts: dict[str, str]) -> list[dict]:
    """Uploads that are already read (no files needed for sorting)."""
    out = []
    for name, text in texts.items():
        upload = store.add_upload(None, None, "intake-test", name, f"_intake/x/{name}", 1, name, "pdf")
        store.set_upload_pages(upload["id"], [(1, text, "text")])
        out.append(store.update_upload(upload["id"], status="ready", pages=1))
    return out


@pytest.fixture
def client(workspace, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_ENGINE", "off")
    with TestClient(app) as test_client:
        yield test_client


def test_clues_find_numbers_cnr_and_parties():
    found = intake.clues(ORDER + "\nCNR: DLST010012342026")
    assert {"no:fir:345/2026", "no:bail:1987/2026", "cnr:DLST010012342026", "parties:state|ramesh kumar"} <= found["keys"]
    assert found["title"] == "State v. Ramesh Kumar"


def test_files_are_grouped_by_shared_numbers_and_matched_to_an_existing_case(workspace, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_ENGINE", "off")
    existing = store.create_case({"title": "Priya Traders v. Sunil Gupta", "case_number": "CC No. 55/2026"})
    uploads = fake_uploads({"fir.pdf": FIR, "order.pdf": ORDER, "bail.pdf": BAIL, "complaint.pdf": NOTICE,
                            "receipt.pdf": RECEIPT})
    result = intake.sort(uploads)
    names = {u["id"]: u["original_name"] for u in uploads}
    groups = {g["title"]: g for g in result["groups"]}
    # The FIR and the bail application share no number - the order cites both, so all three go together
    assert sorted(names[i] for i in groups["State v. Ramesh Kumar"]["upload_ids"]) == ["bail.pdf", "fir.pdf", "order.pdf"]
    assert groups["State v. Ramesh Kumar"]["case_id"] is None
    assert groups["Priya Traders v. Sunil Gupta"]["case_id"] == existing["id"]
    assert [names[i] for i in groups["Files I couldn't place"]["upload_ids"]] == ["receipt.pdf"]
    hearing = [e for e in groups["State v. Ramesh Kumar"]["plan"]["events"] if e["kind"] == "hearing"]
    assert hearing and hearing[0]["time"] == "11:00"


def test_drop_box_end_to_end(client):
    existing = client.post("/api/cases", json={"title": "Priya Traders v. Sunil Gupta", "case_number": "CC No. 55/2026"}).json()
    response = client.post("/api/intake", files=[
        ("uploads", ("fir.pdf", pdf_bytes(FIR), "application/pdf")),
        ("uploads", ("order.pdf", pdf_bytes(ORDER), "application/pdf")),
        ("uploads", ("bail.pdf", pdf_bytes(BAIL), "application/pdf")),
        ("uploads", ("complaint.pdf", pdf_bytes(NOTICE), "application/pdf")),
        ("uploads", ("receipt.pdf", pdf_bytes(RECEIPT), "application/pdf")),
        ("uploads", ("virus.exe", b"MZ", "application/octet-stream")),
    ])
    assert response.status_code == 200
    intake_id = response.json()["id"]
    assert response.json()["data"]["skipped"][0]["name"] == "virus.exe"
    ready = wait_for(lambda: (r := client.get(f"/api/intake/{intake_id}").json())["status"] == "ready" and r)
    groups = {g["title"]: g for g in ready["data"]["groups"]}
    assert len(groups) == 3
    state, loose = groups["State v. Ramesh Kumar"], groups["Files I couldn't place"]

    # The receipt belongs to the State case: move it there (instant, no AI)
    moved = client.post(f"/api/intake/{intake_id}/move", json={"upload_id": loose["upload_ids"][0], "to": state["key"]}).json()
    assert len(moved["data"]["groups"]) == 2
    assert len(next(g for g in moved["data"]["groups"] if g["key"] == state["key"])["upload_ids"]) == 4
    assert client.post(f"/api/intake/{intake_id}/move", json={"upload_id": 99999, "to": "new"}).status_code == 400

    applied = client.post(f"/api/intake/{intake_id}/apply", json={"groups": [{"key": state["key"], "title": "State v. Ramesh Kumar"}]}).json()
    assert applied["status"] == "applied"
    results = {r["title"]: r for r in applied["data"]["result"]}
    assert results["State v. Ramesh Kumar"]["new"] and results["State v. Ramesh Kumar"]["documents"] == 4
    assert results["State v. Ramesh Kumar"]["events"] == 1
    assert not results["Priya Traders v. Sunil Gupta"]["new"]
    assert results["Priya Traders v. Sunil Gupta"]["case_id"] == existing["case"]["id"]

    # The cases, their folders and the calendar
    new_id = results["State v. Ramesh Kumar"]["case_id"]
    filed = client.get(f"/api/cases/{new_id}/documents").json()["folders"]
    assert "04 Police Records" in {f["name"] for f in filed}
    assert sum(len(f["documents"]) for f in filed) == 4
    assert client.get("/api/agenda").json()["next_hearing"]["case_id"] == new_id
    assert len(client.get(f"/api/cases/{existing['case']['id']}/documents").json()["folders"]) == 1
    # Applying twice is refused; closing hides it from the home screen
    assert client.post(f"/api/intake/{intake_id}/apply").status_code == 400
    assert [i["id"] for i in client.get("/api/intake").json()] == [intake_id]
    client.post(f"/api/intake/{intake_id}/close")
    assert client.get("/api/intake").json() == []


def test_discard_keeps_nothing(client):
    intake_id = client.post("/api/intake", files=[("uploads", ("fir.pdf", pdf_bytes(FIR), "application/pdf"))]).json()["id"]
    wait_for(lambda: client.get(f"/api/intake/{intake_id}").json()["status"] == "ready")
    assert client.post(f"/api/intake/{intake_id}/discard").json()["status"] == "discarded"
    assert store.batch_uploads(intake.batch_name(intake_id)) == []
    assert client.get("/api/cases").json() == []


def test_ai_sorting_is_checked(workspace, monkeypatch):
    """The AI's answer is never trusted blindly: unknown case ids become new cases, every file ends up in
    exactly one case, and files the AI forgot are sorted by the rules."""
    uploads = fake_uploads({"fir.pdf": FIR, "order.pdf": ORDER, "complaint.pdf": NOTICE})
    fir, order, complaint = (u["id"] for u in uploads)

    def doc(file_id, doc_type):
        return {"file_id": file_id, "first_page": 1, "last_page": 1, "doc_type": doc_type, "title": doc_type,
                "date": "2026-11-02", "folder": "03 Orders & Judgments", "filename": doc_type, "summary": "", "parties": []}

    answer = {"notes": ["Sure about all of it."], "cases": [
        {"existing_case_id": 999, "title": "State v. Ramesh Kumar", "reason": "Same FIR No. 345/2026",
         "plan": {"summary": "", "case": {f: "" for f in ("title", "client", "opponent", "side", "court", "case_type",
                                                           "case_number", "cnr", "stage")},
                  "documents": [doc(fir, "FIR"), doc(order, "Order"), doc(fir, "FIR")], "events": [], "notes": []}},
    ]}
    monkeypatch.setattr(llm, "available", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: answer)
    result = intake.sort(uploads)
    assert result["made_by"] == "ai"
    first, second = result["groups"]
    assert first["case_id"] is None and sorted(first["upload_ids"]) == sorted([fir, order])
    assert second["upload_ids"] == [complaint] and second["made_by"] == "rules"
    assert any("did not place every file" in n for n in result["notes"])
