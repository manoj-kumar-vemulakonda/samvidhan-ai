"""
Tests for the lawyer's workspace: dates, safe file names, cases, reading files (incl. OCR),
the organisation plan (made without AI here), filing, case isolation, reminders and exports.
No AI is used: these run offline and cost nothing.
"""

from datetime import date, datetime, timedelta

import pymupdf
import pytest

from app import casesearch, config, dates, exports, extract, files, organizer, reminders, store


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
def test_indian_dates_and_hearing_phrases():
    text = ("ORDER dated 02.11.2026. Reply be filed within four weeks. Put up for arguments on 12th November, 2026 "
            "at 10:30 a.m. NDOH 15/12/26. Listed on Nov 20, 2026.")
    assert [str(d[0]) for d in dates.find_dates(text)] == ["2026-11-02", "2026-11-12", "2026-12-15", "2026-11-20"]
    hearings = dates.hearing_dates(text)
    assert [h["date"] for h in hearings] == ["2026-11-12", "2026-12-15", "2026-11-20"]
    assert hearings[0]["time"] == "10:30"
    deadlines = dates.deadline_dates(text, dates.first_date(text))
    assert deadlines[0]["date"] == "2026-11-30"  # four weeks from 02.11.2026
    assert (dates.find_time("at 2 PM"), dates.find_time("10.30 AM"), dates.find_time("14:05")) == ("14:00", "10:30", "14:05")
    assert dates.parse_date("31.02.2026") is None  # not a real date


# ---------------------------------------------------------------------------
# Files and cases
# ---------------------------------------------------------------------------
def test_safe_file_names():
    assert files.safe_name('Order: 12/11/2026 "final"?.pdf') == "Order 12 11 2026 final .pdf"
    assert files.safe_name("CON.pdf") == "_CON.pdf"
    assert files.safe_name("") == "Untitled"
    assert len(files.safe_name("x" * 300 + ".pdf")) <= 110 and files.safe_name("x" * 300 + ".pdf").endswith(".pdf")
    assert files.safe_folder("../../Windows") == "Windows"


def test_paths_cannot_leave_the_workspace(workspace):
    with pytest.raises(ValueError):
        files.resolve("../outside.txt")
    assert files.resolve("cases/x").is_relative_to(workspace)


def test_new_case_gets_a_folder_and_a_chat(workspace):
    case = store.create_case({"title": "Sharma v. Gupta", "cnr": "dlst-0100-1234-2026"})
    assert (workspace / "cases" / case["folder"] / "_originals").is_dir()
    assert case["cnr"] == "DLST010012342026" and case["cnr_info"]["state"] == "Delhi"
    assert len(store.list_chats(case["id"])) == 1
    assert store.describe_cnr("ABC")["valid"] is False


def test_settings_are_typed(workspace):
    s = store.update_settings({"name": "Priya Sharma", "deadline_days": ["7", 3, "x"], "remind_minutes_before": "30",
                               "morning_time": "25:99", "unknown": 1})
    assert s["deadline_days"] == [7, 3] and s["remind_minutes_before"] == 30
    assert s["morning_time"] == "08:00"  # an impossible time is ignored
    assert store.display_name() == "Adv. Priya Sharma"


# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------
def make_pdf(path, pages):
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=11)
    document.save(path)


def test_reads_pdf_word_text_and_scans(tmp_path):
    make_pdf(tmp_path / "a.pdf", ["FIRST INFORMATION REPORT\nFIR No. 12/2026", "Page two text here, long enough to read."])
    pages = extract.read_file(tmp_path / "a.pdf", "pdf")
    assert [p.method for p in pages] == ["text", "text"] and "FIR No. 12/2026" in pages[0].text

    import docx

    word = docx.Document()
    word.add_paragraph("Meeting note: client met on 29.10.2026")
    word.save(tmp_path / "b.docx")
    assert "29.10.2026" in extract.read_file(tmp_path / "b.docx", "docx")[0].text

    (tmp_path / "c.txt").write_text("plain text note", encoding="utf-8")
    assert extract.read_file(tmp_path / "c.txt", "text")[0].text == "plain text note"

    # A "scan": an image with no text layer -> OCR on this PC
    document = pymupdf.open()
    page = document.new_page(width=500, height=200)
    page.insert_text((20, 100), "NEXT DATE OF HEARING 15.12.2026", fontsize=20)
    page.get_pixmap(dpi=150).save(tmp_path / "scan.png")
    scanned = extract.read_file(tmp_path / "scan.png", "image")
    assert scanned[0].method == "ocr" and "15.12.2026" in scanned[0].text


