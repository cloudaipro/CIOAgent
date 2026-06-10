"""Localhost dev-dashboard HTTP server (stdlib only).

Read-only. Binds 127.0.0.1 by default — it serves the operator's own financial
data, so it must never face the network. An optional ``CIO_DASH_TOKEN`` adds a
shared-secret gate (``?token=…`` once, then a session cookie) for the cautious;
with no token set and a loopback bind, no auth is required.

Routes:
  /                     overview
  /usage                token usage per service per day
  /telegram             Telegram conversation history
  /detailed             detailed conversation history (full prompts/responses; GET/POST delete)
  /subscribers          chats opted in to the digest + watchlist briefing
  /memory               per-agent / per-chat memory contents (debug)
  /playbooks            saved reusable procedures (GET renders, POST deletes one)
  /econ                 high-impact economic events + alert status (GET/POST delete)
  /sanitizer            figures-sanitizer audit trail (what was stripped/rejected)
  /committee            list committee runs
  /committee/<run_id>   full sent/returned transcript for one run
  /watchlist            manage watchlists (GET renders, POST mutates
                        create/activate/rename/delete/add/remove/import)
  /portfolio            portfolio view + management (GET renders positions/P&L,
                        POST mutates set_price/refresh_live/import_txns/import_prices)
"""
from __future__ import annotations

import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, urlencode

from cio import convlog, db, devcapture, econ_calendar, memory, portfolio, version, watchlist
from cio.committee import agent_memory, models, sanitizer_log, transcript, usage
from . import views

log = logging.getLogger("cio.dashboard")


