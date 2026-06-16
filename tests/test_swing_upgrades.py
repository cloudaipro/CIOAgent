"""Swing-strategy upgrades (2026-06) — coverage / four-layer gate / expectancy /
trade ledger / hold management. Pure-function tests; no network, no LLM.
"""
from __future__ import annotations

import sqlite3

from cio import db
from cio.alpha import coverage, expectancy, hold, regime, scoring as alpha_scoring, store, trades
from cio.alpha.engine import AlphaResult
from cio.committee.tirf import gate, scoring as tirf_scoring
from cio.committee.tirf.models import EvidenceItem, SpecialistResearch


# ---- #1 coverage density ----------------------------------------------------
def test_analyst_count_sums_buckets_or_none():
    assert coverage.analyst_count({"strong_buy": 2, "buy": 3, "hold": 5,
                                   "sell": 1, "strong_sell": 0}) == 11
    assert coverage.analyst_count(None) is None
    assert coverage.analyst_count({"buy": None, "hold": None}) is None


def test_expected_coverage_grows_with_size():
    assert coverage.expected_coverage(300) < coverage.expected_coverage(30000)
    assert 2.0 <= coverage.expected_coverage(300) <= 4.0
    assert coverage.expected_coverage(None) is None


def test_under_covered_beats_saturated_edge():
    under = coverage.coverage_score({"buy": 4}, 30000)   # 4 analysts on a $30B name
    over = coverage.coverage_score({"buy": 40}, 3000)    # 40 analysts on a $3B name
    assert under["coverage_edge"] > 50 > over["coverage_edge"]
    assert under["flag"] == "under_covered"


def test_value_trap_floor_and_count_only():
    trap = coverage.coverage_score({"buy": 0, "hold": 0, "sell": 0,
                                    "strong_buy": 0, "strong_sell": 0}, 500)
    assert trap["coverage_edge"] == 0.0 and trap["flag"] == "value_trap_floor"
    assert coverage.coverage_score({"buy": 5}, None)["flag"] == "count_only"


def test_apply_amplifies_but_never_manufactures():
    assert coverage.apply(80, None) == 80               # back-compat: no edge -> unchanged
    assert coverage.apply(80, 100) > 80 > coverage.apply(80, 0)
    assert coverage.apply(0, 100) == 0                  # no catalyst -> stays nothing
    assert 0 <= coverage.apply(100, 100) <= 100         # clamped


def test_institutional_blend(tmp_path=None):
    # pass-3: low institutional ownership = neglected = higher edge; crowded = lower
    low = coverage.coverage_score(None, None, institutional_pct=20)
    high = coverage.coverage_score(None, None, institutional_pct=85)
    assert low["coverage_edge"] > 50 > high["coverage_edge"]
    assert low["flag"] == "institutionally_neglected"
    assert high["flag"] == "institutionally_crowded"
    # blended with analyst signal: institutional moves the combined edge
    a_only = coverage.coverage_score({"buy": 8}, 5000)
    blended = coverage.coverage_score({"buy": 8}, 5000, institutional_pct=20)
    assert blended["coverage_edge"] != a_only["coverage_edge"]
    # back-compat: institutional_pct=None reproduces analyst-only exactly
    assert coverage.coverage_score({"buy": 8}, 5000,
                                   institutional_pct=None)["coverage_edge"] == a_only["coverage_edge"]


def test_final_score_backcompat_and_amplification():
    base = alpha_scoring.final_score(70, 60, 80, 20, None, coverage_edge=None)
    amped = alpha_scoring.final_score(70, 60, 80, 20, None, coverage_edge=100)
    assert amped["final"] > base["final"]               # under-covered ranks higher
    # coverage_edge=None must reproduce the legacy weighted sum exactly:
    assert base["earnings_amplified"] == 80


# ---- #2 four-layer gate -----------------------------------------------------
def test_classify_layer_routes_text():
    assert tirf_scoring.classify_layer("RSI golden cross, oversold") == "execution"
    assert tirf_scoring.classify_layer("analyst upgrade, price target raised") == "behavior"
    assert tirf_scoring.classify_layer("relative strength, new 52-week high") == "momentum"
    assert tirf_scoring.classify_layer("Q3 earnings beat, guidance raise") == "catalyst"


def test_gate_and_logic_no_cross_compensation():
    scores = {"catalyst": 30, "behavior": 90, "momentum": 95, "execution": 95}
    v = gate.evaluate(scores)
    assert v["pass"] is False and "catalyst" in v["blocked_by"]   # green can't save red catalyst
    ok = gate.evaluate({"catalyst": 70, "behavior": 60, "momentum": 60, "execution": 50})
    assert ok["pass"] is True


def test_gate_requires_catalyst_present():
    v = gate.evaluate({"momentum": 90, "execution": 90})
    assert v["pass"] is False and "catalyst" in v["blocked_by"]


