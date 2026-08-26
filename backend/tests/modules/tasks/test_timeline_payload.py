"""``task_plan_update`` is quadratic, and only the newest one is ever read.

Each snapshot carries the WHOLE plan — every node's full ``goal`` (the subtask
brief the lead wrote), ``review_criteria`` and ``review_feedback`` — and one is
written on every node flip. A plan of N subtasks emits ~3N of them, each
carrying all N briefs.

In a real 3.5-week install that was 72% of all task-event payload
(1.31 MB of 1.80 MB across 25 tasks), and every byte of it beyond the last
snapshot was written once and read never: the Todo panel takes
``events.reverse().find(type === "task_plan_update")`` and the transcript skips
the type outright.

So the bulk reads return only the newest — which stays complete, because a
consumer must be able to render the whole card from one event. The log is not
rewritten; this is a projection.
"""

from __future__ import annotations

import asyncio
import json

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.datastore import TaskDatastore, TaskEventDatastore
from valuz_agent.modules.tasks.models import PLAN_SNAPSHOT_EVENT, TaskRow
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.service import TaskService

OWNER = "local-test-owner"

# A brief long enough to be worth counting — real ones run far longer.
_BRIEF = "Research the segment and write it up. " * 20


def _seed(db_factory, *, nodes: int = 4) -> None:
    plan = TaskPlan()
    plan.add(
        [
            {
                "key": f"k{i}",
                "title": f"Subtask {i}",
                "goal": _BRIEF,
                "agent": "worker",
                "review_criteria": "must cite sources",
            }
            for i in range(nodes)
        ]
    )
    db = db_factory()
    try:
        db.add(
            TaskRow(
                id="t-fat",
                user_id=OWNER,
                project_id="w1",
                file_path="tasks/t-fat.md",
                title="fat",
                goal="g",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
                plan=plan.to_dict(),
            )
        )
        db.commit()
    finally:
        db.close()


def _emit_snapshots(count: int) -> None:
    """Walk the plan the way execution does — one snapshot per node flip."""

    async def _run() -> None:
        for i in range(count):
            async with async_unit_of_work() as db:
                task_ds = TaskDatastore(db)
                row = await task_ds.get_task(OWNER, "t-fat")
                await planning.emit_plan_update(
                    TaskEventDatastore(db),
                    row,
                    TaskPlan.from_dict(row.plan),
                    actor="system",
                    session_id=None,
                    user_id=OWNER,
                    plan_version=i + 1,
                )

    asyncio.run(_run())


def test_bulk_read_returns_only_the_newest_plan_snapshot(db_factory) -> None:
    _seed(db_factory)
    _emit_snapshots(6)

    async def _read():
        async with async_unit_of_work(commit=False) as db:
            found = await TaskService(db).get_events(OWNER, "t-fat")
            assert found is not None
            return found.events

    events = asyncio.run(_read())
    snapshots = [e for e in events if e.type == PLAN_SNAPSHOT_EVENT]

    assert len(snapshots) == 1, f"6 written, {len(snapshots)} returned"
    assert snapshots[0].payload["plan_version"] == 6, "and it must be the NEWEST"


def test_the_surviving_snapshot_is_still_self_contained(db_factory) -> None:
    """Dropping the older copies must not thin the one that ships.

    A consumer renders the whole plan card from a single event — SSE gives no
    guarantee it saw the previous one.
    """
    _seed(db_factory, nodes=3)
    _emit_snapshots(4)

    async def _read():
        async with async_unit_of_work(commit=False) as db:
            found = await TaskService(db).get_events(OWNER, "t-fat")
            assert found is not None
            return found.events

    snapshot = [e for e in asyncio.run(_read()) if e.type == PLAN_SNAPSHOT_EVENT][0]
    subtasks = snapshot.payload["subtasks"]

    assert len(subtasks) == 3
    for node in subtasks:
        assert node["goal"] == _BRIEF, "the full brief still rides along"
        assert node["review_criteria"] == "must cite sources"
        assert {"key", "label", "status", "agent", "attempts"} <= set(node)


def test_the_log_itself_is_untouched(db_factory) -> None:
    """Append-only holds — the collapse is a read projection, not a rewrite."""
    _seed(db_factory)
    _emit_snapshots(5)

    async def _raw():
        async with async_unit_of_work(commit=False) as db:
            return await TaskEventDatastore(db).list_events(
                OWNER, "w1", "t-fat", include_superseded_plan_snapshots=True
            )

    assert len([e for e in asyncio.run(_raw()) if e.type == PLAN_SNAPSHOT_EVENT]) == 5


def test_the_collapse_is_what_makes_the_page_cheap(db_factory) -> None:
    """The point of the change, in bytes.

    Quadratic growth is the reason a per-node status flip cost as much as the
    whole plan: without this, ten flips on a ten-node plan ship the ten briefs
    ten times.
    """
    _seed(db_factory, nodes=6)
    _emit_snapshots(10)

    async def _sizes() -> tuple[int, int]:
        async with async_unit_of_work(commit=False) as db:
            ds = TaskEventDatastore(db)
            raw = await ds.list_events(
                OWNER, "w1", "t-fat", include_superseded_plan_snapshots=True
            )
            collapsed = await ds.list_events(OWNER, "w1", "t-fat")
        size = lambda rows: sum(len(json.dumps(r.payload, ensure_ascii=False)) for r in rows)  # noqa: E731
        return size(raw), size(collapsed)

    raw_bytes, collapsed_bytes = asyncio.run(_sizes())
    assert collapsed_bytes * 5 < raw_bytes, (
        "10 snapshots of a 6-node plan collapse to 1 — expected roughly a 10x "
        f"cut, got {raw_bytes} -> {collapsed_bytes}"
    )


def test_the_live_stream_still_gets_every_snapshot(db_factory) -> None:
    """Only the bulk replay collapses.

    A client tracking the plan needs each update to advance it; dropping
    intermediate ones from the incremental feed would freeze the panel between
    flips.
    """
    _seed(db_factory)
    _emit_snapshots(4)

    async def _after_zero():
        async with async_unit_of_work(commit=False) as db:
            return await TaskService(db).events_after(OWNER, "w1", "t-fat", 0)

    assert len([e for e in asyncio.run(_after_zero()) if e.type == PLAN_SNAPSHOT_EVENT]) == 4
