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


_TRIGGER_LABELS = {"command": "💬 /committee", "chat": "🗣 chat", "cli": "⌨ cli"}


def _trigger(run: dict) -> str:
    """Human label for what triggered a committee run. Pre-source rows → '—'."""
    src = run.get("source")
    return esc(_TRIGGER_LABELS.get(src, src)) if src else "—"


_CSS = """
:root {
  color-scheme: light;
  --bg: #f5f6f9;
  --bg-grad: radial-gradient(1100px 560px at 82% -12%, #6d5cff12 0, transparent 60%);
  --surface: #ffffff; --surface-2: #f4f5f8; --raised: #ffffff; --raised-hover: #f0f1f5;
  --line: #e4e7ec; --line-soft: #eef0f3; --line-strong: #d6dae1;
  --text: #0d1320; --muted: #5b6675; --faint: #8a94a3;
  --accent: #6d5cff; --accent-strong: #5b4be0; --accent-2: #8b7bff; --accent-soft: #6d5cff14;
  --up: #138a3a; --up-soft: #138a3a14; --down: #d22f2f; --down-soft: #d22f2f12;
  --header-bg: #ffffffd9; --code-bg: #f6f7f9;
  --hover: color-mix(in srgb, var(--text) 6%, transparent);
  --accent-line: color-mix(in srgb, var(--accent) 38%, transparent);
  --radius: 12px; --radius-sm: 8px;
  --shadow: 0 1px 2px #1018281f, 0 10px 28px -14px #10182833;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #07090e;
  --bg-grad: radial-gradient(1100px 560px at 82% -12%, #8b7bff14 0, transparent 60%);
  --surface: #0f141d; --surface-2: #141b26; --raised: #1a2230; --raised-hover: #222c3c;
  --line: #232b3a; --line-soft: #1a212c; --line-strong: #2f3a4c;
  --text: #e8eef6; --muted: #8a97a8; --faint: #5c6675;
  --accent: #8b7bff; --accent-strong: #6d5cff; --accent-2: #a78bfa; --accent-soft: #8b7bff1f;
  --up: #3fb950; --up-soft: #3fb9501a; --down: #f85149; --down-soft: #f851491a;
  --header-bg: #0a0d13e6; --code-bg: #0a0e15;
  --shadow: 0 1px 2px #0006, 0 8px 24px -12px #0009;
}
* { box-sizing: border-box; }
body { font: 14px/1.55 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Inter,sans-serif;
       margin: 0; background: var(--bg); background-image: var(--bg-grad);
       background-attachment: fixed; color: var(--text);
       -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }

header { position: sticky; top: 0; z-index: 20; display: flex; align-items: center;
         gap: 6px; flex-wrap: wrap; padding: 11px 24px;
         background: var(--header-bg); backdrop-filter: blur(14px) saturate(150%);
         border-bottom: 1px solid var(--line); }
header .brand { font-weight: 700; letter-spacing: .3px; margin-right: 16px; font-size: 15px;
                color: var(--text); display: flex; align-items: center; gap: 9px; }
header .brand .dot { width: 9px; height: 9px; border-radius: 50%;
       background: linear-gradient(135deg,var(--accent),var(--accent-2));
       box-shadow: 0 0 12px var(--accent); }
header a { color: var(--muted); text-decoration: none; font-weight: 500;
           padding: 6px 12px; border-radius: 8px; line-height: 1;
           transition: background .15s, color .15s; }
header a:hover { color: var(--text); background: var(--hover); }
header a.active { color: var(--text); background: var(--accent-soft);
                  box-shadow: inset 0 0 0 1px var(--accent-line); }
header .themebtn { margin-left: auto; padding: 0; width: 30px; height: 30px;
       display: inline-flex; align-items: center; justify-content: center; font-size: 14px;
       line-height: 1; background: var(--surface); border: 1px solid var(--line);
       border-radius: 999px; color: var(--text); cursor: pointer;
       transition: background .15s, border-color .15s; }
header .themebtn:hover { background: var(--hover); border-color: var(--line-strong); }
header .lvl { margin-left: 8px; color: var(--muted); font-weight: 600; font-size: 11px;
       text-transform: uppercase; letter-spacing: .5px;
       padding: 5px 11px; border: 1px solid var(--line); border-radius: 999px;
       background: var(--surface); }

main { padding: 30px 24px 72px; max-width: 1180px; margin: 0 auto; }
h1 { font-size: 23px; font-weight: 700; letter-spacing: -.3px; margin: 0 0 22px;
     display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
h2 { font-size: 11.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .7px;
     margin: 28px 0 11px; color: var(--muted); display: flex; align-items: center; gap: 9px; }
h2::before { content: ""; width: 3px; height: 13px; border-radius: 2px; flex: none;
     background: linear-gradient(180deg,var(--accent),var(--accent-2)); }

table { border-collapse: separate; border-spacing: 0; width: 100%; margin: 8px 0 24px;
        background: var(--surface); border: 1px solid var(--line);
        border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow); }
th,td { text-align: left; padding: 11px 15px; border-bottom: 1px solid var(--line-soft);
        vertical-align: top; }
thead th, tr:first-child th { position: sticky; top: 0; }
th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase;
     letter-spacing: .5px; background: var(--surface-2); }
tbody tr:last-child td, table tr:last-child td { border-bottom: 0; }
tr:hover td { background: var(--hover); }
td.num { text-align: right; font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

.bar { background: linear-gradient(90deg,var(--accent-strong),var(--accent)); height: 8px;
       border-radius: 999px; display: inline-block; box-shadow: 0 0 8px var(--accent-soft); }
.msg { white-space: pre-wrap; word-break: break-word; }
.user { color: var(--up); font-weight: 600; } .assistant { color: var(--text); }
td.up { color: var(--up); font-weight: 600; } td.down { color: var(--down); font-weight: 600; }

.cards { display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr));
         gap: 14px; margin: 8px 0 20px; }
.stat { position: relative; border: 1px solid var(--line); border-radius: var(--radius);
        padding: 17px 18px; background: linear-gradient(180deg,var(--surface-2),var(--surface));
        box-shadow: var(--shadow); overflow: hidden;
        transition: transform .15s, border-color .15s, box-shadow .15s; }
.stat::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 2px;
        background: linear-gradient(90deg,var(--accent),var(--accent-2)); opacity: .55; }
.stat:hover { transform: translateY(-2px); border-color: var(--line-strong); }
.stat .k { color: var(--muted); font-size: 11px; text-transform: uppercase;
           letter-spacing: .5px; font-weight: 600; }
.stat .v { font-size: 25px; font-weight: 700; font-variant-numeric: tabular-nums;
           margin-top: 7px; letter-spacing: -.5px; line-height: 1.15; }
.stat .v.up { color: var(--up); } .stat .v.down { color: var(--down); }
.stat .v .sub { display: inline-block; font-size: 12px; font-weight: 600; margin-left: 8px;
           padding: 2px 8px; border-radius: 999px; vertical-align: middle; letter-spacing: 0;
           background: var(--accent-soft); color: var(--muted); }
.stat .v.up .sub { background: var(--up-soft); color: var(--up); }
.stat .v.down .sub { background: var(--down-soft); color: var(--down); }

details { border: 1px solid var(--line); border-radius: var(--radius); margin: 10px 0;
          background: var(--surface); overflow: hidden; box-shadow: var(--shadow); }
summary { cursor: pointer; padding: 13px 16px; font-weight: 600; list-style: none;
          transition: background .15s; }
summary:hover { background: var(--hover); }
summary::-webkit-details-marker { display: none; }
summary::before { content: "▸"; color: var(--accent); margin-right: 10px;
                  display: inline-block; transition: transform .15s; }
details[open] > summary::before { transform: rotate(90deg); }
details > *:not(summary) { padding: 0 16px 14px; }
pre { white-space: pre-wrap; word-break: break-word; background: var(--code-bg);
      border: 1px solid var(--line-soft); border-radius: var(--radius-sm); padding: 12px 14px;
      margin: 8px 0; max-height: 420px; overflow: auto; font-size: 12.5px; line-height: 1.5; }
.sent { border-left: 3px solid var(--accent); padding-left: 12px; margin: 10px 0; }
.ret { border-left: 3px solid var(--up); padding-left: 12px; margin: 10px 0; }
.empty { color: var(--faint); font-style: italic; }

input,textarea,button,select { font: inherit; background: var(--surface-2);
       color: var(--text); border: 1px solid var(--line); border-radius: var(--radius-sm);
       padding: 8px 11px; transition: border-color .15s, box-shadow .15s, background .15s; }
input:focus,textarea:focus,select:focus { outline: none; border-color: var(--accent);
       box-shadow: 0 0 0 3px var(--accent-soft); }
input::placeholder,textarea::placeholder { color: var(--faint); }
button { cursor: pointer; background: var(--raised); font-weight: 600; }
button:hover { background: var(--raised-hover); border-color: var(--line-strong); }
button.primary { background: linear-gradient(180deg,var(--accent),var(--accent-strong));
       border-color: var(--accent-strong); color: #fff; box-shadow: 0 1px 0 #ffffff22 inset, 0 6px 16px -8px var(--accent-strong); }
button.primary:hover { filter: brightness(1.06); }
button.danger { color: var(--down); background: var(--down-soft);
       border-color: color-mix(in srgb, var(--down) 45%, transparent); }
button.danger:hover { background: color-mix(in srgb, var(--down) 18%, transparent); border-color: var(--down); }
form.inline { display: inline; margin: 0; }
textarea { width: 100%; min-height: 96px; resize: vertical; font-family: ui-monospace,monospace; }

.badge { background: var(--accent-soft); color: var(--accent); border: 1px solid var(--accent-line);
         border-radius: 999px; padding: 2px 10px; font-size: 11px; font-weight: 600;
         text-transform: uppercase; letter-spacing: .4px; }
.badge.up { background: var(--up-soft); color: var(--up); border-color: color-mix(in srgb, var(--up) 40%, transparent); }
.badge.down { background: var(--down-soft); color: var(--down); border-color: color-mix(in srgb, var(--down) 40%, transparent); }
.daynav { color: var(--muted); margin: 12px 0 18px; display: flex; gap: 6px;
          flex-wrap: wrap; align-items: center; }

.flash { background: var(--up-soft); border: 1px solid color-mix(in srgb, var(--up) 45%, transparent);
         border-radius: var(--radius-sm);
         padding: 11px 14px; margin: 0 0 18px; color: var(--up); font-weight: 500; }
.flash.err { background: var(--down-soft); border-color: color-mix(in srgb, var(--down) 45%, transparent); color: var(--down); }
.card { border: 1px solid var(--line); border-radius: var(--radius); padding: 22px;
        margin: 16px 0; background: var(--surface); box-shadow: var(--shadow); }
.card h2:first-child { margin-top: 0; }
.row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }

ul.symlist { list-style: none; margin: 10px 0; padding: 0; }
ul.symlist li { display: flex; align-items: center; gap: 12px; padding: 11px 13px;
        border: 1px solid var(--line); border-radius: var(--radius-sm); margin: 6px 0;
        background: var(--surface-2); cursor: grab; transition: border-color .15s, background .15s; }
ul.symlist li:hover { border-color: var(--line-strong); background: var(--hover); }
ul.symlist li.drag { opacity: .4; } ul.symlist li.over { border-color: var(--accent); }
ul.symlist .grip { color: var(--faint); cursor: grab; user-select: none; }
ul.symlist .sym { flex: 1; font-variant-numeric: tabular-nums; font-weight: 600; }
"""