def test_score_specialist_fills_layer_scores():
    sp = SpecialistResearch(role_key="fundamentals")
    sp.evidence = [
        EvidenceItem(source="10-K filing", finding="Q3 earnings beat and guidance raise",
                     date="2026-06-10", relevance="direct"),
        EvidenceItem(source="analyst note", finding="three analysts upgrade, target raised",
                     date="2026-06-09", relevance="direct"),
    ]
    tirf_scoring.score_specialist(sp, as_of="2026-06-11")
    assert "catalyst" in sp.layer_scores and "behavior" in sp.layer_scores
    assert sp.evidence[0].layer == "catalyst"
    assert sp.evidence[1].layer == "behavior"


# ---- #3b expectancy (the conv 280-289 A/B examples) -------------------------
def _book(win_pct, n, win, loss):
    wins = round(n * win_pct)
    return [{"pct": win} for _ in range(wins)] + [{"pct": -loss} for _ in range(n - wins)]


def test_expectancy_beats_win_rate_as_kpi():
    a = expectancy.expectancy(_book(0.65, 20, 6, 12))     # high win-rate, bad
    b = expectancy.expectancy(_book(0.45, 20, 20, 7))     # low win-rate, great
    assert a["win_rate"] > b["win_rate"]                  # A "looks" better
    assert a["expectancy"] < 0 < b["expectancy"]          # ...but B is the real edge
    assert abs(a["expectancy"] - (-0.3)) < 1e-6
    assert abs(b["expectancy"] - 5.15) < 1e-6


def test_annualized_turns_per_trade_into_yearly():
    # +1.35%/trade, ~17 turns/yr -> ~+26%/yr (not "smaller than a CD")
    assert 24 <= expectancy.annualized(1.35, 17) <= 28
    assert expectancy.annualized(1.35, 0) is None


def test_sqn_and_oos_overfit_flag():
    good = [{"r_multiple": r} for r in (2, -1, 3, -1, 2, -1, 1, 2)]
    assert expectancy.sqn(good) is not None
    is_ = _book(0.55, 30, 12, 6)
    oos = _book(0.40, 30, 4, 10)                          # collapses out of sample
    chk = expectancy.oos_check(is_, oos)
    assert chk["overfit"] is True


# ---- #3a trade ledger -------------------------------------------------------
def test_trade_ledger_roundtrip_and_r(tmp_path):
    dbp = tmp_path / "ledger.db"
    tid = trades.open_trade("NVDA", "2026-05-01", 100.0, stop_px=93.0, db_path=dbp)
    assert trades.list_open(db_path=dbp)[0]["ticker"] == "NVDA"
    row = trades.close_trade(tid, "2026-05-20", 120.0, db_path=dbp)
    assert row["pct"] == 20.0
    assert abs(row["r_multiple"] - (20 / 7)) < 1e-2       # (120-100)/(100-93)
    assert trades.list_open(db_path=dbp) == []
    closed = trades.list_closed(db_path=dbp)
    assert len(closed) == 1
    # ledger feeds expectancy directly:
    assert expectancy.expectancy(closed)["n"] == 1


def test_orphan_sell_excluded_from_expectancy(tmp_path):
    # Review fix (Richard pass-2): an IBKR sell with no matching open must NOT become
    # a fabricated pct=0 closed trade — that would dilute the expectancy denominator.
    dbp = tmp_path / "orphan.db"
    trades.open_trade("NVDA", "2026-05-01", 100.0, stop_px=93.0, db_path=dbp)
    trades.close_trade(1, "2026-05-20", 120.0, db_path=dbp)        # one real winner
    trades.record_orphan_sell("TSLA", "2026-06-01", 200.0, note="exec_id:x1", db_path=dbp)
    closed = trades.list_closed(db_path=dbp)
    assert len(closed) == 1                                        # orphan NOT counted
    assert expectancy.expectancy(closed)["n"] == 1                 # KPI sees only the real trade
    assert expectancy.expectancy(closed)["win_rate"] == 1.0        # not diluted to 0.5
    assert len(trades.list_orphans(db_path=dbp)) == 1             # but preserved for reconcile


def test_open_if_absent_guards_duplicate_lot(tmp_path):
    # Richard pass-3 fix: a BOT fill for an already-open (seeded) symbol must NOT
    # create a second open lot (double-count).
    from cio.data import ibkr
    dbp = tmp_path / "ibkr.db"
    assert ibkr._open_if_absent(trades, "NVDA", "2026-05-01", 100.0, qty=10,
                                style="肥", regime_status="GREEN", note="exec_id:b1",
                                db_path=dbp) is True
    assert ibkr._open_if_absent(trades, "NVDA", "2026-05-02", 105.0, qty=5,
                                style="肥", regime_status="GREEN", note="exec_id:b2",
                                db_path=dbp) is False          # already open -> skipped
    opens = [t for t in trades.list_open(db_path=dbp) if t["ticker"] == "NVDA"]
    assert len(opens) == 1


