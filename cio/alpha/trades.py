"""Trade ledger (swing upgrade #3a, 2026-06).

The expectancy KPI (upgrade #3b) needs realized trade outcomes, and the codebase
had nowhere to record them — watchlists and committee decisions are forward-looking
only. This module is that missing ledger: one row per position, opened and later
closed, with the four-layer scores and regime captured AT ENTRY so we can later ask
"do high-catalyst-layer trades actually pay?".

Self-initializing: ``CREATE TABLE IF NOT EXISTS`` on first use, so it needs no
change to the central db.init schema. Pure SQLite over the shared connection
helper; never raises on read paths. Money fields are plain floats — this is a
decision-quality ledger, not the accounting book of record (that is the IBKR sync).
"""
from __future__ import annotations

import json

from .. import db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open',   -- open | closed
    entry_date      TEXT NOT NULL,
    exit_date       TEXT,
    entry_px        REAL NOT NULL,
    exit_px         REAL,
    stop_px         REAL,                            -- planned risk -> R unit
    qty             REAL,
    pct             REAL,                            -- realized % return (signed)
    r_multiple      REAL,                            -- realized return in R units
    style           TEXT,                            -- 肥 | 勤 | neutral
    regime_at_entry TEXT,
    layer_scores    TEXT,                            -- JSON {catalyst,behavior,...}
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
"""


def _conn(db_path=db.DB_PATH):
    conn = db.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def _r_multiple(entry_px, exit_px, stop_px):
    """Realized return in R units (risk = entry-stop). None when no usable stop."""
    try:
        entry, exit_ = float(entry_px), float(exit_px)
        risk = entry - float(stop_px)
        if risk <= 0:
            return None
        return round((exit_ - entry) / risk, 3)
    except (TypeError, ValueError):
        return None


def open_trade(ticker, entry_date, entry_px, *, stop_px=None, qty=None,
               style=None, regime_at_entry=None, layer_scores=None, note="",
               db_path=db.DB_PATH) -> int:
    """Record a newly opened position. Returns the trade id."""
    conn = _conn(db_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO trades (ticker, status, entry_date, entry_px, stop_px, "
                "qty, style, regime_at_entry, layer_scores, note) "
                "VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(ticker).upper(), entry_date, float(entry_px),
                 None if stop_px is None else float(stop_px),
                 None if qty is None else float(qty), style, regime_at_entry,
                 json.dumps(layer_scores) if layer_scores is not None else None, note),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def close_trade(trade_id, exit_date, exit_px, *, note=None, db_path=db.DB_PATH) -> dict | None:
    """Close an open trade; compute pct + r_multiple. Returns the closed row or None."""
    conn = _conn(db_path)
    try:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (int(trade_id),)).fetchone()
        if row is None:
            return None
        entry = float(row["entry_px"])
        exit_ = float(exit_px)
        # `if entry` guards div-by-zero: a 0.0 entry price is meaningless (missing
        # price), so pct stays None rather than dividing by zero.
        pct = round((exit_ - entry) / entry * 100.0, 3) if entry else None
        rmult = _r_multiple(entry, exit_, row["stop_px"])
        with conn:
            conn.execute(
                "UPDATE trades SET status='closed', exit_date=?, exit_px=?, pct=?, "
                "r_multiple=?, note=COALESCE(?, note) WHERE id=?",
                (exit_date, exit_, pct, rmult, note, int(trade_id)),
            )
        return dict(conn.execute("SELECT * FROM trades WHERE id=?", (int(trade_id),)).fetchone())
    finally:
        conn.close()


def record_closed(ticker, entry_date, exit_date, entry_px, exit_px, *, stop_px=None,
                  qty=None, style=None, regime_at_entry=None, layer_scores=None,
                  note="", db_path=db.DB_PATH) -> int:
    """Insert an already-completed trade in one call (e.g. backfill from IBKR fills)."""
    tid = open_trade(ticker, entry_date, entry_px, stop_px=stop_px, qty=qty, style=style,
                     regime_at_entry=regime_at_entry, layer_scores=layer_scores, note=note,
                     db_path=db_path)
    close_trade(tid, exit_date, exit_px, db_path=db_path)
    return tid


def record_orphan_sell(ticker, exit_date, exit_px, *, qty=None, style=None,
                       regime_at_entry=None, note="", db_path=db.DB_PATH) -> int:
    """Record a SELL fill with no matching open trade (first backfill, or an entry
    that predates the ledger). Stored as status='orphan' with NULL pct/r_multiple so
    it is kept for reconciliation but EXCLUDED from the expectancy KPI — list_closed
    only returns status='closed', so an orphan never dilutes win/loss rates. entry_px
    mirrors the sell price solely to satisfy the NOT NULL schema; it is NOT a real
    cost basis, which is why pct/r are left NULL rather than computed as a fake 0%."""
    conn = _conn(db_path)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO trades (ticker, status, entry_date, exit_date, entry_px, "
                "exit_px, qty, style, regime_at_entry, note) "
                "VALUES (?, 'orphan', ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(ticker).upper(), exit_date, exit_date, float(exit_px),
                 float(exit_px), None if qty is None else float(qty), style,
                 regime_at_entry, note),
            )
            return int(cur.lastrowid)
    finally:
        conn.close()


def reconcile_orphan(orphan_id: int, entry_px: float, entry_date: str,
                     db_path=db.DB_PATH) -> dict | None:
    """Convert an 'orphan' sell row into a 'closed' trade once cost basis is known.

    Use when the operator can supply the original entry price (e.g. from IBKR
    avg_cost snapshot, a brokerage statement, or manual lookup).  Computes real
    pct and r_multiple (r_multiple requires stop_px — stays None if absent).
    Returns the updated row, or None if the orphan_id does not exist or is not
    in orphan status.  Never raises.
    """
    try:
        conn = _conn(db_path)
        try:
            row = conn.execute(
                "SELECT * FROM trades WHERE id=? AND status='orphan'",
                (int(orphan_id),),
            ).fetchone()
            if row is None:
                return None
            entry = float(entry_px)
            exit_ = float(row["exit_px"])
            pct = round((exit_ - entry) / entry * 100.0, 3) if entry else None
            rmult = _r_multiple(entry, exit_, row["stop_px"])
            with conn:
                conn.execute(
                    "UPDATE trades SET status='closed', entry_px=?, entry_date=?, "
                    "pct=?, r_multiple=?, "
                    "note=COALESCE(note||' reconciled', 'reconciled') WHERE id=?",
                    (entry, entry_date, pct, rmult, int(orphan_id)),
                )
            return dict(conn.execute(
                "SELECT * FROM trades WHERE id=?", (int(orphan_id),)).fetchone())
        finally:
            conn.close()
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("reconcile_orphan failed: %s", e)
        return None


def list_orphans(db_path=db.DB_PATH) -> list[dict]:
    """Orphan sells awaiting reconciliation, newest first. Never raises."""
    try:
        conn = _conn(db_path)
    except Exception:
        return []
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='orphan' ORDER BY id DESC").fetchall()
        return [_hydrate(dict(r)) for r in rows]
    finally:
        conn.close()


def list_closed(ticker=None, db_path=db.DB_PATH) -> list[dict]:
    """All closed trades (optionally one ticker), newest first. Never raises."""
    try:
        conn = _conn(db_path)
    except Exception:
        return []
    try:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status='closed' AND ticker=? ORDER BY id DESC",
                (str(ticker).upper(),)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status='closed' ORDER BY id DESC").fetchall()
        return [_hydrate(dict(r)) for r in rows]
    finally:
        conn.close()


def list_open(db_path=db.DB_PATH) -> list[dict]:
    """All open positions, newest first. Never raises."""
    try:
        conn = _conn(db_path)
    except Exception:
        return []
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='open' ORDER BY id DESC").fetchall()
        return [_hydrate(dict(r)) for r in rows]
    finally:
        conn.close()


def _hydrate(row: dict) -> dict:
    if row.get("layer_scores"):
        try:
            row["layer_scores"] = json.loads(row["layer_scores"])
        except (TypeError, ValueError):
            pass
    return row
