"""Alpha Hunter — deterministic NASDAQ swing-selection funnel.

Market -> Sector -> Quality -> Earnings -> Momentum -> Ranking -> Watchlist.
Zero LLM cost: pure compute over yfinance OHLCV/fundamentals + finnhub earnings.
See docs/ALPHA-HUNTER-PRD.md. Public entry: ``engine.run``.
"""
from __future__ import annotations

from datetime import datetime

from .engine import run, AlphaResult, ScanCancelled
from . import store, report, regime


def run_and_save(*, publish: bool = True, threshold: float | None = None,
                 db_path=None, **run_kw):
    """Run the funnel and persist it (+ publish the Alpha-<date> watchlist).
    Returns (AlphaResult, {run_id, watchlist_id, watchlist_name, threshold,
    selected_count}). *threshold* defaults to the configured value. The single entry
    point the dashboard button, the /alpha command and the agent tool all call."""
    from .. import db as _db
    result = run(**run_kw)
    cancel_check = run_kw.get("cancel_check")
    if cancel_check is not None and cancel_check():
        raise ScanCancelled("Alpha Hunter scan cancelled")
    meta = store.save_run(result, publish=publish, threshold=threshold,
                          db_path=db_path or _db.DB_PATH)
    return result, meta


def reuse_today_or_run(*, publish: bool = True, threshold: float | None = None,
                       db_path=None, **run_kw):
    """Reuse today's newest completed published run, otherwise run the funnel.

    This is the latency-bounded entry point for conversational tool calls.  The
    explicit ``run_and_save`` entry point remains fresh-by-definition for the
    dashboard and ``/alpha`` command.
    """
    from .. import db as _db
    from .. import watchlist

    path = db_path or _db.DB_PATH
    cancel_check = run_kw.get("cancel_check")
    if cancel_check is not None and cancel_check():
        raise ScanCancelled("Alpha Hunter scan cancelled")
    today = datetime.now().strftime("%Y-%m-%d")
    cached = store.latest_run_for_date(today, db_path=path)
    # A published caller must not reuse a diagnostic publish=False run: doing so
    # would claim a watchlist exists when none was created.
    if cached is not None and (not publish or cached.get("watchlist_id")):
        if cancel_check is not None and cancel_check():
            raise ScanCancelled("Alpha Hunter scan cancelled")
        result = AlphaResult(
            run_date=cached["run_date"],
            regime={"status": cached.get("regime", "UNKNOWN"),
                    "detail": cached.get("regime_detail", "")},
            sectors=cached.get("sectors") or [],
            candidates=cached.get("candidates") or [],
            universe_size=int(cached.get("universe_size") or 0),
        )
        if publish:
            # Preserve run_and_save's contract that the published list is active.
            watchlist.set_active(int(cached["watchlist_id"]), db_path=path)
        meta = {
            "run_id": cached["id"],
            "watchlist_id": cached.get("watchlist_id"),
            "watchlist_name": cached.get("watchlist_name"),
            "selected_count": len(result.candidates),
            "reused": True,
        }
        return result, meta
    return run_and_save(publish=publish, threshold=threshold, db_path=path, **run_kw)


__all__ = ["run", "run_and_save", "reuse_today_or_run", "AlphaResult",
           "ScanCancelled", "store", "report", "regime"]
