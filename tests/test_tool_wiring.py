"""Confirms the EDGAR + Finnhub + ClinicalTrials primary-source tools are actually
WIRED into the agent: present in CIO_TOOLS, exposed to the SDK with the right name
prefix and allow-list, and that each tool calls through to its data-layer function
(tool → data wire), emitting a reviewable cio.evidence log line.
"""
import asyncio
import logging

import pytest

PRIMARY_TOOLS = {
    "sec_filings", "analyst_ratings", "earnings_info",
    "company_profile", "clinical_trials",
}


def _run(coro):
    return asyncio.run(coro)


def _text(result: dict) -> str:
    return result["content"][0]["text"]


# --- registration / exposure ----------------------------------------------

def test_all_primary_tools_in_cio_tools():
    from cio.agent import CIO_TOOLS
    names = {t.name for t in CIO_TOOLS}
    missing = PRIMARY_TOOLS - names
    assert not missing, f"primary tools not registered in CIO_TOOLS: {missing}"


def test_primary_tools_have_mcp_prefixed_names():
    from cio.agent import _TOOL_NAMES
    for t in PRIMARY_TOOLS:
        assert f"mcp__cio__{t}" in _TOOL_NAMES, f"{t} not in _TOOL_NAMES"


def test_primary_tools_in_allowed_tools():
    from cio.agent import build_options
    allowed = set(build_options().allowed_tools)
    for t in PRIMARY_TOOLS:
        assert f"mcp__cio__{t}" in allowed, f"{t} not allow-listed for the SDK"


def test_primary_tools_have_descriptions_naming_their_source():
    from cio.agent import CIO_TOOLS
    by_name = {t.name: (t.description or "") for t in CIO_TOOLS}
    assert "EDGAR" in by_name["sec_filings"] or "SEC" in by_name["sec_filings"]
    assert "Finnhub" in by_name["analyst_ratings"]
    assert "clinicaltrials.gov" in by_name["clinical_trials"]


# --- tool -> data-layer wire (configured path, mocked data) ----------------

def test_sec_filings_calls_edgar(monkeypatch):
    import cio.agent as agent
    from cio.data import edgar
    monkeypatch.setattr(edgar, "_user_agent", lambda: "Test UA test@example.com")
    canned = [{"form": "8-K", "filed": "2025-02-06", "report_date": "2025-02-06",
               "title": "Q4 results", "url": "https://www.sec.gov/x"}]
    called = {}

    def fake_recent(sym, *a, **k):
        called["sym"] = sym
        return canned
    monkeypatch.setattr(edgar, "recent_filings", fake_recent)
    out = _text(_run(agent.t_sec_filings.handler({"symbol": "nbix"})))
    assert called["sym"] == "NBIX"          # symbol normalized + passed through
    assert "8-K" in out and "sec.gov" in out


def test_analyst_ratings_calls_finnhub(monkeypatch):
    import cio.agent as agent
    from cio.data import finnhub
    monkeypatch.setattr(finnhub, "_token", lambda: "k")
    monkeypatch.setattr(finnhub, "analyst_recs",
                        lambda sym: {"period": "2026-05-01", "buy": 12, "hold": 3, "sell": 0})
    out = _text(_run(agent.t_analyst_ratings.handler({"symbol": "AAPL"})))
    assert '"buy": 12' in out


def test_company_profile_calls_finnhub_and_registers_issuer(monkeypatch):
    import cio.agent as agent
    from cio.data import finnhub
    scope = agent._scope()
    agent._ISSUER_DOMAINS.pop(scope, None)
    monkeypatch.setattr(finnhub, "_token", lambda: "k")
    monkeypatch.setattr(finnhub, "company_profile",
                        lambda sym: {"name": "Neurocrine", "weburl": "https://neurocrine.com"})
    out = _text(_run(agent.t_company_profile.handler({"symbol": "NBIX"})))
    assert "Neurocrine" in out
    # the issuer domain is now promoted to Tier 1 for this scope
    assert agent._classify_url("https://ir.neurocrine.com/x", scope) == agent._sp.Tier.PRIMARY


def test_clinical_trials_calls_registry(monkeypatch):
    import cio.agent as agent
    from cio.data import clinicaltrials
    canned = [{"nct_id": "NCT07227818", "title": "Schizophrenia relapse",
               "phase": "PHASE3", "status": "RECRUITING",
               "conditions": ["Schizophrenia"], "interventions": ["NBI-1117568"],
               "url": "https://clinicaltrials.gov/study/NCT07227818"}]
    monkeypatch.setattr(clinicaltrials, "search_trials", lambda q, limit=5: canned)
    out = _text(_run(agent.t_clinical_trials.handler({"query": "neurocrine schizophrenia"})))
    assert "NCT07227818" in out and "Schizophrenia" in out


# --- reviewable evidence logging -------------------------------------------

