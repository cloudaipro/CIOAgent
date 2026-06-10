"""Tests for the detailed conversation history feature (cio/convlog.py + dashboard tab)."""
from __future__ import annotations

from datetime import datetime

import pytest

from cio import convlog, timeutil
from cio.dashboard import views


@pytest.fixture
def logdir(tmp_path, monkeypatch):
    monkeypatch.setenv("CIO_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("CIO_DETAILED_LOG", "1")
    return tmp_path / "logs"


def _when(day="2026-06-10", h=14, m=3):
    return datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), h, m,
                    tzinfo=timeutil.local_tz())


# --- flag -------------------------------------------------------------------

def test_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("CIO_DETAILED_LOG", raising=False)
    monkeypatch.setenv("CIO_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr("cio.dashboard.settings.get_detailed_log", lambda: False)
    assert convlog.enabled() is False
    convlog.log_call("claude", "m", "sys", "user", "resp", 10, when=_when())
    assert convlog.list_days() == []                 # nothing written when disabled


# --- flag precedence: env wins, else persisted dashboard setting ------------

def test_enabled_env_wins_and_locks(monkeypatch):
    monkeypatch.setattr("cio.dashboard.settings.get_detailed_log", lambda: False)
    monkeypatch.setenv("CIO_DETAILED_LOG", "1")
    assert convlog.enabled() is True and convlog.locked_by_env() is True
    monkeypatch.setenv("CIO_DETAILED_LOG", "0")       # env value respected even if "off"
    assert convlog.enabled() is False and convlog.locked_by_env() is True


def test_enabled_falls_back_to_dashboard_setting(monkeypatch):
    monkeypatch.delenv("CIO_DETAILED_LOG", raising=False)
    monkeypatch.setattr("cio.dashboard.settings.get_detailed_log", lambda: True)
    assert convlog.enabled() is True and convlog.locked_by_env() is False
    monkeypatch.setattr("cio.dashboard.settings.get_detailed_log", lambda: False)
    assert convlog.enabled() is False


def test_settings_detailed_log_roundtrip(tmp_path, monkeypatch):
    from cio.dashboard import settings
    monkeypatch.setattr(settings, "_PATH", tmp_path / "s.json")
    assert settings.get_detailed_log() is False       # default off
    settings.set_detailed_log(True)
    assert settings.get_detailed_log() is True
    settings.set_detailed_log(False)
    assert settings.get_detailed_log() is False


@pytest.mark.parametrize("val,exp", [("1", True), ("true", True), ("on", True),
                                     ("yes", True), ("0", False), ("off", False), ("", False)])
def test_flag_parsing(monkeypatch, val, exp):
    monkeypatch.setenv("CIO_DETAILED_LOG", val)
    assert convlog.enabled() is exp


# --- write path -------------------------------------------------------------

def test_log_call_writes_day_file_with_all_fields(logdir):
    convlog.log_call("openai", "gpt-5.5-2026-04-23", "SYS-TEXT", "USER-TEXT",
                     "RESP-TEXT", 123, scope="chat:7", role=None, kind="chat",
                     when=_when())
    f = logdir / "2026" / "06" / "2026-06-10.txt"
    assert f.is_file()                               # directory rule logs/yyyy/mm
    text = f.read_text(encoding="utf-8")
    for needle in ("provider=openai", "model=gpt-5.5-2026-04-23", "tokens=123",
                   "scope=chat:7", "kind=chat", "SYS-TEXT", "USER-TEXT", "RESP-TEXT",
                   "SYSTEM PROMPT", "USER PROMPT", "RESPONSE"):
        assert needle in text, needle


def test_multiple_calls_append_and_count(logdir):
    convlog.log_call("claude", "m", "s1", "u1", "r1", 1, when=_when(m=1))
    convlog.log_call("nim", "k", "s2", "u2", "r2", 2, when=_when(m=2))
    days = convlog.list_days()
    assert len(days) == 1 and days[0]["day"] == "2026-06-10"
    assert days[0]["entries"] == 2                   # both calls in one day file


def test_list_days_newest_first(logdir):
    convlog.log_call("claude", "m", "s", "u", "r", 1, when=_when("2026-06-09"))
    convlog.log_call("claude", "m", "s", "u", "r", 1, when=_when("2026-07-01"))
    days = [d["day"] for d in convlog.list_days()]
    assert days == ["2026-07-01", "2026-06-09"]


# --- read / delete ----------------------------------------------------------

def test_read_and_delete_day(logdir):
    convlog.log_call("claude", "m", "s", "u", "r", 1, when=_when())
    assert "RESPONSE" in (convlog.read_day("2026-06-10") or "")
    assert convlog.delete_day("2026-06-10") is True
    assert convlog.read_day("2026-06-10") is None
    assert convlog.delete_day("2026-06-10") is False  # already gone


def test_invalid_day_is_rejected(logdir):
    # path-traversal / malformed day strings never read or delete anything
    for bad in ("../etc/passwd", "2026-6-1", "2026/06/10", "", "nope"):
        assert convlog.read_day(bad) is None
        assert convlog.delete_day(bad) is False


# --- dashboard view ---------------------------------------------------------

def test_render_detailed_off_state():
    html = views.render_detailed([], None, None, enabled=False, level=1)
    assert "OFF" in html and "CIO_DETAILED_LOG" in html


def test_render_detailed_day_list_and_delete():
    days = [{"day": "2026-06-10", "entries": 3, "bytes": 999}]
    html = views.render_detailed(days, None, None, enabled=True, level=1)
    assert "/detailed?day=2026-06-10" in html        # day selectable
    assert "value='wipe_day'" in html                # per-day delete present
    assert "ON" in html


def test_render_detailed_selected_day_shows_content():
    html = views.render_detailed(
        [{"day": "2026-06-10", "entries": 1, "bytes": 10}], "2026-06-10",
        "FULL LOG <x>", enabled=True, level=1)
    assert "FULL LOG &lt;x&gt;" in html              # content shown + HTML-escaped
    assert "Delete this day" in html
