"""Layer 4 — Candidate Ranking (FR-005).

Final = 0.30*Momentum + 0.20*Trend + 0.30*Earnings
      + 0.10*RevenueGrowth(scaled) + 0.10*VolumeExpansion(scaled)

RevenueGrowth scaled: 0%->0, 50%+->100.
VolumeExpansion: latest volume vs 20-day average -> 1.0x->0, 2.0x+->100.
"""
from __future__ import annotations

from . import metrics

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
                revenue_growth_pct: float | None, df) -> dict:
    """Weighted final score + the two derived sub-scores it adds."""
    rev = metrics.scale(revenue_growth_pct, full_at=50.0, floor=0.0)
    vol = volume_expansion(df)
    final = (W_MOMENTUM * momentum_score + W_TREND * trend_score
             + W_EARNINGS * earnings_score + W_REVENUE * rev + W_VOLUME * vol)
    return {
        "final": round(final, 2),
        "revenue_score": round(rev, 2),
        "volume_expansion": round(vol, 2),
    }
