"""
test_bot_committee.py — offline seam tests for the /committee Telegram command.

Uses a tiny fake Update/Message/Chat stack — no network, no LLM, no live bot.
Monkeypatches cio.committee.run_committee and cio.committee.build_report.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Minimal Telegram object fakes (recording stubs)
# ---------------------------------------------------------------------------

@dataclass
class FakeChat:
    id: int = 12345
    _actions_sent: list[str] = field(default_factory=list)

    async def send_action(self, action) -> None:
        self._actions_sent.append(str(action))


@dataclass
class FakeMessage:
    chat: FakeChat = field(default_factory=FakeChat)
    _replies: list[str] = field(default_factory=list)
    _documents: list[Any] = field(default_factory=list)

    async def reply_text(self, text: str, **kwargs) -> None:
        self._replies.append(text)

    async def reply_document(self, fh, filename: str = "", **kwargs) -> None:
        self._documents.append({"fh": fh, "filename": filename})


@dataclass
class FakeUpdate:
    message: FakeMessage = field(default_factory=FakeMessage)

    @property
    def effective_chat(self) -> FakeChat:
        return self.message.chat


@dataclass
class FakeCtx:
    args: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fake CommitteeResult
# ---------------------------------------------------------------------------

@dataclass
class FakeResult:
    error: str | None = None
    resolved: str = "AAPL"
    as_of: str = "2026-06-01"
    bundle: dict = field(default_factory=dict)
    opinions: list = field(default_factory=list)
    consensus: dict = field(default_factory=lambda: {"agreement_score": 75})
    vote_tally: dict = field(default_factory=lambda: {"buy_count": 3, "hold_count": 2, "sell_count": 1})
    cio: dict = field(default_factory=lambda: {
        "final_recommendation": "Buy",
        "confidence_score": 68,
    })
    round1_opinions: list = field(default_factory=list)
    debate: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_update():
    return FakeUpdate(message=FakeMessage(chat=FakeChat()))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCmdCommitteeNoArg:
    def test_no_arg_replies_usage(self, tmp_path, monkeypatch):
        """No symbol argument → reply with usage instructions, no LLM call."""
        monkeypatch.setattr("cio.bot.UPLOAD_DIR", tmp_path)

        # Patch run_committee to blow up if called — it must NOT be called here
        async def _boom(sym):
            raise AssertionError("run_committee should not be called on no-arg")

        monkeypatch.setattr("cio.committee.run_committee", _boom)

        from cio.bot import cmd_committee

        update = _make_update()
        ctx = FakeCtx(args=[])
        _run(cmd_committee(update, ctx))

        assert len(update.message._replies) == 1
        reply = update.message._replies[0]
        assert "Usage" in reply or "usage" in reply
        assert "/committee" in reply
        assert len(update.message._documents) == 0


class TestCmdCommitteeNoData:
    def test_error_result_sends_no_data_message(self, tmp_path, monkeypatch):
        """result.error set → single 'No data' reply, no document."""
        monkeypatch.setattr("cio.bot.UPLOAD_DIR", tmp_path)

        async def _no_data(sym):
            return FakeResult(error="no data for FAKE999", cio={}, vote_tally={}, consensus={})

        monkeypatch.setattr("cio.committee.run_committee", _no_data)
        monkeypatch.setattr("cio.committee.build_report", lambda sym, r: "# Report")

        from cio.bot import cmd_committee

        update = _make_update()
        ctx = FakeCtx(args=["FAKE999"])
        _run(cmd_committee(update, ctx))

        # Ack + no-data message; no document
        replies = update.message._replies
        assert any("No data" in r or "no data" in r.lower() or "FAKE999" in r for r in replies)
        assert len(update.message._documents) == 0


class TestCmdCommitteeGoodResult:
    def test_good_result_sends_document_and_summary(self, tmp_path, monkeypatch):
        """Good result → reply_document called once + summary reply_text with recommendation."""
        monkeypatch.setattr("cio.bot.UPLOAD_DIR", tmp_path)

        good_result = FakeResult()

        async def _good(sym):
            return good_result

        monkeypatch.setattr("cio.committee.run_committee", _good)
        monkeypatch.setattr("cio.committee.build_report", lambda sym, r: "# Committee Report for AAPL\n\nContent here.")

        from cio.bot import cmd_committee

        update = _make_update()
        ctx = FakeCtx(args=["AAPL"])
        _run(cmd_committee(update, ctx))

        # Document uploaded exactly once
        assert len(update.message._documents) == 1
        doc = update.message._documents[0]
        assert "AAPL" in doc["filename"]
        assert doc["filename"].endswith(".md")

        # Summary text message contains the final recommendation
        replies = update.message._replies
        assert any("Buy" in r or "recommendation" in r.lower() for r in replies), (
            f"Expected 'Buy' in one of the replies: {replies}"
        )

    def test_ack_message_sent_before_run(self, tmp_path, monkeypatch):
        """The ack message (committee convening notice) is sent before the report."""
        monkeypatch.setattr("cio.bot.UPLOAD_DIR", tmp_path)

        call_order: list[str] = []

        async def _good(sym):
            call_order.append("run_committee")
            return FakeResult()

        def _report(sym, r):
            return "# Report"

        monkeypatch.setattr("cio.committee.run_committee", _good)
        monkeypatch.setattr("cio.committee.build_report", _report)

        from cio.bot import cmd_committee

        update = _make_update()
        ctx = FakeCtx(args=["AAPL"])

        # Intercept reply_text to track order
        original_reply = update.message.reply_text

        async def _tracking_reply(text: str, **kwargs):
            call_order.append(f"reply:{text[:30]}")
            await original_reply(text, **kwargs)

        update.message.reply_text = _tracking_reply

        _run(cmd_committee(update, ctx))

        # Ack must come before run_committee is called
        ack_idx = next((i for i, e in enumerate(call_order) if e.startswith("reply:") and "Convening" in e), None)
        run_idx = next((i for i, e in enumerate(call_order) if e == "run_committee"), None)
        assert ack_idx is not None, f"Ack message not found in call_order: {call_order}"
        assert run_idx is not None
        assert ack_idx < run_idx, "Ack must be sent before run_committee"

    def test_exception_in_run_committee_does_not_crash_bot(self, tmp_path, monkeypatch):
        """An exception from run_committee must be caught; bot must not crash."""
        monkeypatch.setattr("cio.bot.UPLOAD_DIR", tmp_path)

        async def _boom(sym):
            raise RuntimeError("LLM exploded")

        monkeypatch.setattr("cio.committee.run_committee", _boom)

        from cio.bot import cmd_committee

        update = _make_update()
        ctx = FakeCtx(args=["AAPL"])

        # Must not raise
        try:
            _run(cmd_committee(update, ctx))
        except Exception as e:
            import pytest
            pytest.fail(f"cmd_committee raised {e} instead of catching it")

        # Should have sent an error message
        assert any("error" in r.lower() or "⚠️" in r for r in update.message._replies)

    def test_symbol_uppercased(self, tmp_path, monkeypatch):
        """Symbol from args is uppercased before use."""
        monkeypatch.setattr("cio.bot.UPLOAD_DIR", tmp_path)

        received_sym: list[str] = []

        async def _capture(sym):
            received_sym.append(sym)
            return FakeResult(resolved=sym.upper())

        monkeypatch.setattr("cio.committee.run_committee", _capture)
        monkeypatch.setattr("cio.committee.build_report", lambda sym, r: "# Report")

        from cio.bot import cmd_committee

        update = _make_update()
        ctx = FakeCtx(args=["aapl"])
        _run(cmd_committee(update, ctx))

        assert received_sym == ["AAPL"]

    def test_summary_under_700_chars(self, tmp_path, monkeypatch):
        """The summary text message must be under 700 characters."""
        monkeypatch.setattr("cio.bot.UPLOAD_DIR", tmp_path)

        async def _good(sym):
            return FakeResult()

        monkeypatch.setattr("cio.committee.run_committee", _good)
        monkeypatch.setattr("cio.committee.build_report", lambda sym, r: "# Report")

        from cio.bot import cmd_committee

        update = _make_update()
        ctx = FakeCtx(args=["AAPL"])
        _run(cmd_committee(update, ctx))

        # The summary is NOT the ack and NOT the document — it's the last text reply
        # after the document is sent. Filter out ack by checking it doesn't mention "Convening".
        summary_replies = [
            r for r in update.message._replies
            if "Convening" not in r and "Usage" not in r and "No data" not in r
        ]
        assert summary_replies, "No summary reply found"
        # All non-ack, non-error replies should be under 700 chars
        for r in summary_replies:
            assert len(r) < 700, f"Summary too long ({len(r)} chars): {r[:100]}"


class TestCmdCommitteeGuardedFields:
    def test_missing_cio_fields_render_na(self, tmp_path, monkeypatch):
        """When cio/vote_tally fields are absent, summary shows 'N/A' not an exception."""
        monkeypatch.setattr("cio.bot.UPLOAD_DIR", tmp_path)

        sparse_result = FakeResult(cio={}, vote_tally={}, consensus={})

        async def _sparse(sym):
            return sparse_result

        monkeypatch.setattr("cio.committee.run_committee", _sparse)
        monkeypatch.setattr("cio.committee.build_report", lambda sym, r: "# Report")

        from cio.bot import cmd_committee

        update = _make_update()
        ctx = FakeCtx(args=["AAPL"])

        try:
            _run(cmd_committee(update, ctx))
        except Exception as e:
            import pytest
            pytest.fail(f"cmd_committee raised {e} with sparse result")

        all_text = " ".join(update.message._replies)
        assert "N/A" in all_text