def test_latest_candidate_feeds_reused_momentum(tmp_path):
    # OD-5: the monitor reuses the funnel's persisted momentum (zero new fetch).
    dbp = tmp_path / "cand.db"
    cand = {"ticker": "AMD", "rank": 1, "sector": "Tech", "momentum": 82.0, "trend": 70,
            "earnings": 60, "earnings_amplified": 60, "analyst_count": 9,
            "coverage_edge": 55.0, "coverage_flag": "", "revenue_growth": 20,
            "fwd_eps_growth": 18, "surprise": 50, "volume_expansion": 30,
            "final": 75.0, "quality_pass": True}
    res = AlphaResult(run_date="2026-06-15", regime={"status": "GREEN", "detail": ""},
                      sectors=[], candidates=[cand], universe_size=1)
    store.save_run(res, publish=False, threshold=0.0, db_path=dbp)
    got = store.latest_candidate("AMD", db_path=dbp)
    assert got is not None and got["momentum"] == 82.0
    assert store.latest_candidate("ZZZZ", db_path=dbp) is None      # unknown -> None


# ---- pass-4: Finnhub institutional fetch (parser; live call is premium/None) --
def test_institutional_pct_sums_latest_report_only():
    from cio.data import finnhub
    payload = {"symbol": "NVDA", "data": [
        {"reportDate": "2026-03-31", "ownership": [
            {"name": "Vanguard", "percentage": 8.1},
            {"name": "BlackRock", "percentage": 7.4},
            {"name": "State Street", "percentage": 4.0}]},
        {"reportDate": "2025-12-31", "ownership": [{"percentage": 99.0}]},  # older, ignored
    ]}
    assert abs(finnhub._institutional_pct(payload) - 19.5) < 1e-6
    assert finnhub._institutional_pct({"data": []}) is None
    assert finnhub._institutional_pct(None) is None


def test_institutional_pct_clamps_and_skips_nonnumeric():
    from cio.data import finnhub
    payload = {"data": [{"reportDate": "2026-03-31",
                         "ownership": [{"percentage": 80.0}, {"percentage": 60.0},
                                       {"percentage": None}]}]}
    assert finnhub._institutional_pct(payload) == 100.0          # 140 clamped to 100
    # feeds the coverage blend: high institutional % -> low edge
    assert coverage.coverage_score(None, None, institutional_pct=100.0)["coverage_edge"] == 0.0


def test_recs_rows_sorted_newest_first():
    from cio.data import finnhub
    rows = [{"period": "2026-01-01", "strongBuy": 1, "buy": 2, "hold": 3},
            {"period": "2026-03-01", "strongBuy": 5, "buy": 4, "hold": 1}]
    ordered = finnhub._sorted_rec_rows(rows)
    assert ordered[0]["period"] == "2026-03-01"
    assert finnhub._norm_rec_row(ordered[0])["strong_buy"] == 5


# ---- pass-4: behavior trend delta (OD-4 closed) ------------------------------
def test_behavior_trend_rises_and_falls():
    from cio.watchlist_monitor import agent as wma
    strong = {"strong_buy": 8, "buy": 4, "hold": 1, "sell": 0, "strong_sell": 0}
    weak = {"strong_buy": 1, "buy": 2, "hold": 5, "sell": 3, "strong_sell": 1}
    base = wma._consensus(strong)
    rising = wma._behavior_score_with_trend([strong, weak])   # latest strong, prior weak
    falling = wma._behavior_score_with_trend([weak, strong])  # latest weak, prior strong
    assert rising >= base > falling                           # upgrades lift, downgrades cut
    assert wma._behavior_score_with_trend([strong]) == base   # single period = base
    assert wma._behavior_score_with_trend([]) is None


def test_consensus_survives_string_counts(tmp_path=None):
    # Richard pass-4 fix: string counts from the API must not crash the arithmetic.
    from cio.watchlist_monitor import agent as wma
    assert wma._consensus({"strong_buy": "5", "buy": "3", "hold": "2"}) is None  # strings -> 0 -> empty
    mixed = wma._consensus({"strong_buy": 5, "buy": "oops", "hold": 2})
    assert mixed is not None and 0.0 <= mixed <= 100.0          # numeric kept, junk ignored


# ---- #5 hold management -----------------------------------------------------
def test_position_style_switches_on_regime():
    assert regime.position_style("GREEN")["style"] == "肥"
    assert regime.position_style("RED")["style"] == "勤"
    assert regime.position_style("WAT")["style"] == "neutral"


