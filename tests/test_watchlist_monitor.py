"""
test_watchlist_monitor.py — offline tests for cio.watchlist_monitor (WMA).

All tests inject bundle_fn / news_fn and monkeypatch engine.ask_role; no network,
no LLM, no yfinance.
"""
from __future__ import annotations

import asyncio
import textwrap

import pytest


def _run(coro):
    return asyncio.run(coro)


FAKE_BUNDLE = {
    "symbol": "MU", "resolved": "MU",
    "quote": {"close": 100.0, "change_pct": 2.0, "volume": 1_000_000},
    "fundamentals": {"name": "Micron Technology", "pe": 15.0, "quoteType": "EQUITY"},
    "ta_signals": {"rsi": "bull"},
    "is_etf": False,
    "as_of": "2026-06-03T00:00:00",
}

NO_DATA_BUNDLE = {"symbol": "ZZZZ", "resolved": None, "quote": None,
                  "fundamentals": None, "ta_signals": {}, "is_etf": False,
                  "as_of": "2026-06-03T00:00:00"}


def _yaml(status="bullish", conv=84, rec="Add", importance="high",
          thesis="positive"):
    return textwrap.dedent(f"""\
        Here is my read.

        ```yaml
        ticker: MU
        company: Micron Technology
        overall_status: {status}
        conviction_score: {conv}
        recommendation: {rec}
        analyst_sentiment: bullish
        event_importance: {importance}
        investment_thesis_change: {thesis}
        external_risk_score: 72
        macro_sensitivity: medium
        geopolitical_sensitivity: high
        commodity_sensitivity: low
        currency_sensitivity: medium
        key_positive_events:
          - HBM demand increased
          - Analyst target raised
        key_negative_events:
          - Memory pricing concerns
        new_risks:
          - China exposure
        upcoming_catalysts:
          - earnings release
        summary: HBM tailwind intact; thesis strengthening.
        ```
    """)


def _fake_news(query, limit=5):
    return [{"title": "Micron HBM demand surges", "description": "analysts bullish",
             "url": "http://example.com/mu"}]


@pytest.fixture
def patch_ask(monkeypatch):
    """Patch engine.ask_role to return a canned yaml block."""
    def _install(text):
        async def _fake(system, user, role_key=None, **kw):
            _install.calls.append((role_key, system, user))
            return text
        _install.calls = []
        monkeypatch.setattr("cio.committee.engine.ask_role", _fake)
        return _install
    return _install


# ---------------------------------------------------------------------------
# monitor_symbol
# ---------------------------------------------------------------------------

def test_monitor_symbol_parses_and_normalizes(patch_ask):
    from cio.watchlist_monitor import monitor_symbol
    installed = patch_ask(_yaml())
    a = _run(monitor_symbol("MU", bundle_fn=lambda s: FAKE_BUNDLE, news_fn=_fake_news))

    assert a["ticker"] == "MU"
    assert a["company"] == "Micron Technology"
    assert a["overall_status"] == "bullish"
    assert a["conviction_score"] == 84
    assert a["recommendation"] == "Add"
    assert a["event_importance"] == "high"
    assert a["investment_thesis_change"] == "positive"
    assert a["key_positive_events"] == ["HBM demand increased", "Analyst target raised"]
    assert a["new_risks"] == ["China exposure"]
    assert a["upcoming_catalysts"] == ["earnings release"]
    assert a["escalate"] is True            # high importance → escalate
    assert a["error"] is None
    # external-risk exposure fields parsed + normalized
    assert a["external_risk_score"] == 72
    assert a["macro_sensitivity"] == "medium"
    assert a["geopolitical_sensitivity"] == "high"
    assert a["commodity_sensitivity"] == "low"
    assert a["currency_sensitivity"] == "medium"
    # role_key must route through the wma model chain
    assert installed.calls[0][0] == "wma"


def test_monitor_symbol_no_data_skips_llm(patch_ask):
    from cio.watchlist_monitor import monitor_symbol
    installed = patch_ask(_yaml())
    a = _run(monitor_symbol("ZZZZ", bundle_fn=lambda s: NO_DATA_BUNDLE, news_fn=_fake_news))

    assert a["error"] and "no data" in a["error"]
    assert a["recommendation"] == "Monitor"
    assert a["escalate"] is False
    assert installed.calls == []            # never spent a model call


