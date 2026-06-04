"""
cio.watchlist_monitor — Watchlist Monitoring Agent (WMA).

First layer of the investment architecture (PRD §11): scans the watchlist before
market open, produces a one-security assessment for each name, and renders a
consolidated morning briefing. Cheap (one model call per security) and degrades
gracefully — flags thesis-breaking events for full committee escalation.

Public API:
  monitor_symbol(symbol)        -> dict           (async)
  monitor_watchlist(symbols?)   -> list[dict]     (async)
  global_macro_snapshot()       -> dict           (async, one call per run)
  build_briefing(assessments)   -> str            (sync markdown)
  briefing_summary(assessments) -> str            (sync short text)
"""
from .agent import monitor_symbol, monitor_watchlist, global_macro_snapshot, as_of_now
from .report import build_briefing, briefing_summary

__all__ = [
    "monitor_symbol",
    "monitor_watchlist",
    "global_macro_snapshot",
    "build_briefing",
    "briefing_summary",
    "as_of_now",
]
