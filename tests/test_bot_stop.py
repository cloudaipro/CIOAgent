"""
test_bot_stop.py — offline tests for the /stop cancellation feature.

Goal: prove /stop cancels in-flight work AND has no side effects:
  * a stopped turn is NOT persisted (memory.log_turn never called)
  * a stopped turn does NOT send the assistant answer to the user
  * the agent is reset so a half-consumed SDK stream can't corrupt the next turn
  * the in-flight-task registry (_running) and the _stopping set never leak
  * a NORMAL turn is completely unaffected (regression)
  * a genuine (shutdown) cancellation still propagates — only user /stop is swallowed
  * /stop is isolated per chat — it never touches another chat's running task

No network, no LLM, no real Telegram. A FakeAgent whose ask() blocks on an
asyncio.Event lets a test cancel a turn deterministically mid-flight.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

import cio.bot as bot


# ---------------------------------------------------------------------------
# Minimal Telegram fakes
# ---------------------------------------------------------------------------

@dataclass
class FakeChat:
    id: int = 1

    async def send_action(self, action) -> None:
        pass


class FakeMessage:
    def __init__(self, chat: FakeChat, reply_raises: bool = False):
        self.chat = chat
        self.reply_raises = reply_raises
        self.replies: list[str] = []
        self.photos: list[Any] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        if self.reply_raises:
            raise RuntimeError("telegram send failed")
        self.replies.append(text)

    async def reply_photo(self, fh, **kwargs) -> None:
        self.photos.append(fh)


class FakeUpdate:
    def __init__(self, chat_id: int = 1, reply_raises: bool = False):
        self.message = FakeMessage(FakeChat(chat_id), reply_raises=reply_raises)

    @property
    def effective_chat(self) -> FakeChat:
        return self.message.chat


@dataclass
class FakeCtx:
    args: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Controllable fake agent
# ---------------------------------------------------------------------------

class FakeAgent:
    """ask() optionally blocks on `release` so a test can cancel mid-turn."""

    def __init__(self, block: bool = True, raises: Exception | None = None):
        self._session_id = "sess-1"
        self.block = block
        self.raises = raises
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.ask_calls = 0
        self.closed = False

    async def ask(self, prompt: str):
        self.ask_calls += 1
        self.started.set()
        if self.block:
            await self.release.wait()
        if self.raises is not None:
            raise self.raises
        return (f"answer:{prompt}", [])

    async def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate module-global registries and stub memory.log_turn per test."""
    bot._agents.clear()
    bot._running.clear()
    bot._stopping.clear()
    log_calls: list[tuple] = []
    monkeypatch.setattr(bot.memory, "log_turn",
                        lambda *a, **k: log_calls.append((a, k)))
    yield log_calls
    bot._agents.clear()
    bot._running.clear()
    bot._stopping.clear()


async def _wait(cond, timeout=1.0):
    await asyncio.wait_for(cond, timeout)


# ===========================================================================
# /stop with nothing running
# ===========================================================================

def test_stop_with_nothing_running_replies_idle():
    async def scenario():
        upd = FakeUpdate(chat_id=1)
        await bot.cmd_stop(upd, FakeCtx())
        return upd

    upd = asyncio.run(scenario())
    assert any("Nothing" in r for r in upd.message.replies)


def test_stop_skips_already_done_tasks():
    """A finished task lingering in the registry is ignored (->'Nothing')."""
    async def scenario():
        async def noop():
            return
        t = asyncio.create_task(noop())
        await t
        bot._running[1] = {t}
        upd = FakeUpdate(chat_id=1)
        await bot.cmd_stop(upd, FakeCtx())
        return upd

    upd = asyncio.run(scenario())
    assert any("Nothing" in r for r in upd.message.replies)


# ===========================================================================
# /stop cancels an in-flight turn — the core no-side-effect contract
# ===========================================================================

