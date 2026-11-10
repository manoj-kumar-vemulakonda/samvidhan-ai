"""Shared test setup: make the project folder importable, use a throw-away workspace, share engines."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Tests never touch your real cases: every test run gets its own empty workspace folder,
# and the reminder loop is not started (the tests call reminders.check() themselves).
os.environ["WORKSPACE_DIR"] = tempfile.mkdtemp(prefix="samvidhan-test-")
os.environ["REMINDERS"] = "0"
# ...and never write to the law library's meaning index (the real app keeps it in index/)
os.environ["LAW_VECTORS"] = "0"
os.environ["INBOX"] = "0"          # the tests call rulebooks.scan_inbox() themselves


@pytest.fixture(scope="session")
def engine():
    from app.search import SearchEngine

    # The index is built by setup.bat; build it here too if it's missing.
    return SearchEngine(build_if_missing=True)


@pytest.fixture(scope="session")
def law_library():
    from app.laws import library

    return library()


@pytest.fixture(scope="session")
def law_search(law_library):
    from app.laws import LawSearch

    # Keyword + exact-reference search only: fast, and the same on every PC.
    return LawSearch(law_library, build_vectors=False)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """A fresh, empty workspace (database + folders) for one test."""
    from app import casesearch, config, db

    monkeypatch.setattr(config, "WORKSPACE_DIR", tmp_path / "workspace")
    db._ready.clear()
    casesearch.reset()
    db.init()
    yield config.WORKSPACE_DIR
    casesearch.reset()