# ---------------------------------------------------------------------------
# The organisation plan (keyword version) and filing
# ---------------------------------------------------------------------------
@pytest.fixture
def uploaded_case(workspace, tmp_path, monkeypatch):
    """A case with a 3-page bundle (2 documents) and an order, read and ready for a plan."""
    monkeypatch.setattr(config, "ANSWER_ENGINE", "off")
    case = store.create_case({"title": "New case"})
    chat = store.list_chats(case["id"])[0]
    originals = files.originals_dir(case, None)
    soon = (date.today() + timedelta(days=5)).strftime("%d.%m.%Y")
    make_pdf(originals / "bundle.pdf", [
        "BAIL APPLICATION\nIN THE COURT OF SESSIONS JUDGE\nState v. Ramesh Kumar\nDated: 27.10.2026",
        "Prayer: release the applicant on bail.",
        "VAKALATNAMA\nI appoint Ms. Priya Sharma, Advocate. Executed on 26.10.2026",
    ])
    make_pdf(originals / "order.pdf", [f"ORDER\n02.11.2026\nState v. Ramesh Kumar\nArguments heard. Put up for further arguments on {soon} at 10:30 AM. CNR DLST010012342026"])
    for name in ("bundle.pdf", "order.pdf"):
        path = originals / name
        store.add_upload(case["id"], chat["id"], "b1", name, files.relative(path), path.stat().st_size, "x" + name, "pdf")
    for upload in store.batch_uploads("b1"):
        pages = extract.read_file(files.resolve(upload["path"]), "pdf")
        store.set_upload_pages(upload["id"], [(p.page, p.text, p.method) for p in pages])
        store.update_upload(upload["id"], status="ready", pages=len(pages))
    return case, chat, soon


def test_plan_without_ai_and_filing(uploaded_case):
    case, chat, soon = uploaded_case
    plan = organizer.make_plan("b1")
    assert plan["made_by"] == "rules" and plan["status"] == "draft"
    data = plan["data"]
    order = next(d for d in data["documents"] if d["source_name"] == "order.pdf")
    assert order["doc_type"] == "Order" and order["folder"] == "03 Orders & Judgments" and order["date"] == "2026-11-02"
    hearing = data["events"][0]
    assert hearing["kind"] == "hearing" and hearing["time"] == "10:30" and hearing["include"]
    assert data["case"]["cnr"]["value"] == "DLST010012342026"
    assert data["case"]["title"]["value"] == "State v. Ramesh Kumar"

    # The lawyer splits the bundle into its two documents before approving
    bundle = next(d for d in data["documents"] if d["source_name"] == "bundle.pdf")
    edited = {**data, "documents": [
        {**bundle, "first_page": 1, "last_page": 2, "title": "Bail application", "doc_type": "Bail application",
         "folder": "02 Applications", "filename": "2026-10-27 Bail application"},
        {**bundle, "key": "dx", "first_page": 3, "last_page": 3, "title": "Vakalatnama", "doc_type": "Vakalatnama",
         "folder": "07 Client Documents", "filename": "2026-10-26 Vakalatnama"},
        order,
    ]}
    result = organizer.apply_plan(plan["id"], edited)
    assert result["plan"]["status"] == "applied"

    documents = store.list_documents(case_id=case["id"])
    by_title = {d["title"]: d for d in documents}
    assert by_title["Bail application"]["pages"] == 2 and by_title["Vakalatnama"]["pages"] == 1
    for d in documents:
        path = files.resolve(d["path"])
        assert path.exists() and path.parent.name == d["folder"]
    with pymupdf.open(files.resolve(by_title["Bail application"]["path"])) as split:
        assert split.page_count == 2
    case_now = store.get_case(case["id"])
    assert case_now["title"] == "State v. Ramesh Kumar" and case_now["cnr"] == "DLST010012342026"
    assert case_now["folder"].startswith("State v. Ramesh Kumar")       # the folder was renamed too
    assert files.resolve(by_title["Vakalatnama"]["path"]).exists()
    upcoming = store.upcoming_events(30, case["id"])
    assert upcoming and upcoming[0]["time"] == "10:30" and upcoming[0]["document_id"] == by_title[order["title"]]["id"]
    # the originals are kept
    assert (files.case_dir(case_now) / "_originals" / "bundle.pdf").exists()
    with pytest.raises(ValueError):
        organizer.apply_plan(plan["id"])  # a plan can only be used once


