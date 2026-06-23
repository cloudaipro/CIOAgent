"""
Stock data fetch + cache for CIOAgent.

Reused / refactored from AI4StockMarket/StockPricePrediction:
  - build_stocks_data.py : load_or_download_stock_data, closest_trading_day, vol_as_int
  - MyPyUtil/util.py     : numpy_datetime64_to_datetime, round_column_precision (vendored below)

Same cache mechanism + data structure as the source: one joblib pickle per symbol,
date-range aware, NYSE-calendar-aligned OHLCV DataFrame indexed by Date.
Dropped from the source: torch/random and the MyPyUtil/ai4stock_util imports (unused here).
"""
import os
import logging
from functools import lru_cache
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yfin
import joblib
import pandas_market_calendars as mcal

from ..timeutil import is_trading_day

log = logging.getLogger(__name__)

# Cache config — same per-symbol-pickle mechanism as the source codebase.
_DEFAULT_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "stock_cache",
)
STOCK_CACHE_DIR = os.environ.get("CIO_STOCK_CACHE_DIR", os.environ.get("CFO_STOCK_CACHE_DIR", _DEFAULT_CACHE_DIR))
STOCK_CACHING = True
STOCK_CACHE_FILE_TYPE = "pkl"  # pkl or csv


# --- vendored from MyPyUtil/util.py ---
def numpy_datetime64_to_datetime(np_datetime):
    """Convert a numpy.datetime64 to a python datetime."""
    if isinstance(np_datetime, np.datetime64):
        return np_datetime.astype("M8[ms]").astype("O")
    raise TypeError("Input must be a numpy.datetime64 object")


def round_column_precision(df, columns, ndigits):
    for col in columns:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: round(x, ndigits))
    return df


# --- reused from build_stocks_data.py ---
def closest_trading_day(date, method="next"):
    """Closest NYSE trading day to `date` ('next' or 'prev')."""
    if isinstance(date, str):
        date = datetime.strptime(date, "%Y-%m-%d")
    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(
        start_date=str(date.year - 1) + "-01-01",
        end_date=str(date.year + 1) + "-12-31",
    )
    return (
        numpy_datetime64_to_datetime(schedule[schedule.index >= date].index.values[0])
        if method == "next"
        else numpy_datetime64_to_datetime(schedule[schedule.index <= date].index.values[-1])
    )


# NASDAQ regular session in US/Eastern. Pre/after-market windows aren't traded on
# the daily OHLC bar this module fetches, so only the regular session matters for
# deciding whether the latest daily bar is still forming.
_NASDAQ_OPEN = time(hour=9, minute=30)
_NASDAQ_CLOSE = time(hour=16, minute=0)
_EASTERN = ZoneInfo("America/New_York")


def nasdaq_trading_status(now=None):
    """NASDAQ trading status for *now* (US/Eastern; defaults to current time).

    Mirrors AI4StockMarket/StockPricePrediction/build_stocks_data.NASDAQTradingStatus,
    but delegates holiday/weekend detection to timeutil.is_trading_day (single
    source of truth, with its own calendar cache + weekday fallback).

    Returns:
        0 — market closed (weekend or holiday)
        1 — pre-market (before 09:30)
        2 — regular hours (09:30–16:00)
        3 — after-market (after 16:00)
    """
    if now is None:
        now = datetime.now(_EASTERN)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_EASTERN)
    # Day-level check (weekend/holiday) is the single source of truth in timeutil;
    # this function only adds the intraday open/close granularity on top of it.
    if not is_trading_day(now.date()):
        return 0
    t = now.timetz().replace(tzinfo=None)
    if t < _NASDAQ_OPEN:
        return 1
    if t <= _NASDAQ_CLOSE:
        return 2
    return 3


def vol_as_int(tick_data):
    tick_data["Volume"] = tick_data["Volume"].astype(int)
    return tick_data


