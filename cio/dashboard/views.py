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

:root { --side-w: 250px; --side-w-min: 66px; }

/* Left sidebar nav — fixed, vertical, collapsible to an icon rail. Hierarchical:
   category groups (.group > .grouplabel) over their links. State (expanded/collapsed)
   lives on <html data-nav> and persists in localStorage, applied pre-paint (no flash). */
.sidebar { position: fixed; top: 0; left: 0; bottom: 0; z-index: 30; width: var(--side-w);
       display: flex; flex-direction: column; gap: 1px; padding: 14px 12px;
       overflow-y: auto; overflow-x: hidden;
       background: var(--header-bg); backdrop-filter: blur(14px) saturate(150%);
       border-right: 1px solid var(--line); transition: width .18s ease; }
:root[data-nav="collapsed"] .sidebar { width: var(--side-w-min); padding: 14px 9px; }

.sidehead { display: flex; align-items: center; gap: 8px; padding: 2px 6px 8px; }
.sidebar .brand { font-weight: 700; letter-spacing: .3px; font-size: 15px; color: var(--text);
       display: flex; align-items: center; gap: 10px; min-width: 0; }
.sidebar .brand .dot { width: 9px; height: 9px; border-radius: 50%; flex: none;
       background: linear-gradient(135deg,var(--accent),var(--accent-2));
       box-shadow: 0 0 12px var(--accent); }
.navtoggle { margin-left: auto; flex: none; width: 28px; height: 28px; padding: 0;
       display: inline-flex; align-items: center; justify-content: center; font-size: 14px;
       line-height: 1; background: var(--surface); border: 1px solid var(--line);
       border-radius: 8px; color: var(--muted); cursor: pointer;
       transition: background .15s, border-color .15s, color .15s; }
.navtoggle:hover { background: var(--hover); color: var(--text); border-color: var(--line-strong); }

.sidebar .search { width: 100%; margin: 0 0 6px; padding: 7px 11px; font-size: 13px; }

.sidebar .group { display: flex; flex-direction: column; gap: 1px; }
.sidebar .grouplabel { font-size: 10px; font-weight: 700; text-transform: uppercase;
       letter-spacing: .7px; color: var(--faint); padding: 13px 10px 4px; }
.sidebar a { display: flex; align-items: center; gap: 11px; color: var(--muted);
       text-decoration: none; font-weight: 500; padding: 8px 10px; border-radius: 8px;
       line-height: 1.15; white-space: nowrap; transition: background .15s, color .15s; }
.sidebar a:hover { color: var(--text); background: var(--hover); }
.sidebar a.active { color: var(--text); background: var(--accent-soft);
       box-shadow: inset 0 0 0 1px var(--accent-line); }
.sidebar a .ico { flex: none; width: 22px; text-align: center; font-size: 15px; line-height: 1; }
.sidebar a .label { overflow: hidden; text-overflow: ellipsis; }

.sidebar .foot { margin-top: auto; display: flex; align-items: center; gap: 9px;
       padding: 12px 6px 2px; }
.themebtn { flex: none; padding: 0; width: 30px; height: 30px; display: inline-flex;
       align-items: center; justify-content: center; font-size: 14px; line-height: 1;
       background: var(--surface); border: 1px solid var(--line); border-radius: 999px;
       color: var(--text); cursor: pointer; transition: background .15s, border-color .15s; }
.themebtn:hover { background: var(--hover); border-color: var(--line-strong); }
.lvl { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase;
       letter-spacing: .5px; padding: 5px 11px; border: 1px solid var(--line);
       border-radius: 999px; background: var(--surface); white-space: nowrap; }

/* collapsed rail — icons only, text bits hidden, controls centered */
:root[data-nav="collapsed"] .sidebar .label,
:root[data-nav="collapsed"] .sidebar .grouplabel,
:root[data-nav="collapsed"] .sidebar .brandtext,
:root[data-nav="collapsed"] .sidebar .search,
:root[data-nav="collapsed"] .sidebar .lvl { display: none; }
:root[data-nav="collapsed"] .sidebar a { justify-content: center; padding: 9px 0; }
:root[data-nav="collapsed"] .sidehead { padding: 2px 0 8px; justify-content: center; }
:root[data-nav="collapsed"] .navtoggle { margin: 0; }
:root[data-nav="collapsed"] .sidebar .brand { justify-content: center; }
:root[data-nav="collapsed"] .sidebar .foot { flex-direction: column; padding: 12px 0 2px; }

main { margin-left: var(--side-w); margin-right: auto; padding: 30px 30px 72px;
       max-width: 1180px; transition: margin-left .18s ease; }
:root[data-nav="collapsed"] main { margin-left: var(--side-w-min); }

