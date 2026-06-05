"""
validate.py — Completeness gates + the five Success Metrics (PRD §13 / proposal §17).

Pure, deterministic, never-raises. Operates on a scored ``ResearchReport`` (evidence
already scored by ``scoring``) and a manifest (from ``repro``). Produces a metrics
dict with the five scores (each 0-100) plus ``tirf_score`` (their mean).

These metrics ARE the quantitative acceptance gates — keep them honest and monotonic:
more/better documentation must never lower a score.
"""
from __future__ import annotations

import logging

from .models import ResearchReport, SpecialistResearch

log = logging.getLogger(__name__)

EVIDENCE_GATE = 3        # proposal §6 Deliverable 2: min 3 evidence items
COUNTER_GATE = 3         # proposal §6 Deliverable 5: min 3 counterarguments


def _clamp(x: float) -> int:
    return int(max(0, min(100, round(x))))


# --- per-specialist sub-metrics ---------------------------------------------

def _explainability_sp(sp: SpecialistResearch) -> int:
    """40% logical chain (>=2 steps), 30% explicit assumptions, 30% non-empty reason."""
    chain = 1.0 if len([s for s in sp.reasoning if s.statement.strip()]) >= 2 else (
        0.5 if sp.reasoning else 0.0)
    assum = 1.0 if sp.assumptions else 0.0
    reason = 1.0 if (sp.reason and sp.reason.strip()) else 0.0
    return _clamp(100 * (0.40 * chain + 0.30 * assum + 0.30 * reason))


def _traceability_sp(sp: SpecialistResearch) -> int:
    """Scaled by evidence count (>=3 ⇒ full) times mean evidence quality."""
    if not sp.evidence:
        return 0
    count_factor = min(sp.evidence_count / EVIDENCE_GATE, 1.0)
    return _clamp(count_factor * sp.evidence_quality)


def _challenge_sp(sp: SpecialistResearch) -> int:
    """Scaled by counterargument count (>=3 ⇒ full)."""
    return _clamp(100 * min(sp.counter_count / COUNTER_GATE, 1.0))


def _reasoning_consistent_sp(sp: SpecialistResearch) -> bool:
    steps = [s for s in sp.reasoning if s.statement.strip()]
    return len(steps) >= 2 and bool(steps[-1].statement.strip())


# --- CIO-review input aggregates (shared with review.py) --------------------

def aggregate_inputs(report: ResearchReport) -> dict:
    """Aggregate per-specialist TIRF packages into the five CIO-review inputs
    (PRD §12). All 0-100. Never raises."""
    sps = report.specialists or []
    n = len(sps) or 1

    # Evidence quality: mean over specialists that supplied evidence
    with_ev = [s for s in sps if s.evidence_count]
    evidence_quality = (
        round(sum(s.evidence_quality for s in with_ev) / len(with_ev), 1)
        if with_ev else 0.0
    )

    assumption_quality = _clamp(100 * sum(1 for s in sps if s.assumptions) / n)
    counter_coverage = _clamp(100 * sum(1 for s in sps if s.meets_counter_gate) / n)

    all_src = report.all_sources()
    source_reliability = (
        _clamp(sum(sr.reliability_score for sr in all_src) / len(all_src))
        if all_src else 0
    )
    reasoning_consistency = _clamp(
        100 * sum(1 for s in sps if _reasoning_consistent_sp(s)) / n)

    return {
        "evidence_quality": evidence_quality,
        "assumption_quality": assumption_quality,
        "counterargument_coverage": counter_coverage,
        "source_reliability": source_reliability,
        "reasoning_consistency": reasoning_consistency,
    }


# --- the five success metrics (report level) --------------------------------

def compute_metrics(report: ResearchReport, manifest: dict | None = None) -> dict:
    """Compute the five success metrics + tirf_score for a report. Never raises."""
    try:
        manifest = manifest or {}
        sps = report.specialists or []
        n = len(sps) or 1

        explainability = _clamp(sum(_explainability_sp(s) for s in sps) / n)
        traceability = _clamp(sum(_traceability_sp(s) for s in sps) / n)

        # Reproducibility: four pins present (PRD §13).
        pins = (
            bool(manifest.get("data_hash") or report.data_hash),
            bool(manifest.get("data_snapshot") or report.data_snapshot),
            bool(manifest.get("prompt_version") or report.prompt_version),
            bool(manifest.get("agent_version") or report.agent_version),
        )
        reproducibility = _clamp(100 * sum(pins) / 4)

        # Challenge coverage: 80% counterargument depth + 20% debate participation.
        challenge_depth = sum(_challenge_sp(s) for s in sps) / n
        debate_part = 100 if report.challenges else 0
        challenge_coverage = _clamp(0.8 * challenge_depth + 0.2 * debate_part)

        # Auditability: pins + a final call + at least some documented support.
        has_support = any(s.evidence or s.reasoning or s.counterarguments for s in sps)
        has_final = bool(str(report.final_recommendation or "").strip())
        auditability = _clamp(
            0.4 * reproducibility
            + 0.3 * (100 if has_final else 0)
            + 0.3 * (100 if has_support else 0)
        )

        metrics = {
            "explainability": explainability,
            "traceability": traceability,
            "auditability": auditability,
            "reproducibility": reproducibility,
            "challenge_coverage": challenge_coverage,
        }
        metrics["tirf_score"] = _clamp(sum(metrics.values()) / 5)
        return metrics
    except Exception:
        log.warning("compute_metrics failed; returning zeros", exc_info=True)
        return {k: 0 for k in ("explainability", "traceability", "auditability",
                               "reproducibility", "challenge_coverage", "tirf_score")}


# --- completeness gate report -----------------------------------------------

def gate_report(report: ResearchReport) -> dict:
    """Per-specialist gate pass/fail + report-level fractions. For surfacing gaps."""
    sps = report.specialists or []
    n = len(sps) or 1
    per = [
        {
            "role_key": s.role_key,
            "evidence_count": s.evidence_count,
            "evidence_gate": s.meets_evidence_gate,
            "counter_count": s.counter_count,
            "counter_gate": s.meets_counter_gate,
            "has_assumptions": bool(s.assumptions),
            "has_reasoning": len(s.reasoning) >= 2,
            "has_sources": bool(s.sources),
        }
        for s in sps
    ]
    return {
        "per_specialist": per,
        "frac_evidence_gate": round(sum(1 for p in per if p["evidence_gate"]) / n, 2),
        "frac_counter_gate": round(sum(1 for p in per if p["counter_gate"]) / n, 2),
        "frac_assumptions": round(sum(1 for p in per if p["has_assumptions"]) / n, 2),
        "frac_reasoning": round(sum(1 for p in per if p["has_reasoning"]) / n, 2),
    }
