"""
agent.py — Watchlist Monitoring Agent core.

For each security on the watchlist the WMA:
  1. gathers a data bundle (price / fundamentals / TA) — reused from the committee
  2. pulls overnight web headlines (Firecrawl, offline-safe)
  3. asks the ``wma`` model chain for a one-security assessment (PRD §7)

It is the FIRST layer of the architecture (PRD §11): one LLM call per security,
far cheaper than the full committee. Securities with high/critical events are
flagged for committee escalation rather than auto-analysed, to respect the
committee's per-run cost ceiling.

Every function is offline-safe — missing data / API errors degrade to a partial
assessment, never an exception.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from datetime import datetime, timezone

from .prompts import WMA_SYSTEM, MACRO_SNAPSHOT_SYSTEM

log = logging.getLogger(__name__)

# One LLM + one web call per security; keep concurrency modest so a big watchlist
# doesn't hammer the model backends or Firecrawl. Env-tunable.
MAX_CONC = int(os.getenv("CIO_WMA_CONCURRENCY", "4"))

_RECS = {"Buy", "Add", "Hold", "Monitor", "Reduce", "Sell"}
_STATUS = {"bullish", "neutral", "bearish"}
_IMPORTANCE = {"low", "medium", "high", "critical"}
_THESIS = {"unchanged", "positive", "negative"}
_SENSITIVITY = {"low", "medium", "high"}
_SENTIMENT = {"risk-on", "cautious", "risk-off"}
_GLOBAL_RISK = {"low", "elevated", "high"}


def _as_list(val) -> list:
    """Coerce a yaml value to a clean list of non-empty strings."""
    if val is None:
        return []
    if isinstance(val, str):
        s = val.strip()
        return [s] if s and s.lower() not in ("none", "n/a", "[]") else []
    if isinstance(val, (list, tuple)):
        return [str(x).strip() for x in val if str(x).strip()]
    return [str(val).strip()]


def _one_of(val, allowed: set[str], default: str) -> str:
    s = str(val or "").strip().lower()
    return s if s in allowed else default


def _rec(val) -> str:
    s = str(val or "").strip().title()
    return s if s in _RECS else "Monitor"


def _conviction(val) -> int:
    try:
        return max(0, min(100, int(float(val))))
    except (TypeError, ValueError):
        return 50


def _score100(val, default: int = 0) -> int:
    try:
        return max(0, min(100, int(float(val))))
    except (TypeError, ValueError):
        return default


def _recent_8k(filings, *, within_days: int = 3, today=None) -> bool:
    """True if a material-event 8-K was filed within the last *within_days*.

    The WMA escalates such names to the full committee even when the LLM read is
    calm — a fresh 8-K (acquisition, CEO change, buyback, guidance cut) is exactly
    the thesis-moving event the cheap daily layer exists to catch (PRD §11).
    Filings come from the committee bundle (cio.data.edgar); empty when EDGAR is
    not configured, so this is a no-op until the operator sets CIO_SEC_UA.
    """
    if not filings:
        return False
    from datetime import date as _date, datetime as _dt
    today = today or _date.today()
    for f in filings:
        if not isinstance(f, dict) or str(f.get("form", "")).upper() != "8-K":
            continue
        filed = f.get("filed")
        if not filed:
            continue
        try:
            d = _dt.strptime(str(filed), "%Y-%m-%d").date()
        except Exception:
            continue
        if 0 <= (today - d).days <= within_days:
            return True
    return False


def _headlines_text(news: list[dict]) -> str:
    """Render web results as a compact numbered block for the prompt."""
    if not news:
        return "none found"
    lines = []
    for i, item in enumerate(news, 1):
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        desc = (item.get("description") or "").strip()
        url = (item.get("url") or "").strip()
        line = f"{i}. {title}"
        if desc:
            line += f" — {desc}"
        if url:
            line += f" ({url})"
        lines.append(line)
    return "\n".join(lines) if lines else "none found"


async def _fetch_news(symbol: str, company: str | None, news_fn) -> list[dict]:
    """Overnight headlines via Firecrawl (or an injected *news_fn* for tests)."""
    if news_fn is None:
        from .. import web
        news_fn = web.search
    query = f"{company or symbol} stock news analyst rating earnings guidance"
    try:
        res = news_fn(query, limit=5)
        if inspect.isawaitable(res):
            res = await res
        return list(res or [])
    except Exception as e:
        log.debug("WMA news fetch failed for %s: %s", symbol, e)
        return []


def _skipped(symbol: str, reason: str) -> dict:
    """Assessment stub for a security we couldn't price (no LLM call spent)."""
    return {
        "ticker": symbol, "company": symbol,
        "overall_status": "neutral", "conviction_score": 0,
        "recommendation": "Monitor", "analyst_sentiment": "neutral",
        "event_importance": "low", "investment_thesis_change": "unchanged",
        "external_risk_score": 0, "macro_sensitivity": "low",
        "geopolitical_sensitivity": "low", "commodity_sensitivity": "low",
        "currency_sensitivity": "low",
        "key_positive_events": [], "key_negative_events": [],
        "new_risks": [], "upcoming_catalysts": [],
        "summary": reason, "escalate": False, "error": reason, "_raw": "",
    }


