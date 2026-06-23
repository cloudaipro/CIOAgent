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
# Market-data type for quote(): 1=real-time (needs a live US-equity subscription),
# 2=frozen, 3=delayed ~15-20min (free), 4=delayed-frozen. Default 3 so the call
# always returns *something* for an account without a real-time sub; the Ticker
# reports the type IBKR actually served, so a delayed quote is labelled delayed.
_DEFAULT_MKTDATA_TYPE = 3
# quote() polls the streaming tickers until a live field arrives or this many
# seconds elapse (then it falls back to whatever did arrive, e.g. prior close).
# reqMktData streams, so we own the wait — no open-ended block. Tunable via
# CIO_IBKR_QUOTE_TIMEOUT for slow/feed-starved sessions.
_QUOTE_TIMEOUT = 6.0
_QUOTE_POLL = 0.25


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


def _quote_client_id() -> int:
    """Client id for market-data quotes — DISTINCT from the portfolio-sync id so a
    quote and a sync (or the operator's live app, which typically holds the default
    id) can connect concurrently without IBKR error 326 'client id already in use'.
    Defaults to the base id + 100; override with CIO_IBKR_QUOTE_CLIENT_ID."""
    raw = (os.getenv("CIO_IBKR_QUOTE_CLIENT_ID") or "").strip()
    return int(raw) if raw.isdigit() else _client_id() + 100


def _ib_factory(client_id: int | None = None):
    """Connected, read-only IB instance (lazy import keeps the dependency
    optional — CIOAgent imports fine without ib_async installed). ``client_id``
    overrides the default sync id (quotes pass a distinct one)."""
    from ib_async import IB

    host, port = tws_endpoint()
    ib = IB()
    ib.connect(host, port, clientId=client_id if client_id is not None else _client_id(),
               timeout=_TIMEOUT, readonly=True)
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


def _normalize_fills(fills) -> list[dict]:
    """ib_async Fill list -> list of normalized fill dicts for the trade ledger.

    Each fill carries: execution_id, symbol, side (BOT/SLD), price, qty,
    time (datetime). We translate side to an open/close signal: BOT = open (buy),
    SLD = close (sell). We cannot know the paired open for a SLD without tracking
    state — so we record_closed with best-effort entry data when the fill is SLD
    and we have a matching open in the ledger, or open_trade when it's BOT.
    """
    out = []
    for f in fills or []:
        try:
            exe = f.execution
            sym = (getattr(exe, "symbol", None) or
                   getattr(getattr(f, "contract", None), "symbol", "") or "").strip().upper()
            if not sym:
                continue
            out.append({
                "exec_id": str(getattr(exe, "execId", "") or ""),
                "symbol": sym,
                "side": str(getattr(exe, "side", "") or "").upper(),  # BOT or SLD
                "price": float(getattr(exe, "price", 0) or 0),
                "qty": float(getattr(exe, "shares", 0) or 0),
                "time": str(getattr(exe, "time", "") or ""),
            })
        except Exception:
            continue
    return out


