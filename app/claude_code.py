"""
Answer questions with Claude Code instead of the Claude API.

Claude Code is the program behind the Claude extension in VS Code. It can answer a
single question without opening a chat ("print mode": claude -p). It uses the Claude
subscription you're signed in with, so no API key is needed and there's no charge per
question (your plan's usage limits apply).

For every question we start:
    claude.exe -p --output-format stream-json --include-partial-messages --tools ""
               --system-prompt <our rules> ...
and send it the sources + question. Its output is one JSON object per line; we pick out
the pieces of text as they arrive and pass them to the web page, exactly like the API path.

Safety: --tools "" means it can only write text (no files, no commands, no web), and it
runs inside an empty folder (.cache/claude-run) so it never reads your project files.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator

from app import config
from app.answer import SYSTEM_PROMPT, build_prompt_text, citation_report

if TYPE_CHECKING:
    from app.search import CaseHit, Hit

RUN_DIR = config.PROJECT_DIR / ".cache" / "claude-run"
TIMEOUT_SECONDS = 240


def claude_command() -> list[str]:
    """The program to run. (A function so tests can swap in a fake.)"""
    path = config.find_claude_code()
    if path and not os.path.exists(path):  # VS Code updated the extension: look again
        config.find_claude_code.cache_clear()
        path = config.find_claude_code()
    if not path:
        raise FileNotFoundError("Claude Code was not found on this PC.")
    return [path]


def build_command() -> list[str]:
    command = claude_command() + [
        "-p",                                  # answer once and exit
        "--output-format", "stream-json",      # one JSON event per line
        "--verbose",                           # (required for stream-json)
        "--include-partial-messages",          # stream the text as it's written
        "--tools", "",                         # no tools: it can only write text
        "--system-prompt", SYSTEM_PROMPT,      # our rules replace Claude Code's own prompt
        "--no-session-persistence",            # don't save these chats in Claude Code's history
        "--strict-mcp-config",                 # don't load any connectors
        "--disable-slash-commands",
        "--effort", config.CLAUDE_EFFORT,
    ]
    if config.CLAUDE_CODE_MODEL:
        command += ["--model", config.CLAUDE_CODE_MODEL]
    return command


def clean_environment() -> dict[str, str]:
    """
    Start Claude Code as a fresh, independent program: drop variables that would tie it
    to another Claude Code session, and any API key (so your subscription is used).
    """
    drop_exact = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_AGENT_SDK_VERSION", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
    return {
        name: value
        for name, value in os.environ.items()
        if name not in drop_exact and not name.startswith("CLAUDE_CODE_")
    }


@dataclass
class StreamJsonParser:
    """Reads Claude Code's stream-json output, one line at a time."""

    model: str | None = None
    text_parts: list[str] = field(default_factory=list)
    result: dict | None = None
    saw_deltas: bool = False

    def feed(self, line: str) -> list[str]:
        """Returns the new pieces of answer text found in this line (often none)."""
        line = line.strip()
        if not line.startswith("{"):
            return []
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []
        kind = data.get("type")
        if kind == "system" and data.get("subtype") == "init":
            self.model = data.get("model") or self.model
        elif kind == "stream_event":
            event = data.get("event", {})
            delta = event.get("delta", {})
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                self.saw_deltas = True
                self.text_parts.append(delta.get("text", ""))
                return [delta.get("text", "")]
        elif kind == "result":
            self.result = data
            # Older versions may not stream: then the whole answer arrives here at once
            if not self.saw_deltas and isinstance(data.get("result"), str) and not data.get("is_error"):
                self.text_parts.append(data["result"])
                return [data["result"]]
        return []

    @property
    def text(self) -> str:
        return "".join(self.text_parts)


