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

    def test_cio_chain_has_three_links(self):
        """resolve_chain('cio') → [openai/claude/nim] with two limits."""
        from cio.committee.models import resolve_chain
        chain = resolve_chain("cio")
        assert len(chain) == 3
        svcs = [link["service"] for link in chain]
        assert svcs == ["openai", "claude", "nim"]
        assert chain[0].get("daily_limit") == 200000
        assert chain[1].get("daily_limit") == 200000
        assert "daily_limit" not in chain[2]

    def test_single_service_role_is_one_link(self):
        """resolve_chain('market') → 1-link list with no daily_limit."""
        from cio.committee.models import resolve_chain
        chain = resolve_chain("market")
        assert len(chain) == 1
        assert chain[0]["service"] == "nim"
        assert "daily_limit" not in chain[0]

    def test_unknown_role_falls_back_to_nim(self):
        """resolve_chain for an unknown role → 1 nim link."""
        from cio.committee.models import resolve_chain
        chain = resolve_chain("xyz_unknown")
        assert len(chain) == 1
        assert chain[0]["service"] == "nim"


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
    """Verify ask_role walks the CIO chain correctly: budget + empty-result fallthrough."""

    def setup_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def teardown_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    @pytest.fixture(autouse=True)
    def patch_usage_db(self, monkeypatch, tmp_path):
        """Each test gets an isolated DB so usage counters don't bleed."""
        self.db = tmp_path / "chain_test.db"
        monkeypatch.setattr("cio.committee.usage.DB_PATH", self.db)

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
        """Pre-seeded openai at limit → skipped; CIO call hits claude."""
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
        """Both openai and claude at limit → CIO call hits nim (no limit)."""
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
        assert len(calls["openai"]) == 1
        assert len(calls["claude"]) == 1
        assert len(calls["nim"]) == 1

    def test_usage_is_recorded_for_successful_link(self, monkeypatch):
        """Tokens from the successful link are persisted in usage DB."""
        calls = self._patch_backends(monkeypatch, ("openai-answer", 350), ("", 0), ("", 0))

        from cio.committee.engine import ask_role
        from cio.committee.usage import used_today
        _run(ask_role("sys", "user", role_key="cio"))
        assert used_today("openai", db_path=self.db) == 350

    def test_specialist_single_link_uses_nim(self, monkeypatch):
        """A specialist role (e.g. market) dispatches to nim via its 1-link chain."""
        calls = self._patch_backends(monkeypatch, ("openai-answer", 100), ("claude-answer", 100), ("nim-answer", 50))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="market"))
        assert result == "nim-answer"
        assert len(calls["nim"]) == 1
        assert len(calls["openai"]) == 0
        assert len(calls["claude"]) == 0

    def test_explicit_service_override_bypasses_chain(self, monkeypatch):
        """Explicit service='claude' single-dispatches; chain is not used."""
        calls = self._patch_backends(monkeypatch, ("openai-answer", 100), ("forced-claude", 100), ("nim-answer", 100))

        from cio.committee.engine import ask_role
        result = _run(ask_role("sys", "user", role_key="cio", service="claude"))
        assert result == "forced-claude"
        assert len(calls["claude"]) == 1
        assert len(calls["openai"]) == 0
        assert len(calls["nim"]) == 0