# Nav items: (label, href). The active link is matched against the page title so
# the current tab is highlighted — Run pages fall under Committee.
_NAV = [
    ("Overview", "/"), ("Token usage", "/usage"), ("Telegram", "/telegram"),
    ("Committee", "/committee"), ("Watchlist", "/watchlist"),
    ("Portfolio", "/portfolio"), ("Subscribers", "/subscribers"),
    ("Memory", "/memory"), ("Playbooks", "/playbooks"),
    ("Econ events", "/econ"), ("Sanitizer", "/sanitizer"),
    ("Configure", "/configure"),
]


# Theme is light by default; the header toggle flips to dark and persists the choice
# in localStorage. The head script applies the stored choice before first paint (no
# flash); the body script wires the button and keeps its icon in sync. No-JS → light.
_THEME_HEAD = (
    "<script>try{document.documentElement.dataset.theme="
    "localStorage.getItem('cio-theme')||'light';}catch(e){}</script>"
)
_THEME_JS = """<script>
(function(){
  var btn=document.getElementById('themebtn');
  if(!btn) return;
  function cur(){return document.documentElement.dataset.theme==='dark'?'dark':'light';}
  function paint(){btn.textContent=cur()==='dark'?'\\u2600':'\\u263E';
    btn.title='Switch to '+(cur()==='dark'?'light':'dark')+' theme';}
  paint();
  btn.addEventListener('click',function(){
    var next=cur()==='dark'?'light':'dark';
    document.documentElement.dataset.theme=next;
    try{localStorage.setItem('cio-theme',next);}catch(e){}
    paint();
  });
})();
</script>"""


