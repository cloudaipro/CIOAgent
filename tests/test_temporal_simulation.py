"""Temporal simulation harness — the test class that would have caught the
memory-misattribution bug (docs/MEMORY-MISATTRIBUTION.md).

Unit tests stub time, process lifecycle, persistence, or the LLM individually;
that bug lived in their *interaction*: a session_id surviving 8 calendar days
of restarts because the roll counter was in-memory. This harness drives the REAL
CIOAgent session lifecycle (real `cio.memory` persistence on a temp DB, real
`ask()` / `_checkpoint()` / `_monthly_rollup()` code paths) under:

  - a **virtual clock** (`sim.next_day()`),
  - **process restarts** (`sim.restart()` rebuilds the agent exactly like the
    bot does at boot: resume from the persisted session_id, counters reset),
  - a **fake LLM** (deterministic replies; mints session ids like the SDK).

After any scenario, `sim.assert_invariants()` checks the property the fix
promises: **no session_id ever serves turns on two different local days**, and
every day boundary leaves a digest behind.

Run:  pytest -q tests/test_temporal_simulation.py
"""
from __future__ import annotations

import asyncio
import functools
import os
import tempfile
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import pytest

import cio.agent as agent
from cio import db, memory  # noqa: E402

CHAT = 77


class _FakeClient:
    async def connect(self): pass
    async def disconnect(self): pass


class TemporalSim:
    """Virtual-clock + restart driver around the real CIOAgent lifecycle."""

    # memory functions the agent's session lifecycle touches; each is rebound to
    # the simulation's temp DB (their db_path defaults bind the real DB at import).
    _MEM_FNS = ("get_last_turn_day", "set_last_turn_day", "add_digest",
                "latest_digest", "promote_hot", "add_playbook", "get_meta",
                "set_meta", "digests_in_month", "remember")

    def __init__(self, monkeypatch, start_day: str):
        self.db = Path(tempfile.mkdtemp()) / "sim.db"
        db.init(self.db)
        self.day = start_day
        self.turn_log: list[tuple[str, str]] = []   # (virtual_day, session_id)
        self._sid_seq = 0
        for name in self._MEM_FNS:
            monkeypatch.setattr(memory, name,
                                functools.partial(getattr(memory, name),
                                                  db_path=self.db))
        monkeypatch.setattr(agent.timeutil, "today_local", lambda: self.day)
        monkeypatch.setattr(agent, "NUDGE_TURNS", 0)
        monkeypatch.setattr(agent, "ROLL_TURNS", 10**9)
        monkeypatch.setattr(agent, "ROLL_TOKENS", 10**9)
        self.restart()

    # --- process lifecycle -------------------------------------------------

    def restart(self) -> None:
        """Mimic a bot reboot exactly (bot.py:111): fresh agent, counters reset,
        resume = the per-chat session_id persisted in the DB."""
        a = agent.CIOAgent(
            chat_id=CHAT,
            resume=memory.get_session_id(CHAT, db_path=self.db),
            on_session_id=lambda sid: memory.set_session_id(CHAT, sid,
                                                            db_path=self.db),
        )
        a._client = _FakeClient()
        a._make_client = lambda resume: _FakeClient()

        async def fake_run(prompt: str):
            # The SDK assigns a session id on the first message of a new thread.
            if a._session_id is None:
                self._sid_seq += 1
                a._note_session(f"sim-session-{self._sid_seq}")
            # Log only user-facing turns (ask() prefixes them with "[context]").
            # Internal digest/playbook/rollup queries run one extra model turn on
            # the OLD thread during a roll — production never logs those to
            # conv_turns either, and the misattribution invariant is about what
            # the user-visible conversation spans.
            if prompt.startswith("[context]"):
                self.turn_log.append((self.day, a._session_id))
            return (f"sim reply (day {self.day})", [])

        a._run_query = fake_run
        self.agent = a

    # --- clock ---------------------------------------------------------------

    def next_day(self, day: str) -> None:
        self.day = day

    # --- turns ---------------------------------------------------------------

    def turn(self, text: str = "hi") -> str:
        out, _imgs, _docs = asyncio.run(self.agent.ask(text))
        return out

    # --- the property under test ---------------------------------------------

    def assert_invariants(self) -> None:
        # 1. No session serves two different local days.
        days_per_session: dict[str, set[str]] = {}
        for day, sid in self.turn_log:
            days_per_session.setdefault(sid, set()).add(day)
        spanning = {s: d for s, d in days_per_session.items() if len(d) > 1}
        assert not spanning, f"session(s) span multiple days: {spanning}"
        # 2. Every day boundary left a digest (one per retired day-thread).
        transitions = 0
        prev_day = None
        for day, _sid in self.turn_log:
            if prev_day is not None and day != prev_day:
                transitions += 1
            prev_day = day
        conn = db.connect(self.db)
        digests = conn.execute(
            "SELECT COUNT(*) c FROM session_digests WHERE chat_id=?",
            (CHAT,)).fetchone()["c"]
        conn.close()
        assert digests >= transitions, (
            f"{transitions} day boundaries but only {digests} digests")
        # 3. The persisted day marker tracks the last active day.
        assert memory.get_last_turn_day(CHAT) == self.turn_log[-1][0]


