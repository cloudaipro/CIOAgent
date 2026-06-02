"""
usage.py — Daily token-budget accounting for committee backends.

Tracks cumulative token usage per service per LOCAL day (CIO_TZ) in the committee.db
SQLite database.  Used by the CIO fallback chain to enforce daily limits
on openai and claude before falling back to NIM.

All functions are safe to call at any time; they never raise.
``DB_PATH`` is module-level so tests can monkeypatch it.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from cio import db as _db
from cio import timeutil as _timeutil

log = logging.getLogger(__name__)

# Default to the committee-side DB alongside the main cio.db.
# Monkeypatch this in tests: `monkeypatch.setattr("cio.committee.usage.DB_PATH", tmp_path / "t.db")`
DB_PATH: Path = _db.DB_PATH.parent / "committee.db"


def _today() -> str:
    """Return today's date (YYYY-MM-DD) on the LOCAL day boundary (CIO_TZ)."""
    return _timeutil.today_local()


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a plain sqlite3 connection (no vec extension needed for this table)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS token_usage (
            service TEXT NOT NULL,
            day     TEXT NOT NULL,
            tokens  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (service, day)
        )
        """
    )
    conn.commit()
    return conn


def used_today(service: str, day: str | None = None, db_path: Path | None = None) -> int:
    """
    Return cumulative tokens used by *service* on *day* (default: today UTC).

    Returns 0 if no row exists.  Never raises.
    """
    effective_day = day or _today()
    effective_path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(effective_path)
        row = conn.execute(
            "SELECT tokens FROM token_usage WHERE service=? AND day=?",
            (service, effective_day),
        ).fetchone()
        conn.close()
        return int(row[0]) if row else 0
    except Exception as exc:
        log.warning("usage.used_today failed: %s", exc)
        return 0


def record(service: str, tokens: int, day: str | None = None, db_path: Path | None = None) -> None:
    """
    Add *tokens* to the running total for *service* on *day* (default: today UTC).

    Uses an upsert so concurrent calls accumulate correctly.
    Silently ignores tokens <= 0.  Never raises.
    """
    if tokens <= 0:
        return
    effective_day = day or _today()
    effective_path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(effective_path)
        conn.execute(
            """
            INSERT INTO token_usage (service, day, tokens) VALUES (?, ?, ?)
            ON CONFLICT (service, day) DO UPDATE SET tokens = tokens + excluded.tokens
            """,
            (service, effective_day, tokens),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("usage.record failed: %s", exc)


def recent(days: int = 30, db_path: Path | None = None) -> list[dict]:
    """
    Return per-service per-day token totals, newest day first (for the dev
    dashboard). Limited to the most recent *days* distinct days. Never raises.
    """
    effective_path = db_path if db_path is not None else DB_PATH
    try:
        conn = _connect(effective_path)
        rows = conn.execute(
            "SELECT service, day, tokens FROM token_usage "
            "WHERE day IN (SELECT DISTINCT day FROM token_usage ORDER BY day DESC LIMIT ?) "
            "ORDER BY day DESC, service",
            (days,),
        ).fetchall()
        conn.close()
        return [{"service": r[0], "day": r[1], "tokens": int(r[2])} for r in rows]
    except Exception as exc:
        log.warning("usage.recent failed: %s", exc)
        return []


def over_budget(service: str, limit: int | None, db_path: Path | None = None) -> bool:
    """
    Return True if *service* has hit or exceeded *limit* tokens today (UTC).

    If *limit* is None (no cap configured) always returns False.  Never raises.
    """
    if limit is None:
        return False
    return used_today(service, db_path=db_path) >= limit
