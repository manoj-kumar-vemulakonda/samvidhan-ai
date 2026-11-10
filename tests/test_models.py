"""
The three sizes of model (fast / main / deep) and prompt caching, checked without calling
Claude: a fake API client records what would have been sent.
"""

import asyncio
from types import SimpleNamespace

from app import answer, assistant, config, llm, store
from app.retrieval import Sources

CACHED = {"type": "ephemeral"}


def fake_api(monkeypatch) -> list[dict]:
    """Switch to the API engine with a fake client; returns the list of requests it receives."""
    calls: list[dict] = []

    class Messages:
        def create(self, **params):
            calls.append(params)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"ok": true}')])

    monkeypatch.setattr(config, "ANSWER_ENGINE", "api")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_api_client", lambda: SimpleNamespace(messages=Messages()))
    return calls


def test_each_size_of_job_gets_its_own_model_and_effort(monkeypatch):
    calls = fake_api(monkeypatch)
    for tier in (llm.FAST, llm.MAIN, llm.DEEP):
        llm.complete("rules", "hello", tier=tier)
    assert [c["model"] for c in calls] == [config.FAST_MODEL, config.CLAUDE_MODEL, config.DEEP_MODEL]
    assert "output_config" not in calls[0]  # quick jobs: the model's own default
    assert calls[1]["output_config"] == {"effort": config.CLAUDE_EFFORT}
    assert calls[2]["output_config"] == {"effort": config.DEEP_EFFORT}


def test_the_system_prompt_and_the_stable_part_are_cached(monkeypatch):
    calls = fake_api(monkeypatch)
    assert llm.complete_json("rules", ["<case>file index</case>", "<question>q</question>"], {"type": "object"}) == {"ok": True}
    request = calls[0]
    assert request["system"] == [{"type": "text", "text": "rules", "cache_control": CACHED}]
    first, last = request["messages"][0]["content"]
    assert first == {"type": "text", "text": "<case>file index</case>", "cache_control": CACHED}
    assert last == {"type": "text", "text": "<question>q</question>"}  # changes every time: never cached
    # No case (empty stable part) or a plain string: sent as plain text
    llm.complete("rules", ["", "just the question"])
    llm.complete("rules", "a string")
    assert calls[1]["messages"][0]["content"] == "just the question"
    assert calls[2]["messages"][0]["content"] == "a string"


class FakeStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    def text_stream(self):
        async def generate():
            yield "A careful answer."

        return generate()

    async def get_final_message(self):
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0,
                                cache_read_input_tokens=4000)
        return SimpleNamespace(stop_reason="end_turn", model=config.DEEP_MODEL, usage=usage)


def test_a_deep_answer_streams_from_the_deep_model(monkeypatch):
    fake_api(monkeypatch)
    calls: list[dict] = []

    def stream(**params):
        calls.append(params)
        return FakeStream()

    messages = SimpleNamespace(stream=stream)
    monkeypatch.setattr(llm, "_async_client", lambda: SimpleNamespace(messages=messages, beta=SimpleNamespace(messages=messages)))

    async def collect():
        return [event async for event in llm.stream("rules", ["<case>x</case>", "<question>y</question>"], tier=llm.DEEP)]

    events = asyncio.run(collect())
    assert events[0] == {"type": "delta", "text": "A careful answer."}
    done = events[-1]
    assert done["type"] == "done" and done["cost"]["cached_tokens"] == 4000
    request = calls[0]
    assert request["model"] == config.DEEP_MODEL and request["output_config"] == {"effort": config.DEEP_EFFORT}
    assert request["system"][0]["cache_control"] == CACHED
    assert request["messages"][0]["content"][0]["cache_control"] == CACHED


def test_cached_tokens_are_priced_at_the_cache_price():
    usage = SimpleNamespace(input_tokens=1000, output_tokens=100, cache_creation_input_tokens=2000,
                            cache_read_input_tokens=10000)
    cost = answer.estimate_cost("claude-sonnet-5-5", usage)
    # $2 per million fresh, $2.50 to save into the cache, $0.20 to read from it, $10 per million out
    expected = (1000 * 2.00 + 2000 * 2.50 + 10000 * 0.20 + 100 * 10.00) / 1_000_000
    assert cost["usd"] == round(expected, 5) == 0.01
    assert cost["input_tokens"] == 13000 and cost["cached_tokens"] == 10000
    # Without caching it would have cost more than twice as much
    plain = answer.estimate_cost("claude-sonnet-5-5", SimpleNamespace(input_tokens=13000, output_tokens=100))
    assert plain["usd"] > 2 * cost["usd"]


def test_claude_code_deep_uses_the_strongest_model_thinking_longer(monkeypatch):
    monkeypatch.setattr(llm, "claude_command", lambda: ["claude.exe"])
    deep = llm._cc_command("rules", llm.DEEP, [])
    assert deep[deep.index("--model") + 1] == config.CLAUDE_CODE_DEEP_MODEL
    assert deep[deep.index("--effort") + 1] == config.DEEP_EFFORT
    fast = llm._cc_command("rules", llm.FAST, [])
    assert fast[fast.index("--model") + 1] == config.CLAUDE_CODE_FAST_MODEL and "--effort" not in fast


def test_the_case_comes_first_so_it_can_be_cached(workspace):
    case = store.create_case({"title": "State v. Ramesh Kumar"})
    document = store.add_document({"case_id": case["id"], "title": "FIR 345/2026", "doc_type": "FIR"}, ["text"])
    sources = Sources(query="q", case=case, case_index=[document])
    stable, changing = assistant.prompt_parts("What next?", sources, [])
    assert "State v. Ramesh Kumar" in stable and "FIR 345/2026" in stable and "What next?" not in stable
    assert changing.endswith("<question>\nWhat next?\n</question>")
    assert assistant.build_prompt("What next?", sources, []) == stable + "\n\n" + changing
    # A general chat has no case part
    assert assistant.prompt_parts("Hi", Sources(query="q"), [])[0] == ""
