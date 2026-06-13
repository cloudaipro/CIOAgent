"""Watchlists for the CIO agent.

The operator keeps several named lists of symbols to track. Exactly one is
*active*; the active list drives the Telegram ``/watchlist`` price snapshot and
the ``watchlist_prices`` agent tool. CRUD + import live here; the dashboard
(write UI) and the bot (read path) both call into this module so there is one
source of truth for the single-active invariant and the NASDAQ-index rule.

Design rules:
  * Single active — set_active() clears is_active on every other row in the same
    transaction. There is no DB constraint for "at most one active" (SQLite can't
    express it), so this function is the ONLY place that flips the flag.
  * NASDAQ index floor — every list is seeded with ^IXIC on create, and the index
    can't be removed (remove_symbol refuses it). So "at least one NASDAQ index"
    always holds. ^IXIC is the Yahoo/yfinance ticker for the NASDAQ Composite,
    matching backtest_data_feeds/int_ta_data_feed.py in AI4StockMarket.
  * Prices are NOT stored here — prices() fetches live via cio.stock.data so the
    figures are never stale cached numbers (consistent with the figures firewall).
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
from pathlib import Path

from . import db

# NASDAQ Composite index ticker (yfinance). Seeded into every watchlist so the
# operator always has a market benchmark alongside individual names.
NASDAQ_INDEX = "^IXIC"

# Same allowed set as cio.stock.data.safe_symbol (letters, digits, ". - ^ ="):
# kept local so the watchlist CRUD/CSV path imports without pulling yfinance —
# only prices() needs the live data layer, and it imports it lazily.
_SAFE_SYM = re.compile(r"[^A-Za-z0-9.\-^=]")


class WatchlistError(ValueError):
    """Invalid watchlist operation (duplicate name, missing list, etc.)."""


def _safe_symbol(symbol: str) -> str:
    """Sanitize a ticker (strip illegal/path chars + leading dots, cap length).
    Mirrors cio.stock.data.safe_symbol. Raises ValueError if nothing valid
    remains."""
    s = _SAFE_SYM.sub("", str(symbol)).lstrip(".")[:24]
    if not s:
        raise ValueError(f"invalid symbol: {symbol!r}")
    return s


def _norm_symbol(symbol: str) -> str:
    """Upper-case + sanitize a ticker. Raises WatchlistError if nothing valid
    remains."""
    try:
        return _safe_symbol(str(symbol).strip().upper())
    except ValueError as e:
        raise WatchlistError(str(e)) from e


# ---- CRUD ------------------------------------------------------------------
def create(name: str, db_path=db.DB_PATH) -> int:
    """Create a watchlist, seed it with the NASDAQ index, return its id.

    The first watchlist created becomes active automatically (so the system is
    never left with lists but no active one). Raises WatchlistError on a blank or
    duplicate name."""
    name = (name or "").strip()
    if not name:
        raise WatchlistError("watchlist name cannot be empty")
    conn = db.connect(db_path)
    try:
        with conn:
            try:
                cur = conn.execute("INSERT INTO watchlists (name) VALUES (?)", (name,))
            except sqlite3.IntegrityError as e:
                raise WatchlistError(f"watchlist {name!r} already exists") from e
            wid = cur.lastrowid
            conn.execute(
                "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol) VALUES (?,?)",
                (wid, NASDAQ_INDEX),
            )
            # First-ever list is active by default.
            if conn.execute("SELECT COUNT(*) FROM watchlists").fetchone()[0] == 1:
                conn.execute("UPDATE watchlists SET is_active = 1 WHERE id = ?", (wid,))
        return wid
    finally:
        conn.close()


def rename(watchlist_id: int, name: str, db_path=db.DB_PATH) -> None:
    """Rename a watchlist. Raises WatchlistError on blank/duplicate name or
    unknown id."""
    name = (name or "").strip()
    if not name:
        raise WatchlistError("watchlist name cannot be empty")
    conn = db.connect(db_path)
    try:
        with conn:
            try:
                cur = conn.execute(
                    "UPDATE watchlists SET name = ? WHERE id = ?", (name, watchlist_id)
                )
            except sqlite3.IntegrityError as e:
                raise WatchlistError(f"watchlist {name!r} already exists") from e
            if cur.rowcount == 0:
                raise WatchlistError(f"no watchlist with id {watchlist_id}")
    finally:
        conn.close()


def delete(watchlist_id: int, db_path=db.DB_PATH) -> None:
    """Delete a watchlist and its items. If it was the active one, promotes the
    oldest remaining list to active so the system keeps a valid active pointer."""
    conn = db.connect(db_path)
    try:
        with conn:
            row = conn.execute(
                "SELECT is_active FROM watchlists WHERE id = ?", (watchlist_id,)
            ).fetchone()
            if row is None:
                raise WatchlistError(f"no watchlist with id {watchlist_id}")
            was_active = row["is_active"]
            # Explicit child delete: connect() doesn't enable PRAGMA foreign_keys,
            # so the ON DELETE CASCADE in the schema wouldn't fire on its own.
            conn.execute("DELETE FROM watchlist_items WHERE watchlist_id = ?", (watchlist_id,))
            conn.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))
            if was_active:
                nxt = conn.execute(
                    "SELECT id FROM watchlists ORDER BY id LIMIT 1"
                ).fetchone()
                if nxt is not None:
                    conn.execute(
                        "UPDATE watchlists SET is_active = 1 WHERE id = ?", (nxt["id"],)
                    )
    finally:
        conn.close()


def set_active(watchlist_id: int, db_path=db.DB_PATH) -> None:
    """Make one watchlist active and clear every other (single-active invariant).
    This is the ONLY function that sets is_active=1."""
    conn = db.connect(db_path)
    try:
        with conn:
            if conn.execute(
                "SELECT 1 FROM watchlists WHERE id = ?", (watchlist_id,)
            ).fetchone() is None:
                raise WatchlistError(f"no watchlist with id {watchlist_id}")
            conn.execute("UPDATE watchlists SET is_active = 0 WHERE id != ?", (watchlist_id,))
            conn.execute("UPDATE watchlists SET is_active = 1 WHERE id = ?", (watchlist_id,))
    finally:
        conn.close()


# ---- reads -----------------------------------------------------------------
def list_watchlists(db_path=db.DB_PATH) -> list[dict]:
    """All watchlists with item counts, active flag, ordered by id."""
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT w.id, w.name, w.is_active, w.created_at, "
            "       (SELECT COUNT(*) FROM watchlist_items i WHERE i.watchlist_id = w.id) AS count "
            "FROM watchlists w ORDER BY w.id"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get(watchlist_id: int, db_path=db.DB_PATH) -> dict | None:
    """One watchlist row (with symbols), or None if it doesn't exist."""
    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, is_active, created_at FROM watchlists WHERE id = ?",
            (watchlist_id,),
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["symbols"] = _symbols(conn, watchlist_id)
        return out
    finally:
        conn.close()


