"""SQLite layer for the CIO agent.

Single local DB file. Source of truth for the stock-portfolio domain is the
`transactions` table; positions and P&L are *derived* from it so cost basis is
always correct. Prices are entered manually (no live feed yet) into `prices`;
the latest row per symbol is used for valuation.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data"
DB_PATH = _DATA / "cio.db"
if not DB_PATH.exists() and (_DATA / "cfo.db").exists():
    DB_PATH = _DATA / "cfo.db"        # keep using the owner's existing DB in place

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
    created_at TEXT   NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS prices (
    symbol     TEXT NOT NULL,
    price_date TEXT NOT NULL,                -- ISO date
    close      REAL NOT NULL,
    PRIMARY KEY (symbol, price_date)
);

CREATE INDEX IF NOT EXISTS idx_txn_symbol ON transactions(symbol);

-- Watchlists: the operator keeps several named lists of symbols to track, but
-- exactly one is "active" (drives the Telegram /watchlist price snapshot). The
-- single-active invariant is enforced in watchlist.set_active() (clears the flag
-- on every other row in the same transaction), not by a DB constraint, since
-- SQLite can't express "at most one row with is_active=1". Every list carries at
-- least one NASDAQ index (^IXIC), seeded on create — see cio/watchlist.py.
CREATE TABLE IF NOT EXISTS watchlists (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    is_active  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS watchlist_items (
    watchlist_id INTEGER NOT NULL REFERENCES watchlists(id) ON DELETE CASCADE,
    symbol       TEXT    NOT NULL,                 -- stored upper-cased
    position     INTEGER NOT NULL DEFAULT 0,       -- display order (drag-to-rearrange)
    added_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (watchlist_id, symbol)             -- re-adding a symbol is a no-op
);

-- Idempotency ledger for CSV imports. A redelivered Telegram upload (e.g. the
-- process died mid-turn before acking, so Telegram replays the message) has
-- identical bytes -> identical hash -> skipped, preventing duplicate rows from
-- corrupting cost basis. The hash row is written in the SAME transaction as the
-- inserts, so a crash mid-commit rolls back both and a later replay re-imports.
CREATE TABLE IF NOT EXISTS imported_files (
    file_hash   TEXT PRIMARY KEY,        -- sha256 of the raw CSV bytes
    source      TEXT,                    -- original filename/path (informational)
    rows        INTEGER NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Durable agent memory. Survives process restarts (24/7 runtime). Used for
-- agent-chosen facts/preferences, NOT financial truth (that stays in
-- transactions/prices). Deterministic + auditable, unlike model-managed files.
CREATE TABLE IF NOT EXISTS agent_memory (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Internal key/value for runtime bookkeeping (e.g. last digest date), kept
-- separate from agent_memory so it never leaks into recall().
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Daily token-budget counters per backend service (UTC day).
-- Used by the CIO fallback chain to enforce per-service daily limits.
-- Natural reset: a new day key is a new row; old rows are inert.
CREATE TABLE IF NOT EXISTS token_usage (
    service TEXT NOT NULL,
    day     TEXT NOT NULL,
    tokens  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (service, day)
);

-- Known Telegram chats. `subscribed` controls the daily digest; `session_id`
-- lets a restarted bot resume the same SDK conversation thread per chat.
CREATE TABLE IF NOT EXISTS chats (
    chat_id    INTEGER PRIMARY KEY,
    subscribed INTEGER NOT NULL DEFAULT 0,
    session_id TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ===== MemCore: tiered memory / context (>= Hermes & OpenClaw) ==============
-- Qualitative agent memory only. Financial FIGURES never live here — they stay
-- in transactions/prices and are recomputed (the "figures firewall").

-- HOT (injected at session start) + WARM (recall on demand) notes, namespaced
-- by scope ('global' or 'chat:<id>'), scored by importance/hits, with provenance.
CREATE TABLE IF NOT EXISTS mem_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT    NOT NULL DEFAULT 'global',
    tier       TEXT    NOT NULL DEFAULT 'warm' CHECK (tier IN ('hot','warm')),
    key        TEXT,                                   -- optional upsert key
    value      TEXT    NOT NULL,
    importance REAL    NOT NULL DEFAULT 1.0,
    hits       INTEGER NOT NULL DEFAULT 0,
    source     TEXT    NOT NULL DEFAULT 'agent',       -- agent|user|auto|legacy
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    expires_at TEXT,
    UNIQUE (scope, key)                                -- NULL keys never collide
);
CREATE INDEX IF NOT EXISTS idx_notes_scope_tier ON mem_notes(scope, tier);

-- Per-scope user profile (Hermes USER.md equivalent), injected at session start.
CREATE TABLE IF NOT EXISTS user_profile (
    scope      TEXT PRIMARY KEY,                       -- 'global' or 'chat:<id>'
    role       TEXT, stack TEXT, prefs TEXT, goals TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- Rolling-session checkpoints: a qualitative summary written BEFORE a fork so a
-- fresh thread can be seeded without the full transcript.
CREATE TABLE IF NOT EXISTS session_digests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER,
    session_id  TEXT,
    summary     TEXT    NOT NULL,
    turn_count  INTEGER NOT NULL DEFAULT 0,
    token_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_digests_chat ON session_digests(chat_id, id);

-- COLD store: every turn, kept for hybrid search (so nothing is truly lost even
-- after compaction/fork).
CREATE TABLE IF NOT EXISTS conv_turns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER,
    session_id TEXT,
    role       TEXT NOT NULL,
    content    TEXT NOT NULL,
    ts         TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_turns_chat ON conv_turns(chat_id, id);

-- Learning loop: named procedures (steps reference tools, never cached numbers).
CREATE TABLE IF NOT EXISTS playbooks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    scope      TEXT    NOT NULL DEFAULT 'global',
    name       TEXT    NOT NULL,
    steps      TEXT    NOT NULL,
    hits       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (scope, name)
);

-- High-impact economic events to warn the operator about before they hit.
-- Dates are populated by the agent (monthly_red_events playbook, via web lookup)
-- plus deterministic NFP seeding; the scheduler alerts ahead of each one once.
CREATE TABLE IF NOT EXISTS econ_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date TEXT    NOT NULL,                     -- ISO YYYY-MM-DD
    name       TEXT    NOT NULL,
    impact     TEXT    NOT NULL DEFAULT 'high',      -- high | medium | low
    time_et    TEXT    NOT NULL DEFAULT '',          -- e.g. '08:30 ET'
    source     TEXT    NOT NULL DEFAULT '',
    alerted    INTEGER NOT NULL DEFAULT 0,           -- 1 once a heads-up was sent
    created_at TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE (event_date, name)
);

-- FTS5 keyword layer (external-content, kept in sync by triggers).
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(value, content='mem_notes', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS mem_notes_ai AFTER INSERT ON mem_notes BEGIN
    INSERT INTO notes_fts(rowid, value) VALUES (new.id, new.value);
END;
CREATE TRIGGER IF NOT EXISTS mem_notes_ad AFTER DELETE ON mem_notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, value) VALUES ('delete', old.id, old.value);
END;
CREATE TRIGGER IF NOT EXISTS mem_notes_au AFTER UPDATE ON mem_notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, value) VALUES ('delete', old.id, old.value);
    INSERT INTO notes_fts(rowid, value) VALUES (new.id, new.value);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(content, content='conv_turns', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS conv_turns_ai AFTER INSERT ON conv_turns BEGIN
    INSERT INTO turns_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS conv_turns_ad AFTER DELETE ON conv_turns BEGIN
    INSERT INTO turns_fts(turns_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;

-- Session digests are searchable too (long-term recall over past-day/period
-- summaries), kept in sync by triggers like turns. Digests are insert/delete only.
CREATE VIRTUAL TABLE IF NOT EXISTS digests_fts USING fts5(summary, content='session_digests', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS session_digests_ai AFTER INSERT ON session_digests BEGIN
    INSERT INTO digests_fts(rowid, summary) VALUES (new.id, new.summary);
END;
CREATE TRIGGER IF NOT EXISTS session_digests_ad AFTER DELETE ON session_digests BEGIN
    INSERT INTO digests_fts(digests_fts, rowid, summary) VALUES ('delete', old.id, old.summary);
END;

-- sqlite-vec semantic layer (vec0 virtual tables; the extension is loaded on
-- every connect, so these CREATE statements succeed). embedding dim = 768
-- (fastembed BAAI/bge-base-en-v1.5); see EMBED_DIM below.
CREATE VIRTUAL TABLE IF NOT EXISTS mem_vec    USING vec0(note_id   INTEGER PRIMARY KEY, embedding float[768]);
CREATE VIRTUAL TABLE IF NOT EXISTS turn_vec   USING vec0(turn_id   INTEGER PRIMARY KEY, embedding float[768]);
CREATE VIRTUAL TABLE IF NOT EXISTS digest_vec USING vec0(digest_id INTEGER PRIMARY KEY, embedding float[768]);
"""