def _page(title: str, body: str, level: int) -> str:
    active = "Committee" if title == "Run" else title
    nav = "".join(
        f"<a href='{esc(href)}' class='{'active' if label == active else ''}'>"
        f"{esc(label)}</a>"
        for label, href in _NAV
    )
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)} · CIO dev dashboard</title>"
        + _THEME_HEAD
        + f"<style>{_CSS}</style></head><body>"
        "<header>"
        "<span class='brand'><span class='dot'></span>CIO</span>"
        + nav
        + "<button id='themebtn' class='themebtn' type='button' "
          "aria-label='Toggle theme' title='Toggle theme'>☾</button>"
        + f"<span class='lvl'>capture level {esc(level)}</span>"
        "</header><main>" + body + "</main>" + _THEME_JS + "</body></html>"
    )


def render_overview(usage_today, runs, turns, level: int, token_q: str = "") -> str:
    rows = "".join(
        f"<tr><td>{esc(u['service'])}</td><td class='num'>{esc(u['tokens'])}</td></tr>"
        for u in usage_today
    ) or "<tr><td class='empty' colspan='2'>no usage recorded today</td></tr>"

    run_rows = "".join(
        f"<tr><td><a href='/committee/{esc(r['run_id'])}{token_q}'>{esc(r['symbol'])}</a></td>"
        f"<td>{_trigger(r)}</td>"
        f"<td>{esc_ts(r['started'])}</td><td class='num'>{esc(r['calls'])}</td>"
        f"<td class='num'>{esc(r['tokens'])}</td></tr>"
        for r in runs
    ) or "<tr><td class='empty' colspan='5'>no committee runs captured</td></tr>"

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
        f"<table><tr><th>Symbol</th><th>Trigger</th><th>Started</th><th>Calls</th><th>Tokens</th></tr>{run_rows}</table>"
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
        f"<td>{esc(r['symbol'])}</td><td>{_trigger(r)}</td><td>{esc_ts(r['started'])}</td>"
        f"<td class='num'>{esc(r['calls'])}</td><td class='num'>{esc(r['tokens'])}</td></tr>"
        for r in runs
    ) or "<tr><td class='empty' colspan='6'>no committee runs captured</td></tr>"
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
        f"<table><tr><th>Run</th><th>Symbol</th><th>Trigger</th><th>Started</th><th>Calls</th><th>Tokens</th></tr>{rows}</table>"
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


