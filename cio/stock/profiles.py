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


def profile_signals(symbol_or_df, profile: str = "committee") -> dict[str, Any]:
    """Run every strategy in *profile* and aggregate verdicts.

    Returns:
      {"profile": name,
       "signals": {strategy: "bull"|"bear"|"neutral"},          # compact view
       "detail":  {strategy: full summarize_signals dict},
       "composite": "bull"|"bear"|"neutral"}                     # majority vote

    Per-strategy failures degrade to omission (same contract the committee
    bundle has always had); never raises for data errors.
    """
    from . import run_strategy, list_strategies

    key = resolve_profile(profile)
    spec = PROFILES[key]
    window = int(spec.get("window", DEFAULT_WINDOW))

    try:
        available = set(list_strategies())
    except Exception:
        available = set()

    signals: dict[str, str] = {}
    detail: dict[str, dict] = {}
    for name in spec["strategies"]:
        if available and name not in available:
            continue
        try:
            df = run_strategy(symbol_or_df, name)
        except Exception as e:
            log.debug("profile %s: strategy %s failed: %s", key, name, e)
            continue
        summary = summarize_signals(df, window=window)
        signals[name] = summary["verdict"]
        detail[name] = summary

    votes = [v for v in signals.values() if v != "neutral"]
    composite = "neutral"
    if votes:
        bulls = votes.count("bull")
        bears = votes.count("bear")
        if bulls > bears:
            composite = "bull"
        elif bears > bulls:
            composite = "bear"

    return {"profile": key, "signals": signals, "detail": detail, "composite": composite}
