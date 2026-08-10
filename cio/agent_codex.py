"""ChatGPT-subscription transports for cio.bot via Codex app-server.

This is deliberately separate from :mod:`cio.agent_openai`: ``openai`` sends
metered API requests with ``OPENAI_API_KEY`` while ``codex`` uses the ChatGPT
account previously authenticated by ``codex login``.  The app-server's dynamic
tools let Codex call the same in-process CIO handlers as the Claude and OpenAI
API runtimes; no portfolio tool is reimplemented here. Committee-style agents
reuse the protocol client through ``committee.engine`` with isolated one-shot
threads and no tools.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path
from typing import Any

from . import context, convlog, db, memory
from .agent import (CIO_TOOLS, SYSTEM_PROMPT, BaseRuntime, _PENDING,
                    _PENDING_DOCS, _env)
from .tool_bridge import _claude_schema

log = logging.getLogger(__name__)

CODEX_BIN = _env("CODEX_BIN", "codex")
CODEX_CWD = _env("CODEX_CWD", "/tmp")
CODEX_START_TIMEOUT = float(_env("CODEX_START_TIMEOUT", "20"))
CODEX_TURN_TIMEOUT = float(_env("CODEX_TURN_TIMEOUT", "600"))

_UPLOAD_PATH_RE = re.compile(r"(/\S+?\.(?:png|jpe?g|gif|webp))", re.I)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

_CODEX_DEVELOPER_INSTRUCTIONS = """
You are running as the conversational model inside cio.bot, not as a coding
assistant. Use the CIO dynamic tools for portfolio, market, memory, research,
and report actions. Do not inspect or modify source code, run shell commands,
or use filesystem tools. Treat tool output as authoritative under the source
policy in the base instructions. Return only the answer intended for the
Telegram user.
""".strip()


class CodexAppServerError(RuntimeError):
    """A protocol, process, authentication, or turn failure."""


def _dynamic_tool_specs(tools=CIO_TOOLS) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": tool.name,
            "description": tool.description,
            "inputSchema": _claude_schema(tool),
        }
        for tool in tools
    ]


def _turn_input(prompt: str) -> list[dict[str, Any]]:
    """Build app-server input, attaching readable uploaded images by path."""
    result: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    seen: set[str] = set()
    for raw in _UPLOAD_PATH_RE.findall(prompt or ""):
        if raw in seen:
            continue
        seen.add(raw)
        path = Path(raw)
        try:
            if path.suffix.lower() in _IMAGE_SUFFIXES and path.is_file():
                result.append({"type": "localImage", "path": str(path.resolve())})
        except OSError:
            log.warning("CodexRuntime: could not attach image %s", path, exc_info=True)
    return result


class CodexAppServer:
    """Small asynchronous JSONL client for ``codex app-server --stdio``."""

    def __init__(self, tools: list = CIO_TOOLS):
        self._tools = {tool.name: tool for tool in tools}
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._turn_waiters: dict[str, asyncio.Future] = {}
        self._completed_turns: dict[str, dict] = {}
        self._turn_usage: dict[str, int] = {}
        self._next_id = 0
        self._write_lock = asyncio.Lock()

    async def start(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        binary = shutil.which(CODEX_BIN) if not os.path.isabs(CODEX_BIN) else CODEX_BIN
        if not binary or not Path(binary).exists():
            raise CodexAppServerError(
                f"Codex CLI not found ({CODEX_BIN!r}); install it and run `codex login`")
        env = os.environ.copy()
        codex_home = os.getenv("CIO_CODEX_HOME")
        if codex_home:
            env["CODEX_HOME"] = codex_home
        self._process = await asyncio.create_subprocess_exec(
            binary, "app-server", "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        try:
            await asyncio.wait_for(self._request("initialize", {
                "clientInfo": {"name": "cio-bot", "title": "CIO Bot", "version": "1"},
                "capabilities": {"experimentalApi": True},
            }), timeout=CODEX_START_TIMEOUT)
            await self._send({"method": "initialized"})
            account = await asyncio.wait_for(
                self._request("account/read", {}), timeout=CODEX_START_TIMEOUT)
        except Exception:
            await self.close()
            raise
        account_info = account.get("account") if isinstance(account, dict) else None
        if (not isinstance(account_info, dict)
                or account_info.get("type") != "chatgpt"):
            await self.close()
            raise CodexAppServerError(
                "Codex is not logged in with a ChatGPT subscription. Run `codex login` "
                "as the same OS user that runs cio.bot, then choose ChatGPT sign-in.")

    async def _send(self, message: dict) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise CodexAppServerError("Codex app-server is not running")
        wire = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            process.stdin.write(wire)
            await process.stdin.drain()

    async def _request(self, method: str, params: dict) -> dict:
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while line := await self._process.stdout.readline():
                try:
                    message = json.loads(line)
                except Exception:
                    log.warning("Codex app-server emitted invalid JSON", exc_info=True)
                    continue
                if "id" in message and "method" in message:
                    await self._handle_server_request(message)
                elif "id" in message:
                    future = self._pending.get(message["id"])
                    if future is not None and not future.done():
                        if message.get("error") is not None:
                            future.set_exception(CodexAppServerError(str(message["error"])))
                        else:
                            future.set_result(message.get("result") or {})
                elif "method" in message:
                    self._handle_notification(message.get("method"), message.get("params") or {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Codex app-server reader failed: %s", exc, exc_info=True)
        finally:
            error = CodexAppServerError("Codex app-server exited")
            for future in [*self._pending.values(), *self._turn_waiters.values()]:
                if not future.done():
                    future.set_exception(error)

    async def _stderr_loop(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        try:
            while line := await self._process.stderr.readline():
                log.warning("Codex app-server: %s", line.decode(errors="replace").rstrip())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("Codex app-server stderr reader failed", exc_info=True)

    async def _handle_server_request(self, message: dict) -> None:
        if message.get("method") != "item/tool/call":
            await self._send({
                "id": message.get("id"),
                "error": {"code": -32601, "message": "unsupported server request"},
            })
            return
        params = message.get("params") or {}
        name = params.get("tool")
        tool = self._tools.get(name)
        if tool is None:
            result = {"success": False, "contentItems": [
                {"type": "inputText", "text": f"Error: unknown CIO tool {name!r}"}
            ]}
        else:
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            arguments = {k: v for k, v in arguments.items() if v is not None}
            try:
                raw = await tool.handler(arguments)
                blocks = (raw or {}).get("content") or []
                text = "".join(
                    block.get("text", "") for block in blocks if isinstance(block, dict)
                )
                result = {"success": True, "contentItems": [
                    {"type": "inputText", "text": text}
                ]}
            except Exception as exc:
                log.warning("Codex dynamic tool %s failed: %s", name, exc, exc_info=True)
                result = {"success": False, "contentItems": [
                    {"type": "inputText", "text": f"Error: {name} failed: {exc}"}
                ]}
        await self._send({"id": message.get("id"), "result": result})

    def _handle_notification(self, method: str, params: dict) -> None:
        if method == "thread/tokenUsage/updated":
            turn_id = params.get("turnId")
            last = ((params.get("tokenUsage") or {}).get("last") or {})
            if turn_id:
                self._turn_usage[turn_id] = int(last.get("totalTokens") or 0)
            return
        if method != "turn/completed":
            return
        turn = params.get("turn") or {}
        turn_id = turn.get("id")
        if not turn_id:
            return
        waiter = self._turn_waiters.get(turn_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(turn)
        else:
            self._completed_turns[turn_id] = turn

    async def start_thread(self, *, model: str | None, instructions: str,
                           resume: str | None = None,
                           developer_instructions: str | None = None,
                           ephemeral: bool = False) -> str:
        await self.start()
        common = {
            "model": model,
            "cwd": CODEX_CWD,
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "baseInstructions": instructions,
            "developerInstructions": (developer_instructions
                                      or _CODEX_DEVELOPER_INSTRUCTIONS),
        }
        if resume:
            try:
                response = await self._request("thread/resume", {
                    **common, "threadId": resume,
                })
                return response["thread"]["id"]
            except Exception as exc:
                log.warning("CodexRuntime: could not resume thread %s; starting fresh: %s",
                            resume, exc)
        response = await self._request("thread/start", {
            **common,
            "dynamicTools": _dynamic_tool_specs(self._tools.values()),
            "ephemeral": ephemeral,
        })
        return response["thread"]["id"]

    async def run_turn(self, thread_id: str, prompt: str,
                       effort: str | None = None) -> tuple[str, int]:
        params = {
            "threadId": thread_id,
            "input": _turn_input(prompt),
        }
        if effort:
            # App-server calls this field `effort`. Values are advertised per
            # model; sending it per turn also updates subsequent turns.
            params["effort"] = effort
        response = await self._request("turn/start", params)
        turn_id = response["turn"]["id"]
        turn = self._completed_turns.pop(turn_id, None)
        if turn is None:
            waiter = asyncio.get_running_loop().create_future()
            self._turn_waiters[turn_id] = waiter
            try:
                turn = await asyncio.wait_for(waiter, timeout=CODEX_TURN_TIMEOUT)
            finally:
                self._turn_waiters.pop(turn_id, None)
        if turn.get("status") != "completed":
            error = turn.get("error") or turn.get("status")
            raise CodexAppServerError(f"Codex turn failed: {error}")
        messages = [item.get("text", "") for item in turn.get("items", [])
                    if item.get("type") == "agentMessage"]
        text = messages[-1] if messages else ""
        return text, self._turn_usage.pop(turn_id, 0)

    async def close(self) -> None:
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            try:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            except ProcessLookupError:
                pass
        current = asyncio.current_task()
        cancelled = []
        for task in (self._reader_task, self._stderr_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
                cancelled.append(task)
        if cancelled:
            await asyncio.gather(*cancelled, return_exceptions=True)
        self._reader_task = self._stderr_task = None


class CodexRuntime(BaseRuntime):
    """Whole-turn CIO runtime billed through the logged-in ChatGPT account."""

    def __init__(self, link: dict, chat_id: int | None = None, on_session_id=None,
                 client_factory=CodexAppServer):
        model = link.get("model") or None
        if str(model).lower() in ("default", "subscription-default"):
            model = None
        effort = (os.getenv("CIO_CODEX_REASONING_EFFORT")
                  or link.get("reasoning_effort")
                  or link.get("thinking_level")
                  or link.get("effort"))
        effort = str(effort).strip().lower() if effort else None
        if effort in ("default", "auto", "none"):
            effort = None
        super().__init__(model, chat_id, on_session_id, "codex", None)
        self._reasoning_effort = effort
        self._client_factory = client_factory
        self._client: CodexAppServer | None = None
        self._system_prompt = ""
        self._meta_key = f"codex_thread:{self._scope}"
        try:
            self._resume = memory.get_meta(self._meta_key, db_path=db.DB_PATH)
        except Exception:
            self._resume = None

    async def _ensure(self) -> None:
        if self._client is not None and self._session_id:
            return
        self._client = self._client_factory()
        self._system_prompt = context.compose_system_prompt(SYSTEM_PROMPT, self._chat_id)
        try:
            thread_id = await self._client.start_thread(
                model=self._model, instructions=self._system_prompt, resume=self._resume)
        except Exception:
            # Let reselect_needed() move the next turn to the chain's API/Claude
            # fallback instead of pinning a missing or logged-out Codex process.
            from .committee import engine
            engine.latch("codex")
            if self._client is not None:
                await self._client.close()
                self._client = None
            raise
        self._session_id = thread_id
        self._resume = thread_id
        try:
            memory.set_meta(self._meta_key, thread_id, db_path=db.DB_PATH)
        except Exception:
            log.warning("CodexRuntime: could not persist thread id", exc_info=True)

    async def _run_query(self, prompt: str) -> tuple[str, list[str]]:
        assert self._client is not None and self._session_id is not None
        try:
            text, tokens = await self._client.run_turn(
                self._session_id, prompt, effort=self._reasoning_effort)
        except Exception as exc:
            log.warning("CodexRuntime turn failed: %s", exc, exc_info=True)
            from .committee import engine
            engine.latch("codex")
            text, tokens = f"OpenAI subscription runtime error: {exc}", 0
        if tokens <= 0:
            tokens = context.count_tokens(prompt) + context.count_tokens(text)
        self._record_usage(tokens, prompt, text, self._service)
        convlog.log_call(self._service, self._model or "codex-default",
                         self._system_prompt, prompt, text, tokens,
                         scope=self._scope, kind="chat")
        images = list(_PENDING)
        _PENDING.clear()
        self._last_docs = list(_PENDING_DOCS)
        _PENDING_DOCS.clear()
        return text or "The OpenAI subscription runtime returned no response.", images

    async def _reset_session(self) -> None:
        assert self._client is not None
        self._system_prompt = context.compose_system_prompt(SYSTEM_PROMPT, self._chat_id)
        thread_id = await self._client.start_thread(
            model=self._model, instructions=self._system_prompt, resume=None)
        self._session_id = thread_id
        self._resume = thread_id
        try:
            memory.set_meta(self._meta_key, thread_id, db_path=db.DB_PATH)
        except Exception:
            log.warning("CodexRuntime: could not persist reset thread id", exc_info=True)

    async def close(self) -> None:
        await super().close()
        if self._client is not None:
            await self._client.close()
            self._client = None
