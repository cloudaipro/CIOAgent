"""IBKR Client Portal Web API — saved-watchlist read (read-only).

The TWS API (cio/data/ibkr.py) cannot read a user's saved watchlists /
Favorites — that capability exists only in IBKR's Client Portal Web API. This
module is a tiny read-only client for exactly that: list the account's
watchlists and read one watchlist's symbols. Holdings still come from TWS;
this covers only the watchlist page's "Sync from IBKR".

It talks to the locally-running **Client Portal Gateway** (a separate IBKR Java
process from TWS; self-signed cert on https://localhost:5000). The operator
authenticates by opening that URL in a browser and logging in — this module
never sees credentials, it only reads the already-authenticated session.

Setup (one-time, in addition to TWS for holdings):
  1. Download "Client Portal Gateway" from IBKR (campus → Web API).
  2. Run it:  bin/run.sh root/conf.yaml   (listens on https://localhost:5000)
  3. Open https://localhost:5000 in a browser and log in.
  4. export CIO_IBKR_CPAPI=https://localhost:5000

Enabled by CIO_IBKR_CPAPI. Unset = disabled: every function returns its empty
value with no network call, so the test suite / CI stay offline.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

_TIMEOUT = 15.0


def gateway_url() -> str | None:
    """Configured gateway base URL (no trailing slash), or None = disabled."""
    url = (os.getenv("CIO_IBKR_CPAPI") or "").strip().rstrip("/")
    return url or None


def enabled() -> bool:
    return gateway_url() is not None


def _request(method: str, path: str):
    """Call the gateway; parsed JSON or None on any error (never raises).

    The gateway ships a self-signed certificate for localhost, so TLS
    verification is disabled — the connection never leaves the machine.
    """
    base = gateway_url()
    if base is None:
        return None
    import httpx

    url = f"{base}/v1/api{path}"
    try:
        resp = httpx.request(method, url, timeout=_TIMEOUT, verify=False)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("ibkr_cpapi %s %s failed: %s", method, path, e)
        return None


def watchlists() -> list[dict] | None:
    """The account's saved watchlists as ``[{"id", "name"}]``.
    None when disabled or the gateway is unreachable/unauthenticated."""
    # The gateway wants a brokerage session initialised before /iserver/*;
    # /iserver/accounts is the cheapest warm-up. Best-effort — ignore its body.
    _request("GET", "/iserver/accounts")
    data = _request("GET", "/iserver/watchlists")
    if not isinstance(data, dict):
        return None
    lists = (data.get("data") or {}).get("user_lists")
    if not isinstance(lists, list):
        return None
    out = []
    for w in lists:
        if isinstance(w, dict) and w.get("id") is not None and w.get("name"):
            out.append({"id": str(w["id"]), "name": str(w["name"])})
    return out


def watchlist_symbols(watchlist_id: str) -> list[str] | None:
    """Tickers in one saved watchlist. None on failure.

    The gateway returns ``{"instruments": [{"ticker": "AAPL", ...}, ...]}``;
    some rows are section dividers without a ticker — those are skipped."""
    data = _request("GET", f"/iserver/watchlist?id={watchlist_id}")
    if not isinstance(data, dict):
        return None
    out: list[str] = []
    for it in (data.get("instruments") or []):
        if not isinstance(it, dict):
            continue
        sym = (it.get("ticker") or it.get("symbol") or "").strip().upper()
        if sym:
            out.append(sym)
    return out


def watchlist_named(name: str) -> dict | None:
    """Resolve a watchlist by (case-insensitive) name to
    ``{"name", "symbols": [...]}``. None when no list matches; the caller can
    call watchlists() itself to show the available names."""
    lists = watchlists()
    if not lists:
        return None
    match = next((w for w in lists if w["name"].strip().lower() == name.strip().lower()),
                 None)
    if match is None:
        return None
    syms = watchlist_symbols(match["id"])
    if syms is None:
        return None
    return {"name": match["name"], "symbols": syms}
