"""
test_tirf.py — offline tests for the Transparent Investment Research Framework
(cio.committee.tirf). Maps 1:1 to docs/TIRF-PRD.md §16 acceptance criteria
(A1…K1) and docs/TIRF-TEST-PLAN.md.

Fully offline: ask_role is monkeypatched, the TIRF store + committee DBs are routed
to tmp_path. No network, no real LLM. Every TIRF function is asserted never-raises.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import textwrap

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from cio.committee import tirf
from cio.committee.tirf import (
    builder, dossier, extract, repro, review, scoring, store, validate,
)
from cio.committee.tirf.models import (
    Assumption, Counterargument, EvidenceItem, ReasoningStep, ResearchReport,
    SourceRef, SpecialistResearch,
)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Isolation: route every committee-side DB to a throwaway file.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_dbs(monkeypatch, tmp_path):
    monkeypatch.setattr("cio.committee.tirf.store.DB_PATH", tmp_path / "tirf.db")
    monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", tmp_path / "mem.db")
    monkeypatch.setattr("cio.committee.transcript.DB_PATH", tmp_path / "tx.db")
    monkeypatch.setattr("cio.committee.usage.DB_PATH", tmp_path / "usage.db")


AS_OF = "2026-06-04T00:00:00"


# ---------------------------------------------------------------------------
# Canned data
# ---------------------------------------------------------------------------

def rich_parsed(vote: str = "BUY", n_evidence: int = 3, n_counter: int = 3,
                with_assumptions: bool = True, with_reasoning: bool = True) -> dict:
    """A specialist's parsed-yaml dict with full TIRF deliverables."""
    evidence = [
        {"source": "SEC 10-Q filing", "date": "2026-06-01", "finding": f"Revenue up {10+i}% YoY",
         "impact": "positive", "relevance": "direct", "confidence": "high"}
        for i in range(n_evidence)
    ]
    d = {
        "vote": vote,
        "confidence": 72,
        "reason": "Strong fundamentals and a supportive setup",
        "evidence": evidence,
        "counterarguments": [f"Risk point {i}" for i in range(n_counter)],
        "sources": ["SEC 10-Q", "Q1 earnings call transcript", "SemiAnalysis industry report"],
    }
    if with_assumptions:
        d["assumptions"] = {"revenue_growth": "15%", "discount_rate": "9%"}
    if with_reasoning:
        d["reasoning"] = ["Evidence shows accelerating revenue",
                          "Higher revenue lifts fair value",
                          "Price sits below fair value"]
    return d


def rich_opinion(key: str, title: str, vote: str = "BUY", **kw) -> dict:
    parsed = rich_parsed(vote=vote, **kw)
    return {"key": key, "title": title, "vote": vote, "confidence": parsed["confidence"],
            "reason": parsed["reason"], "_parsed": parsed}


def bare_opinion(key: str = "x", title: str = "X") -> dict:
    parsed = {"vote": "HOLD", "confidence": 50}
    return {"key": key, "title": title, "vote": "HOLD", "confidence": 50,
            "reason": "", "_parsed": parsed}


FAKE_BUNDLE = {
    "symbol": "MU", "resolved": "MU",
    "quote": {"close": 120.0, "change_pct": 1.5, "volume": 20_000_000},
    "fundamentals": {"name": "Micron", "pe": 18.0, "forward_pe": 9.0, "eps": 6.0,
                     "market_cap": 130_000_000_000, "quoteType": "EQUITY"},
    "ta_signals": {"rsi": "bull", "macd": "bull"},
    "is_etf": False, "as_of": AS_OF,
    "filings": [], "analyst": None, "earnings": None,
}


def full_report(debate: bool = True) -> ResearchReport:
    opinions = [
        rich_opinion("equity", "Equity Research"),
        rich_opinion("valuation", "Valuation"),
        rich_opinion("risk", "Risk Management", vote="SELL"),
        rich_opinion("industry", "Industry Intelligence"),
    ]
    debate_result = {"skipped": False, "exchanges": [
        {"challenger_key": "risk", "challenger_title": "Risk Management",
         "target_key": "valuation", "target_title": "Valuation",
         "challenge": "Valuation ignores tail risk", "response": "Tail risk is priced in"},
    ]} if debate else {"skipped": True, "exchanges": []}
    return builder.build_research_report(
        ticker="MU", bundle=FAKE_BUNDLE, opinions=opinions,
        cio={"final_recommendation": "Buy", "confidence_score": 70},
        debate_result=debate_result, source="cli", run_id="run123",
    )


