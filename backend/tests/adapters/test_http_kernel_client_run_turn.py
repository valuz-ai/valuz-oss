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
from typing import Any

import pytest
import websockets

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from app.schemas import MessageData, UserMessageSchema

from valuz_agent.adapters.kernel_client import KernelClientError, KernelUnavailableError
from valuz_agent.adapters.kernel_client_http import HttpKernelClient


def _fake_message(session_id: str) -> MessageData:
    return MessageData(
        id="m-1",
        session_id=session_id,
        user_message=UserMessageSchema(text="hi", attachments=[]),
        assistant_message="done",
        status="completed",
        total_turns=1,
        started_at=0,
        ended_at=1,
    )


class _FakeKernelWs:
    """One-shot fake of the kernel's WS /run channel."""

    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self.frames = frames
        self.received: list[dict[str, Any]] = []
        self.port: int = 0  # assigned by the OS at serve() time — no TOCTOU
        self._server: Any = None

    async def _handler(self, ws: Any) -> None:
        await ws.send(
            json.dumps(
                {
                    "type": "run_capabilities",
                    "data": {"completion_correlation": "execution_request_id-v1"},
                }
            )
        )
        raw = await ws.recv()
        self.received.append(json.loads(raw))
        if not self.frames:
            return  # dropped-channel case: close immediately, no terminal frame
        frames = list(self.frames)
        if not any(frame["type"] in {"run_result", "error"} for frame in frames):
            last = frames[-1]
            frames.append(
                {
                    "type": "run_result",
                    "data": {"message_id": last.get("data", {}).get("message_id")},
                }
            )
        for frame in frames:
            frame = {**frame, "data": dict(frame.get("data") or {})}
            if frame["type"] == "run_result":
                frame["data"].setdefault(
                    "execution_request_id", self.received[-1]["execution_request_id"]
                )
            await ws.send(json.dumps(frame))
        # Hold the socket open until the CLIENT closes — deterministic on
        # loaded CI, no fixed sleep. (The client disconnects after its
        # terminal frame; the empty-frames case ends via wait_closed too,
        # mapping to KernelUnavailableError client-side.)
        try:
            await asyncio.wait_for(ws.wait_closed(), timeout=3)
        except TimeoutError:
            pass

    async def __aenter__(self) -> _FakeKernelWs:
        self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._server.close()
        await self._server.wait_closed()


def test_run_turn_sends_payload_and_returns_message_on_idle() -> None:
    async def _run():
        frames = [
            {"type": "assistant_message", "data": {"text": "thinking..."}, "timestamp": 1},
            {
                "type": "session_idle",
                "data": {"stop_reason": "end_turn", "message_id": "m-1"},
                "timestamp": 2,
            },
        ]
        async with _FakeKernelWs(frames) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}", token="tok")

            async def _fake_get_message(user_id, message_id: str):
                assert user_id == "owner-a" and message_id == "m-1"
                return _fake_message("sess-1")

            client.get_message = _fake_get_message  # type: ignore[method-assign]
            try:
                message = await client.run_turn(
                    "owner-a",
                    "sess-1",
                    "research AAPL",
                    attachments=[{"source_path": "/tmp/a.pdf", "parsed_path": "/tmp/a.md"}],
                    additional_context="ctx-block",
                    runtime_context={"example.runtime": "opaque-value"},
                    input_metadata={
                        "background_input": {"input_id": "input", "source": "background"}
                    },
                )
            finally:
                await client.aclose()
            return fake.received, message

    received, message = asyncio.run(_run())

    # Optional request correlation does not modify UserMessage/history.
    assert isinstance(received[0].pop("execution_request_id"), str)
    # Outbound payload shape (the kernel's _parse_user_message contract).
    assert received == [
        {
            "message": {
                "text": "research AAPL",
                "attachments": [{"source_path": "/tmp/a.pdf", "parsed_path": "/tmp/a.md"}],
                "additional_context": "ctx-block",
                "metadata": {"background_input": {"input_id": "input", "source": "background"}},
            },
            "runtime_context": {"example.runtime": "opaque-value"},
        }
    ]
    assert isinstance(message, MessageData)
    assert message.assistant_message == "done"


def test_run_turn_treats_session_error_as_terminal() -> None:
    async def _run():
        frames = [
            {
                "type": "session_error",
                "data": {"message": "boom", "message_id": "m-1"},
                "timestamp": 1,
            }
        ]
        async with _FakeKernelWs(frames) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}", token="tok")

            async def _fake_get_message(user_id, message_id: str):
                assert user_id == "owner-a" and message_id == "m-1"
                return _fake_message("sess-1")

            client.get_message = _fake_get_message  # type: ignore[method-assign]
            try:
                return await client.run_turn("owner-a", "sess-1", "hi")
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
                await client.run_turn("owner-a", "missing", "hi")
            finally:
                await client.aclose()

    with pytest.raises(KernelClientError) as excinfo:
        asyncio.run(_run())
    assert "Session not found" in str(excinfo.value)


