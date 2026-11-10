"""
Every AI call in the workspace goes through this file.

Two engines (chosen in .env, see app/config.py):
  * Claude Code on this PC - uses your Claude subscription (no API key), runs claude.exe
    in "print mode" with NO tools, so it can only read what we send and write text.
  * The Claude API - needs ANTHROPIC_API_KEY.

Two sizes of model:
  * FAST - quick jobs: translating a question, reading a scanned page, review-table cells.
  * MAIN - answers, organisation plans, drafts, workflow results.

Four kinds of call:
  complete()       -> text
  complete_json()  -> a dict that matches a JSON schema (organisation plans, review cells)
  read_image()     -> the text on a page image (for scans the local OCR can't read)
  stream()         -> an answer word by word, for the chat
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import subprocess
import threading
import time
from typing import AsyncIterator

from app import config
from app.claude_code import RUN_DIR, StreamJsonParser, claude_command, clean_environment, friendly_error

FAST = "fast"
MAIN = "main"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Claude Code helpers may run in parallel (review tables, OCR) - but not too many at once
_slots = threading.BoundedSemaphore(config.CLAUDE_CODE_PARALLEL)


class AIUnavailable(RuntimeError):
    """No engine is set up (search-only mode)."""


def engine() -> str:
    return config.answer_engine()


def available() -> bool:
    return engine() != "off"


def model_label(tier: str = MAIN) -> str:
    if engine() == "api":
        return config.FAST_MODEL if tier == FAST else config.CLAUDE_MODEL
    if engine() == "claude_code":
        return config.CLAUDE_CODE_FAST_MODEL if tier == FAST else (config.CLAUDE_CODE_MODEL or "Claude Code default")
    return "none"


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------
def _cc_command(system: str, tier: str, extra: list[str]) -> list[str]:
    command = claude_command() + [
        "-p", "--tools", "", "--system-prompt", system, "--no-session-persistence",
        "--strict-mcp-config", "--disable-slash-commands", *extra,
    ]
    model = config.CLAUDE_CODE_FAST_MODEL if tier == FAST else config.CLAUDE_CODE_MODEL
    if model:
        command += ["--model", model]
    if tier == MAIN:
        command += ["--effort", config.CLAUDE_EFFORT]
    return command


def _cc_run(command: list[str], stdin_text: str, timeout: float) -> dict:
    """Run Claude Code once and return its final 'result' record."""
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    with _slots:
        try:
            completed = subprocess.run(
                command, input=stdin_text, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, cwd=RUN_DIR, env=clean_environment(), creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Claude took too long to reply. Please try again.") from error
    result = None
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("type") == "result" or "result" in data:
            result = data
    if result is None:
        raise RuntimeError(friendly_error(completed.stderr or completed.stdout or "no reply"))
    if result.get("is_error"):
        raise RuntimeError(friendly_error(str(result.get("result") or result.get("subtype") or "error")))
    return result


# ---------------------------------------------------------------------------
# The Claude API
# ---------------------------------------------------------------------------
def _api_client():
    import anthropic

    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY or None, max_retries=2)


def _api_create(system: str, content, tier: str, max_tokens: int, output_format: dict | None = None):
    params: dict = {
        "model": config.FAST_MODEL if tier == FAST else config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }
    output_config: dict = {}
    if tier == MAIN:
        output_config["effort"] = config.CLAUDE_EFFORT
    if output_format:
        output_config["format"] = output_format
    if output_config:
        params["output_config"] = output_config
    response = _api_client().messages.create(**params)
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined this request.")
    return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------
def complete(system: str, prompt: str, tier: str = FAST, timeout: float = 180, max_tokens: int = 8000) -> str:
    """Ask once, get text back."""
    kind = engine()
    if kind == "claude_code":
        result = _cc_run(_cc_command(system, tier, ["--output-format", "json"]), prompt, timeout)
        return str(result.get("result") or "").strip()
    if kind == "api":
        return _api_create(system, prompt, tier, max_tokens).strip()
    raise AIUnavailable("AI is switched off (search-only mode).")


def _parse_json_text(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("The AI did not return JSON.")
    return json.loads(text[start:end + 1])


def complete_json(system: str, prompt: str, schema: dict, tier: str = MAIN, timeout: float = 300,
                  max_tokens: int = 16000) -> dict:
    """Ask once, get back a dict that follows `schema` (checked by Claude Code / the API)."""
    kind = engine()
    if kind == "claude_code":
        result = _cc_run(
            _cc_command(system, tier, ["--output-format", "json", "--json-schema", json.dumps(schema)]), prompt, timeout)
        structured = result.get("structured_output")
        if isinstance(structured, dict):
            return structured
        return _parse_json_text(str(result.get("result") or ""))
    if kind == "api":
        text = _api_create(system, prompt, tier, max_tokens, {"type": "json_schema", "schema": schema})
        return _parse_json_text(text)
    raise AIUnavailable("AI is switched off (search-only mode).")


def read_image(png: bytes, instruction: str, tier: str = FAST, timeout: float = 150, media_type: str = "image/png") -> str:
    """Show Claude one image (a scanned page) and get its answer back - used as OCR for hard pages."""
    data = base64.b64encode(png).decode("ascii")
    image_block = {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
    system = ("You transcribe scanned legal documents from India exactly as written, in their original "
              "language and script. Output only the transcription.")
    kind = engine()
    if kind == "claude_code":
        message = {"type": "user", "message": {"role": "user", "content": [image_block, {"type": "text", "text": instruction}]}}
        command = _cc_command(system, tier, ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"])
        result = _cc_run(command, json.dumps(message) + "\n", timeout)
        return str(result.get("result") or "").strip()
    if kind == "api":
        return _api_create(system, [image_block, {"type": "text", "text": instruction}], tier, 8000).strip()
    raise AIUnavailable("AI is switched off (search-only mode).")


async def stream(system: str, prompt: str, tier: str = MAIN) -> AsyncIterator[dict]:
    """
    Stream an answer: yields {"type": "delta", "text"} pieces, then one
    {"type": "done", "text", "model", "cost", "stop_reason"} - or {"type": "error", "message"}.
    """
    kind = engine()
    if kind == "claude_code":
        async for event in _stream_claude_code(system, prompt, tier):
            yield event
    elif kind == "api":
        async for event in _stream_api(system, prompt, tier):
            yield event
    else:
        yield {"type": "error", "message": "AI answers are switched off (search-only mode)."}


async def _stream_claude_code(system: str, prompt: str, tier: str) -> AsyncIterator[dict]:
    try:
        command = _cc_command(system, tier, ["--output-format", "stream-json", "--verbose", "--include-partial-messages"])
    except FileNotFoundError as error:
        yield {"type": "error", "message": str(error)}
        return
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    running: dict = {}

    def put(item) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def worker() -> None:
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=RUN_DIR,
                env=clean_environment(), text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
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
        except Exception as error:
            put(("fail", error))

    threading.Thread(target=worker, daemon=True).start()
    parser = StreamJsonParser()
    deadline = time.monotonic() + 600
    exit_code, stderr_text = None, ""
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield {"type": "error", "message": "Claude took too long to answer. Please try again."}
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
        process = running.get("process")
        if process is not None and process.poll() is None:
            process.kill()
    result = parser.result or {}
    if result.get("is_error") or (exit_code not in (0, None) and not parser.text):
        yield {"type": "error", "message": friendly_error(str(result.get("result") or stderr_text or f"exit code {exit_code}"))}
        return
    usage = result.get("usage") or {}
    usd = float(result.get("total_cost_usd") or 0.0)
    yield {
        "type": "done", "text": parser.text, "model": parser.model or "Claude Code", "engine": "claude_code",
        "stop_reason": result.get("stop_reason"),
        "cost": {
            "input_tokens": sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "usd": round(usd, 5), "inr": round(usd * config.USD_TO_INR, 2), "billing": "subscription",
        },
    }


async def _stream_api(system: str, prompt: str, tier: str) -> AsyncIterator[dict]:
    import anthropic

    from app.answer import estimate_cost
    from app.answer import friendly_error as api_error

    model = config.FAST_MODEL if tier == FAST else config.CLAUDE_MODEL
    client = anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY or None, max_retries=2)
    params: dict = {
        "model": model, "max_tokens": 32000, "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {"effort": config.CLAUDE_EFFORT},
    }
    if "haiku" not in model:
        params["betas"] = ["server-side-fallback-2026-07-01"]
        params["fallbacks"] = "default"
    pieces: list[str] = []
    try:
        if "betas" in params:
            manager = client.beta.messages.stream(**params)
        else:
            manager = client.messages.stream(**params)
        async with manager as response_stream:
            async for text in response_stream.text_stream:
                pieces.append(text)
                yield {"type": "delta", "text": text}
            final = await response_stream.get_final_message()
    except anthropic.APIError as error:
        yield {"type": "error", "message": api_error(error)}
        return
    yield {"type": "done", "text": "".join(pieces), "model": final.model or model, "engine": "api",
           "stop_reason": final.stop_reason, "cost": estimate_cost(final.model or model, final.usage)}