def _runtime_info() -> dict:
    """Runtime health for the overview strip: running vs on-disk code version,
    stale-process warning, and last maintenance's invariant violations.
    Best-effort — the overview must render even if git/meta are unavailable."""
    try:
        import json
        boot = version.boot_info()
        raw = memory.get_meta("last_invariant_violations")
        violations = json.loads(raw) if raw else []
        return {
            "boot_version": boot["version"],
            "boot_time": boot["time"],
            "boot_pid": boot["pid"],
            "repo_version": version.describe(),
            "stale": version.stale_process_check(),
            "violations": violations,
        }
    except Exception:
        log.warning("runtime info unavailable", exc_info=True)
        return {}

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
        for store, label, dbp in (
            ("conversational", "Conversation / portfolio (chat:* · global)", db.DB_PATH),
            ("committee", "Committee agents (committee:<role>)", agent_memory.DB_PATH),
        ):
            scopes = []
            for s in memory.list_scopes(db_path=dbp):
                notes = memory.list_notes(s["scope"], limit=200, db_path=dbp)
                scopes.append({"scope": s["scope"], "count": s["count"], "notes": notes})
            sections.append({"store": store, "label": label, "scopes": scopes})
        return sections

    @staticmethod
    def _db_for_store(store: str):
        """Route a store id to its db. Unknown → conversational (safe default)."""
        return agent_memory.DB_PATH if store == "committee" else db.DB_PATH

    @staticmethod
    def _db_for_scope(scope: str):
        """Committee scopes live in committee.db; everything else in cio.db."""
        return agent_memory.DB_PATH if (scope or "").startswith("committee:") else db.DB_PATH

    def _watchlist_view(self, query: dict, level: int) -> str:
        """Gather watchlist data for the GET render. ?q= searches; ?wl= selects a
        list (else the active one is shown); ?msg=/?err= carry the post-redirect
        flash."""
        q = query.get("q", [""])[0].strip()
        flash = query.get("msg", [""])[0]
        flash_err = query.get("err", ["0"])[0] == "1"
        wls = watchlist.search(q) if q else watchlist.list_watchlists()
        selected = None
        sel_id = query.get("wl", [None])[0]
        if sel_id and sel_id.isdigit():
            selected = watchlist.get(int(sel_id))
        if selected is None and not q:
            selected = watchlist.active()
        return views.render_watchlist(
            wls, selected, level, search_q=q, flash=flash, flash_err=flash_err,
            nasdaq_index=watchlist.NASDAQ_INDEX)

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
                    memory.conv_history(limit=10), level,
                    runtime=_runtime_info())
            elif path == "/usage":
                html = views.render_usage(usage.recent(days=30), level)
            elif path == "/telegram":
                sel_day = query.get("day", [None])[0]
                turns = (memory.conv_history_on_day(sel_day) if sel_day
                         else memory.conv_history(limit=200))
                html = views.render_telegram(
                    turns, level, days=memory.conv_days(), selected_day=sel_day,
                    flash=query.get("msg", [""])[0],
                    flash_err=query.get("err", ["0"])[0] == "1")
            elif path == "/detailed":
                sel_day = query.get("day", [None])[0]
                html = views.render_detailed(
                    convlog.list_days(), sel_day,
                    convlog.read_day(sel_day) if sel_day else None,
                    convlog.enabled(), level,
                    flash=query.get("msg", [""])[0],
                    flash_err=query.get("err", ["0"])[0] == "1")
            elif path == "/subscribers":
                html = views.render_subscribers(memory.list_subscribers(), level)
            elif path == "/memory":
                html = views.render_memory(
                    self._memory_sections(), level,
                    flash=query.get("msg", [""])[0],
                    flash_err=query.get("err", ["0"])[0] == "1")
            elif path == "/playbooks":
                html = views.render_playbooks(
                    memory.list_all_playbooks(), level,
                    flash=query.get("msg", [""])[0],
                    flash_err=query.get("err", ["0"])[0] == "1")
            elif path == "/econ":
                econ_calendar.seed_nfp(months_ahead=2)
                html = views.render_econ_events(
                    econ_calendar.list_all(), level,
                    flash=query.get("msg", [""])[0],
                    flash_err=query.get("err", ["0"])[0] == "1")
            elif path == "/sanitizer":
                html = views.render_sanitizer(sanitizer_log.recent(200), level)
            elif path == "/watchlist":
                html = self._watchlist_view(query, level)
            elif path == "/portfolio":
                html = views.render_portfolio(
                    portfolio.summary(),
                    portfolio.positions().to_dict("records"),
                    portfolio.realized_pl().to_dict("records"),
                    level,
                    flash=query.get("msg", [""])[0],
                    flash_err=query.get("err", ["0"])[0] == "1")
            elif path == "/committee":
                html = views.render_committee_list(
                    transcript.list_runs(100), level,
                    flash=query.get("msg", [""])[0],
                    flash_err=query.get("err", ["0"])[0] == "1")
            elif path == "/configure":
                from .. import logsetup
                from . import settings as _settings
                _logfile = logsetup.current_log_file()
                html = views.render_configure(
                    models.load_config(), level, models.SERVICES,
                    models.model_catalog(), path=models.config_path(),
                    flash=query.get("msg", [""])[0],
                    flash_err=query.get("err", ["0"])[0] == "1",
                    log_to_file=logsetup.file_logging_enabled(),
                    log_file=str(_logfile) if _logfile else "",
                    log_dir=str(logsetup.log_dir()),
                    log_locked_by_env=os.getenv("CIO_LOG_TO_FILE") is not None,
                    detailed_log=convlog.enabled(),
                    detailed_locked_by_env=convlog.locked_by_env())
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

    def _redirect(self, location: str, set_cookie: str | None = None) -> None:
        """303 See Other — Post/Redirect/Get so a browser refresh after a mutation
        doesn't resubmit the form."""
        self.send_response(303)
        self.send_header("Location", location)
        if set_cookie is not None:
            self.send_header("Set-Cookie", set_cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        """The dashboard's write paths: /watchlist and /portfolio mutations. Each
        form posts an `action` field; on success/failure we redirect back to the
        same page with a flash message (PRG pattern). Same auth gate as GET."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        ok, set_cookie = self._authorized(parse_qs(parsed.query))
        if not ok:
            self._send("<h1>401</h1><p>token required</p>", status=401)
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        form = parse_qs(self.rfile.read(length).decode("utf-8")) if length else {}

        if path == "/watchlist":
            self._watchlist_post(form, set_cookie)
        elif path == "/portfolio":
            self._portfolio_post(form, set_cookie)
        elif path == "/memory":
            self._memory_post(form, set_cookie)
        elif path == "/committee":
            self._committee_post(form, set_cookie)
        elif path == "/telegram":
            self._telegram_post(form, set_cookie)
        elif path == "/detailed":
            self._detailed_post(form, set_cookie)
        elif path == "/playbooks":
            self._playbooks_post(form, set_cookie)
        elif path == "/econ":
            self._econ_post(form, set_cookie)
        elif path == "/configure":
            self._configure_post(form, set_cookie)
        else:
            self._send("<h1>404</h1>", status=404)

    def _telegram_post(self, form: dict, set_cookie: str | None) -> None:
        """Telegram page mutation. Only action: wipe_day day=YYYY-MM-DD — delete one
        local day's conversation turns. Irreversible; confirmed client-side. PRG back."""
        action = form.get("action", [""])[0].strip()
        day = form.get("day", [""])[0].strip()
        msg, err = "", False
        try:
            if action == "wipe_day":
                if not day:
                    raise ValueError("missing day")
                n = memory.delete_turns_on_day(day)
                msg = f"deleted Telegram history for {day} ({n} turn(s))"
            else:
                msg, err = f"unknown action {action!r}", True
        except Exception as exc:  # never 500 the operator
            log.warning("telegram POST %s failed: %s", action, exc)
            msg, err = f"error: {exc}", True

        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/telegram?" + urlencode(params), set_cookie)

    def _detailed_post(self, form: dict, set_cookie: str | None) -> None:
        """Detailed-history page mutation. Only action: wipe_day day=YYYY-MM-DD —
        delete one day's detailed-history log file. Irreversible; confirmed. PRG back."""
        action = form.get("action", [""])[0].strip()
        day = form.get("day", [""])[0].strip()
        msg, err = "", False
        try:
            if action == "wipe_day":
                if not day:
                    raise ValueError("missing day")
                ok = convlog.delete_day(day)
                msg = (f"deleted detailed history for {day}" if ok
                       else f"no detailed history file for {day}")
                err = not ok
            else:
                msg, err = f"unknown action {action!r}", True
        except Exception as exc:  # never 500 the operator
            log.warning("detailed POST %s failed: %s", action, exc)
            msg, err = f"error: {exc}", True

        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/detailed?" + urlencode(params), set_cookie)

    def _playbooks_post(self, form: dict, set_cookie: str | None) -> None:
        """Playbooks page mutation. Actions: delete pid=<id> (remove one saved
        playbook) and promote pid=<id> (move a chat-scoped playbook to global).
        Confirmed client-side. PRG back."""
        action = form.get("action", [""])[0].strip()
        pid = form.get("pid", [""])[0].strip()
        msg, err = "", False
        try:
            if action == "delete":
                if not pid.isdigit():
                    raise ValueError("missing/invalid pid")
                n = memory.delete_playbook(int(pid))
                msg = f"deleted playbook ({n} row(s))" if n else "playbook not found"
                err = n == 0
            elif action == "promote":
                if not pid.isdigit():
                    raise ValueError("missing/invalid pid")
                res = memory.promote_playbook(int(pid))
                msg = (f"promoted {res['name']!r} to global"
                       if res["promoted"] else f"{res['name']!r} is already global")
            else:
                msg, err = f"unknown action {action!r}", True
        except Exception as exc:  # never 500 the operator
            log.warning("playbooks POST %s failed: %s", action, exc)
            msg, err = f"error: {exc}", True

        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/playbooks?" + urlencode(params), set_cookie)

    def _econ_post(self, form: dict, set_cookie: str | None) -> None:
        """Econ-events page mutation. Only action: delete eid=<id>. PRG back."""
        action = form.get("action", [""])[0].strip()
        eid = form.get("eid", [""])[0].strip()
        msg, err = "", False
        try:
            if action == "delete":
                if not eid.isdigit():
                    raise ValueError("missing/invalid eid")
                n = econ_calendar.delete_event(int(eid))
                msg = f"deleted event ({n} row(s))" if n else "event not found"
                err = n == 0
            else:
                msg, err = f"unknown action {action!r}", True
        except Exception as exc:  # never 500 the operator
            log.warning("econ POST %s failed: %s", action, exc)
            msg, err = f"error: {exc}", True

        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/econ?" + urlencode(params), set_cookie)

    def _committee_post(self, form: dict, set_cookie: str | None) -> None:
        """Committee page mutation. Only action: wipe_runs — delete every captured
        committee run (transcript). Irreversible; confirmed client-side. PRG back."""
        action = form.get("action", [""])[0].strip()
        msg, err = "", False
        try:
            if action == "wipe_runs":
                n = transcript.clear_all()
                msg = f"deleted all committee runs ({n} call-row(s))"
            else:
                msg, err = f"unknown action {action!r}", True
        except Exception as exc:  # never 500 the operator
            log.warning("committee POST %s failed: %s", action, exc)
            msg, err = f"error: {exc}", True

        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/committee?" + urlencode(params), set_cookie)

    def _memory_post(self, form: dict, set_cookie: str | None) -> None:
        """Memory page mutations (all irreversible; confirmed client-side):
          wipe_agents          — delete every committee agent's notes (committee.db)
          wipe_store store=…    — delete every note in one store (conversational|committee)
          wipe_scope scope=…    — delete one scope's notes (db routed by scope prefix)
        PRG back to /memory with a flash."""
        def f(name: str) -> str:
            return form.get(name, [""])[0].strip()

        action = f("action")
        msg, err = "", False
        try:
            if action == "wipe_agents":
                n = memory.clear_notes(db_path=agent_memory.DB_PATH)
                msg = f"deleted all agent memory ({n} note(s))"
            elif action == "wipe_store":
                store = f("store")
                n = memory.clear_notes(db_path=self._db_for_store(store))
                msg = f"deleted all {store or 'conversational'} memory ({n} note(s))"
            elif action == "wipe_scope":
                scope = f("scope")
                if not scope:
                    raise ValueError("missing scope")
                n = memory.clear_notes(scope=scope, db_path=self._db_for_scope(scope))
                msg = f"deleted scope {scope!r} ({n} note(s))"
            else:
                msg, err = f"unknown action {action!r}", True
        except Exception as exc:  # never 500 the operator
            log.warning("memory POST %s failed: %s", action, exc)
            msg, err = f"error: {exc}", True

        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/memory?" + urlencode(params), set_cookie)

    @staticmethod
    def _set_service_model(node: dict, service: str, model: str) -> None:
        """Apply a service combo + model box onto a config node. Empty model → null
        (some agents legitimately run with model: null)."""
        if service:
            node["service"] = service
        node["model"] = model if model else None

    def _configure_post(self, form: dict, set_cookie: str | None) -> None:
        """Save the committee_models.yaml edits from the Configure tab.

        Reads the live doc (round-trip, comments preserved), assigns the posted
        values onto the existing structure, writes it back, and clears the model
        config cache so the next committee run picks up the change. PRG back."""
        def f(name: str) -> str:
            return form.get(name, [""])[0].strip()

        # The Logging toggle posts to the same /configure path but with
        # form_kind=logging — handle it separately from the model-YAML save.
        if f("form_kind") == "logging":
            self._logging_post(f("log_to_file") == "1", set_cookie)
            return
        if f("form_kind") == "detailed_log":
            self._detailed_log_post(f("detailed_log") == "1", set_cookie)
            return

        msg, err = "", False
        try:
            doc = models.read_doc()
            if isinstance(doc.get("defaults"), dict):
                self._set_service_model(
                    doc["defaults"], f("defaults:service"), f("defaults:model"))

            for key, acfg in (doc.get("agents") or {}).items():
                if not isinstance(acfg, dict):
                    continue
                if "chain" in acfg:
                    for i, link in enumerate(acfg.get("chain") or []):
                        if not isinstance(link, dict):
                            continue
                        svc = f(f"chain:{key}:{i}:service")
                        mdl = f(f"chain:{key}:{i}:model")
                        if svc:
                            link["service"] = svc
                        if mdl:
                            link["model"] = mdl
                        dl = f(f"chain:{key}:{i}:daily_limit")
                        if dl:
                            link["daily_limit"] = int(dl)
                        elif "daily_limit" in link:
                            del link["daily_limit"]
                else:
                    self._set_service_model(
                        acfg, f(f"agent:{key}:service"), f(f"agent:{key}:model"))

            for prov, fields in (
                ("nim", ("base_url", "api_key_env", "max_tokens")),
                ("openai", ("base_url", "api_key_env", "token_param", "max_tokens")),
                ("claude", ("max_thinking_tokens",)),
            ):
                node = doc.get(prov)
                if not isinstance(node, dict):
                    continue
                for field in fields:
                    val = f(f"provider:{prov}:{field}")
                    if val == "":
                        if field in ("max_tokens", "max_thinking_tokens") and field in node:
                            del node[field]  # blank cap → drop, fall back to default
                        continue
                    node[field] = int(val) if field in ("max_tokens", "max_thinking_tokens") else val

            # Model catalog: rebuild each service's list from its surviving rows
            # (blank row = removed) plus any newly-added names (comma/newline split).
            catalog: dict = {}
            for svc in models.SERVICES:
                rows = sorted(
                    (int(k.split(":")[2]), v[0].strip())
                    for k, v in form.items() if k.startswith(f"catalog:{svc}:")
                )
                names = [v for _, v in rows if v]
                added = form.get(f"catalog_add:{svc}", [""])[0]
                for raw in added.replace("\n", ",").split(","):
                    name = raw.strip()
                    if name:
                        names.append(name)
                deduped: list[str] = []
                for n in names:
                    if n not in deduped:
                        deduped.append(n)
                catalog[svc] = deduped
            if any(catalog.values()):
                doc["model_catalog"] = catalog

            models.write_doc(doc)
            msg = "saved committee_models.yaml — applies to the next run"
        except Exception as exc:  # never 500 the operator on a bad form
            log.warning("configure POST failed: %s", exc)
            msg, err = f"error: {exc}", True

        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/configure?" + urlencode(params), set_cookie)

    def _detailed_log_post(self, enabled: bool, set_cookie: str | None) -> None:
        """Persist the detailed-conversation-history choice (shared via
        dashboard_settings.json; the bot process reads it per call). Env override,
        if set, wins and locks it. PRG back."""
        from . import settings as _settings
        msg, err = "", False
        if os.getenv("CIO_DETAILED_LOG") is not None:
            msg, err = "detailed logging is locked by the CIO_DETAILED_LOG env var", True
        else:
            try:
                _settings.set_detailed_log(enabled)
                msg = ("detailed conversation history ON" if enabled
                       else "detailed conversation history OFF")
            except Exception as exc:
                log.warning("detailed-log toggle failed: %s", exc)
                msg, err = f"error: {exc}", True
        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/configure?" + urlencode(params), set_cookie)

    def _logging_post(self, enabled: bool, set_cookie: str | None) -> None:
        """Persist the file-logging choice and apply it live to this process's
        root logger (no restart needed). Env override, if set, wins and locks it."""
        from .. import logsetup
        from . import settings as _settings
        msg, err = "", False
        if os.getenv("CIO_LOG_TO_FILE") is not None:
            msg, err = "file logging is locked by the CIO_LOG_TO_FILE env var", True
        else:
            try:
                _settings.set_log_to_file(enabled)
                path = logsetup.apply_file_logging(enabled)
                msg = (f"file logging ON → {path}" if enabled
                       else "file logging OFF (console only)")
            except Exception as exc:
                log.warning("logging toggle failed: %s", exc)
                msg, err = f"error: {exc}", True
        params = {"msg": msg}
        if err:
            params["err"] = "1"
        self._redirect("/configure?" + urlencode(params), set_cookie)

    def _watchlist_post(self, form: dict, set_cookie: str | None) -> None:
        def f(name: str) -> str:
            return form.get(name, [""])[0].strip()

        action = f("action")
        wl_raw = f("wl_id")
        wl_id = int(wl_raw) if wl_raw.isdigit() else None
        sym = f("symbol").upper()
        msg, err = "", False
        try:
            if action == "create":
                wl_id = watchlist.create(f("name"))
                msg = f"created {f('name')!r}"
            elif action == "activate":
                watchlist.set_active(wl_id)
                msg = "activated"
            elif action == "rename":
                watchlist.rename(wl_id, f("name"))
                msg = "renamed"
            elif action == "delete":
                watchlist.delete(wl_id)
                wl_id, msg = None, "deleted"
            elif action == "add_symbol":
                added = watchlist.add_symbol(wl_id, sym)
                msg = f"added {sym}" if added else f"{sym} already present"
            elif action == "remove_symbol":
                watchlist.remove_symbol(wl_id, sym)
                msg = f"removed {sym}"
            elif action == "import_csv":
                n = watchlist.import_csv(wl_id, text=f("csv_text"))
                msg = f"imported {n} new symbol(s)"
            elif action == "reorder":
                order = [s for s in f("order").split(",") if s]
                watchlist.reorder(wl_id, order)
                msg = "reordered"
            else:
                msg, err = f"unknown action {action!r}", True
        except watchlist.WatchlistError as exc:
            msg, err = str(exc), True
        except Exception as exc:  # never 500 the operator on a bad form
            log.warning("watchlist POST %s failed: %s", action, exc)
            msg, err = f"error: {exc}", True

        params = {}
        if wl_id is not None:
            params["wl"] = wl_id
        if msg:
            params["msg"] = msg
        if err:
            params["err"] = "1"
        target = "/watchlist" + (("?" + urlencode(params)) if params else "")
        self._redirect(target, set_cookie)

    def _portfolio_post(self, form: dict, set_cookie: str | None) -> None:
        """Portfolio mutations: set_price / refresh_live / import_txns /
        import_prices. Pasted CSV text is written to a temp file because the
        portfolio ingest helpers hash file bytes for idempotency."""
        import tempfile

        def f(name: str) -> str:
            return form.get(name, [""])[0].strip()

        action = f("action")
        msg, err = "", False
        try:
            if action == "set_price":
                portfolio.set_price(f("symbol"), float(f("close")),
                                    f("price_date") or None)
                msg = f"set {f('symbol').upper()} = {f('close')}"
            elif action == "refresh_live":
                res = portfolio.refresh_live_prices()
                msg = f"refreshed {len(res['updated'])}, failed {len(res['failed'])}"
                err = bool(res["failed"]) and not res["updated"]
            elif action in ("import_txns", "import_prices"):
                text = form.get("csv_text", [""])[0]
                if not text.strip():
                    raise ValueError("empty CSV")
                with tempfile.NamedTemporaryFile(
                        "w", suffix=".csv", delete=False, encoding="utf-8") as tmp:
                    tmp.write(text)
                    tmp_path = tmp.name
                try:
                    if action == "import_txns":
                        n = portfolio.ingest_transactions_csv(tmp_path)
                        msg = f"imported {n} transaction(s)"
                    else:
                        n = portfolio.ingest_prices_csv(tmp_path)
                        msg = f"imported {n} price row(s)"
                finally:
                    os.unlink(tmp_path)
            else:
                msg, err = f"unknown action {action!r}", True
        except portfolio.DuplicateImport as exc:
            msg, err = str(exc), True
        except Exception as exc:  # never 500 the operator on a bad form/CSV
            log.warning("portfolio POST %s failed: %s", action, exc)
            msg, err = f"error: {exc}", True

        params = {}
        if msg:
            params["msg"] = msg
        if err:
            params["err"] = "1"
        target = "/portfolio" + (("?" + urlencode(params)) if params else "")
        self._redirect(target, set_cookie)


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
