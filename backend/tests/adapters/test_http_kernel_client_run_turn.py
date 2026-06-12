"""HttpKernelClient.run_turn — WS happy path, error frames, drops.

PR #85 review follow-up: the WS channel only had a tokenless-rejection
test. These pin the wire contract against a local fake kernel WS server:
the outbound ``message`` payload shape (text / attachments /
additional_context), turn-terminal frame handling (``session_idle`` /
``session_error``), the message read-back, error-frame mapping, and the
closed-channel mapping.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio
import json
import socket
from typing import Any

import pytest
import websockets

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from app.schemas import MessageData, UserMessageSchema

from valuz_agent.adapters.kernel_client import KernelClientError, KernelUnavailableError
from valuz_agent.adapters.kernel_client_http import HttpKernelClient


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _fake_message(session_id: str) -> MessageData:
    return MessageData(
        id="m-1",
        session_id=session_id,
        user_message=UserMessageSchema(text="hi", attachments=[]),
        assistant_message="done",
        status="completed",
        total_turns=1,
        started_at=0,
    )


class _FakeKernelWs:
    """One-shot fake of the kernel's WS /run channel."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = frames
        self.received: list[dict[str, Any]] = []
        self.port = _free_port()
        self._server: Any = None

    async def _handler(self, ws: Any) -> None:
        raw = await ws.recv()
        self.received.append(json.loads(raw))
        if not self.frames:
            return  # dropped-channel case: close immediately, no terminal frame
        for frame in self.frames:
            await ws.send(json.dumps(frame))
        # Hold the socket open until the CLIENT closes — deterministic on
        # loaded CI, no fixed sleep. (The client disconnects after its
        # terminal frame; the empty-frames case ends via wait_closed too,
        # mapping to KernelUnavailableError client-side.)
        try:
            await asyncio.wait_for(ws.wait_closed(), timeout=10)
        except TimeoutError:
            pass

    async def __aenter__(self) -> _FakeKernelWs:
        self._server = await websockets.serve(self._handler, "127.0.0.1", self.port)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._server.close()
        await self._server.wait_closed()


def test_run_turn_sends_payload_and_returns_message_on_idle() -> None:
    async def _run():
        frames = [
            {"type": "assistant_message", "data": {"text": "thinking..."}, "timestamp": 1},
            {"type": "session_idle", "data": {"stop_reason": "end_turn"}, "timestamp": 2},
        ]
        async with _FakeKernelWs(frames) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}", token="tok")

            async def _fake_list_messages(session_id: str, *, limit: int = 50, offset: int = 0):
                assert session_id == "sess-1" and limit == 1
                return [_fake_message(session_id)]

            client.list_messages = _fake_list_messages  # type: ignore[method-assign]
            try:
                message = await client.run_turn(
                    "sess-1",
                    "research AAPL",
                    attachments=[{"source_path": "/tmp/a.pdf", "parsed_path": "/tmp/a.md"}],
                    additional_context="ctx-block",
                )
            finally:
                await client.aclose()
            return fake.received, message

    received, message = asyncio.run(_run())

    # Outbound payload shape (the kernel's _parse_user_message contract).
    assert received == [
        {
            "message": {
                "text": "research AAPL",
                "attachments": [{"source_path": "/tmp/a.pdf", "parsed_path": "/tmp/a.md"}],
                "additional_context": "ctx-block",
            }
        }
    ]
    assert isinstance(message, MessageData)
    assert message.assistant_message == "done"


def test_run_turn_treats_session_error_as_terminal() -> None:
    async def _run():
        frames = [{"type": "session_error", "data": {"message": "boom"}, "timestamp": 1}]
        async with _FakeKernelWs(frames) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}", token="tok")

            async def _fake_list_messages(session_id: str, *, limit: int = 50, offset: int = 0):
                return [_fake_message(session_id)]

            client.list_messages = _fake_list_messages  # type: ignore[method-assign]
            try:
                return await client.run_turn("sess-1", "hi")
            finally:
                await client.aclose()

    # session_error ends the turn; the message row (status=errored upstream)
    # is still read back rather than raising — error semantics live on the row.
    message = asyncio.run(_run())
    assert isinstance(message, MessageData)


def test_run_turn_maps_error_frame_to_client_error() -> None:
    async def _run():
        frames = [{"type": "error", "data": {"message": "Session not found"}}]
        async with _FakeKernelWs(frames) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}", token="tok")
            try:
                await client.run_turn("missing", "hi")
            finally:
                await client.aclose()

    with pytest.raises(KernelClientError) as excinfo:
        asyncio.run(_run())
    assert "Session not found" in str(excinfo.value)


def test_run_turn_maps_dropped_channel_to_unavailable() -> None:
    async def _run():
        # Server sends nothing and closes immediately → mid-turn drop.
        async with _FakeKernelWs([]) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}", token="tok")
            try:
                await client.run_turn("sess-1", "hi")
            finally:
                await client.aclose()

    with pytest.raises(KernelUnavailableError):
        asyncio.run(_run())
