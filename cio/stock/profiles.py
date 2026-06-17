"""
profiles.py — situation-specific strategy profiles for the strategy engine.

Why profiles (research-backed, 2026-06):
  * Indicator *redundancy / multicollinearity*: stacking indicators from the same
    family (e.g. rsi + stoch + kdj are all short-window momentum oscillators)
    repeats one opinion three times instead of adding information. Best practice
    is one indicator per category: trend, momentum, volume, volatility/regime.
  * Indicator speed must match the decision horizon (Elder's Triple Screen):
    slow/smoothed indicators for position decisions, fast oscillators +
    volatility compression for short-term (wave/swing) timing.

Four-layer architecture (swing upgrade #2): every strategy here belongs to the
EXECUTION layer — entry timing, turn detection, stop placement. They are NOT
signal generators and must never originate a trade on their own. The "why" comes
from the catalyst + behavior layers (committee/TIRF) and the momentum layer
(cio.alpha.momentum); these oscillators only *time* a thesis those layers already
established. A full-bull TA composite with no catalyst is the ROKU trap, not a buy.

Each profile picks a small cross-category set tuned to one CIOAgent situation:

  committee — deep position decisions (slow, high-conviction, divergence-aware)
  monitor   — daily watchlist health check (change/event detection, cheap)
  swing     — short-term wave trading (setup + timing + turn detection)

Verdicts are derived from the engine's event columns (`c_*_BULL` / `c_*_BEAR`)
over a recent window — this replaces the old bundle._latest_signal heuristic,
which searched for "buy"/"sell" column names that no strategy ever produces and
therefore always returned "neutral".
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: How many recent bars count toward a verdict. Event columns fire on a single
#: bar; a 1-bar look would miss a cross from two days ago that is still
#: actionable at decision time.
DEFAULT_WINDOW = 5

PROFILES: dict[str, dict[str, Any]] = {
    # Position decisions: one slow trend line, one long-cycle momentum composite,
    # one classic oscillator (divergence-aware), one volume-flow confirm, and a
    # trend-vs-chop regime gauge so the committee knows which evidence to weight.
    "committee": {
        "strategies": ["trix", "kst", "rsi", "cmf", "er"],
        "window": 10,
        "description": (
            "Position-decision set: TRIX (slow trend), KST (long-cycle momentum), "
            "RSI (momentum + divergence), CMF (volume flow), ER (trend-vs-chop regime)."
        ),
    },
    # Daily watchlist pass: fast event detectors across categories — momentum
    # crossovers, zone exits, volume surprises, volatility compression. Tuned to
    # answer "did anything change overnight?", not "should we own this?".
    "monitor": {
        "strategies": ["macd", "stoch", "pvo", "squeeze"],
        "window": 3,
        "description": (
            "Daily change-detection set: MACD (momentum crossovers), STOCH (zone "
            "exits), PVO (volume surprise), SQUEEZE (volatility compression watch)."
        ),
    },
    # Short-term wave/swing trading: volatility-compression setup, J-confirmed
    # momentum cross timing, Fisher turn detection, volume-force confirmation,
    # and an adaptive trend line to ride the wave.
    "swing": {
        "strategies": ["squeeze", "kdj", "fisher", "efi", "vidya"],
        "window": 3,
        "layer": "execution",
        "description": (
            "Wave-trading set: SQUEEZE (coil/release setup), KDJ (entry timing), "
            "FISHER (turn detection), EFI (volume force), VIDYA (adaptive trend). "
            "EXECUTION layer only — times a catalyst, never originates one."
        ),
    },
}

#: Every profile in this module is the EXECUTION layer of the four-layer
#: architecture; callers reading a composite from here must AND it with the
#: catalyst/behavior/momentum layers (see cio.committee.tirf.gate), never alone.
EXECUTION_LAYER = "execution"

#: Aliases accepted anywhere a profile name is taken.
ALIASES = {"wave": "swing", "wma": "monitor", "watchlist": "monitor"}


def resolve_profile(name: str | None) -> str:
    key = (name or "committee").strip().lower()
    key = ALIASES.get(key, key)
    if key not in PROFILES:
        raise KeyError(f"unknown strategy profile '{name}' "
                       f"(available: {', '.join(sorted(PROFILES))})")
    return key


def list_profiles() -> dict[str, str]:
    """{profile: description} for tool/help surfaces."""
    return {k: v["description"] for k, v in PROFILES.items()}


def summarize_signals(signals_df, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Distill one strategy's signal DataFrame into a verdict dict.

    Counts `c_*_BULL` vs `c_*_BEAR` events over the last *window* bars. When a
    strategy emits no event columns (feature-only, e.g. fisher), falls back to
    the sign of its `f_*` CSLS/trend features on the last bar.

    Returns {"verdict": "bull"|"bear"|"neutral", "bulls": int, "bears": int,
             "events": [recent firing column names]}.
    """
    out = {"verdict": "neutral", "bulls": 0, "bears": 0, "events": []}
    try:
        import pandas as pd
        if signals_df is None or not isinstance(signals_df, pd.DataFrame) or signals_df.empty:
            return out
        recent = signals_df.tail(window)
        bull_cols = [c for c in signals_df.columns if c.endswith("_BULL")]
        bear_cols = [c for c in signals_df.columns if c.endswith("_BEAR")]
        if bull_cols or bear_cols:
            bulls = int(recent[bull_cols].fillna(0).sum().sum()) if bull_cols else 0
            bears = int(recent[bear_cols].fillna(0).sum().sum()) if bear_cols else 0
            fired = [
                c for c in bull_cols + bear_cols
                if (recent[c].fillna(0) == 1).any()
            ]
            out.update(bulls=bulls, bears=bears, events=fired)
        else:
            # Feature-only strategy: read the sign of signed trend/CSLS features.
            f_cols = [c for c in signals_df.columns if c.startswith("f_")]
            last = signals_df[f_cols].iloc[-1].dropna() if f_cols else None
            if last is not None and len(last):
                total = float(last.sum())
                out.update(bulls=int(total > 0), bears=int(total < 0))
        if out["bulls"] > out["bears"]:
            out["verdict"] = "bull"
        elif out["bears"] > out["bulls"]:
            out["verdict"] = "bear"
        return out
    except Exception as e:  # never break a caller over a summary
        log.debug("summarize_signals failed: %s", e)
        return out


