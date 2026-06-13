"""Layer 1 — Sector Ranking (FR-002).

Relative strength of the sector ETF universe: RS = 0.5*3M return + 0.5*6M return.
Returns a list ranked desc by RS. Each candidate stock is later tagged with the
sector ETF it maps to (SECTOR_OF) for reporting context.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import metrics

# Proposal §FR-002 sector universe.
SECTOR_ETFS = ["QQQ", "SMH", "IGV", "HACK", "BOTZ"]

# Coarse stock -> sector-ETF tag for the candidate "Sector" column. Anything not
# listed falls back to "QQQ" (broad NASDAQ). Best-effort labelling only — it does
# not gate scoring in v1 (PRD §FR-002).
SECTOR_OF = {
    # Semis -> SMH
    "NVDA": "SMH", "AVGO": "SMH", "AMD": "SMH", "QCOM": "SMH", "INTC": "SMH",
    "AMAT": "SMH", "MU": "SMH", "LRCX": "SMH", "KLAC": "SMH", "MRVL": "SMH",
    "ASML": "SMH", "ADI": "SMH", "ON": "SMH",
    # Software -> IGV
    "MSFT": "IGV", "ADBE": "IGV", "INTU": "IGV", "PANW": "IGV", "SNPS": "IGV",
    "CDNS": "IGV", "TEAM": "IGV", "CRM": "IGV",
    # Cyber -> HACK
    "CRWD": "HACK", "FTNT": "HACK",
}


def rank(fetch=None) -> list[dict]:
    """Ranked sector list. Offline-safe: ETFs that fail to fetch are dropped."""
    if fetch is None:
        from ..stock import data as stockdata
        fetch = stockdata.load_or_download_stock_data
    end = datetime.now()
    start = end - timedelta(days=300)
    out: list[dict] = []
    for etf in SECTOR_ETFS:
        try:
            df = fetch(etf, start, end)
            close = df["Close"] if (df is not None and not df.empty) else None
        except Exception:
            close = None
        r3 = metrics.ret_pct(close, metrics.BARS_3M) if close is not None else None
        r6 = metrics.ret_pct(close, metrics.BARS_6M) if close is not None else None
        if r3 is None and r6 is None:
            continue
        rs = 0.5 * (r3 or 0.0) + 0.5 * (r6 or 0.0)
        out.append({"ticker": etf, "ret_3m": _r(r3), "ret_6m": _r(r6), "rs": round(rs, 2)})
    out.sort(key=lambda d: d["rs"], reverse=True)
    return out


def sector_of(symbol: str) -> str:
    return SECTOR_OF.get(symbol.strip().upper(), "QQQ")


def _r(x):
    return round(x, 2) if x is not None else None
