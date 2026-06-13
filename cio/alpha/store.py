"""Persistence + watchlist publishing for Alpha Hunter (PRD §5, §6).

save_run() writes one alpha_runs row + its ranked alpha_candidates, and (unless
publish=False) refreshes the watchlist named ``Alpha-<run_date>`` with the Top-N
tickers and sets it active so Telegram ``/watchlist`` shows it immediately. The
dated list is refreshed IN PLACE on a same-day re-run (no duplicate lists).
"""
from __future__ import annotations

import json

from .. import db, watchlist
from .engine import AlphaResult

# Candidate-selection threshold: names with Final Score >= this are published to the
# watchlist (replaces the old fixed Top-20). Operator-configurable in the dashboard;
# persisted in the meta table. Default 80.
DEFAULT_THRESHOLD = 80.0
_THRESHOLD_KEY = "alpha_threshold"


def watchlist_name(run_date: str) -> str:
    """The naming rule: Alpha-yyyy-mm-dd (PRD §5)."""
    return f"Alpha-{run_date}"


def get_threshold(db_path=db.DB_PATH) -> float:
    """The configured Final-Score selection threshold (default 80)."""
    conn = db.connect(db_path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (_THRESHOLD_KEY,)).fetchone()
        if row is None:
            return DEFAULT_THRESHOLD
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            return DEFAULT_THRESHOLD
    finally:
        conn.close()


def set_threshold(value, db_path=db.DB_PATH) -> float:
    """Persist the selection threshold (clamped to 0..100). Returns the stored value."""
    try:
        v = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"threshold must be a number, got {value!r}") from e
    v = max(0.0, min(100.0, v))
    conn = db.connect(db_path)
    try:
        with conn:
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
                         (_THRESHOLD_KEY, str(v)))
        return v
    finally:
        conn.close()


def publish_watchlist(result: AlphaResult, *, threshold: float,
                      activate: bool = True, db_path=db.DB_PATH) -> tuple[int, str]:
    """Create or refresh ``Alpha-<run_date>`` with every candidate scoring at/above
    *threshold*. Returns (watchlist_id, name). Idempotent across same-day re-runs."""
    name = watchlist_name(result.run_date)
    tickers = [c["ticker"] for c in result.select(threshold)]
    existing = watchlist.find_by_name(name, db_path=db_path)
    wid = existing["id"] if existing else watchlist.create(name, db_path=db_path)
    watchlist.set_symbols(wid, tickers, db_path=db_path)
    if activate:
        watchlist.set_active(wid, db_path=db_path)
    return wid, name


def save_run(result: AlphaResult, *, publish: bool = True, threshold: float | None = None,
             db_path=db.DB_PATH) -> dict:
    """Persist a run (+ optionally publish its watchlist). Candidates with Final
    Score >= *threshold* (default = the configured value) are selected. Returns
    {run_id, watchlist_id, watchlist_name, threshold, selected_count}."""
    if threshold is None:
        threshold = get_threshold(db_path=db_path)
    wid, wname = (publish_watchlist(result, threshold=threshold, db_path=db_path)
                  if publish else (None, None))
    ranked = result.select(threshold)
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
        return {"run_id": run_id, "watchlist_id": wid, "watchlist_name": wname,
                "threshold": threshold, "selected_count": len(ranked)}
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