# Tickers use letters, digits and a few punctuation chars (".", "-", "^" indices,
# "=" futures). Anything else (path separators, "..", spaces) is stripped so a
# hostile symbol can never traverse out of the cache dir or name a pickle to load.
import re as _re
_SAFE_SYM = _re.compile(r"[^A-Za-z0-9.\-^=]")


def safe_symbol(symbol: str) -> str:
    """Sanitize a ticker for safe use in a filename. Strips path/illegal chars and
    leading dots; caps length. Raises ValueError if nothing valid remains."""
    s = _SAFE_SYM.sub("", str(symbol)).lstrip(".")[:24]
    if not s:
        raise ValueError(f"invalid symbol: {symbol!r}")
    return s


def _cache_path(symbol):
    os.makedirs(STOCK_CACHE_DIR, exist_ok=True)
    path = os.path.join(STOCK_CACHE_DIR, f"{safe_symbol(symbol)}.{STOCK_CACHE_FILE_TYPE}")
    # Defense in depth: never read/write a pickle outside the cache dir.
    root = os.path.realpath(STOCK_CACHE_DIR)
    if os.path.commonpath([os.path.realpath(path), root]) != root:
        raise ValueError(f"unsafe cache path for symbol {symbol!r}")
    return path


def load_or_download_stock_data(symbol, start, end):
    """
    Load OHLCV from the per-symbol cache or download it from Yahoo Finance.

    Returns a NYSE-aligned DataFrame [Open, High, Low, Close, Adj Close, Volume]
    indexed by Date, sliced to [start, end], or None on download error.
    """
    if isinstance(start, str):
        start = datetime.strptime(start, "%Y-%m-%d")
    if isinstance(end, str):
        end = datetime.strptime(end, "%Y-%m-%d")
    if start > end:
        start, end = end, start

    start = closest_trading_day(start, method="next")
    end = closest_trading_day(end, method="prev")

    stk_file = _cache_path(symbol)
    # During regular hours the latest daily bar is still forming, and right after
    # the close Yahoo revises it for ~15min — so a cached bar for the current
    # session is a stale intraday snapshot for "latest price" callers. When the
    # market is open or settling (status 2/3) AND the request reaches today's
    # session, bypass the cache and re-download so the newest bar is fetched fresh
    # (the download path overwrites the cache with the updated bar). Historical
    # ranges, and any request while the market is closed/pre-market (the most
    # recent session's bar is final), are served from cache as normal.
    now_et = datetime.now(_EASTERN)
    market_live = nasdaq_trading_status(now_et) in (2, 3)
    todays_session = closest_trading_day(now_et.replace(tzinfo=None), method="prev")
    serve_cache = not (market_live and end >= todays_session)
    cached_data = None
    if os.path.isfile(stk_file):
        try:
            if STOCK_CACHE_FILE_TYPE == "pkl":
                cached_data = joblib.load(stk_file)
            else:
                cached_data = pd.read_csv(stk_file).set_index("Date")
                cached_data.index = pd.to_datetime(cached_data.index)
            # Cache hit only if the cached range covers the request AND the request
            # doesn't reach a still-forming current session.
            if cached_data is not None and not cached_data.empty:
                if serve_cache and start >= cached_data.index[0] and end <= cached_data.index[-1]:
                    log.debug("Cache hit: %s", stk_file)
                    return vol_as_int(cached_data.loc[start:end].dropna())
        except Exception:
            cached_data = None

    try:
        start_interval, end_interval = start, end
        if cached_data is not None and not cached_data.empty:
            start_interval = min(cached_data.index[0].to_pydatetime(), start)
            end_interval = max(cached_data.index[-1].to_pydatetime(), end)

        new_data = yfin.download(
            [symbol],
            start=start_interval,
            end=end_interval + timedelta(days=1),
            auto_adjust=False,
            progress=False,
        ).dropna()
        if isinstance(new_data.columns, pd.MultiIndex):
            new_data = new_data.droplevel(1, axis=1)

        round_column_precision(new_data, ["Open", "High", "Low", "Close", "Adj Close"], 2)

        # Reindex onto the NYSE schedule so missing days are explicit.
        nyse = mcal.get_calendar("NYSE")
        schedule = nyse.schedule(start_date=start_interval, end_date=end_interval)
        schedule.index.name = new_data.index.name
        new_data = pd.concat([pd.DataFrame(index=schedule.index), new_data], axis=1, sort=True)

        if STOCK_CACHING:
            if STOCK_CACHE_FILE_TYPE == "pkl":
                joblib.dump(new_data, stk_file)
            else:
                new_data.to_csv(stk_file)
        log.debug("Downloaded and cached %s -> %s", symbol, stk_file)
        try:
            from cio.data import freshness
            freshness.record("yfinance", len(new_data))
        except Exception:
            pass
        return vol_as_int(new_data[start:end].dropna())
    except Exception as e:
        log.error("Error downloading data for %s: %s", symbol, e)
        return None


