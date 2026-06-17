"""
signal_state.py — continuous, STATE-based per-strategy confidence + composite.

WHY (conv_turns 304-311)
------------------------
The composite was driven by ``summarize_signals``, which counts ``c_*_BULL`` /
``c_*_BEAR`` EVENT columns inside an N-bar window. An event scrolling out of that
window flips a strategy bull->neutral with ZERO market change — proven: an EFI
zero-cross reads ``bull`` at +4 bars and ``neutral`` at +5 bars. Running the daily
swing profile on an in-progress bar (intraday) made it worse (repaint). The
strategies that stayed stable across two same-day runs (Fisher) were the
feature/STATE ones; the ones that whipsawed (EFI, KDJ, VIDYA) were EVENT-counted.

THE FIX (research-backed)
-------------------------
* STATE, not events, drives the score. Events become supplementary triggers.
* CONTINUOUS confidence + a DEAD-ZONE (memoryless hysteresis / Schmitt trigger)
  instead of a binary majority vote — no cliff near a single threshold.
* Confirmed bars only (TradingView ``barstate.isconfirmed``) — handled by
  ``profiles._confirmed_view``.
* Same ``panel_state`` engine as the chart chip, so chart + composite + the agent
  tool can never disagree.

References: TradingView repainting / barstate.isconfirmed; Schmitt-trigger
hysteresis for debounce; market-regime hysteresis to avoid whipsaw; continuous
trading-signal confidence over discrete buy/sell/neutral.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas_ta  # noqa: F401  registers the df.ta accessor

#: |z| (in robust MAD units) at which confidence saturates to +/-1.
Z_SAT = 2.0
#: |confidence| below this -> neutral. The dead-band is memoryless hysteresis: a
#: marginal wiggle (e.g. EFI z 0.22<->0.25) cannot flip the label. Set to
#: panel_state's level_band (0.5) / Z_SAT so the verdict word "neutral" lines up
#: with the chart chip's "near-zero" level — one threshold, no chip-vs-tool
#: disagreement. A weak signal still contributes its small confidence to the
#: continuous composite_score even when its discrete word is "neutral".
DEAD_ZONE = 0.25


def _series_and_level(df, name: str):
    """Return ``(series, level)`` for *name* using the SAME ``df.ta.*`` lines the
    chart plots, so the composite reads the same reality the chip shows. ``level``
    is the oscillator's neutral reference (0, 50, or 0.5). ``(None, None)`` when
    the strategy is not a level-centered line we can score."""
    n = name.lower()
    ta = df.ta

    def col(x, i):
        return x[x.columns[i]]

    if n == "efi":
        return ta.efi(), 0.0
    if n == "cmf":
        return ta.cmf(), 0.0
    if n == "vidya":
        return df["Close"] - ta.vidya(), 0.0      # price vs adaptive trend
    if n == "fisher":
        f = ta.fisher()
        return col(f, 0) - col(f, 1), 0.0          # fisher - trigger
    if n == "kdj":
        return col(ta.kdj(), 2), 50.0              # J line, centered at 50
    if n == "squeeze":
        return col(ta.squeeze(), 0), 0.0           # momentum histogram
    if n == "trix":
        return col(ta.trix(), 0), 0.0
    if n == "kst":
        return col(ta.kst(), 0), 0.0
    if n == "rsi":
        return ta.rsi(), 50.0
    if n == "er":
        return ta.er(), 0.5
    if n == "macd":
        return col(ta.macd(), 0), 0.0
    if n == "stoch":
        return col(ta.stoch(), 0), 50.0
    if n == "pvo":
        return col(ta.pvo(), 0), 0.0
    return None, None


def strategy_state(df, name: str) -> Optional[dict]:
    """Continuous state for one strategy.

    Returns ``{confidence, z, level, direction, label}`` or ``None``.
    ``confidence`` in [-1, 1] is signed by which side of the level the line sits
    and scaled by ``|z| / Z_SAT`` (robust MAD distance). Uses ``panel_state`` —
    the chart-chip engine — so display and score share one source of truth."""
    try:
        from .viz.state import panel_state   # lazy: avoid viz<->profiles cycle
        series, level = _series_and_level(df, name)
        if series is None:
            return None
        st = panel_state(series, level=level)
        if not st:
            return None
        c = float(np.clip(st["z"] / Z_SAT, -1.0, 1.0))
        return {
            "confidence": c,
            "z": st["z"],
            "level": st["level"],
            "direction": st["direction"],
            "label": st["label"],
        }
    except Exception:
        return None


def verdict_from_confidence(c: Optional[float], dead: float = DEAD_ZONE) -> str:
    """Dead-zoned label. The dead-band is what stops the cliff."""
    if c is None:
        return "neutral"
    if c >= dead:
        return "bull"
    if c <= -dead:
        return "bear"
    return "neutral"


def composite_score(confidences) -> Optional[float]:
    """Mean of present per-strategy confidences (continuous, in [-1, 1])."""
    vals = [c for c in confidences if c is not None]
    if not vals:
        return None
    return float(np.mean(vals))
