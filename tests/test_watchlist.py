"""Tests for cio.watchlist — CRUD, single-active invariant, the NASDAQ-index
floor, CSV import (operator's portfolio2.csv format), and the price snapshot.

All tests run offline: prices() takes an injected quote_fn so no network/yfinance
is touched. Each test gets its own SQLite file via tmp_path.
"""
import pytest

from cio import watchlist as wl


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "cio.db")


# ---- create / index floor / first-active ----------------------------------
def test_create_seeds_nasdaq_index_and_first_is_active(db):
    wid = wl.create("Tech", db_path=db)
    got = wl.get(wid, db_path=db)
    assert got["symbols"] == [wl.NASDAQ_INDEX]      # seeded with ^IXIC
    assert got["is_active"] == 1                    # first list auto-active


def test_create_blank_and_duplicate_name_raise(db):
    wl.create("Tech", db_path=db)
    with pytest.raises(wl.WatchlistError):
        wl.create("   ", db_path=db)
    with pytest.raises(wl.WatchlistError):
        wl.create("Tech", db_path=db)


def test_second_list_not_auto_active(db):
    wl.create("Tech", db_path=db)
    b = wl.create("Energy", db_path=db)
    assert wl.get(b, db_path=db)["is_active"] == 0
    assert wl.active(db_path=db)["name"] == "Tech"


# ---- single-active invariant ----------------------------------------------
def test_set_active_is_exclusive(db):
    a = wl.create("A", db_path=db)
    b = wl.create("B", db_path=db)
    c = wl.create("C", db_path=db)
    wl.set_active(b, db_path=db)
    actives = [w for w in wl.list_watchlists(db_path=db) if w["is_active"]]
    assert len(actives) == 1 and actives[0]["id"] == b
    wl.set_active(c, db_path=db)
    actives = [w for w in wl.list_watchlists(db_path=db) if w["is_active"]]
    assert len(actives) == 1 and actives[0]["id"] == c
    assert a  # silence linter


def test_set_active_unknown_raises(db):
    with pytest.raises(wl.WatchlistError):
        wl.set_active(999, db_path=db)


# ---- rename / delete -------------------------------------------------------
def test_rename(db):
    a = wl.create("Old", db_path=db)
    wl.create("Other", db_path=db)
    wl.rename(a, "New", db_path=db)
    assert wl.get(a, db_path=db)["name"] == "New"
    with pytest.raises(wl.WatchlistError):       # collide with existing name
        wl.rename(a, "Other", db_path=db)
    with pytest.raises(wl.WatchlistError):       # unknown id
        wl.rename(999, "X", db_path=db)


def test_delete_active_promotes_next(db):
    a = wl.create("A", db_path=db)
    b = wl.create("B", db_path=db)
    wl.set_active(a, db_path=db)
    wl.delete(a, db_path=db)
    # the only remaining list becomes active so the system keeps a valid pointer
    assert wl.active(db_path=db)["id"] == b
    assert wl.get(a, db_path=db) is None


def test_delete_unknown_raises(db):
    with pytest.raises(wl.WatchlistError):
        wl.delete(123, db_path=db)


# ---- items: add / remove / index guard ------------------------------------
def test_add_symbol_normalizes_and_dedupes(db):
    a = wl.create("A", db_path=db)
    assert wl.add_symbol(a, "aapl", db_path=db) is True   # newly added
    assert wl.add_symbol(a, "AAPL", db_path=db) is False  # already present (case-norm)
    assert "AAPL" in wl.get(a, db_path=db)["symbols"]


def test_add_symbol_invalid_and_unknown_list(db):
    a = wl.create("A", db_path=db)
    with pytest.raises(wl.WatchlistError):
        wl.add_symbol(a, "!!!", db_path=db)
    with pytest.raises(wl.WatchlistError):
        wl.add_symbol(999, "AAPL", db_path=db)


def test_remove_symbol_guards_nasdaq_index(db):
    a = wl.create("A", db_path=db)
    wl.add_symbol(a, "AAPL", db_path=db)
    wl.remove_symbol(a, "AAPL", db_path=db)
    assert "AAPL" not in wl.get(a, db_path=db)["symbols"]
    with pytest.raises(wl.WatchlistError):
        wl.remove_symbol(a, wl.NASDAQ_INDEX, db_path=db)
    assert wl.NASDAQ_INDEX in wl.get(a, db_path=db)["symbols"]  # still there