/* narrow screens: never push content full width; expanded sidebar overlays */
@media (max-width: 860px) {
  main, :root[data-nav="collapsed"] main { margin-left: var(--side-w-min); }
  :root[data-nav="expanded"] .sidebar { box-shadow: var(--shadow); }
}
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
.badge.warn { background: color-mix(in srgb, #d99e00 18%, transparent); color: #b58100; border-color: color-mix(in srgb, #d99e00 40%, transparent); }
.muted { color: var(--muted); font-size: 12px; }
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


# Nav is hierarchical: a list of (category, items) where each item is (icon, label, href).
# An empty category renders no header (top-level links). The active link is matched
# against the page title so the current tab is highlighted — Run pages fall under Committee.
_NAV = [
    ("", [
        ("🏠", "Overview", "/"),
    ]),
    ("Committee", [
        ("🏛", "Committee", "/committee"),
        ("📈", "Expectancy", "/expectancy"),
        ("📓", "Playbooks", "/playbooks"),
    ]),
    ("Signals", [
        ("🎯", "Alpha Hunter", "/alpha"),
        ("⭐", "Watchlist", "/watchlist"),
        ("📊", "Indicators", "/indicators"),
    ]),
    ("Markets", [
        ("💼", "Portfolio", "/portfolio"),
        ("🗓", "Econ events", "/econ"),
    ]),
    ("Messaging", [
        ("✈", "Telegram", "/telegram"),
        ("👥", "Subscribers", "/subscribers"),
        ("📜", "Detailed history", "/detailed"),
    ]),
    ("Agent", [
        ("🧠", "Memory", "/memory"),
        ("🧩", "Skills", "/skills"),
        ("🧪", "Sanitizer", "/sanitizer"),
    ]),
    ("System", [
        ("🪙", "Token usage", "/usage"),
        ("🩺", "Data Health", "/health"),
        ("⚙", "Configure", "/configure"),
    ]),
]


# Theme is light by default; the header toggle flips to dark and persists the choice
# in localStorage. The head script applies the stored choice before first paint (no
# flash); the body script wires the button and keeps its icon in sync. No-JS → light.
_THEME_HEAD = (
    "<script>try{var d=document.documentElement;"
    "d.dataset.theme=localStorage.getItem('cio-theme')||'light';"
    "d.dataset.nav=localStorage.getItem('cio-nav')||"
    "(innerWidth<=860?'collapsed':'expanded');}catch(e){}</script>"
)
_THEME_JS = """<script>
(function(){
  var root=document.documentElement;
  // theme toggle
  var btn=document.getElementById('themebtn');
  if(btn){
    var cur=function(){return root.dataset.theme==='dark'?'dark':'light';};
    var paint=function(){btn.textContent=cur()==='dark'?'\\u2600':'\\u263E';
      btn.title='Switch to '+(cur()==='dark'?'light':'dark')+' theme';};
    paint();
    btn.addEventListener('click',function(){
      var next=cur()==='dark'?'light':'dark';
      root.dataset.theme=next;
      try{localStorage.setItem('cio-theme',next);}catch(e){}
      paint();
    });
  }
  // sidebar collapse/expand
  var nt=document.getElementById('navtoggle');
  var navState=function(){return root.dataset.nav==='collapsed'?'collapsed':'expanded';};
  var navPaint=function(){if(!nt)return; var c=navState()==='collapsed';
    nt.textContent=c?'\\u00BB':'\\u00AB';
    nt.title=(c?'Expand':'Collapse')+' sidebar';
    nt.setAttribute('aria-label',nt.title);};
  navPaint();
  if(nt)nt.addEventListener('click',function(){
    var next=navState()==='collapsed'?'expanded':'collapsed';
    root.dataset.nav=next;
    try{localStorage.setItem('cio-nav',next);}catch(e){}
    navPaint();
  });
  // search filter — hides non-matching links and empty category groups
  var s=document.getElementById('navsearch');
  if(s)s.addEventListener('input',function(){
    var q=s.value.trim().toLowerCase();
    root.querySelectorAll('.sidebar .group').forEach(function(g){
      var any=false;
      g.querySelectorAll('a').forEach(function(a){
        var hit=a.textContent.toLowerCase().indexOf(q)>=0;
        a.style.display=hit?'':'none'; if(hit)any=true;
      });
      g.style.display=any?'':'none';
    });
  });
})();
</script>"""


def render_indicators_form(level: int, error: str = "",
                           symbol: str = "", profile: str = "committee") -> str:
    """指標視覺化 — symbol entry form; submits to GET /indicators?symbol=…"""
    err = (f"<p style='color:#d92b2b'>{esc(error)}</p>" if error else "")

    def _prof_opt(v, label):
        sel = " selected" if profile == v else ""
        return f"<option value='{esc(v)}'{sel}>{esc(label)}</option>"

    body = (
        "<h2>指標視覺化 — Technical indicators</h2>"
        "<p>Render candlesticks + MA, RSI / MACD / KDJ sub-panels and divergence "
        "markers (interactive). Same signals the committee profile uses.</p>"
        "<p class='hint'>Candle style is a global setting — change it on the "
        "<a href='/configure'>Configure</a> tab.</p>"
        + err +
        "<form method='get' action='/indicators'>"
        "<input name='symbol' placeholder='LRCX' autofocus value='" + esc(symbol) + "' "
        "style='padding:6px 8px;font-size:14px'/> "
        "<select name='profile' style='padding:6px'>"
        + _prof_opt("committee", "committee")
        + _prof_opt("swing", "swing")
        + _prof_opt("monitor", "monitor") +
        "</select> "
        "<button type='submit' style='padding:6px 12px'>Render</button>"
        "</form>"
    )
    return _page("Indicators", body, level)


_FRESH_COLORS = {
    "fresh": "#1a7f37", "stale": "#9a6700", "very_stale": "#bc4c00",
    "error": "#d92b2b", "no_data": "#57606a",
}


def _age_str(secs) -> str:
    if secs is None:
        return "—"
    secs = int(secs)
    if secs < 90:
        return f"{secs}s ago"
    if secs < 5400:
        return f"{secs // 60}m ago"
    if secs < 36 * 3600:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def render_health(summary: dict, level: int) -> str:
    """Data Health — per-source freshness heartbeats + a worst-required rollup.

    Ported from worldmonitor's data-freshness tracker. Answers "is each source's
    data actually live?" — distinct from a quote's bar-freshness."""
    overall = summary.get("overall", "no_data")
    oc = _FRESH_COLORS.get(overall, "#57606a")
    rows = []
    for r in summary.get("sources", []):
        st = r.get("status", "no_data")
        c = _FRESH_COLORS.get(st, "#57606a")
        req = "✔" if r.get("required") else ""
        err = esc(str(r["last_error"])) if r.get("last_error") else ""
        cnt = r.get("count")
        rows.append(
            "<tr>"
            f"<td>{esc(str(r.get('name', r.get('id', ''))))}</td>"
            f"<td style='text-align:center'>{req}</td>"
            f"<td><span style='color:{c};font-weight:600'>{esc(st)}</span></td>"
            f"<td>{esc(_age_str(r.get('age_seconds')))}</td>"
            f"<td style='text-align:right'>{cnt if cnt is not None else '—'}</td>"
            f"<td style='color:#d92b2b'>{err}</td>"
            "</tr>"
        )
    body = (
        "<h2>Data Health</h2>"
        "<p>When each external source last returned data — the source heartbeat the "
        "committee bundle leans on (distinct from a quote's bar-freshness). A "
        "<b>required</b> source going stale or dark reddens the overall status, so a "
        "panel never shows a confident read over missing inputs.</p>"
        f"<p>Overall (required sources): "
        f"<span style='color:{oc};font-weight:700'>{esc(overall)}</span></p>"
        "<table><tr><th>Source</th><th>Req</th><th>Status</th><th>Last data</th>"
        "<th>Count</th><th>Error</th></tr>"
        + "".join(rows) +
        "</table>"
        "<p class='hint'>fresh &lt;15m · stale &lt;2h · very_stale ≥2h · "
        "no_data never seen · error last call failed. Sources are opt-in: an unset "
        "key reads no_data — honest, not broken.</p>"
    )
    return _page("Data Health", body, level)


def _page(title: str, body: str, level: int) -> str:
    active = "Committee" if title == "Run" else title
    groups = []
    for category, items in _NAV:
        links = "".join(
            f"<a href='{esc(href)}' title='{esc(label)}' "
            f"class='{'active' if label == active else ''}'>"
            f"<span class='ico'>{icon}</span>"
            f"<span class='label'>{esc(label)}</span></a>"
            for icon, label, href in items
        )
        head = f"<div class='grouplabel'>{esc(category)}</div>" if category else ""
        groups.append(f"<div class='group'>{head}{links}</div>")
    nav = "".join(groups)
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)} · CIO dev dashboard</title>"
        + _THEME_HEAD
        + f"<style>{_CSS}</style></head><body>"
        "<nav class='sidebar'>"
        "<div class='sidehead'>"
        "<span class='brand'><span class='dot'></span>"
        "<span class='brandtext'>CIO</span></span>"
        "<button id='navtoggle' class='navtoggle' type='button'>«</button>"
        "</div>"
        "<input id='navsearch' class='search' type='search' placeholder='Search…' "
        "aria-label='Filter navigation' autocomplete='off'>"
        + nav
        + "<div class='foot'>"
          "<button id='themebtn' class='themebtn' type='button' "
          "aria-label='Toggle theme' title='Toggle theme'>☾</button>"
        + f"<span class='lvl'>capture level {esc(level)}</span>"
        "</div></nav>"
        "<main>" + body + "</main>" + _THEME_JS + "</body></html>"
    )


