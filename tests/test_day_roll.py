"""Tests for the day-boundary session roll.

Regression cover for the memory bug where ONE SDK session_id spanned many days, so
the agent treated a multi-day thread as "this conversation" and mis-dated an old
mistake as today's — and the rolling digest never persisted because the per-process
turn counter resets on every restart. The fix: on a new local day, if we resumed a
prior-day thread, digest + reseed BEFORE the turn. Prior days survive as the digest.
"""
import asyncio

import pytest

import cio.agent as agent
import cio.memory as memory
import cio.db as db


# --- persistence round-trip -------------------------------------------------

def _tmpdb(tmp_path):
    p = tmp_path / "roll.db"
    db.init(p)
    return p


def test_last_turn_day_roundtrip(tmp_path):
    p = _tmpdb(tmp_path)
    assert memory.get_last_turn_day(7, db_path=p) is None
    memory.set_last_turn_day(7, "2026-06-09", db_path=p)
    assert memory.get_last_turn_day(7, db_path=p) == "2026-06-09"
    # Distinct chats never collide; None chat → 'global' bucket.
    memory.set_last_turn_day(None, "2026-06-08", db_path=p)
    assert memory.get_last_turn_day(None, db_path=p) == "2026-06-08"
    assert memory.get_last_turn_day(7, db_path=p) == "2026-06-09"


# --- ask() day-boundary roll behaviour --------------------------------------

class _Dummy:
    async def disconnect(self): pass


def _make_agent(monkeypatch, *, last_day, today, session_id):
    """Build a CIOAgent with all I/O stubbed; returns (agent, calls)."""
    calls = {"checkpoint": 0, "set_day": []}
    monkeypatch.setattr(agent.timeutil, "today_local", lambda: today)
    monkeypatch.setattr(agent.memory, "get_last_turn_day", lambda *a, **k: last_day)
    monkeypatch.setattr(agent.memory, "set_last_turn_day",
                        lambda cid, day, *a, **k: calls["set_day"].append((cid, day)))
    # Never fire the end-of-turn ROLL_TURNS/ROLL_TOKENS checkpoint in these tests.
    monkeypatch.setattr(agent, "ROLL_TURNS", 10**9)
    monkeypatch.setattr(agent, "ROLL_TOKENS", 10**9)

    a = agent.CIOAgent(chat_id=1)
    a._session_id = session_id

    async def fake_run(_): return ("ok", [])
    async def fake_ensure(): return None
    async def fake_checkpoint():
        calls["checkpoint"] += 1
        a._session_id = None      # mimic the real reseed
    a._run_query = fake_run
    a._ensure = fake_ensure
    a._checkpoint = fake_checkpoint
    a._make_client = lambda resume: _Dummy()
    a._client = _Dummy()
    return a, calls


def test_rolls_on_new_day_with_resumed_session(monkeypatch):
    a, calls = _make_agent(monkeypatch, last_day="2026-06-08",
                           today="2026-06-09", session_id="sess-x")
    asyncio.run(a.ask("hi"))
    assert calls["checkpoint"] == 1                       # day boundary -> roll
    assert calls["set_day"][-1] == (1, "2026-06-09")      # today persisted


def test_no_roll_same_day(monkeypatch):
    a, calls = _make_agent(monkeypatch, last_day="2026-06-09",
                           today="2026-06-09", session_id="sess-x")
    asyncio.run(a.ask("hi"))
    assert calls["checkpoint"] == 0
    assert calls["set_day"][-1] == (1, "2026-06-09")


def test_no_roll_when_no_resumed_session(monkeypatch):
    """A failed/absent resume leaves session_id None — nothing to digest, no roll."""
    a, calls = _make_agent(monkeypatch, last_day="2026-06-08",
                           today="2026-06-09", session_id=None)
    asyncio.run(a.ask("hi"))
    assert calls["checkpoint"] == 0
    assert calls["set_day"][-1] == (1, "2026-06-09")


