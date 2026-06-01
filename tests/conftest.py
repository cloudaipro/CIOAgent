"""
conftest.py — shared fixtures and helpers for the CFOAgent test suite.

Inserts the repo root onto sys.path so `import cfo` works under pytest
regardless of where pytest is invoked from.
"""
import sys
import os

# Repo root is one level above this file's directory (tests/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import numpy as np
import pandas as pd
import pytest


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
