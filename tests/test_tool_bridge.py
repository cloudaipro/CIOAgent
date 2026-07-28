"""
test_tool_bridge.py — cio.tool_bridge: SdkMcpTool -> agents.FunctionTool
adapter (Architect brief Step 12; plan of record
docs/BOT-CHAT-OPENAI-MIGRATION.md §4). Standalone: nothing else imports
cio.tool_bridge yet — Step 13 wires OpenAIRuntime on top of it.

Offline, no network, no real LLM calls. market_clock is the only tool invoked
end-to-end through a real (non-spy) handler, because it takes no args and
touches nothing but the local clock. Every other invocation test uses a
throwaway SdkMcpTool double so this file never has to touch CIO_TOOLS or any
production handler (brief Decision 7).

Schema parity (TestSchemaParity) is asserted against what claude_agent_sdk
itself registers — via a real create_sdk_mcp_server(...) call, reading back
the schemas through the same request-handler path the SDK uses internally —
rather than against a second reimplementation of its logic. A test that
copies the thing it is checking proves nothing.
"""
from __future__ import annotations

import asyncio
import copy
import json

import pytest
from agents import FunctionTool
from agents.strict_schema import ensure_strict_json_schema
from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server
from mcp import types

import cio.agent as agent
from cio import tool_bridge as tb


def _run(coro):
    return asyncio.run(coro)


async def _ok_handler(args: dict) -> dict:
    return {"content": [{"type": "text", "text": "ok"}]}


def _spy_tool(sink: dict, *, input_schema=None) -> SdkMcpTool:
    """An SdkMcpTool whose handler records the args dict it is called with
    into `sink` (in place) and returns fixed text "ok"."""
    async def handler(args: dict) -> dict:
        sink.clear()
        sink.update(args)
        return {"content": [{"type": "text", "text": "ok"}]}
    return SdkMcpTool(name="spy", description="spy tool",
                       input_schema=input_schema if input_schema is not None
                       else {"a": int, "b": int},
                       handler=handler)


def _sdk_registered_schemas() -> dict[str, dict]:
    """Ground truth for parity: the schemas claude_agent_sdk itself registers
    for CIO_TOOLS, read back through the real create_sdk_mcp_server /
    list_tools request-handler path (not a reimplementation of _build_schema)."""
    cfg = create_sdk_mcp_server("tool-bridge-parity-check", "1.0.0", agent.CIO_TOOLS)
    server = cfg["instance"]

    async def _list_tools():
        result = await server.request_handlers[types.ListToolsRequest](None)
        return result.root.tools

    return {t.name: t.inputSchema for t in _run(_list_tools())}


@pytest.fixture(scope="module")
def real_schemas() -> dict[str, dict]:
    return _sdk_registered_schemas()


# ---------------------------------------------------------------------------
# Conversion coverage — DoD 2 bullet 1, DoD 3
# ---------------------------------------------------------------------------

class TestConversionCoverage:
    def test_all_44_tools_present(self):
        assert len(agent.CIO_TOOLS) == 44
        assert len(tb.OPENAI_TOOLS) == len(agent.CIO_TOOLS)

    def test_preserves_cio_tools_ordering(self):
        assert [ft.name for ft in tb.OPENAI_TOOLS] == [t.name for t in agent.CIO_TOOLS]

    @pytest.mark.parametrize("t", agent.CIO_TOOLS, ids=lambda t: t.name)
    def test_each_tool_converts_without_error(self, t):
        ft = tb.to_function_tool(t)
        assert isinstance(ft, FunctionTool)
        assert ft.name == t.name
        assert ft.description == t.description


# ---------------------------------------------------------------------------
# Schema parity with claude_agent_sdk's own _build_schema — DoD 2 bullet 2
# ---------------------------------------------------------------------------

