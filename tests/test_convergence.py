"""F10 — cross-source convergence tests (cio/convergence.py).

Deterministic + offline: factor extractors are pure; convergence() with no keys
makes no network call and returns a no_signal result.
"""
import pytest

from cio import convergence as cv


# --- individual factors -----------------------------------------------------

def test_ta_factor_directions():
    assert cv._ta_factor("bull")["direction"] == 1
    assert cv._ta_factor("bear")["direction"] == -1
    f = cv._ta_factor("neutral")
    assert f["direction"] == 0 and f["active"] is False


def test_analyst_factor_delta():
    up = [{"strong_buy": 12, "buy": 20, "hold": 5, "sell": 1, "strong_sell": 0},
          {"strong_buy": 8, "buy": 18, "hold": 6, "sell": 2, "strong_sell": 1}]
    assert cv._analyst_factor(up)["direction"] == 1
    down = list(reversed(up))
    assert cv._analyst_factor(down)["direction"] == -1
    assert cv._analyst_factor(None)["active"] is False
    assert cv._analyst_factor(up[:1])["active"] is False     # need 2 snapshots


def test_earnings_factor():
    assert cv._earnings_factor([{"actual": 1.6, "estimate": 1.5, "beat": True}])["direction"] == 1
    assert cv._earnings_factor([{"actual": 1.3, "estimate": 1.5, "beat": False}])["direction"] == -1
    assert cv._earnings_factor(None)["active"] is False


def test_insider_factor():
    assert cv._insider_factor({"cluster_buy": True, "buy_count": 4})["direction"] == 1
    sell = {"cluster_buy": False, "buy_count": 0, "sell_count": 5, "net_shares": -1000}
    assert cv._insider_factor(sell)["direction"] == -1
    assert cv._insider_factor(None)["active"] is False


def test_news_factor_tone_sign():
    assert cv._news_factor({"avg_tone": 3.0, "multiplier": 5.0})["direction"] == 1
    assert cv._news_factor({"avg_tone": -2.5, "multiplier": None})["direction"] == -1
    # spike present but neutral tone -> active False (catalyst, unclear sign)
    assert cv._news_factor({"avg_tone": 0.2})["active"] is False
    assert cv._news_factor(None)["active"] is False


# --- blend ------------------------------------------------------------------

def _f(name, d, active=True, w=1.0):
    return {"name": name, "direction": d, "active": active, "weight": w, "detail": ""}


def test_blend_no_active_is_no_signal():
    out = cv.blend([_f("ta", 0, active=False)], "AAPL")
    assert out["label"] == "no_signal" and out["conviction"] == "none"
    assert out["score"] == 0 and out["active_count"] == 0


def test_blend_strong_bullish_high_conviction():
    # TA(+2) + insider(+2) + earnings(+1) all bullish -> score +100, all agree.
    factors = [_f("ta", 1, w=2), _f("insider", 1, w=2), _f("earnings", 1, w=1)]
    out = cv.blend(factors, "NVDA")
    assert out["score"] == 100
    assert out["label"] == "strong_bullish"
    assert out["agreement"] == 1.0
    assert out["conviction"] == "high"          # 3 active, full agreement, |score|>=50
    assert set(out["agree_factors"]) == {"ta", "insider", "earnings"}


def test_blend_mixed_low_agreement():
    # opposing votes cancel -> mixed, low conviction.
    out = cv.blend([_f("ta", 1, w=2), _f("insider", -1, w=2)], "X")
    assert out["score"] == 0 and out["label"] == "mixed"


def test_blend_partial_bullish_medium():
    # TA(+2) + analyst(+1) bullish, earnings(-1) against -> net +, medium conviction.
    out = cv.blend([_f("ta", 1, w=2), _f("analyst", 1, w=1), _f("earnings", -1, w=1)], "X")
    assert out["score"] > 0 and out["label"] in ("bullish", "strong_bullish")
    assert out["conviction"] in ("medium", "high")


# --- end-to-end convergence() offline ---------------------------------------

def test_convergence_offline_no_keys_no_network(monkeypatch):
    for k in ("FINNHUB_API_KEY",):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CIO_GDELT_ENABLED", "0")
    # Any network attempt should blow up; offline path must not reach it.
    import cio.data._http as http
    monkeypatch.setattr(http, "get_json",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    out = cv.convergence("AAPL", ta_composite="neutral", include_news=True)
    assert out["label"] == "no_signal"
    assert out["conviction"] == "none"


def test_convergence_blends_passed_inputs(monkeypatch):
    # Provide TA + insider directly; analyst/earnings/news self-gate to inactive
    # (no finnhub key, GDELT off) -> result driven by the two supplied bullish signals.
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setenv("CIO_GDELT_ENABLED", "0")
    out = cv.convergence("AAPL", ta_composite="bull",
                         insider={"cluster_buy": True, "buy_count": 3},
                         include_news=False)
    assert out["score"] == 100 and out["label"] == "strong_bullish"
    assert out["active_count"] == 2


def test_format_line():
    out = cv.blend([_f("ta", 1, w=2), _f("insider", 1, w=2)], "NVDA")
    line = cv.format_line(out)
    assert line.startswith("CONVERGENCE: strong_bullish") and "score=+100" in line
    assert cv.format_line({"active_count": 0}) == "CONVERGENCE: N/A (no active signals)"


# --- committee bundle integration -------------------------------------------

def test_bundle_renders_convergence_line():
    from cio.committee.bundle import format_bundle
    conv = cv.blend([_f("ta", 1, w=2), _f("insider", 1, w=2)], "AAPL")
    b = {"symbol": "AAPL", "quote": {"close": 1.0}, "fundamentals": {"name": "Apple"},
         "ta_signals": {}, "is_etf": False, "as_of": "x",
         "filings": [], "analyst": None, "earnings": None, "insider": None,
         "convergence": conv}
    txt = format_bundle(b)
    assert "CONVERGENCE: strong_bullish" in txt


def test_bundle_convergence_na_when_absent():
    from cio.committee.bundle import format_bundle
    b = {"symbol": "AAPL", "quote": {"close": 1.0}, "fundamentals": {"name": "Apple"},
         "ta_signals": {}, "is_etf": False, "as_of": "x",
         "filings": [], "analyst": None, "earnings": None, "insider": None,
         "convergence": None}
    assert "CONVERGENCE: N/A (no active signals)" in format_bundle(b)
