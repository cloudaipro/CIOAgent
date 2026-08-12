"""
fallback_model.py — chain-walking Model for OpenAI-compatible bot runtimes.

FallbackModel wraps an ordered list of same-service fallback-chain links (the shape
``cio.committee.models.bot_chat_chain()`` returns) behind a single
``agents.models.interface.Model``. The Agents SDK calls ``get_response`` once
per model call within a turn, running tool calls *between* calls — so walking
the chain at this boundary (once per ``get_response``, not once per turn)
never replays a tool that already fired: a fallback only changes which model
answers the *next* call. See docs/BOT-CHAT-OPENAI-MIGRATION.md and
handoff/ARCHITECT-BRIEF.md, Step 14.

``cio.agent_openai`` constructs it for either hosted OpenAI or local Muse.

Every link this class is constructed with has the selected service (see
FallbackModel.__init__) — a link's ``service`` is the same key
``cio.committee.engine``'s limit latch and ``cio.committee.usage``'s daily
budget are keyed on, both reused unchanged here rather than reinvented.
Because the latch is keyed by service while links differ by model, each
``get_response`` walk snapshots that read once at entry rather than
re-checking it per link (Step 14a) — a latch the walk itself writes still
protects the *next* walk, but does not blind this walk to the links still
ahead of it. The daily budget check is deliberately not snapshotted; see
the comment in ``get_response`` for why that asymmetry is correct rather
than an oversight.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

import openai
from agents import Model, ModelResponse, OpenAIResponsesModel

from cio.committee import engine, models, usage

log = logging.getLogger(__name__)

# Given one fallback-chain link ({"service": "openai", "model": ..., ...}),
# return a Model to delegate to, or None if the link cannot be built right
# now (e.g. no API key configured — Decision 7 / Standing Rule R2).
# Overridable via FallbackModel(..., model_factory=...) so tests never need a
# real API key or network access (brief Flags: "if you find yourself needing
# a real API key to test, the seam is wrong").
ModelFactory = Callable[[dict], Model | None]

# _classify_error() outcomes (Decision 6).
_LATCH = "latch"        # latch the service, then try the next link
_CONTINUE = "continue"  # try the next link; the service itself is not at fault
_RERAISE = "reraise"    # do not try further links; the request itself is bad


class FallbackModel(Model):
    """A ``Model`` that walks one provider's ordered fallback-chain links.

    Construction filters *links* down to the selected service (OpenAI by
    default); a chain with no matching link is a wiring error, not a runtime condition to
    degrade from, so it raises ``ValueError`` immediately.

    Every ``get_response`` call re-walks the (already-filtered) chain from
    the head. For each link, in order: skip it if its service is latched or
    over its daily token budget; otherwise build-or-reuse the delegate model
    and call it. The first successful call's ``ModelResponse`` is returned
    unchanged, after recording usage for the service that answered. A failed
    call is classified (Decision 6): a bad request re-raises immediately, a
    rate limit or auth failure latches the service before trying the next
    link, anything else just tries the next link. If every link is
    exhausted, the last exception raised is re-raised; if no link was even
    attempted (all skipped), a ``RuntimeError`` is raised naming why. This
    method never returns a synthetic or empty ``ModelResponse``.
    """

    def __init__(self, links: list[dict], *, model_factory: ModelFactory | None = None,
                 service: str = "openai") -> None:
        self._service = service
        self._links: list[dict] = [
            dict(link) for link in links
            if isinstance(link, dict) and link.get("service") == service
        ]
        if not self._links:
            raise ValueError(f"FallbackModel: no {service} links in the given chain")
        self._model_factory: ModelFactory = model_factory or self._build_delegate
        self._delegates: dict[int, Model] = {}      # link index -> built Model (build/reuse)
        self._clients: dict[str, openai.AsyncOpenAI] = {}  # base_url -> client (Decision 7)
        # Tokens this object has written to the usage ledger since the last
        # `reset_recorded()`. The runtime driving the tool loop reads it to
        # tell "usage already recorded, per model call" apart from "no usage
        # recorded at all" -- see `recorded_tokens`.
        self._recorded_tokens = 0

    def reset_recorded(self) -> None:
        """Zero the recorded-token counter. Called by the runtime at the start
        of a turn; turns are serialized by `BaseRuntime._LOCK`, so one counter
        per model object is enough."""
        self._recorded_tokens = 0

    @property
    def recorded_tokens(self) -> int:
        """Tokens written to the usage ledger since `reset_recorded()`.

        This model records usage per *model call* (`_record_usage` below), and
        one agent turn is many calls. A turn-level recorder on top of that
        would double-count every openai chat turn -- which it did until this
        counter existed, burning the openai daily budget at 2x the real rate.
        Zero means nothing was recorded (no call succeeded, or the API
        reported no usage) and the caller's own estimate should stand in."""
        return self._recorded_tokens

    # -- delegate construction (default model_factory) -----------------------

    def _client_for(self, base_url: str, api_key_env: str,
                    *, key_optional: bool = False) -> openai.AsyncOpenAI | None:
        """The cached AsyncOpenAI client for *base_url*, built lazily.

        One client per distinct base_url, not one per call (Decision 7).
        Returns None — never raises — when *api_key_env* is unset: a missing
        key degrades to a skipped link and a log line (Standing Rule R2).
        """
        client = self._clients.get(base_url)
        if client is not None:
            return client
        key = os.getenv(api_key_env)
        if not key:
            if key_optional:
                key = "local"
            else:
                log.warning("FallbackModel: %s is not set; skipping the %s link(s) at %s",
                            api_key_env, self._service, base_url)
                return None
        client = openai.AsyncOpenAI(api_key=key, base_url=base_url)
        self._clients[base_url] = client
        return client

    def _build_delegate(self, link: dict) -> Model | None:
        """Default model_factory: an OpenAIResponsesModel bound to the cached
        client for this link's configured base_url. None if that client cannot
        be built right now (missing key).

        Responses, not Chat Completions — established by live smoke (KG-19),
        not by preference. gpt-5.6-terra rejects the combination of function
        tools and its own default reasoning effort on /v1/chat/completions:

            400 Function tools with reasoning_effort are not supported for
            gpt-5.6-terra in /v1/chat/completions. To use function tools, use
            /v1/responses or set reasoning_effort to 'none'.

        Since bot chat presents 44 tools, the model's tool-selection judgement
        is the thing we least want to switch off, so the other repair the API
        offers -- reasoning_effort='none' -- is the wrong trade here. Responses
        is also the native surface for what this path already produces: the
        `input_image` content parts `agent_openai.build_turn_input` emits and
        the item shape `SQLiteSession` persists are both Responses-shaped, and
        were being down-converted per call.
        """
        if self._service == "muse":
            settings = models.muse_settings()
            client = self._client_for(settings["base_url"], settings["api_key_env"],
                                      key_optional=True)
            if client is None:
                return None
            # Local llama-server/vLLM deployments implement Chat Completions;
            # unlike OpenAI's hosted path they are not assumed to implement
            # the Responses API.
            from agents import OpenAIChatCompletionsModel
            return OpenAIChatCompletionsModel(
                model=link["model"], openai_client=client)

        settings = models.openai_settings()
        client = self._client_for(settings["base_url"], settings["api_key_env"])
        if client is None:
            return None
        return OpenAIResponsesModel(model=link["model"], openai_client=client)

    def _delegate_for(self, index: int, link: dict) -> Model | None:
        """Build-or-reuse the delegate Model for ``self._links[index]``
        (Decision 4: "build/reuse"). Cached for the life of this instance so
        a multi-call turn does not rebuild a working delegate on every call."""
        cached = self._delegates.get(index)
        if cached is not None:
            return cached
        delegate = self._model_factory(link)
        if delegate is not None:
            self._delegates[index] = delegate
        return delegate

    # -- agents.models.interface.Model ----------------------------------------

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ) -> ModelResponse:
        last_exc: Exception | None = None

        # Snapshot each service's latch state once, here, before the walk
        # starts -- not via a fresh engine.is_latched() call inside the loop
        # below (Decision 1, Step 14a). The latch is keyed by service alone,
        # but every link this class holds shares one service string
        # ("openai") while differing by *model* (see __init__), and OpenAI
        # enforces rate limits per model: gpt-5.6-terra being limited says
        # nothing about gpt-5.4-mini. The latch's job is to stop the *next*
        # walk from rediscovering a known-bad service, not to blind this
        # walk to its own remaining links -- so a latch written mid-walk
        # (engine.latch, below) still applies to future walks, just not to
        # the links still ahead of it here. Contrast usage.over_budget()
        # below, which stays deliberately unsnapshotted: a daily token
        # budget is account-wide rather than per-model, so once one openai
        # link exhausts it, every later openai link in the same walk is
        # correctly skipped too.
        latched_at_entry = {
            link["service"] for link in self._links if engine.is_latched(link["service"])
        }

        for index, link in enumerate(self._links):
            service = link["service"]
            model_name = link.get("model")

            if service in latched_at_entry:
                log.info("FallbackModel: %s is latched; skipping %s", service, model_name)
                continue
            if usage.over_budget(service, link.get("daily_limit")):
                log.info("FallbackModel: %s is over its daily budget; skipping %s",
                          service, model_name)
                continue

            delegate = self._delegate_for(index, link)
            if delegate is None:
                continue  # missing API key; already logged by _client_for

            try:
                response = await delegate.get_response(
                    system_instructions, input, model_settings, tools, output_schema,
                    handoffs, tracing,
                    previous_response_id=previous_response_id,
                    conversation_id=conversation_id,
                    prompt=prompt,
                )
            except Exception as exc:
                last_exc = exc
                action = _classify_error(exc)
                if action == _RERAISE:
                    log.warning(
                        "FallbackModel: %s link %s failed with a non-retryable error; "
                        "not trying further links: %s", service, model_name, exc)
                    raise
                if action == _LATCH:
                    engine.latch(service)
                log.warning("FallbackModel: %s link %s failed (%s); trying the next link: %s",
                            service, model_name, action, exc)
                continue
            else:
                self._recorded_tokens += _record_usage(service, response)
                return response

        if last_exc is not None:
            raise last_exc
        raise RuntimeError(
            "FallbackModel: every openai link was skipped (latched, over budget, or "
            "missing an API key); none could be tried"
        )

    def stream_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ):
        """Bot chat does not stream today (Decision 2) — raise rather than
        half-build a streaming path nothing calls yet."""
        raise NotImplementedError("FallbackModel.stream_response: bot chat does not stream today")


def _record_usage(service: str, response: ModelResponse) -> int:
    """Usage recording (Decision 5): total_tokens when non-zero, else the sum
    of input_tokens + output_tokens. Only ever called for a link that just
    succeeded — never for one that failed. Returns what it recorded, so the
    caller can report whether this turn's usage is already on the ledger."""
    u = response.usage
    total = 0
    if u is not None:
        total = u.total_tokens or (u.input_tokens + u.output_tokens)
    usage.record(service, total)
    return total


def _classify_error(exc: Exception) -> str:
    """Classify a delegate call failure per Decision 6's table. Order matters:
    RateLimitError, AuthenticationError, PermissionDeniedError and
    BadRequestError are all openai.APIStatusError subclasses, so those
    specific checks must run before the generic status_code >= 500 check."""
    if isinstance(exc, openai.BadRequestError):
        return _RERAISE
    if isinstance(exc, openai.RateLimitError):
        return _LATCH
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return _LATCH
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return _CONTINUE
    if isinstance(exc, openai.APIStatusError) and (exc.status_code or 0) >= 500:
        return _CONTINUE
    return _CONTINUE
