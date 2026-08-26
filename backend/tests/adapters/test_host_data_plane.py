"""Host data plane — non-runtime facades on the durable; control writes live-first.

Pins the two planes of the ``kernel_client`` module facade:

- with ``bind_host_data_store`` bound (boot's ``bind_data_service``), NON-RUNTIME
  reads and the stranded reset run kernel route semantics against the DURABLE
  store — never against the process kernel's runtime store;
- CONTROL writes route to the session's LIVE execution kernel when one exists
  (runtime authority — its dual-write mirrors down), and fall back to the
  durable data plane only when the scope has no live kernel;
- unbound (bare embedding / unit tests) everything degrades to the process
  client — OSS single-process behavior unchanged.
"""

# ruff: noqa: I001 — boot.kernel side-effect import MUST precede src.*/app.* (sys.path)
from __future__ import annotations

import uuid

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.adapters.sqlalchemy_store.models import Base
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.types import Message, Session, UserMessage

from valuz_agent.adapters import kernel_client
from valuz_agent.ports.extensions import ext


async def _mk_store(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return SQLAlchemyStore(async_sessionmaker(engine, expire_on_commit=False)), engine


def _sess(sid: str, owner: str, cwd: str, status: str = "idle") -> Session:
    return Session(
        id=sid,
        user_id=owner,
        agent_config=AgentConfig(id="a", name="a", model="claude-sonnet-4-6"),
        cwd=cwd,
        status=status,
    )


@pytest.fixture
async def durable(tmp_path):
    store, engine = await _mk_store(tmp_path / "durable.db")
    kernel_client.bind_host_data_store(lambda: store)
    yield store
    kernel_client.bind_host_data_store(None)
    await engine.dispose()


async def test_reads_route_to_bound_durable(durable, tmp_path):
    sid = uuid.uuid4().hex
    await durable.save_session(_sess(sid, "u", str(tmp_path)))
    # The facade read serves the durable row without touching the process
    # kernel (whose dependencies are not even initialized in this test).
    got = await kernel_client.get_session("u", sid)
    assert got is not None and got.id == sid
    assert [s.id for s in await kernel_client.list_sessions("u")] == [sid]


async def test_reset_stranded_applies_kernel_semantics_to_durable(durable, tmp_path):
    sid = uuid.uuid4().hex
    await durable.save_session(_sess(sid, "u", str(tmp_path), status="running"))
    mid = uuid.uuid4().hex
    await durable.save_message(
        "u",
        Message(
            id=mid,
            session_id=sid,
            user_message=UserMessage(text="hi"),
            started_at=0,
            status="running",
        ),
    )
    assert await kernel_client.reset_stranded_session("u", sid) is True
    after = await durable.load_session("u", sid)
    assert after is not None and after.status == "idle"
    assert after.stop_reason is not None and after.stop_reason.category == "host_restart"
    (msg,) = await durable.list_messages_for_session("u", sid)
    assert msg.status == "errored"
    # Idempotent: an already-reset session is a no-op.
    assert await kernel_client.reset_stranded_session("u", sid) is False


async def test_control_write_falls_back_to_durable_when_no_live_kernel(
    durable, tmp_path, monkeypatch
):
    """Scoped allocator, sandbox gone (peek → None) → the write lands on the
    durable via the data plane instead of provisioning anything."""

    class _DeadAllocator:
        async def ensure(self, *, owner_user_id, scope=None, new_turn=False):  # noqa: ANN001
            raise AssertionError("control write must never provision")

        async def peek(self, *, owner_user_id, scope=None):  # noqa: ANN001
            return None  # no live sandbox for any scope

    monkeypatch.setattr(ext, "sandbox_allocator", _DeadAllocator(), raising=False)
    sid = uuid.uuid4().hex
    await durable.save_session(_sess(sid, "u", str(tmp_path)))
    assert await kernel_client.delete_session("u", sid) is True
    assert await durable.load_session("u", sid) is None


async def test_control_write_prefers_live_kernel(durable, tmp_path, monkeypatch):
    """A live scope kernel (peek → endpoint) receives the control write — the
    durable is NOT written directly (the live kernel's mirror owns that)."""
    from valuz_agent.ports.sandbox_allocator import SandboxLease
    from valuz_agent.ports.sandbox_provider import SandboxEndpoint

    class _LiveAllocator:
        async def peek(self, *, owner_user_id, scope=None):  # noqa: ANN001
            return SandboxLease(
                endpoint=SandboxEndpoint(sandbox_id="sb", base_url="https://sb.pool", token="t")
            )

    calls: list[tuple[str, str]] = []

    class _FakeHttpKernel:
        async def delete_session(self, user_id, session_id):  # noqa: ANN001
            calls.append((user_id, session_id))
            return True

    monkeypatch.setattr(ext, "sandbox_allocator", _LiveAllocator(), raising=False)
    monkeypatch.setitem(kernel_client._endpoint_clients, "https://sb.pool", _FakeHttpKernel())
    sid = uuid.uuid4().hex
    await durable.save_session(_sess(sid, "u", str(tmp_path)))
    assert await kernel_client.delete_session("u", sid) is True
    assert calls == [("u", sid)]
    assert await durable.load_session("u", sid) is not None  # untouched here


async def test_unbound_data_plane_degrades_to_process_client(monkeypatch):
    kernel_client.bind_host_data_store(None)
    sentinel = object()

    class _FakeClient:
        async def get_session(self, user_id, session_id):  # noqa: ANN001
            return sentinel

    monkeypatch.setattr(kernel_client, "client", _FakeClient())
    assert await kernel_client.get_session("u", "s") is sentinel
