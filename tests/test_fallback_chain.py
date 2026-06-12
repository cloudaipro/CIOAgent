"""
test_fallback_chain.py — Offline tests for Step 8: OpenAI backend + CIO token-budget
fallback chain.

No real LLM calls, no real network calls.  All backends are monkeypatched.
``DB_PATH`` in usage.py is monkeypatched to a tmp_path so tests never touch
the real committee.db.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A. usage.py — record / used_today / over_budget
# ---------------------------------------------------------------------------

class TestUsageModule:
    """All DB writes go to a temp file via DB_PATH monkeypatch."""

    @pytest.fixture(autouse=True)
    def patch_db(self, monkeypatch, tmp_path):
        """Redirect DB_PATH to an isolated temp DB for every test."""
        self.db = tmp_path / "test_usage.db"
        monkeypatch.setattr("cio.committee.usage.DB_PATH", self.db)

    def test_record_and_used_today_accumulate(self):
        from cio.committee.usage import record, used_today
        record("openai", 500, db_path=self.db)
        record("openai", 300, db_path=self.db)
        assert used_today("openai", db_path=self.db) == 800

    def test_different_day_is_independent(self):
        from cio.committee.usage import record, used_today
        record("openai", 1000, day="2026-01-01", db_path=self.db)
        record("openai", 500, day="2026-01-02", db_path=self.db)
        assert used_today("openai", day="2026-01-01", db_path=self.db) == 1000
        assert used_today("openai", day="2026-01-02", db_path=self.db) == 500

    def test_different_service_is_independent(self):
        from cio.committee.usage import record, used_today
        record("openai", 100, db_path=self.db)
        record("claude", 200, db_path=self.db)
        assert used_today("openai", db_path=self.db) == 100
        assert used_today("claude", db_path=self.db) == 200

    def test_used_today_missing_returns_zero(self):
        from cio.committee.usage import used_today
        assert used_today("nim", db_path=self.db) == 0

    def test_record_ignores_zero_tokens(self):
        from cio.committee.usage import record, used_today
        record("openai", 0, db_path=self.db)
        record("openai", -5, db_path=self.db)
        assert used_today("openai", db_path=self.db) == 0

    def test_over_budget_true_at_limit(self):
        from cio.committee.usage import record, over_budget
        record("openai", 200000, db_path=self.db)
        assert over_budget("openai", 200000, db_path=self.db) is True

    def test_over_budget_true_above_limit(self):
        from cio.committee.usage import record, over_budget
        record("openai", 200001, db_path=self.db)
        assert over_budget("openai", 200000, db_path=self.db) is True

    def test_over_budget_false_under_limit(self):
        from cio.committee.usage import record, over_budget
        record("openai", 199999, db_path=self.db)
        assert over_budget("openai", 200000, db_path=self.db) is False

    def test_over_budget_false_when_limit_none(self):
        from cio.committee.usage import record, over_budget
        record("nim", 999999, db_path=self.db)
        assert over_budget("nim", None, db_path=self.db) is False

    def test_over_budget_false_with_no_usage(self):
        from cio.committee.usage import over_budget
        assert over_budget("openai", 200000, db_path=self.db) is False


# ---------------------------------------------------------------------------
# B. models.py — resolve_chain
# ---------------------------------------------------------------------------

class TestResolveChain:
    """resolve_chain reads the real YAML (or built-in fallback)."""

    def setup_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def teardown_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    # NOTE: link order / models / daily_limits inside each setting are
    # operator-tunable from the dashboard, so these repo-yaml tests assert the
    # MECHANISM (named resolution, structure) — never specific operator picks.
    # Content semantics are pinned in TestNamedChainConfig / TestChainSelection.

    def test_cio_resolves_to_named_setting_with_three_links(self):
        """cio resolves to a NAMED chain setting whose links match chains()."""
        from cio.committee.models import resolve_chain, resolve_chain_name, chains, SERVICES
        name = resolve_chain_name("cio")
        chain = resolve_chain("cio")
        assert name is not None
        assert chain == chains()[name]
        assert len(chain) == 3
        assert all(link["service"] in SERVICES and link["model"] for link in chain)

    def test_specialist_uses_named_chain(self):
        """Every specialist resolves to a named setting (no legacy inline left)."""
        from cio.committee.models import resolve_chain, resolve_chain_name, chains
        for role in ("market", "macro", "equity", "industry", "valuation",
                     "quant", "etf", "risk", "catalyst", "moderator"):
            name = resolve_chain_name(role)
            assert name is not None, role
            assert resolve_chain(role) == chains()[name]

    def test_unknown_role_falls_back_to_default_chain(self):
        """An unknown role resolves to the defaults.chain setting."""
        from cio.committee.models import load_config, resolve_chain, resolve_chain_name, chains
        default_name = load_config().get("defaults", {}).get("chain")
        assert isinstance(default_name, str)
        assert resolve_chain_name("xyz_unknown") == default_name
        assert resolve_chain("xyz_unknown") == chains()[default_name]

    def test_translator_uses_named_chain(self):
        """translator routes through a named 3-link setting too."""
        from cio.committee.models import resolve_chain, resolve_chain_name, chains
        name = resolve_chain_name("translator")
        assert name is not None
        chain = resolve_chain("translator")
        assert chain == chains()[name]
        assert len(chain) == 3

    def test_resolve_returns_chain_head(self):
        """resolve(role) == (service, model) of the chain's first link."""
        from cio.committee.models import resolve, resolve_chain
        for role in ("cio", "market", "translator"):
            head = resolve_chain(role)[0]
            assert resolve(role) == (head["service"], head["model"])

    def test_chain_names_nonempty_and_cover_agents(self):
        from cio.committee.models import chain_names, resolve_chain_name
        names = set(chain_names())
        assert names
        for role in ("cio", "wma", "market", "translator"):
            assert resolve_chain_name(role) in names


