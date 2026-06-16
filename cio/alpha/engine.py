"""Alpha Hunter orchestrator (PRD §3).

Runs the funnel Market -> Sector -> per-ticker (Quality, Earnings, Momentum,
Ranking) and returns an AlphaResult. Pure orchestration over injectable fetchers
so the whole pipeline is unit-testable offline. No LLM calls.

Per ticker the engine fetches OHLCV exactly once (~400 days) and reuses that frame
for every layer. A ticker that fails quality is dropped before ranking; survivors
are sorted by Final Score and the top ``TOP_N`` become the watchlist.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from . import earnings, momentum, quality, regime, scoring, sectors, universe, metrics, coverage

log = logging.getLogger(__name__)

TOP_N = 20
_LOOKBACK_DAYS = 400


@dataclass
class AlphaResult:
    run_date: str
    regime: dict
    sectors: list = field(default_factory=list)
    candidates: list = field(default_factory=list)  # quality-PASS, ranked desc
    universe_size: int = 0

    def top(self, n: int = TOP_N) -> list:
        return self.candidates[:n]

    def select(self, threshold: float) -> list:
        """Ranked candidates whose Final Score meets *threshold* (>=). Candidates
        are already sorted desc, so this returns the qualifying prefix."""
        return [c for c in self.candidates
                if c.get("final") is not None and c["final"] >= threshold]


def _round(x, n=2):
    return round(x, n) if isinstance(x, (int, float)) else x


def _ohlcv(symbol, fetch):
    try:
        end = datetime.now()
        start = end - timedelta(days=_LOOKBACK_DAYS)
        df = fetch(symbol, start, end)
        if df is None or df.empty:
            return None
        return df
    except Exception:
        return None


def _market_cap(fund: dict) -> float | None:
    """Best-effort market cap (USD millions) from the fundamentals dict."""
    for key in ("market_cap", "marketCap", "marketCapitalization"):
        v = fund.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return None


def _evaluate_ticker(symbol, *, fetch, fundamentals_fn, surprises_fn, recs_fn,
                     institutional_fn, qqq_r3, qqq_r6, regime_status) -> dict | None:
    """Run all per-ticker layers. Returns a candidate dict, or None if no OHLCV."""
    df = _ohlcv(symbol, fetch)
    if df is None:
        return None
    close = df["Close"]

    fund = {}
    try:
        fund = fundamentals_fn(symbol) or {}
    except Exception:
        fund = {}

    q = quality.evaluate(fund, df)

    try:
        surprises = surprises_fn(symbol)
    except Exception:
        surprises = None

    # Coverage density (swing upgrade #1): neglected names get their catalyst
    # amplified. Degrades to neutral (edge 50, no effect) when recs unavailable.
    recs = None
    if recs_fn is not None:
        try:
            recs = recs_fn(symbol)
        except Exception:
            recs = None
    inst_pct = None
    if institutional_fn is not None:
        try:
            inst_pct = institutional_fn(symbol)
        except Exception:
            inst_pct = None
    cov = coverage.coverage_score(recs, _market_cap(fund), institutional_pct=inst_pct)

    e = earnings.evaluate(q["fwd_eps_growth"], df, surprises)
    m = momentum.evaluate(close, qqq_r3, qqq_r6)
    s = scoring.final_score(m["momentum_score"], m["trend_score"],
                            e["earnings_score"], q["revenue_growth"], df,
                            coverage_edge=cov["coverage_edge"])

    return {
        "ticker": symbol,
        "sector": sectors.sector_of(symbol),
        "regime": regime_status,
        "quality_pass": q["pass"],
        "quality_reasons": q["reasons"],
        "momentum": m["momentum_score"],
        "trend": m["trend_score"],
        "rs_pass": m["rs_pass"],
        "earnings": e["earnings_score"],
        "earnings_amplified": s.get("earnings_amplified"),
        "analyst_count": cov["analyst_count"],
        "coverage_edge": cov["coverage_edge"],
        "coverage_flag": cov["flag"],
        "revenue_growth": _round(q["revenue_growth"]),
        "fwd_eps_growth": _round(q["fwd_eps_growth"]),
        "surprise": e["surprise_score"],
        "volume_expansion": s["volume_expansion"],
        "final": s["final"],
    }


def run(*, universe_path=None, fetch=None, fundamentals_fn=None,
        surprises_fn=None, recs_fn=None, institutional_fn=None) -> AlphaResult:
    """Execute the full funnel. All fetchers default to the live data layer; pass
    your own for offline tests. Never raises — degrades to UNKNOWN/empty.

    *recs_fn* (symbol -> analyst-rec dict) feeds the coverage-density amplifier;
    defaults to ``finnhub.analyst_recs``. Pass ``recs_fn=lambda s: None`` to disable.
    *institutional_fn* (symbol -> institutional-ownership %) is the second coverage
    signal; defaults to the EDGAR 13F aggregator when available, else None (no
    effect). Pass your own for offline tests.
    """
    if fetch is None:
        from ..stock import data as stockdata
        fetch = stockdata.load_or_download_stock_data
    if fundamentals_fn is None:
        from ..stock import data as stockdata
        fundamentals_fn = stockdata.fundamentals
    if surprises_fn is None:
        from ..data import finnhub
        surprises_fn = finnhub.earnings_surprises
    if recs_fn is None:
        from ..data import finnhub
        recs_fn = finnhub.analyst_recs
    if institutional_fn is None:
        # Finnhub /stock/ownership (13F). Returns a real % on a premium key, None on
        # the free tier (403) or when FINNHUB_API_KEY is unset — coverage treats None
        # as "no signal", so the funnel stays back-compatible / offline-safe.
        from ..data import finnhub
        institutional_fn = finnhub.institutional_ownership_pct

    run_date = datetime.now().strftime("%Y-%m-%d")

    # Layer 0 — fetch QQQ once; classify regime + derive its 3M/6M returns.
    qqq_close = regime._qqq_close(fetch)
    reg = regime.classify(qqq_close)
    qqq_r3 = metrics.ret_pct(qqq_close, metrics.BARS_3M) if qqq_close is not None else None
    qqq_r6 = metrics.ret_pct(qqq_close, metrics.BARS_6M) if qqq_close is not None else None

    # Layer 1 — sector ranking (reported context).
    sect = sectors.rank(fetch)

    # Layers 2-4 — per ticker.
    syms = universe.load(universe_path)
    candidates: list[dict] = []
    for sym in syms:
        cand = _evaluate_ticker(
            sym, fetch=fetch, fundamentals_fn=fundamentals_fn,
            surprises_fn=surprises_fn, recs_fn=recs_fn, institutional_fn=institutional_fn,
            qqq_r3=qqq_r3, qqq_r6=qqq_r6, regime_status=reg["status"])
        if cand is not None:
            candidates.append(cand)

    ranked = sorted((c for c in candidates if c["quality_pass"]),
                    key=lambda c: c["final"], reverse=True)
    for i, c in enumerate(ranked, 1):
        c["rank"] = i

    return AlphaResult(run_date=run_date, regime=reg, sectors=sect,
                       candidates=ranked, universe_size=len(syms))
