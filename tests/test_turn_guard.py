"""
test_turn_guard.py — Step 13a: proves `_guarded_turn`, not the transport, sets
the per-turn scope guard. A `BaseRuntime` subclass whose `_run_query` does
nothing but record `_ACTIVE_SCOPE` must see its own scope — never a stale
value or another runtime's — which is exactly the cross-chat memory leak this
step closes. No network, no LLM.
"""
from __future__ import annotations

import asyncio

import cio.agent as agent


class _RecordingRuntime(agent.BaseRuntime):
    """Minimal BaseRuntime subclass: `_run_query` does nothing but record the
    global `_guarded_turn` is responsible for setting, proving the guard —
    not any transport — is what sets it."""

    def __init__(self, chat_id):
        super().__init__(model=None, chat_id=chat_id, on_session_id=None,
                          service="test", session_id=None)
        self.seen_scope = None

    async def _run_query(self, prompt):
        self.seen_scope = agent._ACTIVE_SCOPE
        return (prompt, [])


def test_guarded_turn_sets_active_scope_per_runtime():
    """Each `_guarded_turn` call sets `_ACTIVE_SCOPE` to ITS OWN scope before
    `_run_query` runs. Two runtimes with different scopes called back-to-back
    must never see each other's scope."""
    async def run():
        first = _RecordingRuntime(chat_id=1)
        second = _RecordingRuntime(chat_id=2)

        text1, images1 = await first._guarded_turn("hello")
        assert (text1, images1) == ("hello", [])
        assert first.seen_scope == "chat:1" == first._scope

        text2, images2 = await second._guarded_turn("world")
        assert (text2, images2) == ("world", [])
        assert second.seen_scope == "chat:2" == second._scope

        # first's recorded scope survives second's later, different-scope turn.
        assert first.seen_scope == "chat:1"

    asyncio.run(run())