def render_overview(usage_today, runs, turns, level: int, token_q: str = "",
                    runtime: dict | None = None, flash: str = "",
                    flash_err: bool = False) -> str:
    # Runtime health strip: which code the live process runs vs what's on disk,
    # plus last night's invariant violations. A stale process (the 2026-06-10
    # incident: bot ran pre-fix code for hours after the commit) shows red here.
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    rt = ""
    if runtime:
        stale = runtime.get("stale")
        ver_line = (f"running <code>{esc(runtime.get('boot_version') or '?')}</code> "
                    f"since {esc(runtime.get('boot_time') or '?')} "
                    f"(pid {esc(runtime.get('boot_pid') or '?')}) · "
                    f"source tree <code>{esc(runtime.get('repo_version') or '?')}</code>")
        if stale:
            ver_line += f"<br><b style='color:#c0392b'>⚠ {esc(stale)}</b>"
        viol = runtime.get("violations") or []
        if viol:
            v_line = "<br>".join(f"⚠ {esc(v)}" for v in viol)
            v_block = f"<p style='color:#c0392b'><b>Invariant violations:</b><br>{v_line}</p>"
        else:
            v_block = "<p style='color:#27ae60'>invariants: OK</p>"
        # The persisted violation list is last maintenance's snapshot — after a
        # restart it can show an already-resolved I6 until the next nightly run.
        # The button forces backup + maintenance now so the snapshot refreshes.
        maint_btn = (
            "<form class='inline' method='post' action='/' "
            "onsubmit=\"return confirm('Run maintenance now? Backs up both DBs, "
            "then purges expired notes, prunes old turns and re-checks invariants.');\">"
            "<input type='hidden' name='action' value='run_maintenance'>"
            "<button type='submit'>Run maintenance now</button></form>"
        )
        rt = f"<h2>Runtime {maint_btn}</h2><p>{ver_line}</p>{v_block}"

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
        f"{flash_html}"
        f"{rt}"
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


def render_detailed(days, selected_day: str | None, content: str | None,
                    enabled: bool, level: int, flash: str = "",
                    flash_err: bool = False) -> str:
    """Detailed conversation history viewer (convlog day files). Mirrors the Telegram
    tab: a day selector across logged days, the selected day's full text, and a
    per-day delete button (irreversible, confirmed, auth-gated, PRG).

    *days* is convlog.list_days() — [{day, entries, bytes}], newest first.
    *content* is the selected day's raw text (None when no day selected/missing)."""
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    status = (
        "<p class='ok'>Logging is <strong>ON</strong> (CIO_DETAILED_LOG).</p>"
        if enabled else
        "<p class='empty'>Logging is <strong>OFF</strong>. Set "
        "<code>CIO_DETAILED_LOG=1</code> to capture future conversations. "
        "Existing day files (if any) are still viewable below.</p>"
    )

    def _navlink(label: str, href: str, active: bool) -> str:
        cls = " class='badge'" if active else ""
        return f"<a{cls} href='{esc(href)}'>{esc(label)}</a>"
    nav_items = [_navlink("All", "/detailed", selected_day is None)]
    for d in (days or []):
        nav_items.append(_navlink(
            f"{d['day']} ({d['entries']})", f"/detailed?day={d['day']}",
            d["day"] == selected_day))
    day_nav = ("<div class='daynav'>Days: " + " · ".join(nav_items) + "</div>"
               if days else "")

    if selected_day:
        del_btn = _wipe_form(
            "wipe_day", "Delete this day",
            f"Delete all detailed history for {selected_day}? This cannot be undone.",
            path="/detailed", day=selected_day,
        )
        if content is None:
            inner = f"<p class='empty'>no detailed history for {esc(selected_day)}.</p>"
        else:
            inner = (f"<h2>{esc(selected_day)} {del_btn}</h2>"
                     f"<pre class='steps'>{esc(content)}</pre>")
    elif days:
        def _day_row(d: dict) -> str:
            del_btn = _wipe_form(
                "wipe_day", "Delete",
                f"Delete all detailed history for {d['day']}? This cannot be undone.",
                path="/detailed", day=d["day"],
            )
            return (f"<tr><td><a href='/detailed?day={esc(d['day'])}'>{esc(d['day'])}</a></td>"
                    f"<td class='num'>{esc(d['entries'])}</td>"
                    f"<td class='num'>{esc(d['bytes'])}</td>"
                    f"<td>{del_btn}</td></tr>")
        rows = "".join(_day_row(d) for d in days)
        inner = ("<table><tr><th>Day (local)</th><th>Entries</th><th>Bytes</th>"
                 f"<th></th></tr>{rows}</table>"
                 "<p class='empty'>Pick a day above to read its full log.</p>")
    else:
        inner = "<p class='empty'>no detailed history logged yet.</p>"

    body = ("<h1>Detailed conversation history</h1>" + flash_html + status
            + day_nav + inner)
    return _page("Detailed history", body, level)


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
        # Chat-scoped playbooks can be promoted to global (shared by all chats);
        # global ones already are, so no button there.
        promote_btn = ""
        if (r.get("scope") or "") != "global":
            promote_btn = _action_form(
                "promote", "Promote to global",
                f"Promote {r.get('name')!r} to GLOBAL (all chats)? This overwrites any "
                f"global playbook of the same name and removes this chat-scoped copy.",
                path="/playbooks", pid=r.get("id"),
            )
        return (f"<tr><td>{esc(r.get('name'))}</td>"
                f"<td>{esc(r.get('scope'))}</td>"
                f"<td class='num'>{esc(r.get('hits'))}</td>"
                f"<td class='msg'><pre class='steps'>{esc(r.get('steps'))}</pre></td>"
                f"<td>{esc_ts(r.get('created_at'))}</td>"
                f"<td>{promote_btn} {del_btn}</td></tr>")

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