def render_playbooks(rows, level: int, flash: str = "", flash_err: bool = False) -> str:
    """Saved reusable procedures (memory.list_all_playbooks). Steps reference tools,
    not cached numbers, so a playbook never goes stale. Per-row delete (PRG, confirmed).

    *rows* are dicts: id, scope, name, steps, hits, created_at."""
    def _row(r: dict) -> str:
        del_btn = _wipe_form(
            "delete", f"Delete",
            f"Delete playbook {r.get('name')!r}? This cannot be undone.",
            path="/playbooks", pid=r.get("id"),
        )
        return (f"<tr><td>{esc(r.get('name'))}</td>"
                f"<td>{esc(r.get('scope'))}</td>"
                f"<td class='num'>{esc(r.get('hits'))}</td>"
                f"<td class='msg'><pre class='steps'>{esc(r.get('steps'))}</pre></td>"
                f"<td>{esc_ts(r.get('created_at'))}</td>"
                f"<td>{del_btn}</td></tr>")

    body_rows = "".join(_row(r) for r in rows) or (
        "<tr><td class='empty' colspan='6'>no playbooks saved yet — the agent saves "
        "them with save_playbook, or auto-distills them from recurring tasks.</td></tr>")
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    body = (
        "<h1>Playbooks</h1>" + flash_html +
        "<p>Reusable procedures the agent can replay by name. Steps reference "
        "<em>tools</em>, not cached numbers, so a playbook never goes stale — each "
        "run fetches fresh data. <code>hits</code> counts how often it has been recalled.</p>"
        "<table><tr><th>Name</th><th>Scope</th><th>Hits</th><th>Steps</th>"
        f"<th>Created</th><th></th></tr>{body_rows}</table>"
    )
    return _page("Playbooks", body, level)


