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
_OWN_TTL = 24 * 3600


def _token() -> str | None:
    t = (os.getenv("FINNHUB_API_KEY") or "").strip()
    return t or None


def _record_fresh(n: int) -> None:
    """Heartbeat the data-freshness monitor when a Finnhub call yields data.
    Best-effort — a freshness-store hiccup must never affect a data fetch."""
    if n <= 0:
        return
    try:
        from . import freshness
        freshness.record("finnhub", n)
    except Exception:
        pass


def _institutional_enabled() -> bool:
    """Institutional ownership is a PREMIUM Finnhub endpoint. Off by default so the
    free tier never hammers it with 403s (one wasted ~1s rate-limited call + a log
    warning per symbol). Opt in with CIO_FINNHUB_INSTITUTIONAL=1 once the key has
    institutional-data access."""
    return (os.getenv("CIO_FINNHUB_INSTITUTIONAL") or "").strip().lower() in (
        "1", "true", "yes", "on")


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
    out = _parse_news(cached, limit)
    _record_fresh(len(out))
    return out


# --- analyst recommendation trends -----------------------------------------
def _norm_rec_row(r: dict) -> dict:
    """One /stock/recommendation row -> our snake_case shape."""
    return {
        "period": r.get("period"),
        "strong_buy": r.get("strongBuy"),
        "buy": r.get("buy"),
        "hold": r.get("hold"),
        "sell": r.get("sell"),
        "strong_sell": r.get("strongSell"),
    }


def _sorted_rec_rows(rows):
    """Recommendation rows newest-first; [] when unusable."""
    return sorted((r for r in (rows or []) if isinstance(r, dict) and r.get("period")),
                  key=lambda r: r.get("period", ""), reverse=True)


def _latest_recs(rows):
    ordered = _sorted_rec_rows(rows)
    return _norm_rec_row(ordered[0]) if ordered else None


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
    rec = _latest_recs(cached)
    _record_fresh(1 if rec else 0)
    return rec


def analyst_recs_history(symbol: str, periods: int = 2) -> list[dict]:
    """Latest *periods* analyst-rec snapshots, newest first. [] when disabled/missing.

    Reuses the SAME cached /stock/recommendation payload as analyst_recs (Finnhub
    returns several months of counts in one response), so a second period costs no
    extra network call once warm. Feeds the behavior-layer trend delta (OD-4)."""
    tok = _token()
    if not tok:
        return []
    sym = symbol.strip().upper()
    cached = _cache.read("finnhub_recs", sym, _RECS_TTL)
    if cached is None:
        data = get_json(f"{_BASE}/stock/recommendation",
                        params={"symbol": sym, "token": tok}, limiter=_limiter)
        cached = data if isinstance(data, list) else []
        _cache.write("finnhub_recs", sym, cached)
    return [_norm_rec_row(r) for r in _sorted_rec_rows(cached)[:max(1, periods)]]


# --- institutional ownership (13F) -----------------------------------------
def _institutional_pct(data) -> float | None:
    """Sum the latest 13F report's per-holder percentages into one ownership %.

    None when the payload carries no usable holder percentages. Clamped 0..100
    (13F sums can exceed 100 via options/overlap)."""
    if not isinstance(data, dict):
        return None
    reports = data.get("data")
    if not isinstance(reports, list) or not reports:
        return None
    try:
        latest = sorted(reports, key=lambda r: r.get("reportDate", ""), reverse=True)[0]
    except Exception:
        return None
    holders = latest.get("ownership")
    if not isinstance(holders, list) or not holders:
        return None
    total, seen = 0.0, False
    for h in holders:
        pct = h.get("percentage") if isinstance(h, dict) else None
        if isinstance(pct, (int, float)):
            total += float(pct)
            seen = True
    if not seen:
        return None
    return round(max(0.0, min(100.0, total)), 2)


def institutional_ownership_pct(symbol: str) -> float | None:
    """Institutional ownership % of *symbol* from 13F filings, or None.

    Sums the most recent 13F report's per-holder percentages
    (Finnhub /stock/ownership). Feeds the Alpha Hunter coverage-density blend
    (cio.alpha.coverage): low institutional ownership = neglected = higher edge.

    NOTE: /stock/ownership is a PREMIUM Finnhub endpoint, so this is OFF BY DEFAULT —
    set CIO_FINNHUB_INSTITUTIONAL=1 to enable it once your key has institutional-data
    access. Disabled, or no token, or a 403 on a non-premium key -> None (no signal;
    coverage treats None safely). Gating it off by default stops the free tier from
    hitting a guaranteed-403 endpoint once per symbol (log spam + wasted rate-limited
    calls). The summed % is best-effort (depends on how many holders the tier returns)."""
    if not _institutional_enabled():
        return None
    tok = _token()
    if not tok:
        return None
    sym = symbol.strip().upper()
    cached = _cache.read("finnhub_ownership", sym, _OWN_TTL)
    if cached is None:
        data = get_json(f"{_BASE}/stock/ownership",
                        params={"symbol": sym, "token": tok}, limiter=_limiter)
        cached = data if isinstance(data, dict) else {}
        _cache.write("finnhub_ownership", sym, cached)
    return _institutional_pct(cached)


