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


def test_render_configure_named_chains():
    """The Configure tab renders named chain settings (editable links, delete
    checkbox, add box) and a chain dropdown per agent + defaults."""
    cfg = {
        "chains": {
            "premium": [
                {"service": "openai", "model": "g1", "daily_limit": 100},
                {"service": "claude", "model": "c1"},
                {"service": "nim", "model": "n1"},
            ],
            "standard": [{"service": "claude", "model": "c1"}],
        },
        "defaults": {"chain": "standard"},
        "agents": {"market": {"chain": "standard"}, "cio": {"chain": "premium"},
                   "legacyboi": {"service": "claude", "model": "x"}},
    }
    sugg = {"claude": ["c1"], "openai": ["g1"], "nim": ["n1"]}
    html = views.render_configure(cfg, 1, ["claude", "nim", "openai"], sugg)
    # chain editor
    assert "chainlink:premium:0:service" in html
    assert "chainlink:premium:2:daily_limit" in html
    assert "chain_del:premium" in html
    assert "chain_add" in html
    # per-agent chain dropdowns incl. defaults
    assert "defaults:chain" in html
    assert "agent:market:chain" in html
    assert "agent:cio:chain" in html
    # legacy inline agent gets the convert placeholder, config untouched until picked
    assert "agent:legacyboi:chain" in html
    assert "legacy inline" in html
    # old per-service agent widgets are gone
    assert "agent:market:service" not in html


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


def test_overview_run_maintenance(live, monkeypatch):
    """POST / run_maintenance backs up first, force-maintains BOTH DBs, PRG back."""
    from cio import backup as _backup
    calls = {"backup": 0, "maintain": []}
    monkeypatch.setattr(_backup, "backup_all",
                        lambda *a, **k: calls.__setitem__("backup", calls["backup"] + 1))

    def fake_maintain(db_path=None, force=False):
        calls["maintain"].append((db_path, force))
        return {"ran": True, "purged": 2, "violations": []}
    monkeypatch.setattr(dash_server.memory, "maintain", fake_maintain)

    status, resp = _post(live, "/", {"action": "run_maintenance"})
    assert status == 303
    loc = resp.getheader("Location", "")
    assert loc.startswith("/?") and "err=1" not in loc
    assert calls["backup"] == 1
    assert len(calls["maintain"]) == 2          # cio.db + committee.db
    assert all(force for _, force in calls["maintain"])


def test_configure_post_named_chain_roundtrip(live, monkeypatch, tmp_path):
    """POST /configure edits chain links, adds/deletes settings, reassigns agents,
    and refuses to delete a setting still in use."""
    import yaml as _yaml
    from cio.committee import models as _models

    p = tmp_path / "models.yaml"
    p.write_text(
        "chains:\n"
        "  premium:\n"
        "  - {service: openai, model: g1, daily_limit: 100}\n"
        "  - {service: claude, model: c1}\n"
        "  - {service: nim, model: n1}\n"
        "  spare:\n"
        "  - {service: nim, model: nx}\n"
        "defaults: {chain: premium}\n"
        "agents:\n"
        "  market: {chain: premium}\n"
        "  cio: {chain: premium}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CIO_MODELS_CONFIG", str(p))
    _models.load_config.cache_clear()

    status, resp = _post(live, "/configure", {
        "form_kind": "models",
        "chainlink:premium:0:model": "g2",            # edit a link model
        "chainlink:premium:1:daily_limit": "555",     # add a limit
        "chain_add": "newone",                        # create a setting
        "agent:market:chain": "newone",               # reassign an agent
        "chain_del:spare": "1",                       # unreferenced → deleted
        "chain_del:premium": "1",                     # referenced → refused
        "defaults:chain": "premium",
    })
    assert status == 303
    loc = resp.getheader("Location", "")
    assert "err=1" not in loc

    saved = _yaml.safe_load(p.read_text(encoding="utf-8"))
    assert saved["chains"]["premium"][0]["model"] == "g2"
    assert saved["chains"]["premium"][1]["daily_limit"] == 555
    assert "spare" not in saved["chains"]            # deleted
    assert "premium" in saved["chains"]              # delete refused (in use)
    assert len(saved["chains"]["newone"]) == 3       # template links
    assert saved["agents"]["market"]["chain"] == "newone"
    assert saved["defaults"]["chain"] == "premium"

    # the running process resolves the new assignment immediately (cache cleared)
    assert _models.resolve_chain_name("market") == "newone"
    _models.load_config.cache_clear()


