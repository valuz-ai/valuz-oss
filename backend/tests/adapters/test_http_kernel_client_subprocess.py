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
import re
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


def _migrate_kernel_db(db_path) -> None:
    """Migrate the kernel chain into a private SQLite file.

    ``boot.kernel`` binds settings + stamps DATABASE_URL at import, so the
    override must go through VALUZ_KERNEL_DATABASE_URL and a module
    re-import (same mechanics as the DB-separation probe), with env vars
    AND the original module objects restored afterwards — later tests
    monkeypatch module attributes and must hit the objects
    already-imported call sites hold.
    """
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
        for name in [n for n in sys.modules if n.startswith(reimport_prefixes)]:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)


@pytest.fixture(scope="module")
def kernel_proc(tmp_path_factory):
    """Migrate a private SQLite file, then run the kernel app on it as a
    real subprocess (the provisioning shape a SandboxProvider performs)."""
    tmp_path = tmp_path_factory.mktemp("kernel-subproc")
    db_path = tmp_path / "kernel.db"

    _migrate_kernel_db(db_path)

    proc = _spawn_kernel(db_path, extra_env={"KERNEL_AUTH_TOKEN": TOKEN})
    state, port = _wait_exit_or_healthy(proc)
    if state != "healthy":
        raise RuntimeError(f"kernel subprocess exited early:\n{port}")

    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.asyncio
async def test_rest_round_trip_against_standalone_kernel(kernel_proc) -> None:
    client = HttpKernelClient(kernel_proc, token=TOKEN)
    owner = "owner-a"
    try:
        session_id = str(uuid.uuid4())
        created = await client.create_session(
            owner,
            CreateSessionRequest(
                id=session_id,
                agent_config=AgentConfigSchema(name="probe-agent"),
                cwd="/tmp/probe-cwd",
                runtime_provider="claude_agent",
                metadata={"valuz": {"name": "subproc-probe"}},
            ),
        )
        assert created.id == session_id

        loaded = await client.get_session(owner, session_id)
        assert loaded is not None
        assert loaded.metadata["valuz"]["name"] == "subproc-probe"
        assert await client.get_session(owner, "no-such-session") is None
        # Owner isolation: a different owner cannot see owner-a's session.
        assert await client.get_session("owner-b", session_id) is None
        assert await client.list_sessions("owner-b", ids=[session_id]) == []

        listed = await client.list_sessions(owner, ids=[session_id])
        assert [s.id for s in listed] == [session_id]

        updated = await client.update_session(
            owner, session_id, UpdateSessionRequest(metadata={"valuz": {"name": "renamed"}})
        )
        assert updated.metadata["valuz"]["name"] == "renamed"

        finalized = await client.finalize_session(
            owner, session_id, FinalizeSessionRequest(status="idle")
        )
        assert finalized.status == "idle"

        # No message rows yet → out-of-band append reports not-persisted.
        assert (
            await client.append_event(
                owner, session_id, EventPayload(type="session_error", data={})
            )
            is False
        )
        await client.emit_live_event(owner, session_id, "session_error", {"category": "Probe"})

        assert await client.get_events(owner, session_id, after_seq=0) == []
        window = await client.get_events_window(owner, session_id, turn_limit=5)
        assert window.items == [] and window.has_more is False
        assert await client.usage_rollup(owner, 0, 4_102_444_800_000) == []
        assert await client.list_messages(owner, session_id) == []

        # Interrupt on an idle session is a silent no-op (in-process parity).
        await client.interrupt(owner, session_id)

        # owner-b can't delete owner-a's session.
        assert await client.delete_session("owner-b", session_id) is False
        assert await client.delete_session(owner, session_id) is True
        assert await client.delete_session(owner, session_id) is False
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_sse_subscription_delivers_live_events(kernel_proc) -> None:
    client = HttpKernelClient(kernel_proc, token=TOKEN)
    owner = "owner-a"
    try:
        session_id = str(uuid.uuid4())
        await client.create_session(
            owner,
            CreateSessionRequest(
                id=session_id,
                agent_config=AgentConfigSchema(name="probe-agent"),
                cwd="/tmp/probe-cwd",
                runtime_provider="claude_agent",
            ),
        )

        received: asyncio.Queue = asyncio.Queue()

        async def _follow() -> None:
            async for item in client.subscribe_session_events(owner, session_id):
                await received.put(item)
                return

        follower = asyncio.create_task(_follow())
        try:
            # Emit on a retry loop until the stream observes a frame —
            # robust to subscription-attach latency without a fixed sleep.
            async def _emit_until_received() -> None:
                while received.empty():
                    await client.emit_live_event(
                        owner, session_id, "session_error", {"category": "Live"}
                    )
                    await asyncio.sleep(0.1)

            await asyncio.wait_for(_emit_until_received(), timeout=10)
            frame = await received.get()
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
            await bare.list_sessions("owner-a")
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
    owner = "owner-a"
    try:
        sids = []
        for _ in range(2):
            sid = str(uuid.uuid4())
            sids.append(sid)
            await client.create_session(
                owner,
                CreateSessionRequest(
                    id=sid,
                    agent_config=AgentConfigSchema(name="probe-agent"),
                    cwd="/tmp/probe-cwd",
                    runtime_provider="claude_agent",
                ),
            )

        received: asyncio.Queue = asyncio.Queue()

        seen_sessions: set = set()

        async def _follow() -> None:
            async for item in client.subscribe_all_events():
                # The first emit retries until observed — keep one frame
                # per session so repeats don't crowd out the second one.
                if item.session_id in seen_sessions:
                    continue
                seen_sessions.add(item.session_id)
                await received.put(item)
                if len(seen_sessions) >= 2:
                    return

        follower = asyncio.create_task(_follow())
        try:
            # Emit on a retry loop until the follower observes a frame —
            # robust to subscription-attach latency without a fixed sleep.
            async def _emit_until_received() -> None:
                while received.empty():
                    await client.emit_live_event(
                        owner, sids[0], "session_error", {"category": "G1"}
                    )
                    await asyncio.sleep(0.1)

            await asyncio.wait_for(_emit_until_received(), timeout=10)
            await client.emit_live_event(owner, sids[1], "requires_action", {"pending_id": "p9"})

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


