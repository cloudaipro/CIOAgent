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

    bad_actions = sorted(set(df["action"]) - {"BUY", "SELL", "DIV"})
    if bad_actions:
        raise ValueError(f"CSV has invalid action values: {bad_actions} "
                         "(must be BUY, SELL or DIV)")

    # Blank cells parse as NaN, and sqlite3 binds NaN as NULL — which the NOT NULL
    # columns reject (a DIV row routinely leaves quantity empty). Coerce numerics
    # to their defaults, blank currency to USD, and blank notes to real NULL.
    for col in ("quantity", "price", "fees"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["currency"] = (df["currency"].fillna("USD").astype(str).str.strip()
                      .str.upper().replace("", "USD"))
    df["notes"] = df["notes"].astype(object).where(df["notes"].notna(), None)
    df["txn_date"] = df["txn_date"].astype(str).str.strip()

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


def sync_ibkr(db_path=db.DB_PATH, snapshot_fn=None) -> dict:
    """Pull the live IBKR snapshot and reconcile it against the local book.

    The transactions table stays the source of truth for cost basis — IBKR
    positions are NOT written as transactions. The sync does two things:
      1. writes IBKR's mark price for every held symbol into the prices table
         (same effect as refresh_live_prices, but broker-quality marks), and
      2. reports drift: symbols where IBKR quantity != local book quantity,
         so the operator knows the manual ledger is stale.

    Returns {"account", "synced": [{symbol, price}], "drift": [...],
             "cash": {...}} or {"error": ...} when IBKR is not configured or
    TWS / IB Gateway is unreachable.
    """
    if snapshot_fn is None:
        from .data import ibkr
        if not ibkr.enabled():
            return {"error": "IBKR not configured (set CIO_IBKR_TWS=host:port)"}
        snapshot_fn = ibkr.snapshot

    snap = snapshot_fn()
    if not snap:
        return {"error": "TWS / IB Gateway unreachable — make sure it is "
                         "running, logged in, and the API socket is enabled"}

    local = positions(db_path)
    local_qty = dict(zip(local["symbol"], local["quantity"])) if len(local) else {}

    synced, drift = [], []
    for p in snap["positions"]:
        sym = p["symbol"]
        if p.get("last_price") is not None:
            set_price(sym, p["last_price"], db_path=db_path)
            synced.append({"symbol": sym, "price": p["last_price"]})
        lq = float(local_qty.get(sym, 0.0))
        if abs(lq - p["quantity"]) > 1e-6:
            drift.append({"symbol": sym, "ibkr_qty": p["quantity"], "local_qty": lq})
    # Symbols held locally but absent at IBKR are drift too (sold elsewhere?).
    ibkr_syms = {p["symbol"] for p in snap["positions"]}
    for sym, lq in local_qty.items():
        if sym not in ibkr_syms and lq > 1e-6:
            drift.append({"symbol": sym, "ibkr_qty": 0.0, "local_qty": float(lq)})

    return {"account": snap["account"], "synced": synced,
            "drift": drift, "cash": snap.get("cash", {})}


def align_with_ibkr(db_path=db.DB_PATH, snapshot_fn=None) -> dict:
    """Rebuild the local book from the live IBKR snapshot (destructive).

    Replaces ALL rows in the transactions table with one synthetic BUY per
    current IBKR position, priced at IBKR's own average cost — so quantities
    AND cost basis match the broker exactly. Realized-P&L / dividend history
    is wiped with the old rows (the caller backs the DB up first; the
    dashboard wires this through backup.backup_all()).

    Also writes each position's mark price into the prices table, so the
    portfolio view values immediately.

    Returns {"account", "wiped": n_old_rows, "adopted": n_positions} or
    {"error": ...} mirroring sync_ibkr's failure modes.
    """
    from datetime import date as _date

    if snapshot_fn is None:
        from .data import ibkr
        if not ibkr.enabled():
            return {"error": "IBKR not configured (set CIO_IBKR_TWS=host:port)"}
        snapshot_fn = ibkr.snapshot

    snap = snapshot_fn()
    if not snap:
        return {"error": "TWS / IB Gateway unreachable — make sure it is "
                         "running, logged in, and the API socket is enabled"}

    today = _date.today().isoformat()
    rows = [
        (today, p["symbol"], "BUY", p["quantity"], p.get("avg_cost") or 0.0,
         0.0, p.get("currency") or "USD",
         f"ibkr-align {snap['account']} {today}")
        for p in snap["positions"]
    ]

    conn = db.connect(db_path)
    try:
        # One transaction: wipe + adopt commit together or not at all.
        with conn:
            wiped = conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"]
            conn.execute("DELETE FROM transactions")
            conn.executemany(
                "INSERT INTO transactions "
                "(txn_date,symbol,action,quantity,price,fees,currency,notes) "
                "VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
    finally:
        conn.close()

    for p in snap["positions"]:
        if p.get("last_price") is not None:
            set_price(p["symbol"], p["last_price"], db_path=db_path)

    return {"account": snap["account"], "wiped": wiped, "adopted": len(rows)}


def reconcile_ibkr(db_path=db.DB_PATH, snapshot_fn=None) -> dict:
    """Flatten quantity drift against IBKR by BOOKING transactions, not wiping.

    Unlike :func:`align_with_ibkr` (which deletes the whole ledger), this
    appends the minimum closing / opening trades so local quantities match
    the broker while realized-P&L and dividend history are preserved:

      * local quantity > IBKR  -> a SELL of the surplus at the local average
        cost, so the close books **zero** realized P&L. The true exit price is
        unknown (the shares simply left the broker — sold or transferred
        elsewhere), so we record no invented gain or loss; the operator can
        edit the SELL later if the real fill is known.
      * IBKR quantity > local   -> a BUY of the shortfall at IBKR's average
        cost, adopting the extra shares the broker reports.

    Symbols absent at IBKR are treated as quantity 0 (a full close). Every
    IBKR-held symbol's mark price is written to the prices table, same as
    :func:`sync_ibkr`.

    Returns {"account", "closed": [{symbol, sold_qty, price}],
             "opened": [{symbol, bought_qty, price}], "priced": n} or
    {"error": ...} mirroring sync_ibkr's failure modes.
    """
    from datetime import date as _date

    if snapshot_fn is None:
        from .data import ibkr
        if not ibkr.enabled():
            return {"error": "IBKR not configured (set CIO_IBKR_TWS=host:port)"}
        snapshot_fn = ibkr.snapshot

    snap = snapshot_fn()
    if not snap:
        return {"error": "TWS / IB Gateway unreachable — make sure it is "
                         "running, logged in, and the API socket is enabled"}

    today = _date.today().isoformat()
    acct = snap["account"]
    note = f"ibkr-reconcile {acct} {today}"

    local = positions(db_path)
    local_rows = {r["symbol"]: r for r in local.to_dict("records")} if len(local) else {}
    ibkr_pos = {p["symbol"]: p for p in snap["positions"]}

    closed, opened, new_txns = [], [], []

    # Local positions that exceed IBKR (or that IBKR no longer holds): SELL the
    # surplus at local average cost -> the close realizes zero P&L.
    for sym, r in local_rows.items():
        lq = float(r["quantity"])
        iq = float(ibkr_pos[sym]["quantity"]) if sym in ibkr_pos else 0.0
        diff = round(lq - iq, 6)
        if diff > 1e-6:
            avg = float(r["avg_cost"])
            cur = (ibkr_pos.get(sym, {}) or {}).get("currency") or "USD"
            new_txns.append((today, sym, "SELL", diff, avg, 0.0, cur, note))
            closed.append({"symbol": sym, "sold_qty": diff, "price": avg})

    # IBKR positions that exceed local: BUY the shortfall at IBKR average cost.
    for sym, p in ibkr_pos.items():
        iq = float(p["quantity"])
        lq = float(local_rows[sym]["quantity"]) if sym in local_rows else 0.0
        diff = round(iq - lq, 6)
        if diff > 1e-6:
            avg = float(p.get("avg_cost") or 0.0)
            cur = p.get("currency") or "USD"
            new_txns.append((today, sym, "BUY", diff, avg, 0.0, cur, note))
            opened.append({"symbol": sym, "bought_qty": diff, "price": avg})

    if new_txns:
        conn = db.connect(db_path)
        try:
            with conn:
                conn.executemany(
                    "INSERT INTO transactions "
                    "(txn_date,symbol,action,quantity,price,fees,currency,notes) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    new_txns,
                )
        finally:
            conn.close()

    priced = 0
    for p in snap["positions"]:
        if p.get("last_price") is not None:
            set_price(p["symbol"], p["last_price"], db_path=db_path)
            priced += 1

    return {"account": acct, "closed": closed, "opened": opened, "priced": priced}


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
