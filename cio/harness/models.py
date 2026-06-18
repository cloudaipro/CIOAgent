"""models.py — shared data structures for the harness layer.

Pure dataclasses + enums. No LLM, no DB, no network. These are the in-memory
shapes the four harness capabilities (consistency / citation / event_study /
registry) produce and exchange.

Mirrors the TIRF convention: every collection defaults empty, every field has a
safe default, so a bare/partial input still yields a valid, serialisable object
instead of a None-hole that explodes downstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Any, Callable


class Severity(IntEnum):
    """Finding severity. BLOCK is the only level that fails a CheckResult."""
    INFO = 1
    WARN = 2
    BLOCK = 3


@dataclass
class Finding:
    """One issue raised by a harness check."""
    code: str = ""
    severity: Severity = Severity.INFO
    message: str = ""
    fix: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = int(self.severity)
        d["severity_label"] = self.severity.name
        return d


@dataclass
class CheckResult:
    """Outcome of a deterministic check: a list of findings + a roll-up.

    ``ok`` is True iff no BLOCK finding is present (WARN/INFO do not fail the
    check, they annotate it). ``blocked`` is the inverse for readability.
    """
    findings: list[Finding] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return any(f.severity >= Severity.BLOCK for f in self.findings)

    @property
    def ok(self) -> bool:
        return not self.blocked

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARN]

    @property
    def blocks(self) -> list[Finding]:
        return [f for f in self.findings if f.severity >= Severity.BLOCK]

    def codes(self) -> list[str]:
        return [f.code for f in self.findings]

    def to_row(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "findings": [f.to_row() for f in self.findings],
            "context": dict(self.context),
        }


# --- V2 citation ------------------------------------------------------------
@dataclass
class CitationVerdict:
    """Per-URL result of fetch-before-cite."""
    url: str = ""
    host: str = ""
    tier: int = 3                # source_policy.Tier value
    tier_label: str = ""
    http_status: int | None = None
    live: bool = False
    ok: bool = False             # live AND tier acceptable for its use
    backs_material: bool = False
    reason: str = ""

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CitationReport:
    verdicts: list[CitationVerdict] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    material_verified: bool = False

    @property
    def blocked(self) -> bool:
        return any(f.severity >= Severity.BLOCK for f in self.findings)

    @property
    def ok(self) -> bool:
        return not self.blocked

    @property
    def dead_urls(self) -> list[str]:
        return [v.url for v in self.verdicts if not v.live]

    def to_row(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "blocked": self.blocked,
            "material_verified": self.material_verified,
            "verdicts": [v.to_row() for v in self.verdicts],
            "findings": [f.to_row() for f in self.findings],
        }


# --- V3 event study ---------------------------------------------------------
class EventType(IntEnum):
    OTHER = 0
    ANALYST_ACTION = 1        # upgrade/downgrade, PT change
    PRODUCT_MILESTONE = 2     # tape-out, GA, first production
    STRATEGIC_CUSTOMER = 3    # marquee customer / design win
    GOV_ANNOUNCEMENT = 4      # policy, official statement
    EARNINGS = 5
    MNA = 6                   # merger / acquisition


@dataclass
class EventStudyResult:
    """A distribution of post-catalyst forward returns. Never a point estimate."""
    event_type: str = ""
    horizon_days: int = 20
    n: int = 0
    mean: float = 0.0
    median: float = 0.0
    p25: float = 0.0
    p75: float = 0.0
    hit_rate: float = 0.0           # fraction of analogs with positive return
    sample: str = "reference"       # "historical" | "reference"
    note: str = ""

    def to_row(self) -> dict[str, Any]:
        return asdict(self)


# --- meta registry ----------------------------------------------------------
class SkillStatus(IntEnum):
    PROPOSED = 1
    VERIFIED = 2
    APPROVED = 3
    ACTIVE = 4
    REJECTED = 5
    RETIRED = 6


@dataclass
class AuditEntry:
    ts: str = ""
    actor: str = ""
    action: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class HarnessSkill:
    """A harness capability under registry governance.

    ``check`` is a callable ``(case_input) -> Any`` used both at verification
    time (compared against a case's expected output) and at run time. For
    self-authored skills the registry never execs free-form model text — a skill
    arrives as a concrete callable + a verifier case set, and is admitted only by
    passing that set and an explicit owner approval.
    """
    id: str = ""
    name: str = ""
    version: int = 1
    kind: str = "validator"          # validator | resolver | analytic
    trigger: str = ""                # the defect this addresses
    origin: str = "builtin"          # builtin | self_authored | user
    status: SkillStatus = SkillStatus.PROPOSED
    created_at: str = ""
    approved_by: str = ""
    check: Callable[[Any], Any] | None = None
    audit: list[AuditEntry] = field(default_factory=list)
    verify_detail: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "kind": self.kind,
            "trigger": self.trigger,
            "origin": self.origin,
            "status": int(self.status),
            "status_label": self.status.name,
            "created_at": self.created_at,
            "approved_by": self.approved_by,
            "audit": [asdict(a) for a in self.audit],
            "verify_detail": dict(self.verify_detail),
        }
