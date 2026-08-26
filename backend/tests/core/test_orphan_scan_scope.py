"""Stranded-session recovery at the kernel layer.

Two mechanisms, one per plane:

- kernel boot scans reconcile ONLY the kernel's own lineage: ``self._store``
  reads are the kernel's runtime sqlite (RuntimeStore authority), so sessions
  live on other processes are structurally out of reach — the sweep is
  unconditional and safe in every deployment.
- the HOST reconciles everything else: it decides which sessions are stranded
  (sandbox liveness) and calls the per-session ``reset_stranded_session`` —
  the reset semantics are idle + resumable ``host_restart``, messages errored.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

import pytest

from src.core.agent_config import AgentConfig
from src.core.orchestrator import SessionOrchestrator
from src.core.types import Session


def _running_session(user_id: str, sid: str) -> Session:
    return Session(
        user_id=user_id,
        id=sid,
        agent_config=AgentConfig(id=sid + "-a", name="a", model="m"),
        cwd="/tmp",
        status="running",
    )


class _FakeStore:
    """Records list/save calls; returns a fixed session set for ``list_sessions``."""

    def __init__(self, sessions: list[Session] | None = None) -> None:
        self._sessions = sessions or []
        self.list_calls = 0
        self.saved_sessions: list[Session] = []
        self.appended_events: list = []

    async def list_sessions(self, user_id, *, status=None, limit=None):  # noqa: ANN001, ANN002
        self.list_calls += 1
        return [s for s in self._sessions if status is None or s.status == status]

    async def load_session(self, user_id, session_id):  # noqa: ANN001
        for s in self._sessions:
            if s.user_id == user_id and s.id == session_id:
                return s
        return None

    async def save_session(self, session):  # noqa: ANN001
        self.saved_sessions.append(session)

    async def list_messages_for_session(self, user_id, session_id):  # noqa: ANN001
        return []

    async def save_message(self, user_id, message):  # noqa: ANN001
        pass

    async def get_events(self, user_id, session_id, *, types=None, limit=None):  # noqa: ANN001
        return []

    async def append_event(self, user_id, session_id, message_id, event, **kw):  # noqa: ANN001
        self.appended_events.append((session_id, event))
        return 1


# ── own-lineage boot scan (invariant: a kernel reconciles ONLY its lineage) ─


@pytest.mark.asyncio
async def test_boot_scan_resets_own_lineage_running_sessions() -> None:
    # The scan sweeps ``self._store`` — which for an execution kernel reads its
    # own lineage by composition. Stranded rows flip to idle + resumable
    # host_restart.
    store = _FakeStore([_running_session("u1", "s1")])
    orch = SessionOrchestrator(store)

    reset = await orch.scan_orphan_runs()

    assert reset == 1
    (saved,) = store.saved_sessions
    assert saved.id == "s1" and saved.status == "idle"
    assert saved.stop_reason is not None and saved.stop_reason.category == "host_restart"


# ── host-driven per-session reset (shared-durable tiers) ────────────────────


@pytest.mark.asyncio
async def test_reset_stranded_session_resets_running_via_durable() -> None:
    # The host confirmed the session's sandbox is gone and asks for the reset:
    # loaded from the durable authority (the session never touched the deciding
    # process's local store), stamped idle + resumable host_restart.
    durable = _FakeStore([_running_session("u1", "s1")])
    orch = SessionOrchestrator(durable)

    assert await orch.reset_stranded_session("u1", "s1") is True

    (saved,) = durable.saved_sessions
    assert saved.id == "s1" and saved.status == "idle"
    assert saved.stop_reason is not None and saved.stop_reason.category == "host_restart"


@pytest.mark.asyncio
async def test_reset_stranded_session_noops_when_not_running() -> None:
    # Idempotence under racing host replicas: a second reset (session already
    # idle) and an unknown session both no-op.
    idle = _running_session("u1", "s1")
    idle.status = "idle"
    durable = _FakeStore([idle])
    orch = SessionOrchestrator(durable)

    assert await orch.reset_stranded_session("u1", "s1") is False
    assert await orch.reset_stranded_session("u1", "missing") is False
    assert durable.saved_sessions == []
