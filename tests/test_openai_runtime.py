"""
test_openai_runtime.py -- Offline tests for Step 15: OpenAIRuntime, the
OpenAI Agents SDK transport for BaseRuntime's shared turn pipeline
(cio.agent_openai; Architect brief Step 15; plan of record
docs/BOT-CHAT-OPENAI-MIGRATION.md).

No real LLM calls, no network. `agents.Runner.run` is monkeypatched to a fake
for every test that runs a turn, so no delegate Model, FallbackModel walk, or
OpenAI client is ever exercised end to end -- exactly the seam the brief's
Flags section names ("Runner.run is patchable"). A real `SQLiteSession` is
used (it is local sqlite, not network) but `cio.db.DB_PATH` is monkeypatched
to a tmp_path for every test so its `agent_sessions`/`agent_messages` tables
never land in the owner's real data/cfo.db. `context.compose_system_prompt`
is left untouched -- it is read-only (get_profile/list_notes/latest_digest/
list_playbooks), and CIOAgent's own constructor already exercises the same
call against the same real DB in the existing suite (see test_bot_runtime.py),
so this isn't a new risk.

This module intentionally does NOT test wiring -- Step 15 does not wire
OpenAIRuntime into select_runtime/bot.py; Step 16 does.
"""
from __future__ import annotations

import asyncio

import pytest
from agents import Usage

import cio.agent as agent
import cio.agent_openai as agent_openai
from cio.agent_openai import MAX_TURNS, OpenAIRuntime
from cio.committee import usage as _usage


def _run(coro):
    return asyncio.run(coro)


# A minimally-shaped OpenAI fallback-chain link -- just enough for
# FallbackModel.__init__ to accept it (service == "openai"). Never actually
# dialled: Runner.run is monkeypatched in every test that reaches _run_query.
_LINKS = [{"service": "openai", "model": "gpt-test", "daily_limit": None}]


class _FakeResult:
    """Duck-typed stand-in for agents.result.RunResult -- _run_query only
    ever reads .final_output and .context_wrapper.usage."""

    def __init__(self, final_output, usage=None):
        self.final_output = final_output
        self.context_wrapper = type("_Ctx", (), {"usage": usage})()


def _fake_runner_run(result=None, exc=None, sink: dict | None = None):
    """A replacement for agents.Runner.run. Records the call's (agent, input,
    kwargs) into *sink* when given; raises *exc* or returns *result*."""

    async def run(agent_, input_, **kwargs):
        if sink is not None:
            sink["agent"] = agent_
            sink["input"] = input_
            sink["kwargs"] = kwargs
        if exc is not None:
            raise exc
        return result

    return run


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch, tmp_path):
    """Every test that reaches _ensure() builds a real SQLiteSession; keep
    its tables out of the owner's real data/cfo.db."""
    monkeypatch.setattr(agent_openai.db, "DB_PATH", tmp_path / "openai_test.db")


def _make_runtime(chat_id=999, on_session_id=None):
    return OpenAIRuntime(_LINKS, chat_id=chat_id, on_session_id=on_session_id)


# ---------------------------------------------------------------------------
# Shape: BaseRuntime subclass, hooks implemented, does not wire ask()
# ---------------------------------------------------------------------------

class TestShape:
    def test_is_a_base_runtime_subclass(self):
        assert issubclass(OpenAIRuntime, agent.BaseRuntime)

    def test_implements_all_three_hooks(self):
        for name in ("_run_query", "_ensure", "_reset_session"):
            assert getattr(OpenAIRuntime, name) is not getattr(agent.BaseRuntime, name), (
                f"{name} was not overridden")

    def test_does_not_define_its_own_ask(self):
        # ask() must stay inherited from BaseRuntime -- a runtime-local ask()
        # is exactly the drift Step 13 exists to prevent.
        assert OpenAIRuntime.ask is agent.BaseRuntime.ask

    def test_max_turns_exceeds_sdk_default_of_10(self):
        assert MAX_TURNS > 10

    def test_construction_does_not_touch_the_database(self, monkeypatch):
        # _ensure() is never called here -- construction must stay lazy
        # (brief Decision 4), so nothing should build a SQLiteSession yet.
        def _boom(*a, **kw):
            raise AssertionError("SQLiteSession must not be built at construction")
        monkeypatch.setattr(agent_openai, "SQLiteSession", _boom)
        rt = _make_runtime()
        assert rt._session is None
        assert rt._agent is None


# ---------------------------------------------------------------------------
# _ensure -- lazy, idempotent, reports the session id
# ---------------------------------------------------------------------------

class TestEnsure:
    def test_ensure_builds_session_and_agent_once(self):
        rt = _make_runtime()
        _run(rt._ensure())
        assert rt._session is not None
        assert rt._agent is not None

    def test_ensure_is_idempotent(self):
        rt = _make_runtime()
        _run(rt._ensure())
        session1, agent1 = rt._session, rt._agent
        _run(rt._ensure())
        assert rt._session is session1
        assert rt._agent is agent1

    def test_ensure_reports_session_id_via_callback(self):
        seen = []
        rt = OpenAIRuntime(_LINKS, chat_id=555, on_session_id=seen.append)
        _run(rt._ensure())
        assert seen == ["chat:555"]
        assert rt.session_id == "chat:555"


