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
