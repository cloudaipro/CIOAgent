"""Offline tests for the ChatGPT-subscription Codex runtime."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import cio.agent as agent
import cio.agent_codex as agent_codex
from cio.agent_codex import CodexAppServer, CodexRuntime, CodexTurnTimeout


def _run(coro):
    return asyncio.run(coro)


class _FakeClient:
    def __init__(self):
        self.starts = []
        self.turns = []
        self.closed = False

    async def start_thread(self, *, model, instructions, resume=None):
        self.starts.append((model, instructions, resume))
        return "codex-thread-2" if len(self.starts) > 1 else "codex-thread-1"

    async def run_turn(self, thread_id, prompt, effort=None, model=None):
        self.turns.append((thread_id, prompt, effort))
        return "hello from subscription", 17

    async def close(self):
        self.closed = True


def _runtime(monkeypatch, tmp_path, model="default", reasoning_effort=None):
    monkeypatch.setattr(agent_codex.db, "DB_PATH", tmp_path / "codex.db")
    monkeypatch.setattr(agent_codex.memory, "get_meta", lambda *a, **k: None)
    monkeypatch.setattr(agent_codex.memory, "set_meta", lambda *a, **k: None)
    monkeypatch.setattr(agent.memory, "get_last_turn_day", lambda *a, **k: None)
    client = _FakeClient()
    link = {"service": "codex", "model": model}
    if reasoning_effort:
        link["reasoning_effort"] = reasoning_effort
    runtime = CodexRuntime(link, chat_id=42,
                           client_factory=lambda: client)
    return runtime, client


def test_codex_runtime_uses_base_turn_pipeline(monkeypatch, tmp_path):
    runtime, _ = _runtime(monkeypatch, tmp_path)
    assert issubclass(CodexRuntime, agent.BaseRuntime)
    assert runtime.ask is not None
    assert runtime._service == "codex"
    assert runtime._model is None  # "default" means the subscription default


def test_ensure_starts_subscription_thread_and_run_query(monkeypatch, tmp_path):
    runtime, client = _runtime(monkeypatch, tmp_path, reasoning_effort="max")
    _run(runtime._ensure())
    assert runtime.session_id == "codex-thread-1"
    assert client.starts[0][0] is None
    text, images = _run(runtime._guarded_turn("hello"))
    assert text == "hello from subscription"
    assert images == []
    assert client.turns == [("codex-thread-1", "hello", "max")]


def test_reasoning_effort_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CIO_CODEX_REASONING_EFFORT", "ULTRA")
    runtime, client = _runtime(monkeypatch, tmp_path, reasoning_effort="low")
    _run(runtime._ensure())
    _run(runtime._guarded_turn("hard question"))
    assert runtime._reasoning_effort == "ultra"
    assert client.turns[-1] == ("codex-thread-1", "hard question", "ultra")


def test_reset_starts_fresh_thread(monkeypatch, tmp_path):
    runtime, client = _runtime(monkeypatch, tmp_path, model="gpt-test")
    _run(runtime._ensure())
    _run(runtime._reset_session())
    assert runtime.session_id == "codex-thread-2"
    assert client.starts[-1][0] == "gpt-test"
    assert client.starts[-1][2] is None


def test_close_closes_app_server(monkeypatch, tmp_path):
    runtime, client = _runtime(monkeypatch, tmp_path)
    _run(runtime._ensure())
    _run(runtime.close())
    assert client.closed is True


def test_dynamic_specs_cover_every_cio_tool():
    specs = agent_codex._dynamic_tool_specs()
    assert [spec["name"] for spec in specs] == [tool.name for tool in agent.CIO_TOOLS]
    assert all(spec["type"] == "function" for spec in specs)
    assert all(spec["inputSchema"].get("type") == "object" for spec in specs)


def test_turn_input_attaches_existing_image(tmp_path):
    image = tmp_path / "receipt.png"
    image.write_bytes(b"not-really-an-image")
    parts = agent_codex._turn_input(f"read {image}")
    assert parts[0]["type"] == "text"
    assert parts[1] == {"type": "localImage", "path": str(image.resolve())}


def test_dynamic_tool_request_calls_existing_handler():
    seen = {}

    async def handler(arguments):
        seen.update(arguments)
        return {"content": [{"type": "text", "text": "tool result"}]}

    tool = SimpleNamespace(name="sample", handler=handler)
    server = CodexAppServer([tool])
    sent = []

    async def send(message):
        sent.append(message)

    server._send = send
    _run(server._handle_server_request({
        "id": 9, "method": "item/tool/call",
        "params": {"tool": "sample", "arguments": {"x": 1, "optional": None}},
    }))
    assert seen == {"x": 1}
    assert sent == [{"id": 9, "result": {"success": True, "contentItems": [
        {"type": "inputText", "text": "tool result"}
    ]}}]


def test_turn_completion_extracts_last_agent_message():
    server = CodexAppServer([])
    seen = {}

    async def request(method, params):
        assert method == "turn/start"
        seen.update(params)
        loop = asyncio.get_running_loop()
        loop.call_soon(server._handle_notification, "turn/completed", {
            "threadId": "thread-1",
            "turn": {"id": "turn-1", "status": "completed", "items": [
                {"type": "agentMessage", "text": "final answer"}
            ]},
        })
        return {"turn": {"id": "turn-1"}}

    server._request = request
    text, tokens = _run(server.run_turn("thread-1", "hi", effort="high"))
    assert text == "final answer"
    assert tokens == 0
    assert seen["effort"] == "high"


def test_turn_timeout_interrupts_remote_turn_and_preserves_diagnostics(monkeypatch):
    server = CodexAppServer([])
    monkeypatch.setattr(agent_codex, "CODEX_TURN_TIMEOUT", 0.01)
    monkeypatch.setattr(agent_codex, "CODEX_INTERRUPT_TIMEOUT", 0.05)
    calls = []

    async def request(method, params):
        calls.append((method, params))
        if method == "turn/start":
            return {"turn": {"id": "turn-timeout"}}
        assert method == "turn/interrupt"
        # Simulate app-server acknowledging the interrupt by completing the
        # turn notification while the client is in its settlement grace period.
        asyncio.get_running_loop().call_soon(
            server._handle_notification, "turn/completed", {
                "turn": {"id": "turn-timeout", "status": "interrupted"}
            })
        return {}

    server._request = request
    with pytest.raises(CodexTurnTimeout) as raised:
        _run(server.run_turn("thread-timeout", "slow request", effort="max",
                             model="gpt-5.6-luna"))

    error = raised.value
    assert calls == [
        ("turn/start", {"threadId": "thread-timeout",
                         "input": [{"type": "text", "text": "slow request"}],
                         "effort": "max"}),
        ("turn/interrupt", {"threadId": "thread-timeout", "turnId": "turn-timeout"}),
    ]
    assert error.thread_id == "thread-timeout"
    assert error.turn_id == "turn-timeout"
    assert error.model == "gpt-5.6-luna"
    assert error.effort == "max"
    assert error.interrupt_sent is True
    assert error.settled is True
    assert "thread_id=thread-timeout" in str(error)
    assert "turn_id=turn-timeout" in str(error)
    assert "gpt-5.6-luna" in str(error)
    assert "max" in str(error)
    assert "OpenAI subscription runtime error" not in error.user_message
    assert server._completed_turns == {}
    assert server._abandoned_turns == set()


def test_late_completion_after_timeout_is_discarded(monkeypatch):
    server = CodexAppServer([])
    monkeypatch.setattr(agent_codex, "CODEX_TURN_TIMEOUT", 0.01)
    monkeypatch.setattr(agent_codex, "CODEX_INTERRUPT_TIMEOUT", 0.01)

    async def request(method, params):
        if method == "turn/start":
            return {"turn": {"id": "turn-late"}}
        assert method == "turn/interrupt"
        return {}

    server._request = request
    with pytest.raises(CodexTurnTimeout) as raised:
        _run(server.run_turn("thread-late", "slow request", model="gpt-test"))

    assert raised.value.interrupt_sent is True
    assert raised.value.settled is False
    server._handle_notification("thread/tokenUsage/updated", {
        "turnId": "turn-late", "tokenUsage": {"last": {"totalTokens": 999}}
    })
    server._handle_notification("turn/completed", {
        "turn": {"id": "turn-late", "status": "completed",
                  "items": [{"type": "agentMessage", "text": "late"}]}
    })
    assert server._turn_usage == {}
    assert server._completed_turns == {}
    assert server._abandoned_turns == set()


def test_runtime_timeout_is_user_facing_and_does_not_latch_codex(monkeypatch, tmp_path):
    runtime, client = _runtime(monkeypatch, tmp_path, model="gpt-test",
                               reasoning_effort="medium")
    from cio.committee import engine
    latched = []

    async def timeout(*args, **kwargs):
        raise CodexTurnTimeout(
            thread_id="codex-thread-1", turn_id="turn-timeout",
            model="gpt-test", effort="medium", timeout=600, elapsed=600.1,
            interrupt_sent=True, settled=True)

    monkeypatch.setattr(client, "run_turn", timeout)
    monkeypatch.setattr(engine, "latch", lambda service: latched.append(service))
    _run(runtime._ensure())
    text, images = _run(runtime._run_query("slow request"))

    assert images == []
    assert "exceeded the 600-second response limit" in text
    assert "OpenAI subscription runtime error" not in text
    assert latched == []


def test_unsettled_timeout_resets_the_codex_transport(monkeypatch, tmp_path):
    runtime, client = _runtime(monkeypatch, tmp_path, model="gpt-test",
                               reasoning_effort="medium")

    async def timeout(*args, **kwargs):
        raise CodexTurnTimeout(
            thread_id="codex-thread-1", turn_id="turn-timeout",
            model="gpt-test", effort="medium", timeout=600, elapsed=600.1,
            interrupt_sent=True, settled=False)

    monkeypatch.setattr(client, "run_turn", timeout)
    _run(runtime._ensure())
    text, _ = _run(runtime._run_query("slow request"))

    assert client.closed is True
    assert runtime._client is None
    assert runtime._session_id is None
    assert "did not confirm settlement" in text


def test_one_shot_thread_is_ephemeral_and_has_no_dynamic_tools():
    server = CodexAppServer([])
    seen = {}

    async def start():
        return None

    async def request(method, params):
        seen["method"] = method
        seen["params"] = params
        return {"thread": {"id": "one-shot-thread"}}

    server.start = start
    server._request = request
    thread_id = _run(server.start_thread(
        model="gpt-test",
        instructions="role instructions",
        developer_instructions="one shot only",
        ephemeral=True,
    ))

    assert thread_id == "one-shot-thread"
    assert seen["method"] == "thread/start"
    assert seen["params"]["dynamicTools"] == []
    assert seen["params"]["ephemeral"] is True
    assert seen["params"]["baseInstructions"] == "role instructions"
    assert seen["params"]["developerInstructions"] == "one shot only"


def test_committee_codex_backend_passes_model_effort_and_prompt(monkeypatch):
    from cio.committee import engine

    class FakeOneShotClient:
        def __init__(self):
            self.starts = []
            self.turns = []

        async def start_thread(self, **kwargs):
            self.starts.append(kwargs)
            return "committee-thread"

        async def run_turn(self, thread_id, prompt, effort=None, model=None):
            self.turns.append((thread_id, prompt, effort))
            return "committee answer", 123

    client = FakeOneShotClient()

    async def fake_client():
        return client

    monkeypatch.setattr(engine, "_codex_client", fake_client)
    text, tokens = _run(engine._ask_codex(
        "market role", "analyze TER", "gpt-5.6-luna", "max"))

    assert (text, tokens) == ("committee answer", 123)
    assert client.starts[0]["model"] == "gpt-5.6-luna"
    assert client.starts[0]["instructions"] == "market role"
    assert client.starts[0]["ephemeral"] is True
    assert client.turns == [("committee-thread", "analyze TER", "max")]


def test_committee_chain_dispatches_codex_without_claude(monkeypatch, tmp_path):
    from cio.committee import engine, models

    cfg = tmp_path / "models.yaml"
    cfg.write_text("""