@pytest.fixture
def sim(monkeypatch):
    return TemporalSim(monkeypatch, start_day="2099-01-01")


# --- scenarios ----------------------------------------------------------------

def test_eight_days_of_overnight_restarts(sim):
    """The exact incident shape: 8 calendar days, the process restarting each
    morning, a few turns per day. Pre-fix, one session_id served all 8 days."""
    for i in range(1, 9):
        sim.next_day(f"2099-01-{i:02d}")
        sim.restart()                       # overnight reboot
        sim.turn("morning question")
        sim.turn("follow-up")
    sim.assert_invariants()
    # 7 boundaries -> 7 digests, 8 distinct sessions
    assert len({sid for _d, sid in sim.turn_log}) == 8


def test_same_day_restart_preserves_session(sim):
    """Restarts within one day must RESUME, not rotate — continuity matters."""
    sim.turn()
    sid_before = sim.agent._session_id
    sim.restart()                           # crash + reboot, same local day
    sim.turn()
    assert sim.agent._session_id == sid_before
    sim.assert_invariants()


def test_midnight_crossing_without_restart(sim):
    """A process running continuously across midnight still rolls on the first
    post-midnight turn (wall-clock based, not process-lifetime based)."""
    sim.turn()
    sid_day1 = sim.agent._session_id
    sim.next_day("2099-01-02")              # no restart
    sim.turn()
    assert sim.agent._session_id != sid_day1
    sim.assert_invariants()


def test_multiday_downtime_rolls_once(sim):
    """Bot down for days: first turn after the gap rolls exactly once."""
    sim.turn()
    sim.next_day("2099-01-05")
    sim.restart()
    sim.turn()
    sim.assert_invariants()
    conn = db.connect(sim.db)
    n = conn.execute("SELECT COUNT(*) c FROM session_digests").fetchone()["c"]
    conn.close()
    assert n == 1


def test_growth_roll_within_one_day(sim, monkeypatch):
    """ROLL_TURNS still rotates sessions inside a single day; several sessions
    per day are fine — the invariant is one DAY per session, not one session
    per day."""
    monkeypatch.setattr(agent, "ROLL_TURNS", 3)
    for _ in range(10):
        sim.turn()
    sim.assert_invariants()
    assert len({sid for _d, sid in sim.turn_log}) > 1


def test_brand_new_chat_first_day_no_roll(sim):
    """A brand-new chat must not roll (nothing to digest)."""
    sim.turn()
    conn = db.connect(sim.db)
    n = conn.execute("SELECT COUNT(*) c FROM session_digests").fetchone()["c"]
    conn.close()
    assert n == 0
    sim.assert_invariants()


def test_month_boundary_writes_rollup(sim, monkeypatch):
    """Crossing a month boundary consolidates the month's digests into a HOT
    monthly_rollup note. Virtual days are placed around a real month boundary
    relative to digest creation: digests_in_month matches on created_at (real
    'now'), so the virtual previous month must equal the real current month."""
    from datetime import date
    real_month = date.today().strftime("%Y-%m")
    year, month = int(real_month[:4]), int(real_month[5:])
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    # two days in the real current month -> one day-roll digest lands in it
    sim.next_day(f"{real_month}-01")
    sim.restart()
    sim.turn()
    sim.next_day(f"{real_month}-02")
    sim.turn()                              # day roll -> digest for the month
    # now cross into the next month
    sim.next_day(f"{ny:04d}-{nm:02d}-01")
    sim.turn()
    note = memory.recall(f"monthly_rollup:{real_month}", scope=f"chat:{CHAT}",
                         db_path=sim.db)
    assert note, "month boundary did not produce a monthly rollup note"
    assert memory.get_meta(f"last_rollup_month:{CHAT}") == real_month
    sim.assert_invariants()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
