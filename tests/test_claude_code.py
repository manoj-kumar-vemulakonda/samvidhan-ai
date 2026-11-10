"""
Tests for answering with Claude Code. A small fake "Claude Code" program (written by
the test) prints the same kind of output as the real one, so nothing real is called.
"""

import json
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from app import claude_code, config
from app.claude_code import StreamJsonParser, build_command, clean_environment
from app.main import app

FAKE_PROGRAM = textwrap.dedent(
    """
    import json, sys
    prompt = sys.stdin.buffer.read().decode("utf-8")
    def emit(obj):
        print(json.dumps(obj), flush=True)
    if "FAIL-LOGIN" in prompt:
        emit({"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in - please run /login"})
        sys.exit(1)
    assert "<sources>" in prompt and "Question:" in prompt
    emit({"type": "system", "subtype": "init", "model": "claude-opus-5-5", "tools": []})
    for piece in ["Within 24 hours ", "[Article 22(2)]. ", "Also [Article 999]."]:
        emit({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
              "delta": {"type": "text_delta", "text": piece}}})
    emit({"type": "result", "subtype": "success", "is_error": False, "result": "(full text)",
          "stop_reason": "end_turn", "total_cost_usd": 0.03,
          "usage": {"input_tokens": 4000, "output_tokens": 300}})
    """
)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    program = tmp_path / "fake_claude.py"
    program.write_text(FAKE_PROGRAM, encoding="utf-8")
    monkeypatch.setattr(claude_code, "claude_command", lambda: [sys.executable, str(program)])
    monkeypatch.setattr(config, "ANSWER_ENGINE", "claude_code")
    monkeypatch.setattr(config, "find_claude_code", lambda: str(program))


def read_events(response):
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_command_is_text_only_with_our_rules(monkeypatch):
    monkeypatch.setattr(claude_code, "claude_command", lambda: ["claude.exe"])
    command = build_command()
    assert command[command.index("--tools") + 1] == ""          # no tools at all
    assert "Samvidhan AI" in command[command.index("--system-prompt") + 1]
    assert "-p" in command and "--no-session-persistence" in command and "--strict-mcp-config" in command


def test_environment_is_cleaned(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc")
    monkeypatch.setenv("SOMETHING_ELSE", "kept")
    env = clean_environment()
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env and "CLAUDE_CODE_SESSION_ID" not in env
    assert env["SOMETHING_ELSE"] == "kept"


def test_parser_reads_stream_json():
    parser = StreamJsonParser()
    lines = [
        '{"type":"system","subtype":"init","model":"claude-opus-5-5"}',
        "not json at all",
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hi "}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"there"}}}',
        '{"type":"result","is_error":false,"result":"Hi there"}',
    ]
    pieces = [piece for line in lines for piece in parser.feed(line)]
    assert pieces == ["Hi ", "there"]  # the result text isn't repeated
    assert parser.model == "claude-opus-5-5" and parser.text == "Hi there"


def test_parser_without_streaming_uses_result():
    parser = StreamJsonParser()
    assert parser.feed('{"type":"result","is_error":false,"result":"Whole answer"}') == ["Whole answer"]


def test_ask_through_claude_code(fake_claude):
    with TestClient(app) as client:
        assert client.get("/api/status").json()["engine"] == "claude_code"
        response = client.post("/api/ask", json={"question": "Can police keep me in custody for 3 days?"})
    events = read_events(response)
    names = [name for name, _ in events]
    assert names[0] == "sources" and names.count("delta") == 3 and names[-1] == "done"
    done = events[-1][1]
    assert done["engine"] == "claude_code" and done["model"] == "claude-opus-5-5"
    assert done["cost"]["billing"] == "subscription" and done["cost"]["input_tokens"] == 4000
    assert done["citations"]["verified"] == ["22"] and done["citations"]["unverified"] == ["999"]


def test_login_problem_is_explained(fake_claude):
    with TestClient(app) as client:
        response = client.post("/api/ask", json={"question": "FAIL-LOGIN please"})
    name, data = read_events(response)[-1]
    assert name == "error" and "isn't signed in" in data["message"]