@lru_cache(maxsize=128)
def normalize_symbol(symbol: str) -> str:
    """
    Resolve a bare 4-digit TW code to a yfinance ticker.

    - Bare 4-digit string  → try "{code}.TW"; if data comes back empty try "{code}.TWO".
    - Already has a suffix (.TW / .TWO / other) → pass through unchanged.
    - Non-numeric / US symbols → pass through unchanged.

    The resolved symbol is cached so repeated calls with the same input are free.
    """
    s = symbol.strip()
    # Already has an exchange suffix or is non-numeric → pass through.
    if "." in s:
        return s
    if not s.isdigit():
        return s
    # Bare 4-digit numeric code — try .TW first, then .TWO.
    for suffix in (".TW", ".TWO"):
        candidate = s + suffix
        try:
            end = datetime.now()
            start = end - timedelta(days=10)
            df = load_or_download_stock_data(candidate, start, end)
            if df is not None and not df.empty:
                return candidate
        except Exception:
            pass
    # Nothing worked — default to .TW (caller will handle empty data).
    return s + ".TW"


_FUNDAMENTALS_FIELDS = (
    "name", "pe", "forward_pe", "pb", "yield_pct", "eps", "roe_pct", "margin_pct",
    "market_cap", "wk52_high", "wk52_low", "short_ratio", "shares_short",
    "revenue_q", "quoteType",
    # Alpha Hunter quality/earnings inputs (yfinance .info, fail to None):
    "forward_eps", "free_cash_flow", "revenue_growth_pct", "earnings_growth_pct",
)


