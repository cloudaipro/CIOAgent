"""
transcript.py — Developer capture of every committee LLM call.

Records the exact content SENT to a model (system + user prompt) and the
content RETURNED, per role, grouped by committee run, so the developer dashboard
can verify the committee is behaving correctly (PRD dev-dashboard §2a/§2b).

Lives in committee.db alongside ``token_usage`` (usage.py). Every function is
safe to call at any time and never raises. ``DB_PATH`` is module-level so tests
can monkeypatch it:
``monkeypatch.setattr("cio.committee.transcript.DB_PATH", tmp_path / "t.db")``.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from cio import db as _db
from cio import devcapture

log = logging.getLogger(__name__)

# Committee-side DB, alongside cio.db (mirrors usage.py).
DB_PATH: Path = _db.DB_PATH.parent / "committee.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS committee_transcript (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT,                       -- one committee run (groups calls)
    symbol        TEXT,
    role_key      TEXT,                       -- specialist/moderator/cio/debate role
    service       TEXT,                       -- openai | claude | nim
    model         TEXT,
    system_prompt TEXT,                       -- content SENT (system)
    user_prompt   TEXT,                       -- content SENT (user)
    response      TEXT,                       -- content RETURNED
    tokens        INTEGER NOT NULL DEFAULT 0,
    source        TEXT,                       -- what triggered the run: command | chat | cli
    ts            TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_transcript_run ON committee_transcript(run_id, id);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a plain sqlite3 connection and ensure the table (and any later columns)
    exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Migrate DBs created before the `source` column existed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(committee_transcript)")}
    if "source" not in cols:
        conn.execute("ALTER TABLE committee_transcript ADD COLUMN source TEXT")
        conn.commit()
    return conn


def record(
    role_key: str | None,
    service: str | None,
    model: str | None,
    system_prompt: str,
    user_prompt: str,
    response: str,
    tokens: int = 0,
    run_id: str | None = None,
    symbol: str | None = None,
    source: str | None = None,
    db_path: Path | None = None,
) -> None:
    """Persist one LLM call (sent + returned). Prunes per capture level. Never raises."""
    path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(path)
        conn.execute(
            "INSERT INTO committee_transcript "
            "(run_id,symbol,role_key,service,model,system_prompt,user_prompt,response,tokens,source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, symbol, role_key, service, model, system_prompt, user_prompt,
             response, int(tokens or 0), source),
        )
        conn.commit()
        if devcapture.prune_enabled():
            _prune(conn, devcapture.keep_runs())
        conn.close()
    except Exception as exc:
        log.warning("transcript.record failed: %s", exc)


def _prune(conn: sqlite3.Connection, keep_runs: int) -> None:
    """Keep only the newest *keep_runs* runs (by latest row id); delete older
    runs' rows. NULL-run_id rows are never pruned."""
    conn.execute(
        "DELETE FROM committee_transcript "
        "WHERE run_id IS NOT NULL AND run_id NOT IN ("
        "  SELECT run_id FROM ("
        "    SELECT run_id, MAX(id) AS m FROM committee_transcript "
        "    WHERE run_id IS NOT NULL GROUP BY run_id ORDER BY m DESC LIMIT ?"
        "  )"
        ")",
        (keep_runs,),
    )
    conn.commit()


def clear_all(db_path: Path | None = None) -> int:
    """Delete every captured committee run (all transcript rows). Returns the number
    of call-rows removed. Best-effort; never raises. Irreversible."""
    path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(path)
        n = conn.execute("SELECT COUNT(*) c FROM committee_transcript").fetchone()["c"]
        conn.execute("DELETE FROM committee_transcript")
        conn.commit()
        conn.close()
        return n
    except Exception as exc:
        log.warning("transcript.clear_all failed: %s", exc)
        return 0


def list_runs(limit: int = 100, db_path: Path | None = None) -> list[dict]:
    """One summary row per run_id, newest first: run_id, symbol, started, calls, tokens."""
    path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(path)
        rows = conn.execute(
            "SELECT run_id, MAX(symbol) AS symbol, MIN(ts) AS started, "
            "       COUNT(*) AS calls, SUM(tokens) AS tokens, MAX(source) AS source "
            "FROM committee_transcript WHERE run_id IS NOT NULL "
            "GROUP BY run_id ORDER BY MAX(id) DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("transcript.list_runs failed: %s", exc)
        return []


def get_run(run_id: str, db_path: Path | None = None) -> list[dict]:
    """Every call in one run, in call order."""
    path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(path)
        rows = conn.execute(
            "SELECT * FROM committee_transcript WHERE run_id=? ORDER BY id",
            (run_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        log.warning("transcript.get_run failed: %s", exc)
        return []