class TestSchemaParity:
    def test_real_schema_names_match_cio_tools(self, real_schemas):
        assert set(real_schemas) == {t.name for t in agent.CIO_TOOLS}

    @pytest.mark.parametrize("t", agent.CIO_TOOLS, ids=lambda t: t.name)
    def test_claude_schema_matches_real_sdk_output(self, t, real_schemas):
        assert tb._claude_schema(t) == real_schemas[t.name]

    def test_shape_distribution_matches_verified_brief_counts(self):
        # Brief Decisions 3/4: 16 empty, 27 param-mapping, 1 already JSON
        # Schema (harness_event_study). Independently re-verified against the
        # live CIO_TOOLS before writing the converter; pinned here so a future
        # CIO_TOOLS edit that changes the mix doesn't drift unnoticed.
        shapes = {"empty": 0, "param_type": 0, "already_schema": 0}
        for t in agent.CIO_TOOLS:
            s = t.input_schema
            if s == {}:
                shapes["empty"] += 1
            elif "type" in s and "properties" in s and isinstance(s["type"], str):
                shapes["already_schema"] += 1
            else:
                shapes["param_type"] += 1
        assert shapes == {"empty": 16, "param_type": 27, "already_schema": 1}


class TestUnknownTypeFallback:
    def test_unrecognised_python_type_falls_back_to_string(self):
        spy = SdkMcpTool(name="spy_unknown_type", description="d",
                          input_schema={"weird": list}, handler=_ok_handler)
        schema = tb._claude_schema(spy)
        assert schema["properties"]["weird"] == {"type": "string"}
        assert schema["required"] == ["weird"]


# ---------------------------------------------------------------------------
# Optional-parameter nullability — DoD 2 bullet 3, brief Decision 4
# ---------------------------------------------------------------------------

class TestOptionalParamNullability:
    def test_harness_event_study_horizon_days_is_nullable_and_required(self):
        schema = tb._openai_schema(agent.t_harness_event_study)
        assert schema["required"] == ["event_type", "horizon_days"]
        assert schema["properties"]["horizon_days"] == {
            "anyOf": [{"type": "integer"}, {"type": "null"}]
        }
        # The already-required property is carried through unchanged.
        assert (schema["properties"]["event_type"]
                == agent.t_harness_event_study.input_schema["properties"]["event_type"])

    def test_does_not_mutate_the_original_input_schema(self):
        before = copy.deepcopy(agent.t_harness_event_study.input_schema)
        tb._openai_schema(agent.t_harness_event_study)
        assert agent.t_harness_event_study.input_schema == before

    @pytest.mark.parametrize("t", agent.CIO_TOOLS, ids=lambda t: t.name)
    def test_ensure_strict_json_schema_accepts_each_converted_schema(self, t):
        # Must not raise, for any of the 44 -- brief DoD 2 bullet 3.
        ensure_strict_json_schema(copy.deepcopy(tb._openai_schema(t)))


# ---------------------------------------------------------------------------
# on_invoke_tool: argument parsing / None-stripping / output joining
# ---------------------------------------------------------------------------

class TestOnInvokeToolArgs:
    def test_empty_string_args_become_empty_dict(self):
        received: dict = {}
        ft = tb.to_function_tool(_spy_tool(received))
        out = _run(ft.on_invoke_tool(None, ""))
        assert received == {}
        assert out == "ok"

    def test_blank_args_become_empty_dict(self):
        received: dict = {}
        ft = tb.to_function_tool(_spy_tool(received))
        _run(ft.on_invoke_tool(None, "   "))
        assert received == {}

    def test_none_valued_keys_are_stripped_before_the_handler(self):
        received: dict = {}
        ft = tb.to_function_tool(_spy_tool(received))
        _run(ft.on_invoke_tool(None, json.dumps({"a": 1, "b": None})))
        assert received == {"a": 1}


