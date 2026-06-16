"""Layer 3b — Coverage Density (swing-strategy upgrade #1, 2026-06).

Edge thesis (committee discussion 280-289): the durable retail edge is NOT small
market cap, it is *neglected information* — names that few analysts cover and few
institutions hold reprice slowly when a catalyst lands, so the catalyst-to-price
diffusion is where a patient operator can still extract alpha.

Academic anchor: Hong, Lim & Stein (2000) "Bad News Travels Slowly" — momentum and
post-earnings drift are materially stronger in low-analyst-coverage stocks.

This module turns analyst count (+ market cap, when available) into a 0..100
``coverage_edge`` where 100 = maximally under-covered for its size (best edge) and
0 = saturated / value-trap. It NEVER originates a signal — ``apply()`` only
*amplifies or damps the catalyst (earnings) score* a funnel already computed, so a
neglected name with a real catalyst ranks above a crowded one, and a neglected name
with no catalyst still ranks nowhere. Pure, deterministic, never-raises.
"""
from __future__ import annotations

from . import metrics

# Amplifier strength: coverage can move the catalyst score by at most ±AMP_K.
AMP_K = 0.30
# Residual -> edge sensitivity (analysts above/below the size-expected count).
_RESID_SENSITIVITY = 2.5
# Liquidity / value-trap floor: 0 analysts under this market cap (USD millions) is
# treated as un-investable neglect, not edge.
_MICRO_CAP_MUSD = 1000.0


def analyst_count(recs: dict | None) -> int | None:
    """Total analysts covering, summed across rating buckets. None when unknown.

    Accepts the dict shape returned by ``finnhub.analyst_recs``:
    {strong_buy, buy, hold, sell, strong_sell} (any may be None).
    """
    if not isinstance(recs, dict):
        return None
    buckets = ("strong_buy", "buy", "hold", "sell", "strong_sell")
    vals = [recs.get(k) for k in buckets]
    if all(v is None for v in vals):
        return None
    return int(sum(v for v in vals if isinstance(v, (int, float))))


def expected_coverage(market_cap_musd: float | None) -> float | None:
    """Analysts a stock of this size would *typically* attract (log model).

    Calibrated so ~$300M -> 3, ~$3B -> 11, ~$30B -> 19, ~$300B -> 27, ~$3T -> 35.
    market_cap is in USD millions (Finnhub ``marketCapitalization`` convention).
    None when market cap is unknown.
    """
    try:
        mc = float(market_cap_musd)
    except (TypeError, ValueError):
        return None
    if mc <= 0:
        return None
    import math
    expected = 3.0 + 8.0 * math.log10(max(mc, 50.0) / 300.0)
    return max(2.0, min(45.0, expected))


def _analyst_edge(n: int, market_cap_musd: float | None, out: dict):
    """Analyst-coverage edge (0..100) + side effects on *out* (expected/residual/flag).

    Returns the edge, or None if it can't be derived. May set out['flag'] to
    'value_trap_floor' (and return 0.0) for un-investable micro-cap neglect.
    """
    exp = expected_coverage(market_cap_musd)
    if exp is not None:
        out["expected"] = round(exp, 1)
        residual = n - exp                       # negative => under-covered for size
        out["residual"] = round(residual, 1)
        if n == 0 and market_cap_musd is not None and float(market_cap_musd) < _MICRO_CAP_MUSD:
            out["flag"] = "value_trap_floor"
            return 0.0
        out["flag"] = "under_covered" if residual < -3 else ("saturated" if residual > 5 else "")
        return max(0.0, min(100.0, 50.0 - _RESID_SENSITIVITY * residual))
    # Count-only fallback (no market cap): band by raw count. Conservative — without
    # size context we can't tell genuine neglect from a shell, so the sweet spot
    # (3-10) gets a moderate edge, never the maximum.
    out["flag"] = "count_only"
    if n <= 2:
        return 58.0
    if n <= 10:
        return 70.0
    if n <= 18:
        return 52.0
    if n <= 28:
        return 40.0
    return 28.0


def _institutional_edge(institutional_pct: float | None, out: dict):
    """Institutional-ownership edge (0..100): low ownership = neglected = high edge,
    crowded = low edge. Returns the edge or None. Tags out['flag'] when no analyst
    tag already applies."""
    try:
        pct = float(institutional_pct)
    except (TypeError, ValueError):
        return None
    pct = max(0.0, min(100.0, pct))
    edge = 100.0 - pct                            # 20% owned -> 80 edge; 80% -> 20 edge
    if not out["flag"]:
        if pct < 30:
            out["flag"] = "institutionally_neglected"
        elif pct > 70:
            out["flag"] = "institutionally_crowded"
    return round(edge, 1)


def coverage_score(recs: dict | None, market_cap_musd: float | None = None,
                   institutional_pct: float | None = None) -> dict:
    """Blend analyst count (+ size) and institutional ownership % into a coverage-edge.

    Returns:
      {analyst_count, market_cap, institutional_pct, expected, residual,
       coverage_edge, flag}

    The two neglect signals are averaged when both present (genuine edge needs BOTH
    thin analyst coverage AND low institutional ownership — conv 280-289). Passing
    institutional_pct=None reproduces the analyst-only behaviour exactly (back-compat).
    coverage_edge is 0..100, default 50 (neutral / unknown -> no amplification).
    flag in {"", "under_covered", "saturated", "value_trap_floor", "count_only",
             "institutionally_neglected", "institutionally_crowded"}.
    """
    out = {
        "analyst_count": None, "market_cap": market_cap_musd,
        "institutional_pct": institutional_pct, "expected": None, "residual": None,
        "coverage_edge": 50.0, "flag": "",
    }
    n = analyst_count(recs)
    out["analyst_count"] = n

    a_edge = _analyst_edge(n, market_cap_musd, out) if n is not None else None
    # A value-trap floor is decisive — neglect we can't trade isn't edge, period.
    if out["flag"] == "value_trap_floor":
        out["coverage_edge"] = 0.0
        return out
    i_edge = _institutional_edge(institutional_pct, out)

    edges = [e for e in (a_edge, i_edge) if e is not None]
    if edges:
        out["coverage_edge"] = round(sum(edges) / len(edges), 1)
    return out


def apply(earnings_score: float, coverage_edge: float | None, k: float = AMP_K) -> float:
    """Amplify/damp a catalyst (earnings) score by coverage edge. Clamped 0..100.

    edge 50 -> x1.0 (no change); edge 100 -> x(1+k); edge 0 -> x(1-k). Because the
    multiplier rides ON the earnings score, coverage can never manufacture a
    catalyst that isn't there — earnings_score 0 stays 0.
    """
    try:
        e = float(earnings_score)
    except (TypeError, ValueError):
        return 0.0
    if coverage_edge is None:
        return e
    mult = 1.0 + k * (float(coverage_edge) - 50.0) / 50.0
    return round(max(0.0, min(100.0, e * mult)), 2)
