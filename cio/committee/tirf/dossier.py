"""
dossier.py — Research Dossier renderer (PRD §11 / proposal §7).

Renders the 11 required sections as Markdown from a ``ResearchReport``. Never
raises; any missing piece renders ``_Insufficient data._`` (same posture as
report.py). Also exposes ``tirf_appendix`` — a compact transparency block appended
to the existing 14-section committee report so committee members receive the
evidence/assumption/counterargument layer inline (proposal §12).
"""
from __future__ import annotations

import logging

from .models import ResearchReport, SpecialistResearch

log = logging.getLogger(__name__)

_NA = "_Insufficient data._"

# 11 required section titles (proposal §7), in order.
REQUIRED_SECTIONS = [
    "Executive Summary",
    "Investment Thesis",
    "Evidence Summary",
    "Financial Analysis",
    "Industry Analysis",
    "Valuation Analysis",
    "Risks",
    "Counterarguments",
    "Assumptions",
    "Sources",
    "Final Recommendation",
]


def _by_key(report: ResearchReport, key: str) -> SpecialistResearch | None:
    return next((s for s in report.specialists if s.role_key == key), None)


def _section(title: str, body: str) -> str:
    return f"## {title}\n\n{body}\n"


def _specialist_block(sp: SpecialistResearch | None) -> str:
    if sp is None:
        return _NA
    parts = [f"**View:** {sp.vote} (confidence {sp.confidence})"]
    if sp.reason:
        parts.append(f"**Rationale:** {sp.reason}")
    # top evidence by score
    ev = sorted(sp.evidence, key=lambda e: e.item_score, reverse=True)[:3]
    if ev:
        parts.append("**Key evidence:**")
        for e in ev:
            parts.append(f"- {e.finding or '(unspecified)'} "
                         f"_(source: {e.source or 'n/a'}; score {e.item_score})_")
    return "\n\n".join(parts) if parts else _NA


def _evidence_table(report: ResearchReport) -> str:
    rows = report.all_evidence()
    if not rows:
        return _NA
    out = ["| Source | Tier | Date | Finding | Impact | Score |",
           "|---|---|---|---|---|---|"]
    for e in sorted(rows, key=lambda x: x.item_score, reverse=True):
        finding = (e.finding or "")[:80]
        out.append(f"| {e.source or 'n/a'} | {e.source_tier or '?'} | {e.date or '—'} "
                   f"| {finding} | {e.impact} | {e.item_score} |")
    mean = round(sum(e.item_score for e in rows) / len(rows), 1)
    out.append("")
    out.append(f"**Mean evidence quality:** {mean}  |  **Evidence items:** {len(rows)}")
    return "\n".join(out)


def _counterarguments(report: ResearchReport) -> str:
    items: list[str] = []
    for sp in report.specialists:
        for c in sp.counterarguments:
            if c.argument:
                items.append(f"- {c.argument}  _({sp.role_key})_")
    return "\n".join(items) if items else _NA


def _assumptions(report: ResearchReport) -> str:
    pairs = report.all_assumptions()
    if not pairs:
        return _NA
    out = ["| Agent | Assumption | Value | Confidence |", "|---|---|---|---|"]
    for role_key, a in pairs:
        out.append(f"| {role_key} | {a.name or '—'} | {a.value or '—'} | {a.confidence} |")
    return "\n".join(out)


def _sources(report: ResearchReport) -> str:
    srcs = report.all_sources()
    if not srcs:
        return _NA
    # dedupe by reference, keep best reliability
    seen: dict[str, int] = {}
    for s in srcs:
        seen[s.reference] = max(seen.get(s.reference, 0), s.reliability_score)
    out = ["| Reference | Reliability |", "|---|---|"]
    for ref, rel in sorted(seen.items(), key=lambda kv: kv[1], reverse=True):
        out.append(f"| {ref} | {rel} |")
    return "\n".join(out)


def _thesis(report: ResearchReport) -> str:
    """Synthesize an investment thesis from the bullish specialists' reasoning."""
    lines: list[str] = []
    for sp in report.specialists:
        if str(sp.vote).upper().replace("STRONG ", "") == "BUY" and sp.reasoning:
            chain = " → ".join(s.statement for s in sp.reasoning if s.statement)
            if chain:
                lines.append(f"- **{sp.title or sp.role_key}:** {chain}")
    if not lines:
        # fall back to any reasoning chain
        for sp in report.specialists:
            if sp.reasoning:
                chain = " → ".join(s.statement for s in sp.reasoning if s.statement)
                if chain:
                    lines.append(f"- **{sp.title or sp.role_key}:** {chain}")
                    break
    return "\n".join(lines) if lines else _NA


# What each layer answers — used by the verbose (operator-facing) gate block.
_LAYER_DOC: dict[str, str] = {
    "catalyst": "WHY the price should move — earnings, guidance, filings, regulatory change",
    "behavior": "Is the move NOT yet priced in — analyst-estimate revisions, fund flow, positioning, ownership",
    "momentum": "Durable trend alpha — relative strength, 6–12 month trend, new highs",
    "execution": "Entry timing only — RSI / MACD / KDJ / Squeeze / Fisher; a timing tool, never a reason to buy",
}
_GATE_ORDER = ("catalyst", "behavior", "momentum", "execution")