# ===========================================================================
# C. Evidence scoring (PRD §6)
# ===========================================================================

class TestEvidenceScoring:
    def test_classify_source_tiers(self):                       # C1
        assert scoring.classify_source("SEC 10-Q filing") == ("SEC Filing", 100)
        assert scoring.classify_source("Q1 earnings call transcript") == ("Earnings Call", 90)
        assert scoring.classify_source("management guidance update") == ("Company Guidance", 85)
        assert scoring.classify_source("SemiAnalysis industry report") == ("Industry Research", 80)
        assert scoring.classify_source("Bloomberg news article") == ("News Source", 60)
        assert scoring.classify_source("a reddit thread") == ("Social Media", 20)
        assert scoring.classify_source("some random blog") == ("Unknown", 50)
        assert scoring.classify_source("") == ("Unknown", 50)

    def test_recency_buckets(self):                             # C2
        assert scoring.recency_score("2026-06-01", AS_OF) == 100   # 3d
        assert scoring.recency_score("2026-05-15", AS_OF) == 80    # 20d
        assert scoring.recency_score("2026-04-05", AS_OF) == 60    # 60d
        assert scoring.recency_score("2025-11-16", AS_OF) == 30    # ~200d
        assert scoring.recency_score("", AS_OF) == 30              # undated
        assert scoring.recency_score("2026-07-01", AS_OF) == 30    # future

    def test_relevance_and_composite(self):                     # C3
        assert scoring.relevance_score("direct") == 100
        assert scoring.relevance_score("related") == 70
        assert scoring.relevance_score("indirect") == 40
        assert scoring.relevance_score("garbage") == 70           # default related
        # Worked composite: SEC(100) + related(70) + 60d(60)
        # = .5*100 + .3*70 + .2*60 = 50 + 21 + 12 = 83
        item = EvidenceItem(source="SEC 10-Q", date="2026-04-05", relevance="related")
        scoring.score_item(item, AS_OF)
        assert item.reliability_score == 100
        assert item.relevance_score == 70
        assert item.recency_score == 60
        assert item.item_score == 83


# ===========================================================================
# B. Data contract & extraction (PRD §5)
# ===========================================================================

class TestExtraction:
    def test_extract_full_yaml(self):                           # B1
        sp = extract.extract_specialist(rich_parsed(), "equity", "Equity Research")
        assert sp.role_key == "equity"
        assert len(sp.evidence) == 3
        assert sp.evidence[0].source == "SEC 10-Q filing"
        assert sp.evidence[0].impact == "positive"
        assert len(sp.assumptions) == 2
        assert {a.name for a in sp.assumptions} == {"revenue_growth", "discount_rate"}
        assert len(sp.reasoning) == 3
        assert sp.reasoning[0].step_no == 1
        assert len(sp.counterarguments) == 3
        assert len(sp.sources) == 3

    def test_extract_bare_yaml(self):                           # B2
        sp = extract.extract_specialist({"vote": "HOLD", "confidence": 50}, "x", "X")
        assert sp.vote == "HOLD"
        assert sp.evidence == [] and sp.assumptions == []
        assert sp.counterarguments == [] and sp.sources == []
        assert sp.evidence_count == 0

    def test_extract_malformed_shapes(self):                    # B3
        parsed = {
            "vote": "BUY",
            "evidence": ["bare string evidence", "another finding"],   # list of str
            "assumptions": ["growth high", {"rate": "9%"}],            # mixed list
            "reasoning": {"steps": ["a", "b"]},                        # dict
            "counterarguments": "single counter",                     # scalar
            "sources": [{"reference": "SEC"}],                         # list of dict
        }
        sp = extract.extract_specialist(parsed, "x", "X")              # must not raise
        assert len(sp.evidence) == 2
        assert sp.evidence[0].finding == "bare string evidence"
        assert len(sp.reasoning) == 2
        assert len(sp.counterarguments) == 1
        assert sp.sources[0].reference == "SEC"

    def test_extract_raw_failed_yaml(self):                     # B3
        sp = extract.extract_specialist({"_raw": "model emitted prose"}, "x", "X")
        assert sp.vote == "HOLD"
        assert sp.reason == "model emitted prose"
        assert sp.evidence == []

    def test_extract_from_opinion(self):
        sp = extract.extract_from_opinion(rich_opinion("risk", "Risk Management"))
        assert sp.role_key == "risk" and len(sp.evidence) == 3


