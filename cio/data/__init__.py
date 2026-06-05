"""cio.data — opt-in external data sources (SEC EDGAR, Finnhub).

Both are config-gated and offline-safe: with neither CIO_SEC_UA nor
FINNHUB_API_KEY set they return empty results without any network call, so the
rest of CIOAgent — and the whole test suite — behaves exactly as before. Set the
env values (see .env.example) to switch each source on.
"""
from . import edgar, finnhub
from .edgar import recent_filings
from .finnhub import analyst_recs, company_news, earnings_calendar

__all__ = [
    "edgar",
    "finnhub",
    "recent_filings",
    "analyst_recs",
    "company_news",
    "earnings_calendar",
]
