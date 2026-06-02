"""Localhost dev-dashboard HTTP server (stdlib only).

Read-only. Binds 127.0.0.1 by default — it serves the operator's own financial
data, so it must never face the network. An optional ``CIO_DASH_TOKEN`` adds a
shared-secret gate (``?token=…`` once, then a session cookie) for the cautious;
with no token set and a loopback bind, no auth is required.

Routes:
  /                     overview
  /usage                token usage per service per day
  /telegram             Telegram conversation history
  /memory               per-agent / per-chat memory contents (debug)
  /committee            list committee runs
  /committee/<run_id>   full sent/returned transcript for one run
"""
from __future__ import annotations

import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from cio import db, devcapture, memory
from cio.committee import agent_memory, transcript, usage
from . import views

log = logging.getLogger("cio.dashboard")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


class _Handler(BaseHTTPRequestHandler):
    server_version = "CIODash/1.0"

    # ---- helpers -----------------------------------------------------------
    def _send(self, html: str, status: int = 200, set_cookie: str | None = None) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, query: dict) -> tuple[bool, str | None]:
        """Return (ok, set_cookie). No token configured → always ok."""
        token = os.getenv("CIO_DASH_TOKEN")
        if not token:
            return True, None
        if query.get("token", [None])[0] == token:
            return True, f"cio_dash={token}; Path=/; HttpOnly; SameSite=Strict"
        cookies = self.headers.get("Cookie", "")
        if f"cio_dash={token}" in cookies:
            return True, None
        return False, None

    def log_message(self, fmt, *args):  # quieter than default stderr spew
        log.debug("%s - %s", self.address_string(), fmt % args)

    @staticmethod
    def _memory_sections() -> list:
        """Gather memory contents from both stores for the memory tab.

        Portfolio/conversation memory lives in db.DB_PATH; committee agents keep
        their own isolated notes in agent_memory.DB_PATH.
        """
        sections = []
        for label, dbp in (
            ("Conversation / portfolio (chat:* · global)", db.DB_PATH),
            ("Committee agents (committee:<role>)", agent_memory.DB_PATH),
        ):
            scopes = []
            for s in memory.list_scopes(db_path=dbp):
                notes = memory.list_notes(s["scope"], limit=200, db_path=dbp)
                scopes.append({"scope": s["scope"], "count": s["count"], "notes": notes})
            sections.append({"label": label, "scopes": scopes})
        return sections

    # ---- routing -----------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        ok, set_cookie = self._authorized(query)
        if not ok:
            self._send("<h1>401</h1><p>token required: append ?token=YOUR_TOKEN</p>",
                       status=401)
            return

        level = devcapture.level()
        try:
            if path == "/":
                html = views.render_overview(
                    usage.recent(days=1), transcript.list_runs(10),
                    memory.conv_history(limit=10), level)
            elif path == "/usage":
                html = views.render_usage(usage.recent(days=30), level)
            elif path == "/telegram":
                html = views.render_telegram(memory.conv_history(limit=200), level)
            elif path == "/memory":
                html = views.render_memory(self._memory_sections(), level)
            elif path == "/committee":
                html = views.render_committee_list(transcript.list_runs(100), level)
            elif path.startswith("/committee/"):
                run_id = path.split("/committee/", 1)[1]
                html = views.render_committee_run(run_id, transcript.get_run(run_id), level)
            else:
                self._send("<h1>404</h1>", status=404, set_cookie=set_cookie)
                return
        except Exception as exc:  # a dashboard read must never 500 the operator
            log.warning("dashboard render failed for %s: %s", path, exc)
            self._send(f"<h1>500</h1><pre>{views.esc(exc)}</pre>", status=500,
                       set_cookie=set_cookie)
            return

        self._send(html, set_cookie=set_cookie)


def serve(host: str | None = None, port: int | None = None) -> None:
    """Start the blocking dev-dashboard server (Ctrl-C to stop)."""
    host = host or os.getenv("CIO_DASH_HOST", DEFAULT_HOST)
    port = port or int(os.getenv("CIO_DASH_PORT", str(DEFAULT_PORT)))
    httpd = ThreadingHTTPServer((host, port), _Handler)
    auth = "token-gated" if os.getenv("CIO_DASH_TOKEN") else "no auth (loopback)"
    log.info("CIO dev dashboard on http://%s:%d  (%s, capture level %d)",
             host, port, auth, devcapture.level())
    if host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("CIO_DASH_TOKEN"):
        log.warning("dashboard bound to %s with NO token — it exposes your data. "
                    "Set CIO_DASH_TOKEN or bind 127.0.0.1.", host)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log.info("dashboard stopped")
    finally:
        httpd.server_close()