def _exec_id_logged(exec_id: str, db_path) -> bool:
    """True if this exec_id already has a row in the trades ledger (idempotency)."""
    if not exec_id:
        return False
    try:
        from ..alpha import trades as trade_ledger
        conn = trade_ledger._conn(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM trades WHERE note LIKE ?",
                (f"%exec_id:{exec_id}%",),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


def _seed_positions(positions: list[dict], db_path, regime_status: str, style: str) -> int:
    """Seed the trade ledger with open IBKR positions BEFORE processing fills.

    For each position that has no existing 'open' ledger row, insert one using
    avg_cost as entry_px and today's date as a proxy entry_date.  This ensures
    that subsequent SLD fills can match an open row and compute a *real* pct
    instead of falling through to record_orphan_sell.

    Idempotent: skips symbols that already have an open row in the ledger.
    IBKR-readonly: only writes to the local SQLite ledger, never to IBKR.

    Returns the count of positions seeded.
    """
    from ..alpha import trades as trade_ledger
    from datetime import date

    seeded = 0
    today = date.today().isoformat()
    for pos in positions:
        sym = pos.get("symbol", "").strip().upper()
        if not sym:
            continue
        avg_cost = pos.get("avg_cost")
        try:
            entry_px = float(avg_cost)
        except (TypeError, ValueError):
            continue
        if entry_px <= 0:
            continue
        # Idempotency check: skip if any open row already exists for this symbol.
        existing = [t for t in trade_ledger.list_open(db_path=db_path)
                    if t.get("ticker") == sym]
        if existing:
            continue
        try:
            trade_ledger.open_trade(
                sym, today, entry_px,
                qty=pos.get("quantity"),
                style=style,
                regime_at_entry=regime_status,
                layer_scores=None,
                note="ibkr_position_seed",
                db_path=db_path,
            )
            seeded += 1
            log.debug("ibkr seed: opened %s @ %.4f", sym, entry_px)
        except Exception as e:
            log.debug("ibkr seed: failed to seed %s: %s", sym, e)
    return seeded


def _open_if_absent(trade_ledger, symbol, date_str, price, *, qty, style,
                    regime_status, note, db_path) -> bool:
    """Open a BOT lot only when the symbol has no open lot yet — the position seed
    (or a prior BOT) already represents it, so a second open would double-count
    (Richard pass-3 finding). Returns True if a row was written, False if skipped.
    Never raises."""
    try:
        sym = str(symbol).upper()
        if any(t.get("ticker") == sym
               for t in trade_ledger.list_open(db_path=db_path)):
            return False
        trade_ledger.open_trade(sym, date_str, price, qty=qty, style=style,
                                regime_at_entry=regime_status, layer_scores=None,
                                note=note, db_path=db_path)
        return True
    except Exception as e:
        log.debug("ibkr _open_if_absent failed for %s: %s", symbol, e)
        return False


def sync_trades(db_path=None) -> dict:
    """Read IBKR fills and record them into the swing-trade ledger.

    Safety: read-only connect (readonly=True); we only write to the *local*
    SQLite ledger (cio.alpha.trades), never to IBKR.

    Idempotency: each fill is keyed on its execution_id (stored in the note
    field as 'exec_id:<id>'). Re-running never duplicates a row.  Positions
    are seeded once (``_seed_positions`` skips symbols already in the ledger).

    Layer scores: not available from IBKR fills — stored as None on backfill.
    Regime at entry: from regime.evaluate() at sync time (best available proxy).
    Style: from regime.position_style at sync time.

    Returns: {synced: [fill-dicts], skipped: int, seeded: int, error: str|None}
    """
    if not enabled():
        return {"synced": [], "skipped": 0, "seeded": 0, "error": None}

    from ..alpha import regime as regime_mod, trades as trade_ledger

    if db_path is None:
        from .. import db as cio_db
        db_path = cio_db.DB_PATH

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ib = None
    fills = []
    positions = []
    try:
        ib = _ib_factory()
        fills = list(ib.fills() or [])
        # Fetch current portfolio positions for the pre-seed step.
        accts = ib.managedAccounts()
        acct = (os.getenv("CIO_IBKR_ACCOUNT") or "").strip() or (
            accts[0] if accts else None)
        if acct:
            positions = _normalize_positions(ib.portfolio(acct))
    except Exception as e:
        log.warning("ibkr sync_trades: fills() failed: %s", e)
        return {"synced": [], "skipped": 0, "seeded": 0, "error": str(e)}
    finally:
        try:
            if ib is not None and ib.isConnected():
                ib.disconnect()
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()

    normalized = _normalize_fills(fills)
    # Regime context at sync time — best-effort proxy for regime_at_entry.
    try:
        reg = regime_mod.evaluate()
        regime_status = (reg or {}).get("status", "UNKNOWN")
        style_info = regime_mod.position_style(regime_status)
        style = style_info.get("style", "neutral")
    except Exception:
        regime_status, style = "UNKNOWN", "neutral"

    # Seed open ledger rows from current positions BEFORE processing fills so
    # that SLD fills can match a seeded open and compute a real pct.
    seeded = _seed_positions(positions, db_path, regime_status, style)

    synced: list[dict] = []
    skipped = 0
    for f in normalized:
        exec_id = f["exec_id"]
        if _exec_id_logged(exec_id, db_path):
            skipped += 1
            continue
        note = f"exec_id:{exec_id}" if exec_id else "ibkr_fill"
        try:
            date_str = (f["time"] or "")[:10]  # first 10 chars = YYYY-MM-DD
            if not date_str:
                from datetime import date
                date_str = date.today().isoformat()
            if f["side"] == "BOT":
                _open_if_absent(
                    trade_ledger, f["symbol"], date_str, f["price"],
                    qty=f["qty"], style=style, regime_status=regime_status,
                    note=note, db_path=db_path,
                )
            elif f["side"] == "SLD":
                # Attempt to match an existing open position on the same symbol.
                open_trades = [t for t in trade_ledger.list_open(db_path=db_path)
                               if t.get("ticker") == f["symbol"].upper()]
                if open_trades:
                    # Close the oldest open position.
                    trade_ledger.close_trade(
                        open_trades[-1]["id"], date_str, f["price"],
                        note=note, db_path=db_path,
                    )
                else:
                    # No matching open (entry predates the ledger / first backfill).
                    # Record as an ORPHAN, not a fabricated pct=0 closed trade — a
                    # zero-pct close would dilute the expectancy KPI's win/loss rates
                    # (Richard finding). Orphans are kept for reconciliation but
                    # excluded from expectancy until a real entry cost basis exists.
                    trade_ledger.record_orphan_sell(
                        f["symbol"], date_str, f["price"],
                        qty=f["qty"], style=style,
                        regime_at_entry=regime_status, note=note, db_path=db_path,
                    )
            synced.append(f)
        except Exception as e:
            log.debug("ibkr sync_trades: failed to log fill %s: %s", exec_id, e)
    return {"synced": synced, "skipped": skipped, "seeded": seeded, "error": None}


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


# --- market-data quotes (read-only; extended-hours last price) ---------------
def _mktdata_type() -> int:
    raw = (os.getenv("CIO_IBKR_MKTDATA_TYPE") or "").strip()
    return int(raw) if raw in ("1", "2", "3", "4") else _DEFAULT_MKTDATA_TYPE


def _quote_timeout() -> float:
    raw = (os.getenv("CIO_IBKR_QUOTE_TIMEOUT") or "").strip()
    try:
        v = float(raw)
        return v if v > 0 else _QUOTE_TIMEOUT
    except ValueError:
        return _QUOTE_TIMEOUT


def _ticker_ready(tk) -> bool:
    """True once a tradable field (last, or both bid & ask) has arrived, so the
    poll can stop early instead of waiting the full timeout. Prior close alone is
    NOT ready — we wait for a live/extended print up to the deadline, then fall
    back to the close in ``_normalize_tickers``."""
    if _fnum(getattr(tk, "last", None), price=True) is not None:
        return True
    return (_fnum(getattr(tk, "bid", None), price=True) is not None
            and _fnum(getattr(tk, "ask", None), price=True) is not None)


def _fnum(x, *, price=False):
    """float(x) or None for None / NaN / non-numeric. IBKR signals 'no data' with
    NaN *and* the sentinel -1 on price/size fields, so with ``price=True`` any
    value <= 0 is also treated as missing (must never leak in as a real price)."""
    import math
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    if price and f <= 0:
        return None
    return f


def _normalize_tickers(tickers, default_mdt: int) -> dict:
    """ib_async Ticker list -> {SYM: {price, price_source, bid, ask, prev_close,
    market_data_type, delayed, halted}}. Pure (no I/O), unit-testable without a TWS.

    `price` resolves last trade -> bid/ask midpoint -> prior close, and
    `price_source` ('last'|'midpoint'|'close') records which, so callers can tell a
    REAL extended-hours/live print from a mere prior-close fallback (IBKR returns
    only the close, last/bid/ask = -1, when no live tick is on the line — e.g.
    market closed or a delayed feed with nothing streaming). Symbols with no usable
    price at all are omitted.
    """
    out: dict = {}
    for tk in tickers or []:
        sym = (getattr(getattr(tk, "contract", None), "symbol", "") or "").strip().upper()
        if not sym:
            continue
        last = _fnum(getattr(tk, "last", None), price=True)
        bid = _fnum(getattr(tk, "bid", None), price=True)
        ask = _fnum(getattr(tk, "ask", None), price=True)
        prev_close = _fnum(getattr(tk, "close", None), price=True)
        if last is not None:
            price, price_source = last, "last"
        elif bid is not None and ask is not None:
            price, price_source = (bid + ask) / 2, "midpoint"
        elif prev_close is not None:
            price, price_source = prev_close, "close"
        else:
            continue
        mdt = int(_fnum(getattr(tk, "marketDataType", None)) or default_mdt)
        out[sym] = {
            "price": price,
            "price_source": price_source,
            "bid": bid,
            "ask": ask,
            "prev_close": prev_close,
            "market_data_type": mdt,
            "delayed": mdt in (3, 4),
            "halted": bool(_fnum(getattr(tk, "halted", 0))),
        }
    return out


def quote(symbols) -> dict:
    """Last / extended-hours price for one or more US-equity symbols via the TWS
    API (read-only market-data request — never places an order).

    `symbols`: a symbol string or an iterable of them. Index symbols (leading
    '^', e.g. ^IXIC) are skipped: TWS needs a separate index data line and
    yfinance covers the benchmark.

    Returns ``{SYM: {...}}`` (see ``_normalize_tickers``) for symbols that
    resolved; unresolved ones are omitted. ``{}`` when IBKR is disabled,
    unreachable, ib_async is not installed, or nothing came back. Never raises.

    Connects, reads, disconnects per call (same rationale as ``snapshot``).
    ``market_data_type`` on each quote reflects what IBKR actually served (3/4 =
    delayed ~15-20min), so a delayed quote stays honestly labelled even when a live
    type was requested.

    The blocking ib_async work runs in a dedicated worker thread with its own fresh
    event loop, so this is safe to call from a thread that already has a running
    event loop (e.g. the async agent tool path) — a bare ``new_event_loop`` there
    raises "This event loop is already running".
    """
    if isinstance(symbols, str):
        symbols = [symbols]
    syms = [s.strip().upper() for s in symbols
            if s and not s.strip().startswith("^")]
    if not syms or not enabled():
        return {}
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_quote_blocking, syms).result()


