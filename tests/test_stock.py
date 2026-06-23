"""
Offline pytest suite for cio.stock — fetch/cache/strategy subsystem.

All tests run WITHOUT network access. Real yfinance.download is monkeypatched
wherever a download would otherwise occur.
"""
import warnings

# Suppress noisy pandas / numpy deprecation chatter from vendored engine code.
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import pytest
import pandas as pd
import numpy as np

import cio.stock as s
from tests.conftest import make_ohlcv, make_wide_ohlcv


# ---------------------------------------------------------------------------
# 1. strategy count
# ---------------------------------------------------------------------------

def test_lists_40_strategies():
    """list_strategies() must return exactly 40 names (aberration + fisher registered)."""
    strategies = s.list_strategies()
    assert len(strategies) == 40, (
        f"Expected 40 strategies, got {len(strategies)}: {strategies}"
    )
    assert "aberration" in strategies and "fisher" in strategies


# ---------------------------------------------------------------------------
# 2. engine regression — every strategy must return a DataFrame
# ---------------------------------------------------------------------------

def test_engine_runs_all_strategies():
    """StrategyEngine.run_all() must return a DataFrame for every strategy."""
    df = make_ohlcv(n_rows=350)
    engine = s.StrategyEngine()
    results = engine.run_all(df)

    failures = {
        name: type(val).__name__
        for name, val in results.items()
        if not isinstance(val, pd.DataFrame)
    }

    assert not failures, (
        f"{len(failures)} strategy/strategies failed — "
        + ", ".join(f"{n}({t})" for n, t in sorted(failures.items()))
    )


# ---------------------------------------------------------------------------
# 3. cache round-trip
# ---------------------------------------------------------------------------

def test_cache_roundtrip(tmp_path, monkeypatch):
    """
    load_or_download_stock_data must:
    - write <SYMBOL>.pkl on first call,
    - return a non-empty DataFrame,
    - NOT call yfin.download on a second call whose range is covered (cache hit).
    """
    import cio.stock.data as data_mod

    # Redirect cache to the temp directory.
    monkeypatch.setattr(data_mod, "STOCK_CACHE_DIR", str(tmp_path))

    wide_frame = make_wide_ohlcv()
    call_count = {"n": 0}

    def fake_download(*args, **kwargs):
        call_count["n"] += 1
        return wide_frame

    monkeypatch.setattr(data_mod.yfin, "download", fake_download)

    # First call — should trigger download.
    result1 = data_mod.load_or_download_stock_data("TEST", "2024-02-01", "2024-03-01")
    # Second call — same range, must be a cache hit.
    result2 = data_mod.load_or_download_stock_data("TEST", "2024-02-01", "2024-03-01")

    pkl_file = tmp_path / "TEST.pkl"
    assert pkl_file.exists(), "TEST.pkl was not written to the cache directory"
    assert result1 is not None and not result1.empty, "First call returned empty result"
    assert result2 is not None and not result2.empty, "Second call returned empty result"
    assert call_count["n"] == 1, (
        f"yfin.download called {call_count['n']} times; expected 1 (cache hit on 2nd call)"
    )


# ---------------------------------------------------------------------------
# 4. latest_quote offline
# ---------------------------------------------------------------------------

def test_latest_quote_offline(monkeypatch):
    """latest_quote returns a dict with numeric 'price' and 'volume' keys."""
    import cio.stock.data as data_mod

    synthetic = make_ohlcv(n_rows=10)

    def fake_load(*args, **kwargs):
        return synthetic

    monkeypatch.setattr(data_mod, "load_or_download_stock_data", fake_load)
    # Pin market closed so latest_quote never reaches out for an extended-hours price.
    monkeypatch.setattr(data_mod, "nasdaq_trading_status", lambda *a, **k: 0)

    result = s.latest_quote("X")
    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert "price" in result, f"'price' key missing from result: {result}"
    assert "volume" in result, f"'volume' key missing from result: {result}"
    assert isinstance(result["price"], (int, float)), (
        f"'price' must be numeric, got {type(result['price'])}"
    )
    assert isinstance(result["volume"], (int, float)), (
        f"'volume' must be numeric, got {type(result['volume'])}"
    )


# ---------------------------------------------------------------------------
# 4b. latest_quote freshness signal (market_status / is_live / quote_kind)
# ---------------------------------------------------------------------------

