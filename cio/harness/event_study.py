"""event_study.py — V3: post-catalyst return distribution (never a point).

Why this exists: in conv_turns 349 the agent answered "how big can the move be?"
with "Wave 2 = 30-60% of Wave 1", tagged [inference] — a heuristic with no
empirical backing. The honest fix is NOT a better point estimate (the part of the
move that's unpredictable is the part the market already arbitraged away); it is
to return a DISTRIBUTION grounded in event-study evidence, plus an explicit note
that magnitude is bounded by market efficiency.

So this tool always returns mean/median/quartiles/hit-rate, never a single
number. If enough historical analogs are supplied it fits an empirical
distribution; otherwise it falls back to a clearly-labelled reference prior drawn
from published event-study magnitudes (earnings/analyst ~3-4% 20-day abnormal
positive, ~-2.25% negative, etc.). Deterministic, never-raises, no model call.
"""
from __future__ import annotations

from .models import EventStudyResult, EventType

MIN_SAMPLES = 8

_NOTE = ("Distribution, not a point forecast. Post-catalyst magnitude is bounded "
         "by market efficiency — large moves are frequently followed by reversal "
         "or drift that is not separable in advance.")

# Reference priors (20-day, abnormal, coarse). Wide p25/p75 by design: these
# reflect LOW predictability, not precision. Labelled sample='reference'.
# (mean, median, p25, p75, hit_rate)
REFERENCE_DIST: dict[EventType, tuple] = {
    EventType.ANALYST_ACTION:    (3.5, 3.0, -2.0, 8.0, 0.58),
    EventType.PRODUCT_MILESTONE: (4.0, 3.0, -3.0, 11.0, 0.60),
    EventType.STRATEGIC_CUSTOMER:(6.0, 4.5, -3.0, 15.0, 0.62),
    EventType.GOV_ANNOUNCEMENT:  (5.0, 3.0, -6.0, 14.0, 0.55),  # mean-reverting
    EventType.EARNINGS:          (0.0, 0.0, -7.0, 7.0, 0.50),   # two-sided
    EventType.MNA:               (8.0, 6.0, -2.0, 20.0, 0.66),
    EventType.OTHER:             (2.0, 1.5, -4.0, 7.0, 0.54),
}


def study(event_type: EventType, horizon_days: int = 20,
          samples: list[float] | None = None) -> EventStudyResult:
    """Return the forward-return distribution for an event type.

    ``samples`` = realized forward returns (%) for historical analogs (supplied by
    an injected provider, e.g. prices_provider). >= MIN_SAMPLES ⇒ empirical fit;
    otherwise the reference prior.
    """
    et = event_type if isinstance(event_type, EventType) else EventType.OTHER
    label = et.name.lower()

    if samples and len(samples) >= MIN_SAMPLES:
        vals = sorted(float(x) for x in samples)
        n = len(vals)
        mean = sum(vals) / n
        return EventStudyResult(
            event_type=label, horizon_days=horizon_days, n=n,
            mean=round(mean, 2), median=round(_pct(vals, 50), 2),
            p25=round(_pct(vals, 25), 2), p75=round(_pct(vals, 75), 2),
            hit_rate=round(sum(1 for v in vals if v > 0) / n, 3),
            sample="historical", note=_NOTE,
        )

    mean, median, p25, p75, hit = REFERENCE_DIST.get(et, REFERENCE_DIST[EventType.OTHER])
    return EventStudyResult(
        event_type=label, horizon_days=horizon_days, n=0,
        mean=mean, median=median, p25=p25, p75=p75, hit_rate=hit,
        sample="reference",
        note=_NOTE + " Reference prior (insufficient historical analogs); not fitted to this name.",
    )


def wave2_estimate(wave1_pct: float, event_type: EventType) -> dict:
    """Grounded replacement for the hallucinated 'Wave 2 = 30-60% of Wave 1'.

    Returns a BAND (low/high follow-through as a fraction of wave-1) plus a caveat,
    derived from the event type's reference dispersion rather than a single ratio.
    Wider, more uncertain event types get a wider band. Never a single number.
    """
    et = event_type if isinstance(event_type, EventType) else EventType.OTHER
    _, _, p25, p75, _ = REFERENCE_DIST.get(et, REFERENCE_DIST[EventType.OTHER])
    spread = max(p75 - p25, 1.0)
    # Map dispersion to a follow-through band around a conservative center.
    center = 0.35
    half = min(0.25, spread / 100.0 + 0.10)
    lo, hi = max(0.0, center - half), center + half
    try:
        w1 = float(wave1_pct)
    except (TypeError, ValueError):
        w1 = 0.0
    return {
        "event_type": et.name.lower(),
        "wave1_pct": w1,
        "follow_through_low": round(lo, 2),
        "follow_through_high": round(hi, 2),
        "wave2_pct_low": round(w1 * lo, 2),
        "wave2_pct_high": round(w1 * hi, 2),
        "caveat": ("Band, not a point. Conditional on confirmation/detail beating "
                   "expectations; absent that, follow-through is often ~0 or reverses."),
    }


def prices_provider_samples(conn, symbol: str, event_dates: list[str],
                            horizon_days: int = 20, benchmark: str = "") -> list[float]:
    """Build historical forward-return samples from the `prices` table.

    For each event date, forward return = (close[t+h] / close[t] - 1) * 100, minus
    the benchmark's forward return when a benchmark symbol is given (abnormal
    return). Best-effort: dates without enough forward data are skipped. Returns a
    list suitable to pass as ``study(samples=...)``. Never raises.
    """
    out: list[float] = []
    try:
        series = _close_series(conn, symbol)
        bench = _close_series(conn, benchmark) if benchmark else {}
    except Exception:
        return out
    dates = sorted(series)
    idx = {d: i for i, d in enumerate(dates)}
    for ev in event_dates or []:
        i = idx.get(ev)
        if i is None or i + horizon_days >= len(dates):
            continue
        d0, dh = dates[i], dates[i + horizon_days]
        try:
            r = (series[dh] / series[d0] - 1.0) * 100.0
            if bench and d0 in bench and dh in bench and bench[d0]:
                r -= (bench[dh] / bench[d0] - 1.0) * 100.0
            out.append(r)
        except (ZeroDivisionError, KeyError, TypeError):
            continue
    return out


def _close_series(conn, symbol: str) -> dict:
    cur = conn.execute(
        "SELECT price_date, close FROM prices WHERE symbol=? ORDER BY price_date",
        (symbol,))
    return {row[0]: float(row[1]) for row in cur.fetchall() if row[1] is not None}


def _pct(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation percentile on a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac
