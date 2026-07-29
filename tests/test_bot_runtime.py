"""
test_bot_runtime.py — cio.bot_runtime: the runtime-selection seam for the
general Telegram bot chat (Architect brief Step 10 / migration plan build
order 1-2; Step 11 wired the seam into cio.bot._agent() and unified chain
resolution behind models.bot_chat_chain(), migration plan build order 3).

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
    """Force models.bot_chat_chain() to return *links* for this test, without
    touching the real config/committee_models.yaml or dashboard_settings.json."""
    monkeypatch.setattr(bot_runtime.models, "bot_chat_chain", lambda: links)


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
        assert "claude limit-latched; falling through" in caplog.text
        # Proves the WALK moved past claude to openai. Since Step 16 the openai
        # link is honoured rather than skipped, so the proof is the runtime TYPE:
        # landing on OpenAIRuntime cannot happen by dispatching the latched
        # claude link anyway.
        assert type(rt).__name__ == "OpenAIRuntime"

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
        assert "claude at daily limit; falling through" in caplog.text
        assert type(rt).__name__ == "OpenAIRuntime"

    def test_openai_link_is_honoured(self, monkeypatch, caplog):
        """Since Step 16 a surviving openai link builds an OpenAIRuntime.

        Replaces the Step 11-15 scaffold, which skipped the link and logged
        that no OpenAI runtime existed yet — deliberately loud rather than
        silently answering on Claude while claiming to honour the operator's
        choice. The runtime now exists, so the link is honoured for real.
        """
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        _set_chain(monkeypatch, [{"service": "openai", "model": "gpt-5.6-terra"}])
        rt = bot_runtime.select_runtime(chat_id=106)
        assert type(rt).__name__ == "OpenAIRuntime"
        # The walk stopped at the openai link, so the "nothing survived" tail
        # must NOT have been reached.
        assert "every bot_chat link was skipped" not in caplog.text

    def test_openai_runtime_receives_every_openai_link_from_here_on(self, monkeypatch):
        """FallbackModel walks per model call, so it must get the openai links
        BEHIND the selected one too — otherwise a rate limit on the head has
        nothing to fall back to (the defect 14a fixed one layer down)."""
        _set_chain(monkeypatch, [
            {"service": "claude", "model": "claude-opus-4-8"},
            {"service": "openai", "model": "gpt-5.6-terra"},
            {"service": "openai", "model": "gpt-5.4-mini"},
        ])
        from cio.committee import engine
        engine.latch("claude")          # force the walk past the claude head
        rt = bot_runtime.select_runtime(chat_id=115)
        assert type(rt).__name__ == "OpenAIRuntime"
        assert [l["model"] for l in rt._links] == ["gpt-5.6-terra", "gpt-5.4-mini"]

    def test_claude_link_behind_an_openai_link_is_still_reachable(self, monkeypatch, caplog):
        """An openai link that cannot be built falls through to a claude link
        behind it, rather than ending the walk."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra"},
            {"service": "claude", "model": "claude-opus-4-8"},
        ])
        # Make OpenAIRuntime construction fail so the fall-through path runs.
        import cio.agent_openai as ao
        monkeypatch.setattr(ao, "OpenAIRuntime",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        rt = bot_runtime.select_runtime(chat_id=114)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "could not build OpenAIRuntime" in caplog.text
        assert "every bot_chat link was skipped" not in caplog.text

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

    def test_bot_chat_chain_exception_falls_back_to_claude(self, monkeypatch):
        """select_runtime's own defensive layer: even if bot_chat_chain() (which
        has its own internal fallback/never-raises contract, Decision 1) somehow
        still raised, select_runtime must not propagate it. Only the FIRST call
        raises: ClaudeRuntime's own construction also calls bot_chat_chain()
        (via build_options -> bot_chat_model()) for the SDK model string, an
        orthogonal, expected second call this test isn't exercising."""
        calls = {"n": 0}

        def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("config exploded")
            return [{"service": "claude", "model": "claude-opus-4-8"}]
        monkeypatch.setattr(bot_runtime.models, "bot_chat_chain", _flaky)
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
    def test_claude_override_bypasses_chain_resolution(self, monkeypatch, caplog):
        """CIO_BOT_ENGINE=claude skips select_runtime's own chain WALK (the
        skip/latch/budget loop) even when the chain would behave very
        differently if actually walked. (ClaudeRuntime's own construction
        separately consults bot_chat_chain() -- via build_options() ->
        bot_chat_model() -- for the specific Claude model string; that is an
        orthogonal, expected call this test does not guard against.)"""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        monkeypatch.setenv("CIO_BOT_ENGINE", "claude")
        _set_chain(monkeypatch, [{"service": "nim", "model": "n1"}])

        rt = bot_runtime.select_runtime(chat_id=112)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        # The chain walk itself never ran: no skip/fall-through log from it.
        assert "nim link skipped" not in caplog.text
        assert "falling through" not in caplog.text

    def test_unrecognized_override_is_ignored(self, monkeypatch, caplog):
        """Any value other than claude|openai logs a warning and normal chain
        resolution proceeds."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        monkeypatch.setenv("CIO_BOT_ENGINE", "gemini")
        _set_chain(monkeypatch, [{"service": "claude", "model": "m"}])
        rt = bot_runtime.select_runtime(chat_id=113)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "not recognized" in caplog.text

    def test_openai_override_forces_the_openai_runtime(self, monkeypatch):
        """CIO_BOT_ENGINE=openai takes the openai links regardless of chain
        order — the debugging escape hatch D6 describes."""
        monkeypatch.setenv("CIO_BOT_ENGINE", "openai")
        _set_chain(monkeypatch, [
            {"service": "claude", "model": "claude-opus-4-8"},
            {"service": "openai", "model": "gpt-5.6-terra"},
        ])
        rt = bot_runtime.select_runtime(chat_id=116)
        assert type(rt).__name__ == "OpenAIRuntime"

    def test_openai_override_without_an_openai_link_says_so(self, monkeypatch, caplog):
        """An override that cannot be satisfied must not be honoured silently."""
        caplog.set_level(logging.INFO, logger="cio.bot_runtime")
        monkeypatch.setenv("CIO_BOT_ENGINE", "openai")
        _set_chain(monkeypatch, [{"service": "claude", "model": "claude-opus-4-8"}])
        rt = bot_runtime.select_runtime(chat_id=117)
        assert isinstance(rt, bot_runtime.ClaudeRuntime)
        assert "no usable openai link" in caplog.text


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
# models.bot_chat_chain (Decision 1 / locked D5: the /configure selection is
# the single source of truth select_runtime and bot_chat_link both derive from)
# ---------------------------------------------------------------------------

class TestBotChatChain:
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

    def test_dashboard_selection_wins_over_yaml_agents_bot_chat(self, tmp_path, monkeypatch):
        """The operator's /configure pick wins over agents.bot_chat.chain even
        when both name a real, different chain (the contradiction Richard's
        Step 10 review escalated)."""
        self._write_settings(tmp_path, {"bot_chat_chain": "operator-pick"})
        self._write_models_yaml(tmp_path, monkeypatch, """
agents:
  bot_chat: {chain: yaml-default}
chains:
  operator-pick:
  - {service: claude, model: from-dashboard}
  yaml-default:
  - {service: claude, model: from-yaml}
""")
        assert _models.bot_chat_chain() == [{"service": "claude", "model": "from-dashboard"}]

    def test_unknown_dashboard_name_falls_back_to_yaml_chain(self, tmp_path, monkeypatch):
        """A /configure name that isn't a real chain setting isn't trusted --
        falls through to resolve_chain('bot_chat') (the yaml agents.bot_chat.chain),
        same as an unset selection."""
        self._write_settings(tmp_path, {"bot_chat_chain": "does-not-exist"})
        self._write_models_yaml(tmp_path, monkeypatch, """
agents:
  bot_chat: {chain: yaml-default}
chains:
  yaml-default:
  - {service: claude, model: from-yaml}
""")
        assert _models.bot_chat_chain() == [{"service": "claude", "model": "from-yaml"}]
        assert _models.bot_chat_chain() == _models.resolve_chain("bot_chat")

    def test_bot_chat_chain_and_bot_chat_link_agree(self, tmp_path, monkeypatch):
        """bot_chat_link() takes its links from bot_chat_chain() so the two
        can't resolve to different chains -- the defect Step 10's reviewer
        escalated. Its resolved link must be a member of the chain."""
        self._write_settings(tmp_path, {"bot_chat_chain": "mix"})
        self._write_models_yaml(tmp_path, monkeypatch, """
chains:
  mix:
  - {service: openai, model: gpt-x}
  - {service: claude, model: opus-test}
""")
        chain = _models.bot_chat_chain()
        link = _models.bot_chat_link()
        assert chain == [{"service": "openai", "model": "gpt-x"},
                          {"service": "claude", "model": "opus-test"}]
        assert link in chain
        assert link == {"service": "claude", "model": "opus-test"}


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


# ---------------------------------------------------------------------------
# cio.bot._agent() -- wired through the runtime seam (Decisions 2 and 4)
# ---------------------------------------------------------------------------

class TestAgentSeam:
    """_agent() now selects a runtime through bot_runtime.select_runtime()
    instead of constructing CIOAgent directly, caching one runtime per chat
    exactly as before (locked decision D4: choose at session start/roll, then
    pin). Memory calls are stubbed so these never touch the real data/cio.db."""

    @pytest.fixture(autouse=True)
    def _clean_agent_cache(self, monkeypatch):
        import cio.bot as bot_mod
        bot_mod._agents.clear()
        monkeypatch.setattr(bot_mod.memory, "touch_chat", lambda chat_id: None)
        monkeypatch.setattr(bot_mod.memory, "get_session_id", lambda chat_id: None)
        monkeypatch.setattr(bot_mod.memory, "set_session_id", lambda chat_id, sid: None)
        yield
        bot_mod._agents.clear()

    def test_agent_returns_and_caches_a_runtime(self, monkeypatch):
        """A second call for the same chat returns the SAME object -- the
        per-chat cache (and therefore warm()/session survival) is unchanged."""
        import cio.bot as bot_mod
        _set_chain(monkeypatch, [{"service": "claude", "model": "m"}])
        rt1 = bot_mod._agent(9001)
        rt2 = bot_mod._agent(9001)
        assert rt1 is rt2
        assert isinstance(rt1, bot_runtime.ClaudeRuntime)

    def test_select_runtime_exception_still_yields_a_working_agent(self, monkeypatch):
        """R2: a routing bug in select_runtime must never take the bot down --
        _agent() degrades to constructing CIOAgent directly."""
        import cio.bot as bot_mod

        def _boom(chat_id, *, resume=None, on_session_id=None):
            raise RuntimeError("routing exploded")
        monkeypatch.setattr(bot_mod, "select_runtime", _boom)

        rt = bot_mod._agent(9002)
        assert isinstance(rt, bot_mod.CIOAgent)
        assert bot_mod._agent(9002) is rt   # still cached despite the fallback path


# ---------------------------------------------------------------------------
# reselect_needed -- the narrow escape hatch from the D4 pin
# ---------------------------------------------------------------------------

class _FakeRuntime:
    """Stands in for a pinned runtime: reselect_needed only reads _service."""
    def __init__(self, service): self._service = service


def _budget_db(monkeypatch, tmp_path, service, spent):
    """Point cio.committee.usage at a throwaway db with *spent* tokens already
    recorded against *service*, so over_budget() can be driven from a test."""
    from cio.committee import usage as _usage
    db = tmp_path / "usage.db"
    monkeypatch.setattr(_usage, "DB_PATH", db)
    _usage.record(service, spent, db_path=db)


class TestReselectNeeded:
    def test_false_while_the_pinned_service_can_still_answer(self, monkeypatch, tmp_path):
        _budget_db(monkeypatch, tmp_path, "openai", 1)   # not the real day's usage
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra", "daily_limit": 120000},
            {"service": "claude", "model": "claude-opus-5"},
        ])
        assert bot_runtime.reselect_needed(_FakeRuntime("openai")) is False

    def test_true_when_the_pinned_service_is_over_budget(self, monkeypatch, tmp_path):
        """The stranded-chat bug: openai crossed its daily limit AFTER the pin,
        FallbackModel holds only openai links, so the chain's claude link is
        unreachable until something unpins the runtime."""
        _budget_db(monkeypatch, tmp_path, "openai", 200)
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra", "daily_limit": 100},
            {"service": "claude", "model": "claude-opus-5"},
        ])
        assert bot_runtime.reselect_needed(_FakeRuntime("openai")) is True

    def test_true_when_the_pinned_service_is_latched(self, monkeypatch):
        from cio.committee import engine
        engine.latch("openai")
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra"},
            {"service": "claude", "model": "claude-opus-5"},
        ])
        assert bot_runtime.reselect_needed(_FakeRuntime("openai")) is True

    def test_false_when_no_other_service_could_answer_either(self, monkeypatch):
        """Both dead: moving buys nothing, and select_runtime would return an
        over-budget Claude anyway. Stay pinned rather than churn the session."""
        from cio.committee import engine
        engine.latch("openai")
        engine.latch("claude")
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra"},
            {"service": "claude", "model": "claude-opus-5"},
        ])
        assert bot_runtime.reselect_needed(_FakeRuntime("openai")) is False

    def test_false_when_the_only_alternative_is_nim(self, monkeypatch):
        """Bot chat never routes to nim (44-tool surface), so a nim link does
        not count as a usable alternative."""
        from cio.committee import engine
        engine.latch("openai")
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra"},
            {"service": "nim", "model": "moonshotai/kimi-k2.6"},
        ])
        assert bot_runtime.reselect_needed(_FakeRuntime("openai")) is False

    def test_engine_override_disables_reselection(self, monkeypatch):
        """CIO_BOT_ENGINE is honoured over budget, so it must also pin."""
        from cio.committee import engine
        engine.latch("openai")
        monkeypatch.setenv("CIO_BOT_ENGINE", "openai")
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra"},
            {"service": "claude", "model": "claude-opus-5"},
        ])
        assert bot_runtime.reselect_needed(_FakeRuntime("openai")) is False

    def test_chain_lookup_failure_keeps_the_current_runtime(self, monkeypatch):
        def _boom():
            raise RuntimeError("config exploded")
        monkeypatch.setattr(bot_runtime.models, "bot_chat_chain", _boom)
        assert bot_runtime.reselect_needed(_FakeRuntime("openai")) is False

    def test_unknown_transport_is_left_alone(self, monkeypatch):
        _set_chain(monkeypatch, [{"service": "claude", "model": "claude-opus-5"}])
        assert bot_runtime.reselect_needed(_FakeRuntime("something-else")) is False


class TestAgentEviction:
    """cio.bot._agent() acts on reselect_needed: evict the stranded runtime,
    re-select from the chain, and queue the user-facing notice."""

    @pytest.fixture(autouse=True)
    def _clean_agent_cache(self, monkeypatch):
        import cio.bot as bot_mod
        bot_mod._agents.clear()
        bot_mod._switch_notice.clear()
        monkeypatch.setattr(bot_mod.memory, "touch_chat", lambda chat_id: None)
        monkeypatch.setattr(bot_mod.memory, "get_session_id", lambda chat_id: None)
        monkeypatch.setattr(bot_mod.memory, "set_session_id", lambda chat_id, sid: None)
        yield
        bot_mod._agents.clear()
        bot_mod._switch_notice.clear()

    def test_stranded_runtime_is_replaced_and_the_user_is_told(self, monkeypatch):
        import cio.bot as bot_mod
        from cio.committee import engine
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra"},
            {"service": "claude", "model": "claude-opus-5"},
        ])
        first = bot_mod._agent(9101)
        assert type(first).__name__ == "OpenAIRuntime"

        engine.latch("openai")            # budget/limit hit mid-session
        second = bot_mod._agent(9101)
        assert second is not first
        assert isinstance(second, bot_runtime.ClaudeRuntime)
        assert 9101 in bot_mod._switch_notice

    def test_healthy_runtime_is_still_pinned(self, monkeypatch):
        """D4 is intact: no eviction while the pinned service can answer, and
        no spurious notice."""
        import cio.bot as bot_mod
        _set_chain(monkeypatch, [
            {"service": "openai", "model": "gpt-5.6-terra"},
            {"service": "claude", "model": "claude-opus-5"},
        ])
        first = bot_mod._agent(9102)
        assert bot_mod._agent(9102) is first
        assert 9102 not in bot_mod._switch_notice
