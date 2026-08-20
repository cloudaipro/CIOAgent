"""
bundle.py — gather_bundle / format_bundle

Pulls all pre-computed data for one symbol via the cio.stock facade.
Never raises; all fields default to None when data is missing.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)
# Shared evidence stream (same logger the chat tools use) so a committee run's
# EDGAR/Finnhub usage is visible in the logs too — tagged via=committee.
_evlog = logging.getLogger("cio.evidence")

# Lazy import so the stock subsystem is not loaded at module import time.
_stock = None


def _s():
    global _stock
    if _stock is None:
        from .. import stock as _s_mod
        _stock = _s_mod
    return _stock


# TA strategy selection now lives in cio.stock.profiles — situation-specific
# sets (committee / monitor / swing) chosen one-per-category to avoid the
# redundancy of the old hardcoded list (rsi+stoch+kdj were three oscillators
# from the same momentum family). The old _latest_signal helper is gone: it
# searched for "buy"/"sell" column names no strategy produces, so ta_signals
# was permanently "neutral".


def _external(symbol: str, is_etf: bool) -> tuple[list, Any, Any, Any]:
    """Opt-in EDGAR + Finnhub extras for the bundle.

    Returns (filings, analyst, earnings, insider). All sources are config-gated
    and offline-safe (see cio.data): unset CIO_SEC_UA / FINNHUB_API_KEY -> empty,
    no network. Analyst/earnings/insider are skipped for ETFs (not meaningful).
    This never raises, so a flaky source never breaks bundle gathering.
    """
    filings: list = []
    analyst = None
    earnings = None
    insider = None
    try:
        from .. import data
        edgar_on = bool(data.edgar._user_agent())
        finnhub_on = bool(data.finnhub._token())

        filings = data.recent_filings(symbol, limit=4)
        _evlog.info("tool=sec_filings symbol=%s configured=%s source=EDGAR filings=%d via=committee",
                    symbol, edgar_on, len(filings))

        if is_etf:
            _evlog.info("tool=analyst_ratings symbol=%s configured=%s skipped=etf via=committee",
                        symbol, finnhub_on)
            _evlog.info("tool=earnings_info symbol=%s configured=%s skipped=etf via=committee",
                        symbol, finnhub_on)
            _evlog.info("tool=insider_tx symbol=%s configured=%s skipped=etf via=committee",
                        symbol, finnhub_on)
        else:
            analyst = data.analyst_recs(symbol)
            _evlog.info("tool=analyst_ratings symbol=%s configured=%s source=Finnhub found=%s via=committee",
                        symbol, finnhub_on, analyst is not None)
            earnings = data.earnings_calendar(symbol)
            _evlog.info("tool=earnings_info symbol=%s configured=%s source=Finnhub found=%s via=committee",
                        symbol, finnhub_on, earnings is not None)
            insider = data.insider_net(symbol)
            _evlog.info("tool=insider_tx symbol=%s configured=%s source=Finnhub found=%s via=committee",
                        symbol, finnhub_on, insider is not None)
    except Exception as e:
        log.debug("external data fetch failed for %s: %s", symbol, e)
    return filings, analyst, earnings, insider


def _convergence_on() -> bool:
    return (os.getenv("CIO_CONVERGENCE") or "1").strip().lower() not in (
        "0", "false", "no", "off")


def _swing_context(stock, symbol: str, confirmed_asof=None) -> dict | None:
    """Deterministic daily-bar levels needed to discuss an executable swing.

    The indicator profile supplies direction/state; this block supplies price
    structure and volatility so the CIO can define a trigger and invalidation
    without inventing levels. Never raises.
    """
    try:
        import pandas as pd
        from datetime import timedelta

        end = datetime.now()
        df = stock.get_history(symbol, end - timedelta(days=260), end)
        if df is None or df.empty:
            return None
        if confirmed_asof:
            asof = str(confirmed_asof)[:10]
            mask = [str(x.date() if hasattr(x, "date") else x)[:10] <= asof
                    for x in df.index]
            df = df.loc[mask]
        if df.empty or not {"High", "Low", "Close"}.issubset(df.columns):
            return None
        df = df.dropna(subset=["Close"])
        if df.empty:
            return None

        close = pd.to_numeric(df["Close"], errors="coerce").dropna()
        high = pd.to_numeric(df["High"], errors="coerce")
        low = pd.to_numeric(df["Low"], errors="coerce")
        if close.empty:
            return None
        prev = pd.to_numeric(df["Close"], errors="coerce").shift(1)
        true_range = pd.concat(
            [(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1
        ).max(axis=1)
        volume = (pd.to_numeric(df["Volume"], errors="coerce")
                  if "Volume" in df.columns else None)

        def _round(value, places=2):
            try:
                return round(float(value), places) if pd.notna(value) else None
            except (TypeError, ValueError):
                return None

        latest = close.iloc[-1]
        ma20 = close.tail(20).mean() if len(close) >= 20 else None
        ma50 = close.tail(50).mean() if len(close) >= 50 else None

        def _ret(bars):
            if len(close) <= bars or close.iloc[-bars - 1] == 0:
                return None
            return (latest / close.iloc[-bars - 1] - 1.0) * 100.0

        avg_volume = volume.tail(20).mean() if volume is not None and len(volume) >= 20 else None
        rel_volume = (volume.iloc[-1] / avg_volume
                      if volume is not None and avg_volume not in (None, 0) else None)
        return {
            "bar_date": str(df.index[-1].date() if hasattr(df.index[-1], "date")
                            else df.index[-1])[:10],
            "close": _round(latest),
            "ma20": _round(ma20),
            "ma50": _round(ma50),
            "high_20": _round(high.tail(20).max()),
            "low_20": _round(low.tail(20).min()),
            "atr14": _round(true_range.tail(14).mean()),
            "relative_volume_20": _round(rel_volume),
            "return_5d_pct": _round(_ret(5)),
            "return_20d_pct": _round(_ret(20)),
            "distance_ma20_pct": _round(
                ((latest / ma20) - 1.0) * 100.0 if ma20 not in (None, 0) else None),
        }
    except Exception as e:
        log.debug("swing context failed for %s: %s", symbol, e)
        return None


def _swing_regime(stock) -> dict | None:
    """QQQ trend regime for swing sizing/holding posture; offline-safe."""
    try:
        from ..alpha import regime
        return regime.evaluate(fetch=stock.get_history)
    except Exception as e:
        log.debug("swing market regime failed: %s", e)
        return None


def _convergence(symbol: str, is_etf: bool, ta_composite: str, insider):
    """F10 cross-source convergence for the bundle. Reuses the TA composite +
    insider already gathered (analyst trend is a cache hit; earnings-surprise +
    news-spike are fetched inside, each self-gating). ETFs skip it (per-name
    signals don't apply). Off with CIO_CONVERGENCE=0. Never raises."""
    if is_etf or not _convergence_on():
        return None
    try:
        from .. import convergence as conv_mod
        result = conv_mod.convergence(symbol, ta_composite=ta_composite, insider=insider)
        _evlog.info("tool=convergence symbol=%s label=%s score=%d conviction=%s active=%d via=committee",
                    symbol, result.get("label"), result.get("score", 0),
                    result.get("conviction"), result.get("active_count", 0))
        return result
    except Exception as e:
        log.debug("convergence failed for %s: %s", symbol, e)
        return None


def gather_bundle(symbol: str, profile: str = "committee") -> dict[str, Any]:
    """
    Gather all available data for *symbol*.

    *profile* selects the situation-specific TA strategy set
    (see cio.stock.profiles: committee / monitor / swing).

    Returns a dict with keys:
      symbol, resolved, quote, fundamentals, ta_signals, ta_profile,
      ta_composite, is_etf, as_of, filings, analyst, earnings

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
            "ta_profile": profile,
            "ta_composite": "neutral",
            "ta_detail": {},
            "ta_composite_score": None,
            "ta_stability": None,
            "ta_asof": None,
            "swing_context": None,
            "market_regime": None,
            "is_etf": False,
            # Local time (CIO convention everywhere else; utcnow is deprecated and
            # would also skew TIRF recency scoring against local-dated evidence).
            "as_of": datetime.now().isoformat(),
            "filings": [],
            "analyst": None,
            "earnings": None,
            "insider": None,
            "convergence": None,
        }

    # TA signals — situation profile, best-effort (profile_signals never raises
    # for per-strategy data errors; a failing strategy is simply omitted).
    ta_composite = "neutral"
    ta_detail: dict[str, Any] = {}
    ta_composite_score = None
    ta_stability = None
    ta_asof = None
    try:
        prof = s.run_strategy_profile(resolved, profile)
        ta_signals = prof["signals"]
        ta_composite = prof["composite"]
        ta_detail = prof.get("detail") or {}
        ta_composite_score = prof.get("composite_score")
        ta_stability = prof.get("stability")
        ta_asof = prof.get("asof")
    except Exception as e:
        log.debug("strategy profile %s failed for %s: %s", profile, resolved, e)

    swing_context = _swing_context(s, resolved, ta_asof) if profile == "swing" else None
    market_regime = _swing_regime(s) if profile == "swing" else None

    filings, analyst, earnings, insider = _external(resolved, is_etf)
    convergence = _convergence(resolved, is_etf, ta_composite, insider)

    return {
        "symbol": symbol,
        "resolved": resolved,
        "quote": quote,
        "fundamentals": fund,
        "ta_signals": ta_signals,
        "ta_profile": profile,
        "ta_composite": ta_composite,
        "ta_detail": ta_detail,
        "ta_composite_score": ta_composite_score,
        "ta_stability": ta_stability,
        "ta_asof": ta_asof,
        "swing_context": swing_context,
        "market_regime": market_regime,
        "is_etf": is_etf,
        "as_of": datetime.now().isoformat(),
        "filings": filings,
        "analyst": analyst,
        "earnings": earnings,
        "insider": insider,
        "convergence": convergence,
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
        f"PE: {_fmt(f.get('pe'))}  FWD_PE: {_fmt(f.get('forward_pe'))}  "
        f"PB: {_fmt(f.get('pb'))}  "
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
        composite = bundle.get("ta_composite")
        prof = bundle.get("ta_profile")
        extra = f"  (composite:{composite}, profile:{prof})" if composite and prof else ""
        lines.append(f"TA_SIGNALS: {ta_str}{extra}")
        state_parts = []
        if bundle.get("ta_composite_score") is not None:
            state_parts.append(f"score:{bundle.get('ta_composite_score')}")
        if bundle.get("ta_stability"):
            state_parts.append(f"stability:{bundle.get('ta_stability')}")
        if bundle.get("ta_asof"):
            state_parts.append(f"confirmed_asof:{bundle.get('ta_asof')}")
        if state_parts:
            lines.append("TA_STATE: " + "  ".join(state_parts))
        detail = bundle.get("ta_detail") or {}
        detail_parts = []
        for name, item in detail.items():
            if not isinstance(item, dict):
                continue
            bits = [
                f"confidence={_fmt(item.get('confidence'))}",
                f"state={_fmt(item.get('state'))}",
                f"direction={_fmt(item.get('direction'))}",
            ]
            events = item.get("events") or []
            if events:
                bits.append("events=" + ",".join(str(x) for x in events[:4]))
            detail_parts.append(f"{name}[{' '.join(bits)}]")
        if detail_parts:
            lines.append("TA_DETAIL: " + "  ".join(detail_parts))
    else:
        lines.append("TA_SIGNALS: N/A (no source)")

    swing = bundle.get("swing_context") or {}
    if swing:
        lines.append("SWING_CONTEXT: " + "  ".join(
            f"{key}:{_fmt(value)}" for key, value in swing.items()))
    regime = bundle.get("market_regime") or {}
    if regime:
        lines.append(
            f"MARKET_REGIME: {regime.get('status') or 'UNKNOWN'}  "
            f"detail:{regime.get('detail') or 'N/A (no source)'}  "
            f"QQQ:{_fmt(regime.get('qqq'))}  MA50:{_fmt(regime.get('ma50'))}  "
            f"MA200:{_fmt(regime.get('ma200'))}"
        )

    # SEC EDGAR filings — primary-source material events / reports (opt-in).
    filings = bundle.get("filings") or []
    if filings:
        parts = [f"{f.get('form')}({f.get('filed')})" for f in filings[:4] if isinstance(f, dict)]
        lines.append("FILINGS: " + "  ".join(parts))
    else:
        lines.append("FILINGS: N/A (no source)")

    # Finnhub analyst recommendation trend — buy/hold/sell counts (opt-in).
    rec = bundle.get("analyst")
    if rec:
        lines.append(
            f"ANALYST: strong_buy={_fmt(rec.get('strong_buy'))}  buy={_fmt(rec.get('buy'))}  "
            f"hold={_fmt(rec.get('hold'))}  sell={_fmt(rec.get('sell'))}  "
            f"strong_sell={_fmt(rec.get('strong_sell'))}  (period {rec.get('period') or 'N/A'})"
        )
    else:
        lines.append("ANALYST: N/A (no source)")

    # Finnhub earnings calendar — next scheduled report + estimates (opt-in).
    earn = bundle.get("earnings")
    if earn:
        lines.append(
            f"EARNINGS: next={earn.get('date') or 'N/A'}  "
            f"eps_est={_fmt(earn.get('eps_estimate'))}  eps_actual={_fmt(earn.get('eps_actual'))}"
        )
    else:
        lines.append("EARNINGS: N/A (no source)")

    # Finnhub insider transactions — net buy/sell pressure + conviction cluster (opt-in).
    ins = bundle.get("insider")
    if ins:
        cluster = "  CLUSTER-BUY" if ins.get("cluster_buy") else ""
        lines.append(
            f"INSIDER: buys={_fmt(ins.get('buy_count'))}  sells={_fmt(ins.get('sell_count'))}  "
            f"net_shares={_fmt(ins.get('net_shares'))}{cluster}"
        )
    else:
        lines.append("INSIDER: N/A (no source)")

    # F10 cross-source convergence — deterministic tally of where the independent
    # streams (TA / analyst trend / earnings / insider / news) agree.
    conv = bundle.get("convergence")
    if conv and conv.get("active_count"):
        from .. import convergence as conv_mod
        lines.append(conv_mod.format_line(conv))
    else:
        lines.append("CONVERGENCE: N/A (no active signals)")

    lines.append(f"IS_ETF: {bundle.get('is_etf', False)}")

    return "\n".join(lines)
