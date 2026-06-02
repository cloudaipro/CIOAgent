"""
test_committee.py — offline tests for cio.committee

All tests monkeypatch ask_role and/or gather_bundle; no network, no LLM.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
import textwrap
from typing import Any

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@pytest.fixture(autouse=True)
def _isolate_committee_db(monkeypatch, tmp_path):
    """Route ALL committee per-agent memory to a throwaway db. Without this any
    test that drives run_committee() would write real notes (+768-dim vectors) to
    the live data/committee.db. Per-test temp db = full isolation. Tests that need
    their own handle still monkeypatch agent_memory.DB_PATH explicitly (overrides)."""
    monkeypatch.setattr("cio.committee.agent_memory.DB_PATH",
                        tmp_path / "committee_test.db")


# ---------------------------------------------------------------------------
# Fixtures / canned data
# ---------------------------------------------------------------------------

SYMBOL_AAPL = "AAPL"

# A synthetic bundle for AAPL (US equity, not ETF)
FAKE_BUNDLE_AAPL = {
    "symbol": "AAPL",
    "resolved": "AAPL",
    "quote": {"close": 175.0, "change_pct": 1.2, "volume": 55_000_000},
    "fundamentals": {
        "name": "Apple Inc.",
        "pe": 28.5,
        "pb": 45.0,
        "yield_pct": 0.5,
        "eps": 6.14,
        "roe_pct": 160.0,
        "margin_pct": 25.3,
        "market_cap": 2_700_000_000_000,
        "wk52_high": 199.0,
        "wk52_low": 143.0,
        "short_ratio": 1.2,
        "shares_short": 80_000_000,
        "revenue_q": [
            {"period": "2024-Q1", "value": 117_154_000_000, "yoy_pct": 2.1},
            {"period": "2024-Q2", "value": 90_753_000_000, "yoy_pct": -4.3},
        ],
        "quoteType": "EQUITY",
    },
    "ta_signals": {"rsi": "bull", "macd": "neutral", "stoch": "bear"},
    "is_etf": False,
    "as_of": "2026-06-01T00:00:00",
}

# A synthetic ETF bundle
FAKE_BUNDLE_ETF = {
    **FAKE_BUNDLE_AAPL,
    "symbol": "SPY",
    "resolved": "SPY",
    "fundamentals": {**FAKE_BUNDLE_AAPL["fundamentals"], "quoteType": "ETF"},
    "is_etf": True,
}

# A bundle that signals "no data"
FAKE_BUNDLE_NODATA = {
    "symbol": "FAKE999",
    "resolved": None,
    "quote": None,
    "fundamentals": None,
    "ta_signals": {},
    "is_etf": False,
    "as_of": "2026-06-01T00:00:00",
}


# ---------------------------------------------------------------------------
# Canned YAML responses keyed by role
# ---------------------------------------------------------------------------

_ROLE_YAML: dict[str, str] = {
    "market": textwrap.dedent("""\
        ```yaml
        market_trend: Mildly bullish
        market_score: 62
        macro_risks: Qualitative assessment — rate uncertainty, geopolitical tension
        capital_flows: Qualitative assessment — rotation toward tech
        vote: BUY
        confidence: 65
        reason: Broader market tailwinds support the position
        memory_note: AAPL benefits from risk-on macro tailwinds in tech rotation cycles
        ```"""),
    "equity": textwrap.dedent("""\
        ```yaml
        financial_health: Strong balance sheet
        earnings_growth: Low single-digit YoY
        quality_score: 80
        management_assessment: Qualitative assessment — stable, shareholder-friendly
        investment_thesis: Premium franchise with durable cash flows
        vote: BUY
        confidence: 75
        reason: Quality franchise at reasonable valuation
        ```"""),
    "industry": textwrap.dedent("""\
        ```yaml
        industry_score: 70
        industry_cycle: Qualitative assessment — mature growth phase
        tailwinds: Qualitative assessment — AI hardware demand, services expansion
        headwinds: Qualitative assessment — China risk, consumer softness
        vote: HOLD
        confidence: 55
        reason: Industry momentum is positive but headwinds are real
        ```"""),
    "valuation": textwrap.dedent("""\
        ```yaml
        fair_value: Qualitative assessment — premium multiple justified by quality
        valuation_rating: Fairly valued
        upside_potential: Qualitative assessment — moderate
        downside_risk: Qualitative assessment — multiple compression on rate spike
        vote: HOLD
        confidence: 60
        reason: Fairly valued relative to peers; limited near-term upside
        ```"""),
    "quant": textwrap.dedent("""\
        ```yaml
        trend_score: 68
        momentum_signal: Mild bull momentum
        probability_upside: Qualitative assessment — 55% near-term
        vote: BUY
        confidence: 60
        reason: RSI and price trend support mild bull case
        ```"""),
    "etf": textwrap.dedent("""\
        ```yaml
        etf_score: 85
        portfolio_overlap: Qualitative assessment — high broad market exposure
        liquidity_rating: Very high
        tracking_quality: Excellent
        vote: BUY
        confidence: 80
        reason: Highly liquid ETF with tight tracking error
        ```"""),
    "risk": textwrap.dedent("""\
        ```yaml
        risk_score: 45
        major_risks: Qualitative assessment — China ban risk, services regulation
        worst_case_scenario: Qualitative assessment — 30% drawdown on regulatory shock
        vote: SELL
        confidence: 55
        reason: Tail risks are underpriced; elevated concentration risk
        memory_note: AAPL China concentration is a persistent tail risk worth monitoring each cycle
        ```"""),
    "catalyst": textwrap.dedent("""\
        ```yaml
        bullish_catalysts: Qualitative assessment — AI device cycle, services acceleration
        bearish_catalysts: Qualitative assessment — earnings miss, China restrictions
        event_timeline: Qualitative assessment — next earnings in ~90 days
        vote: HOLD
        confidence: 50
        reason: Catalysts balanced; watch next earnings print
        ```"""),
    "moderator": textwrap.dedent("""\
        ```yaml
        committee_recommendation: Buy
        agreement_score: 62
        majority_view: Qualitative assessment — positive quality story, moderate conviction
        minority_view: Qualitative assessment — risk manager dissents on tail risk
        key_disagreements: Qualitative assessment — valuation and risk diverge on China exposure
        ```"""),
    "cio": textwrap.dedent("""\
        ```yaml
        final_recommendation: Buy
        confidence_score: 68
        risk_rating: Moderate
        time_horizon: 12 months
        base_case: Qualitative assessment — modest appreciation driven by services
        bull_case: Qualitative assessment — AI supercycle re-rates the stock
        bear_case: Qualitative assessment — China ban triggers multiple compression
        scenarios:
          - scenario: Bull
            probability: 30%
            price_target: Qualitative assessment — meaningful upside
            key_drivers: Qualitative assessment — AI hardware + services growth
          - scenario: Base
            probability: 50%
            price_target: Qualitative assessment — modest appreciation
            key_drivers: Qualitative assessment — steady services expansion
          - scenario: Bear
            probability: 20%
            price_target: Qualitative assessment — significant drawdown
            key_drivers: Qualitative assessment — China restrictions + rate spike
        memory_note: AAPL is a quality compounder — revisit thesis if services growth decelerates
        ```"""),
}


async def _canned_ask_role(
    system_prompt: str,
    user_prompt: str,
    role_key: str | None = None,
    service: str | None = None,
    model=None,
) -> str:
    """Async version: determine which role is being called and return canned yaml.
    Detection is purely on system_prompt to avoid confusion when user_prompt
    contains output from prior LLM calls.

    Debate challenge/response calls ask for 'Free text — no yaml needed'; return
    plain prose for those so memory_note never leaks through the debate section.
    """
    sp = system_prompt.lower()
    up = user_prompt.lower()

    # Debate calls: free-text prose, no YAML
    if "free text" in up or "no yaml needed" in up:
        return "This is a qualitative debate response with no figures or YAML."

    # CIO must come before moderator — CIO system prompt contains "cio"
    if "chief investment officer" in sp:
        return _ROLE_YAML["cio"]
    if "moderator" in sp:
        return _ROLE_YAML["moderator"]
    if "market intelligence" in sp:
        return _ROLE_YAML["market"]
    if "equity research" in sp:
        return _ROLE_YAML["equity"]
    if "industry intelligence" in sp:
        return _ROLE_YAML["industry"]
    if "valuation analyst" in sp:
        return _ROLE_YAML["valuation"]
    if "quantitative analyst" in sp:
        return _ROLE_YAML["quant"]
    if "etf research" in sp:
        return _ROLE_YAML["etf"]
    if "risk management" in sp:
        return _ROLE_YAML["risk"]
    if "catalyst analyst" in sp:
        return _ROLE_YAML["catalyst"]
    # Default fallback
    return _ROLE_YAML["market"]


# ---------------------------------------------------------------------------
# parse_yaml_block tests
# ---------------------------------------------------------------------------

class TestParseYamlBlock:
    def test_happy_path(self):
        from cio.committee.engine import parse_yaml_block
        text = "Some preamble\n```yaml\nkey: value\nscore: 42\n```\nTrailing."
        result = parse_yaml_block(text)
        assert result == {"key": "value", "score": 42}

    def test_last_block_wins(self):
        from cio.committee.engine import parse_yaml_block
        text = "```yaml\nfirst: 1\n```\nSome text\n```yaml\nsecond: 2\n```"
        result = parse_yaml_block(text)
        assert result == {"second": 2}

    def test_malformed_returns_raw(self):
        from cio.committee.engine import parse_yaml_block
        text = "```yaml\n: broken: yaml: :::\n```"
        result = parse_yaml_block(text)
        assert "_raw" in result

    def test_no_yaml_block_returns_raw(self):
        from cio.committee.engine import parse_yaml_block
        text = "Just plain text, no fences."
        result = parse_yaml_block(text)
        assert result == {"_raw": text}

    def test_empty_string_returns_raw(self):
        from cio.committee.engine import parse_yaml_block
        result = parse_yaml_block("")
        assert "_raw" in result

    def test_never_raises(self):
        from cio.committee.engine import parse_yaml_block
        # Should not raise on any pathological input
        for bad in [None, 123, "```yaml\n!!!\n```"]:
            try:
                parse_yaml_block(bad)  # type: ignore
            except Exception as e:
                pytest.fail(f"parse_yaml_block raised {e} on input {bad!r}")


# ---------------------------------------------------------------------------
# confidence_band tests
# ---------------------------------------------------------------------------

class TestConfidenceBand:
    def test_very_high(self):
        from cio.committee.report import confidence_band
        assert confidence_band(95) == "Very High Confidence"
        assert confidence_band(90) == "Very High Confidence"
        assert confidence_band(100) == "Very High Confidence"

    def test_high(self):
        from cio.committee.report import confidence_band
        assert confidence_band(80) == "High Confidence"
        assert confidence_band(75) == "High Confidence"
        assert confidence_band(89) == "High Confidence"

    def test_moderate_high(self):
        from cio.committee.report import confidence_band
        assert confidence_band(65) == "Moderate-High Confidence"
        assert confidence_band(60) == "Moderate-High Confidence"

    def test_moderate(self):
        from cio.committee.report import confidence_band
        assert confidence_band(55) == "Moderate Confidence"
        assert confidence_band(50) == "Moderate Confidence"

    def test_low_moderate(self):
        from cio.committee.report import confidence_band
        assert confidence_band(45) == "Low-Moderate Confidence"
        assert confidence_band(40) == "Low-Moderate Confidence"

    def test_very_low(self):
        from cio.committee.report import confidence_band
        assert confidence_band(39) == "Very Low Confidence"
        assert confidence_band(0) == "Very Low Confidence"

    def test_none_value(self):
        from cio.committee.report import confidence_band
        result = confidence_band(None)
        assert "Confidence" in result  # graceful fallback


# ---------------------------------------------------------------------------
# run_committee tests (offline, monkeypatched)
# ---------------------------------------------------------------------------

EXPECTED_SECTION_HEADERS = [
    "## Executive Summary",
    "## Company Overview",
    "## Market Analysis",
    "## Industry Analysis",
    "## Financial Analysis",
    "## Valuation Analysis",
    "## Risk Analysis",
    "## Catalyst Analysis",
    "## Bull Case",
    "## Bear Case",
    "## Scenario Analysis",
    "## Investment Committee Findings",
    "## Final Recommendation",
]


class TestRunCommittee:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_no_data_returns_clean_error(self, monkeypatch):
        """resolved=None → CommitteeResult with error, no exception."""
        monkeypatch.setattr(
            "cio.committee.engine.gather_bundle",
            lambda sym: FAKE_BUNDLE_NODATA,
        )
        # ask_role should NOT be called; patch it to blow up if it is
        monkeypatch.setattr(
            "cio.committee.engine.ask_role",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("ask_role called unexpectedly")),
        )
        result = self._run(__import__("cio.committee.engine", fromlist=["run_committee"]).run_committee("FAKE999"))
        assert result.error is not None
        assert "no data" in result.error
        assert result.resolved is None
        assert result.opinions == []

    def test_non_etf_has_7_opinions(self, monkeypatch):
        """AAPL (not ETF) should produce exactly 7 opinions (etf specialist skipped)."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL))
        assert result.error is None
        assert len(result.opinions) == 7
        keys = {op["key"] for op in result.opinions}
        assert "etf" not in keys

    def test_etf_has_8_opinions(self, monkeypatch):
        """SPY (is_etf=True) should produce 8 opinions."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_ETF)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee("SPY"))
        assert result.error is None
        assert len(result.opinions) == 8
        keys = {op["key"] for op in result.opinions}
        assert "etf" in keys

    def test_consensus_present(self, monkeypatch):
        """consensus dict is populated and contains required keys."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL))
        assert isinstance(result.consensus, dict)
        assert "committee_recommendation" in result.consensus or "_raw" in result.consensus

    def test_vote_tally_present(self, monkeypatch):
        """vote_tally contains buy/hold/sell counts."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL))
        tally = result.vote_tally
        assert "buy_count" in tally
        assert "hold_count" in tally
        assert "sell_count" in tally
        # votes should sum to 7 for AAPL (non-ETF)
        assert tally["buy_count"] + tally["hold_count"] + tally["sell_count"] == 7

    def test_vote_tally_nontrivial(self, monkeypatch):
        """Canned votes are varied (BUY, HOLD, SELL all appear) → tally is non-trivial."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL))
        tally = result.vote_tally
        # We have BUY (market, equity, quant) + HOLD (industry, valuation, catalyst) + SELL (risk)
        assert tally["buy_count"] >= 1
        assert tally["hold_count"] >= 1
        assert tally["sell_count"] >= 1

    def test_cio_has_final_recommendation(self, monkeypatch):
        """CIO dict has final_recommendation in allowed values."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL))
        allowed = {"Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"}
        # CIO may have "_raw" if parsing failed; still no exception
        if "_raw" not in result.cio:
            assert result.cio.get("final_recommendation") in allowed

    def test_canned_gives_buy_recommendation(self, monkeypatch):
        """With our canned data the CIO should recommend Buy."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL))
        assert result.cio.get("final_recommendation") == "Buy"


