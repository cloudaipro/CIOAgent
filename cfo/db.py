"""SQLite layer for the CFO agent.

Single local DB file. Source of truth for the stock-portfolio domain is the
`transactions` table; positions and P&L are *derived* from it so cost basis is
always correct. Prices are entered manually (no live feed yet) into `prices`;
the latest row per symbol is used for valuation.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "cfo.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_date  TEXT    NOT NULL,              -- ISO date YYYY-MM-DD
    symbol    TEXT    NOT NULL,
    action    TEXT    NOT NULL CHECK (action IN ('BUY','SELL','DIV')),
    quantity  REAL    NOT NULL DEFAULT 0,    -- shares; 0 for DIV
    price     REAL    NOT NULL DEFAULT 0,    -- per-share price; for DIV = cash amount
    fees      REAL    NOT NULL DEFAULT 0,
    currency  TEXT    NOT NULL DEFAULT 'USD',
    notes     TEXT,
    created_at TEXT   NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS prices (
    symbol     TEXT NOT NULL,
    price_date TEXT NOT NULL,                -- ISO date
    close      REAL NOT NULL,
    PRIMARY KEY (symbol, price_date)
);

CREATE INDEX IF NOT EXISTS idx_txn_symbol ON transactions(symbol);

-- Idempotency ledger for CSV imports. A redelivered Telegram upload (e.g. the
-- process died mid-turn before acking, so Telegram replays the message) has
-- identical bytes -> identical hash -> skipped, preventing duplicate rows from
-- corrupting cost basis. The hash row is written in the SAME transaction as the
-- inserts, so a crash mid-commit rolls back both and a later replay re-imports.
CREATE TABLE IF NOT EXISTS imported_files (
    file_hash   TEXT PRIMARY KEY,        -- sha256 of the raw CSV bytes
    source      TEXT,                    -- original filename/path (informational)
    rows        INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Durable agent memory. Survives process restarts (24/7 runtime). Used for
-- agent-chosen facts/preferences, NOT financial truth (that stays in
-- transactions/prices). Deterministic + auditable, unlike model-managed files.
CREATE TABLE IF NOT EXISTS agent_memory (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Internal key/value for runtime bookkeeping (e.g. last digest date), kept
-- separate from agent_memory so it never leaks into recall().
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Known Telegram chats. `subscribed` controls the daily digest; `session_id`
-- lets a restarted bot resume the same SDK conversation thread per chat.
CREATE TABLE IF NOT EXISTS chats (
    chat_id    INTEGER PRIMARY KEY,
    subscribed INTEGER NOT NULL DEFAULT 0,
    session_id TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a connection, ensuring the schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def init(db_path: Path | str = DB_PATH) -> None:
    """Create the DB and schema if missing."""
    connect(db_path).close()
