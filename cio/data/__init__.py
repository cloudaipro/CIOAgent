"""cio.data — opt-in external data sources (SEC EDGAR, Finnhub, ClinicalTrials,
IBKR Client Portal Gateway).

All are config-gated and offline-safe: with no API keys / env vars set they
return empty results without any network call, so the rest of CIOAgent — and the
whole test suite — behaves exactly as before. Set the env values (see
.env.example) to switch each source on.
"""
from . import edgar, finnhub, clinicaltrials, ibkr
from .edgar import recent_filings
from .finnhub import (
    analyst_recs,
    company_news,
    earnings_calendar,
    company_profile,
    insider_transactions,
    insider_net,
)
from .clinicaltrials import search_trials

__all__ = [
    "edgar",
    "finnhub",
    "clinicaltrials",
    "ibkr",
    "recent_filings",
    "analyst_recs",
    "company_news",
    "earnings_calendar",
    "company_profile",
    "insider_transactions",
    "insider_net",
    "search_trials",
]