def _bar_date(df) -> str | None:
    try:
        x = df.index[-1]
        return str(x.date()) if hasattr(x, "date") else str(x)
    except Exception:
        return None


def _is_unconfirmed(last_date, now_et) -> bool:
    """A daily bar is unconfirmed if it is today's (ET) session and the cash
    market has not closed yet (before 16:00 ET). Pure + directly testable."""
    try:
        return last_date == now_et.date() and now_et.hour < 16
    except Exception:
        return False


def _confirmed_view(df, now_et=None):
    """Drop the last bar when it is an in-progress session, so a daily swing
    thesis reads only confirmed bars (TradingView ``barstate.isconfirmed``). Two
    intraday re-runs then return the same verdict instead of repainting. Returns
    ``(df_view, asof_bar_date)``. ``now_et`` is injectable for testing."""
    try:
        if df is None or len(df) < 2:
            return df, _bar_date(df)
        if now_et is None:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            now_et = datetime.now(ZoneInfo("America/New_York"))
        last = df.index[-1]
        last_date = last.date() if hasattr(last, "date") else None
        if last_date is not None and _is_unconfirmed(last_date, now_et):
            view = df.iloc[:-1]
            return view, _bar_date(view)
        return df, _bar_date(df)
    except Exception:
        return df, _bar_date(df)


def _resolve_ohlc(symbol_or_df):
    """OHLC frame from a symbol, or pass a DataFrame straight through."""
    import pandas as pd
    if isinstance(symbol_or_df, pd.DataFrame):
        return symbol_or_df
    from datetime import datetime, timedelta
    from .data import load_or_download_stock_data
    end = datetime.now()
    start = end - timedelta(days=400)
    return load_or_download_stock_data(symbol_or_df, start, end)