# Embedding dimension for the fastembed model; sqlite-vec vec0 tables above must
# match. Kept here so recall.py and the schema agree on one source of truth.
# 768 = BAAI/bge-base-en-v1.5 (full precision, higher recall fidelity).
EMBED_DIM = 768


def _load_vec(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension. Required (no fallback) — vec0 tables in the
    schema and all semantic search depend on it; a load failure is a setup bug."""
    import sqlite_vec
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _migrate(conn: sqlite3.Connection) -> None:
    """One-time data migration, guarded by a meta flag so deletes don't resurrect.
    Moves legacy agent_memory rows into mem_notes (warm, global)."""
    if conn.execute("SELECT 1 FROM meta WHERE key='schema_migrated_v2'").fetchone():
        return
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO mem_notes (scope, tier, key, value, source, "
            "created_at, updated_at) "
            "SELECT 'global','warm', key, value, 'legacy', updated_at, updated_at "
            "FROM agent_memory"
        )
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_migrated_v2','1')")


def _migrate_watchlist_position(conn: sqlite3.Connection) -> None:
    """Add watchlist_items.position to a pre-existing table (the column was added
    after the watchlist feature first shipped). CREATE TABLE IF NOT EXISTS won't
    alter an existing table, so back-fill it here. Idempotent: skipped once the
    column is present. New rows default to 0; watchlist._symbols() falls back to
    symbol order, so an un-reordered legacy list still renders sensibly."""
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(watchlist_items)")]
    if cols and "position" not in cols:
        with conn:
            conn.execute(
                "ALTER TABLE watchlist_items ADD COLUMN position INTEGER NOT NULL DEFAULT 0"
            )


def _drop_stale_vec(conn: sqlite3.Connection) -> bool:
    """If the stored embedding dim differs from EMBED_DIM, drop the vec0 tables so
    the schema recreates them at the new dim. Returns True if dropped (needs
    reindex). No-op on a fresh DB (vec tables don't exist yet)."""
    has_vec = conn.execute("SELECT 1 FROM sqlite_master WHERE name='mem_vec'").fetchone()
    if not has_vec:
        return False
    has_meta = conn.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
    recorded = None
    if has_meta:
        r = conn.execute("SELECT value FROM meta WHERE key='embed_dim'").fetchone()
        recorded = int(r["value"]) if r else None
    if recorded != EMBED_DIM:
        conn.execute("DROP TABLE IF EXISTS mem_vec")
        conn.execute("DROP TABLE IF EXISTS turn_vec")
        conn.execute("DROP TABLE IF EXISTS digest_vec")
        return True
    return False


# Paths whose schema/migrations already ran in this process. Re-running the full
# SCHEMA script plus a meta write on EVERY connect is a write transaction per call —
# under the parallel committee that serializes writers for no benefit. Init once per
# process per path; the file-exists check covers a DB deleted mid-process (tests).
_INITIALIZED: set[str] = set()


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a connection: load sqlite-vec; on first use of a path in this process,
    ensure schema and run one-time migrations."""
    path = Path(db_path)
    key = str(path.resolve())
    if key in _INITIALIZED and not path.exists():
        _INITIALIZED.discard(key)          # file was removed; re-initialize
    if key in _INITIALIZED:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        _load_vec(conn)
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _load_vec(conn)            # before executescript: schema creates vec0 tables
    dropped = _drop_stale_vec(conn)
    conn.executescript(SCHEMA)
    _migrate(conn)
    _migrate_watchlist_position(conn)
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('embed_dim',?)",
                     (str(EMBED_DIM),))
        if dropped:   # vectors were wiped on a dim change -> need re-embedding
            conn.execute("INSERT OR REPLACE INTO meta (key,value) VALUES ('vec_reindex_needed','1')")
    _INITIALIZED.add(key)
    return conn


def init(db_path: Path | str = DB_PATH) -> None:
    """Create the DB and schema if missing."""
    connect(db_path).close()
