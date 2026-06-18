"""advisor.py — propose-only Digester/Planner (HarnessX AEGIS, declawed).

HarnessX's AEGIS loop is Digester → Planner → Evolver → Critic+gate, and it
auto-ships edits on a verifier score. CIOAgent has no market verifier (a trade's
correctness is unknowable for months, and profit ≠ a correct decision), so the
auto-ship half is off the table. But the first two stages — Digester (compress
traces into "what keeps failing") and Planner (propose untried directions) — need
only traces + rules, not a verifier. This module borrows exactly those two and
discards the autonomy.

HARD BOUNDARY: this advisor can only PROPOSE. It writes PROPOSED records via
store.propose and calls nothing else — never verify/approve/activate. The human
gate (cio.harness.admin / the dashboard /skills tab) remains the only path to
ACTIVE. Worst case is a bad suggestion the owner rejects at the gate (archived
with reason). It runs on demand over a batch of accumulated traces, not per turn.

Deterministic core: pattern detection is rule-based over structured trace records
(the finding codes the after_model run loop already produces). No LLM is in the
tested path; an LLM may later enrich the human-readable ``rule_spec`` text, which
is out of this deterministic core.

Trace record shape (minimal, documented):
    {"codes": ["R3_RR_FLOOR", ...], "ref": "<transcript anchor>"}
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import store


@dataclass
class DefectPattern:
    code: str
    count: int
    example: str = ""


@dataclass
class ProposalDraft:
    name: str
    trigger: str
    kind: str = "validator"
    rule_spec: str = ""


# Known finding codes → a proposal template (name, what it would address, kind).
# Only codes with a template become drafts; an unknown recurring code is reported
# by digest() but produces no auto-draft (the owner still sees it).
_TEMPLATES: dict[str, tuple[str, str, str]] = {
    "R3_RR_FLOOR": (
        "rr_floor_escalation",
        "sub-floor reward:risk recurred as WARN; consider escalating to BLOCK",
        "validator"),
    "R1_REL_WEAKNESS": (
        "rel_weakness_catalyst_enforcer",
        "relative-weakness entries recurred; consider a hard catalyst-clearance gate",
        "validator"),
    "C_DEAD_URL": (
        "citation_staleness_recheck",
        "dead cited URLs recurred; consider a staleness re-check on aging citations",
        "resolver"),
    "C_MATERIAL_UNVERIFIED": (
        "material_corroboration_tighten",
        "material facts under-corroborated; consider requiring a second live source",
        "resolver"),
}


def digest(traces: list[dict], min_count: int = 3) -> list[DefectPattern]:
    """Compress traces into recurring defect patterns. A code must appear at least
    ``min_count`` times to surface (single occurrences are noise). Sorted by
    frequency, descending. Never raises."""
    counts: dict[str, int] = {}
    examples: dict[str, str] = {}
    for t in traces or []:
        try:
            codes = t.get("codes") or []
            ref = t.get("ref", "")
        except AttributeError:
            continue
        for code in codes:
            counts[code] = counts.get(code, 0) + 1
            examples.setdefault(code, ref)
    pats = [DefectPattern(code=c, count=n, example=examples.get(c, ""))
            for c, n in counts.items() if n >= min_count]
    return sorted(pats, key=lambda p: (-p.count, p.code))


def plan(patterns: list[DefectPattern]) -> list[ProposalDraft]:
    """Map recurring patterns to draft proposals using the known templates. A
    pattern with no template is skipped (reported by digest, not auto-drafted)."""
    out: list[ProposalDraft] = []
    for p in patterns:
        tmpl = _TEMPLATES.get(p.code)
        if tmpl is None:
            continue
        name, what, kind = tmpl
        out.append(ProposalDraft(
            name=name, kind=kind,
            trigger=f"{what} (seen {p.count}x; e.g. {p.example or 'n/a'})",
            rule_spec=(f"Auto-digest: {p.count} occurrences of {p.code}. "
                       "Owner to implement a concrete check in candidates.py, "
                       "then verify/approve/activate via the gate.")))
    return out


def run_advisor(traces: list[dict], path=None, min_count: int = 3) -> list[dict]:
    """Digest → plan → file PROPOSED records. Returns the records filed. This is
    the ONLY side effect; the advisor cannot move a record past PROPOSED.
    Idempotency is intentionally NOT enforced here — the owner dedups at review;
    the store keeps a full audit either way."""
    drafts = plan(digest(traces, min_count=min_count))
    filed: list[dict] = []
    for d in drafts:
        rec = store.propose(d.name, d.trigger, kind=d.kind, rule_spec=d.rule_spec,
                            origin="advisor",
                            path=path or store.DEFAULT_STORE)
        filed.append(rec)
    return filed