def test_stop_cancels_inflight_turn_no_side_effects(_clean_state):
    log_calls = _clean_state

    async def scenario():
        fake = FakeAgent(block=True)
        bot._agents[1] = fake                       # real _agent() returns this
        upd = FakeUpdate(chat_id=1)
        run_task = asyncio.create_task(bot._run(upd, "hello"))

        await _wait(fake.started.wait())            # turn is mid-flight
        assert bot._running.get(1) and len(bot._running[1]) == 1

        stop_upd = FakeUpdate(chat_id=1)
        await bot.cmd_stop(stop_upd, FakeCtx())     # cancel it
        await _wait(run_task)                        # let it unwind

        return fake, upd, stop_upd, run_task

    fake, upd, stop_upd, run_task = asyncio.run(scenario())

    # turn was cancelled cleanly (handled, not bubbled)
    assert run_task.cancelled() is False
    # NO persistence — the turn never completed
    assert log_calls == []
    # NO assistant answer leaked to the user; only the stop notice
    assert not any("answer:" in r for r in upd.message.replies)
    assert any("Stopped" in r for r in upd.message.replies)
    assert any("Stopping" in r for r in stop_upd.message.replies)
    # agent was reset (closed + dropped) so next turn rebuilds fresh
    assert fake.closed is True
    assert 1 not in bot._agents
    # registries clean — no leak
    assert bot._running.get(1) in (None, set())
    assert bot._stopping == set()


# ===========================================================================
# Regression: a normal turn is unaffected
# ===========================================================================

def test_normal_turn_completes_and_persists(_clean_state):
    log_calls = _clean_state

    async def scenario():
        fake = FakeAgent(block=False)               # completes immediately
        bot._agents[1] = fake
        upd = FakeUpdate(chat_id=1)
        await bot._run(upd, "hello")
        return fake, upd

    fake, upd = asyncio.run(scenario())

    assert fake.ask_calls == 1
    assert fake.closed is False                      # NOT reset on success
    assert 1 in bot._agents                          # agent retained
    assert len(log_calls) == 1                       # turn persisted exactly once
    assert any("answer:hello" in r for r in upd.message.replies)
    assert bot._running.get(1) in (None, set())      # registry cleaned
    assert bot._stopping == set()


# ===========================================================================
# Registry cleanup on a normal error (no /stop)
# ===========================================================================

def test_agent_error_cleans_registry_and_does_not_persist(_clean_state):
    log_calls = _clean_state

    async def scenario():
        fake = FakeAgent(block=False, raises=RuntimeError("boom"))
        bot._agents[1] = fake
        upd = FakeUpdate(chat_id=1)
        await bot._run(upd, "hello")                 # must not raise
        return fake, upd

    fake, upd = asyncio.run(scenario())

    assert any("Agent error" in r for r in upd.message.replies)
    assert log_calls == []                           # failed turn not persisted
    assert fake.closed is False                      # plain error != cancel; no reset
    assert bot._running.get(1) in (None, set())      # registry cleaned even on error
    assert bot._stopping == set()


# ===========================================================================
# Genuine (non-user) cancellation must propagate, not be swallowed
# ===========================================================================

def test_genuine_cancellation_propagates(_clean_state):
    log_calls = _clean_state

    async def scenario():
        fake = FakeAgent(block=True)
        bot._agents[1] = fake
        upd = FakeUpdate(chat_id=1)
        run_task = asyncio.create_task(bot._run(upd, "hello"))
        await _wait(fake.started.wait())

        # Cancel WITHOUT marking it in _stopping (simulates shutdown cancel).
        run_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run_task
        return fake, run_task

    fake, run_task = asyncio.run(scenario())

    assert run_task.cancelled() is True              # propagated, not swallowed
    assert log_calls == []                           # nothing persisted
    assert fake.closed is False                      # NOT treated as a user stop
    assert bot._running.get(1) in (None, set())      # finally still cleaned up
    assert bot._stopping == set()


# ===========================================================================
# /stop is isolated per chat
# ===========================================================================

