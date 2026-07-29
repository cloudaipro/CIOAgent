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


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# Per-host cooldown latch. A 429 means the upstream has already decided we are
# over budget; the committee bundles symbols back-to-back, so without a latch one
# throttled host produces one 429 per symbol (5 in a row on 2026-07-29). After a
# retry-exhausted 429 the host is skipped outright for CIO_HTTP_COOLDOWN seconds.
_cooldown: dict[str, float] = {}
_cooldown_lock = threading.Lock()


def _host(url: str) -> str:
    part = url.split("://", 1)[-1]
    return part.split("/", 1)[0]


def _cooling(host: str) -> float:
    """Seconds left on *host*'s cooldown (0.0 = free to call)."""
    with _cooldown_lock:
        until = _cooldown.get(host, 0.0)
        left = until - time.monotonic()
        if left <= 0:
            _cooldown.pop(host, None)
            return 0.0
        return left


def _latch(host: str, seconds: float) -> None:
    with _cooldown_lock:
        _cooldown[host] = max(_cooldown.get(host, 0.0), time.monotonic() + seconds)


def _retry_after(exc) -> float | None:
    """Honour a Retry-After header when the upstream sends one."""
    resp = getattr(exc, "response", None)
    raw = (resp.headers.get("retry-after") if resp is not None else None)
    try:
        return max(0.0, float(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def get_json(url, *, params=None, headers=None, timeout=None, limiter=None,
             retries=None):
    """GET *url* and return parsed JSON, or ``None`` on any error.

    Never raises — a flaky network or a rate-limit response degrades to ``None``
    and the caller falls back to its empty result. *timeout* defaults to
    ``CIO_HTTP_TIMEOUT`` (15s) when not given explicitly.

    Transient failures (429, 5xx, timeouts, and the non-JSON body a throttled
    host returns instead of an error status) are retried with exponential backoff
    up to ``CIO_HTTP_RETRIES`` extra attempts. A host that still answers 429 after
    that is latched into a cooldown so the remaining callers fail fast instead of
    deepening the throttle.
    """
    import httpx

    if timeout is None:
        timeout = _default_timeout()
    attempts = 1 + max(0, _int_env("CIO_HTTP_RETRIES", 2) if retries is None
                       else int(retries))
    cooldown_s = float(_int_env("CIO_HTTP_COOLDOWN", 300))
    host = _host(url)

    left = _cooling(host)
    if left > 0:
        log.warning("data.get_json skipped %s: host cooling down %.0fs (rate limited)",
                    url, left)
        return None

    backoff = 1.0
    for attempt in range(attempts):
        try:
            if limiter is not None:
                limiter.wait()
            resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            # A throttled host sometimes answers 200 with an HTML/plain body,
            # which surfaces as a JSON decode error — retry those like a 429.
            decode_err = isinstance(e, ValueError) and status is None
            transient = (status in (408, 425, 429) or (status or 0) >= 500
                         or decode_err
                         or isinstance(e, (httpx.TimeoutException,
                                           httpx.NetworkError)))
            last = attempt == attempts - 1
            if not transient or last:
                if status == 429:
                    _latch(host, cooldown_s)
                    log.warning("data.get_json failed for %s: %s "
                                "(rate limited; %s cooling down %.0fs)",
                                url, e, host, cooldown_s)
                else:
                    log.warning("data.get_json failed for %s: %s", url, e)
                return None
            delay = _retry_after(e) or backoff
            log.info("data.get_json retrying %s in %.1fs (attempt %d/%d): %s",
                     url, delay, attempt + 1, attempts, e)
            time.sleep(delay)
            backoff *= 2
    return None
