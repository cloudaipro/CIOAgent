"""tools.py — Anthropic tool specs + dispatch + the default skill registry.

This is the wiring surface: it exposes V1/V2/V3 as callable tools (so the live
agent can invoke them without editing the 71KB cio/agent.py), and it builds a
default SkillRegistry in which the three built-ins are admitted THROUGH the same
admission gate every self-authored skill must pass (dogfooding the gate).

Wiring point (documented, not auto-applied): append TOOL_SPECS to the agent's
tool list and route those names to dispatch(); see HARNESS-TESTING-PLAN.md.
"""
from __future__ import annotations

from typing import Any

from . import consistency, citation, event_study
from .consistency import TradePlan
from .citation import Citation
from .models import EventType, CheckResult, CitationReport, SkillManifest
from .registry import SkillRegistry, VerifyCase, HarnessSkill


# --- skill check wrappers (single-arg, for the registry) --------------------
def check_trade_plan_skill(inp: Any) -> CheckResult:
    plan = inp if isinstance(inp, TradePlan) else TradePlan(**(inp or {}))
    return consistency.check_trade_plan(plan)


def verify_citations_skill(inp: dict) -> CitationReport:
    inp = inp or {}
    cits = [c if isinstance(c, Citation) else Citation(**c)
            for c in inp.get("citations", [])]
    return citation.verify_citations(
        cits, resolver=inp.get("resolver"), issuer_domains=inp.get("issuer_domains"))


def event_study_skill(inp: dict):
    inp = inp or {}
    et = _coerce_event_type(inp.get("event_type"))
    return event_study.study(et, horizon_days=inp.get("horizon_days", 20),
                             samples=inp.get("samples"))


def _coerce_event_type(v) -> EventType:
    if isinstance(v, EventType):
        return v
    if isinstance(v, str):
        try:
            return EventType[v.strip().upper()]
        except KeyError:
            return EventType.OTHER
    if isinstance(v, int):
        try:
            return EventType(v)
        except ValueError:
            return EventType.OTHER
    return EventType.OTHER


# --- Anthropic tool specs ---------------------------------------------------
TOOL_SPECS = [
    {
        "name": "harness_check_trade_plan",
        "description": ("Deterministic consistency gate for an emitted entry/exit "
                        "plan. Flags a pullback/limit entry that doubles as a "
                        "relative-weakness signal (Rule 2c), incoherent stop/target, "
                        "sub-floor R:R, short pre-earnings windows, and chasing."),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "entry_kind": {"type": "string",
                               "enum": ["pullback", "limit", "breakout", "market"]},
                "entry_price": {"type": "number"},
                "current_price": {"type": "number"},
                "stop_price": {"type": "number"},
                "target_price": {"type": "number"},
                "market_bias": {"type": "string", "enum": ["up", "flat", "down"]},
                "pct_today": {"type": "number"},
                "at_upper_band": {"type": "boolean"},
                "entry_date": {"type": "string"},
                "earnings_date": {"type": "string"},
                "min_hold_days": {"type": "integer"},
            },
        },
    },
    {
        "name": "harness_verify_citations",
        "description": ("Fetch-before-cite. Resolves every cited URL and fails "
                        "closed on a dead link; only live sources count toward "
                        "material-fact corroboration (reuses source_policy)."),
        "input_schema": {
            "type": "object",
            "properties": {
                "citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "url": {"type": "string"},
                            "backs_material": {"type": "boolean"},
                        },
                        "required": ["url"],
                    },
                },
            },
            "required": ["citations"],
        },
    },
    {
        "name": "harness_event_study",
        "description": ("Post-catalyst forward-return DISTRIBUTION by event type "
                        "(mean/median/quartiles/hit-rate). Never a point forecast; "
                        "falls back to a labelled reference prior when analogs are "
                        "scarce."),
        "input_schema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string",
                               "enum": [e.name.lower() for e in EventType]},
                "horizon_days": {"type": "integer"},
            },
            "required": ["event_type"],
        },
    },
]


def dispatch(name: str, args: dict) -> dict:
    """Route a tool call to its capability and return a JSON-able dict.
    Live calls use the real http_resolver (no fake injected here)."""
    if name == "harness_check_trade_plan":
        return check_trade_plan_skill(args).to_row()
    if name == "harness_verify_citations":
        return verify_citations_skill(args).to_row()
    if name == "harness_event_study":
        return event_study_skill(args).to_row()
    return {"error": f"unknown tool: {name}"}


