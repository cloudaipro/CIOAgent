"""Claude agent layer.

Runs on your Claude Code / Pro subscription auth via `claude-agent-sdk` — no
ANTHROPIC_API_KEY required. Portfolio functions are exposed to the model as
in-process MCP tools. Chart tools drop PNG paths into a per-request "outbox"
that the bot reads and sends as photos after the turn.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)

import urllib.parse

from . import alpha, charts, context, convlog, memory, portfolio, recall, timeutil, watchlist, web
from .data import source_policy as _sp

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = PROJECT_ROOT / "data" / "reports"

# Dedicated logger for the evidence/primary-source tools. Every call emits one
# INFO line — tool, symbol, whether the source was configured, and the result
# count — so an operator can confirm from the logs that EDGAR/Finnhub/CT are
# actually wired and firing (and whether they hit the real source). Mirrored to
# a date-based file when file logging is on (see cio.logsetup).
_evlog = logging.getLogger("cio.evidence")


def _ev(tool: str, symbol: str, configured: bool, **extra) -> None:
    base = f"tool={tool} symbol={symbol} configured={configured}"
    kv = " ".join(f"{k}={v}" for k, v in extra.items())
    _evlog.info("%s %s" % (base, kv) if kv else base)

# Collector for image paths produced by chart tools during a turn. A plain
# module global (not a contextvar) because the SDK runs MCP tool callbacks in a
# separate task context. `_LOCK` serializes turns so this stays correct even if
# two chats arrive at once. `_PENDING_DOCS` is the same idea for file documents
# (e.g. the committee PDF) the bot sends via reply_document.
_PENDING: list[str] = []
_PENDING_DOCS: list[str] = []
# Per-scope web-source registry, keyed by chat scope. web_search appends {url,title}
# in order; the model cites results by their 1-based number ([1],[2]…) and ask()
# appends a verified Sources: footer from this list. URLs never pass through model
# output, so they can't be truncated/mistyped into a 404. The list PERSISTS across
# turns within a session (the model cites a number from an earlier turn's search) and
# is reset only on session roll / close — kept in sync with the SDK thread it indexes.
# Keyed dict (not a flat list) so concurrent chats can't cross-reference each other.
_SOURCES: dict[str, list[dict]] = {}
# Per-scope issuer domains resolved from company_profile weburl. Passed to
# source_policy.classify() so the company's own IR pages rank as Tier 1.
# (v1: set is empty until company_profile runs in this scope — documented.)
_ISSUER_DOMAINS: dict[str, set[str]] = {}
# Set by web_search, reset each turn in _run_query; gates the footer's list-all
# fallback so only a turn that actually searched can dump uncited sources.
_SEARCHED_THIS_TURN = False
_LOCK = asyncio.Lock()

# Rolling session: after this many turns or approx tokens, checkpoint a digest
# and reseed a fresh session so a single chat's transcript can't grow forever.
def _env(name, default=None):          # name without prefix, e.g. "MODEL"
    return os.getenv("CIO_" + name, os.getenv("CFO_" + name, default))


ROLL_TURNS = int(_env("ROLL_TURNS", "40"))
ROLL_TOKENS = int(_env("ROLL_TOKENS", "16000"))

# Every N turns, remind the agent to persist anything notable (Hermes-style
# nudge) — cheap prompt augmentation, no extra LLM call.
NUDGE_TURNS = int(_env("NUDGE_TURNS", "8"))
_NUDGE_SUFFIX = (
    "\n\n(System reminder: if anything durable about my preferences, plans, or "
    "watchlist came up, save it with the remember tool. Never save figures.)"
)

_DIGEST_PROMPT = (
    "Summarize our conversation so far in 4-6 sentences for your own future "
    "reference: decisions made, my stated preferences, and open threads. Then, if "
    "any mistakes surfaced this session — a wrong fact, a stale/cached figure, a "
    "bad source, a correction I gave you — add a final 'Lessons:' line naming them "
    "briefly so you do NOT repeat them next time. Do NOT include specific dollar "
    "amounts, prices, or P&L numbers (those are recomputed from data). Do not call "
    "any tools — just write the summary."
)

# Monthly rollup (digest-of-digests): consolidate a month of daily digests into one
# durable long-term memo, stored as a HOT note so it is injected every session.
_ROLLUP_PROMPT = (
    "Below are this chat's daily session digests for one calendar month. Consolidate "
    "them into a SHORT durable memo (5-8 bullet points) capturing what carries forward: "
    "standing decisions, the operator's preferences/strategy, recurring themes, and any "
    "lessons to avoid repeating. Drop one-off chatter. Do NOT include specific dollar "
    "amounts, prices, or P&L numbers (those are recomputed from data). Do not call any "
    "tools — just write the memo.\n\nDaily digests:\n"
)

# Self-improving reflection (W10): distill a reusable procedure if one occurred.
_PLAYBOOK_PROMPT = (
    "Reflect on this session: did we complete a repeatable, multi-step procedure "
    "worth reusing next time (e.g. a monthly review, a rebalancing check)? If yes, "
    "reply EXACTLY in this form:\nNAME: <short_snake_case_name>\nSTEPS: <numbered "
    "steps that reference your tools; NO dollar amounts or prices>\nIf nothing is "
    "reusable, reply with just: NONE. Do not call any tools."
)


def _parse_playbook(text: str):
    """Parse the distillation reply into (name, steps) or None."""
    if not text or text.strip().upper().startswith("NONE"):
        return None
    m = re.search(r"NAME:\s*(.+)", text)
    s = re.search(r"STEPS:\s*(.+)", text, re.S)
    if not m or not s:
        return None
    name = m.group(1).strip().splitlines()[0][:60]
    steps = s.group(1).strip()
    return (name, steps) if name and steps else None


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


_CITE_RE = re.compile(r"\[(\d+)\]")
# A trailing Sources/References block the model wrote itself (any heading style),
# stripped so we don't emit it twice alongside our authoritative footer.
_MODEL_SOURCES_RE = re.compile(
    r"\n+[#>*\s]*(?:sources|references|來源|資料來源|參考(?:資料|來源)?)\s*[:：]?\s*\n.*\Z",
    re.S | re.I,
)


def _append_sources(text: str, sources: list[dict], searched: bool = False,
                    scope: str | None = None) -> str:
    """Append a verified `Sources:` footer built from the session's web_search registry.

    URLs come straight from the tool (never the model), so they can't be truncated
    into a 404. The model cites results by their raw registry number [n]; we strip
    any Sources block the model wrote itself, then renumber the refs it actually used
    to a clean 1..k sequence in BOTH the prose and the footer. If it cited none but a
    search ran THIS turn, we list them all so the real links are never lost.

    The registry persists across turns, so *searched* gates the list-all fallback:
    an unrelated later turn that cites nothing leaves the registry alone (no footer
    spam of stale sources).

    Each source line shows its Tier. A corroboration verdict line is appended when
    ≥1 source is cited (✅ or ⚠️ per source_policy.is_verified)."""
    if not sources:
        return text
    stripped = _MODEL_SOURCES_RE.sub("", text).rstrip()    # drop model's own block
    order: list[int] = []                                  # cited registry idxs, first-seen
    for m in _CITE_RE.findall(stripped):
        i = int(m)
        if 1 <= i <= len(sources) and i not in order:
            order.append(i)
    if not order and not searched:
        return text          # unrelated turn — leave the message untouched
    text = stripped
    if order:
        remap = {old: new for new, old in enumerate(order, 1)}   # 1->1, 8->2, 4->3 …
        text = _CITE_RE.sub(
            lambda m: f"[{remap[int(m.group(1))]}]" if int(m.group(1)) in remap
            else m.group(0),
            text,
        )
        idxs = order
    else:
        idxs = list(range(1, len(sources) + 1))
        remap = {i: i for i in idxs}

    # Build footer lines with tier label
    lines = []
    cited_tiers: list[_sp.Tier] = []
    for i in idxs:
        src = sources[i - 1]
        tier = src.get("tier")
        if tier is None:
            # Compute tier if not already stored (legacy entries)
            tier = _classify_url(src["url"], scope or "global").value
        cited_tiers.append(_sp.Tier(tier))
        tier_label = {1: "PRIMARY", 2: "REPUTABLE", 3: "LOW-TRUST"}.get(int(tier), "UNKNOWN")
        lines.append(f"[{remap[i]}] {src['url']}  (Tier {tier} {tier_label})")

    footer = f"{text}\n\nSources:\n" + "\n".join(lines)

    # Corroboration verdict — only when ≥1 source was cited
    if order:
        if _sp.is_verified(cited_tiers):
            verdict = "✅ Material facts corroborated (≥1 primary or ≥2 reputable)."
        else:
            verdict = "⚠️ Single-source / unverified — treat flagged claims as provisional."
        footer = footer + "\n" + verdict

    return footer


def _emit_image(path: str | None, ok_msg: str, empty_msg: str) -> dict:
    if not path:
        return _text(empty_msg)
    _PENDING.append(path)
    return _text(ok_msg)


def _emit_doc(path: str | None, ok_msg: str, empty_msg: str) -> dict:
    """Queue a file (e.g. PDF) for the bot to send as a document this turn."""
    if not path:
        return _text(empty_msg)
    _PENDING_DOCS.append(path)
    return _text(ok_msg)


def _clock_context() -> str:
    """Authoritative 'now': local + US/Eastern wall clock, NASDAQ status and the
    latest settled trading session. The agent has no other reliable source for
    today's date — without this it guesses and mislabels stale closes as 'today'."""
    from datetime import datetime
    from .stock.data import _EASTERN, nasdaq_trading_status, closest_trading_day

    tz = timeutil.local_tz()
    now_local = datetime.now(tz)
    now_et = datetime.now(_EASTERN)
    status = {0: "closed", 1: "premarket", 2: "open", 3: "afterhours"}[
        nasdaq_trading_status(now_et)]
    session = closest_trading_day(now_et.replace(tzinfo=None), method="prev").date()
    return (
        f"now: {now_local:%Y-%m-%d %H:%M} {now_local.tzname()} | "
        f"ET: {now_et:%Y-%m-%d %H:%M} | "
        f"NASDAQ: {status} | latest settled session: {session:%Y-%m-%d}"
    )