def _composite_label(df, strategies, available):
    """(label, score) for *df*: continuous state score + dead-zone. Used for the
    current bar and (one confirmed bar back) for the stability check."""
    from .signal_state import strategy_state, composite_score, verdict_from_confidence
    confs = []
    for name in strategies:
        if available and name not in available:
            continue
        st = strategy_state(df, name)
        confs.append(st["confidence"] if st else None)
    score = composite_score(confs)
    return (verdict_from_confidence(score) if score is not None else "neutral"), score


def profile_signals(symbol_or_df, profile: str = "committee", *,
                    confirmed_only: bool = True) -> dict[str, Any]:
    """Run every strategy in *profile* and aggregate a CONTINUOUS, state-based
    composite.

    Returns:
      {"profile": name,
       "signals":   {strategy: "bull"|"bear"|"neutral"},   # dead-zoned state word
       "detail":    {strategy: {events, confidence, state, direction}},
       "composite": "bull"|"bear"|"neutral",               # dead-zoned mean conf
       "composite_score": float | None,                    # mean confidence [-1,1]
       "stability": "stable"|"fresh_flip",                 # vs one confirmed bar back
       "asof": bar_date | None}                            # confirmed bar evaluated

    Design (conv_turns 304-311): the verdict is driven by STATE confidence
    (``signal_state.strategy_state``), NOT by counting ``c_*_BULL``/``c_*_BEAR``
    events in a window — the latter cliffs when an event ages out (the EFI
    bull->neutral artifact). Events are reported in ``detail[*].events`` as
    supplementary triggers only. With ``confirmed_only`` (default) an in-progress
    daily bar is dropped so intraday re-runs are stable. Never raises for
    per-strategy data errors.
    """
    from . import run_strategy, list_strategies
    from .signal_state import strategy_state, verdict_from_confidence, composite_score

    key = resolve_profile(profile)
    spec = PROFILES[key]
    window = int(spec.get("window", DEFAULT_WINDOW))

    df = _resolve_ohlc(symbol_or_df)
    if confirmed_only:
        df, asof = _confirmed_view(df)
    else:
        asof = _bar_date(df)

    try:
        available = set(list_strategies())
    except Exception:
        available = set()

    signals: dict[str, str] = {}
    detail: dict[str, dict] = {}
    confs: list[float | None] = []
    for name in spec["strategies"]:
        if available and name not in available:
            continue
        # Layer 2 — events are supplementary triggers only, never scored.
        events: list[str] = []
        try:
            sdf = run_strategy(df, name)
            events = summarize_signals(sdf, window=window)["events"]
        except Exception as e:
            log.debug("profile %s: %s events failed: %s", key, name, e)
        # Layer 3 — STATE confidence (continuous) drives the verdict + dead-zone.
        st = strategy_state(df, name)
        c = st["confidence"] if st else None
        confs.append(c)
        signals[name] = verdict_from_confidence(c)
        detail[name] = {
            "events": events,
            "confidence": (round(c, 3) if c is not None else None),
            "state": (st["label"] if st else None),
            "direction": (st["direction"] if st else None),
        }

    score = composite_score(confs)
    composite = verdict_from_confidence(score) if score is not None else "neutral"

    # Layer 4 — stability: recompute one confirmed bar back; flag a fresh flip so
    # callers can step size down (regime-transition whipsaw guard).
    stability = "stable"
    try:
        if df is not None and len(df) > 8:
            prev_label, _ = _composite_label(df.iloc[:-1], spec["strategies"], available)
            if prev_label != composite:
                stability = "fresh_flip"
    except Exception:
        pass

    return {
        "profile": key,
        "signals": signals,
        "detail": detail,
        "composite": composite,
        "composite_score": (round(score, 3) if score is not None else None),
        "stability": stability,
        "asof": asof,
    }
