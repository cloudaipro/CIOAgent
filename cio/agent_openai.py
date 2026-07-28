"""OpenAI Agents SDK transport for the shared BaseRuntime turn pipeline
(Architect brief Step 15; plan of record docs/BOT-CHAT-OPENAI-MIGRATION.md).

`BaseRuntime` (cio.agent) owns the whole turn pipeline -- day roll, clock
injection, nudge, sources footer, harness hook, verifier, checkpoint
triggers, monthly rollup. `OpenAIRuntime` supplies only the three transport
hooks (`_ensure`, `_run_query`, `_reset_session`) plus `close`; it must never
grow an `ask()` of its own (Step 13 split `BaseRuntime` out precisely to
keep a second transport from drifting off the first).

Vision (Step 17): the Claude path reads an uploaded image with the CLI's
`Read` builtin, which does not exist here. A `read_image` tool cannot replace
it either -- `tool_bridge` returns a *string*, so a tool result can never
carry an image (see plan §7.1). OpenAI takes the image as an input content
part instead, so `_run_query` rewrites a prompt that references an uploaded
file into a multimodal input list.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
import re
from pathlib import Path

from agents import Agent, ModelSettings, Runner, SQLiteSession

from . import context, convlog, db
from .agent import SYSTEM_PROMPT, BaseRuntime, _PENDING, _PENDING_DOCS, _env
from .committee import models
from .fallback_model import FallbackModel
from .tool_bridge import OPENAI_TOOLS

log = logging.getLogger(__name__)

# Runner.run's own default (10) counts model calls, not chat turns -- one
# turn here can research a symbol across several tool calls before
# answering. The Claude CLI path has no comparable cap, so inheriting 10
# would make this path give up where the Claude path succeeds. Same
# CIO_*/CFO_* fallback convention as the rest of cio/agent.py (_env).
MAX_TURNS = int(_env("MAX_TURNS", "24"))

# Paths the photo/document handlers write (cio/bot.py UPLOAD_DIR). Matching on
# the path the prompt itself carries is deliberate: the bot tells the agent
# where it saved the upload, so that sentence is the only reliable signal that
# this turn has an image. Bounded to real image suffixes so a CSV upload -- read
# by ingest_transactions_csv, a tool -- is never inlined as a picture.
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_UPLOAD_PATH_RE = re.compile(r"(/\S+?\.(?:png|jpe?g|gif|webp))", re.I)
_MAX_IMAGE_BYTES = 20 * 1024 * 1024   # refuse to inline something absurd

# What a turn tells the user when the model produced no text at all. Named
# rather than inlined so the test that pins this behaviour cannot drift from it.
NO_OUTPUT_MESSAGE = (
    "I ran out of output budget before I could answer. Raise the OpenAI output "
    "cap (`openai.max_tokens` in config/committee_models.yaml, or the "
    "CIO_OPENAI_MAX_TOKENS env var) and ask again."
)


def _model_settings() -> ModelSettings:
    """The per-turn `ModelSettings` for the OpenAI runtime.

    Read at agent-build time rather than at import, so an operator's config
    change takes effect on the next session roll instead of the next restart.

    **store=False** is a deliberate carry-over, not a new policy. FallbackModel
    used to delegate to Chat Completions, which retains no transcript; KG-19's
    live smoke moved it to Responses, which defaults to store=true and would
    have started retaining every chat turn -- positions, holdings, P&L --
    server-side as a silent side effect of a transport fix. Our SQLiteSession
    is the session of record, so nothing here needs OpenAI-side state.

    **max_tokens** carries `openai.max_tokens` from the yaml (env override
    CIO_OPENAI_MAX_TOKENS), which until KG-20 governed the committee path only
    -- bot chat ran uncapped on the model's own default while the config file
    claimed otherwise. The SDK maps it to Responses' `max_output_tokens`.

    `token_param` from the same settings block is deliberately *not* used here:
    it chooses between Chat Completions' `max_completion_tokens` and
    `max_tokens`, and this path is on Responses, which has neither. Wiring it
    would imply a choice that does not exist.

    Never raises -- a config read that fails degrades to "no cap" plus a log
    line, which is the pre-KG-20 behaviour and cannot take the chat down
    (Standing Rule R2).
    """
    cap = None
    try:
        cap = models.openai_settings().get("max_output_tokens")
    except Exception:
        log.warning("could not read the OpenAI output cap; this turn runs uncapped",
                    exc_info=True)
    # A non-positive cap is a misconfiguration, not an instruction to send
    # max_output_tokens=0 and guarantee an empty turn.
    if not isinstance(cap, int) or cap <= 0:
        cap = None
    return ModelSettings(store=False, max_tokens=cap)


def _image_parts(prompt: str) -> list[dict]:
    """Base64 `input_image` parts for every readable image path in *prompt*.

    Returns an empty list when there is nothing to attach, which is the common
    case — the caller then sends the plain string it already had. Never raises:
    an unreadable or oversized file degrades to "no image" plus a log line, so
    the turn still runs and the model simply says it cannot see the receipt
    (Standing Rule R2).
    """
    parts: list[dict] = []
    for match in _UPLOAD_PATH_RE.findall(prompt or ""):
        path = Path(match)
        # Belt and braces: `_UPLOAD_PATH_RE` already matches image extensions
        # only, so this cannot currently reject anything (verified by mutation
        # — removing it changes no test). It stays as the guard that still
        # holds if the regex is ever widened, which is the likely edit here.
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            continue
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size > _MAX_IMAGE_BYTES:
                log.warning("image %s is %d bytes; too large to inline", path, size)
                continue
            mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
            data = base64.b64encode(path.read_bytes()).decode("ascii")
        except Exception:
            log.warning("could not read image %s; the turn will run without it",
                        path, exc_info=True)
            continue
        parts.append({"type": "input_image", "image_url": f"data:{mime};base64,{data}"})
    return parts


def build_turn_input(prompt: str):
    """The `Runner.run` input for this turn: the plain string, or a multimodal
    input list when the prompt references an uploaded image.

    OpenAI vision takes an image as an input content part, never as a tool
    result — `tool_bridge` joins content-block *text*, so an image cannot ride
    back from a handler (plan §7.1). Whether the model can actually see it
    depends on the link's model being vision-capable; a text-only link silently
    ignores the part, which is why chain config should mark vision links.
    """
    parts = _image_parts(prompt)
    if not parts:
        return prompt
    return [{"role": "user", "content": [{"type": "input_text", "text": prompt}, *parts]}]


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
        # Built once and reused across agent rebuilds. FallbackModel caches an
        # AsyncOpenAI (and so an httpx connection pool) per base_url, so
        # constructing a new one on every session roll would abandon a pool
        # each time. The Agent is rebuilt on a roll to refresh its injected
        # memory; the model behind it has no such need.
        self._model_impl: FallbackModel | None = None
        self._system_prompt: str = ""   # kept for the detailed-history log (convlog)

    def _build_agent(self) -> Agent:
        """(Re)compose the system prompt (with this chat's injected memory
        block) and build a fresh `Agent` bound to it. Called from `_ensure`
        (first build) and `_reset_session` (post-checkpoint rebuild) so both
        paths pick up the latest digest the same way. The FallbackModel is
        built once and carried across rebuilds (see `__init__`)."""
        prompt = context.compose_system_prompt(SYSTEM_PROMPT, self._chat_id)
        self._system_prompt = prompt
        if self._model_impl is None:
            self._model_impl = FallbackModel(self._links)
        return Agent(
            name="cio",
            instructions=prompt,
            tools=OPENAI_TOOLS,
            model=self._model_impl,
            model_settings=_model_settings(),
        )

    async def _ensure(self) -> None:
        """Build the session and agent lazily, once. Idempotent: a session
        or agent already in place is left alone."""
        if self._session is None:
            session_id = f"chat:{self._chat_id}"
            self._session = SQLiteSession(session_id=session_id, db_path=str(db.DB_PATH))
            # Record it locally -- the dashboard and memory.log_turn group turns by
            # `session_id` -- but do NOT publish it through `_note_session`. That
            # callback persists into `chats.session_id`, which is the *Claude*
            # resume token: a `chat:<id>` key landing there is handed straight to
            # the CLI as `--resume chat:<id>`, which it cannot resolve, and it then
            # exits 1 on the first message of every subsequent Claude turn. This
            # session needs no stored token anyway -- its id is deterministic and
            # rebuilt here on every boot, unlike the Claude SDK's assigned id.
            self._session_id = session_id
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
            result = await Runner.run(self._agent, build_turn_input(prompt),
                                      session=self._session, max_turns=MAX_TURNS)
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
        if not text.strip():
            # Responses counts *reasoning* tokens against max_output_tokens, so
            # a capped turn can end with no text at all -- a path that did not
            # exist while this runtime was (wrongly) uncapped. cio/bot.py:265
            # renders empty text as "(no response)", which is indistinguishable
            # from a model that simply chose to say nothing; name the cause
            # instead. Usage and convlog above deliberately recorded the real
            # empty output rather than this stand-in.
            log.warning("OpenAI turn produced no text after %d tokens; the output "
                        "cap is the usual cause", tokens)
            text = NO_OUTPUT_MESSAGE
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
