"""Stock-portfolio domain logic.

Everything derives from the `transactions` table. Cost basis uses the
**average-cost** method (simplest correct approach for a solo operator):
a BUY blends into the average; a SELL realizes P&L against the running average
without changing it. Valuation uses the latest manually-entered price.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from . import db


class DuplicateImport(Exception):
    """Raised when a CSV with identical bytes has already been imported."""

    def __init__(self, file_hash: str, rows: int):
        self.file_hash = file_hash
        self.rows = rows
        super().__init__(f"already imported ({rows} rows, hash {file_hash[:12]}…)")

# ----- CSV ingest -----------------------------------------------------------

TXN_COLS = ["txn_date", "symbol", "action", "quantity", "price", "fees", "currency", "notes"]


def ingest_transactions_csv(path: str | Path, db_path=db.DB_PATH) -> int:
    """Load a transactions CSV into the DB. Returns rows inserted.

    Idempotent by file content: re-importing a CSV with identical bytes raises
    `DuplicateImport` instead of inserting again, so a redelivered upload after a
    mid-turn crash can't double-import and corrupt cost basis.

    Expected columns (case-insensitive, extras ignored):
        txn_date, symbol, action, quantity, price[, fees, currency, notes]
    action is one of BUY / SELL / DIV.
    """
    raw = Path(path).read_bytes()
    file_hash = hashlib.sha256(raw).hexdigest()

    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"txn_date", "symbol", "action"} - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing required columns: {sorted(missing)}")

    for col, default in [("quantity", 0), ("price", 0), ("fees", 0),
                         ("currency", "USD"), ("notes", None)]:
        if col not in df.columns:
            df[col] = default

    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["action"] = df["action"].astype(str).str.strip().str.upper()

    rows = list(df[TXN_COLS].itertuples(index=False, name=None))
    conn = db.connect(db_path)
    prior = conn.execute(
        "SELECT rows FROM imported_files WHERE file_hash = ?", (file_hash,)
    ).fetchone()
    if prior is not None:
        conn.close()
        raise DuplicateImport(file_hash, prior["rows"])

    n = len(rows)
    try:
        # One transaction: the inserts AND the idempotency record commit together
        # (or roll back together on crash), so a replay re-imports cleanly.
        with conn:
            conn.executemany(
                "INSERT INTO transactions "
                "(txn_date,symbol,action,quantity,price,fees,currency,notes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            conn.execute(
                "INSERT INTO imported_files (file_hash, source, rows) VALUES (?,?,?)",
                (file_hash, str(path), n),
            )
    finally:
        conn.close()
    return n


def ingest_prices_csv(path: str | Path, db_path=db.DB_PATH) -> int:
    """Load a prices CSV: columns symbol, price_date, close."""
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    rows = df[["symbol", "price_date", "close"]].itertuples(index=False, name=None)
    conn = db.connect(db_path)
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO prices (symbol,price_date,close) VALUES (?,?,?)",
            list(rows),
        )
    conn.close()
    return len(df)


def set_price(symbol: str, close: float, price_date: str | None = None, db_path=db.DB_PATH) -> None:
    """Manually set the latest price for one symbol."""
    from datetime import date
    price_date = price_date or date.today().isoformat()
    conn = db.connect(db_path)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO prices (symbol,price_date,close) VALUES (?,?,?)",
            (symbol.strip().upper(), price_date, float(close)),
        )
    conn.close()


# ----- derived views --------------------------------------------------------

def _latest_prices(conn) -> dict[str, float]:
    rows = conn.execute(
        "SELECT symbol, close FROM prices p WHERE price_date = "
        "(SELECT MAX(price_date) FROM prices WHERE symbol = p.symbol)"
    ).fetchall()
    return {r["symbol"]: r["close"] for r in rows}


def positions(db_path=db.DB_PATH) -> pd.DataFrame:
    """Current open positions with average cost basis and unrealized P&L.

    Columns: symbol, quantity, avg_cost, cost_basis, last_price,
             market_value, unrealized_pl, unrealized_pct
    """
    conn = db.connect(db_path)
    txns = conn.execute(
        "SELECT txn_date,symbol,action,quantity,price,fees FROM transactions "
        "ORDER BY txn_date, id"
    ).fetchall()
    prices = _latest_prices(conn)
    conn.close()

    book: dict[str, dict] = {}  # symbol -> {qty, avg_cost}
    for t in txns:
        sym = t["symbol"]
        b = book.setdefault(sym, {"qty": 0.0, "avg_cost": 0.0})
        if t["action"] == "BUY":
            new_qty = b["qty"] + t["quantity"]
            if new_qty > 0:
                cost = b["avg_cost"] * b["qty"] + t["price"] * t["quantity"] + t["fees"]
                b["avg_cost"] = cost / new_qty
            b["qty"] = new_qty
        elif t["action"] == "SELL":
            b["qty"] -= t["quantity"]
            if b["qty"] <= 1e-9:
                b["qty"] = 0.0
                b["avg_cost"] = 0.0
        # DIV does not change position

    out = []
    for sym, b in book.items():
        if b["qty"] <= 1e-9:
            continue
        last = prices.get(sym)
        cost_basis = b["avg_cost"] * b["qty"]
        mkt = last * b["qty"] if last is not None else None
        upl = (mkt - cost_basis) if mkt is not None else None
        out.append({
            "symbol": sym,
            "quantity": round(b["qty"], 4),
            "avg_cost": round(b["avg_cost"], 4),
            "cost_basis": round(cost_basis, 2),
            "last_price": last,
            "market_value": round(mkt, 2) if mkt is not None else None,
            "unrealized_pl": round(upl, 2) if upl is not None else None,
            "unrealized_pct": round(upl / cost_basis * 100, 2) if upl is not None and cost_basis else None,
        })
    return pd.DataFrame(out, columns=[
        "symbol", "quantity", "avg_cost", "cost_basis",
        "last_price", "market_value", "unrealized_pl", "unrealized_pct",
    ])


def realized_pl(db_path=db.DB_PATH) -> pd.DataFrame:
    """Realized P&L per symbol from SELLs (average-cost) plus dividends."""
    conn = db.connect(db_path)
    txns = conn.execute(
        "SELECT txn_date,symbol,action,quantity,price,fees FROM transactions "
        "ORDER BY txn_date, id"
    ).fetchall()
    conn.close()

    book: dict[str, dict] = {}
    realized: dict[str, float] = {}
    divs: dict[str, float] = {}
    for t in txns:
        sym = t["symbol"]
        b = book.setdefault(sym, {"qty": 0.0, "avg_cost": 0.0})
        if t["action"] == "BUY":
            new_qty = b["qty"] + t["quantity"]
            if new_qty > 0:
                cost = b["avg_cost"] * b["qty"] + t["price"] * t["quantity"] + t["fees"]
                b["avg_cost"] = cost / new_qty
            b["qty"] = new_qty
        elif t["action"] == "SELL":
            pl = (t["price"] - b["avg_cost"]) * t["quantity"] - t["fees"]
            realized[sym] = realized.get(sym, 0.0) + pl
            b["qty"] -= t["quantity"]
            if b["qty"] <= 1e-9:
                b["qty"] = 0.0
                b["avg_cost"] = 0.0
        elif t["action"] == "DIV":
            divs[sym] = divs.get(sym, 0.0) + t["price"]

    syms = sorted(set(realized) | set(divs))
    out = [{
        "symbol": s,
        "realized_pl": round(realized.get(s, 0.0), 2),
        "dividends": round(divs.get(s, 0.0), 2),
        "total": round(realized.get(s, 0.0) + divs.get(s, 0.0), 2),
    } for s in syms]
    return pd.DataFrame(out, columns=["symbol", "realized_pl", "dividends", "total"])


def refresh_live_prices(symbols=None, quote_fn=None, db_path=db.DB_PATH) -> dict:
    """Fetch live prices for open positions and write them to the prices table.

    Args:
        symbols: list of symbol strings to refresh; defaults to open positions
                 (rows with quantity > 0).
        quote_fn: callable(sym) -> dict|None; defaults to cio.stock.get_quote
                  (lazy import so portfolio stays importable without stock deps).
        db_path: path to the SQLite database.

    Returns:
        {"updated": [{"symbol": .., "price": ..}],
         "failed":  [sym, ..],
         "as_of":   "YYYY-MM-DD"}
    """
    from datetime import date as _date

    if quote_fn is None:
        from .stock import get_quote as quote_fn  # lazy — keeps portfolio offline-safe

    if symbols is None:
        pos = positions(db_path)
        symbols = pos.loc[pos["quantity"] > 0, "symbol"].tolist()

    updated = []
    failed = []
    for sym in symbols:
        try:
            q = quote_fn(sym)
            if q is not None and q.get("price") is not None:
                set_price(sym, q["price"], q.get("date"), db_path=db_path)
                updated.append({"symbol": sym, "price": q["price"]})
            else:
                failed.append(sym)
        except Exception:
            failed.append(sym)

    return {
        "updated": updated,
        "failed": failed,
        "as_of": _date.today().isoformat(),
    }


def summary(db_path=db.DB_PATH) -> dict:
    """Portfolio totals."""
    pos = positions(db_path)
    rpl = realized_pl(db_path)
    mkt = pos["market_value"].dropna().sum()
    cost = pos["cost_basis"].sum()
    upl = pos["unrealized_pl"].dropna().sum()
    return {
        "positions": len(pos),
        "market_value": round(float(mkt), 2),
        "cost_basis": round(float(cost), 2),
        "unrealized_pl": round(float(upl), 2),
        "unrealized_pct": round(float(upl / cost * 100), 2) if cost else 0.0,
        "realized_pl": round(float(rpl["realized_pl"].sum()), 2) if len(rpl) else 0.0,
        "dividends": round(float(rpl["dividends"].sum()), 2) if len(rpl) else 0.0,
    }
