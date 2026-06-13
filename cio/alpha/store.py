"""Persistence + watchlist publishing for Alpha Hunter (PRD §5, §6).

save_run() writes one alpha_runs row + its ranked alpha_candidates, and (unless
publish=False) refreshes the watchlist named ``Alpha-<run_date>`` with the Top-N
tickers and sets it active so Telegram ``/watchlist`` shows it immediately. The
dated list is refreshed IN PLACE on a same-day re-run (no duplicate lists).
"""
from __future__ import annotations

import json

from .. import db, watchlist
from .engine import AlphaResult, TOP_N


def watchlist_name(run_date: str) -> str:
    """The naming rule: Alpha-yyyy-mm-dd (PRD §5)."""
    return f"Alpha-{run_date}"


def publish_watchlist(result: AlphaResult, *, top_n: int = TOP_N,
                      activate: bool = True, db_path=db.DB_PATH) -> tuple[int, str]:
    """Create or refresh ``Alpha-<run_date>`` with the top-N candidate tickers.
    Returns (watchlist_id, name). Idempotent across same-day re-runs."""
    name = watchlist_name(result.run_date)
    tickers = [c["ticker"] for c in result.top(top_n)]
    existing = watchlist.find_by_name(name, db_path=db_path)
    wid = existing["id"] if existing else watchlist.create(name, db_path=db_path)
    watchlist.set_symbols(wid, tickers, db_path=db_path)
    if activate:
        watchlist.set_active(wid, db_path=db_path)
    return wid, name


def save_run(result: AlphaResult, *, publish: bool = True, top_n: int = TOP_N,
             db_path=db.DB_PATH) -> dict:
    """Persist a run (+ optionally publish its watchlist). Returns
    {run_id, watchlist_id, watchlist_name}."""
    wid, wname = (publish_watchlist(result, top_n=top_n, db_path=db_path)
                  if publish else (None, None))
    ranked = result.top(top_n)
    conn = db.connect(db_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO alpha_runs (run_date, regime, regime_detail, sectors_json, "
                "candidate_count, universe_size, watchlist_id, watchlist_name) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (result.run_date, result.regime.get("status", "UNKNOWN"),
                 result.regime.get("detail", ""), json.dumps(result.sectors),
                 len(ranked), result.universe_size, wid, wname),
            )
            run_id = cur.lastrowid
            for c in ranked:
                conn.execute(
                    "INSERT OR REPLACE INTO alpha_candidates (run_id, rank, ticker, "
                    "sector, momentum, trend, earnings, revenue_growth, fwd_eps_growth, "
                    "surprise, volume_expansion, final, quality_pass) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, c.get("rank"), c["ticker"], c.get("sector", ""),
                     c.get("momentum"), c.get("trend"), c.get("earnings"),
                     c.get("revenue_growth"), c.get("fwd_eps_growth"),
                     c.get("surprise"), c.get("volume_expansion"), c.get("final"),
                     1 if c.get("quality_pass", True) else 0),
                )
        return {"run_id": run_id, "watchlist_id": wid, "watchlist_name": wname}
    finally:
        conn.close()


# ---- reads (dashboard) -----------------------------------------------------
def list_runs(limit: int = 20, db_path=db.DB_PATH) -> list[dict]:
    """Recent runs, newest first."""
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM alpha_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def latest_run(db_path=db.DB_PATH) -> dict | None:
    """The most recent run with its sectors + ranked candidates, or None."""
    conn = db.connect(db_path)
    try:
        row = conn.execute("SELECT * FROM alpha_runs ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        out = dict(row)
        out["sectors"] = json.loads(out.get("sectors_json") or "[]")
        cands = conn.execute(
            "SELECT * FROM alpha_candidates WHERE run_id = ? ORDER BY rank",
            (out["id"],),
        ).fetchall()
        out["candidates"] = [dict(c) for c in cands]
        return out
    finally:
        conn.close()
