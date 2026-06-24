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
    ``{url, error}`` on failure. When Firecrawl fails or returns nothing — which
    is what anti-bot hosts (DataDome marketscreener) trigger as a backend 500 /
    ``document_antibot`` — and the browser fallback is enabled, retry with a real
    headless Chromium that carries a browser fingerprint Firecrawl can't.
    """
    import httpx

    payload = {"url": url, "formats": ["markdown"], "onlyMainContent": True}
    err: str | None = None
    try:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(_base_url() + "/v2/scrape",
                                     headers=_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("web.scrape failed for %s: %s", url, e)
        data, err = None, str(e)

    d = (data or {}).get("data") or {}
    md = d.get("markdown") or ""
    meta = d.get("metadata") or {}

    if not md and _browser_enabled():
        fb = await _browser_scrape(url)
        if fb and fb.get("markdown"):
            return fb
        err = err or "firecrawl returned no content (anti-bot?); browser fallback empty"

    if not md:
        return {"url": url, "error": err or "no content"}

    return {
        "url": url,
        "title": meta.get("title") or "",
        "markdown": md[:_max_chars()],
    }


def _browser_enabled() -> bool:
    # Dedicated CIO_WEB_BROWSER, falling back to the citation gate's flag so a
    # single CIO_CITATION_BROWSER=1 turns on headless rescue for both paths.
    v = (os.getenv("CIO_WEB_BROWSER")
         or os.getenv("CIO_CITATION_BROWSER") or "")
    return v.strip().lower() in {"1", "true", "yes", "on"}


# Desktop-Chrome UA; default headless string is itself a bot tell.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def _browser_scrape(url: str) -> dict | None:
    """Headless Chromium fallback (playwright async + light stealth). Returns
    ``{url, title, markdown}`` from the rendered page body, or None on any
    failure (playwright missing, launch error, blocked). Never raises."""
    try:
        from playwright.async_api import async_playwright
    except Exception:
        log.warning("web._browser_scrape: playwright not installed")
        return None
    try:
        async with async_playwright() as p:
            # Real Chrome (channel="chrome") carries a fingerprint Akamai/DataDome
            # trust; the bundled chromium gets an "Access Denied" shell on hosts
            # like marketscreener. Fall back to bundled if the channel is absent.
            launch_args = dict(
                headless=True,
                args=["--no-sandbox",
                      "--disable-blink-features=AutomationControlled"],
            )
            try:
                browser = await p.chromium.launch(channel="chrome", **launch_args)
            except Exception:
                browser = await p.chromium.launch(**launch_args)
            try:
                ctx = await browser.new_context(
                    user_agent=_BROWSER_UA,
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                )
                await ctx.add_init_script(
                    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=_timeout() * 1000)
                title = await page.title()
                text = await page.inner_text("body")
            finally:
                await browser.close()
    except Exception as e:
        log.warning("web._browser_scrape failed for %s: %s", url, e)
        return None

    text = (text or "").strip()
    if not text or _is_block_shell(title, text):
        return None
    return {"url": url, "title": title or "", "markdown": text[:_max_chars()]}


# Anti-bot WAFs (Akamai/Cloudflare/DataDome) answer with a short 200 "denied"
# shell. Returning that as content would feed the agent garbage and mask the
# block, so treat a short body carrying a known block marker as a failure.
_BLOCK_MARKERS = (
    "access denied", "you don't have permission", "errors.edgesuite.net",
    "attention required", "cloudflare", "captcha", "are you a human",
    "request blocked", "bot detection", "datadome",
)


def _is_block_shell(title: str, text: str) -> bool:
    blob = f"{title}\n{text}".lower()
    if len(text) < 1500 and any(m in blob for m in _BLOCK_MARKERS):
        return True
    return False