# ---------------------------------------------------------------------------
# B2. models.py — named chain settings (custom yaml)
# ---------------------------------------------------------------------------

class TestNamedChainConfig:
    """Named-chain resolution against a temp yaml (CIO_MODELS_CONFIG)."""

    def setup_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def teardown_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def _write(self, tmp_path, monkeypatch, text: str) -> None:
        p = tmp_path / "models.yaml"
        p.write_text(text, encoding="utf-8")
        monkeypatch.setenv("CIO_MODELS_CONFIG", str(p))
        from cio.committee.models import load_config
        load_config.cache_clear()

    def test_agent_resolves_named_chain(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, """
chains:
  cheap:
  - {service: nim, model: m1, daily_limit: 5}
  - {service: nim, model: m2}
  - {service: claude, model: c1}
defaults: {chain: cheap}
agents:
  market: {chain: cheap}
""")
        from cio.committee.models import resolve_chain, resolve_chain_name
        chain = resolve_chain("market")
        assert [l["model"] for l in chain] == ["m1", "m2", "c1"]
        assert chain[0]["daily_limit"] == 5
        assert "daily_limit" not in chain[1]
        assert resolve_chain_name("market") == "cheap"

    def test_unknown_chain_name_falls_back_to_defaults(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, """
chains:
  good:
  - {service: claude, model: ok}
defaults: {chain: good}
agents:
  market: {chain: nonexistent}
""")
        from cio.committee.models import resolve_chain
        chain = resolve_chain("market")
        assert [l["model"] for l in chain] == ["ok"]

    def test_legacy_inline_chain_still_works(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, """
defaults: {service: claude, model: dft}
agents:
  cio:
    chain:
    - {service: openai, model: g1, daily_limit: 100}
    - {service: nim, model: n1}
""")
        from cio.committee.models import resolve_chain, resolve_chain_name
        chain = resolve_chain("cio")
        assert [l["model"] for l in chain] == ["g1", "n1"]
        assert chain[0]["daily_limit"] == 100
        assert resolve_chain_name("cio") is None  # inline, not named

    def test_legacy_service_model_agent_still_works(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, """
defaults: {service: claude, model: dft}
agents:
  market: {service: nim, model: legacy-m}
""")
        from cio.committee.models import resolve_chain, resolve
        chain = resolve_chain("market")
        assert chain == [{"service": "nim", "model": "legacy-m"}]
        assert resolve("market") == ("nim", "legacy-m")

    def test_legacy_defaults_service_model(self, tmp_path, monkeypatch):
        """No chains anywhere → old defaults {service, model} still resolve."""
        self._write(tmp_path, monkeypatch, """
defaults: {service: claude, model: old-default}
agents: {}
""")
        from cio.committee.models import resolve_chain
        chain = resolve_chain("market")
        assert chain == [{"service": "claude", "model": "old-default"}]

    def test_empty_config_never_returns_empty_chain(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, "agents: {}\n")
        from cio.committee.models import resolve_chain
        chain = resolve_chain("anything")
        assert chain and chain[0]["service"] == "claude"

    def test_chains_helper_normalizes(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, """
chains:
  raw:
  - {service: nim}
  - not-a-dict
  - {model: only-model, daily_limit: "12"}
defaults: {chain: raw}
agents: {}
""")
        from cio.committee.models import chains
        links = chains()["raw"]
        assert len(links) == 2                      # non-dict link skipped
        assert links[0] == {"service": "nim", "model": "claude-opus-4-8"}
        assert links[1]["daily_limit"] == 12        # str → int