# ===========================================================================
# D. Validation & metrics (PRD §13)
# ===========================================================================

class TestValidationMetrics:
    def test_evidence_gate_boundary(self):                      # D1
        two = extract.extract_specialist(rich_parsed(n_evidence=2), "x", "X")
        three = extract.extract_specialist(rich_parsed(n_evidence=3), "x", "X")
        assert two.meets_evidence_gate is False
        assert three.meets_evidence_gate is True

    def test_counter_gate_boundary(self):                       # D1
        two = extract.extract_specialist(rich_parsed(n_counter=2), "x", "X")
        three = extract.extract_specialist(rich_parsed(n_counter=3), "x", "X")
        assert two.meets_counter_gate is False
        assert three.meets_counter_gate is True

    def test_full_report_scores_high(self):                     # D2
        rep = full_report(debate=True)
        assert rep.metrics["tirf_score"] >= 80

    def test_empty_report_scores_low(self):                     # D2
        rep = builder.build_research_report(
            ticker="MU", bundle={"as_of": AS_OF}, opinions=[bare_opinion()], cio={})
        assert rep.metrics["tirf_score"] < 40                   # no raise, low score

    def test_all_five_metrics_in_range(self):                   # D3
        rep = full_report()
        for k in ("explainability", "traceability", "auditability",
                  "reproducibility", "challenge_coverage"):
            assert 0 <= rep.metrics[k] <= 100
        assert 0 <= rep.metrics["tirf_score"] <= 100

    def test_reproducibility_score_100(self):                   # E3
        rep = full_report()
        assert rep.metrics["reproducibility"] == 100

    def test_gate_report_fractions(self):
        rep = full_report()
        g = validate.gate_report(rep)
        assert g["frac_evidence_gate"] == 1.0
        assert g["frac_counter_gate"] == 1.0
        assert len(g["per_specialist"]) == 4


# ===========================================================================
# E. Versioning & reproducibility (PRD §8/§9)
# ===========================================================================

class TestReproVersioning:
    def test_data_hash_stable_and_sensitive(self):             # E2
        h1 = repro.data_hash(repro.data_snapshot(FAKE_BUNDLE))
        h2 = repro.data_hash(repro.data_snapshot(dict(FAKE_BUNDLE)))
        assert h1 == h2 and len(h1) == 64
        changed = {**FAKE_BUNDLE, "quote": {**FAKE_BUNDLE["quote"], "close": 999.0}}
        assert repro.data_hash(repro.data_snapshot(changed)) != h1

    def test_manifest_pins_and_verify(self):                   # E3
        m = repro.manifest(FAKE_BUNDLE, research_version=2)
        for pin in ("data_snapshot", "data_hash", "prompt_version", "agent_version"):
            assert m[pin]
        assert repro.verify(FAKE_BUNDLE, m["data_hash"]) is True
        changed = {**FAKE_BUNDLE, "quote": {"close": 1.0}}
        assert repro.verify(changed, m["data_hash"]) is False

    def test_version_autoincrement(self, tmp_path):            # E1
        r1 = full_report(); r2 = full_report()
        store.persist(r1)
        store.persist(r2)
        assert r1.version == 1
        assert r2.version == 2
        assert store.latest_version("MU") == 2


# ===========================================================================
# F. Persistence & retrieval (PRD §10/§14)
# ===========================================================================

