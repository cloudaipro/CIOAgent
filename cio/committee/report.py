"""
report.py — build_report / confidence_band

Renders the PRD §9 markdown report (14 sections incl. Global Macro &
Geopolitical Environment) from a CommitteeResult.
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

def _human_num(x: Any) -> str:
    """
    Humanize a large number into $T/$B/$M notation.

    Examples: 4523118034944 → '$4.52T', 912345678901 → '$912.3B', 45000000 → '$45.0M'.
    Falls back to str(x) on non-numeric input.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    abs_v = abs(v)
    sign = "-" if v < 0 else ""
    if abs_v >= 1e12:
        return f"{sign}${abs_v / 1e12:.2f}T"
    if abs_v >= 1e9:
        return f"{sign}${abs_v / 1e9:.1f}B"
    if abs_v >= 1e6:
        return f"{sign}${abs_v / 1e6:.1f}M"
    return f"{sign}${abs_v:,.0f}"


def _v(val: Any, default: str = "_Insufficient data._") -> str:
    """Render a value; use default when None or empty."""
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def _num(x: Any, default: str = "_Insufficient data._", pct: bool = False) -> str:
    """Render a number rounded to <=2 dp (trailing zeros stripped). Non-numeric → _v."""
    if x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return _v(x, default)
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return f"{s}%" if pct else s


def _field(label: str, value: Any) -> str:
    """
    Labeled markdown block.

    Dict value (e.g. a specialist wrapping its answer as
    ``{'qualitative_assessment': [...]}``) → flattened to its values, so the raw
    Python dict repr never leaks into the report. List/tuple value → label line
    followed by a bullet list (avoids the raw `['a', 'b']` repr leaking); dict
    items inside the list (e.g. ``{'catalyst': 'X', 'date': 'Y'}``) are joined to
    a readable line rather than leaking the dict repr. Scalar → inline bold line.
    """
    def _coerce(x: Any) -> str:
        # dict item (e.g. {'catalyst': 'X', 'date': 'Y'}) → "X — Y", no repr leak
        if isinstance(x, dict):
            parts = [str(v).strip() for v in x.values() if str(v).strip()]
            return " — ".join(parts)
        return str(x).strip()

    if isinstance(value, dict):
        flat: list = []
        for v in value.values():
            if isinstance(v, (list, tuple)):
                flat.extend(v)
            else:
                flat.append(v)
        value = flat
    if isinstance(value, (list, tuple)):
        items = [c for x in value if (c := _coerce(x))]
        if items:
            body = "\n".join(f"- {it}" for it in items)
            return f"**{label}:**\n\n{body}"
        return f"**{label}:** _Insufficient data._"
    return f"**{label}:** {_v(value)}"


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
# Debate subsection renderer
# ---------------------------------------------------------------------------

