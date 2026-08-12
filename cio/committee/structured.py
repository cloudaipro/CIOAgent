"""Provider-neutral structured-output contracts for the committee pipeline.

The committee asks every decision-making model for JSON that is constrained by
one of these schemas.  Provider adapters map the same schema to their native
structured-output mechanism; this module also validates the returned value so
transport-level success can never be mistaken for a usable committee answer.
"""
from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator


def _string(description: str = "") -> dict:
    out = {"type": "string", "minLength": 1}
    if description:
        out["description"] = description
    return out


def _number(minimum: float = 0, maximum: float = 100) -> dict:
    return {"type": "number", "minimum": minimum, "maximum": maximum}


def _strings(min_items: int = 1) -> dict:
    return {"type": "array", "items": _string(), "minItems": min_items}


EVIDENCE_ITEM = {
    "type": "object",
    "properties": {
        "source": _string(),
        "date": _string("YYYY-MM-DD, or the DATA as-of timestamp when appropriate"),
        "finding": _string(),
        "impact": {"type": "string", "enum": ["positive", "negative", "neutral"]},
        "relevance": {"type": "string", "enum": ["direct", "related", "indirect"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["source", "date", "finding", "impact", "relevance", "confidence"],
    "additionalProperties": False,
}

ASSUMPTION_ITEM = {
    "type": "object",
    "properties": {
        "name": _string(),
        "value": _string(),
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["name", "value", "confidence"],
    "additionalProperties": False,
}

COMMON_SPECIALIST_PROPERTIES = {
    "vote": {"type": "string", "enum": ["BUY", "HOLD", "SELL"]},
    "confidence": _number(),
    "reason": _string(),
    "evidence": {"type": "array", "items": EVIDENCE_ITEM, "minItems": 3},
    "assumptions": {"type": "array", "items": ASSUMPTION_ITEM, "minItems": 1},
    "reasoning": _strings(),
    "counterarguments": _strings(3),
    "sources": _strings(),
    "memory_note": _string("One durable qualitative lesson without prices or P&L figures"),
}

ROLE_PROPERTIES: dict[str, dict[str, dict]] = {
    "market": {
        "market_trend": _string(),
        "market_score": _number(),
        "macro_risks": _string(),
        "capital_flows": _string(),
    },
    "macro": {
        "macro_environment": {"type": "string", "enum": ["supportive", "neutral", "restrictive"]},
        "geopolitical_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "commodity_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "currency_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "regulatory_risk": {"type": "string", "enum": ["low", "medium", "high"]},
        "major_events": _strings(),
        "affected_sectors_positive": _strings(),
        "affected_sectors_negative": _strings(),
    },
    "equity": {
        "financial_health": _string(),
        "earnings_growth": _string(),
        "quality_score": _string(),
        "management_assessment": _string(),
        "investment_thesis": _string(),
    },
    "industry": {
        "industry_score": _number(),
        "industry_cycle": _string(),
        "tailwinds": _strings(),
        "headwinds": _strings(),
    },
    "valuation": {
        "fair_value": _string("A supported value or an explicit statement that it is not quantifiable"),
        "valuation_rating": _string(),
        "upside_potential": _string(),
        "downside_risk": _string(),
    },
    "quant": {
        "trend_score": _number(-100, 100),
        "momentum_signal": _string(),
        "probability_upside": _string(),
    },
    "etf": {
        "etf_score": _number(),
        "portfolio_overlap": _string(),
        "liquidity_rating": _string(),
        "tracking_quality": _string(),
    },
    "risk": {
        "risk_score": _number(),
        "major_risks": _strings(),
        "worst_case_scenario": _string(),
    },
    "catalyst": {
        "bullish_catalysts": _strings(),
        "bearish_catalysts": _strings(),
        "event_timeline": _strings(),
    },
}


def specialist_schema(role_key: str) -> dict:
    role = ROLE_PROPERTIES[role_key]
    properties = {**role, **COMMON_SPECIALIST_PROPERTIES}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


MODERATOR_SCHEMA = {
    "type": "object",
    "properties": {
        "committee_recommendation": {
            "type": "string", "enum": ["STRONG BUY", "BUY", "HOLD", "SELL", "STRONG SELL"]
        },
        "agreement_score": _number(),
        "majority_view": _string(),
        "minority_view": _string(),
        "key_disagreements": _string(),
    },
    "required": [
        "committee_recommendation", "agreement_score", "majority_view",
        "minority_view", "key_disagreements",
    ],
    "additionalProperties": False,
}

SCENARIO_ITEM = {
    "type": "object",
    "properties": {
        "scenario": {"type": "string", "enum": ["Base", "Bull", "Bear"]},
        "probability": _number(),
        "price_target": _string("A DATA-supported value or 'Not quantifiable'"),
        "key_drivers": _string(),
    },
    "required": ["scenario", "probability", "price_target", "key_drivers"],
    "additionalProperties": False,
}

CIO_SCHEMA = {
    "type": "object",
    "properties": {
        "final_recommendation": {
            "type": "string", "enum": ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]
        },
        "confidence_score": _number(),
        "risk_rating": _string(),
        "time_horizon": _string(),
        "macro_alignment_score": _number(),
        "geopolitical_risk_score": _number(),
        "external_risk_adjustment": _string(),
        "base_case": _string(),
        "bull_case": _string(),
        "bear_case": _string(),
        "scenarios": {"type": "array", "items": SCENARIO_ITEM, "minItems": 3, "maxItems": 3},
        "memory_note": _string("One durable qualitative lesson without prices or P&L figures"),
    },
    "required": [
        "final_recommendation", "confidence_score", "risk_rating", "time_horizon",
        "macro_alignment_score", "geopolitical_risk_score", "external_risk_adjustment",
        "base_case", "bull_case", "bear_case", "scenarios", "memory_note",
    ],
    "additionalProperties": False,
}

DEBATE_TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "text": _string("The debate rebuttal or response as plain prose, at most 120 words"),
    },
    "required": ["text"],
    "additionalProperties": False,
}


def response_format(schema: dict, name: str) -> dict:
    """OpenAI-compatible strict response_format used by OpenAI and llama.cpp."""
    safe_name = re.sub(r"[^A-Za-z0-9_-]", "_", name)[:64] or "committee_output"
    return {
        "type": "json_schema",
        "json_schema": {
            "name": safe_name,
            "strict": True,
            "schema": schema,
        },
    }


def prompt_instruction(schema: dict) -> str:
    """Visible fallback contract for servers that ignore native schema fields."""
    return (
        "Return ONLY one JSON object matching this JSON Schema. Do not use a "
        "Markdown fence or add prose outside the object:\n"
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    )


def parse(text: str) -> dict:
    """Parse native/plain JSON first, then fenced JSON, then legacy fenced YAML."""
    if not isinstance(text, str):
        return {"_raw": text}
    raw = text.strip()
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            return value
    except (TypeError, ValueError):
        pass

    json_blocks = re.findall(r"```json\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if json_blocks:
        try:
            value = json.loads(json_blocks[-1].strip())
            if isinstance(value, dict):
                return value
        except (TypeError, ValueError):
            pass

    # Backward compatibility for stored transcripts and non-native test doubles.
    import yaml
    yaml_blocks = re.findall(r"```yaml\s*(.*?)```", raw, re.DOTALL | re.IGNORECASE)
    if yaml_blocks:
        try:
            value = yaml.safe_load(yaml_blocks[-1].strip())
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    return {"_raw": text}


def validate(value: Any, schema: dict) -> tuple[bool, str]:
    """Return a concise validation result suitable for operator logs."""
    if not isinstance(value, dict) or "_raw" in value:
        return False, "response is not a parseable JSON object"
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: list(e.path))
    if not errors:
        return True, ""
    err = errors[0]
    path = ".".join(str(p) for p in err.absolute_path) or "<root>"
    return False, f"{path}: {err.message}"


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