def _quote_freshness_note(quote: dict | None) -> str:
    """Advisory appended to a quote tool result when the bar is NOT a live intraday
    quote, so the agent labels it by date instead of calling it 'today's move'."""
    if not quote or quote.get("quote_kind") == "live_intraday":
        return ""
    market = quote.get("market_status", "closed")
    bar_date = quote.get("date")          # actual bar date in cache
    session_date = quote.get("session_date") or bar_date  # expected current session
    kind = quote.get("quote_kind", "settled_close")
    if kind == "stale_close":
        # Cache has an older bar; session_date data is not yet available.
        return (
            f"\n\nWARNING: STALE DATA — cache has {bar_date} close; "
            f"{session_date} data is NOT yet in cache. "
            f"Report this price as the {bar_date} close — do NOT present it as "
            f"today's or {session_date}'s price. Call market_clock to confirm current date."
        )
    # settled_close: bar_date == session_date, market closed/pre-market — final bar.
    return (
        f"\n\nNOTE: NASDAQ {market} — this is the SETTLED {session_date} close "
        f"(final, latest real price), NOT a live \"now\" quote. "
        f"Report it as 'as of {session_date} close'; do NOT present it as today's move. "
        f"For *why* it moved, use web_search (narrative only, not the figure)."
    )


# ----- tools ----------------------------------------------------------------

@tool("portfolio_summary",
      "Portfolio totals: market value, cost basis, unrealized & realized P&L, dividends.",
      {})
async def t_summary(args):
    return _text(json.dumps(portfolio.summary(), indent=2))


@tool("list_positions",
      "Open positions with quantity, average cost, last price, market value and unrealized P&L.",
      {})
async def t_positions(args):
    df = portfolio.positions()
    return _text(df.to_string(index=False) if len(df) else "No open positions.")


@tool("realized_pl", "Realized P&L and dividends per symbol from closed/partial sells.", {})
async def t_realized(args):
    df = portfolio.realized_pl()
    return _text(df.to_string(index=False) if len(df) else "No realized P&L yet.")


@tool("set_price", "Set the latest market price for one symbol (manual, no live feed).",
      {"symbol": str, "price": float})
async def t_set_price(args):
    portfolio.set_price(args["symbol"], float(args["price"]))
    sym = args["symbol"].upper()
    _auto_note(f"manually updated the price for {sym} (current value via portfolio tools)")
    return _text(f"Price set: {sym} = {args['price']}")


@tool("ingest_transactions_csv",
      "Import a transactions CSV (columns: txn_date,symbol,action,quantity,price[,fees,currency,notes]).",
      {"path": str})
async def t_ingest(args):
    try:
        n = portfolio.ingest_transactions_csv(args["path"])
        _auto_note("imported a new transactions CSV; holdings updated (figures via portfolio tools)")
        return _text(f"Imported {n} transactions.")
    except portfolio.DuplicateImport as e:
        # Same file already imported (e.g. a redelivered upload) — not an error.
        return _text(f"Already imported this exact file ({e.rows} rows); skipped to "
                     f"avoid duplicates.")
    except Exception as e:  # surface ingest errors to the model
        return _text(f"Import failed: {e}")


@tool("allocation_chart", "Generate a portfolio allocation pie chart and send it to the user.", {})
async def t_alloc_chart(args):
    return _emit_image(charts.allocation_pie(),
                       "Allocation chart generated; it will be sent to the user.",
                       "No priced positions to chart yet.")


@tool("pl_chart", "Generate an unrealized-P&L bar chart and send it to the user.", {})
async def t_pl_chart(args):
    return _emit_image(charts.pl_bar(),
                       "P&L chart generated; it will be sent to the user.",
                       "No priced positions to chart yet.")


# Scope of the chat whose turn is currently running. Turns are serialized by
# _LOCK, so a module global is safe; CIOAgent sets it before each query so the
# memory tools read/write the right per-chat namespace.
_ACTIVE_SCOPE = "global"


def _scope() -> str:
    return _ACTIVE_SCOPE


def _sources_for(scope: str) -> list[dict]:
    """The persistent web-source registry for one chat scope (created on first use)."""
    return _SOURCES.setdefault(scope, [])


def _issuer_domains_for(scope: str) -> set[str]:
    """The per-scope issuer domain set (populated by company_profile calls)."""
    return _ISSUER_DOMAINS.setdefault(scope, set())


def _register_issuer_domain(weburl: str, scope: str) -> None:
    """Extract hostname from weburl and add to this scope's issuer-domain set."""
    if not weburl:
        return
    try:
        parsed = urllib.parse.urlparse(weburl if "://" in weburl else "https://" + weburl)
        host = (parsed.hostname or "").strip().lower().lstrip(".")
        if host.startswith("www."):
            host = host[4:]
        if host:
            _issuer_domains_for(scope).add(host)
    except Exception:
        pass


