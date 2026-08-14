"""Asyncio NDJSON JSON-RPC client for the DeepSeek Harness SDK runtime.

The dsh SDK runtime (`dsh-jsonrpc-agent`) speaks newline-delimited JSON-RPC 2.0
over stdio: stdout carries only protocol frames, diagnostics go to stderr. The
official Python SDK is synchronous/thread-based; the kernel runs fully async,
so this is a native-asyncio reimplementation of the same thin wire protocol
(see docs/references/deepseek-harness/python-sdk.md for the verified contract).

Wire surface used by the runtime adapter:

* ``initialize {cwd, provider, model, maxTokens?}`` — per-process model config
* ``session/prompt {sessionId, contentBlocks}`` -> ``{messageId}`` (enqueue
  receipt; turn outcome arrives via notifications)
* ``shutdown`` — graceful dispose + exit 0
* notifications ``session.event`` / ``session.status`` /
  ``subagent.started`` / ``subagent.finished``
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel pushed to the notification queue when the transport dies so a
# consumer blocked on ``get()`` wakes up instead of hanging forever.
TRANSPORT_CLOSED = object()


class DshTransportClosedError(RuntimeError):
    """The dsh runtime subprocess exited or closed its stdio pipes.

    Registered in ``src.runtimes.interruption`` so a mid-turn process death is
    classified as an interruption (resumable) rather than an execution error.
    """


class DshJsonRpcError(RuntimeError):
    """The dsh runtime answered a request with a JSON-RPC error object."""

    def __init__(self, code: int | None, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass(frozen=True)
class DshNotification:
    method: str
    payload: dict[str, Any]


class DshRuntimeClient:
    """One dsh runtime subprocess + its JSON-RPC stdio channel.

    Single-consumer contract: exactly one coroutine reads ``notifications``
    (the runtime's turn loop). Requests may be issued concurrently.
    """

    def __init__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        stderr_tail_lines: int = 400,
    ) -> None:
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self.notifications: asyncio.Queue[DshNotification | object] = asyncio.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=stderr_tail_lines)
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if self._proc is not None:
            raise RuntimeError("DshRuntimeClient.start called twice")
        self._proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
        )
        self._reader_task = asyncio.create_task(
            self._reader_loop(), name="dsh-runtime-stdout"
        )
        self._stderr_task = asyncio.create_task(
            self._stderr_loop(), name="dsh-runtime-stderr"
        )

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        proc = self._proc
        if proc is None or proc.returncode is not None or proc.stdin is None:
            raise self._closed_error("dsh runtime is not running")
        request_id = uuid.uuid4().hex
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        try:
            proc.stdin.write(json.dumps(message, separators=(",", ":")).encode() + b"\n")
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            self._pending.pop(request_id, None)
            raise self._closed_error("failed to write to dsh runtime") from exc
        try:
            if timeout is not None:
                return await asyncio.wait_for(future, timeout)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def initialize(
        self,
        *,
        cwd: str,
        provider: str,
        model: str,
        max_tokens: int | None = None,
        timeout: float | None = 60.0,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"cwd": cwd, "provider": provider, "model": model}
        if max_tokens is not None:
            params["maxTokens"] = max_tokens
        result = await self.request("initialize", params, timeout=timeout)
        return result if isinstance(result, dict) else {}

    async def session_prompt(
        self, session_id: str, content_blocks: list[dict[str, Any]]
    ) -> str:
        result = await self.request(
            "session/prompt",
            {"sessionId": session_id, "contentBlocks": content_blocks},
        )
        message_id = result.get("messageId") if isinstance(result, dict) else None
        if not isinstance(message_id, str):
            raise DshJsonRpcError(None, "session/prompt returned no messageId", result)
        return message_id

    async def close(self, *, shutdown_timeout: float = 2.0) -> None:
        """Graceful shutdown -> terminate -> kill, then reap the reader tasks."""
        proc = self._proc
        if proc is None:
            return
        if proc.returncode is None:
            try:
                await self.request("shutdown", timeout=shutdown_timeout)
            except Exception:
                logger.debug("dsh shutdown request failed", exc_info=True)
            if proc.stdin is not None:
                try:
                    proc.stdin.close()
                except Exception:
                    logger.debug("dsh stdin close failed", exc_info=True)
            if proc.returncode is None:
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=shutdown_timeout)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        await self._reap_tasks()
        self._proc = None
        self._fail_pending(self._closed_error("dsh runtime closed"))

    def kill(self) -> None:
        """Hard-stop the subprocess (interrupt path). Safe when already dead."""
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    # -- internals --

    async def _reader_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    logger.debug("dsh runtime emitted non-JSON stdout line: %.200s", text)
                    continue
                if isinstance(message, dict):
                    self._dispatch(message)
        finally:
            self._fail_pending(self._closed_error("dsh runtime stdout closed"))
            self.notifications.put_nowait(TRANSPORT_CLOSED)

    def _dispatch(self, message: dict[str, Any]) -> None:
        msg_id = message.get("id")
        method = message.get("method")
        if isinstance(msg_id, str) and method is None:
            future = self._pending.get(msg_id)
            if future is None or future.done():
                return
            error = message.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                future.set_exception(
                    DshJsonRpcError(
                        code if isinstance(code, int) else None,
                        str(error.get("message", "JSON-RPC error")),
                        error.get("data"),
                    )
                )
            else:
                future.set_result(message.get("result"))
            return
        if isinstance(method, str) and msg_id is None:
            params = message.get("params")
            self.notifications.put_nowait(
                DshNotification(method, params if isinstance(params, dict) else {})
            )
            return
        # Server->client requests are reserved-but-unsent on the current wire
        # (future approval flows). Log so a protocol upgrade is visible.
        logger.warning("dsh runtime sent an unexpected frame shape: %.200s", str(message))

    async def _stderr_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            self._stderr_tail.append(line.decode("utf-8", errors="replace").rstrip())

    async def _reap_tasks(self) -> None:
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                try:
                    await asyncio.wait_for(task, timeout=1.0)
                except TimeoutError:
                    task.cancel()
        self._reader_task = None
        self._stderr_task = None

    def _fail_pending(self, exc: BaseException) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for future in pending:
            if not future.done():
                future.set_exception(exc)

    def _closed_error(self, reason: str) -> DshTransportClosedError:
        parts = [reason]
        proc = self._proc
        if proc is not None and proc.returncode is not None:
            parts.append(f"exit code: {proc.returncode}")
        tail = self.stderr_tail()
        if tail:
            parts.append(f"stderr tail:\n{tail}")
        return DshTransportClosedError("\n".join(parts))