def _four_layer_gate_block(review: dict, verbose: bool = False) -> str:
    """Four-layer gate summary. Never raises.

    verbose=False → compact score echo (audit appendix + dossier header).
    verbose=True  → full self-explanatory block: per-layer table, causal
                    reading order, and the exact verdict logic. Used for the
                    operator-facing advisory section of the committee report.
    """
    try:
        gate = review.get("four_layer_gate")
        if not gate:
            return ""
        scores = gate.get("scores") or gate.get("layer_scores") or {}
        blocked = gate.get("blocked_by") or []
        missing = gate.get("missing") or []
        thresholds = gate.get("thresholds") or {}
        passed = bool(gate.get("pass"))

        def _thr(layer: str) -> str:
            try:
                return f"{float(thresholds.get(layer)):.0f}"
            except (TypeError, ValueError):
                return "?"

        def _score_str(layer: str) -> str:
            s = scores.get(layer)
            if isinstance(s, (int, float)):
                return f"{s:.0f}"
            return "—" if s is None else str(s)

        # ── compact echo (unchanged behaviour) ──────────────────────────────
        if not verbose:
            lines: list[str] = []
            for layer in _GATE_ORDER:
                flag = " ⚠" if layer in blocked else ""
                lines.append(f"  {layer}: {_score_str(layer)}/{_thr(layer)}{flag}")
            verdict = "**PASS**" if passed else f"**⚠ gate: blocked by {blocked}**"
            return f"\n**Four-Layer Gate:** {verdict}\n" + "\n".join(lines) + "\n"

        # ── verbose, self-explanatory block ─────────────────────────────────
        def _status(layer: str) -> str:
            s = scores.get(layer)
            if layer in missing or s is None:
                return "✖ missing — blocks the gate" if layer in blocked else "— no evidence (optional layer)"
            if layer in blocked:
                return "⚠ below threshold — blocks the gate"
            return "✅ clears threshold"

        verdict = "**PASS**" if passed else "**⚠ BLOCKED**"
        out: list[str] = [
            f"**Verdict:** {verdict}",
            "",
            "The four-layer gate is a swing-trade safety check. It splits the "
            "committee's evidence into four *causal* layers, scores each one "
            "independently on a 0–100 scale, then **AND-gates** them: every layer "
            "must clear its own threshold on its own merits, so one strong layer can "
            "never paper over a weak one. It is the structural fix for the \"ROKU "
            "trap\" — a flawless entry-timing reading (5/5 oscillators green) that "
            "masked a catalyst which had already been spent.",
            "",
            "| Layer | What it asks | Score | Threshold | Status |",
            "|---|---|---|---|---|",
        ]
        for layer in _GATE_ORDER:
            out.append(
                f"| {layer} | {_LAYER_DOC.get(layer, '')} | {_score_str(layer)} "
                f"| {_thr(layer)} | {_status(layer)} |"
            )

        # Verdict logic — spelled out from this run's numbers.
        if passed:
            cleared = ", ".join(
                f"{l} {_score_str(l)}≥{_thr(l)}" for l in _GATE_ORDER if l in scores
            )
            logic = (
                "**Verdict logic.** The gate passes only when the mandatory "
                "*catalyst* layer is present **and** every present layer sits at or "
                f"above its own threshold. Here all clear: {cleared} → **PASS**."
            )
        else:
            reasons: list[str] = []
            for l in blocked:
                if l in missing:
                    extra = (" (catalyst is mandatory — a trade with no \"why\" is "
                             "the indicator-soup trap)") if l == "catalyst" else ""
                    reasons.append(f"*{l}* has no evidence at all{extra}")
                else:
                    reasons.append(f"*{l}* {_score_str(l)} is below its {_thr(l)} threshold")
            logic = (
                "**Verdict logic.** Because the layers are AND-gated, a single "
                "failing layer blocks the whole gate. Blocked by: "
                + "; ".join(reasons) + "."
            )

        out += [
            "",
            logic,
            "",
            "**How to read it (causal order).** A swing trade is *entered* on "
            "**catalyst + behavior** — the move must be real *and* not yet priced "
            "in — and only *timed* with **momentum + execution**. That is why the "
            "\"why\" layers carry the higher bar (catalyst 60, behavior 50) while "
            "execution carries the lowest (40): a green execution score is a timing "
            "tool, never a reason to enter. Catalyst is the single **mandatory** "
            "layer — its absence blocks the gate even if the other three are green.",
            "",
            "**This gate is advisory.** It makes a spent-catalyst / green-chart "
            "setup visible to the committee; it does not change, override, or veto "
            "the CIO vote.",
            "",
        ]
        return "\n".join(out)
    except Exception:
        return ""


