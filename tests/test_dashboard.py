"""Tests for the dev dashboard: HTML rendering, routing, and the auth gate.

The handler's data sources are monkeypatched so this stays a pure HTTP/render
test (the DB layer is covered by test_transcript.py / test_memcore.py).
"""
from __future__ import annotations

import sys
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cio import db, memory
from cio.dashboard import server as dash_server
from cio.dashboard import views


# ---------------------------------------------------------------------------
# Telegram turn capture (conv_turns)
# ---------------------------------------------------------------------------

def test_log_turn_roundtrip(tmp_path):
    dbp = tmp_path / "cio.db"
    db.init(dbp)
    memory.log_turn(5, "sess", "how's my portfolio?", "Up 3%.", db_path=dbp)
    hist = memory.conv_history(chat_id=5, db_path=dbp)
    roles = {h["role"]: h["content"] for h in hist}
    assert roles["user"] == "how's my portfolio?"
    assert roles["assistant"] == "Up 3%."


def test_log_turn_skipped_at_level3(tmp_path, monkeypatch):
    monkeypatch.setenv("CIO_CAPTURE_LEVEL", "3")
    dbp = tmp_path / "cio.db"
    db.init(dbp)
    memory.log_turn(5, "sess", "hi", "hello", db_path=dbp)
    assert memory.conv_history(chat_id=5, db_path=dbp) == []


# ---------------------------------------------------------------------------
# View rendering (pure functions)
# ---------------------------------------------------------------------------

def test_render_escapes_user_content():
    turns = [{"chat_id": 1, "role": "user", "content": "<script>x</script>", "ts": "t"}]
    html = views.render_telegram(turns, level=1)
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_playbooks_promote_button_only_for_chat_scope():
    """The /playbooks page offers 'Promote to global' for chat-scoped playbooks only."""
    rows = [
        {"id": 2, "scope": "global", "name": "mre", "steps": "s", "hits": 3, "created_at": "t"},
        {"id": 5, "scope": "chat:8535885767", "name": "mre", "steps": "s", "hits": 0, "created_at": "t"},
    ]
    html = views.render_playbooks(rows, level=1)
    assert html.count("value='promote'") == 1        # exactly the chat-scoped row
    assert "Promote to global" in html
    assert "pid' value='5'" in html                  # promote targets the chat copy


def test_render_configure_detailed_log_toggle():
    """The Configure tab exposes an enable/disable toggle for detailed history."""
    cfg = {"agents": {}, "defaults": {}}
    sugg = {"claude": []}
    off = views.render_configure(cfg, 1, ["claude"], sugg, detailed_log=False)
    assert "Detailed conversation history" in off
    assert "value='detailed_log'" in off and "Enable detailed history" in off
    on = views.render_configure(cfg, 1, ["claude"], sugg, detailed_log=True)
    assert "Disable detailed history" in on and "/detailed" in on
    locked = views.render_configure(cfg, 1, ["claude"], sugg, detailed_locked_by_env=True)
    assert "Locked by" in locked and "CIO_DETAILED_LOG" in locked


def test_render_committee_run_shows_sent_and_returned():
    calls = [{
        "run_id": "r1", "symbol": "AAPL", "role_key": "risk", "service": "openai",
        "model": "gpt-x", "system_prompt": "SYSTEXT", "user_prompt": "USRTEXT",
        "response": "ANSTEXT", "tokens": 5, "ts": "t",
    }]
    html = views.render_committee_run("r1", calls, level=1)
    assert "SYSTEXT" in html and "USRTEXT" in html and "ANSTEXT" in html
    assert "SENT" in html and "RETURNED" in html


def test_render_portfolio_view_and_colors():
    summ = {"positions": 1, "market_value": 1500.0, "cost_basis": 1000.0,
            "unrealized_pl": 500.0, "unrealized_pct": 50.0,
            "realized_pl": -20.0, "dividends": 10.0}
    positions = [{"symbol": "AAPL", "quantity": 10, "avg_cost": 100.0,
                  "cost_basis": 1000.0, "last_price": 150.0, "market_value": 1500.0,
                  "unrealized_pl": 500.0, "unrealized_pct": 50.0}]
    realized = [{"symbol": "TSLA", "realized_pl": -20.0, "dividends": 10.0,
                 "total": -10.0}]
    html = views.render_portfolio(summ, positions, realized, level=1)
    assert "AAPL" in html and "TSLA" in html
    assert "td.up" in html and "td.down" in html  # green=up / red=down css
    assert "class='num up'" in html   # positive unrealized P&L coloured up
    assert "class='num down'" in html  # negative realized P&L coloured down


def test_render_portfolio_handles_missing_price():
    """A position with no last price must render blank, not crash."""
    summ = {"positions": 1, "market_value": 0, "cost_basis": 1000.0,
            "unrealized_pl": 0, "unrealized_pct": 0,
            "realized_pl": 0, "dividends": 0}
    positions = [{"symbol": "AAPL", "quantity": 10, "avg_cost": 100.0,
                  "cost_basis": 1000.0, "last_price": None, "market_value": None,
                  "unrealized_pl": None, "unrealized_pct": None}]
    html = views.render_portfolio(summ, positions, [], level=1)
    assert "AAPL" in html


