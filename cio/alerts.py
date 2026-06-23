"""Alert dedup + cooldown — port of worldmonitor's breaking-news-alerts discipline.

worldmonitor learned the hard way that a naive "fire on every matching item" alerter
spams the same story on every poll/restart. Its guard: a per-event key (normalized
title + source + host), a per-event cooldown, and a short global cooldown so a burst
can't machine-gun notifications. This is that guard for CIOAgent's Telegram alerts —
backed by the SQLite alert_cooldown table so it survives bot restarts (the news-spike
job, F5, runs on a scheduler and the process recycles).

Every function is offline-safe: a DB hiccup degrades to "not a duplicate / no-op",
never raises — a freshness/dedup wobble must never swallow or crash a real alert.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from urllib.parse import urlparse

from . import db

log = logging.getLogger(__name__)

DEFAULT_EVENT_COOLDOWN_S = 30 * 60   # per-event: same catalyst won't re-fire for 30m
DEFAULT_GLOBAL_COOLDOWN_S = 60       # global: at most ~1 alert/min across all events
_GLOBAL_KEY = "__global__"

_PUNCT = re.compile(r"[^\w\s]")


def _normalize_title(title: str) -> str:
    return _PUNCT.sub("", (title or "").lower()).strip()[:80]


def make_key(headline: str, source: str = "", link: str = "") -> str:
    """Stable dedup key for an alert (normalized title + source + link host)."""
    host = ""
    if link:
        try:
            host = urlparse(link).hostname or ""
        except Exception:
            host = ""
    raw = f"{_normalize_title(headline)}|{(source or '').strip().lower()}|{host.lower()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def is_duplicate(key: str, cooldown_s: float = DEFAULT_EVENT_COOLDOWN_S, *,
                 now: float | None = None, db_path=db.DB_PATH) -> bool:
    """True if *key* fired within the last *cooldown_s* seconds. Offline-safe."""
    if not key:
        return False
    now = time.time() if now is None else now
    try:
        conn = db.connect(db_path)
        try:
            row = conn.execute(
                "SELECT fired_at FROM alert_cooldown WHERE key=?", (key,)
            ).fetchone()
        finally:
            conn.close()
    except Exception as e:
        log.debug("alert dedup read failed: %s", e)
        return False
    return bool(row) and (now - float(row["fired_at"])) < cooldown_s


def mark_fired(key: str, *, now: float | None = None, db_path=db.DB_PATH) -> None:
    """Record *key* as fired at *now* (upsert). Offline-safe."""
    if not key:
        return
    now = time.time() if now is None else now
    try:
        conn = db.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO alert_cooldown(key, fired_at) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET fired_at=excluded.fired_at",
                (key, now),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        log.debug("alert dedup write failed: %s", e)


def claim(headline: str, source: str = "", link: str = "",
          cooldown_s: float = DEFAULT_EVENT_COOLDOWN_S, *,
          now: float | None = None, db_path=db.DB_PATH) -> bool:
    """Atomic "should I send this alert?". Returns True and records the fire when the
    event is NOT a recent duplicate; False (send nothing) when it is.
    """
    key = make_key(headline, source, link)
    if is_duplicate(key, cooldown_s, now=now, db_path=db_path):
        return False
    mark_fired(key, now=now, db_path=db_path)
    return True


def global_claim(cooldown_s: float = DEFAULT_GLOBAL_COOLDOWN_S, *,
                 now: float | None = None, db_path=db.DB_PATH) -> bool:
    """Global rate gate: True at most once per *cooldown_s* across all alerts."""
    if is_duplicate(_GLOBAL_KEY, cooldown_s, now=now, db_path=db_path):
        return False
    mark_fired(_GLOBAL_KEY, now=now, db_path=db_path)
    return True


def prune(older_than_s: float = 7 * 86400, *, now: float | None = None,
          db_path=db.DB_PATH) -> int:
    """Delete cooldown rows older than *older_than_s*. Returns rows removed."""
    now = time.time() if now is None else now
    try:
        conn = db.connect(db_path)
        try:
            cur = conn.execute(
                "DELETE FROM alert_cooldown WHERE fired_at < ?", (now - older_than_s,)
            )
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()
    except Exception as e:
        log.debug("alert prune failed: %s", e)
        return 0
