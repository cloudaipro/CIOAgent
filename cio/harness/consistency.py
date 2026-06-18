"""consistency.py — V1: deterministic trade-plan consistency gate.

Why this exists: in conv_turns 326-329 the agent recommended a MCHP shallow-
pullback LIMIT buy at $97.50 while the stock was at $99 intraday (+4.7%) and the
market was up. The user caught the flaw the agent did not: a limit *below* the
current price only fills if the stock falls while the market does not — i.e. the
stock underperforms the market — which is exactly the relative-strength anomaly
(Rule 2c) the agent itself uses to flag "something may be wrong inside." The
entry trigger and the anomaly rule were never jointly enforced.

This module enforces them jointly. It cross-checks an emitted trade plan against
the agent's own rule set BEFORE the plan leaves the agent, turning "the user
happened to catch it" into "an automated check always catches it". Pure,
deterministic, never-raises. No model call.

R1 SEVERITY SEMANTICS (aligned with the stored rules this enforces —
swing_entry_threesome_rule "Rule 6" element 2, swing_screen_catalyst_rule
"Rule 2c", and playbook swing_watchlist_reevaluation steps 7/9/10, all of which
are BINARY: any index-up / symbol-down fill is relative weakness): R1 firing at
ANY severity means the entry is NOT a valid naked entry — a catalyst check is
mandatory first. The 2% ``rel_strength_pct_2c`` threshold only escalates
WARN -> BLOCK; a WARN is "catalyst check required, not yet valid", never "small,
ignore". ``detail.catalyst_check_required`` is True for both WARN and BLOCK, so a
sub-threshold dip is not silently passable. This keeps the deterministic check and
the binary prompt/playbook rules in agreement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from .models import CheckResult, Finding, Severity


@dataclass
class TradePlan:
    """Structured form of an emitted entry/exit plan. All fields optional so a
    partial plan still checks the rules it has data for (missing data ⇒ that
    rule is skipped, never a crash)."""
    symbol: str = ""
    side: str = "long"
    entry_kind: str = ""            # pullback | limit | breakout | market
    entry_price: float | None = None
    current_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    market_bias: str = ""          # up | flat | down (assumed over entry window)
    market_move_pct: float | None = None  # expected market move over window (%); inferred from bias if None
    structure_low: float | None = None
    pct_today: float | None = None  # today's % move, e.g. 4.7 for +4.7%
    at_upper_band: bool = False
    entry_date: str = ""           # ISO yyyy-mm-dd
    earnings_date: str = ""        # ISO yyyy-mm-dd
    min_hold_days: int | None = None
    rel_strength_pct_2c: float = 2.0   # Rule 2c threshold (relative deviation %)


DEFAULT_CFG = {
    "min_rr": 1.0,            # R3 floor
    "extended_pct": 4.0,     # R5 "extended day" threshold (% move today)
    "earnings_blackout_days": 7,  # R4 pre-earnings buffer
    # R1: when only market direction is known, assume a representative move so the
    # RELATIVE underperformance (stock drop + market gain) is measured, not just the
    # stock's drop. +0.7% mirrors the "strong tape" day in conv_turns 329. Override
    # per-plan with TradePlan.market_move_pct.
    "assumed_move_pct": {"up": 0.7, "flat": 0.0, "down": -0.7},
}


def check_trade_plan(plan: TradePlan, cfg: dict | None = None) -> CheckResult:
    """Run the rule set over a plan. Returns a CheckResult (never raises)."""
    c = {**DEFAULT_CFG, **(cfg or {})}
    findings: list[Finding] = []
    ctx: dict[str, Any] = {"symbol": plan.symbol, "entry_kind": plan.entry_kind}

    for rule in (_r1_rel_weakness, _r2_plan_coherence, _r3_rr_floor,
                 _r4_earnings_window, _r5_chase):
        try:
            f = rule(plan, c)
        except Exception:        # never let one rule break the gate
            f = None
        if f is not None:
            findings.append(f)
    return CheckResult(findings=findings, context=ctx)


# --- rules ------------------------------------------------------------------
def _r1_rel_weakness(p: TradePlan, c: dict) -> Finding | None:
    """The load-bearing rule. A pullback/limit entry below current price that is
    expected to fill while the market is up or flat implies the stock falls
    relative to the market ⇒ relative weakness ⇒ trips Rule 2c. BLOCK if the
    implied underperformance clears the 2c threshold, else WARN."""
    if p.entry_kind not in ("pullback", "limit"):
        return None
    if p.entry_price is None or p.current_price is None:
        return None
    if not (p.entry_price < p.current_price):
        return None
    if p.market_bias not in ("up", "flat"):
        return None
    price_drop = (p.current_price - p.entry_price) / p.current_price * 100.0
    mv = p.market_move_pct
    if mv is None:
        mv = c["assumed_move_pct"].get(p.market_bias, 0.0)
    # Relative underperformance the fill implies = how far the stock drops PLUS how
    # far the market rises over the same window. This is the Rule 2c quantity.
    implied_underperf = price_drop + max(0.0, mv)
    block = implied_underperf >= p.rel_strength_pct_2c
    sev = Severity.BLOCK if block else Severity.WARN
    # Severity only escalates; it does NOT make a sub-threshold dip "safe to enter".
    # The stored rules (Rule 6 / Rule 2c / playbook step 7) are binary: any index-up,
    # symbol-down fill is relative weakness ⇒ catalyst check required ⇒ not a valid
    # naked entry. So R1 at ANY severity carries catalyst_check_required=True.
    verdict = ("strong relative weakness — do not enter" if block else
               "relative weakness — NOT a valid naked entry until a catalyst check "
               "clears it (Rule 6 / swing_screen_catalyst_rule)")
    return Finding(
        code="R1_REL_WEAKNESS",
        severity=sev,
        message=(
            f"{p.entry_kind} entry at {p.entry_price:g} below current {p.current_price:g} "
            f"with market '{p.market_bias}': a fill implies ~{implied_underperf:.1f}% "
            f"underperformance vs market — {verdict}. (Rule 2c threshold "
            f"{p.rel_strength_pct_2c:g}%.)"
        ),
        fix=("Run a catalyst check first; present the entry only if the catalyst clears. "
             "Otherwise condition the pullback on the market also pulling back (RS "
             "maintained), or switch to a breakout/confirmation entry above current price."),
        detail={"implied_underperf_pct": round(implied_underperf, 2),
                "price_drop_pct": round(price_drop, 2),
                "assumed_market_move_pct": round(mv, 2),
                "threshold_pct": p.rel_strength_pct_2c,
                "catalyst_check_required": True},
    )


def _r2_plan_coherence(p: TradePlan, c: dict) -> Finding | None:
    """A long plan must have stop < entry and target > entry. Incoherent ⇒ BLOCK."""
    if p.side != "long" or p.entry_price is None:
        return None
    bad = []
    if p.stop_price is not None and p.stop_price >= p.entry_price:
        bad.append("stop>=entry")
    if p.target_price is not None and p.target_price <= p.entry_price:
        bad.append("target<=entry")
    if not bad:
        return None
    return Finding(
        code="R2_PLAN_COHERENCE",
        severity=Severity.BLOCK,
        message=f"Incoherent long plan: {', '.join(bad)}.",
        fix="For a long: stop below entry, target above entry.",
        detail={"violations": bad},
    )


def _r3_rr_floor(p: TradePlan, c: dict) -> Finding | None:
    """Reward:risk below the floor ⇒ WARN (the 0.75:1 the user implicitly hit)."""
    if None in (p.entry_price, p.stop_price, p.target_price):
        return None
    risk = p.entry_price - p.stop_price
    reward = p.target_price - p.entry_price
    if risk <= 0:
        return None  # coherence handled by R2
    rr = reward / risk
    if rr >= c["min_rr"]:
        return None
    return Finding(
        code="R3_RR_FLOOR",
        severity=Severity.WARN,
        message=f"R:R {rr:.2f}:1 below floor {c['min_rr']:.2f}:1.",
        fix="Tighten the stop toward structure or raise the target; do not enter below the floor.",
        detail={"rr": round(rr, 2), "risk": round(risk, 4), "reward": round(reward, 4)},
    )


def _r4_earnings_window(p: TradePlan, c: dict) -> Finding | None:
    """A swing whose forced exit (earnings - blackout) lands before a real hold
    completes ⇒ WARN. Catches the 7/29 forced exit vs 8/5 earnings squeeze."""
    if not p.entry_date or not p.earnings_date or p.min_hold_days is None:
        return None
    ed = _parse(p.entry_date)
    ea = _parse(p.earnings_date)
    if ed is None or ea is None:
        return None
    forced_exit = ea - timedelta(days=int(c["earnings_blackout_days"]))
    earliest_complete = ed + timedelta(days=int(p.min_hold_days))
    if earliest_complete <= forced_exit:
        return None
    return Finding(
        code="R4_EARNINGS_WINDOW",
        severity=Severity.WARN,
        message=(f"Swing window too short: min hold to {earliest_complete.isoformat()} "
                 f"exceeds forced exit {forced_exit.isoformat()} (earnings {p.earnings_date})."),
        fix="Wait for a post-earnings setup, or size down for an event trade.",
        detail={"forced_exit": forced_exit.isoformat(),
                "earliest_complete": earliest_complete.isoformat()},
    )


def _r5_chase(p: TradePlan, c: dict) -> Finding | None:
    """Market/breakout entry on an extended day at the upper band ⇒ WARN (chase)."""
    if p.entry_kind not in ("market", "breakout"):
        return None
    if p.pct_today is None or not p.at_upper_band:
        return None
    if p.pct_today < c["extended_pct"]:
        return None
    return Finding(
        code="R5_CHASE",
        severity=Severity.WARN,
        message=(f"{p.entry_kind} entry on +{p.pct_today:g}% day at the upper band — "
                 "chasing an extended move."),
        fix="Wait for confirmation or a pullback that keeps relative strength.",
        detail={"pct_today": p.pct_today},
    )


def _parse(s: str) -> date | None:
    try:
        return date.fromisoformat(s)
    except (ValueError, TypeError):
        return None
