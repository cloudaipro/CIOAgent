"""
test_agent_committee.py — the conversational agent's run_committee tool.

Covers the seam added when the committee was wired into the chat agent (not just
the /committee slash command): the tool must run the shared pipeline, push the
report path into the document outbox (_PENDING_DOCS), and return the summary text
— never fabricate a verdict. Also checks ask() threads docs out of _run_query's
per-turn stash. No network, no LLM.
"""
from __future__ import annotations

import asyncio

import cio.agent as agent
from cio.committee.delivery import CommitteeArtifact


def _run(coro):
    return asyncio.run(coro)


def _call_tool(args):
    return _run(agent.t_committee.handler(args))


def test_run_committee_tool_emits_document(monkeypatch):
    """Tool pushes the report path into _PENDING_DOCS and returns the summary."""
    async def _fake_produce(symbol, lang, reports_dir, source="command"):
        return CommitteeArtifact(symbol=symbol.upper(),
                                 doc_path="/tmp/AAPL_committee.pdf",
                                 summary="📋 *AAPL Committee Summary*\nBuy")

    monkeypatch.setattr("cio.committee.delivery.produce_report", _fake_produce)
    agent._PENDING_DOCS.clear()

    out = _call_tool({"symbol": "aapl", "lang": ""})

    assert agent._PENDING_DOCS == ["/tmp/AAPL_committee.pdf"]
    text = out["content"][0]["text"]
    assert "Buy" in text and "sent to the user" in text
    agent._PENDING_DOCS.clear()


def test_run_committee_tool_error_emits_no_document(monkeypatch):
    """On a pipeline error the tool returns the message and emits NO document."""
    async def _fake_produce(symbol, lang, reports_dir, source="command"):
        return CommitteeArtifact(symbol=symbol.upper(),
                                 error="No data for ZZZ. Check the symbol.")

    monkeypatch.setattr("cio.committee.delivery.produce_report", _fake_produce)
    agent._PENDING_DOCS.clear()

    out = _call_tool({"symbol": "ZZZ", "lang": ""})

    assert agent._PENDING_DOCS == []
    assert "No data for ZZZ" in out["content"][0]["text"]


def test_run_committee_tool_requires_symbol():
    """Empty symbol → guidance, no run."""
    out = _call_tool({"symbol": "  ", "lang": ""})
    assert "symbol" in out["content"][0]["text"].lower()


def test_produce_report_tags_source_for_capture(monkeypatch):
    """produce_report sets the run-source ContextVar the transcript capture reads,
    so chat-triggered runs are distinguishable from /committee ones."""
    import cio.committee.engine as engine
    from cio.committee import delivery

    seen = {}

    async def _fake_run(sym):
        seen["source"] = engine._RUN_SOURCE.get()   # what _capture would record
        class _R:  # minimal stand-in; .error short-circuits before render
            error = "stop here"
        return _R()

    monkeypatch.setattr("cio.committee.run_committee", _fake_run)

    _run(delivery.produce_report("AAPL", "", reports_dir=None, source="chat"))
    assert seen["source"] == "chat"


def test_ask_threads_docs_from_run_query(monkeypatch):
    """ask() returns (text, images, docs); docs come from the turn's _last_docs."""
    async def run():
        a = agent.CIOAgent(chat_id=7)

        async def fake_run(_prompt):
            a._last_docs = ["/tmp/report.pdf"]   # what the real _run_query stashes
            return ("done", [])

        a._run_query = fake_run
        a._ensure = lambda: asyncio.sleep(0)
        text, images, docs = await a.ask("convene committee on AAPL")
        return text, images, docs, a._last_docs

    text, images, docs, leftover = _run(run())
    assert (text, images, docs) == ("done", [], ["/tmp/report.pdf"])
    assert leftover == []   # drained so a later checkpoint turn can't resend it
