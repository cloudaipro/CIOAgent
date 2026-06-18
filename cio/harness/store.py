"""store.py — durable governance records for self-authored harness skills.

The in-memory SkillRegistry (registry.py) governs the lifecycle within a process.
This module persists the *governance state* across restarts as JSON, and is the
ONLY thing the live agent can write to: the agent's `harness_propose_skill` tool
appends a PROPOSED record here and can do nothing else. Verify/approve/activate
are owner actions performed via `python -m cio.harness.admin`, which calls the
gate-enforcing `transition()` below.

Why JSON, not cfo.db: least blast radius — a malformed proposal can never corrupt
the trading database. Why no callables stored: we never serialise/exec model-authored
code. A record carries a human-readable `rule_spec`; the actual check is committed
to code (see candidates.py) and only then can the skill be verified.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .models import SkillStatus

DEFAULT_STORE = Path(__file__).resolve().parents[2] / "data" / "harness_skills.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _load(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"skills": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "skills" not in data:
            return {"skills": []}
        return data
    except Exception:
        return {"skills": []}


def _save(path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def propose(name: str, trigger: str, kind: str = "validator", rule_spec: str = "",
            origin: str = "self_authored", path=DEFAULT_STORE) -> dict:
    """Append a PROPOSED record. This is all the agent can do."""
    data = _load(path)
    rec = {
        "id": "sk_" + uuid.uuid4().hex[:10],
        "name": name,
        "kind": kind,
        "trigger": trigger,
        "rule_spec": rule_spec,
        "origin": origin,
        "status": int(SkillStatus.PROPOSED),
        "status_label": SkillStatus.PROPOSED.name,
        "created_at": _now(),
        "approved_by": "",
        "audit": [{"ts": _now(), "actor": "agent", "action": "propose",
                   "detail": {"kind": kind}}],
    }
    data["skills"].append(rec)
    _save(path, data)
    return rec


def all_records(path=DEFAULT_STORE) -> list[dict]:
    return _load(path).get("skills", [])


def get(sid: str, path=DEFAULT_STORE) -> dict | None:
    return next((r for r in _load(path).get("skills", []) if r["id"] == sid), None)


# Gate ordering — IDENTICAL to the in-memory SkillRegistry. The load-bearing
# invariant: approve requires VERIFIED (owner approval cannot precede external
# verification); activate requires APPROVED.
_ALLOWED_FROM = {
    "verify":   {SkillStatus.PROPOSED, SkillStatus.REJECTED},
    "approve":  {SkillStatus.VERIFIED},
    "activate": {SkillStatus.APPROVED},
    "reject":   {SkillStatus.PROPOSED, SkillStatus.VERIFIED},
    "retire":   {SkillStatus.VERIFIED, SkillStatus.APPROVED, SkillStatus.ACTIVE},
}
_RESULT = {
    "verify": SkillStatus.VERIFIED, "approve": SkillStatus.APPROVED,
    "activate": SkillStatus.ACTIVE, "reject": SkillStatus.REJECTED,
    "retire": SkillStatus.RETIRED,
}


def transition(sid: str, action: str, actor: str, detail: dict | None = None,
               path=DEFAULT_STORE) -> dict:
    """Move a record through the gate. Refuses (does not raise) on an illegal
    transition. Returns {ok, ...}."""
    data = _load(path)
    rec = next((r for r in data["skills"] if r["id"] == sid), None)
    if rec is None:
        return {"ok": False, "reason": f"unknown skill: {sid}"}
    allowed = _ALLOWED_FROM.get(action)
    if allowed is None:
        return {"ok": False, "reason": f"unknown action: {action}"}
    cur = SkillStatus(rec["status"])
    if cur not in allowed:
        return {"ok": False,
                "reason": f"{action} requires {sorted(s.name for s in allowed)}, got {cur.name}"}
    if action == "approve" and not actor:
        return {"ok": False, "reason": "approver required"}
    new = _RESULT[action]
    rec["status"] = int(new)
    rec["status_label"] = new.name
    if action == "approve":
        rec["approved_by"] = actor
    rec.setdefault("audit", []).append(
        {"ts": _now(), "actor": actor, "action": action, "detail": detail or {}})
    _save(path, data)
    return {"ok": True, "id": sid, "status": new.name}
