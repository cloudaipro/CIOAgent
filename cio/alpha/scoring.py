"""Layer 4 — Candidate Ranking (FR-005).

Final = 0.30*Momentum + 0.20*Trend + 0.30*Earnings(coverage-amplified)
      + 0.10*RevenueGrowth(scaled) + 0.10*VolumeExpansion(scaled)

RevenueGrowth scaled: 0%->0, 50%+->100.
VolumeExpansion: latest volume vs 20-day average -> 1.0x->0, 2.0x+->100.

Coverage (swing upgrade #1): when a ``coverage_edge`` is supplied the Earnings
(catalyst) component is amplified/damped by ``coverage.apply`` BEFORE weighting, so
an under-covered name with a real catalyst out-ranks a saturated one. Passing
coverage_edge=None reproduces the original score exactly (back-compatible).
"""
from __future__ import annotations

from . import metrics, coverage

W_MOMENTUM = 0.30
W_TREND = 0.20
W_EARNINGS = 0.30
W_REVENUE = 0.10
W_VOLUME = 0.10


def volume_expansion(df, window: int = 20) -> float:
    """0..100 from latest volume vs its 20-day average (1x->0, 2x+->100)."""
    if df is None or "Volume" not in df or len(df) < window:
        return 0.0
    avg = float(df["Volume"].iloc[-window:].mean())
    if avg <= 0:
        return 0.0
    ratio = float(df["Volume"].iloc[-1]) / avg
    return metrics.scale((ratio - 1.0) * 100.0, full_at=100.0, floor=0.0)


def final_score(momentum_score: float, trend_score: float, earnings_score: float,
                revenue_growth_pct: float | None, df,
                coverage_edge: float | None = None) -> dict:
    """Weighted final score + the derived sub-scores it adds.

    When *coverage_edge* (0..100) is given, the Earnings/catalyst component is
    amplified or damped by it (``coverage.apply``); None leaves it untouched.
    """
    rev = metrics.scale(revenue_growth_pct, full_at=50.0, floor=0.0)
    vol = volume_expansion(df)
    earn = coverage.apply(earnings_score, coverage_edge)
    final = (W_MOMENTUM * momentum_score + W_TREND * trend_score
             + W_EARNINGS * earn + W_REVENUE * rev + W_VOLUME * vol)
    return {
        "final": round(final, 2),
        "revenue_score": round(rev, 2),
        "volume_expansion": round(vol, 2),
        "earnings_amplified": round(earn, 2),
    }