def test_run_turn_disables_keepalive_ping_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run channel must connect with ``ping_timeout=None``.

    A whole agent turn rides this socket and can legitimately go quiet for
    long stretches; the websockets default 20s pong timeout would close a
    healthy-but-busy turn with 1011 (``keepalive ping timeout``). Pin the
    connect kwargs so that regression can't silently return.
    """
    captured: dict[str, Any] = {}
    real_connect = websockets.connect

    def _spy_connect(*args: Any, **kwargs: Any):
        captured.update(kwargs)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(websockets, "connect", _spy_connect)

    async def _run():
        frames = [
            {
                "type": "session_idle",
                "data": {"stop_reason": "end_turn", "message_id": "m-1"},
                "timestamp": 1,
            }
        ]
        async with _FakeKernelWs(frames) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}", token="tok")

            async def _fake_get_message(user_id, message_id: str):
                assert user_id == "owner-a" and message_id == "m-1"
                return _fake_message("sess-1")

            client.get_message = _fake_get_message  # type: ignore[method-assign]
            try:
                await client.run_turn("owner-a", "sess-1", "hi")
            finally:
                await client.aclose()

    asyncio.run(_run())

    assert captured.get("ping_timeout") is None
    assert captured.get("ping_interval") == 20


def test_run_turn_maps_dropped_channel_to_unavailable() -> None:
    async def _run():
        # Server sends nothing and closes immediately → mid-turn drop.
        async with _FakeKernelWs([]) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}", token="tok")
            try:
                await client.run_turn("owner-a", "sess-1", "hi")
            finally:
                await client.aclose()

    with pytest.raises(KernelUnavailableError):
        asyncio.run(_run())


def test_run_turn_reads_exact_execution_message_not_a_concurrent_next_turn() -> None:
    async def run():
        async with _FakeKernelWs([{"type": "session_idle", "data": {"message_id": "m-1"}}]) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}")

            async def exact(owner, message_id):
                assert owner == "owner-a" and message_id == "m-1"
                return _fake_message("sess-1").model_copy(update={"ended_at": 1})

            async def latest(*args, **kwargs):
                return [
                    _fake_message("sess-1").model_copy(
                        update={"id": "next-turn", "assistant_message": "unrelated next turn"}
                    )
                ]

            client.get_message = exact
            client.list_messages = latest
            try:
                return await client.run_turn("owner-a", "sess-1", "synthetic input")
            finally:
                await client.aclose()

    result = asyncio.run(run())
    assert result.id == "m-1" and result.assistant_message == "done"


@pytest.mark.parametrize(
    "patch",
    [
        None,
        {"id": "other"},
        {"session_id": "foreign-session"},
        {"status": "running"},
        {"ended_at": None},
    ],
)
def test_run_turn_rejects_unfinalized_or_mismatched_exact_readback(patch) -> None:
    async def run():
        async with _FakeKernelWs([{"type": "session_idle", "data": {"message_id": "m-1"}}]) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}")

            async def exact(owner, message_id):
                assert owner == "owner-a" and message_id == "m-1"
                return None if patch is None else _fake_message("sess-1").model_copy(update=patch)

            client.get_message = exact
            try:
                await client.run_turn("owner-a", "sess-1", "synthetic input")
            finally:
                await client.aclose()

    with pytest.raises(KernelClientError, match="not durably finalized"):
        asyncio.run(run())


def test_run_turn_rejects_terminal_without_execution_message_identity() -> None:
    async def run():
        async with _FakeKernelWs(
            [{"type": "session_error", "data": {"message": "failed"}}]
        ) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}")
            try:
                await client.run_turn("owner-a", "sess-1", "synthetic input")
            finally:
                await client.aclose()

    with pytest.raises(KernelClientError, match="no execution message id"):
        asyncio.run(run())


def test_run_turn_ignores_completed_replay_from_a_prior_execution() -> None:
    async def run():
        frames = [
            {"type": "session_idle", "data": {"message_id": "prior-execution"}},
            {"type": "run_result", "data": {"message_id": "m-1"}},
        ]
        async with _FakeKernelWs(frames) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}")

            async def exact(owner, message_id):
                assert owner == "owner-a"
                return _fake_message("sess-1").model_copy(update={"id": message_id})

            client.get_message = exact
            try:
                return await client.run_turn("owner-a", "sess-1", "synthetic new input")
            finally:
                await client.aclose()

    assert asyncio.run(run()).id == "m-1"


def test_run_turn_rejects_old_kernel_before_sending_input() -> None:
    async def run():
        received = []

        async def old_kernel(ws):
            await ws.send(json.dumps({"type": "session_idle", "data": {"message_id": "old"}}))
            try:
                received.append(await ws.recv())
            except websockets.exceptions.ConnectionClosed:
                pass

        async with websockets.serve(old_kernel, "127.0.0.1", 0) as server:
            client = HttpKernelClient(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}")
            try:
                with pytest.raises(KernelClientError) as exc:
                    await client.run_turn("owner", "main", "never submitted")
                assert exc.value.status == 501
                assert received == []
            finally:
                await client.aclose()

    asyncio.run(run())


def test_run_turn_rejects_silent_old_kernel_before_sending_input(monkeypatch) -> None:
    monkeypatch.setattr(
        "valuz_agent.adapters.kernel_client_http._CAPABILITY_HANDSHAKE_TIMEOUT_SECONDS", 0.01
    )

    async def run():
        received = []

        async def old_kernel(ws):
            try:
                received.append(await ws.recv())
            except websockets.exceptions.ConnectionClosed:
                pass

        async with websockets.serve(old_kernel, "127.0.0.1", 0) as server:
            client = HttpKernelClient(f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}")
            try:
                with pytest.raises(KernelClientError) as exc:
                    await client.run_turn("owner", "main", "never submitted")
                assert exc.value.status == 501
                assert received == []
            finally:
                await client.aclose()

    asyncio.run(run())


def test_run_turn_rejects_unknown_request_result() -> None:
    async def run():
        async with _FakeKernelWs(
            [
                {
                    "type": "run_result",
                    "data": {"execution_request_id": "foreign-request", "message_id": "m-1"},
                }
            ]
        ) as fake:
            client = HttpKernelClient(f"http://127.0.0.1:{fake.port}")
            try:
                await client.run_turn("owner", "main", "synthetic input")
            finally:
                await client.aclose()

    with pytest.raises(KernelClientError, match="another execution request"):
        asyncio.run(run())