def fundamentals(symbol: str) -> dict:
    """
    Fetch key fundamental data for *symbol* from yfinance.

    Every field defaults to None; never raises.  Fields:
      name, pe, pb, yield_pct, eps, roe_pct, margin_pct,
      market_cap, wk52_high, wk52_low, short_ratio, shares_short,
      revenue_q  (list of {"period": str, "value": float, "yoy_pct": float|None})
    """
    result = {f: None for f in _FUNDAMENTALS_FIELDS}
    try:
        import yfinance as yf
        tk = yf.Ticker(symbol)
        info = tk.info or {}

        def _get(key, transform=None):
            v = info.get(key)
            if v is None:
                return None
            try:
                return transform(v) if transform else v
            except Exception:
                return None

        result["name"] = _get("longName")
        result["pe"] = _get("trailingPE")
        result["forward_pe"] = _get("forwardPE")   # forward P/E (next-12m est earnings)
        result["pb"] = _get("priceToBook")
        result["yield_pct"] = _get("dividendYield")     # already a percent
        result["eps"] = _get("trailingEps")
        result["roe_pct"] = _get("returnOnEquity", lambda v: v * 100)   # fraction → %
        result["margin_pct"] = _get("profitMargins", lambda v: v * 100)  # fraction → %
        result["market_cap"] = _get("marketCap")
        result["wk52_high"] = _get("fiftyTwoWeekHigh")
        result["wk52_low"] = _get("fiftyTwoWeekLow")
        result["short_ratio"] = _get("shortRatio")
        result["shares_short"] = _get("sharesShort")
        result["quoteType"] = _get("quoteType")

        # Alpha Hunter inputs. forwardEps/trailingEps drive forward-EPS growth;
        # freeCashflow gates quality; revenueGrowth/earningsGrowth are yoy fractions
        # from yfinance -> percent. All optional; None when yfinance omits them.
        result["forward_eps"] = _get("forwardEps")
        result["free_cash_flow"] = _get("freeCashflow")
        result["revenue_growth_pct"] = _get("revenueGrowth", lambda v: v * 100)
        result["earnings_growth_pct"] = _get("earningsGrowth", lambda v: v * 100)

        # Quarterly revenue
        try:
            qis = tk.quarterly_income_stmt
            if qis is not None and "Total Revenue" in qis.index:
                rev_series = qis.loc["Total Revenue"].dropna().sort_index()
                # Build (period, value) pairs with YoY where prior year exists.
                items = []
                for ts, val in rev_series.items():
                    label = ts.strftime("%Y-Q") + str((ts.month - 1) // 3 + 1) if hasattr(ts, "strftime") else str(ts)
                    # Look for same quarter a year earlier.
                    yoy = None
                    try:
                        import pandas as pd
                        prior_ts = ts - pd.DateOffset(years=1)
                        # Find closest entry within 45 days.
                        diffs = abs(rev_series.index - prior_ts)
                        closest_idx = diffs.argmin()
                        if diffs[closest_idx].days <= 45:
                            prior_val = rev_series.iloc[closest_idx]
                            if prior_val and prior_val != 0:
                                yoy = (val - prior_val) / abs(prior_val) * 100
                    except Exception:
                        pass
                    items.append({"period": label, "value": float(val), "yoy_pct": yoy})
                result["revenue_q"] = items if len(items) >= 2 else None
        except Exception:
            result["revenue_q"] = None

    except Exception:
        pass

    return result


def latest_quote(symbol, lookback_days=10):
    """
    Latest price / volume / OHLC for a symbol (requirement 1), via the cached fetch.
    Returns a dict or None if no data could be fetched.
    """
    end = datetime.now()
    start = end - timedelta(days=max(lookback_days, 5) + 5)
    df = load_or_download_stock_data(symbol, start, end)
    if df is None or df.empty:
        return None
    row = df.iloc[-1]
    close = float(row["Close"])
    # Day change vs the previous trading session's close (None if only one row).
    prev_close = float(df.iloc[-2]["Close"]) if len(df) >= 2 else None
    change = (close - prev_close) if prev_close is not None else None
    change_pct = (change / prev_close * 100) if prev_close not in (None, 0) else None

    # Freshness signal so callers (the LLM agent) can tell a *live intraday* quote
    # from a *settled prior-session close*. The bar itself is always the latest real
    # price for the current market state; what matters is how it should be labelled.
    now_et = datetime.now(_EASTERN)
    status_code = nasdaq_trading_status(now_et)
    market_status = {0: "closed", 1: "premarket", 2: "open", 3: "afterhours"}[status_code]
    session_date = closest_trading_day(now_et.replace(tzinfo=None), method="prev").date()
    bar_date = df.index[-1].date()
    is_live = status_code in (2, 3) and bar_date == session_date
    if is_live:
        quote_kind = "live_intraday"
    elif bar_date == session_date:
        quote_kind = "settled_close"
    else:
        quote_kind = "stale_close"
    return {
        "symbol": symbol,
        "date": df.index[-1].strftime("%Y-%m-%d"),
        "open": float(row["Open"]),
        "high": float(row["High"]),
        "low": float(row["Low"]),
        "close": close,
        "price": close,
        "prev_close": prev_close,
        "change": change,
        "change_pct": change_pct,
        "volume": int(row["Volume"]),
        "market_status": market_status,
        "session_date": session_date.strftime("%Y-%m-%d"),
        "is_live": is_live,
        "quote_kind": quote_kind,
    }