def test_monitor_symbol_bad_recommendation_falls_back(patch_ask):
    from cio.watchlist_monitor import monitor_symbol
    patch_ask(_yaml(rec="YOLO", status="sideways", importance="nuclear",
                    thesis="maybe"))
    a = _run(monitor_symbol("MU", bundle_fn=lambda s: FAKE_BUNDLE, news_fn=_fake_news))
    assert a["recommendation"] == "Monitor"        # invalid → default
    assert a["overall_status"] == "neutral"        # invalid → default
    assert a["event_importance"] == "low"          # invalid → default
    assert a["investment_thesis_change"] == "unchanged"


def test_low_importance_unchanged_does_not_escalate(patch_ask):
    from cio.watchlist_monitor import monitor_symbol
    patch_ask(_yaml(importance="low", thesis="unchanged"))
    a = _run(monitor_symbol("MU", bundle_fn=lambda s: FAKE_BUNDLE, news_fn=_fake_news))
    assert a["escalate"] is False


# ---------------------------------------------------------------------------
# monitor_watchlist
# ---------------------------------------------------------------------------

def test_monitor_watchlist_preserves_order(patch_ask):
    from cio.watchlist_monitor import monitor_watchlist
    patch_ask(_yaml())
    out = _run(monitor_watchlist(
        ["MU", "NVDA", "TSM"],
        bundle_fn=lambda s: {**FAKE_BUNDLE, "resolved": s},
        news_fn=_fake_news,
    ))
    assert [a["ticker"] for a in out] == ["MU", "NVDA", "TSM"]


def test_monitor_watchlist_empty_symbols():
    from cio.watchlist_monitor import monitor_watchlist
    assert _run(monitor_watchlist([])) == []


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def test_build_briefing_has_prd_sections(patch_ask):
    from cio.watchlist_monitor import monitor_watchlist, build_briefing
    patch_ask(_yaml())
    out = _run(monitor_watchlist(["MU"], bundle_fn=lambda s: FAKE_BUNDLE,
                                 news_fn=_fake_news))
    md = build_briefing(out, as_of="2026-06-03")
    for heading in ("Executive Summary", "Highest Priority Alerts",
                    "New Risks", "Upcoming Catalysts", "Watchlist Review"):
        assert heading in md
    assert "Committee Escalation" in md     # high importance → escalation section
    assert "MU" in md


def test_build_briefing_empty():
    from cio.watchlist_monitor import build_briefing
    md = build_briefing([], as_of="2026-06-03")
    assert "No active watchlist" in md


# ---------------------------------------------------------------------------
# global macro snapshot + macro-aware briefing
# ---------------------------------------------------------------------------

_MACRO_YAML = textwrap.dedent("""\
    Global desk read.

    ```yaml
    market_sentiment: risk-off
    geopolitical_risk: high
    commodity_risk: high
    key_events:
      - Brent crude above $95
      - New semiconductor export controls
    summary: Oil spike and export controls weigh on risk appetite.
    ```
""")


def test_global_macro_snapshot_parses_and_normalizes(patch_ask):
    from cio.watchlist_monitor import global_macro_snapshot
    installed = patch_ask(_MACRO_YAML)
    m = _run(global_macro_snapshot(news_fn=_fake_news))
    assert m["market_sentiment"] == "risk-off"
    assert m["geopolitical_risk"] == "high"
    assert m["commodity_risk"] == "high"
    assert "Brent crude above $95" in m["key_events"]
    # one shared call, routed through the macro role
    assert installed.calls[0][0] == "macro"


def test_global_macro_snapshot_offline_safe(patch_ask):
    from cio.watchlist_monitor import global_macro_snapshot
    patch_ask("no yaml here")               # unparseable → neutral defaults
    m = _run(global_macro_snapshot(news_fn=lambda q, limit=5: []))
    assert m["market_sentiment"] == "cautious"
    assert m["geopolitical_risk"] == "low"
    assert m["commodity_risk"] == "low"


def test_build_briefing_with_macro_sections(patch_ask):
    from cio.watchlist_monitor import monitor_watchlist, build_briefing
    patch_ask(_yaml())
    out = _run(monitor_watchlist(["MU"], bundle_fn=lambda s: FAKE_BUNDLE,
                                 news_fn=_fake_news))
    macro = {"market_sentiment": "risk-off", "geopolitical_risk": "high",
             "commodity_risk": "high", "key_events": ["Brent crude above $95"],
             "summary": "Risk-off backdrop."}
    md = build_briefing(out, as_of="2026-06-03", macro=macro)
    assert "Global Market Intelligence" in md
    assert "Watchlist Exposure Analysis" in md
    assert "Macro & Geopolitical Alerts" in md
    assert "Brent crude above $95" in md
    # per-security high geopolitical sensitivity surfaces in exposure table
    assert "geopolitical" in md.lower()


