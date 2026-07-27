"""Parent-death watchdog (boot/parent_watchdog.py)."""

from __future__ import annotations

import asyncio
import os

import pytest

from valuz_agent.boot import parent_watchdog as pw


def test_parent_alive_should_report_true_for_own_pid() -> None:
    assert pw.parent_alive(os.getpid()) is True


def test_parent_alive_should_report_false_for_missing_pid() -> None:
    # Max pid space on macOS/Linux is far below this sentinel.
    assert pw.parent_alive(2**22 + 12345) is False


def test_parent_pid_from_env_should_parse_only_positive_ints(monkeypatch) -> None:
    monkeypatch.delenv("VALUZ_PARENT_PID", raising=False)
    assert pw.parent_pid_from_env() is None
    monkeypatch.setenv("VALUZ_PARENT_PID", "notanumber")
    assert pw.parent_pid_from_env() is None
    monkeypatch.setenv("VALUZ_PARENT_PID", "-4")
    assert pw.parent_pid_from_env() is None
    monkeypatch.setenv("VALUZ_PARENT_PID", "4242")
    assert pw.parent_pid_from_env() == 4242


async def test_watch_parent_should_fire_on_dead_once_parent_disappears() -> None:
    alive_states = iter([True, True, False])
    fired = asyncio.Event()

    await asyncio.wait_for(
        pw.watch_parent(
            123,
            alive=lambda pid: next(alive_states),
            on_dead=fired.set,
            poll_interval_s=0.01,
        ),
        timeout=1.0,
    )
    assert fired.is_set()


async def test_watch_parent_should_keep_polling_while_parent_lives() -> None:
    fired = asyncio.Event()
    task = asyncio.create_task(
        pw.watch_parent(
            os.getpid(),
            alive=lambda pid: True,
            on_dead=fired.set,
            poll_interval_s=0.01,
        )
    )
    await asyncio.sleep(0.1)
    assert not fired.is_set()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_start_is_noop_without_env(monkeypatch) -> None:
    monkeypatch.delenv("VALUZ_PARENT_PID", raising=False)

    async def run() -> None:
        pw.start_parent_watchdog()
        assert pw._task is None

    asyncio.run(run())
