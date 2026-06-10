"""
Tests for cio.stock.profiles — situation-specific strategy profiles.

Offline: all strategy runs use synthetic OHLCV DataFrames.
"""
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import numpy as np
import pandas as pd
import pytest

import cio.stock as s
from cio.stock import profiles
from tests.conftest import make_ohlcv


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_profiles_exist():
    assert set(profiles.PROFILES) == {"committee", "monitor", "swing"}


def test_aliases_resolve():
    assert profiles.resolve_profile("wave") == "swing"
    assert profiles.resolve_profile("wma") == "monitor"
    assert profiles.resolve_profile("watchlist") == "monitor"
    assert profiles.resolve_profile(None) == "committee"
    assert profiles.resolve_profile("COMMITTEE") == "committee"


def test_unknown_profile_raises():
    with pytest.raises(KeyError, match="unknown strategy profile"):
        profiles.resolve_profile("scalping")


def test_profile_strategies_all_registered():
    """Every strategy named in a profile must exist in the engine."""
    available = set(s.list_strategies())
    for name, spec in profiles.PROFILES.items():
        missing = [x for x in spec["strategies"] if x not in available]
        assert not missing, f"profile {name} references unknown strategies {missing}"


def test_no_same_family_redundancy_in_committee():
    """Regression: old hardcoded set stacked rsi+stoch+kdj (same momentum family)."""
    strategies = set(profiles.PROFILES["committee"]["strategies"])
    same_family = {"rsi", "stoch", "kdj", "rsx", "willr"}
    assert len(strategies & same_family) <= 1, (
        "committee profile must not stack multiple stochastic-family oscillators"
    )


def test_list_profiles_facade():
    d = s.list_strategy_profiles()
    assert set(d) == {"committee", "monitor", "swing"}
    assert all(isinstance(v, str) and v for v in d.values())


# ---------------------------------------------------------------------------
# summarize_signals
# ---------------------------------------------------------------------------

def _df_with_events(bull_rows=(), bear_rows=(), n=10):
    idx = pd.date_range("2026-01-01", periods=n)
    df = pd.DataFrame(0, index=idx, columns=["c_X_CROSSOVER_BULL", "c_X_CROSSOVER_BEAR"])
    for r in bull_rows:
        df.iloc[r, 0] = 1
    for r in bear_rows:
        df.iloc[r, 1] = 1
    return df


def test_summarize_bull_event_in_window():
    out = profiles.summarize_signals(_df_with_events(bull_rows=[-2]), window=5)
    assert out["verdict"] == "bull"
    assert out["bulls"] == 1 and out["bears"] == 0
    assert out["events"] == ["c_X_CROSSOVER_BULL"]


def test_summarize_event_outside_window_ignored():
    out = profiles.summarize_signals(_df_with_events(bull_rows=[0]), window=3)
    assert out["verdict"] == "neutral"


def test_summarize_bear_majority():
    out = profiles.summarize_signals(
        _df_with_events(bull_rows=[-1], bear_rows=[-1, -2]), window=5
    )
    assert out["verdict"] == "bear"


def test_summarize_feature_only_fallback():
    """Strategies without event columns (e.g. fisher) use f_ feature sign."""
    idx = pd.date_range("2026-01-01", periods=5)
    df = pd.DataFrame({"f_FISHER_CSLS": [1, 2, 3, 4, 5]}, index=idx, dtype=float)
    assert profiles.summarize_signals(df)["verdict"] == "bull"
    df2 = -df
    assert profiles.summarize_signals(df2)["verdict"] == "bear"


def test_summarize_garbage_input_neutral():
    assert profiles.summarize_signals(None)["verdict"] == "neutral"
    assert profiles.summarize_signals("not a frame")["verdict"] == "neutral"
    assert profiles.summarize_signals(pd.DataFrame())["verdict"] == "neutral"


# ---------------------------------------------------------------------------
# profile_signals end-to-end (synthetic data, no network)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("profile", ["committee", "monitor", "swing"])
def test_profile_signals_shape(profile):
    df = make_ohlcv(350)
    res = s.run_strategy_profile(df, profile)
    assert res["profile"] == profile
    assert res["composite"] in {"bull", "bear", "neutral"}
    expected = set(profiles.PROFILES[profile]["strategies"])
    assert set(res["signals"]) == expected
    assert all(v in {"bull", "bear", "neutral"} for v in res["signals"].values())
    assert set(res["detail"]) == expected


def test_profile_signals_not_all_neutral():
    """Regression: old bundle._latest_signal always returned neutral."""
    df = make_ohlcv(350)
    any_signal = False
    for profile in profiles.PROFILES:
        res = s.run_strategy_profile(df, profile)
        if any(v != "neutral" for v in res["signals"].values()):
            any_signal = True
    assert any_signal, "all profiles all-neutral on 350 bars of random walk — summarizer broken?"


def test_wave_alias_via_facade():
    df = make_ohlcv(350)
    assert s.run_strategy_profile(df, "wave")["profile"] == "swing"


# ---------------------------------------------------------------------------
# bundle integration
# ---------------------------------------------------------------------------

def test_format_bundle_renders_composite_and_profile():
    from cio.committee.bundle import format_bundle
    bundle = {
        "symbol": "X", "resolved": "X", "as_of": "now",
        "quote": {"close": 1.0}, "fundamentals": None,
        "ta_signals": {"trix": "bull", "rsi": "neutral"},
        "ta_profile": "committee", "ta_composite": "bull",
        "is_etf": False, "filings": [], "analyst": None, "earnings": None,
    }
    text = format_bundle(bundle)
    assert "trix:bull" in text
    assert "composite:bull" in text and "profile:committee" in text


def test_format_bundle_legacy_shape_still_works():
    """Old bundles without ta_profile/ta_composite keys must still render."""
    from cio.committee.bundle import format_bundle
    bundle = {
        "symbol": "X", "resolved": "X", "as_of": "now",
        "quote": None, "fundamentals": None,
        "ta_signals": {"rsi": "bull"},
        "is_etf": False, "filings": [], "analyst": None, "earnings": None,
    }
    text = format_bundle(bundle)
    assert "rsi:bull" in text


def test_gather_bundle_uses_profile(monkeypatch):
    """gather_bundle(symbol, profile=...) must route through run_strategy_profile."""
    from cio.committee import bundle as b

    df = make_ohlcv(350)
    calls = {}

    class FakeStock:
        @staticmethod
        def normalize_symbol(x):
            return x

        @staticmethod
        def get_quote(x):
            return {"close": 100.0}

        @staticmethod
        def fundamentals(x):
            return {}

        @staticmethod
        def run_strategy_profile(sym, profile):
            calls["profile"] = profile
            return {"profile": profile, "signals": {"macd": "bull"},
                    "detail": {}, "composite": "bull"}

    monkeypatch.setattr(b, "_stock", FakeStock)
    monkeypatch.setattr(b, "_external", lambda sym, etf: ([], None, None))

    out = b.gather_bundle("AAPL", profile="monitor")
    assert calls["profile"] == "monitor"
    assert out["ta_signals"] == {"macd": "bull"}
    assert out["ta_composite"] == "bull"
    assert out["ta_profile"] == "monitor"
