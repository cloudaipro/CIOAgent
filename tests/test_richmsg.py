"""
test_richmsg.py — offline tests for the Telegram Rich Messages sender.

Rich Messages (Bot API 10.1) have no python-telegram-bot binding, so
cio.richmsg POSTs sendRichMessage over raw HTTP. These tests prove:
  * the success path returns True and posts the exact {chat_id, rich_message:
    {markdown}} body to the sendRichMessage endpoint with the bot token
  * an API rejection (ok=False or non-200) returns False (caller falls back)
  * a transport error returns False and never raises
  * the CIO_RICH_MESSAGES=0 kill switch short-circuits before any HTTP call
  * oversize text (> 32768 chars), a missing token, and empty text all return
    False without an HTTP call

No network: httpx.AsyncClient is monkeypatched with a fake that records the
request and returns a scripted response.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

import cio.richmsg as richmsg


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeBot:
    token: str = "123:ABC"


@dataclass
class FakeResponse:
    status_code: int = 200
    _json: dict = field(default_factory=lambda: {"ok": True, "result": {}})

    def json(self) -> dict:
        return self._json


class FakeClient:
    """Stand-in for httpx.AsyncClient. Records the last POST; scriptable."""

    last_url: str | None = None
    last_json: Any = None

    def __init__(self, response: FakeResponse | None = None,
                 raises: bool = False, **_kw) -> None:
        self._response = response or FakeResponse()
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        FakeClient.last_url = url
        FakeClient.last_json = json
        if self._raises:
            raise RuntimeError("boom")
        return self._response


def _patch_client(monkeypatch, **kw):
    FakeClient.last_url = None
    FakeClient.last_json = None
    monkeypatch.setattr(richmsg.httpx, "AsyncClient",
                        lambda **_: FakeClient(**kw))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_success_posts_markdown_body(monkeypatch):
    monkeypatch.setenv("CIO_RICH_MESSAGES", "1")
    _patch_client(monkeypatch)
    ok = asyncio.run(richmsg.send_rich_text(FakeBot(), 42, "# Report\n**bold**"))
    assert ok is True
    assert FakeClient.last_url == \
        "https://api.telegram.org/bot123:ABC/sendRichMessage"
    assert FakeClient.last_json == {
        "chat_id": 42,
        "rich_message": {"markdown": "# Report\n**bold**"},
    }


def test_api_rejection_returns_false(monkeypatch):
    monkeypatch.setenv("CIO_RICH_MESSAGES", "1")
    _patch_client(monkeypatch, response=FakeResponse(
        status_code=400, _json={"ok": False, "description": "bad markdown"}))
    ok = asyncio.run(richmsg.send_rich_text(FakeBot(), 1, "x"))
    assert ok is False


def test_transport_error_returns_false(monkeypatch):
    monkeypatch.setenv("CIO_RICH_MESSAGES", "1")
    _patch_client(monkeypatch, raises=True)
    ok = asyncio.run(richmsg.send_rich_text(FakeBot(), 1, "x"))
    assert ok is False  # must swallow, never raise


def test_kill_switch_skips_http(monkeypatch):
    monkeypatch.setenv("CIO_RICH_MESSAGES", "0")
    _patch_client(monkeypatch)
    ok = asyncio.run(richmsg.send_rich_text(FakeBot(), 1, "x"))
    assert ok is False
    assert FakeClient.last_url is None  # no HTTP attempted


def test_oversize_skips_http(monkeypatch):
    monkeypatch.setenv("CIO_RICH_MESSAGES", "1")
    _patch_client(monkeypatch)
    ok = asyncio.run(richmsg.send_rich_text(FakeBot(), 1, "a" * 32769))
    assert ok is False
    assert FakeClient.last_url is None


def test_missing_token_skips_http(monkeypatch):
    monkeypatch.setenv("CIO_RICH_MESSAGES", "1")
    _patch_client(monkeypatch)
    ok = asyncio.run(richmsg.send_rich_text(FakeBot(token=""), 1, "x"))
    assert ok is False
    assert FakeClient.last_url is None


def test_empty_markdown_skips_http(monkeypatch):
    monkeypatch.setenv("CIO_RICH_MESSAGES", "1")
    _patch_client(monkeypatch)
    ok = asyncio.run(richmsg.send_rich_text(FakeBot(), 1, ""))
    assert ok is False
    assert FakeClient.last_url is None


def test_reply_rich_uses_message_chat(monkeypatch):
    monkeypatch.setenv("CIO_RICH_MESSAGES", "1")
    _patch_client(monkeypatch)

    class FakeMessage:
        chat_id = 7
        def get_bot(self):
            return FakeBot()

    ok = asyncio.run(richmsg.reply_rich(FakeMessage(), "**hi**"))
    assert ok is True
    assert FakeClient.last_json["chat_id"] == 7
