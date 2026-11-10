"""
Tests for the web routes. The Claude API is replaced by a fake, so these tests
run offline and never spend money.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import answer, config
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:  # runs the start-up code (loads the search engine)
        yield test_client


def read_events(response) -> list[tuple[str, dict]]:
    """Split a Server-Sent Events response into (event name, data) pairs."""
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


# ---- a fake Claude client ---------------------------------------------------
class FakeStream:
    def __init__(self, pieces, stop_reason="end_turn"):
        self.pieces = pieces
        self.stop_reason = stop_reason

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def generate():
            for piece in self.pieces:
                yield piece

        return generate()

    async def get_final_message(self):
        usage = SimpleNamespace(
            input_tokens=5000, output_tokens=1000, cache_creation_input_tokens=0, cache_read_input_tokens=0
        )
        return SimpleNamespace(stop_reason=self.stop_reason, model=config.CLAUDE_MODEL, usage=usage)


class FakeClient:
    def __init__(self, pieces):
        self.calls = []
        fake = self

        class Messages:
            def stream(self, **params):
                fake.calls.append(params)
                return FakeStream(pieces)

        self.beta = SimpleNamespace(messages=Messages())


# ---- tests ------------------------------------------------------------------
def test_status(client):
    data = client.get("/api/status").json()
    assert data["articles"] > 480
    assert "as_on" in data["source"]


def test_parts_and_article(client):
    parts = client.get("/api/parts").json()
    assert parts[0]["title"] == "Preamble"
    article = client.get("/api/articles/21a").json()
    assert article["id"] == "21A" and article["label"] == "Article 21A"
    assert client.get("/api/articles/9999").status_code == 404


def test_search(client):
    data = client.get("/api/search", params={"q": "right to education"}).json()
    assert data["results"][0]["id"] == "21A"


def test_home_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Samvidhan AI" in response.text


def test_ask_with_answers_off_gives_search_results(client, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_ENGINE", "off")
    response = client.post("/api/ask", json={"question": "Can police arrest me without a warrant?"})
    events = read_events(response)
    assert events[0][0] == "sources"
    assert events[0][1]["sources"][0]["id"] == "22"
    assert events[-1] == ("done", {"type": "done", "mode": "search_only"})


def test_ask_with_fake_claude_streams_answer_and_checks_citations(client, monkeypatch):
    fake = FakeClient(["Within 24 hours ", "[Article 22(2)]. ", "Also [Article 999]."])
    monkeypatch.setattr(config, "ANSWER_ENGINE", "api")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(answer, "get_client", lambda: fake)

    response = client.post(
        "/api/ask", json={"question": "Can police keep me in custody for 3 days?", "style": "detailed"}
    )
    events = read_events(response)
    names = [name for name, _ in events]
    assert names[0] == "sources" and names.count("delta") == 3 and names[-1] == "done"

    done = events[-1][1]
    assert done["engine"] == "api" and done["cost"]["billing"] == "api"
    assert done["citations"]["verified"] == ["22"]
    assert done["citations"]["unverified"] == ["999"]
    assert done["cost"]["input_tokens"] == 5000 and done["cost"]["usd"] > 0

    # What we sent to Claude: rules in the system prompt, sources + question in the message
    params = fake.calls[0]
    assert "Answer style: DETAILED" in params["messages"][-1]["content"]
    assert '<source cite_as="[Article 22]"' in params["messages"][-1]["content"]
    assert params["output_config"] == {"effort": config.CLAUDE_EFFORT}
    if config.CLAUDE_MODEL == "claude-opus-5-5":
        assert params["fallbacks"] == "default"


def test_ask_rejects_empty_or_huge_questions(client):
    assert client.post("/api/ask", json={"question": ""}).status_code == 422
    too_long = "x" * (config.MAX_QUESTION_CHARS + 1)
    assert client.post("/api/ask", json={"question": too_long}).status_code == 422
