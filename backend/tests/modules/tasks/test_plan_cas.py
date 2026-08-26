"""persist_plan is a CAS write door — version bumps + conflict re-apply.

Two defects this pins against regression:

* Node-status writes (dispatch → in_review → done …) used to leave
  ``plan_version`` frozen, so every ``task_plan_update`` snapshot after the
  last structural edit carried the same version — and the frontend plan-card
  feed, which dedups on ``plan_version``, silently discarded all of them.
* The plan column is a whole-document JSON write. Without the version
  predicate, two concurrent read-modify-write cycles (lead loop vs heartbeat
  vs user stop) both started from the same snapshot and the loser's node
  mutations were reverted by the winner's stale copy.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.tasks import planning
from valuz_agent.modules.tasks.datastore import TaskDatastore, TaskEventDatastore
from valuz_agent.modules.tasks.models import TaskEventRow, TaskRow
from valuz_agent.modules.tasks.plan import TaskPlan
from valuz_agent.modules.tasks.planning import PlanConflictError

OWNER = "local-test-owner"


def _never_wins(monkeypatch) -> None:
    """Make every CAS round lose, so persist_plan exhausts its retries."""

    async def _lose(self, user_id, row, plan, *, expected_version):
        return False

    monkeypatch.setattr(TaskDatastore, "cas_update_plan", _lose)


def _seed_task(db_factory, task_id: str = "t-cas") -> None:
    plan = TaskPlan()
    plan.add(
        [
            {"key": "a", "title": "A", "goal": "ga", "agent": "worker"},
            {"key": "b", "title": "B", "goal": "gb", "agent": "worker"},
        ]
    )
    db = db_factory()
    try:
        db.add(
            TaskRow(
                id=task_id,
                user_id=OWNER,
                project_id="w1",
                file_path=f"tasks/{task_id}.md",
                title="cas",
                goal="g",
                status="active",
                lead_agent_slug="lead",
                current_holder="lead",
                plan=plan.to_dict(),
                plan_version=3,
            )
        )
        db.commit()
    finally:
        db.close()


def test_node_status_writes_bump_plan_version(db_factory) -> None:
    """Every persist_plan write moves the version — the feed's dedup key.

    mark_node_dispatched / mark_in_review are pure node-status writes (no
    structural edit); their snapshots must still carry fresh versions or the
    plan-card feed drops them.
    """
    _seed_task(db_factory)

    async def _run() -> None:
        await planning.mark_node_dispatched(
            project_id="w1",
            task_id="t-cas",
            subtask_key="a",
            agent="worker",
            session_id="m1",
            user_id=OWNER,
        )
        await planning.mark_in_review(
            task_id="t-cas", project_id="w1", member_session_id="m1", user_id=OWNER
        )

    # mark_in_review resolves the member's run row; seed it via the real store
    db = db_factory()
    try:
        from valuz_agent.modules.tasks.models import TaskSessionRow

        db.add(
            TaskSessionRow(
                id="r1",
                user_id=OWNER,
                project_id="w1",
                task_id="t-cas",
                session_id="m1",
                agent_slug="worker",
                sequence=1,
                kind="subtask",
                subtask_key="a",
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()

    asyncio.run(_run())

    db = db_factory()
    try:
        row = db.get(TaskRow, "t-cas")
        assert row.plan_version == 5, "two node-status writes → version 3 → 5"
        versions = [
            e.payload["plan_version"]
            for e in db.execute(
                select(TaskEventRow).order_by(TaskEventRow.sequence)
            ).scalars()
            if e.type == "task_plan_update"
        ]
        assert versions == [4, 5], (
            "snapshots must carry strictly increasing versions — equal versions "
            f"are dropped by the plan-card feed dedup (got {versions})"
        )
    finally:
        db.close()


def test_cas_conflict_reapplies_mutation_on_fresh_plan(db_factory) -> None:
    """A writer holding a stale row retries and composes with the winner.

    Simulates the real interleaving: writer A loads the task, then writer B
    commits a node change (bumping the version) before A persists. A's CAS
    must fail, reload, re-apply its own mutation, and land WITHOUT reverting
    B's node.
    """
    _seed_task(db_factory, task_id="t-race")

    async def _run() -> None:
        async with async_unit_of_work() as db_a:
            task_ds_a = TaskDatastore(db_a)
            event_ds_a = TaskEventDatastore(db_a)
            stale_row = await task_ds_a.get_task(OWNER, "t-race")

            # Writer B lands first, on its own unit of work.
            async with async_unit_of_work() as db_b:
                task_ds_b = TaskDatastore(db_b)
                row_b = await task_ds_b.get_task(OWNER, "t-race")

                def _b(p: TaskPlan) -> bool:
                    p.update_node("b", status="in_progress")
                    return True

                assert (
                    await planning.persist_plan(
                        task_ds_b,
                        event_ds_a,
                        row_b,
                        mutate=_b,
                        actor="system",
                        session_id=None,
                        user_id=OWNER,
                    )
                    is not None
                )

            # Writer A persists off its stale row — must retry, not revert B.
            def _a(p: TaskPlan) -> bool:
                p.update_node("a", status="in_progress")
                return True

            persisted = await planning.persist_plan(
                task_ds_a,
                event_ds_a,
                stale_row,
                mutate=_a,
                actor="system",
                session_id=None,
                user_id=OWNER,
            )
            assert persisted is not None

    asyncio.run(_run())

    db = db_factory()
    try:
        row = db.get(TaskRow, "t-race")
        statuses = {n["key"]: n["status"] for n in row.plan["subtasks"]}
        assert statuses == {"a": "in_progress", "b": "in_progress"}, (
            "the CAS retry must compose both writers' node mutations — "
            f"a last-writer-wins revert leaves one 'planned' (got {statuses})"
        )
        assert row.plan_version == 5, "two writes → 3 → 5"
    finally:
        db.close()


def test_snapshot_carries_the_version_this_write_installed(db_factory, monkeypatch) -> None:
    """If ANOTHER writer lands between our CAS commit and the row refresh, the
    refreshed row already shows the later version — stamping that onto OUR
    (older) snapshot makes the feed's dedup discard the real newer snapshot.
    The emitted version must be expected+1, what this write provably installed."""
    from valuz_agent.modules.tasks.datastore import TaskDatastore

    _seed_task(db_factory, task_id="t-stamp")
    real = TaskDatastore.cas_update_plan

    async def _cas_then_lose_refresh_race(self, user_id, row, plan, *, expected_version):
        wrote = await real(self, user_id, row, plan, expected_version=expected_version)
        if wrote:
            # Simulate the refresh picking up a later writer's version.
            row.plan_version = (row.plan_version or 0) + 1
        return wrote

    monkeypatch.setattr(TaskDatastore, "cas_update_plan", _cas_then_lose_refresh_race)

    async def _run() -> None:
        async with async_unit_of_work() as db:
            task_ds = TaskDatastore(db)
            event_ds = TaskEventDatastore(db)
            row = await task_ds.get_task(OWNER, "t-stamp")

            def _mut(p: TaskPlan) -> bool:
                p.update_node("a", status="in_progress")
                return True

            assert (
                await planning.persist_plan(
                    task_ds,
                    event_ds,
                    row,
                    mutate=_mut,
                    actor="system",
                    session_id=None,
                    user_id=OWNER,
                )
                is not None
            )

    asyncio.run(_run())

    db = db_factory()
    try:
        snap = [
            e
            for e in db.execute(select(TaskEventRow).order_by(TaskEventRow.sequence)).scalars()
            if e.type == "task_plan_update"
        ][-1]
        # Seeded at version 3; this write installed 4 — NOT the raced-ahead 5.
        assert snap.payload["plan_version"] == 4, snap.payload["plan_version"]
    finally:
        db.close()


def test_abandoned_write_is_not_reported_as_a_declined_mutation(db_factory, monkeypatch) -> None:
    """Exhausting the CAS retries must raise, not return None.

    None is ``mutate`` declining — a legitimate no-op with nothing to write.
    Losing every round is a valid write that did not land. Collapsing the two
    made every caller's None-branch lie about which one happened.
    """
    _seed_task(db_factory, task_id="t-exhaust")
    _never_wins(monkeypatch)

    async def _run() -> None:
        async with async_unit_of_work() as db:
            row = await TaskDatastore(db).get_task(OWNER, "t-exhaust")
            with pytest.raises(PlanConflictError):
                await planning.persist_plan(
                    TaskDatastore(db),
                    TaskEventDatastore(db),
                    row,
                    mutate=lambda p: bool(p.update_node("a", status="in_progress") or True),
                    actor="system",
                    session_id=None,
                    user_id=OWNER,
                )

    asyncio.run(_run())


def test_review_under_contention_says_retry_not_no_such_key(db_factory, monkeypatch) -> None:
    """The lead must be told to retry — not that its key is gone.

    ``no subtask with key 'a'`` steers the model into re-planning the subtask
    it just reviewed. The node is right there; only the write was lost.
    """
    _seed_task(db_factory, task_id="t-review")

    # The node must be reviewable before contention is even reachable.
    db = db_factory()
    try:
        row = db.get(TaskRow, "t-review")
        plan = TaskPlan.from_dict(row.plan)
        plan.update_node("a", status="in_progress")
        plan.update_node("a", status="in_review")
        row.plan = plan.to_dict()
        db.commit()
    finally:
        db.close()

    _never_wins(monkeypatch)

    out = asyncio.run(
        planning.review_subtask(
            task_id="t-review",
            project_id="w1",
            lead_session_id="lead",
            decision="approve",
            subtask_key="a",
            user_id=OWNER,
        )
    )
    assert "no subtask" not in out["error"], out["error"]
    assert "review_subtask again" in out["error"], out["error"]


def test_sweeps_tolerate_a_lost_write_and_name_what_diverged(db_factory, monkeypatch) -> None:
    """Bookkeeping sweeps keep going — aborting them mid-sequence leaves a
    worse state than a stale node. The tolerance is explicit (a different
    function) rather than an ignored return value, and the log names the
    inconsistency rather than the failed call."""
    _seed_task(db_factory, task_id="t-sweep")
    _never_wins(monkeypatch)

    async def _run() -> planning.TaskPlan | None:
        async with async_unit_of_work() as db:
            row = await TaskDatastore(db).get_task(OWNER, "t-sweep")
            return await planning.persist_plan_best_effort(
                TaskDatastore(db),
                TaskEventDatastore(db),
                row,
                mutate=lambda p: bool(p.update_node("a", status="in_progress") or True),
                actor="system",
                session_id=None,
                user_id=OWNER,
                diverges="node 'a' stays pre-dispatch",
            )

    assert asyncio.run(_run()) is None


def test_execution_progress_is_not_a_structural_revision(db_factory) -> None:
    """Every plan write bumps the version (the CAS token's job), but only a
    plan-DOCUMENT change is ``structural``. The chat feed spawns a new plan
    card per structural revision and updates in place otherwise — without the
    distinction, each dispatch / in_review / approve would append another card
    to the conversation."""
    _seed_task(db_factory, task_id="t-struct")

    async def _run() -> None:
        await planning.mark_node_dispatched(
            project_id="w1",
            task_id="t-struct",
            subtask_key="a",
            agent="worker",
            session_id="m1",
            user_id=OWNER,
        )
        # A real document change for contrast.
        await planning.modify_plan(
            task_id="t-struct",
            project_id="w1",
            user_id=OWNER,
            lead_session_id="lead",
            add=[{"key": "c", "title": "C", "agent": "worker"}],
        )

    asyncio.run(_run())

    db = db_factory()
    try:
        flags = [
            (e.payload["plan_version"], e.payload["structural"])
            for e in db.execute(
                select(TaskEventRow).order_by(TaskEventRow.sequence)
            ).scalars()
            if e.type == "task_plan_update"
        ]
        assert flags == [(4, False), (5, True)], (
            "node-status progress must not read as a plan revision "
            f"(got {flags})"
        )
    finally:
        db.close()