def _freshness_quote(monkeypatch, *, status, bar_date, session_date, ext=None):
    """latest_quote with the market status and bar/session dates pinned.

    `ext` stubs extended_hours_price (the live pre/after-market fetch): None means
    'no extended-hours quote available'; a dict overlays a live price. Always pinned
    so the test never hits the network."""
    import cio.stock.data as data_mod
    from datetime import datetime

    df = pd.DataFrame(
        {"Open": [1, 2], "High": [1, 2], "Low": [1, 2], "Close": [10, 9], "Volume": [1, 2]},
        index=pd.to_datetime(["2020-01-01", bar_date]),
    )
    monkeypatch.setattr(data_mod, "load_or_download_stock_data", lambda *a, **k: df)
    monkeypatch.setattr(data_mod, "nasdaq_trading_status", lambda now=None: status)
    monkeypatch.setattr(data_mod, "extended_hours_price", lambda *a, **k: ext)
    monkeypatch.setattr(
        data_mod, "closest_trading_day",
        lambda x, method="prev": datetime.strptime(session_date, "%Y-%m-%d"),
    )
    return s.latest_quote("X")


def test_latest_quote_live_intraday(monkeypatch):
    """Market open + bar is the latest session -> live_intraday, is_live True."""
    q = _freshness_quote(monkeypatch, status=2, bar_date="2026-06-05", session_date="2026-06-05")
    assert q["market_status"] == "open"
    assert q["is_live"] is True
    assert q["quote_kind"] == "live_intraday"
    assert q["session_date"] == "2026-06-05"


def test_latest_quote_settled_close(monkeypatch):
    """Market closed + bar IS the latest session -> settled_close, not live."""
    q = _freshness_quote(monkeypatch, status=0, bar_date="2026-06-05", session_date="2026-06-05")
    assert q["market_status"] == "closed"
    assert q["is_live"] is False
    assert q["quote_kind"] == "settled_close"


def test_latest_quote_stale_close(monkeypatch):
    """Market closed + bar is BEHIND the latest session -> stale_close."""
    q = _freshness_quote(monkeypatch, status=0, bar_date="2026-06-04", session_date="2026-06-05")
    assert q["is_live"] is False
    assert q["quote_kind"] == "stale_close"
    assert q["date"] == "2026-06-04" and q["session_date"] == "2026-06-05"


def test_latest_quote_afterhours_overlays_post_market_price(monkeypatch):
    """Afterhours WITH an extended-hours print -> live_postmarket; `price` is the AH
    price, `close` stays the regular session close, and the change% is computed
    against that close (8.7 vs 9 = -3.33%). Regression for the MU bug where the
    $1211 session close was reported while the stock traded $1170 after hours."""
    q = _freshness_quote(
        monkeypatch, status=3, bar_date="2026-06-05", session_date="2026-06-05",
        ext={"price": 8.7, "source": "ibkr", "delayed": False},
    )
    assert q["market_status"] == "afterhours"
    assert q["is_live"] is True
    assert q["quote_kind"] == "live_postmarket"
    assert q["price"] == 8.7                 # the live AH price, not the 9 close
    assert q["close"] == 9 and q["regular_close"] == 9
    assert q["extended_hours_price"] == 8.7
    assert q["extended_hours_change_pct"] == -3.33     # (8.7-9)/9*100, computed here
    assert q["quote_source"] == "ibkr"
    assert q["extended_hours_delayed"] is False


def test_latest_quote_afterhours_delayed_flag_propagates(monkeypatch):
    """A delayed IBKR print is still used, but flagged delayed so the agent can warn."""
    q = _freshness_quote(
        monkeypatch, status=3, bar_date="2026-06-05", session_date="2026-06-05",
        ext={"price": 8.7, "source": "ibkr", "delayed": True},
    )
    assert q["quote_kind"] == "live_postmarket"
    assert q["price"] == 8.7
    assert q["extended_hours_delayed"] is True


def test_latest_quote_afterhours_without_ext_is_settled_not_live(monkeypatch):
    """Afterhours but NO extended-hours price available -> honest settled_close,
    NOT live_intraday (the original mislabel)."""
    q = _freshness_quote(
        monkeypatch, status=3, bar_date="2026-06-05", session_date="2026-06-05", ext=None,
    )
    assert q["market_status"] == "afterhours"
    assert q["is_live"] is False
    assert q["quote_kind"] == "settled_close"
    assert q["price"] == 9                    # falls back to the regular close
    assert q["extended_hours_price"] is None
    assert q["quote_source"] is None


def test_ibkr_extended_price_rejects_close_only(monkeypatch):
    """IBKR returning only the prior close (price_source 'close', no live tick) is
    NOT an after-hours print -> _ibkr_extended_price returns None so the caller
    falls through to an honest settled/stale close (validated live at 01:40 ET)."""
    import cio.stock.data as data_mod
    import cio.data.ibkr as ibkr_mod
    monkeypatch.setattr(ibkr_mod, "enabled", lambda: True)
    monkeypatch.setattr(
        ibkr_mod, "quote",
        lambda sym: {"MU": {"price": 1211.38, "price_source": "close", "delayed": True}},
    )
    assert data_mod._ibkr_extended_price("MU") is None
    # A real last print IS accepted, with the delayed flag carried through.
    monkeypatch.setattr(
        ibkr_mod, "quote",
        lambda sym: {"MU": {"price": 1170.27, "price_source": "last", "delayed": True}},
    )
    got = data_mod._ibkr_extended_price("MU")
    assert got == {"price": 1170.27, "source": "ibkr", "delayed": True}


