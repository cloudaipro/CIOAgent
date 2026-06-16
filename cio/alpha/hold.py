"""Hold management (swing upgrade #5, 2026-06).

Implements "肥 with a guard": in a trending regime, let winners run on a trailing
stop to capture the AI-cycle right tail — but never buy-and-forget. The position is
held only while the four causal layers stay green; the moment the **catalyst layer
breaks** (the reason you entered is gone), exit. This is the discipline that makes
肥 viable in a mature 2026 AI tape, where the real risk is a trend→bust regime flip
giving back the whole run.

Consumes the four-layer scores (``cio.committee.tirf.gate``) and the regime style
(``cio.alpha.regime.position_style``). Pure, deterministic, never-raises.
"""
from __future__ import annotations

from . import regime

# Catalyst score at/under this = the thesis has broken -> exit regardless of timing.
CATALYST_BREAK = 45.0
# Behavior score at/over this = the move is widely recognised (crowded / euphoric)
# -> trim into strength, don't add. The edge ("not yet priced") is gone.
BEHAVIOR_EUPHORIC = 85.0


def hold_decision(layer_scores: dict, regime_status: str,
                  *, in_profit: bool = True) -> dict:
    """Decide hold / trim / exit for an open position. Returns a decision dict.

    {action: 'hold'|'trim'|'exit', style, stop_mode, reason}

    Priority (highest first):
      1. catalyst broken            -> EXIT  (Rule 1/5: the why is gone)
      2. trending + behavior crowded -> TRIM  (edge priced in; bank some)
      3. trending, layers intact     -> HOLD on trailing stop (肥)
      4. broken regime               -> trim/tighten (勤)
    """
    style = regime.position_style(regime_status)
    scores = layer_scores or {}
    catalyst = scores.get("catalyst")
    behavior = scores.get("behavior")
    status = str(regime_status or "").upper()

    # 1. Catalyst-break guard — overrides everything, even a green execution layer.
    if catalyst is not None and catalyst <= CATALYST_BREAK:
        return _d("exit", style, f"catalyst layer broke ({catalyst:.0f} <= {CATALYST_BREAK:.0f}) — thesis gone")

    # 2. Crowded/euphoric behaviour in a live trend -> trim into strength.
    if status == "GREEN" and behavior is not None and behavior >= BEHAVIOR_EUPHORIC:
        return _d("trim", style, f"behavior layer euphoric ({behavior:.0f}) — edge priced in, trim into strength")

    # 3. Trending tape, thesis intact -> ride it on a trailing stop (肥).
    if status == "GREEN":
        return _d("hold", style, "trend intact + catalyst alive — run winner on trailing stop (肥)")

    # 4. Broken tape -> defensive (勤): book gains / tighten.
    if status == "RED":
        action = "trim" if in_profit else "exit"
        return _d(action, style, "regime RED — defensive, book gains / tighten (勤)")

    # Mixed (YELLOW/UNKNOWN) -> hold but on a standard (non-trailing) stop.
    return _d("hold", style, "mixed regime — hold selectively on standard stop")


def _d(action, style, reason) -> dict:
    return {"action": action, "style": style["style"],
            "stop_mode": style["stop_mode"], "reason": reason}