chains:
  subscription:
  - {service: codex, model: gpt-5.6-luna, reasoning_effort: xhigh}
  - {service: claude, model: claude-opus-4-8}
defaults: {chain: subscription}
agents:
  market: {chain: subscription}
  wma: {chain: subscription}
""", encoding="utf-8")
    monkeypatch.setenv("CIO_MODELS_CONFIG", str(cfg))
    monkeypatch.setattr(engine._usage, "record", lambda *a, **k: None)
    monkeypatch.setattr(engine._usage, "over_budget", lambda *a, **k: False)
    models.load_config.cache_clear()
    engine._LIMIT_LATCH.clear()
    calls = []

    async def fake_codex(sp, up, model=None, reasoning_effort=None):
        calls.append((sp, up, model, reasoning_effort))
        return ("codex answer", 44)

    async def no_claude(*args, **kwargs):
        raise AssertionError("Codex must not fall through to Claude")

    monkeypatch.setattr(engine, "_ask_codex", fake_codex)
    monkeypatch.setattr(engine, "_ask_claude", no_claude)
    try:
        assert _run(engine.ask_role("market role", "TER", role_key="market")) == "codex answer"
        assert _run(engine.ask_role("wma role", "watch TER", role_key="wma")) == "codex answer"
    finally:
        models.load_config.cache_clear()
        engine._LIMIT_LATCH.clear()

    assert calls == [
        ("market role", "TER", "gpt-5.6-luna", "xhigh"),
        ("wma role", "watch TER", "gpt-5.6-luna", "xhigh"),
    ]


def test_unknown_committee_service_does_not_fall_through_to_claude(monkeypatch):
    from cio.committee import engine

    async def no_claude(*args, **kwargs):
        raise AssertionError("unknown service must not become Claude")

    monkeypatch.setattr(engine, "_ask_claude", no_claude)
    assert _run(engine._dispatch("typo", "sys", "user", "model")) == ("", 0)


def test_close_shared_committee_codex_backend():
    from cio.committee import engine

    class FakeClient:
        closed = False

        async def close(self):
            self.closed = True

    async def scenario():
        client = FakeClient()
        engine._CODEX_CLIENT = client
        engine._CODEX_CLIENT_LOOP = asyncio.get_running_loop()
        engine._CODEX_CLIENT_LOCK = asyncio.Lock()
        await engine.close_codex_backend()
        assert client.closed is True
        assert engine._CODEX_CLIENT is None
        assert engine._CODEX_CLIENT_LOOP is None

    _run(scenario())
