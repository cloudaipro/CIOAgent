"""cio.harness — deterministic harness-engineering layer.

Converts user-found defects into durable, zero-LLM-cost checks:

  V1 consistency  — trade-plan rule-consistency gate (entry/stop self-contradiction)
  V2 citation     — fetch-before-cite / URL liveness (reuses source_policy)
  V3 event_study  — post-catalyst return DISTRIBUTION (never a point forecast)
  registry        — self-authoring loop with a strict admission gate
                    (PROPOSED→VERIFIED→APPROVED→ACTIVE)

See docs/HARNESS-ENGINEERING-EVALUATION.md (why) and
docs/HARNESS-ENGINEERING-SPEC.md (what).
"""
from __future__ import annotations

from .models import (
    Severity, Finding, CheckResult,
    CitationVerdict, CitationReport,
    EventType, EventStudyResult,
    SkillStatus, HarnessSkill, AuditEntry,
)
from .consistency import TradePlan, check_trade_plan
from .citation import Citation, verify_citations, http_resolver
from .event_study import study, wave2_estimate
from .registry import SkillRegistry, VerifyCase, GateError
from . import tools, store

__all__ = [
    "Severity", "Finding", "CheckResult",
    "CitationVerdict", "CitationReport",
    "EventType", "EventStudyResult",
    "SkillStatus", "HarnessSkill", "AuditEntry",
    "TradePlan", "check_trade_plan",
    "Citation", "verify_citations", "http_resolver",
    "study", "wave2_estimate",
    "SkillRegistry", "VerifyCase", "GateError",
    "tools", "store",
]
