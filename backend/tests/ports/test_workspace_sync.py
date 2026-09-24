"""``ext.workspace_sync`` — the port and its two fail-open call-site helpers."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from valuz_agent.ports import workspace_sync as ws
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.workspace_sync import (
    NoopWorkspaceSync,
    WorkspaceSyncPort,
    ensure_readable,
    notify_written,
)


@pytest.fixture
def port(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    bound = SimpleNamespace(after_write=AsyncMock(), before_read=AsyncMock())
    monkeypatch.setattr(ext, "workspace_sync", bound)
    return bound


def test_the_default_binding_is_the_noop() -> None:
    assert type(ext.workspace_sync) is NoopWorkspaceSync
    assert isinstance(ext.workspace_sync, WorkspaceSyncPort)
    assert ws.get_workspace_sync() is ext.workspace_sync


def test_set_workspace_sync_rebinds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ext, "workspace_sync", ext.workspace_sync)
    bound = SimpleNamespace(after_write=AsyncMock(), before_read=AsyncMock())
    ws.set_workspace_sync(bound)  # type: ignore[arg-type]
    assert ext.workspace_sync is bound


async def test_the_noop_answers_none() -> None:
    noop = NoopWorkspaceSync()
    assert await noop.after_write(owner_user_id="u1", paths=[Path("/a")]) is None
    assert await noop.before_read(owner_user_id="u1", paths=[Path("/a")]) is None


async def test_helpers_are_inert_under_the_noop() -> None:
    """The OSS default: nothing is iterated, nothing awaited, nothing raised."""

    def _never_iterated():  # type: ignore[no-untyped-def]
        raise AssertionError("the no-op binding must not consume the paths")
        yield Path("/x")  # pragma: no cover

    await notify_written("u1", _never_iterated())
    await ensure_readable("u1", _never_iterated())


async def test_notify_written_passes_owner_absolute_paths_and_project(port) -> None:  # type: ignore[no-untyped-def]
    await notify_written("u1", [Path("/w/a.md"), "/w/b.md", Path("/w/a.md")], project_id="p1")

    port.after_write.assert_awaited_once_with(
        owner_user_id="u1", paths=(Path("/w/a.md"), Path("/w/b.md")), project_id="p1"
    )
    port.before_read.assert_not_awaited()


async def test_ensure_readable_passes_owner_absolute_paths_and_project(port) -> None:  # type: ignore[no-untyped-def]
    await ensure_readable("u1", [Path("/w/run")], project_id="p1")

    port.before_read.assert_awaited_once_with(
        owner_user_id="u1", paths=(Path("/w/run"),), project_id="p1"
    )
    port.after_write.assert_not_awaited()


@pytest.mark.parametrize("paths", [[], [""], ["relative/file.md"], [Path("rel")], [Path()]])
async def test_helpers_skip_empty_and_relative_path_lists(port, paths) -> None:  # type: ignore[no-untyped-def]
    await notify_written("u1", paths)
    await ensure_readable("u1", paths)

    port.after_write.assert_not_awaited()
    port.before_read.assert_not_awaited()


async def test_helpers_skip_a_missing_owner(port) -> None:  # type: ignore[no-untyped-def]
    await notify_written("", [Path("/w/a.md")])
    await ensure_readable("", [Path("/w/a.md")])

    port.after_write.assert_not_awaited()
    port.before_read.assert_not_awaited()


async def test_notify_written_is_fail_open(port, caplog) -> None:  # type: ignore[no-untyped-def]
    port.after_write.side_effect = RuntimeError("log unreachable")

    with caplog.at_level(logging.WARNING, logger=ws.__name__):
        await notify_written("u1", [Path("/w/a.md")])  # must not raise

    assert "after_write failed" in caplog.text


async def test_ensure_readable_is_fail_open_on_error(port, caplog) -> None:  # type: ignore[no-untyped-def]
    port.before_read.side_effect = RuntimeError("barrier broke")

    with caplog.at_level(logging.WARNING, logger=ws.__name__):
        await ensure_readable("u1", [Path("/w/a.md")])  # must not raise

    assert "before_read failed" in caplog.text


async def test_ensure_readable_honours_its_timeout(port, caplog) -> None:  # type: ignore[no-untyped-def]
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _hang(**_kw: object) -> None:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    port.before_read.side_effect = _hang
    loop = asyncio.get_running_loop()
    t0 = loop.time()

    with caplog.at_level(logging.WARNING, logger=ws.__name__):
        await ensure_readable("u1", [Path("/w/a.md")], timeout_s=0.05)

    assert loop.time() - t0 < 5
    assert started.is_set() and cancelled.is_set()
    assert "timed out" in caplog.text


async def test_notify_written_does_not_swallow_cancellation(port) -> None:  # type: ignore[no-untyped-def]
    """Fail-open covers errors, not a cancelled host task."""
    port.after_write.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await notify_written("u1", [Path("/w/a.md")])


def _raises_after_first() -> object:
    yield Path("/w/a.md")
    raise PermissionError("EACCES while walking the caller's paths")


@pytest.mark.parametrize(
    "paths",
    [
        pytest.param(_raises_after_first, id="raising-generator"),
        pytest.param(lambda: [None], id="none-entry"),
    ],
)
async def test_helpers_are_fail_open_when_the_paths_iterable_raises(port, caplog, paths) -> None:  # type: ignore[no-untyped-def]
    """Normalising the caller's paths is inside the fail-open block too."""
    with caplog.at_level(logging.WARNING, logger=ws.__name__):
        await notify_written("u1", paths())  # must not raise
        await ensure_readable("u1", paths())  # must not raise

    assert "after_write failed" in caplog.text
    assert "before_read failed" in caplog.text
    port.after_write.assert_not_awaited()
    port.before_read.assert_not_awaited()