# --- insider transactions --------------------------------------------------
_INSIDER_TTL = 24 * 3600


def _parse_insider_rows(rows, limit: int) -> list[dict]:
    """Finnhub /stock/insider-transactions rows -> our snake_case shape.

    is_buy flags an open-market PURCHASE (transactionCode 'P') with a positive
    share change — the conviction signal. Grants ('A'), option exercises ('M'),
    gifts etc. inflate `change` but are NOT discretionary buys, so they are not
    counted as buys."""
    out: list[dict] = []
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        code = (r.get("transactionCode") or "").strip().upper()
        change = r.get("change")
        num_change = change if isinstance(change, (int, float)) else None
        out.append({
            "name": (r.get("name") or "").strip(),
            "change": num_change,
            "transaction_date": r.get("transactionDate") or r.get("filingDate"),
            "transaction_code": code,
            "transaction_price": r.get("transactionPrice"),
            "is_buy": code == "P" and num_change is not None and num_change > 0,
        })
        if len(out) >= limit:
            break
    return out


def insider_transactions(symbol: str, months: int = 3, limit: int = 40) -> list[dict]:
    """Recent insider transactions for *symbol*, newest-first. [] when disabled.

    Source: Finnhub /stock/insider-transactions (free tier). Each row:
    {name, change, transaction_date, transaction_code, transaction_price, is_buy}.
    Returns [] both when disabled (no key) and when there is simply no data — the
    aggregate helper insider_net() is the one that distinguishes the two (None vs
    zeros), matching how callers actually branch."""
    tok = _token()
    if not tok:
        return []
    sym = symbol.strip().upper()
    cached = _cache.read("finnhub_insider", f"{sym}:{months}", _INSIDER_TTL)
    if cached is None:
        today = date.today()
        params = {"symbol": sym,
                  "from": str(today - timedelta(days=int(max(1, months)) * 31)),
                  "to": str(today), "token": tok}
        data = get_json(f"{_BASE}/stock/insider-transactions", params=params,
                        limiter=_limiter)
        rows = data.get("data") if isinstance(data, dict) else None
        cached = rows if isinstance(rows, list) else []
        _cache.write("finnhub_insider", f"{sym}:{months}", cached)
    return _parse_insider_rows(cached, limit)


def insider_net(symbol: str, months: int = 3, cluster_min: int = 3) -> dict | None:
    """Aggregate insider buy/sell pressure for *symbol*. None when disabled.

    {buy_count, sell_count, net_shares, cluster_buy}. cluster_buy is True when at
    least *cluster_min* DISTINCT insiders made open-market purchases in the window
    — the classic conviction cluster the Alpha funnel and committee care about.
    Returns a zero-filled dict (not None) when enabled but no data, so callers can
    tell 'disabled' from 'quiet'."""
    if not _token():
        return None
    rows = insider_transactions(symbol, months=months, limit=200)
    buyers: set[str] = set()
    buy_count = sell_count = 0
    net = 0.0
    for r in rows:
        change = r.get("change")
        if not isinstance(change, (int, float)):
            continue
        net += change
        if r.get("is_buy"):
            buy_count += 1
            if r.get("name"):
                buyers.add(r["name"])
        elif r.get("transaction_code") == "S" and change < 0:
            sell_count += 1
    return {
        "buy_count": buy_count,
        "sell_count": sell_count,
        "net_shares": int(net),
        "cluster_buy": len(buyers) >= max(1, cluster_min),
    }


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


# --- earnings surprises (beat history) -------------------------------------
def earnings_surprises(symbol: str, quarters: int = 4) -> list[dict] | None:
    """Last *quarters* reported EPS actual-vs-estimate rows, newest first.

    Each row: {period, actual, estimate, beat}. None when finnhub is disabled.
    Empty list when finnhub is enabled but returns no history (so callers can tell
    "disabled" from "no data"). Source: /stock/earnings (free tier).
    """
    tok = _token()
    if not tok:
        return None
    sym = symbol.strip().upper()
    cached = _cache.read("finnhub_surprise", sym, _EARN_TTL)
    if cached is None:
        data = get_json(f"{_BASE}/stock/earnings",
                        params={"symbol": sym, "token": tok}, limiter=_limiter)
        cached = data if isinstance(data, list) else []
        _cache.write("finnhub_surprise", sym, cached)
    rows = sorted(
        (r for r in cached if isinstance(r, dict) and r.get("period")),
        key=lambda r: r.get("period", ""), reverse=True,
    )[:quarters]
    out = []
    for r in rows:
        actual, est = r.get("actual"), r.get("estimate")
        beat = actual is not None and est is not None and actual > est
        out.append({"period": r.get("period"), "actual": actual,
                    "estimate": est, "beat": beat})
    return out


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
