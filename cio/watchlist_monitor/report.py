"""
report.py — render a Watchlist Monitoring briefing (PRD §8) from assessments.

build_briefing  -> full markdown (PDF source / dashboard)
briefing_summary -> short plain-text recap for a Telegram message

Never raises; missing fields render as em-dash / empty sections.
"""
from __future__ import annotations

from typing import Any

_IMPORTANCE_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}
_STATUS_EMOJI = {"bullish": "🟢", "neutral": "⚪", "bearish": "🔴"}
_SENS_RANK = {"high": 2, "medium": 1, "low": 0}
_SENS_EMOJI = {"high": "🔴", "medium": "🟠", "low": "🟢"}


def _priority_key(a: dict) -> tuple:
    """Sort key: most material event first, then highest conviction."""
    return (
        _IMPORTANCE_RANK.get(a.get("event_importance", "low"), 0),
        a.get("conviction_score", 0),
    )


def _counts(assessments: list[dict]) -> dict[str, int]:
    out = {"bullish": 0, "neutral": 0, "bearish": 0}
    for a in assessments:
        out[a.get("overall_status", "neutral")] = out.get(a.get("overall_status", "neutral"), 0) + 1
    return out


def _market_environment(assessments: list[dict]) -> str:
    """Coarse read of the tape from the spread of overall_status calls."""
    c = _counts(assessments)
    if c["bullish"] > c["bearish"] * 2:
        return "constructive"
    if c["bearish"] > c["bullish"]:
        return "cautious"
    return "mixed"


def _highest_priority(assessments: list[dict]) -> dict | None:
    scored = [a for a in assessments if not a.get("error")]
    return max(scored, key=_priority_key) if scored else None


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {x}" for x in items) if items else "- _none_"


def _macro_alerts(assessments: list[dict], macro: dict | None) -> list[str]:
    """PRD macro / geopolitical alert lines, from the global snapshot + per-security
    sensitivity. Derived (no extra LLM calls)."""
    alerts: list[str] = []
    if macro:
        if macro.get("commodity_risk") == "high":
            alerts.append("**macro** (high): commodity risk elevated — "
                          f"{macro.get('summary') or 'see global intelligence'}")
        if macro.get("geopolitical_risk") == "high":
            alerts.append("**geopolitical** (high): "
                          f"{macro.get('summary') or 'elevated geopolitical risk'}")
    for a in assessments:
        if a.get("error"):
            continue
        if a.get("geopolitical_sensitivity") == "high":
            alerts.append(f"**geopolitical** ({a['ticker']}): high geopolitical "
                          "sensitivity on current events")
        elif a.get("commodity_sensitivity") == "high":
            alerts.append(f"**macro** ({a['ticker']}): high commodity sensitivity")
    return alerts


def _exposure_table(assessments: list[dict]) -> str:
    """Watchlist Exposure Analysis — per-security external-risk read, sorted by
    geopolitical sensitivity then external_risk_score."""
    rows = [a for a in assessments if not a.get("error")]
    if not rows:
        return "_No securities to assess._"
    rows = sorted(
        rows,
        key=lambda a: (_SENS_RANK.get(a.get("geopolitical_sensitivity", "low"), 0),
                       a.get("external_risk_score", 0)),
        reverse=True,
    )
    out = [
        "| Ticker | Ext. Risk | Macro | Geopolitical | Commodity | Currency |",
        "|---|---|---|---|---|---|",
    ]
    for a in rows:
        def _s(k):
            v = a.get(k, "low")
            return f"{_SENS_EMOJI.get(v, '🟢')} {v}"
        out.append(
            f"| {a.get('ticker', '?')} | {a.get('external_risk_score', 0)} | "
            f"{_s('macro_sensitivity')} | {_s('geopolitical_sensitivity')} | "
            f"{_s('commodity_sensitivity')} | {_s('currency_sensitivity')} |"
        )
    return "\n".join(out)


def briefing_summary(assessments: list[dict], macro: dict | None = None) -> str:
    """One short plain-text block for Telegram (no Markdown formatting chars)."""
    if not assessments:
        return "📋 Watchlist briefing: no active watchlist / no securities to review."
    c = _counts(assessments)
    alerts = [a for a in assessments
              if a.get("event_importance") in ("high", "critical") and not a.get("error")]
    escalate = [a["ticker"] for a in assessments if a.get("escalate")]
    lines = [
        "📋 Pre-market watchlist briefing",
        f"Environment: {_market_environment(assessments)}  |  "
        f"🟢 {c['bullish']}  ⚪ {c['neutral']}  🔴 {c['bearish']}",
    ]
    if macro:
        lines.append(
            f"Global: {macro.get('market_sentiment', 'cautious')}  |  "
            f"geopolitical {macro.get('geopolitical_risk', 'low')}  |  "
            f"commodity {macro.get('commodity_risk', 'low')}")
    hp = _highest_priority(assessments)
    if hp:
        lines.append(f"Highest priority: {hp['ticker']} "
                     f"({hp['event_importance']}, {hp['recommendation']})")
    if alerts:
        lines.append("Alerts: " + ", ".join(
            f"{a['ticker']} ({a['event_importance']})" for a in alerts))
    if escalate:
        lines.append("⚠️ Consider /committee: " + ", ".join(escalate))
    lines.append("Full briefing attached.")
    return "\n".join(lines)