def _quote_blocking(syms: list) -> dict:
    """The synchronous reqMktData read for ``quote`` — connect, stream, poll,
    cancel, disconnect — in its own fresh event loop. Must run on a thread with no
    already-running loop (``quote`` guarantees this by offloading to a worker)."""
    import time
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    ib = None
    qualified: list = []
    try:
        from ib_async import Stock
        mdt = _mktdata_type()
        ib = _ib_factory(client_id=_quote_client_id())
        try:
            ib.reqMarketDataType(mdt)
        except Exception:
            pass
        contracts = [Stock(s, "SMART", "USD") for s in syms]
        qualified = list(ib.qualifyContracts(*contracts) or [])
        if not qualified:
            return {}
        # Stream all symbols at once, then poll a single bounded window — one wait
        # for the whole batch, not per-symbol. Break early once every ticker has a
        # live field; otherwise fall back to whatever arrived (prior close) at the
        # deadline rather than blocking open-ended like reqTickers.
        tickers = [ib.reqMktData(c, "", False, False) for c in qualified]
        deadline = time.monotonic() + _quote_timeout()
        while time.monotonic() < deadline:
            ib.sleep(_QUOTE_POLL)
            if all(_ticker_ready(t) for t in tickers):
                break
        return _normalize_tickers(tickers, mdt)
    except Exception as e:
        log.warning("ibkr quote failed: %s", e)
        return {}
    finally:
        for c in qualified:
            try:
                ib.cancelMktData(c)
            except Exception:
                pass
        try:
            if ib is not None and ib.isConnected():
                ib.disconnect()
        except Exception:
            pass
        asyncio.set_event_loop(None)
        loop.close()