def ask_once(prompt: str, system_prompt: str, model: str | None = "haiku", timeout: float = 60) -> str:
    """
    Ask Claude Code one short question and return its reply (no streaming).
    Used for small jobs like translating a Hindi question into English search words,
    with a fast model ("haiku") so it only takes a few seconds.
    """
    command = claude_command() + [
        "-p", "--output-format", "json", "--tools", "", "--system-prompt", system_prompt,
        "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands",
    ]
    if model:
        command += ["--model", model]
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        command,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        cwd=RUN_DIR,
        env=clean_environment(),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines = [line for line in completed.stdout.splitlines() if line.strip().startswith("{")]
    if not lines:
        raise RuntimeError(friendly_error(completed.stderr or "no reply"))
    data = json.loads(lines[-1])
    if data.get("is_error"):
        raise RuntimeError(friendly_error(str(data.get("result") or "error")))
    return str(data.get("result") or "").strip()


def friendly_error(message: str) -> str:
    lower = message.lower()
    if "login" in lower or "not logged in" in lower or "authenticat" in lower:
        return ("Claude Code isn't signed in. Open the Claude panel in VS Code, sign in, "
                "then ask again.")
    if "limit" in lower:
        return ("You've reached your Claude usage limit for now. It resets after a while - "
                "or add an API key in .env to keep going.")
    return f"Claude Code couldn't answer: {message.strip()[:300]}"


async def stream_answer(
    question: str, hits: list["Hit"], style: str, history: list[dict], cases: list["CaseHit"] | None = None
) -> AsyncIterator[dict]:
    """Same events as app.answer.stream_answer: delta ... done (or error)."""
    try:
        command = build_command()
    except FileNotFoundError as error:
        yield {"type": "error", "message": str(error)}
        return
    prompt = build_prompt_text(question, hits, style, history, cases)
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    running: dict = {}

    def put(item) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def worker() -> None:
        # Runs in a background thread: start Claude Code and pass its output lines along.
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=RUN_DIR,
                env=clean_environment(),
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),  # no black window
            )
            running["process"] = process
            errors: list[str] = []
            reader = threading.Thread(target=lambda: errors.extend(process.stderr), daemon=True)
            reader.start()
            process.stdin.write(prompt)
            process.stdin.close()
            for line in process.stdout:
                put(("line", line))
            code = process.wait()
            reader.join(timeout=2)
            put(("exit", code, "".join(errors[-20:])))
        except Exception as error:  # e.g. the program couldn't be started
            put(("fail", error))

    threading.Thread(target=worker, daemon=True).start()

    parser = StreamJsonParser()
    deadline = time.monotonic() + TIMEOUT_SECONDS
    exit_code, stderr_text = None, ""
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield {"type": "error", "message": "Claude Code took too long to answer. Please try again."}
                return
            try:
                item = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                continue
            if item[0] == "line":
                for piece in parser.feed(item[1]):
                    yield {"type": "delta", "text": piece}
            elif item[0] == "exit":
                exit_code, stderr_text = item[1], item[2]
                break
            else:
                yield {"type": "error", "message": f"Couldn't start Claude Code: {item[1]}"}
                return
    finally:
        # If the page stopped listening (Stop button) or we timed out, stop Claude Code too.
        process = running.get("process")
        if process is not None and process.poll() is None:
            process.kill()

    result = parser.result or {}
    if result.get("is_error") or (exit_code not in (0, None) and not parser.text):
        message = str(result.get("result") or stderr_text or f"exit code {exit_code}")
        yield {"type": "error", "message": friendly_error(message)}
        return

    usage = result.get("usage") or {}
    input_tokens = sum(
        int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    equivalent_usd = float(result.get("total_cost_usd") or 0.0)
    stop_reason = result.get("stop_reason")
    notice = None
    if stop_reason == "refusal":
        notice = "Claude declined to answer this question. Try asking it a different way."
    elif stop_reason == "max_tokens":
        notice = "The answer was cut off because it got too long. Try asking a narrower question."

    yield {
        "type": "done",
        "mode": "ai",
        "engine": "claude_code",
        "notice": notice,
        "model": parser.model or "Claude Code",
        "stop_reason": stop_reason,
        "cost": {
            "input_tokens": input_tokens,
            "output_tokens": int(usage.get("output_tokens") or 0),
            "usd": round(equivalent_usd, 5),  # what the same answer would cost through the API
            "inr": round(equivalent_usd * config.USD_TO_INR, 2),
            "billing": "subscription",  # you aren't charged per question
        },
        "citations": citation_report(parser.text, hits, cases),
    }
