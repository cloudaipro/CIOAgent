"""Pure price-series math for Alpha Hunter layers.

Everything here takes a pandas Close Series (Date-indexed, ascending) and returns
plain floats/bools — no I/O, no fetching, so the layer modules stay unit-testable
with synthetic series and the engine fetches each ticker's OHLCV exactly once.
"""
from __future__ import annotations

import pandas as pd

# Approx trading-day windows (NYSE ~21 sessions/month).
BARS_3M = 63
BARS_6M = 126


def sma(close: pd.Series, window: int) -> float | None:
    """Latest simple moving average over *window* bars, or None if too short."""
    if close is None or len(close) < window:
        return None
    return float(close.iloc[-window:].mean())


def ret_pct(close: pd.Series, bars: int) -> float | None:
    """Percent return over the last *bars* sessions, or None if too short."""
    if close is None or len(close) <= bars:
        return None
    past = float(close.iloc[-1 - bars])
    if past == 0:
        return None
    return (float(close.iloc[-1]) - past) / abs(past) * 100.0


def slope_up(close: pd.Series, window: int, lookback: int = 10) -> bool:
    """True if the *window*-SMA is higher now than *lookback* bars ago (rising)."""
    if close is None or len(close) < window + lookback:
        return False
    now = close.iloc[-window:].mean()
    then = close.iloc[-window - lookback:-lookback].mean()
    return bool(now > then)


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def scale(value: float | None, full_at: float, *, floor: float = 0.0) -> float:
    """Linear 0..100 score: <=floor -> 0, >=full_at -> 100. None -> 0."""
    if value is None or full_at <= floor:
        return 0.0
    return clamp((value - floor) / (full_at - floor) * 100.0)
