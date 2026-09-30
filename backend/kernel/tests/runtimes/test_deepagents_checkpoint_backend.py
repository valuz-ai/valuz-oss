"""Checkpoint backend selection for the DeepAgents runtime.

The gate used to be hardcoded to ``_in_sandbox()``. It is now
``_checkpoint_backend()``, which keeps that as the default but lets a
deployment pin the store explicitly with ``DEEPAGENTS_CHECKPOINT_BACKEND``.
These cover the default-preservation contract (so the override cannot silently
change existing installs) and both override directions.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from src.runtimes.deepagents.runtime import (
    CHECKPOINT_BACKEND_ENV,
    DeepAgentsRuntime,
    _build_local_shell_backend,
    _checkpoint_backend,
    _state_citation_artifacts,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (CHECKPOINT_BACKEND_ENV, "IS_SANDBOX", "KERNEL_STORE"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_unchanged_without_the_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Local resident process → sqlite (historical behaviour).
    assert _checkpoint_backend() == "sqlite"

    # Sandbox image sets IS_SANDBOX → file.
    monkeypatch.setenv("IS_SANDBOX", "1")
    assert _checkpoint_backend() == "file"
    monkeypatch.delenv("IS_SANDBOX")

    # SaaS store tier also implies the ephemeral sandbox → file.
    monkeypatch.setenv("KERNEL_STORE", "remote")
    assert _checkpoint_backend() == "file"


@pytest.mark.parametrize("value", ["file", "FILE", " file "])
def test_override_pins_file_even_when_local(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(CHECKPOINT_BACKEND_ENV, value)
    assert _checkpoint_backend() == "file"


@pytest.mark.parametrize("value", ["sqlite", "SQLite"])
def test_override_pins_sqlite_even_inside_the_sandbox(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("IS_SANDBOX", "1")
    monkeypatch.setenv(CHECKPOINT_BACKEND_ENV, value)
    assert _checkpoint_backend() == "sqlite"


def test_unrecognised_value_falls_back_to_the_gate_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo must not take the runtime down — fall back to the deployment gate.
    monkeypatch.setenv(CHECKPOINT_BACKEND_ENV, "postgres")
    assert _checkpoint_backend() == "sqlite"
    monkeypatch.setenv("IS_SANDBOX", "1")
    assert _checkpoint_backend() == "file"


def test_local_backend_maps_virtual_conversation_history_into_workspace(tmp_path) -> None:
    backend = _build_local_shell_backend(str(tmp_path))

    assert backend.virtual_mode is True
    assert backend.cwd == tmp_path.resolve()


def test_state_citation_artifacts_replays_final_middleware_tool_sidecar() -> None:
    message = SimpleNamespace(
        tool_call_id="tool-call-1",
        name="revenue_breakdown",
        content='{"data":[{"revenue":42}],"_valuz_evidence_hint":{"collectionHandle":"evc_test","contentRoot":"/data"}}',
        artifact={"_valuz_citation_content": '{"_valuz_evidence":[]}'},
    )
    state = SimpleNamespace(values={"messages": [message]})

    assert _state_citation_artifacts(state) == [
        (
            "tool-call-1",
            "revenue_breakdown",
            message.content,
            '{"_valuz_evidence":[]}',
        )
    ]


@pytest.mark.asyncio
async def test_sqlite_checkpointer_retries_transient_disk_io_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from langgraph.checkpoint.sqlite import aio as checkpoint_aio

    setup_attempts = 0
    closed_attempts: list[int] = []

    class _Saver:
        def __init__(self, attempt: int) -> None:
            self.attempt = attempt

        async def setup(self) -> None:
            nonlocal setup_attempts
            setup_attempts += 1
            if self.attempt == 1:
                raise sqlite3.OperationalError("disk I/O error")

    class _ContextManager:
        def __init__(self, attempt: int) -> None:
            self.attempt = attempt
            self.saver = _Saver(attempt)

        async def __aenter__(self):
            return self.saver

        async def __aexit__(self, *_args) -> None:
            closed_attempts.append(self.attempt)

    class _AsyncSqliteSaver:
        attempts = 0

        @classmethod
        def from_conn_string(cls, _path: str):
            cls.attempts += 1
            return _ContextManager(cls.attempts)

    monkeypatch.setattr(checkpoint_aio, "AsyncSqliteSaver", _AsyncSqliteSaver)
    runtime = object.__new__(DeepAgentsRuntime)
    runtime.checkpoint_db = str(tmp_path / "checkpoints.db")
    runtime._checkpointer = None
    runtime._checkpointer_cm = None

    saver = await runtime._open_checkpointer()

    assert saver.attempt == 2
    assert setup_attempts == 2
    assert closed_attempts == [1]


@pytest.mark.asyncio
async def test_sqlite_checkpointer_does_not_retry_other_operational_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from langgraph.checkpoint.sqlite import aio as checkpoint_aio

    class _Saver:
        async def setup(self) -> None:
            raise sqlite3.OperationalError("database is locked")

    class _ContextManager:
        async def __aenter__(self):
            return _Saver()

        async def __aexit__(self, *_args) -> None:
            return None

    class _AsyncSqliteSaver:
        attempts = 0

        @classmethod
        def from_conn_string(cls, _path: str):
            cls.attempts += 1
            return _ContextManager()

    monkeypatch.setattr(checkpoint_aio, "AsyncSqliteSaver", _AsyncSqliteSaver)
    runtime = object.__new__(DeepAgentsRuntime)
    runtime.checkpoint_db = str(tmp_path / "checkpoints.db")
    runtime._checkpointer = None
    runtime._checkpointer_cm = None

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        await runtime._open_checkpointer()

    assert _AsyncSqliteSaver.attempts == 1
