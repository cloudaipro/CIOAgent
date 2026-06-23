"""
conftest.py — shared fixtures and helpers for the CIOAgent test suite.

Inserts the repo root onto sys.path so `import cio` works under pytest
regardless of where pytest is invoked from.
"""
import sys
import os

# Repo root is one level above this file's directory (tests/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Force the detailed-history feature OFF for the whole suite: convlog.enabled()
# lets the env var win and lock the value, so this overrides any persisted
# dashboard setting and stops agent/committee tests writing to the real logs/ dir.
# (test_convlog overrides this per-test via monkeypatch.)
os.environ["CIO_DETAILED_LOG"] = "0"

import numpy as np
import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _isolate_freshness(monkeypatch, tmp_path):
    """Point the data-freshness heartbeat store at a temp file so source fetchers
    exercised in tests (the finnhub/edgar/yfinance freshness hooks) never write the
    repo's real data/source_freshness.json."""
    monkeypatch.setenv("CIO_FRESHNESS_FILE", str(tmp_path / "source_freshness.json"))


@pytest.fixture(autouse=True)
def _clear_limit_latch():
    """engine._LIMIT_LATCH is module-global; any test that drives a real
    backend through a limit notice would otherwise latch the service for
    every later test in the run."""
    from cio.committee import engine
    engine._LIMIT_LATCH.clear()
    yield
    engine._LIMIT_LATCH.clear()


def make_ohlcv(n_rows=350, seed=42):
    """
    Build a synthetic OHLCV DataFrame suitable for strategy testing.

    - DatetimeIndex of n_rows business days ending today-ish.
    - Close: seeded random walk > 0.
    - High = Close + small positive offset.
    - Low  = Close - small positive offset (always < Close).
    - Open between Low and High.
    - Volume: positive integers.
    - Columns: Open, High, Low, Close, Adj Close, Volume.
    """
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2024-12-31", periods=n_rows)

    # Random-walk price series, guaranteed positive.
    log_returns = rng.normal(0, 0.01, n_rows)
    close = 100.0 * np.exp(np.cumsum(log_returns))

    offset = np.abs(rng.normal(0.5, 0.2, n_rows)) + 0.01
    high = close + offset
    low = close - offset
    open_ = low + rng.random(n_rows) * (high - low)
    volume = rng.integers(500_000, 5_000_000, n_rows)

    df = pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Adj Close": close * 0.99,
            "Volume": volume.astype(int),
        },
        index=idx,
    )
    df.index.name = "Date"
    return df


@pytest.fixture
def ohlcv():
    """Pytest fixture: 350-row synthetic OHLCV DataFrame."""
    return make_ohlcv()


def make_wide_ohlcv():
    """
    Synthetic OHLCV covering 2024-01-01 .. 2024-12-31 for cache tests.
    Returned as a flat (non-MultiIndex) DataFrame.
    """
    return make_ohlcv(n_rows=260, seed=7)
