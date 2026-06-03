"""
agent.py — Watchlist Monitoring Agent core.

For each security on the watchlist the WMA:
  1. gathers a data bundle (price / fundamentals / TA) — reused from the committee
  2. pulls overnight web headlines (Firecrawl, offline-safe)
  3. asks the ``wma`` model chain for a one-security assessment (PRD §7)

It is the FIRST layer of the architecture (PRD §11): one LLM call per security,
far cheaper than the full committee. Securities with high/critical events are
flagged for committee escalation rather than auto-analysed, to respect the
committee's per-run cost ceiling.

Every function is offline-safe — missing data / API errors degrade to a partial
assessment, never an exception.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from datetime import datetime

from .prompts import WMA_SYSTEM

log = logging.getLogger(__name__)

# One LLM + one web call per security; keep concurrency modest so a big watchlist
# doesn't hammer the model backends or Firecrawl. Env-tunable.
MAX_CONC = int(os.getenv("CIO_WMA_CONCURRENCY", "4"))

_RECS = {"Buy", "Add", "Hold", "Monitor", "Reduce", "Sell"}
_STATUS = {"bullish", "neutral", "bearish"}
_IMPORTANCE = {"low", "medium", "high", "critical"}
_THESIS = {"unchanged", "positive", "negative"}


def _as_list(val) -> list:
    """Coerce a yaml value to a clean list of non-empty strings."""
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        return [s] if s and s.lower() not in ("none", "n/a", "[]") else []
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    return [str(val).strip()]


def _one_of(val, allowed: set[str], default: str) -> str:
    s = str(val or "").strip().lower()
    return s if s in allowed else default


def _rec(val) -> str:
    s = str(val or "").strip().title()
    return s if s in _RECS else "Monitor"


def _conviction(val) -> int:
    try:
        return max(0, min(100, int(float(val))))
    except (TypeError, ValueError):
        return 50


def _headlines_text(news: list[dict]) -> str:
    """Render web results as a compact numbered block for the prompt."""
    if not news:
        return "none found"
    lines = []
    for i, item in enumerate(news, 1):
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        desc = (item.get("description") or "").strip()
        url = (item.get("url") or "").strip()
        line = f"{i}. {title}"
        if desc:
            line += f" — {desc}"
        if url:
            line += f" ({url})"
        lines.append(line)
    return "\n".join(lines) if lines else "none found"


async def _fetch_news(symbol: str, company: str | None, news_fn) -> list[dict]:
    """Overnight headlines via Firecrawl (or an injected *news_fn* for tests)."""
    if news_fn is None:
        from .. import web
        news_fn = web.search
    query = f"{company or symbol} stock news analyst rating earnings guidance"
    try:
        res = news_fn(query, limit=5)
        if inspect.isawaitable(res):
            res = await res
        return list(res or [])
    except Exception as e:
        log.debug("WMA news fetch failed for %s: %s", symbol, e)
        return []


def _skipped(symbol: str, reason: str) -> dict:
    """Assessment stub for a security we couldn't price (no LLM call spent)."""
    return {
        "ticker": symbol, "company": symbol,
        "overall_status": "neutral", "conviction_score": 0,
        "recommendation": "Monitor", "analyst_sentiment": "neutral",
        "event_importance": "low", "investment_thesis_change": "unchanged",
        "key_positive_events": [], "key_negative_events": [],
        "new_risks": [], "upcoming_catalysts": [],
        "summary": reason, "escalate": False, "error": reason, "_raw": "",
    }


async def monitor_symbol(symbol: str, *, bundle_fn=None, news_fn=None) -> dict:
    """Assess one security. Returns a normalized assessment dict (PRD §7).

    *bundle_fn* / *news_fn* are injectable for tests; both default to the live
    committee bundle and Firecrawl web search. Never raises.
    """
    from ..committee import engine
    from ..committee.bundle import gather_bundle, format_bundle

    bundle_fn = bundle_fn or gather_bundle
    try:
        bundle = await asyncio.to_thread(bundle_fn, symbol)
    except Exception as e:
        log.debug("WMA bundle failed for %s: %s", symbol, e)
        return _skipped(symbol, f"data error: {e}")

    if not bundle or bundle.get("resolved") is None:
        return _skipped(symbol, f"no data for {symbol}")

    resolved = bundle["resolved"]
    fund = bundle.get("fundamentals") or {}
    company = fund.get("name") or resolved
    bundle_text = format_bundle(bundle)

    news = await _fetch_news(resolved, company, news_fn)
    user_prompt = (
        f"You are analyzing: {resolved} ({company})\n\n"
        f"DATA:\n{bundle_text}\n\n"
        f"OVERNIGHT_HEADLINES:\n{_headlines_text(news)}"
    )

    raw = await engine.ask_role(WMA_SYSTEM, user_prompt, role_key="wma")
    parsed = engine.parse_yaml_block(raw)

    importance = _one_of(parsed.get("event_importance"), _IMPORTANCE, "low")
    thesis = _one_of(parsed.get("investment_thesis_change"), _THESIS, "unchanged")
    return {
        "ticker": resolved,
        "company": str(parsed.get("company") or company),
        "overall_status": _one_of(parsed.get("overall_status"), _STATUS, "neutral"),
        "conviction_score": _conviction(parsed.get("conviction_score")),
        "recommendation": _rec(parsed.get("recommendation")),
        "analyst_sentiment": _one_of(parsed.get("analyst_sentiment"), _STATUS, "neutral"),
        "event_importance": importance,
        "investment_thesis_change": thesis,
        "key_positive_events": _as_list(parsed.get("key_positive_events")),
        "key_negative_events": _as_list(parsed.get("key_negative_events")),
        "new_risks": _as_list(parsed.get("new_risks")),
        "upcoming_catalysts": _as_list(parsed.get("upcoming_catalysts")),
        "summary": str(parsed.get("summary") or parsed.get("_raw") or "").strip(),
        # Escalate to the full committee on a thesis-breaking event (PRD §11).
        "escalate": importance in ("high", "critical") or thesis == "negative",
        "error": None,
        "_raw": raw,
    }


async def monitor_watchlist(symbols: list[str] | None = None, *,
                            bundle_fn=None, news_fn=None) -> list[dict]:
    """Assess every security on *symbols* (default: the active watchlist).

    Runs under a bounded semaphore so a large list stays within rate limits.
    Returns assessments in input order. Returns [] when there is no watchlist.
    """
    if symbols is None:
        from .. import watchlist
        wl = watchlist.active()
        symbols = wl["symbols"] if wl else []
    if not symbols:
        return []

    sem = asyncio.Semaphore(max(1, MAX_CONC))

    async def _bounded(sym: str) -> dict:
        async with sem:
            try:
                return await monitor_symbol(sym, bundle_fn=bundle_fn, news_fn=news_fn)
            except Exception as e:  # defensive — monitor_symbol shouldn't raise
                log.warning("WMA monitor_symbol crashed for %s: %s", sym, e)
                return _skipped(sym, f"error: {e}")

    return list(await asyncio.gather(*[_bounded(s) for s in symbols]))


def as_of_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")
