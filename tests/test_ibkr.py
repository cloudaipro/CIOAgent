"""Offline tests for cio.data.ibkr (TWS API client via ib_async) and
portfolio.sync_ibkr. No network and no TWS: the env gate keeps the client
dormant, and the happy paths monkeypatch the IB factory with a stub."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cio import db, portfolio
from cio.data import ibkr


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh SQLite DB with AAPL (10) and MSFT (5) BUY transactions."""
    db_path = tmp_path / "test_cio.db"
    conn = db.connect(db_path)
    with conn:
        conn.executemany(
            "INSERT INTO transactions "
            "(txn_date, symbol, action, quantity, price, fees, currency) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                ("2026-01-10", "AAPL", "BUY", 10, 185.0, 0, "USD"),
                ("2026-01-10", "MSFT", "BUY", 5, 420.0, 0, "USD"),
            ],
        )
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# env gate — disabled by default, no network call
# ---------------------------------------------------------------------------

def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("CIO_IBKR_TWS", raising=False)
    assert not ibkr.enabled()
    assert ibkr.snapshot() is None


def test_tws_endpoint_parsing(monkeypatch):
    monkeypatch.setenv("CIO_IBKR_TWS", "127.0.0.1:7496")
    assert ibkr.tws_endpoint() == ("127.0.0.1", 7496)
    monkeypatch.setenv("CIO_IBKR_TWS", "4001")           # bare port
    assert ibkr.tws_endpoint() == ("127.0.0.1", 4001)
    monkeypatch.setenv("CIO_IBKR_TWS", "not-a-port")     # garbage -> disabled
    assert ibkr.tws_endpoint() is None
    assert not ibkr.enabled()


def test_client_id_env(monkeypatch):
    monkeypatch.delenv("CIO_IBKR_CLIENT_ID", raising=False)
    assert ibkr._client_id() == 17
    monkeypatch.setenv("CIO_IBKR_CLIENT_ID", "42")
    assert ibkr._client_id() == 42


# ---------------------------------------------------------------------------
# stub IB — mirrors the ib_async surface snapshot() touches
# ---------------------------------------------------------------------------

def _item(sym, qty, avg, mkt, mval, upl, ccy="USD"):
    return SimpleNamespace(
        contract=SimpleNamespace(symbol=sym, currency=ccy),
        position=qty, averageCost=avg, marketPrice=mkt,
        marketValue=mval, unrealizedPNL=upl,
    )


class StubIB:
    def __init__(self, accounts=("U1234567",)):
        self._accounts = list(accounts)
        self.disconnected = False

    def managedAccounts(self):
        return self._accounts

    def portfolio(self, acct):
        return [
            _item("AAPL", 10.0, 185.0, 201.5, 2015.0, 165.0),
            _item("NVDA", 3.0, 900.0, 950.0, 2850.0, 150.0),
            _item("GONE", 0.0, 0.0, 1.0, 0.0, 0.0),    # closed — skipped
        ]

    def accountValues(self, acct):
        return [
            SimpleNamespace(tag="TotalCashBalance", currency="BASE", value="9999"),
            SimpleNamespace(tag="TotalCashBalance", currency="USD", value="5000.25"),
            SimpleNamespace(tag="NetLiquidation", currency="USD", value="123"),
        ]

    def isConnected(self):
        return not self.disconnected

    def disconnect(self):
        self.disconnected = True

    # --- market-data surface used by ibkr.quote() (streaming + bounded poll) ---
    def reqMarketDataType(self, mdt):
        self.requested_mdt = mdt

    def qualifyContracts(self, *contracts):
        return list(contracts)

    def reqMktData(self, contract, *a, **k):
        # One ticker per contract; symbol carried on contract.symbol. Default
        # ticker has a live `last` so the poll loop is ready immediately (no wait).
        return getattr(self, "_tickers", {}).get(getattr(contract, "symbol", "")) \
            or SimpleNamespace(contract=contract, last=100.0, bid=99.0, ask=101.0,
                               close=98.0, marketDataType=3, halted=0)

    def sleep(self, *a):
        pass

    def cancelMktData(self, contract):
        self.cancelled = getattr(self, "cancelled", [])
        self.cancelled.append(getattr(contract, "symbol", ""))