def _security_block(a: dict) -> str:
    emoji = _STATUS_EMOJI.get(a.get("overall_status", "neutral"), "⚪")
    head = (f"### {emoji} {a.get('ticker', '?')} — {a.get('company', '')}".rstrip(" —"))
    if a.get("error"):
        return f"{head}\n\n_{a['error']}_\n"
    flag = "  ⚠️ **committee escalation**" if a.get("escalate") else ""
    body = [
        head,
        "",
        f"**Status:** {a.get('overall_status', '—')}  |  "
        f"**Conviction:** {a.get('conviction_score', '—')}  |  "
        f"**Recommendation:** {a.get('recommendation', '—')}  |  "
        f"**Event:** {a.get('event_importance', '—')}{flag}",
        f"**Analyst sentiment:** {a.get('analyst_sentiment', '—')}  |  "
        f"**Thesis change:** {a.get('investment_thesis_change', 'unchanged')}",
        "",
    ]
    hd = a.get("hold_decision")
    if isinstance(hd, dict) and hd.get("action"):
        # Four-layer / regime hold posture (swing upgrade #5): action + why.
        style = hd.get("style") or "neutral"
        stop = hd.get("stop_mode") or "standard"
        body.append(
            f"**Hold posture:** {hd['action'].upper()} "
            f"({style}, {stop} stop) — {hd.get('reason', '')}")
        body.append("")
    if a.get("summary"):
        body += [a["summary"], ""]
    body += [
        "**Positive:**", _bullets(a.get("key_positive_events", [])), "",
        "**Negative:**", _bullets(a.get("key_negative_events", [])), "",
        "**New risks:**", _bullets(a.get("new_risks", [])), "",
        "**Upcoming catalysts:**", _bullets(a.get("upcoming_catalysts", [])), "",
    ]
    return "\n".join(body)


def build_briefing(assessments: list[dict], as_of: str = "",
                   watchlist_name: str | None = None,
                   macro: dict | None = None) -> str:
    """Render the full morning briefing markdown (PRD §8).

    *macro* is the optional global_macro_snapshot() dict; when present it renders
    the Global Market Intelligence section (before stock analysis) plus macro /
    geopolitical alerts.
    """
    title = "# Watchlist Monitoring Briefing"
    if watchlist_name:
        title += f": {watchlist_name}"
    parts: list[str] = [title, f"\n_Generated: {as_of}_\n"]

    if not assessments:
        parts.append("\n_No active watchlist or no securities to review._\n")
        return "\n".join(parts)

    ranked = sorted(assessments, key=_priority_key, reverse=True)
    c = _counts(assessments)
    hp = _highest_priority(assessments)

    # ── §1 Executive Summary ────────────────────────────────────────────────
    parts.append("## Executive Summary\n")
    parts.append(f"**Market environment:** {_market_environment(assessments)}  ")
    parts.append(f"**Watchlist:** 🟢 bullish {c['bullish']}  |  "
                 f"⚪ neutral {c['neutral']}  |  🔴 bearish {c['bearish']}  ")
    parts.append(f"**Highest priority:** {hp['ticker'] if hp else '—'}\n")

    # ── Global Market Intelligence (must appear before stock analysis) ──────
    if macro:
        parts.append("## Global Market Intelligence\n")
        parts.append(f"**Market sentiment:** {macro.get('market_sentiment', 'cautious')}  |  "
                     f"**Geopolitical risk:** {macro.get('geopolitical_risk', 'low')}  |  "
                     f"**Commodity risk:** {macro.get('commodity_risk', 'low')}  ")
        if macro.get("summary"):
            parts.append(f"\n{macro['summary']}\n")
        parts.append("**Key events:**")
        parts.append(_bullets(macro.get("key_events", [])) + "\n")

    # ── Macro & Geopolitical Alerts ─────────────────────────────────────────
    macro_alerts = _macro_alerts(assessments, macro)
    parts.append("## Macro & Geopolitical Alerts\n")
    parts.append(_bullets(macro_alerts) + "\n")

    # ── Watchlist Exposure Analysis ─────────────────────────────────────────
    parts.append("## Watchlist Exposure Analysis\n")
    parts.append(_exposure_table(assessments) + "\n")

    # ── §2 Highest Priority Alerts (high/critical only) ─────────────────────
    alerts = [a for a in ranked
              if a.get("event_importance") in ("high", "critical") and not a.get("error")]
    parts.append("## Highest Priority Alerts\n")
    if alerts:
        for a in alerts:
            parts.append(f"- **{a['ticker']}** ({a['event_importance']}, "
                         f"{a['recommendation']}): {a.get('summary') or '—'}")
        parts.append("")
    else:
        parts.append("_No high or critical events._\n")

    # ── §4 New Risks (aggregate) ────────────────────────────────────────────
    risks: list[str] = []
    for a in ranked:
        for r in a.get("new_risks", []):
            risks.append(f"{a['ticker']}: {r}")
    parts.append("## New Risks\n")
    parts.append(_bullets(risks) + "\n")

    # ── §5 Upcoming Catalysts (aggregate) ───────────────────────────────────
    cats: list[str] = []
    for a in ranked:
        for cat in a.get("upcoming_catalysts", []):
            cats.append(f"{a['ticker']}: {cat}")
    parts.append("## Upcoming Catalysts\n")
    parts.append(_bullets(cats) + "\n")

    # Escalation note (PRD §11)
    escalate = [a["ticker"] for a in ranked if a.get("escalate")]
    if escalate:
        parts.append("## Committee Escalation\n")
        parts.append("These securities show thesis-relevant events — consider a full "
                     "committee run (`/committee SYMBOL`):\n")
        parts.append(_bullets(escalate) + "\n")

    # ── §3 Watchlist Review (per security, priority order) ──────────────────
    parts.append("## Watchlist Review\n")
    for a in ranked:
        parts.append(_security_block(a))

    return "\n".join(parts)
