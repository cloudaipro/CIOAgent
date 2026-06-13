"""Layer 0 — Market Regime (FR-001).

QQQ daily vs its 50/200-day SMAs + 50MA slope -> GREEN / YELLOW / RED.
  GREEN : QQQ > 50MA AND 50MA > 200MA AND 50MA rising
  RED   : QQQ < 200MA
  YELLOW: anything in between (below 50MA but holding 200MA, or 50<200, etc.)
UNKNOWN when QQQ data can't be fetched (offline-safe).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import metrics

QQQ = "QQQ"


def classify(close) -> dict:
    """Classify a regime from a QQQ Close series. Pure — no fetch."""
    ma50 = metrics.sma(close, 50)
    ma200 = metrics.sma(close, 200)
    if close is None or len(close) == 0 or ma50 is None or ma200 is None:
        return {"status": "UNKNOWN", "qqq": None, "ma50": ma50, "ma200": ma200,
                "slope_up": None, "detail": "insufficient QQQ history"}
    price = float(close.iloc[-1])
    rising = metrics.slope_up(close, 50)
    if price < ma200:
        status, detail = "RED", "QQQ below 200MA"
    elif price > ma50 and ma50 > ma200 and rising:
        status, detail = "GREEN", "QQQ>50MA>200MA, 50MA rising"
    else:
        status, detail = "YELLOW", "mixed: above 200MA but not full uptrend"
    return {"status": status, "qqq": round(price, 2), "ma50": round(ma50, 2),
            "ma200": round(ma200, 2), "slope_up": rising, "detail": detail}


def evaluate(fetch=None) -> dict:
    """Fetch QQQ (~400 calendar days) and classify. Offline-safe -> UNKNOWN."""
    close = _qqq_close(fetch)
    return classify(close)


def _qqq_close(fetch=None):
    """QQQ Close series, or None on any fetch failure."""
    if fetch is None:
        from ..stock import data as stockdata
        fetch = stockdata.load_or_download_stock_data
    try:
        end = datetime.now()
        start = end - timedelta(days=400)
        df = fetch(QQQ, start, end)
        if df is None or df.empty or "Close" not in df:
            return None
        return df["Close"]
    except Exception:
        return None
