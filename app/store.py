"""
Reading and writing the workspace data: settings, cases, chats, messages, uploads,
documents, calendar events, notifications, review tables and workflow runs.

Every function returns plain dicts, ready to send to the web page as JSON.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app import db, files

# ---------------------------------------------------------------------------
# Settings (your profile and preferences)
# ---------------------------------------------------------------------------
DEFAULT_SETTINGS: dict = {
    "name": "",                 # e.g. "Priya Sharma"
    "title": "Adv.",            # shown before your name in greetings and drafts
    "enrolment": "",            # Bar Council enrolment number (printed on drafts)
    "firm": "",
    "address": "",
    "phone": "",
    "email": "",
    "courts": "",               # courts you usually appear in
    "practice_areas": "",       # e.g. "criminal, family"
    "style": "detailed",        # answers: "simple" or "detailed"
    "answer_language": "auto",  # "auto" = the language of the question
    "morning_time": "08:00",    # daily agenda + morning-of reminders
    "evening_time": "19:00",    # evening-before reminders for hearings
    "remind_evening_before": True,
    "remind_morning_of": True,
    "remind_minutes_before": 60,      # 0 = off (only for hearings with a time)
    "deadline_days": [7, 3, 1],       # countdown for deadlines and limitation dates
    "daily_agenda": True,
    "desktop_notifications": True,
    "keep_originals": True,
    "ocr_with_claude": True,          # let Claude read pages the local OCR can't
    "onboarded": False,
}


def get_settings() -> dict:
    stored = {r["key"]: db.loads(r["value"]) for r in db.rows("SELECT key, value FROM settings")}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in stored.items() if k in DEFAULT_SETTINGS}}


def update_settings(changes: dict) -> dict:
    current = get_settings()
    for key, value in changes.items():
        if key not in DEFAULT_SETTINGS:
            continue
        default = DEFAULT_SETTINGS[key]
        if isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int) and not isinstance(default, bool):
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        elif isinstance(default, list):
            value = sorted({int(v) for v in (value or []) if str(v).strip().lstrip("-").isdigit() and 0 < int(v) <= 365},
                           reverse=True)
        else:
            value = str(value or "").strip()[:500]
            if key.endswith("_time") and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
                continue
        current[key] = value
        db.run("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, db.dumps(value)))
    return current


def display_name(settings: dict | None = None) -> str:
    """'Adv. Priya Sharma' (or '' if no name was given yet)."""
    settings = settings or get_settings()
    name = settings.get("name", "").strip()
    return f"{settings.get('title', '').strip()} {name}".strip() if name else ""


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------
CASE_FIELDS = {"title", "client", "opponent", "side", "court", "case_type", "case_number", "cnr", "stage",
               "status", "notes"}


def _case_row(case: dict) -> dict:
    return case


def create_case(fields: dict) -> dict:
    title = str(fields.get("title") or "").strip() or "New case"
    stamp = db.now()
    values = {k: str(fields.get(k) or "").strip()[:300] for k in CASE_FIELDS - {"title", "status", "notes"}}
    values["cnr"] = normalise_cnr(values.get("cnr", ""))
    case_id = db.run(
        "INSERT INTO cases (title, client, opponent, side, court, case_type, case_number, cnr, stage, notes,"
        " created_at, updated_at) VALUES (:title, :client, :opponent, :side, :court, :case_type, :case_number,"
        " :cnr, :stage, :notes, :at, :at)",
        {**values, "title": title[:200], "notes": str(fields.get("notes") or "")[:5000], "at": stamp},
    )
    folder = files.case_folder_name(title, case_id)
    db.run("UPDATE cases SET folder = ? WHERE id = ?", (folder, case_id))
    case = get_case(case_id)
    files.case_dir(case)
    files.originals_dir(case, None)
    create_chat(case_id=case_id, title="Case chat")
    return get_case(case_id)


def get_case(case_id: int) -> dict | None:
    case = db.one("SELECT * FROM cases WHERE id = ?", (case_id,))
    if case:
        case["next_event"] = next_event(case_id)
        counts = db.one(
            "SELECT (SELECT COUNT(*) FROM documents WHERE case_id = :id) AS documents,"
            " (SELECT COUNT(*) FROM chats WHERE case_id = :id) AS chats", {"id": case_id})
        case.update(counts or {})
        case["cnr_info"] = describe_cnr(case["cnr"]) if case["cnr"] else None
    return case


def list_cases(status: str | None = None, query: str = "") -> list[dict]:
    sql = "SELECT * FROM cases"
    params: list = []
    where = []
    if status in ("active", "closed"):
        where.append("status = ?")
        params.append(status)
    if query.strip():
        where.append("(title LIKE ? OR client LIKE ? OR opponent LIKE ? OR case_number LIKE ? OR cnr LIKE ? OR court LIKE ?)")
        params += [f"%{query.strip()}%"] * 6
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY status = 'closed', updated_at DESC"
    cases = db.rows(sql, params)
    upcoming = {}
    for event in db.rows(
        "SELECT * FROM events WHERE status = 'open' AND date >= ? AND case_id IS NOT NULL ORDER BY date, time",
        (date.today().isoformat(),),
    ):
        upcoming.setdefault(event["case_id"], event)
    for case in cases:
        case["next_event"] = upcoming.get(case["id"])
    return cases


def update_case(case_id: int, fields: dict) -> dict | None:
    fields = dict(fields)
    if "cnr" in fields:
        fields["cnr"] = normalise_cnr(fields["cnr"])
    if "status" in fields and fields["status"] not in ("active", "closed"):
        fields.pop("status")
    clean = {k: str(v or "").strip()[:5000 if k == "notes" else 300] for k, v in fields.items() if k in CASE_FIELDS}
    if "title" in clean and not clean["title"]:
        clean.pop("title")
    db.update("cases", case_id, clean, CASE_FIELDS)
    return get_case(case_id)


def touch_case(case_id: int | None) -> None:
    if case_id:
        db.run("UPDATE cases SET updated_at = ? WHERE id = ?", (db.now(), case_id))


def delete_case(case_id: int) -> None:
    """Removes the case from the app. Its folder on disk is moved to workspace/_deleted (not erased)."""
    case = db.one("SELECT * FROM cases WHERE id = ?", (case_id,))
    if not case:
        return
    with db.connect() as connection:
        connection.execute("DELETE FROM page_search WHERE case_id = ?", (case_id,))
        connection.execute("DELETE FROM cases WHERE id = ?", (case_id,))
    folder = files.resolve(f"cases/{case['folder']}") if case.get("folder") else None
    if folder and folder.exists():
        bin_dir = files.resolve("_deleted")
        bin_dir.mkdir(parents=True, exist_ok=True)
        folder.rename(files.unique_path(bin_dir / folder.name))


# ---- CNR (eCourts case number) ---------------------------------------------
STATE_CODES = {
    "AN": "Andaman & Nicobar", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh", "AS": "Assam", "BR": "Bihar",
    "CH": "Chandigarh", "CG": "Chhattisgarh", "DN": "Dadra & Nagar Haveli and Daman & Diu", "DL": "Delhi",
    "GA": "Goa", "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh", "JK": "Jammu & Kashmir",
    "JH": "Jharkhand", "KA": "Karnataka", "KL": "Kerala", "LA": "Ladakh", "LD": "Lakshadweep",
    "MP": "Madhya Pradesh", "MH": "Maharashtra", "MN": "Manipur", "ML": "Meghalaya", "MZ": "Mizoram",
    "NL": "Nagaland", "OD": "Odisha", "OR": "Odisha", "PY": "Puducherry", "PB": "Punjab", "RJ": "Rajasthan",
    "SK": "Sikkim", "TN": "Tamil Nadu", "TS": "Telangana", "TG": "Telangana", "TR": "Tripura",
    "UP": "Uttar Pradesh", "UK": "Uttarakhand", "UA": "Uttarakhand", "WB": "West Bengal",
}
ECOURTS_URL = "https://services.ecourts.gov.in/ecourtindia_v6/?p=cnr_status/searchByCNR"
HIGH_COURT_URL = "https://hcservices.ecourts.gov.in/hcservices/main.php"


def normalise_cnr(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()[:16]


def describe_cnr(cnr: str) -> dict:
    """
    Explain a CNR: 4 letters (state + district/establishment code), then 12 digits
    (2 = court/establishment number, 6 = case number, 4 = year). E.g. DLND020047882015.
    """
    cnr = normalise_cnr(cnr)
    valid = bool(re.fullmatch(r"[A-Z]{4}\d{12}", cnr))
    info = {"cnr": cnr, "valid": valid, "ecourts_url": ECOURTS_URL, "high_court_url": HIGH_COURT_URL,
            "sms": f"ECOURTS {cnr}", "sms_number": "9766899899"}
    if valid:
        year = int(cnr[12:16])
        info.update({
            "state_code": cnr[:2], "state": STATE_CODES.get(cnr[:2], "unknown state code"),
            "district_code": cnr[2:4], "establishment": cnr[4:6], "case_serial": cnr[6:12].lstrip("0") or "0",
            "year": year, "year_ok": 1950 <= year <= date.today().year,
        })
    else:
        info["problem"] = "A CNR has 16 characters: 4 letters then 12 digits, e.g. DLND020047882015."
    return info


# ---------------------------------------------------------------------------
# Chats and messages
# ---------------------------------------------------------------------------
def create_chat(case_id: int | None = None, title: str = "New chat") -> dict:
    stamp = db.now()
    chat_id = db.run("INSERT INTO chats (case_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                     (case_id, (title or "New chat")[:120], stamp, stamp))
    return get_chat(chat_id)


def get_chat(chat_id: int) -> dict | None:
    return db.one("SELECT * FROM chats WHERE id = ?", (chat_id,))


def list_chats(case_id: int | None = None, general: bool = False, limit: int = 100) -> list[dict]:
    if general:
        return db.rows("SELECT * FROM chats WHERE case_id IS NULL ORDER BY updated_at DESC LIMIT ?", (limit,))
    return db.rows("SELECT * FROM chats WHERE case_id = ? ORDER BY created_at", (case_id,))


def rename_chat(chat_id: int, title: str) -> dict | None:
    db.update("chats", chat_id, {"title": (title or "").strip()[:120] or "New chat"}, {"title"})
    return get_chat(chat_id)


def delete_chat(chat_id: int) -> None:
    with db.connect() as connection:
        connection.execute("DELETE FROM page_search WHERE chat_id = ?", (chat_id,))
        connection.execute("DELETE FROM chats WHERE id = ?", (chat_id,))


def add_message(chat_id: int, role: str, content: str = "", data: dict | None = None) -> dict:
    stamp = db.now()
    message_id = db.run("INSERT INTO messages (chat_id, role, content, data, created_at) VALUES (?, ?, ?, ?, ?)",
                        (chat_id, role, content, db.dumps(data or {}), stamp))
    db.run("UPDATE chats SET updated_at = ? WHERE id = ?", (stamp, chat_id))
    chat = get_chat(chat_id)
    if chat:
        touch_case(chat["case_id"])
    return get_message(message_id)


def get_message(message_id: int) -> dict | None:
    message = db.one("SELECT * FROM messages WHERE id = ?", (message_id,))
    if message:
        message["data"] = db.loads(message["data"], {})
    return message


def update_message(message_id: int, content: str | None = None, data: dict | None = None, merge: bool = True) -> dict | None:
    message = get_message(message_id)
    if not message:
        return None
    new_data = {**message["data"], **(data or {})} if merge else (data or {})
    db.run("UPDATE messages SET content = ?, data = ? WHERE id = ?",
           (message["content"] if content is None else content, db.dumps(new_data), message_id))
    return get_message(message_id)


def list_messages(chat_id: int) -> list[dict]:
    messages = db.rows("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))
    for message in messages:
        message["data"] = db.loads(message["data"], {})
    return messages


def chat_history(chat_id: int, before_id: int | None = None, turns: int = 3) -> list[dict]:
    """The last few question/answer pairs, for follow-up questions."""
    pairs: list[dict] = []
    question = None
    for message in list_messages(chat_id):
        if before_id and message["id"] >= before_id:
            break
        if message["role"] == "user":
            question = message["content"]
        elif message["role"] == "assistant" and question is not None:
            pairs.append({"question": question, "answer": message["content"][:4000]})
            question = None
    return pairs[-turns:]


# ---------------------------------------------------------------------------
# Uploads and their page texts
# ---------------------------------------------------------------------------
UPLOAD_FIELDS = {"status", "progress", "error", "pages", "ocr_pages", "kind"}


def add_upload(case_id: int | None, chat_id: int | None, batch: str, original_name: str, path: str,
               size: int, sha256: str, kind: str) -> dict:
    upload_id = db.run(
        "INSERT INTO uploads (case_id, chat_id, batch, original_name, path, size, sha256, kind, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (case_id, chat_id, batch, original_name, path, size, sha256, kind, db.now()))
    return get_upload(upload_id)


def get_upload(upload_id: int) -> dict | None:
    return db.one("SELECT * FROM uploads WHERE id = ?", (upload_id,))


def update_upload(upload_id: int, **fields) -> dict | None:
    db.update("uploads", upload_id, fields, UPLOAD_FIELDS, touch=False)
    return get_upload(upload_id)


def batch_uploads(batch: str) -> list[dict]:
    return db.rows("SELECT * FROM uploads WHERE batch = ? ORDER BY id", (batch,))


def pending_uploads(case_id: int) -> list[dict]:
    """Uploads of a case that are not filed yet (being read, waiting for a plan, or failed)."""
    return db.rows("SELECT * FROM uploads WHERE case_id = ? AND status != 'filed' ORDER BY id", (case_id,))


def set_upload_pages(upload_id: int, pages: list[tuple[int, str, str]]) -> None:
    with db.connect() as connection:
        connection.execute("DELETE FROM upload_pages WHERE upload_id = ?", (upload_id,))
        connection.executemany("INSERT INTO upload_pages (upload_id, page, text, method) VALUES (?, ?, ?, ?)",
                               [(upload_id, p, t, m) for p, t, m in pages])


def upload_pages(upload_id: int) -> list[dict]:
    return db.rows("SELECT page, text, method FROM upload_pages WHERE upload_id = ? ORDER BY page", (upload_id,))


def find_duplicate_upload(sha256: str, case_id: int | None, exclude_batch: str) -> dict | None:
    if not case_id:
        return None
    return db.one("SELECT * FROM uploads WHERE sha256 = ? AND case_id = ? AND batch != ? ORDER BY id LIMIT 1",
                  (sha256, case_id, exclude_batch))


# ---------------------------------------------------------------------------
# Organisation plans
# ---------------------------------------------------------------------------
def create_plan(case_id: int | None, chat_id: int | None, batch: str, status: str = "making") -> dict:
    stamp = db.now()
    plan_id = db.run("INSERT INTO plans (case_id, chat_id, batch, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (case_id, chat_id, batch, status, stamp, stamp))
    return get_plan(plan_id)


def get_plan(plan_id: int) -> dict | None:
    plan = db.one("SELECT * FROM plans WHERE id = ?", (plan_id,))
    if plan:
        plan["data"] = db.loads(plan["data"], {})
    return plan


def update_plan(plan_id: int, status: str | None = None, data: dict | None = None, made_by: str | None = None) -> dict | None:
    fields: dict = {}
    if status:
        fields["status"] = status
    if data is not None:
        fields["data"] = db.dumps(data)
    if made_by:
        fields["made_by"] = made_by
    db.update("plans", plan_id, fields, {"status", "data", "made_by"})
    return get_plan(plan_id)


# ---------------------------------------------------------------------------
# Documents (organised files) and their pages
# ---------------------------------------------------------------------------
DOCUMENT_FIELDS = {"title", "doc_type", "folder", "filename", "path", "doc_date", "summary"}


def add_document(fields: dict, pages: list[str]) -> dict:
    """Store an organised document with the text of each of its pages (also for keyword search)."""
    stamp = db.now()
    with db.connect() as connection:
        cursor = connection.execute(
            "INSERT INTO documents (case_id, chat_id, upload_id, title, doc_type, folder, filename, path, doc_date,"
            " page_from, page_to, pages, summary, parties, created_at) VALUES (:case_id, :chat_id, :upload_id, :title,"
            " :doc_type, :folder, :filename, :path, :doc_date, :page_from, :page_to, :pages, :summary, :parties, :at)",
            {
                "case_id": fields.get("case_id"), "chat_id": fields.get("chat_id"), "upload_id": fields.get("upload_id"),
                "title": fields.get("title") or "Document", "doc_type": fields.get("doc_type") or "",
                "folder": fields.get("folder") or "", "filename": fields.get("filename") or "",
                "path": fields.get("path") or "", "doc_date": fields.get("doc_date") or "",
                "page_from": fields.get("page_from") or 1, "page_to": fields.get("page_to") or len(pages) or 1,
                "pages": max(1, len(pages)), "summary": fields.get("summary") or "",
                "parties": db.dumps(fields.get("parties") or []), "at": stamp,
            },
        )
        document_id = int(cursor.lastrowid)
        connection.executemany("INSERT INTO document_pages (document_id, page, text) VALUES (?, ?, ?)",
                               [(document_id, i, text) for i, text in enumerate(pages, start=1)])
        connection.executemany(
            "INSERT INTO page_search (text, document_id, page, case_id, chat_id) VALUES (?, ?, ?, ?, ?)",
            [(text, document_id, i, fields.get("case_id"), fields.get("chat_id"))
             for i, text in enumerate(pages, start=1) if text.strip()])
    touch_case(fields.get("case_id"))
    return get_document(document_id)


def get_document(document_id: int) -> dict | None:
    document = db.one("SELECT * FROM documents WHERE id = ?", (document_id,))
    if document:
        document["parties"] = db.loads(document["parties"], [])
    return document


def list_documents(case_id: int | None = None, chat_id: int | None = None) -> list[dict]:
    if case_id is not None:
        documents = db.rows("SELECT * FROM documents WHERE case_id = ? ORDER BY folder, doc_date, id", (case_id,))
    else:
        documents = db.rows("SELECT * FROM documents WHERE chat_id = ? AND case_id IS NULL ORDER BY id", (chat_id,))
    for document in documents:
        document["parties"] = db.loads(document["parties"], [])
    return documents


def document_pages(document_id: int) -> list[dict]:
    return db.rows("SELECT page, text FROM document_pages WHERE document_id = ? ORDER BY page", (document_id,))


def document_text(document_id: int, limit: int | None = None) -> str:
    parts = []
    for page in document_pages(document_id):
        parts.append(f"[page {page['page']}]\n{page['text'].strip()}")
    text = "\n\n".join(parts)
    return text[:limit] if limit else text


def update_document(document_id: int, fields: dict) -> dict | None:
    db.update("documents", document_id, fields, DOCUMENT_FIELDS, touch=False)
    return get_document(document_id)


def delete_document(document_id: int) -> None:
    with db.connect() as connection:
        connection.execute("DELETE FROM page_search WHERE document_id = ?", (document_id,))
        connection.execute("DELETE FROM documents WHERE id = ?", (document_id,))


def move_chat_documents_to_case(chat_id: int, case_id: int) -> None:
    with db.connect() as connection:
        connection.execute("UPDATE documents SET case_id = ?, chat_id = NULL WHERE chat_id = ? AND case_id IS NULL",
                           (case_id, chat_id))
        connection.execute("UPDATE page_search SET case_id = ?, chat_id = NULL WHERE chat_id = ? AND case_id IS NULL",
                           (case_id, chat_id))


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
EVENT_KINDS = ("hearing", "deadline", "limitation", "meeting", "reminder", "other")
EVENT_FIELDS = {"title", "kind", "date", "time", "location", "notes", "status", "case_id"}


def clean_event_fields(fields: dict) -> dict:
    clean: dict = {}
    if "title" in fields:
        clean["title"] = str(fields["title"] or "").strip()[:200] or "Event"
    if "kind" in fields:
        clean["kind"] = fields["kind"] if fields["kind"] in EVENT_KINDS else "other"
    if "date" in fields:
        value = str(fields["date"] or "")[:10]
        datetime.strptime(value, "%Y-%m-%d")  # raises ValueError if not a real date
        clean["date"] = value
    if "time" in fields:
        value = str(fields["time"] or "").strip()[:5]
        clean["time"] = value if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value) else ""
    for key in ("location", "notes"):
        if key in fields:
            clean[key] = str(fields[key] or "").strip()[:1000]
    if "status" in fields and fields["status"] in ("open", "done", "cancelled"):
        clean["status"] = fields["status"]
    if "case_id" in fields:
        clean["case_id"] = int(fields["case_id"]) if fields["case_id"] else None
    return clean


def add_event(fields: dict) -> dict:
    clean = clean_event_fields({"kind": "hearing", **fields})
    if "date" not in clean:
        raise ValueError("An event needs a date.")
    stamp = db.now()
    event_id = db.run(
        "INSERT INTO events (case_id, title, kind, date, time, location, notes, document_id, page, quote, origin,"
        " created_at, updated_at) VALUES (:case_id, :title, :kind, :date, :time, :location, :notes, :document_id,"
        " :page, :quote, :origin, :at, :at)",
        {
            "case_id": clean.get("case_id"), "title": clean.get("title", "Event"), "kind": clean.get("kind", "other"),
            "date": clean["date"], "time": clean.get("time", ""), "location": clean.get("location", ""),
            "notes": clean.get("notes", ""), "document_id": fields.get("document_id"), "page": fields.get("page"),
            "quote": str(fields.get("quote") or "")[:500], "origin": fields.get("origin") or "manual", "at": stamp,
        },
    )
    return get_event(event_id)


def get_event(event_id: int) -> dict | None:
    return db.one(
        "SELECT e.*, c.title AS case_title, c.court AS case_court, d.title AS document_title"
        " FROM events e LEFT JOIN cases c ON c.id = e.case_id LEFT JOIN documents d ON d.id = e.document_id"
        " WHERE e.id = ?", (event_id,))


def update_event(event_id: int, fields: dict) -> dict | None:
    db.update("events", event_id, clean_event_fields(fields), EVENT_FIELDS)
    return get_event(event_id)


def delete_event(event_id: int) -> None:
    db.run("DELETE FROM events WHERE id = ?", (event_id,))


def list_events(start: str, end: str, case_id: int | None = None, include_done: bool = True) -> list[dict]:
    sql = ("SELECT e.*, c.title AS case_title, c.court AS case_court, d.title AS document_title FROM events e"
           " LEFT JOIN cases c ON c.id = e.case_id LEFT JOIN documents d ON d.id = e.document_id"
           " WHERE e.date >= ? AND e.date <= ?")
    params: list = [start, end]
    if case_id is not None:
        sql += " AND e.case_id = ?"
        params.append(case_id)
    if not include_done:
        sql += " AND e.status = 'open'"
    sql += " ORDER BY e.date, CASE WHEN e.time = '' THEN '99:99' ELSE e.time END, e.id"
    return db.rows(sql, params)


def upcoming_events(days: int = 14, case_id: int | None = None) -> list[dict]:
    today = date.today()
    return [e for e in list_events(today.isoformat(), (today + timedelta(days=days)).isoformat(), case_id)
            if e["status"] == "open"]


def next_event(case_id: int | None = None, kinds: tuple = ()) -> dict | None:
    for event in upcoming_events(400, case_id):
        if not kinds or event["kind"] in kinds:
            return event
    return None


def overdue_events() -> list[dict]:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return [e for e in list_events("2000-01-01", yesterday) if e["status"] == "open" and e["kind"] in ("deadline", "limitation")]


def event_exists(case_id: int | None, day: str, kind: str, title: str) -> bool:
    return db.one("SELECT id FROM events WHERE case_id IS ? AND date = ? AND kind = ? AND lower(title) = lower(?)",
                  (case_id, day, kind, title)) is not None


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def add_notification(key: str, title: str, body: str = "", kind: str = "reminder",
                     case_id: int | None = None, event_id: int | None = None) -> dict | None:
    """Adds a notification unless one with the same key exists (then returns None)."""
    with db.connect() as connection:
        cursor = connection.execute(
            "INSERT OR IGNORE INTO notifications (key, kind, title, body, case_id, event_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)", (key, kind, title[:200], body[:2000], case_id, event_id, db.now()))
        if not cursor.rowcount:
            return None
        notification_id = cursor.lastrowid
    return db.one("SELECT * FROM notifications WHERE id = ?", (notification_id,))


def list_notifications(limit: int = 50) -> list[dict]:
    return db.rows("SELECT n.*, c.title AS case_title FROM notifications n LEFT JOIN cases c ON c.id = n.case_id"
                   " ORDER BY n.id DESC LIMIT ?", (limit,))


def unread_count() -> int:
    found = db.one("SELECT COUNT(*) AS n FROM notifications WHERE read_at IS NULL")
    return int(found["n"]) if found else 0


def mark_read(notification_ids: list[int] | None = None) -> None:
    if notification_ids:
        marks = ",".join("?" for _ in notification_ids)
        db.run(f"UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND id IN ({marks})",
               [db.now(), *notification_ids])
    else:
        db.run("UPDATE notifications SET read_at = ? WHERE read_at IS NULL", (db.now(),))


# ---------------------------------------------------------------------------
# Review tables
# ---------------------------------------------------------------------------
def create_review(case_id: int, title: str, template: str, columns: list[dict], document_ids: list[int]) -> dict:
    stamp = db.now()
    review_id = db.run(
        "INSERT INTO reviews (case_id, title, template, columns, document_ids, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case_id, title[:200], template, db.dumps(columns), db.dumps(document_ids), stamp, stamp))
    with db.connect() as connection:
        connection.executemany(
            "INSERT OR IGNORE INTO review_cells (review_id, document_id, column_key) VALUES (?, ?, ?)",
            [(review_id, d, c["key"]) for d in document_ids for c in columns])
    return get_review(review_id)


def get_review(review_id: int) -> dict | None:
    review = db.one("SELECT * FROM reviews WHERE id = ?", (review_id,))
    if not review:
        return None
    review["columns"] = db.loads(review["columns"], [])
    review["document_ids"] = db.loads(review["document_ids"], [])
    review["cells"] = db.rows("SELECT * FROM review_cells WHERE review_id = ?", (review_id,))
    return review


def list_reviews(case_id: int) -> list[dict]:
    reviews = db.rows("SELECT id, case_id, title, template, status, created_at, updated_at FROM reviews"
                      " WHERE case_id = ? ORDER BY id DESC", (case_id,))
    return reviews


def update_review(review_id: int, **fields) -> None:
    db.update("reviews", review_id, fields, {"status", "report", "title"})


def set_cell(review_id: int, document_id: int, column_key: str, **fields) -> None:
    allowed = {"answer", "quote", "page", "risk", "status"}
    changes = {k: v for k, v in fields.items() if k in allowed}
    if not changes:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in changes)
    db.run(f"UPDATE review_cells SET {assignments} WHERE review_id = :r AND document_id = :d AND column_key = :c",
           {**changes, "r": review_id, "d": document_id, "c": column_key})


def delete_review(review_id: int) -> None:
    db.run("DELETE FROM reviews WHERE id = ?", (review_id,))


# ---------------------------------------------------------------------------
# Workflow runs
# ---------------------------------------------------------------------------
def create_run(chat_id: int, case_id: int | None, skill: str, inputs: dict) -> dict:
    stamp = db.now()
    run_id = db.run("INSERT INTO runs (chat_id, case_id, skill, inputs, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (chat_id, case_id, skill, db.dumps(inputs), stamp, stamp))
    return get_run(run_id)


def get_run(run_id: int) -> dict | None:
    found = db.one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if found:
        found["inputs"] = db.loads(found["inputs"], {})
        found["plan"] = db.loads(found["plan"], {})
    return found


def update_run(run_id: int, **fields) -> dict | None:
    if "plan" in fields:
        fields["plan"] = db.dumps(fields["plan"])
    if "inputs" in fields:
        fields["inputs"] = db.dumps(fields["inputs"])
    db.update("runs", run_id, fields, {"status", "plan", "output", "message_id", "inputs"})
    return get_run(run_id)
