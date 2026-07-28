"""Regression cover for the dead-CLI bug — cause and resilience.

The cause: `chats.session_id` holds the Claude CLI's own session id (a UUID) and
is what a restart passes to `--resume`. `OpenAIRuntime._ensure` published its
internal SQLiteSession key (`chat:<chat_id>`) through the same `on_session_id`
callback, so any chat that ever answered on OpenAI poisoned the slot. The CLI
then got `--resume chat:8535885767`, started fine, and exited 1 while handling the
first message — reproduced verbatim against the real CLI. Every turn on that chat
failed identically until the row was cleared.

The resilience: `CIOAgent._connected` records that *we* once connected, not that
the child is alive, so `_ensure()` short-circuited and later turns wrote into a
dead pipe. And the SDK re-raises a CLI death as a *bare* `Exception`, so a narrow
`except (CLIConnectionError, ProcessError)` misses the commonest case.

Covered below: the session-id validator, the OpenAI writer fix (in
tests/test_openai_runtime.py), the DB migration that clears already-poisoned
rows, the transport probe in `_ensure`, reconnect-and-retry on the write, and the
mid-stream handling that drops `_connected` and re-tries once on a fresh session.
"""
import asyncio

import pytest
from claude_agent_sdk import CLIConnectionError, ProcessError

import cio.agent as agent
import cio.bot as bot
import cio.db as db


def _run(coro):
    return asyncio.run(coro)


# A CLI-issued session id. Must be UUID-shaped: anything else is rejected before
# it can reach `--resume` (see `_resumable_session_id`).
_SESSION = "fc179316-1eae-442c-8814-5a46a5594076"


# --- fakes ------------------------------------------------------------------

class _FakeProcess:
    def __init__(self, returncode=None):
        self.returncode = returncode


class _FakeTransport:
    def __init__(self, ready=True, returncode=None):
        self._ready = ready
        self._process = _FakeProcess(returncode)

    def is_ready(self):
        return self._ready


class _FakeClient:
    """Stands in for ClaudeSDKClient.

    `fail_writes` writes raise the dead-child error before any succeed; a
    `stream_error` is raised partway through `receive_response`.
    """

    def __init__(self, *, transport=None, fail_writes=0, stream_error=None,
                 kill_on_stream_error=True):
        self._transport = transport if transport is not None else _FakeTransport()
        self._fail_writes = fail_writes
        self.stream_error = stream_error
        # A CLI death takes the child with it; an application-level error doesn't.
        self.kill_on_stream_error = kill_on_stream_error
        self.writes: list[str] = []
        self.connects = 0
        self.disconnects = 0

    async def connect(self):
        self.connects += 1

    async def disconnect(self):
        self.disconnects += 1

    async def query(self, prompt, session_id="default"):
        if self._fail_writes > 0:
            self._fail_writes -= 1
            raise CLIConnectionError("Cannot write to terminated process (exit code: 1)")
        self.writes.append(prompt)

    async def receive_response(self):
        if self.stream_error:
            if self.kill_on_stream_error:
                self._transport = _FakeTransport(ready=False, returncode=1)
            raise self.stream_error
        return
        yield  # pragma: no cover - makes this an async generator


