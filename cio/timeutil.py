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
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

DEFAULT_TZ = "America/Vancouver"

# Cache the set of Nasdaq trading dates per calendar year — the schedule is fixed
# for a year, so we build it once. Keyed by year → set[date].
_TRADING_DAYS_CACHE: dict[int, set[date]] = {}


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


def _trading_days_for_year(year: int) -> set[date] | None:
    """Set of Nasdaq trading dates for *year* via the NYSE calendar (Nasdaq shares
    the NYSE holiday schedule). Cached. Returns None when the calendar library is
    unavailable so the caller can fall back to a weekday rule."""
    if year in _TRADING_DAYS_CACHE:
        return _TRADING_DAYS_CACHE[year]
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        log.debug("pandas_market_calendars not installed; using weekday fallback")
        return None
    try:
        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(start_date=f"{year}-01-01", end_date=f"{year}-12-31")
        days = {ts.date() for ts in schedule.index}
    except Exception as exc:
        log.warning("_trading_days_for_year(%d) failed: %s", year, exc)
        return None
    _TRADING_DAYS_CACHE[year] = days
    return days


def is_trading_day(when: "str | date | datetime | None" = None) -> bool:
    """Whether *when* is a Nasdaq trading day (holidays + weekends excluded).

    *when* may be a YYYY-MM-DD string, a date/datetime, or None (today, local TZ).
    Mirrors AI4StockMarket/StockPricePrediction/build_stocks_data.is_trading_day:
    uses the NYSE calendar via pandas_market_calendars. If that library is missing
    or errors, degrades to a Mon-Fri weekday check so scheduling never breaks.
    """
    if when is None:
        d = datetime.now(local_tz()).date()
    elif isinstance(when, datetime):
        d = when.date()
    elif isinstance(when, date):
        d = when
    elif isinstance(when, str):
        try:
            d = datetime.strptime(when.strip()[:10], "%Y-%m-%d").date()
        except ValueError:
            log.warning("is_trading_day: unparseable date %r", when)
            return False
    else:
        log.warning("is_trading_day: unsupported type %r", type(when))
        return False

    days = _trading_days_for_year(d.year)
    if days is not None:
        return d in days
    return d.weekday() < 5  # fallback: Mon-Fri
