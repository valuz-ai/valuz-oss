"""UI bus pushes reach other backend processes through ``ext.ui_push_transport``."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from valuz_agent.modules.plugin_ui.push import UiPushHub
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.ui_push_transport import LocalUiPushTransport


class _RecordingTransport:
    def __init__(self) -> None:
        self.published: list[Mapping[str, Any]] = []

    async def start(self, deliver: Any) -> None:
        del deliver

    async def publish(self, message: Mapping[str, Any]) -> None:
        self.published.append(dict(message))

    async def stop(self) -> None:
        return None


@pytest.fixture
def transport() -> Iterator[_RecordingTransport]:
    previous = ext.ui_push_transport
    bound = _RecordingTransport()
    ext.ui_push_transport = bound
    try:
        yield bound
    finally:
        ext.ui_push_transport = previous


async def _first(hub: UiPushHub, user: str, session: str | None) -> Any:
    stream = hub.subscribe(user, session)
    task = asyncio.ensure_future(stream.__anext__())
    await asyncio.sleep(0)
    return stream, task


async def test_oss_default_is_local_and_publishes_nothing() -> None:
    assert isinstance(ext.ui_push_transport, LocalUiPushTransport)
    hub = UiPushHub()
    assert hub.push("u1", "toast", {"text": "hi"}) == 0


async def test_a_push_is_delivered_here_and_published(transport: _RecordingTransport) -> None:
    hub = UiPushHub()
    stream, task = await _first(hub, "u1", "s1")
    assert hub.push("u1", "status", {"text": "syncing"}, session_id="s1") == 1
    message = await asyncio.wait_for(task, 1)
    assert message.payload == {"text": "syncing"}
    await asyncio.sleep(0)
    assert len(transport.published) == 1
    wire = transport.published[0]
    assert wire["origin"] == hub.origin
    assert wire["kind"] == "status" and wire["session_id"] == "s1"
    await stream.aclose()


async def test_a_push_from_another_process_reaches_local_streams() -> None:
    producer, consumer = UiPushHub(), UiPushHub()
    stream, task = await _first(consumer, "u1", None)
    from valuz_agent.modules.plugin_ui.push import UiPush

    wire = UiPush(kind="toast", user_id="u1", session_id=None, payload={"text": "x"}).to_wire(
        producer.origin
    )
    assert consumer.deliver_remote(wire) == 1
    message = await asyncio.wait_for(task, 1)
    assert message.payload["text"] == "x" and message.push_id == wire["push_id"]
    await stream.aclose()


async def test_an_echo_of_our_own_push_is_ignored() -> None:
    hub = UiPushHub()
    from valuz_agent.modules.plugin_ui.push import UiPush

    wire = UiPush(kind="toast", user_id="u1", session_id=None, payload={}).to_wire(hub.origin)
    assert hub.deliver_remote(wire) == 0


async def test_malformed_or_unknown_remote_pushes_are_dropped() -> None:
    hub = UiPushHub()
    assert hub.deliver_remote({"origin": "other"}) == 0
    assert (
        hub.deliver_remote(
            {"origin": "other", "kind": "bogus", "user_id": "u", "push_id": "p", "payload": {}}
        )
        == 0
    )
