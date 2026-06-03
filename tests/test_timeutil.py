"""Tests for cio.timeutil — local-zone display + day boundary."""
from __future__ import annotations

import importlib

import pytest


def _reload(monkeypatch, tz: str | None):
    if tz is None:
        monkeypatch.delenv("CIO_TZ", raising=False)
    else:
        monkeypatch.setenv("CIO_TZ", tz)
    import cio.timeutil as tu
    return importlib.reload(tu)


def test_utc_to_local_vancouver_summer(monkeypatch):
    """16:24 UTC in June (PDT, UTC-7) → 09:24 local."""
    tu = _reload(monkeypatch, "America/Vancouver")
    assert tu.utc_to_local("2026-06-02 16:24:43") == "2026-06-02 09:24:43"


def test_utc_to_local_respects_cio_tz(monkeypatch):
    tu = _reload(monkeypatch, "UTC")
    assert tu.utc_to_local("2026-06-02 16:24:43") == "2026-06-02 16:24:43"


def test_default_zone_is_vancouver(monkeypatch):
    tu = _reload(monkeypatch, None)
    assert str(tu.local_tz()) == "America/Vancouver"


def test_bad_zone_falls_back(monkeypatch):
    tu = _reload(monkeypatch, "Not/AZone")
    assert str(tu.local_tz()) in ("America/Vancouver", "UTC")


@pytest.mark.parametrize("bad", ["", None, "not-a-timestamp"])
def test_unparseable_returns_input(monkeypatch, bad):
    tu = _reload(monkeypatch, "America/Vancouver")
    assert tu.utc_to_local(bad) == (bad or "")


def test_iso_with_z_suffix(monkeypatch):
    tu = _reload(monkeypatch, "America/Vancouver")
    assert tu.utc_to_local("2026-06-02T16:24:43Z") == "2026-06-02 09:24:43"


# ---------------------------------------------------------------------------
# is_trading_day — Nasdaq calendar (with weekday fallback)
# ---------------------------------------------------------------------------

from datetime import date, datetime  # noqa: E402

import cio.timeutil as _tu  # noqa: E402


def test_is_trading_day_uses_calendar_set(monkeypatch):
    """When the calendar resolves, membership decides — a known holiday is False."""
    # 2026-07-03 is the observed Independence Day market close; 2026-07-06 a Monday.
    trading = {date(2026, 7, 6), date(2026, 7, 2)}
    monkeypatch.setattr(_tu, "_trading_days_for_year", lambda y: trading)
    assert _tu.is_trading_day("2026-07-06") is True
    assert _tu.is_trading_day("2026-07-03") is False   # holiday, not in set
    assert _tu.is_trading_day(date(2026, 7, 2)) is True


def test_is_trading_day_weekday_fallback(monkeypatch):
    """No calendar library → Mon-Fri fallback."""
    monkeypatch.setattr(_tu, "_trading_days_for_year", lambda y: None)
    assert _tu.is_trading_day(date(2026, 6, 3)) is True    # Wednesday
    assert _tu.is_trading_day(date(2026, 6, 6)) is False   # Saturday
    assert _tu.is_trading_day(date(2026, 6, 7)) is False   # Sunday


def test_is_trading_day_accepts_datetime_and_str(monkeypatch):
    monkeypatch.setattr(_tu, "_trading_days_for_year", lambda y: None)
    assert _tu.is_trading_day(datetime(2026, 6, 3, 14, 30)) is True   # Wed
    assert _tu.is_trading_day("2026-06-06") is False                  # Sat


def test_is_trading_day_unparseable_is_false(monkeypatch):
    monkeypatch.setattr(_tu, "_trading_days_for_year", lambda y: None)
    assert _tu.is_trading_day("garbage") is False