def test_render_empty_response_labelled():
    calls = [{
        "run_id": "r1", "symbol": "AAPL", "role_key": "risk", "service": "openai",
        "model": "", "system_prompt": "s", "user_prompt": "u", "response": "",
        "tokens": 0, "ts": "t",
    }]
    html = views.render_committee_run("r1", calls, level=1)
    assert "fell through" in html


# ---------------------------------------------------------------------------
# HTTP routing + auth (live ephemeral server)
# ---------------------------------------------------------------------------

@pytest.fixture
def live(monkeypatch):
    """Start the handler on an ephemeral port with stubbed data sources."""
    monkeypatch.setattr(dash_server.usage, "recent",
                        lambda *a, **k: [{"service": "openai", "day": "2026-06-01", "tokens": 42}])
    monkeypatch.setattr(dash_server.transcript, "list_runs",
                        lambda *a, **k: [{"run_id": "r1", "symbol": "AAPL",
                                          "started": "t", "calls": 1, "tokens": 9}])
    monkeypatch.setattr(dash_server.transcript, "get_run",
                        lambda run_id, **k: [{"run_id": run_id, "symbol": "AAPL",
                                              "role_key": "risk", "service": "openai",
                                              "model": "gpt-x", "system_prompt": "S",
                                              "user_prompt": "U", "response": "A",
                                              "tokens": 9, "ts": "t"}])
    monkeypatch.setattr(dash_server.memory, "conv_history",
                        lambda *a, **k: [{"id": 1, "chat_id": 5, "session_id": None,
                                          "role": "user", "content": "hi", "ts": "t"}])
    monkeypatch.setattr(dash_server.memory, "list_subscribers",
                        lambda *a, **k: [{"chat_id": 12345, "updated_at": "2026-06-03 14:00:00"}])

    class _DF:  # stand-in for the pandas frames the portfolio funcs return
        def __init__(self, rows): self._rows = rows
        def to_dict(self, _orient): return self._rows
    monkeypatch.setattr(dash_server.portfolio, "summary",
                        lambda *a, **k: {"positions": 1, "market_value": 1500.0,
                                         "cost_basis": 1000.0, "unrealized_pl": 500.0,
                                         "unrealized_pct": 50.0, "realized_pl": 0.0,
                                         "dividends": 0.0})
    monkeypatch.setattr(dash_server.portfolio, "positions",
                        lambda *a, **k: _DF([{"symbol": "AAPL", "quantity": 10,
                                              "avg_cost": 100.0, "cost_basis": 1000.0,
                                              "last_price": 150.0, "market_value": 1500.0,
                                              "unrealized_pl": 500.0, "unrealized_pct": 50.0}]))
    monkeypatch.setattr(dash_server.portfolio, "realized_pl",
                        lambda *a, **k: _DF([]))

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), dash_server._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield port
    httpd.shutdown()
    httpd.server_close()


def _get(port, path, headers=None):
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    conn.close()
    return resp.status, body, resp


def test_routes_render(live):
    for path in ("/", "/usage", "/telegram", "/detailed", "/subscribers", "/committee",
                 "/committee/r1", "/portfolio"):
        status, body, _ = _get(live, path)
        assert status == 200, path
        assert "CIO dev dashboard" in body
    # subscribers page lists the opted-in chat id
    _, sub_body, _ = _get(live, "/subscribers")
    assert "12345" in sub_body
    # committee run shows the captured exchange
    _, run_body, _ = _get(live, "/committee/r1")
    assert "RETURNED" in run_body


def _post(port, path, fields):
    body = "&".join(f"{k}={v}" for k, v in fields.items())
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("POST", path, body=body,
                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status, resp


def test_portfolio_set_price_redirects(live, monkeypatch):
    calls = {}
    monkeypatch.setattr(dash_server.portfolio, "set_price",
                        lambda sym, close, date=None, **k: calls.update(
                            symbol=sym, close=close))
    status, resp = _post(live, "/portfolio",
                         {"action": "set_price", "symbol": "AAPL", "close": "150"})
    assert status == 303  # PRG redirect
    assert calls == {"symbol": "AAPL", "close": 150.0}
    assert "/portfolio?" in resp.getheader("Location", "")


def test_unknown_route_404(live):
    status, _, _ = _get(live, "/nope")
    assert status == 404


def test_token_gate(monkeypatch, live):
    monkeypatch.setenv("CIO_DASH_TOKEN", "secret")
    # No token → 401
    status, _, _ = _get(live, "/")
    assert status == 401
    # Correct token → 200 and sets a cookie
    status, _, resp = _get(live, "/?token=secret")
    assert status == 200
    assert "cio_dash=secret" in resp.getheader("Set-Cookie", "")
    # Cookie alone now authorizes
    status, _, _ = _get(live, "/", headers={"Cookie": "cio_dash=secret"})
    assert status == 200
