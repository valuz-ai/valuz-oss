"""ADR-011 — single-writer lock tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from valuz_agent.infra.single_writer import (
    AnotherInstanceRunning,
    acquire_single_writer_lock,
    read_lock_holder_pid,
    release_single_writer_lock,
)


pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only lock semantics")


@pytest.fixture(autouse=True)
def _reset_lock_state() -> None:
    # Make sure each test starts and ends without holding the module-level
    # lock — otherwise tests within the same process pollute each other.
    release_single_writer_lock()
    yield
    release_single_writer_lock()


class TestAcquireAndRelease:
    def test_should_acquire_lock_when_not_held(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".scheduler.lock"
        acquire_single_writer_lock(lock_path)
        assert lock_path.exists()

    def test_should_be_idempotent_within_same_process(self, tmp_path: Path) -> None:
        lock_path = tmp_path / ".scheduler.lock"
        acquire_single_writer_lock(lock_path)
        # Second call must not raise.
        acquire_single_writer_lock(lock_path)

    def test_should_record_pid_in_lock_file(self, tmp_path: Path) -> None:
        import os

        lock_path = tmp_path / ".scheduler.lock"
        acquire_single_writer_lock(lock_path)
        assert read_lock_holder_pid(lock_path) == os.getpid()

    def test_should_raise_when_second_process_tries(self, tmp_path: Path) -> None:
        """Simulate a second concurrent instance by running the acquire
        path in a subprocess after we hold the lock in-process. The
        subprocess must exit with ``AnotherInstanceRunning``."""
        import subprocess
        import textwrap

        lock_path = tmp_path / ".scheduler.lock"
        acquire_single_writer_lock(lock_path)

        script = textwrap.dedent(
            f"""
            from pathlib import Path
            from valuz_agent.infra.single_writer import (
                acquire_single_writer_lock,
                AnotherInstanceRunning,
            )
            try:
                acquire_single_writer_lock(Path({str(lock_path)!r}))
            except AnotherInstanceRunning:
                raise SystemExit(42)
            raise SystemExit(0)
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 42, (
            f"expected AnotherInstanceRunning (exit 42); got {result.returncode}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )


class TestReadLockHolderPid:
    def test_should_return_none_when_lock_file_missing(self, tmp_path: Path) -> None:
        assert read_lock_holder_pid(tmp_path / "nope.lock") is None

    def test_should_return_none_when_file_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.lock"
        p.write_text("")
        assert read_lock_holder_pid(p) is None

    def test_should_return_none_for_malformed_contents(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.lock"
        p.write_text("not-a-pid")
        assert read_lock_holder_pid(p) is None