def render_dossier(report: ResearchReport) -> str:
    """Render the full 11-section Research Dossier as Markdown. Never raises."""
    try:
        m = report.metrics or {}
        rv = report.review or {}
        header = (
            f"# Research Dossier: {report.ticker}\n\n"
            f"_Report {report.report_id or '(unsaved)'} · v{report.version} · "
            f"as of {report.as_of} · source {report.source}_\n\n"
            f"**TIRF Score:** {m.get('tirf_score', '—')}  ·  "
            f"**CIO Review:** {rv.get('verdict', '—')} "
            f"({rv.get('overall_score', '—')})\n"
            + _four_layer_gate_block(rv)
        )

        secs: list[str] = []

        # 1. Executive Summary
        secs.append(_section("Executive Summary",
            f"**Final Recommendation:** {report.final_recommendation or _NA}  \n"
            f"**Confidence:** {report.confidence}  \n"
            f"**Explainability:** {m.get('explainability', '—')}  |  "
            f"**Traceability:** {m.get('traceability', '—')}  |  "
            f"**Auditability:** {m.get('auditability', '—')}  |  "
            f"**Reproducibility:** {m.get('reproducibility', '—')}  |  "
            f"**Challenge Coverage:** {m.get('challenge_coverage', '—')}"))

        # 2. Investment Thesis
        secs.append(_section("Investment Thesis", _thesis(report)))

        # 3. Evidence Summary
        secs.append(_section("Evidence Summary", _evidence_table(report)))

        # 4-7. Per-domain analysis from the relevant specialist
        secs.append(_section("Financial Analysis", _specialist_block(_by_key(report, "equity"))))
        secs.append(_section("Industry Analysis", _specialist_block(_by_key(report, "industry"))))
        secs.append(_section("Valuation Analysis", _specialist_block(_by_key(report, "valuation"))))
        secs.append(_section("Risks", _specialist_block(_by_key(report, "risk"))))

        # 8. Counterarguments
        secs.append(_section("Counterarguments", _counterarguments(report)))

        # 9. Assumptions
        secs.append(_section("Assumptions", _assumptions(report)))

        # 10. Sources
        secs.append(_section("Sources", _sources(report)))

        # 11. Final Recommendation
        flags = rv.get("flags") or []
        flag_txt = ("\n\n**CIO review flags:**\n" + "\n".join(f"- {f}" for f in flags)) if flags else ""
        secs.append(_section("Final Recommendation",
            f"**{report.final_recommendation or _NA}** "
            f"(confidence {report.confidence})  \n"
            f"**Verdict:** {rv.get('verdict', '—')} · overall {rv.get('overall_score', '—')}"
            f"{flag_txt}"))

        return header + "\n" + "\n".join(secs)
    except Exception:
        log.warning("render_dossier failed", exc_info=True)
        return f"# Research Dossier: {getattr(report, 'ticker', '?')}\n\n{_NA}\n"


def tirf_appendix(report: ResearchReport) -> str:
    """A compact transparency appendix for the committee report (proposal §12).

    Folded into report.build_report so committee members get the evidence/
    assumption/counterargument/reproducibility layer inline. Never raises.
    """
    try:
        m = report.metrics or {}
        rv = report.review or {}
        lines = [
            "## TIRF Transparency Appendix",
            "",
            f"_Research report {report.report_id or '(unsaved)'} · v{report.version} · "
            f"prompt {report.prompt_version} · agent {report.agent_version}_",
            "",
            "**Success Metrics**",
            "",
            "| Explainability | Traceability | Auditability | Reproducibility | Challenge Cov. | TIRF |",
            "|---|---|---|---|---|---|",
            f"| {m.get('explainability','—')} | {m.get('traceability','—')} "
            f"| {m.get('auditability','—')} | {m.get('reproducibility','—')} "
            f"| {m.get('challenge_coverage','—')} | **{m.get('tirf_score','—')}** |",
            "",
            "**CIO Review Scorecard**",
            "",
            "| Evidence | Assumptions | Counterargs | Sources | Reasoning | Verdict |",
            "|---|---|---|---|---|---|",
            f"| {rv.get('scores',{}).get('evidence_quality','—')} "
            f"| {rv.get('scores',{}).get('assumption_quality','—')} "
            f"| {rv.get('scores',{}).get('counterargument_coverage','—')} "
            f"| {rv.get('scores',{}).get('source_reliability','—')} "
            f"| {rv.get('scores',{}).get('reasoning_consistency','—')} "
            f"| **{rv.get('verdict','—')}** |",
            "",
            "### Evidence Ledger",
            "",
            _evidence_table(report),
            "",
            "### Counterarguments",
            "",
            _counterarguments(report),
            "",
            "### Assumptions",
            "",
            _assumptions(report),
            "",
            "### Sources",
            "",
            _sources(report),
        ]
        # Four-layer gate block (swing upgrade #2 visibility)
        gate_block = _four_layer_gate_block(rv)
        if gate_block:
            lines += ["", "### Four-Layer Gate", "", gate_block.strip()]
        return "\n".join(lines) + "\n"
    except Exception:
        log.warning("tirf_appendix failed", exc_info=True)
        return ""
