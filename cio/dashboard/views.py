"""HTML rendering for the dev dashboard — pure functions, no I/O.

Each ``render_*`` takes already-fetched data and returns a full HTML page.
Server-rendered, no client JS. Everything user-derived goes through ``esc``.
"""
from __future__ import annotations

from html import escape

from cio import timeutil


def esc(value) -> str:
    """HTML-escape any value (None → '')."""
    return escape("" if value is None else str(value))


def esc_ts(value) -> str:
    """HTML-escape a stored UTC timestamp, displayed in the local zone (CIO_TZ)."""
    return escape(timeutil.utc_to_local(value))


_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; margin: 0;
       background: #0e1116; color: #e6edf3; }
header { background: #161b22; padding: 12px 20px; border-bottom: 1px solid #30363d; }
header a { color: #58a6ff; text-decoration: none; margin-right: 18px; font-weight: 600; }
header .lvl { float: right; color: #8b949e; font-weight: 400; }
main { padding: 20px; max-width: 1100px; margin: 0 auto; }
h1 { font-size: 18px; margin: 0 0 16px; }
h2 { font-size: 15px; margin: 24px 0 8px; color: #8b949e; }
table { border-collapse: collapse; width: 100%; margin: 8px 0 20px; }
th,td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #21262d;
        vertical-align: top; }
th { color: #8b949e; font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
a { color: #58a6ff; }
.bar { background: #1f6feb; height: 10px; border-radius: 2px; display: inline-block; }
.msg { white-space: pre-wrap; word-break: break-word; }
.user { color: #7ee787; } .assistant { color: #e6edf3; }
td.up { color: #7ee787; } td.down { color: #f85149; }
.cards { display: flex; gap: 12px; flex-wrap: wrap; margin: 8px 0 16px; }
.stat { flex: 1; min-width: 150px; border: 1px solid #30363d; border-radius: 8px;
        padding: 12px 16px; background: #161b22; }
.stat .k { color: #8b949e; font-size: 12px; } .stat .v { font-size: 20px;
        font-variant-numeric: tabular-nums; margin-top: 4px; }
.stat .v.up { color: #7ee787; } .stat .v.down { color: #f85149; }
details { border: 1px solid #30363d; border-radius: 6px; margin: 8px 0;
          background: #161b22; }
summary { cursor: pointer; padding: 8px 12px; font-weight: 600; }
pre { white-space: pre-wrap; word-break: break-word; background: #0d1117;
      border: 1px solid #21262d; border-radius: 6px; padding: 10px; margin: 6px 0;
      max-height: 420px; overflow: auto; }
.sent { border-left: 3px solid #1f6feb; } .ret { border-left: 3px solid #7ee787; }
.empty { color: #8b949e; font-style: italic; }
input,textarea,button,select { font: inherit; background: #0d1117; color: #e6edf3;
       border: 1px solid #30363d; border-radius: 6px; padding: 5px 8px; }
button { cursor: pointer; background: #21262d; }
button.primary { background: #1f6feb; border-color: #1f6feb; color: #fff; }
button.danger { color: #f85149; }
form.inline { display: inline; margin: 0; }
textarea { width: 100%; min-height: 90px; }
.badge { background: #1f6feb; color: #fff; border-radius: 10px; padding: 1px 8px;
         font-size: 12px; }
.flash { background: #1b2a16; border: 1px solid #2ea043; border-radius: 6px;
         padding: 8px 12px; margin: 0 0 16px; color: #7ee787; }
.flash.err { background: #2a1616; border-color: #f85149; color: #f85149; }
.card { border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin: 12px 0;
        background: #161b22; }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
ul.symlist { list-style: none; margin: 8px 0; padding: 0; }
ul.symlist li { display: flex; align-items: center; gap: 10px; padding: 8px 10px;
        border: 1px solid #21262d; border-radius: 6px; margin: 4px 0;
        background: #0d1117; cursor: grab; }
ul.symlist li.drag { opacity: .4; } ul.symlist li.over { border-color: #1f6feb; }
ul.symlist .grip { color: #8b949e; cursor: grab; user-select: none; }
ul.symlist .sym { flex: 1; font-variant-numeric: tabular-nums; }
"""


def _page(title: str, body: str, level: int) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{esc(title)} · CIO dev dashboard</title>"
        f"<style>{_CSS}</style></head><body>"
        "<header>"
        "<a href='/'>Overview</a><a href='/usage'>Token usage</a>"
        "<a href='/telegram'>Telegram</a><a href='/committee'>Committee</a>"
        "<a href='/watchlist'>Watchlist</a><a href='/portfolio'>Portfolio</a>"
        "<a href='/subscribers'>Subscribers</a>"
        "<a href='/memory'>Memory</a>"
        "<a href='/sanitizer'>Sanitizer</a>"
        f"<span class='lvl'>capture level {esc(level)}</span>"
        "</header><main>" + body + "</main></body></html>"
    )


def render_overview(usage_today, runs, turns, level: int, token_q: str = "") -> str:
    rows = "".join(
        f"<tr><td>{esc(u['service'])}</td><td class='num'>{esc(u['tokens'])}</td></tr>"
        for u in usage_today
    ) or "<tr><td class='empty' colspan='2'>no usage recorded today</td></tr>"

    run_rows = "".join(
        f"<tr><td><a href='/committee/{esc(r['run_id'])}{token_q}'>{esc(r['symbol'])}</a></td>"
        f"<td>{esc_ts(r['started'])}</td><td class='num'>{esc(r['calls'])}</td>"
        f"<td class='num'>{esc(r['tokens'])}</td></tr>"
        for r in runs
    ) or "<tr><td class='empty' colspan='4'>no committee runs captured</td></tr>"

    turn_rows = "".join(
        f"<tr><td class='{esc(t['role'])}'>{esc(t['role'])}</td>"
        f"<td class='msg'>{esc((t['content'] or '')[:200])}</td>"
        f"<td>{esc_ts(t['ts'])}</td></tr>"
        for t in turns
    ) or "<tr><td class='empty' colspan='3'>no Telegram turns captured</td></tr>"

    body = (
        "<h1>Overview</h1>"
        "<h2>Tokens used today (local)</h2>"
        f"<table><tr><th>Service</th><th>Tokens</th></tr>{rows}</table>"
        "<h2>Recent committee runs</h2>"
        f"<table><tr><th>Symbol</th><th>Started</th><th>Calls</th><th>Tokens</th></tr>{run_rows}</table>"
        "<h2>Recent Telegram turns</h2>"
        f"<table><tr><th>Role</th><th>Message</th><th>When</th></tr>{turn_rows}</table>"
    )
    return _page("Overview", body, level)


def render_usage(usage_rows, level: int) -> str:
    peak = max((u["tokens"] for u in usage_rows), default=0) or 1
    rows = "".join(
        f"<tr><td>{esc(u['day'])}</td><td>{esc(u['service'])}</td>"
        f"<td class='num'>{esc(u['tokens'])}</td>"
        f"<td><span class='bar' style='width:{int(200 * u['tokens'] / peak)}px'></span></td></tr>"
        for u in usage_rows
    ) or "<tr><td class='empty' colspan='4'>no usage recorded</td></tr>"
    body = (
        "<h1>Token usage — per service per day</h1>"
        f"<table><tr><th>Day (local)</th><th>Service</th><th>Tokens</th><th></th></tr>{rows}</table>"
    )
    return _page("Token usage", body, level)


def render_subscribers(subscribers, level: int) -> str:
    """List chats opted in to the daily digest + 06:00 watchlist briefing."""
    rows = "".join(
        f"<tr><td class='num'>{esc(s['chat_id'])}</td>"
        f"<td>{esc_ts(s['updated_at'])}</td></tr>"
        for s in subscribers
    ) or "<tr><td class='empty' colspan='2'>no subscribers yet — users opt in with /subscribe</td></tr>"
    body = (
        "<h1>Subscribers</h1>"
        f"<p>{esc(len(subscribers))} chat(s) receive the daily portfolio digest and the "
        "06:00 pre-market watchlist briefing on trading days.</p>"
        f"<table><tr><th>Chat ID</th><th>Subscribed since</th></tr>{rows}</table>"
    )
    return _page("Subscribers", body, level)


def render_telegram(turns, level: int, days=None, selected_day: str | None = None,
                    flash: str = "", flash_err: bool = False) -> str:
    """Telegram history grouped by local calendar day, newest day first, with a
    per-day delete button (irreversible, confirmed, auth-gated, PRG).

    *days* is memory.conv_days() — [{day, count}], newest first — rendered as a day
    selector at the top. *selected_day* (from ``?day=``) restricts the view to one day;
    None shows the recent history across days.
    """
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    # Day selector: "All" + one link per day. The active choice is marked.
    def _navlink(label: str, href: str, active: bool) -> str:
        cls = " class='badge'" if active else ""
        return f"<a{cls} href='{esc(href)}'>{esc(label)}</a>"
    nav_items = [_navlink("All", "/telegram", selected_day is None)]
    for d in (days or []):
        nav_items.append(_navlink(
            f"{d['day']} ({d['count']})", f"/telegram?day={d['day']}",
            d["day"] == selected_day))
    day_nav = ("<div class='daynav'>Days: " + " · ".join(nav_items) + "</div>"
               if days else "")

    # Group turns by local day, preserving the newest-first order they arrive in.
    days: dict[str, list] = {}
    for t in turns:
        days.setdefault(timeutil.local_day(t.get("ts")), []).append(t)

    blocks: list[str] = []
    for day, items in days.items():
        rows = "".join(
            f"<tr><td class='num'>{esc(t['chat_id'])}</td>"
            f"<td class='{esc(t['role'])}'>{esc(t['role'])}</td>"
            f"<td class='msg'>{esc(t['content'])}</td>"
            f"<td>{esc_ts(t['ts'])}</td></tr>"
            for t in items
        )
        del_btn = _wipe_form(
            "wipe_day", "Delete this day",
            f"Delete all Telegram history for {day}? This cannot be undone.",
            path="/telegram", day=day,
        )
        blocks.append(
            f"<h2>{esc(day or 'unknown')} · {len(items)} turn(s) {del_btn}</h2>"
            "<table><tr><th>Chat</th><th>Role</th><th>Message</th><th>When</th></tr>"
            f"{rows}</table>"
        )
    empty_msg = (f"<p class='empty'>no Telegram turns for {esc(selected_day)}.</p>"
                 if selected_day else
                 "<p class='empty'>no Telegram turns captured (level 3 disables this).</p>")
    body_inner = "".join(blocks) or empty_msg
    body = ("<h1>Telegram conversation history</h1>" + flash_html + day_nav
            + body_inner)
    return _page("Telegram", body, level)


def render_committee_list(runs, level: int, token_q: str = "",
                          flash: str = "", flash_err: bool = False) -> str:
    rows = "".join(
        f"<tr><td><a href='/committee/{esc(r['run_id'])}{token_q}'>{esc(r['run_id'])}</a></td>"
        f"<td>{esc(r['symbol'])}</td><td>{esc_ts(r['started'])}</td>"
        f"<td class='num'>{esc(r['calls'])}</td><td class='num'>{esc(r['tokens'])}</td></tr>"
        for r in runs
    ) or "<tr><td class='empty' colspan='5'>no committee runs captured</td></tr>"
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    # Destructive: delete every captured run. Only shown when there's something to clear.
    wipe_btn = _wipe_form(
        "wipe_runs", "Delete all committee runs",
        "Delete ALL captured committee runs? This cannot be undone.",
        path="/committee",
    ) if runs else ""
    body = (
        f"<h1>Committee runs {wipe_btn}</h1>" + flash_html +
        f"<table><tr><th>Run</th><th>Symbol</th><th>Started</th><th>Calls</th><th>Tokens</th></tr>{rows}</table>"
    )
    return _page("Committee", body, level)


def render_sanitizer(rows, level: int) -> str:
    """Audit trail of the LLM figures-sanitizer: what it stripped or rejected.

    *rows* are sanitizer_log.recent() dicts: role_key, symbol, action, original,
    cleaned, removed (list), ts. 'cleaned' = rewritten and stored; 'rejected' =
    dropped (nothing qualitative survived)."""
    def _row(r: dict) -> str:
        act = r.get("action") or ""
        badge = ("<span class='badge down'>rejected</span>" if act == "rejected"
                 else "<span class='badge up'>cleaned</span>")
        removed = ", ".join(r.get("removed") or []) or "—"
        cleaned = esc(r.get("cleaned")) if r.get("cleaned") else "<span class='empty'>(dropped)</span>"
        return (f"<tr><td>{esc_ts(r.get('ts'))}</td>"
                f"<td>{esc(r.get('role_key'))}</td>"
                f"<td>{esc(r.get('symbol'))}</td>"
                f"<td>{badge}</td>"
                f"<td class='msg'><span class='empty'>{esc(removed)}</span></td>"
                f"<td class='msg'>{esc(r.get('original'))}</td>"
                f"<td class='msg'>{cleaned}</td></tr>")

    body_rows = "".join(_row(r) for r in rows) or (
        "<tr><td class='empty' colspan='7'>no sanitizer activity yet — figures get "
        "stripped or rejected here when committee agents write figure-laden notes.</td></tr>")
    body = (
        "<h1>Figures-sanitizer audit</h1>"
        "<p>Every time the LLM sanitizer rewrites a memory note to remove stale "
        "figures, or rejects one outright, it is logged here. The deterministic regex "
        "firewall is the final gate; this shows the smart pass's decisions.</p>"
        "<table><tr><th>When</th><th>Agent</th><th>Symbol</th><th>Action</th>"
        "<th>Removed</th><th>Original</th><th>Stored</th></tr>"
        f"{body_rows}</table>"
    )
    return _page("Sanitizer", body, level)


def _wipe_form(action: str, label: str, confirm: str, **hidden) -> str:
    """A small auth-gated POST form with a JS-confirm danger button. Hidden fields
    carry the action target (store/scope). All values are HTML-escaped. *path* is the
    form target (popped from hidden kwargs; defaults to /memory)."""
    path = hidden.pop("path", "/memory")
    fields = f"<input type='hidden' name='action' value='{esc(action)}'>"
    for name, val in hidden.items():
        fields += f"<input type='hidden' name='{esc(name)}' value='{esc(val)}'>"
    return (
        f"<form class='inline' method='post' action='{esc(path)}' "
        f"onsubmit=\"return confirm('{esc(confirm)}');\">"
        f"{fields}<button type='submit' class='danger'>{esc(label)}</button></form>"
    )


def render_memory(sections, level: int, flash: str = "", flash_err: bool = False) -> str:
    """Per-agent / per-chat memory contents, for debugging.

    *sections* is a list of ``{"store", "label", "scopes": [{"scope","count","notes"}]}``
    where each note is a mem_notes row dict. One <details> per scope; HOT notes
    (injected into prompts) flagged so you can see what each agent 'knows'. Each store
    has a "delete all" button and each scope a per-scope delete button (all irreversible,
    confirmed client-side, auth-gated, PRG).
    """
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    blocks: list[str] = []
    for sec in sections:
        scopes = sec.get("scopes") or []
        store = sec.get("store") or ""
        # Store-level "delete all" button next to the section header.
        store_btn = _wipe_form(
            "wipe_store", f"Delete all {esc(store)} memory",
            f"Delete ALL {store} memory? This cannot be undone.", store=store,
        ) if scopes else ""
        blocks.append(
            f"<h2>{esc(sec.get('label'))} {store_btn}</h2>")
        if not scopes:
            blocks.append("<p class='empty'>no memory in this store.</p>")
            continue
        for sc in scopes:
            scope = sc.get("scope") or ""
            notes = sc.get("notes") or []
            note_rows = "".join(
                f"<tr><td>{esc(n.get('tier'))}</td>"
                f"<td>{esc(n.get('key'))}</td>"
                f"<td class='msg'>{esc(n.get('value'))}</td>"
                f"<td class='num'>{esc(n.get('hits'))}</td>"
                f"<td class='num'>{esc(n.get('importance'))}</td>"
                f"<td>{esc(n.get('source'))}</td>"
                f"<td>{esc_ts(n.get('updated_at'))}</td></tr>"
                for n in notes
            ) or "<tr><td class='empty' colspan='7'>no notes</td></tr>"
            scope_btn = _wipe_form(
                "wipe_scope", "Delete scope",
                f"Delete all notes in {scope}? This cannot be undone.", scope=scope,
            )
            blocks.append(
                f"<details><summary>{esc(scope)} "
                f"· {esc(sc.get('count'))} note(s) {scope_btn}</summary>"
                "<table><tr><th>Tier</th><th>Key</th><th>Value</th><th>Hits</th>"
                "<th>Imp</th><th>Source</th><th>Updated</th></tr>"
                f"{note_rows}</table></details>"
            )
    body = "<h1>Agent memory contents</h1>" + flash_html + "".join(blocks)
    return _page("Memory", body, level)


# Minimal vanilla drag-to-reorder for the symbol list. The dashboard is otherwise
# no-JS; this is the one script, self-contained and scoped to #symlist. On drop it
# writes the new symbol order into the reorder form's hidden field and submits
# (PRG redirect re-renders). Without JS the list is still readable and every other
# action keeps working — only dragging is inert.
_REORDER_JS = """<script>
(function(){
  var list=document.getElementById('symlist'),
      form=document.getElementById('orderform'),
      inp=document.getElementById('order_input');
  if(!list||!form||!inp) return;
  var drag=null;
  function order(){return Array.prototype.map.call(list.children,
      function(li){return li.getAttribute('data-sym');}).join(',');}
  Array.prototype.forEach.call(list.children,function(li){
    li.addEventListener('dragstart',function(){drag=li;li.classList.add('drag');});
    li.addEventListener('dragend',function(){li.classList.remove('drag');});
    li.addEventListener('dragover',function(e){
      e.preventDefault();
      if(!drag||drag===li) return;
      var r=li.getBoundingClientRect(),
          after=(e.clientY-r.top)/r.height>0.5;
      list.insertBefore(drag, after?li.nextSibling:li);
    });
    li.addEventListener('drop',function(e){e.preventDefault();inp.value=order();form.submit();});
  });
  form.addEventListener('submit',function(){inp.value=order();});
})();
</script>"""


def _hidden(wl_id, action: str) -> str:
    """Hidden form fields shared by every watchlist mutation form."""
    return (f"<input type='hidden' name='action' value='{esc(action)}'>"
            f"<input type='hidden' name='wl_id' value='{esc(wl_id)}'>")


def render_watchlist(watchlists, selected, level: int,
                     search_q: str = "", flash: str = "", flash_err: bool = False,
                     nasdaq_index: str = "^IXIC") -> str:
    """Watchlist manager: create / activate / rename / delete lists, manage their
    symbols, search, and import a CSV. Server-rendered forms POST to /watchlist;
    no JS. *watchlists* is the list_watchlists() result; *selected* is one
    watchlist dict (id,name,is_active,symbols) or None.

    Prices are NOT shown here — they're the Telegram /watchlist feature. This page
    only manages list membership, so it stays fast (no per-symbol network fetch).
    """
    flash_html = (f"<p class='flash{' err' if flash_err else ''}'>{esc(flash)}</p>"
                  if flash else "")

    # --- create + search bar ---
    create_form = (
        "<form method='post' action='/watchlist' class='row'>"
        "<input type='hidden' name='action' value='create'>"
        "<input name='name' placeholder='New watchlist name' required>"
        "<button class='primary' type='submit'>Create</button></form>"
    )
    search_form = (
        "<form method='get' action='/watchlist' class='row'>"
        f"<input name='q' value='{esc(search_q)}' placeholder='Search name or symbol'>"
        "<button type='submit'>Search</button>"
        + ("<a href='/watchlist'>clear</a>" if search_q else "")
        + "</form>"
    )

    # --- watchlist table ---
    rows = []
    for w in watchlists:
        active_badge = " <span class='badge'>active</span>" if w["is_active"] else ""
        matched = w.get("matched")
        match_note = (f"<br><span class='empty'>matched: {esc(', '.join(matched))}</span>"
                      if search_q and matched else "")
        activate = "" if w["is_active"] else (
            "<form method='post' action='/watchlist' class='inline'>"
            f"{_hidden(w['id'], 'activate')}"
            "<button type='submit'>Activate</button></form>")
        delete = (
            "<form method='post' action='/watchlist' class='inline' "
            "onsubmit=\"return confirm('Delete this watchlist?')\">"
            f"{_hidden(w['id'], 'delete')}"
            "<button class='danger' type='submit'>Delete</button></form>")
        rows.append(
            f"<tr><td><a href='/watchlist?wl={esc(w['id'])}'>{esc(w['name'])}</a>"
            f"{active_badge}{match_note}</td>"
            f"<td class='num'>{esc(w['count'])}</td>"
            f"<td class='row'>{activate} {delete}</td></tr>"
        )
    table_rows = "".join(rows) or (
        "<tr><td class='empty' colspan='3'>no watchlists"
        f"{' match your search' if search_q else ' yet — create one above'}</td></tr>")
    list_table = (
        "<table><tr><th>Name</th><th>Symbols</th><th>Actions</th></tr>"
        f"{table_rows}</table>")

    # --- selected watchlist detail ---
    detail = ""
    if selected is not None:
        items = []
        for s in selected["symbols"]:
            is_idx = s == nasdaq_index
            rm = ("<span class='empty'>required index</span>" if is_idx else
                  "<form method='post' action='/watchlist' class='inline'>"
                  f"{_hidden(selected['id'], 'remove_symbol')}"
                  f"<input type='hidden' name='symbol' value='{esc(s)}'>"
                  "<button class='danger' type='submit'>Remove</button></form>")
            items.append(
                f"<li draggable='true' data-sym='{esc(s)}'>"
                f"<span class='grip' title='Drag to reorder'>&#x2630;</span>"
                f"<span class='sym'>{esc(s)}</span>{rm}</li>")
        if items:
            # Draggable list + a separate order form (forms can't nest, so the
            # per-row Remove forms live inside the <li> and the reorder form sits
            # beside the list; JS writes the dragged order into it). No JS → the
            # list still renders and Remove/Add still work; only drag is inert.
            sym_table = (
                "<p class='empty'>Drag rows to reorder · this order drives the "
                "Telegram /watchlist output.</p>"
                f"<ul id='symlist' class='symlist'>{''.join(items)}</ul>"
                "<form id='orderform' method='post' action='/watchlist' class='inline'>"
                f"{_hidden(selected['id'], 'reorder')}"
                "<input type='hidden' id='order_input' name='order' value=''>"
                "<button type='submit'>Save order</button></form>"
                + _REORDER_JS)
        else:
            sym_table = "<p class='empty'>no symbols</p>"
        rename_form = (
            "<form method='post' action='/watchlist' class='row'>"
            f"{_hidden(selected['id'], 'rename')}"
            f"<input name='name' value='{esc(selected['name'])}' required>"
            "<button type='submit'>Rename</button></form>")
        add_form = (
            "<form method='post' action='/watchlist' class='row'>"
            f"{_hidden(selected['id'], 'add_symbol')}"
            "<input name='symbol' placeholder='AAPL' required>"
            "<button class='primary' type='submit'>Add symbol</button></form>")
        import_form = (
            "<form method='post' action='/watchlist'>"
            f"{_hidden(selected['id'], 'import_csv')}"
            "<textarea name='csv_text' placeholder='Paste CSV: a row of tickers "
            "(e.g. \"AAPL\",\"MSFT\",\"NVDA\") or one symbol per line'></textarea>"
            "<div class='row'><button class='primary' type='submit'>Import CSV</button>"
            "<span class='empty'>existing symbols are skipped</span></div></form>")
        active_note = (" <span class='badge'>active</span>" if selected["is_active"]
                       else "")
        detail = (
            f"<div class='card'><h2>{esc(selected['name'])}{active_note}</h2>"
            f"<h2>Symbols</h2>{sym_table}"
            f"<h2>Add symbol</h2>{add_form}"
            f"<h2>Import from CSV</h2>{import_form}"
            f"<h2>Rename</h2>{rename_form}</div>")

    body = (
        "<h1>Watchlists</h1>" + flash_html
        + f"<div class='card'><div class='row' style='justify-content:space-between'>"
          f"{create_form}{search_form}</div></div>"
        + list_table + detail
    )
    return _page("Watchlist", body, level)


def _money(value) -> str:
    """Format a number as a plain string, '' for None."""
    return "" if value is None else f"{value:,.2f}"


def _signed_cell(value, suffix: str = "") -> str:
    """A right-aligned table cell coloured green when >0, red when <0.

    Green=up, red=down — matches the charts and reports.
    """
    if value is None:
        return "<td class='num'></td>"
    cls = "up" if value > 0 else ("down" if value < 0 else "")
    sign = "+" if value > 0 else ""
    return f"<td class='num {cls}'>{esc(sign + _money(value) + suffix)}</td>"


def render_portfolio(summ, positions, realized, level: int,
                     flash: str = "", flash_err: bool = False) -> str:
    """Portfolio tab: read view (summary cards, open positions, realized P&L) plus
    the management write surface (set price, import transactions/prices CSV, refresh
    live prices). Forms POST to /portfolio; PRG redirect re-renders with a flash.

    *summ* is portfolio.summary(); *positions* and *realized* are lists of row
    dicts (DataFrame.to_dict('records')). Prices are manually entered or refreshed,
    so a position with no price shows blank market value — never a crash.
    """
    flash_html = (f"<p class='flash{' err' if flash_err else ''}'>{esc(flash)}</p>"
                  if flash else "")

    upl = summ.get("unrealized_pl") or 0
    upl_cls = "up" if upl > 0 else ("down" if upl < 0 else "")
    rpl = summ.get("realized_pl") or 0
    rpl_cls = "up" if rpl > 0 else ("down" if rpl < 0 else "")
    cards = (
        "<div class='cards'>"
        f"<div class='stat'><div class='k'>Positions</div>"
        f"<div class='v'>{esc(summ.get('positions'))}</div></div>"
        f"<div class='stat'><div class='k'>Market value</div>"
        f"<div class='v'>{esc(_money(summ.get('market_value')))}</div></div>"
        f"<div class='stat'><div class='k'>Cost basis</div>"
        f"<div class='v'>{esc(_money(summ.get('cost_basis')))}</div></div>"
        f"<div class='stat'><div class='k'>Unrealized P&amp;L</div>"
        f"<div class='v {upl_cls}'>{esc(_money(upl))} "
        f"({esc(summ.get('unrealized_pct'))}%)</div></div>"
        f"<div class='stat'><div class='k'>Realized P&amp;L</div>"
        f"<div class='v {rpl_cls}'>{esc(_money(rpl))}</div></div>"
        f"<div class='stat'><div class='k'>Dividends</div>"
        f"<div class='v'>{esc(_money(summ.get('dividends')))}</div></div>"
        "</div>"
    )

    pos_rows = "".join(
        f"<tr><td>{esc(p['symbol'])}</td>"
        f"<td class='num'>{esc(p['quantity'])}</td>"
        f"<td class='num'>{esc(_money(p['avg_cost']))}</td>"
        f"<td class='num'>{esc(_money(p['cost_basis']))}</td>"
        f"<td class='num'>{esc(_money(p['last_price']))}</td>"
        f"<td class='num'>{esc(_money(p['market_value']))}</td>"
        f"{_signed_cell(p['unrealized_pl'])}"
        f"{_signed_cell(p['unrealized_pct'], '%')}</tr>"
        for p in positions
    ) or "<tr><td class='empty' colspan='8'>no open positions — import a transactions CSV below</td></tr>"
    pos_table = (
        "<table><tr><th>Symbol</th><th>Qty</th><th>Avg cost</th><th>Cost basis</th>"
        "<th>Last</th><th>Market value</th><th>Unrealized P&amp;L</th><th>%</th></tr>"
        f"{pos_rows}</table>")

    rpl_rows = "".join(
        f"<tr><td>{esc(r['symbol'])}</td>"
        f"{_signed_cell(r['realized_pl'])}"
        f"<td class='num'>{esc(_money(r['dividends']))}</td>"
        f"{_signed_cell(r['total'])}</tr>"
        for r in realized
    ) or "<tr><td class='empty' colspan='4'>no realized P&amp;L yet</td></tr>"
    rpl_table = (
        "<table><tr><th>Symbol</th><th>Realized P&amp;L</th><th>Dividends</th>"
        f"<th>Total</th></tr>{rpl_rows}</table>")

    # --- management write forms (mirror the watchlist POST pattern) ---
    set_price_form = (
        "<form method='post' action='/portfolio' class='row'>"
        "<input type='hidden' name='action' value='set_price'>"
        "<input name='symbol' placeholder='AAPL' required>"
        "<input name='close' type='number' step='any' placeholder='Price' required>"
        "<input name='price_date' type='date'>"
        "<button class='primary' type='submit'>Set price</button></form>")
    refresh_form = (
        "<form method='post' action='/portfolio' class='inline'>"
        "<input type='hidden' name='action' value='refresh_live'>"
        "<button type='submit'>Refresh live prices</button></form>")
    txns_form = (
        "<form method='post' action='/portfolio'>"
        "<input type='hidden' name='action' value='import_txns'>"
        "<textarea name='csv_text' placeholder='txn_date,symbol,action,quantity,price"
        "[,fees,currency,notes] — action is BUY / SELL / DIV'></textarea>"
        "<div class='row'><button class='primary' type='submit'>Import transactions</button>"
        "<span class='empty'>idempotent: an identical CSV is rejected</span></div></form>")
    prices_form = (
        "<form method='post' action='/portfolio'>"
        "<input type='hidden' name='action' value='import_prices'>"
        "<textarea name='csv_text' placeholder='symbol,price_date,close'></textarea>"
        "<div class='row'><button class='primary' type='submit'>Import prices</button>"
        "<span class='empty'>upserts the latest close per symbol</span></div></form>")

    body = (
        "<h1>Portfolio</h1>" + flash_html + cards
        + "<h2>Open positions</h2>" + pos_table
        + "<h2>Realized P&amp;L</h2>" + rpl_table
        + "<div class='card'><h2>Set price</h2>"
        + "<div class='row'>" + set_price_form + refresh_form + "</div>"
        + "<h2>Import transactions (CSV)</h2>" + txns_form
        + "<h2>Import prices (CSV)</h2>" + prices_form + "</div>"
    )
    return _page("Portfolio", body, level)


def render_committee_run(run_id: str, calls, level: int) -> str:
    if not calls:
        body = f"<h1>Run {esc(run_id)}</h1><p class='empty'>no calls found for this run.</p>"
        return _page("Run", body, level)

    blocks = []
    for c in calls:
        head = (
            f"{esc(c['role_key'])} · {esc(c['service'])}:{esc(c['model']) or '(default)'} "
            f"· {esc(c['tokens'])} tok · {esc_ts(c['ts'])}"
        )
        resp = c["response"] or ""
        blocks.append(
            "<details open><summary>" + head + "</summary>"
            "<div class='sent'><strong>SENT — system</strong>"
            f"<pre>{esc(c['system_prompt'])}</pre></div>"
            "<div class='sent'><strong>SENT — user</strong>"
            f"<pre>{esc(c['user_prompt'])}</pre></div>"
            "<div class='ret'><strong>RETURNED</strong>"
            f"<pre>{esc(resp) if resp.strip() else '(empty — fell through to next backend)'}</pre></div>"
            "</details>"
        )
    sym = calls[0].get("symbol") or ""
    body = f"<h1>Run {esc(run_id)} · {esc(sym)}</h1>" + "".join(blocks)
    return _page("Run", body, level)