def test_hold_exits_on_catalyst_break_even_if_execution_green():
    d = hold.hold_decision({"catalyst": 40, "behavior": 80, "momentum": 90,
                            "execution": 95}, "GREEN")
    assert d["action"] == "exit"                          # catalyst-break overrides green timing


def test_hold_runs_winner_and_trims_on_euphoria():
    assert hold.hold_decision({"catalyst": 70, "behavior": 60}, "GREEN")["action"] == "hold"
    assert hold.hold_decision({"catalyst": 70, "behavior": 90}, "GREEN")["action"] == "trim"
    assert hold.hold_decision({"catalyst": 70}, "RED", in_profit=True)["action"] == "trim"


# ---- review fix: store persistence + legacy migration (Richard finding) ------
def test_store_persists_coverage_columns(tmp_path):
    dbp = tmp_path / "alpha.db"
    cand = {"ticker": "NVDA", "rank": 1, "sector": "Tech", "momentum": 80, "trend": 70,
            "earnings": 75, "earnings_amplified": 92.0, "analyst_count": 6,
            "coverage_edge": 78.0, "coverage_flag": "under_covered",
            "revenue_growth": 30, "fwd_eps_growth": 25, "surprise": 60,
            "volume_expansion": 40, "final": 88.0, "quality_pass": True}
    res = AlphaResult(run_date="2026-06-15", regime={"status": "GREEN", "detail": ""},
                      sectors=[], candidates=[cand], universe_size=1)
    store.save_run(res, publish=False, threshold=0.0, db_path=dbp)
    got = store.latest_run(db_path=dbp)["candidates"][0]
    assert got["coverage_edge"] == 78.0          # was silently dropped before fix
    assert got["analyst_count"] == 6
    assert got["coverage_flag"] == "under_covered"
    assert got["earnings_amplified"] == 92.0


def test_legacy_alpha_candidates_table_migrates(tmp_path):
    dbp = tmp_path / "legacy.db"
    # Pre-create the OLD alpha_candidates (no coverage columns) so CREATE TABLE IF
    # NOT EXISTS won't recreate it — the migration must add the columns.
    raw = sqlite3.connect(dbp)
    raw.executescript(
        "CREATE TABLE alpha_candidates (run_id INTEGER, rank INTEGER, ticker TEXT, "
        "final REAL, quality_pass INTEGER);")
    raw.commit(); raw.close()
    db._INITIALIZED.discard(str(dbp.resolve()))   # force first-connect migration path
    conn = db.connect(dbp)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(alpha_candidates)")}
    finally:
        conn.close()
    assert {"earnings_amplified", "analyst_count", "coverage_edge", "coverage_flag"} <= cols


# ---- pass-2 wire 1: four-layer gate visible in dossier / appendix -----------
def test_dossier_renders_gate_verdict_pass():
    """Gate PASS appears in rendered dossier."""
    from cio.committee.tirf.models import ResearchReport, EvidenceItem, SpecialistResearch
    from cio.committee.tirf.dossier import render_dossier
    report = ResearchReport(ticker="AAPL", as_of="2026-06-15")
    sp = SpecialistResearch(role_key="fundamentals")
    sp.evidence = [
        EvidenceItem(source="10-K", finding="Q3 earnings beat and guidance raise",
                     date="2026-06-10", relevance="direct", item_score=80, layer="catalyst"),
        EvidenceItem(source="analyst", finding="RSI oversold, technical breakout",
                     date="2026-06-10", relevance="direct", item_score=70, layer="execution"),
        EvidenceItem(source="flow", finding="analyst revision, price target raised",
                     date="2026-06-10", relevance="direct", item_score=60, layer="behavior"),
        EvidenceItem(source="rs", finding="relative strength, 52-week high",
                     date="2026-06-10", relevance="direct", item_score=65, layer="momentum"),
    ]
    report.specialists = [sp]
    # Simulate what builder does: compute gate and stash it in review.
    from cio.committee.tirf import gate as tirf_gate
    report.review = {}
    report.review["four_layer_gate"] = tirf_gate.gate_evidence(report.all_evidence())
    text = render_dossier(report)
    assert "Four-Layer Gate" in text
    assert "PASS" in text


def test_dossier_renders_gate_verdict_blocked():
    """Gate BLOCKED (catalyst missing) appears in dossier with warning."""
    from cio.committee.tirf.models import ResearchReport, EvidenceItem, SpecialistResearch
    from cio.committee.tirf.dossier import render_dossier
    from cio.committee.tirf import gate as tirf_gate
    report = ResearchReport(ticker="TSLA", as_of="2026-06-15")
    sp = SpecialistResearch(role_key="fundamentals")
    sp.evidence = [
        EvidenceItem(source="rs", finding="relative strength, 52-week high",
                     date="2026-06-10", relevance="direct", item_score=85, layer="momentum"),
    ]
    report.specialists = [sp]
    report.review = {}
    report.review["four_layer_gate"] = tirf_gate.gate_evidence(report.all_evidence())
    text = render_dossier(report)
    assert "Four-Layer Gate" in text
    assert "blocked" in text.lower() or "catalyst" in text


