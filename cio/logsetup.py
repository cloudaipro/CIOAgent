"""Central logging setup — console always, optional date-based file on disk.

Both entry points (the Telegram bot and the dashboard server) call
``configure_logging()`` at start instead of their own ``basicConfig``. When file
logging is enabled the root logger also writes to ``logs/cio-YYYY-MM-DD.log`` —
a fresh file per day, so the operator can review/grep a specific day's activity
(including the ``cio.evidence`` lines that confirm which primary-source tools ran).

Enable via either:
  * env ``CIO_LOG_TO_FILE`` = 1/true/yes/on  (overrides the dashboard setting), or
  * the Configure tab toggle (persisted in dashboard_settings.json).
``CIO_LOG_DIR`` overrides the directory (default ``<project>/logs``).
"""
from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_LOG_DIR = _PROJECT_ROOT / "logs"
_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# The dated file handler currently attached to root, if any. Module-level so a
# live toggle can add/remove it without restarting the process.
_FILE_HANDLER: logging.Handler | None = None


def log_dir() -> Path:
    return Path(os.getenv("CIO_LOG_DIR") or _DEFAULT_LOG_DIR)


def dated_log_path(d: date | None = None) -> Path:
    d = d or date.today()
    return log_dir() / f"cio-{d:%Y-%m-%d}.log"


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def file_logging_enabled() -> bool:
    """Env override wins; otherwise the persisted dashboard setting."""
    env = os.getenv("CIO_LOG_TO_FILE")
    if env is not None:
        return _truthy(env)
    try:
        from .dashboard import settings
        return settings.get_log_to_file()
    except Exception:
        return False


def current_log_file() -> Path | None:
    """The path being written to right now, or None when file logging is off."""
    if _FILE_HANDLER is not None:
        return Path(getattr(_FILE_HANDLER, "baseFilename", "")) or None
    return None


def apply_file_logging(enabled: bool) -> Path | None:
    """Attach or detach the dated file handler on the root logger (idempotent).

    Returns the active log-file path when enabled, else None. Safe to call live
    from the dashboard toggle — it mutates the running process's root logger.
    """
    global _FILE_HANDLER
    root = logging.getLogger()
    if enabled:
        if _FILE_HANDLER is None:
            try:
                log_dir().mkdir(parents=True, exist_ok=True)
                handler = logging.FileHandler(dated_log_path(), encoding="utf-8")
                handler.setFormatter(logging.Formatter(_FMT))
                root.addHandler(handler)
                _FILE_HANDLER = handler
            except OSError as e:
                logging.getLogger("cio.logsetup").warning(
                    "could not enable file logging: %s", e)
                return None
        return current_log_file()
    # disabling
    if _FILE_HANDLER is not None:
        root.removeHandler(_FILE_HANDLER)
        try:
            _FILE_HANDLER.close()
        except Exception:
            pass
        _FILE_HANDLER = None
    return None


def configure_logging(level: int = logging.INFO) -> Path | None:
    """Idempotent root-logger setup. Console handler always; dated file when enabled.
    Returns the active log-file path or None."""
    root = logging.getLogger()
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in root.handlers):
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(_FMT))
        root.addHandler(console)
    root.setLevel(level)
    # Quiet noisy third-party loggers: httpx logs each request URL at INFO, which
    # for Finnhub includes the API token as a query param — must never be written
    # to a persisted log file. Cap them at WARNING.
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return apply_file_logging(file_logging_enabled())
