"""
test_bot_runtime.py — cio.bot_runtime: the runtime-selection seam for the
general Telegram bot chat (Architect brief Step 10 / migration plan build
order 1-2).

Offline, no real LLM calls, no network. select_runtime() constructs real
ClaudeRuntime (== CIOAgent) instances — construction alone never connects
(only warm()/ask() do), the same premise tests/test_day_roll.py and friends
already rely on — so these tests assert on the returned object's type and
identity rather than mocking construction.

Because this step ships exactly one runtime, every surviving link currently
resolves to the SAME ClaudeRuntime type regardless of which link survived —
so the return value alone can't prove *which* link won. Tests that need to
distinguish "skipped the head link and fell through" from "used the head link
directly" assert on the select_runtime log output (caplog) as well.
"""
from __future__ import annotations

import logging

import pytest

from cio import bot_runtime
from cio.committee import models as _models


def _set_chain(monkeypatch, links):
    """Force models.resolve_chain('bot_chat') to return *links* for this test,
    without touching the real config/committee_models.yaml."""
    monkeypatch.setattr(
        bot_runtime.models, "resolve_chain",
        lambda role_key: links if role_key == "bot_chat" else [])


@pytest.fixture(autouse=True)
def _clean_engine_env(monkeypatch):
    monkeypatch.delenv("CIO_BOT_ENGINE", raising=False)


# ---------------------------------------------------------------------------
# BotRuntime protocol
# ---------------------------------------------------------------------------

class TestBotRuntimeProtocol:
    def test_claude_runtime_satisfies_the_protocol(self, monkeypatch):
        _set_chain(monkeypatch, [{"service": "claude", "model": "claude-opus-4-8"}])
        rt = bot_runtime.select_runtime(chat_id=101)
        assert isinstance(rt, bot_runtime.BotRuntime)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)

    def test_claude_runtime_is_cioagent(self):
        """Decision 2: same class, same file — not a new wrapper type."""
        import cio.agent as agent
        assert bot_runtime.ClaudeRuntime is agent.CIOAgent


# ---------------------------------------------------------------------------
# select_runtime — chain walk / skip rules
# ---------------------------------------------------------------------------

class TestSelectRuntimeChainWalk:
    def test_healthy_claude_head_is_used_directly(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        _set_chain(monkeypatch, [{"service": "claude", "model": "claude-opus-4-8"}])
        rt = bot_runtime.select_runtime(chat_id=102)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        # No skip fired -- the head link was used as-is.
        assert "skipped" not in caplog.text
        assert "falling through" not in caplog.text

    def test_nim_link_skipped(self, monkeypatch, caplog):
        """Bot chat is claude+openai only: a nim link is skipped even though
        the committee itself would happily dispatch to it."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        _set_chain(monkeypatch, [
            {"service": "nim", "model": "moonshotai/kimi-k2.6"},
            {"service": "claude", "model": "claude-opus-4-8"},
        ])
        rt = bot_runtime.select_runtime(chat_id=103)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "nim link skipped" in caplog.text

    def test_latched_service_skipped(self, monkeypatch, caplog):
        """A limit-latched head link falls through to the next link."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        from cio.committee import engine
        engine.latch("claude")
        _set_chain(monkeypatch, [
            {"service": "claude", "model": "claude-opus-4-8"},
            {"service": "openai", "model": "gpt-5.6-terra"},
        ])
        rt = bot_runtime.select_runtime(chat_id=104)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "claude limit-latched; falling through" in caplog.text
        # Proves the WALK moved past claude to openai, rather than coincidentally
        # landing on ClaudeRuntime by dispatching the latched claude link anyway.
        assert "openai link selected but no OpenAI runtime yet" in caplog.text

    def test_over_budget_link_skipped(self, monkeypatch, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        from cio.committee import usage as _usage
        db = tmp_path / "usage.db"
        monkeypatch.setattr(_usage, "DB_PATH", db)
        _usage.record("claude", 100, db_path=db)
        assert _usage.over_budget("claude", 50, db_path=db) is True   # sanity

        _set_chain(monkeypatch, [
            {"service": "claude", "model": "claude-opus-4-8", "daily_limit": 50},
            {"service": "openai", "model": "gpt-5.6-terra"},
        ])
        rt = bot_runtime.select_runtime(chat_id=105)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "claude at daily limit; falling through" in caplog.text
        assert "openai link selected but no OpenAI runtime yet" in caplog.text

    def test_openai_link_falls_back_to_claude_runtime(self, monkeypatch, caplog):
        """No OpenAIRuntime exists in this step (STEP-11): a surviving openai
        link still answers the turn, on Claude."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        _set_chain(monkeypatch, [{"service": "openai", "model": "gpt-5.6-terra"}])
        rt = bot_runtime.select_runtime(chat_id=106)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "openai link selected but no OpenAI runtime yet; using Claude" in caplog.text

    def test_all_links_skipped_still_returns_a_runtime(self, monkeypatch, caplog):
        """Budget/latch are advisory: a chat that cannot answer is worse than
        one that answers over budget."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        from cio.committee import engine
        engine.latch("claude")
        engine.latch("openai")
        _set_chain(monkeypatch, [
            {"service": "nim", "model": "moonshotai/kimi-k2.6"},
            {"service": "claude", "model": "claude-opus-4-8"},
            {"service": "openai", "model": "gpt-5.6-terra"},
        ])
        rt = bot_runtime.select_runtime(chat_id=107)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "every bot_chat link was skipped" in caplog.text

    def test_empty_chain_returns_a_runtime(self, monkeypatch):
        _set_chain(monkeypatch, [])
        rt = bot_runtime.select_runtime(chat_id=108)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)

    def test_malformed_link_is_skipped_not_fatal(self, monkeypatch, caplog):
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        _set_chain(monkeypatch, ["not-a-dict", {"service": "claude", "model": "m"}])
        rt = bot_runtime.select_runtime(chat_id=109)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "malformed link" in caplog.text

    def test_resolve_chain_exception_falls_back_to_claude(self, monkeypatch):
        def _boom(role_key):
            raise RuntimeError("config exploded")
        monkeypatch.setattr(bot_runtime.models, "resolve_chain", _boom)
        rt = bot_runtime.select_runtime(chat_id=110)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)

    def test_latch_lookup_exception_falls_through_link(self, monkeypatch, caplog):
        """A throwing latch/budget lookup degrades that link away rather than
        crashing the whole turn (Standing Rule R2)."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        from cio.committee import engine as _engine
        monkeypatch.setattr(_engine, "is_latched",
                            lambda svc: (_ for _ in ()).throw(RuntimeError("boom")))
        _set_chain(monkeypatch, [
            {"service": "claude", "model": "claude-opus-4-8"},
            {"service": "openai", "model": "gpt-5.6-terra"},
        ])
        rt = bot_runtime.select_runtime(chat_id=111)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "error evaluating link" in caplog.text


# ---------------------------------------------------------------------------
# CIO_BOT_ENGINE override
# ---------------------------------------------------------------------------

class TestEngineOverride:
    def test_claude_override_bypasses_chain_resolution(self, monkeypatch):
        monkeypatch.setenv("CIO_BOT_ENGINE", "claude")

        def _must_not_be_called(role_key):
            raise AssertionError("resolve_chain must not be called under CIO_BOT_ENGINE=claude")
        monkeypatch.setattr(bot_runtime.models, "resolve_chain", _must_not_be_called)

        rt = bot_runtime.select_runtime(chat_id=112)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)

    def test_unrecognized_override_is_ignored(self, monkeypatch, caplog):
        """Any value other than 'claude' logs a warning and normal chain
        resolution proceeds (Step 11 adds 'openai')."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        monkeypatch.setenv("CIO_BOT_ENGINE", "openai")
        _set_chain(monkeypatch, [{"service": "claude", "model": "m"}])
        rt = bot_runtime.select_runtime(chat_id=113)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "not recognized" in caplog.text