# Status -> badge css modifier (theme-aware .badge.up/.down/.warn; plain = neutral).
_SKILL_BADGE = {"PROPOSED": "", "VERIFIED": "warn", "APPROVED": "up",
                "ACTIVE": "up", "REJECTED": "down", "RETIRED": ""}


def render_skills(records, level: int, flash: str = "", flash_err: bool = False) -> str:
    """Self-authored harness-skill approval queue (alternative to the admin CLI).

    The agent may only PROPOSE a skill (harness_propose_skill); it can never
    approve its own. The operator drives the same gate here as on the CLI:
    Verify -> Approve -> Activate, with Approve refused before Verify and Activate
    before Approve (enforced server-side in store.transition, not in this view —
    the buttons are just the surface). *records* are store.all_records() dicts."""
    def _form(action: str, label: str, sid: str, confirm: str,
              danger: bool = False, extra: str = "") -> str:
        cls = "danger" if danger else ""
        return (
            f"<form class='inline' method='post' action='/skills' "
            f"onsubmit=\"return confirm('{esc(confirm)}');\">"
            f"<input type='hidden' name='action' value='{esc(action)}'>"
            f"<input type='hidden' name='id' value='{esc(sid)}'>{extra}"
            f"<button type='submit' class='{cls}'>{esc(label)}</button></form>")

    def _actions(r: dict) -> str:
        sid, st, nm = r.get("id"), r.get("status_label"), r.get("name")
        out = []
        if st in ("PROPOSED", "REJECTED"):
            out.append(_form("verify", "Verify", sid,
                f"Verify {nm!r}? Runs committed test cases (candidates.py) if present, "
                "otherwise records a manual owner attestation."))
        if st == "VERIFIED":
            who = ("<input name='by' value='operator' aria-label='approver' "
                   "style='width:84px;padding:3px;margin-right:4px'>")
            out.append(_form("approve", "Approve", sid,
                f"Approve {nm!r} for activation?", extra=who))
        if st == "APPROVED":
            out.append(_form("activate", "Activate", sid, f"Activate {nm!r} now?"))
        if st in ("PROPOSED", "VERIFIED"):
            out.append(_form("reject", "Reject", sid, f"Reject {nm!r}?", danger=True))
        if st in ("VERIFIED", "APPROVED", "ACTIVE"):
            out.append(_form("retire", "Retire", sid, f"Retire {nm!r}?", danger=True))
        return " ".join(out) or "<span class='hint'>—</span>"

    def _row(r: dict) -> str:
        st = r.get("status_label") or "?"
        badge = f"<span class='badge {_SKILL_BADGE.get(st, '')}'>{esc(st)}</span>"
        spec = (f"<pre class='steps'>{esc(r.get('rule_spec'))}</pre>"
                if r.get("rule_spec") else "")
        return (
            f"<tr><td><code>{esc(r.get('id'))}</code></td>"
            f"<td>{esc(r.get('name'))}<br>"
            f"<span class='hint'>{esc(r.get('kind'))} · {esc(r.get('origin'))}</span></td>"
            f"<td>{badge}</td>"
            f"<td class='msg'>{esc(r.get('trigger'))}{spec}</td>"
            f"<td>{esc(r.get('approved_by') or '—')}</td>"
            f"<td>{esc_ts(r.get('created_at'))}</td>"
            f"<td>{_actions(r)}</td></tr>")

    body_rows = "".join(_row(r) for r in records) or (
        "<tr><td class='empty' colspan='7'>no skills proposed yet — the agent files "
        "them with the harness_propose_skill tool when a user catches a defect it "
        "could not have caught.</td></tr>")
    flash_html = (f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
                  if flash else "")
    body = (
        "<h1>Self-authored skills</h1>" + flash_html +
        "<p>Deterministic checks the agent proposed after a user found a defect. The "
        "agent can <em>propose</em> but never approve its own — drive the gate here: "
        "<b>Verify</b> (runs committed test cases from <code>candidates.py</code>, else "
        "a manual owner attestation) → <b>Approve</b> → <b>Activate</b>. Approve is "
        "refused before Verify, Activate before Approve — identical to "
        "<code>python&nbsp;-m&nbsp;cio.harness.admin</code>.</p>"
        "<table><tr><th>ID</th><th>Name</th><th>Status</th><th>Trigger / spec</th>"
        f"<th>Approved by</th><th>Created</th><th>Actions</th></tr>{body_rows}</table>")
    return _page("Skills", body, level)


_REGIME_CLASS = {"GREEN": "up", "YELLOW": "warn", "RED": "down", "UNKNOWN": ""}

# Coverage-flag display config: (badge_css_modifier, short_label, tooltip_text)
_COV_FLAG_META: dict[str, tuple[str, str, str]] = {
    "under_covered": (
        "up",
        "under-covered",
        "Fewer analysts than expected for this market cap → catalyst news diffuses slowly → edge window (Hong, Lim & Stein 2000). Earnings score amplified.",
    ),
    "saturated": (
        "warn",
        "saturated",
        "More analysts than expected → news prices in fast, little edge window. Earnings score dampened.",
    ),
    "value_trap_floor": (
        "down",
        "value-trap",
        "0 analysts + market cap < $1 B → un-investable neglect. Coverage amplification forced to 0 (no edge).",
    ),
    "institutionally_neglected": (
        "up",
        "inst-neglected",
        "Institutional ownership < 30 % → reinforces analyst neglect signal; slow repricing expected.",
    ),
    "institutionally_crowded": (
        "warn",
        "inst-crowded",
        "Institutional ownership > 70 % → crowded from both angles; fast price discovery.",
    ),
    "count_only": (
        "",
        "count-only",
        "No market-cap data → edge estimated from raw analyst count only (less precise; conservative).",
    ),
}


def _cov_flag_badge(flag: str | None) -> str:
    """Return a colored <span class='badge'> for a CovFlag value."""
    if not flag:
        return "—"
    meta = _COV_FLAG_META.get(flag)
    if not meta:
        return esc(flag)
    mod, label, tip = meta
    cls = f"badge {mod}".strip() if mod else "badge"
    return f"<span class='{cls}' title='{esc(tip)}'>{esc(label)}</span>"


def _cov_edge_tip(edge: float | None) -> str:
    """Human-readable tooltip for a CovEdge value."""
    if edge is None:
        return "Coverage edge unknown"
    mult = 1.0 + 0.30 * (float(edge) - 50.0) / 50.0
    if edge >= 70:
        interp = "significantly under-covered"
    elif edge >= 55:
        interp = "slightly under-covered"
    elif edge >= 45:
        interp = "neutral coverage"
    elif edge >= 30:
        interp = "slightly over-covered"
    else:
        interp = "saturated / value-trap"
    return f"Coverage edge {edge:.0f}/100 — {interp}. Earnings score ×{mult:.2f}."


def _alpha_num(x, suffix: str = "") -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:g}{suffix}"
    return f"{esc(x)}{suffix}"


