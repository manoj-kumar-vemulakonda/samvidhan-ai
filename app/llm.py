"""
Every AI call in the workspace goes through this file.

Two engines (chosen in .env, see app/config.py):
  * Claude Code on this PC - uses your Claude subscription (no API key), runs claude.exe
    in "print mode" with NO tools, so it can only read what we send and write text.
  * The Claude API - needs ANTHROPIC_API_KEY.

Three sizes of model:
  * FAST - quick jobs: translating a question, reading a scanned page.
  * MAIN - answers, organisation plans, drafts, task results, review tables.
  * DEEP - the "Deep" button in the chat: the strongest model, thinking longer (slower, about 2x the cost).

Four kinds of call:
  complete()       -> text
  complete_json()  -> a dict that matches a JSON schema (organisation plans, review cells)
  read_image()     -> the text on a page image (for scans the local OCR can't read)
  stream()         -> an answer word by word, for the chat

A prompt is a string, or a list of parts from the most stable to the least - for example
[the case file index, the sources and the question]. With the API, the system prompt and the
stable parts are marked for prompt caching: if the same beginning is sent again within
5 minutes (the next question in the same case), Claude reads it back from its cache at a
tenth of the price or less. Claude Code caches by itself, so there the parts are just joined.
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
DEEP = "deep"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CACHE ={"type": "ephemeral"}  # "keep this beginning of the prompt for 5 minutes"

# Claude Code helpers may run in parallel (review tables, OCR) - but not too many at once
_slots = threading.BoundedSemaphore(config.CLAUDE_CODE_PARALLEL)


class AIUnavailable(RuntimeError):
    """No engine is set up (search-only mode)."""


def engine() -> str:
    return config.answer_engine()


def available() -> bool:
    return engine() != "off"


def api_model(tier: str = MAIN) -> str:
    """The Claude API model for a size of job."""
    return {FAST: config.FAST_MODEL, DEEP: config.DEEP_MODEL}.get(tier, config.CLAUDE_MODEL)


def _cc_model(tier: str) -> str:
    """The Claude Code model name for a size of job ('' = Claude Code's own default)."""
    return {FAST: config.CLAUDE_CODE_FAST_MODEL, DEEP: config.CLAUDE_CODE_DEEP_MODEL}.get(tier, config.CLAUDE_CODE_MODEL)


def _effort(tier: str) -> str | None:
    """How hard Claude thinks: set for MAIN and DEEP jobs; quick jobs use the model's default."""
    return {MAIN: config.CLAUDE_EFFORT, DEEP: config.DEEP_EFFORT}.get(tier)


def model_label(tier: str = MAIN) -> str:
    if engine() == "api":
        return api_model(tier)
    if engine() == "claude_code":
        return _cc_model(tier) or "Claude Code default"
    return "none"


def _join(prompt) -> str:
    """A prompt as one piece of text (Claude Code, and anywhere caching doesn't apply)."""
    return prompt if isinstance(prompt, str) else "\n\n".join(part for part in prompt if part)


def _system_blocks(system: str) -> list[dict]:
    """The system prompt, marked for caching (it is the same for every question that day)."""
    return [{"type": "text", "text": system, "cache_control": CACHE}]


def _user_content(prompt) -> str | list[dict]:
    """
    The user's message for the API. A list of parts becomes one text block per part, and a
    cache mark goes after each stable part (never on the last one, which changes every time).
    A cache mark on text shorter than about 500 tokens is simply ignored by the API.
    """
    if isinstance(prompt, str):
        return prompt
    parts = [part for part in prompt if part]
    if len(parts) <= 1:
        return parts[0] if parts else ""
    blocks = [{"type": "text", "text": part} for part in parts]
    for block in blocks[:-1][-2:]:  # at most 2 marks here + 1 on the system prompt (the API allows 4)
        block["cache_control"] = CACHE
    return blocks


# ---------------------------------------------------------------------------
# Claude Code
# ---------------------------------------------------------------------------
def _cc_command(system: str, tier: str, extra: list[str]) -> list[str]:
    command = claude_command() + [
        "-p", "--tools", "", "--system-prompt", system, "--no-session-persistence",
        "--strict-mcp-config", "--disable-slash-commands", *extra,
    ]
    model = _cc_model(tier)
    if model:
        command += ["--model", model]
    effort = _effort(tier)
    if effort:
        command += ["--effort", effort]
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
    """One API call. `content` is the user's message, ready to send (see _user_content)."""
    params: dict = {
        "model": api_model(tier),
        "max_tokens": max_tokens,
        "system": _system_blocks(system),
        "messages": [{"role": "user", "content": content}],
    }
    output_config: dict = {}
    effort = _effort(tier)
    if effort:
        output_config["effort"] = effort
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
def complete(system: str, prompt, tier: str = FAST, timeout: float = 180, max_tokens: int = 8000) -> str:
    """Ask once, get text back."""
    kind = engine()
    if kind == "claude_code":
        result = _cc_run(_cc_command(system, tier, ["--output-format", "json"]), _join(prompt), timeout)
        return str(result.get("result") or "").strip()
    if kind == "api":
        return _api_create(system, _user_content(prompt), tier, max_tokens).strip()
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


def complete_json(system: str, prompt, schema: dict, tier: str = MAIN, timeout: float = 300,
                  max_tokens: int = 16000) -> dict:
    """Ask once, get back a dict that follows `schema` (checked by Claude Code / the API)."""
    kind = engine()
    if kind == "claude_code":
        result = _cc_run(
            _cc_command(system, tier, ["--output-format", "json", "--json-schema", json.dumps(schema)]),
            _join(prompt), timeout)
        structured = result.get("structured_output")
        if isinstance(structured, dict):
            return structured
        return _parse_json_text(str(result.get("result") or ""))
    if kind == "api":
        text = _api_create(system, _user_content(prompt), tier, max_tokens, {"type": "json_schema", "schema": schema})
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


async def stream(system: str, prompt, tier: str = MAIN) -> AsyncIterator[dict]:
    """
    Stream an answer: yields {"type": "delta", "text"} pieces, then one
    {"type": "done", "text", "model", "cost", "stop_reason"} - or {"type": "error", "message"}.
    """
    kind = engine()
    if kind == "claude_code":
        async for event in _stream_claude_code(system, _join(prompt), tier):
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


def _async_client():
    import anthropic

    return anthropic.AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY or None, max_retries=2)


async def _stream_api(system: str, prompt, tier: str) -> AsyncIterator[dict]:
    import anthropic

    from app.answer import estimate_cost
    from app.answer import friendly_error as api_error

    model = api_model(tier)
    client = _async_client()
    params: dict = {
        "model": model, "max_tokens": 32000, "system": _system_blocks(system),
        "messages": [{"role": "user", "content": _user_content(prompt)}],
        "output_config": {"effort": _effort(tier) or config.CLAUDE_EFFORT},
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
