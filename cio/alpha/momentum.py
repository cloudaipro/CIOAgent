"""Layer 3 — Momentum Engine (FR-004).

Relative strength: 3M and 6M return both beat QQQ.
Trend template: price > 50MA, 50MA > 150MA, 150MA > 200MA.

  momentum_score: 0..100 from average excess return vs QQQ (-25%->0, 0->50, +25%->100)
  trend_score:    fraction of the 3 trend conditions met -> 0 / 33 / 67 / 100
"""
from __future__ import annotations

from . import metrics


def evaluate(close, qqq_ret_3m: float | None, qqq_ret_6m: float | None) -> dict:
    """Pure: stock Close series + QQQ 3M/6M returns -> momentum/trend scores."""
    r3 = metrics.ret_pct(close, metrics.BARS_3M)
    r6 = metrics.ret_pct(close, metrics.BARS_6M)

    rs_pass = (
        r3 is not None and qqq_ret_3m is not None and r3 > qqq_ret_3m
        and r6 is not None and qqq_ret_6m is not None and r6 > qqq_ret_6m
    )

    ex3 = (r3 - qqq_ret_3m) if (r3 is not None and qqq_ret_3m is not None) else None
    ex6 = (r6 - qqq_ret_6m) if (r6 is not None and qqq_ret_6m is not None) else None
    parts = [e for e in (ex3, ex6) if e is not None]
    excess = sum(parts) / len(parts) if parts else None
    # -25% excess -> 0, 0 -> 50, +25% -> 100.
    momentum_score = metrics.clamp(50.0 + (excess * 2.0)) if excess is not None else 0.0

    price = float(close.iloc[-1]) if (close is not None and len(close)) else None
    ma50, ma150, ma200 = (metrics.sma(close, 50), metrics.sma(close, 150),
                          metrics.sma(close, 200))
    conds = [
        price is not None and ma50 is not None and price > ma50,
        ma50 is not None and ma150 is not None and ma50 > ma150,
        ma150 is not None and ma200 is not None and ma150 > ma200,
    ]
    met = sum(1 for c in conds if c)
    trend_score = round(met / 3.0 * 100.0, 1)

    return {
        "momentum_score": round(momentum_score, 2),
        "trend_score": trend_score,
        "rs_pass": bool(rs_pass),
        "ret_3m": round(r3, 2) if r3 is not None else None,
        "ret_6m": round(r6, 2) if r6 is not None else None,
        "trend_met": met,
    }
