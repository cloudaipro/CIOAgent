"""
CIOAgent stock subsystem — fetch + cache + strategies.

Reused from AI4StockMarket/StockPricePrediction (reviewed / refactored):
  - fetch + cache .............. data.py
  - strategy engine ............ engine/  (indicators.py, parameter_grid.py, strategies/)

The engine is vendored byte-for-byte. Its modules use the source's flat import style
(`from indicators import ...`, `from parameter_grid import ...`,
 `from strategies.ta_util import ...`, `from strategies import *`), so we put the
engine dir on sys.path instead of editing 40+ files. Keeps the reuse faithful.
"""
import os
import sys
from datetime import datetime, timedelta

_ENGINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

from .data import (  # noqa: E402
    load_or_download_stock_data,
    latest_quote,
    closest_trading_day,
    fundamentals,
    normalize_symbol,
    STOCK_CACHE_DIR,
)
from .panel import render_panel, related_links  # noqa: E402


def get_quote(symbol):
    """Latest price / volume / OHLC for a symbol (req 1)."""
    return latest_quote(symbol)


def get_history(symbol, start, end):
    """OHLCV history DataFrame for a symbol over [start, end] (cached) (req 2)."""
    return load_or_download_stock_data(symbol, start, end)


class StrategyEngine:
    """Thin wrapper over the vendored SPP signal_creator — provides all strategies (req 3)."""

    def __init__(self, open="Open", high="High", low="Low", close="Close", volume="Volume"):
        from strategies.signal_creator import signal_creator  # engine on sys.path
        self._sc = signal_creator(open=open, high=high, low=low, close=close, volume=volume)

    def list_strategies(self):
        """Names of every available strategy."""
        return sorted(self._sc.creators.keys())

    def run(self, df, strategy, **params):
        """Buy/sell-signal DataFrame for one strategy on an OHLCV DataFrame."""
        return self._sc.create_signals(df, strategy, **params)

    def run_all(self, df, **params):
        """Run every strategy. Returns {name: signals_df}; a failing strategy maps to its Exception."""
        out = {}
        for name in self.list_strategies():
            try:
                out[name] = self._sc.create_signals(df, name, **params)
            except Exception as e:  # noqa: BLE001
                out[name] = e
        return out


_engine = None


def get_engine():
    """Lazily-built shared StrategyEngine."""
    global _engine
    if _engine is None:
        _engine = StrategyEngine()
    return _engine


def list_strategies():
    return get_engine().list_strategies()


def run_strategy(symbol_or_df, strategy, start=None, end=None, **params):
    """
    Run one strategy. `symbol_or_df` may be an OHLCV DataFrame or a ticker symbol
    (history is fetched/cached for the last ~400 days when start/end are omitted).
    """
    import pandas as pd

    if isinstance(symbol_or_df, pd.DataFrame):
        df = symbol_or_df
    else:
        if start is None or end is None:
            end = end or datetime.now()
            start = start or (end - timedelta(days=400))
        df = load_or_download_stock_data(symbol_or_df, start, end)
        if df is None:
            raise ValueError(f"No data for {symbol_or_df}")
    return get_engine().run(df, strategy, **params)


def list_strategy_profiles():
    """{profile: description} of situation-specific strategy sets."""
    from .profiles import list_profiles
    return list_profiles()


def run_strategy_profile(symbol_or_df, profile: str = "committee"):
    """Run a situation profile (committee/monitor/swing) and aggregate verdicts."""
    from .profiles import profile_signals
    return profile_signals(symbol_or_df, profile)


def render_indicators(symbol_or_df, profile: str = "committee", *, html: bool = False, **kw):
    """
    Render the technical-indicator chart (指標視覺化) and return the output path.

    Default (``indicators=None``) draws candles + MA20/60/120 with RSI/MACD/KDJ
    sub-panels and committee divergence + swing markers. For full control pass an
    autoplot-style ``indicators`` dict — ``{label: {"type": ..., ...}}`` with
    over-chart types (over/MA/Swings/flags) and below-chart types (below/RSI/
    MACD/multi/Crossover/threshold); see cio.stock.viz.spec for the contract.
    ``over_cap`` / ``below_cap`` bound how many of each are drawn.

    PNG by default (messages / committee PDF). ``html=True`` produces an
    interactive bokeh page for the dashboard (requires the optional bokeh dep).
    """
    from .viz import render_indicator_png, render_indicator_html
    if html:
        return render_indicator_html(symbol_or_df, profile, **kw)
    return render_indicator_png(symbol_or_df, profile, **kw)


__all__ = [
    "get_quote",
    "get_history",
    "list_strategies",
    "run_strategy",
    "list_strategy_profiles",
    "run_strategy_profile",
    "StrategyEngine",
    "get_engine",
    "load_or_download_stock_data",
    "latest_quote",
    "closest_trading_day",
    "fundamentals",
    "normalize_symbol",
    "render_panel",
    "render_indicators",
    "related_links",
    "STOCK_CACHE_DIR",
]