# --- default registry (dogfoods the admission gate on the built-ins) --------
def _builtin_verify_cases() -> dict[str, list[VerifyCase]]:
    """Held-out cases each built-in must pass to be admitted. Offline/deterministic
    (V2 cases inject a dict-backed fake resolver)."""
    blocked = lambda r, exp: r.blocked is exp
    has_code = lambda r, code: code in r.codes()
    sample_is = lambda r, s: r.sample == s

    fake = {"https://live.sec.gov/x": 200, "https://dead.cnbc.com/x": 404}
    resolver = lambda u: fake.get(u)

    return {
        "v1_consistency": [
            VerifyCase("mchp_block",
                       {"symbol": "MCHP", "entry_kind": "limit", "entry_price": 97.5,
                        "current_price": 99.0, "market_bias": "up"},
                       True, matcher=blocked),
            VerifyCase("clean_ok",
                       {"symbol": "X", "entry_kind": "breakout", "entry_price": 100,
                        "current_price": 99, "stop_price": 95, "target_price": 110,
                        "market_bias": "up"},
                       False, matcher=blocked),
        ],
        "v2_citation": [
            VerifyCase("dead_url_block",
                       {"citations": [{"url": "https://dead.cnbc.com/x",
                                       "backs_material": True}], "resolver": resolver},
                       True, matcher=blocked),
            VerifyCase("live_primary_ok",
                       {"citations": [{"url": "https://live.sec.gov/x",
                                       "backs_material": True}], "resolver": resolver},
                       False, matcher=blocked),
        ],
        "v3_event_study": [
            VerifyCase("reference_fallback",
                       {"event_type": "strategic_customer"},
                       "reference", matcher=sample_is),
            VerifyCase("historical_fit",
                       {"event_type": "earnings",
                        "samples": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
                       "historical", matcher=sample_is),
        ],
    }


def build_default_registry(approver: str = "owner",
                           clock=None) -> SkillRegistry:
    """Build a registry with V1/V2/V3 admitted through register→verify→approve→
    activate. If any built-in failed its own verifier it would NOT be active —
    same gate as a self-authored skill."""
    reg = SkillRegistry(clock=clock)
    cases = _builtin_verify_cases()
    specs = [
        ("v1_consistency", "Trade-plan consistency gate", "validator",
         "entry/stop self-contradiction (Rule 2c)", check_trade_plan_skill,
         _MANIFESTS["v1_consistency"]),
        ("v2_citation", "Fetch-before-cite", "resolver",
         "fabricated / dead source URL", verify_citations_skill,
         _MANIFESTS["v2_citation"]),
        ("v3_event_study", "Post-catalyst distribution", "analytic",
         "ungrounded magnitude forecast", event_study_skill,
         _MANIFESTS["v3_event_study"]),
    ]
    for sid, nm, kind, trig, fn, manifest in specs:
        sk = HarnessSkill(id=sid, name=nm, kind=kind, trigger=trig,
                          origin="builtin", check=fn, manifest=manifest)
        reg.admit(sk, cases[sid], approver=approver)
    return reg


# Dogfood the change-manifest on the built-ins (HarnessX §B.3). They are origin=
# "builtin" so the opt-in require_manifest gate exempts them, but carrying a
# manifest keeps the dashboard/audit surface uniform and documents the trace
# signature you'd grep production transcripts for to confirm each is firing.
_MANIFESTS = {
    "v1_consistency": SkillManifest(
        bucket="processor",
        predicted_stabilizes=["trade-plan emission"],
        attribution_signature="CheckResult finding code in {R1_REL_WEAKNESS,R2_PLAN_COHERENCE,R3_RR_FLOOR,R4_EARNINGS_WINDOW,R5_CHASE}",
        capability_evidence="tests/test_harness.py::TestV1Consistency",
        rollback_target="pre-harness SYSTEM_PROMPT (no consistency gate)"),
    "v2_citation": SkillManifest(
        bucket="processor",
        predicted_stabilizes=["material-fact citation"],
        attribution_signature="CitationReport finding code in {C_DEAD_URL,C_MATERIAL_UNVERIFIED}",
        capability_evidence="tests/test_harness.py::TestV2Citation",
        rollback_target="source_policy tiering alone (no liveness check)"),
    "v3_event_study": SkillManifest(
        bucket="tool",
        predicted_unlocks=["grounded magnitude answers"],
        attribution_signature="EventStudyResult with sample in {historical,reference}",
        capability_evidence="tests/test_harness.py::TestV3EventStudy",
        rollback_target="prior heuristic point estimate"),
}
