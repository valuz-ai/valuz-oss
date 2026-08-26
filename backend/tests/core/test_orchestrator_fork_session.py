"""``SessionOrchestrator.fork_session`` — the runtime-fork step of session
fork (docs/design/session-fork.md §6.5).

It must build the NEW session's runtime through the standard factory,
drive ``RuntimePort.fork_session``, and on failure evict the half-built
runtime so a retry constructs a fresh one. The session object is passed
in-memory — persistence is the caller's commit point, so nothing here
touches session rows.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import pytest
import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.agent_config import AgentConfig
from src.core.orchestrator import SessionOrchestrator
from src.core.types import Session


class _ForkRuntime:
    def __init__(self, sink: object, *, fail: bool = False) -> None:
        self.sink = sink
        self.fail = fail
        self.fork_calls: list[tuple[str, str, str | None]] = []
        self.closed = False
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        self.sink = sink

    def set_session_rule_finder(self, finder: object) -> None:  # pragma: no cover
        pass

    async def fork_session(
        self,
        session: Session,
        *,
        source_native_session_id: str,
        anchor: str | None = None,
    ) -> str:
        self.fork_calls.append((session.id, source_native_session_id, anchor))
        if self.fail:
            raise RuntimeError("thread/fork refused")
        session.runtime_session_id = "th-forked"
        return "th-forked"

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:
        self.closed = True


class _NullStore:
    async def load_session(self, user_id: str, session_id: str) -> None:  # pragma: no cover
        return None


def _session(tmp_path) -> Session:
    return Session(
        id="forked-1",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        runtime_provider="codex",
    )


def _wire(monkeypatch, *, fail: bool = False) -> list[_ForkRuntime]:
    runtimes: list[_ForkRuntime] = []

    def create_runtime(*args, **kwargs) -> _ForkRuntime:  # noqa: ANN002, ANN003
        runtime = _ForkRuntime(args[2], fail=fail)
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)
    return runtimes


async def test_fork_session_drives_port_and_backfills(tmp_path, monkeypatch) -> None:
    runtimes = _wire(monkeypatch)
    session = _session(tmp_path)

    new_id = await SessionOrchestrator(_NullStore()).fork_session(
        "owner-1", session, source_native_session_id="th-src", anchor="turn-2"
    )

    assert new_id == "th-forked"
    assert session.runtime_session_id == "th-forked"
    assert runtimes[0].fork_calls == [("forked-1", "th-src", "turn-2")]
    # The runtime stays warm (not closed) for the first Send.
    assert runtimes[0].closed is False


async def test_fork_session_failure_evicts_and_reraises(tmp_path, monkeypatch) -> None:
    runtimes = _wire(monkeypatch, fail=True)
    orchestrator = SessionOrchestrator(_NullStore())
    session = _session(tmp_path)

    with pytest.raises(RuntimeError):
        await orchestrator.fork_session("owner-1", session, source_native_session_id="th-src")

    assert runtimes[0].closed is True
    assert session.runtime_session_id is None
    # A retry builds a FRESH runtime rather than reusing the evicted one.
    with pytest.raises(RuntimeError):
        await orchestrator.fork_session("owner-1", session, source_native_session_id="th-src")
    assert len(runtimes) == 2
