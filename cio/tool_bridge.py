"""tool_bridge.py — adapts CIO_TOOLS (claude_agent_sdk.SdkMcpTool) into
agents.FunctionTool so the OpenAI Agents SDK side can call the same 45 tools.

Standalone module: nothing imports this yet. cio/agent_openai.py (Step 13) is
the first caller. This step only proves the 45 tools cross cleanly to the
OpenAI side; it wires up no runtime, model, or session (Architect brief
Step 12; plan of record docs/BOT-CHAT-OPENAI-MIGRATION.md §4).

Two schema quirks this module exists to handle — see handoff/ARCHITECT-BRIEF.md
Step 12, Decisions 3 and 4 (independently re-verified against the live .venv
before writing this, not just trusted):

1. SdkMcpTool.input_schema is not JSON Schema for 44 of the 45 CIO_TOOLS — it's
   either `{}` (16 tools) or a `{param_name: python_type}` mapping (28 tools;
   only str/int/float/bool appear). claude_agent_sdk turns that into real JSON
   Schema at MCP-registration time via a private closure (`_build_schema`,
   claude_agent_sdk/__init__.py:399-417, local to create_sdk_mcp_server — not
   importable). `_claude_schema` below reimplements that closure's exact
   behaviour so a converted tool's schema never drifts from what the
   Claude-side runtime already advertises for the same tool. Parity is proved
   in tests/test_tool_bridge.py by reading back real output from
   create_sdk_mcp_server, not by re-deriving the same logic a second time.

2. agents.strict_schema.ensure_strict_json_schema (run automatically by
   FunctionTool.__post_init__, since strict_json_schema defaults to True) turns
   every property into "required" without making it nullable. Today that only
   affects harness_event_study.horizon_days — the one CIO_TOOLS schema that is
   already JSON Schema *and* leaves a property out of "required". Left alone,
   the model would be forced to always supply horizon_days, silently defeating
   the `inp.get("horizon_days", 20)` default at cio/harness/tools.py:39 (a
   present-but-null key returns None from .get, not the default). `_openai_schema`
   pre-promotes any such property to `{"anyOf": [<original>, {"type": "null"}]}`
   + required, so the model can pass JSON null to mean "omit"; `to_function_tool`'s
   `on_invoke_tool` then strips every None-valued key before calling the
   handler, restoring the Claude-side default semantics exactly.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agents import FunctionTool
from claude_agent_sdk import SdkMcpTool

from .agent import CIO_TOOLS

log = logging.getLogger(__name__)

_PY_TYPE_TO_JSON_TYPE = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _claude_schema(t: SdkMcpTool) -> dict[str, Any]:
    """Reimplement claude_agent_sdk's private `_build_schema`
    (__init__.py:399-417) exactly, for the shapes CIO_TOOLS actually uses:

    - already valid JSON Schema (has a string "type" + "properties")  -> passed
      through unchanged, same as the real SDK.
    - a `{param_name: python_type}` mapping, INCLUDING `{}`           -> an
      object schema with every key in "required" (the real SDK does not treat
      any parameter as optional at this layer — optionality is a concept this
      module introduces on the OpenAI side only, in `_openai_schema`).

    An unrecognised python type falls back to `{"type": "string"}` rather than
    raising — a tool that cannot be precisely described must still be callable.

    Do not import the original: it is a closure local to create_sdk_mcp_server.
    """
    schema = t.input_schema
    if isinstance(schema, dict):
        if ("type" in schema and "properties" in schema
                and isinstance(schema["type"], str)):
            return schema
        properties = {name: {"type": _PY_TYPE_TO_JSON_TYPE.get(py_type, "string")}
                      for name, py_type in schema.items()}
        return {"type": "object", "properties": properties,
                "required": list(properties.keys())}
    # No CIO_TOOLS schema takes this path today (verified: 16 empty-dict, 27
    # param-mapping, 1 passthrough — see tests/test_tool_bridge.py). Kept so a
    # hypothetical future non-dict schema is still callable, not fatal.
    #
    # KG-16: claude_agent_sdk DOES handle TypedDict schemas here, via
    # _typeddict_to_json_schema. We deliberately do not — but returning an empty
    # schema silently means the model is told the tool takes NO parameters while
    # the Claude path still sees them all: a parity break the parity test cannot
    # catch, because no tool exercises this branch. Warn loudly so the divergence
    # is visible the moment someone adds such a tool, rather than discovered as a
    # tool that mysteriously never receives its arguments.
    log.warning(
        "tool_bridge: %r has a non-dict input_schema (%s); the OpenAI side will see it as "
        "taking NO parameters, while the Claude path still passes them. Convert it to a "
        "{name: type} dict, or teach _claude_schema about TypedDict (KG-16).",
        t.name, type(schema).__name__,
    )
    return {"type": "object", "properties": {}}


def _openai_schema(t: SdkMcpTool) -> dict[str, Any]:
    """`_claude_schema(t)`, with every property that is in "properties" but not
    in "required" promoted to nullable + required (module docstring, point 2).

    Never mutates `t.input_schema` or anything reachable from it — always
    returns either the unmodified schema (no optional properties) or a fresh
    dict built from shallow copies.
    """
    schema = _claude_schema(t)
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    optional = [name for name in properties if name not in required]
    if not optional:
        return schema
    new_properties = dict(properties)
    for name in optional:
        new_properties[name] = {"anyOf": [properties[name], {"type": "null"}]}
    return {**schema, "properties": new_properties, "required": list(properties.keys())}


def to_function_tool(t: SdkMcpTool) -> FunctionTool:
    """Adapt one Claude-side SdkMcpTool into an OpenAI Agents SDK FunctionTool.
    Reads `t`; never modifies it or its handler (brief Decision 7 — the bridge
    adapts, it does not modify)."""
    schema = _openai_schema(t)

    async def on_invoke_tool(_ctx: Any, args: str) -> str:
        if args and args.strip():
            try:
                parsed = json.loads(args)
            except json.JSONDecodeError as e:
                return f"Error: {t.name} received malformed arguments: {e}"
        else:
            parsed = {}
        if not isinstance(parsed, dict):
            return (f"Error: {t.name} received malformed arguments: "
                     "expected a JSON object")
        # Strip None so a null the model sent for an optional param (see
        # _openai_schema) falls through to the handler's own default instead
        # of overriding it with None — module docstring, point 2.
        parsed = {k: v for k, v in parsed.items() if v is not None}
        try:
            result = await t.handler(parsed)
        except Exception as e:  # R2: a tool failure must never kill the turn
            return f"Error: {t.name} failed: {e}"
        blocks = (result or {}).get("content") or []
        return "".join(b.get("text", "") for b in blocks if isinstance(b, dict))

    return FunctionTool(
        name=t.name,
        description=t.description,
        params_json_schema=schema,
        on_invoke_tool=on_invoke_tool,
        strict_json_schema=True,  # brief Decision 4: safe now optionals are nullable
    )


# Built at import time, preserving CIO_TOOLS' ordering (brief Decision 5).
OPENAI_TOOLS: list[FunctionTool] = [to_function_tool(t) for t in CIO_TOOLS]