# ---------------------------------------------------------------------------
# C. _ask_openai — monkeypatched AsyncOpenAI, no real network
# ---------------------------------------------------------------------------

class TestAskOpenAI:
    """Tests for the _ask_openai backend."""

    def setup_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def teardown_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def test_returns_text_and_tokens_with_key(self, monkeypatch):
        """_ask_openai returns (text, tokens) when OPENAI_API_KEY is set."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test")

        # Build a fake response object mimicking the openai SDK structure
        fake_usage = MagicMock()
        fake_usage.total_tokens = 77
        fake_choice = MagicMock()
        fake_choice.message.content = "openai answer"
        fake_resp = MagicMock()
        fake_resp.choices = [fake_choice]
        fake_resp.usage = fake_usage

        # Fake AsyncOpenAI: constructor records args, create returns fake_resp
        constructed = []

        class FakeAsyncOpenAI:
            def __init__(self, api_key, base_url):
                constructed.append((api_key, base_url))

            @property
            def chat(self):
                return self

            @property
            def completions(self):
                return self

            async def create(self, **kwargs):
                return fake_resp

        monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

        from cio.committee.engine import _ask_openai
        text, tok = _run(_ask_openai("sys", "user", model="gpt-5.5-2026-04-23"))
        assert text == "openai answer"
        assert tok == 77
        assert len(constructed) == 1
        assert constructed[0][0] == "sk-fake-test"

    def test_returns_empty_when_key_unset(self, monkeypatch):
        """_ask_openai returns ('', 0) and does NOT construct AsyncOpenAI if key is missing."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        constructed = []

        class ShouldNotConstruct:
            def __init__(self, *a, **kw):
                constructed.append(True)
                raise AssertionError("AsyncOpenAI should not be constructed without API key")

        monkeypatch.setattr("openai.AsyncOpenAI", ShouldNotConstruct)

        from cio.committee.engine import _ask_openai
        text, tok = _run(_ask_openai("sys", "user"))
        assert text == ""
        assert tok == 0
        assert not constructed

    def test_returns_empty_on_api_error(self, monkeypatch):
        """_ask_openai returns ('', 0) on any API exception."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test")

        class FakeAsyncOpenAI:
            def __init__(self, *a, **kw):
                pass

            @property
            def chat(self):
                return self

            @property
            def completions(self):
                return self

            async def create(self, **kwargs):
                raise RuntimeError("API error")

        monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

        from cio.committee.engine import _ask_openai
        text, tok = _run(_ask_openai("sys", "user"))
        assert text == ""
        assert tok == 0


# ---------------------------------------------------------------------------
# D. Chain selection — ask_role dispatches by budget
# ---------------------------------------------------------------------------

class TestChainSelection:
    """Verify ask_role walks the chain correctly: budget + empty-result fallthrough.

    Uses a FIXED temp yaml (not the operator-tunable repo config) so the
    daily_limit semantics under test cannot drift with config edits."""

    def setup_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def teardown_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    @pytest.fixture(autouse=True)
    def patch_usage_db(self, monkeypatch, tmp_path):
        """Each test gets an isolated DB so usage counters don't bleed, and a
        pinned chain config with known daily limits."""
        self.db = tmp_path / "chain_test.db"
        monkeypatch.setattr("cio.committee.usage.DB_PATH", self.db)
        cfg = tmp_path / "models.yaml"
        cfg.write_text("""
chains:
  premium:
  - {service: openai, model: gpt-5.5-2026-04-23, daily_limit: 200000}
  - {service: claude, model: claude-opus-4-8, daily_limit: 200000}
  - {service: nim, model: moonshotai/kimi-k2.6}
  standard:
  - {service: claude, model: claude-opus-4-8}
  - {service: openai, model: gpt-5.5-2026-04-23, daily_limit: 200000}
  - {service: nim, model: moonshotai/kimi-k2.6}
defaults: {chain: standard}
agents:
  market: {chain: standard}
  cio: {chain: premium}
