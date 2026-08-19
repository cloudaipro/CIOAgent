"""Tests for the dedicated Telegram swing-scan workflow.

All market work is stubbed: these tests verify routing, lifecycle persistence,
cancellation plumbing, and report semantics without network or model calls.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from cio import db, swing
from cio.alpha.engine import AlphaResult, ScanCancelled
from cio.alpha import engine


def _result(*, status="GREEN", candidates=None):
    return AlphaResult(
        run_date="2026-08-18",
        regime={"status": status, "detail": "test regime"},
        candidates=candidates or [],
        universe_size=40,
    )


@pytest.fixture(autouse=True)
def _clean_jobs():
    with swing._LOCK:
        swing._JOBS.clear()
        swing._ACTIVE_BY_CHAT.clear()
    yield
    with swing._LOCK:
        swing._JOBS.clear()
        swing._ACTIVE_BY_CHAT.clear()


def test_report_is_traditional_chinese_and_marks_market_action():
    result = _result(candidates=[{
        "rank": 1, "ticker": "NVDA", "final": 91.2,
        "momentum": 88, "trend": 100, "earnings": 84,
    }])
    text = swing.format_report(
        result,
        {"threshold": 80, "watchlist_name": "Alpha-2026-08-18"},
    )
    assert "今日波段候選" in text
    assert "NVDA" in text and "91.2" in text
    assert "選擇性" in text
    assert "Alpha-2026-08-18" in text
    assert "不是自動下單" in text


def test_red_regime_does_not_present_candidates_as_a_new_entry_signal():
    text = swing.format_report(
        _result(status="RED", candidates=[{"rank": 1, "ticker": "AAPL", "final": 90}]),
        {"threshold": 80},
    )
    assert "不建議今天新開波段部位" in text
    assert "AAPL" in text


def test_alpha_scan_honours_cooperative_cancellation():
    with pytest.raises(ScanCancelled):
        engine.run(cancel_check=lambda: True)


def test_job_runs_without_a_codex_turn_and_persists_status(tmp_path, monkeypatch):
    result = _result(candidates=[{"rank": 1, "ticker": "AMD", "final": 86}])
    calls = []

    def fake_reuse(**kwargs):
        calls.append(kwargs)
        return result, {"run_id": 17, "threshold": 80,
                        "watchlist_name": "Alpha-2026-08-18"}

    monkeypatch.setattr(swing.alpha, "reuse_today_or_run", fake_reuse)
    db_path = tmp_path / "swing.db"
    job = swing.create_job(123, language="tc", db_path=db_path)
    done = swing.execute(job.job_id, db_path=db_path)

    assert done is job
    assert job.status == "completed"
    assert job.result is result
    assert calls and calls[0]["publish"] is True
    assert callable(calls[0]["cancel_check"])
    conn = db.connect(db_path)
    row = conn.execute(
        "SELECT status, run_id, language FROM swing_jobs WHERE job_id=?",
        (job.job_id,),
    ).fetchone()
    conn.close()
    assert dict(row) == {"status": "completed", "run_id": 17, "language": "tc"}


def test_cancelled_job_sets_event_and_does_not_complete(tmp_path, monkeypatch):
    result = _result(candidates=[])
    seen = {}

    def fake_reuse(**kwargs):
        seen["check"] = kwargs["cancel_check"]
        raise ScanCancelled("stop")

    monkeypatch.setattr(swing.alpha, "reuse_today_or_run", fake_reuse)
    db_path = tmp_path / "swing-cancel.db"
    job = swing.create_job(456, db_path=db_path)
    swing.cancel_job(job.job_id, db_path=db_path)
    done = swing.execute(job.job_id, db_path=db_path)

    assert done.status == "cancelled"
    assert job.cancel_event.is_set()
    assert "check" not in seen  # queued cancellation never starts the worker


# --- tiny Telegram fakes for direct-intent routing -------------------------
@dataclass
class _Chat:
    id: int = 789

    async def send_action(self, action):
        pass


class _Message:
    def __init__(self, chat, text="今天有哪些適合進場做波段操作"):
        self.chat = chat
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


@dataclass
class _Update:
    message: _Message = field(default_factory=lambda: _Message(_Chat()))

    @property
    def effective_chat(self):
        return self.message.chat

    @property
    def effective_message(self):
        return self.message


@dataclass
class _Ctx:
    args: list[str] = field(default_factory=list)


@pytest.mark.parametrize("text", [
    "今天有哪些適合進場做波段操作",
    (
        "我沒有要你做波段掃描，我是問你問題：為什麼之前問你"
        "「今天有哪些適合進場做波段操作」，你只推薦三支？"
    ),
    (
        "為什麼我之前問你說「今天有哪些適合進場做波段操作」，或者要求你做 "
        "swing refresh 的時候，你給我的推薦股票只有三支；但後來又能找其他股票？"
    ),
])
def test_every_natural_language_message_reaches_the_agent(text, monkeypatch):
    import cio.bot as bot

    prompts = []

    async def fail_swing(update, ctx):
        raise AssertionError("ordinary text must never call cmd_swing before the LLM")

    async def fake_run(update, prompt):
        prompts.append(prompt)

    monkeypatch.setattr(bot, "cmd_swing", fail_swing)
    monkeypatch.setattr(bot, "_run", fake_run)
    update = _Update(message=_Message(_Chat(), text))
    asyncio.run(bot.on_text(update, _Ctx()))

    assert prompts == [text]


def test_llm_confirmed_handoff_invokes_cmd_swing(monkeypatch):
    import cio.bot as bot

    class _Agent:
        session_id = "test-session"

        async def ask(self, prompt):
            assert prompt == "請強制重新掃描今天的波段候選"
            return "I will hand this off.", [], []

        def take_actions(self):
            return [{"command": "swing", "language": "tc", "refresh": True}]

    calls = []
    logged = []

    async def fake_swing(update, ctx):
        calls.append(list(ctx.args))

    monkeypatch.setattr(bot, "_agent", lambda chat_id: _Agent())
    monkeypatch.setattr(bot, "cmd_swing", fake_swing)
    monkeypatch.setattr(bot.memory, "log_turn", lambda *args: logged.append(args))
    text = "請強制重新掃描今天的波段候選"
    update = _Update(message=_Message(_Chat(), text))

    asyncio.run(bot.on_text(update, _Ctx()))

    assert calls == [["zh", "refresh"]]
    assert logged and logged[0][2] == text
    assert update.message.replies == []  # cmd_swing owns all user-visible replies


def test_request_swing_scan_tool_records_a_narrow_handoff():
    import cio.agent as agent

    tool = next(t for t in agent.CIO_TOOLS if t.name == "request_swing_scan")
    agent._PENDING_ACTIONS.clear()
    try:
        asyncio.run(tool.handler({"language": "en", "refresh": True}))
        assert agent._PENDING_ACTIONS == [{
            "command": "swing",
            "language": "en",
            "refresh": True,
        }]
    finally:
        agent._PENDING_ACTIONS.clear()


def test_swing_command_completes_without_constructing_an_agent(monkeypatch):
    import cio.bot as bot

    result = _result(candidates=[{
        "rank": 1, "ticker": "MSFT", "final": 87,
        "momentum": 82, "trend": 100, "earnings": 79,
    }])
    monkeypatch.setenv("CIO_RICH_MESSAGES", "0")
    monkeypatch.setattr(swing, "_persist", lambda *args, **kwargs: None)

    def fake_execute(job_id):
        job = swing.get_job(job_id)
        job.result = result
        job.meta = {"threshold": 80}
        return swing._set_status(job, "completed")

    monkeypatch.setattr(bot.swing, "execute", fake_execute)
    monkeypatch.setattr(bot, "_agent",
                        lambda chat_id: pytest.fail("swing must bypass _agent"))
    update = _Update()
    asyncio.run(bot.cmd_swing(update, _Ctx()))

    assert any("已啟動今日波段掃描" in r for r in update.message.replies)
    assert any("MSFT" in r and "今日波段候選" in r
               for r in update.message.replies)
    assert bot._running.get(update.effective_chat.id) in (None, set())
