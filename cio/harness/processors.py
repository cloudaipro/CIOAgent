"""processors.py — concrete after_model processors (HarnessX items 1 + 4).

These wrap the EXISTING pure checks (consistency.check_trade_plan,
citation.verify_citations) as hook-bound processors. The check logic is unchanged;
only *invocation* moves from "model called the MCP tool" to "the run loop fired
the processor". Because the loop fires unconditionally, V1/V2 no longer need to be
exposed as model-elective tools (owner decision: door-guard runs V1/V2, V3 stays a
model-pulled analytic tool — it has no unconditional trigger).

Design choices:
  * ANNOTATE-ONLY by default. A processor returns a ⚠️ note to append (same
    contract as cio/agent.py:_run_verifier). It never silently drops the model's
    answer — the bad plan is flagged inline, deterministically, every turn. The
    `intercept` capability exists in the types for a future hard-block mode.
  * STRUCTURED INPUT. V1 reads ```plan {json}``` blocks the model is instructed to
    emit (SYSTEM_PROMPT). Deterministic parse, no NLU, no guessing from prose — if
    no block is present the processor is a clean no-op (cannot false-positive).
  * V2 reads the turn's STRUCTURED sources (already assembled by the agent), not
    URLs scraped from prose. Liveness is the core 347 fix; material-corroboration
    applies only when the answer carries a material claim (flag passed in
    event.extra["material"]).
  * VARIANT ROUTING (item 4). build_runloop consults cio.stock.profiles.harness_for
    to decide which processors run for the situation (committee = full, monitor =
    citation only, swing = consistency). Profiles list "v3" too, but v3 has no
    processor here — it stays a tool — so the loop simply skips it.
"""
from __future__ import annotations

import json
import re
from dataclasses import fields as _dc_fields

from . import consistency, citation
from .consistency import TradePlan
from .citation import Citation, http_resolver
from .models import Severity
from .runloop import Hook, Event, ProcessorResult, Runloop

# ```plan { ... }``` — the model emits any entry/exit plan in this fenced block so
# the gate parses it deterministically instead of guessing from prose.
_PLAN_RE = re.compile(r"```plan\s*(\{.*?\})\s*```", re.DOTALL)

_PLAN_FIELDS = {f.name for f in _dc_fields(TradePlan)}


def _clean_plan(d: dict) -> dict:
    """Keep only known TradePlan fields so TradePlan(**d) can't blow up on an
    extra key the model included."""
    return {k: v for k, v in d.items() if k in _PLAN_FIELDS}


def _extract_plans(text: str) -> list[dict]:
    out: list[dict] = []
    for m in _PLAN_RE.finditer(text or ""):
        try:
            d = json.loads(m.group(1))
        except Exception:
            continue
        if isinstance(d, dict):
            out.append(_clean_plan(d))
    return out


def _findings_note(header: str, findings) -> str:
    blocks = [f for f in findings if f.severity >= Severity.BLOCK]
    warns = [f for f in findings if f.severity == Severity.WARN]
    if not blocks and not warns:
        return ""
    lines = ["", header]
    for f in blocks:
        lines.append(f"  ⛔ BLOCK {f.code}: {f.message}")
    for f in warns:
        lines.append(f"  ⚠️ WARN {f.code}: {f.message}")
    return "\n".join(lines)


class ConsistencyProcessor:
    """V1 as an after_model processor: parse plan blocks, run check_trade_plan,
    annotate on any finding (R1 carries catalyst_check_required at any severity)."""
    name = "v1_consistency"
    hook = Hook.AFTER_MODEL

    def process(self, event: Event) -> ProcessorResult:
        findings = []
        for pd in _extract_plans(event.text):
            res = consistency.check_trade_plan(TradePlan(**pd))
            findings.extend(res.findings)
        note = _findings_note("⚠️ Harness — trade-plan gate:", findings)
        return ProcessorResult(name=self.name, findings=findings, note=note)


class CitationProcessor:
    """V2 as an after_model processor: verify the turn's cited sources are live and
    (for material answers) sufficiently corroborated. Dead link ⇒ BLOCK note."""
    name = "v2_citation"
    hook = Hook.AFTER_MODEL

    def process(self, event: Event) -> ProcessorResult:
        material = bool(event.extra.get("material"))
        cits = _citations_from_sources(event.sources, material)
        if not cits:
            return ProcessorResult(name=self.name)
        resolver = event.resolver or http_resolver
        rep = citation.verify_citations(
            cits, resolver=resolver,
            issuer_domains=event.extra.get("issuer_domains"))
        note = _findings_note("⚠️ Harness — citation gate:", rep.findings)
        return ProcessorResult(name=self.name, findings=rep.findings, note=note)


def _citations_from_sources(sources, material: bool) -> list[Citation]:
    out: list[Citation] = []
    for s in sources or []:
        url = s.get("url") if isinstance(s, dict) else None
        if not url:
            continue
        out.append(Citation(url=url, backs_material=material))
    return out


# --- variant routing (item 4) ----------------------------------------------
# Maps a profile's harness capability id -> processor class. v3 is intentionally
# absent: it is a model-pulled analytic, not a hook processor, so a profile that
# lists "v3" simply contributes no processor here.
_PROCESSORS = {
    "v1": ConsistencyProcessor,
    "v2": CitationProcessor,
}


def _harness_for(profile: str) -> list[str]:
    """Which capabilities are active for a situation. Lazy import keeps cio.harness
    import-light and avoids any import cycle with cio.stock."""
    try:
        from cio.stock import profiles as _p
        return _p.harness_for(profile)
    except Exception:
        return ["v1", "v2"]   # safe default: full after_model scrutiny


def build_runloop(profile: str = "committee") -> Runloop:
    """Assemble the after_model run loop for a situation from its profile."""
    rl = Runloop()
    for key in _harness_for(profile):
        cls = _PROCESSORS.get(key)
        if cls is not None:
            rl.add(cls())
    return rl


def after_model_note(text: str, scope: str = "global", profile: str = "committee",
                     sources=None, resolver=None, material: bool = False) -> str | None:
    """Convenience the agent calls in ask() — mirrors _run_verifier's str|None
    contract. Builds the profile's run loop, fires AFTER_MODEL, returns the
    aggregated note to append (or None). Never raises."""
    try:
        rl = build_runloop(profile)
        ev = Event(hook=Hook.AFTER_MODEL, text=text or "", scope=scope,
                   profile=profile, sources=list(sources or []),
                   resolver=resolver, extra={"material": bool(material)})
        res = rl.fire(ev)
        return res.note or None
    except Exception:
        return None