# ---- CSV import ------------------------------------------------------------
# Mirrors AI4StockMarket/resources/portfolio2.csv: one row of quoted tickers.
SAMPLE_CSV = '"AAL","AAOI","AAPL","ABNB","NVDA","MSFT"'


def test_import_csv_quoted_row(db):
    a = wl.create("A", db_path=db)
    added = wl.import_csv(a, text=SAMPLE_CSV, db_path=db)
    assert added == 6
    syms = wl.get(a, db_path=db)["symbols"]
    for t in ("AAL", "AAOI", "AAPL", "ABNB", "NVDA", "MSFT", wl.NASDAQ_INDEX):
        assert t in syms


def test_import_csv_is_idempotent(db):
    a = wl.create("A", db_path=db)
    wl.import_csv(a, text=SAMPLE_CSV, db_path=db)
    assert wl.import_csv(a, text=SAMPLE_CSV, db_path=db) == 0   # all already present


def test_import_csv_handles_header_and_lines(db):
    a = wl.create("A", db_path=db)
    added = wl.import_csv(a, text="symbol\nAAPL\nMSFT\n", db_path=db)
    assert added == 2                                          # 'symbol' header dropped
    assert "SYMBOL" not in wl.get(a, db_path=db)["symbols"]


def test_import_csv_no_valid_symbols_raises(db):
    a = wl.create("A", db_path=db)
    with pytest.raises(wl.WatchlistError):
        wl.import_csv(a, text=",,,\n", db_path=db)


def test_import_csv_from_file(db, tmp_path):
    a = wl.create("A", db_path=db)
    p = tmp_path / "tickers.csv"
    p.write_text(SAMPLE_CSV, encoding="utf-8")
    assert wl.import_csv(a, source=p, db_path=db) == 6


# ---- search ----------------------------------------------------------------
def test_search_by_name_and_symbol(db):
    a = wl.create("Tech", db_path=db)
    wl.create("Energy", db_path=db)
    wl.add_symbol(a, "NVDA", db_path=db)

    by_name = wl.search("tech", db_path=db)
    assert [w["name"] for w in by_name] == ["Tech"]

    by_sym = wl.search("NVDA", db_path=db)
    assert len(by_sym) == 1 and by_sym[0]["name"] == "Tech"
    assert by_sym[0]["matched"] == ["NVDA"]

    assert len(wl.search("", db_path=db)) == 2          # empty query -> all
    assert wl.search("zzz", db_path=db) == []           # no match


# ---- prices ----------------------------------------------------------------
def _fake_quote(sym):
    if sym == "GOOG":
        return None                                      # simulate no data
    return {"symbol": sym, "date": "2026-06-02", "close": 100.5,
            "price": 100.5, "volume": 1000}


def test_prices_uses_active_and_reports_missing(db):
    a = wl.create("A", db_path=db)
    wl.add_symbol(a, "AAPL", db_path=db)
    wl.add_symbol(a, "GOOG", db_path=db)
    snap = wl.prices(quote_fn=_fake_quote, db_path=db)   # active list
    assert snap["id"] == a and snap["watchlist"] == "A"
    got = {q["symbol"] for q in snap["quotes"]}
    assert got == {"AAPL", wl.NASDAQ_INDEX}
    assert snap["missing"] == ["GOOG"]


def test_prices_explicit_id(db):
    wl.create("A", db_path=db)
    b = wl.create("B", db_path=db)
    wl.add_symbol(b, "TSLA", db_path=db)
    snap = wl.prices(b, quote_fn=_fake_quote, db_path=db)
    assert snap["id"] == b
    assert {q["symbol"] for q in snap["quotes"]} == {"TSLA", wl.NASDAQ_INDEX}


def test_prices_no_watchlist_is_empty(db):
    snap = wl.prices(quote_fn=_fake_quote, db_path=db)
    assert snap == {"watchlist": None, "id": None, "quotes": [], "missing": []}
    assert "No active watchlist" in wl.format_prices(snap)