def _ta_composite_to_execution(ta_composite: str, ta_signals: dict) -> float:
    """Map the monitor-profile TA composite to an execution-layer score (0..100).

    Uses bull/bear vote counts from ta_signals for a finer score than the
    three-bucket composite alone.  Returns 50 (neutral) when data is absent.

    REAL: ta_composite and ta_signals come from profiles.profile_signals
    (resolved, "monitor") run inside gather_bundle — zero extra network calls.
    """
    composite = str(ta_composite or "").strip().lower()
    if not ta_signals:
        return {"bull": 75.0, "bear": 25.0, "neutral": 50.0}.get(composite, 50.0)
    verdicts = list(ta_signals.values())
    bulls = sum(1 for v in verdicts if str(v).lower() == "bull")
    bears = sum(1 for v in verdicts if str(v).lower() == "bear")
    total = len(verdicts)
    if total == 0:
        return 50.0
    net = (bulls - bears) / total   # -1..+1
    return max(0.0, min(100.0, 50.0 + net * 50.0))


def _num(v) -> float:
    """Coerce a rec-count to a number; non-numeric (None, str, ...) -> 0. Defends
    _consensus against string counts from the API (Richard pass-4 finding), matching
    the isinstance filter in coverage.analyst_count."""
    return float(v) if isinstance(v, (int, float)) else 0.0


def _consensus(counts: dict | None) -> float | None:
    """Map analyst strong_buy/buy/hold/sell/strong_sell counts to a 0..100 bull
    consensus. None when counts absent or empty."""
    if not isinstance(counts, dict):
        return None
    sb = _num(counts.get("strong_buy"))
    b = _num(counts.get("buy"))
    h = _num(counts.get("hold"))
    s = _num(counts.get("sell"))
    ss = _num(counts.get("strong_sell"))
    bull_w = sb * 2 + b
    bear_w = ss * 2 + s
    denom = bull_w + bear_w + h
    if denom == 0:
        return None
    net = (bull_w - bear_w) / denom  # -1..+1
    return max(0.0, min(100.0, 50.0 + net * 50.0))


def _analyst_behavior_score(analyst: dict | None) -> float | None:
    """Single-period behavior-layer score (0..100). None when absent. The
    trend-aware variant is _behavior_score_with_trend."""
    return _consensus(analyst)


def _behavior_score_with_trend(history: list[dict] | None) -> float | None:
    """Behavior-layer score folding in the period-over-period analyst trend (OD-4).

    base = latest-period consensus, nudged by HALF the latest-vs-prior delta: a
    rising (net upgrades) trend lifts the score, a falling (downgrades) trend cuts
    it — capturing "is the repricing starting?" not just the static level. *history*
    is newest-first (finnhub.analyst_recs_history). None when no usable period."""
    if not history:
        return None
    base = _consensus(history[0])
    if base is None:
        return None
    if len(history) >= 2:
        prior = _consensus(history[1])
        if prior is not None:
            base = base + 0.5 * (base - prior)
    return max(0.0, min(100.0, base))


