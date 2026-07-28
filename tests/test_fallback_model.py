"""
test_fallback_model.py — Offline tests for Step 14: FallbackModel, the
chain-walking agents.models.interface.Model for the OpenAI side of bot chat.

No real LLM calls, no real network calls, no real API key. Every delegate is
a fake injected via FallbackModel(..., model_factory=...) -- see the brief's
Flags: "if you find yourself needing a real API key to test, the seam is
wrong." `usage.DB_PATH` is monkeypatched to a tmp_path so tests never touch
the real committee.db; the committee's limit latch (`engine._LIMIT_LATCH`) is
cleared before and after every test since it is module-global state.
"""
from __future__ import annotations

import asyncio

import httpx
import openai
import pytest

from agents import ModelResponse, ModelSettings, ModelTracing, Usage

from cio.committee import engine, usage
from cio.fallback_model import FallbackModel, _classify_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


# .invalid is reserved (RFC 2606) for exactly this: guaranteed never a real,
# reachable host. Used only to build syntactically valid httpx objects for
# constructing real openai.* exceptions -- never dialled.
_REQUEST = httpx.Request("POST", "https://example.invalid/v1/chat/completions")


def _api_status_error(cls, status_code: int, message: str = "boom"):
    """Build a real openai.APIStatusError (sub)class instance for tests --
    they all require a genuine httpx.Response, so build a minimal one."""
    resp = httpx.Response(status_code, request=_REQUEST, json={"error": {"message": message}})
    return cls(message, response=resp, body=None)


def _canned_response(input_tokens=10, output_tokens=5, total_tokens=15) -> ModelResponse:
    return ModelResponse(
        output=[],
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens),
        response_id="resp-test",
    )


def _call_kwargs() -> dict:
    """Boilerplate args for Model.get_response. FallbackModel only forwards
    these untouched, and the fakes below never inspect them."""
    return dict(
        system_instructions="sys",
        input="hello",
        model_settings=ModelSettings(),
        tools=[],
        output_schema=None,
        handoffs=[],
        tracing=ModelTracing.DISABLED,
        previous_response_id=None,
        conversation_id=None,
        prompt=None,
    )


