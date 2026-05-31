"""Claude agent layer.

Runs on your Claude Code / Pro subscription auth via `claude-agent-sdk` — no
ANTHROPIC_API_KEY required. Portfolio functions are exposed to the model as
in-process MCP tools. Chart tools drop PNG paths into a per-request "outbox"
that the bot reads and sends as photos after the turn.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)

from . import charts, context, memory, portfolio, recall

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Collector for image paths produced by chart tools during a turn. A plain
# module global (not a contextvar) because the SDK runs MCP tool callbacks in a
# separate task context. `_LOCK` serializes turns so this stays correct even if
# two chats arrive at once.
_PENDING: list[str] = []
_LOCK = asyncio.Lock()

# Rolling session: after this many turns or approx tokens, checkpoint a digest
# and reseed a fresh session so a single chat's transcript can't grow forever.
ROLL_TURNS = int(os.getenv("CFO_ROLL_TURNS", "40"))
ROLL_TOKENS = int(os.getenv("CFO_ROLL_TOKENS", "16000"))

# Every N turns, remind the agent to persist anything notable (Hermes-style
# nudge) — cheap prompt augmentation, no extra LLM call.
NUDGE_TURNS = int(os.getenv("CFO_NUDGE_TURNS", "8"))
_NUDGE_SUFFIX = (
    "\n\n(System reminder: if anything durable about my preferences, plans, or "
    "watchlist came up, save it with the remember tool. Never save figures.)"
)

_DIGEST_PROMPT = (
    "Summarize our conversation so far in 4-6 sentences for your own future "
    "reference: decisions made, my stated preferences, and open threads. Do NOT "
    "include specific dollar amounts, prices, or P&L numbers (those are recomputed "
    "from data). Do not call any tools — just write the summary."
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


def _emit_image(path: str | None, ok_msg: str, empty_msg: str) -> dict:
    if not path:
        return _text(empty_msg)
    _PENDING.append(path)
    return _text(ok_msg)


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
# _LOCK, so a module global is safe; CFOAgent sets it before each query so the
# memory tools read/write the right per-chat namespace.
_ACTIVE_SCOPE = "global"


def _scope() -> str:
    return _ACTIVE_SCOPE


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
      "Optional `key` makes it an upsert; `important`=true pins it into startup context.",
      {"value": str, "key": str, "important": bool})
async def t_remember(args):
    try:
        memory.remember(args["value"], key=args.get("key") or None, scope=_scope(),
                        tier="hot" if args.get("important") else "warm",
                        importance=2.0 if args.get("important") else 1.0, source="agent")
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
      "Hybrid (keyword + semantic) search over your saved notes AND past conversation "
      "turns. Use when the user refers to something from earlier that isn't in the "
      "injected memory above — e.g. 'what did I say about NVDA a while back?'.",
      {"query": str, "k": int})
async def t_search(args):
    hits = recall.search(args["query"], k=int(args.get("k") or 5), scope=_scope())
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


CFO_TOOLS = [t_summary, t_positions, t_realized, t_set_price, t_ingest, t_alloc_chart,
             t_pl_chart, t_remember, t_recall, t_forget, t_search, t_get,
             t_save_playbook, t_list_playbooks]
_TOOL_NAMES = ["mcp__cfo__" + t.name for t in CFO_TOOLS]

SYSTEM_PROMPT = """You are the user's personal CFO agent, focused on their stock portfolio.

Rules:
- NEVER invent numbers. Get every figure from the tools. If data is missing, say so.
- Prices are entered manually (no live feed). If a position has no price, tell the user
  to send e.g. "set AAPL 230" and they will get a price.
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
    server = create_sdk_mcp_server("cfo", "1.0.0", CFO_TOOLS)
    return ClaudeAgentOptions(
        system_prompt=system_prompt or SYSTEM_PROMPT,
        mcp_servers={"cfo": server},
        allowed_tools=_TOOL_NAMES + ["Read"],
        disallowed_tools=["Bash", "Write", "Edit", "WebFetch", "WebSearch"],
        permission_mode="bypassPermissions",
        cwd=str(PROJECT_ROOT),
        model=model or os.getenv("CFO_MODEL") or None,
        resume=resume,
        hooks=hooks,
    )


class CFOAgent:
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

    async def _on_precompact(self, input_data, tool_use_id, ctx) -> dict:
        """PreCompact hook: the SDK is about to lossily summarize old turns.
        Flag a checkpoint so we durably persist a digest right after this turn —
        nothing notable is lost to the summary."""
        import logging
        logging.getLogger("cfo.agent").info("PreCompact (%s) for %s — will checkpoint",
                                             input_data.get("trigger"), self._scope)
        self._compaction_pending = True
        return {}

    def _make_client(self, resume: str | None) -> ClaudeSDKClient:
        """Build a client whose system prompt has this chat's memory injected.
        Called on init, on fresh-session fallback, and on rolling-session fork —
        so each (re)connect refreshes the injected context."""
        prompt = context.compose_system_prompt(SYSTEM_PROMPT, self._chat_id)
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
            logging.getLogger("cfo.agent").warning(
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
        """One locked turn against the current client; returns (text, images)."""
        async with _LOCK:
            global _ACTIVE_SCOPE
            _ACTIVE_SCOPE = self._scope   # memory tools read/write this chat's namespace
            _PENDING.clear()
            await self._client.query(prompt)
            parts: list[str] = []
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    self._note_session(msg.session_id)
                    for blk in msg.content:
                        if isinstance(blk, TextBlock):
                            parts.append(blk.text)
            images = list(_PENDING)
            _PENDING.clear()
            return "\n".join(parts).strip(), images

    async def ask(self, prompt: str) -> tuple[str, list[str]]:
        """Send a turn; return (assistant_text, image_paths). May trigger a
        rolling-session checkpoint afterwards if the transcript is getting large."""
        await self._ensure()
        # Periodic nudge to persist notable context (cheap; no extra LLM call).
        if NUDGE_TURNS and self._turns and self._turns % NUDGE_TURNS == 0:
            prompt = prompt + _NUDGE_SUFFIX
        text, images = await self._run_query(prompt)
        self._turns += 1
        self._tokens += context.count_tokens(prompt) + context.count_tokens(text)
        if (self._compaction_pending or self._turns >= ROLL_TURNS
                or self._tokens >= ROLL_TOKENS):
            await self._checkpoint()
        return text, images

    async def _checkpoint(self) -> None:
        """Bound transcript growth: digest the current session, persist it BEFORE
        forking, then reseed a fresh session whose injected context now includes
        that digest. Financial data is untouched (it lives in the DB)."""
        import logging
        log = logging.getLogger("cfo.agent")
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

    async def close(self):
        if self._connected:
            await self._client.disconnect()
            self._connected = False
