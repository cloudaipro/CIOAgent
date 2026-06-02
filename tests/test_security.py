"""
test_security.py — offline security regression tests.

Covers the path-traversal / pickle-sink hardening on stock symbols and the
filename sanitizer used by the Telegram report writer.
"""
from __future__ import annotations

import asyncio
import os

import pytest


# ---------------------------------------------------------------------------
# Symbol sanitization (stock cache pickle sink)
# ---------------------------------------------------------------------------

class TestSafeSymbol:
    def test_legit_tickers_pass_unchanged(self):
        from cio.stock.data import safe_symbol
        for sym in ("AAPL", "2330.TW", "3293.TWO", "^GSPC", "BRK-B", "ES=F"):
            assert safe_symbol(sym) == sym

    def test_strips_path_separators(self):
        from cio.stock.data import safe_symbol
        # Slashes removed; "etcpasswd" remains (harmless, stays in the cache dir).
        assert "/" not in safe_symbol("../../etc/passwd")
        assert "/" not in safe_symbol("a/b/c")
        assert ".." not in safe_symbol("../../etc/passwd")  # leading dots stripped

    def test_empty_or_dotonly_after_sanitize_raises(self):
        from cio.stock.data import safe_symbol
        # Anything that reduces to nothing (incl. pure-dot traversal) is rejected.
        for bad in ("../", "/", "...", "..", "   ", "\x00"):
            with pytest.raises(ValueError):
                safe_symbol(bad)

    def test_cache_path_never_escapes_cache_dir(self, monkeypatch, tmp_path):
        from cio.stock import data
        monkeypatch.setattr(data, "STOCK_CACHE_DIR", str(tmp_path))
        # A hostile symbol must resolve inside the cache dir or raise — never write
        # to /tmp/evil or load an arbitrary pickle outside the cache.
        for hostile in ("../../evil", "..%2f..%2fevil", "/abs/evil", "a/../../b"):
            try:
                p = data._cache_path(hostile)
            except ValueError:
                continue  # rejected outright — fine
            root = os.path.realpath(str(tmp_path))
            assert os.path.commonpath([os.path.realpath(p), root]) == root

    def test_legit_cache_path_inside_dir(self, monkeypatch, tmp_path):
        from cio.stock import data
        monkeypatch.setattr(data, "STOCK_CACHE_DIR", str(tmp_path))
        p = data._cache_path("AAPL")
        assert p == os.path.join(str(tmp_path), "AAPL.pkl")


# ---------------------------------------------------------------------------
# Bot filename sanitizer + access-control gate
# ---------------------------------------------------------------------------

class TestBotSafeName:
    def test_safe_name_strips_traversal(self):
        from cio.bot import _safe_name
        assert "/" not in _safe_name("../../etc/cron.d/x")
        assert _safe_name("AAPL") == "AAPL"
        assert _safe_name("") == "report"          # fallback
        assert _safe_name("../") == "report"


class TestAccessGate:
    def _fake_update(self, chat_id):
        class _Chat:
            id = chat_id
        class _Upd:
            effective_chat = _Chat()
        return _Upd()

    def test_gate_blocks_unauthorized_when_allowlist_set(self, monkeypatch):
        import cio.bot as bot
        monkeypatch.setattr(bot, "ALLOWED_CHATS", {111})
        from telegram.ext import ApplicationHandlerStop
        with pytest.raises(ApplicationHandlerStop):
            asyncio.run(bot._gate(self._fake_update(999), None))

    def test_gate_allows_authorized(self, monkeypatch):
        import cio.bot as bot
        monkeypatch.setattr(bot, "ALLOWED_CHATS", {111})
        # Authorized chat → returns without raising.
        asyncio.run(bot._gate(self._fake_update(111), None))

    def test_gate_open_when_no_allowlist(self, monkeypatch):
        import cio.bot as bot
        monkeypatch.setattr(bot, "ALLOWED_CHATS", set())
        # No allowlist → never blocks (back-compat).
        asyncio.run(bot._gate(self._fake_update(999), None))
