"""Shared sync HTTP + rate limiting for cio.data fetchers.

The committee bundle (cio.committee.bundle.gather_bundle) is synchronous and the
stock layer it sits beside (cio.stock.data) is sync too, so these data fetchers
use a sync httpx client rather than the async one in cio.web. Every helper is
offline-safe: any failure returns None, never raises.
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger(__name__)

# Default read/connect timeout (seconds) for every cio.data GET. Overridable via
# CIO_HTTP_TIMEOUT so a slow upstream (e.g. finnhub /stock/earnings under the free
# tier) can be given more headroom without a code change. Falls back to 15s on an
# unset/garbage value.
def _default_timeout() -> float:
    try:
        return float(os.getenv("CIO_HTTP_TIMEOUT", "15"))
    except (TypeError, ValueError):
        return 15.0


class RateLimiter:
    """Minimum-interval limiter. Thread-safe so it also bounds the WMA, which
    builds bundles concurrently via ``asyncio.to_thread``."""

    def __init__(self, min_interval: float):
        self._mi = max(0.0, float(min_interval))
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            delta = time.monotonic() - self._last
            if delta < self._mi:
                time.sleep(self._mi - delta)
            self._last = time.monotonic()


def get_json(url, *, params=None, headers=None, timeout=None, limiter=None):
    """GET *url* and return parsed JSON, or ``None`` on any error.

    Never raises — a flaky network or a rate-limit response degrades to ``None``
    and the caller falls back to its empty result. *timeout* defaults to
    ``CIO_HTTP_TIMEOUT`` (15s) when not given explicitly.
    """
    import httpx

    if timeout is None:
        timeout = _default_timeout()
    try:
        if limiter is not None:
            limiter.wait()
        resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("data.get_json failed for %s: %s", url, e)
        return None