def _classify_url(url: str, scope: str) -> _sp.Tier:
    """Classify a URL's trust tier using the current scope's issuer domains."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").strip().lower()
    except Exception:
        host = ""
    return _sp.classify(host, _issuer_domains_for(scope))


def _auto_note(text: str) -> None:
    """Deterministic event capture (source=auto). Never raises into a tool, and
    the figures firewall still applies so no numbers slip into memory."""
    try:
        memory.remember(text, scope=_scope(), source="auto", tier="warm")
    except Exception:
        pass


@tool("remember",
      "Persist a QUALITATIVE fact across sessions (preferences, watchlist, plans, context). "
      "NEVER store financial figures/prices — those are recomputed from portfolio data. "
      "Optional `key` makes it an upsert; `important`=true pins it into startup context. "
      "For time-bound notes (an upcoming event/plan that stops mattering), set `ttl_days` "
      "so the note expires instead of lingering forever.",
      {"value": str, "key": str, "important": bool, "ttl_days": int})
async def t_remember(args):
    try:
        # important=true is an operator pin: source='user' exempts it from both
        # eviction and hot-cap demotion (it stays injected until forgotten).
        memory.remember(args["value"], key=args.get("key") or None, scope=_scope(),
                        tier="hot" if args.get("important") else "warm",
                        importance=2.0 if args.get("important") else 1.0,
                        source="user" if args.get("important") else "agent",
                        ttl_days=args.get("ttl_days") or None)
    except memory.FiguresFirewallError as e:
        return _text(str(e))
    return _text(f"Remembered: {args.get('key') or args['value'][:50]}")


@tool("recall",
      "Recall a remembered fact by exact key, or list recent notes if key is omitted. "
      "For fuzzy/semantic lookup over history use memory_search instead.",
      {"key": str})
async def t_recall(args):
    key = args.get("key")
    if key:
        v = memory.recall(key, scope=_scope())
        return _text(v if v else f"No memory for key: {key}")
    notes = memory.list_notes(scope=_scope(), limit=20)
    body = "\n".join(f"- {n['key'] or n['id']}: {n['value']}" for n in notes)
    return _text(body or "Nothing remembered yet.")


@tool("forget", "Delete a remembered note by key.", {"key": str})
async def t_forget(args):
    ok = memory.forget(key=args["key"], scope=_scope())
    return _text(f"Forgot: {args['key']}" if ok else f"No such memory: {args['key']}")


@tool("memory_search",
      "Hybrid (keyword + semantic) search over your saved notes, past conversation "
      "turns, AND prior session digests (the long-term per-day/per-month summaries). "
      "Use when the user refers to something from earlier that isn't in the injected "
      "memory above — e.g. 'what did I say about NVDA a while back?' or 'what did we "
      "conclude last month?'.",
      {"query": str, "k": int})
async def t_search(args):
    hits = recall.search(args["query"], k=int(args.get("k") or 5), scope=_scope(),
                         kinds=("note", "turn", "digest"))
    if not hits:
        return _text("No matches in memory or history.")
    return _text("\n".join(f"[{h['kind']} {h['id']}] {h['text']}" for h in hits))


@tool("memory_get", "Fetch one saved note by its id (from memory_search results).",
      {"id": int})
async def t_get(args):
    n = memory.get_note(int(args["id"]), )
    if not n:
        return _text(f"No note with id {args['id']}.")
    keep = ("id", "scope", "tier", "key", "value", "importance", "hits", "source")
    return _text(json.dumps({k: n[k] for k in keep}, indent=2))


@tool("save_playbook",
      "Save a reusable procedure for a recurring task (e.g. 'monthly_review'): a short "
      "name and step-by-step instructions that reference your tools. Steps must NOT "
      "contain figures/prices — describe the steps, recompute numbers when run.",
      {"name": str, "steps": str})
async def t_save_playbook(args):
    try:
        memory.add_playbook(args["name"], args["steps"], scope=_scope())
    except memory.FiguresFirewallError as e:
        return _text(str(e))
    return _text(f"Saved playbook: {args['name']}")


@tool("list_playbooks", "List your saved playbooks (reusable procedures) and their steps.", {})
async def t_list_playbooks(args):
    pbs = memory.list_playbooks(scope=_scope())
    if not pbs:
        return _text("No playbooks saved yet.")
    return _text("\n\n".join(f"## {p['name']}\n{p['steps']}" for p in pbs))


# ----- economic-event calendar (auto-alert before high-impact releases) ------

@tool("add_econ_event",
      "Record a high-impact economic event so the bot warns the operator ahead of it. "
      "Use real, verified release dates (look them up). NFP is auto-seeded, so add the "
      "others: CPI, Core CPI, PPI, PCE, FOMC decision, GDP, Retail Sales, JOLTS. "
      "event_date is YYYY-MM-DD; impact is high/medium/low; time_et like '08:30 ET'.",
      {"event_date": str, "name": str, "impact": str, "time_et": str, "source": str})
async def t_add_econ_event(args):
    from . import econ_calendar
    try:
        new = econ_calendar.add_event(
            args["event_date"], args["name"],
            impact=args.get("impact") or "high",
            time_et=args.get("time_et") or "",
            source=args.get("source") or "")
    except ValueError as e:
        return _text(f"Could not add event: {e}")
    verb = "Added" if new else "Updated"
    return _text(f"{verb} econ event: {args['event_date']} {args['name']}.")


@tool("list_econ_events",
      "List upcoming high-impact economic events the bot will alert on (next ~45 days).",
      {})
async def t_list_econ_events(args):
    from . import econ_calendar
    econ_calendar.seed_nfp(months_ahead=2)
    evs = econ_calendar.list_upcoming(days=45)
    if not evs:
        return _text("No upcoming econ events recorded. Run the monthly_red_events playbook to populate them.")
    lines = []
    for e in evs:
        t = f" {e['time_et']}" if e.get("time_et") else ""
        flag = " (alerted)" if e.get("alerted") else ""
        lines.append(f"- {e['event_date']}{t} — {e['name']} [{(e.get('impact') or 'high').upper()}]{flag}")
    return _text("Upcoming high-impact events:\n" + "\n".join(lines))


@tool("econ_events_pdf",
      "Render the upcoming high-impact economic events as a PDF and send it to the user. "
      "Call this at the end of the monthly_red_events playbook so the user gets a clean "
      "table (Telegram cannot render Markdown tables in text).",
      {})
async def t_econ_events_pdf(args):
    from . import econ_calendar
    from datetime import date
    econ_calendar.seed_nfp(months_ahead=2)
    evs = econ_calendar.list_upcoming(days=45)
    if not evs:
        return _text("No upcoming econ events to render. Add events first.")
    md = econ_calendar.render_report_md(evs)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = REPORTS_DIR / f"econ_events_{date.today():%Y-%m}.pdf"
    try:
        from .committee.render_pdf import markdown_to_pdf
        markdown_to_pdf(md, pdf_path, title="Economic Red-Events")
    except Exception:
        import logging
        logging.getLogger("cio.agent").exception(
            "econ_events_pdf render failed; sending markdown instead")
        md_path = REPORTS_DIR / f"econ_events_{date.today():%Y-%m}.md"
        md_path.write_text(md, encoding="utf-8")
        return _emit_doc(str(md_path), "Econ-events file generated; it will be sent.",
                         "Could not render the econ-events file.")
    return _emit_doc(str(pdf_path), "Econ-events PDF generated; it will be sent to the user.",
                     "Could not render the econ-events PDF.")


@tool("econ_events_image",
      "Render the upcoming high-impact economic events as a TABLE IMAGE and send it. "
      "Telegram renders images inline, so prefer this over the PDF for the "
      "monthly_red_events playbook — the user sees the table without downloading.",
      {})
async def t_econ_events_image(args):
    from . import econ_calendar
    from datetime import date
    econ_calendar.seed_nfp(months_ahead=2)
    evs = econ_calendar.list_upcoming(days=45)
    if not evs:
        return _text("No upcoming econ events to render. Add events first.")
    title = f"Economic Red-Events — {date.today():%B %Y}"
    path = charts.econ_events_table(evs, title=title)
    return _emit_image(path, "Econ-events table image generated; it will be sent to the user.",
                       "Could not render the econ-events image.")


# ----- stock market tools (live price/volume + cache + TA strategies) --------
# The stock subsystem is imported lazily inside each tool so the agent (and the
# TA engine's heavier deps) only load when a stock tool is actually used.

@tool("stock_quote",
      "Live latest price, volume and OHLC for a stock symbol (Yahoo Finance, cached).",
      {"symbol": str})
async def t_stock_quote(args):
    from . import stock
    q = stock.get_quote(args["symbol"].upper())
    if not q:
        return _text(f"No quote available for {args['symbol'].upper()}.")
    return _text(json.dumps(q, indent=2) + _quote_freshness_note(q))


@tool("stock_history",
      "Historical daily OHLCV for a symbol between two YYYY-MM-DD dates (cached). "
      "Returns the row count and the most recent rows.",
      {"symbol": str, "start": str, "end": str})
async def t_stock_history(args):
    from . import stock
    df = stock.get_history(args["symbol"].upper(), args["start"], args["end"])
    if df is None or len(df) == 0:
        return _text(f"No data for {args['symbol'].upper()} in that range.")
    tail = df[["Open", "High", "Low", "Close", "Volume"]].tail(10)
    return _text(f"{len(df)} rows {df.index[0].date()}..{df.index[-1].date()}\n{tail.to_string()}")


@tool("list_stock_strategies",
      "List the technical-analysis strategies available for stock signal analysis.",
      {})
async def t_list_strategies(args):
    from . import stock
    names = stock.list_strategies()
    return _text(f"{len(names)} strategies:\n" + ", ".join(names))


@tool("run_stock_strategy",
      "Run one technical-analysis strategy on a symbol and report its latest firing signals. "
      "Use list_stock_strategies for valid names (e.g. rsi, macd, stoch).",
      {"symbol": str, "strategy": str})
async def t_run_strategy(args):
    from . import stock
    sym = args["symbol"].upper()
    name = args["strategy"].lower()
    try:
        sig = stock.run_strategy(sym, name)
    except KeyError:
        return _text(f"Unknown strategy '{name}'. See list_stock_strategies.")
    except ValueError as e:
        return _text(str(e))
    if sig is None or len(sig) == 0:
        return _text(f"No signals produced for {sym} / {name}.")
    last = sig.iloc[-1]
    firing = [c for c, v in last.items() if v == 1]
    recent = sig.tail(60)
    active_recent = [c for c in sig.columns if (recent[c] == 1).any()]
    return _text(json.dumps({
        "symbol": sym,
        "strategy": name,
        "as_of": str(sig.index[-1].date()),
        "signals_firing_today": firing,
        "signals_active_last_60d": active_recent,
    }, indent=2))


@tool("run_strategy_profile",
      "Run a situation-specific technical-analysis profile on a symbol and report per-strategy "
      "verdicts plus a composite. Profiles: committee (position decisions), monitor (daily "
      "watchlist change detection), swing/wave (short-term trading). Prefer this over "
      "run_stock_strategy when assessing a symbol for one of those situations.",
      {"symbol": str, "profile": str})
async def t_run_strategy_profile(args):
    from . import stock
    sym = args["symbol"].upper()
    profile = (args.get("profile") or "committee").lower()
    try:
        res = stock.run_strategy_profile(sym, profile)
    except KeyError as e:
        return _text(str(e))
    except ValueError as e:
        return _text(str(e))
    return _text(json.dumps({
        "symbol": sym,
        "profile": res["profile"],
        "composite": res["composite"],
        "signals": res["signals"],
        "recent_events": {k: v["events"] for k, v in res["detail"].items() if v["events"]},
    }, indent=2))


@tool("refresh_prices",
      "Fetch live market prices (latest close) for all open positions and update valuations.",
      {})
async def t_refresh_prices(args):
    r = portfolio.refresh_live_prices()
    return _text(json.dumps(r, indent=2))


@tool("stock_panel",
      "Render a one-stop single-stock panel image (price, fundamentals, revenue, links) and send it. "
      "Set with_indicators=true to ALSO send the technical-indicator chart "
      "(RSI/MACD/KDJ + divergence) alongside the panel.",
      {"symbol": str, "with_indicators": bool})
async def t_stock_panel(args):
    from . import stock
    sym = args["symbol"].upper()
    path = stock.render_panel(sym)
    links = stock.related_links(sym)      # dict name->url
    if args.get("with_indicators") and path:
        try:
            ind = stock.render_indicators(sym, "committee")
            _PENDING.append(ind)
        except Exception:
            logging.getLogger("cio.agent").debug(
                "indicator chart skipped for panel %s", sym, exc_info=True)
    return _emit_image(path,
        "Stock panel generated; it will be sent to the user.\n相關連結: " +
        " · ".join(f"[{k}]({v})" for k, v in links.items()),
        f"No data for {sym}.")


@tool("stock_indicators",
      "Render a technical-indicator chart (指標視覺化) for one stock and send it: "
      "candlesticks + MA20/60/120 with RSI / MACD / KDJ sub-panels and divergence + "
      "swing markers (the same signals the committee profile uses). Use whenever the "
      "user wants to SEE indicators, divergence, or overlay lines on the chart — this "
      "is the in-house replacement for sending them to TradingView. 'profile' selects "
      "the indicator/signal set (committee|swing|monitor, default committee).",
      {"symbol": str, "profile": str})
async def t_stock_indicators(args):
    from . import stock
    sym = args["symbol"].upper()
    profile = (args.get("profile") or "committee").lower()
    try:
        path = stock.render_indicators(sym, profile)
    except Exception as e:  # noqa: BLE001
        return _text(f"Could not render indicators for {sym}: {e}")
    return _emit_image(path,
        f"{sym} 指標視覺化 ({profile}) generated; it will be sent to the user. "
        "紅色 ▼ = bear divergence (價創新高、動能未跟上)。",
        f"No data for {sym}.")


@tool("watchlist_prices",
      "Latest price, volume and OHLC for every symbol in the user's ACTIVE watchlist "
      "(Yahoo Finance, cached). Use when the user asks about 'my watchlist' or 'the "
      "stocks I'm watching'. The active list always includes the NASDAQ index ^IXIC "
      "as a market benchmark. Watchlists are managed in the dashboard, not here.",
      {})
async def t_watchlist_prices(args):
    snap = watchlist.prices()
    if snap["id"] is None:
        return _text("No active watchlist yet. The user can create one in the dashboard.")
    # Pick the most severe freshness case: stale_close > settled_close > live_intraday.
    _kind_rank = {"stale_close": 2, "settled_close": 1, "live_intraday": 0}
    worst = max(
        (q for q in snap.get("quotes", []) if q),
        key=lambda q: _kind_rank.get(q.get("quote_kind", ""), 0),
        default=None,
    )
    note = _quote_freshness_note(worst)
    return _text(json.dumps(snap, indent=2) + note)


# ----- watchlist operations (Telegram can manage the lists, not just read) ---
def _resolve_watchlist(name: str | None) -> dict | None:
    """A watchlist by name (case-insensitive), else the active one."""
    name = (name or "").strip()
    if name:
        wl = watchlist.find_by_name(name)
        if wl:
            return wl
        for w in watchlist.list_watchlists():
            if w["name"].lower() == name.lower():
                return watchlist.get(w["id"])
        return None
    return watchlist.active()


@tool("list_watchlists",
      "List every watchlist the user has (id, name, whether it's the active one, "
      "and symbol count). Use to answer 'what watchlists do I have' or before "
      "adding/removing on a specific list.", {})
async def t_list_watchlists(args):
    return _text(json.dumps(watchlist.list_watchlists(), indent=2))


@tool("watchlist_add",
      "Add a symbol to a watchlist. Targets the named list if `name` is given "
      "(e.g. an Alpha-yyyy-mm-dd list), otherwise the ACTIVE list. Use when the user "
      "says 'add NVDA to my watchlist' or 'put TSLA on the alpha list'.",
      {"symbol": str, "name": str})
async def t_watchlist_add(args):
    wl = _resolve_watchlist(args.get("name"))
    if wl is None:
        return _text("No matching watchlist. Ask the user which list, or create one in the dashboard.")
    try:
        added = watchlist.add_symbol(wl["id"], args["symbol"])
    except watchlist.WatchlistError as e:
        return _text(f"Could not add: {e}")
    sym = args["symbol"].strip().upper()
    return _text(f"{'Added' if added else 'Already present:'} {sym} on {wl['name']!r}.")


@tool("watchlist_remove",
      "Remove a symbol from a watchlist (named list, else the ACTIVE one). The "
      "NASDAQ index ^IXIC is the benchmark floor and cannot be removed.",
      {"symbol": str, "name": str})
async def t_watchlist_remove(args):
    wl = _resolve_watchlist(args.get("name"))
    if wl is None:
        return _text("No matching watchlist to remove from.")
    try:
        watchlist.remove_symbol(wl["id"], args["symbol"])
    except watchlist.WatchlistError as e:
        return _text(f"Could not remove: {e}")
    return _text(f"Removed {args['symbol'].strip().upper()} from {wl['name']!r}.")


@tool("watchlist_activate",
      "Make a watchlist the ACTIVE one (the list /watchlist and watchlist_prices "
      "report). Identify it by name, e.g. 'Alpha-2026-06-12'.", {"name": str})
async def t_watchlist_activate(args):
    wl = _resolve_watchlist(args.get("name"))
    if wl is None:
        return _text(f"No watchlist named {args.get('name')!r}.")
    watchlist.set_active(wl["id"])
    return _text(f"{wl['name']!r} is now the active watchlist.")


@tool("run_alpha_hunter",
      "Run the Alpha Hunter funnel (Market→Sector→Quality→Earnings→Momentum→Ranking) "
      "over the NASDAQ universe and publish a fresh watchlist named "
      "Alpha-yyyy-mm-dd (set active) with every candidate scoring at/above the "
      "configured Final-Score threshold. Deterministic, no model cost; it is "
      "network-bound and can take a minute. Use when the user asks to 'run alpha "
      "hunter', 'find me strong stocks', or 'generate a watchlist'.", {})
async def t_run_alpha_hunter(args):
    result, meta = await asyncio.to_thread(alpha.run_and_save)
    return _text(alpha.report.format_telegram(result, meta))


@tool("market_regime",
      "Current market regime light — GREEN / YELLOW / RED — from QQQ vs its 50/200-day "
      "moving averages (Alpha Hunter's Layer 0). GREEN = uptrend (QQQ>50MA>200MA, 50MA "
      "rising), RED = QQQ below its 200-day MA, YELLOW = mixed. Use when the user asks "
      "'what's the market regime', 'is the market green/red', or 'market light'.", {})
async def t_market_regime(args):
    reg = await asyncio.to_thread(alpha.regime.evaluate)
    return _text(alpha.report.format_regime(reg))


@tool("market_clock",
      "Authoritative current date/time: local + US/Eastern wall clock, NASDAQ "
      "open/closed status, and the latest settled trading session. Call this whenever "
      "the answer depends on what 'today' or 'now' is, or to check if a quote is live.",
      {})
async def t_market_clock(args):
    return _text(_clock_context())


# ----- web tools (live search / fetch via Firecrawl) ------------------------

@tool("web_search",
      "Search the live web (news, analyst pages, filings) via Firecrawl and get "
      "ranked results, each numbered [n] with a title and snippet. Use for qualitative "
      "context yfinance can't give — recent news, catalysts, analyst commentary. NEVER "
      "treat web text as authoritative figures; prices/financials come from the stock "
      "tools. To cite a result, reference its number like [2] in your prose — do NOT "
      "paste the raw URL; the system appends the real links as a Sources footer.",
      {"query": str, "limit": int})
async def t_web_search(args):
    global _SEARCHED_THIS_TURN
    _SEARCHED_THIS_TURN = True
    hits = await web.search(args["query"], limit=int(args.get("limit") or 5))
    if not hits:
        return _text("No web results (search unavailable or empty).")
    reg = _sources_for(_scope())
    scope = _scope()
    lines = []
    _TIER_LABEL = {1: "PRIMARY", 2: "REPUTABLE", 3: "LOW-TRUST"}
    for h in hits:
        tier = _classify_url(h["url"], scope)
        tier_int = int(tier)
        label = _TIER_LABEL.get(tier_int, "UNKNOWN")
        reg.append({"url": h["url"], "title": h["title"], "tier": tier_int})
        n = len(reg)                            # stable 1-based index across the session
        line = f"[{n}] {h['title']}  ⟨TIER {tier_int} {label}⟩\n    {h['description']}"
        if tier == _sp.Tier.LOW_TRUST:
            line += "\n    — leads only; cannot back a stated fact, corroborate with a primary source"
        lines.append(line)
    return _text("\n".join(lines))


def _resolve_source_ref(ref: str) -> str:
    """Map a web_search result number ([n] or bare 'n') to its URL; pass a real
    URL through unchanged. Lets the model scrape by index without ever handling
    raw URLs."""
    s = (ref or "").strip().strip("[]")
    if s.isdigit():
        reg = _sources_for(_scope())
        i = int(s)
        if 1 <= i <= len(reg):
            return reg[i - 1]["url"]
    return ref


@tool("web_scrape",
      "Fetch one page's main content as markdown (via Firecrawl). To read a web_search "
      "result, pass its number (e.g. \"2\"); a full URL also works. Output is length-capped.",
      {"url": str})
async def t_web_scrape(args):
    url = _resolve_source_ref(args["url"])
    r = await web.scrape(url)
    if r.get("error"):
        return _text(f"Could not fetch {url}: {r['error']}")
    if not r.get("markdown"):
        return _text(f"No readable content at {url}.")
    _TIER_LABEL = {1: "PRIMARY", 2: "REPUTABLE", 3: "LOW-TRUST"}
    tier = _classify_url(r["url"], _scope())
    tier_int = int(tier)
    label = _TIER_LABEL.get(tier_int, "UNKNOWN")
    header = f"# {r.get('title') or url}  ⟨TIER {tier_int} {label}⟩\n{r['url']}\n"
    if tier == _sp.Tier.LOW_TRUST:
        header += "⚠️ LOW-TRUST source: leads only — cannot back a stated fact, corroborate with a primary source\n"
    return _text(header + "\n" + r["markdown"])


# ----- investment committee -------------------------------------------------

@tool("run_committee",
      "Convene the REAL multi-agent investment committee on ONE stock symbol and send "
      "the user the official PDF report. This runs the actual committee pipeline "
      "(specialists → debate → CIO vote), ~10-20 model calls, 1-3 minutes. "
      "Use this ONLY when the user explicitly asks to convene/run the committee, or for "
      "a committee report / verdict / vote on a symbol. NEVER fabricate, simulate, or "
      "describe a committee outcome yourself — always call this tool so the real "
      "subsystem (with its cost ceiling and locked process) produces the verdict. "
      "lang: 'zh'/'tc' for a 繁體中文 report, otherwise leave empty for English.",
      {"symbol": str, "lang": str})
async def t_committee(args):
    sym = str(args.get("symbol") or "").strip()
    if not sym:
        return _text("Provide a stock symbol, e.g. AAPL or 2330.TW.")
    from .committee.delivery import produce_report
    art = await produce_report(sym, args.get("lang"), REPORTS_DIR, source="chat")
    if art.error:
        return _text(art.error)
    _PENDING_DOCS.append(str(art.doc_path))
    return _text(art.summary + "\n\n(The full committee report has been sent to the user.)")


# ----- primary-source data tools (evidence integrity) -----------------------

@tool("sec_filings",
      "Recent SEC/EDGAR filings (8-K/10-Q/10-K) for a symbol — PRIMARY source for "
      "financial facts. Returns form type, filing date, title and SEC.gov URL. "
      "Requires CIO_SEC_UA to be set (format: 'AppName YourName your@email.com').",
      {"symbol": str})
async def t_sec_filings(args):
    from .data import edgar
    sym = (args.get("symbol") or "").strip().upper()
    if not sym:
        _ev("sec_filings", "", False, reason="no_symbol")
        return _text("Provide a stock symbol.")
    ua = edgar._user_agent()
    if not ua:
        _ev("sec_filings", sym, False, reason="no_CIO_SEC_UA")
        return _text("EDGAR not configured — set CIO_SEC_UA to enable (format: 'AppName Name email@example.com').")
    filings = edgar.recent_filings(sym)
    _ev("sec_filings", sym, True, source="EDGAR", filings=len(filings))
    if not filings:
        return _text(f"No recent EDGAR filings found for {sym} (symbol may not file with the SEC).")
    import json
    return _text(json.dumps(filings, indent=2))


@tool("analyst_ratings",
      "Latest analyst buy/hold/sell counts (Finnhub) — authoritative source for "
      "analyst ratings. Returns period, strong_buy, buy, hold, sell, strong_sell. "
      "Requires FINNHUB_API_KEY.",
      {"symbol": str})
async def t_analyst_ratings(args):
    from .data import finnhub
    sym = (args.get("symbol") or "").strip().upper()
    if not sym:
        _ev("analyst_ratings", "", False, reason="no_symbol")
        return _text("Provide a stock symbol.")
    if not finnhub._token():
        _ev("analyst_ratings", sym, False, reason="no_FINNHUB_API_KEY")
        return _text("Finnhub not configured — set FINNHUB_API_KEY to enable.")
    rec = finnhub.analyst_recs(sym)
    _ev("analyst_ratings", sym, True, source="Finnhub", found=rec is not None)
    if rec is None:
        return _text(f"No analyst recommendations found for {sym}.")
    import json
    return _text(json.dumps(rec, indent=2))


@tool("earnings_info",
      "Next/most-recent earnings date and estimates (Finnhub). Returns date, "
      "eps_estimate, eps_actual, revenue_estimate, and timing (bmo/amc). "
      "Requires FINNHUB_API_KEY.",
      {"symbol": str})
async def t_earnings_info(args):
    from .data import finnhub
    sym = (args.get("symbol") or "").strip().upper()
    if not sym:
        _ev("earnings_info", "", False, reason="no_symbol")
        return _text("Provide a stock symbol.")
    if not finnhub._token():
        _ev("earnings_info", sym, False, reason="no_FINNHUB_API_KEY")
        return _text("Finnhub not configured — set FINNHUB_API_KEY to enable.")
    earn = finnhub.earnings_calendar(sym)
    _ev("earnings_info", sym, True, source="Finnhub", found=earn is not None)
    if earn is None:
        return _text(f"No earnings data found for {sym}.")
    import json
    return _text(json.dumps(earn, indent=2))


@tool("company_profile",
      "Company profile including official website (Finnhub) — PRIMARY identity source. "
      "Returns name, weburl, finnhubIndustry, ipo date, and marketCap. Also registers "
      "the company's domain so its IR pages are treated as Tier-1 primary sources. "
      "Requires FINNHUB_API_KEY.",
      {"symbol": str})
async def t_company_profile(args):
    from .data import finnhub
    sym = (args.get("symbol") or "").strip().upper()
    if not sym:
        _ev("company_profile", "", False, reason="no_symbol")
        return _text("Provide a stock symbol.")
    if not finnhub._token():
        _ev("company_profile", sym, False, reason="no_FINNHUB_API_KEY")
        return _text("Finnhub not configured — set FINNHUB_API_KEY to enable.")
    profile = finnhub.company_profile(sym)
    _ev("company_profile", sym, True, source="Finnhub", found=profile is not None,
        weburl=(profile or {}).get("weburl") or "-")
    if profile is None:
        return _text(f"No company profile found for {sym}.")
    # Register the issuer domain for tier-1 IR-page resolution.
    _register_issuer_domain(profile.get("weburl") or "", _scope())
    import json
    return _text(json.dumps(profile, indent=2))


@tool("clinical_trials",
      "Search clinicaltrials.gov — the ONLY authoritative registry for trial phase, "
      "indication, endpoint, and status. Use this before stating any clinical fact. "
      "Returns nct_id, title, phase, status, conditions, interventions, and URL. "
      "No API key required.",
      {"query": str, "limit": int})
async def t_clinical_trials(args):
    from .data import clinicaltrials
    q = (args.get("query") or "").strip()
    if not q:
        _ev("clinical_trials", "", False, reason="no_query")
        return _text("Provide a search query (e.g. company name + drug + indication).")
    limit = int(args.get("limit") or 5)
    results = clinicaltrials.search_trials(q, limit=limit)
    _ev("clinical_trials", q[:40], True, source="clinicaltrials.gov", results=len(results))
    if not results:
        return _text(f"No clinical trials found for query: {q!r}. "
                     "The trial may not be registered or the query needs adjustment.")
    import json
    return _text(json.dumps(results, indent=2))


CIO_TOOLS = [t_summary, t_positions, t_realized, t_set_price, t_ingest, t_alloc_chart,
             t_pl_chart, t_remember, t_recall, t_forget, t_search, t_get,
             t_save_playbook, t_list_playbooks,
             t_add_econ_event, t_list_econ_events, t_econ_events_pdf, t_econ_events_image,
             t_stock_quote, t_stock_history, t_list_strategies, t_run_strategy,
             t_run_strategy_profile,
             t_refresh_prices, t_stock_panel, t_stock_indicators, t_watchlist_prices,
             t_list_watchlists, t_watchlist_add, t_watchlist_remove,
             t_watchlist_activate, t_run_alpha_hunter, t_market_regime,
             t_market_clock, t_web_search, t_web_scrape, t_committee,
             t_sec_filings, t_analyst_ratings, t_earnings_info, t_company_profile,
             t_clinical_trials]
_TOOL_NAMES = ["mcp__cio__" + t.name for t in CIO_TOOLS]

SYSTEM_PROMPT = """You are the user's personal CIO agent, focused on their stock portfolio.

