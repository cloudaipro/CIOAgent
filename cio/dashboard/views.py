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
details { border: 1px solid #30363d; border-radius: 6px; margin: 8px 0;
          background: #161b22; }
summary { cursor: pointer; padding: 8px 12px; font-weight: 600; }
pre { white-space: pre-wrap; word-break: break-word; background: #0d1117;
      border: 1px solid #21262d; border-radius: 6px; padding: 10px; margin: 6px 0;
      max-height: 420px; overflow: auto; }
.sent { border-left: 3px solid #1f6feb; } .ret { border-left: 3px solid #7ee787; }
.empty { color: #8b949e; font-style: italic; }
"""


def _page(title: str, body: str, level: int) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{esc(title)} · CIO dev dashboard</title>"
        f"<style>{_CSS}</style></head><body>"
        "<header>"
        "<a href='/'>Overview</a><a href='/usage'>Token usage</a>"
        "<a href='/telegram'>Telegram</a><a href='/committee'>Committee</a>"
        "<a href='/memory'>Memory</a>"
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


def render_telegram(turns, level: int) -> str:
    rows = "".join(
        f"<tr><td class='num'>{esc(t['chat_id'])}</td>"
        f"<td class='{esc(t['role'])}'>{esc(t['role'])}</td>"
        f"<td class='msg'>{esc(t['content'])}</td>"
        f"<td>{esc_ts(t['ts'])}</td></tr>"
        for t in turns
    ) or "<tr><td class='empty' colspan='4'>no Telegram turns captured (level 3 disables this)</td></tr>"
    body = (
        "<h1>Telegram conversation history</h1>"
        f"<table><tr><th>Chat</th><th>Role</th><th>Message</th><th>When</th></tr>{rows}</table>"
    )
    return _page("Telegram", body, level)


def render_committee_list(runs, level: int, token_q: str = "") -> str:
    rows = "".join(
        f"<tr><td><a href='/committee/{esc(r['run_id'])}{token_q}'>{esc(r['run_id'])}</a></td>"
        f"<td>{esc(r['symbol'])}</td><td>{esc_ts(r['started'])}</td>"
        f"<td class='num'>{esc(r['calls'])}</td><td class='num'>{esc(r['tokens'])}</td></tr>"
        for r in runs
    ) or "<tr><td class='empty' colspan='5'>no committee runs captured</td></tr>"
    body = (
        "<h1>Committee runs</h1>"
        f"<table><tr><th>Run</th><th>Symbol</th><th>Started</th><th>Calls</th><th>Tokens</th></tr>{rows}</table>"
    )
    return _page("Committee", body, level)


def render_memory(sections, level: int) -> str:
    """Per-agent / per-chat memory contents, for debugging.

    *sections* is a list of ``{"label": str, "scopes": [{"scope", "count", "notes"}]}``
    where each note is a mem_notes row dict. One <details> per scope; HOT notes
    (injected into prompts) flagged so you can see what each agent 'knows'.
    """
    blocks: list[str] = []
    for sec in sections:
        scopes = sec.get("scopes") or []
        blocks.append(f"<h2>{esc(sec.get('label'))}</h2>")
        if not scopes:
            blocks.append("<p class='empty'>no memory in this store.</p>")
            continue
        for sc in scopes:
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
            blocks.append(
                f"<details><summary>{esc(sc.get('scope'))} "
                f"· {esc(sc.get('count'))} note(s)</summary>"
                "<table><tr><th>Tier</th><th>Key</th><th>Value</th><th>Hits</th>"
                "<th>Imp</th><th>Source</th><th>Updated</th></tr>"
                f"{note_rows}</table></details>"
            )
    body = "<h1>Agent memory contents</h1>" + "".join(blocks)
    return _page("Memory", body, level)


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
