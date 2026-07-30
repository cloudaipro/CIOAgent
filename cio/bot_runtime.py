"""
bot_runtime.py — Runtime seam for the general Telegram bot chat.

Every other agent (committee specialists, moderator, CIO, translator, WMA) goes
through ``engine.ask_role``'s ``(system, user) -> text`` transport, so the named
fallback chains (docs/FALLBACK-CHAINS.md) cover them for free. Bot chat cannot
use that transport — it drives a 44-tool tool-calling loop, not a single prompt
— so it needs its own runtime selection. Chain *policy* (chains, skip rules) is
shared with the committee; chain *transport* is not.

A ``BotRuntime`` is whoever drives that tool loop for one chat turn, and there
are two, because ``claude-agent-sdk`` is an agent rather than an API client: it
runs its own loop and keeps its own transcript, so it cannot serve a single
model call inside someone else's loop.

  ``ClaudeRuntime``  — ``CIOAgent``; the Claude CLI drives the loop
  ``OpenAIRuntime``  — the Agents SDK loop runs in *this* process, with
                       ``FallbackModel`` walking the chain's openai links per
                       model call

``select_runtime`` walks the operator's chain and returns a runtime for the
first link that survives the skips. That choice is then **pinned** for the
session by ``cio/bot.py``'s per-chat cache (locked decision D4): the two
transcripts live in different places, so switching mid-thread would need a
history sync and a mutating-tool ledger that do not exist. ``reselect_needed``
is the one exception, and it unpins only when the pinned service has stopped
being able to answer at all — see its docstring.

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
    runtime (Step 12+'s OpenAIRuntime) must satisfy this same four-member
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


def _link_usable(link: dict) -> bool:
    """The same three skip rules ``select_runtime`` applies per link, factored
    out so the staleness check below cannot drift from the selection walk."""
    service = link.get("service")
    if service == "nim":
        return False
    if engine.is_latched(service):
        return False
    if _usage.over_budget(service, link.get("daily_limit")):
        return False
    return True


def reselect_needed(runtime: BotRuntime) -> bool:
    """True when *runtime*'s pinned service can no longer answer but another
    service in the chain still can.

    Decision D4 pins one runtime per chat for the session, because the two
    transcripts live in different places. That pin is applied at *selection*
    time, so a service that goes over budget or gets limit-latched **after**
    the pin strands the chat: ``FallbackModel`` only ever holds the chain's
    openai links (cio/fallback_model.py:77-79), so an exhausted openai budget
    raises "every openai link was skipped" on every turn while the chain's
    claude link sits unreachable behind the cache.

    This is the narrow escape hatch for exactly that state — not routine
    re-routing. It says yes only when *both* hold:
      * no link of the runtime's own service is usable any more, and
      * some link of a different service is.
    A chat that can still answer on its pinned service therefore never moves,
    and neither does one where moving would not help.

    ``CIO_BOT_ENGINE`` forces a runtime for debugging; an override is honoured
    over budget, so it disables this entirely. Never raises: a lookup that
    throws means "stay put", the same bias the rest of this module takes.
    """
    try:
        if os.getenv("CIO_BOT_ENGINE"):
            return False
        service = getattr(runtime, "_service", None)
        if service not in ("openai", "claude"):
            return False   # unknown transport: not ours to second-guess
        links = [l for l in (models.bot_chat_chain() or []) if isinstance(l, dict)]
        if any(l.get("service") == service and _link_usable(l) for l in links):
            return False   # the pinned service still has a live link
        return any(l.get("service") not in (None, service) and _link_usable(l)
                   for l in links)
    except Exception:
        log.exception("reselect_needed: staleness check failed; keeping the current runtime")
        return False


def select_runtime(chat_id: int, *, resume: str | None = None,
                    on_session_id=None) -> BotRuntime:
    """Resolve the operator's ``bot_chat`` chain and return a runtime for *chat_id*.

    Chain source is ``models.bot_chat_chain()`` — the operator's ``/configure``
    selection when they've made one, else the yaml ``agents.bot_chat.chain``
    (locked decision D5). ``models.bot_chat_link()`` resolves the identical
    chain, so the two can no longer disagree on which chain is in effect.
    Walked in order. Per link:
      1. ``service == "nim"`` → skip. Bot chat is claude+openai only — 44 tools
         is too wide a surface for a weak tool-caller.
      2. the service is limit-latched → skip.
      3. the service is over its configured daily budget → skip.
      4. ``service == "openai"`` → skip (logged). No OpenAIRuntime exists yet
         (Step 12+); silently answering on Claude while claiming to honour an
         openai link would be the same attribution dishonesty R4 exists to
         prevent, so the walk continues to the next link instead.
    The first ``claude`` (or otherwise unrecognized) link surviving all four
    decides the runtime. *resume* and *on_session_id* are forwarded to it
    unchanged — same constructor arguments ``cio.bot._agent()`` has always
    passed.

    ``CIO_BOT_ENGINE=claude`` forces ClaudeRuntime and skips chain resolution
    entirely. Any other value is logged and ignored (Step 12+ adds ``openai``).

    Never raises. A chat that cannot answer is worse than one that answers over
    budget, so budget/latch are advisory here: a missing chain, an unknown
    chain name, an empty link list, a malformed link, or a usage/latch lookup
    that throws all degrade to ClaudeRuntime, and if every link is skipped this
    still returns a (over-budget) ClaudeRuntime rather than nothing.
    """
    def _claude() -> BotRuntime:
        return ClaudeRuntime(chat_id=chat_id, resume=resume, on_session_id=on_session_id)

    def _openai(links: list) -> BotRuntime | None:
        """Build an OpenAIRuntime over *links*, or None if it cannot be built.

        Imported lazily: `cio.agent_openai` pulls in the whole Agents SDK plus
        the 44-tool bridge, which every importer of this module would otherwise
        pay for even on a Claude-only chain. Returns None rather than raising —
        FallbackModel's constructor rejects an empty link list by design, and a
        routing problem must never take the chat down (Standing Rule R2).
        """
        if not links:
            return None
        try:
            from .agent_openai import OpenAIRuntime
            return OpenAIRuntime(links, chat_id=chat_id, on_session_id=on_session_id)
        except Exception:
            log.exception("select_runtime: could not build OpenAIRuntime; falling through")
            return None

    override = os.getenv("CIO_BOT_ENGINE")
    if override:
        if override == "claude":
            return _claude()
        if override == "openai":
            # Force the OpenAI path for debugging, using whatever openai links
            # the chain has. No openai link -> say so and use Claude, rather
            # than silently honouring an override that cannot be satisfied.
            try:
                forced = [l for l in (models.bot_chat_chain() or [])
                          if isinstance(l, dict) and l.get("service") == "openai"]
            except Exception:
                log.exception("CIO_BOT_ENGINE=openai: chain lookup failed")
                forced = []
            runtime = _openai(forced)
            if runtime is not None:
                return runtime
            log.warning("CIO_BOT_ENGINE=openai but the chain has no usable openai "
                        "link; using Claude")
            return _claude()
        log.warning("CIO_BOT_ENGINE=%r not recognized (claude|openai); ignoring", override)

    try:
        chain = models.bot_chat_chain() or []
    except Exception:
        log.exception("select_runtime: bot_chat_chain() failed; using Claude")
        return _claude()

    for index, link in enumerate(chain):
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
                # Hand FallbackModel every openai link from HERE onward, not
                # just this one: it walks them per model call, so a rate limit
                # on this link falls through to the next without replaying a
                # tool. Links before this one were skipped for a reason.
                openai_links = [
                    l for l in chain[index:]
                    if isinstance(l, dict) and l.get("service") == "openai"
                ]
                runtime = _openai(openai_links)
                if runtime is not None:
                    return runtime
                continue    # construction failed; try the next link

            # service == "claude", or any value we don't recognize: Claude is
            # the safe default for the first link that survives the skips.
            return _claude()
        except Exception:
            log.exception("select_runtime: error evaluating link %r; skipping", link)
            continue

    log.warning("select_runtime: every bot_chat link was skipped; using Claude "
               "anyway (a chat that can't answer is worse than one over budget)")
    return _claude()
