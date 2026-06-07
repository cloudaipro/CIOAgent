"""Tests for date-based file logging (cio.logsetup) and the dashboard settings
store that backs the Configure-tab toggle.
"""
import logging
from datetime import date

import pytest

from cio import logsetup
from cio.dashboard import settings


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    # Point logs at a temp dir and the settings store at a temp file; ensure the
    # dated handler is detached before and after each test.
    monkeypatch.setenv("CIO_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(settings, "_PATH", tmp_path / "dashboard_settings.json")
    monkeypatch.delenv("CIO_LOG_TO_FILE", raising=False)
    logsetup.apply_file_logging(False)
    yield
    logsetup.apply_file_logging(False)


# --- settings store ---------------------------------------------------------

def test_settings_default_false():
    assert settings.get_log_to_file() is False


def test_settings_roundtrip():
    settings.set_log_to_file(True)
    assert settings.get_log_to_file() is True
    settings.set_log_to_file(False)
    assert settings.get_log_to_file() is False


# --- file logging on/off ----------------------------------------------------

def test_dated_log_path_is_date_based():
    p = logsetup.dated_log_path(date(2026, 6, 6))
    assert p.name == "cio-2026-06-06.log"


def test_apply_file_logging_creates_dated_file():
    path = logsetup.apply_file_logging(True)
    assert path is not None
    assert path.name == f"cio-{date.today():%Y-%m-%d}.log"
    assert path.exists()
    # a log record actually lands in the file (production sets INFO via
    # configure_logging; set it here so the INFO record isn't filtered).
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("cio.evidence").info("wire check")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "wire check" in path.read_text()


def test_apply_file_logging_off_returns_none_and_detaches():
    logsetup.apply_file_logging(True)
    assert logsetup.current_log_file() is not None
    assert logsetup.apply_file_logging(False) is None
    assert logsetup.current_log_file() is None


def test_idempotent_no_duplicate_handlers():
    logsetup.apply_file_logging(True)
    n1 = len(logging.getLogger().handlers)
    logsetup.apply_file_logging(True)            # second call must not add another
    assert len(logging.getLogger().handlers) == n1


# --- enabled-flag resolution (env override vs setting) ----------------------

def test_env_override_wins(monkeypatch):
    settings.set_log_to_file(False)
    monkeypatch.setenv("CIO_LOG_TO_FILE", "1")
    assert logsetup.file_logging_enabled() is True
    monkeypatch.setenv("CIO_LOG_TO_FILE", "0")
    assert logsetup.file_logging_enabled() is False


def test_setting_used_when_env_unset():
    settings.set_log_to_file(True)
    assert logsetup.file_logging_enabled() is True


def test_configure_logging_quiets_httpx():
    # httpx logs request URLs (incl. the Finnhub API token) at INFO — must be
    # capped so the token never reaches a persisted log file.
    logging.getLogger("httpx").setLevel(logging.INFO)
    logsetup.configure_logging()
    assert logging.getLogger("httpx").level == logging.WARNING


# --- Configure-tab rendering -----------------------------------------------

def test_configure_tab_shows_logging_toggle():
    from cio.dashboard import views
    on = views.render_configure({}, 0, [], {}, log_to_file=True,
                                log_file="/x/logs/cio-2026-06-06.log", log_dir="/x/logs")
    assert "Logging" in on and "Disable file logging" in on and "cio-2026-06-06.log" in on
    off = views.render_configure({}, 0, [], {}, log_to_file=False, log_dir="/x/logs")
    assert "Enable file logging" in off
    locked = views.render_configure({}, 0, [], {}, log_to_file=True,
                                    log_dir="/x/logs", log_locked_by_env=True)
    assert "CIO_LOG_TO_FILE" in locked and "Disable file logging" not in locked
