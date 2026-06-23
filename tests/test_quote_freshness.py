"""Tests for the quote-freshness advisory the agent appends to quote tool results.

Regression cover for the cache-staleness bug: a `stale_close` (the cache holds an
OLDER bar than today's settled session) was being labelled "SETTLED <today> close",
so the agent reported a days-old price as today's. The note must now WARN and name
the real bar date, and `watchlist_prices` must surface the WORST freshness across
the snapshot, not just the first quote's.
"""
import asyncio

import cio.agent as agent


def _run(coro):
    return asyncio.run(coro)


def _text(result: dict) -> str:
    return result["content"][0]["text"]


def _q(kind, *, date, session_date, market_status="closed"):
    return {"symbol": "X", "date": date, "session_date": session_date,
            "market_status": market_status, "quote_kind": kind, "price": 10.0}


# --- _quote_freshness_note --------------------------------------------------

def test_note_none_quote_is_empty():
    assert agent._quote_freshness_note(None) == ""


def test_note_live_intraday_is_empty():
    q = _q("live_intraday", date="2026-06-05", session_date="2026-06-05", market_status="open")
    assert agent._quote_freshness_note(q) == ""


def test_note_live_postmarket_flags_after_hours_and_change():
    """An after-hours quote must NOT be silent (it isn't the regular close): the note
    names it after-hours, shows the AH price and the move vs the regular close."""
    q = {"symbol": "MU", "date": "2026-06-22", "session_date": "2026-06-22",
         "market_status": "afterhours", "quote_kind": "live_postmarket",
         "price": 1170.27, "regular_close": 1211.38, "extended_hours_change_pct": -3.39}
    note = agent._quote_freshness_note(q)
    assert "after-hours" in note
    assert "1,170.27" in note and "1,211.38" in note
    assert "-3.39%" in note
    assert "STALE" not in note and "WARNING" not in note   # it's live, not stale


def test_note_live_postmarket_delayed_warns_and_names_source():
    """A DELAYED IBKR extended-hours quote must add a caution and name the source,
    so the agent does not present a 15-20min-old price as an exact AH print."""
    q = {"symbol": "MU", "date": "2026-06-22", "session_date": "2026-06-22",
         "market_status": "afterhours", "quote_kind": "live_postmarket",
         "price": 1170.27, "regular_close": 1211.38, "extended_hours_change_pct": -3.39,
         "quote_source": "ibkr", "extended_hours_delayed": True}
    note = agent._quote_freshness_note(q)
    assert "after-hours" in note
    assert "DELAYED" in note
    assert "ibkr" in note            # source named
    assert "broker app" in note      # told to confirm exact print


def test_note_settled_close_says_settled_not_stale():
    q = _q("settled_close", date="2026-06-05", session_date="2026-06-05")
    note = agent._quote_freshness_note(q)
    assert "SETTLED" in note
    assert "2026-06-05" in note
    assert "STALE" not in note          # a settled (fresh) close must not warn


def test_note_stale_close_warns_with_real_bar_date():
    """The core regression: stale cache must WARN, naming the actual bar date and
    the missing session, and forbid presenting it as today's price."""
    q = _q("stale_close", date="2026-06-04", session_date="2026-06-05")
    note = agent._quote_freshness_note(q)
    assert "STALE" in note and "WARNING" in note
    assert "2026-06-04" in note          # the real (older) bar date
    assert "2026-06-05" in note          # the session whose data is missing
    assert "do NOT" in note              # instruction not to mislabel
    # It must NOT call a stale bar a "SETTLED <session> close".
    assert "SETTLED 2026-06-05 close" not in note


# --- t_watchlist_prices worst-case freshness --------------------------------

def _patch_snapshot(monkeypatch, quotes):
    snap = {"watchlist": "W", "id": 1, "quotes": quotes, "missing": []}
    monkeypatch.setattr(agent.watchlist, "prices", lambda *a, **k: snap)


def test_watchlist_note_uses_worst_when_one_is_stale(monkeypatch):
    """A snapshot of mostly-fresh quotes plus ONE stale quote must still WARN."""
    quotes = [
        _q("settled_close", date="2026-06-05", session_date="2026-06-05"),
        _q("stale_close",   date="2026-06-04", session_date="2026-06-05"),
        _q("live_intraday", date="2026-06-05", session_date="2026-06-05", market_status="open"),
    ]
    _patch_snapshot(monkeypatch, quotes)
    out = _text(_run(agent.t_watchlist_prices.handler({})))
    assert "STALE" in out and "2026-06-04" in out


def test_watchlist_note_settled_when_none_stale(monkeypatch):
    quotes = [_q("settled_close", date="2026-06-05", session_date="2026-06-05")]
    _patch_snapshot(monkeypatch, quotes)
    out = _text(_run(agent.t_watchlist_prices.handler({})))
    assert "SETTLED" in out and "STALE" not in out


def test_watchlist_note_empty_when_all_live(monkeypatch):
    quotes = [_q("live_intraday", date="2026-06-05", session_date="2026-06-05", market_status="open")]
    _patch_snapshot(monkeypatch, quotes)
    out = _text(_run(agent.t_watchlist_prices.handler({})))
    assert "STALE" not in out and "SETTLED" not in out


def test_watchlist_no_active_list(monkeypatch):
    monkeypatch.setattr(agent.watchlist, "prices",
                        lambda *a, **k: {"watchlist": None, "id": None, "quotes": [], "missing": []})
    out = _text(_run(agent.t_watchlist_prices.handler({})))
    assert "No active watchlist" in out
