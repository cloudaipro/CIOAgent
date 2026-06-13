"""Alpha Hunter — offline unit tests (no network).

Every layer is exercised with synthetic OHLCV/fundamentals so the suite stays
offline and deterministic (PRD §10). The engine + store are driven with injected
fetchers and a temp DB.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cio import db, watchlist
from cio.alpha import (earnings, engine, metrics, momentum, quality, regime,
                       scoring, store, universe)


# ---- synthetic data helpers ------------------------------------------------
def _series(values):
    idx = pd.date_range("2023-01-02", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


def _df(closes, *, volume=1_000_000, last_vol=None, gap_at=None):
    """OHLCV frame from a close list. gap_at=(i, pct) injects an open gap-up."""
    idx = pd.date_range("2023-01-02", periods=len(closes), freq="B")
    close = np.array(closes, dtype=float)
    open_ = close.copy()
    if gap_at is not None:
        i, pct = gap_at
        open_[i] = close[i - 1] * (1 + pct / 100.0)
    vols = np.full(len(closes), volume, dtype=float)
    if last_vol is not None:
        vols[-1] = last_vol
    return pd.DataFrame({
        "Open": open_, "High": np.maximum(open_, close) * 1.01,
        "Low": np.minimum(open_, close) * 0.99, "Close": close,
        "Adj Close": close, "Volume": vols,
    }, index=idx)


def _uptrend(n=260, start=50.0, step=0.4):
    return [start + step * i for i in range(n)]


# ---- metrics ---------------------------------------------------------------
def test_sma_and_returns():
    s = _series(_uptrend(260))
    assert metrics.sma(s, 50) is not None
    assert metrics.sma(s, 300) is None
    assert metrics.ret_pct(s, metrics.BARS_3M) > 0
    assert metrics.slope_up(s, 50) is True


def test_scale_bounds():
    assert metrics.scale(None, 50) == 0.0
    assert metrics.scale(0, 50) == 0.0
    assert metrics.scale(50, 50) == 100.0
    assert metrics.scale(25, 50) == 50.0


# ---- Layer 0: regime -------------------------------------------------------
def test_regime_green():
    r = regime.classify(_series(_uptrend(260)))
    assert r["status"] == "GREEN"
    assert r["slope_up"] is True


def test_regime_red():
    # Flat plateau then a deep, sustained decline so price ends below the 200MA.
    closes = [100.0] * 200 + [100.0 - i for i in range(60)]   # last ~41
    r = regime.classify(_series(closes))
    assert r["status"] == "RED"


def test_regime_unknown_on_short():
    assert regime.classify(_series(_uptrend(20)))["status"] == "UNKNOWN"


# ---- Layer 2: quality ------------------------------------------------------
def _good_fund():
    return {"market_cap": 5e9, "revenue_growth_pct": 30.0, "free_cash_flow": 1e8,
            "eps": 4.0, "forward_eps": 5.0}   # fwd growth = 25%


def test_quality_pass():
    q = quality.evaluate(_good_fund(), _df(_uptrend(60), volume=2_000_000))
    assert q["pass"] is True
    assert q["fwd_eps_growth"] == pytest.approx(25.0)


def test_quality_fails_closed_on_missing():
    q = quality.evaluate({}, _df(_uptrend(60)))
    assert q["pass"] is False
    assert q["reasons"]


def test_quality_fails_low_cap():
    f = _good_fund(); f["market_cap"] = 1e9
    assert quality.evaluate(f, _df(_uptrend(60), volume=2_000_000))["pass"] is False


# ---- Layer 2.5: earnings ---------------------------------------------------
def test_revision_signal_unfilled_gap():
    # Flat then a +8% gap that never fills.
    closes = [100] * 20 + [110 + i for i in range(15)]
    df = _df(closes, gap_at=(20, 8.0))
    assert earnings.revision_signal(df) == 100.0


def test_revision_signal_none():
    assert earnings.revision_signal(_df(_uptrend(60))) == 0.0


def test_surprise_score_beats():
    s = [{"beat": True}] * 4
    assert earnings.surprise_score(s) == 100.0
    assert earnings.surprise_score([{"beat": True}, {"beat": True}]) == 50.0
    assert earnings.surprise_score([{"beat": True}, {"beat": False}]) == 25.0
    assert earnings.surprise_score(None) == 0.0


def test_earnings_combine():
    e = earnings.evaluate(40.0, _df(_uptrend(60)), [{"beat": True}] * 4)
    assert 0 <= e["earnings_score"] <= 100
    assert e["surprise_score"] == 100.0


# ---- Layer 3: momentum -----------------------------------------------------
def test_momentum_rs_and_trend():
    s = _series(_uptrend(260))
    m = momentum.evaluate(s, qqq_ret_3m=1.0, qqq_ret_6m=1.0)
    assert m["rs_pass"] is True            # steep uptrend beats a tame QQQ
    assert m["trend_score"] == 100.0
    assert m["momentum_score"] > 50


def test_momentum_no_rs_when_lagging():
    s = _series(_uptrend(260, step=0.05))   # barely rising
    m = momentum.evaluate(s, qqq_ret_3m=999.0, qqq_ret_6m=999.0)
    assert m["rs_pass"] is False


# ---- Layer 4: scoring ------------------------------------------------------
def test_final_score_weighting():
    df = _df(_uptrend(60), volume=1_000_000, last_vol=2_000_000)
    s = scoring.final_score(80.0, 100.0, 60.0, 40.0, df)
    # 0.3*80+0.2*100+0.3*60+0.1*rev+0.1*vol
    assert 0 < s["final"] <= 100
    # Latest bar 2x the others; the 20-day avg includes that bar -> ~1.9x -> ~90.
    assert s["volume_expansion"] == pytest.approx(90.48, abs=0.5)


# ---- engine (offline, injected fetchers) -----------------------------------
def _fake_fetch_factory():
    qqq = _df(_uptrend(260, start=300, step=0.3))
    strong = _df(_uptrend(260, start=50, step=0.6), volume=3_000_000)
    weak = _df(_uptrend(260, start=50, step=0.02), volume=3_000_000)

    def fetch(sym, start, end):
        if sym == "QQQ":
            return qqq
        if sym in ("SMH", "IGV", "HACK", "BOTZ"):
            return _df(_uptrend(260, start=100, step=0.3))
        return strong if sym == "STRONG" else weak
    return fetch


def test_engine_run_ranks_passing(tmp_path):
    uni = tmp_path / "u.txt"
    uni.write_text("STRONG\nWEAK\n")

    def funds(sym):
        return {"market_cap": 5e9, "revenue_growth_pct": 40.0, "free_cash_flow": 1e8,
                "eps": 4.0, "forward_eps": 6.0}

    res = engine.run(universe_path=str(uni), fetch=_fake_fetch_factory(),
                     fundamentals_fn=funds, surprises_fn=lambda s: [{"beat": True}] * 4)
    assert res.regime["status"] == "GREEN"
    assert res.universe_size == 2
    tickers = [c["ticker"] for c in res.candidates]
    assert "STRONG" in tickers
    # STRONG out-ranks WEAK (higher momentum/trend).
    assert res.candidates[0]["ticker"] == "STRONG"
    assert res.candidates[0]["rank"] == 1


def test_engine_offline_degrades():
    res = engine.run(universe_path=None, fetch=lambda *a, **k: None,
                     fundamentals_fn=lambda s: {}, surprises_fn=lambda s: None)
    assert res.regime["status"] == "UNKNOWN"
    assert res.candidates == []


# ---- store + watchlist publish ---------------------------------------------
def test_save_run_publishes_dated_watchlist(tmp_path):
    dbf = tmp_path / "t.db"
    res = engine.AlphaResult(
        run_date="2026-06-12",
        regime={"status": "GREEN", "detail": "x"},
        sectors=[{"ticker": "SMH", "rs": 10, "ret_3m": 5, "ret_6m": 15}],
        candidates=[{"rank": 1, "ticker": "STRONG", "sector": "SMH", "momentum": 80,
                     "trend": 100, "earnings": 60, "revenue_growth": 40,
                     "fwd_eps_growth": 25, "surprise": 100, "volume_expansion": 50,
                     "final": 77.0, "quality_pass": True}],
        universe_size=2)
    meta = store.save_run(res, db_path=dbf)
    assert meta["watchlist_name"] == "Alpha-2026-06-12"

    wl = watchlist.find_by_name("Alpha-2026-06-12", db_path=dbf)
    assert wl["is_active"] == 1
    assert "STRONG" in wl["symbols"]
    assert watchlist.NASDAQ_INDEX in wl["symbols"]   # benchmark floor kept

    latest = store.latest_run(db_path=dbf)
    assert latest["regime"] == "GREEN"
    assert latest["candidates"][0]["ticker"] == "STRONG"


def test_publish_is_idempotent_same_day(tmp_path):
    dbf = tmp_path / "t.db"
    res = engine.AlphaResult(run_date="2026-06-12", regime={"status": "GREEN"},
                             candidates=[{"rank": 1, "ticker": "AAA", "final": 1.0}],
                             universe_size=1)
    m1 = store.save_run(res, db_path=dbf)
    res2 = engine.AlphaResult(run_date="2026-06-12", regime={"status": "GREEN"},
                              candidates=[{"rank": 1, "ticker": "BBB", "final": 2.0}],
                              universe_size=1)
    m2 = store.save_run(res2, db_path=dbf)
    assert m1["watchlist_id"] == m2["watchlist_id"]   # same dated list, refreshed
    wl = watchlist.find_by_name("Alpha-2026-06-12", db_path=dbf)
    assert "BBB" in wl["symbols"] and "AAA" not in wl["symbols"]
