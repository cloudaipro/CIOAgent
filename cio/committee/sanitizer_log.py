"""sanitizer_log.py — audit trail of the LLM figures-sanitizer's decisions.

Every time note_sanitizer rewrites a note to strip stale figures, or rejects a
note outright, a row is recorded here so the dev dashboard can show exactly what
was removed and why. This is the visibility layer for the figures firewall's smart
pass (committee.note_sanitizer).

Mirrors transcript.py: lives in committee.db, plain sqlite3, never raises, DB_PATH
is module-level for test monkeypatching. Bounded to the newest MAX_ROWS rows so it
can't grow without limit over months of 24/7 use.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from cio import db as _db

log = logging.getLogger(__name__)

DB_PATH: Path = _db.DB_PATH.parent / "committee.db"
MAX_ROWS = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sanitizer_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key  TEXT,
    symbol    TEXT,
    action    TEXT,                       -- 'cleaned' | 'rejected'
    original  TEXT,                       -- note as the agent wrote it
    cleaned   TEXT,                       -- rewrite stored ('' when rejected)
    removed   TEXT,                       -- JSON array of stripped figures
    ts        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_sanlog_ts ON sanitizer_log(id DESC);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def record(role_key: str | None, symbol: str | None, action: str,
           original: str, cleaned: str = "", removed=None,
           db_path: Path | None = None) -> None:
    """Persist one sanitizer decision. Best-effort; never raises."""
    path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(path)
        conn.execute(
            "INSERT INTO sanitizer_log (role_key,symbol,action,original,cleaned,removed) "
            "VALUES (?,?,?,?,?,?)",
            (role_key, symbol, action, original, cleaned,
             json.dumps(list(removed or []))),
        )
        # bound the table to the newest MAX_ROWS rows
        conn.execute(
            "DELETE FROM sanitizer_log WHERE id <= "
            "(SELECT MAX(id) FROM sanitizer_log) - ?", (MAX_ROWS,),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("sanitizer_log.record failed: %s", exc)


def recent(limit: int = 200, db_path: Path | None = None) -> list[dict]:
    """Newest decisions first; `removed` decoded back to a list. Never raises."""
    path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(path)
        rows = conn.execute(
            "SELECT role_key,symbol,action,original,cleaned,removed,ts "
            "FROM sanitizer_log ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["removed"] = json.loads(d["removed"]) if d["removed"] else []
            except Exception:
                d["removed"] = []
            out.append(d)
        return out
    except Exception as exc:
        log.warning("sanitizer_log.recent failed: %s", exc)
        return []