def test_case_search_never_mixes_cases(workspace):
    first = store.create_case({"title": "First"})
    second = store.create_case({"title": "Second"})
    store.add_document({"case_id": first["id"], "title": "Order A", "doc_type": "Order"}, ["The court granted bail to Ramesh."])
    store.add_document({"case_id": second["id"], "title": "Order B", "doc_type": "Order"}, ["The court rejected bail to Suresh."])
    hits = casesearch.search("bail court order", case_id=first["id"], use_vectors=False)
    assert hits and all(h.title == "Order A" for h in hits)
    hits = casesearch.search("Suresh bail", case_id=first["id"], use_vectors=False)
    assert all(h.title == "Order A" for h in hits)


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------
def test_reminders_follow_the_settings(workspace):
    settings = {**store.DEFAULT_SETTINGS, "name": "Priya Sharma", "morning_time": "08:00", "evening_time": "19:00",
                "remind_minutes_before": 60, "deadline_days": [3, 1]}
    hearing = {"id": 1, "case_id": None, "title": "Bail arguments", "kind": "hearing", "date": "2026-11-12", "time": "10:30",
               "location": "Saket", "status": "open", "case_title": "State v. Ramesh Kumar"}
    deadline = {"id": 2, "case_id": None, "title": "Reply due", "kind": "deadline", "date": "2026-11-15", "time": "",
                "location": "", "status": "open", "case_title": "Sharma v. Gupta"}
    keys = lambda now: {r["key"] for r in reminders.due_reminders(now, settings, [hearing, deadline])}
    assert keys(datetime(2026, 11, 11, 18, 59)) == set()
    assert "ev1:eve:2026-11-12" in keys(datetime(2026, 11, 11, 19, 5))
    morning = reminders.due_reminders(datetime(2026, 11, 12, 8, 1), settings, [hearing, deadline])
    assert any(r["kind"] == "agenda" and "Good morning, Adv. Priya Sharma" in r["title"] for r in morning)
    assert "ev1:soon:2026-11-1210:30" in keys(datetime(2026, 11, 12, 9, 31))
    assert "ev2:minus3:2026-11-15" in keys(datetime(2026, 11, 12, 8, 30))
    assert "ev2:overdue:2026-11-15" in keys(datetime(2026, 11, 16, 9, 0))


def test_reminders_are_sent_once(workspace):
    tomorrow = date.today() + timedelta(days=1)
    store.update_settings({"evening_time": "00:00", "morning_time": "00:00"})
    store.add_event({"title": "Arguments", "kind": "hearing", "date": tomorrow.isoformat()})
    first = reminders.check(datetime.combine(date.today(), datetime.min.time()).replace(hour=0, minute=5))
    again = reminders.check(datetime.combine(date.today(), datetime.min.time()).replace(hour=0, minute=6))
    assert first and not again
    assert store.unread_count() == len(first)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
def test_word_excel_and_calendar_exports(workspace):
    store.update_settings({"name": "Priya Sharma", "enrolment": "D/1234/2015"})
    content = exports.to_docx("Bail application", "# Facts\n- **Arrested** on 24.10.2026\n1. Ground one\n| a | b |")
    assert content[:2] == b"PK"
    event = store.add_event({"title": "Hearing, bail", "kind": "hearing", "date": "2026-11-12", "time": "10:30"})
    ics = exports.calendar_ics([event])
    assert "BEGIN:VEVENT" in ics and "DTSTART;TZID=Asia/Kolkata:20261112T103000" in ics and "Hearing\\, bail" in ics
    case = store.create_case({"title": "X"})
    document = store.add_document({"case_id": case["id"], "title": "Doc", "doc_type": "Order"}, ["text"])
    review = store.create_review(case["id"], "R", "custom", [{"key": "k", "label": "K", "question": "?"}], [document["id"]])
    store.set_cell(review["id"], document["id"], "k", answer="Yes", risk="high", status="done", page=1)
    assert exports.review_xlsx(store.get_review(review["id"]), {document["id"]: document})[:2] == b"PK"
    assert exports.data_room_zip(case)[:2] == b"PK"
