"""F5 — news-spike detector: unscheduled-catalyst alerting for watchlist names.

econ_calendar.py covers DATED catalysts (NFP/CPI/FOMC). This is the other half: an
UNSCHEDULED news surge on a holding — the kind that "blindsides the portfolio".

Ported from worldmonitor's trending-keywords spike rule (rolling window vs 7-day
baseline, multi-source gate, cooldown). Key adaptation: worldmonitor persists a
rolling timestamp window because the browser ingests RSS itself; we instead QUERY
GDELT, which already aggregates global article volume over time. So GDELT is our
history store — no rolling-window table to keep. The only persistent state is the
per-catalyst alert cooldown (cio.alerts, F9).

Zero LLM — pure article counts. Offline-safe: sources off -> no volume -> no spike
-> silence. Tunable via env: CIO_SPIKE_MIN_COUNT (5), CIO_SPIKE_MULT (3),
CIO_SPIKE_MIN_SOURCES (2), CIO_SPIKE_COOLDOWN_MIN (30).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_WINDOW_H = 2                          # "now" window
_BASELINE_H = 168                      # 7 days
_WINDOWS_IN_BASELINE = _BASELINE_H / _WINDOW_H   # 84 two-hour windows in a week


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


def _min_count() -> int:
    return max(1, _int_env("CIO_SPIKE_MIN_COUNT", 5))


def _mult() -> float:
    return max(1.0, _float_env("CIO_SPIKE_MULT", 3.0))


def _min_sources() -> int:
    return max(1, _int_env("CIO_SPIKE_MIN_SOURCES", 2))


def cooldown_s() -> int:
    return max(1, _int_env("CIO_SPIKE_COOLDOWN_MIN", 30)) * 60


def _query_for(symbol: str) -> str:
    """GDELT query for a ticker — the company name (quoted phrase) when Finnhub can
    resolve it, else the bare ticker. A name beats a ticker: 'AAPL' alone is noisy,
    '"Apple Inc"' is precise."""
    try:
        from cio.data import finnhub
        prof = finnhub.company_profile(symbol)
        name = prof.get("name") if isinstance(prof, dict) else None
        if name:
            return f'"{name.strip()}"'
    except Exception:
        pass
    return symbol


def detect_spike(symbol, *, query=None, tone_fn=None, headlines_fn=None, news_fn=None):
    """Return a spike dict for *symbol*, or None when no spike.

    Spike iff articles in the last 2h >= CIO_SPIKE_MIN_COUNT AND (>= MULT x the 2h
    baseline derived from 7-day volume, OR no prior coverage at all) AND >= 2
    distinct sources. Functions are injectable for offline tests.
    """
    from cio.data import gdelt, finnhub
    tone_fn = tone_fn or gdelt.tone_volume
    headlines_fn = headlines_fn or gdelt.headlines
    news_fn = news_fn or finnhub.company_news

    q = query or _query_for(symbol)
    vol_2h = int((tone_fn(q, hours=_WINDOW_H) or {}).get("volume", 0))
    if vol_2h < _min_count():
        return None

    vol_7d = int((tone_fn(q, hours=_BASELINE_H) or {}).get("volume", 0))
    baseline = (vol_7d / _WINDOWS_IN_BASELINE) if vol_7d > 0 else 0.0
    multiplier = (vol_2h / baseline) if baseline > 0 else None
    if baseline > 0 and vol_2h < _mult() * baseline:
        return None

    heads = headlines_fn(q, hours=_WINDOW_H, limit=20) or []
    news = news_fn(symbol) or []
    sources = {(h.get("domain") or "").lower() for h in heads}
    sources |= {(n.get("source") or "").lower() for n in news}
    sources.discard("")
    if len(sources) < _min_sources():
        return None

    top = [{"title": h.get("title", ""), "url": h.get("url", ""),
            "source": h.get("domain", "")} for h in heads[:3] if h.get("title")]
    if not top:
        top = [{"title": n.get("title", ""), "url": n.get("url", ""),
                "source": n.get("source", "")} for n in news[:3] if n.get("title")]

    return {
        "symbol": symbol,
        "count": vol_2h,
        "baseline": round(baseline, 2),
        "multiplier": round(multiplier, 1) if multiplier is not None else None,
        "sources": len(sources),
        "top_headlines": top,
        "query": q,
    }


def active_symbols() -> list[str]:
    """Symbols on the active watchlist ([] when none / on error)."""
    try:
        from .. import watchlist
        wl = watchlist.active()
        return list(wl["symbols"]) if wl and wl.get("symbols") else []
    except Exception:
        return []


def scan(symbols=None) -> list[dict]:
    """Detect spikes across *symbols* (default: active watchlist). Never raises."""
    if symbols is None:
        symbols = active_symbols()
    out: list[dict] = []
    for s in symbols or []:
        try:
            sp = detect_spike(s)
            if sp:
                out.append(sp)
        except Exception:
            log.debug("spike scan failed for %s", s, exc_info=True)
    return out


def format_spike_alert(spikes: list[dict]) -> str:
    """Deterministic Telegram text for one or more spikes. Plain text (no parse_mode)."""
    lines = ["🚨 News spike on your watchlist", ""]
    for sp in spikes:
        mult = f"{sp['multiplier']}×" if sp.get("multiplier") else "new coverage"
        lines.append(
            f"• {sp['symbol']}: {sp['count']} articles/2h "
            f"({mult} vs 7d baseline, {sp['sources']} sources)"
        )
        for h in sp.get("top_headlines", [])[:2]:
            t = (h.get("title") or "").strip()
            if t:
                lines.append(f"    – {t}")
    lines += [
        "",
        "Unscheduled catalyst — review before it moves. Not a committee verdict.",
    ]
    return "\n".join(lines)
