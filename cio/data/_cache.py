"""TTL disk cache for cio.data fetchers.

Keeps EDGAR / Finnhub responses on disk so repeat lookups (the WMA re-runs the
same watchlist every morning; the committee re-reviews the same names) stay well
under the free-tier rate limits. Same spirit as the per-symbol joblib cache in
cio.stock.data, but JSON + TTL instead of pickle. Offline-safe; never raises.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

log = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "data_cache",
)


def _dir() -> str:
    # Resolved per call so CIO_DATA_CACHE_DIR can be set after import (tests).
    return os.environ.get("CIO_DATA_CACHE_DIR", _DEFAULT_CACHE_DIR)


def _path(namespace: str, key: str) -> str:
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    d = os.path.join(_dir(), namespace)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{h}.json")


def read(namespace: str, key: str, ttl_seconds: float):
    """Return cached value if present and younger than *ttl_seconds*, else None."""
    try:
        p = _path(namespace, key)
        if os.path.isfile(p) and (time.time() - os.path.getmtime(p)) < ttl_seconds:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log.debug("cache read miss %s/%s: %s", namespace, key, e)
    return None


def write(namespace: str, key: str, value):
    """Persist *value* (JSON-serialisable). Returns *value* for call chaining."""
    try:
        with open(_path(namespace, key), "w", encoding="utf-8") as f:
            json.dump(value, f)
    except Exception as e:
        log.debug("cache write failed %s/%s: %s", namespace, key, e)
    return value
