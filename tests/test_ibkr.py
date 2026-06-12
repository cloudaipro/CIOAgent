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


@pytest.fixture
def fake_tws(monkeypatch):
    stub = StubIB()
    monkeypatch.setenv("CIO_IBKR_TWS", "127.0.0.1:7496")
    monkeypatch.delenv("CIO_IBKR_ACCOUNT", raising=False)
    monkeypatch.setattr(ibkr, "_ib_factory", lambda: stub)
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
