"""
The workspace database: ONE file, workspace/samvidhan.db (SQLite, built into Python - no
server to install). It holds your cases, chats, documents, page texts, calendar,
reminders, review tables and settings. Your actual files live next to it in
workspace/cases/... (see app/files.py).

Small helpers keep the rest of the code short:
    rows("SELECT ...", params) -> list of dicts
    one("SELECT ...", params)  -> a dict or None
    run("INSERT ...", params)  -> the new row's id
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app import config

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- One row per case (a "matter"): its details and its folder on disk
CREATE TABLE IF NOT EXISTS cases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    client      TEXT NOT NULL DEFAULT '',
    opponent    TEXT NOT NULL DEFAULT '',
    side        TEXT NOT NULL DEFAULT '',   -- whom you act for: Petitioner, Accused, Complainant...
    court       TEXT NOT NULL DEFAULT '',
    case_type   TEXT NOT NULL DEFAULT '',   -- Criminal, Civil, Family, ...
    case_number TEXT NOT NULL DEFAULT '',
    cnr         TEXT NOT NULL DEFAULT '',   -- the eCourts case number (16 characters)
    stage       TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',  -- active / closed
    folder      TEXT NOT NULL DEFAULT '',   -- folder name inside workspace/cases
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Chats: case_id is empty for general chats (not about any case)
CREATE TABLE IF NOT EXISTS chats (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER REFERENCES cases(id) ON DELETE CASCADE,
    title      TEXT NOT NULL DEFAULT 'New chat',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chats_by_case ON chats(case_id);

CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role       TEXT NOT NULL,                -- user / assistant / plan / notice / workflow / files
    content    TEXT NOT NULL DEFAULT '',
    data       TEXT NOT NULL DEFAULT '{}',   -- JSON: sources, citations, plan id, files...
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_by_chat ON messages(chat_id);

-- Files as they were uploaded (kept untouched in _originals), and the text of each page
CREATE TABLE IF NOT EXISTS uploads (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       INTEGER REFERENCES cases(id) ON DELETE CASCADE,
    chat_id       INTEGER REFERENCES chats(id) ON DELETE SET NULL,
    batch         TEXT NOT NULL,             -- files uploaded together share a batch
    original_name TEXT NOT NULL,
    path          TEXT NOT NULL,             -- relative to the workspace folder
    size          INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT '',  -- pdf / docx / image / text
    pages         INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'received',  -- received / reading / ready / failed / filed
    progress      TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    ocr_pages     INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS uploads_by_batch ON uploads(batch);
CREATE TABLE IF NOT EXISTS upload_pages (
    upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    page      INTEGER NOT NULL,              -- 1 = first page
    text      TEXT NOT NULL DEFAULT '',
    method    TEXT NOT NULL DEFAULT 'text',  -- text / ocr / vision / docx / plain
    PRIMARY KEY (upload_id, page)
);

-- The AI's organisation plans (you approve them before anything moves)
CREATE TABLE IF NOT EXISTS plans (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER REFERENCES cases(id) ON DELETE CASCADE,
    chat_id    INTEGER REFERENCES chats(id) ON DELETE SET NULL,
    batch      TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'draft',  -- making / draft / applied / discarded / failed
    made_by    TEXT NOT NULL DEFAULT '',       -- ai / rules
    data       TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Organised documents (one upload can become several documents when a PDF is split)
CREATE TABLE IF NOT EXISTS documents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id    INTEGER REFERENCES cases(id) ON DELETE CASCADE,
    chat_id    INTEGER REFERENCES chats(id) ON DELETE CASCADE,   -- files in a general chat
    upload_id  INTEGER REFERENCES uploads(id) ON DELETE SET NULL,
    title      TEXT NOT NULL,
    doc_type   TEXT NOT NULL DEFAULT '',
    folder     TEXT NOT NULL DEFAULT '',
    filename   TEXT NOT NULL DEFAULT '',
    path       TEXT NOT NULL DEFAULT '',     -- relative to the workspace folder
    doc_date   TEXT NOT NULL DEFAULT '',     -- YYYY-MM-DD
    page_from  INTEGER NOT NULL DEFAULT 1,   -- pages of the uploaded file it came from
    page_to    INTEGER NOT NULL DEFAULT 1,
    pages      INTEGER NOT NULL DEFAULT 1,
    summary    TEXT NOT NULL DEFAULT '',
    parties    TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS documents_by_case ON documents(case_id);
CREATE TABLE IF NOT EXISTS document_pages (
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page        INTEGER NOT NULL,            -- 1 = first page of the document
    text        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (document_id, page)
);
-- Keyword search over every page (SQLite's built-in full-text search, BM25 ranking)
CREATE VIRTUAL TABLE IF NOT EXISTS page_search USING fts5(
    text, document_id UNINDEXED, page UNINDEXED, case_id UNINDEXED, chat_id UNINDEXED,
    tokenize = 'unicode61 remove_diacritics 2'
);

-- The calendar
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     INTEGER REFERENCES cases(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'hearing',  -- hearing / deadline / limitation / meeting / reminder / other
    date        TEXT NOT NULL,                    -- YYYY-MM-DD
    time        TEXT NOT NULL DEFAULT '',         -- HH:MM, empty = all day
    location    TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    document_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    page        INTEGER,
    quote       TEXT NOT NULL DEFAULT '',         -- the words in the document the date came from
    origin      TEXT NOT NULL DEFAULT 'manual',   -- manual / documents
    status      TEXT NOT NULL DEFAULT 'open',     -- open / done / cancelled
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_date ON events(date);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    key        TEXT NOT NULL UNIQUE,              -- stops the same reminder being sent twice
    kind       TEXT NOT NULL DEFAULT 'reminder',  -- reminder / agenda / overdue / info
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    case_id    INTEGER REFERENCES cases(id) ON DELETE CASCADE,
    event_id   INTEGER REFERENCES events(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    read_at    TEXT
);

-- Review tables / due diligence
CREATE TABLE IF NOT EXISTS reviews (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    template     TEXT NOT NULL DEFAULT 'custom',
    columns      TEXT NOT NULL DEFAULT '[]',     -- [{key, label, question}]
    document_ids TEXT NOT NULL DEFAULT '[]',
    status       TEXT NOT NULL DEFAULT 'draft',  -- draft / running / done / failed
    report       TEXT NOT NULL DEFAULT '',       -- the final due-diligence report
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_cells (
    review_id   INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    column_key  TEXT NOT NULL,
    answer      TEXT NOT NULL DEFAULT '',
    quote       TEXT NOT NULL DEFAULT '',
    page        INTEGER,
    risk        TEXT NOT NULL DEFAULT '',        -- high / medium / low / none
    status      TEXT NOT NULL DEFAULT 'pending', -- pending / running / done / error
    PRIMARY KEY (review_id, document_id, column_key)
);

-- Workflow runs (a Skill: plan -> your approval -> result)
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    case_id    INTEGER REFERENCES cases(id) ON DELETE CASCADE,
    skill      TEXT NOT NULL,
    inputs     TEXT NOT NULL DEFAULT '{}',
    plan       TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'planning', -- planning / waiting / running / done / failed / cancelled
    output     TEXT NOT NULL DEFAULT '',
    message_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_lock = threading.RLock()
_ready: set[str] = set()


def db_file() -> Path:
    return config.WORKSPACE_DIR / "samvidhan.db"


def now() -> str:
    """The current local time as text, e.g. 2026-11-08T14:05:00."""
    return datetime.now().replace(microsecond=0).isoformat()


def init() -> None:
    """Create the workspace folder and the tables (safe to call many times)."""
    path = db_file()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        finally:
            connection.close()
        _ready.add(str(path))


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A connection inside a transaction: committed if all goes well, undone on an error."""
    path = db_file()
    if str(path) not in _ready:
        init()
    with _lock:  # one writer at a time: simple and safe for a single-user app
        connection = sqlite3.connect(path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def rows(sql: str, params: tuple | list | dict = ()) -> list[dict]:
    with connect() as connection:
        return [dict(r) for r in connection.execute(sql, params).fetchall()]


def one(sql: str, params: tuple | list | dict = ()) -> dict | None:
    with connect() as connection:
        found = connection.execute(sql, params).fetchone()
        return dict(found) if found else None


def run(sql: str, params: tuple | list | dict = ()) -> int:
    """Run one INSERT / UPDATE / DELETE; returns the new row id (for INSERT)."""
    with connect() as connection:
        cursor = connection.execute(sql, params)
        return int(cursor.lastrowid or 0)


def run_many(sql: str, items: list) -> None:
    with connect() as connection:
        connection.executemany(sql, items)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return default


def update(table: str, row_id: int, fields: dict, allowed: set[str], touch: bool = True) -> None:
    """UPDATE table SET <allowed fields> WHERE id = row_id."""
    changes = {k: v for k, v in fields.items() if k in allowed}
    if touch:
        changes["updated_at"] = now()
    if not changes:
        return
    assignments = ", ".join(f"{name} = :{name}" for name in changes)
    run(f"UPDATE {table} SET {assignments} WHERE id = :row_id", {**changes, "row_id": row_id})
