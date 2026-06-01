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