Rules:
- NEVER invent numbers. Get every figure from the tools. If data is missing, say so.
- LIVE PRICES ARE AVAILABLE. For any "what's the price / how much is X" question, call
  stock_quote (Yahoo Finance, cached) and answer with the real quote. Use
  refresh_prices to update all open positions with live closes before valuing the
  portfolio. set_price is only a MANUAL OVERRIDE for symbols Yahoo can't price
  (e.g. private/illiquid holdings) — do NOT tell the user "no live feed".
- DATA FRESHNESS. The first line of each turn is a [context] clock (local + ET time,
  NASDAQ status, latest settled session) — it is the ONLY source of truth for what
  "today"/"now" is. Trust it; never assume the date. market_clock returns the same.
  Quotes carry market_status / session_date / is_live / quote_kind. ALWAYS report a
  price with its as-of date. When NASDAQ is closed the latest settled close IS the
  current price — say "as of <date> close"; NEVER present a prior-session close as
  "today's" move. Do NOT use web tools to obtain a price (the stock tools are
  authoritative for figures); use web_search/web_scrape only for the *narrative* of
  why something moved, labelling each figure by its as-of date.
- WEB ACCESS: use web_search to find live news/analyst/filing pages and web_scrape to
  read one. Use it for qualitative context (catalysts, news, sentiment) — NOT for prices
  or financials, which always come from the stock tools. Each web_search result is
  labelled ⟨TIER n LABEL⟩ — trust that label. web_search results are numbered [1], [2], …;
  cite a source by its number in your prose (e.g. "jobs report missed [1]").
  Do NOT paste raw URLs and do NOT write your own "Sources" / "References" list — the
  system appends the real, complete links as a single Sources footer automatically.
  Pasting a URL yourself risks truncating it into a dead 404 link.
