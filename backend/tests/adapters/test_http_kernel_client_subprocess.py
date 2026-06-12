"""HttpKernelClient ↔ standalone kernel subprocess — end-to-end smoke.

The minimal form of kernel independent deployment: the kernel app runs
as a bare uvicorn subprocess on localhost with its own SQLite file and a
bearer token; the host side talks to it exclusively through
``HttpKernelClient``. Every REST surface in the seam contract gets a
round-trip; the SSE subscription is exercised live; auth is verified to
reject tokenless callers.

``run_turn`` (the WS channel) is not driven here — it needs a real
runtime + model. Its auth gate is still covered (tokenless connect is
closed with 4401).
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import uuid

import httpx
import pytest

import valuz_agent.boot.kernel as kb

from app.schemas import (  # type: ignore[import-not-found]
    AgentConfigSchema,
    CreateSessionRequest,
    EventPayload,
    FinalizeSessionRequest,
    UpdateSessionRequest,
)

from valuz_agent.adapters.kernel_client import KernelClientError
from valuz_agent.adapters.kernel_client_http import HttpKernelClient

TOKEN = "test-kernel-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def kernel_proc(tmp_path_factory):
    """Migrate a private SQLite file, then run the kernel app on it as a
    real subprocess (the provisioning shape a SandboxProvider performs)."""
    tmp_path = tmp_path_factory.mktemp("kernel-subproc")
    db_path = tmp_path / "kernel.db"

    # Provision step 1 — migrate the kernel chain into the private file.
    # ``boot.kernel`` binds settings + stamps DATABASE_URL at import, so
    # the override must go through VALUZ_KERNEL_DATABASE_URL and a module
    # re-import (same mechanics as the DB-separation probe), with both
    # env vars restored afterwards so later tests see the defaults.
    reimport_prefixes = ("valuz_agent.infra.config", "valuz_agent.boot.kernel")
    saved_modules = {
        name: mod for name, mod in sys.modules.items() if name.startswith(reimport_prefixes)
    }
    previous_kernel_url = os.environ.get("VALUZ_KERNEL_DATABASE_URL")
    previous_db_url = os.environ.get("DATABASE_URL")
    os.environ["VALUZ_KERNEL_DATABASE_URL"] = f"sqlite:///{db_path}"
    try:
        for name in saved_modules:
            sys.modules.pop(name, None)
        import valuz_agent.boot.kernel as kb_fresh

        kb_fresh.run_kernel_migrations()
    finally:
        if previous_kernel_url is None:
            os.environ.pop("VALUZ_KERNEL_DATABASE_URL", None)
        else:
            os.environ["VALUZ_KERNEL_DATABASE_URL"] = previous_kernel_url
        if previous_db_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_db_url
        # Restore the ORIGINAL module objects — later tests monkeypatch
        # module attributes and must hit the objects already-imported
        # call sites hold, not fresh re-imports.
        for name in [n for n in sys.modules if n.startswith(reimport_prefixes)]:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)

    # Provision step 2 — spawn the kernel server.
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "KERNEL_AUTH_TOKEN": TOKEN,
            "PYTHONPATH": str(kb.KERNEL_DIR),
        }
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=str(kb.KERNEL_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    base_url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError(f"kernel subprocess exited early:\n{out}")
        try:
            if httpx.get(f"{base_url}/health", timeout=1.0).status_code == 200:
                break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(0.2)
    else:
        proc.terminate()
        raise RuntimeError(f"kernel subprocess never became healthy: {last_exc}")

    yield base_url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_rest_round_trip_against_standalone_kernel(kernel_proc) -> None:
    client = HttpKernelClient(kernel_proc, token=TOKEN)
    try:
        session_id = str(uuid.uuid4())
        created = await client.create_session(
            CreateSessionRequest(
                id=session_id,
                agent_config=AgentConfigSchema(name="probe-agent"),
                cwd="/tmp/probe-cwd",
                runtime_provider="claude_agent",
                metadata={"valuz": {"name": "subproc-probe"}},
            )
        )
        assert created.id == session_id

        loaded = await client.get_session(session_id)
        assert loaded is not None
        assert loaded.metadata["valuz"]["name"] == "subproc-probe"
        assert await client.get_session("no-such-session") is None

        listed = await client.list_sessions(ids=[session_id])
        assert [s.id for s in listed] == [session_id]

        updated = await client.update_session(
            session_id, UpdateSessionRequest(metadata={"valuz": {"name": "renamed"}})
        )
        assert updated.metadata["valuz"]["name"] == "renamed"

        finalized = await client.finalize_session(
            session_id, FinalizeSessionRequest(status="idle")
        )
        assert finalized.status == "idle"

        # No message rows yet → out-of-band append reports not-persisted.
        assert (
            await client.append_event(session_id, EventPayload(type="session_error", data={}))
            is False
        )
        await client.emit_live_event(session_id, "session_error", {"category": "Probe"})

        assert await client.get_events(session_id, after_seq=0) == []
        window = await client.get_events_window(session_id, turn_limit=5)
        assert window.items == [] and window.has_more is False
        assert await client.usage_rollup(0, 4_102_444_800_000) == []
        assert await client.list_messages(session_id) == []

        # Interrupt on an idle session is a silent no-op (in-process parity).
        await client.interrupt(session_id)

        assert await client.delete_session(session_id) is True
        assert await client.delete_session(session_id) is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sse_subscription_delivers_live_events(kernel_proc) -> None:
    client = HttpKernelClient(kernel_proc, token=TOKEN)
    try:
        session_id = str(uuid.uuid4())
        await client.create_session(
            CreateSessionRequest(
                id=session_id,
                agent_config=AgentConfigSchema(name="probe-agent"),
                cwd="/tmp/probe-cwd",
                runtime_provider="claude_agent",
            )
        )

        received: asyncio.Queue = asyncio.Queue()

        async def _follow() -> None:
            async for item in client.subscribe_session_events(session_id):
                await received.put(item)
                return

        follower = asyncio.create_task(_follow())
        try:
            # Give the stream a moment to attach its tap, then emit.
            await asyncio.sleep(0.5)
            await client.emit_live_event(session_id, "session_error", {"category": "Live"})
            frame = await asyncio.wait_for(received.get(), timeout=10)
            assert frame.type == "session_error"
            assert frame.data["category"] == "Live"
            assert frame.seq is None  # live frame, not a DB row
        finally:
            follower.cancel()
            try:
                await follower
            except asyncio.CancelledError:
                pass
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_tokenless_requests_are_rejected(kernel_proc) -> None:
    bare = HttpKernelClient(kernel_proc, token=None)
    try:
        with pytest.raises(KernelClientError) as excinfo:
            await bare.list_sessions()
        assert excinfo.value.status == 401
    finally:
        await bare.aclose()

    # Health stays open for supervisors.
    assert httpx.get(f"{kernel_proc}/health", timeout=5.0).status_code == 200


@pytest.mark.asyncio
async def test_tokenless_ws_run_channel_is_rejected(kernel_proc) -> None:
    import websockets

    ws_url = kernel_proc.replace("http://", "ws://", 1) + f"/api/v1/sessions/{uuid.uuid4()}/run"
    async with websockets.connect(ws_url, max_size=None) as ws:
        with pytest.raises(websockets.exceptions.ConnectionClosed) as excinfo:
            await ws.recv()
    assert excinfo.value.rcvd is not None and excinfo.value.rcvd.code == 4401


def test_http_client_covers_the_full_protocol_surface() -> None:
    """Method-for-method parity with the contract table (minus the declared
    in-process-only supervision hooks)."""
    from tests.adapters.test_kernel_client_contract import (
        EXPECTED_ROUTES,
        EXPECTED_STREAMS,
    )

    in_process_only = {"scan_orphan_pendings", "scan_orphan_runs", "cleanup_runtime"}
    for name in (set(EXPECTED_ROUTES) | set(EXPECTED_STREAMS)) - in_process_only:
        assert hasattr(HttpKernelClient, name), f"HttpKernelClient lacks {name}"


@pytest.mark.asyncio
async def test_global_sse_stream_delivers_events_with_session_ids(kernel_proc) -> None:
    """``subscribe_all_events`` carries every session's live events with
    ``session_id`` stamped on each frame (the DecisionAggregator contract)."""
    client = HttpKernelClient(kernel_proc, token=TOKEN)
    try:
        sids = []
        for _ in range(2):
            sid = str(uuid.uuid4())
            sids.append(sid)
            await client.create_session(
                CreateSessionRequest(
                    id=sid,
                    agent_config=AgentConfigSchema(name="probe-agent"),
                    cwd="/tmp/probe-cwd",
                    runtime_provider="claude_agent",
                )
            )

        received: asyncio.Queue = asyncio.Queue()

        async def _follow() -> None:
            async for item in client.subscribe_all_events():
                await received.put(item)
                if received.qsize() >= 2:
                    return

        follower = asyncio.create_task(_follow())
        try:
            await asyncio.sleep(0.5)
            await client.emit_live_event(sids[0], "session_error", {"category": "G1"})
            await client.emit_live_event(sids[1], "requires_action", {"pending_id": "p9"})

            frames = [await asyncio.wait_for(received.get(), timeout=10) for _ in range(2)]
        finally:
            follower.cancel()
            try:
                await follower
            except asyncio.CancelledError:
                pass

        by_session = {f.session_id: f for f in frames}
        assert set(by_session) == set(sids)
        assert by_session[sids[0]].type == "session_error"
        assert by_session[sids[0]].data["category"] == "G1"
        assert by_session[sids[1]].type == "requires_action"
        assert by_session[sids[1]].data["pending_id"] == "p9"
    finally:
        await client.aclose()