def find_by_name(name: str, db_path=db.DB_PATH) -> dict | None:
    """The watchlist with this exact name (with symbols), or None. Used by Alpha
    Hunter to refresh its dated list in place instead of creating duplicates."""
    name = (name or "").strip()
    if not name:
        return None
    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, is_active, created_at FROM watchlists WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["symbols"] = _symbols(conn, out["id"])
        return out
    finally:
        conn.close()


def set_symbols(watchlist_id: int, symbols, *, db_path=db.DB_PATH) -> list[str]:
    """Replace a list's contents with *symbols* (order preserved). The NASDAQ index
    floor (^IXIC) is always kept/seeded so the benchmark invariant holds. Returns the
    resulting symbol order. Raises WatchlistError on an unknown list."""
    norm: list[str] = []
    seen: set[str] = set()
    for s in symbols or []:
        try:
            sym = _norm_symbol(s)
        except WatchlistError:
            continue
        if sym not in seen:
            seen.add(sym)
            norm.append(sym)
    conn = db.connect(db_path)
    try:
        with conn:
            if conn.execute(
                "SELECT 1 FROM watchlists WHERE id = ?", (watchlist_id,)
            ).fetchone() is None:
                raise WatchlistError(f"no watchlist with id {watchlist_id}")
            conn.execute("DELETE FROM watchlist_items WHERE watchlist_id = ?", (watchlist_id,))
            final = ([NASDAQ_INDEX] + [s for s in norm if s != NASDAQ_INDEX])
            for pos, sym in enumerate(final):
                conn.execute(
                    "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol, position) "
                    "VALUES (?,?,?)",
                    (watchlist_id, sym, pos),
                )
        return final
    finally:
        conn.close()


def active(db_path=db.DB_PATH) -> dict | None:
    """The active watchlist (with symbols), or None if none exists yet."""
    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id, name, is_active, created_at FROM watchlists WHERE is_active = 1 "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["symbols"] = _symbols(conn, out["id"])
        return out
    finally:
        conn.close()


