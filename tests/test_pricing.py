"""Offline tests for portfolio.refresh_live_prices (Step 2).

No network calls — all quotes are injected via quote_fn.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Ensure cio is importable regardless of invocation path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cio import db, portfolio


# ---------------------------------------------------------------------------
# Fixture: tmp sqlite db seeded with two buy transactions
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Return a path to a fresh SQLite DB with AAPL and MSFT BUY transactions."""
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
# fake_quote used across tests
# ---------------------------------------------------------------------------

def fake_quote(sym):
    """Returns a valid quote for AAPL (and any symbol != MSFT), None for MSFT."""
    if sym == "MSFT":
        return None
    return {"price": 100.0 + len(sym), "date": "2026-05-29"}


# ---------------------------------------------------------------------------
# Test 1: updated / failed partitioning and DB write
# ---------------------------------------------------------------------------

def test_refresh_updated_and_failed(tmp_db):
    result = portfolio.refresh_live_prices(quote_fn=fake_quote, db_path=tmp_db)

    # Structure
    assert "updated" in result
    assert "failed" in result
    assert "as_of" in result

    updated_syms = [r["symbol"] for r in result["updated"]]
    assert "AAPL" in updated_syms
    assert "MSFT" in result["failed"]

    # Price written to DB
    conn = db.connect(tmp_db)
    latest = portfolio._latest_prices(conn)
    conn.close()
    expected_price = 100.0 + len("AAPL")
    assert latest.get("AAPL") == pytest.approx(expected_price)

    # positions() also reflects the new price
    pos = portfolio.positions(tmp_db)
    aapl_row = pos[pos["symbol"] == "AAPL"]
    assert not aapl_row.empty
    assert aapl_row.iloc[0]["last_price"] == pytest.approx(expected_price)


# ---------------------------------------------------------------------------
# Test 2: lazy coupling — importing cio.portfolio must NOT pull in cio.stock
# ---------------------------------------------------------------------------

def test_lazy_coupling_subprocess():
    """Run in a subprocess so sys.modules starts clean."""
    code = (
        "import sys, os; "
        "sys.path.insert(0, r'" + str(_REPO_ROOT) + "'); "
        "import cio.portfolio; "
        "assert 'cio.stock' not in sys.modules, "
        "'cio.stock was imported at portfolio import time'"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"Subprocess failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}"
    )


# ---------------------------------------------------------------------------
# Test 3: per-symbol exception → captured in failed, loop continues
# ---------------------------------------------------------------------------

def test_exception_in_quote_fn_goes_to_failed(tmp_db):
    """A quote_fn that raises for MSFT should add MSFT to failed; AAPL still updates."""

    def raising_quote(sym):
        if sym == "MSFT":
            raise RuntimeError("network timeout")
        return {"price": 200.0, "date": "2026-05-29"}

    result = portfolio.refresh_live_prices(quote_fn=raising_quote, db_path=tmp_db)

    assert "MSFT" in result["failed"]
    updated_syms = [r["symbol"] for r in result["updated"]]
    assert "AAPL" in updated_syms

    # AAPL price landed in DB
    conn = db.connect(tmp_db)
    latest = portfolio._latest_prices(conn)
    conn.close()
    assert latest.get("AAPL") == pytest.approx(200.0)


# ---------------------------------------------------------------------------
# Test 4: explicit symbols list overrides open-positions default
# ---------------------------------------------------------------------------

def test_explicit_symbols(tmp_db):
    """Passing symbols= overrides the default open-positions lookup."""
    result = portfolio.refresh_live_prices(
        symbols=["AAPL"],
        quote_fn=fake_quote,
        db_path=tmp_db,
    )
    updated_syms = [r["symbol"] for r in result["updated"]]
    assert updated_syms == ["AAPL"]
    # MSFT not requested — not in either list
    assert "MSFT" not in result["failed"]
    assert "MSFT" not in updated_syms


# ---------------------------------------------------------------------------
# Test 5: as_of is a valid ISO date string
# ---------------------------------------------------------------------------

def test_as_of_is_iso_date(tmp_db):
    from datetime import date
    result = portfolio.refresh_live_prices(quote_fn=fake_quote, db_path=tmp_db)
    # Should not raise
    parsed = date.fromisoformat(result["as_of"])
    assert parsed is not None
