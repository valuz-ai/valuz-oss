"""Every path that builds a runtime must carry the runtime context.

``_ensure_runtime`` materializes the session's opaque credential markers, so a
deployment that stores a marker instead of a key (the whole point of the
per-operation context) breaks on ANY caller that forgets to pass one. Only
``run`` did: ``fork_session`` and ``prepare_runtime`` build a runtime too, and
both raised ``runtime context is missing marker`` — the fork route wrapped that
as ``502 Native thread fork failed``, so no cloud session could be forked at
all, and warming an idle session 500'd.

The fork case is the sharp one because forking needs no model call whatsoever:
the runtime's ``fork_session`` is an offline transcript transform. It failed on
the credential it never uses.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import pytest
import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.agent_config import AgentConfig
from src.core.orchestrator import SessionOrchestrator
from src.core.runtime_context import runtime_context_marker
from src.core.types import ModelProvider, Session

_KEY = "example.runtime"
_MARKER = runtime_context_marker(_KEY)
_CONTEXT = {_KEY: "resolved-secret"}


class _Runtime:
    """Records the api_key it was constructed with."""

    def __init__(self, session: Session) -> None:
        self.api_key = session.model_provider.api_key if session.model_provider else None
        self.closed = False
        self.has_live_background_tasks = False
        self.prepared = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:  # pragma: no cover
        pass

    def set_session_rule_finder(self, finder: object) -> None:  # pragma: no cover
        pass

    async def fork_session(self, session: Session, **_: object) -> str:
        session.runtime_session_id = "th-forked"
        return "th-forked"

    async def prepare(self, session: Session) -> None:
        self.prepared = True

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:
        self.closed = True


def _session(tmp_path) -> Session:
    return Session(
        id="session-1",
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        runtime_provider="codex",
        model="managed-model",
        model_provider=ModelProvider(
            api_key=_MARKER,
            base_url="https://gateway.test",
            api_protocol="openai_response",
        ),
    )


class _Store:
    def __init__(self, session: Session | None) -> None:
        self._session = session
        self.saved: list[Session] = []

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return self._session

    async def save_session(self, session: Session) -> None:
        # ``prepare_runtime`` persists the runtime_session_id codex allocates.
        self.saved.append(session)


def _wire(monkeypatch) -> list[_Runtime]:
    runtimes: list[_Runtime] = []

    def create_runtime(*args, **kwargs) -> _Runtime:  # noqa: ANN002, ANN003
        runtime = _Runtime(args[1])
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)
    return runtimes


async def test_fork_materializes_the_marker_from_the_given_context(tmp_path, monkeypatch) -> None:
    runtimes = _wire(monkeypatch)
    session = _session(tmp_path)

    new_id = await SessionOrchestrator(_Store(None)).fork_session(
        "owner-1",
        session,
        source_native_session_id="th-src",
        runtime_context=_CONTEXT,
    )

    assert new_id == "th-forked"
    assert runtimes[0].api_key == "resolved-secret"
    # The PERSISTED session keeps the marker — materialization is runtime-only.
    assert session.model_provider is not None
    assert session.model_provider.api_key == _MARKER


async def test_fork_without_a_context_still_fails_loudly(tmp_path, monkeypatch) -> None:
    """The guard stays: a missing context is a bug in the caller, not a
    reason to hand the runtime an unresolved marker."""
    _wire(monkeypatch)
    with pytest.raises(ValueError, match="missing marker"):
        await SessionOrchestrator(_Store(None)).fork_session(
            "owner-1", _session(tmp_path), source_native_session_id="th-src"
        )


async def test_prepare_materializes_the_marker_from_the_given_context(
    tmp_path, monkeypatch
) -> None:
    runtimes = _wire(monkeypatch)
    session = _session(tmp_path)

    await SessionOrchestrator(_Store(session)).prepare_runtime(
        "owner-1", session.id, runtime_context=_CONTEXT
    )

    assert runtimes[0].api_key == "resolved-secret"
    assert runtimes[0].prepared is True