class FakeDelegate:
    """Stand-in for a delegate Model: records how many times it was called,
    then either raises a canned exception or returns a canned ModelResponse."""

    def __init__(self, *, response: ModelResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = 0

    async def get_response(self, *args, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response

    def stream_response(self, *args, **kwargs):
        raise NotImplementedError


def _factory(by_model: dict):
    """model_factory that looks up the fake delegate to use by link['model']."""
    def factory(link):
        return by_model[link["model"]]
    return factory


# ---------------------------------------------------------------------------
# Isolation: every test gets its own usage DB and clean latch state, since
# both are process-global (module-level dict / sqlite file on disk).
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_usage_db(monkeypatch, tmp_path):
    monkeypatch.setattr("cio.committee.usage.DB_PATH", tmp_path / "fallback_model_test_usage.db")


@pytest.fixture(autouse=True)
def _clean_latch():
    engine._LIMIT_LATCH.clear()
    yield
    engine._LIMIT_LATCH.clear()


# ---------------------------------------------------------------------------
# A. Construction (Decision 3)
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_no_openai_links_raises_value_error(self):
        with pytest.raises(ValueError):
            FallbackModel([{"service": "claude", "model": "claude-opus-4-8"}])

    def test_empty_links_raises_value_error(self):
        with pytest.raises(ValueError):
            FallbackModel([])

    def test_filters_out_non_openai_links(self):
        head = FakeDelegate(response=_canned_response())
        fm = FallbackModel(
            [
                {"service": "claude", "model": "claude-opus-4-8"},
                {"service": "openai", "model": "gpt-a"},
                {"service": "nim", "model": "kimi"},
            ],
            model_factory=_factory({"gpt-a": head}),
        )
        assert len(fm._links) == 1
        assert fm._links[0]["service"] == "openai"


# ---------------------------------------------------------------------------
# B. The walk (Decision 4)
# ---------------------------------------------------------------------------

class TestWalk:
    def test_healthy_head_answers_and_nothing_else_called(self):
        head = FakeDelegate(response=_canned_response())
        second = FakeDelegate(response=_canned_response())
        fm = FallbackModel(
            [{"service": "openai", "model": "gpt-a"}, {"service": "openai", "model": "gpt-b"}],
            model_factory=_factory({"gpt-a": head, "gpt-b": second}),
        )

        result = _run(fm.get_response(**_call_kwargs()))

        assert result is head.response
        assert head.calls == 1
        assert second.calls == 0

    def test_rate_limit_on_head_latches_and_next_link_answers(self, monkeypatch):
        """RateLimitError on link 0 must (a) signal engine.latch("openai")
        and (b) still let link 1 answer.

        engine.latch/is_latched share a single key ("openai") across every
        link this class ever holds (every link here has service == "openai"
        by construction). Before Step 14a that meant a *real* latch written
        by link 0 would also block link 1 within this same walk -- the
        defect TestLatchSnapshot below exists to fix and prove fixed. This
        test predates that fix and still earns its keep afterwards: it
        isolates FallbackModel's own contribution from engine.py's latch
        mechanics -- does it call engine.latch with the right service on a
        RateLimitError, and does it keep walking afterwards -- via a
        recording spy rather than the real mutator. TestLatchSnapshot is
        the real-latch counterpart a spy can never substitute for.
        """
        latch_calls = []
        monkeypatch.setattr("cio.committee.engine.latch", latch_calls.append)

        head = FakeDelegate(error=_api_status_error(openai.RateLimitError, 429))
        second = FakeDelegate(response=_canned_response())
        fm = FallbackModel(
            [{"service": "openai", "model": "gpt-a"}, {"service": "openai", "model": "gpt-b"}],
            model_factory=_factory({"gpt-a": head, "gpt-b": second}),
        )

        result = _run(fm.get_response(**_call_kwargs()))

        assert result is second.response
        assert head.calls == 1
        assert second.calls == 1
        assert latch_calls == ["openai"]

    def test_bad_request_reraises_without_trying_next_link(self):
        err = _api_status_error(openai.BadRequestError, 400)
        head = FakeDelegate(error=err)
        second = FakeDelegate(response=_canned_response())
        fm = FallbackModel(
            [{"service": "openai", "model": "gpt-a"}, {"service": "openai", "model": "gpt-b"}],
            model_factory=_factory({"gpt-a": head, "gpt-b": second}),
        )

        with pytest.raises(openai.BadRequestError):
            _run(fm.get_response(**_call_kwargs()))

        assert head.calls == 1
        assert second.calls == 0
        assert engine.is_latched("openai") is False

    def test_over_budget_link_skipped_without_being_called(self):
        usage.record("openai", 100)  # today's usage already at link 0's cap
        head = FakeDelegate(response=_canned_response())
        second = FakeDelegate(response=_canned_response())
        fm = FallbackModel(
            [
                {"service": "openai", "model": "gpt-a", "daily_limit": 100},  # at limit -> skip
                {"service": "openai", "model": "gpt-b"},                      # no cap -> tried
            ],
            model_factory=_factory({"gpt-a": head, "gpt-b": second}),
        )

        result = _run(fm.get_response(**_call_kwargs()))

        assert result is second.response
        assert head.calls == 0
        assert second.calls == 1

    def test_latched_service_skipped_without_being_called(self):
        engine.latch("openai")
        head = FakeDelegate(response=_canned_response())
        fm = FallbackModel(
            [{"service": "openai", "model": "gpt-a"}],
            model_factory=_factory({"gpt-a": head}),
        )

        with pytest.raises(RuntimeError):
            _run(fm.get_response(**_call_kwargs()))

        assert head.calls == 0

    def test_all_links_exhausted_raises_last_exception_not_empty_response(self):
        """Every link fails with a non-latching, non-reraising error
        (APIConnectionError), so the walk tries every one of them and then
        raises the LAST failure -- never a synthetic/empty ModelResponse."""
        err1 = openai.APIConnectionError(request=_REQUEST)
        err2 = openai.APIConnectionError(message="second failure", request=_REQUEST)
        head = FakeDelegate(error=err1)
        second = FakeDelegate(error=err2)
        fm = FallbackModel(
            [{"service": "openai", "model": "gpt-a"}, {"service": "openai", "model": "gpt-b"}],
            model_factory=_factory({"gpt-a": head, "gpt-b": second}),
        )

        with pytest.raises(openai.APIConnectionError) as excinfo:
            _run(fm.get_response(**_call_kwargs()))

        assert excinfo.value is err2
        assert head.calls == 1
        assert second.calls == 1
        assert engine.is_latched("openai") is False  # connection errors never latch

    def test_delegate_is_built_once_and_reused_across_calls(self):
        build_count = {"n": 0}

        def factory(link):
            build_count["n"] += 1
            return FakeDelegate(response=_canned_response())

        fm = FallbackModel([{"service": "openai", "model": "gpt-a"}], model_factory=factory)

        _run(fm.get_response(**_call_kwargs()))
        _run(fm.get_response(**_call_kwargs()))

        assert build_count["n"] == 1

    def test_stream_response_raises_not_implemented(self):
        head = FakeDelegate(response=_canned_response())
        fm = FallbackModel([{"service": "openai", "model": "gpt-a"}], model_factory=_factory({"gpt-a": head}))

        with pytest.raises(NotImplementedError):
            fm.stream_response(**_call_kwargs())


# ---------------------------------------------------------------------------
# B2. Latch snapshot (Step 14a, Decision 1) -- the real engine.latch/
#     is_latched, not the monkeypatched spy used above. That spy proves
#     FallbackModel *signals* a latch correctly, but since it never mutates
#     engine._LIMIT_LATCH it can't prove the bug this step fixes: a real
#     latch written by link 0 must not blind the same walk to link 1 (they
#     share one service key -- "openai" -- despite being different models).
# ---------------------------------------------------------------------------

class TestLatchSnapshot:
    def test_real_latch_mid_walk_still_lets_the_next_link_answer(self):
        """Link 0 raises a real RateLimitError, which really latches
        "openai" via engine.latch (no monkeypatch here). Because the latch
        read is snapshotted once at the start of the walk (Decision 1),
        link 1 -- same service, different model -- must still be tried and
        must answer. Before this step, the live re-check meant link 1 was
        skipped and the chain raised RuntimeError instead."""
        head = FakeDelegate(error=_api_status_error(openai.RateLimitError, 429))
        second = FakeDelegate(response=_canned_response())
        fm = FallbackModel(
            [
                {"service": "openai", "model": "gpt-5.6-terra"},
                {"service": "openai", "model": "gpt-5.4-mini"},
            ],
            model_factory=_factory({"gpt-5.6-terra": head, "gpt-5.4-mini": second}),
        )

        result = _run(fm.get_response(**_call_kwargs()))

        assert result is second.response
        assert head.calls == 1
        assert second.calls == 1

    def test_real_latch_written_mid_walk_still_blocks_the_next_walk(self):
        """Same scenario as above, checking the other half of Decision 1:
        this walk was allowed to see past its own latch, but the latch
        engine.latch wrote must still be visible afterwards -- to the next
        turn's FallbackModel.get_response call, and to the committee's own
        dispatch, both of which key off the same engine._LIMIT_LATCH."""
        head = FakeDelegate(error=_api_status_error(openai.RateLimitError, 429))
        second = FakeDelegate(response=_canned_response())
        fm = FallbackModel(
            [
                {"service": "openai", "model": "gpt-5.6-terra"},
                {"service": "openai", "model": "gpt-5.4-mini"},
            ],
            model_factory=_factory({"gpt-5.6-terra": head, "gpt-5.4-mini": second}),
        )

        _run(fm.get_response(**_call_kwargs()))

        assert engine.is_latched("openai") is True


# ---------------------------------------------------------------------------
# C. Usage recording (Decision 5)
# ---------------------------------------------------------------------------

class TestUsageRecording:
    def test_usage_recorded_once_for_the_service_that_answered(self):
        head = FakeDelegate(response=_canned_response(input_tokens=100, output_tokens=23, total_tokens=123))
        fm = FallbackModel([{"service": "openai", "model": "gpt-a"}], model_factory=_factory({"gpt-a": head}))

        _run(fm.get_response(**_call_kwargs()))

        assert usage.used_today("openai") == 123
        assert usage.used_today("claude") == 0

    def test_usage_falls_back_to_input_plus_output_when_total_is_zero(self):
        head = FakeDelegate(response=_canned_response(input_tokens=40, output_tokens=10, total_tokens=0))
        fm = FallbackModel([{"service": "openai", "model": "gpt-a"}], model_factory=_factory({"gpt-a": head}))

        _run(fm.get_response(**_call_kwargs()))

        assert usage.used_today("openai") == 50

    def test_usage_not_recorded_for_a_failed_link(self):
        head = FakeDelegate(error=openai.APIConnectionError(request=_REQUEST))
        second = FakeDelegate(response=_canned_response(total_tokens=42))
        fm = FallbackModel(
            [{"service": "openai", "model": "gpt-a"}, {"service": "openai", "model": "gpt-b"}],
            model_factory=_factory({"gpt-a": head, "gpt-b": second}),
        )

        _run(fm.get_response(**_call_kwargs()))

        assert usage.used_today("openai") == 42  # only the link that answered


# ---------------------------------------------------------------------------
# D. Missing API key (Standing Rule R2) -- exercises the DEFAULT
#    model_factory (_build_delegate / _client_for), not an injected fake.
# ---------------------------------------------------------------------------

class TestMissingApiKey:
    def test_missing_api_key_skips_link_without_crashing(self, monkeypatch):
        monkeypatch.setattr(
            "cio.committee.models.openai_settings",
            lambda: {
                "base_url": "https://example.invalid/v1",
                "api_key_env": "CIO_TEST_FALLBACK_MODEL_MISSING_KEY",
                "max_output_tokens": 2048,
                "token_param": "max_completion_tokens",
            },
        )
        monkeypatch.delenv("CIO_TEST_FALLBACK_MODEL_MISSING_KEY", raising=False)

        # No model_factory override here on purpose -- this is the one test
        # exercising the real default factory's missing-key branch.
        fm = FallbackModel([{"service": "openai", "model": "gpt-a"}])

        with pytest.raises(RuntimeError):
            _run(fm.get_response(**_call_kwargs()))


# ---------------------------------------------------------------------------
# E. _classify_error — Decision 6's table, row by row
# ---------------------------------------------------------------------------

class TestClassifyError:
    def test_rate_limit_latches(self):
        assert _classify_error(_api_status_error(openai.RateLimitError, 429)) == "latch"

    def test_server_error_continues_without_latching(self):
        assert _classify_error(_api_status_error(openai.InternalServerError, 500)) == "continue"
        assert _classify_error(_api_status_error(openai.APIStatusError, 503)) == "continue"

    def test_timeout_continues(self):
        assert _classify_error(openai.APITimeoutError(request=_REQUEST)) == "continue"

    def test_connection_error_continues(self):
        assert _classify_error(openai.APIConnectionError(request=_REQUEST)) == "continue"

    def test_authentication_error_latches(self):
        assert _classify_error(_api_status_error(openai.AuthenticationError, 401)) == "latch"

    def test_permission_denied_latches(self):
        assert _classify_error(_api_status_error(openai.PermissionDeniedError, 403)) == "latch"

    def test_bad_request_reraises(self):
        assert _classify_error(_api_status_error(openai.BadRequestError, 400)) == "reraise"

    def test_anything_else_continues(self):
        assert _classify_error(ValueError("something unrelated")) == "continue"
        assert _classify_error(_api_status_error(openai.NotFoundError, 404)) == "continue"