def test_evidence_log_fires_on_configured_call(monkeypatch, caplog):
    import cio.agent as agent
    from cio.data import edgar
    monkeypatch.setattr(edgar, "_user_agent", lambda: "Test UA test@example.com")
    monkeypatch.setattr(edgar, "recent_filings", lambda sym, *a, **k: [])
    with caplog.at_level(logging.INFO, logger="cio.evidence"):
        _run(agent.t_sec_filings.handler({"symbol": "NBIX"}))
    recs = [r for r in caplog.records if r.name == "cio.evidence"]
    assert recs, "no cio.evidence log emitted"
    msg = recs[-1].getMessage()
    assert "tool=sec_filings" in msg and "symbol=NBIX" in msg and "configured=True" in msg


def test_evidence_log_marks_unconfigured(monkeypatch, caplog):
    import cio.agent as agent
    from cio.data import finnhub
    monkeypatch.setattr(finnhub, "_token", lambda: None)
    with caplog.at_level(logging.INFO, logger="cio.evidence"):
        _run(agent.t_analyst_ratings.handler({"symbol": "AAPL"}))
    msg = [r for r in caplog.records if r.name == "cio.evidence"][-1].getMessage()
    assert "tool=analyst_ratings" in msg and "configured=False" in msg


def test_evidence_log_on_empty_input_guards(caplog):
    # Every primary tool logs even on the empty-input guard, so the logs never
    # silently skip a call. clinical_trials guards on query; the rest on symbol.
    import cio.agent as agent
    cases = [
        (agent.t_clinical_trials, {"query": ""}, "tool=clinical_trials"),
        (agent.t_sec_filings, {"symbol": ""}, "tool=sec_filings"),
        (agent.t_analyst_ratings, {"symbol": ""}, "tool=analyst_ratings"),
        (agent.t_earnings_info, {"symbol": ""}, "tool=earnings_info"),
        (agent.t_company_profile, {"symbol": ""}, "tool=company_profile"),
    ]
    for tool, args, marker in cases:
        with caplog.at_level(logging.INFO, logger="cio.evidence"):
            caplog.clear()
            _run(tool.handler(args))
        msgs = [r.getMessage() for r in caplog.records if r.name == "cio.evidence"]
        assert any(marker in m and "configured=False" in m for m in msgs), \
            f"{marker} did not log on empty-input guard"


def test_committee_bundle_logs_evidence(monkeypatch, caplog):
    # The committee uses EDGAR/Finnhub via bundle._external (NOT the chat tools),
    # so it must emit its own cio.evidence lines, tagged via=committee.
    from cio.committee import bundle
    from cio import data
    monkeypatch.setattr(data.edgar, "_user_agent", lambda: "UA test@example.com")
    monkeypatch.setattr(data.finnhub, "_token", lambda: "k")
    monkeypatch.setattr(data, "recent_filings", lambda sym, limit=4: [{"form": "8-K"}])
    monkeypatch.setattr(data, "analyst_recs", lambda sym: {"buy": 5})
    monkeypatch.setattr(data, "earnings_calendar", lambda sym: {"date": "2026-07-01"})
    monkeypatch.setattr(data, "insider_net",
                        lambda sym: {"buy_count": 2, "sell_count": 0,
                                     "net_shares": 100, "cluster_buy": False})
    with caplog.at_level(logging.INFO, logger="cio.evidence"):
        bundle._external("NBIX", is_etf=False)
    msgs = [r.getMessage() for r in caplog.records if r.name == "cio.evidence"]
    assert any("tool=sec_filings" in m and "via=committee" in m for m in msgs)
    assert any("tool=analyst_ratings" in m and "found=True" in m for m in msgs)
    assert any("tool=earnings_info" in m and "via=committee" in m for m in msgs)
    assert any("tool=insider_tx" in m and "found=True" in m for m in msgs)


def test_committee_bundle_logs_etf_skips(monkeypatch, caplog):
    from cio.committee import bundle
    from cio import data
    monkeypatch.setattr(data.edgar, "_user_agent", lambda: None)     # EDGAR off
    monkeypatch.setattr(data.finnhub, "_token", lambda: None)        # Finnhub off
    monkeypatch.setattr(data, "recent_filings", lambda sym, limit=4: [])
    with caplog.at_level(logging.INFO, logger="cio.evidence"):
        bundle._external("SPY", is_etf=True)
    msgs = [r.getMessage() for r in caplog.records if r.name == "cio.evidence"]
    assert any("tool=sec_filings" in m and "configured=False" in m for m in msgs)
    assert any("tool=analyst_ratings" in m and "skipped=etf" in m for m in msgs)


def test_ev_no_trailing_space(caplog):
    import cio.agent as agent
    with caplog.at_level(logging.INFO, logger="cio.evidence"):
        agent._ev("t", "X", True)                      # no extra kwargs
    msg = [r for r in caplog.records if r.name == "cio.evidence"][-1].getMessage()
    assert msg == "tool=t symbol=X configured=True"     # no trailing space
