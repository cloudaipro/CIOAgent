"""
bundle.py — gather_bundle / format_bundle

Pulls all pre-computed data for one symbol via the cio.stock facade.
Never raises; all fields default to None when data is missing.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

# Lazy import so the stock subsystem is not loaded at module import time.
_stock = None


def _s():
    global _stock
    if _stock is None:
        from .. import stock as _s_mod
        _stock = _s_mod
    return _stock


# TA strategies to sample — small, representative set
_TA_STRATEGIES = ["rsi", "macd", "stoch", "trix", "kdj"]


def _latest_signal(signals_df) -> str:
    """Extract the most recent signal label from a strategy result DataFrame."""
    try:
        import pandas as pd
        if signals_df is None or not isinstance(signals_df, pd.DataFrame):
            return "neutral"
        # Look for a 'signal' or 'Signal' column; fall back to last row buy/sell cols
        for col in ("signal", "Signal"):
            if col in signals_df.columns:
                val = signals_df[col].dropna().iloc[-1]
                return str(val).lower()
        # Some strategies produce buy/sell boolean columns
        row = signals_df.iloc[-1]
        buy_cols = [c for c in row.index if "buy" in c.lower()]
        sell_cols = [c for c in row.index if "sell" in c.lower()]
        if buy_cols and sell_cols:
            if any(row[c] for c in buy_cols):
                return "bull"
            if any(row[c] for c in sell_cols):
                return "bear"
        return "neutral"
    except Exception:
        return "neutral"


def gather_bundle(symbol: str) -> dict[str, Any]:
    """
    Gather all available data for *symbol*.

    Returns a dict with keys:
      symbol, resolved, quote, fundamentals, ta_signals, is_etf, as_of

    If the symbol cannot be resolved (no data), resolved=None and the engine
    should abort with a clean "no data" result.
    """
    s = _s()
    resolved = None
    try:
        resolved = s.normalize_symbol(symbol)
    except Exception:
        resolved = symbol

    quote: dict | None = None
    fund: dict | None = None
    ta_signals: dict[str, str] = {}
    is_etf = False

    try:
        quote = s.get_quote(resolved)
    except Exception as e:
        log.debug("get_quote failed for %s: %s", resolved, e)

    try:
        fund = s.fundamentals(resolved)
        if fund:
            # quoteType may come through info; detect ETF
            qt = fund.get("quoteType")
            is_etf = bool(qt and str(qt).upper() == "ETF")
    except Exception as e:
        log.debug("fundamentals failed for %s: %s", resolved, e)

    # Check whether we have any useful data at all
    has_data = (quote is not None and any(v is not None for v in quote.values())) or (
        fund is not None and any(v is not None for v in fund.values())
    )
    if not has_data:
        return {
            "symbol": symbol,
            "resolved": None,
            "quote": None,
            "fundamentals": None,
            "ta_signals": {},
            "is_etf": False,
            "as_of": datetime.utcnow().isoformat(),
        }

    # TA signals — best-effort, each guarded
    available = []
    try:
        available = s.list_strategies()
    except Exception:
        pass

    for name in _TA_STRATEGIES:
        if name not in available:
            continue
        try:
            df = s.run_strategy(resolved, name)
            ta_signals[name] = _latest_signal(df)
        except Exception as e:
            log.debug("strategy %s failed for %s: %s", name, resolved, e)

    return {
        "symbol": symbol,
        "resolved": resolved,
        "quote": quote,
        "fundamentals": fund,
        "ta_signals": ta_signals,
        "is_etf": is_etf,
        "as_of": datetime.utcnow().isoformat(),
    }


def _fmt(val, suffix="") -> str:
    """Format a value for display; None → 'N/A (no source)'."""
    if val is None:
        return "N/A (no source)"
    if isinstance(val, float):
        return f"{val:.2f}{suffix}"
    return f"{val}{suffix}"


def format_bundle(bundle: dict) -> str:
    """
    Render bundle as a compact labeled text block for prompt injection.

    Missing fields are shown as 'N/A (no source)' per spec.
    """
    lines: list[str] = []
    sym = bundle.get("resolved") or bundle.get("symbol", "?")
    as_of = bundle.get("as_of", "")
    lines.append(f"SYMBOL: {sym}  (as_of: {as_of})")

    q = bundle.get("quote") or {}
    lines.append(
        f"PRICE: {_fmt(q.get('close'))}  "
        f"CHANGE: {_fmt(q.get('change_pct'), '%')}  "
        f"VOLUME: {_fmt(q.get('volume'))}"
    )

    f = bundle.get("fundamentals") or {}
    lines.append(
        f"PE: {_fmt(f.get('pe'))}  PB: {_fmt(f.get('pb'))}  "
        f"YIELD: {_fmt(f.get('yield_pct'), '%')}  EPS: {_fmt(f.get('eps'))}"
    )
    lines.append(
        f"ROE: {_fmt(f.get('roe_pct'), '%')}  MARGIN: {_fmt(f.get('margin_pct'), '%')}  "
        f"MKTCAP: {_fmt(f.get('market_cap'))}"
    )
    lines.append(
        f"52W_HIGH: {_fmt(f.get('wk52_high'))}  "
        f"52W_LOW: {_fmt(f.get('wk52_low'))}"
    )

    rev_q = f.get("revenue_q")
    if rev_q:
        rev_parts = []
        for item in rev_q[-4:]:
            yoy = f.get("yoy_pct") if isinstance(item, dict) else None
            yoy_str = f" YoY:{item.get('yoy_pct', 'N/A (no source)')}%" if isinstance(item, dict) else ""
            period = item.get("period", "?") if isinstance(item, dict) else "?"
            val = item.get("value") if isinstance(item, dict) else None
            rev_parts.append(f"{period}={_fmt(val)}{yoy_str}")
        lines.append("REVENUE_Q: " + "  ".join(rev_parts))
    else:
        lines.append("REVENUE_Q: N/A (no source)")

    ta = bundle.get("ta_signals") or {}
    if ta:
        ta_str = "  ".join(f"{k}:{v}" for k, v in ta.items())
        lines.append(f"TA_SIGNALS: {ta_str}")
    else:
        lines.append("TA_SIGNALS: N/A (no source)")

    lines.append(f"IS_ETF: {bundle.get('is_etf', False)}")

    return "\n".join(lines)