@pytest.fixture
def fake_tws(monkeypatch):
    stub = StubIB()
    monkeypatch.setenv("CIO_IBKR_TWS", "127.0.0.1:7496")
    monkeypatch.delenv("CIO_IBKR_ACCOUNT", raising=False)
    monkeypatch.setattr(ibkr, "_ib_factory", lambda *a, **k: stub)
    return stub


def test_snapshot_normalizes_positions_and_cash(fake_tws):
    snap = ibkr.snapshot()
    assert snap["account"] == "U1234567"
    syms = {p["symbol"]: p for p in snap["positions"]}
    assert set(syms) == {"AAPL", "NVDA"}                 # zero-qty row dropped
    assert syms["AAPL"]["last_price"] == 201.5
    assert syms["AAPL"]["quantity"] == 10.0
    assert snap["cash"] == {"USD": 5000.25}              # BASE + non-cash tags skipped
    assert fake_tws.disconnected                          # socket released after call


def test_account_pinned_by_env(fake_tws, monkeypatch):
    monkeypatch.setenv("CIO_IBKR_ACCOUNT", "U7654321")
    assert ibkr.snapshot()["account"] == "U7654321"


def test_snapshot_none_when_connect_fails(monkeypatch):
    monkeypatch.setenv("CIO_IBKR_TWS", "127.0.0.1:7496")

    def boom():
        raise ConnectionRefusedError("TWS not running")
    monkeypatch.setattr(ibkr, "_ib_factory", boom)
    assert ibkr.snapshot() is None                        # never raises


def test_snapshot_none_when_no_accounts(monkeypatch):
    monkeypatch.setenv("CIO_IBKR_TWS", "127.0.0.1:7496")
    monkeypatch.delenv("CIO_IBKR_ACCOUNT", raising=False)
    monkeypatch.setattr(ibkr, "_ib_factory", lambda: StubIB(accounts=()))
    assert ibkr.snapshot() is None


# ---------------------------------------------------------------------------
# market-data quote() — extended-hours / last price (read-only)
# ---------------------------------------------------------------------------

def _tk(sym, *, last=None, bid=None, ask=None, close=None, mdt=3, halted=0):
    return SimpleNamespace(contract=SimpleNamespace(symbol=sym), last=last, bid=bid,
                           ask=ask, close=close, marketDataType=mdt, halted=halted)


def test_normalize_tickers_last_price_and_delayed_flag():
    """last is the reported price (price_source 'last'); mdt 3/4 -> delayed True."""
    out = ibkr._normalize_tickers([_tk("MU", last=1170.27, close=1211.38, mdt=3)], 3)
    assert out["MU"]["price"] == 1170.27
    assert out["MU"]["price_source"] == "last"
    assert out["MU"]["prev_close"] == 1211.38
    assert out["MU"]["market_data_type"] == 3
    assert out["MU"]["delayed"] is True


def test_normalize_tickers_live_type_not_delayed():
    out = ibkr._normalize_tickers([_tk("AAPL", last=201.5, mdt=1)], 1)
    assert out["AAPL"]["delayed"] is False


def test_normalize_tickers_midpoint_then_close_fallback():
    """No last -> bid/ask midpoint; no last+no quote -> prior close (flagged
    'close'); nothing -> dropped. price_source records which."""
    nan = float("nan")
    out = ibkr._normalize_tickers([
        _tk("MID", last=nan, bid=10.0, ask=12.0, close=9.0),   # -> midpoint 11.0
        _tk("CLS", last=nan, bid=nan, ask=nan, close=9.0),     # -> close 9.0
        _tk("DEAD", last=nan, bid=nan, ask=nan, close=nan),    # -> dropped
    ], 3)
    assert out["MID"]["price"] == 11.0 and out["MID"]["price_source"] == "midpoint"
    assert out["CLS"]["price"] == 9.0 and out["CLS"]["price_source"] == "close"
    assert "DEAD" not in out


