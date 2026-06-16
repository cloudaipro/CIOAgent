"""
builder.py — Assemble a scored, validated, reviewed ResearchReport (PRD §4).

Pure orchestration of the deterministic TIRF layer: extract → score → manifest →
metrics → CIO review. No LLM calls (zero-cost invariant), no DB writes (the caller
decides when to persist). Never raises — a TIRF assembly failure returns a minimal
report rather than breaking the committee run.
"""
from __future__ import annotations

import logging
from typing import Any

from . import extract, gate, repro, review, scoring, validate
from .models import ResearchReport

log = logging.getLogger(__name__)


def _cio_confidence(cio: Any) -> Any:
    if isinstance(cio, dict):
        return cio.get("confidence_score", cio.get("confidence"))
    return None


def build_research_report(
    *,
    ticker: str,
    bundle: dict,
    opinions: list[dict],
    cio: dict | None = None,
    debate_result: dict | None = None,
    source: str = "command",
    run_id: str | None = None,
) -> ResearchReport:
    """Build the committee-level ResearchReport from the pipeline outputs.

    ``opinions`` are the final (Round-3) specialist opinion dicts; each may carry a
    ``_parsed`` raw-yaml dict that the extractor reads the TIRF deliverables from.
    Never raises.
    """
    try:
        cio = cio or {}
        report = ResearchReport(
            ticker=str(ticker).upper(),
            as_of=str(bundle.get("as_of", "")),
            source=source,
            run_id=run_id,
            final_recommendation=str(cio.get("final_recommendation", "") if isinstance(cio, dict) else ""),
            confidence=_cio_confidence(cio),
        )

        # Reproducibility pins (PRD §9)
        man = repro.manifest(bundle)
        report.prompt_version = man["prompt_version"]
        report.agent_version = man["agent_version"]
        report.data_hash = man["data_hash"]
        report.data_snapshot = man["data_snapshot"]

        # Per-specialist extraction + scoring
        for op in opinions or []:
            try:
                sp = extract.extract_from_opinion(op)
                scoring.score_specialist(sp, report.as_of)
                report.specialists.append(sp)
            except Exception:
                log.debug("specialist extract/score skipped for %s",
                          op.get("key"), exc_info=True)

        # Challenge protocol (debate) → report.challenges
        if debate_result and not debate_result.get("skipped", True):
            for ex in debate_result.get("exchanges", []) or []:
                report.challenges.append({
                    "challenger_key": ex.get("challenger_key"),
                    "challenger_title": ex.get("challenger_title"),
                    "target_key": ex.get("target_key"),
                    "target_title": ex.get("target_title"),
                    "challenge": ex.get("challenge"),
                    "response": ex.get("response"),
                })

        # Success metrics + CIO review scorecard (deterministic)
        report.metrics = validate.compute_metrics(report, man)
        report.review = review.cio_review(report)

        # Four-layer gate (swing upgrade #2, pass-2 enforcement): aggregate every
        # specialist's evidence into catalyst/behavior/momentum/execution scores and
        # AND-gate them, so a report whose catalyst layer is red is flagged even when
        # the execution layer is green (the ROKU trap). Advisory — surfaced, never
        # blocks the run. Stashed in review (review_json) to persist with zero schema
        # drift; promote to a first-class column later if wanted.
        try:
            report.review["four_layer_gate"] = gate.gate_evidence(report.all_evidence())
        except Exception:
            log.debug("four-layer gate compute skipped for %s", ticker, exc_info=True)
        return report
    except Exception:
        log.warning("build_research_report failed for %s", ticker, exc_info=True)
        return ResearchReport(ticker=str(ticker).upper(), source=source, run_id=run_id)