def _catalyst_score_from_bundle(assessment: dict, bundle: dict | None) -> float | None:
    """Derive a catalyst-layer score from bundle filings + earnings data.

    REAL signals (all from bundle, no extra network calls):
      * upcoming_catalysts list from the LLM assessment
      * bundle['earnings']: Finnhub next-earnings date proximity
      * bundle['filings']: recent 8-K within 3 days
    Returns None when all absent (guard inactive — documented).
    """
    signals: list[float] = []

    cats = assessment.get("upcoming_catalysts") or []
    if cats:
        signals.append(80.0)

    if bundle:
        earnings = bundle.get("earnings")
        if isinstance(earnings, dict):
            from datetime import date as _date, datetime as _dt
            edate = earnings.get("date")
            if edate:
                try:
                    today = _date.today()
                    d = _dt.strptime(str(edate), "%Y-%m-%d").date()
                    days_away = (d - today).days
                    if 0 <= days_away <= 7:
                        signals.append(90.0)
                    elif 8 <= days_away <= 30:
                        signals.append(65.0)
                except Exception:
                    pass

        filings = bundle.get("filings") or []
        if _recent_8k(filings, within_days=3):
            signals.append(85.0)

    if not signals:
        return None
    return max(0.0, min(100.0, sum(signals) / len(signals)))


def _hold_decision_for_assessment(assessment: dict,
                                   bundle: dict | None = None) -> dict:
    """Hold decision with real TA-derived layer scores.

    Layer scoring — what is REAL vs OMITTED:

    execution [REAL]:
      profiles.profile_signals(resolved, "monitor") is run inside gather_bundle
      and stored as bundle['ta_composite'] / bundle['ta_signals'].
      _ta_composite_to_execution maps the monitor TA composite (macd/stoch/pvo/
      squeeze) bull/bear votes to 0..100.  Zero extra network calls.

    catalyst [REAL]:
      Three sub-signals (all from bundle, no extra calls):
        * upcoming_catalysts list from the LLM assessment
        * Finnhub next-earnings proximity (bundle['earnings'])
        * Recent 8-K filing within 3 days (bundle['filings'])
      Averaged when multiple fire.  Guard inactive when all absent (None).

    behavior [REAL]:
      Analyst recommendation consensus + period-over-period TREND (OD-4 closed):
      _behavior_score_with_trend folds the latest-vs-prior delta into the score, so
      net upgrades lift it and downgrades cut it. Uses finnhub.analyst_recs_history,
      which reuses the cached /stock/recommendation payload (no extra network call).

    momentum [REAL when available]:
      Reuses the alpha funnel's persisted momentum score (alpha_candidates) for
      this ticker via store.latest_candidate — zero extra network call (the funnel
      already computed it from OHLCV). Omitted (momentum guard inactive) only when
      the ticker was in no recent alpha run.

    regime_status: from regime.evaluate() (live QQQ); degrades to UNKNOWN offline.
    Never raises.
    """
    try:
        from ..alpha import hold, regime as regime_mod
        layer_scores: dict = {}

        # execution <- real TA composite from monitor bundle
        ta_composite = (bundle or {}).get("ta_composite", "")
        ta_signals = (bundle or {}).get("ta_signals") or {}
        layer_scores["execution"] = _ta_composite_to_execution(ta_composite, ta_signals)

        # catalyst <- filings + earnings proximity + upcoming_catalysts
        cat_score = _catalyst_score_from_bundle(assessment, bundle)
        if cat_score is not None:
            layer_scores["catalyst"] = cat_score

        # behavior <- analyst rec consensus + period-over-period trend (OD-4 delta).
        # analyst_recs_history reuses the same cached /stock/recommendation payload
        # the bundle already fetched, so the prior period costs no extra network call.
        beh_score = None
        try:
            sym = (bundle or {}).get("resolved") or assessment.get("symbol")
            if sym:
                from ..data import finnhub
                beh_score = _behavior_score_with_trend(
                    finnhub.analyst_recs_history(sym, periods=2))
        except Exception:
            beh_score = None
        if beh_score is None:                       # fall back to single-period bundle data
            beh_score = _analyst_behavior_score((bundle or {}).get("analyst"))
        if beh_score is not None:
            layer_scores["behavior"] = beh_score

        # momentum <- reuse the alpha funnel's persisted momentum score for this
        # ticker (zero fetch — the last alpha run already computed it from OHLCV).
        try:
            from ..alpha import store as alpha_store
            sym = (bundle or {}).get("resolved") or assessment.get("symbol")
            if sym:
                cand = alpha_store.latest_candidate(sym)
                if cand and isinstance(cand.get("momentum"), (int, float)):
                    layer_scores["momentum"] = float(cand["momentum"])
        except Exception:
            pass

        regime_result = regime_mod.evaluate()
        regime_status = (regime_result or {}).get("status", "UNKNOWN")
        return hold.hold_decision(layer_scores, regime_status)
    except Exception as e:
        log.debug("hold_decision for monitor symbol failed: %s", e)
        return {"action": "hold", "style": "neutral", "stop_mode": "standard",
                "reason": f"hold decision unavailable ({e})"}


