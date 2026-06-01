"""
test_committee.py — offline tests for cio.committee

All tests monkeypatch ask_role and/or gather_bundle; no network, no LLM.
"""
from __future__ import annotations

import asyncio
import textwrap
from typing import Any

import pytest

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
        ```"""),
}


async def _canned_ask_role(system_prompt: str, user_prompt: str, model=None) -> str:
    """Async version: determine which role is being called and return canned yaml.
    Detection is purely on system_prompt to avoid confusion when user_prompt
    contains output from prior LLM calls."""
    sp = system_prompt.lower()

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


async def _debate_ask_role(system_prompt: str, user_prompt: str, model=None) -> str:
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
        return await _canned_ask_role(system_prompt, user_prompt, model)
    return await _canned_ask_role(system_prompt, user_prompt, model)


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

        async def _all_hold(system_prompt, user_prompt, model=None):
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