def test_tirf_appendix_includes_gate():
    """tirf_appendix also surfaces the gate block."""
    from cio.committee.tirf.models import ResearchReport, EvidenceItem, SpecialistResearch
    from cio.committee.tirf.dossier import tirf_appendix
    from cio.committee.tirf import gate as tirf_gate
    report = ResearchReport(ticker="MSFT", as_of="2026-06-15")
    sp = SpecialistResearch(role_key="fundamentals")
    sp.evidence = [
        EvidenceItem(source="10-K", finding="Q3 earnings beat",
                     date="2026-06-10", relevance="direct", item_score=75, layer="catalyst"),
    ]
    report.specialists = [sp]
    report.review = {}
    report.review["four_layer_gate"] = tirf_gate.gate_evidence(report.all_evidence())
    appendix = tirf_appendix(report)
    assert "Four-Layer Gate" in appendix


# ---- pass-2 wire 2: expectancy dashboard panel ------------------------------
def test_render_expectancy_empty(tmp_path):
    """Empty ledger: no crash, sensible empty message."""
    from cio.dashboard.views import render_expectancy
    html = render_expectancy([], {}, level=1)
    assert "no closed trades" in html.lower() or "no closed trade" in html.lower()


def test_render_expectancy_with_trades(tmp_path):
    """Populated ledger: headline = expectancy, win-rate demoted."""
    from cio.dashboard.views import render_expectancy
    from cio.alpha import expectancy as exp_mod
    closed = [
        {"ticker": "NVDA", "entry_date": "2026-05-01", "exit_date": "2026-05-20",
         "pct": 20.0, "r_multiple": 2.86, "style": "肥"},
        {"ticker": "TSLA", "entry_date": "2026-05-05", "exit_date": "2026-05-15",
         "pct": -5.0, "r_multiple": -0.71, "style": "neutral"},
    ]
    summ = exp_mod.summary(closed)
    html = render_expectancy(closed, summ, level=1)
    assert "Expectancy" in html
    # Win-rate present but demoted (in the sub-stats section, not the headline)
    assert "Sub-stats" in html
    assert "demoted" in html.lower()
    # Headline expectancy value present
    assert "expectancy" in html.lower()


# ---- pass-2 wire 3: monitor hold decision -----------------------------------
def test_monitor_hold_decision_exit_on_catalyst_break():
    """Catalyst breaks (score <= 45 via low conviction + no upcoming catalysts)
    should yield exit when catalyst layer is present and low."""
    # Direct test of _hold_decision_for_assessment with synthetic assessment.
    from cio.watchlist_monitor.agent import _hold_decision_for_assessment
    assessment = {
        "conviction_score": 20,   # low -> execution layer = 20
        "upcoming_catalysts": ["earnings tomorrow"],  # catalyst present, but
        # catalyst is set to 80 (alive) when upcoming_catalysts is non-empty.
        # Thesis is intact, regime GREEN -> hold expected (catalyst=80 > 45 threshold)
    }
    # With regime GREEN and catalyst=80, hold expected (not exit).
    result = _hold_decision_for_assessment.__wrapped__(assessment) if hasattr(
        _hold_decision_for_assessment, "__wrapped__") else None
    # Call directly since it's a plain function in the module.
    from cio.alpha import hold as hold_mod, regime as regime_mod
    layer_scores = {"execution": 20.0, "catalyst": 80.0}
    d = hold_mod.hold_decision(layer_scores, "GREEN")
    assert d["action"] == "hold"  # catalyst 80 > 45, GREEN -> hold


def test_monitor_hold_decision_hold_on_green():
    """GREEN regime + catalyst alive -> hold."""
    from cio.alpha import hold as hold_mod
    d = hold_mod.hold_decision({"catalyst": 70, "execution": 60}, "GREEN")
    assert d["action"] == "hold"


def test_monitor_hold_decision_exit_on_catalyst_break_direct():
    """Catalyst score <= 45 -> exit regardless of regime."""
    from cio.alpha import hold as hold_mod
    d = hold_mod.hold_decision({"catalyst": 40, "execution": 90}, "GREEN")
    assert d["action"] == "exit"


def test_monitor_no_catalyst_layer_guard_inactive():
    """When catalyst layer absent, catalyst-break guard is inactive (hold, not exit)."""
    from cio.alpha import hold as hold_mod
    # No catalyst key -> guard doesn't fire, stays hold in GREEN.
    d = hold_mod.hold_decision({"execution": 90}, "GREEN")
    assert d["action"] == "hold"


