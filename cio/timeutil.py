"""
timeutil.py — Local-timezone helpers for the CIO agent.

All persisted timestamps are stored UTC (SQLite ``datetime('now')``). The operator
lives in one place, so the dashboard should DISPLAY local time and token usage
should roll over on the LOCAL day boundary — not UTC.

Timezone is ``CIO_TZ`` (IANA name), defaulting to America/Vancouver. Every helper
is defensive: a bad zone name or unparseable timestamp degrades gracefully instead
of raising into a render path.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

DEFAULT_TZ = "America/Vancouver"


def local_tz() -> ZoneInfo:
    """Return the configured local zone (CIO_TZ), falling back to Vancouver/UTC."""
    name = os.getenv("CIO_TZ", DEFAULT_TZ)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        log.warning("timeutil: bad CIO_TZ %r; falling back to %s", name, DEFAULT_TZ)
        try:
            return ZoneInfo(DEFAULT_TZ)
        except Exception:
            return ZoneInfo("UTC")


def today_local() -> str:
    """Today's date (YYYY-MM-DD) in the local zone — the token-usage day boundary."""
    return datetime.now(local_tz()).date().isoformat()


def utc_to_local(ts: str | None) -> str:
    """
    Convert a stored UTC timestamp string to local-zone "YYYY-MM-DD HH:MM:SS".

    Accepts the SQLite ``datetime('now')`` format ("YYYY-MM-DD HH:MM:SS", naive UTC)
    and ISO variants. On any parse failure returns the original string unchanged so
    a display never breaks.
    """
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
    except ValueError:
        return ts
    if dt.tzinfo is None:                 # SQLite datetime('now') is naive UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz()).strftime("%Y-%m-%d %H:%M:%S")