def render_alpha(latest, runs, level: int, threshold: float = 80.0, flash: str = "",
                 flash_err: bool = False) -> str:
    """Alpha Hunter tab (PRD §7): regime light, sector ranking, selected candidates,
    a Run button, a selection-threshold control, and recent run history. *latest* is
    alpha_store.latest_run() (or None); *runs* is alpha_store.list_runs()."""
    flash_html = (f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
                  if flash else "")

    run_btn = (
        "<form class='inline' method='post' action='/alpha'>"
        "<input type='hidden' name='action' value='run_hunter'>"
        "<button type='submit'>▶ Run Alpha Hunter</button></form>"
    )
    thr_form = (
        "<form class='inline' method='post' action='/alpha' style='margin-left:10px'>"
        "<input type='hidden' name='action' value='set_threshold'>"
        "<label>Selection threshold (Final ≥) "
        f"<input type='number' name='threshold' min='0' max='100' step='1' "
        f"value='{esc(threshold)}' style='width:5em'></label> "
        "<button type='submit'>Save</button></form>"
    )
    controls = f"<div style='display:flex;align-items:center;flex-wrap:wrap'>{run_btn}{thr_form}</div>"
    intro = ("<p>Deterministic NASDAQ swing funnel — Market → Sector → Quality → "
             f"Earnings → Momentum → Ranking. Zero LLM cost. A run publishes every "
             f"candidate scoring <b>Final ≥ {esc(threshold)}</b> to the "
             "<code>Alpha-yyyy-mm-dd</code> watchlist and sets it active, so Telegram "
             "<code>/watchlist</code> shows it immediately.</p>")

    if latest is None:
        body = ("<h1>Alpha Hunter</h1>" + flash_html + intro + controls +
                "<p class='empty'>No runs yet. Click <b>Run Alpha Hunter</b> to scan "
                "the universe (may take a minute on a cold cache).</p>")
        return _page("Alpha Hunter", body, level)

    reg = (latest.get("regime") or "UNKNOWN")
    light = (f"<span class='badge {_REGIME_CLASS.get(reg, '')}'>{esc(reg)}</span> "
             f"<span class='muted'>{esc(latest.get('regime_detail') or '')}</span>")

    # Sectors.
    sect_rows = "".join(
        f"<tr><td>{esc(s.get('ticker'))}</td>"
        f"<td class='num'>{_alpha_num(s.get('rs'))}</td>"
        f"<td class='num'>{_alpha_num(s.get('ret_3m'), '%')}</td>"
        f"<td class='num'>{_alpha_num(s.get('ret_6m'), '%')}</td></tr>"
        for s in (latest.get("sectors") or [])
    ) or "<tr><td class='empty' colspan='4'>no sector data</td></tr>"

    # Candidates.
    def _cand_row(c: dict) -> str:
        anlst = c.get("analyst_count")
        anlst_tip = (
            f"{anlst} analyst(s) covering this name"
            if anlst is not None else "Analyst count unavailable (no Finnhub key or no coverage data)"
        )
        edge = c.get("coverage_edge")
        edge_tip = _cov_edge_tip(edge)
        return (
            f"<tr><td class='num'>{esc(c.get('rank'))}</td>"
            f"<td><b>{esc(c.get('ticker'))}</b></td>"
            f"<td>{esc(c.get('sector'))}</td>"
            f"<td class='num'>{_alpha_num(c.get('final'))}</td>"
            f"<td class='num'>{_alpha_num(c.get('momentum'))}</td>"
            f"<td class='num'>{_alpha_num(c.get('trend'))}</td>"
            f"<td class='num'>{_alpha_num(c.get('earnings'))}</td>"
            f"<td class='num'>{_alpha_num(c.get('revenue_growth'), '%')}</td>"
            f"<td class='num'>{_alpha_num(c.get('fwd_eps_growth'), '%')}</td>"
            f"<td class='num'>{_alpha_num(c.get('surprise'))}</td>"
            f"<td class='num' title='{esc(anlst_tip)}'>{_alpha_num(anlst)}</td>"
            f"<td class='num' title='{esc(edge_tip)}'>{_alpha_num(edge)}</td>"
            f"<td>{_cov_flag_badge(c.get('coverage_flag'))}</td></tr>"
        )

    cand_rows = "".join(
        _cand_row(c) for c in (latest.get("candidates") or [])
    ) or "<tr><td class='empty' colspan='13'>no candidates met the selection threshold</td></tr>"

    wl_link = ""
    if latest.get("watchlist_id"):
        wl_link = (f" · published <a href='/watchlist?wl={esc(latest['watchlist_id'])}'>"
                   f"{esc(latest.get('watchlist_name'))}</a>")

    # Run history.
    hist_rows = "".join(
        f"<tr><td>{esc(r.get('run_date'))}</td>"
        f"<td><span class='badge {_REGIME_CLASS.get(r.get('regime',''), '')}'>"
        f"{esc(r.get('regime'))}</span></td>"
        f"<td class='num'>{esc(r.get('candidate_count'))}</td>"
        f"<td class='num'>{esc(r.get('universe_size'))}</td>"
        f"<td>{(f'<a href=' + chr(39) + '/watchlist?wl=' + str(r.get('watchlist_id')) + chr(39) + '>' + esc(r.get('watchlist_name') or '') + '</a>') if r.get('watchlist_id') else '—'}</td>"
        f"<td>{esc_ts(r.get('created_at'))}</td></tr>"
        for r in (runs or [])
    ) or "<tr><td class='empty' colspan='6'>no runs</td></tr>"

    body = (
        "<h1>Alpha Hunter</h1>" + flash_html + intro + controls +
        f"<h2>Latest run — {esc(latest.get('run_date'))}{wl_link}</h2>"
        f"<p>Market regime: {light}</p>"
        "<h3>Sector ranking <span class='muted'>(RS = 0.5·3M + 0.5·6M)</span></h3>"
        "<table><tr><th>Sector</th><th>RS</th><th>3M</th><th>6M</th></tr>"
        f"{sect_rows}</table>"
        f"<h3>Selected candidates <span class='muted'>(Final ≥ {esc(threshold)})</span></h3>"
        "<table><tr>"
        "<th title='Rank within this run'>#</th>"
        "<th title='Stock ticker symbol'>Ticker</th>"
        "<th title='GICS sector'>Sector</th>"
        "<th title='Composite score (0-100): weighted blend of momentum, trend, earnings, growth, surprise. Threshold filter applied here.'>Final</th>"
        "<th title='Momentum score (0-100): price relative strength vs QQQ benchmark'>Mom</th>"
        "<th title='Trend score (0-100): EMA alignment and breakout structure'>Trend</th>"
        "<th title='Earnings score (0-100): EPS beat magnitude and guidance, before coverage amplification'>Earn</th>"
        "<th title='Revenue growth % year-over-year'>Rev</th>"
        "<th title='Forward EPS growth % (next fiscal year estimate vs current)'>fEPS</th>"
        "<th title='Earnings surprise score (0-100): beat rate and magnitude across last 4 quarters'>Surp</th>"
        "<th title='Analyst count: total analysts covering this name (strong_buy + buy + hold + sell + strong_sell). Hover each row for detail. Requires Finnhub key.'>Anlst</th>"
        "<th title='Coverage-density edge (0-100): 50=neutral, >50=under-covered for size (edge), <50=saturated (no edge). Multiplies earnings score by up to ×1.30 or down to ×0.70. Hover each row for the exact multiplier.'>CovEdge</th>"
        "<th title='Coverage flag: qualitative label for the coverage situation. Hover the badge for a full explanation.'>CovFlag</th>"
        f"</tr>{cand_rows}</table>"
        "<details><summary>Column guide — Selected candidates</summary>"
        "<table>"
        "<tr><th>Column</th><th>What it measures</th><th>How to read it</th></tr>"
        "<tr><td><b>Final</b></td><td>Composite score 0–100</td><td>Weighted blend: 30% Mom + 20% Trend + 30% Earn (coverage-amplified) + 10% Rev + 10% fEPS. Threshold (default 80) filters this column.</td></tr>"
        "<tr><td><b>Mom</b></td><td>Price momentum 0–100</td><td>Relative strength vs QQQ over 3M/6M. Higher = outperforming the index.</td></tr>"
        "<tr><td><b>Trend</b></td><td>Technical trend 0–100</td><td>EMA alignment + breakout structure. Higher = cleaner uptrend.</td></tr>"
        "<tr><td><b>Earn</b></td><td>Earnings quality 0–100</td><td>EPS beat magnitude + guidance. <em>Before</em> coverage amplification — compare with Final to see the coverage lift.</td></tr>"
        "<tr><td><b>Rev</b></td><td>Revenue growth %</td><td>Year-over-year. Raw fundamental, not scored.</td></tr>"
        "<tr><td><b>fEPS</b></td><td>Forward EPS growth %</td><td>Next fiscal year estimate vs current. Captures consensus expectation.</td></tr>"
        "<tr><td><b>Surp</b></td><td>Earnings surprise 0–100</td><td>Beat rate + magnitude over last 4 quarters. Anchors the catalyst evidence.</td></tr>"
        "<tr><td><b>Anlst</b></td><td>Analyst count</td><td>Total analysts covering (any rating). Context: ~$300M cap expects ~3; ~$3B expects ~11; ~$30B expects ~19. Requires Finnhub key. — = unavailable.</td></tr>"
        "<tr><td><b>CovEdge</b></td><td>Coverage-density edge 0–100</td><td>"
        "50 = neutral (no effect). &gt;50 = under-covered for size → earnings score amplified (×1.00 to ×1.30). "
        "&lt;50 = saturated → earnings score dampened (×1.00 to ×0.70). "
        "Formula: 50 − 2.5 × (actual − expected_analysts). "
        "Blended with institutional % when CIO_FINNHUB_INSTITUTIONAL=1. "
        "Hover each cell for the exact multiplier.</td></tr>"
        "<tr><td><b>CovFlag</b></td><td>Coverage situation label</td><td>"
        "<span class='badge up'>under-covered</span> Fewer analysts than size expects → edge. "
        "<span class='badge warn'>saturated</span> More analysts than expected → no edge. "
        "<span class='badge down'>value-trap</span> 0 analysts + micro-cap → un-investable; amplification zeroed. "
        "<span class='badge up'>inst-neglected</span> Institutional ownership &lt;30 % → reinforces neglect. "
        "<span class='badge warn'>inst-crowded</span> Institutional ownership &gt;70 % → crowded. "
        "<span class='badge'>count-only</span> No market-cap data; conservative estimate. "
        "— = no coverage data or legacy row.</td></tr>"
        "</table></details>"
        "<h3>Recent runs</h3>"
        "<table><tr><th>Date</th><th>Regime</th><th>Candidates</th><th>Universe</th>"
        f"<th>Watchlist</th><th>Ran at</th></tr>{hist_rows}</table>"
    )
    return _page("Alpha Hunter", body, level)