def test_configure_blank_daily_limit_clears(live, monkeypatch, tmp_path):
    """Posting an EMPTY daily_limit field clears the limit from the yaml
    (regression: parse_qs dropped blank values, so present-but-blank looked
    like absent and the old limit survived the save)."""
    import yaml as _yaml
    from cio.committee import models as _models

    p = tmp_path / "models.yaml"
    p.write_text(
        "chains:\n"
        "  premium:\n"
        "  - {service: claude, model: c1, daily_limit: 200000}\n"
        "defaults: {chain: premium}\n"
        "agents: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CIO_MODELS_CONFIG", str(p))
    _models.load_config.cache_clear()

    status, resp = _post(live, "/configure", {
        "form_kind": "models",
        "chainlink:premium:0:service": "claude",
        "chainlink:premium:0:model": "c1",
        "chainlink:premium:0:daily_limit": "",   # blank = clear
    })
    assert status == 303
    assert "err=1" not in resp.getheader("Location", "")

    saved = _yaml.safe_load(p.read_text(encoding="utf-8"))
    assert "daily_limit" not in saved["chains"]["premium"][0]
    _models.load_config.cache_clear()


def test_config_reload_on_mtime_change(monkeypatch, tmp_path):
    """The bot process picks up an external save (e.g. from the dashboard
    process) without a restart: load_config re-reads when the file mtime moves."""
    import os as _os
    from cio.committee import models as _models

    p = tmp_path / "models.yaml"
    p.write_text("chains:\n  c:\n  - {service: claude, model: m, daily_limit: 1}\n",
                 encoding="utf-8")
    _models.load_config.cache_clear()
    assert _models.load_config(path=str(p))["chains"]["c"][0]["daily_limit"] == 1

    p.write_text("chains:\n  c:\n  - {service: claude, model: m}\n", encoding="utf-8")
    _os.utime(p, (_os.path.getmtime(p) + 2,) * 2)  # force mtime forward
    assert "daily_limit" not in _models.load_config(path=str(p))["chains"]["c"][0]
    _models.load_config.cache_clear()


def test_configure_partial_post_preserves_limits(live, monkeypatch, tmp_path):
    """A partial POST (e.g. scripted curl with one field) must not clear
    daily_limit values or provider token caps that were not posted."""
    import yaml as _yaml
    from cio.committee import models as _models

    p = tmp_path / "models.yaml"
    p.write_text(
        "chains:\n"
        "  premium:\n"
        "  - {service: openai, model: g1, daily_limit: 100}\n"
        "defaults: {chain: premium}\n"
        "agents: {}\n"
        "nim: {base_url: u, api_key_env: K, max_tokens: 999}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CIO_MODELS_CONFIG", str(p))
    _models.load_config.cache_clear()

    status, _ = _post(live, "/configure", {"form_kind": "models", "chain_add": "x"})
    assert status == 303

    saved = _yaml.safe_load(p.read_text(encoding="utf-8"))
    assert saved["chains"]["premium"][0]["daily_limit"] == 100   # untouched
    assert saved["nim"]["max_tokens"] == 999                     # untouched
    assert "x" in saved["chains"]
    _models.load_config.cache_clear()


def test_configure_post_converts_legacy_agent(live, monkeypatch, tmp_path):
    """An agent on a legacy inline {service, model} config converts to a named
    chain when one is picked; left on the placeholder it stays untouched."""
    import yaml as _yaml
    from cio.committee import models as _models

    p = tmp_path / "models.yaml"
    p.write_text(
        "chains:\n"
        "  std:\n"
        "  - {service: claude, model: c1}\n"
        "defaults: {chain: std}\n"
        "agents:\n"
        "  market: {service: claude, model: old-m}\n"
        "  macro: {service: nim, model: keep-me}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CIO_MODELS_CONFIG", str(p))
    _models.load_config.cache_clear()

    status, _ = _post(live, "/configure", {
        "form_kind": "models",
        "agent:market:chain": "std",   # convert
        "agent:macro:chain": "",       # placeholder → untouched
    })
    assert status == 303

    saved = _yaml.safe_load(p.read_text(encoding="utf-8"))
    assert saved["agents"]["market"] == {"chain": "std"}          # converted, legacy keys dropped
    assert saved["agents"]["macro"] == {"service": "nim", "model": "keep-me"}
    _models.load_config.cache_clear()


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
