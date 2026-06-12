"""Interactive Brokers TWS API client (live account, read-only).

CIOAgent talks to IBKR through the **TWS API** socket of a locally-running
Trader Workstation or IB Gateway, using `ib_async` (the community continuation
of ib_insync — BSD-2, github.com/ib-api-reloaded/ib_async; safety-reviewed
2026-06-12: its only network connection is the user-configured TWS socket).
The operator stays logged in to TWS/Gateway; this module never sees or stores
credentials, it only reads portfolio state from the session.

Setup (one-time):
  1. TWS:  File → Global Configuration → API → Settings:
       - Enable ActiveX and Socket Clients
       - Read-Only API (recommended — blocks orders at the TWS level)
       - note the socket port (TWS live 7496, paper 7497;
         IB Gateway live 4001, paper 4002)
  2. export CIO_IBKR_TWS=127.0.0.1:7496

Enabled by CIO_IBKR_TWS. Unset = disabled: every function returns its empty
value with no network call, so the test suite / CI stay offline.
CIO_IBKR_ACCOUNT pins one account id; default is the first managed account.
CIO_IBKR_CLIENT_ID sets the API client id (default 17) — must be unique among
clients connected to the same TWS.

Read-only by design: we connect with `readonly=True` (the API session itself
refuses order placement) and wrap no order/transfer endpoints, matching the
operator decision that the bot never trades.
"""
from __future__ import annotations

import asyncio
import logging
import os

log = logging.getLogger(__name__)

_TIMEOUT = 10.0
_DEFAULT_CLIENT_ID = 17


def tws_endpoint() -> tuple[str, int] | None:
    """(host, port) from CIO_IBKR_TWS ('host:port' or bare 'port'), or None."""
    raw = (os.getenv("CIO_IBKR_TWS") or "").strip()
    if not raw:
        return None
    if ":" in raw:
        host, _, port = raw.rpartition(":")
    else:
        host, port = "127.0.0.1", raw
    try:
        return (host or "127.0.0.1"), int(port)
    except ValueError:
        log.warning("ibkr: bad CIO_IBKR_TWS value %r (want host:port)", raw)
        return None


def enabled() -> bool:
    return tws_endpoint() is not None


def _client_id() -> int:
    raw = (os.getenv("CIO_IBKR_CLIENT_ID") or "").strip()
    return int(raw) if raw.isdigit() else _DEFAULT_CLIENT_ID


def _ib_factory():
    """Connected, read-only IB instance (lazy import keeps the dependency
    optional — CIOAgent imports fine without ib_async installed)."""
    from ib_async import IB

    host, port = tws_endpoint()
    ib = IB()
    ib.connect(host, port, clientId=_client_id(), timeout=_TIMEOUT, readonly=True)
    return ib


def _normalize_positions(items) -> list[dict]:
    """ib_async PortfolioItem list -> the snapshot's position dicts."""
    out = []
    for it in items:
        qty = float(it.position or 0)
        sym = (getattr(it.contract, "symbol", "") or "").strip().upper()
        if not sym or qty == 0:
            continue
        out.append({
            "symbol": sym,
            "quantity": qty,
            "avg_cost": it.averageCost,
            "last_price": it.marketPrice,
            "market_value": it.marketValue,
            "unrealized_pl": it.unrealizedPNL,
            "currency": (getattr(it.contract, "currency", "") or "USD").upper(),
        })
    return out


def _cash_balances(ib, acct: str) -> dict[str, float]:
    """Settled cash per currency from account values (BASE aggregate skipped)."""
    out: dict[str, float] = {}
    for v in ib.accountValues(acct):
        if v.tag != "TotalCashBalance" or v.currency.upper() == "BASE":
            continue
        try:
            out[v.currency.upper()] = float(v.value)
        except (TypeError, ValueError):
            continue
    return out


def snapshot() -> dict | None:
    """One-call live view: {account, positions: [...], cash: {...}}.
    None when disabled or TWS/Gateway is unreachable. Never raises.

    Connects, reads, disconnects per call — the dashboard sync is a manual,
    occasional action, and a persistent socket would hold one of TWS's limited
    client-id slots for nothing. The dashboard serves from worker threads, so
    a fresh event loop is installed for the duration of the (synchronous)
    ib_async calls and torn down after.
    """
    if not enabled():
        return None
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ib = None
    try:
        ib = _ib_factory()
        accts = ib.managedAccounts()
        acct = (os.getenv("CIO_IBKR_ACCOUNT") or "").strip() or (
            accts[0] if accts else None)
        if not acct:
            log.warning("ibkr snapshot: no managed account visible")
            return None
        return {
            "account": acct,
            "positions": _normalize_positions(ib.portfolio(acct)),
            "cash": _cash_balances(ib, acct),
        }
    except Exception as e:
        log.warning("ibkr snapshot failed: %s", e)
        return None
    finally:
        try:
            if ib is not None and ib.isConnected():
                ib.disconnect()
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()