# ---------------------------------------------------------------------------
# One turn, via _guarded_turn (BaseRuntime) so a reentrant-_LOCK regression
# in _run_query would hang this test rather than pass silently -- exactly
# the hazard the brief's "THE ONE HAZARD" section warns about.
# ---------------------------------------------------------------------------

class TestRunQuery:
    def test_returns_text_from_final_output(self, monkeypatch):
        monkeypatch.setattr(agent_openai.Runner, "run", _fake_runner_run(
            result=_FakeResult("hello from openai",
                               Usage(input_tokens=10, output_tokens=5, total_tokens=15))))
        rt = _make_runtime()
        text, images = _run(rt._guarded_turn("hi"))
        assert text == "hello from openai"
        assert images == []

    def test_final_output_none_becomes_empty_string(self, monkeypatch):
        monkeypatch.setattr(agent_openai.Runner, "run", _fake_runner_run(
            result=_FakeResult(None, Usage(input_tokens=1, output_tokens=1, total_tokens=2))))
        rt = _make_runtime()
        text, _ = _run(rt._guarded_turn("hi"))
        assert text == ""

    def test_max_turns_is_passed_to_runner_run(self, monkeypatch):
        sink: dict = {}
        monkeypatch.setattr(agent_openai.Runner, "run", _fake_runner_run(
            result=_FakeResult("ok"), sink=sink))
        rt = _make_runtime()
        _run(rt._guarded_turn("hi"))
        assert sink["kwargs"]["max_turns"] == MAX_TURNS

    def test_usage_recorded_against_openai_not_claude(self, monkeypatch):
        recorded: dict = {}
        monkeypatch.setattr(agent_openai.Runner, "run", _fake_runner_run(
            result=_FakeResult("ok", Usage(input_tokens=3, output_tokens=4, total_tokens=7))))
        monkeypatch.setattr(_usage, "record",
                            lambda service, tokens: recorded.update(service=service, tokens=tokens))
        rt = _make_runtime()
        _run(rt._guarded_turn("hi"))
        assert recorded["service"] == "openai"
        assert recorded["service"] != "claude"
        assert recorded["tokens"] == 7

    def test_usage_falls_back_to_count_tokens_when_absent(self, monkeypatch):
        recorded: dict = {}
        monkeypatch.setattr(agent_openai.Runner, "run", _fake_runner_run(
            result=_FakeResult("a short reply", usage=None)))
        monkeypatch.setattr(_usage, "record",
                            lambda service, tokens: recorded.update(service=service, tokens=tokens))
        rt = _make_runtime()
        _run(rt._guarded_turn("hi"))
        assert recorded["tokens"] > 0   # local context.count_tokens estimate, never 0

    def test_images_staged_by_a_tool_are_drained(self, monkeypatch):
        async def run(agent_, input_, **kwargs):
            agent_openai._PENDING.append("/tmp/chart.png")
            return _FakeResult("see chart")
        monkeypatch.setattr(agent_openai.Runner, "run", run)
        rt = _make_runtime()
        text, images = _run(rt._guarded_turn("chart please"))
        assert images == ["/tmp/chart.png"]
        assert agent_openai._PENDING == []   # drained, not just copied

    def test_runner_exception_yields_a_turn_string_not_a_raise(self, monkeypatch):
        monkeypatch.setattr(agent_openai.Runner, "run", _fake_runner_run(
            exc=RuntimeError("FallbackModel: every openai link was skipped")))
        rt = _make_runtime()
        text, images = _run(rt._guarded_turn("hi"))
        assert "FallbackModel: every openai link was skipped" in text
        assert images == []


# ---------------------------------------------------------------------------
# _reset_session -- clears the session, rebuilds the agent
# ---------------------------------------------------------------------------

class TestResetSession:
    def test_reset_session_clears_the_session_and_rebuilds_the_agent(self):
        rt = _make_runtime()
        _run(rt._ensure())
        old_agent = rt._agent
        cleared = {}

        async def fake_clear():
            cleared["called"] = True
        rt._session.clear_session = fake_clear

        _run(rt._reset_session())
        assert cleared.get("called") is True
        assert rt._agent is not None
        assert rt._agent is not old_agent


# ---------------------------------------------------------------------------
# close -- pops source registries, closes the session, never raises
# ---------------------------------------------------------------------------

class TestClose:
    def test_close_closes_the_session(self):
        rt = _make_runtime()
        _run(rt._ensure())
        closed = {}
        rt._session.close = lambda: closed.setdefault("called", True)
        _run(rt.close())
        assert closed.get("called") is True

    def test_close_before_ensure_never_raises(self):
        rt = _make_runtime()
        _run(rt.close())   # no _ensure() call -- self._session is still None

    def test_close_swallows_a_session_close_error(self):
        rt = _make_runtime()
        _run(rt._ensure())

        def _boom():
            raise RuntimeError("disk full")
        rt._session.close = _boom
        _run(rt.close())   # must not propagate
