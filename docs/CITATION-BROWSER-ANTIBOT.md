# Technical Report: Anti-Bot URL Failures in the Citation Gate and Web Scraper

**Date:** 2026-06-23
**Branch:** `fix/citation-browser-antibot-fallback`
**Components:** `cio/harness/citation.py`, `cio/web.py`
**Trigger:** `conv_turns` records 488/489 — operator could open a marketscreener URL manually, but the bot reported `⛔ BLOCK C_DEAD_URL ... (status=403)` and, on a follow-up read, `500 Internal Server Error`.

---

## 1. Summary

A cited/requested URL on an anti-bot–protected host (marketscreener.com) failed in two distinct ways:

1. The **citation gate** marked the URL dead (`C_DEAD_URL`, status 403) and blocked the answer.
2. The **web_scrape tool** could not return the page content (Firecrawl backend `500` / `document_antibot`).

Both were false negatives: the page is live and readable in a normal browser. Root cause is **server-side anti-bot protection (Akamai Bot Manager)** rejecting non-browser clients — not a dead page and not a server bug. The fix adds a headless-browser fallback to both paths, preferring the real Chrome binary, which clears Akamai where a plain HTTP client and the bundled Chromium do not.

---

## 2. Background — two independent code paths

The two symptoms come from two unrelated subsystems that were initially conflated:

| Path | Code | Purpose | Client used |
|------|------|---------|-------------|
| Citation gate | `cio/harness/citation.py` → `http_resolver` | Verify a cited URL is **live** (status code) before allowing it as a source | stdlib `urllib` (HEAD/GET) |
| Web scrape tool | `cio/agent.py` `web_scrape` → `cio/web.py` `scrape` | Fetch a page's **content** as markdown | Firecrawl (`localhost:3002` `/v2/scrape`) |

The original report (`C_DEAD_URL`, 403) is the **citation gate**. The "read the URL content" request (`500`) is the **web scraper**. Fixing one does not fix the other; both needed work.

---

## 3. Investigation

### 3.1 Citation gate (403)

`http_resolver` issued a stdlib request with `User-Agent: CIOAgent-harness/1.0` and treated `live = 200 <= status < 400`. Liveness logic then mapped 403 → not-live → `C_DEAD_URL` (BLOCK). The host returns 403 to any client whose fingerprint (UA, TLS, no JS challenge solve) does not look like a browser. So 403 here means **"refused a bot,"** not **"page does not exist."** Conflating an anti-bot refusal with a 404 is the false positive.

### 3.2 Web scraper (500)

`web.scrape` calls Firecrawl, which itself fetches the page server-side. Firecrawl returned `500` and, via its CLI, `document_antibot`. Firecrawl is the production scrape backend and works on ordinary sites; the failure is specific to anti-bot hosts that block Firecrawl's fetcher.

### 3.3 Identifying the WAF

Initial hypothesis was DataDome. A headless-browser probe returned a 200 page whose body was:

```
Access Denied
You don't have permission to access "..." on this server.
Reference #18.8b951eb8...
https://errors.edgesuite.net/...
```

`errors.edgesuite.net` is **Akamai**, i.e. **Akamai Bot Manager**, which fingerprints TLS/JS far more aggressively than a UA check. Light stealth (spoofed UA + `navigator.webdriver=undefined`) was **not** sufficient.

### 3.4 The breakthrough

| Client | Result on marketscreener |
|--------|--------------------------|
| stdlib `urllib` | 403 |
| Firecrawl | 500 / `document_antibot` |
| Playwright **bundled chromium** + stealth | 200 "Access Denied" shell |
| Playwright **`channel="chrome"`** (real Chrome) | Full page content ✅ |

The real Chrome binary carries a fingerprint Akamai trusts; the bundled Chromium does not. This is the pivot the fix relies on.

---

## 4. Fix

### 4.1 Citation gate — `cio/harness/citation.py`

- Added `ANTI_BOT_STATUSES = {401, 403, 429, 503}` — refusals, distinct from true-dead.
- Split the stdlib probe into `_stdlib_status`; `http_resolver` now runs it first and **escalates only anti-bot statuses** to a headless browser. True-dead statuses (404/410/real 5xx) never pay the browser cost.
- `browser_resolver` runs Playwright in a **dedicated thread** (the sync API refuses a running asyncio loop, and the harness can be driven from async code). It requires a real body (>500 chars) so a 200 challenge shell does not count as live.
- Gated by `CIO_CITATION_BROWSER` **and** Playwright being importable. Any failure (no Playwright, launch error, timeout) **fails safe** to the stdlib status.

### 4.2 Web scraper — `cio/web.py`

- When Firecrawl returns no content **and** the browser fallback is enabled, retry via `_browser_scrape` (Playwright async API — `scrape` is already async, so no thread needed) and return the rendered body text.
- `_is_block_shell` rejects short "Access Denied / Cloudflare / captcha / DataDome" bodies so a denial page is **never** returned to the agent as content (this also fixed a bug where the 254-char Akamai shell was being returned as markdown).
- Enabled by `CIO_WEB_BROWSER`, falling back to `CIO_CITATION_BROWSER`, so a single flag turns on headless rescue for both paths.

### 4.3 Common

Both paths launch with `channel="chrome"` and **fall back to bundled chromium** if real Chrome is absent.

---

## 5. Configuration

```bash
pip install playwright
playwright install chromium      # bundled fallback; real Chrome used when present
export CIO_CITATION_BROWSER=1    # enables both paths (CIO_WEB_BROWSER overrides web only)
```

Documented in `.env.example`. The feature is **off by default**; with the flag unset or Playwright absent, behavior is unchanged from before this fix.

---

## 6. Verification

- End-to-end `web.scrape(<marketscreener META consensus>)` returned real content: *Mean consensus BUY*, average/high/low target prices, and analyst rating lines (Jefferies/UBS/New Street $730–$825).
- `_is_block_shell` confirmed to drop the Akamai "Access Denied" shell (returns error, not content).
- Test suite: `tests/test_harness_x.py` + `tests/test_harness.py` → **105 passed** (+4 new escalation tests covering: disabled-keeps-stdlib, anti-bot-escalates, 404-never-escalates, browser-failure-falls-back).

---

## 7. Limitations & follow-ups

- **Requires a real Chrome binary on the host** for Akamai-class sites. On a headless server with only bundled Chromium, anti-bot hosts remain blocked (ordinary sites still work). Provision `google-chrome` where this matters.
- Light inline stealth only. If a host beats it, upgrade to the `playwright-stealth` package, or use a **persistent context with the operator's real Chrome profile** (carries trusted cookies/fingerprint).
- Per-protected-URL cost ~2–5 s (browser launch + nav). Acceptable because escalation fires only on anti-bot statuses / empty Firecrawl results, not on the common path.
- Alternative for marketscreener specifically: bypass the host entirely via the `analyst_ratings` tool (Yahoo/Finnhub) for consensus data.