def test_build_briefing_without_macro_omits_global(patch_ask):
    """Backward-compat: no macro arg → no Global Market Intelligence section."""
    from cio.watchlist_monitor import monitor_watchlist, build_briefing
    patch_ask(_yaml())
    out = _run(monitor_watchlist(["MU"], bundle_fn=lambda s: FAKE_BUNDLE,
                                 news_fn=_fake_news))
    md = build_briefing(out, as_of="2026-06-03")
    assert "Global Market Intelligence" not in md
    # exposure analysis is always rendered
    assert "Watchlist Exposure Analysis" in md


def test_briefing_summary(patch_ask):
    from cio.watchlist_monitor import monitor_watchlist, briefing_summary
    patch_ask(_yaml())
    out = _run(monitor_watchlist(["MU"], bundle_fn=lambda s: FAKE_BUNDLE,
                                 news_fn=_fake_news))
    s = briefing_summary(out)
    assert "watchlist briefing" in s.lower()
    assert "/committee" in s                 # escalation hint


# ---------------------------------------------------------------------------
# model chain config
# ---------------------------------------------------------------------------

def test_wma_chain_resolves_three_links():
    """wma resolves to a NAMED 3-link chain (same setting machinery as cio).
    Link order/models are operator-tunable from the dashboard — assert the
    mechanism, not the operator's current picks."""
    from cio.committee.models import load_config, resolve_chain, resolve_chain_name, chains
    load_config.cache_clear()
    name = resolve_chain_name("wma")
    chain = resolve_chain("wma")
    assert name is not None
    assert chain == chains()[name]
    assert len(chain) == 3
    assert all(link["service"] in ("openai", "claude", "nim") and link["model"]
               for link in chain)
    load_config.cache_clear()


# ---------------------------------------------------------------------------
# F7 — deterministic market brief (zero LLM)
# ---------------------------------------------------------------------------

def _assess(status):
    return {"ticker": "X", "overall_status": status, "event_importance": "low"}


def test_market_brief_breadth_counts():
    from cio.watchlist_monitor import build_market_brief
    a = [_assess("bullish"), _assess("bullish"), _assess("neutral"), _assess("bearish")]
    out = build_market_brief(a)
    assert "leaders 2" in out and "neutral 1" in out and "defensive 1" in out


def test_market_brief_bias_risk_on_off():
    from cio.watchlist_monitor.report import _risk_bias
    assert _risk_bias({"bullish": 5, "neutral": 1, "bearish": 1}, None) == "risk-on"
    assert _risk_bias({"bullish": 1, "neutral": 1, "bearish": 4}, None) == "risk-off"
    assert _risk_bias({"bullish": 2, "neutral": 1, "bearish": 2}, None) == "mixed"


def test_market_brief_regime_overrides_breadth():
    from cio.watchlist_monitor.report import _risk_bias
    # Green breadth but risk-off macro -> cannot read risk-on.
    out = _risk_bias({"bullish": 5, "bearish": 0}, {"label": "risk-off"})
    assert "risk-on" not in out and "macro caution" in out


def test_market_brief_regime_line_present_only_with_regime():
    from cio.watchlist_monitor import build_market_brief
    a = [_assess("neutral")]
    assert "Macro:" not in build_market_brief(a, regime=None)
    regime = {"label": "caution", "inverted": True, "spread_2s10s": -45.0,
              "hy_spread": 420.0}
    out = build_market_brief(a, regime=regime)
    assert "Macro:" in out and "INVERTED" in out and "HY OAS 420bps" in out


def test_market_brief_makes_no_llm_call(monkeypatch):
    # The brief is pure rules — it must never construct an Anthropic/OpenAI client.
    import cio.committee.engine as engine

    async def _boom(*a, **k):
        raise AssertionError("market brief must not call the LLM")

    monkeypatch.setattr(engine, "ask_role", _boom)
    from cio.watchlist_monitor import build_market_brief
    out = build_market_brief([_assess("bullish"), _assess("bearish")])
    assert "Bias:" in out


def test_regime_snapshot_none_when_fred_disabled(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    from cio.watchlist_monitor.report import _regime_snapshot
    assert _regime_snapshot() is None
