"""
review.py — CIO Review Scorecard (PRD §12 / proposal §14).

Before a recommendation is finalised, the CIO must verify five things:
Evidence Quality, Assumption Quality, Counterargument Coverage, Source Reliability,
and Reasoning Consistency. TIRF computes them **deterministically** from the
aggregated specialist packages — no extra LLM call (zero-cost invariant, PRD §16/A1).

Each is 0-100 with a pass threshold; the scorecard returns the sub-scores, a mean,
a pass/flag verdict, and the list of weakest dimensions for the CIO/operator to see.
"""
from __future__ import annotations

import logging

from .models import ResearchReport
from .validate import aggregate_inputs

log = logging.getLogger(__name__)

# Pass thresholds per dimension (institutional-but-pragmatic defaults).
THRESHOLDS = {
    "evidence_quality": 60,
    "assumption_quality": 50,
    "counterargument_coverage": 50,
    "source_reliability": 60,
    "reasoning_consistency": 50,
}

_LABELS = {
    "evidence_quality": "Evidence Quality",
    "assumption_quality": "Assumption Quality",
    "counterargument_coverage": "Counterargument Coverage",
    "source_reliability": "Source Reliability",
    "reasoning_consistency": "Reasoning Consistency",
}


def cio_review(report: ResearchReport) -> dict:
    """Compute the CIO review scorecard for a report. Never raises.

    Returns:
      {
        scores: {dim: 0-100, ...},
        thresholds: {dim: int, ...},
        passed: {dim: bool, ...},
        overall_score: 0-100,
        verdict: "pass" | "review",     # "review" when any dimension is below threshold
        flags: [human-readable strings for sub-threshold dimensions],
      }
    """
    try:
        scores = aggregate_inputs(report)
        passed = {k: scores.get(k, 0) >= THRESHOLDS[k] for k in THRESHOLDS}
        flags = [
            f"{_LABELS[k]} below bar ({scores.get(k, 0)} < {THRESHOLDS[k]})"
            for k in THRESHOLDS if not passed[k]
        ]
        overall = int(round(sum(scores.values()) / len(scores))) if scores else 0
        return {
            "scores": scores,
            "thresholds": dict(THRESHOLDS),
            "passed": passed,
            "overall_score": overall,
            "verdict": "pass" if all(passed.values()) else "review",
            "flags": flags,
        }
    except Exception:
        log.warning("cio_review failed; returning empty scorecard", exc_info=True)
        return {"scores": {}, "thresholds": dict(THRESHOLDS), "passed": {},
                "overall_score": 0, "verdict": "review", "flags": ["scorecard error"]}
