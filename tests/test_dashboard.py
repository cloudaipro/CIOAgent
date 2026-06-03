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


def test_render_committee_run_shows_sent_and_returned():
    calls = [{
        "run_id": "r1", "symbol": "AAPL", "role_key": "risk", "service": "openai",
        "model": "gpt-x", "system_prompt": "SYSTEXT", "user_prompt": "USRTEXT",
        "response": "ANSTEXT", "tokens": 5, "ts": "t",
    }]
    html = views.render_committee_run("r1", calls, level=1)
    assert "SYSTEXT" in html and "USRTEXT" in html and "ANSTEXT" in html
    assert "SENT" in html and "RETURNED" in html


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
    for path in ("/", "/usage", "/telegram", "/subscribers", "/committee", "/committee/r1"):
        status, body, _ = _get(live, path)
        assert status == 200, path
        assert "CIO dev dashboard" in body
    # subscribers page lists the opted-in chat id
    _, sub_body, _ = _get(live, "/subscribers")
    assert "12345" in sub_body
    # committee run shows the captured exchange
    _, run_body, _ = _get(live, "/committee/r1")
    assert "RETURNED" in run_body


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