def test_batch_ibkr_quotes_gating(monkeypatch):
    """One batched fetch only in pre/after-market AND when IBKR is enabled; a
    regular-session or closed refresh, or a disabled client, pays no socket."""
    import cio.stock.data as data_mod
    import cio.data.ibkr as ibkr_mod
    calls = []
    monkeypatch.setattr(ibkr_mod, "enabled", lambda: True)
    monkeypatch.setattr(ibkr_mod, "quote",
                        lambda syms: calls.append(list(syms)) or {"MU": {"price": 1}})

    # Regular session (status 2) -> no fetch.
    monkeypatch.setattr(data_mod, "nasdaq_trading_status", lambda *a, **k: 2)
    assert data_mod.batch_ibkr_quotes(["MU"]) == {} and calls == []

    # After-hours (status 3) + enabled -> forwards the whole list once.
    monkeypatch.setattr(data_mod, "nasdaq_trading_status", lambda *a, **k: 3)
    assert data_mod.batch_ibkr_quotes(["MU", "AAPL"]) == {"MU": {"price": 1}}
    assert calls == [["MU", "AAPL"]]

    # After-hours but IBKR disabled -> no fetch.
    monkeypatch.setattr(ibkr_mod, "enabled", lambda: False)
    calls.clear()
    assert data_mod.batch_ibkr_quotes(["MU"]) == {} and calls == []


def test_latest_quote_uses_injected_ibkr_quote_no_fetch(monkeypatch):
    """latest_quote(ibkr_quote=...) reuses the batched entry through the REAL
    extended_hours_price/_adapt path and never calls the single-symbol IBKR fetch
    (the batched watchlist path)."""
    import cio.stock.data as data_mod
    from datetime import datetime as _dt
    # Blow up if the single-symbol fetch is reached — it must not be.
    monkeypatch.setattr(data_mod, "_ibkr_extended_price",
                        lambda *a, **k: pytest.fail("single IBKR fetch must not run"))
    df = pd.DataFrame(
        {"Open": [1, 2], "High": [1, 2], "Low": [1, 2], "Close": [10, 9], "Volume": [1, 2]},
        index=pd.to_datetime(["2020-01-01", "2026-06-05"]),
    )
    monkeypatch.setattr(data_mod, "load_or_download_stock_data", lambda *a, **k: df)
    monkeypatch.setattr(data_mod, "nasdaq_trading_status", lambda *a, **k: 3)
    monkeypatch.setattr(data_mod, "closest_trading_day",
                        lambda x, method="prev": _dt(2026, 6, 5))
    out = s.latest_quote("MU", ibkr_quote={"price": 8.7, "price_source": "last", "delayed": True})
    assert out["price"] == 8.7
    assert out["quote_kind"] == "live_postmarket"
    assert out["quote_source"] == "ibkr"
    assert out["extended_hours_delayed"] is True


def test_latest_quote_premarket_overlays_pre_market_price(monkeypatch):
    """Pre-market WITH an extended-hours print -> live_premarket overlay."""
    q = _freshness_quote(
        monkeypatch, status=1, bar_date="2026-06-05", session_date="2026-06-05",
        ext={"price": 9.5, "source": "yfinance", "delayed": False},
    )
    assert q["market_status"] == "premarket"
    assert q["is_live"] is True
    assert q["quote_kind"] == "live_premarket"
    assert q["price"] == 9.5
    assert q["extended_hours_change_pct"] == 5.56      # (9.5-9)/9*100, rounded
    assert q["quote_source"] == "yfinance"


# ---------------------------------------------------------------------------
# 5. run_strategy on a DataFrame
# ---------------------------------------------------------------------------

def test_run_strategy_on_dataframe():
    """run_strategy(df, 'rsi') on a DataFrame must return a non-empty DataFrame."""
    df = make_ohlcv(n_rows=350)
    result = s.run_strategy(df, "rsi")
    assert isinstance(result, pd.DataFrame), (
        f"Expected DataFrame, got {type(result)}"
    )
    assert not result.empty, "run_strategy returned an empty DataFrame"


# ---------------------------------------------------------------------------
# 6. run_strategy raises ValueError when data is None
# ---------------------------------------------------------------------------

def test_run_strategy_no_data_raises(monkeypatch):
    """run_strategy with a symbol must raise ValueError when fetch returns None."""
    # run_strategy in cio/stock/__init__.py calls load_or_download_stock_data via
    # the name it imported into its own namespace — patch that binding directly.
    monkeypatch.setattr(s, "load_or_download_stock_data", lambda *a, **kw: None)

    with pytest.raises(ValueError, match="FAKE"):
        s.run_strategy("FAKE", "rsi")