# ---------------------------------------------------------------------------
# models.bot_chat_link / bot_chat_model
# ---------------------------------------------------------------------------

class TestBotChatLink:
    """bot_chat_link() replaces the claude-link gate: an OpenAI-only chain no
    longer resolves to nothing. bot_chat_model()'s own narrower contract (a
    Claude SDK model, for ClaudeAgentOptions) is unchanged."""

    @pytest.fixture(autouse=True)
    def fresh_models(self, monkeypatch, tmp_path):
        _models.load_config.cache_clear()
        monkeypatch.setattr("cio.dashboard.settings._PATH",
                            tmp_path / "dashboard_settings.json")
        monkeypatch.delenv("CIO_MODEL", raising=False)
        monkeypatch.delenv("CFO_MODEL", raising=False)

    def _write_settings(self, tmp_path, data):
        import json
        path = tmp_path / "dashboard_settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh)

    def _write_models_yaml(self, tmp_path, monkeypatch, yaml_content):
        path = tmp_path / "committee_models.yaml"
        path.write_text(yaml_content)
        monkeypatch.setenv("CIO_MODELS_CONFIG", str(path))
        _models.load_config.cache_clear()

    def test_openai_only_chain_no_longer_returns_none(self, tmp_path, monkeypatch):
        """The bug this replaces: an operator's OpenAI-only chain used to make
        the resolver return None, i.e. silently do nothing."""
        self._write_settings(tmp_path, {"bot_chat_chain": "oa-only"})
        self._write_models_yaml(tmp_path, monkeypatch, """
chains:
  oa-only:
  - {service: openai, model: gpt-x}
  - {service: nim, model: n1}
""")
        assert _models.bot_chat_link() == {"service": "openai", "model": "gpt-x"}
        # bot_chat_model's contract (a Claude SDK model only) is unchanged.
        assert _models.bot_chat_model() is None

    def test_claude_link_present_wins_even_out_of_position(self, tmp_path, monkeypatch):
        self._write_settings(tmp_path, {"bot_chat_chain": "mix"})
        self._write_models_yaml(tmp_path, monkeypatch, """
chains:
  mix:
  - {service: openai, model: gpt-x}
  - {service: claude, model: opus-test}
""")
        assert _models.bot_chat_link() == {"service": "claude", "model": "opus-test"}
        assert _models.bot_chat_model() == "opus-test"

    def test_env_lock_returns_none(self, tmp_path, monkeypatch):
        self._write_settings(tmp_path, {"bot_chat_chain": "oa-only"})
        self._write_models_yaml(tmp_path, monkeypatch, """
chains:
  oa-only:
  - {service: openai, model: gpt-x}
""")
        monkeypatch.setenv("CIO_MODEL", "claude-haiku-4-5-20251001")
        assert _models.bot_chat_link() is None
        assert _models.bot_chat_model() is None


# ---------------------------------------------------------------------------
# committee/engine.py public aliases (Decision 5)
# ---------------------------------------------------------------------------

class TestEnginePublicAliases:
    """bot_runtime needs these outside the committee package; the underscore
    names stay because existing tests patch them directly. Same objects, not
    wrappers, so a monkeypatch of either name is visible through the other's
    module attribute lookup."""

    def test_aliases_are_the_same_objects(self):
        from cio.committee import engine
        assert engine.latch is engine._latch
        assert engine.is_latched is engine._latched
        assert engine.is_limit_notice is engine._is_limit_notice
        assert engine.capture_call is engine._capture