def _spawn_kernel(db_path, extra_env):
    """Spawn the kernel app on an OS-assigned port (``--port 0``) — the
    bound port is read back from uvicorn's startup line, so there is no
    allocate-then-rebind TOCTOU window."""
    env = dict(os.environ)
    # The auth posture under test must come from ``extra_env`` alone — a
    # token inherited from the parent environment would defeat the
    # no-token cases.
    env.pop("KERNEL_AUTH_TOKEN", None)
    env.pop("KERNEL_ALLOW_UNAUTHENTICATED", None)
    env.update(
        {
            "DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
            "PYTHONPATH": str(kb.KERNEL_DIR),
            **extra_env,
        }
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--log-level",
            "info",  # the bind line is INFO-level — it carries the port
        ],
        cwd=str(kb.KERNEL_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


_BIND_LINE = re.compile(r"Uvicorn running on http://127\.0\.0\.1:(\d+)")


def _wait_exit_or_healthy(proc, deadline_s=30):
    """Read stdout until the bind line or EOF.

    Returns ``('healthy', port)`` once /health responds, or
    ``('exited', output)`` when the process dies first (the fail-fast
    cases). Blocking ``readline`` is safe: uvicorn either prints the bind
    line or exits (EOF).
    """
    lines: list[str] = []
    while True:
        raw = proc.stdout.readline()
        if not raw:
            proc.wait(timeout=10)
            return "exited", "".join(lines)
        text = raw.decode(errors="replace")
        lines.append(text)
        m = _BIND_LINE.search(text)
        if m:
            port = int(m.group(1))
            deadline = time.monotonic() + deadline_s
            while time.monotonic() < deadline:
                try:
                    if httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0).status_code == 200:
                        return "healthy", port
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(0.1)
            proc.kill()
            raise RuntimeError("kernel bound a port but never became healthy")


def test_standalone_kernel_refuses_to_start_without_auth_token(tmp_path):
    """PR #87 review: the fail-fast gate itself had zero coverage."""
    proc = _spawn_kernel(tmp_path / "k.db", extra_env={})
    state, output = _wait_exit_or_healthy(proc)
    assert state == "exited"
    assert "refuses to start without auth" in output


def test_unauthenticated_optin_requires_loopback_bind(tmp_path):
    """KERNEL_ALLOW_UNAUTHENTICATED=1 with a non-loopback bind must die —
    AppConfig.host defaults to 0.0.0.0, so the opt-in alone must never
    expose the surface on all interfaces."""
    proc = _spawn_kernel(
        tmp_path / "k.db",
        extra_env={"KERNEL_ALLOW_UNAUTHENTICATED": "1", "HOST": "0.0.0.0"},
    )
    state, output = _wait_exit_or_healthy(proc)
    assert state == "exited"
    assert "requires a loopback bind" in output


def test_unauthenticated_optin_rejects_localhost_hostname(tmp_path):
    """``localhost`` is a HOSTNAME — bind-time resolution could map it to a
    non-loopback address, so the opt-in accepts IP literals only."""
    proc = _spawn_kernel(
        tmp_path / "k.db",
        extra_env={"KERNEL_ALLOW_UNAUTHENTICATED": "1", "HOST": "localhost"},
    )
    state, output = _wait_exit_or_healthy(proc)
    assert state == "exited"
    assert "requires a loopback bind" in output


def test_unauthenticated_optin_on_loopback_starts_open(tmp_path):
    """The explicit loopback opt-in still works for development — and the
    open route answers with a real 200, so a crashed server can't read as
    'auth gate absent'."""
    db_path = tmp_path / "k.db"
    _migrate_kernel_db(db_path)
    proc = _spawn_kernel(
        db_path,
        extra_env={"KERNEL_ALLOW_UNAUTHENTICATED": "1", "HOST": "127.0.0.1"},
    )
    try:
        state, port = _wait_exit_or_healthy(proc)
        assert state == "healthy"
        # No token middleware installed — a tokenless request reaches the
        # route and SUCCEEDS (migrated schema: a 500 would fail the test).
        # The owner header is still required (auth-off ≠ owner-less reads).
        resp = httpx.get(
            f"http://127.0.0.1:{port}/api/v1/sessions",
            headers={"X-Valuz-Owner-Id": "owner-a"},
            timeout=5.0,
        )
        assert resp.status_code == 200
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.asyncio
async def test_wrong_bearer_is_rejected_on_mutation_route(kernel_proc) -> None:
    """Symmetric to the open-path test: with KERNEL_AUTH_TOKEN set, the
    bearer middleware IS installed — a wrong token must be rejected on a
    session-mutation route before any handler runs."""
    resp = httpx.post(
        f"{kernel_proc}/api/v1/sessions",
        json={"id": "x", "agent_config": {"name": "a"}, "cwd": "/tmp/x"},
        headers={"Authorization": "Bearer WRONG"},
        timeout=5.0,
    )
    assert resp.status_code == 401
