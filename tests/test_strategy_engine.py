"""
Regression suite for cio/stock/engine/strategies — offline, synthetic data only.

Covers the bug classes found in the 2026-06 vendored-engine audit:
  registration gaps, output contract (index/binary/prefix), dead parameters,
  default-signal name validity, look-ahead bias, and edge-case error clarity.
"""
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import importlib
import os

import numpy as np
import pandas as pd
import pytest

import cio.stock as s
from tests.conftest import make_ohlcv

ENGINE = s.StrategyEngine()
ALL_STRATEGIES = ENGINE.list_strategies()

_STRAT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(s.__file__)), "engine", "strategies"
)


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------

def test_every_strategy_file_is_registered():
    """Every *_strategy.py must be importable from __init__ and runnable via signal_creator."""
    files = sorted(
        f[: -len("_strategy.py")]
        for f in os.listdir(_STRAT_DIR)
        if f.endswith("_strategy.py")
    )
    missing = [f for f in files if f not in ALL_STRATEGIES]
    assert not missing, f"strategy files not registered in signal_creator: {missing}"


# ---------------------------------------------------------------------------
# output contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_output_contract(name):
    """Index aligned to input; c_ columns binary; no inf; prefix/suffix applied."""
    df = make_ohlcv(350)
    out = ENGINE.run(df, name, prefix="P", suffix="S")
    assert out.index.equals(df.index), "output index must equal input index"
    for col in out.columns:
        assert col.startswith("P_") and col.endswith("_S"), (
            f"{col}: prefix/suffix not applied"
        )
        vals = out[col].to_numpy(dtype=float, na_value=0.0)
        assert not np.isinf(vals).any(), f"{col}: contains inf"
        base = col[2:-2]
        if base.startswith("c_"):
            uniq = set(out[col].dropna().unique().tolist())
            assert uniq <= {0, 1, 0.0, 1.0, True, False}, (
                f"{col}: c_ column must be binary, got {sorted(uniq)[:6]}"
            )


# ---------------------------------------------------------------------------
# parameter sensitivity — every grid parameter must change the output
# ---------------------------------------------------------------------------

def _grid_for(name):
    try:
        mod = importlib.import_module(f"strategies.{name}_strategy")
    except ModuleNotFoundError:
        return None, None
    attr = next((a for a in dir(mod) if a.endswith("_grid_of_parameter")), None)
    return mod, (list(getattr(mod, attr)) if attr else None)


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_no_dead_parameters(name):
    mod, grid = _grid_for(name)
    if not grid or len(grid) < 2:
        pytest.skip("no multi-point parameter grid")
    df = make_ohlcv(350)
    base = ENGINE.run(df, name, **grid[0]).fillna(-999).round(8)
    for key in grid[0]:
        alts = sorted({g[key] for g in grid} - {grid[0][key]})
        if not alts:
            continue
        params = dict(grid[0])
        params[key] = alts[-1]
        alt = ENGINE.run(df, name, **params).fillna(-999).round(8)
        assert not base.equals(alt), (
            f"{name}: parameter '{key}' {grid[0][key]} -> {params[key]} had no effect"
        )


def test_ema_length_is_independent_of_length():
    """Regression: rsi/rsx/pgo read ema_length from the 'length' kwarg."""
    df = make_ohlcv(350)
    for name in ("rsi", "rsx", "pgo"):
        a = ENGINE.run(df, name).fillna(-999).round(8)
        b = ENGINE.run(df, name, ema_length=3).fillna(-999).round(8)
        assert not a.equals(b), f"{name}: ema_length kwarg has no effect"


# ---------------------------------------------------------------------------
# default signal names must exist in the output
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_default_signal_column_exists(name):
    mod, _ = _grid_for(name)
    if mod is None:
        pytest.skip("module not importable standalone")
    default_attr = next((a for a in dir(mod) if a.startswith("default_")), None)
    if default_attr is None:
        pytest.skip("strategy declares no default signal")
    col = getattr(mod, default_attr)
    out = ENGINE.run(make_ohlcv(350), name)
    assert col in out.columns, (
        f"{name}: declared default signal {col!r} not among output columns"
    )