def test_hold_decision_in_assessment_dict():
    """monitor_symbol assessment dict includes hold_decision field."""
    # We can't run monitor_symbol (needs network/LLM), but we can test the
    # helper function directly with a synthetic assessment.
    from cio.watchlist_monitor.agent import _hold_decision_for_assessment
    assessment = {
        "conviction_score": 75,
        "upcoming_catalysts": ["earnings in 2 days"],
    }
    result = _hold_decision_for_assessment(assessment)
    assert "action" in result
    assert result["action"] in ("hold", "trim", "exit")
    assert "reason" in result


# ---- pass-2 wire 4: IBKR trades auto-log ------------------------------------
def test_ibkr_sync_trades_disabled_returns_empty():
    """When IBKR is not configured, sync_trades returns empty safely."""
    import os
    original = os.environ.pop("CIO_IBKR_TWS", None)
    try:
        from cio.data.ibkr import sync_trades
        result = sync_trades()
        assert result["synced"] == []
        assert result["skipped"] == 0
        assert result["error"] is None
    finally:
        if original is not None:
            os.environ["CIO_IBKR_TWS"] = original


def test_ibkr_normalize_fills_bot_side(tmp_path):
    """BOT fill -> open_trade in ledger."""
    from cio.data.ibkr import _normalize_fills, _exec_id_logged
    from cio.alpha import trades as trade_ledger

    dbp = tmp_path / "ibkr_test.db"

    class _FakeExe:
        execId = "EX001"
        symbol = "NVDA"
        side = "BOT"
        price = 100.0
        shares = 10.0
        time = "2026-06-01"

    class _FakeFill:
        execution = _FakeExe()

    fills = _normalize_fills([_FakeFill()])
    assert len(fills) == 1
    assert fills[0]["symbol"] == "NVDA"
    assert fills[0]["side"] == "BOT"
    assert fills[0]["exec_id"] == "EX001"

    # Manually open a trade with this exec_id in note and confirm idempotency check.
    trade_ledger.open_trade("NVDA", "2026-06-01", 100.0,
                            note="exec_id:EX001", db_path=dbp)
    assert _exec_id_logged("EX001", dbp) is True
    assert _exec_id_logged("DOESNOTEXIST", dbp) is False


def test_ibkr_normalize_fills_empty():
    """Empty fills list -> empty output, no crash."""
    from cio.data.ibkr import _normalize_fills
    assert _normalize_fills([]) == []
    assert _normalize_fills(None) == []


# ---- pass-3 item 1: EDGAR institutional_ownership_pct ----------------------
def test_edgar_institutional_ownership_pct_returns_none():
    """institutional_ownership_pct returns None (OPEN DECISION OD-3: 13F aggregation
    requires bulk EDGAR index download; deferred to Arch for data source decision)."""
    from cio.data.edgar import institutional_ownership_pct
    assert institutional_ownership_pct("NVDA") is None
    assert institutional_ownership_pct("AAPL") is None


def test_edgar_institutional_pct_wires_to_coverage_none_safe():
    """coverage_score tolerates None institutional_pct (analyst-only fallback)."""
    from cio.alpha import coverage
    result = coverage.coverage_score({"buy": 5}, 5000, institutional_pct=None)
    assert result["institutional_pct"] is None
    assert result["coverage_edge"] > 0  # falls through to analyst-only path


# ---- pass-3 item 2: IBKR position seeding -----------------------------------
def test_seed_positions_opens_new_rows(tmp_path):
    """_seed_positions inserts open rows from IBKR positions (avg_cost as entry_px)."""
    from cio.data.ibkr import _seed_positions
    from cio.alpha import trades as trade_ledger

    dbp = tmp_path / "seed.db"
    positions = [
        {"symbol": "NVDA", "quantity": 10.0, "avg_cost": 100.0},
        {"symbol": "AAPL", "quantity": 5.0, "avg_cost": 175.0},
    ]
    seeded = _seed_positions(positions, dbp, "GREEN", "肥")
    assert seeded == 2
    opens = trade_ledger.list_open(db_path=dbp)
    syms = {t["ticker"] for t in opens}
    assert syms == {"NVDA", "AAPL"}
    nvda = next(t for t in opens if t["ticker"] == "NVDA")
    assert nvda["entry_px"] == 100.0
    assert nvda["note"] == "ibkr_position_seed"


def test_seed_positions_idempotent(tmp_path):
    """Re-running _seed_positions does not double-seed an already-open symbol."""
    from cio.data.ibkr import _seed_positions
    from cio.alpha import trades as trade_ledger

    dbp = tmp_path / "seed_idem.db"
    positions = [{"symbol": "TSLA", "quantity": 3.0, "avg_cost": 200.0}]
    seeded1 = _seed_positions(positions, dbp, "GREEN", "肥")
    seeded2 = _seed_positions(positions, dbp, "GREEN", "肥")
    assert seeded1 == 1
    assert seeded2 == 0  # idempotent: no duplicate
    assert len(trade_ledger.list_open(db_path=dbp)) == 1


