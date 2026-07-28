"""OpenAI Agents SDK transport for the shared BaseRuntime turn pipeline
(Architect brief Step 15; plan of record docs/BOT-CHAT-OPENAI-MIGRATION.md).

`BaseRuntime` (cio.agent) owns the whole turn pipeline -- day roll, clock
injection, nudge, sources footer, harness hook, verifier, checkpoint
triggers, monthly rollup. `OpenAIRuntime` supplies only the three transport
hooks (`_ensure`, `_run_query`, `_reset_session`) plus `close`; it must never
grow an `ask()` of its own (Step 13 split `BaseRuntime` out precisely to
keep a second transport from drifting off the first).

Not wired in yet: `cio.bot_runtime.select_runtime` still returns
`ClaudeRuntime` for every chat. Step 16 wires this in.
"""
from __future__ import annotations

import logging

from agents import Agent, Runner, SQLiteSession

from . import context, convlog, db
from .agent import SYSTEM_PROMPT, BaseRuntime, _PENDING, _PENDING_DOCS, _env
from .fallback_model import FallbackModel
from .tool_bridge import OPENAI_TOOLS

log = logging.getLogger(__name__)

# Runner.run's own default (10) counts model calls, not chat turns -- one
# turn here can research a symbol across several tool calls before
# answering. The Claude CLI path has no comparable cap, so inheriting 10
# would make this path give up where the Claude path succeeds. Same
# CIO_*/CFO_* fallback convention as the rest of cio/agent.py (_env).
MAX_TURNS = int(_env("MAX_TURNS", "24"))


class OpenAIRuntime(BaseRuntime):
    """OpenAI Agents SDK transport: a `SQLiteSession` + `Agent(model=
    FallbackModel(links))` pair, built lazily by `_ensure()`.

    Implements only the three `BaseRuntime` hooks -- `_ensure`, `_run_query`,
    `_reset_session` -- plus `close`. Everything else (day roll, sources
    footer, checkpoint triggers, usage/convlog bookkeeping conventions, ...)
    is inherited unchanged from `BaseRuntime`.
    """

    def __init__(self, links: list[dict], model: str | None = None,
                 chat_id: int | None = None, on_session_id=None):
        # "openai" is what Standing Rule R4 (usage.record / convlog.log_call
        # attribution) keys on -- it is the entire reason the hardcoded
        # "claude" literal was removed from BaseRuntime in Step 10. No SDK
        # session id exists yet at construction time (session_id=None); the
        # real one is reported through `_note_session` once `_ensure` builds it.
        super().__init__(model, chat_id, on_session_id, "openai", None)
        self._links = links
        self._session: SQLiteSession | None = None
        self._agent: Agent | None = None
        self._system_prompt: str = ""   # kept for the detailed-history log (convlog)

    def _build_agent(self) -> Agent:
        """(Re)compose the system prompt (with this chat's injected memory
        block) and build a fresh `Agent` bound to it. Called from `_ensure`
        (first build) and `_reset_session` (post-checkpoint rebuild) so both
        paths pick up the latest digest the same way."""
        prompt = context.compose_system_prompt(SYSTEM_PROMPT, self._chat_id)
        self._system_prompt = prompt
        return Agent(
            name="cio",
            instructions=prompt,
            tools=OPENAI_TOOLS,
            model=FallbackModel(self._links),
        )

    async def _ensure(self) -> None:
        """Build the session and agent lazily, once. Idempotent: a session
        or agent already in place is left alone."""
        if self._session is None:
            session_id = f"chat:{self._chat_id}"
            self._session = SQLiteSession(session_id=session_id, db_path=str(db.DB_PATH))
            self._note_session(session_id)
        if self._agent is None:
            self._agent = self._build_agent()

    async def _run_query(self, prompt: str) -> tuple[str, list[str]]:
        """One turn against the current Agent/session; returns (text, images).

        Called only via `BaseRuntime._guarded_turn`, which has already
        acquired `_LOCK` and pointed the memory tools at this runtime's scope
        before this runs -- so, unlike `_guarded_turn`, this method must NOT
        touch `_LOCK` or `_ACTIVE_SCOPE`/`_SEARCHED_THIS_TURN` itself
        (`_LOCK` is an `asyncio.Lock`, not reentrant; re-acquiring it here
        would deadlock the turn).
        """
        try:
            result = await Runner.run(self._agent, prompt, session=self._session,
                                      max_turns=MAX_TURNS)
        except Exception as e:
            # R2: a transport failure -- including a FallbackModel chain
            # exhaustion -- must not escape as a bare exception into the
            # Telegram handler. FallbackModel already raises a meaningful
            # message on exhaustion; surface it rather than a generic one.
            log.warning("OpenAIRuntime turn failed: %s", e, exc_info=True)
            return f"Sorry, I hit an error and couldn't finish that: {e}", []

        text = str(result.final_output) if result.final_output is not None else ""
        usage = result.context_wrapper.usage
        tokens = 0
        if usage is not None:
            tokens = usage.total_tokens or (usage.input_tokens + usage.output_tokens)
        if tokens <= 0:
            tokens = context.count_tokens(prompt) + context.count_tokens(text)
        self._record_usage(tokens, prompt, text, self._service)
        # Detailed conversation history (opt-in, off by default): full text log.
        convlog.log_call(self._service, self._model or "openai-agents-sdk",
                         self._system_prompt, prompt, text, tokens,
                         scope=self._scope, kind="chat")
        images = list(_PENDING)
        _PENDING.clear()
        # Documents (committee PDF) are stashed on the instance, not returned,
        # so the (text, images) signature stays stable for tests that stub
        # _run_query. ask() drains it before any checkpoint turn overwrites it.
        self._last_docs = list(_PENDING_DOCS)
        _PENDING_DOCS.clear()
        return text, images

    async def _reset_session(self) -> None:
        """The fork half of `_checkpoint`: the digest is already persisted by
        `_checkpoint` before this runs, so the session's transcript can be
        cleared in place -- its id (`chat:<chat_id>`) is deterministic and
        never needs to be recreated, unlike the Claude SDK's assigned id.
        The agent is rebuilt (not just left as-is) so its instructions
        re-compose the system prompt with the digest just persisted -- same
        reason `CIOAgent._reset_session` calls `_make_client(None)`."""
        await self._session.clear_session()
        self._agent = self._build_agent()

    async def close(self):
        await super().close()   # pops the source registries
        if self._session is not None:
            try:
                self._session.close()   # sync -- SQLiteSession.close() is not a coroutine
            except Exception:
                log.warning("OpenAIRuntime: session close failed", exc_info=True)