async def monitor_symbol(symbol: str, *, bundle_fn=None, news_fn=None) -> dict:
    """Assess one security. Returns a normalized assessment dict (PRD §7).

    *bundle_fn* / *news_fn* are injectable for tests; both default to the live
    committee bundle and Firecrawl web search. Never raises.
    """
    import functools

    from ..committee import engine
    from ..committee.bundle import gather_bundle, format_bundle

    # Daily monitoring uses the cheap change-detection strategy profile
    # (cio.stock.profiles "monitor"), not the committee's position-decision set.
    bundle_fn = bundle_fn or functools.partial(gather_bundle, profile="monitor")
    try:
        bundle = await asyncio.to_thread(bundle_fn, symbol)
    except Exception as e:
        log.debug("WMA bundle failed for %s: %s", symbol, e)
        return _skipped(symbol, f"data error: {e}")

    if not bundle or bundle.get("resolved") is None:
        return _skipped(symbol, f"no data for {symbol}")

    resolved = bundle["resolved"]
    fund = bundle.get("fundamentals") or {}
    company = fund.get("name") or resolved
    bundle_text = format_bundle(bundle)

    news = await _fetch_news(resolved, company, news_fn)
    user_prompt = (
        f"You are analyzing: {resolved} ({company})\n\n"
        f"DATA:\n{bundle_text}\n\n"
        f"OVERNIGHT_HEADLINES:\n{_headlines_text(news)}"
    )

    raw = await engine.ask_role(WMA_SYSTEM, user_prompt, role_key="wma")
    parsed = engine.parse_yaml_block(raw)

    importance = _one_of(parsed.get("event_importance"), _IMPORTANCE, "low")
    thesis = _one_of(parsed.get("investment_thesis_change"), _THESIS, "unchanged")
    recent_8k = _recent_8k(bundle.get("filings"))
    assessment = {
        "ticker": resolved,
        "company": str(parsed.get("company") or company),
        "overall_status": _one_of(parsed.get("overall_status"), _STATUS, "neutral"),
        "conviction_score": _conviction(parsed.get("conviction_score")),
        "recommendation": _rec(parsed.get("recommendation")),
        "analyst_sentiment": _one_of(parsed.get("analyst_sentiment"), _STATUS, "neutral"),
        "event_importance": importance,
        "investment_thesis_change": thesis,
        "external_risk_score": _score100(parsed.get("external_risk_score"), 0),
        "macro_sensitivity": _one_of(parsed.get("macro_sensitivity"), _SENSITIVITY, "low"),
        "geopolitical_sensitivity": _one_of(parsed.get("geopolitical_sensitivity"), _SENSITIVITY, "low"),
        "commodity_sensitivity": _one_of(parsed.get("commodity_sensitivity"), _SENSITIVITY, "low"),
        "currency_sensitivity": _one_of(parsed.get("currency_sensitivity"), _SENSITIVITY, "low"),
        "key_positive_events": _as_list(parsed.get("key_positive_events")),
        "key_negative_events": _as_list(parsed.get("key_negative_events")),
        "new_risks": _as_list(parsed.get("new_risks")),
        "upcoming_catalysts": _as_list(parsed.get("upcoming_catalysts")),
        "summary": str(parsed.get("summary") or parsed.get("_raw") or "").strip(),
        # Escalate to the full committee on a thesis-breaking event (PRD §11):
        # a high/critical LLM read, a negative thesis change, or a fresh 8-K.
        "escalate": importance in ("high", "critical") or thesis == "negative" or recent_8k,
        "error": None,
        "_raw": raw,
    }
    # Wire 3: hold-decision call (swing upgrade #5). Surfaces action/reason in the
    # monitor report. Zero new LLM calls — real TA/catalyst/behavior from bundle.
    assessment["hold_decision"] = _hold_decision_for_assessment(assessment, bundle)
    return assessment