class TestStore:
    def test_persist_roundtrip(self):                          # F1
        rep = full_report()
        rid = store.persist(rep)
        assert rid and rep.report_id == rid
        row = store.get_report(rid)
        assert row["ticker"] == "MU"
        assert row["version"] == 1
        assert row["final_recommendation"] == "Buy"
        assert row["tirf_score"] == rep.metrics["tirf_score"]
        assert row["data_hash"] == rep.data_hash

    def test_persist_children(self):                           # F2
        rep = full_report()
        rid = store.persist(rep)
        ev = store.get_evidence(rid)
        asm = store.get_assumptions(rid)
        src = store.get_sources(rid)
        ctr = store.get_counterarguments(rid)
        assert len(ev) == 4 * 3            # 4 specialists × 3 evidence
        assert len(asm) == 4 * 2
        assert len(ctr) == 4 * 3
        assert len(src) == 4 * 3
        assert ev[0]["item_score"] > 0     # scored before persist

    def test_get_latest(self):
        store.persist(full_report())
        store.persist(full_report())
        row = store.get_latest("MU")
        assert row["version"] == 2

    def test_store_never_raises_bad_db(self, monkeypatch):     # F3
        import pathlib
        monkeypatch.setattr(store, "DB_PATH", pathlib.Path("/proc/cannot/write/here.db"))
        assert store.persist(full_report()) == ""              # graceful "", no raise
        assert store.get_report("nope") is None
        assert store.list_reports() == []


# ===========================================================================
# G. Challenge protocol (PRD §12)
# ===========================================================================

class TestChallengeProtocol:
    def test_challenges_persisted(self):                       # G1
        rep = full_report(debate=True)
        assert len(rep.challenges) == 1
        rid = store.persist(rep)
        chs = store.get_challenges(rid)
        assert len(chs) == 1
        assert chs[0]["challenger_key"] == "risk"
        # session row + response row
        conn = sqlite3.connect(store.DB_PATH)
        conn.row_factory = sqlite3.Row
        sess = conn.execute("SELECT * FROM committee_sessions WHERE report_id=?", (rid,)).fetchone()
        resp = conn.execute("SELECT * FROM committee_responses WHERE report_id=?", (rid,)).fetchall()
        conn.close()
        assert sess["n_challenges"] == 1 and sess["debate_on"] == 1
        assert sess["n_specialists"] == 4
        assert len(resp) == 1 and resp[0]["response"] == "Tail risk is priced in"


# ===========================================================================
# H. CIO review (PRD §12)
# ===========================================================================

class TestCioReview:
    def test_cio_review_scorecard(self):                       # H1
        rep = full_report()
        sc = review.cio_review(rep)
        for k in ("evidence_quality", "assumption_quality", "counterargument_coverage",
                  "source_reliability", "reasoning_consistency"):
            assert 0 <= sc["scores"][k] <= 100
        assert sc["verdict"] in ("pass", "review")
        assert 0 <= sc["overall_score"] <= 100

    def test_weak_report_flags(self):
        rep = builder.build_research_report(
            ticker="MU", bundle={"as_of": AS_OF}, opinions=[bare_opinion()], cio={})
        sc = review.cio_review(rep)
        assert sc["verdict"] == "review"
        assert len(sc["flags"]) > 0


# ===========================================================================
# I. Dossier (PRD §11)
# ===========================================================================

class TestDossier:
    def test_dossier_has_11_sections(self):                    # I1
        md = dossier.render_dossier(full_report())
        for title in dossier.REQUIRED_SECTIONS:
            assert f"## {title}" in md
        assert len(dossier.REQUIRED_SECTIONS) == 11

    def test_dossier_empty_safe(self):                         # I2
        md = dossier.render_dossier(ResearchReport(ticker="ZZZ"))
        assert "Research Dossier: ZZZ" in md
        assert "_Insufficient data._" in md

    def test_tirf_appendix(self):
        ap = dossier.tirf_appendix(full_report())
        assert "TIRF Transparency Appendix" in ap
        assert "Evidence Ledger" in ap
        assert "CIO Review Scorecard" in ap


# ===========================================================================
# A + J. Zero-cost invariant & integration (PRD §16 A1, J1, J2)
# ===========================================================================

