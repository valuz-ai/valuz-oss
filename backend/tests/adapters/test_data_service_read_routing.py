"""Host read-routing — remote tier reads history from the DataService.

The SaaS sandbox is ephemeral, so in ``remote`` mode the host reads event
HISTORY straight from the central DataService (not via the maybe-dead sandbox
kernel). Covers the host ``DataServiceReadClient`` row→EventData mapping and the
``event_sse_adapter`` reader routing. No network — httpx ``MockTransport``.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede app.* (sys.path)
from __future__ import annotations

import json

import httpx
import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for app.*

from valuz_agent.adapters import event_sse_adapter as sse
from valuz_agent.adapters.data_service_client import DataServiceReadClient


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ds")


async def test_client_maps_get_events_after_rows():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "seq": 59,
                        "session_id": "s1",
                        "message_id": "m1",
                        "type": "user_message",
                        "data": {"text": "hi"},
                        "timestamp": 123,
                    }
                ]
            },
        )

    client = DataServiceReadClient(
        base_url="http://ds", token="jwt-A", http_client=_mock_client(handler)
    )
    try:
        events = await client.get_events("u", "s1", after_seq=0, limit=200)
    finally:
        await client.aclose()

    assert seen["path"] == "/rpc/get_events_after"
    assert seen["auth"] == "Bearer jwt-A"
    assert seen["body"] == {"session_id": "s1", "after_seq": 0, "limit": 200}
    assert len(events) == 1
    e = events[0]
    assert e.seq == 59 and e.type == "user_message" and e.data == {"text": "hi"}
    assert e.message_id == "m1" and e.timestamp == 123


async def test_client_maps_get_events_window():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "events": [
                        {
                            "seq": 1,
                            "session_id": "s",
                            "message_id": "m",
                            "type": "thinking",
                            "data": {},
                            "timestamp": 1,
                        }
                    ],
                    "has_more": True,
                }
            },
        )

    client = DataServiceReadClient(
        base_url="http://ds", token="t", http_client=_mock_client(handler)
    )
    try:
        window = await client.get_events_window("u", "s", before_seq=None, turn_limit=20)
    finally:
        await client.aclose()
    assert window.has_more is True
    assert len(window.items) == 1 and window.items[0].seq == 1


async def test_client_reads_message_token_buckets_for_session_rollup():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "m1",
                        "input_tokens": 12,
                        "output_tokens": 3,
                        "cache_read_tokens": 40,
                        "cache_write_tokens": 2,
                    }
                ]
            },
        )

    client = DataServiceReadClient(
        base_url="http://ds", token="t", http_client=_mock_client(handler)
    )
    try:
        messages = await client.list_messages("u", "s1", limit=200, offset=400)
    finally:
        await client.aclose()

    assert seen["path"] == "/rpc/list_messages_for_session"
    assert seen["body"] == {"session_id": "s1", "limit": 200, "offset": 400}
    assert messages == [
        {
            "input_tokens": 12,
            "output_tokens": 3,
            "cache_read_tokens": 40,
            "cache_write_tokens": 2,
        }
    ]


# ── reader routing: reads are unified through the in-process DataService ──
# (sandbox-agnostic — no "is the sandbox alive?" branch). The host reads its
# bound DataService store directly; in local mode it falls back to the kernel.

from src.core.store_port import StoredEvent  # noqa: E402

from valuz_agent.adapters.data_reader import (  # noqa: E402
    _KernelClientReader,
    bind_data_reader,
    data_reader,
)
from valuz_agent.adapters.data_service_local import LocalDataServiceReader  # noqa: E402


class _FakeStore:
    """Minimal StorePort read surface for the local-reader routing tests."""

    async def get_events_after(self, user_id, session_id, *, after_seq, limit):
        return [
            StoredEvent(
                seq=5,
                session_id=session_id,
                message_id="m",
                type="user_message",
                data={"text": "yo"},
                timestamp=9,
            )
        ]

    async def get_events_window(self, user_id, session_id, *, before_seq=None, turn_limit=20):
        return ([], False)


@pytest.fixture
def reset_reader():
    bind_data_reader(None)
    yield
    bind_data_reader(None)


def test_local_mode_reads_via_kernel(reset_reader):
    # No DataReader bound → reads go through the kernel-seam default.
    assert isinstance(data_reader(), _KernelClientReader)


def test_durable_mode_reads_via_local_data_service(reset_reader):
    # A bound in-process DataService reader → reads go straight to it.
    bind_data_reader(LocalDataServiceReader(_FakeStore()))  # type: ignore[arg-type]
    assert isinstance(sse._history_reader(), LocalDataServiceReader)


async def test_list_events_after_reads_from_data_service(reset_reader):
    """End-to-end: with a DataReader bound, list_events_after reads + translates
    frames straight from it — the kernel seam is never touched (sandbox-agnostic)."""
    from valuz_agent.infra.auth_context import reset_current_user_id, set_current_user_id

    bind_data_reader(LocalDataServiceReader(_FakeStore()))  # type: ignore[arg-type]
    tok = set_current_user_id("u")
    try:
        frames = await sse.list_events_after("s", after_seq=0, limit=10)
    finally:
        reset_current_user_id(tok)
    assert len(frames) == 1
    assert frames[0].seq == 5