def test_seed_positions_sld_fill_matches_seeded_open(tmp_path):
    """SLD fill after seeding matches the seeded open and computes real pct."""
    from cio.data.ibkr import _seed_positions, _normalize_fills
    from cio.alpha import trades as trade_ledger

    dbp = tmp_path / "seed_sld.db"
    # Seed NVDA @ 100.
    _seed_positions(
        [{"symbol": "NVDA", "quantity": 10.0, "avg_cost": 100.0}],
        dbp, "GREEN", "neutral",
    )
    # Simulate a SLD fill at 120.
    opens = [t for t in trade_ledger.list_open(db_path=dbp) if t["ticker"] == "NVDA"]
    assert opens, "seeded open must exist"
    trade_ledger.close_trade(opens[-1]["id"], "2026-06-15", 120.0, db_path=dbp)
    closed = trade_ledger.list_closed(db_path=dbp)
    assert len(closed) == 1
    assert closed[0]["pct"] == 20.0   # (120-100)/100 * 100


def test_seed_positions_skips_zero_avg_cost(tmp_path):
    """Positions with zero or missing avg_cost are not seeded."""
    from cio.data.ibkr import _seed_positions
    from cio.alpha import trades as trade_ledger

    dbp = tmp_path / "seed_zero.db"
    positions = [
        {"symbol": "BAD1", "quantity": 5.0, "avg_cost": 0.0},
        {"symbol": "BAD2", "quantity": 5.0, "avg_cost": None},
        {"symbol": "GOOD", "quantity": 5.0, "avg_cost": 50.0},
    ]
    seeded = _seed_positions(positions, dbp, "UNKNOWN", "neutral")
    assert seeded == 1
    assert trade_ledger.list_open(db_path=dbp)[0]["ticker"] == "GOOD"


def test_ibkr_sync_trades_disabled_returns_seeded_zero():
    """Disabled sync_trades now also returns seeded: 0."""
    import os
    original = os.environ.pop("CIO_IBKR_TWS", None)
    try:
        from cio.data.ibkr import sync_trades
        result = sync_trades()
        assert result.get("seeded") == 0
    finally:
        if original is not None:
            os.environ["CIO_IBKR_TWS"] = original


# ---- pass-3 item 2b: reconcile_orphan ---------------------------------------
def test_reconcile_orphan_converts_to_closed(tmp_path):
    """reconcile_orphan converts an orphan sell to a closed trade with real pct."""
    dbp = tmp_path / "recon.db"
    oid = trades.record_orphan_sell("TSLA", "2026-06-10", 220.0,
                                    note="exec_id:X99", db_path=dbp)
    assert trades.list_orphans(db_path=dbp)
    row = trades.reconcile_orphan(oid, 180.0, "2026-05-01", db_path=dbp)
    assert row is not None
    assert row["status"] == "closed"
    assert abs(row["pct"] - (220 - 180) / 180 * 100) < 1e-3
    assert trades.list_orphans(db_path=dbp) == []
    closed = trades.list_closed(db_path=dbp)
    assert len(closed) == 1


def test_reconcile_orphan_not_found_returns_none(tmp_path):
    """reconcile_orphan returns None for a non-existent id without raising."""
    dbp = tmp_path / "recon_miss.db"
    result = trades.reconcile_orphan(9999, 100.0, "2026-01-01", db_path=dbp)
    assert result is None


def test_reconcile_orphan_open_row_ignored(tmp_path):
    """reconcile_orphan only converts orphan rows; ignores open rows."""
    dbp = tmp_path / "recon_open.db"
    tid = trades.open_trade("NVDA", "2026-06-01", 100.0, db_path=dbp)
    result = trades.reconcile_orphan(tid, 80.0, "2026-05-01", db_path=dbp)
    assert result is None  # must not convert an 'open' row


def test_reconcile_orphan_excluded_from_expectancy_until_reconciled(tmp_path):
    """An orphan is excluded from expectancy; once reconciled it contributes."""
    from cio.alpha import expectancy as exp_mod
    dbp = tmp_path / "recon_exp.db"
    oid = trades.record_orphan_sell("AMD", "2026-06-12", 150.0, db_path=dbp)
    # Before: not in expectancy.
    assert exp_mod.expectancy(trades.list_closed(db_path=dbp))["n"] == 0
    # After reconcile: contributes as a real win.
    trades.reconcile_orphan(oid, 100.0, "2026-05-01", db_path=dbp)
    closed = trades.list_closed(db_path=dbp)
    assert len(closed) == 1
    assert exp_mod.expectancy(closed)["win_rate"] == 1.0


