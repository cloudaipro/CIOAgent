"""Finnhub: analyst recommendations, earnings calendar, company news.

Structured financial context the committee bundle was missing — it previously saw
only price / fundamentals / TA, so specialists inferred analyst sentiment and
catalysts from headlines. Finnhub gives ground-truth:

  * analyst_recs(symbol)      -> latest buy / hold / sell counts
  * earnings_calendar(symbol) -> next earnings date + estimates
  * company_news(symbol)      -> recent structured headlines

Enabled by FINNHUB_API_KEY (free tier: 60 calls/min). Unset = disabled, returns
empty with no network call, so the test suite / CI stay offline by default.
Every function is offline-safe.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from . import _cache
from ._http import RateLimiter, get_json

log = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"
# Free tier = 60 calls/min -> ~1.05s between calls keeps us under the cap.
_limiter = RateLimiter(1.05)

_NEWS_TTL = 3 * 3600
_RECS_TTL = 24 * 3600
_EARN_TTL = 24 * 3600
_PROFILE_TTL = 24 * 3600


def _token() -> str | None:
    t = (os.getenv("FINNHUB_API_KEY") or "").strip()
    return t or None


# --- company news ----------------------------------------------------------
def _parse_news(rows, limit: int) -> list[dict]:
    out: list[dict] = []
    for it in (rows or []):
        if not isinstance(it, dict):
            continue
        out.append({
            "title": (it.get("headline") or "").strip(),
            "description": (it.get("summary") or "").strip(),
            "url": (it.get("url") or "").strip(),
            "source": (it.get("source") or "").strip(),
            "datetime": it.get("datetime"),
        })
        if len(out) >= limit:
            break
    return out


def company_news(symbol: str, days: int = 7, limit: int = 8) -> list[dict]:
    """Recent company headlines (title/summary/source/url). [] when disabled."""
    tok = _token()
    if not tok:
        return []
    sym = symbol.strip().upper()
    cached = _cache.read("finnhub_news", f"{sym}:{days}", _NEWS_TTL)
    if cached is None:
        today = date.today()
        params = {"symbol": sym, "from": str(today - timedelta(days=days)),
                  "to": str(today), "token": tok}
        data = get_json(f"{_BASE}/company-news", params=params, limiter=_limiter)
        cached = data if isinstance(data, list) else []
        _cache.write("finnhub_news", f"{sym}:{days}", cached)
    return _parse_news(cached, limit)


# --- analyst recommendation trends -----------------------------------------
def _latest_recs(rows):
    if not rows:
        return None
    try:
        latest = sorted(rows, key=lambda r: r.get("period", ""), reverse=True)[0]
    except Exception:
        return None
    return {
        "period": latest.get("period"),
        "strong_buy": latest.get("strongBuy"),
        "buy": latest.get("buy"),
        "hold": latest.get("hold"),
        "sell": latest.get("sell"),
        "strong_sell": latest.get("strongSell"),
    }


def analyst_recs(symbol: str):
    """Latest analyst buy/hold/sell counts dict, or None when disabled/missing."""
    tok = _token()
    if not tok:
        return None
    sym = symbol.strip().upper()
    cached = _cache.read("finnhub_recs", sym, _RECS_TTL)
    if cached is None:
        data = get_json(f"{_BASE}/stock/recommendation",
                        params={"symbol": sym, "token": tok}, limiter=_limiter)
        cached = data if isinstance(data, list) else []
        _cache.write("finnhub_recs", sym, cached)
    return _latest_recs(cached)


# --- earnings calendar -----------------------------------------------------
def _next_earnings(rows, today: str | None = None):
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("date")]
    if not rows:
        return None
    today = today or date.today().isoformat()
    try:
        future = sorted((r for r in rows if r["date"] >= today), key=lambda r: r["date"])
        pick = future[0] if future else sorted(rows, key=lambda r: r["date"])[-1]
    except Exception:
        return None
    return {
        "date": pick.get("date"),
        "eps_estimate": pick.get("epsEstimate"),
        "eps_actual": pick.get("epsActual"),
        "revenue_estimate": pick.get("revenueEstimate"),
        "hour": pick.get("hour"),
    }


# --- company profile -------------------------------------------------------
def company_profile(symbol: str) -> dict | None:
    """Company profile including official website URL. None when disabled/missing.

    Returns a subset of Finnhub's /stock/profile2 response:
      {name, weburl, finnhubIndustry, ipo, marketCap}

    weburl is used for issuer-domain resolution (source_policy classify).
    """
    tok = _token()
    if not tok:
        return None
    sym = symbol.strip().upper()
    cached = _cache.read("finnhub_profile", sym, _PROFILE_TTL)
    if cached is None:
        data = get_json(f"{_BASE}/stock/profile2",
                        params={"symbol": sym, "token": tok}, limiter=_limiter)
        if not isinstance(data, dict) or not data:
            _cache.write("finnhub_profile", sym, {})
            return None
        cached = {
            "name": data.get("name") or "",
            "weburl": data.get("weburl") or "",
            "finnhubIndustry": data.get("finnhubIndustry") or "",
            "ipo": data.get("ipo") or "",
            "marketCap": data.get("marketCapitalization"),
        }
        _cache.write("finnhub_profile", sym, cached)
    return cached if cached else None


def earnings_calendar(symbol: str, days_ahead: int = 120):
    """Next (or most recent) earnings event dict, or None when disabled/missing."""
    tok = _token()
    if not tok:
        return None
    sym = symbol.strip().upper()
    cached = _cache.read("finnhub_earn", sym, _EARN_TTL)
    if cached is None:
        today = date.today()
        params = {"symbol": sym, "from": str(today - timedelta(days=2)),
                  "to": str(today + timedelta(days=days_ahead)), "token": tok}
        data = get_json(f"{_BASE}/calendar/earnings", params=params, limiter=_limiter)
        cal = (data or {}).get("earningsCalendar") if isinstance(data, dict) else None
        cached = cal if isinstance(cal, list) else []
        _cache.write("finnhub_earn", sym, cached)
    return _next_earnings(cached)