def test_stop_only_affects_requesting_chat(_clean_state):
    log_calls = _clean_state

    async def scenario():
        a, b = FakeAgent(block=True), FakeAgent(block=True)
        bot._agents[1], bot._agents[2] = a, b
        upd1, upd2 = FakeUpdate(chat_id=1), FakeUpdate(chat_id=2)
        t1 = asyncio.create_task(bot._run(upd1, "one"))
        t2 = asyncio.create_task(bot._run(upd2, "two"))
        await _wait(a.started.wait())
        await _wait(b.started.wait())

        # Stop only chat 1.
        await bot.cmd_stop(FakeUpdate(chat_id=1), FakeCtx())
        await _wait(t1)

        # chat 2 still running and untouched.
        assert not t2.done()
        assert b.closed is False
        assert 2 in bot._agents
        assert bot._running.get(2) and len(bot._running[2]) == 1

        # Now let chat 2 finish normally.
        b.release.set()
        await _wait(t2)
        return a, b, upd2

    a, b, upd2 = asyncio.run(scenario())

    assert a.closed is True                           # chat 1 reset
    assert b.closed is False                          # chat 2 completed normally
    # exactly one persisted turn — chat 2's; chat 1's stop was not logged
    assert len(log_calls) == 1
    assert any("answer:two" in r for r in upd2.message.replies)
    assert bot._running == {}                         # both chats cleaned out
    assert bot._stopping == set()


# ===========================================================================
# Stop path is robust even if the Telegram reply fails
# ===========================================================================

def test_stop_reply_failure_does_not_crash(_clean_state):
    log_calls = _clean_state

    async def scenario():
        fake = FakeAgent(block=True)
        bot._agents[1] = fake
        upd = FakeUpdate(chat_id=1, reply_raises=True)   # reply_text raises
        run_task = asyncio.create_task(bot._run(upd, "hello"))
        await _wait(fake.started.wait())
        await bot.cmd_stop(FakeUpdate(chat_id=1), FakeCtx())
        await _wait(run_task)                            # must not raise
        return fake, run_task

    fake, run_task = asyncio.run(scenario())

    assert run_task.cancelled() is False                 # handled despite reply error
    assert fake.closed is True                           # reset still happened
    assert 1 not in bot._agents
    assert log_calls == []
    assert bot._running.get(1) in (None, set())
    assert bot._stopping == set()


# ===========================================================================
# _untrack_task centrally clears the stop marker (no _stopping leak)
# ===========================================================================

def test_untrack_clears_stop_marker():
    """Even if a task was marked in _stopping but the inner stop-handler never
    ran (e.g. /stop arrived during the post-ask reply), the finally-path untrack
    must drop it from both registries so nothing leaks."""
    async def scenario():
        async def noop():
            return
        t = asyncio.create_task(noop())
        await t
        bot._running[9] = {t}
        bot._stopping.add(t)
        bot._untrack_task(9, t)

    asyncio.run(scenario())
    assert bot._stopping == set()
    assert bot._running.get(9) in (None, set())


# ===========================================================================
# /stop cancels a committee run too
# ===========================================================================

def test_stop_cancels_committee_run(_clean_state, monkeypatch):
    log_calls = _clean_state
    gate = type("G", (), {"started": asyncio.Event(), "release": asyncio.Event()})()

    async def _blocking_run(sym):
        gate.started.set()
        await gate.release.wait()
        raise AssertionError("should have been cancelled before returning")

    monkeypatch.setattr("cio.committee.run_committee", _blocking_run)

    async def scenario():
        upd = FakeUpdate(chat_id=5)
        ctx = FakeCtx(args=["AAPL"])
        run_task = asyncio.create_task(bot.cmd_committee(upd, ctx))
        await _wait(gate.started.wait())
        assert bot._running.get(5) and len(bot._running[5]) == 1

        await bot.cmd_stop(FakeUpdate(chat_id=5), FakeCtx())
        await _wait(run_task)
        return upd, run_task

    upd, run_task = asyncio.run(scenario())

    assert run_task.cancelled() is False                 # wrapper handled the cancel
    assert any("stopped" in r.lower() for r in upd.message.replies)
    assert bot._running.get(5) in (None, set())          # cleaned
    assert bot._stopping == set()
    assert log_calls == []                               # committee path never logs turns