def _exp_num(x, suffix: str = "") -> str:
    """Format an expectancy stat value; None/missing → '—'."""
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:g}{suffix}"
    return f"{esc(x)}{suffix}"


def render_expectancy(closed_trades: list, summary: dict, level: int) -> str:
    """Expectancy KPI panel (swing upgrade #3b).

    Headline = expectancy (pct and R); profit_factor / SQN / payoff_ratio / n
    are the primary stats. Win-rate is shown small/demoted — it is a sub-stat,
    not the headline metric (the whole point of the upgrade). Empty-ledger case
    renders gracefully.
    """
    if not closed_trades or not summary:
        body = (
            "<h1>Expectancy</h1>"
            "<p class='empty'>No closed trades yet. Open and close a position via the "
            "trade ledger to compute expectancy. Win-rate is demoted here by design — "
            "it cannot distinguish a profitable book from a loss-making one.</p>"
        )
        return _page("Expectancy", body, level)

    exp_pct = summary.get("expectancy_pct")
    exp_r = summary.get("expectancy_R")
    n = summary.get("n", 0)
    low_sample = summary.get("low_sample", True)
    pf = summary.get("profit_factor")
    sqn_val = summary.get("sqn")
    payoff = summary.get("payoff_ratio")
    win_rate = summary.get("win_rate")
    avg_win = summary.get("avg_win")
    avg_loss = summary.get("avg_loss")
    ann = summary.get("annualized_pct")
    tpy = summary.get("turns_per_year")

    exp_pct_str = f"{exp_pct:+.2f}%" if isinstance(exp_pct, float) else "—"
    exp_r_str = f"{exp_r:+.3f}R" if isinstance(exp_r, float) else "—"
    exp_class = "up" if isinstance(exp_pct, float) and exp_pct > 0 else (
        "down" if isinstance(exp_pct, float) and exp_pct < 0 else "")

    low_sample_note = (
        f"<p class='empty'>⚠ Low sample ({n} trades, need 20+ for confidence).</p>"
        if low_sample else ""
    )

    ann_card = ""
    if ann is not None:
        ann_class = "up" if ann > 0 else "down"
        ann_card = (
            f"<div class='stat'><div class='k'>Annualized (est.)</div>"
            f"<div class='v {ann_class}'>{_exp_num(ann, '%')}"
            f"<span class='sub'>{_exp_num(tpy)} turns/yr</span></div></div>"
        )

    cards = (
        f"<div class='cards'>"
        f"<div class='stat'><div class='k'>Expectancy / trade</div>"
        f"<div class='v {exp_class}'>{exp_pct_str}"
        f"<span class='sub'>{exp_r_str}</span></div></div>"
        f"<div class='stat'><div class='k'>Profit factor</div>"
        f"<div class='v'>{_exp_num(pf)}</div></div>"
        f"<div class='stat'><div class='k'>SQN</div>"
        f"<div class='v'>{_exp_num(sqn_val)}</div></div>"
        f"<div class='stat'><div class='k'>Payoff ratio</div>"
        f"<div class='v'>{_exp_num(payoff)}</div></div>"
        f"<div class='stat'><div class='k'>Trades (n)</div>"
        f"<div class='v'>{esc(n)}</div></div>"
        f"{ann_card}"
        f"</div>"
    )

    # Win-rate demoted to a sub-stat table below the headline cards.
    sub_table = (
        "<h2>Sub-stats (demoted — use expectancy, not win-rate)</h2>"
        "<table><tr><th>Win rate</th><th>Avg win</th><th>Avg loss</th></tr>"
        f"<tr><td class='num'>{_exp_num(win_rate, '%') if win_rate is not None else '—'}</td>"
        f"<td class='num up'>{_exp_num(avg_win, '%')}</td>"
        f"<td class='num down'>{_exp_num(avg_loss, '%')}</td></tr></table>"
    )

    # Compact closed-trades ledger.
    trade_rows = "".join(
        f"<tr><td>{esc(t.get('ticker'))}</td>"
        f"<td>{esc(t.get('entry_date'))}</td>"
        f"<td>{esc(t.get('exit_date'))}</td>"
        f"<td class='num {'up' if (t.get('pct') or 0) > 0 else 'down'}'>"
        f"{_exp_num(t.get('pct'), '%')}</td>"
        f"<td class='num'>{_exp_num(t.get('r_multiple'), 'R')}</td>"
        f"<td>{esc(t.get('style') or '—')}</td></tr>"
        for t in closed_trades[:50]  # cap display at 50
    ) or "<tr><td class='empty' colspan='6'>no trades</td></tr>"

    body = (
        "<h1>Expectancy</h1>"
        "<p>Expectancy = win% × avg_win − loss% × avg_loss. "
        "A 65%-win book can lose money; a 45%-win book can compound hard. "
        "Expectancy captures both magnitude and frequency. Win-rate is demoted.</p>"
        + low_sample_note + cards + sub_table +
        "<h2>Closed trades ledger</h2>"
        "<table><tr><th>Ticker</th><th>Entry</th><th>Exit</th>"
        f"<th>Pct</th><th>R</th><th>Style</th></tr>{trade_rows}</table>"
    )
    return _page("Expectancy", body, level)


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


