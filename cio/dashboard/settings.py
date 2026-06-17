"""Tiny JSON store for operator-tunable dashboard settings.

Separate from the committee-model YAML (cio.committee.models): this holds runtime
operational flags the operator flips from the Configure tab — whether logs are
mirrored to a date-based file on disk (read by cio.logsetup), and whether the
detailed conversation history is captured (read by cio.convlog). Persisted so the
choices survive restarts and are shared across the bot + dashboard processes.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "dashboard_settings.json"
_LOCK = threading.Lock()
_DEFAULTS: dict = {"log_to_file": False, "detailed_log": False}


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


def get_detailed_log() -> bool:
    """Whether detailed conversation history is captured. Robust against a
    hand-edited file (accepts bools + common truthy strings)."""
    val = get("detailed_log", False)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


def set_detailed_log(enabled: bool) -> None:
    set("detailed_log", bool(enabled))


def get_candle_style() -> str:
    """Global candle style used by all chart renders (dashboard + bot).

    "standard" — color = close vs open (intraday direction).
    "hollow"   — color = close vs prev_close (day-over-day direction);
                 hollow outline when close >= open, solid fill otherwise.
    """
    val = get("candle_style", "standard")
    return val if val in ("standard", "hollow") else "standard"


def set_candle_style(style: str) -> None:
    if style not in ("standard", "hollow"):
        raise ValueError(f"candle_style must be 'standard' or 'hollow', got {style!r}")
    set("candle_style", style)
