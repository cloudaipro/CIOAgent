"""
Tests for cio.stock.viz.state — the deterministic 2-D oscillator state that
replaced the overloaded one-word panel verdict (which collapsed level+direction
and misled the chart's downstream narrator on EFI; see conv_turns#296-297).

No network: synthetic series / DataFrames.
"""
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import numpy as np
import pytest

from cio.stock.viz.state import panel_state
from cio.stock.viz import build_spec, indicator_states
from tests.conftest import make_ohlcv


# --------------------------------------------------------------------------- #
# panel_state unit
# --------------------------------------------------------------------------- #
def test_clearly_positive_building():
    rng = np.random.default_rng(0)
    s = panel_state(np.linspace(-2, 4, 40) * 1e6 + rng.normal(0, 0.2e6, 40), level=0.0)
    assert s is not None
    assert s["level"] == "pos"
    assert s["direction"] == "up"
    assert s["label"].startswith("positive")


def test_clearly_negative_deepening():
    rng = np.random.default_rng(1)
    s = panel_state(np.linspace(2, -5, 40) * 1e6 + rng.normal(0, 0.2e6, 40), level=0.0)
    assert s["level"] == "neg"
    assert s["direction"] == "down"
    assert "negative" in s["label"]


def test_mchp_like_crossed_up_then_fading():
    # deep-negative dip -> cross >0 -> peak -> single-bar fade (the MCHP 6/16 shape)
    base = [-0.3, -0.6, 0.3, 0.9, 0.7, 1.0, 2.7, 1.0, 2.0, 1.9, 2.6, 1.0, 0.2,
            -0.3, 0.1, -0.4, 0.8, 0.4, -0.5, 0.6, 0.4, -1.8, -0.8, -1.2, 0.1,
            1.0, 0.1]
    s = panel_state(np.array(base) * 1e7, level=0.0, trend_k=7)
    # the bug was claiming "未明確翻正" (never crossed positive). State must show
    # a recent upward zero-cross — the narrator can no longer deny it.
    assert s["cross_dir"] == "up"
    assert s["bars_since_cross"] == 3
    assert s["direction"] == "up"
    assert ">" in s["label"] and "0" in s["label"]
    # peak above zero was 1 bar before the final fade bar
    assert s["peak"] is not None and s["peak"] > 0
    assert s["peak_bars_ago"] == 1


def test_too_short_returns_none():
    assert panel_state(np.array([1.0, 2.0, 3.0]), level=0.0) is None


def test_nan_and_flat_safe():
    assert panel_state(np.full(20, np.nan), level=0.0) is None
    s = panel_state(np.zeros(30), level=0.0)            # perfectly flat on the line
    assert s is not None and s["level"] == "zero"


def test_deterministic():
    v = np.sin(np.linspace(0, 6, 50)) * 1e6
    assert panel_state(v, level=0.0)["label"] == panel_state(v, level=0.0)["label"]


# --------------------------------------------------------------------------- #
# wiring into build_spec / panels
# --------------------------------------------------------------------------- #
def test_efi_panel_gets_chip_not_bare_word():
    df = make_ohlcv(n_rows=220, seed=11)
    spec = build_spec(df, "T", symbol="T", indicators=["efi"], window=60)
    efi = next(p for p in spec.panels if p.name.upper() == "EFI")
    assert efi.chip is not None
    assert efi.chip == efi.state["label"]
    assert "EFI" in spec.states
    # chip must carry direction, not the overloaded single word that caused the bug
    assert efi.chip not in ("neutral", "bull", "bear")
    assert "·" in efi.chip


def test_multiline_panel_has_no_state():
    # KDJ is multi-line (not zero-centered single series) -> scope guard skips it
    df = make_ohlcv(n_rows=220, seed=12)
    spec = build_spec(df, "T", symbol="T", indicators=["kdj"], window=60)
    kdj = next(p for p in spec.panels if p.name.upper() == "KDJ")
    assert kdj.chip is None and kdj.state is None


def test_indicator_states_matches_chart_chips():
    # the text/LLM path must quote the EXACT chips the chart draws (single source)
    df = make_ohlcv(n_rows=220, seed=11)
    text = indicator_states(df, "swing")
    chart = build_spec(df, "swing", symbol="X").states
    assert text  # non-empty (EFI present in swing profile)
    assert {k: v["label"] for k, v in text.items()} == \
           {k: v["label"] for k, v in chart.items()}


def test_indicator_states_failsafe():
    import pandas as pd
    assert indicator_states(pd.DataFrame(), "swing") == {}


def test_render_with_chip_does_not_raise(tmp_path):
    from cio.stock.viz.mpl_plot import render
    df = make_ohlcv(n_rows=220, seed=13)
    out = render(df, "T", symbol="T", indicators=["efi"], window=60,
                 out_dir=str(tmp_path), filename="state.png")
    assert out and out.endswith("state.png")