- EVIDENCE INTEGRITY — enforced rules for material facts (clinical / financial /
  corporate-action / regulatory / analyst):
  1. MATERIAL FACTS FROM RIGHT-CLASS TIER-1 ONLY: clinical facts (trial phase,
     indication, endpoint, p-value, approval status) → clinical_trials tool
     (clinicaltrials.gov). Financial facts (revenue, EPS, guidance) → sec_filings or
     earnings_info. Analyst ratings → analyst_ratings. Prices/valuation figures →
     stock tools (NEVER web). A Tier-1 finance page is the WRONG CLASS for a clinical
     claim — even reputable sources can be wrong-class.
  2. TIER-3 CANNOT BACK A STATED FACT: sources marked ⟨TIER 3 LOW-TRUST⟩ are leads
     only. Read them for direction, then confirm via a primary tool before asserting.
     A web_search snippet is NOT evidence — web_scrape (or call a primary tool) the
     source before asserting its content.
  3. RIGHT CLASS: a clinical endpoint must come from the trial registry, not a
     finance or news page — even Tier-1 financial sources are wrong-class for clinical.
  4. CORROBORATION: a material fact needs ≥1 Tier-1 OR ≥2 independent Tier-2 sources;
     otherwise prefix the sentence [unverified] before stating it.
  5. LABEL EVERY FACTUAL LINE: cite facts with their [n] source number(s). Anything
     you reasoned/estimated yourself (ratios, projections, sentiment, "worth buying")
     must be prefixed [inference]. Never blend cited fact and inference in one sentence.
  6. BUY/SELL DECISIONS: for a genuine buy/sell verdict, offer run_committee (the
     verified gold-path with locked process) rather than free-handing a verdict.
