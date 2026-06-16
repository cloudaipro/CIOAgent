"""SEC EDGAR filings (8-K material events, 10-Q / 10-K reports) for one symbol.

Primary-source signal the yfinance/Firecrawl layer doesn't carry: official company
filings straight from the SEC. Free, no API key — but the SEC fair-access policy
REQUIRES a User-Agent that identifies you (name + email). So EDGAR is opt-in:

  * CIO_SEC_UA set   -> enabled (e.g. "CIOAgent Your Name you@example.com")
  * CIO_SEC_UA unset -> disabled, returns [] with no network call

That gate keeps the test suite / CI fully offline by default and enforces the
SEC's contact requirement. Every function is offline-safe and returns [] on error.

Docs: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
"""
from __future__ import annotations

import logging
import os

from . import _cache
from ._http import RateLimiter, get_json

log = logging.getLogger(__name__)

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_DEFAULT_FORMS = ("8-K", "10-Q", "10-K")

# SEC permits ~10 req/s; stay under it (~8/s) per the fair-access guidance.
_limiter = RateLimiter(0.13)

_TICKERS_TTL = 7 * 24 * 3600   # ticker -> CIK map changes rarely
_SUBMISSIONS_TTL = 3 * 3600    # filings are revised a few times a day


def _user_agent() -> str | None:
    ua = (os.getenv("CIO_SEC_UA") or "").strip()
    return ua or None


def _cik_for(symbol: str, ua: str) -> int | None:
    """Resolve a ticker to its zero-paddable CIK via the SEC ticker map."""
    sym = symbol.strip().upper()
    data = _cache.read("edgar_tickers", "all", _TICKERS_TTL)
    if data is None:
        data = get_json(_TICKERS_URL, headers={"User-Agent": ua}, limiter=_limiter)
        if not isinstance(data, dict):
            return None
        _cache.write("edgar_tickers", "all", data)
    for row in data.values():
        if isinstance(row, dict) and str(row.get("ticker", "")).upper() == sym:
            try:
                return int(row["cik_str"])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def _parse_submissions(data: dict, forms, limit: int) -> list[dict]:
    """Extract recent filings of *forms* from a submissions JSON doc.

    EDGAR stores ``filings.recent`` as parallel arrays in newest-first order, so
    we iterate in order and keep the first *limit* that match the wanted forms.
    """
    out: list[dict] = []
    try:
        recent = (((data or {}).get("filings") or {}).get("recent")) or {}
        f_form = recent.get("form") or []
        f_filed = recent.get("filingDate") or []
        f_report = recent.get("reportDate") or []
        f_acc = recent.get("accessionNumber") or []
        f_doc = recent.get("primaryDocument") or []
        f_desc = recent.get("primaryDocDescription") or []
        cik = data.get("cik")
        want = {str(f).upper() for f in forms}
        for i, form in enumerate(f_form):
            if str(form).upper() not in want:
                continue
            acc = f_acc[i] if i < len(f_acc) else ""
            doc = f_doc[i] if i < len(f_doc) else ""
            url = ""
            if cik and acc and doc:
                url = (f"https://www.sec.gov/Archives/edgar/data/"
                       f"{int(cik)}/{str(acc).replace('-', '')}/{doc}")
            out.append({
                "form": str(form),
                "filed": f_filed[i] if i < len(f_filed) else "",
                "report_date": f_report[i] if i < len(f_report) else "",
                "title": (f_desc[i] if i < len(f_desc) else "") or str(form),
                "url": url,
            })
            if len(out) >= limit:
                break
    except Exception as e:
        log.debug("edgar parse failed: %s", e)
    return out


def institutional_ownership_pct(symbol: str) -> float | None:  # noqa: ARG001
    """EDGAR 13F institutional ownership % for *symbol*.

    OPEN DECISION (OD-3, bounced to Arch):
    EDGAR 13F is a *filer-side* form — each institution files what securities
    it holds. There is no EDGAR API endpoint that returns "all 13F filers that
    own ticker X". Aggregating requires:
      (a) downloading the full quarterly 13F index (~500 MB per quarter),
      (b) filtering for rows whose CUSIP/ticker matches *symbol*, and
      (c) summing share counts across thousands of filers, then dividing by
          shares-outstanding (itself a separate data point not on EDGAR).
    The SEC EDGAR full-text search (efts.sec.gov) covers 13F-HR text but
    returns XML filings, not a pre-aggregated ownership %, so real aggregation
    would require substantial new infrastructure.

    Recommended data source: Finnhub's institutional-ownership endpoint
    ``GET /stock/institutional-ownership`` (free tier, no extra key), which
    returns ``investorHolding[].share / sharesOutstanding`` → instant %.
    Alternatively SEC EDGAR via the submissions-bulk index requires 13F index
    download + CUSIP resolution (heavy, quarterly cadence).

    Until that work is done, return None.  The coverage blend in
    ``cio.alpha.coverage.coverage_score`` treats None as "no signal" and falls
    through to the analyst-only path — safe, no fabrication.
    """
    return None


def recent_filings(symbol: str, forms=_DEFAULT_FORMS, limit: int = 5) -> list[dict]:
    """Most-recent EDGAR filings for *symbol* limited to *forms*.

    Returns a list of ``{form, filed, report_date, title, url}`` newest-first, or
    ``[]`` when EDGAR is disabled (no CIO_SEC_UA), the symbol has no US filings
    (ADRs, TW codes, ETFs typically won't match 8-K/10-Q/10-K), or on any error.
    """
    ua = _user_agent()
    if not ua:
        return []
    cik = _cik_for(symbol, ua)
    if cik is None:
        return []
    data = _cache.read("edgar_sub", str(cik), _SUBMISSIONS_TTL)
    if data is None:
        data = get_json(_SUBMISSIONS_URL.format(cik=cik),
                        headers={"User-Agent": ua}, limiter=_limiter)
        if not isinstance(data, dict):
            return []
        _cache.write("edgar_sub", str(cik), data)
    return _parse_submissions(data, forms, limit)
