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
    from cio.committee.models import load_config, resolve_chain
    load_config.cache_clear()
    chain = resolve_chain("wma")
    services = [c["service"] for c in chain]
    assert services == ["openai", "claude", "nim"]
    assert chain[0]["model"].startswith("gpt-5.5")
    assert chain[1]["model"] == "claude-opus-4-8"
    assert "kimi" in chain[2]["model"]
