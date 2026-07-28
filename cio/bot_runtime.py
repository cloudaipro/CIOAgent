"""
bot_runtime.py — Runtime seam for the general Telegram bot chat.

Every other agent (committee specialists, moderator, CIO, translator, WMA) goes
through ``engine.ask_role``'s ``(system, user) -> text`` transport, so the named
fallback chains (docs/FALLBACK-CHAINS.md) cover them for free. Bot chat cannot
use that transport — it drives a 42-tool tool-calling loop, not a single prompt
— so it needs its own runtime selection. Chain *policy* (chains, skip rules) is
shared with the committee; chain *transport* is not.

A ``BotRuntime`` is whoever drives that tool loop for one chat turn. This step
ships exactly one implementation, ``ClaudeRuntime`` (today's ``CIOAgent``,
unchanged). ``select_runtime`` is the seam Step 11's ``OpenAIRuntime`` plugs
into; until then a surviving ``openai`` link still runs the chat, on Claude.

Plan of record: docs/BOT-CHAT-OPENAI-MIGRATION.md.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from .agent import CIOAgent as ClaudeRuntime
from .committee import engine, models
from .committee import usage as _usage

log = logging.getLogger(__name__)


@runtime_checkable
class BotRuntime(Protocol):
    """The whole-turn driver behind one chat's tool-calling loop.

    Exactly the surface cio/bot.py already drives a chat agent through — read
    off the call sites (cio/bot.py:181,230,255,661). Do not widen it: a second
    runtime (Step 11's OpenAIRuntime) must satisfy this same four-member
    surface and nothing more.
    """

    session_id: str | None

    async def ask(self, prompt: str) -> tuple[str, list[str], list[str]]:
        """Send one turn; return (assistant_text, image_paths, doc_paths)."""
        ...

    async def warm(self) -> None:
        """Eagerly connect/resume so the first real turn has no startup lag."""
        ...

    async def close(self) -> None:
        """Release any connection/session resources this runtime holds."""
        ...


def select_runtime(chat_id: int) -> BotRuntime:
    """Resolve the operator's ``bot_chat`` chain and return a runtime for *chat_id*.

    ``bot_chat`` is a role_key like any other agent's (models.resolve_chain),
    walked in order. Per link:
      1. ``service == "nim"`` → skip. Bot chat is claude+openai only — 44 tools
         is too wide a surface for a weak tool-caller.
      2. the service is limit-latched → skip.
      3. the service is over its configured daily budget → skip.
    The first link surviving all three decides the runtime. In this step the
    only runtime that exists is ClaudeRuntime: a surviving ``claude`` link uses
    it directly, and a surviving ``openai`` link *also* falls back to it —
    temporary, deleted in Step 11 once OpenAIRuntime exists.

    ``CIO_BOT_ENGINE=claude`` forces ClaudeRuntime and skips chain resolution
    entirely. Any other value is logged and ignored (Step 11 adds ``openai``).

    Never raises. A chat that cannot answer is worse than one that answers over
    budget, so budget/latch are advisory here: a missing chain, an unknown
    chain name, an empty link list, a malformed link, or a usage/latch lookup
    that throws all degrade to ClaudeRuntime, and if every link is skipped this
    still returns a (over-budget) ClaudeRuntime rather than nothing.
    """
    override = os.getenv("CIO_BOT_ENGINE")
    if override:
        if override == "claude":
            return ClaudeRuntime(chat_id=chat_id)
        log.warning("CIO_BOT_ENGINE=%r not recognized (only 'claude' today); ignoring",
                    override)

    try:
        chain = models.resolve_chain("bot_chat") or []
    except Exception:
        log.exception("select_runtime: resolve_chain('bot_chat') failed; using Claude")
        return ClaudeRuntime(chat_id=chat_id)

    for link in chain:
        try:
            if not isinstance(link, dict):
                log.warning("select_runtime: malformed link %r; skipping", link)
                continue
            service = link.get("service")

            if service == "nim":
                log.info("select_runtime: nim link skipped (bot chat is claude+openai only)")
                continue
            if engine.is_latched(service):
                log.info("select_runtime: %s limit-latched; falling through", service)
                continue
            if _usage.over_budget(service, link.get("daily_limit")):
                log.info("select_runtime: %s at daily limit; falling through", service)
                continue

            if service == "openai":
                # STEP-11: replace with a real OpenAIRuntime. Until then a
                # surviving openai link still runs the chat turn — on Claude.
                log.info("select_runtime: openai link selected but no OpenAI "
                         "runtime yet; using Claude")
                return ClaudeRuntime(chat_id=chat_id)

            # service == "claude", or any other value we don't recognize yet:
            # Claude is the only runtime this step ships, so it is also the
            # safe default for the first link that survives the skips above.
            return ClaudeRuntime(chat_id=chat_id)
        except Exception:
            log.exception("select_runtime: error evaluating link %r; skipping", link)
            continue

    log.warning("select_runtime: every bot_chat link was skipped; using Claude "
               "anyway (a chat that can't answer is worse than one over budget)")
    return ClaudeRuntime(chat_id=chat_id)
