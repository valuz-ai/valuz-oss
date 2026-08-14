"""Shared fixtures for the tasks-module tests.

``db_factory`` is a REAL tmp sqlite rather than fake datastore classes: the
module's invariants live in its persistence (status machine, event sequence,
owner scoping), and a hand-rolled fake models the API rather than the data —
two broke on unrelated edits during the 2026-07 refactor and one had a bug
encoded as expected behaviour. Fakes remain right for SEAMS (kernel client,
sibling modules' stores); stubbing a seam is not stubbing yourself.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.database import Base
from valuz_agent.infra.execution_lease import ExecutionLeaseRow
from valuz_agent.modules.agents.models import AgentRow, ProjectMemberRow
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.projects.models import ProjectRow
from valuz_agent.modules.tasks.models import (
    TaskEventRow,
    TaskMailboxRow,
    TaskRow,
    TaskSessionRow,
)

# Every task test that touches the DB wants the same three tables. Creating all
# of them unconditionally is cheaper than each module deciding, and removes the
# "this test failed because its fixture forgot a table" class of confusion —
# which is exactly how the eight copies had drifted (one created a single
# table, another two, the rest three).
#
# ``valuz_project_member`` rides along because a real read path reaches it:
# ``service.list_members`` goes through the resolver seam into
# ``ProjectMemberDatastore``. One extra empty table costs nothing.
_TASK_TABLES = [
    TaskRow.__table__,
    TaskEventRow.__table__,
    TaskSessionRow.__table__,
    # The actors' durable inbox. Every loop reads it at each idle tick, so it
    # is not optional for any test that runs a loop.
    TaskMailboxRow.__table__,
    ProjectMemberRow.__table__,
    # ``resolve_agent_display_names`` joins membership → library agent to stamp
    # ``agent_name`` into every event payload and plan snapshot.
    AgentRow.__table__,
    # ``block_task`` writes a failure notification — a real path out of the
    # task module (events.block_task → notifications.projectors), so the
    # table has to exist wherever a task can go blocked.
    NotificationRow.__table__,
    # Project deletion cascades into the task tables, so the delete path — and
    # the boot sweep that purges tasks whose project is gone — both read this.
    ProjectRow.__table__,
    # Execution ownership: the actor loop acquires one before driving and the
    # health watchdog reads it as its liveness oracle. Shared infra table, not
    # a task table — the same primitive serves other subsystems.
    ExecutionLeaseRow.__table__,
]


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    """A tmp-SQLite async sessionmaker bound into ``infra.db.AsyncSessionLocal``.

    The host is fully async (``async_unit_of_work`` / aiosqlite), so we patch
    ``infra.db.AsyncSessionLocal`` and the code under test binds to this tmp
    engine with no other changes. The returned SYNC sessionmaker is for the
    test's own seed/read helpers — simpler than awaiting in a fixture helper,
    and it reads the same file.
    """
    import valuz_agent.infra.db as db_mod

    db_file = tmp_path / "tasks.db"
    sync_engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine, tables=_TASK_TABLES)

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setattr(
        db_mod,
        "AsyncSessionLocal",
        async_sessionmaker(bind=async_engine, expire_on_commit=False),
    )
    return sessionmaker(bind=sync_engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Delivery, for tests
# ---------------------------------------------------------------------------


async def deliver_async(
    session_id, msg, *, task_id="t1", project_id="w1", user_id="local-test-owner"
):
    """Put a message where an actor will actually find it.

    Tests used to call ``mailbox_registry.put``, which is gone along with the
    registry: messages live in ``valuz_task_mailbox`` and waking is a separate,
    payload-free ring. Writing to a process-local queue would now be simulating
    a path production does not have.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.tasks import mailbox_store

    async with async_unit_of_work() as db:
        await mailbox_store.enqueue(
            db,
            session_id=session_id,
            task_id=task_id,
            project_id=project_id,
            user_id=user_id,
            kind=msg.kind,
            text=msg.text,
            from_session=msg.from_session,
            origin=msg.origin,
            payload=dict(msg.payload or {}),
        )
    await mailbox_store.ring_for(session_id)


def deliver(session_id, msg, **kw):
    """``deliver_async`` for a test that is not already on an event loop."""
    import asyncio

    asyncio.run(deliver_async(session_id, msg, **kw))


@pytest.fixture(autouse=True)
def _fresh_notifier():
    """A ring remembered by one test must not wake the next one's wait.

    The notifier is a module-level singleton and remembers rings that arrived
    with nobody parked — deliberately, so a ring landing between a check and a
    park is not lost. Across tests that is just leakage, and it showed up as an
    order-dependent failure: a stale ring made a later wait return instantly,
    the loop took an extra slice, and a backstop fired that the test was
    asserting stayed out.
    """
    from valuz_agent.modules.tasks import notifier as _notifier_mod

    _notifier_mod.bind_notifier(_notifier_mod.InProcessNotifier())
    yield
