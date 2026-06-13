"""Alpha Hunter — deterministic NASDAQ swing-selection funnel.

Market -> Sector -> Quality -> Earnings -> Momentum -> Ranking -> Watchlist.
Zero LLM cost: pure compute over yfinance OHLCV/fundamentals + finnhub earnings.
See docs/ALPHA-HUNTER-PRD.md. Public entry: ``engine.run``.
"""
from __future__ import annotations

from .engine import run, AlphaResult
from . import store, report


def run_and_save(*, publish: bool = True, db_path=None, **run_kw):
    """Run the funnel and persist it (+ publish the Alpha-<date> watchlist).
    Returns (AlphaResult, {run_id, watchlist_id, watchlist_name}). The single entry
    point the dashboard button, the /alpha command and the agent tool all call."""
    from .. import db as _db
    result = run(**run_kw)
    meta = store.save_run(result, publish=publish,
                          db_path=db_path or _db.DB_PATH)
    return result, meta


__all__ = ["run", "run_and_save", "AlphaResult", "store", "report"]