def test_normalize_tickers_minus_one_sentinel_is_missing():
    """IBKR sends -1 on price fields when no live tick is on the line (market
    closed / nothing streaming). It must NOT leak in as a real price; only the
    valid `close` survives, flagged price_source 'close' (regression: a live test
    against Gateway at 01:40 ET returned last/bid/ask = -1)."""
    out = ibkr._normalize_tickers(
        [_tk("MU", last=-1.0, bid=-1.0, ask=-1.0, close=1211.38, mdt=3)], 3)
    assert out["MU"]["price"] == 1211.38
    assert out["MU"]["price_source"] == "close"
    assert out["MU"]["bid"] is None and out["MU"]["ask"] is None


def test_quote_disabled_returns_empty(monkeypatch):
    monkeypatch.delenv("CIO_IBKR_TWS", raising=False)
    assert ibkr.quote("MU") == {}


def test_quote_index_symbols_skipped(fake_tws):
    """Leading-'^' index symbols are skipped (TWS needs a separate index line)."""
    assert ibkr.quote("^IXIC") == {}


def test_quote_happy_path_and_teardown(fake_tws, monkeypatch):
    """End-to-end glue: a stubbed ib_async module + StubIB returns a normalized
    quote and the socket is released afterwards."""
    import sys
    fake_mod = SimpleNamespace(Stock=lambda sym, exch, ccy: SimpleNamespace(symbol=sym))
    monkeypatch.setitem(sys.modules, "ib_async", fake_mod)
    fake_tws._tickers = {"MU": _tk("MU", last=1170.27, close=1211.38, mdt=3)}
    out = ibkr.quote("MU")
    assert out["MU"]["price"] == 1170.27
    assert out["MU"]["price_source"] == "last"
    assert out["MU"]["delayed"] is True
    assert fake_tws.requested_mdt == 3
    assert fake_tws.cancelled == ["MU"]           # market-data line cancelled
    assert fake_tws.disconnected                  # socket released after the call


def test_quote_batches_multiple_symbols_one_connect(fake_tws):
    """A list of symbols streams in one connect and returns all of them; the index
    symbol is dropped. One socket for the batch, not one per symbol."""
    fake_tws._tickers = {
        "MU": _tk("MU", last=1170.27, close=1211.38, mdt=3),
        "AAPL": _tk("AAPL", last=297.0, close=297.01, mdt=3),
    }
    out = ibkr.quote(["MU", "AAPL", "^IXIC"])
    assert set(out) == {"MU", "AAPL"}             # ^IXIC skipped
    assert out["MU"]["price"] == 1170.27 and out["AAPL"]["price"] == 297.0
    assert set(fake_tws.cancelled) == {"MU", "AAPL"}


def test_quote_default_mktdata_type_is_delayed(monkeypatch):
    monkeypatch.delenv("CIO_IBKR_MKTDATA_TYPE", raising=False)
    assert ibkr._mktdata_type() == 3              # free delayed by default
    monkeypatch.setenv("CIO_IBKR_MKTDATA_TYPE", "1")
    assert ibkr._mktdata_type() == 1              # live for subscribers


def test_quote_client_id_distinct_from_sync(monkeypatch):
    """Quotes use a DISTINCT client id (base+100) so they never collide with the
    portfolio-sync id / the operator's live app (IBKR error 326). Overridable."""
    monkeypatch.delenv("CIO_IBKR_QUOTE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CIO_IBKR_CLIENT_ID", raising=False)
    assert ibkr._client_id() == 17
    assert ibkr._quote_client_id() == 117           # base 17 + 100, never == sync id
    monkeypatch.setenv("CIO_IBKR_CLIENT_ID", "5")
    assert ibkr._quote_client_id() == 105
    monkeypatch.setenv("CIO_IBKR_QUOTE_CLIENT_ID", "42")
    assert ibkr._quote_client_id() == 42            # explicit override wins


def test_quote_timeout_env_and_default(monkeypatch):
    monkeypatch.delenv("CIO_IBKR_QUOTE_TIMEOUT", raising=False)
    assert ibkr._quote_timeout() == 6.0
    monkeypatch.setenv("CIO_IBKR_QUOTE_TIMEOUT", "2.5")
    assert ibkr._quote_timeout() == 2.5
    monkeypatch.setenv("CIO_IBKR_QUOTE_TIMEOUT", "junk")   # bad -> default
    assert ibkr._quote_timeout() == 6.0


