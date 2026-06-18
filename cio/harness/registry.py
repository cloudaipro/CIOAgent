"""registry.py — the self-authoring loop (meta-capability) with admission gate.

Why this exists: the whole point of "harness engineering" as a capability is that
when a user finds a defect, the agent turns it into a DURABLE check — not a
one-session patch. But the evaluation (HARNESS-ENGINEERING-EVALUATION.md) is
blunt about the trap: an agent that authors AND validates its own checks by its
own judgment reproduces the self-correction failure it is trying to fix
(Huang et al. 2023). Voyager only works because skills are execution-validated
before admission; Reflexion only works because the feedback is external.

So this registry enforces a strict admission gate every skill must pass — built-in
or self-authored, no fast path:

    PROPOSED --verify(cases)--> VERIFIED --approve(owner)--> APPROVED --activate--> ACTIVE

Invariants (the safeguards, enforced here):
  * verify() requires 100% pass on the must-pass case set (external verification).
  * approve() is REFUSED unless status is VERIFIED (human approval cannot precede
    external verification).
  * activate() only from APPROVED.
  * run_active() dispatches ONLY active skills.
  * every transition is appended to the skill's audit trail.

Deterministic, never-execs-free-form-text: a skill arrives as a concrete callable
plus a verifier case set. Pure in-memory with optional JSON persistence; it does
NOT migrate cfo.db (least blast radius).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

from .models import HarnessSkill, SkillStatus, AuditEntry


@dataclass
class VerifyCase:
    """One held-out verification case: feed ``input`` to skill.check, expect
    ``expected`` (compared by ``matcher``, default equality)."""
    name: str
    input: Any
    expected: Any
    matcher: Callable[[Any, Any], bool] | None = None

    def passes(self, got: Any) -> bool:
        m = self.matcher or (lambda a, b: a == b)
        try:
            return bool(m(got, self.expected))
        except Exception:
            return False


@dataclass
class GateError:
    """Returned (not raised) when a transition is refused, so callers can branch
    without try/except and the refusal itself is auditable."""
    ok: bool = False
    reason: str = ""


class SkillRegistry:
    """Governs harness skills through the admission gate."""

    def __init__(self, pass_threshold: float = 1.0,
                 clock: Callable[[], str] | None = None):
        self._skills: dict[str, HarnessSkill] = {}
        self.pass_threshold = pass_threshold
        self._clock = clock or (lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    # --- lifecycle ----------------------------------------------------------
    def register(self, skill: HarnessSkill) -> HarnessSkill:
        """Add a skill in PROPOSED state. Re-registering an id is refused."""
        if skill.id in self._skills:
            raise ValueError(f"skill id already registered: {skill.id}")
        skill.status = SkillStatus.PROPOSED
        skill.created_at = skill.created_at or self._clock()
        self._audit(skill, "system", "register",
                    {"origin": skill.origin, "kind": skill.kind})
        self._skills[skill.id] = skill
        return skill

    def verify(self, skill_id: str, cases: list[VerifyCase],
               actor: str = "ci") -> GateError | dict:
        """External-verification gate. Run the skill against held-out cases.

        100% (>= pass_threshold) ⇒ VERIFIED; any shortfall ⇒ REJECTED. Returns a
        result dict on success, GateError on a structural refusal.
        """
        sk = self._skills.get(skill_id)
        if sk is None:
            return GateError(reason=f"unknown skill: {skill_id}")
        if sk.status not in (SkillStatus.PROPOSED, SkillStatus.REJECTED):
            return GateError(reason=f"verify requires PROPOSED, got {sk.status.name}")
        if sk.check is None:
            return GateError(reason="skill has no check callable")
        if not cases:
            return GateError(reason="verification requires at least one case")

        results = []
        passed = 0
        for case in cases:
            try:
                got = sk.check(case.input)
                ok = case.passes(got)
            except Exception as e:                 # a throwing check is a failing check
                got, ok = f"<raised {type(e).__name__}>", False
            passed += int(ok)
            results.append({"case": case.name, "ok": ok, "got": _safe(got)})

        rate = passed / len(cases)
        detail = {"passed": passed, "total": len(cases), "rate": round(rate, 3),
                  "results": results}
        if rate >= self.pass_threshold:
            sk.status = SkillStatus.VERIFIED
            sk.verify_detail = detail
            self._audit(sk, actor, "verify_pass", detail)
        else:
            sk.status = SkillStatus.REJECTED
            sk.verify_detail = detail
            self._audit(sk, actor, "verify_fail", detail)
        return detail

    def approve(self, skill_id: str, approver: str) -> GateError | HarnessSkill:
        """Human-in-the-loop gate. REFUSED unless VERIFIED — approval cannot
        precede external verification."""
        sk = self._skills.get(skill_id)
        if sk is None:
            return GateError(reason=f"unknown skill: {skill_id}")
        if sk.status != SkillStatus.VERIFIED:
            return GateError(
                reason=f"approve requires VERIFIED, got {sk.status.name} "
                       "(verification must precede approval)")
        if not approver:
            return GateError(reason="approver required")
        sk.status = SkillStatus.APPROVED
        sk.approved_by = approver
        self._audit(sk, approver, "approve", {})
        return sk

    def activate(self, skill_id: str, actor: str = "system") -> GateError | HarnessSkill:
        sk = self._skills.get(skill_id)
        if sk is None:
            return GateError(reason=f"unknown skill: {skill_id}")
        if sk.status != SkillStatus.APPROVED:
            return GateError(reason=f"activate requires APPROVED, got {sk.status.name}")
        sk.status = SkillStatus.ACTIVE
        self._audit(sk, actor, "activate", {})
        return sk

    def retire(self, skill_id: str, actor: str = "system") -> GateError | HarnessSkill:
        sk = self._skills.get(skill_id)
        if sk is None:
            return GateError(reason=f"unknown skill: {skill_id}")
        sk.status = SkillStatus.RETIRED
        self._audit(sk, actor, "retire", {})
        return sk

    def admit(self, skill: HarnessSkill, cases: list[VerifyCase],
              approver: str) -> GateError | HarnessSkill:
        """Convenience: register → verify → approve → activate in one call,
        short-circuiting on the first refusal. Still passes every gate."""
        self.register(skill)
        v = self.verify(skill.id, cases)
        if isinstance(v, GateError):
            return v
        if skill.status != SkillStatus.VERIFIED:
            return GateError(reason="verification failed", )
        a = self.approve(skill.id, approver)
        if isinstance(a, GateError):
            return a
        return self.activate(skill.id)

    # --- dispatch -----------------------------------------------------------
    def run_active(self, skill_id: str, value: Any) -> Any:
        """Run ONLY an active skill. Non-active ⇒ refused (GateError)."""
        sk = self._skills.get(skill_id)
        if sk is None:
            return GateError(reason=f"unknown skill: {skill_id}")
        if sk.status != SkillStatus.ACTIVE:
            return GateError(reason=f"skill not active: {sk.status.name}")
        return sk.check(value)

    # --- queries ------------------------------------------------------------
    def get(self, skill_id: str) -> HarnessSkill | None:
        return self._skills.get(skill_id)

    def active(self) -> list[HarnessSkill]:
        return [s for s in self._skills.values() if s.status == SkillStatus.ACTIVE]

    def all(self) -> list[HarnessSkill]:
        return list(self._skills.values())

    # --- persistence (metadata only; callables are re-bound on load) --------
    def to_json(self) -> str:
        return json.dumps({"skills": [s.to_row() for s in self._skills.values()],
                           "pass_threshold": self.pass_threshold}, indent=2)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

    # --- internal -----------------------------------------------------------
    def _audit(self, sk: HarnessSkill, actor: str, action: str, detail: dict) -> None:
        sk.audit.append(AuditEntry(ts=self._clock(), actor=actor,
                                   action=action, detail=detail))


def _safe(v: Any) -> Any:
    """Make a check output JSON-loggable without exploding on rich objects."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    for attr in ("to_row", "codes"):
        if hasattr(v, attr):
            try:
                return getattr(v, attr)()
            except Exception:
                pass
    return repr(v)[:200]