class TestHarnessEventStudyDefaultPreserved:
    """cio/harness/tools.py:39 reads inp.get('horizon_days', 20) -- a
    present-but-null key would defeat that default (brief Decision 4). Uses a
    double with the real tool's name/schema, not the real handler, so this
    stays a pure adapter test decoupled from event_study's business logic."""

    def _tool(self, sink: dict) -> SdkMcpTool:
        real = agent.t_harness_event_study
        return _spy_tool(sink, input_schema=real.input_schema)

    def test_omitted_horizon_days_reaches_handler_absent(self):
        received: dict = {}
        ft = tb.to_function_tool(self._tool(received))
        _run(ft.on_invoke_tool(None, json.dumps({"event_type": "earnings"})))
        assert "horizon_days" not in received
        assert received["event_type"] == "earnings"

    def test_explicit_null_horizon_days_reaches_handler_absent(self):
        received: dict = {}
        ft = tb.to_function_tool(self._tool(received))
        _run(ft.on_invoke_tool(
            None, json.dumps({"event_type": "earnings", "horizon_days": None})))
        assert "horizon_days" not in received
        assert received["event_type"] == "earnings"


# ---------------------------------------------------------------------------
# Real end-to-end invocation — DoD 2 bullet 4
# ---------------------------------------------------------------------------

class TestMarketClockEndToEnd:
    """The one real (non-spy) end-to-end check -- market_clock takes no args
    and touches nothing but the local clock, so it's safe to actually invoke
    through the full adapter path."""

    def test_matches_the_handler_called_directly(self):
        ft = tb.to_function_tool(agent.t_market_clock)
        via_adapter = _run(ft.on_invoke_tool(None, "{}"))
        direct = _run(agent.t_market_clock.handler({}))
        assert via_adapter == direct["content"][0]["text"]

    def test_also_matches_via_module_level_OPENAI_TOOLS(self):
        ft = next(f for f in tb.OPENAI_TOOLS if f.name == "market_clock")
        via_adapter = _run(ft.on_invoke_tool(None, ""))
        direct = _run(agent.t_market_clock.handler({}))
        assert via_adapter == direct["content"][0]["text"]


# ---------------------------------------------------------------------------
# Failure paths never raise out of on_invoke_tool — brief Decision 6 / R2
# ---------------------------------------------------------------------------

class TestFailurePathsNeverRaise:
    def test_malformed_json_args_returns_error_string(self):
        ft = tb.to_function_tool(agent.t_market_clock)
        out = _run(ft.on_invoke_tool(None, "{not valid json"))
        assert isinstance(out, str) and "error" in out.lower()

    def test_non_object_json_args_returns_error_string(self):
        ft = tb.to_function_tool(agent.t_market_clock)
        out = _run(ft.on_invoke_tool(None, "[1, 2, 3]"))
        assert isinstance(out, str) and "error" in out.lower()

    def test_raising_handler_returns_error_string_instead_of_raising(self):
        async def boom(args: dict) -> dict:
            raise RuntimeError("boom")
        ft = tb.to_function_tool(
            SdkMcpTool(name="boom_tool", description="d", input_schema={}, handler=boom))
        out = _run(ft.on_invoke_tool(None, "{}"))
        assert isinstance(out, str)
        assert "error" in out.lower()
        assert "boom" in out


class TestContentBlockJoining:
    def test_multiple_text_blocks_are_joined(self):
        async def handler(args: dict) -> dict:
            return {"content": [{"type": "text", "text": "a"},
                                 {"type": "text", "text": "b"}]}
        ft = tb.to_function_tool(
            SdkMcpTool(name="multi", description="d", input_schema={}, handler=handler))
        assert _run(ft.on_invoke_tool(None, "{}")) == "ab"

    def test_block_without_text_key_contributes_nothing(self):
        async def handler(args: dict) -> dict:
            return {"content": [{"type": "text", "text": "a"}, {"type": "image"}]}
        ft = tb.to_function_tool(
            SdkMcpTool(name="mixed", description="d", input_schema={}, handler=handler))
        assert _run(ft.on_invoke_tool(None, "{}")) == "a"

    def test_empty_content_list_returns_empty_string(self):
        async def handler(args: dict) -> dict:
            return {"content": []}
        ft = tb.to_function_tool(
            SdkMcpTool(name="empty", description="d", input_schema={}, handler=handler))
        assert _run(ft.on_invoke_tool(None, "{}")) == ""
