"""
test_bugfixes.py — regression tests for the 2026-06-10 code-review fixes.

Each test pins one bug that the review found live in production paths:

  B1  _compute_vote_tally crashed on a non-numeric / null YAML confidence,
      violating run_committee's "never raises" contract.
  B2  debate._vote_score (→ select_debate_pairs) had the same crash.
  B3  scheduler.start() with CIO_DIGEST_HOUR=off returned early and silently
      disabled EVERY other job (price refresh, briefing, maintenance+backup,
      econ alerts).
  B4  ingest_transactions_csv crashed on blank numeric cells (NaN binds as
      NULL → NOT NULL constraint), e.g. a DIV row with an empty quantity.
  B5  memory.get_playbook returned an arbitrary row when the same name exists
      in both the chat scope and global; the chat scope must win.
"""
import asyncio

import pytest


# ---------------------------------------------------------------------------
# B1 — vote tally must tolerate junk confidence values
# ---------------------------------------------------------------------------

class TestSafeConfidence:
    def test_tally_with_null_confidence(self):
        from cio.committee.engine import _compute_vote_tally
        opinions = [
            {"vote": "BUY", "confidence": None},
            {"vote": "SELL", "confidence": "high"},     # YAML word, not a number
            {"vote": "HOLD", "confidence": float("nan")},
            {"vote": "BUY", "confidence": "72"},        # numeric string is fine
        ]
        tally = _compute_vote_tally(opinions)           # must not raise
        assert tally["buy_count"] == 2
        assert tally["sell_count"] == 1
        assert tally["hold_count"] == 1
        assert isinstance(tally["confidence_weighted_score"], float)

    def test_safe_confidence_clamps(self):
        from cio.committee.engine import _safe_confidence
        assert _safe_confidence(None) == 50.0
        assert _safe_confidence("high") == 50.0
        assert _safe_confidence(float("nan")) == 50.0
        assert _safe_confidence(-10) == 0.0
        assert _safe_confidence(150) == 100.0
        assert _safe_confidence("65") == 65.0


# ---------------------------------------------------------------------------
# B2 — debate pair selection must tolerate junk confidence values
# ---------------------------------------------------------------------------

class TestDebateConfidence:
    def test_select_pairs_with_bad_confidence(self):
        from cio.committee.debate import select_debate_pairs
        opinions = [
            {"key": "risk", "title": "Risk", "vote": "SELL", "confidence": None},
            {"key": "equity", "title": "Equity", "vote": "BUY", "confidence": "very high"},
            {"key": "macro", "title": "Macro", "vote": "HOLD", "confidence": 50},
        ]
        pairs = select_debate_pairs(opinions, max_pairs=2)  # must not raise
        assert pairs, "disagreement present → at least the core pair"
        bear, bull = pairs[0]
        assert bear["vote"] == "SELL" and bull["vote"] == "BUY"


# ---------------------------------------------------------------------------
# B3 — digest=off must not disable the other scheduled jobs
# ---------------------------------------------------------------------------

class TestSchedulerDigestOff:
    def test_other_jobs_survive_digest_off(self, monkeypatch):
        from cio import scheduler

        monkeypatch.setenv("CIO_DIGEST_HOUR", "off")
        # Make the boot catch-up paths deterministic / side-effect free.
        monkeypatch.setattr(scheduler.memory, "get_meta", lambda *a, **k: None)

        async def _run():
            sched = scheduler.start(bot=None)
            try:
                assert sched is not None, "digest=off must still return a scheduler"
                ids = {j.id for j in sched.get_jobs()}
                assert "daily_digest" not in ids
                # The independent jobs must all still be registered.
                assert "price_refresh" in ids
                assert "watchlist_briefing" in ids
                assert "memory_maintenance" in ids
                assert "econ_event_alert" in ids
            finally:
                if sched is not None:
                    sched.shutdown(wait=False)

        asyncio.run(_run())

    def test_digest_on_registers_job(self, monkeypatch):
        from cio import scheduler

        monkeypatch.setenv("CIO_DIGEST_HOUR", "8")
        monkeypatch.setattr(scheduler.memory, "get_meta", lambda *a, **k: None)

        async def _run():
            sched = scheduler.start(bot=None)
            try:
                ids = {j.id for j in sched.get_jobs()}
                assert "daily_digest" in ids
            finally:
                sched.shutdown(wait=False)

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# B4 — CSV ingest must tolerate blank cells (NaN) in numeric/text columns
# ---------------------------------------------------------------------------

class TestIngestBlankCells:
    def test_div_row_with_blank_quantity(self, tmp_path):
        from cio import portfolio

        csv = tmp_path / "txns.csv"
        csv.write_text(
            "txn_date,symbol,action,quantity,price,fees,currency,notes\n"
            "2026-01-05,AAPL,BUY,10,200,1,USD,first buy\n"
            "2026-02-01,AAPL,DIV,,2.4,,,\n"          # blank qty/fees/currency/notes
            "2026-03-01,AAPL,SELL,5,220,,USD,\n"     # blank fees/notes
        )
        dbp = tmp_path / "t.db"
        n = portfolio.ingest_transactions_csv(csv, db_path=dbp)
        assert n == 3

        pos = portfolio.positions(db_path=dbp)
        row = pos[pos["symbol"] == "AAPL"].iloc[0]
        assert row["quantity"] == 5            # 10 bought - 5 sold; DIV qty -> 0
        rpl = portfolio.realized_pl(db_path=dbp)
        assert float(rpl[rpl["symbol"] == "AAPL"]["dividends"].iloc[0]) == 2.4

    def test_invalid_action_is_rejected_clearly(self, tmp_path):
        from cio import portfolio

        csv = tmp_path / "bad.csv"
        csv.write_text(
            "txn_date,symbol,action,quantity,price\n"
            "2026-01-05,AAPL,TRANSFER,10,200\n"
        )
        with pytest.raises(ValueError, match="invalid action"):
            portfolio.ingest_transactions_csv(csv, db_path=tmp_path / "t.db")


# ---------------------------------------------------------------------------
# B5 — chat-scoped playbook shadows the global one with the same name
# ---------------------------------------------------------------------------

class TestPlaybookScopePreference:
    def test_chat_scope_wins_on_name_collision(self, tmp_path):
        from cio import memory

        dbp = tmp_path / "m.db"
        memory.add_playbook("monthly_review", "global steps", scope="global", db_path=dbp)
        memory.add_playbook("monthly_review", "chat steps", scope="chat:42", db_path=dbp)

        got = memory.get_playbook("monthly_review", scope="chat:42", db_path=dbp)
        assert got is not None
        assert got["steps"] == "chat steps"

        # Without a chat-scoped copy, the global one is still found.
        got_global = memory.get_playbook("monthly_review", scope="chat:99", db_path=dbp)
        assert got_global is not None
        assert got_global["steps"] == "global steps"
