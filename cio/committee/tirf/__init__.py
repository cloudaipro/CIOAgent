"""
TIRF — Transparent Investment Research Framework (cio.committee.tirf).

A deterministic, zero-LLM-cost documentation + audit layer over the committee:
every specialist conclusion is backed by evidence, assumptions, reasoning,
counterarguments, and sources — then scored, validated, versioned, persisted, and
rendered into a Research Dossier. See docs/TIRF-PRD.md.

Public API (proposal §16, as a Python API — no HTTP server per locked surface):
  build_research_report(...)     → ResearchReport      (extract+score+validate+review)
  persist(report)                → report_id           (committee.db, versioned)
  get_report / get_evidence / get_assumptions / ...    (retrieval)
  render_dossier(report)         → Markdown memo (11 sections)
  tirf_appendix(report)          → compact appendix for the committee report
  cio_review(report)             → CIO review scorecard
  score_evidence(item, as_of)    → scored EvidenceItem
"""
from __future__ import annotations

from .models import (
    Assumption,
    Counterargument,
    EvidenceItem,
    ReasoningStep,
    ResearchReport,
    SourceRef,
    SpecialistResearch,
)
from .builder import build_research_report
from .scoring import classify_source, score_item as score_evidence, score_specialist
from .validate import compute_metrics, gate_report, aggregate_inputs, EVIDENCE_GATE, COUNTER_GATE
from .review import cio_review
from .repro import manifest, data_hash, data_snapshot, verify, PROMPT_VERSION, AGENT_VERSION
from .dossier import render_dossier, tirf_appendix, REQUIRED_SECTIONS
from .store import (
    persist,
    get_report,
    get_latest,
    get_evidence,
    get_assumptions,
    get_reasoning,
    get_counterarguments,
    get_sources,
    get_challenges,
    list_reports,
    latest_version,
)

__all__ = [
    # models
    "EvidenceItem", "Assumption", "ReasoningStep", "Counterargument",
    "SourceRef", "SpecialistResearch", "ResearchReport",
    # build / score / validate / review
    "build_research_report", "score_evidence", "score_specialist", "classify_source",
    "compute_metrics", "gate_report", "aggregate_inputs", "cio_review",
    "EVIDENCE_GATE", "COUNTER_GATE",
    # reproducibility
    "manifest", "data_hash", "data_snapshot", "verify",
    "PROMPT_VERSION", "AGENT_VERSION",
    # dossier
    "render_dossier", "tirf_appendix", "REQUIRED_SECTIONS",
    # store
    "persist", "get_report", "get_latest", "get_evidence", "get_assumptions",
    "get_reasoning", "get_counterarguments", "get_sources", "get_challenges",
    "list_reports", "latest_version",
]
