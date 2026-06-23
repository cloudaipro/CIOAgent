"""Data-source freshness heartbeat — "is this source's data actually live?"

DISTINCT from the price-bar/quote staleness the agent appends to quote results
(cio.agent + tests/test_quote_freshness.py). That answers "is THIS quote's bar
today's?"; this answers "when did source X last return ANY data?". The committee
votes on a bundle stitched from several opt-in sources (EDGAR, Finnhub, yfinance,
IBKR, GDELT, FRED); if one silently goes dark, the bundle still renders and the
panel shows a confident "all clear" over missing inputs. This tracker is the
guard worldmonitor's data-freshness.ts exists for, ported to our stack.

Heartbeat store: one small JSON file (atomic-replace write, raw read — no TTL
gating, the age IS the signal). Cross-process safe so the dashboard can read the
same heartbeats the bot/committee write. Every function is offline-safe and never
raises — a freshness-store hiccup must never break a data fetch.
"""
from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger(__name__)

_DEFAULT_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "source_freshness.json",
)

# Age thresholds. worldmonitor escalates again at 6h; for a solo committee,
# source data >= 2h old is already "do not trust as live", so everything past the
# stale bound collapses to very_stale.
_FRESH_MAX_S = 15 * 60
_STALE_MAX_S = 2 * 3600

# Worst-wins ordering for the rollup (higher = worse).
_SEVERITY = {"fresh": 0, "stale": 1, "very_stale": 2, "error": 3, "no_data": 4}

# Known sources + whether the committee bundle treats them as load-bearing. A
# required source going no_data/very_stale is what should make the rollup red.
SOURCE_REGISTRY: dict[str, dict] = {
    "yfinance": {"name": "Yahoo Finance prices", "required": True},
    "finnhub": {"name": "Finnhub (analyst / news / insider)", "required": True},
    "edgar": {"name": "SEC EDGAR filings", "required": False},
    "gdelt": {"name": "GDELT news", "required": False},
    "fred": {"name": "FRED macro / yield curve", "required": False},
    "ibkr": {"name": "IBKR portfolio", "required": False},
}


def _file() -> str:
    # Resolved per call so CIO_FRESHNESS_FILE can be set after import (tests).
    return os.environ.get("CIO_FRESHNESS_FILE", _DEFAULT_FILE)


def _load() -> dict:
    try:
        with open(_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.debug("freshness load failed: %s", e)
        return {}


def _save(store: dict) -> None:
    path = _file()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f)
        os.replace(tmp, path)  # atomic on POSIX; no torn reads for the dashboard
    except Exception as e:
        log.debug("freshness save failed: %s", e)


def record(source_id: str, count: int = 1, error: str | None = None) -> None:
    """Stamp a heartbeat for *source_id*.

    Success (error=None): set fetched_at=now, count, clear last_error.
    Failure (error given): set last_error + errored_at WITHOUT bumping fetched_at,
    so a source that errors after going quiet still reads as stale/error, not fresh.
    Never raises.
    """
    if not source_id:
        return
    store = _load()
    entry = store.get(source_id) if isinstance(store.get(source_id), dict) else {}
    now = time.time()
    if error is None:
        entry.update({"fetched_at": now, "count": int(count), "last_error": None})
    else:
        entry.update({"last_error": str(error)[:300], "errored_at": now})
    store[source_id] = entry
    _save(store)


def status(source_id: str, *, now: float | None = None) -> str:
    """fresh / stale / very_stale / error / no_data for *source_id*."""
    return _status_from_entry(_load().get(source_id), now=now)


def _status_from_entry(entry, *, now: float | None = None) -> str:
    if not isinstance(entry, dict):
        return "no_data"
    now = time.time() if now is None else now
    fetched_at = entry.get("fetched_at")
    errored_at = entry.get("errored_at")
    # An error that is at least as recent as the last success surfaces as error.
    if entry.get("last_error") and errored_at is not None and (
        fetched_at is None or errored_at >= fetched_at
    ):
        return "error"
    if not isinstance(fetched_at, (int, float)):
        return "no_data"
    age = now - fetched_at
    if age < _FRESH_MAX_S:
        return "fresh"
    if age < _STALE_MAX_S:
        return "stale"
    return "very_stale"


def summary(*, now: float | None = None) -> dict:
    """Per-source rows + a worst-wins rollup over the REQUIRED sources.

    {"sources": [{id, name, required, status, fetched_at, age_seconds, count,
    last_error}], "overall": <worst required status>, "checked_at": now}.
    A missing OPTIONAL source does not redden the rollup; a missing REQUIRED one
    does. Offline-safe.
    """
    now = time.time() if now is None else now
    store = _load()
    # Registry first (stable order), then any extra recorded ids not in it.
    ids = list(SOURCE_REGISTRY.keys()) + [
        k for k in store.keys() if k not in SOURCE_REGISTRY
    ]
    rows: list[dict] = []
    worst = "fresh"
    any_required = False
    for sid in ids:
        meta = SOURCE_REGISTRY.get(sid, {"name": sid, "required": False})
        entry = store.get(sid)
        st = _status_from_entry(entry, now=now)
        fetched_at = entry.get("fetched_at") if isinstance(entry, dict) else None
        rows.append({
            "id": sid,
            "name": meta["name"],
            "required": meta["required"],
            "status": st,
            "fetched_at": fetched_at,
            "age_seconds": (now - fetched_at) if isinstance(fetched_at, (int, float)) else None,
            "count": entry.get("count") if isinstance(entry, dict) else None,
            "last_error": entry.get("last_error") if isinstance(entry, dict) else None,
        })
        if meta["required"]:
            any_required = True
            if _SEVERITY[st] > _SEVERITY[worst]:
                worst = st
    overall = worst if any_required else "fresh"
    return {"sources": rows, "overall": overall, "checked_at": now}