def _symbols(conn: sqlite3.Connection, watchlist_id: int) -> list[str]:
    # User-defined order (drag-to-rearrange); symbol is the tie-breaker so a list
    # that's never been reordered (all position 0) still renders deterministically.
    rows = conn.execute(
        "SELECT symbol FROM watchlist_items WHERE watchlist_id = ? "
        "ORDER BY position, symbol",
        (watchlist_id,),
    ).fetchall()
    return [r["symbol"] for r in rows]


def _next_position(conn: sqlite3.Connection, watchlist_id: int) -> int:
    """Position for a symbol appended to the end of a list."""
    return conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 FROM watchlist_items WHERE watchlist_id = ?",
        (watchlist_id,),
    ).fetchone()[0]


def search(query: str, db_path=db.DB_PATH) -> list[dict]:
    """Watchlists whose name OR any contained symbol matches *query* (substring,
    case-insensitive). Each result carries the matching symbols. Empty query
    returns every list."""
    q = (query or "").strip().upper()
    conn = db.connect(db_path)
    try:
        out = []
        for w in conn.execute(
            "SELECT id, name, is_active FROM watchlists ORDER BY id"
        ).fetchall():
            syms = _symbols(conn, w["id"])
            name_hit = q in w["name"].upper()
            sym_hits = [s for s in syms if q in s]
            if not q or name_hit or sym_hits:
                d = dict(w)
                d["symbols"] = syms
                d["count"] = len(syms)
                d["matched"] = syms if (not q or name_hit) else sym_hits
                out.append(d)
        return out
    finally:
        conn.close()


# ---- items -----------------------------------------------------------------
def add_symbol(watchlist_id: int, symbol: str, db_path=db.DB_PATH) -> bool:
    """Add one symbol. Returns True if newly added, False if it was already there.
    Raises WatchlistError on unknown list or invalid symbol."""
    sym = _norm_symbol(symbol)
    conn = db.connect(db_path)
    try:
        with conn:
            if conn.execute(
                "SELECT 1 FROM watchlists WHERE id = ?", (watchlist_id,)
            ).fetchone() is None:
                raise WatchlistError(f"no watchlist with id {watchlist_id}")
            cur = conn.execute(
                "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol, position) "
                "VALUES (?,?,?)",
                (watchlist_id, sym, _next_position(conn, watchlist_id)),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def remove_symbol(watchlist_id: int, symbol: str, db_path=db.DB_PATH) -> None:
    """Remove one symbol. Refuses to remove the NASDAQ index so every list keeps
    its market benchmark (requirement: at least one NASDAQ index per list)."""
    sym = _norm_symbol(symbol)
    if sym == NASDAQ_INDEX:
        raise WatchlistError(
            f"{NASDAQ_INDEX} is the required NASDAQ index and can't be removed"
        )
    conn = db.connect(db_path)
    try:
        with conn:
            conn.execute(
                "DELETE FROM watchlist_items WHERE watchlist_id = ? AND symbol = ?",
                (watchlist_id, sym),
            )
    finally:
        conn.close()


def reorder(watchlist_id: int, ordered: list[str], db_path=db.DB_PATH) -> list[str]:
    """Set the display order of a list's symbols to *ordered* (drag-to-rearrange).

    Lenient by design — the UI submits the symbols it currently shows, which may
    drift from the DB under a concurrent edit. Symbols in *ordered* that still
    exist are placed first in the given order; any current symbol not mentioned is
    appended after, keeping its prior relative order. Unknown symbols are ignored.
    Returns the resulting symbol order. Raises WatchlistError on an unknown list."""
    conn = db.connect(db_path)
    try:
        with conn:
            if conn.execute(
                "SELECT 1 FROM watchlists WHERE id = ?", (watchlist_id,)
            ).fetchone() is None:
                raise WatchlistError(f"no watchlist with id {watchlist_id}")
            current = _symbols(conn, watchlist_id)            # current DB order
            current_set = set(current)
            seen: dict[str, None] = {}
            for s in ordered:
                try:
                    sym = _safe_symbol(str(s).strip().upper())
                except ValueError:
                    continue
                if sym in current_set:
                    seen.setdefault(sym, None)                # dedup, keep first
            final = list(seen.keys()) + [s for s in current if s not in seen]
            for pos, sym in enumerate(final):
                conn.execute(
                    "UPDATE watchlist_items SET position = ? "
                    "WHERE watchlist_id = ? AND symbol = ?",
                    (pos, watchlist_id, sym),
                )
        return final
    finally:
        conn.close()


def _parse_csv_symbols(text: str) -> list[str]:
    """Extract tickers from CSV text. Handles the operator's portfolio2.csv format
    (one row of quoted tickers, no header) as well as a one-symbol-per-line or a
    'symbol'/'ticker' column. Every cell is flattened, a leading header cell is
    dropped, cells are sanitized, and order-preserving de-duplication is applied.
    Cells that aren't valid tickers are skipped silently."""
    seen: dict[str, None] = {}
    for row in csv.reader(io.StringIO(text)):
        for cell in row:
            tok = (cell or "").strip().upper()
            if not tok or tok in ("SYMBOL", "TICKER", "SYMBOLS", "TICKERS"):
                continue
            try:
                sym = _safe_symbol(tok)
            except ValueError:
                continue
            seen.setdefault(sym, None)
    return list(seen.keys())


def import_csv(watchlist_id: int, source: str | Path | None = None, *,
               text: str | None = None, db_path=db.DB_PATH) -> int:
    """Import symbols from a CSV into a watchlist. Provide either a file *source*
    (path) or raw *text*. Returns the number of symbols newly added (existing ones
    are ignored — import is naturally idempotent via the PK). Raises WatchlistError
    on unknown list or if no valid symbols are found."""
    if text is None:
        if source is None:
            raise WatchlistError("import_csv needs a file path or text")
        text = Path(source).read_text(encoding="utf-8-sig")
    symbols = _parse_csv_symbols(text)
    if not symbols:
        raise WatchlistError("no valid symbols found in CSV")
    conn = db.connect(db_path)
    try:
        with conn:
            if conn.execute(
                "SELECT 1 FROM watchlists WHERE id = ?", (watchlist_id,)
            ).fetchone() is None:
                raise WatchlistError(f"no watchlist with id {watchlist_id}")
            added = 0
            pos = _next_position(conn, watchlist_id)
            for sym in symbols:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO watchlist_items (watchlist_id, symbol, position) "
                    "VALUES (?,?,?)",
                    (watchlist_id, sym, pos),
                )
                if cur.rowcount:
                    added += 1
                    pos += 1     # only consume a slot for symbols actually added
        return added
    finally:
        conn.close()