""", encoding="utf-8")
        monkeypatch.setenv("CIO_MODELS_CONFIG", str(cfg))
        from cio.committee.models import load_config
        load_config.cache_clear()

    def _patch_backends(self, monkeypatch, openai_result, claude_result, nim_result):
        """Monkeypatch all three _ask_* backends; record which were called."""
        calls = {"openai": [], "claude": [], "nim": []}

        async def fake_openai(sp, up, model=None):
            calls["openai"].append(model)
            return openai_result

        async def fake_claude(sp, up, model=None):
            calls["claude"].append(model)
            return claude_result

        async def fake_nim(sp, up, model=None):
            calls["nim"].append(model)
            return nim_result

        monkeypatch.setattr("cio.committee.engine._ask_openai", fake_openai)
        monkeypatch.setattr("cio.committee.engine._ask_claude", fake_claude)
        monkeypatch.setattr("cio.committee.engine._ask_nim", fake_nim)
        return calls

    def test_fresh_budget_uses_openai(self, monkeypatch):
        """With all budgets fresh, CIO call hits openai (chain head)."""
        calls = self._patch_backends(monkeypatch, ("openai-answer", 100), ("claude-answer", 100), ("nim-answer", 100))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="cio"))
        assert result == "openai-answer"
        assert len(calls["openai"]) == 1
        assert len(calls["claude"]) == 0
        assert len(calls["nim"]) == 0

    def test_openai_over_budget_uses_claude(self, monkeypatch):
        """Pre-seeded openai at limit → skipped; CIO call hits claude (2nd link)."""
        from cio.committee.usage import record
        record("openai", 200000, db_path=self.db)

        calls = self._patch_backends(monkeypatch, ("openai-answer", 100), ("claude-answer", 100), ("nim-answer", 100))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="cio"))
        assert result == "claude-answer"
        assert len(calls["openai"]) == 0
        assert len(calls["claude"]) == 1
        assert len(calls["nim"]) == 0

    def test_openai_and_claude_over_budget_uses_nim(self, monkeypatch):
        """Both openai and claude at limit → CIO call hits nim (no limit, last resort)."""
        from cio.committee.usage import record
        record("openai", 200000, db_path=self.db)
        record("claude", 200000, db_path=self.db)

        calls = self._patch_backends(monkeypatch, ("openai-answer", 100), ("claude-answer", 100), ("nim-answer", 100))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="cio"))
        assert result == "nim-answer"
        assert len(calls["openai"]) == 0
        assert len(calls["claude"]) == 0
        assert len(calls["nim"]) == 1

    def test_openai_empty_result_falls_through_to_claude(self, monkeypatch):
        """When _ask_openai returns ('', 0) (key missing/error), chain continues to claude."""
        calls = self._patch_backends(monkeypatch, ("", 0), ("claude-answer", 100), ("nim-answer", 100))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="cio"))
        assert result == "claude-answer"
        assert len(calls["openai"]) == 1
        assert len(calls["claude"]) == 1
        assert len(calls["nim"]) == 0

    def test_all_empty_returns_empty_string(self, monkeypatch):
        """If every link returns empty, ask_role returns ''."""
        calls = self._patch_backends(monkeypatch, ("", 0), ("", 0), ("", 0))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="cio"))
        assert result == ""
        # All three were attempted
        assert len(calls["nim"]) == 1
        assert len(calls["claude"]) == 1
        assert len(calls["openai"]) == 1

    def test_usage_is_recorded_for_successful_link(self, monkeypatch):
        """Tokens from the successful link are persisted in usage DB."""
        calls = self._patch_backends(monkeypatch, ("", 0), ("", 0), ("nim-answer", 350))

        from cio.committee.engine import ask_role
        from cio.committee.usage import used_today
        _run(ask_role("sys", "user", role_key="cio"))
        assert used_today("nim", db_path=self.db) == 350

    def test_specialist_head_uses_claude(self, monkeypatch):
        """A specialist role (e.g. market) dispatches to claude — the head of its
        'standard' named chain — when claude answers."""
        calls = self._patch_backends(monkeypatch, ("openai-answer", 100), ("claude-answer", 100), ("nim-answer", 50))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="market"))
        assert result == "claude-answer"
        assert len(calls["claude"]) == 1
        assert len(calls["openai"]) == 0
        assert len(calls["nim"]) == 0

    def test_specialist_falls_through_to_openai(self, monkeypatch):
        """Specialists now degrade too: claude empty → openai (standard link 2)."""
        calls = self._patch_backends(monkeypatch, ("openai-answer", 100), ("", 0), ("nim-answer", 50))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="market"))
        assert result == "openai-answer"
        assert len(calls["claude"]) == 1
        assert len(calls["openai"]) == 1
        assert len(calls["nim"]) == 0

    def test_explicit_service_override_bypasses_chain(self, monkeypatch):
        """Explicit service='claude' single-dispatches; chain is not used."""
        calls = self._patch_backends(monkeypatch, ("openai-answer", 100), ("forced-claude", 100), ("nim-answer", 100))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="cio", service="claude"))
        assert result == "forced-claude"
        assert len(calls["claude"]) == 1
        assert len(calls["openai"]) == 0
        assert len(calls["nim"]) == 0


# ---------------------------------------------------------------------------
# E. Limit latch — per-service circuit breaker
# ---------------------------------------------------------------------------

class TestLimitLatch:
    """Once a backend reports a limit notice the service is latched: later
    chain dispatches skip it without spawning a backend call. Expiry is lazy —
    when the stored deadline passes, the next call probes the service again."""

    def setup_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def teardown_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    @pytest.fixture(autouse=True)
    def clean_latch(self):
        """Latch state is module-global; isolate every test."""
        from cio.committee import engine
        engine._LIMIT_LATCH.clear()
        yield
        engine._LIMIT_LATCH.clear()

    @pytest.fixture(autouse=True)
    def patch_usage_db(self, monkeypatch, tmp_path):
        """Isolated usage DB + pinned chain config (claude-headed 'standard')."""
        self.db = tmp_path / "latch_test.db"
        monkeypatch.setattr("cio.committee.usage.DB_PATH", self.db)
        cfg = tmp_path / "models.yaml"
        cfg.write_text("""