def test_no_roll_for_brand_new_chat(monkeypatch):
    """First-ever turn: last_turn_day is None -> never rolls."""
    a, calls = _make_agent(monkeypatch, last_day=None,
                           today="2026-06-09", session_id="sess-x")
    asyncio.run(a.ask("hi"))
    assert calls["checkpoint"] == 0
    assert calls["set_day"][-1] == (1, "2026-06-09")


# --- monthly rollup (digest-of-digests) -------------------------------------

def test_digests_in_month_filters_by_chat_and_month(tmp_path):
    p = _tmpdb(tmp_path)
    conn = db.connect(p)
    with conn:
        conn.execute("INSERT INTO session_digests(chat_id,session_id,summary,created_at) "
                     "VALUES (1,'s','june A','2026-06-03 09:00:00')")
        conn.execute("INSERT INTO session_digests(chat_id,session_id,summary,created_at) "
                     "VALUES (1,'s','june B','2026-06-20 09:00:00')")
        conn.execute("INSERT INTO session_digests(chat_id,session_id,summary,created_at) "
                     "VALUES (1,'s','july A','2026-07-01 09:00:00')")
        conn.execute("INSERT INTO session_digests(chat_id,session_id,summary,created_at) "
                     "VALUES (2,'s','other chat june','2026-06-05 09:00:00')")
    conn.close()
    rows = memory.digests_in_month(1, "2026-06", db_path=p)
    assert [r["summary"] for r in rows] == ["june A", "june B"]   # chat 1, June, oldest-first


def test_month_boundary_triggers_rollup(monkeypatch):
    """Crossing into a new month rolls the day AND consolidates the prior month into a
    HOT note keyed monthly_rollup:<YYYY-MM>, once."""
    stored = {}
    meta = {}
    monkeypatch.setattr(agent.memory, "digests_in_month",
                        lambda cid, ym, *a, **k: [{"created_at": "2026-06-30 09:00:00",
                                                   "summary": "trimmed chips, kept cash"}])
    monkeypatch.setattr(agent.memory, "get_meta", lambda key, *a, **k: meta.get(key))
    monkeypatch.setattr(agent.memory, "set_meta",
                        lambda key, val, *a, **k: meta.__setitem__(key, val))
    monkeypatch.setattr(agent.memory, "remember",
                        lambda value, **kw: stored.update({"value": value, **kw}))

    a, calls = _make_agent(monkeypatch, last_day="2026-06-30",
                           today="2026-07-01", session_id="sess-x")

    async def fake_run(prompt):
        if prompt.startswith(agent._ROLLUP_PROMPT[:20]):
            return ("MEMO: kept a cash buffer; trimmed semis", [])
        return ("ok", [])
    a._run_query = fake_run

    asyncio.run(a.ask("hi"))
    assert calls["checkpoint"] == 1                              # day roll fired
    assert stored.get("key") == "monthly_rollup:2026-06"        # HOT memo keyed by month
    assert stored.get("tier") == "hot"
    assert meta.get("last_rollup_month:1") == "2026-06"         # once-per-month guard set


def test_no_rollup_when_already_done_this_month(monkeypatch):
    """The once-per-month guard skips a second rollup for the same month."""
    stored = {}
    meta = {"last_rollup_month:1": "2026-06"}
    monkeypatch.setattr(agent.memory, "digests_in_month",
                        lambda *a, **k: [{"created_at": "2026-06-30 09:00:00", "summary": "x"}])
    monkeypatch.setattr(agent.memory, "get_meta", lambda key, *a, **k: meta.get(key))
    monkeypatch.setattr(agent.memory, "set_meta",
                        lambda key, val, *a, **k: meta.__setitem__(key, val))
    monkeypatch.setattr(agent.memory, "remember",
                        lambda value, **kw: stored.update({"value": value, **kw}))

    a, calls = _make_agent(monkeypatch, last_day="2026-06-30",
                           today="2026-07-01", session_id="sess-x")
    asyncio.run(a.ask("hi"))
    assert calls["checkpoint"] == 1                              # day still rolls
    assert stored == {}                                          # but no rollup memo written
