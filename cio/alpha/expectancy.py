"""Expectancy KPI (swing upgrade #3b, 2026-06).

Replaces win-rate as the headline metric. Win-rate ignores magnitude and rewards
the disposition effect (cut winners to lock the W, hold losers to avoid the L) — the
exact behaviour that sinks retail. A 65%-win book can bleed money (avg loss > avg
win); a 45%-win book can compound hard (let winners run). Expectancy captures both:

    expectancy = win% * avg_win - loss% * avg_loss          (per trade, in %)

We also report it in R-multiples (return per unit of risk taken), annualize it via
turnover (a fat per-trade edge traded 3x/year is worse than a thin one traded 50x),
and expose ``oos_check`` — the real teeth behind ROKU rule 6: an out-of-sample
expectancy that collapses below half the in-sample figure is the overfit signal,
NOT a high win-rate. Pure, deterministic, never-raises.
"""
from __future__ import annotations

import math

# Overfit flag fires when OOS expectancy < this fraction of in-sample expectancy.
OOS_DEGRADE_LIMIT = 0.5
# Minimum closed trades before any expectancy figure is treated as meaningful.
MIN_SAMPLE = 20


def _nums(trades, key):
    out = []
    for t in trades or []:
        v = t.get(key) if isinstance(t, dict) else getattr(t, key, None)
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            out.append(float(v))
    return out


def expectancy(trades, key: str = "pct") -> dict:
    """Per-trade expectancy over closed trades, keyed on 'pct' (or 'r_multiple').

    Returns: {n, win_rate, loss_rate, avg_win, avg_loss, expectancy, profit_factor,
              payoff_ratio, key, low_sample}. Empty/None -> a zeroed dict.
    """
    vals = _nums(trades, key)
    n = len(vals)
    base = {"n": n, "win_rate": 0.0, "loss_rate": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "expectancy": 0.0, "profit_factor": None, "payoff_ratio": None,
            "key": key, "low_sample": n < MIN_SAMPLE}
    if n == 0:
        return base
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v < 0]   # negative numbers
    win_rate = len(wins) / n
    loss_rate = len(losses) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0   # positive magnitude
    exp = win_rate * avg_win - loss_rate * avg_loss
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    base.update(
        win_rate=round(win_rate, 4),
        loss_rate=round(loss_rate, 4),
        avg_win=round(avg_win, 3),
        avg_loss=round(avg_loss, 3),
        expectancy=round(exp, 4),
        profit_factor=(round(gross_win / gross_loss, 3) if gross_loss > 0 else None),
        payoff_ratio=(round(avg_win / avg_loss, 3) if avg_loss > 0 else None),
    )
    return base


def annualized(expectancy_pct: float, trades_per_year: float) -> float | None:
    """Compound a per-trade %% expectancy over a year's turnover. None if invalid.

    annual = (1 + e)^turns - 1, with e expressed as a fraction (so 1.35 -> 0.0135).
    """
    try:
        e = float(expectancy_pct) / 100.0
        turns = float(trades_per_year)
    except (TypeError, ValueError):
        return None
    if turns <= 0 or (1.0 + e) <= 0:
        return None
    return round(((1.0 + e) ** turns - 1.0) * 100.0, 2)


def turns_per_year(avg_hold_days: float, deployment: float = 1.0) -> float | None:
    """Capital turnover/year for an avg hold, scaled by fraction of time deployed."""
    try:
        d = float(avg_hold_days)
        dep = max(0.0, min(1.0, float(deployment)))
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    return round(365.0 / d * dep, 2)


def sqn(trades, key: str = "r_multiple") -> float | None:
    """System Quality Number = mean(R)/stdev(R) * sqrt(n) (Van Tharp). None if n<2."""
    vals = _nums(trades, key)
    n = len(vals)
    if n < 2:
        return None
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return round(mean / sd * math.sqrt(n), 3)


def oos_check(in_sample, out_sample, key: str = "pct") -> dict:
    """ROKU rule 6 teeth: flag overfit when OOS expectancy collapses vs in-sample.

    Triggers on expectancy DEGRADATION (and low OOS sample), never on win-rate.
    Returns {is_expectancy, oos_expectancy, ratio, overfit, reason}.
    """
    ise = expectancy(in_sample, key)["expectancy"]
    ose = expectancy(out_sample, key)
    oos_exp = ose["expectancy"]
    reasons = []
    overfit = False
    if ise > 0:
        ratio = round(oos_exp / ise, 3)
        if oos_exp <= 0:
            overfit, _ = True, reasons.append("OOS expectancy <= 0 while in-sample positive")
        elif ratio < OOS_DEGRADE_LIMIT:
            overfit, _ = True, reasons.append(
                f"OOS expectancy {ratio:.0%} of in-sample (< {OOS_DEGRADE_LIMIT:.0%})")
    else:
        ratio = None
        reasons.append("in-sample expectancy non-positive; nothing to validate")
    if ose["low_sample"]:
        reasons.append(f"OOS sample {ose['n']} < {MIN_SAMPLE} (low confidence)")
    return {"is_expectancy": ise, "oos_expectancy": oos_exp, "ratio": ratio,
            "overfit": overfit, "reason": "; ".join(reasons)}


def summary(trades, *, avg_hold_days: float | None = None,
            deployment: float = 1.0) -> dict:
    """Full KPI block for the dashboard. Win-rate is included but DEMOTED — it is a
    sub-stat of expectancy here, not the headline."""
    pct = expectancy(trades, "pct")
    out = {
        "headline": "expectancy",
        "expectancy_pct": pct["expectancy"],
        "expectancy_R": expectancy(trades, "r_multiple")["expectancy"],
        "profit_factor": pct["profit_factor"],
        "payoff_ratio": pct["payoff_ratio"],
        "sqn": sqn(trades),
        "n": pct["n"],
        "low_sample": pct["low_sample"],
        # demoted, for reference only:
        "win_rate": pct["win_rate"],
        "avg_win": pct["avg_win"],
        "avg_loss": pct["avg_loss"],
    }
    if avg_hold_days:
        tpy = turns_per_year(avg_hold_days, deployment)
        out["turns_per_year"] = tpy
        out["annualized_pct"] = annualized(pct["expectancy"], tpy) if tpy else None
    return out