def render_econ_events(rows, level: int, flash: str = "", flash_err: bool = False) -> str:
    """High-impact economic events the bot alerts on (econ_calendar.list_all).
    Per-row delete (PRG, confirmed). *rows*: id, event_date, name, impact, time_et,
    source, alerted, created_at."""
    def _row(r: dict) -> str:
        del_btn = _wipe_form(
            "delete", "Delete",
            f"Delete event {r.get('name')!r} on {r.get('event_date')}?",
            path="/econ", eid=r.get("id"),
        )
        src = r.get("source") or ""
        src_html = (f"<a href='{esc(src)}' target='_blank' rel='noopener'>link</a>"
                    if src.startswith("http") else esc(src) or "—")
        alerted = "<span class='badge up'>sent</span>" if r.get("alerted") else "—"
        return (f"<tr><td>{esc(r.get('event_date'))}</td>"
                f"<td>{esc(r.get('name'))}</td>"
                f"<td>{esc((r.get('impact') or '').upper())}</td>"
                f"<td>{esc(r.get('time_et'))}</td>"
                f"<td>{src_html}</td>"
                f"<td>{alerted}</td>"
                f"<td>{del_btn}</td></tr>")

    body_rows = "".join(_row(r) for r in rows) or (
        "<tr><td class='empty' colspan='7'>no economic events recorded — NFP auto-seeds, "
        "and the agent adds the rest via the monthly_red_events playbook.</td></tr>")
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    body = (
        "<h1>Economic events</h1>" + flash_html +
        "<p>High-impact releases the bot warns subscribed chats about, ahead of time. "
        "NFP is seeded deterministically (first Friday); CPI/PPI/PCE/FOMC/GDP/Retail are "
        "populated by the agent from verified sources. <code>sent</code> = a heads-up "
        "already went out.</p>"
        "<table><tr><th>Date</th><th>Event</th><th>Impact</th><th>Time</th>"
        f"<th>Source</th><th>Alerted</th><th></th></tr>{body_rows}</table>"
    )
    return _page("Econ events", body, level)


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
        f"<div class='v {upl_cls}'>{esc(_money(upl))}"
        f"<span class='sub'>{esc(summ.get('unrealized_pct'))}%</span></div></div>"
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


# ---------------------------------------------------------------------------
# Configure tab — edit config/committee_models.yaml from the UI
# ---------------------------------------------------------------------------

def _svc_select(name: str, current, services) -> str:
    """A service combo box (class 'svc' so JS can pair it to the model box in the
    same row). If *current* isn't in the known list it's kept as a selected option
    so an unusual value isn't silently dropped."""
    opts = list(services)
    if current and current not in opts:
        opts = [current] + opts
    cells = "".join(
        f"<option value='{esc(s)}'{' selected' if s == current else ''}>{esc(s)}</option>"
        for s in opts
    )
    return f"<select class='svc' name='{esc(name)}'>{cells}</select>"


def _model_select(name: str, current, service, catalog) -> str:
    """A model dropdown listing the *service*'s whole catalog (unlike a datalist,
    a <select> always shows every option, not just ones matching typed text). An
    unknown *current* value is kept as a selected option so it isn't lost. To use a
    model not listed, add it under 'Manage model catalog'. Class 'mdl' lets JS
    repopulate it when the paired service select changes."""
    opts = list(catalog.get(service or "", []) or [])
    if current and current not in opts:
        opts = [current] + opts
    if not opts:
        return (f"<select class='mdl' name='{esc(name)}'>"
                "<option value=''>(none — add under Manage model catalog)</option></select>")
    cells = "".join(
        f"<option value='{esc(o)}'{' selected' if o == current else ''}>{esc(o)}</option>"
        for o in opts
    )
    return f"<select class='mdl' name='{esc(name)}'>{cells}</select>"


