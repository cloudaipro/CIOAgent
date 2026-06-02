"""Web search + scrape backed by Firecrawl.

Thin async wrapper over a Firecrawl instance (self-hosted or cloud). The agent's
``web_search`` / ``web_scrape`` tools call these so the CIO can pull live web
context (news, analyst pages, filings) that yfinance does not carry.

Config (env):
  CIO_FIRECRAWL_URL  base URL (falls back to FIRECRAWL_API_URL, then localhost:3002)
  FIRECRAWL_API_KEY  optional bearer token (self-hosted instances need none)
  CIO_WEB_MAX_CHARS  per-result markdown cap, protects the prompt budget (default 6000)
  CIO_WEB_TIMEOUT    request timeout seconds (default 45)

Every function is offline-safe: any failure returns an empty result / error dict
rather than raising, so a flaky network never breaks a turn.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def _base_url() -> str:
    url = (os.getenv("CIO_FIRECRAWL_URL")
           or os.getenv("FIRECRAWL_API_URL")
           or "http://localhost:3002")
    return url.rstrip("/")


def _headers() -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    key = os.getenv("FIRECRAWL_API_KEY")
    if key:
        h["Authorization"] = f"Bearer {key}"
    return h


def _max_chars() -> int:
    try:
        return int(os.getenv("CIO_WEB_MAX_CHARS", "6000"))
    except ValueError:
        return 6000


def _timeout() -> float:
    try:
        return float(os.getenv("CIO_WEB_TIMEOUT", "45"))
    except ValueError:
        return 45.0


async def search(query: str, limit: int = 5, scrape: bool = False) -> list[dict]:
    """Web search via Firecrawl ``/v2/search``.

    Returns a list of ``{title, url, description, markdown?}`` dicts (markdown only
    when *scrape* is True). Returns ``[]`` on any error.
    """
    import httpx

    payload: dict = {"query": query, "limit": max(1, min(int(limit or 5), 10))}
    if scrape:
        payload["scrapeOptions"] = {"formats": ["markdown"]}

    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(_base_url() + "/v2/search",
                                     headers=_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("web.search failed for %r: %s", query, e)
        return []

    web = ((data or {}).get("data") or {}).get("web") or []
    cap = _max_chars()
    out: list[dict] = []
    for item in web:
        if not isinstance(item, dict):
            continue
        row = {
            "title": item.get("title") or "",
            "url": item.get("url") or "",
            "description": item.get("description") or "",
        }
        md = item.get("markdown")
        if md:
            row["markdown"] = md[:cap]
        out.append(row)
    return out


async def scrape(url: str) -> dict:
    """Scrape one URL to markdown via Firecrawl ``/v2/scrape``.

    Returns ``{url, title, markdown}`` (markdown capped to CIO_WEB_MAX_CHARS) or
    ``{url, error}`` on failure.
    """
    import httpx

    payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(_base_url() + "/v2/scrape",
                                     headers=_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("web.scrape failed for %s: %s", url, e)
        return {"url": url, "error": str(e)}

    d = (data or {}).get("data") or {}
    md = d.get("markdown") or ""
    meta = d.get("metadata") or {}
    return {
        "url": url,
        "title": meta.get("title") or "",
        "markdown": md[:_max_chars()],
    }
