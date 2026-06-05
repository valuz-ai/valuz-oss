"""Coverage for the on-loop SetupJob controller.

A ``_FakeJob`` keeps the test off the network. The controller runs each job's
blocking ``run`` in ``asyncio.to_thread`` and persists status/progress on the
event loop, so the tests ``await`` the controller and poll ``get`` for the
terminal state.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from valuz_agent.infra.database import Base
from valuz_agent.modules.parser.models import SetupJobRow  # noqa: F401  (model registration)
from valuz_agent.modules.parser.setup_jobs.base import (
    SetupJobAlreadyRunning,
    SetupJobController,
    SetupJobNotFound,
)


class _FakeJob:
    setup_id: str = "fake_models"

    def __init__(
        self, *, fail: bool = False, work_cycles: int = 4, cycle_sleep_s: float = 0.01
    ) -> None:
        self._fail = fail
        self._work_cycles = work_cycles
        self._cycle_sleep_s = cycle_sleep_s
        self.completed = threading.Event()
        self.complete_flag = False  # what is_complete returns

    def is_complete(self) -> bool:
        return self.complete_flag

    def run(self, *, progress_cb, cancel_event):  # type: ignore[no-untyped-def]
        # Runs off the loop (``asyncio.to_thread``). Reports progress via the
        # controller-supplied callback; never touches the DB directly.
        try:
            for i in range(self._work_cycles):
                if cancel_event.is_set():
                    return
                progress_cb((i + 1) * 100, self._work_cycles * 100)
                if cancel_event.wait(self._cycle_sleep_s):
                    return
            if self._fail:
                raise RuntimeError("simulated failure")
            self.complete_flag = True
        finally:
            self.completed.set()


@pytest_asyncio.fixture
async def _db(tmp_path, monkeypatch):
    """Point ``infra.db.AsyncSessionLocal`` at a file-backed (WAL) async engine.

    The controller persists on the loop; the download worker bridges progress
    back via ``run_coroutine_threadsafe``. ``NullPool`` + WAL keeps the engine
    robust under that one-writer-on-the-loop access.
    """
    import valuz_agent.infra.db as db_mod
    import valuz_agent.modules.parser  # noqa: F401 — register SetupJobRow

    db_file = tmp_path / "setup.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", poolclass=NullPool)

    @event.listens_for(async_engine.sync_engine, "connect")
    def _pragma(dbapi_conn, _record):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()

    monkeypatch.setattr(
        db_mod, "AsyncSessionLocal", async_sessionmaker(bind=async_engine, expire_on_commit=False)
    )
    yield


async def _wait_status(controller, setup_id, wanted, *, timeout=15.0):
    """Poll ``get`` until ``status`` is in ``wanted`` (a set) or timeout."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        status = (await controller.get(setup_id)).status
        if status in wanted:
            return status
        await asyncio.sleep(0.02)
    return (await controller.get(setup_id)).status


class TestRegistration:
    async def test_known_setup_ids_lists_registered(self, _db):
        controller = SetupJobController(jobs=[_FakeJob()])
        assert controller.known_setup_ids() == ["fake_models"]
        assert controller.has("fake_models")

    async def test_duplicate_setup_id_rejected(self, _db):
        with pytest.raises(ValueError, match="duplicate"):
            SetupJobController(jobs=[_FakeJob(), _FakeJob()])


class TestStatusAndStart:
    async def test_get_synthesises_pending_for_never_started(self, _db):
        controller = SetupJobController(jobs=[_FakeJob()])
        status = await controller.get("fake_models")
        assert status.status == "pending"
        assert status.started_at is None

    async def test_start_runs_to_succeeded(self, _db):
        controller = SetupJobController(jobs=[_FakeJob()])
        await controller.start("fake_models")
        assert await _wait_status(controller, "fake_models", {"succeeded"}) == "succeeded"

    async def test_start_is_short_circuit_when_already_complete(self, _db):
        job = _FakeJob()
        job.complete_flag = True  # ``is_complete`` returns True up front
        controller = SetupJobController(jobs=[job])
        await controller.start("fake_models")
        # The job's ``run`` should never have been scheduled.
        assert not job.completed.is_set()
        assert (await controller.get("fake_models")).status == "succeeded"

    async def test_start_double_raises(self, _db):
        controller = SetupJobController(jobs=[_FakeJob(work_cycles=200)])
        await controller.start("fake_models")
        with pytest.raises(SetupJobAlreadyRunning):
            await controller.start("fake_models")
        await controller.cancel("fake_models")  # tidy up


class TestCancellation:
    async def test_cancel_event_signals_worker_and_writes_status(self, _db):
        job = _FakeJob(work_cycles=1000, cycle_sleep_s=0.5)
        controller = SetupJobController(jobs=[job])
        await controller.start("fake_models")
        assert await _wait_status(controller, "fake_models", {"running"}) == "running"

        await controller.cancel("fake_models")
        final = await _wait_status(
            controller, "fake_models", {"cancelled", "succeeded", "failed"}, timeout=30.0
        )
        assert final in ("cancelled", "succeeded", "failed")


class TestFailureHandling:
    async def test_run_exception_marks_failed(self, _db):
        controller = SetupJobController(jobs=[_FakeJob(fail=True)])
        await controller.start("fake_models")
        assert await _wait_status(controller, "fake_models", {"failed"}) == "failed"
        status = await controller.get("fake_models")
        assert status.error == "simulated failure"


class TestNotFound:
    async def test_unknown_setup_id_404s(self, _db):
        controller = SetupJobController(jobs=[_FakeJob()])
        with pytest.raises(SetupJobNotFound):
            await controller.get("nope")
        with pytest.raises(SetupJobNotFound):
            await controller.start("nope")
        with pytest.raises(SetupJobNotFound):
            await controller.cancel("nope")


def test_rapidocr_is_complete_reads_marker_file(tmp_path: Path, monkeypatch):
    """The router's capability gate relies on ``is_complete`` being a
    pure filesystem probe — no DB, no network. The marker also encodes
    the model version so a stale PP-OCRv4 directory doesn't read as
    "ready" after the v4→v5 cutover; pin that contract here too."""
    from valuz_agent.modules.parser.setup_jobs.rapidocr import RapidOcrSetupJob

    job = RapidOcrSetupJob()
    monkeypatch.setattr(
        type(job),
        "model_dir",
        lambda self: tmp_path,
    )
    # No marker → not complete.
    assert job.is_complete() is False
    # Marker without the expected model_version line → still not complete.
    (tmp_path / "READY").write_text("2026-05-15T00:00:00+00:00", encoding="utf-8")
    assert job.is_complete() is False
    # Marker with the v5 model_version line → complete.
    (tmp_path / "READY").write_text(
        "timestamp=2026-05-15T00:00:00+00:00\nmodel_version=PP-OCRv5\n",
        encoding="utf-8",
    )
    assert job.is_complete() is True