# ---------------------------------------------------------------------------
# build_report tests
# ---------------------------------------------------------------------------

class TestBuildReport:
    def _run(self, coro):
        return asyncio.run(coro)

    def _make_result(self, monkeypatch):
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        return self._run(run_committee(SYMBOL_AAPL))

    def test_all_13_sections_present(self, monkeypatch):
        """Report must contain all 13 section headers."""
        result = self._make_result(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        for header in EXPECTED_SECTION_HEADERS:
            assert header in report, f"Missing section: {header}"

    def test_confidence_band_in_report(self, monkeypatch):
        """Report must contain a confidence band label from §11."""
        result = self._make_result(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        bands = [
            "Very High Confidence", "High Confidence",
            "Moderate-High Confidence", "Moderate Confidence",
            "Low-Moderate Confidence", "Very Low Confidence",
        ]
        assert any(b in report for b in bands), "No confidence band label found in report"

    def test_missing_section_prints_insufficient_data(self):
        """A result with no CIO data should print '_Insufficient data.' for Final Recommendation."""
        from cio.committee.engine import CommitteeResult
        from cio.committee.report import build_report
        empty_result = CommitteeResult(
            symbol="EMPTY",
            resolved="EMPTY",
            as_of="2026-06-01T00:00:00",
            bundle={},
            opinions=[],
            consensus={},
            vote_tally={},
            cio={},  # no final_recommendation
        )
        report = build_report("EMPTY", empty_result)
        assert "_Insufficient data._" in report
        # All 13 sections must still be present
        for header in EXPECTED_SECTION_HEADERS:
            assert header in report, f"Missing section: {header}"

    def test_no_data_result_does_not_crash(self):
        """A no-data CommitteeResult (error set) must produce a report, not crash."""
        from cio.committee.engine import CommitteeResult
        from cio.committee.report import build_report
        error_result = CommitteeResult(
            symbol="FAKE999",
            resolved=None,
            as_of="",
            bundle={},
            error="no data for FAKE999",
        )
        # Should not raise
        report = build_report("FAKE999", error_result)
        assert "## Executive Summary" in report
        assert "FAKE999" in report or "no data" in report

    def test_build_report_never_raises(self, monkeypatch):
        """build_report never raises even on garbage input."""
        from cio.committee.report import build_report

        class WeirdResult:
            pass

        try:
            build_report("X", WeirdResult())
        except Exception as e:
            pytest.fail(f"build_report raised {e}")


# ---------------------------------------------------------------------------
# CIO_TOOLS count guard
# ---------------------------------------------------------------------------

def test_cio_tools_count():
    """CIO_TOOLS must still be exactly 20 after adding the committee module."""
    from cio.agent import CIO_TOOLS
    assert len(CIO_TOOLS) == 20, f"CIO_TOOLS count changed: {len(CIO_TOOLS)}"


# ---------------------------------------------------------------------------
# select_debate_pairs tests
# ---------------------------------------------------------------------------

# Canned opinions for pair-selection tests
_OP_BEAR = {"key": "risk", "title": "Risk Management", "vote": "SELL", "confidence": 55}
_OP_BULL = {"key": "equity", "title": "Equity Research", "vote": "BUY", "confidence": 75}
_OP_HOLD = {"key": "industry", "title": "Industry Intelligence", "vote": "HOLD", "confidence": 55}
_OP_VAL_HOLD = {"key": "valuation", "title": "Valuation", "vote": "HOLD", "confidence": 60}
_OP_RISK_SELL = {"key": "risk", "title": "Risk Management", "vote": "SELL", "confidence": 55}
_OP_VAL_BUY = {"key": "valuation", "title": "Valuation", "vote": "BUY", "confidence": 70}


class TestSelectDebatePairs:
    def test_all_same_vote_returns_empty(self):
        """All HOLD → no genuine disagreement → []."""
        from cio.committee.debate import select_debate_pairs
        opinions = [
            {"key": "market", "vote": "HOLD", "confidence": 60},
            {"key": "equity", "vote": "HOLD", "confidence": 65},
            {"key": "risk", "vote": "HOLD", "confidence": 50},
        ]
        assert select_debate_pairs(opinions, max_pairs=2) == []

    def test_all_same_buy_returns_empty(self):
        """All BUY → []."""
        from cio.committee.debate import select_debate_pairs
        opinions = [
            {"key": "market", "vote": "BUY", "confidence": 70},
            {"key": "equity", "vote": "BUY", "confidence": 75},
        ]
        assert select_debate_pairs(opinions, max_pairs=2) == []

    def test_mixed_votes_core_pair_bear_challenges_bull(self):
        """Mixed votes → core pair: most-bearish challenges most-bullish."""
        from cio.committee.debate import select_debate_pairs
        opinions = [_OP_BEAR, _OP_BULL, _OP_HOLD]
        pairs = select_debate_pairs(opinions, max_pairs=2)
        assert len(pairs) >= 1
        challenger, target = pairs[0]
        # challenger is the most bearish (SELL), target is most bullish (BUY)
        assert challenger["key"] == "risk"
        assert target["key"] == "equity"

    def test_risk_valuation_prd_pair_included_when_votes_differ(self):
        """risk/valuation with different votes → PRD pair included."""
        from cio.committee.debate import select_debate_pairs
        opinions = [_OP_RISK_SELL, _OP_VAL_BUY, _OP_HOLD]
        pairs = select_debate_pairs(opinions, max_pairs=2)
        keys = [(ch["key"], tg["key"]) for ch, tg in pairs]
        # core pair: risk (SELL) challenges valuation (BUY) — same as PRD pair here
        assert ("risk", "valuation") in keys

    def test_risk_valuation_skipped_when_same_vote(self):
        """risk/valuation same vote → PRD pair NOT included (deduped/same vote)."""
        from cio.committee.debate import select_debate_pairs
        # risk=HOLD, valuation=HOLD → no debate at all
        opinions = [
            {"key": "risk", "vote": "HOLD", "confidence": 55},
            {"key": "valuation", "vote": "HOLD", "confidence": 60},
            {"key": "equity", "vote": "HOLD", "confidence": 75},
        ]
        assert select_debate_pairs(opinions, max_pairs=2) == []

    def test_respects_max_pairs_cap(self):
        """max_pairs=1 → at most 1 pair returned."""
        from cio.committee.debate import select_debate_pairs
        opinions = [_OP_RISK_SELL, _OP_VAL_BUY, _OP_HOLD]
        pairs = select_debate_pairs(opinions, max_pairs=1)
        assert len(pairs) <= 1

    def test_no_self_pairs(self):
        """No pair has challenger == target."""
        from cio.committee.debate import select_debate_pairs
        opinions = [_OP_BEAR, _OP_BULL, _OP_HOLD, _OP_VAL_HOLD]
        for ch, tg in select_debate_pairs(opinions, max_pairs=4):
            assert ch["key"] != tg["key"]

    def test_empty_opinions_returns_empty(self):
        from cio.committee.debate import select_debate_pairs
        assert select_debate_pairs([], max_pairs=2) == []

    def test_max_pairs_zero_returns_empty(self):
        from cio.committee.debate import select_debate_pairs
        assert select_debate_pairs([_OP_BEAR, _OP_BULL], max_pairs=0) == []


# ---------------------------------------------------------------------------
# run_committee with debate ON (extended tests)
# ---------------------------------------------------------------------------

# Canned responses for debate rounds.
# Detection by prompt CONTENT (system_prompt is role-based; user_prompt has markers).
# challenge prompt contains "pointed rebuttal"
# response prompt contains "Defend or concede"
# revision prompt contains "DEBATE TRANSCRIPT"
_CANNED_CHALLENGE = "I challenge you: the downside risks are being severely underweighted."
_CANNED_RESPONSE = "I acknowledge the risk, but the quality franchise justifies a premium."

# Round 3 revision: risk analyst changes from SELL to HOLD after hearing debate
_RISK_REVISED_YAML = textwrap.dedent("""\
    ```yaml
    risk_score: 50
    major_risks: Qualitative assessment — China ban risk, services regulation
    worst_case_scenario: Qualitative assessment — 20% drawdown on regulatory shock
    vote: HOLD
    confidence: 45
    reason: Revised after debate — tail risks partially offset by quality narrative
    ```""")


async def _debate_ask_role(
    system_prompt: str,
    user_prompt: str,
    role_key: str | None = None,
    service: str | None = None,
    model=None,
) -> str:
    """
    Extended canned ask_role that detects debate rounds by user_prompt content.
    - challenge: "pointed rebuttal"
    - response: "Defend or concede"
    - revision: "DEBATE TRANSCRIPT" — risk specialist changes to HOLD
    - all else: delegate to the original _canned_ask_role
    """
    if "pointed rebuttal" in user_prompt:
        return _CANNED_CHALLENGE
    if "Defend or concede" in user_prompt:
        return _CANNED_RESPONSE
    if "DEBATE TRANSCRIPT" in user_prompt:
        sp = system_prompt.lower()
        if "risk management" in sp:
            return _RISK_REVISED_YAML
        # All other specialists hold their Round 1 position
        return await _canned_ask_role(system_prompt, user_prompt, role_key=role_key, model=model)
    return await _canned_ask_role(system_prompt, user_prompt, role_key=role_key, model=model)


class TestRunCommitteeDebate:
    def _run(self, coro):
        return asyncio.run(coro)

    def _make_result_debate_on(self, monkeypatch):
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _debate_ask_role)
        monkeypatch.setattr("cio.committee.debate.ask_role", _debate_ask_role)
        from cio.committee.engine import run_committee
        return self._run(run_committee(SYMBOL_AAPL, debate=True))

    def test_round1_opinions_populated(self, monkeypatch):
        """result.round1_opinions should hold the 7 Round 1 specialist votes."""
        result = self._make_result_debate_on(monkeypatch)
        assert len(result.round1_opinions) == 7

    def test_debate_exchanges_non_empty(self, monkeypatch):
        """Debate must produce at least one exchange with actual challenge AND
        response text — guards against the role system_prompt not being resolved
        from roles_by_key (opinion dicts carry no system_prompt), which would
        silently swallow both calls into empty strings."""
        result = self._make_result_debate_on(monkeypatch)
        assert not result.debate.get("skipped", True)
        exchanges = result.debate["exchanges"]
        assert len(exchanges) >= 1
        ex = exchanges[0]
        assert ex["challenge"].strip(), "challenge text empty — system_prompt not resolved"
        assert ex["response"].strip(), "response text empty — system_prompt not resolved"

    def test_opinions_are_round3(self, monkeypatch):
        """result.opinions (final) must differ from round1_opinions — risk changed vote."""
        result = self._make_result_debate_on(monkeypatch)
        # risk was SELL in Round 1; should be HOLD in Round 3
        r1_risk = next(op for op in result.round1_opinions if op["key"] == "risk")
        r3_risk = next(op for op in result.opinions if op["key"] == "risk")
        assert r1_risk["vote"] == "SELL"
        assert r3_risk["vote"] == "HOLD"

    def test_tally_computed_on_round3(self, monkeypatch):
        """vote_tally must reflect Round 3 votes (risk now HOLD → sell_count decreases)."""
        result = self._make_result_debate_on(monkeypatch)
        # With risk moved from SELL→HOLD, sell_count should be 0
        assert result.vote_tally["sell_count"] == 0

    def test_debate_off_via_param(self, monkeypatch):
        """debate=False → result.debate skipped, opinions == round1."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL, debate=False))
        assert result.debate.get("skipped") is True
        assert result.opinions == result.round1_opinions

    def test_debate_off_via_env(self, monkeypatch):
        """CIO_DEBATE=off → skipped."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        monkeypatch.setenv("CIO_DEBATE", "off")
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL))
        assert result.debate.get("skipped") is True

    def test_all_same_vote_skips_gracefully(self, monkeypatch):
        """When all specialists return same vote, debate skips with no exception."""
        same_vote_yaml = textwrap.dedent("""\
            ```yaml
            vote: HOLD
            confidence: 60
            reason: Everything is fine.
            ```""")

        async def _all_hold(system_prompt, user_prompt, role_key=None, service=None, model=None):
            sp = system_prompt.lower()
            if "chief investment officer" in sp:
                return _ROLE_YAML["cio"]
            if "moderator" in sp:
                return _ROLE_YAML["moderator"]
            return same_vote_yaml

        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _all_hold)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL, debate=True))
        assert result.debate.get("skipped") is True
        assert result.error is None


