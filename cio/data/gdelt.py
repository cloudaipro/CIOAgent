"""GDELT DOC 2.1 — global news headlines + tone/volume for one query.

The zero-cost news source: GDELT's DOC API is free, needs NO API key, and returns
JSON, so it slots straight onto the existing _http.get_json path. It indexes
worldwide news in ~15-min cycles — ideal for the news-spike detector (F5), which
needs article VOLUME per symbol over time, and gives a sentiment hint (avg tone)
for free.

Because it is keyless, GDELT is the one cio.data source that is ENABLED BY DEFAULT
(every other source needs a key/UA). Set CIO_GDELT_ENABLED=0 to turn it off. Still
offline-safe: any network failure degrades to []/zeros via get_json, never raises.

Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
"""
from __future__ import annotations

import logging
import os

from . import _cache
from ._http import RateLimiter, get_json

log = logging.getLogger(__name__)

_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# GDELT asks light users to stay around 1 request/sec.
_limiter = RateLimiter(1.0)

_ART_TTL = 3600   # headlines: 1h
_TONE_TTL = 3600  # tone/volume: 1h


def _enabled() -> bool:
    v = (os.getenv("CIO_GDELT_ENABLED") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _with_lang(query: str) -> str:
    """Append a GDELT `sourcelang:` filter so we only get coverage in one language.

    GDELT is global/multilingual — a bare ticker/name query returns headlines in
    every language, which is noise for an English-reading operator AND skews the
    spike volume baseline with foreign-language coverage. Default `eng` (verified
    live: GDELT's English code). CIO_GDELT_LANG overrides; set it empty to opt back
    into all languages. Applied to BOTH headlines and tone_volume so the displayed
    stories and the spike volume count use the same language scope."""
    lang = (os.getenv("CIO_GDELT_LANG", "eng") or "").strip()
    if lang and "sourcelang:" not in query:
        return f"{query} sourcelang:{lang}"
    return query


def _record_fresh(n: int) -> None:
    if n <= 0:
        return
    try:
        from . import freshness
        freshness.record("gdelt", n)
    except Exception:
        pass


def _parse_articles(rows, limit: int) -> list[dict]:
    out: list[dict] = []
    for a in (rows or []):
        if not isinstance(a, dict):
            continue
        domain = (a.get("domain") or "").strip()
        out.append({
            "title": (a.get("title") or "").strip(),
            "url": (a.get("url") or "").strip(),
            "source": domain,
            "domain": domain,
            "seendate": a.get("seendate"),
        })
        if len(out) >= limit:
            break
    return out


def headlines(query: str, hours: int = 24, limit: int = 20) -> list[dict]:
    """Recent news articles matching *query*, newest-first. [] when disabled/empty.

    Each row: {title, url, source, domain, seendate}. *query* is the GDELT DOC
    query string (e.g. a company name); the caller is responsible for building it.
    """
    if not _enabled():
        return []
    q = (query or "").strip()
    if not q:
        return []
    q = _with_lang(q)              # English-only by default; cache key varies with it
    n = min(250, max(1, int(limit)))
    h = max(1, int(hours))
    key = f"{q}:{h}:{n}"
    cached = _cache.read("gdelt_art", key, _ART_TTL)
    if cached is None:
        params = {"query": q, "mode": "ArtList", "format": "json",
                  "maxrecords": n, "timespan": f"{h}h", "sort": "DateDesc"}
        data = get_json(_DOC_URL, params=params, limiter=_limiter)
        arts = data.get("articles") if isinstance(data, dict) else None
        cached = arts if isinstance(arts, list) else []
        _cache.write("gdelt_art", key, cached)
        _record_fresh(len(cached))
    return _parse_articles(cached, limit)


def tone_volume(query: str, hours: int = 24) -> dict:
    """{volume, avg_tone} for *query* over the window. Zeros when disabled/empty.

    One ToneChart call yields both: volume = total matching articles, avg_tone =
    count-weighted mean of GDELT's tone bins (negative = more negative coverage).
    Feeds the F5 spike baseline + a cheap sentiment hint.
    """
    zero = {"volume": 0, "avg_tone": 0.0}
    if not _enabled():
        return zero
    q = (query or "").strip()
    if not q:
        return zero
    q = _with_lang(q)              # same language scope as headlines() -> consistent volume
    h = max(1, int(hours))
    cached = _cache.read("gdelt_tone", f"{q}:{h}", _TONE_TTL)
    if cached is None:
        params = {"query": q, "mode": "ToneChart", "format": "json",
                  "timespan": f"{h}h"}
        data = get_json(_DOC_URL, params=params, limiter=_limiter)
        bins = data.get("tonechart") if isinstance(data, dict) else None
        cached = bins if isinstance(bins, list) else []
        _cache.write("gdelt_tone", f"{q}:{h}", cached)
        _record_fresh(len(cached))
    total = sum(int(b.get("count", 0)) for b in cached if isinstance(b, dict))
    if total <= 0:
        return zero
    weighted = sum(float(b.get("bin", 0)) * int(b.get("count", 0))
                   for b in cached if isinstance(b, dict))
    return {"volume": total, "avg_tone": round(weighted / total, 2)}
