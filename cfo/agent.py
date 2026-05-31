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
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)

from . import charts, memory, portfolio

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Collector for image paths produced by chart tools during a turn. A plain
# module global (not a contextvar) because the SDK runs MCP tool callbacks in a
# separate task context. `_LOCK` serializes turns so this stays correct even if
# two chats arrive at once.
_PENDING: list[str] = []
_LOCK = asyncio.Lock()


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
    return _text(f"Price set: {args['symbol'].upper()} = {args['price']}")


@tool("ingest_transactions_csv",
      "Import a transactions CSV (columns: txn_date,symbol,action,quantity,price[,fees,currency,notes]).",
      {"path": str})
async def t_ingest(args):
    try:
        n = portfolio.ingest_transactions_csv(args["path"])
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


@tool("remember",
      "Persist a small fact across sessions/restarts (preferences, watchlist, context). "
      "NOT for financial figures — those live in the portfolio data.",
      {"key": str, "value": str})
async def t_remember(args):
    memory.remember(args["key"], args["value"])
    return _text(f"Remembered: {args['key']}")


@tool("recall",
      "Recall a remembered fact by key, or list all remembered facts if key is omitted.",
      {"key": str})
async def t_recall(args):
    facts = memory.recall(args.get("key") or None)
    return _text(json.dumps(facts, indent=2) if facts else "Nothing remembered yet.")


@tool("forget", "Delete a remembered fact by key.", {"key": str})
async def t_forget(args):
    ok = memory.forget(args["key"])
    return _text(f"Forgot: {args['key']}" if ok else f"No such memory: {args['key']}")


CFO_TOOLS = [t_summary, t_positions, t_realized, t_set_price, t_ingest, t_alloc_chart,
             t_pl_chart, t_remember, t_recall, t_forget]
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
- You run 24/7. Use remember/recall/forget to keep durable context (preferences,
  watchlist, recurring questions) across restarts. Never store figures there —
  recompute those from the portfolio tools every time."""


def build_options(model: str | None = None, resume: str | None = None) -> ClaudeAgentOptions:
    server = create_sdk_mcp_server("cfo", "1.0.0", CFO_TOOLS)
    return ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={"cfo": server},
        allowed_tools=_TOOL_NAMES + ["Read"],
        disallowed_tools=["Bash", "Write", "Edit", "WebFetch", "WebSearch"],
        permission_mode="bypassPermissions",
        cwd=str(PROJECT_ROOT),
        model=model or os.getenv("CFO_MODEL") or None,
        resume=resume,
    )


class CFOAgent:
    """One conversational session (per chat). Reuses a connected SDK client.

    For 24/7 operation the SDK handles long-context overflow with automatic
    compaction; durable facts go to the memory tools. `resume` reconnects to a
    prior SDK session so a restarted bot continues the same thread. The latest
    `session_id` is reported via `on_session_id` so the caller can persist it.
    """

    def __init__(self, model: str | None = None, resume: str | None = None,
                 on_session_id=None):
        self._model = model
        self._resume = resume
        self._client = ClaudeSDKClient(options=build_options(model, resume))
        self._connected = False
        self._on_session_id = on_session_id
        self._session_id: str | None = resume

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
            self._client = ClaudeSDKClient(options=build_options(self._model))
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

    async def ask(self, prompt: str) -> tuple[str, list[str]]:
        """Send a turn; return (assistant_text, image_paths)."""
        await self._ensure()
        async with _LOCK:
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

    async def close(self):
        if self._connected:
            await self._client.disconnect()
            self._connected = False
