"""
models.py — TIRF data structures (PRD §5, §15).

Pure dataclasses; no LLM, no DB, no I/O. These are the in-memory shape that
``extract`` produces, ``scoring``/``validate`` enrich, and ``store`` persists.

Every collection defaults to empty so a bare specialist (no TIRF keys) still
yields a valid, serialisable object — never None-holes that explode downstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class EvidenceItem:
    """One supporting fact (proposal Deliverable 2)."""
    source: str = ""
    date: str = ""                 # ISO yyyy-mm-dd (may be "")
    finding: str = ""
    impact: str = "neutral"        # positive | negative | neutral
    relevance: str = "related"     # direct | related | indirect
    confidence: str = "medium"     # high | medium | low
    # Filled by scoring.py (deterministic):
    source_tier: str = ""          # classified tier label
    reliability_score: int = 0
    recency_score: int = 0
    relevance_score: int = 0
    item_score: int = 0
    # Four-layer causal tag (swing upgrade #2): catalyst | behavior | momentum |
    # execution. Separates "why price will move" (catalyst/behavior) from "math
    # left after it moved" (momentum/execution) so the gate can't let a green
    # execution layer mask a red catalyst (the ROKU failure mode).
    layer: str = "catalyst"

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Assumption:
    """One explicit assumption (proposal Deliverable 3 / §9)."""
    name: str = ""
    value: str = ""
    confidence: str = "medium"


@dataclass
class ReasoningStep:
    """One link in the logical chain (proposal Deliverable 4)."""
    step_no: int = 0
    statement: str = ""


@dataclass
class Counterargument:
    """One opposing view (proposal Deliverable 5)."""
    argument: str = ""


@dataclass
class SourceRef:
    """One reference (proposal Deliverable 6)."""
    reference: str = ""
    source_tier: str = ""
    reliability_score: int = 0


@dataclass
class SpecialistResearch:
    """The full TIRF deliverable package emitted by ONE specialist in one turn.

    Carries the structured output (vote/confidence/reason — proposal Deliverable 1)
    alongside the five documentation packages, plus the deterministic scores added
    later. ``conclusion`` mirrors the vote so the reasoning chain has a head.
    """
    role_key: str = ""
    title: str = ""
    vote: str = "HOLD"
    confidence: Any = 50
    reason: str = ""
    conclusion: str = ""
    evidence: list[EvidenceItem] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    reasoning: list[ReasoningStep] = field(default_factory=list)
    counterarguments: list[Counterargument] = field(default_factory=list)
    sources: list[SourceRef] = field(default_factory=list)

    # ---- convenience aggregates (set by scoring/validate) ----
    evidence_quality: float = 0.0      # mean item score
    evidence_count: int = 0
    counter_count: int = 0
    # Four-layer scores (swing upgrade #2): mean item_score per causal layer,
    # filled by scoring.score_specialist via gate.layer_scores. Empty until scored.
    layer_scores: dict = field(default_factory=dict)

    @property
    def meets_evidence_gate(self) -> bool:
        return self.evidence_count >= 3

    @property
    def meets_counter_gate(self) -> bool:
        return self.counter_count >= 3


@dataclass
class ResearchReport:
    """The committee-level TIRF report for one ticker/run (the persisted root).

    Aggregates every specialist's SpecialistResearch, the CIO decision, the
    reproducibility manifest, the success metrics, and the CIO review scorecard.
    ``report_id`` and ``version`` are assigned at persist time by ``store``.
    """
    ticker: str = ""
    agent: str = "committee"
    report_id: str = ""
    version: int = 0
    as_of: str = ""
    source: str = "command"        # command | chat | cli
    run_id: str | None = None      # links to committee_transcript run grouping

    # Reproducibility pins (repro.py)
    prompt_version: str = ""
    agent_version: str = ""
    data_hash: str = ""
    data_snapshot: str = ""        # canonical JSON string

    # CIO structured output
    final_recommendation: str = ""
    confidence: Any = None

    # Per-specialist research packages
    specialists: list[SpecialistResearch] = field(default_factory=list)

    # Challenge protocol (debate), persisted to children
    challenges: list[dict] = field(default_factory=list)   # {challenger_*, target_*, challenge, response}

    # Deterministic outputs (validate.py / review.py)
    metrics: dict = field(default_factory=dict)            # 5 success metrics + tirf_score
    review: dict = field(default_factory=dict)             # CIO review scorecard

    # ---- aggregates ----
    @property
    def evidence_quality(self) -> float:
        scores = [s.evidence_quality for s in self.specialists if s.evidence_count]
        return round(sum(scores) / len(scores), 1) if scores else 0.0

    def all_evidence(self) -> list[EvidenceItem]:
        out: list[EvidenceItem] = []
        for s in self.specialists:
            out.extend(s.evidence)
        return out

    def all_sources(self) -> list[SourceRef]:
        out: list[SourceRef] = []
        for s in self.specialists:
            out.extend(s.sources)
        return out

    def all_assumptions(self) -> list[tuple[str, Assumption]]:
        out: list[tuple[str, Assumption]] = []
        for s in self.specialists:
            for a in s.assumptions:
                out.append((s.role_key, a))
        return out
