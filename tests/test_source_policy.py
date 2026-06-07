"""Evidence-integrity tests (ARCHITECT-BRIEF items 1-4).

Covers the locked source-trust policy (cio/data/source_policy.py), the agent's
tier-classification + Sources-footer rendering, the gate-safety of the new
primary-source tools, and clinicaltrials offline-safety.
"""
import asyncio

import pytest

from cio.data import source_policy as sp
from cio.data.source_policy import Tier


def _run(coro):
    return asyncio.run(coro)


def _tool_text(result: dict) -> str:
    return result["content"][0]["text"]


# --- classify(): tiering + fail-closed -------------------------------------

def test_monexa_is_low_trust():
    assert sp.classify("monexa.ai") == Tier.LOW_TRUST


def test_unlisted_dot_ai_is_low_trust():
    assert sp.classify("foo.ai") == Tier.LOW_TRUST


def test_unknown_domain_fails_closed_to_low_trust():
    assert sp.classify("totally-unknown.example") == Tier.LOW_TRUST


def test_primary_sources():
    assert sp.classify("clinicaltrials.gov") == Tier.PRIMARY
    assert sp.classify("data.sec.gov") == Tier.PRIMARY
    assert sp.classify("www.fda.gov") == Tier.PRIMARY


def test_reputable_sources():
    assert sp.classify("www.reuters.com") == Tier.REPUTABLE
    assert sp.classify("finance.yahoo.com") == Tier.REPUTABLE


def test_pr_wire_is_reputable_not_primary():
    # Owner decision: wires are Tier 2, not Tier 1.
    assert sp.classify("prnewswire.com") == Tier.REPUTABLE
    assert sp.classify("businesswire.com") == Tier.REPUTABLE


def test_issuer_domain_promoted_to_primary():
    iss = {"neurocrine.com"}
    assert sp.classify("neurocrine.com", iss) == Tier.PRIMARY
    # subdomain (IR page) also resolves Tier 1
    assert sp.classify("ir.neurocrine.com", iss) == Tier.PRIMARY
    # without the issuer set it fails closed
    assert sp.classify("neurocrine.com") == Tier.LOW_TRUST


def test_known_low_trust_listed():
    assert sp.classify("fool.com") == Tier.LOW_TRUST
    assert sp.classify("reddit.com") == Tier.LOW_TRUST


# --- is_verified(): corroboration rule -------------------------------------

def test_one_primary_verifies():
    assert sp.is_verified([Tier.PRIMARY]) is True


def test_single_reputable_does_not_verify():
    assert sp.is_verified([Tier.REPUTABLE]) is False


def test_two_reputable_verify():
    assert sp.is_verified([Tier.REPUTABLE, Tier.REPUTABLE]) is True


def test_low_trust_never_verifies():
    assert sp.is_verified([Tier.LOW_TRUST, Tier.LOW_TRUST]) is False
    assert sp.is_verified([Tier.LOW_TRUST]) is False


# --- agent: _classify_url + footer rendering -------------------------------

def test_classify_url_uses_scope_issuer_domains():
    import cio.agent as agent
    scope = "test:classify"
    agent._ISSUER_DOMAINS.pop(scope, None)
    assert agent._classify_url("https://neurocrine.com/pipeline", scope) == Tier.LOW_TRUST
    agent._register_issuer_domain("https://neurocrine.com", scope)
    assert agent._classify_url("https://ir.neurocrine.com/x", scope) == Tier.PRIMARY


def test_footer_tier_labels_and_unverified_verdict():
    import cio.agent as agent
    sources = [{"url": "https://monexa.ai/a", "title": "x", "tier": Tier.LOW_TRUST}]
    out = agent._append_sources("Claim [1].", sources, searched=True, scope="test:foot1")
    assert "Tier 3 LOW-TRUST" in out
    assert "⚠️" in out and "unverified" in out.lower()


def test_footer_primary_gives_corroborated_verdict():
    import cio.agent as agent
    sources = [{"url": "https://clinicaltrials.gov/study/NCT1", "title": "t",
                "tier": Tier.PRIMARY}]
    out = agent._append_sources("Phase 3 schizophrenia [1].", sources,
                                searched=True, scope="test:foot2")
    assert "Tier 1 PRIMARY" in out
    assert "✅" in out


def test_footer_two_reputable_corroborated():
    import cio.agent as agent
    sources = [
        {"url": "https://reuters.com/a", "title": "a", "tier": Tier.REPUTABLE},
        {"url": "https://apnews.com/b", "title": "b", "tier": Tier.REPUTABLE},
    ]
    out = agent._append_sources("M&A confirmed [1][2].", sources,
                                searched=True, scope="test:foot3")
    assert "✅" in out


# --- gate-safety of the new primary-source tools ---------------------------

def test_sec_filings_tool_gated(monkeypatch):
    import cio.agent as agent
    monkeypatch.delenv("CIO_SEC_UA", raising=False)
    out = _tool_text(_run(agent.t_sec_filings.handler({"symbol": "AAPL"})))
    assert "not configured" in out.lower()


def test_analyst_ratings_tool_gated(monkeypatch):
    import cio.agent as agent
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    out = _tool_text(_run(agent.t_analyst_ratings.handler({"symbol": "AAPL"})))
    assert "not configured" in out.lower()


def test_company_profile_tool_gated(monkeypatch):
    import cio.agent as agent
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    out = _tool_text(_run(agent.t_company_profile.handler({"symbol": "AAPL"})))
    assert "not configured" in out.lower()


# --- clinicaltrials offline-safety -----------------------------------------

def test_clinicaltrials_offline_safe(monkeypatch):
    from cio.data import clinicaltrials
    # _http.get_json degrades to None on any network failure; search_trials must
    # then return [] (not raise). Patch the name bound in the module.
    monkeypatch.setattr(clinicaltrials, "get_json", lambda *a, **k: None)
    assert clinicaltrials.search_trials("anything") == []


def test_clinicaltrials_empty_query():
    from cio.data import clinicaltrials
    assert clinicaltrials.search_trials("") == []
    assert clinicaltrials.search_trials("   ") == []


# --- scope isolation + verifier gating (review fixes) ----------------------

def test_issuer_domains_isolated_by_scope():
    import cio.agent as agent
    agent._ISSUER_DOMAINS.pop("scopeA", None)
    agent._ISSUER_DOMAINS.pop("scopeB", None)
    agent._register_issuer_domain("https://neurocrine.com", "scopeA")
    # scopeA sees it as Tier 1; scopeB must NOT (no cross-scope leak).
    assert agent._classify_url("https://neurocrine.com/x", "scopeA") == Tier.PRIMARY
    assert agent._classify_url("https://neurocrine.com/x", "scopeB") == Tier.LOW_TRUST


def test_verifier_off_is_noop(monkeypatch):
    import cio.agent as agent
    monkeypatch.delenv("CIO_VERIFY_CLAIMS", raising=False)
    monkeypatch.delenv("CFO_VERIFY_CLAIMS", raising=False)
    # Even with a material-looking claim, disabled flag → None, no network/anthropic call.
    out = _run(agent._run_verifier("Revenue was $2.31B [1].",
                                   [{"url": "https://sec.gov/x", "title": "t", "tier": Tier.PRIMARY}]))
    assert out is None