def test_ticker_ready_waits_for_live_field():
    """last (or both bid&ask) -> ready; prior close alone or -1 sentinels -> not."""
    assert ibkr._ticker_ready(_tk("X", last=10.0)) is True
    assert ibkr._ticker_ready(_tk("X", bid=9.0, ask=11.0)) is True
    assert ibkr._ticker_ready(_tk("X", last=-1.0, bid=-1.0, ask=-1.0, close=9.0)) is False
    assert ibkr._ticker_ready(_tk("X", close=9.0)) is False


# ---------------------------------------------------------------------------
# portfolio.sync_ibkr — price writes + drift report
# ---------------------------------------------------------------------------

def test_sync_ibkr_writes_prices_and_reports_drift(tmp_db):
    snap = {
        "account": "U1234567",
        "positions": [
            {"symbol": "AAPL", "quantity": 10.0, "last_price": 201.5},   # matches
            {"symbol": "NVDA", "quantity": 3.0, "last_price": 950.0},    # not local
        ],
        "cash": {"USD": 5000.25},
    }
    res = portfolio.sync_ibkr(db_path=tmp_db, snapshot_fn=lambda: snap)

    assert res["account"] == "U1234567"
    assert {s["symbol"] for s in res["synced"]} == {"AAPL", "NVDA"}
    # AAPL price landed in the prices table
    conn = db.connect(tmp_db)
    latest = portfolio._latest_prices(conn)
    conn.close()
    assert latest["AAPL"] == 201.5
    # drift: NVDA held at IBKR but not locally; MSFT held locally but not at IBKR
    drift = {d["symbol"]: d for d in res["drift"]}
    assert set(drift) == {"NVDA", "MSFT"}
    assert drift["MSFT"]["ibkr_qty"] == 0.0 and drift["MSFT"]["local_qty"] == 5.0
    assert drift["NVDA"]["local_qty"] == 0.0


def test_align_with_ibkr_rebuilds_book(tmp_db):
    snap = {
        "account": "U1234567",
        "positions": [
            {"symbol": "MU", "quantity": 1.0, "avg_cost": 120.0,
             "last_price": 123.45, "currency": "USD"},
        ],
        "cash": {"USD": 5000.25},
    }
    res = portfolio.align_with_ibkr(db_path=tmp_db, snapshot_fn=lambda: snap)
    assert res == {"account": "U1234567", "wiped": 2, "adopted": 1}

    # Local book now mirrors IBKR exactly — qty AND broker avg cost.
    pos = portfolio.positions(tmp_db)
    assert pos["symbol"].tolist() == ["MU"]
    row = pos.iloc[0]
    assert row["quantity"] == 1.0
    assert row["avg_cost"] == 120.0
    assert row["last_price"] == 123.45   # mark written to prices table
    # And the next sync reports zero drift.
    res2 = portfolio.sync_ibkr(db_path=tmp_db, snapshot_fn=lambda: snap)
    assert res2["drift"] == []


def test_align_with_ibkr_error_when_snapshot_fails(tmp_db):
    res = portfolio.align_with_ibkr(db_path=tmp_db, snapshot_fn=lambda: None)
    assert "error" in res
    # Failure must not touch the existing book.
    assert len(portfolio.positions(tmp_db)) == 2


def test_sync_ibkr_error_when_disabled(tmp_db, monkeypatch):
    monkeypatch.delenv("CIO_IBKR_TWS", raising=False)
    res = portfolio.sync_ibkr(db_path=tmp_db)
    assert "error" in res


def test_sync_ibkr_error_when_snapshot_fails(tmp_db):
    res = portfolio.sync_ibkr(db_path=tmp_db, snapshot_fn=lambda: None)
    assert "error" in res


# ---------------------------------------------------------------------------
# portfolio.reconcile_ibkr — non-destructive quantity reconcile
# ---------------------------------------------------------------------------