def _agent(monkeypatch, client):
    """A CIOAgent with the SDK client swapped out and turn bookkeeping stubbed."""
    monkeypatch.setattr(agent.CIOAgent, "_record_usage", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(agent.convlog, "log_call", lambda *a, **k: None)
    a = agent.CIOAgent(chat_id=1)
    a._client = client
    return a


# --- _transport_alive -------------------------------------------------------

def test_alive_when_child_is_running(monkeypatch):
    a = _agent(monkeypatch, _FakeClient())
    assert a._transport_alive() is True


def test_dead_when_child_exited(monkeypatch):
    a = _agent(monkeypatch, _FakeClient(transport=_FakeTransport(returncode=1)))
    assert a._transport_alive() is False


def test_dead_when_transport_not_ready(monkeypatch):
    a = _agent(monkeypatch, _FakeClient(transport=_FakeTransport(ready=False)))
    assert a._transport_alive() is False


def test_dead_when_no_transport_yet(monkeypatch):
    client = _FakeClient()
    client._transport = None
    a = _agent(monkeypatch, client)
    assert a._transport_alive() is False


def test_unknown_transport_shape_assumed_alive(monkeypatch):
    """An SDK whose internals we can't read must not be declared dead on every
    turn — the write path's retry is the fallback."""
    class _Opaque:
        pass

    a = _agent(monkeypatch, _FakeClient(transport=_Opaque()))
    assert a._transport_alive() is True


# --- _ensure ----------------------------------------------------------------

def test_ensure_is_a_noop_while_the_child_lives(monkeypatch):
    client = _FakeClient()
    a = _agent(monkeypatch, client)
    a._connected = True
    _run(a._ensure())
    assert client.connects == 0 and client.disconnects == 0


def test_ensure_rebuilds_after_the_child_dies(monkeypatch):
    """The bug: this used to return early and leave the corpse in place."""
    dead = _FakeClient(transport=_FakeTransport(returncode=1))
    fresh = _FakeClient()
    a = _agent(monkeypatch, dead)
    a._connected = True
    a._session_id = _SESSION
    a._make_client = lambda resume: fresh

    _run(a._ensure())

    assert dead.disconnects == 1        # corpse released
    assert a._client is fresh and fresh.connects == 1
    assert a._connected is True
    assert a._resume == _SESSION      # same thread resumed, context kept


def test_reconnect_falls_back_to_a_fresh_session_when_resume_fails(monkeypatch):
    """The transcript can be gone (reboot, pruned session). Resume failure must
    not brick the chat — `_ensure`'s existing fallback still applies."""
    dead = _FakeClient(transport=_FakeTransport(returncode=1))

    class _ResumeRejects(_FakeClient):
        async def connect(self):
            self.connects += 1
            raise RuntimeError(f"No conversation found with session ID {_SESSION}")

    resumed, fresh = _ResumeRejects(), _FakeClient()
    built: list[str | None] = []

    a = _agent(monkeypatch, dead)
    a._connected = True
    a._session_id = _SESSION

    def make(resume):
        built.append(resume)
        return resumed if resume else fresh

    a._make_client = make
    _run(a._ensure())

    assert built == [_SESSION, None]
    assert a._client is fresh and a._connected is True
    assert a._session_id is None


# --- _run_query -------------------------------------------------------------

def test_write_failure_reconnects_and_retries_once(monkeypatch):
    """Nothing reached the child when the write raised, so re-sending the prompt
    cannot duplicate the turn."""
    dead = _FakeClient(fail_writes=1)
    fresh = _FakeClient()
    a = _agent(monkeypatch, dead)
    a._connected = True
    a._make_client = lambda resume: fresh

    text, images = _run(a._run_query("watchlist briefing"))

    assert text == "" and images == []
    assert fresh.writes == ["watchlist briefing"]   # delivered on the retry
    assert a._client is fresh and a._connected is True


def test_second_write_failure_propagates(monkeypatch):
    """One retry, not a loop: a still-dead CLI is a real outage."""
    dead = _FakeClient(fail_writes=2)
    a = _agent(monkeypatch, dead)
    a._connected = True
    a._make_client = lambda resume: dead   # reconnect lands on a still-broken CLI

    with pytest.raises(CLIConnectionError):
        _run(a._run_query("hi"))


def test_mid_stream_death_is_not_retried_but_unsticks_the_next_turn(monkeypatch):
    """This turn's tool calls already ran, so re-sending could repeat their side
    effects — drop `_connected` instead so the next `_ensure` rebuilds."""
    client = _FakeClient(stream_error=ProcessError("Command failed", exit_code=1))
    a = _agent(monkeypatch, client)
    a._connected = True

    with pytest.raises(ProcessError):
        _run(a._run_query("hi"))

    assert client.writes == ["hi"]      # sent exactly once
    assert a._connected is False


def test_bare_exception_from_the_message_reader_still_unsticks(monkeypatch):
    """The exact shape seen live: the SDK's reader re-raises a CLI death as a
    plain `Exception`, so `except (CLIConnectionError, ProcessError)` misses it
    and `_connected` stayed True — sticky all over again."""
    client = _FakeClient(stream_error=Exception(
        "Command failed with exit code 1 (exit code: 1)"))
    a = _agent(monkeypatch, client)
    a._connected = True

    with pytest.raises(Exception):
        _run(a._run_query("hi"))

    assert a._connected is False


def test_an_application_error_does_not_unstick_a_live_client(monkeypatch):
    """Broadening the except must not reclassify ordinary turn failures: the
    child is still alive, so the connection stays."""
    client = _FakeClient(stream_error=ValueError("bad tool input"),
                         kill_on_stream_error=False)
    a = _agent(monkeypatch, client)
    a._connected = True

    with pytest.raises(ValueError):
        _run(a._run_query("hi"))

    assert a._connected is True


def test_unproven_resume_that_dies_is_retried_once_on_a_fresh_session(monkeypatch):
    """A `--resume` token the CLI cannot resolve lets the process start and then
    kills it on the first message — indistinguishable from any other mid-stream
    death except that the thread never said anything. Drop the token and retry."""
    doomed = _FakeClient(stream_error=Exception(
        "Command failed with exit code 1 (exit code: 1)"))
    fresh = _FakeClient()
    a = _agent(monkeypatch, doomed)
    a._connected = True
    a._resume = "00000000-1111-2222-3333-444444444444"
    a._resume_proven = False
    a._make_client = lambda resume: fresh

    text, _ = _run(a._run_query("hi"))

    assert text == ""
    assert fresh.writes == ["hi"]       # re-sent on the fresh session
    assert a._resume is None            # poisoned token dropped
    assert a._connected is True


def test_a_proven_session_that_dies_is_not_retried(monkeypatch):
    """Once the thread has produced messages the resume token is exonerated, and
    a retry would re-run whatever tools this turn already fired."""
    doomed = _FakeClient(stream_error=Exception(
        "Command failed with exit code 1 (exit code: 1)"))
    a = _agent(monkeypatch, doomed)
    a._connected = True
    a._resume = "00000000-1111-2222-3333-444444444444"
    a._resume_proven = True

    with pytest.raises(Exception):
        _run(a._run_query("hi"))

    assert doomed.writes == ["hi"]      # sent exactly once, never re-sent


# --- the cause: session-id poisoning ----------------------------------------

def test_only_cli_shaped_session_ids_are_resumable():
    uuid = "06fbb57f-ceee-450f-9181-855975f8cbb1"
    assert agent._resumable_session_id(uuid) == uuid
    assert agent._resumable_session_id(uuid.upper()) == uuid.upper()
    # What OpenAIRuntime used to write into the shared slot — reproduced against
    # the real CLI as: connect() OK, then exit code 1 on the first message.
    assert agent._resumable_session_id("chat:8535885767") is None
    assert agent._resumable_session_id("") is None
    assert agent._resumable_session_id(None) is None
    assert agent._resumable_session_id("not-a-session") is None


def test_a_poisoned_resume_never_reaches_the_cli(monkeypatch):
    monkeypatch.setattr(agent.CIOAgent, "_record_usage", staticmethod(lambda *a, **k: None))
    a = agent.CIOAgent(chat_id=1, resume="chat:8535885767")
    assert a._resume is None and a.session_id is None


def test_a_good_resume_is_kept(monkeypatch):
    monkeypatch.setattr(agent.CIOAgent, "_record_usage", staticmethod(lambda *a, **k: None))
    uuid = "06fbb57f-ceee-450f-9181-855975f8cbb1"
    a = agent.CIOAgent(chat_id=1, resume=uuid)
    assert a._resume == uuid and a.session_id == uuid


def test_migration_clears_already_poisoned_rows(tmp_path):
    """The writer is fixed, but a live DB can already hold `chat:<id>` — and every
    Claude turn on that chat fails until it's gone."""
    p = tmp_path / "chats.db"
    db.init(p)
    uuid = "06fbb57f-ceee-450f-9181-855975f8cbb1"
    conn = db.connect(p)
    with conn:
        conn.execute("INSERT INTO chats (chat_id, session_id) VALUES (1, ?)", (uuid,))
        conn.execute("INSERT INTO chats (chat_id, session_id) VALUES (2, 'chat:8535885767')")
        conn.execute("INSERT INTO chats (chat_id, session_id) VALUES (3, NULL)")
    conn.close()

    db._INITIALIZED.discard(str(p.resolve()))   # force the migration to run again
    conn = db.connect(p)
    rows = {r["chat_id"]: r["session_id"]
            for r in conn.execute("SELECT chat_id, session_id FROM chats")}
    conn.close()

    assert rows == {1: uuid, 2: None, 3: None}


# --- bot-level backstop -----------------------------------------------------

def test_bot_recognises_the_dead_child_error():
    assert bot._is_transport_error(
        CLIConnectionError("Cannot write to terminated process (exit code: 1)"))
    assert bot._is_transport_error(ProcessError("Command failed", exit_code=1))
    # The bare-Exception form the SDK's message reader raises.
    assert bot._is_transport_error(
        Exception("Command failed with exit code 1 (exit code: 1)"))
    assert bot._is_transport_error(
        CLIConnectionError("ProcessTransport is not ready for writing"))


def test_bot_recognises_a_wrapped_transport_error():
    try:
        try:
            raise CLIConnectionError("Cannot write to terminated process (exit code: 1)")
        except CLIConnectionError as e:
            raise RuntimeError("turn failed") from e
    except RuntimeError as outer:
        assert bot._is_transport_error(outer)


def test_bot_leaves_ordinary_turn_errors_alone():
    """A normal bad turn must not throw away the chat's live agent."""
    assert not bot._is_transport_error(ValueError("bad symbol"))
    assert not bot._is_transport_error(KeyError("AAPL"))