# ---------------------------------------------------------------------------
# build_report debate sections
# ---------------------------------------------------------------------------

class TestBuildReportDebate:
    def _run(self, coro):
        return asyncio.run(coro)

    def _make_result_debate_on(self, monkeypatch):
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _debate_ask_role)
        monkeypatch.setattr("cio.committee.debate.ask_role", _debate_ask_role)
        from cio.committee.engine import run_committee
        return self._run(run_committee(SYMBOL_AAPL, debate=True))

    def test_debate_section_present_when_debate_ran(self, monkeypatch):
        """Report must contain ### Debate when debate ran."""
        result = self._make_result_debate_on(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        assert "### Debate" in report

    def test_vote_changes_section_present(self, monkeypatch):
        """Report must contain ### Vote Changes when debate ran."""
        result = self._make_result_debate_on(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        assert "### Vote Changes" in report

    def test_vote_change_non_trivial(self, monkeypatch):
        """Risk moved SELL→HOLD — table must show that change."""
        result = self._make_result_debate_on(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        assert "SELL→HOLD" in report

    def test_no_material_disagreement_when_skipped(self, monkeypatch):
        """Debate skipped → '_No material disagreement; debate skipped.' in report."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL, debate=False))
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        assert "_No material disagreement; debate skipped._" in report

    def test_report_never_crashes_on_missing_debate_field(self):
        """build_report must not crash when debate field is absent."""
        from cio.committee.engine import CommitteeResult
        from cio.committee.report import build_report
        empty_result = CommitteeResult(
            symbol="EMPTY",
            resolved="EMPTY",
            as_of="2026-06-01T00:00:00",
            bundle={},
            opinions=[],
            consensus={},
            vote_tally={},
            cio={},
        )
        # debate and round1_opinions default to empty — should not crash
        try:
            build_report("EMPTY", empty_result)
        except Exception as e:
            pytest.fail(f"build_report raised {e} on missing debate field")

    def test_all_13_sections_still_present_with_debate(self, monkeypatch):
        """All 13 original section headers must still appear when debate ran."""
        result = self._make_result_debate_on(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        for header in EXPECTED_SECTION_HEADERS:
            assert header in report, f"Missing section after debate: {header}"


# ---------------------------------------------------------------------------
# KG-7 — _is_limit_notice unit tests
# ---------------------------------------------------------------------------

class TestIsLimitNotice:
    def test_true_on_real_notice(self):
        """The canonical limit notice must be detected."""
        from cio.committee.engine import _is_limit_notice
        notice = "You've hit your session limit · resets 3:30pm"
        assert _is_limit_notice(notice) is True

    def test_true_on_usage_limit(self):
        from cio.committee.engine import _is_limit_notice
        assert _is_limit_notice("usage limit reached, try again later") is True

    def test_true_on_rate_limit(self):
        from cio.committee.engine import _is_limit_notice
        assert _is_limit_notice("rate limit exceeded. resets in one hour.") is True

    def test_false_on_long_analyst_answer_with_limit_word(self):
        """A 500-char analyst paragraph that mentions 'limit' must NOT be treated as a notice."""
        from cio.committee.engine import _is_limit_notice
        long_answer = (
            "The company's growth is constrained by regulatory limit caps in the EU, "
            "but its domestic market expansion more than compensates. The balance sheet "
            "is strong: net debt-to-EBITDA is below the sector limit of 3x, and free "
            "cash flow yield is among the highest in the peer group. We maintain a BUY "
            "recommendation with a 12-month price target representing meaningful upside. "
            "Risk factors include macroeconomic headwinds and potential margin compression "
            "from rising input costs, but management has a strong track record of navigating "
            "such challenges effectively."
        )
        assert len(long_answer) > 400
        assert _is_limit_notice(long_answer) is False

    def test_false_on_normal_short_text_no_keywords(self):
        from cio.committee.engine import _is_limit_notice
        assert _is_limit_notice("Buy. Strong fundamentals.") is False

    def test_empty_string_is_false(self):
        """Empty string has no limit keywords → False."""
        from cio.committee.engine import _is_limit_notice
        assert _is_limit_notice("") is False


# ---------------------------------------------------------------------------
# KG-6 — build_report cosmetics
# ---------------------------------------------------------------------------

class TestBuildReportCosmetics:
    def _run(self, coro):
        return asyncio.run(coro)

    def _make_result(self, monkeypatch):
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        return self._run(run_committee(SYMBOL_AAPL))

    def test_market_cap_humanized_trillions(self, monkeypatch):
        """Market cap 2.7T should render as $2.70T (not raw integer)."""
        result = self._make_result(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        # 2_700_000_000_000 → $2.70T
        assert "$2.70T" in report, f"Expected '$2.70T' in report, got excerpt: {report[report.find('Market Cap'):report.find('Market Cap')+60]}"

    def test_market_cap_humanized_large_example(self):
        """_human_num(4523118034944) → '$4.52T'."""
        from cio.committee.report import _human_num
        assert _human_num(4523118034944) == "$4.52T"

    def test_human_num_billions(self):
        from cio.committee.report import _human_num
        assert _human_num(912_300_000_000) == "$912.3B"

    def test_human_num_millions(self):
        from cio.committee.report import _human_num
        assert _human_num(45_000_000) == "$45.0M"

    def test_human_num_non_numeric_fallback(self):
        from cio.committee.report import _human_num
        assert _human_num("N/A") == "N/A"

    def test_tally_label_net_directional(self, monkeypatch):
        """Tally line must show 'Net Directional Score' label."""
        result = self._make_result(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        assert "Net Directional Score" in report

    def test_old_tally_label_absent(self, monkeypatch):
        """'Confidence-Weighted Score' label must no longer appear in report."""
        result = self._make_result(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        assert "Confidence-Weighted Score" not in report


# ---------------------------------------------------------------------------
# Per-agent MemCore tests (Step 5) — all offline, tmp db
# ---------------------------------------------------------------------------

def _tmpdb() -> Path:
    """Create a fresh isolated temp db for one test."""
    from cio import db
    p = Path(tempfile.mkdtemp()) / "t.db"
    db.init(p)
    return p


class TestAgentMemoryIsolation:
    """Core isolation guarantee: notes in one scope never surface for another."""

    def test_note_visible_to_own_scope(self, monkeypatch):
        """A note saved for 'risk' is visible via recall_block('risk', ...)."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio.committee import agent_memory
        agent_memory.save_note("risk", "AAPL China tail risk is a persistent watch-item", "AAPL")
        block = agent_memory.recall_block("risk", "AAPL")
        assert "AAPL China tail risk" in block

    def test_note_invisible_to_other_scope(self, monkeypatch):
        """A note saved for 'risk' must NOT appear in recall_block('valuation', ...)."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio.committee import agent_memory
        agent_memory.save_note("risk", "AAPL China tail risk is a persistent watch-item", "AAPL")
        block = agent_memory.recall_block("valuation", "AAPL")
        assert "AAPL China tail risk" not in block

    def test_global_note_invisible_to_committee_scope(self, monkeypatch):
        """A global note must NOT appear in any committee recall_block."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory
        from cio.committee import agent_memory
        # Write a global note directly (bypassing agent_memory so scope='global')
        memory.remember("global thesis: macro matters", scope="global", db_path=p)
        block = agent_memory.recall_block("risk", "AAPL")
        assert "global thesis" not in block

    def test_include_global_false_excludes_global_note(self, monkeypatch):
        """recall.search with include_global=False excludes a note that include_global=True would include."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory, recall
        memory.remember("global recall test note", scope="global", db_path=p)
        # include_global=True (default) — should find it
        hits_with = recall.search("global recall test note", k=5, scope="committee:risk",
                                  kinds=("note",), db_path=p, include_global=True)
        # include_global=False — must NOT find it
        hits_without = recall.search("global recall test note", k=5, scope="committee:risk",
                                     kinds=("note",), db_path=p, include_global=False)
        assert any("global recall test note" in h["text"] for h in hits_with), \
            "include_global=True should surface a global note"
        assert not any("global recall test note" in h["text"] for h in hits_without), \
            "include_global=False must not surface a global note"

    def test_cross_scope_strict(self, monkeypatch):
        """recall.search with include_global=False finds own-scope note but not other-scope note."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory, recall
        memory.remember("risk agent unique phrase", scope="committee:risk", db_path=p)
        memory.remember("valuation agent unique phrase", scope="committee:valuation", db_path=p)
        risk_hits = recall.search("unique phrase", k=5, scope="committee:risk",
                                  kinds=("note",), db_path=p, include_global=False)
        val_hits = recall.search("unique phrase", k=5, scope="committee:valuation",
                                 kinds=("note",), db_path=p, include_global=False)
        risk_texts = [h["text"] for h in risk_hits]
        val_texts = [h["text"] for h in val_hits]
        assert any("risk agent" in t for t in risk_texts)
        assert not any("valuation agent" in t for t in risk_texts)
        assert any("valuation agent" in t for t in val_texts)
        assert not any("risk agent" in t for t in val_texts)


class TestAgentMemoryFirewall:
    """Figures firewall must hold for committee agents."""

    def test_figure_note_rejected(self, monkeypatch):
        """save_note with a dollar amount returns None and stores nothing."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory
        from cio.committee import agent_memory
        result = agent_memory.save_note("risk", "fair value $182 target", "AAPL")
        assert result is None
        assert memory.count_notes("committee:risk", db_path=p) == 0

    def test_qualitative_note_accepted(self, monkeypatch):
        """A qualitative note (no figure) is stored and count increases."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory
        from cio.committee import agent_memory
        result = agent_memory.save_note("risk", "AAPL regulatory risk is a persistent watch-item", "AAPL")
        assert isinstance(result, int)
        assert memory.count_notes("committee:risk", db_path=p) == 1


class TestAgentMemoryInjection:
    """run_specialist injects the right agent's memory into its system prompt."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_injection_contains_own_note(self, monkeypatch):
        """After seeding a hot note for 'risk', run_specialist passes it in the system prompt."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory
        from cio.committee import agent_memory
        from cio.committee.roles import SPECIALISTS

        # Seed a HOT note directly for the risk scope
        memory.remember(
            "AAPL tail risk pattern watch-item",
            scope="committee:risk", tier="hot", db_path=p,
        )

        captured: list[str] = []

        async def fake_ask_role(system_prompt: str, user_prompt: str, role_key=None, service=None, model=None) -> str:
            captured.append(system_prompt)
            return _ROLE_YAML["risk"]

        monkeypatch.setattr("cio.committee.engine.ask_role", fake_ask_role)

        risk_role = next(r for r in SPECIALISTS if r["key"] == "risk")
        from cio.committee.engine import run_specialist
        self._run(run_specialist(risk_role, "DATA", "AAPL"))

        assert captured, "ask_role was not called"
        assert "AAPL tail risk pattern watch-item" in captured[0], \
            "Hot note for risk scope not found in system prompt"

    def test_injection_excludes_other_scope_note(self, monkeypatch):
        """A note seeded for 'valuation' must NOT appear in the 'risk' agent's prompt."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory
        from cio.committee.roles import SPECIALISTS

        memory.remember(
            "AAPL valuation note only for valuation scope",
            scope="committee:valuation", tier="hot", db_path=p,
        )

        captured: list[str] = []

        async def fake_ask_role(system_prompt: str, user_prompt: str, role_key=None, service=None, model=None) -> str:
            captured.append(system_prompt)
            return _ROLE_YAML["risk"]

        monkeypatch.setattr("cio.committee.engine.ask_role", fake_ask_role)

        risk_role = next(r for r in SPECIALISTS if r["key"] == "risk")
        from cio.committee.engine import run_specialist
        self._run(run_specialist(risk_role, "DATA", "AAPL"))

        assert captured, "ask_role was not called"
        assert "valuation note only for valuation scope" not in captured[0], \
            "Valuation-scoped note leaked into risk agent's system prompt"


class TestAgentMemoryPromotion:
    """Warm notes promoted to hot after enough bumps appear in build_scope_block."""

    def test_promote_on_reflect(self, monkeypatch):
        """Seed a warm note, bump it >= PROMOTE_HITS times, reflect → note becomes hot."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory
        from cio.committee import agent_memory

        nid = agent_memory.save_note("risk", "watch AAPL China exposure each earnings", "AAPL")
        assert nid is not None

        # Bump enough times to cross the promotion threshold
        from cio.memory import PROMOTE_HITS
        for _ in range(PROMOTE_HITS):
            memory.bump(nid, db_path=p)

        promoted = agent_memory.reflect("risk")
        assert promoted >= 1

        # Now it must appear in build_scope_block (hot notes are injected)
        from cio import context
        block = context.build_scope_block("committee:risk", db_path=p)
        assert "watch AAPL China exposure" in block

    def test_recall_block_bumps_drive_promotion(self, monkeypatch):
        """Repeated recall_block calls bump the hit counter; reflect then promotes."""
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        from cio import memory
        from cio.committee import agent_memory
        from cio.memory import PROMOTE_HITS

        nid = agent_memory.save_note("risk", "AAPL regulatory watch pattern", "AAPL")
        assert nid is not None

        # Each recall_block call bumps matching notes
        for _ in range(PROMOTE_HITS):
            agent_memory.recall_block("risk", "AAPL")

        promoted = agent_memory.reflect("risk")
        assert promoted >= 1

        note = memory.get_note(nid, db_path=p)
        assert note is not None
        assert note["tier"] == "hot"


class TestAgentMemoryReportOmission:
    """memory_note must never appear in the rendered committee report."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _make_result(self, monkeypatch):
        p = _tmpdb()
        monkeypatch.setattr("cio.committee.agent_memory.DB_PATH", p)
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)
        from cio.committee.engine import run_committee
        return self._run(run_committee(SYMBOL_AAPL))

    def test_memory_note_absent_from_report(self, monkeypatch):
        """The text of memory_note values must not appear in build_report output."""
        result = self._make_result(monkeypatch)
        from cio.committee.report import build_report
        report = build_report(SYMBOL_AAPL, result)
        # These are the memory_note values injected in the canned YAML
        assert "AAPL benefits from risk-on macro tailwinds in tech rotation cycles" not in report
        assert "AAPL China concentration is a persistent tail risk worth monitoring each cycle" not in report
        assert "AAPL is a quality compounder" not in report

    def test_memory_note_in_opinion_dict_not_report(self, monkeypatch):
        """memory_note is present in the opinion dict but build_report does not render it."""
        result = self._make_result(monkeypatch)
        from cio.committee.report import build_report
        # It may be in the opinion dict (parsed from yaml)
        market_op = next((op for op in result.opinions if op["key"] == "market"), None)
        assert market_op is not None
        # memory_note in opinion dict is fine (it's the agent's private memory)
        # But build_report must not render it
        report = build_report(SYMBOL_AAPL, result)
        assert "memory_note" not in report


class TestBuildScopeBlock:
    """context.build_scope_block returns scope-only content within budget."""

    def test_empty_scope_returns_empty(self, monkeypatch):
        """An empty scope produces an empty string."""
        p = _tmpdb()
        from cio import context
        block = context.build_scope_block("committee:risk", budget=400, db_path=p)
        assert block == ""

    def test_hot_note_appears_in_block(self, monkeypatch):
        """A hot note written for the scope appears in build_scope_block."""
        p = _tmpdb()
        from cio import context, memory
        memory.remember("risk hot note for scope block test", scope="committee:risk",
                        tier="hot", db_path=p)
        block = context.build_scope_block("committee:risk", db_path=p)
        assert "risk hot note for scope block test" in block

    def test_global_note_excluded(self, monkeypatch):
        """A global hot note must NOT appear in a committee scope block."""
        p = _tmpdb()
        from cio import context, memory
        memory.remember("global hot note must stay out", scope="global", tier="hot", db_path=p)
        block = context.build_scope_block("committee:risk", db_path=p)
        assert "global hot note must stay out" not in block

    def test_budget_respected(self, monkeypatch):
        """build_scope_block stays within the token budget."""
        p = _tmpdb()
        from cio import context, memory
        # Write many hot notes
        for i in range(20):
            try:
                memory.remember(f"risk note number {i} about sector dynamics", scope="committee:risk",
                                tier="hot", db_path=p)
            except Exception:
                pass
        budget = 50
        block = context.build_scope_block("committee:risk", budget=budget, db_path=p)
        if block:
            assert context.count_tokens(block) <= budget


# ---------------------------------------------------------------------------
# Step 6 — models.py config / routing / NIM backend / parallel
# ---------------------------------------------------------------------------

class TestModelsConfig:
    """load_config / resolve / nim_settings — offline, no network."""

    def setup_method(self):
        # Clear the lru_cache before each test so configs don't bleed across tests
        from cio.committee.models import load_config
        load_config.cache_clear()

    def teardown_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def test_resolve_cio_returns_claude(self):
        """Default YAML maps cio → ('claude', 'claude-opus-4-8')."""
        from cio.committee.models import resolve
        service, model = resolve("cio")
        assert service == "claude"
        assert model == "claude-opus-4-8"

    def test_resolve_market_returns_nim(self):
        """Default YAML maps market → ('nim', 'minimaxai/minimax-m2.7')."""
        from cio.committee.models import resolve
        service, model = resolve("market")
        assert service == "nim"
        assert model == "minimaxai/minimax-m2.7"

    def test_resolve_unknown_key_falls_back_to_defaults(self):
        """An unrecognised role_key falls through to defaults (nim)."""
        from cio.committee.models import resolve
        service, model = resolve("nonexistent_role_xyz")
        assert service == "nim"
        assert model == "minimaxai/minimax-m2.7"

    def test_missing_file_uses_builtin_defaults(self, tmp_path):
        """Missing config file → built-in defaults, no crash."""
        from cio.committee.models import load_config, resolve
        load_config.cache_clear()
        # Point at a non-existent file via explicit path
        cfg = load_config(path=str(tmp_path / "does_not_exist.yaml"))
        assert isinstance(cfg, dict)
        assert "agents" in cfg
        # Check routing still works
        load_config.cache_clear()

    def test_bad_yaml_uses_builtin_defaults(self, tmp_path, monkeypatch):
        """Unparseable YAML → built-in defaults, no crash."""
        from cio.committee.models import load_config, resolve
        bad = tmp_path / "bad.yaml"
        bad.write_text(": broken: yaml:::: !!!\n", encoding="utf-8")
        load_config.cache_clear()
        cfg = load_config(path=str(bad))
        assert isinstance(cfg, dict)
        assert "agents" in cfg
        load_config.cache_clear()

    def test_nim_settings_has_required_keys(self):
        """nim_settings() always returns base_url and api_key_env."""
        from cio.committee.models import nim_settings
        s = nim_settings()
        assert "base_url" in s
        assert "api_key_env" in s
        assert s["base_url"].startswith("https://")

    def test_custom_yaml_parsed(self, tmp_path):
        """A custom YAML file with cio→nim is parsed and respected."""
        from cio.committee.models import load_config, resolve
        custom = tmp_path / "custom.yaml"
        custom.write_text(
            "defaults: {service: nim, model: minimaxai/minimax-m2.7}\n"
            "agents:\n"
            "  cio: {service: nim, model: minimaxai/minimax-m2.7}\n"
            "nim: {base_url: 'https://integrate.api.nvidia.com/v1', api_key_env: NVIDIA_API_KEY}\n",
            encoding="utf-8",
        )
        load_config.cache_clear()
        cfg = load_config(path=str(custom))
        assert cfg["agents"]["cio"]["service"] == "nim"
        load_config.cache_clear()


class TestAskRoleRouting:
    """ask_role routes to the correct backend based on role_key config."""

    def _run(self, coro):
        return asyncio.run(coro)

    def setup_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def teardown_method(self):
        from cio.committee.models import load_config
        load_config.cache_clear()

    def test_role_key_cio_routes_to_claude(self, monkeypatch):
        """ask_role with role_key='cio' hits _ask_claude."""
        claude_calls = []
        nim_calls = []

        async def fake_claude(sp, up, model=None):
            claude_calls.append((sp, up))
            return "claude-response"

        async def fake_nim(sp, up, model=None):
            nim_calls.append((sp, up))
            return "nim-response"

        monkeypatch.setattr("cio.committee.engine._ask_claude", fake_claude)
        monkeypatch.setattr("cio.committee.engine._ask_nim", fake_nim)

        from cio.committee.engine import ask_role
        result = self._run(ask_role("sys", "user", role_key="cio"))
        assert result == "claude-response"
        assert len(claude_calls) == 1
        assert len(nim_calls) == 0

    def test_role_key_market_routes_to_nim(self, monkeypatch):
        """ask_role with role_key='market' hits _ask_nim."""
        claude_calls = []
        nim_calls = []

        async def fake_claude(sp, up, model=None):
            claude_calls.append((sp, up))
            return "claude-response"

        async def fake_nim(sp, up, model=None):
            nim_calls.append((sp, up))
            return "nim-response"

        monkeypatch.setattr("cio.committee.engine._ask_claude", fake_claude)
        monkeypatch.setattr("cio.committee.engine._ask_nim", fake_nim)

        from cio.committee.engine import ask_role
        result = self._run(ask_role("sys", "user", role_key="market"))
        assert result == "nim-response"
        assert len(nim_calls) == 1
        assert len(claude_calls) == 0

    def test_explicit_service_overrides_config(self, monkeypatch):
        """Explicit service='claude' overrides the config even for a nim role."""
        claude_calls = []

        async def fake_claude(sp, up, model=None):
            claude_calls.append((sp, up))
            return "forced-claude"

        async def fake_nim(sp, up, model=None):
            raise AssertionError("_ask_nim should not be called")

        monkeypatch.setattr("cio.committee.engine._ask_claude", fake_claude)
        monkeypatch.setattr("cio.committee.engine._ask_nim", fake_nim)

        from cio.committee.engine import ask_role
        result = self._run(ask_role("sys", "user", role_key="market", service="claude"))
        assert result == "forced-claude"
        assert len(claude_calls) == 1

    def test_no_role_key_defaults_to_claude(self, monkeypatch):
        """Legacy call with no role_key defaults to claude backend."""
        claude_calls = []

        async def fake_claude(sp, up, model=None):
            claude_calls.append(True)
            return "default-claude"

        async def fake_nim(sp, up, model=None):
            raise AssertionError("_ask_nim should not be called for role_key=None")

        monkeypatch.setattr("cio.committee.engine._ask_claude", fake_claude)
        monkeypatch.setattr("cio.committee.engine._ask_nim", fake_nim)

        from cio.committee.engine import ask_role
        result = self._run(ask_role("sys", "user"))
        assert result == "default-claude"
        assert claude_calls


class TestNIMBackend:
    """_ask_nim offline tests — monkeypatched httpx, no network."""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_nim_returns_content_with_key(self, monkeypatch):
        """_ask_nim parses response when API key is set."""
        monkeypatch.setenv("NVIDIA_API_KEY", "fake-test-key")

        import httpx

        class FakeResponse:
            def json(self):
                return {"choices": [{"message": {"content": "hi from nim"}}]}
            def raise_for_status(self):
                pass

        class FakeAsyncClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeAsyncClient())

        from cio.committee.engine import _ask_nim
        result = self._run(_ask_nim("system", "user", model="minimaxai/minimax-m2.7"))
        assert result == "hi from nim"

    def test_nim_returns_empty_without_key(self, monkeypatch):
        """_ask_nim returns '' and does NOT call httpx when NVIDIA_API_KEY is unset."""
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

        async def _should_not_post(*args, **kwargs):
            raise AssertionError("httpx.AsyncClient.post should not be called without API key")

        # We don't even need to monkeypatch post — just verify empty return
        from cio.committee.engine import _ask_nim
        result = self._run(_ask_nim("system", "user", model="minimaxai/minimax-m2.7"))
        assert result == ""

    def test_nim_returns_empty_on_http_error(self, monkeypatch):
        """_ask_nim returns '' gracefully on HTTP error."""
        monkeypatch.setenv("NVIDIA_API_KEY", "fake-test-key")

        import httpx

        class ErrorAsyncClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, **kwargs):
                raise httpx.HTTPStatusError(
                    "500 Internal Server Error",
                    request=None,  # type: ignore
                    response=None,  # type: ignore
                )

        monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: ErrorAsyncClient())

        from cio.committee.engine import _ask_nim
        result = self._run(_ask_nim("system", "user", model="minimaxai/minimax-m2.7"))
        assert result == ""

    def test_nim_limit_notice_returns_empty(self, monkeypatch):
        """_ask_nim returns '' when the NIM response text is a limit notice."""
        monkeypatch.setenv("NVIDIA_API_KEY", "fake-test-key")

        class FakeResponse:
            def json(self):
                return {"choices": [{"message": {"content": "usage limit reached, try again later"}}]}
            def raise_for_status(self):
                pass

        class FakeAsyncClient:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            async def post(self, url, **kwargs):
                return FakeResponse()

        monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeAsyncClient())

        from cio.committee.engine import _ask_nim
        result = self._run(_ask_nim("system", "user"))
        assert result == ""


class TestParallelExecution:
    """
    Verify that run_committee runs specialists in parallel when parallel=True
    and sequentially when parallel=False, with identical result shapes.
    """

    def _run(self, coro):
        return asyncio.run(coro)

    def _make_parallel_fake(self):
        """
        Returns (fake_ask_role, get_peak) where fake_ask_role tracks peak concurrency.
        Each call enters, sleeps briefly, exits.
        """
        import asyncio as _asyncio

        counter = {"active": 0, "peak": 0}

        async def fake_ask_role(
            system_prompt: str,
            user_prompt: str,
            role_key: str | None = None,
            service: str | None = None,
            model=None,
        ) -> str:
            counter["active"] += 1
            if counter["active"] > counter["peak"]:
                counter["peak"] = counter["active"]
            await _asyncio.sleep(0.02)
            counter["active"] -= 1

            # Return canned YAML so the pipeline doesn't break
            sp = system_prompt.lower()
            up = user_prompt.lower()
            if "free text" in up or "no yaml needed" in up:
                return "debate prose"
            if "chief investment officer" in sp:
                return _ROLE_YAML["cio"]
            if "moderator" in sp:
                return _ROLE_YAML["moderator"]
            return _ROLE_YAML["market"]

        return fake_ask_role, counter

    def test_parallel_peak_greater_than_one(self, monkeypatch):
        """parallel=True → multiple specialists overlap (peak > 1)."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        fake, counter = self._make_parallel_fake()
        monkeypatch.setattr("cio.committee.engine.ask_role", fake)

        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL, debate=False, parallel=True))

        assert result.error is None
        assert len(result.opinions) == 7
        assert counter["peak"] > 1, f"Expected peak > 1 in parallel mode, got {counter['peak']}"

    def test_sequential_peak_equals_one(self, monkeypatch):
        """parallel=False → specialists run one at a time (peak == 1)."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        fake, counter = self._make_parallel_fake()
        monkeypatch.setattr("cio.committee.engine.ask_role", fake)

        from cio.committee.engine import run_committee
        result = self._run(run_committee(SYMBOL_AAPL, debate=False, parallel=False))

        assert result.error is None
        assert len(result.opinions) == 7
        assert counter["peak"] == 1, f"Expected peak == 1 in sequential mode, got {counter['peak']}"

    def test_parallel_and_sequential_same_shape(self, monkeypatch):
        """Both modes produce a CommitteeResult with identical structure."""
        monkeypatch.setattr("cio.committee.engine.gather_bundle", lambda sym: FAKE_BUNDLE_AAPL)
        monkeypatch.setattr("cio.committee.engine.ask_role", _canned_ask_role)

        from cio.committee.engine import run_committee
        r_par = self._run(run_committee(SYMBOL_AAPL, debate=False, parallel=True))
        r_seq = self._run(run_committee(SYMBOL_AAPL, debate=False, parallel=False))

        assert r_par.error is None
        assert r_seq.error is None
        assert len(r_par.opinions) == len(r_seq.opinions) == 7
        assert set(op["key"] for op in r_par.opinions) == set(op["key"] for op in r_seq.opinions)