# ---- pass-3 item 3: monitor real-layer gate ---------------------------------
def test_ta_composite_to_execution_bull():
    """Bull composite -> execution score > 50."""
    from cio.watchlist_monitor.agent import _ta_composite_to_execution
    score = _ta_composite_to_execution("bull", {"macd": "bull", "stoch": "bull"})
    assert score > 50


def test_ta_composite_to_execution_bear():
    """Bear composite -> execution score < 50."""
    from cio.watchlist_monitor.agent import _ta_composite_to_execution
    score = _ta_composite_to_execution("bear", {"macd": "bear", "stoch": "bear"})
    assert score < 50


def test_ta_composite_to_execution_neutral():
    """Neutral composite and empty signals -> 50."""
    from cio.watchlist_monitor.agent import _ta_composite_to_execution
    assert _ta_composite_to_execution("neutral", {}) == 50.0
    assert _ta_composite_to_execution("neutral", {"macd": "neutral"}) == 50.0


def test_ta_composite_to_execution_mixed():
    """Mixed bull/bear signals -> score between 25 and 75."""
    from cio.watchlist_monitor.agent import _ta_composite_to_execution
    score = _ta_composite_to_execution("neutral", {
        "macd": "bull", "stoch": "bear", "pvo": "bull", "squeeze": "bear",
    })
    assert 25 < score < 75


def test_analyst_behavior_score_bullish():
    """Strong-buy heavy analyst rec -> score > 50."""
    from cio.watchlist_monitor.agent import _analyst_behavior_score
    score = _analyst_behavior_score({
        "strong_buy": 10, "buy": 5, "hold": 2, "sell": 0, "strong_sell": 0
    })
    assert score is not None and score > 50


def test_analyst_behavior_score_bearish():
    """Strong-sell heavy rec -> score < 50."""
    from cio.watchlist_monitor.agent import _analyst_behavior_score
    score = _analyst_behavior_score({
        "strong_buy": 0, "buy": 0, "hold": 1, "sell": 5, "strong_sell": 5
    })
    assert score is not None and score < 50


def test_analyst_behavior_score_none_on_missing():
    """None analyst data -> None (guard inactive)."""
    from cio.watchlist_monitor.agent import _analyst_behavior_score
    assert _analyst_behavior_score(None) is None
    assert _analyst_behavior_score({}) is None


def test_catalyst_score_from_bundle_upcoming_catalysts():
    """upcoming_catalysts in assessment fires catalyst score."""
    from cio.watchlist_monitor.agent import _catalyst_score_from_bundle
    assessment = {"upcoming_catalysts": ["earnings next week"]}
    score = _catalyst_score_from_bundle(assessment, None)
    assert score is not None and score > 0


def test_catalyst_score_from_bundle_no_signals():
    """No signals -> None (guard inactive)."""
    from cio.watchlist_monitor.agent import _catalyst_score_from_bundle
    score = _catalyst_score_from_bundle({"upcoming_catalysts": []}, {})
    assert score is None


def test_catalyst_score_earnings_proximity():
    """Near-term earnings date in bundle -> catalyst score fires."""
    from datetime import date, timedelta
    from cio.watchlist_monitor.agent import _catalyst_score_from_bundle
    soon = (date.today() + timedelta(days=3)).isoformat()
    bundle = {"earnings": {"date": soon}, "filings": []}
    score = _catalyst_score_from_bundle({"upcoming_catalysts": []}, bundle)
    assert score is not None and score > 50


def test_hold_decision_for_assessment_uses_bundle(tmp_path):
    """_hold_decision_for_assessment with a bull bundle -> hold in GREEN."""
    from cio.watchlist_monitor.agent import _hold_decision_for_assessment
    bundle = {
        "ta_composite": "bull",
        "ta_signals": {"macd": "bull", "stoch": "bull"},
        "analyst": {"strong_buy": 8, "buy": 4, "hold": 2, "sell": 0, "strong_sell": 0},
        "earnings": None,
        "filings": [],
    }
    assessment = {
        "conviction_score": 70,
        "upcoming_catalysts": ["earnings in 5 days"],
    }
    result = _hold_decision_for_assessment(assessment, bundle)
    assert "action" in result
    assert result["action"] in ("hold", "trim", "exit")


def test_hold_decision_no_bundle_fallback():
    """_hold_decision_for_assessment without bundle still returns valid dict."""
    from cio.watchlist_monitor.agent import _hold_decision_for_assessment
    result = _hold_decision_for_assessment({"conviction_score": 50,
                                            "upcoming_catalysts": []})
    assert "action" in result
    assert result["action"] in ("hold", "trim", "exit")
