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


#: Regime -> position style (swing upgrade #5). A trending tape (GREEN) is where
#: the fat right tail of an AI-cycle re-rating lives, so let winners run on a
#: trailing stop (肥). A broken tape (RED) has no tail to catch — book gains, trade
#: tighter and shorter (勤). YELLOW is mixed: half-measures, neutral.
_STYLE_BY_REGIME = {
    "GREEN":  {"style": "肥", "hold": "run_winners", "stop_mode": "trailing"},
    "YELLOW": {"style": "neutral", "hold": "selective", "stop_mode": "standard"},
    "RED":    {"style": "勤", "hold": "book_gains", "stop_mode": "tight"},
}
_STYLE_UNKNOWN = {"style": "neutral", "hold": "selective", "stop_mode": "standard"}


def position_style(regime_status: str) -> dict:
    """Map a regime status (GREEN/YELLOW/RED/UNKNOWN) to a hold/stop style dict.

    Pure lookup; unknown statuses degrade to the neutral profile. Never raises.
    """
    return dict(_STYLE_BY_REGIME.get(str(regime_status or "").upper(), _STYLE_UNKNOWN))


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