def _action_form(action: str, label: str, confirm: str, **hidden) -> str:
    """Like _wipe_form but a normal (non-danger) button — for non-destructive
    mutations such as promoting a playbook to global. *path* defaults to /."""
    path = hidden.pop("path", "/")
    fields = f"<input type='hidden' name='action' value='{esc(action)}'>"
    for name, val in hidden.items():
        fields += f"<input type='hidden' name='{esc(name)}' value='{esc(val)}'>"
    return (
        f"<form class='inline' method='post' action='{esc(path)}' "
        f"onsubmit=\"return confirm('{esc(confirm)}');\">"
        f"{fields}<button type='submit'>{esc(label)}</button></form>"
    )


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
        "<button type='submit'>Refresh live prices</button></form>"
        # Swing trade-ledger sync (cio.alpha.trades) — seeds open positions then
        # logs fills, so /expectancy has data. Distinct from the portfolio drift
        # sync below. No-op with a flash when CIO_IBKR_TWS is unset.
        "<form method='post' action='/portfolio' class='inline'>"
        "<input type='hidden' name='action' value='sync_trades'>"
        "<button type='submit' title='populate the swing trade ledger from IBKR fills "
        "for the Expectancy tab'>Sync trade ledger</button></form>"
        # IBKR sync: broker marks + qty-drift report. Server-side no-op with a
        # flash error when CIO_IBKR_TWS is unset, so always shown.
        "<form method='post' action='/portfolio' class='inline'>"
        "<input type='hidden' name='action' value='sync_ibkr'>"
        "<button type='submit'>Sync from IBKR</button></form>"
        # Non-destructive reconcile: books the minimum closing SELL / opening
        # BUY trades so local quantities match IBKR, KEEPING realized-P&L and
        # dividend history. Closes of positions IBKR no longer holds book zero
        # realized P&L (true exit price unknown). Light confirm — it mutates.
        "<form method='post' action='/portfolio' class='inline' "
        "onsubmit=\"return confirm('Reconcile with IBKR? Books closing / "
        "opening trades so quantities match the broker. History is kept; "
        "closed-out positions realize zero P&amp;L (exit price unknown).');\">"
        "<input type='hidden' name='action' value='reconcile_ibkr'>"
        "<button type='submit'>Reconcile with IBKR</button></form>"
        # Destructive: replaces the whole transactions ledger with the live
        # IBKR positions (at broker average cost). DB is backed up first
        # server-side; still confirmed client-side like other wipes.
        "<form method='post' action='/portfolio' class='inline' "
        "onsubmit=\"return confirm('Align book with IBKR? This DELETES all "
        "local transactions (incl. realized P&amp;L / dividend history) and "
        "rebuilds them from current IBKR positions at broker average cost. "
        "A DB backup is taken first.');\">"
        "<input type='hidden' name='action' value='align_ibkr'>"
        "<button type='submit' class='danger'>Align book with IBKR</button></form>")
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
def _chain_select(name: str, current, chain_names) -> str:
    """A chain-setting combo box. *current* may be None/'' for an agent still on
    a legacy inline config — that renders a '(legacy inline)' placeholder which,
    if left selected, keeps the agent's config untouched on save."""
    cells = ""
    if not current:
        cells += "<option value='' selected>(legacy inline — pick a chain to convert)</option>"
    elif current not in chain_names:
        cells += f"<option value='{esc(current)}' selected>{esc(current)} (missing!)</option>"
    cells += "".join(
        f"<option value='{esc(n)}'{' selected' if n == current else ''}>{esc(n)}</option>"
        for n in chain_names
    )
    return f"<select name='{esc(name)}'>{cells}</select>"


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
                     log_dir: str = "", log_locked_by_env: bool = False,
                     detailed_log: bool = False,
                     detailed_locked_by_env: bool = False,
                     candle_style: str = "standard") -> str:
    """Edit committee model routing. One big POST form → /configure.

    Fallback-chain settings render first: each named setting is a table of
    fallback links (service + model + daily_limit) and can be deleted; new
    settings are added by name. Every agent (and defaults) then picks one
    setting by name from a dropdown. A collapsible section exposes provider
    connection knobs (base_url / api_key_env / token caps).
    """
    flash_html = (
        f"<p class='flash {'err' if flash_err else 'ok'}'>{esc(flash)}</p>"
        if flash else ""
    )
    agents = cfg.get("agents") or {}
    defaults = cfg.get("defaults") or {}
    chains_cfg = cfg.get("chains") or {}
    chain_names = [str(n) for n in chains_cfg.keys()]

    # --- named fallback-chain settings (editable) ---
    chain_blocks = []
    for cname, links in chains_cfg.items():
        link_rows = "".join(
            "<tr>"
            f"<td class='num'>{i + 1}</td>"
            f"<td>{_svc_select(f'chainlink:{esc(cname)}:{i}:service', (link or {}).get('service'), services)}</td>"
            f"<td>{_model_select(f'chainlink:{esc(cname)}:{i}:model', (link or {}).get('model'), (link or {}).get('service'), model_suggestions)}</td>"
            f"<td><input name='chainlink:{esc(cname)}:{i}:daily_limit' class='num' "
            f"value='{esc((link or {}).get('daily_limit') or '')}' "
            "placeholder='none' inputmode='numeric'></td>"
            "</tr>"
            for i, link in enumerate(links if isinstance(links, list) else [])
        )
        chain_blocks.append(
            f"<h3><code>{esc(cname)}</code> "
            f"<label style='font-weight:normal;font-size:.85em;margin-left:10px'>"
            f"<input type='checkbox' name='chain_del:{esc(cname)}' value='1'> "
            "delete this setting</label></h3>"
            "<table><tr><th>#</th><th>Service</th><th>Model</th>"
            "<th>Daily token limit</th></tr>" + link_rows + "</table>"
        )
    chains_section = (
        "<h2>Fallback chain settings</h2>"
        "<p class='hint'>Each setting is an ordered list of fallback links: a call "
        "tries link 1 first, falling through when a service is over its daily token "
        "limit or returns empty (missing key / API error / limit notice). Assign a "
        "setting to each agent below. A setting in use by an agent cannot be deleted.</p>"
        + "".join(chain_blocks)
        + "<p><input name='chain_add' placeholder='new setting name (e.g. cheap)' "
        "pattern='[A-Za-z0-9_-]+'> <span class='hint'>added on save with a 3-link "
        "template (claude → openai → nim); edit it after.</span></p>"
    )

    # --- agents: each picks a chain setting by name ---
    agent_rows = [
        f"<tr><td>defaults</td>"
        f"<td>{_chain_select('defaults:chain', defaults.get('chain') if isinstance(defaults.get('chain'), str) else '', chain_names)}</td>"
        "<td class='hint'>any agent not listed below</td></tr>"
    ]
    for key, acfg in agents.items():
        if not isinstance(acfg, dict):
            continue
        cur = acfg.get("chain")
        cur_name = cur if isinstance(cur, str) else ""
        agent_rows.append(
            f"<tr><td>{esc(key)}</td>"
            f"<td>{_chain_select(f'agent:{esc(key)}:chain', cur_name, chain_names)}</td>"
            "<td></td></tr>"
        )
    agents_tbl = (
        "<table><tr><th>Agent</th><th>Fallback chain</th><th></th></tr>"
        + "".join(agent_rows) + "</table>"
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

    # --- detailed conversation history: indicator + toggle ---
    if detailed_log:
        d_status = "<strong style='color:#1a7f37'>ON</strong> — capturing every LLM call"
    else:
        d_status = "<strong>OFF</strong> — no detailed history captured"
    if detailed_locked_by_env:
        d_toggle = ("<p class='hint'>Locked by the <code>CIO_DETAILED_LOG</code> "
                    "environment variable; unset it to control this from here.</p>")
    else:
        d_want = "0" if detailed_log else "1"
        d_btn = "Disable detailed history" if detailed_log else "Enable detailed history"
        d_toggle = (
            "<form method='post' action='/configure' style='margin-top:8px'>"
            "<input type='hidden' name='form_kind' value='detailed_log'>"
            f"<input type='hidden' name='detailed_log' value='{d_want}'>"
            f"<button type='submit' class='primary'>{d_btn}</button>"
            "</form>"
        )
    detailed_section = (
        "<h2>Detailed conversation history</h2>"
        f"<p>Full prompt/response capture: {d_status}.</p>"
        "<p class='hint'>When ON, every LLM call (main agent + each committee agent) is "
        "appended verbatim — system prompt, user prompt, response, provider, model, "
        "tokens — to <code>logs/YYYY/MM/YYYY-MM-DD.txt</code>. View it on the "
        "<a href='/detailed'>Detailed history</a> tab. Off by default.</p>"
        + d_toggle
    )

    # --- candle style section ---
    cs_hollow = candle_style == "hollow"
    cs_other = "standard" if cs_hollow else "hollow"
    cs_other_label = "Standard (close vs open)" if cs_hollow else "Hollow (close vs prev close)"
    cs_cur_label = "Hollow (close vs prev close)" if cs_hollow else "Standard (close vs open)"
    candle_section = (
        "<h2>Candle style</h2>"
        f"<p>Current: <strong>{esc(cs_cur_label)}</strong>. "
        "Applies globally — dashboard chart, bot messages, committee PDFs.</p>"
        "<p class='hint'>"
        "<b>Standard:</b> green = close &ge; open (intraday up); "
        "red = close &lt; open (intraday down). "
        "A +1% day can show red if the stock opened higher and sold off.<br>"
        "<b>Hollow:</b> color = close vs prev close (day-over-day direction); "
        "hollow outline = close &ge; open; solid fill = close &lt; open. "
        "+1% day always shows green."
        "</p>"
        "<form method='post' action='/configure' style='margin-top:8px'>"
        "<input type='hidden' name='form_kind' value='candle_style'>"
        f"<input type='hidden' name='candle_style' value='{esc(cs_other)}'>"
        f"<button type='submit' class='primary'>Switch to {esc(cs_other_label)}</button>"
        "</form>"
    )

    body = (
        "<h1>Configure committee models</h1>"
        + flash_html
        + (f"<p class='hint'>Editing <code>{esc(path)}</code>. "
           "Pick service and model from the dropdowns; add model names under "
           "“Manage model catalog” below. Saving takes effect immediately — no bot restart needed.</p>")
        + "<form method='post' action='/configure'>"
        + "<input type='hidden' name='form_kind' value='models'>"
        + chains_section
        + "<h2>Agents</h2>" + agents_tbl
        + providers
        + catalog_ui
        + "<p style='margin-top:16px'><button type='submit' class='primary'>Save changes</button></p>"
        + "</form>"
        + logging_section
        + detailed_section
        + candle_section
        + _configure_js({s: list(model_suggestions.get(s, []) or []) for s in services})
    )
    return _page("Configure", body, level)