# Canned yaml for the integration run.
_RICH_SPECIALIST_YAML = textwrap.dedent("""\
    ```yaml
    vote: BUY
    confidence: 70
    reason: Fundamentals and setup support a constructive view
    evidence:
      - source: "SEC 10-Q filing"
        date: "2026-06-01"
        finding: "Revenue up 18% YoY"
        impact: positive
        relevance: direct
        confidence: high
      - source: "Q1 earnings call transcript"
        date: "2026-05-20"
        finding: "Guidance raised"
        impact: positive
        relevance: direct
        confidence: medium
      - source: "SemiAnalysis industry report"
        date: "2026-05-10"
        finding: "Sector demand accelerating"
        impact: positive
        relevance: related
        confidence: medium
    assumptions:
      revenue_growth: "15%"
      discount_rate: "9%"
    reasoning:
      - "Evidence shows accelerating revenue"
      - "Higher revenue lifts fair value"
      - "Price sits below fair value"
    counterarguments:
      - "Demand could normalize"
      - "Margins may compress"
      - "Rates could spike"
    sources:
      - "SEC 10-Q"
      - "Earnings call"
      - "Industry report"
    memory_note: Durable franchise riding a cyclical sector tailwind
    ```""")

_MODERATOR_YAML = textwrap.dedent("""\
    ```yaml
    committee_recommendation: Buy
    agreement_score: 70
    majority_view: Constructive
    minority_view: Risk dissents
    key_disagreements: Valuation vs risk
    ```""")

_CIO_YAML = textwrap.dedent("""\
    ```yaml
    final_recommendation: Buy
    confidence_score: 71
    risk_rating: Moderate
    time_horizon: 12 months
    macro_alignment_score: 65
    geopolitical_risk_score: 40
    external_risk_adjustment: Qualitative assessment — minor trim
    base_case: Qualitative assessment — steady appreciation
    bull_case: Qualitative assessment — upside on cycle
    bear_case: Qualitative assessment — downside on glut
    scenarios:
      - scenario: Base
        probability: 55%
        price_target: Qualitative assessment — modest upside
        key_drivers: Qualitative assessment — demand
    memory_note: Cyclical compounder; revisit on margin inflection
    ```""")


async def _integration_ask_role(system_prompt, user_prompt, role_key=None,
                                service=None, model=None):
    sp = system_prompt.lower()
    if "free text" in user_prompt.lower() or "no yaml" in user_prompt.lower():
        return "Qualitative debate response."
    if "chief investment officer" in sp:
        return _CIO_YAML
    if "moderator" in sp:
        return _MODERATOR_YAML
    return _RICH_SPECIALIST_YAML


class TestIntegration:
    def test_tirf_adds_zero_llm_calls(self, monkeypatch):      # A1
        from cio.committee import roles
        calls = {"n": 0}

        async def _counting(system_prompt, user_prompt, role_key=None, service=None, model=None):
            calls["n"] += 1
            return await _integration_ask_role(system_prompt, user_prompt, role_key)

        monkeypatch.setattr("cio.committee.engine.ask_role", _counting)
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda s: FAKE_BUNDLE)

        from cio.committee.engine import run_committee
        result = _run(run_committee("MU", debate=False))

        # Exactly specialists(non-ETF) + moderator + CIO. TIRF (pure Python) adds 0.
        expected = len([r for r in roles.SPECIALISTS if r["key"] != "etf"]) + 2
        assert calls["n"] == expected
        assert result.tirf is not None

    def test_run_committee_attaches_tirf(self, monkeypatch):   # J1
        monkeypatch.setattr("cio.committee.engine.ask_role", _integration_ask_role)
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda s: FAKE_BUNDLE)

        from cio.committee.engine import run_committee
        result = _run(run_committee("MU", debate=False))

        assert result.tirf is not None
        assert result.tirf.metrics["tirf_score"] > 0
        assert result.tirf.report_id                      # persisted (id assigned)
        # round-trips out of the store
        row = store.get_latest("MU")
        assert row is not None and row["report_id"] == result.tirf.report_id

    def test_report_contains_tirf_appendix(self, monkeypatch):  # J2
        monkeypatch.setattr("cio.committee.engine.ask_role", _integration_ask_role)
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda s: FAKE_BUNDLE)

        from cio.committee.engine import run_committee
        from cio.committee.report import build_report
        result = _run(run_committee("MU", debate=False))
        md = build_report("MU", result)
        assert "TIRF Transparency Appendix" in md
        assert "Evidence Ledger" in md
        assert "Investment Committee Report: MU" in md


# ===========================================================================
# K. Docs present (PRD §16 K1)
# ===========================================================================

def test_docs_present():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    assert (root / "docs" / "TIRF-PRD.md").exists()
    assert (root / "docs" / "TIRF-TEST-PLAN.md").exists()