# ---------------------------------------------------------------------------
# look-ahead bias — truncation invariance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_truncation_invariance(name):
    """Signals on a common window must not change when later rows are appended."""
    full = make_ohlcv(350)
    trunc = full.iloc[:-30]
    rf = ENGINE.run(full, name)
    rt = ENGINE.run(trunc, name)
    common = rt.index[100:]
    a = rf.reindex(common).fillna(-999).round(6)
    b = rt.reindex(common).fillna(-999).round(6)
    diff = [c for c in a.columns if not a[c].equals(b[c])]
    assert not diff, f"{name}: look-ahead bias in columns {diff}"


# ---------------------------------------------------------------------------
# edge cases — graceful or informative failure, never a cryptic crash
# ---------------------------------------------------------------------------

def _run_edge(df, name):
    """Strategies may raise ValueError naming the strategy on degenerate input."""
    try:
        out = ENGINE.run(df, name)
    except ValueError as e:
        assert name in str(e), f"{name}: error message must name the strategy: {e}"
        return None
    assert isinstance(out, pd.DataFrame)
    vals = out.to_numpy(dtype=float, na_value=0.0)
    assert not np.isinf(vals).any(), f"{name}: inf in output"
    return out


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_short_input(name):
    _run_edge(make_ohlcv(30), name)


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_constant_price_input(name):
    df = make_ohlcv(350)
    for c in ("Open", "High", "Low", "Close", "Adj Close"):
        df[c] = 100.0
    _run_edge(df, name)


@pytest.mark.parametrize("name", ALL_STRATEGIES)
def test_zero_volume_input(name):
    df = make_ohlcv(350)
    df["Volume"] = 0
    _run_edge(df, name)


# ---------------------------------------------------------------------------
# specific regression fixes
# ---------------------------------------------------------------------------

def test_kdj_bear_uses_p_j():
    """Regression: KDJ bear crossover repeated p_k > p_d instead of p_j > p_d."""
    src = open(os.path.join(_STRAT_DIR, "kdj_strategy.py")).read()
    bear_block = src.split('"CROSSOVER_BEAR",')[0].split('"CROSSOVER_BULL",')[1]
    assert '(data["p_j"] > data["p_d"])' in bear_block


def test_qqe_confirm_uses_price_above_ema_for_bull():
    """Regression: RSIMACONFIRM_BULL must require close > EMA (standard confirmation)."""
    src = open(os.path.join(_STRAT_DIR, "qqe_strategy.py")).read()
    bull_block = src.split('"RSIMACONFIRM_BULL",')[0].split('"RSIMACONFIRM_BEAR"] = 0')[1]
    assert '(data["ema"] < data["close"])' in bull_block


def test_cmo_default_thresholds_not_degenerate():
    """Regression: cmo default limit_delta=0 made OVERBOUGHT fire on ~half of bars."""
    out = ENGINE.run(make_ohlcv(350), "cmo")
    ob = out["c_CMO_OVERBOUGHT"].fillna(0)
    assert ob.mean() < 0.25, (
        f"default OVERBOUGHT rate {ob.mean():.2f} — thresholds look degenerate"
    )


def test_eri_divergence_labels_match_docstrings():
    """Regression: ERI bullish setup (bear power rising) must set DIVERGENCE_BULL."""
    src = open(os.path.join(_STRAT_DIR, "eri_strategy.py")).read()
    first_assign = src.index('"DIVERGENCE_BULL",\n    ] = 1')
    bear_rising = src.index('(data["bear"] > data["p_bear"])')
    assert bear_rising < first_assign, (
        "bear-power-rising block must assign DIVERGENCE_BULL"
    )