def test_format_prices_renders_rows(db):
    a = wl.create("A", db_path=db)
    wl.add_symbol(a, "AAPL", db_path=db)
    out = wl.format_prices(wl.prices(quote_fn=_fake_quote, db_path=db))
    assert "Watchlist: A" in out
    assert "AAPL" in out and "100.50" in out


# ---- reorder ---------------------------------------------------------------
def test_default_order_is_insertion(db):
    a = wl.create("A", db_path=db)                 # ^IXIC seeded at position 0
    wl.add_symbol(a, "MSFT", db_path=db)
    wl.add_symbol(a, "AAPL", db_path=db)
    assert wl.get(a, db_path=db)["symbols"] == [wl.NASDAQ_INDEX, "MSFT", "AAPL"]


def test_reorder_permutation(db):
    a = wl.create("A", db_path=db)
    for s in ("AAPL", "MSFT", "NVDA"):
        wl.add_symbol(a, s, db_path=db)
    result = wl.reorder(a, ["NVDA", "AAPL", "MSFT", wl.NASDAQ_INDEX], db_path=db)
    assert result == ["NVDA", "AAPL", "MSFT", wl.NASDAQ_INDEX]
    assert wl.get(a, db_path=db)["symbols"] == result


def test_reorder_partial_appends_remaining_in_prior_order(db):
    a = wl.create("A", db_path=db)
    for s in ("AAPL", "MSFT", "NVDA"):
        wl.add_symbol(a, s, db_path=db)
    # only mention two; the rest keep their existing relative order, appended after
    result = wl.reorder(a, ["NVDA", "AAPL"], db_path=db)
    assert result[:2] == ["NVDA", "AAPL"]
    assert set(result[2:]) == {"MSFT", wl.NASDAQ_INDEX}
    assert result[2:] == [wl.NASDAQ_INDEX, "MSFT"]   # prior order: ^IXIC then MSFT


def test_reorder_ignores_unknown_and_dedups(db):
    a = wl.create("A", db_path=db)
    wl.add_symbol(a, "AAPL", db_path=db)
    # 'FOO' not in list (ignored); 'aapl' dup (normalized, kept once)
    result = wl.reorder(a, ["FOO", "aapl", "AAPL"], db_path=db)
    assert result.count("AAPL") == 1
    assert "FOO" not in result
    assert set(result) == {"AAPL", wl.NASDAQ_INDEX}


def test_reorder_unknown_list_raises(db):
    with pytest.raises(wl.WatchlistError):
        wl.reorder(999, ["AAPL"], db_path=db)


def test_reorder_drives_prices_order(db):
    a = wl.create("A", db_path=db)
    for s in ("AAPL", "MSFT"):
        wl.add_symbol(a, s, db_path=db)
    wl.reorder(a, ["MSFT", "AAPL", wl.NASDAQ_INDEX], db_path=db)
    snap = wl.prices(a, quote_fn=_fake_quote, db_path=db)
    assert [q["symbol"] for q in snap["quotes"]] == ["MSFT", "AAPL", wl.NASDAQ_INDEX]


# ---- quote-board image (charts.watchlist_table) ---------------------------
from cio import charts


def test_fmt_vol():
    assert charts._fmt_vol(9900) == "9.90K"
    assert charts._fmt_vol(22_500_000) == "22.5M"
    assert charts._fmt_vol(1_050_000_000) == "1.05B"
    assert charts._fmt_vol(None) == "—"
    assert charts._fmt_vol(500) == "500"


def test_chg_color():
    assert charts._chg_color(1.0) == charts._C_UP
    assert charts._chg_color(-1.0) == charts._C_DOWN
    assert charts._chg_color(None) == charts._C_FLAT


def _board_snapshot():
    return {"watchlist": "W", "id": 1, "missing": ["CBRS"], "quotes": [
        {"symbol": "^IXIC", "close": 26864.62, "change": -229.28,
         "change_pct": -0.85, "volume": 9900},
        {"symbol": "AMD", "close": 532.72, "change": 11.18,
         "change_pct": 2.14, "volume": 16_000_000},
    ]}


def test_watchlist_table_renders_png():
    path = charts.watchlist_table(_board_snapshot())
    assert path and path.endswith(".png")
    with open(path, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"     # valid PNG magic


def test_watchlist_table_empty_returns_none():
    assert charts.watchlist_table({"quotes": [], "missing": []}) is None