# ---- prices ----------------------------------------------------------------
def prices(watchlist_id: int | None = None, *, quote_fn=None, db_path=db.DB_PATH) -> dict:
    """Latest prices for a watchlist (the active one if *watchlist_id* is None).

    Returns ``{"watchlist": <name>|None, "id": <id>|None, "quotes": [...],
    "missing": [<symbol>, ...]}``. Each quote is the dict from
    ``stock.data.latest_quote`` (symbol, date, close, price, volume, OHLC).
    ``quote_fn`` overrides the price source (used by tests). If no watchlist
    exists, ``id``/``watchlist`` are None and quotes is empty."""
    if quote_fn is None:
        from .stock import data as stockdata  # lazy: only the price path needs yfinance
        quote_fn = stockdata.latest_quote
    fetch = quote_fn
    wl = get(watchlist_id, db_path=db_path) if watchlist_id is not None else active(db_path=db_path)
    if wl is None:
        return {"watchlist": None, "id": None, "quotes": [], "missing": []}
    quotes, missing = [], []
    for sym in wl["symbols"]:
        try:
            q = fetch(sym)
        except Exception:
            q = None
        if q:
            quotes.append(q)
        else:
            missing.append(sym)
    return {"watchlist": wl["name"], "id": wl["id"], "quotes": quotes, "missing": missing}


def format_prices(snapshot: dict) -> str:
    """Render a prices() snapshot as a plain-text block for Telegram. Pure string
    formatting — no I/O — so the bot command and the agent tool share one layout."""
    if snapshot["id"] is None:
        return "No active watchlist. Create one in the dashboard first."
    lines = [f"📋 Watchlist: {snapshot['watchlist']}"]
    for q in snapshot["quotes"]:
        lines.append(f"  {q['symbol']:<8} {q['close']:>12,.2f}   ({q['date']})")
    if not snapshot["quotes"]:
        lines.append("  (no prices available)")
    if snapshot["missing"]:
        lines.append("  no data: " + ", ".join(snapshot["missing"]))
    return "\n".join(lines)
