"""Regression coverage for committee structured-output integrity."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace


def _run(coro):
    return asyncio.run(coro)


_SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "minLength": 1}},
    "required": ["answer"],
    "additionalProperties": False,
}


def test_debate_schema_is_minimal_text_envelope():
    from cio.committee import structured
    schema = structured.DEBATE_TEXT_SCHEMA
    assert schema["required"] == ["text"]
    assert set(schema["properties"]) == {"text"}
    assert schema["additionalProperties"] is False


def test_parser_prefers_json_and_keeps_legacy_yaml_compatibility():
    from cio.committee import structured

    assert structured.parse('{"answer":"qualitative assessment: neutral"}') == {
        "answer": "qualitative assessment: neutral"
    }
    assert structured.parse("```json\n{\"answer\":\"ok\"}\n```") == {"answer": "ok"}
    assert structured.parse("```yaml\nanswer: ok\n```") == {"answer": "ok"}


def test_invalid_nonempty_output_retries_then_falls_through(monkeypatch):
    from cio.committee import engine

    calls = []

    async def fake_dispatch(service, system, user, model, effort=None,
                            response_schema=None, schema_name="committee_output"):
        calls.append((service, response_schema, schema_name))
        if service == "muse":
            return "not json despite being non-empty", 10
        return '{"answer":"valid fallback"}', 11

    monkeypatch.setattr(engine, "_dispatch", fake_dispatch)
    monkeypatch.setattr(engine, "_resolve_chain", lambda role: [
        {"service": "muse", "model": "local"},
        {"service": "codex", "model": "subscription"},
    ])
    monkeypatch.setattr(engine, "_resolve_chain_name", lambda role: "test")
    monkeypatch.setattr(engine, "_latched", lambda service: False)
    monkeypatch.setattr(engine._usage, "over_budget", lambda service, limit: False)
    monkeypatch.setattr(engine._usage, "record", lambda service, tokens: None)
    monkeypatch.setattr(engine, "_capture", lambda *args: None)
    monkeypatch.setenv("CIO_STRUCTURED_RETRIES", "1")

    raw = _run(engine.ask_structured_role(
        "system", "user", role_key="market",
        response_schema=_SIMPLE_SCHEMA, schema_name="test_answer"))

    assert json.loads(raw) == {"answer": "valid fallback"}
    assert [c[0] for c in calls] == ["muse", "muse", "codex"]
    assert all(c[1] == _SIMPLE_SCHEMA for c in calls)


def test_muse_sends_native_json_schema(monkeypatch):
    from cio.committee import engine

    sent = {}

    class Response:
        is_success = True
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "choices": [{"message": {"content": '{"answer":"ok"}'},
                             "finish_reason": "stop"}],
                "usage": {"total_tokens": 10},
            }

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            sent.update(kwargs)
            return Response()

    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr(engine, "muse_settings", lambda: {
        "base_url": "http://127.0.0.1:8001/v1", "api_key_env": "NO_KEY",
        "max_output_tokens": 100, "timeout": 30, "reasoning_strength": "xhigh",
    })

    text, _ = _run(engine._ask_muse(
        "system", "user", "muse", response_schema=_SIMPLE_SCHEMA,
        schema_name="answer_schema"))

    assert json.loads(text) == {"answer": "ok"}
    fmt = sent["json"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"] == _SIMPLE_SCHEMA


def test_openai_sends_native_json_schema(monkeypatch):
    from cio.committee import engine
    import openai

    sent = {}

    class Completions:
        async def create(self, **kwargs):
            sent.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer":"ok"}'))],
                usage=SimpleNamespace(total_tokens=9),
            )

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=Completions())

    monkeypatch.setenv("TEST_OPENAI_KEY", "key")
    monkeypatch.setattr(openai, "AsyncOpenAI", Client)
    monkeypatch.setattr(engine, "openai_settings", lambda: {
        "api_key_env": "TEST_OPENAI_KEY", "base_url": "https://example.invalid/v1",
        "token_param": "max_completion_tokens", "max_output_tokens": 100,
    })

    text, tokens = _run(engine._ask_openai(
        "system", "user", "model", _SIMPLE_SCHEMA, "answer_schema"))

    assert (json.loads(text), tokens) == ({"answer": "ok"}, 9)
    assert sent["response_format"]["json_schema"]["schema"] == _SIMPLE_SCHEMA


def test_nim_sends_native_json_schema(monkeypatch):
    from cio.committee import engine

    sent = {}

    class Response:
        status_code = 200
        is_success = True
        headers = {}
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": '{"answer":"ok"}'}}],
                    "usage": {"total_tokens": 8}}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            sent.update(kwargs)
            return Response()

    monkeypatch.setenv("TEST_NIM_KEY", "key")
    monkeypatch.setattr("httpx.AsyncClient", Client)
    monkeypatch.setattr(engine, "nim_settings", lambda: {
        "api_key_env": "TEST_NIM_KEY", "base_url": "https://example.invalid/v1",
        "max_output_tokens": 100,
    })

    text, tokens = _run(engine._ask_nim(
        "system", "user", "model", _SIMPLE_SCHEMA, "answer_schema"))

    assert (json.loads(text), tokens) == ({"answer": "ok"}, 8)
    assert sent["json"]["response_format"]["json_schema"]["schema"] == _SIMPLE_SCHEMA


def test_claude_uses_output_format_and_reads_structured_result(monkeypatch):
    from cio.committee import engine
    import claude_agent_sdk

    seen = {}

    class Client:
        def __init__(self, options):
            seen["output_format"] = options.output_format

        async def connect(self):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield claude_agent_sdk.ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id="s",
                usage={"input_tokens": 3, "output_tokens": 4},
                structured_output={"answer": "ok"},
            )

        async def disconnect(self):
            pass

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)
    monkeypatch.setattr(engine, "claude_settings", lambda: {})

    text, _ = _run(engine._ask_claude(
        "system", "user", "model", _SIMPLE_SCHEMA))

    assert json.loads(text) == {"answer": "ok"}
    assert seen["output_format"] == {"type": "json_schema", "schema": _SIMPLE_SCHEMA}


def test_codex_turn_passes_output_schema(monkeypatch):
    from cio import agent_codex

    server = object.__new__(agent_codex.CodexAppServer)
    server._turn_waiters = {}
    server._completed_turns = {
        "turn-1": {"id": "turn-1", "status": "completed",
                   "items": [{"type": "agentMessage", "text": '{"answer":"ok"}'}]}
    }
    server._turn_usage = {"turn-1": 7}
    server._abandoned_turns = set()
    seen = {}

    async def request(method, params):
        seen.update(params)
        return {"turn": {"id": "turn-1"}}

    server._request = request
    text, tokens = _run(server.run_turn(
        "thread-1", "prompt", output_schema=_SIMPLE_SCHEMA))

    assert (text, tokens) == ('{"answer":"ok"}', 7)
    assert seen["outputSchema"] == _SIMPLE_SCHEMA


def test_opinion_table_preserves_full_reason():
    from cio.committee.report import _opinion_table

    reason = "full rationale " * 40 + "with | pipe"
    md = _opinion_table([{
        "title": "Macro", "vote": "HOLD", "confidence": 50, "reason": reason,
    }])
    assert "..." not in md
    assert "full rationale " * 40 in md
    assert r"with \| pipe" in md


def test_collision_safe_report_path_never_overwrites(tmp_path):
    from cio.committee.delivery import _collision_safe_path

    base = tmp_path / "ABNB_committee_2026-08-12_zh.pdf"
    base.with_suffix(".md").write_text("first", encoding="utf-8")
    result = SimpleNamespace(
        as_of="2026-08-12T12:00:07.461012",
        tirf=SimpleNamespace(run_id="3cec1a06f0ec"),
    )
    resolved = _collision_safe_path(base, result)
    assert resolved != base
    assert "120007_3cec1a06f0ec" in resolved.name