def test_reconcile_flat_ibkr_closes_local_and_keeps_history(tmp_db):
    """IBKR holds nothing (the screenshot case): every local position is
    closed by a SELL at avg cost (zero realized P&L), and pre-existing
    realized-P&L history survives."""
    # Add a prior, already-closed AAPL round-trip: realized +200 to protect.
    conn = db.connect(tmp_db)
    with conn:
        conn.executemany(
            "INSERT INTO transactions "
            "(txn_date,symbol,action,quantity,price,fees,currency) VALUES (?,?,?,?,?,?,?)",
            [("2026-03-01", "TSLA", "BUY", 2, 100.0, 0, "USD"),
             ("2026-03-10", "TSLA", "SELL", 2, 200.0, 0, "USD")],  # realized +200
        )
    conn.close()

    snap = {"account": "U1234567", "positions": [], "cash": {}}
    res = portfolio.reconcile_ibkr(db_path=tmp_db, snapshot_fn=lambda: snap)

    assert res["account"] == "U1234567"
    assert {c["symbol"] for c in res["closed"]} == {"AAPL", "MSFT"}
    assert res["opened"] == []
    # Open positions are gone.
    assert len(portfolio.positions(tmp_db)) == 0
    # The closes booked zero realized P&L (true exit price unknown), but the
    # earlier TSLA gain is untouched -> history preserved, not wiped.
    rpl = {r["symbol"]: r["realized_pl"] for r in portfolio.realized_pl(tmp_db).to_dict("records")}
    assert rpl["TSLA"] == 200.0
    assert rpl["AAPL"] == 0.0 and rpl["MSFT"] == 0.0


def test_reconcile_partial_close_and_open(tmp_db):
    """Local AAPL=10/MSFT=5; IBKR AAPL=4 (sell 6), MSFT=5 (match), NVDA=3
    (buy 3 at broker cost)."""
    snap = {
        "account": "U1234567",
        "positions": [
            {"symbol": "AAPL", "quantity": 4.0, "avg_cost": 185.0,
             "last_price": 190.0, "currency": "USD"},
            {"symbol": "MSFT", "quantity": 5.0, "avg_cost": 420.0,
             "last_price": 430.0, "currency": "USD"},
            {"symbol": "NVDA", "quantity": 3.0, "avg_cost": 900.0,
             "last_price": 950.0, "currency": "USD"},
        ],
        "cash": {},
    }
    res = portfolio.reconcile_ibkr(db_path=tmp_db, snapshot_fn=lambda: snap)
    closed = {c["symbol"]: c for c in res["closed"]}
    opened = {o["symbol"]: o for o in res["opened"]}
    assert closed["AAPL"]["sold_qty"] == 6.0
    assert "MSFT" not in closed  # already matches -> no trade
    assert opened["NVDA"]["bought_qty"] == 3.0

    pos = {p["symbol"]: p for p in portfolio.positions(tmp_db).to_dict("records")}
    assert pos["AAPL"]["quantity"] == 4.0
    assert pos["MSFT"]["quantity"] == 5.0
    assert pos["NVDA"]["quantity"] == 3.0
    assert pos["NVDA"]["avg_cost"] == 900.0  # adopted at IBKR avg cost
    # Reconcile is idempotent: a second pass finds no drift.
    res2 = portfolio.reconcile_ibkr(db_path=tmp_db, snapshot_fn=lambda: snap)
    assert res2["closed"] == [] and res2["opened"] == []


def test_reconcile_no_drift_books_nothing(tmp_db):
    snap = {
        "account": "U1234567",
        "positions": [
            {"symbol": "AAPL", "quantity": 10.0, "avg_cost": 185.0,
             "last_price": None, "currency": "USD"},
            {"symbol": "MSFT", "quantity": 5.0, "avg_cost": 420.0,
             "last_price": None, "currency": "USD"},
        ],
        "cash": {},
    }
    res = portfolio.reconcile_ibkr(db_path=tmp_db, snapshot_fn=lambda: snap)
    assert res["closed"] == [] and res["opened"] == []
    pos = portfolio.positions(tmp_db)
    assert sorted(pos["symbol"].tolist()) == ["AAPL", "MSFT"]


def test_reconcile_error_when_snapshot_fails(tmp_db):
    res = portfolio.reconcile_ibkr(db_path=tmp_db, snapshot_fn=lambda: None)
    assert "error" in res
    assert len(portfolio.positions(tmp_db)) == 2  # book untouched on failure


def test_reconcile_error_when_disabled(tmp_db, monkeypatch):
    monkeypatch.delenv("CIO_IBKR_TWS", raising=False)
    res = portfolio.reconcile_ibkr(db_path=tmp_db)
    assert "error" in res