def _debate_section(debate_data: dict, round1_opinions: list[dict], final_opinions: list[dict]) -> str:
    """
    Render the ### Debate and ### Vote Changes subsections.

    Returns an empty string (no subsection) when debate data is absent.
    Never raises.
    """
    try:
        if not debate_data:
            return ""

        parts: list[str] = []

        if debate_data.get("skipped", True):
            parts.append("\n### Debate\n\n_No material disagreement; debate skipped._")
        else:
            # ### Debate — exchanges
            exchanges = debate_data.get("exchanges") or []
            debate_lines: list[str] = ["\n### Debate\n"]
            for ex in exchanges:
                c_title = _v(ex.get("challenger_title"), "?")
                t_title = _v(ex.get("target_title"), "?")
                challenge = _v(ex.get("challenge"), "_No challenge recorded._")
                response = _v(ex.get("response"), "_No response recorded._")
                debate_lines.append(f"**{c_title} challenges {t_title}:**\n\n{challenge}\n")
                debate_lines.append(f"**{t_title} responds:**\n\n{response}\n")
            parts.append("\n".join(debate_lines))

            # ### Vote Changes (Round 1 → Round 3)
            r1_by_key = {op.get("key"): op for op in round1_opinions}
            r3_by_key = {op.get("key"): op for op in final_opinions}
            all_keys = list(r1_by_key.keys())

            change_lines: list[str] = [
                "\n### Vote Changes (Round 1 → Round 3)\n",
                "| Specialist | R1 vote (conf) | R3 vote (conf) | Δ |",
                "|---|---|---|---|",
            ]
            for key in all_keys:
                r1 = r1_by_key.get(key, {})
                r3 = r3_by_key.get(key, r1)
                title = _v(r1.get("title") or r3.get("title"), key)
                r1_vote = _v(r1.get("vote"), "?")
                r1_conf = _v(r1.get("confidence"), "?")
                r3_vote = _v(r3.get("vote"), "?")
                r3_conf = _v(r3.get("confidence"), "?")
                r1_cell = f"{r1_vote} ({r1_conf})"
                r3_cell = f"{r3_vote} ({r3_conf})"
                if r1_vote == r3_vote and str(r1_conf) == str(r3_conf):
                    delta = "— (unchanged)"
                else:
                    delta = f"{r1_vote}→{r3_vote}"
                change_lines.append(f"| {title} | {r1_cell} | {r3_cell} | {delta} |")
            parts.append("\n".join(change_lines))

        return "\n".join(parts)
    except Exception:
        return "\n_Insufficient data._"


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_report(symbol: str, result) -> str:
    """
    Render the PRD §9 markdown report (14 sections) from a CommitteeResult (or dict-like).

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
    round1_opinions = _get("round1_opinions") or []
    debate_data = _get("debate") or {}

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
    mktcap_str = _human_num(mktcap) if mktcap is not None else "_Insufficient data._"
    overview_lines = [
        f"**Name:** {_v(name)}",
        f"**Market Cap:** {mktcap_str}",
        f"**52W High:** {_v(fund.get('wk52_high'))}  |  **52W Low:** {_v(fund.get('wk52_low'))}",
        f"**Last Price:** {_v(quote.get('close'))}  |  **Change:** {_num(quote.get('change_pct'), pct=True)}",
    ]
    sections.append(_section("Company Overview", "\n".join(overview_lines)))

    # ── 3. Market Analysis ────────────────────────────────────────────────
    mkt = next((op for op in opinions if op.get("key") == "market"), None)
    if mkt:
        mkt_body = "\n\n".join([
            _field("Market Trend", mkt.get("market_trend")),
            _field("Market Score", mkt.get("market_score")),
            _field("Macro Risks", mkt.get("macro_risks")),
            _field("Capital Flows", mkt.get("capital_flows")),
        ])
    else:
        mkt_body = "_Insufficient data._"
    sections.append(_section("Market Analysis", mkt_body))

    # ── 3b. Global Macro & Geopolitical Environment ───────────────────────
    mac = next((op for op in opinions if op.get("key") == "macro"), None)
    if mac:
        def _lst(v):
            if isinstance(v, (list, tuple)):
                return ", ".join(str(x) for x in v) or "_Insufficient data._"
            return _v(v)
        macro_body = (
            f"**Macro Environment:** {_v(mac.get('macro_environment'))}  \n"
            f"**Major Events:** {_lst(mac.get('major_events'))}  \n"
            f"**Sectors Helped:** {_lst(mac.get('affected_sectors_positive'))}  \n"
            f"**Sectors Hurt:** {_lst(mac.get('affected_sectors_negative'))}\n\n"
            f"**External Risk Matrix**\n\n"
            f"| Geopolitical | Commodity | Currency | Regulatory |\n"
            f"|---|---|---|---|\n"
            f"| {_v(mac.get('geopolitical_risk'), '?')} "
            f"| {_v(mac.get('commodity_risk'), '?')} "
            f"| {_v(mac.get('currency_risk'), '?')} "
            f"| {_v(mac.get('regulatory_risk'), '?')} |"
        )
    else:
        macro_body = "_Insufficient data._"
    sections.append(_section("Global Macro & Geopolitical Environment", macro_body))

    # ── 4. Industry Analysis ──────────────────────────────────────────────
    ind = next((op for op in opinions if op.get("key") == "industry"), None)
    if ind:
        ind_body = "\n\n".join([
            _field("Industry Score", ind.get("industry_score")),
            _field("Industry Cycle", ind.get("industry_cycle")),
            _field("Tailwinds", ind.get("tailwinds")),
            _field("Headwinds", ind.get("headwinds")),
        ])
    else:
        ind_body = "_Insufficient data._"
    sections.append(_section("Industry Analysis", ind_body))

    # ── 5. Financial Analysis ─────────────────────────────────────────────
    eq = next((op for op in opinions if op.get("key") == "equity"), None)
    fin_lines = [
        f"**PE:** {_num(fund.get('pe'))}  |  **PB:** {_num(fund.get('pb'))}",
        f"**EPS:** {_num(fund.get('eps'))}  |  **ROE:** {_num(fund.get('roe_pct'), pct=True)}",
        f"**Profit Margin:** {_num(fund.get('margin_pct'), pct=True)}",
        f"**Dividend Yield:** {_num(fund.get('yield_pct'), pct=True)}",
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
        risk_body = "\n\n".join([
            _field("Risk Score", risk.get("risk_score")),
            _field("Major Risks", risk.get("major_risks")),
            _field("Worst Case Scenario", risk.get("worst_case_scenario")),
        ])
    else:
        risk_body = "_Insufficient data._"
    sections.append(_section("Risk Analysis", risk_body))

    # ── 8. Catalyst Analysis ──────────────────────────────────────────────
    cat = next((op for op in opinions if op.get("key") == "catalyst"), None)
    if cat:
        cat_body = "\n\n".join([
            _field("Bullish Catalysts", cat.get("bullish_catalysts")),
            _field("Bearish Catalysts", cat.get("bearish_catalysts")),
            _field("Event Timeline", cat.get("event_timeline")),
        ])
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
        f"**Net Directional Score (-1 all-sell … +1 all-buy):** {_v(vote_tally.get('confidence_weighted_score'))}  \n"
        f"**Tally Recommendation:** {_v(vote_tally.get('tally_recommendation'))}"
    )
    # Debate subsections
    committee_body_parts.append(_debate_section(debate_data, round1_opinions, opinions))
    sections.append(_section("Investment Committee Findings", "\n".join(committee_body_parts)))

    # ── 13. Final Recommendation ──────────────────────────────────────────
    if cio and not cio.get("_raw"):
        final_body = (
            f"**Final Recommendation:** {final_rec}  \n"
            f"**Confidence Score:** {_v(conf_score)} — **{band}**  \n"
            f"**Risk Rating:** {_v(cio.get('risk_rating'))}  \n"
            f"**Time Horizon:** {_v(cio.get('time_horizon'))}  \n"
            f"**Macro Alignment:** {_v(cio.get('macro_alignment_score'))}  |  "
            f"**Geopolitical Risk:** {_v(cio.get('geopolitical_risk_score'))}  \n"
            f"**External Risk Adjustment:** {_v(cio.get('external_risk_adjustment'))}  \n\n"
            f"**Base Case:** {base_case}  \n"
            f"**Bull Case:** {_v(cio.get('bull_case'))}  \n"
            f"**Bear Case:** {_v(cio.get('bear_case'))}"
        )
    else:
        final_body = "_Insufficient data._"
    sections.append(_section("Final Recommendation", final_body))

    # ── TIRF Transparency Appendix (evidence/assumptions/counterargs/scores) ──
    # Folded in so committee members receive the audit layer inline (proposal §12).
    # Never breaks the report if the TIRF layer is absent or errors.
    tirf_report = _get("tirf")
    if tirf_report is not None:
        try:
            from .tirf import tirf_appendix
            from .tirf.dossier import _four_layer_gate_block
            appendix = tirf_appendix(tirf_report)
            if appendix and appendix.strip():
                sections.append("\n" + appendix)
            # Surface the four-layer gate verdict in the main report body as a
            # compact advisory block (swing upgrade #2, pass-2 wiring). Advisory
            # only — never blocks the committee run.
            rv_tirf = getattr(tirf_report, "review", None) or {}
            gate_block = _four_layer_gate_block(rv_tirf, verbose=True)
            if gate_block and gate_block.strip():
                sections.append("\n## Four-Layer Gate (swing advisory)\n\n"
                                + gate_block)
        except Exception:
            pass

    # ── Assemble ──────────────────────────────────────────────────────────
    header = f"# Investment Committee Report: {resolved}\n\n_Generated: {as_of}_\n"
    return header + "\n".join(sections)
