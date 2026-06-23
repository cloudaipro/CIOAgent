"""F10 — cross-source convergence: one deterministic conviction signal.

The WorldMonitor cross-stream idea, applied to a single security: independent
signal streams pointing the SAME way is far stronger evidence than any one of them
alone. This blends five deterministic, zero-LLM streams CIOAgent already produces:

  1. TA composite        (cio.stock strategy profile — bull/bear/neutral)
  2. Analyst-rating delta (Finnhub recommendation trend, this period vs last)
  3. Earnings beat        (Finnhub last reported actual vs estimate)
  4. Insider cluster      (Finnhub open-market buys — F6 insider_net)
  5. News spike + tone    (GDELT volume surge + tone — F5 detect_spike)

into a single `{score, label, conviction, agreement, factors}` result. It is NOT
a price target and NOT an LLM judgement — it is a deterministic tally of where the
independent streams agree, meant to sit IN FRONT of the committee (cheap pre-filter
/ context line) and feed the Alpha funnel.

Design rules (same contract as cio.data):
  * Every factor is offline-safe and self-gating. A source that is off / empty
    contributes an INACTIVE factor (no vote), never an error.
  * `score` is the weighted mean over the ACTIVE factors only, so it reflects the
    direction of what we actually know; `conviction` separately captures HOW MUCH
    we know (active count x agreement). Absent sources lower conviction, not score.
  * Zero LLM, zero new dependencies.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# Per-factor weights. Insider open-market clusters and the TA composite are the
# highest-signal deterministic streams, so they weigh double. Overridable via env
# for tuning without a code change (see _weight).
_DEFAULT_WEIGHTS = {
    "ta": 2.0,
    "analyst": 1.0,
    "earnings": 1.0,
    "insider": 2.0,
    "news": 1.0,
}

# News tone magnitude needed before a spike votes a direction. Below this the
# surge is a catalyst of unclear sign -> active=False (no directional vote).
_TONE_DEADZONE = 1.0


def _weight(name: str) -> float:
    try:
        v = os.getenv(f"CIO_CONV_W_{name.upper()}")
        return float(v) if v is not None else _DEFAULT_WEIGHTS[name]
    except (TypeError, ValueError):
        return _DEFAULT_WEIGHTS[name]


def _factor(name: str, direction: int, active: bool, detail: str) -> dict:
    return {"name": name, "direction": int(direction), "active": bool(active),
            "weight": _weight(name), "detail": detail}


# --- individual factor extractors (each offline-safe, never raises) ---------

def _ta_factor(composite: str | None) -> dict:
    c = (composite or "").strip().lower()
    if c == "bull":
        return _factor("ta", +1, True, "TA composite bull")
    if c == "bear":
        return _factor("ta", -1, True, "TA composite bear")
    return _factor("ta", 0, False, f"TA composite {c or 'n/a'}")


def _rec_value(row: dict) -> float:
    """Net rating score for one recommendation snapshot (strong views weigh 2)."""
    g = lambda k: float(row.get(k) or 0)   # noqa: E731
    return g("strong_buy") * 2 + g("buy") - g("sell") - g("strong_sell") * 2


def _analyst_factor(history: list[dict] | None) -> dict:
    if not history or len(history) < 2:
        return _factor("analyst", 0, False, "analyst trend n/a")
    delta = _rec_value(history[0]) - _rec_value(history[1])
    if delta > 0:
        return _factor("analyst", +1, True, f"analyst upgrades (Δ{delta:+.0f})")
    if delta < 0:
        return _factor("analyst", -1, True, f"analyst downgrades (Δ{delta:+.0f})")
    return _factor("analyst", 0, False, "analyst trend flat")


def _earnings_factor(surprises: list[dict] | None) -> dict:
    if not surprises:
        return _factor("earnings", 0, False, "earnings surprise n/a")
    latest = surprises[0]
    actual, est = latest.get("actual"), latest.get("estimate")
    if latest.get("beat"):
        return _factor("earnings", +1, True, f"earnings beat ({actual} vs {est})")
    if actual is not None and est is not None and actual < est:
        return _factor("earnings", -1, True, f"earnings miss ({actual} vs {est})")
    return _factor("earnings", 0, False, "earnings in line")


def _insider_factor(net: dict | None) -> dict:
    if not net:
        return _factor("insider", 0, False, "insider data n/a")
    if net.get("cluster_buy"):
        return _factor("insider", +1, True,
                       f"insider cluster buy ({net.get('buy_count')} buyers)")
    buys, sells = net.get("buy_count") or 0, net.get("sell_count") or 0
    if buys == 0 and sells > 0 and (net.get("net_shares") or 0) < 0:
        return _factor("insider", -1, True, f"insider net selling ({sells} sells)")
    return _factor("insider", 0, False, "insider neutral")


def _news_factor(spike: dict | None) -> dict:
    if not spike:
        return _factor("news", 0, False, "no news spike")
    tone = float(spike.get("avg_tone") or 0.0)
    mult = spike.get("multiplier")
    burst = f"{mult}x" if mult else "new coverage"
    if tone >= _TONE_DEADZONE:
        return _factor("news", +1, True, f"positive news spike ({burst}, tone {tone:+.1f})")
    if tone <= -_TONE_DEADZONE:
        return _factor("news", -1, True, f"negative news spike ({burst}, tone {tone:+.1f})")
    return _factor("news", 0, False, f"news spike, neutral tone ({burst})")


# --- blend ------------------------------------------------------------------

def _label(score: int) -> str:
    if score >= 50:
        return "strong_bullish"
    if score >= 15:
        return "bullish"
    if score <= -50:
        return "strong_bearish"
    if score <= -15:
        return "bearish"
    return "mixed"


def _conviction(active_count: int, agreement: float, score: int) -> str:
    if active_count >= 3 and agreement >= 0.66 and abs(score) >= 50:
        return "high"
    if active_count >= 2 and agreement >= 0.60:
        return "medium"
    if active_count == 0:
        return "none"
    return "low"


def blend(factors: list[dict], symbol: str = "") -> dict:
    """Combine factor dicts into the convergence result. Pure / no I/O."""
    active = [f for f in factors if f["active"]]
    if not active:
        return {
            "symbol": symbol, "score": 0, "label": "no_signal",
            "conviction": "none", "agreement": 0.0, "active_count": 0,
            "factors": factors, "summary": f"{symbol}: no active signals".strip(),
        }
    tot_w = sum(f["weight"] for f in active)
    bull_w = sum(f["weight"] for f in active if f["direction"] > 0)
    bear_w = sum(f["weight"] for f in active if f["direction"] < 0)
    score = round(100 * sum(f["direction"] * f["weight"] for f in active) / tot_w)
    agreement = round(max(bull_w, bear_w) / tot_w, 2)
    label = _label(score)
    conv = _conviction(len(active), agreement, score)
    agree_names = [f["name"] for f in active
                   if (f["direction"] > 0) == (score >= 0) and f["direction"] != 0]
    summary = (f"{symbol}: {label} (score {score:+d}, {conv} conviction, "
               f"{len(active)} signals, {int(agreement*100)}% agree)").strip()
    return {
        "symbol": symbol, "score": score, "label": label, "conviction": conv,
        "agreement": agreement, "active_count": len(active),
        "agree_factors": agree_names, "factors": factors, "summary": summary,
    }


def convergence(symbol: str, *, ta_composite: str | None = None,
                analyst_history: list[dict] | None = None,
                insider: dict | None = None,
                include_news: bool = True,
                spike: dict | None = None) -> dict:
    """Deterministic cross-source convergence signal for *symbol*.

    Callers may pass already-fetched inputs to avoid refetching (the committee
    bundle passes ta_composite + insider it already has; analyst_recs_history is a
    cache hit on the same endpoint as analyst_recs). Anything not supplied is
    fetched here, each source self-gating. Never raises.

    include_news=False skips the GDELT spike lookup (use in cost-sensitive paths).
    """
    from .data import finnhub

    sym = (symbol or "").strip().upper()
    factors: list[dict] = []

    # 1. TA
    factors.append(_ta_factor(ta_composite))

    # 2. analyst trend delta (reuses the cached recommendation endpoint)
    try:
        hist = analyst_history if analyst_history is not None \
            else finnhub.analyst_recs_history(sym, periods=2)
    except Exception:
        hist = None
    factors.append(_analyst_factor(hist))

    # 3. earnings beat
    try:
        surprises = finnhub.earnings_surprises(sym, quarters=1)
    except Exception:
        surprises = None
    factors.append(_earnings_factor(surprises))

    # 4. insider cluster (F6)
    try:
        net = insider if insider is not None else finnhub.insider_net(sym)
    except Exception:
        net = None
    factors.append(_insider_factor(net))

    # 5. news spike + tone (F5)
    sp = spike
    if include_news and sp is None:
        try:
            from .watchlist_monitor import spike as spike_mod
            sp = spike_mod.detect_spike(sym)
        except Exception:
            sp = None
    factors.append(_news_factor(sp))

    return blend(factors, sym)


def format_line(result: dict) -> str:
    """One compact line for the committee bundle (mirrors the INSIDER/ANALYST style)."""
    if not result or result.get("active_count", 0) == 0:
        return "CONVERGENCE: N/A (no active signals)"
    agree = "  ".join(result.get("agree_factors", []))
    return (f"CONVERGENCE: {result['label']} score={result['score']:+d} "
            f"conviction={result['conviction']} "
            f"agree={int(result['agreement'] * 100)}% [{agree}]")
