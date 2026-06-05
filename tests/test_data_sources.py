"""test_data_sources.py — offline tests for cio.data (SEC EDGAR + Finnhub) and
their wiring into the committee bundle and the WMA escalation trigger.

No network: env gates keep the fetchers dormant unless explicitly enabled, and
the happy-path tests monkeypatch the HTTP layer with canned fixtures. A tmp cache
dir keeps the disk cache out of the repo's data/ tree.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from cio.data import edgar, finnhub


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _tmp_cache(monkeypatch, tmp_path):
    """Isolate the data cache so tests never read/write the real data/ dir."""
    monkeypatch.setenv("CIO_DATA_CACHE_DIR", str(tmp_path / "data_cache"))


# ---------------------------------------------------------------------------
# EDGAR
# ---------------------------------------------------------------------------

EDGAR_SUBMISSIONS = {
    "cik": 320193,
    "filings": {
        "recent": {
            "form": ["8-K", "4", "10-Q", "8-K", "10-K"],
            "filingDate": ["2026-06-03", "2026-06-02", "2026-05-01",
                           "2026-04-15", "2026-02-01"],
            "reportDate": ["2026-06-03", "", "2026-03-31", "2026-04-15",
                           "2025-12-31"],
            "accessionNumber": ["0000320193-26-000070", "x",
                                "0000320193-26-000060", "0000320193-26-000055",
                                "0000320193-26-000010"],
            "primaryDocument": ["aapl-8k.htm", "x", "aapl-10q.htm",
                                "aapl-8k2.htm", "aapl-10k.htm"],
            "primaryDocDescription": ["Material Event", "", "Quarterly Report",
                                      "Material Event", "Annual Report"],
        }
    },
}


def test_edgar_parse_filters_orders_and_builds_url():
    out = edgar._parse_submissions(EDGAR_SUBMISSIONS, ("8-K", "10-Q", "10-K"), 5)
    # form "4" is excluded; the other four kept in newest-first order.
    assert [f["form"] for f in out] == ["8-K", "10-Q", "8-K", "10-K"]
    assert out[0]["filed"] == "2026-06-03"
    assert out[0]["title"] == "Material Event"
    assert out[0]["url"] == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019326000070/aapl-8k.htm"
    )


def test_edgar_parse_respects_limit():
    out = edgar._parse_submissions(EDGAR_SUBMISSIONS, ("8-K", "10-Q", "10-K"), 2)
    assert len(out) == 2


def test_edgar_disabled_without_user_agent(monkeypatch):
    monkeypatch.delenv("CIO_SEC_UA", raising=False)

    def _boom(*a, **k):
        raise AssertionError("EDGAR must not hit the network when CIO_SEC_UA unset")

    monkeypatch.setattr("cio.data.edgar.get_json", _boom)
    assert edgar.recent_filings("AAPL") == []


def test_edgar_recent_filings_happy_path(monkeypatch):
    monkeypatch.setenv("CIO_SEC_UA", "CIOAgent Test test@example.com")
    monkeypatch.setattr("cio.data.edgar._cik_for", lambda sym, ua: 320193)
    monkeypatch.setattr("cio.data.edgar.get_json", lambda *a, **k: EDGAR_SUBMISSIONS)
    out = edgar.recent_filings("AAPL", limit=4)
    assert len(out) == 4
    assert out[0]["form"] == "8-K"


def test_edgar_unresolvable_symbol_returns_empty(monkeypatch):
    monkeypatch.setenv("CIO_SEC_UA", "CIOAgent Test test@example.com")
    monkeypatch.setattr("cio.data.edgar._cik_for", lambda sym, ua: None)
    monkeypatch.setattr("cio.data.edgar.get_json",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no cik -> no submissions call")))
    assert edgar.recent_filings("NOPE") == []


# ---------------------------------------------------------------------------
# Finnhub
# ---------------------------------------------------------------------------

FINNHUB_NEWS = [
    {"headline": "Apple unveils chip", "summary": "big news", "url": "http://a",
     "source": "Reuters", "datetime": 1_717_000_000},
    {"headline": "Apple services grow", "summary": "", "url": "http://b",
     "source": "CNBC", "datetime": 1_717_100_000},
]

FINNHUB_RECS = [
    {"period": "2026-05-01", "strongBuy": 12, "buy": 20, "hold": 5,
     "sell": 1, "strongSell": 0},
    {"period": "2026-04-01", "strongBuy": 10, "buy": 18, "hold": 6,
     "sell": 1, "strongSell": 0},
]

FINNHUB_EARN = [
    {"date": "2026-04-30", "epsEstimate": 1.5, "epsActual": 1.6,
     "revenueEstimate": 9.0e10, "hour": "amc"},
    {"date": "2026-07-31", "epsEstimate": 1.4, "epsActual": None,
     "revenueEstimate": 8.5e10, "hour": "amc"},
]


def test_finnhub_parse_news_maps_headline_to_title():
    out = finnhub._parse_news(FINNHUB_NEWS, 8)
    assert out[0]["title"] == "Apple unveils chip"
    assert out[0]["source"] == "Reuters"
    assert len(out) == 2


def test_finnhub_latest_recs_picks_newest_period():
    rec = finnhub._latest_recs(FINNHUB_RECS)
    assert rec["period"] == "2026-05-01"
    assert rec["strong_buy"] == 12
    assert rec["hold"] == 5


def test_finnhub_next_earnings_picks_future():
    nxt = finnhub._next_earnings(FINNHUB_EARN, today="2026-06-04")
    assert nxt["date"] == "2026-07-31"
    assert nxt["eps_estimate"] == 1.4


def test_finnhub_next_earnings_falls_back_to_most_recent_past():
    nxt = finnhub._next_earnings(FINNHUB_EARN, today="2026-09-01")
    assert nxt["date"] == "2026-07-31"


def test_finnhub_disabled_without_key(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)

    def _boom(*a, **k):
        raise AssertionError("Finnhub must not hit the network without a key")

    monkeypatch.setattr("cio.data.finnhub.get_json", _boom)
    assert finnhub.company_news("AAPL") == []
    assert finnhub.analyst_recs("AAPL") is None
    assert finnhub.earnings_calendar("AAPL") is None


def test_finnhub_company_news_happy_path(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    monkeypatch.setattr("cio.data.finnhub.get_json", lambda *a, **k: FINNHUB_NEWS)
    out = finnhub.company_news("AAPL", limit=8)
    assert [n["title"] for n in out] == ["Apple unveils chip", "Apple services grow"]


def test_finnhub_analyst_recs_happy_path(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    monkeypatch.setattr("cio.data.finnhub.get_json", lambda *a, **k: FINNHUB_RECS)
    rec = finnhub.analyst_recs("AAPL")
    assert rec["strong_buy"] == 12


# ---------------------------------------------------------------------------
# bundle.format_bundle — new FILINGS / ANALYST / EARNINGS lines
# ---------------------------------------------------------------------------

def _bundle(**extra):
    base = {
        "symbol": "AAPL", "resolved": "AAPL",
        "quote": {"close": 200.0, "change_pct": 1.0, "volume": 1000},
        "fundamentals": {"name": "Apple"},
        "ta_signals": {"rsi": "bull"}, "is_etf": False,
        "as_of": "2026-06-04T00:00:00",
        "filings": [], "analyst": None, "earnings": None,
    }
    base.update(extra)
    return base


def test_format_bundle_renders_new_blocks():
    from cio.committee.bundle import format_bundle
    b = _bundle(
        filings=[{"form": "8-K", "filed": "2026-06-03", "title": "Material Event",
                  "url": "http://x"}],
        analyst={"period": "2026-05-01", "strong_buy": 12, "buy": 20, "hold": 5,
                 "sell": 1, "strong_sell": 0},
        earnings={"date": "2026-07-31", "eps_estimate": 1.4, "eps_actual": None},
    )
    text = format_bundle(b)
    assert "FILINGS: 8-K(2026-06-03)" in text
    assert "ANALYST: strong_buy=12" in text
    assert "EARNINGS: next=2026-07-31" in text


def test_format_bundle_na_when_sources_absent():
    from cio.committee.bundle import format_bundle
    text = format_bundle(_bundle())
    assert "FILINGS: N/A (no source)" in text
    assert "ANALYST: N/A (no source)" in text
    assert "EARNINGS: N/A (no source)" in text


def test_gather_bundle_external_disabled_by_default(monkeypatch):
    """With no CIO_SEC_UA / FINNHUB_API_KEY, gather_bundle adds empty extras and
    makes no network call (sources self-gate)."""
    from cio.committee import bundle as bundle_mod
    monkeypatch.delenv("CIO_SEC_UA", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr("cio.data._http.get_json",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    filings, analyst, earnings = bundle_mod._external("AAPL", is_etf=False)
    assert filings == [] and analyst is None and earnings is None


# ---------------------------------------------------------------------------
# WMA escalation on a fresh 8-K
# ---------------------------------------------------------------------------

def test_recent_8k_detects_fresh_filing():
    from cio.watchlist_monitor.agent import _recent_8k
    today = date(2026, 6, 4)
    fresh = [{"form": "8-K", "filed": "2026-06-03"}]
    stale = [{"form": "8-K", "filed": "2026-05-01"}]
    other = [{"form": "10-Q", "filed": "2026-06-03"}]
    assert _recent_8k(fresh, today=today) is True
    assert _recent_8k(stale, today=today) is False
    assert _recent_8k(other, today=today) is False
    assert _recent_8k([], today=today) is False


_CALM_YAML = """```yaml
ticker: AAPL
company: Apple
overall_status: neutral
conviction_score: 50
recommendation: Hold
analyst_sentiment: neutral
event_importance: low
investment_thesis_change: unchanged
summary: nothing notable
```"""


def _patch_calm_ask(monkeypatch):
    async def _fake(system, user, role_key=None, **kw):
        return _CALM_YAML
    monkeypatch.setattr("cio.committee.engine.ask_role", _fake)


def test_monitor_symbol_escalates_on_fresh_8k(monkeypatch):
    """A calm LLM read (low importance, unchanged thesis) still escalates when a
    fresh 8-K is present in the bundle."""
    from cio.watchlist_monitor import monitor_symbol
    _patch_calm_ask(monkeypatch)
    filed = (date.today() - timedelta(days=1)).isoformat()
    b = _bundle(filings=[{"form": "8-K", "filed": filed, "title": "Material Event"}])
    a = _run(monitor_symbol("AAPL", bundle_fn=lambda s: b,
                            news_fn=lambda q, limit=5: []))
    assert a["event_importance"] == "low"
    assert a["investment_thesis_change"] == "unchanged"
    assert a["escalate"] is True            # fresh 8-K forces escalation


def test_monitor_symbol_no_filing_no_escalation(monkeypatch):
    """Control: same calm read, no filings -> no escalation."""
    from cio.watchlist_monitor import monitor_symbol
    _patch_calm_ask(monkeypatch)
    a = _run(monitor_symbol("AAPL", bundle_fn=lambda s: _bundle(),
                            news_fn=lambda q, limit=5: []))
    assert a["escalate"] is False
