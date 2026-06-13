"""Layer 2.5 — Earnings Engine (FR-003A).

Earnings Score = 0.40*fwd_eps + 0.40*revision + 0.20*surprise

  A. Forward EPS growth (40%): scaled 0..100 (15%->~30, 50%+->100).
  B. EPS revision, Lite mode (40%): an earnings gap-up > 5% that stayed unfilled
     for 10 trading days = 100, else 0. Detected from price alone (no analyst feed).
  C. Surprise (20%): last-4-quarter beat ratio -> 100/75/50/25/0. From finnhub;
     0 (not None) when finnhub is disabled, so the layer never hard-fails on it.
"""
from __future__ import annotations

from . import metrics

GAP_PCT = 5.0          # min gap-up to count (FR-003A.B)
FILL_WINDOW = 10       # sessions the gap must stay unfilled
_SCAN = 40             # how far back to look for a gap event


def fwd_eps_component(fwd_eps_growth: float | None) -> float:
    """0..100 from forward EPS growth %."""
    return metrics.scale(fwd_eps_growth, full_at=50.0, floor=0.0)


def revision_signal(df) -> float:
    """Lite-mode EPS-revision proxy: 100 if a recent >5% gap-up stayed unfilled for
    FILL_WINDOW sessions, else 0. Pure price (Open/Close/Low). Needs OHLC columns."""
    if df is None or len(df) < 3:
        return 0.0
    for col in ("Open", "Close", "Low"):
        if col not in df:
            return 0.0
    n = len(df)
    start = max(1, n - _SCAN)
    for i in range(start, n):
        prev_close = float(df["Close"].iloc[i - 1])
        open_i = float(df["Open"].iloc[i])
        if prev_close <= 0:
            continue
        gap = (open_i - prev_close) / prev_close * 100.0
        if gap <= GAP_PCT:
            continue
        # Unfilled = no later session traded back down through the pre-gap close,
        # measured over up to FILL_WINDOW sessions after the gap.
        window = df["Low"].iloc[i:i + FILL_WINDOW]
        if float(window.min()) > prev_close:
            return 100.0
    return 0.0


def surprise_score(surprises) -> float:
    """Beat ratio of last 4 quarters -> 100/75/50/25/0. 0.0 when no data."""
    if not surprises:
        return 0.0
    beats = sum(1 for r in surprises if r.get("beat"))
    return {4: 100.0, 3: 75.0, 2: 50.0, 1: 25.0}.get(beats, 0.0)


def evaluate(fwd_eps_growth: float | None, df, surprises) -> dict:
    """Combine the three components into an earnings score."""
    a = fwd_eps_component(fwd_eps_growth)
    b = revision_signal(df)
    c = surprise_score(surprises)
    score = 0.40 * a + 0.40 * b + 0.20 * c
    return {
        "earnings_score": round(score, 2),
        "fwd_eps_component": round(a, 2),
        "revision_signal": round(b, 2),
        "surprise_score": round(c, 2),
        "fwd_eps_growth": round(fwd_eps_growth, 2) if fwd_eps_growth is not None else None,
    }
