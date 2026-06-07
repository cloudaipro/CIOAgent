"""Tiny JSON store for operator-tunable dashboard settings.

Separate from the committee-model YAML (cio.committee.models): this holds runtime
operational flags the operator flips from the Configure tab — currently just
whether logs are mirrored to a date-based file on disk. Persisted so the choice
survives restarts; read by cio.logsetup at process start.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "dashboard_settings.json"
_LOCK = threading.Lock()
_DEFAULTS: dict = {"log_to_file": False}


def _read() -> dict:
    try:
        with _PATH.open() as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _write(data: dict) -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PATH.with_suffix(".json.tmp")
    with tmp.open("w") as fh:
        json.dump(data, fh, indent=2)
    tmp.replace(_PATH)   # atomic swap


def get(key: str, default=None):
    val = _read().get(key, _DEFAULTS.get(key, default))
    return val if val is not None else default


def set(key: str, value) -> None:
    with _LOCK:
        data = _read()
        data[key] = value
        _write(data)


def get_log_to_file() -> bool:
    # Robust against a hand-edited file: accept real bools and common truthy
    # strings; anything else (incl. the string "0"/"false") reads as False.
    val = get("log_to_file", False)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def set_log_to_file(enabled: bool) -> None:
    set("log_to_file", bool(enabled))