chains:
  standard:
  - {service: claude, model: claude-opus-4-8}
  - {service: openai, model: gpt-5.5-2026-04-23, daily_limit: 200000}
  - {service: nim, model: moonshotai/kimi-k2.6}
defaults: {chain: standard}
agents:
  market: {chain: standard}
""", encoding="utf-8")
        monkeypatch.setenv("CIO_MODELS_CONFIG", str(cfg))
        from cio.committee.models import load_config
        load_config.cache_clear()

    def _patch_backends(self, monkeypatch, openai_result, claude_result, nim_result):
        calls = {"openai": [], "claude": [], "nim": []}

        async def fake_openai(sp, up, model=None):
            calls["openai"].append(model)
            return openai_result

        async def fake_claude(sp, up, model=None):
            calls["claude"].append(model)
            return claude_result

        async def fake_nim(sp, up, model=None):
            calls["nim"].append(model)
            return nim_result

        monkeypatch.setattr("cio.committee.engine._ask_openai", fake_openai)
        monkeypatch.setattr("cio.committee.engine._ask_claude", fake_claude)
        monkeypatch.setattr("cio.committee.engine._ask_nim", fake_nim)
        return calls

    def test_latch_set_and_query(self):
        from cio.committee.engine import _latch, _latched
        assert _latched("claude") is False
        _latch("claude")
        assert _latched("claude") is True
        assert _latched("openai") is False

    def test_latch_lazy_expiry(self):
        import time
        from cio.committee import engine
        engine._LIMIT_LATCH["claude"] = time.monotonic() - 1  # already expired
        assert engine._latched("claude") is False

    def test_limit_notice_sets_latch(self, monkeypatch):
        """Real _ask_openai: a limit-notice reply latches the service."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test")

        fake_choice = MagicMock()
        fake_choice.message.content = "You've hit your usage limit. resets 3pm. Try again later."
        fake_resp = MagicMock()
        fake_resp.choices = [fake_choice]
        fake_resp.usage = None

        class FakeAsyncOpenAI:
            def __init__(self, *a, **kw):
                pass

            @property
            def chat(self):
                return self

            @property
            def completions(self):
                return self

            async def create(self, **kwargs):
                return fake_resp

        monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)

        from cio.committee.engine import _ask_openai, _latched
        text, tok = _run(_ask_openai("sys", "user"))
        assert text == ""
        assert _latched("openai") is True

    def test_chain_skips_latched_service(self, monkeypatch):
        """Latched claude head → chain dispatches straight to openai; claude
        backend is never invoked (no wasted subprocess)."""
        from cio.committee.engine import _latch, ask_role
        _latch("claude")
        calls = self._patch_backends(
            monkeypatch, ("openai-answer", 100), ("claude-answer", 100), ("nim-answer", 50))

        result = _run(ask_role("sys", "user", role_key="market"))
        assert result == "openai-answer"
        assert len(calls["claude"]) == 0
        assert len(calls["openai"]) == 1

    def test_expired_latch_probes_again(self, monkeypatch):
        """After the TTL deadline passes, the chain tries the service again."""
        import time
        from cio.committee import engine
        engine._LIMIT_LATCH["claude"] = time.monotonic() - 1  # expired
        calls = self._patch_backends(
            monkeypatch, ("openai-answer", 100), ("claude-answer", 100), ("nim-answer", 50))

        result = _run(engine.ask_role("sys", "user", role_key="market"))
        assert result == "claude-answer"
        assert len(calls["claude"]) == 1
        assert len(calls["openai"]) == 0
