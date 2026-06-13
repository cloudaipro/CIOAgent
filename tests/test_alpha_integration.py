"""Alpha Hunter — integration / edge / security tests (offline, synthetic).

Complements tests/test_alpha.py (units). Covers engine orchestration, store +
watchlist side effects, boundary behaviour, and symbol-injection safety. No network.
See docs/ALPHA-HUNTER-TEST-PLAN.md (sections 3-6).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cio import watchlist
from cio.alpha import (earnings, engine, momentum, quality, regime, scoring,
                       sectors, store, universe)


# ---- builders --------------------------------------------------------------
def _df(closes, *, volume=1_000_000, last_vol=None):
    idx = pd.date_range("2023-01-02", periods=len(closes), freq="B")
    close = np.array(closes, dtype=float)
    vols = np.full(len(closes), volume, dtype=float)
    if last_vol is not None:
        vols[-1] = last_vol
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Adj Close": close, "Volume": vols,
    }, index=idx)


def _up(n=260, start=50.0, step=0.4):
    return [start + step * i for i in range(n)]


def _good_fund():
    return {"market_cap": 5e9, "revenue_growth_pct": 40.0, "free_cash_flow": 1e8,
            "eps": 4.0, "forward_eps": 6.0}


# ---- R3 / R5: regime YELLOW + offline --------------------------------------
def test_regime_yellow():
    # Long uptrend (200MA far below price) then a ~8% pullback that dips under the
    # rising 50MA but holds well above the 200MA -> YELLOW (above 200, not full uptrend).
    base = _up(250, start=50.0, step=0.5)            # 50 -> 174.5
    closes = base + [base[-1] * 0.92] * 8            # pull back to ~160
    ry = regime.classify(pd.Series(closes, index=pd.date_range("2023-01-02", periods=len(closes), freq="B")))
    assert ry["status"] == "YELLOW"


def test_regime_evaluate_offline():
    assert regime.evaluate(fetch=lambda *a, **k: None)["status"] == "UNKNOWN"

    def boom(*a, **k):
        raise RuntimeError("network down")
    assert regime.evaluate(fetch=boom)["status"] == "UNKNOWN"


# ---- S: sectors ------------------------------------------------------------
def test_sectors_rank_order_and_drop():
    series = {
        "QQQ": _df(_up(200, step=0.1)),
        "SMH": _df(_up(200, step=0.5)),     # strongest
        "IGV": _df(_up(200, step=0.2)),
        "HACK": None,                        # fails to fetch -> dropped
        "BOTZ": _df(_up(200, step=0.05)),
    }

    def fetch(sym, start, end):
        return series.get(sym)

    ranked = sectors.rank(fetch)
    tickers = [s["ticker"] for s in ranked]
    assert "HACK" not in tickers
    assert ranked[0]["ticker"] == "SMH"            # highest RS first
    assert ranked == sorted(ranked, key=lambda s: s["rs"], reverse=True)


def test_sector_of():
    assert sectors.sector_of("nvda") == "SMH"
    assert sectors.sector_of("MSFT") == "IGV"
    assert sectors.sector_of("CRWD") == "HACK"
    assert sectors.sector_of("ZZZZ") == "QQQ"


# ---- Q: quality each gate fails closed -------------------------------------
@pytest.mark.parametrize("mutate,token", [
    (lambda f: f.update(market_cap=1e9), "cap"),
    (lambda f: f.update(revenue_growth_pct=5.0), "rev"),
    (lambda f: f.update(forward_eps=4.2), "fwdEPS"),   # 5% growth < 15%
    (lambda f: f.update(free_cash_flow=-1.0), "fcf"),
])
def test_quality_each_gate(mutate, token):
    f = _good_fund()
    mutate(f)
    q = quality.evaluate(f, _df(_up(60), volume=2_000_000))
    assert q["pass"] is False
    assert any(token in r for r in q["reasons"])


def test_quality_dollar_volume_gate():
    q = quality.evaluate(_good_fund(), _df(_up(60), volume=10_000))  # tiny $vol
    assert q["pass"] is False
    assert any("$vol" in r for r in q["reasons"])


def test_quality_forward_eps_growth_negative_base():
    f = _good_fund(); f["eps"] = -1.0
    assert quality.forward_eps_growth(f) is None


def test_avg_dollar_volume_short():
    assert quality.avg_dollar_volume(_df(_up(10)), window=20) is None


# ---- E: earnings boundaries ------------------------------------------------
def test_gap_exactly_five_percent_not_counted():
    # Open exactly +5% over prior close must NOT trigger (strict >).
    closes = [100.0] * 20 + [105.0] + [106.0] * 14
    df = _df(closes)
    df.loc[df.index[20], "Open"] = 100.0 * 1.05   # exactly 5%
    assert earnings.revision_signal(df) == 0.0


def test_surprise_all_ratios():
    mk = lambda n: [{"beat": True}] * n + [{"beat": False}] * (4 - n)
    assert [earnings.surprise_score(mk(n)) for n in range(5)] == [0.0, 25.0, 50.0, 75.0, 100.0]


# ---- M: momentum -----------------------------------------------------------
def test_momentum_partial_trend():
    s = pd.Series(_up(260, step=0.4), index=pd.date_range("2023-01-02", periods=260, freq="B"))
    m = momentum.evaluate(s, qqq_ret_3m=0.0, qqq_ret_6m=0.0)
    assert m["trend_score"] == 100.0
    m2 = momentum.evaluate(pd.Series(_up(60)), qqq_ret_3m=0.0, qqq_ret_6m=0.0)
    assert m2["trend_score"] in (0.0, 33.3, 66.7)   # not enough history for full template


def test_momentum_parity_score():
    # Stock matches QQQ exactly -> excess 0 -> score ~50, rs_pass False (not strictly >).
    s = pd.Series(_up(260, step=0.4), index=pd.date_range("2023-01-02", periods=260, freq="B"))
    r3 = (s.iloc[-1] - s.iloc[-1 - 63]) / s.iloc[-1 - 63] * 100
    r6 = (s.iloc[-1] - s.iloc[-1 - 126]) / s.iloc[-1 - 126] * 100
    m = momentum.evaluate(s, qqq_ret_3m=r3, qqq_ret_6m=r6)
    assert m["rs_pass"] is False
    assert m["momentum_score"] == pytest.approx(50.0, abs=0.5)


# ---- C: scoring bounds -----------------------------------------------------
def test_final_score_max_bounded():
    df = _df(_up(60), volume=1_000_000, last_vol=10_000_000)
    s = scoring.final_score(100.0, 100.0, 100.0, 100.0, df)
    assert s["final"] <= 100.0
    assert s["volume_expansion"] <= 100.0


def test_volume_expansion_at_parity():
    assert scoring.volume_expansion(_df(_up(40), volume=1_000_000)) == 0.0


# ---- engine integration ----------------------------------------------------
def _fetch_factory(strong_step=0.6, weak_step=0.02, qqq=None):
    q = qqq if qqq is not None else _df(_up(260, start=300, step=0.3))
    strong = _df(_up(260, start=50, step=strong_step), volume=3_000_000)
    weak = _df(_up(260, start=50, step=weak_step), volume=3_000_000)

    def fetch(sym, start, end):
        if sym == "QQQ":
            return q
        if sym in ("SMH", "IGV", "HACK", "BOTZ"):
            return _df(_up(260, start=100, step=0.3))
        return strong if sym == "STRONG" else weak
    return fetch


def test_engine_excludes_quality_fail(tmp_path):
    uni = tmp_path / "u.txt"; uni.write_text("STRONG\nWEAK\n")

    def funds(sym):
        f = _good_fund()
        if sym == "WEAK":
            f["market_cap"] = 1e9   # WEAK fails quality
        return f

    res = engine.run(universe_path=str(uni), fetch=_fetch_factory(),
                     fundamentals_fn=funds, surprises_fn=lambda s: None)
    tickers = [c["ticker"] for c in res.candidates]
    assert "STRONG" in tickers and "WEAK" not in tickers


def test_engine_partial_qqq_none_no_crash(tmp_path):
    uni = tmp_path / "u.txt"; uni.write_text("STRONG\n")
    strong = _df(_up(260, start=50, step=0.6), volume=3_000_000)

    def fetch(sym, start, end):
        return None if sym in ("QQQ", "SMH", "IGV", "HACK", "BOTZ") else strong

    res = engine.run(universe_path=str(uni), fetch=fetch,
                     fundamentals_fn=lambda s: _good_fund(),
                     surprises_fn=lambda s: None)
    assert res.regime["status"] == "UNKNOWN"
    # STRONG still passes quality and gets ranked (momentum degrades, no exception).
    assert any(c["ticker"] == "STRONG" for c in res.candidates)


# ---- store integration -----------------------------------------------------
def _result(date="2026-06-12", candidates=None, sectors_=None):
    return engine.AlphaResult(
        run_date=date, regime={"status": "GREEN", "detail": "x"},
        sectors=sectors_ or [], candidates=candidates or [], universe_size=len(candidates or []))


def test_save_run_no_publish(tmp_path):
    dbf = tmp_path / "t.db"
    res = _result(candidates=[{"rank": 1, "ticker": "AAA", "final": 1.0}])
    meta = store.save_run(res, publish=False, db_path=dbf)
    assert meta["watchlist_name"] is None
    assert watchlist.find_by_name("Alpha-2026-06-12", db_path=dbf) is None
    assert store.latest_run(db_path=dbf)["candidate_count"] == 1


def test_top_n_cap(tmp_path):
    dbf = tmp_path / "t.db"
    cands = [{"rank": i, "ticker": f"T{i:02d}", "final": 100 - i} for i in range(1, 26)]
    res = _result(candidates=cands)
    store.save_run(res, db_path=dbf)            # default top_n=20
    latest = store.latest_run(db_path=dbf)
    assert latest["candidate_count"] == 20
    assert len(latest["candidates"]) == 20
    wl = watchlist.find_by_name("Alpha-2026-06-12", db_path=dbf)
    non_index = [s for s in wl["symbols"] if s != watchlist.NASDAQ_INDEX]
    assert len(non_index) == 20


def test_empty_candidates_publishes_index_only(tmp_path):
    dbf = tmp_path / "t.db"
    meta = store.save_run(_result(candidates=[]), db_path=dbf)
    wl = watchlist.find_by_name(meta["watchlist_name"], db_path=dbf)
    assert wl["symbols"] == [watchlist.NASDAQ_INDEX]
    assert store.latest_run(db_path=dbf)["candidate_count"] == 0


def test_latest_run_roundtrip(tmp_path):
    dbf = tmp_path / "t.db"
    res = _result(
        candidates=[{"rank": 1, "ticker": "AAA", "sector": "SMH", "final": 9.0,
                     "momentum": 80, "trend": 100, "earnings": 60,
                     "revenue_growth": 40, "fwd_eps_growth": 25, "surprise": 75,
                     "volume_expansion": 50, "quality_pass": True}],
        sectors_=[{"ticker": "SMH", "rs": 12.0, "ret_3m": 5.0, "ret_6m": 19.0}])
    store.save_run(res, db_path=dbf)
    latest = store.latest_run(db_path=dbf)
    assert latest["sectors"][0]["ticker"] == "SMH"
    assert latest["candidates"][0]["sector"] == "SMH"


# ---- run_and_save end to end -----------------------------------------------
def test_run_and_save_end_to_end(tmp_path, monkeypatch):
    from cio import alpha as alpha_pkg
    uni = tmp_path / "u.txt"; uni.write_text("STRONG\nWEAK\n")
    dbf = tmp_path / "t.db"
    res, meta = alpha_pkg.run_and_save(
        db_path=dbf, universe_path=str(uni), fetch=_fetch_factory(),
        fundamentals_fn=lambda s: _good_fund(), surprises_fn=lambda s: [{"beat": True}] * 4)
    assert meta["watchlist_name"].startswith("Alpha-")
    wl = watchlist.find_by_name(meta["watchlist_name"], db_path=dbf)
    assert wl["is_active"] == 1 and "STRONG" in wl["symbols"]


# ---- edge: universe --------------------------------------------------------
def test_universe_fallback_on_bad_path():
    syms = universe.load("/no/such/file/xyz.txt")
    assert syms and all(s.isupper() or "." in s for s in syms)


def test_universe_parse_strips_and_dedupes(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("# header\n\n  aapl  \nMSFT # inline\naapl\n^ixic\n")
    syms = universe.load(str(f))
    assert syms == ["AAPL", "MSFT", "^IXIC"]


# ---- edge: naming rule -----------------------------------------------------
def test_naming_rule_format():
    assert store.watchlist_name("2026-06-12") == "Alpha-2026-06-12"


# ---- edge: set_symbols -----------------------------------------------------
def test_set_symbols_index_floor_dedupe_replace(tmp_path):
    dbf = tmp_path / "t.db"
    wid = watchlist.create("L", db_path=dbf)
    watchlist.add_symbol(wid, "OLD", db_path=dbf)
    out = watchlist.set_symbols(wid, ["nvda", "NVDA", "AAPL"], db_path=dbf)
    assert out[0] == watchlist.NASDAQ_INDEX          # floor first
    assert out.count("NVDA") == 1                     # de-duped
    assert "OLD" not in out                           # replaced, not appended
    assert "AAPL" in out


# ---- security --------------------------------------------------------------
def test_universe_sanitizes_hostile_symbols(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("../../etc/passwd\nAA/PL\n..\nNVDA\n")
    for s in universe.load(str(f)):
        assert "/" not in s and ".." not in s        # no path traversal survives


def test_index_floor_cannot_be_removed(tmp_path):
    dbf = tmp_path / "t.db"
    wid = watchlist.create("L", db_path=dbf)
    with pytest.raises(watchlist.WatchlistError):
        watchlist.remove_symbol(wid, watchlist.NASDAQ_INDEX, db_path=dbf)


def test_report_renders_without_raise():
    from cio.alpha import report
    res = _result(candidates=[{"rank": 1, "ticker": "AAA", "final": 9.0,
                               "momentum": 80, "trend": 100, "earnings": 60}])
    txt = report.format_telegram(res, {"watchlist_name": "Alpha-2026-06-12"})
    assert "Alpha-2026-06-12" in txt and "AAA" in txt
    assert "No candidates" in report.format_telegram(_result(candidates=[]), None)
