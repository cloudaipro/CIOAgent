"""
report.py — build_report / confidence_band

Renders PRD §9 thirteen-section markdown report from a CommitteeResult.
Never crashes on missing data — prints the header + "_Insufficient data._".
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# §11 Confidence band mapping
# ---------------------------------------------------------------------------

def confidence_band(score: int | float) -> str:
    """
    Map a 0-100 confidence score to a label per PRD §11.

    90-100 → Very High Confidence
    75-89  → High Confidence
    60-74  → Moderate-High Confidence
    50-59  → Moderate Confidence
    40-49  → Low-Moderate Confidence
    <40    → Very Low Confidence
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Unknown Confidence"

    if s >= 90:
        return "Very High Confidence"
    if s >= 75:
        return "High Confidence"
    if s >= 60:
        return "Moderate-High Confidence"
    if s >= 50:
        return "Moderate Confidence"
    if s >= 40:
        return "Low-Moderate Confidence"
    return "Very Low Confidence"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _v(val: Any, default: str = "_Insufficient data._") -> str:
    """Render a value; use default when None or empty."""
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def _section(title: str, content: str) -> str:
    return f"## {title}\n\n{content}\n"


def _opinion_table(opinions: list[dict]) -> str:
    if not opinions:
        return "_Insufficient data._"
    header = "| Specialist | Vote | Confidence | Reason |"
    sep = "|---|---|---|---|"
    rows = [header, sep]
    for op in opinions:
        vote = _v(op.get("vote"), "?")
        conf = _v(op.get("confidence"), "?")
        reason = _v(op.get("reason"), "—")
        # Truncate long reasons for table readability
        if len(reason) > 200:
            reason = reason[:197] + "..."
        title = _v(op.get("title"), op.get("key", "?"))
        rows.append(f"| {title} | {vote} | {conf} | {reason} |")
    return "\n".join(rows)