- INVESTMENT COMMITTEE: when the user asks to "convene the committee", run a committee,
  or wants the committee's verdict/vote on a stock, call the run_committee tool. It runs
  the REAL multi-agent committee and sends the official PDF. NEVER invent committee seats,
  votes, or a verdict yourself — do not simulate it inline. If unsure whether they want the
  full (cost-bearing) run, confirm the symbol first, then call the tool.
- Be concise and direct. Lead with the number that answers the question.
- Use allocation_chart / pl_chart when a visual helps or the user asks to "see" something.
- If the user sends an image path, use the Read tool to view it (e.g. a receipt or broker
  screenshot) and extract the relevant figures.
- Currency is whatever the data says; default USD.
- You run 24/7. Persistent memory you've saved is injected above under "Persistent
  memory" — trust it and don't re-ask. Use remember/recall/forget for qualitative
  context and memory_search for older details. NEVER store figures in memory —
  recompute those from the portfolio tools every time."""


def build_options(model: str | None = None, resume: str | None = None,
                  system_prompt: str | None = None, hooks=None) -> ClaudeAgentOptions:
    server = create_sdk_mcp_server("cio", "1.0.0", CIO_TOOLS)
    return ClaudeAgentOptions(
        system_prompt=system_prompt or SYSTEM_PROMPT,
        mcp_servers={"cio": server},
        allowed_tools=_TOOL_NAMES + ["Read"],
        disallowed_tools=["Bash", "Write", "Edit", "WebFetch", "WebSearch"],
        permission_mode="bypassPermissions",
        cwd=str(PROJECT_ROOT),
        # Explicit argument wins over the env default — without it a programmatic
        # CIOAgent(model=...) override was silently ignored.
        model=model or _env("MODEL") or None,
        resume=resume,
        hooks=hooks,
    )


_MATERIAL_CLAIM_RE = re.compile(
    r"\b(trial|phase|endpoint|p[<-]\s*0\.\d+|efficacy|revenue|EPS|earnings|"
    r"guidance|acquisition|merger|dividend|buyback|rating|target price|approval|"
    r"FDA|PDUFA|MADRS|indication)\b",
    re.I,
)
_WEB_CITE_RE = re.compile(r"\[\d+\]")


def _has_material_or_web_claim(text: str) -> bool:
    """Quick heuristic: True if the answer contains a material claim or a web citation."""
    return bool(_MATERIAL_CLAIM_RE.search(text)) or bool(_WEB_CITE_RE.search(text))


async def _run_verifier(answer: str, sources: list[dict]) -> str | None:
    """Item 5 — optional Haiku post-pass to flag unsupported material claims.

    Gated by CIO_VERIFY_CLAIMS=1 (default off). At most 1 call per turn.
    Skipped entirely if no material/web claim is present.
    Returns a formatted ⚠️ Verifier note to append, or None.
    """
    if _env("VERIFY_CLAIMS", "0") != "1":
        return None
    if not _has_material_or_web_claim(answer):
        return None

    # Build a minimal source context (title + tier) to keep the prompt small.
    src_ctx = "\n".join(
        f"[{i+1}] (Tier {s.get('tier', '?')}) {s.get('title', s.get('url', ''))}"
        for i, s in enumerate(sources)
    ) or "(no sources cited)"

    prompt = (
        "You are a claim verifier. Review the ANSWER below and the SOURCES list.\n"
        "Flag any material claim (clinical, financial, corporate-action, regulatory, "
        "analyst) that (a) cites no source or cites only a Tier-3 (LOW-TRUST) source, "
        "or (b) cannot be confirmed from a right-class Tier-1 source.\n"
        "Reply ONLY with a short bulleted list of flagged claims (one line each), "
        "or 'PASS' if no issues found. Do not rewrite the answer.\n\n"
        f"SOURCES:\n{src_ctx}\n\nANSWER:\n{answer}"
    )
    try:
        import anthropic

        def _call():
            client = anthropic.Anthropic()
            return client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
            )

        # The sync SDK call would otherwise block the event loop (and with it the
        # Telegram dispatcher) for the whole request; run it in a worker thread.
        msg = await asyncio.to_thread(_call)
        result = (msg.content[0].text if msg.content else "").strip()
        if result and result.upper() != "PASS":
            return f"\n\n⚠️ Verifier (Haiku):\n{result}"
    except Exception as e:
        import logging
        logging.getLogger("cio.agent").warning("verifier call failed: %s", e)
    return None


def _usage_tokens(u) -> int:
    """Sum input+output tokens from an SDK usage payload (dict or object), 0 if absent."""
    if u is None:
        return 0
    def g(k):
        val = u.get(k) if isinstance(u, dict) else getattr(u, k, 0)
        return int(val or 0)
    try:
        return g("input_tokens") + g("output_tokens")
    except Exception:
        return 0


class CIOAgent:
    """One conversational session (per chat). Reuses a connected SDK client.

    For 24/7 operation the SDK handles long-context overflow with automatic
    compaction; durable facts go to the memory tools. `resume` reconnects to a
    prior SDK session so a restarted bot continues the same thread. The latest
    `session_id` is reported via `on_session_id` so the caller can persist it.
    """

    def __init__(self, model: str | None = None, resume: str | None = None,
                 on_session_id=None, chat_id: int | None = None):
        self._model = model
        self._resume = resume
        self._chat_id = chat_id
        self._scope = f"chat:{chat_id}" if chat_id is not None else "global"
        self._client = self._make_client(resume)
        self._connected = False
        self._on_session_id = on_session_id
        self._session_id: str | None = resume
        self._turns = 0          # turns since last checkpoint (rolling session)
        self._tokens = 0         # approx tokens since last checkpoint
        self._compaction_pending = False   # set by PreCompact hook -> checkpoint soon
        self._last_docs: list[str] = []    # doc paths from the most recent main turn
        # Local day of the last persisted turn (survives restarts via the meta
        # table). Drives the day-boundary roll so one chat thread never spans days.
        try:
            self._last_turn_day = memory.get_last_turn_day(chat_id)
        except Exception:
            self._last_turn_day = None

    async def _on_precompact(self, input_data, tool_use_id, ctx) -> dict:
        """PreCompact hook: the SDK is about to lossily summarize old turns.
        Flag a checkpoint so we durably persist a digest right after this turn —
        nothing notable is lost to the summary."""
        import logging
        logging.getLogger("cio.agent").info("PreCompact (%s) for %s — will checkpoint",
                                             input_data.get("trigger"), self._scope)
        self._compaction_pending = True
        return {}

    def _make_client(self, resume: str | None) -> ClaudeSDKClient:
        """Build a client whose system prompt has this chat's memory injected.
        Called on init, on fresh-session fallback, and on rolling-session fork —
        so each (re)connect refreshes the injected context."""
        prompt = context.compose_system_prompt(SYSTEM_PROMPT, self._chat_id)
        self._system_prompt = prompt   # kept for the detailed-history log (convlog)
        hooks = {"PreCompact": [HookMatcher(hooks=[self._on_precompact])]}
        return ClaudeSDKClient(
            options=build_options(self._model, resume, system_prompt=prompt, hooks=hooks))

    async def _ensure(self):
        if self._connected:
            return
        try:
            await self._client.connect()
        except Exception as e:
            # Stale/missing transcript after a reboot ("No conversation found
            # with session ID ...") must not brick the chat. Fall back to a
            # fresh session — financial truth lives in the DB regardless.
            if not self._resume:
                raise
            import logging
            logging.getLogger("cio.agent").warning(
                "resume %s failed (%s); starting a fresh session", self._resume, e)
            self._resume = None
            self._session_id = None
            self._client = self._make_client(None)
            await self._client.connect()
        self._connected = True

    async def warm(self) -> None:
        """Eagerly connect (and resume) so the first message has no startup lag."""
        await self._ensure()

    def _note_session(self, session_id: str | None) -> None:
        if session_id and session_id != self._session_id:
            self._session_id = session_id
            if self._on_session_id:
                self._on_session_id(session_id)

    async def _run_query(self, prompt: str) -> tuple[str, list[str]]:
        """One locked turn against the current client; returns (text, images).

        Also records the turn's Claude token usage so the dev dashboard reflects
        real chat consumption (input+output from each AssistantMessage.usage)."""
        async with _LOCK:
            global _ACTIVE_SCOPE, _SEARCHED_THIS_TURN
            _ACTIVE_SCOPE = self._scope   # memory tools read/write this chat's namespace
            _SEARCHED_THIS_TURN = False
            _PENDING.clear()
            _PENDING_DOCS.clear()
            # NB: _SOURCES is NOT cleared here — it persists across turns within a
            # session so cross-turn [n] citations resolve. Reset on roll/close only.
            await self._client.query(prompt)
            parts: list[str] = []
            tokens = 0          # accumulated per-AssistantMessage usage (often empty)
            result_tokens = 0   # authoritative turn total from the final ResultMessage
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    self._note_session(msg.session_id)
                    for blk in msg.content:
                        if isinstance(blk, TextBlock):
                            parts.append(blk.text)
                    tokens += _usage_tokens(msg.usage)
                elif isinstance(msg, ResultMessage):
                    # ResultMessage.usage is the cumulative usage for the whole turn
                    # (the agent SDK leaves AssistantMessage.usage empty), so prefer it.
                    self._note_session(getattr(msg, "session_id", None))
                    result_tokens = _usage_tokens(msg.usage)
            text = "\n".join(parts).strip()
            # Effective tokens: ResultMessage total > summed AssistantMessage usage >
            # a local estimate (so the figure is never a misleading 0).
            eff_tokens = result_tokens or tokens
            if eff_tokens <= 0:
                eff_tokens = context.count_tokens(prompt) + context.count_tokens(text)
            # Sources footer is appended in ask() (user-facing only), so internal
            # digest/playbook turns that reuse _run_query don't get a footer.
            self._record_usage(eff_tokens, prompt, text)
            # Detailed conversation history (opt-in, off by default): full text log.
            convlog.log_call("claude", self._model or "claude-agent-sdk",
                             getattr(self, "_system_prompt", "") or "", prompt, text,
                             eff_tokens, scope=self._scope, kind="chat")
            images = list(_PENDING)
            _PENDING.clear()
            # Documents (committee PDF) are stashed on the instance, not returned,
            # so the (text, images) signature stays stable for tests that stub
            # _run_query. ask() drains it before any checkpoint turn overwrites it.
            self._last_docs = list(_PENDING_DOCS)
            _PENDING_DOCS.clear()
            return text, images

    @staticmethod
    def _record_usage(tokens: int, prompt: str, text: str) -> None:
        """Add this turn's Claude tokens to the daily usage table. Never raises.
        Falls back to a local estimate when the SDK reports no usage."""
        try:
            if tokens <= 0:
                tokens = context.count_tokens(prompt) + context.count_tokens(text)
            from .committee import usage as _usage
            _usage.record("claude", tokens)
        except Exception:
            pass

    async def ask(self, prompt: str) -> tuple[str, list[str], list[str]]:
        """Send a turn; return (assistant_text, image_paths, doc_paths). May trigger
        a rolling-session checkpoint afterwards if the transcript is getting large."""
        await self._ensure()
        # Day-boundary roll: a new local day starts a new conversation. If we
        # resumed a prior-day thread, digest + reseed BEFORE this turn so (a) the
        # rolling digest reliably persists even when the bot restarts daily (the
        # per-process turn counter alone never reaches ROLL_TURNS across reboots),
        # and (b) one SDK thread never spans multiple days — otherwise the agent
        # treats the whole multi-day thread as "this conversation" and mis-dates an
        # old mistake as today's. Prior days survive as the injected digest.
        today = timeutil.today_local()
        if self._last_turn_day and self._last_turn_day != today and self._session_id:
            import logging
            logging.getLogger("cio.agent").info(
                "day boundary %s→%s for %s — rolling session",
                self._last_turn_day, today, self._scope)
            prev_month = self._last_turn_day[:7]
            await self._checkpoint()
            # Month boundary too → consolidate the month that just ended into a durable
            # long-term HOT memo (digest-of-digests). Runs after the day roll so the
            # final day's digest is included. Once-per-month, best-effort.
            if prev_month != today[:7]:
                await self._monthly_rollup(prev_month)
        # Inject the authoritative clock as the turn's first line so the agent never
        # guesses the date or mislabels a settled close as "today" (see DATA FRESHNESS).
        prompt = f"[context] {_clock_context()}\n\n{prompt}"
        # Periodic nudge to persist notable context (cheap; no extra LLM call).
        if NUDGE_TURNS and self._turns and self._turns % NUDGE_TURNS == 0:
            prompt = prompt + _NUDGE_SUFFIX
        text, images = await self._run_query(prompt)
        # Append the verified Sources footer (user-facing only) BEFORE any checkpoint
        # below resets this scope's registry.
        text = _append_sources(text, list(_sources_for(self._scope)),
                               searched=_SEARCHED_THIS_TURN, scope=self._scope)
        # Optional claim verifier (CIO_VERIFY_CLAIMS=1, default off) — runs before any
        # checkpoint resets this scope's registry. No-op when disabled / no material claim.
        note = await _run_verifier(text, list(_sources_for(self._scope)))
        if note:
            text = text + note
        # Capture this turn's documents before any checkpoint turn clears the stash.
        docs, self._last_docs = self._last_docs, []
        # Record the local day of this turn so a restart on a later day can detect
        # the boundary and roll (persisted; the in-memory counter resets on reboot).
        self._last_turn_day = today
        try:
            memory.set_last_turn_day(self._chat_id, today)
        except Exception:
            pass
        self._turns += 1
        self._tokens += context.count_tokens(prompt) + context.count_tokens(text)
        if (self._compaction_pending or self._turns >= ROLL_TURNS
                or self._tokens >= ROLL_TOKENS):
            await self._checkpoint()
        return text, images, docs

    async def _checkpoint(self) -> None:
        """Bound transcript growth: digest the current session, persist it BEFORE
        forking, then reseed a fresh session whose injected context now includes
        that digest. Financial data is untouched (it lives in the DB)."""
        import logging
        log = logging.getLogger("cio.agent")
        try:
            digest, _ = await self._run_query(_DIGEST_PROMPT)
        except Exception:
            log.exception("checkpoint digest failed; deferring roll")
            return
        if digest.strip():
            memory.add_digest(self._chat_id, self._session_id, digest,
                              turn_count=self._turns, token_count=self._tokens)
        # --- self-improving reflection (W10): runs while the session is still live ---
        try:
            promoted = memory.promote_hot(self._scope)   # useful notes -> injected
            if promoted:
                log.info("promoted %d note(s) to hot for %s", promoted, self._scope)
        except Exception:
            log.exception("promote_hot failed")
        try:
            pb_text, _ = await self._run_query(_PLAYBOOK_PROMPT)
            parsed = _parse_playbook(pb_text)
            if parsed:
                name, steps = parsed
                try:
                    memory.add_playbook(name, steps, scope=self._scope)
                    log.info("auto-distilled playbook '%s' for %s", name, self._scope)
                except memory.FiguresFirewallError:
                    pass   # never persist a procedure that smuggles figures
        except Exception:
            log.exception("playbook distillation failed")
        # reseed a fresh, small session (digest already saved -> safe if this fails)
        self._turns = 0
        self._tokens = 0
        self._compaction_pending = False
        _SOURCES.pop(self._scope, None)   # old source numbers die with the old thread
        _ISSUER_DOMAINS.pop(self._scope, None)   # issuer set is rebuilt on demand
        try:
            await self._client.disconnect()
        except Exception:
            pass
        self._resume = None
        self._session_id = None
        self._client = self._make_client(None)   # re-injects context incl. new digest
        self._connected = False
        await self._ensure()
        log.info("rolled session for %s; digest saved, fresh thread seeded", self._scope)

    async def _monthly_rollup(self, year_month: str) -> None:
        """Consolidate a month of daily digests into one durable long-term memo, stored
        as a HOT note (injected every session) and keyed `monthly_rollup:<YYYY-MM>` so a
        re-run upserts. Best-effort; once per month (guarded by a meta marker). Runs on
        the freshly reseeded post-day-roll thread, feeding the month's digests in-prompt."""
        import logging
        log = logging.getLogger("cio.agent")
        try:
            if memory.get_meta(f"last_rollup_month:{self._chat_id}") == year_month:
                return                                  # already rolled this month
            digests = memory.digests_in_month(self._chat_id, year_month)
            if not digests:
                return
            joined = "\n".join(f"- [{d['created_at'][:10]}] {d['summary']}" for d in digests)
            memo, _ = await self._run_query(_ROLLUP_PROMPT + joined)
            if memo.strip():
                try:
                    memory.remember(memo.strip(), key=f"monthly_rollup:{year_month}",
                                    scope=self._scope, tier="hot", importance=4.0,
                                    source="auto")
                except memory.FiguresFirewallError:
                    log.info("monthly rollup %s rejected by figures firewall", year_month)
            memory.set_meta(f"last_rollup_month:{self._chat_id}", year_month)
            log.info("monthly rollup %s saved for %s (%d digests)",
                     year_month, self._scope, len(digests))
        except Exception:
            log.exception("monthly rollup failed for %s", year_month)

    async def close(self):
        _SOURCES.pop(self._scope, None)
        _ISSUER_DOMAINS.pop(self._scope, None)
        if self._connected:
            await self._client.disconnect()
            self._connected = False
