"""
Tests for the continuous, state-based composite (cio.stock.signal_state +
profiles.profile_signals) that replaced the event-window vote.

Covers the four fixes from conv_turns 304-311:
  L1 confirmed-bar gate (no intraday repaint)
  L2 events are supplementary, never scored
  L3 continuous confidence + dead-zone (no event-window cliff)
  L4 stability / fresh-flip flag

No network: synthetic OHLCV.
"""
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

import cio.stock as s
from cio.stock import profiles
from cio.stock.signal_state import (
    strategy_state, verdict_from_confidence, composite_score, DEAD_ZONE,
)
from tests.conftest import make_ohlcv

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- #
# L3 — continuous confidence + dead-zone
# --------------------------------------------------------------------------- #
def test_verdict_dead_zone():
    assert verdict_from_confidence(DEAD_ZONE + 0.01) == "bull"
    assert verdict_from_confidence(-DEAD_ZONE - 0.01) == "bear"
    assert verdict_from_confidence(0.0) == "neutral"
    assert verdict_from_confidence(DEAD_ZONE / 2) == "neutral"   # marginal -> neutral
    assert verdict_from_confidence(None) == "neutral"


def test_composite_score_is_mean():
    assert composite_score([0.4, -0.2, None, 0.0]) == pytest.approx(0.0666, abs=1e-3)
    assert composite_score([None, None]) is None


def test_strategy_state_confidence_in_range():
    df = make_ohlcv(300, seed=7)
    for name in ("efi", "kdj", "fisher", "squeeze", "vidya"):
        st = strategy_state(df, name)
        assert st is not None, name
        assert -1.0 <= st["confidence"] <= 1.0
        assert st["level"] in {"pos", "neg", "zero"}


def test_composite_score_matches_detail_confidences():
    df = make_ohlcv(320, seed=3)
    res = s.run_strategy_profile(df, "swing", confirmed_only=False)
    confs = [d["confidence"] for d in res["detail"].values() if d["confidence"] is not None]
    assert res["composite_score"] == pytest.approx(round(float(np.mean(confs)), 3), abs=1e-3)


# --------------------------------------------------------------------------- #
# L1 — confirmed-bar gate (no intraday repaint)
# --------------------------------------------------------------------------- #
def test_is_unconfirmed_predicate():
    d = dt.date(2026, 6, 17)
    assert profiles._is_unconfirmed(d, dt.datetime(2026, 6, 17, 11, tzinfo=ET)) is True
    assert profiles._is_unconfirmed(d, dt.datetime(2026, 6, 17, 17, tzinfo=ET)) is False
    assert profiles._is_unconfirmed(dt.date(2026, 6, 16),
                                    dt.datetime(2026, 6, 17, 11, tzinfo=ET)) is False


def _df_with_today_last_bar(seed=5):
    df = make_ohlcv(320, seed=seed)
    idx = list(df.index[:-1]) + [pd.Timestamp(dt.date(2026, 6, 17))]
    df.index = pd.DatetimeIndex(idx)
    return df


def test_confirmed_view_drops_live_bar_intraday():
    df = _df_with_today_last_bar()
    intraday = dt.datetime(2026, 6, 17, 11, tzinfo=ET)
    postclose = dt.datetime(2026, 6, 17, 17, tzinfo=ET)
    view_i, _ = profiles._confirmed_view(df, intraday)
    view_p, _ = profiles._confirmed_view(df, postclose)
    assert len(view_i) == len(df) - 1     # live bar dropped during the session
    assert len(view_p) == len(df)         # confirmed after the close


def test_no_repaint_across_intraday_ticks():
    """Two intraday re-runs with a different live-bar close must yield the same
    confirmed state (the conv_turns#308 instability)."""
    df = _df_with_today_last_bar()
    intraday = dt.datetime(2026, 6, 17, 11, tzinfo=ET)
    a = df.copy(); a.iloc[-1, a.columns.get_loc("Close")] = df["Close"].iloc[-1] * 1.05
    b = df.copy(); b.iloc[-1, b.columns.get_loc("Close")] = df["Close"].iloc[-1] * 0.95
    va, _ = profiles._confirmed_view(a, intraday)
    vb, _ = profiles._confirmed_view(b, intraday)
    assert strategy_state(va, "efi")["confidence"] == strategy_state(vb, "efi")["confidence"]


# --------------------------------------------------------------------------- #
# L2 — events are supplementary, never scored
# --------------------------------------------------------------------------- #
def test_events_separate_from_score():
    df = make_ohlcv(320, seed=9)
    res = s.run_strategy_profile(df, "swing", confirmed_only=False)
    for name, d in res["detail"].items():
        assert "events" in d and "confidence" in d
    # composite is the mean of confidences only — events do not enter it
    confs = [d["confidence"] for d in res["detail"].values() if d["confidence"] is not None]
    assert res["composite_score"] == pytest.approx(round(float(np.mean(confs)), 3), abs=1e-3)


# --------------------------------------------------------------------------- #
# L4 — stability / fresh-flip
# --------------------------------------------------------------------------- #
def test_stability_field_matches_prior_bar():
    df = make_ohlcv(320, seed=2)
    res = s.run_strategy_profile(df, "swing", confirmed_only=False)
    assert res["stability"] in {"stable", "fresh_flip"}
    prev_label, _ = profiles._composite_label(df.iloc[:-1], profiles.PROFILES["swing"]["strategies"],
                                              set(s.list_strategies()))
    expected = "fresh_flip" if prev_label != res["composite"] else "stable"
    assert res["stability"] == expected


# --------------------------------------------------------------------------- #
# contract / new keys
# --------------------------------------------------------------------------- #
def test_profile_signals_new_keys():
    df = make_ohlcv(300, seed=1)
    res = s.run_strategy_profile(df, "swing")
    for k in ("composite_score", "stability", "asof"):
        assert k in res
    assert res["composite"] in {"bull", "bear", "neutral"}
    assert res["composite_score"] is None or -1.0 <= res["composite_score"] <= 1.0
    for d in res["detail"].values():
        assert set(d) == {"events", "confidence", "state", "direction"}