async def monitor_watchlist(symbols: list[str] | None = None, *,
                            bundle_fn=None, news_fn=None) -> list[dict]:
    """Assess every security on *symbols* (default: the active watchlist).

    Runs under a bounded semaphore so a large list stays within rate limits.
    Returns assessments in input order. Returns [] when there is no watchlist.
    """
    if symbols is None:
        from .. import watchlist
        wl = watchlist.active()
        symbols = wl["symbols"] if wl else []
    if not symbols:
        return []

    sem = asyncio.Semaphore(max(1, MAX_CONC))

    async def _bounded(sym: str) -> dict:
        async with sem:
            try:
                return await monitor_symbol(sym, bundle_fn=bundle_fn, news_fn=news_fn)
            except Exception as e:  # defensive — monitor_symbol shouldn't raise
                log.warning("WMA monitor_symbol crashed for %s: %s", sym, e)
                return _skipped(sym, f"error: {e}")

    return list(await asyncio.gather(*[_bounded(s) for s in symbols]))


async def global_macro_snapshot(*, news_fn=None) -> dict:
    """One shared macro/geopolitical read for the whole briefing (PRD §"Global
    Market Intelligence"). A SINGLE LLM call per watchlist run — not per security —
    so the WMA's first layer stays cheap. Never raises; degrades to a neutral read.
    """
    from ..committee import engine

    if news_fn is None:
        from .. import web
        news_fn = web.search
    query = ("global markets macro geopolitical oil inflation interest rates "
             "central bank sanctions today")
    try:
        res = news_fn(query, limit=6)
        if inspect.isawaitable(res):
            res = await res
        news = list(res or [])
    except Exception as e:
        log.debug("WMA macro snapshot news fetch failed: %s", e)
        news = []
    user_prompt = (
        "Summarise this morning's global backdrop for a portfolio manager.\n\n"
        f"OVERNIGHT_HEADLINES:\n{_headlines_text(news)}"
    )
    try:
        raw = await engine.ask_role(MACRO_SNAPSHOT_SYSTEM, user_prompt, role_key="macro")
    except Exception as e:
        log.debug("WMA macro snapshot failed: %s", e)
        raw = ""
    parsed = engine.parse_yaml_block(raw) if raw else {}
    return {
        "market_sentiment": _one_of(parsed.get("market_sentiment"), _SENTIMENT, "cautious"),
        "geopolitical_risk": _one_of(parsed.get("geopolitical_risk"), _GLOBAL_RISK, "low"),
        "commodity_risk": _one_of(parsed.get("commodity_risk"), _GLOBAL_RISK, "low"),
        "key_events": _as_list(parsed.get("key_events")),
        "summary": str(parsed.get("summary") or parsed.get("_raw") or "").strip(),
        "_raw": raw,
    }


def as_of_now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
