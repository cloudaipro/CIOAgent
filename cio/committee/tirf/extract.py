"""
extract.py — Parse TIRF deliverables out of a specialist's parsed yaml (PRD §5).

Tolerant by design: LLMs emit evidence/assumptions/reasoning/counterarguments/
sources in many shapes (list-of-dicts, list-of-strings, maps, scalars). Every
helper coerces gracefully and never raises; missing keys yield empty lists, which
``validate`` then scores as low completeness rather than an error.

Input is the dict produced by ``engine.parse_yaml_block`` for one specialist.
Output is a fully-populated (but un-scored) ``SpecialistResearch``.
"""
from __future__ import annotations

import logging
from typing import Any

from .models import (
    Assumption,
    Counterargument,
    EvidenceItem,
    ReasoningStep,
    SourceRef,
    SpecialistResearch,
)

log = logging.getLogger(__name__)


def _as_list(val: Any) -> list:
    """Coerce a yaml value into a list. None→[]; scalar→[scalar]; dict→[dict]."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, (str, int, float, dict)):
        return [val]
    try:
        return list(val)
    except Exception:
        return []


def _txt(val: Any) -> str:
    return "" if val is None else str(val).strip()


def _evidence(raw: Any) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    for el in _as_list(raw):
        try:
            if isinstance(el, dict):
                items.append(EvidenceItem(
                    source=_txt(el.get("source")),
                    date=_txt(el.get("date")),
                    finding=_txt(el.get("finding") or el.get("fact") or el.get("note")),
                    impact=(_txt(el.get("impact")) or "neutral").lower(),
                    relevance=(_txt(el.get("relevance")) or "related").lower(),
                    confidence=(_txt(el.get("confidence")) or "medium").lower(),
                ))
            else:
                # bare string evidence: the whole string is the finding
                items.append(EvidenceItem(finding=_txt(el), impact="neutral"))
        except Exception:
            log.debug("evidence item parse skipped: %r", el, exc_info=True)
    return items


def _assumptions(raw: Any) -> list[Assumption]:
    out: list[Assumption] = []
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, dict):
                out.append(Assumption(name=_txt(k), value=_txt(v.get("value") or v),
                                      confidence=(_txt(v.get("confidence")) or "medium").lower()))
            else:
                out.append(Assumption(name=_txt(k), value=_txt(v)))
        return out
    for el in _as_list(raw):
        if isinstance(el, dict):
            # {name:.., value:..} or a single {key: value}
            if "name" in el or "value" in el:
                out.append(Assumption(name=_txt(el.get("name")), value=_txt(el.get("value")),
                                      confidence=(_txt(el.get("confidence")) or "medium").lower()))
            else:
                for k, v in el.items():
                    out.append(Assumption(name=_txt(k), value=_txt(v)))
        else:
            out.append(Assumption(name=_txt(el)))
    return out


def _reasoning(raw: Any) -> list[ReasoningStep]:
    steps: list[ReasoningStep] = []
    src = raw
    if isinstance(raw, dict):
        # tolerate {steps: [...], conclusion: ..}
        src = raw.get("steps") or raw.get("chain") or list(raw.values())
    for i, el in enumerate(_as_list(src), start=1):
        if isinstance(el, dict):
            text = _txt(el.get("statement") or el.get("step") or el.get("text") or el)
        else:
            text = _txt(el)
        if text:
            steps.append(ReasoningStep(step_no=i, statement=text))
    return steps


def _counterargs(raw: Any) -> list[Counterargument]:
    out: list[Counterargument] = []
    for el in _as_list(raw):
        if isinstance(el, dict):
            text = _txt(el.get("argument") or el.get("counterargument") or el.get("point") or el)
        else:
            text = _txt(el)
        if text:
            out.append(Counterargument(argument=text))
    return out


def _sources(raw: Any) -> list[SourceRef]:
    out: list[SourceRef] = []
    for el in _as_list(raw):
        if isinstance(el, dict):
            ref = _txt(el.get("source") or el.get("reference") or el.get("name") or el)
        else:
            ref = _txt(el)
        if ref:
            out.append(SourceRef(reference=ref))
    return out


def extract_specialist(parsed: dict, role_key: str = "", title: str = "") -> SpecialistResearch:
    """Build a SpecialistResearch from one specialist's parsed yaml dict.

    ``parsed`` is the dict returned by engine.parse_yaml_block. Tolerant of
    {"_raw": ...} (failed yaml) — yields an empty research object with the vote
    defaulted. Never raises.
    """
    parsed = parsed if isinstance(parsed, dict) else {}
    vote = _txt(parsed.get("vote") or "HOLD") or "HOLD"
    reason = _txt(parsed.get("reason") or parsed.get("_raw"))
    sp = SpecialistResearch(
        role_key=role_key,
        title=title,
        vote=vote,
        confidence=parsed.get("confidence", 50),
        reason=reason,
        conclusion=vote,
        evidence=_evidence(parsed.get("evidence")),
        assumptions=_assumptions(parsed.get("assumptions")),
        reasoning=_reasoning(parsed.get("reasoning")),
        counterarguments=_counterargs(parsed.get("counterarguments")),
        sources=_sources(parsed.get("sources")),
    )
    sp.evidence_count = len(sp.evidence)
    sp.counter_count = len(sp.counterarguments)
    return sp


def extract_from_opinion(opinion: dict) -> SpecialistResearch:
    """Extract from an engine opinion dict that already carries ``_parsed`` (the raw
    yaml) — or fall back to reading the TIRF keys straight off the opinion.

    run_specialist attaches the parsed yaml under ``_parsed`` so TIRF can read the
    original deliverables without re-calling the model.
    """
    parsed = opinion.get("_parsed")
    if not isinstance(parsed, dict):
        # opinion itself may carry the keys (merged role fields path)
        parsed = opinion
    return extract_specialist(parsed, role_key=opinion.get("key", ""),
                              title=opinion.get("title", ""))