def _scenario_table(scenarios: Any) -> str:
    if not scenarios or not isinstance(scenarios, list):
        return "_Insufficient data._"
    header = "| Scenario | Probability | Price Target | Key Drivers |"
    sep = "|---|---|---|---|"
    rows = [header, sep]
    for s in scenarios:
        if not isinstance(s, dict):
            continue
        sc = _v(s.get("scenario"), "?")
        prob = _v(s.get("probability"), "?")
        pt = _v(s.get("price_target"), "?")
        kd = _v(s.get("key_drivers"), "?")
        rows.append(f"| {sc} | {prob} | {pt} | {kd} |")
    if len(rows) == 2:  # only header+sep
        return "_Insufficient data._"
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_report(symbol: str, result) -> str:
    """
    Render a 13-section PRD §9 markdown report from a CommitteeResult (or dict-like).

    Never raises; missing sections render as "## Title\n\n_Insufficient data._".
    """
    # Support both dataclass and dict access
    def _get(attr: str, default=None):
        try:
            if hasattr(result, attr):
                return getattr(result, attr)
            return result.get(attr, default)
        except Exception:
            return default

    resolved = _get("resolved") or symbol
    as_of = _get("as_of", "")
    bundle = _get("bundle") or {}
    opinions = _get("opinions") or []
    consensus = _get("consensus") or {}
    vote_tally = _get("vote_tally") or {}
    cio = _get("cio") or {}
    error = _get("error")

    fund = bundle.get("fundamentals") or {}
    quote = bundle.get("quote") or {}
    name = fund.get("name") or resolved

    sections: list[str] = []

    # ── 1. Executive Summary ───────────────────────────────────────────────
    final_rec = _v(cio.get("final_recommendation"))
    conf_score = cio.get("confidence_score")
    band = confidence_band(conf_score) if conf_score is not None else "Unknown Confidence"
    base_case = _v(cio.get("base_case"))
    tally_rec = _v(vote_tally.get("tally_recommendation"))

    if error:
        exec_body = f"**Error:** {error}"
    else:
        exec_body = (
            f"**Symbol:** {resolved}  \n"
            f"**Final Recommendation:** {final_rec}  \n"
            f"**Confidence:** {_v(conf_score)} ({band})  \n"
            f"**Committee Tally:** {tally_rec} "
            f"(BUY: {vote_tally.get('buy_count', 0)}, "
            f"HOLD: {vote_tally.get('hold_count', 0)}, "
            f"SELL: {vote_tally.get('sell_count', 0)})  \n"
            f"**Base Case:** {base_case}  \n"
            f"**As of:** {as_of}"
        )
    sections.append(_section("Executive Summary", exec_body))

    # ── 2. Company Overview ────────────────────────────────────────────────
    mktcap = fund.get("market_cap")
    overview_lines = [
        f"**Name:** {_v(name)}",
        f"**Market Cap:** {_v(mktcap)}",
        f"**52W High:** {_v(fund.get('wk52_high'))}  |  **52W Low:** {_v(fund.get('wk52_low'))}",
        f"**Last Price:** {_v(quote.get('close'))}  |  **Change:** {_v(quote.get('change_pct'))}%",
    ]
    sections.append(_section("Company Overview", "\n".join(overview_lines)))

    # ── 3. Market Analysis ────────────────────────────────────────────────
    mkt = next((op for op in opinions if op.get("key") == "market"), None)
    if mkt:
        mkt_body = (
            f"**Market Trend:** {_v(mkt.get('market_trend'))}  \n"
            f"**Market Score:** {_v(mkt.get('market_score'))}  \n"
            f"**Macro Risks:** {_v(mkt.get('macro_risks'))}  \n"
            f"**Capital Flows:** {_v(mkt.get('capital_flows'))}"
        )
    else:
        mkt_body = "_Insufficient data._"
    sections.append(_section("Market Analysis", mkt_body))

    # ── 4. Industry Analysis ──────────────────────────────────────────────
    ind = next((op for op in opinions if op.get("key") == "industry"), None)
    if ind:
        ind_body = (
            f"**Industry Score:** {_v(ind.get('industry_score'))}  \n"
            f"**Industry Cycle:** {_v(ind.get('industry_cycle'))}  \n"
            f"**Tailwinds:** {_v(ind.get('tailwinds'))}  \n"
            f"**Headwinds:** {_v(ind.get('headwinds'))}"
        )
    else:
        ind_body = "_Insufficient data._"
    sections.append(_section("Industry Analysis", ind_body))

    # ── 5. Financial Analysis ─────────────────────────────────────────────
    eq = next((op for op in opinions if op.get("key") == "equity"), None)
    fin_lines = [
        f"**PE:** {_v(fund.get('pe'))}  |  **PB:** {_v(fund.get('pb'))}",
        f"**EPS:** {_v(fund.get('eps'))}  |  **ROE:** {_v(fund.get('roe_pct'))}%",
        f"**Profit Margin:** {_v(fund.get('margin_pct'))}%",
        f"**Dividend Yield:** {_v(fund.get('yield_pct'))}%",
    ]
    if eq:
        fin_lines += [
            f"**Financial Health:** {_v(eq.get('financial_health'))}",
            f"**Earnings Growth:** {_v(eq.get('earnings_growth'))}",
            f"**Quality Score:** {_v(eq.get('quality_score'))}",
        ]
    sections.append(_section("Financial Analysis", "\n".join(fin_lines)))

    # ── 6. Valuation Analysis ─────────────────────────────────────────────
    val = next((op for op in opinions if op.get("key") == "valuation"), None)
    if val:
        val_body = (
            f"**Fair Value:** {_v(val.get('fair_value'))}  \n"
            f"**Valuation Rating:** {_v(val.get('valuation_rating'))}  \n"
            f"**Upside Potential:** {_v(val.get('upside_potential'))}  \n"
            f"**Downside Risk:** {_v(val.get('downside_risk'))}"
        )
    else:
        val_body = "_Insufficient data._"
    sections.append(_section("Valuation Analysis", val_body))

    # ── 7. Risk Analysis ──────────────────────────────────────────────────
    risk = next((op for op in opinions if op.get("key") == "risk"), None)
    if risk:
        risk_body = (
            f"**Risk Score:** {_v(risk.get('risk_score'))}  \n"
            f"**Major Risks:** {_v(risk.get('major_risks'))}  \n"
            f"**Worst Case Scenario:** {_v(risk.get('worst_case_scenario'))}"
        )
    else:
        risk_body = "_Insufficient data._"
    sections.append(_section("Risk Analysis", risk_body))

    # ── 8. Catalyst Analysis ──────────────────────────────────────────────
    cat = next((op for op in opinions if op.get("key") == "catalyst"), None)
    if cat:
        cat_body = (
            f"**Bullish Catalysts:** {_v(cat.get('bullish_catalysts'))}  \n"
            f"**Bearish Catalysts:** {_v(cat.get('bearish_catalysts'))}  \n"
            f"**Event Timeline:** {_v(cat.get('event_timeline'))}"
        )
    else:
        cat_body = "_Insufficient data._"
    sections.append(_section("Catalyst Analysis", cat_body))

    # ── 9. Bull Case ──────────────────────────────────────────────────────
    bull = _v(cio.get("bull_case"))
    sections.append(_section("Bull Case", bull))

    # ── 10. Bear Case ─────────────────────────────────────────────────────
    bear = _v(cio.get("bear_case"))
    sections.append(_section("Bear Case", bear))

    # ── 11. Scenario Analysis ─────────────────────────────────────────────
    scenarios = cio.get("scenarios")
    sections.append(_section("Scenario Analysis", _scenario_table(scenarios)))

    # ── 12. Investment Committee Findings ─────────────────────────────────
    committee_body_parts = [_opinion_table(opinions)]
    if consensus:
        committee_body_parts.append(
            f"\n**Committee Recommendation:** {_v(consensus.get('committee_recommendation'))}  \n"
            f"**Agreement Score:** {_v(consensus.get('agreement_score'))}  \n"
            f"**Majority View:** {_v(consensus.get('majority_view'))}  \n"
            f"**Minority View:** {_v(consensus.get('minority_view'))}  \n"
            f"**Key Disagreements:** {_v(consensus.get('key_disagreements'))}"
        )
    committee_body_parts.append(
        f"\n**Vote Tally:** BUY {vote_tally.get('buy_count', 0)} | "
        f"HOLD {vote_tally.get('hold_count', 0)} | "
        f"SELL {vote_tally.get('sell_count', 0)}  \n"
        f"**Confidence-Weighted Score:** {_v(vote_tally.get('confidence_weighted_score'))}  \n"
        f"**Tally Recommendation:** {_v(vote_tally.get('tally_recommendation'))}"
    )
    sections.append(_section("Investment Committee Findings", "\n".join(committee_body_parts)))

    # ── 13. Final Recommendation ──────────────────────────────────────────
    if cio and not cio.get("_raw"):
        final_body = (
            f"**Final Recommendation:** {final_rec}  \n"
            f"**Confidence Score:** {_v(conf_score)} — **{band}**  \n"
            f"**Risk Rating:** {_v(cio.get('risk_rating'))}  \n"
            f"**Time Horizon:** {_v(cio.get('time_horizon'))}  \n\n"
            f"**Base Case:** {base_case}  \n"
            f"**Bull Case:** {_v(cio.get('bull_case'))}  \n"
            f"**Bear Case:** {_v(cio.get('bear_case'))}"
        )
    else:
        final_body = "_Insufficient data._"
    sections.append(_section("Final Recommendation", final_body))

    # ── Assemble ──────────────────────────────────────────────────────────
    header = f"# Investment Committee Report: {resolved}\n\n_Generated: {as_of}_\n"
    return header + "\n".join(sections)