# JS: dependent dropdown. When a service select changes, rebuild the model select in
# the same row from that service's catalog (kept in window.__cat), preserving the
# current pick if it still exists, else selecting the first model.
def _configure_js(catalog: dict) -> str:
    import json
    return (
        "<script>window.__cat=" + json.dumps(catalog) + ";"
        "(function(){"
        "document.querySelectorAll('select.svc').forEach(function(sel){"
        "sel.addEventListener('change',function(){"
        "var tr=sel.closest('tr');if(!tr)return;"
        "var m=tr.querySelector('select.mdl');if(!m)return;"
        "var prev=m.value,list=(window.__cat[sel.value]||[]);"
        "m.innerHTML='';"
        "list.forEach(function(name){var o=document.createElement('option');"
        "o.value=name;o.textContent=name;if(name===prev)o.selected=true;m.appendChild(o);});"
        "if(m.selectedIndex<0&&m.options.length)m.selectedIndex=0;"
        "});});})();</script>"
    )


def render_configure(cfg, level: int, services, model_suggestions,
                     path: str = "", flash: str = "", flash_err: bool = False,
                     log_to_file: bool = False, log_file: str = "",
                     log_dir: str = "", log_locked_by_env: bool = False) -> str:
    """Edit committee model routing. One big POST form → /configure.

    Simple agents render as service combo + model box; chain agents (cio/wma)
    render each fallback link with service + model + daily_limit. A collapsible
    section exposes provider connection knobs (base_url / api_key_env / token caps).
    """
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    agents = cfg.get("agents") or {}
    defaults = cfg.get("defaults") or {}

    # --- defaults ---
    def_rows = (
        "<table><tr><th>Scope</th><th>Service</th><th>Model</th></tr>"
        f"<tr><td>defaults</td>"
        f"<td>{_svc_select('defaults:service', defaults.get('service'), services)}</td>"
        f"<td>{_model_select('defaults:model', defaults.get('model'), defaults.get('service'), model_suggestions)}</td></tr></table>"
    )

    # --- simple (single-service) agents ---
    simple_rows, chain_blocks = [], []
    for key, acfg in agents.items():
        if isinstance(acfg, dict) and "chain" in acfg:
            link_rows = "".join(
                "<tr>"
                f"<td class='num'>{i}</td>"
                f"<td>{_svc_select(f'chain:{esc(key)}:{i}:service', (link or {}).get('service'), services)}</td>"
                f"<td>{_model_select(f'chain:{esc(key)}:{i}:model', (link or {}).get('model'), (link or {}).get('service'), model_suggestions)}</td>"
                f"<td><input name='chain:{esc(key)}:{i}:daily_limit' class='num' "
                f"value='{esc((link or {}).get('daily_limit') or '')}' "
                "placeholder='none' inputmode='numeric'></td>"
                "</tr>"
                for i, link in enumerate(acfg.get("chain") or [])
            )
            chain_blocks.append(
                f"<h2>{esc(key)} · fallback chain</h2>"
                "<table><tr><th>#</th><th>Service</th><th>Model</th>"
                "<th>Daily token limit</th></tr>" + link_rows + "</table>"
            )
        elif isinstance(acfg, dict):
            simple_rows.append(
                f"<tr><td>{esc(key)}</td>"
                f"<td>{_svc_select(f'agent:{esc(key)}:service', acfg.get('service'), services)}</td>"
                f"<td>{_model_select(f'agent:{esc(key)}:model', acfg.get('model'), acfg.get('service'), model_suggestions)}</td></tr>"
            )
    simple_tbl = (
        "<table><tr><th>Agent</th><th>Service</th><th>Model</th></tr>"
        + "".join(simple_rows) + "</table>"
    )

    # --- provider connection settings (collapsed) ---
    def _txt(name, val, ph=""):
        return (f"<input name='{esc(name)}' value='{esc(val if val is not None else '')}' "
                f"placeholder='{esc(ph)}'>")
    nim = cfg.get("nim") or {}
    oa = cfg.get("openai") or {}
    cl = cfg.get("claude") or {}
    providers = (
        "<details><summary>Provider connection settings</summary>"
        "<table><tr><th>NIM</th><th>Value</th></tr>"
        f"<tr><td>base_url</td><td>{_txt('provider:nim:base_url', nim.get('base_url'))}</td></tr>"
        f"<tr><td>api_key_env</td><td>{_txt('provider:nim:api_key_env', nim.get('api_key_env'))}</td></tr>"
        f"<tr><td>max_tokens</td><td>{_txt('provider:nim:max_tokens', nim.get('max_tokens'), 'output cap')}</td></tr>"
        "</table>"
        "<table><tr><th>OpenAI</th><th>Value</th></tr>"
        f"<tr><td>base_url</td><td>{_txt('provider:openai:base_url', oa.get('base_url'))}</td></tr>"
        f"<tr><td>api_key_env</td><td>{_txt('provider:openai:api_key_env', oa.get('api_key_env'))}</td></tr>"
        f"<tr><td>token_param</td><td>{_txt('provider:openai:token_param', oa.get('token_param'), 'max_completion_tokens')}</td></tr>"
        f"<tr><td>max_tokens</td><td>{_txt('provider:openai:max_tokens', oa.get('max_tokens'), 'output cap')}</td></tr>"
        "</table>"
        "<table><tr><th>Claude</th><th>Value</th></tr>"
        f"<tr><td>max_thinking_tokens</td><td>{_txt('provider:claude:max_thinking_tokens', cl.get('max_thinking_tokens'), 'blank = SDK default')}</td></tr>"
        "</table></details>"
    )

    # --- model catalog management (collapsed): edits the service→models lists that
    # drive the dropdowns above. Clear a box to remove; add box appends names. ---
    catalog_rows = []
    for svc in services:
        mods = model_suggestions.get(svc, []) or []
        inputs = "".join(
            f"<input name='catalog:{esc(svc)}:{i}' value='{esc(m)}' "
            "style='display:block;margin:3px 0'>"
            for i, m in enumerate(mods)
        )
        catalog_rows.append(
            f"<tr><td style='vertical-align:top'><strong>{esc(svc)}</strong></td>"
            f"<td>{inputs}"
            f"<input name='catalog_add:{esc(svc)}' "
            "placeholder='add model(s) — comma separated' "
            "style='display:block;margin-top:6px'></td></tr>"
        )
    catalog_ui = (
        "<details><summary>Manage model catalog</summary>"
        "<p class='hint'>Clear a box to remove that model; use the bottom box to add "
        "one or more (comma separated). These names populate the model dropdowns above.</p>"
        "<table><tr><th>Service</th><th>Models</th></tr>"
        + "".join(catalog_rows) + "</table></details>"
    )

    # --- logging section: indicator + toggle for date-based file logging ---
    if log_to_file:
        status = (f"<strong style='color:#1a7f37'>ON</strong> — writing to "
                  f"<code>{esc(log_file or '(opening…)')}</code>")
    else:
        status = "<strong>OFF</strong> — logs go to the console only"
    if log_locked_by_env:
        toggle = ("<p class='hint'>Locked by the <code>CIO_LOG_TO_FILE</code> "
                  "environment variable; unset it to control this from here.</p>")
    else:
        want = "0" if log_to_file else "1"
        btn = "Disable file logging" if log_to_file else "Enable file logging"
        toggle = (
            "<form method='post' action='/configure' style='margin-top:8px'>"
            "<input type='hidden' name='form_kind' value='logging'>"
            f"<input type='hidden' name='log_to_file' value='{want}'>"
            f"<button type='submit' class='primary'>{btn}</button>"
            "</form>"
        )
    logging_section = (
        "<h2>Logging</h2>"
        f"<p>Date-based log file on disk: {status}.</p>"
        f"<p class='hint'>Directory: <code>{esc(log_dir)}</code> · one file per day "
        "(<code>cio-YYYY-MM-DD.log</code>). Captures the <code>cio.evidence</code> "
        "lines that confirm which primary-source tools (EDGAR / Finnhub / "
        "ClinicalTrials) actually fired.</p>"
        + toggle
    )

    body = (
        "<h1>Configure committee models</h1>"
        + flash_html
        + (f"<p class='hint'>Editing <code>{esc(path)}</code>. "
           "Pick service and model from the dropdowns; add model names under "
           "“Manage model catalog” below. Saving applies to the next committee run.</p>")
        + "<form method='post' action='/configure'>"
        + "<input type='hidden' name='form_kind' value='models'>"
        + "<h2>Defaults</h2>" + def_rows
        + "<h2>Agents</h2>" + simple_tbl
        + "".join(chain_blocks)
        + providers
        + catalog_ui
        + "<p style='margin-top:16px'><button type='submit' class='primary'>Save changes</button></p>"
        + "</form>"
        + logging_section
        + _configure_js({s: list(model_suggestions.get(s, []) or []) for s in services})
    )
    return _page("Configure", body, level)
