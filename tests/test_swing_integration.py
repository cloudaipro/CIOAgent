"""Comprehensive integration / edge / back-compat tests for the swing upgrades
(pass 1-4). Complements the unit suite in test_swing_upgrades.py.

Offline by construction: no network, no API keys, no live IBKR. Verifies the
cross-module wires hold end-to-end, the never-raises invariant survives malformed
input, and the back-compat / migration guarantees hold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from cio.alpha import coverage, engine, expectancy, hold, regime, scoring, trades
from cio.committee.tirf import gate, scoring as tirf_scoring
from cio.committee.tirf.models import EvidenceItem, SpecialistResearch
from cio.data import finnhub, ibkr
from cio.dashboard import views
from cio.watchlist_monitor import agent as wma
from cio.watchlist_monitor import report as wma_report


def _df(closes, volume=2_000_000):
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="D")
    close = np.array(closes, float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) * 1.01,
                         "Low": np.minimum(open_, close) * 0.99, "Close": close,
                         "Adj Close": close,
                         "Volume": np.full(len(close), volume, float)}, index=idx)


def _up(n=260, start=50.0, step=0.5):
    return [start + step * i for i in range(n)]


# ===================== Phase 3 — integration smoke (offline) =================
def test_funnel_coverage_amplifies_ranking_end_to_end(tmp_path):
    uni = tmp_path / "u.txt"
    uni.write_text("AAA\nBBB\n")
    qqq = _df(_up(260, 300, 0.3))

    def fetch(sym, start, end):
        return qqq if sym == "QQQ" else _df(_up(260, 50, 0.6), volume=3_000_000)

    funds = lambda s: {"market_cap": 5e9, "revenue_growth_pct": 40.0,
                       "free_cash_flow": 1e8, "eps": 4.0, "forward_eps": 6.0}
    recs = {"AAA": {"buy": 3}, "BBB": {"buy": 45}}   # AAA under-covered, BBB saturated
    res = engine.run(universe_path=str(uni), fetch=fetch, fundamentals_fn=funds,
                     surprises_fn=lambda s: [{"beat": True}] * 4,
                     recs_fn=lambda s: recs.get(s), institutional_fn=lambda s: None)
    by = {c["ticker"]: c for c in res.candidates}
    assert by["AAA"]["coverage_edge"] > by["BBB"]["coverage_edge"]
    assert by["AAA"]["final"] >= by["BBB"]["final"]      # neglect amplifies the catalyst
    assert by["AAA"]["analyst_count"] == 3 and by["AAA"]["coverage_flag"]


def test_expectancy_over_real_ledger(tmp_path):
    dbp = tmp_path / "x.db"
    t1 = trades.open_trade("AAA", "2026-01-01", 100.0, stop_px=95.0, db_path=dbp)
    trades.close_trade(t1, "2026-01-20", 120.0, db_path=dbp)      # +20%
    t2 = trades.open_trade("BBB", "2026-02-01", 100.0, stop_px=95.0, db_path=dbp)
    trades.close_trade(t2, "2026-02-10", 95.0, db_path=dbp)       # -5%
    summ = expectancy.summary(trades.list_closed(db_path=dbp), avg_hold_days=14)
    assert summ["n"] == 2 and summ["headline"] == "expectancy"
    assert summ["expectancy_pct"] == round(0.5 * 20 - 0.5 * 5, 4)  # +7.5
    assert summ["annualized_pct"] is not None


def test_ibkr_disabled_is_safe(monkeypatch):
    monkeypatch.delenv("CIO_IBKR_TWS", raising=False)
    assert ibkr.enabled() is False
    assert ibkr.sync_trades() == {"synced": [], "skipped": 0, "seeded": 0, "error": None}


def test_dashboard_expectancy_renders_empty_and_populated():
    empty = views.render_expectancy([], {}, 0)
    assert isinstance(empty, str) and empty
    summ = expectancy.summary([{"pct": 12.0, "r_multiple": 2.0},
                               {"pct": -5.0, "r_multiple": -1.0}])
    html = views.render_expectancy([{"ticker": "AAA", "pct": 12.0},
                                    {"ticker": "BBB", "pct": -5.0}], summ, 0)
    assert isinstance(html, str) and html


def test_monitor_hold_decision_runs_offline(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    from cio.alpha import store as alpha_store
    monkeypatch.setattr(alpha_store, "latest_candidate", lambda *a, **k: None)
    bundle = {"resolved": "AAA", "ta_composite": "bull",
              "ta_signals": {"macd": "bull", "stoch": "bull",
                             "pvo": "neutral", "squeeze": "bull"},
              "analyst": {"strong_buy": 5, "buy": 3, "hold": 1},
              "earnings": None, "filings": []}
    assessment = {"conviction_score": 70, "upcoming_catalysts": ["earnings"]}
    d = wma._hold_decision_for_assessment(assessment, bundle)
    assert d["action"] in {"hold", "trim", "exit"} and "reason" in d


def test_committee_gate_from_specialist_evidence():
    sp = SpecialistResearch(role_key="fund")
    sp.evidence = [
        EvidenceItem(source="10-K", finding="Q3 earnings beat, guidance raise",
                     date="2026-06-10", relevance="direct"),
        EvidenceItem(source="analyst", finding="upgrades, price target raised",
                     date="2026-06-10", relevance="direct"),
        EvidenceItem(source="chart", finding="relative strength, new high",
                     date="2026-06-10", relevance="related"),
    ]
    tirf_scoring.score_specialist(sp, as_of="2026-06-11")
    v = gate.gate_evidence(sp.evidence)
    assert {"pass", "blocked_by", "missing", "scores", "layer_scores"} <= set(v)
    assert "catalyst" in v["layer_scores"]


# ===================== Phase 4 — edge / never-raises fuzz ====================
def test_pure_functions_never_raise_on_garbage():
    # coverage
    assert coverage.coverage_score(None, None, None)["coverage_edge"] == 50.0
    assert coverage.coverage_score({}, "x", "y")["coverage_edge"] == 50.0
    assert coverage.apply(None, None) == 0.0
    assert coverage.analyst_count("nope") is None
    # four-layer gate
    assert gate.evaluate({})["pass"] is False
    assert gate.layer_scores([]) == {}
    assert gate.gate_evidence(None)["pass"] is False
    # expectancy
    assert expectancy.expectancy([])["n"] == 0
    assert expectancy.expectancy(None)["expectancy"] == 0.0
    assert expectancy.summary([])["n"] == 0
    assert expectancy.annualized("x", 5) is None
    assert expectancy.sqn([]) is None
    assert expectancy.oos_check([], [])["overfit"] is False
    # hold + regime
    assert hold.hold_decision({}, "")["action"] in {"hold", "trim", "exit"}
    assert hold.hold_decision(None, None)["action"] in {"hold", "trim", "exit"}
    assert regime.position_style(None)["style"] == "neutral"
    assert regime.position_style(123)["style"] == "neutral"
    # tirf layer classify
    assert tirf_scoring.classify_layer("") == "catalyst"
    assert tirf_scoring.classify_layer(None) == "catalyst"
    # finnhub parsers
    assert finnhub._institutional_pct("garbage") is None
    assert finnhub._institutional_pct({"data": [{"ownership": [{"percentage": "x"}]}]}) is None
    # monitor scoring
    assert wma._consensus({"strong_buy": "5"}) is None
    assert wma._behavior_score_with_trend(None) is None


def test_institutional_gated_off_by_default(monkeypatch):
    # The premium /stock/ownership 403-spam source: with the flag off, NO network
    # call is made at all (the funnel stays fast + quiet on the free tier).
    monkeypatch.setenv("FINNHUB_API_KEY", "fake")
    monkeypatch.setattr(finnhub._cache, "read", lambda *a, **k: None)   # force cache miss
    monkeypatch.setattr(finnhub._cache, "write", lambda *a, **k: None)
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return {"data": [{"reportDate": "2026-03-31",
                          "ownership": [{"percentage": 12.0}]}]}

    monkeypatch.setattr(finnhub, "get_json", fake_get)
    monkeypatch.delenv("CIO_FINNHUB_INSTITUTIONAL", raising=False)
    assert finnhub.institutional_ownership_pct("ABNB") is None          # gated off
    assert calls == []                                                  # no call -> no 403
    monkeypatch.setenv("CIO_FINNHUB_INSTITUTIONAL", "1")
    assert finnhub.institutional_ownership_pct("ABNB") == 12.0          # opt-in -> works
    assert calls == [1]


def test_trades_helpers_never_raise_on_empty(tmp_path):
    dbp = tmp_path / "empty.db"
    assert trades.list_closed(db_path=dbp) == []
    assert trades.list_open(db_path=dbp) == []
    assert trades.list_orphans(db_path=dbp) == []
    assert trades.close_trade(999, "2026-01-01", 10.0, db_path=dbp) is None
    assert trades.reconcile_orphan(999, 10.0, "2026-01-01", db_path=dbp) is None


# ===================== Phase 5 — back-compat + migration =====================
def test_coverage_backcompat_is_identity():
    legacy = (0.30 * 70 + 0.20 * 60 + 0.30 * 80
              + 0.10 * scoring.metrics.scale(20, full_at=50.0) + 0.10 * 0.0)
    got = scoring.final_score(70, 60, 80, 20, None, coverage_edge=None)["final"]
    assert got == round(legacy, 2)
    a = coverage.coverage_score({"buy": 8}, 5000)["coverage_edge"]
    b = coverage.coverage_score({"buy": 8}, 5000, institutional_pct=None)["coverage_edge"]
    assert a == b


# ===================== Phase 6 — gap-fill (pass 5) ===========================
def test_gap1_alpha_table_shows_coverage_columns():
    latest = {"run_date": "2026-06-16", "regime": "GREEN", "regime_detail": "",
              "sectors": [], "watchlist_id": None, "watchlist_name": None,
              "candidates": [{"rank": 1, "ticker": "AAA", "sector": "Tech",
                              "final": 88.0, "momentum": 80, "trend": 70, "earnings": 75,
                              "revenue_growth": 30, "fwd_eps_growth": 25, "surprise": 60,
                              "analyst_count": 4, "coverage_edge": 78.0,
                              "coverage_flag": "under_covered"}]}
    html = views.render_alpha(latest, [], 0)
    assert "CovEdge" in html and "Anlst" in html          # header columns present
    assert "under-covered" in html                        # coverage badge rendered (hyphenated label)


def test_gap2_portfolio_has_sync_trades_button():
    summ = {"positions": 0, "market_value": 0, "cost_basis": 0, "unrealized_pl": 0,
            "unrealized_pct": 0, "realized_pl": 0, "dividends": 0}
    html = views.render_portfolio(summ, [], [], 0)
    assert "sync_trades" in html and "Sync trade ledger" in html


def test_gap3_briefing_shows_hold_posture():
    a = {"ticker": "AAA", "company": "Alpha Co", "overall_status": "bullish",
         "conviction_score": 70, "recommendation": "hold", "event_importance": "low",
         "analyst_sentiment": "positive", "investment_thesis_change": "unchanged",
         "summary": "steady", "key_positive_events": [], "key_negative_events": [],
         "new_risks": [], "upcoming_catalysts": [],
         "hold_decision": {"action": "hold", "style": "肥", "stop_mode": "trailing",
                           "reason": "trend intact + catalyst alive"}}
    block = wma_report._security_block(a)
    assert "Hold posture" in block and "HOLD" in block and "trailing" in block
    brief = wma_report.build_briefing([a], as_of="2026-06-16")
    assert "Hold posture" in brief


def test_db_migration_idempotent(tmp_path):
    from cio import db
    dbp = tmp_path / "mig.db"
    db.connect(dbp).close()                              # first init + migrate
    db._INITIALIZED.discard(str(dbp.resolve()))
    db.connect(dbp).close()                              # migrate again
    conn = db.connect(dbp)
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(alpha_candidates)")]
    finally:
        conn.close()
    assert cols.count("coverage_edge") == 1              # not duplicated
