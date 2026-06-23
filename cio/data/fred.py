"""FRED — Treasury yield curve + credit-spread regime context.

worldmonitor's daily-market-brief renders a regime line (yield curve inversion, HY
spread, VIX). The free, durable, no-vendor-lock piece of that is the Federal Reserve
(FRED) — Treasury constant-maturity yields (DGS2/DGS10/DGS30) and the ICE BofA
high-yield OAS (BAMLH0A0HYM2). This module fetches those so F7's brief can state the
macro backdrop deterministically (no LLM).

Gated by FRED_API_KEY (free: https://fred.stlouisfed.org/docs/api/api_key.html).
Unset -> {} with NO network call, like every other opt-in cio.data source. Cached 6h,
offline-safe, freshness-tracked.
"""
from __future__ import annotations

import logging
import os

from . import _cache
from ._http import RateLimiter, get_json

log = logging.getLogger(__name__)

_BASE = "https://api.stlouisfed.org/fred/series/observations"
_limiter = RateLimiter(0.6)
_TTL = 6 * 3600


def _key() -> str | None:
    k = (os.getenv("FRED_API_KEY") or "").strip()
    return k or None


def _record_fresh(n: int) -> None:
    if n <= 0:
        return
    try:
        from . import freshness
        freshness.record("fred", n)
    except Exception:
        pass


def _latest(series_id: str) -> float | None:
    """Most-recent numeric observation for *series_id*, or None.

    FRED encodes a missing print as '.', which we skip — so a holiday/no-print day
    falls back to the prior real value rather than poisoning the curve with a None.
    """
    tok = _key()
    if not tok:
        return None
    cached = _cache.read("fred_obs", series_id, _TTL)
    if cached is None:
        params = {"series_id": series_id, "api_key": tok, "file_type": "json",
                  "sort_order": "desc", "limit": 5}
        data = get_json(_BASE, params=params, limiter=_limiter)
        obs = data.get("observations") if isinstance(data, dict) else None
        cached = obs if isinstance(obs, list) else []
        _cache.write("fred_obs", series_id, cached)
        _record_fresh(len(cached))
    for o in cached:
        if not isinstance(o, dict):
            continue
        v = (o.get("value") or "").strip()
        if v and v != ".":
            try:
                return float(v)
            except ValueError:
                continue
    return None


def yield_curve() -> dict:
    """Treasury yield curve snapshot. {} when disabled (no key, no network).

    {rate_2y, rate_10y, rate_30y, spread_2s10s (bps), inverted}. Keys present only
    for series that returned a value; {} when FRED is off or all series failed.
    """
    if not _key():
        return {}
    out: dict = {}
    r2 = _latest("DGS2")
    r10 = _latest("DGS10")
    r30 = _latest("DGS30")
    if r2 is not None:
        out["rate_2y"] = r2
    if r10 is not None:
        out["rate_10y"] = r10
    if r30 is not None:
        out["rate_30y"] = r30
    if r2 is not None and r10 is not None:
        spread = round((r10 - r2) * 100, 0)   # percentage points -> basis points
        out["spread_2s10s"] = spread
        out["inverted"] = spread < 0
    return out


def hy_spread() -> float | None:
    """ICE BofA US High Yield OAS in basis points, or None when disabled/missing.

    FRED BAMLH0A0HYM2 is quoted in percentage points; x100 -> bps to match the
    yield-curve spread unit. Widening HY OAS = rising credit stress (risk-off)."""
    if not _key():
        return None
    v = _latest("BAMLH0A0HYM2")
    return round(v * 100, 0) if v is not None else None


def regime_label() -> str | None:
    """One-word risk backdrop from curve + credit, or None when disabled/insufficient.

    Deterministic, no LLM: 'risk-off' when the curve is inverted or HY OAS is wide
    (>=500bps), 'caution' on one mild stress signal, else 'risk-on'."""
    if not _key():
        return None
    yc = yield_curve()
    hy = hy_spread()
    if not yc and hy is None:
        return None
    inverted = bool(yc.get("inverted"))
    wide = hy is not None and hy >= 500
    moderate = hy is not None and hy >= 400
    if inverted and wide:
        return "risk-off"
    if inverted or wide:
        return "caution"
    if moderate:
        return "caution"
    return "risk-on"
