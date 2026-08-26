"""TaskPlan helper — DAG validation, ready computation, status mapping (VALUZ-TASK S1)."""

from __future__ import annotations

import pytest

from valuz_agent.modules.tasks.plan import PlanError, Subtask, TaskPlan, panel_status


def _node(key: str, depends_on: list[str] | None = None, status: str = "planned") -> dict:
    return {
        "key": key,
        "title": f"node {key}",
        "goal": f"do {key}",
        "agent": "researcher",
        "depends_on": depends_on or [],
        "status": status,
    }


def test_should_round_trip_to_and_from_dict() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a"), _node("b", ["a"])]})
    again = TaskPlan.from_dict(plan.to_dict())
    assert [n.key for n in again.nodes] == ["a", "b"]


def test_should_return_empty_plan_for_none_or_empty() -> None:
    assert TaskPlan.from_dict(None).is_empty
    assert TaskPlan.from_dict({}).is_empty
    assert TaskPlan.from_dict({"subtasks": []}).is_empty


def test_should_compute_ready_as_planned_nodes_with_all_deps_done() -> None:
    plan = TaskPlan.from_dict(
        {
            "subtasks": [
                _node("a", status="done"),
                _node("b", ["a"]),  # dep done → ready
                _node("c", ["b"]),  # dep not done → not ready
            ]
        }
    )
    assert plan.ready_keys() == ["b"]


def test_should_treat_no_dependency_planned_node_as_ready() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a"), _node("b")]})
    assert plan.ready_keys() == ["a", "b"]


def test_should_exclude_non_planned_nodes_from_ready() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a", status="in_progress")]})
    assert plan.ready_keys() == []


def test_should_treat_paused_node_as_ready_for_resume() -> None:
    # A node parked by a user pause/stop (whose member run didn't survive) must
    # be re-dispatchable on resume — else the lead is stuck (VALUZ pause/stop).
    plan = TaskPlan.from_dict({"subtasks": [_node("a", status="paused")]})
    assert plan.ready_keys() == ["a"]


def test_should_allow_dispatching_a_paused_node() -> None:
    from valuz_agent.modules.tasks.planning import resolve_dispatch_node

    plan = TaskPlan.from_dict({"subtasks": [_node("a", status="paused")]})
    resolved = resolve_dispatch_node(plan, "a", None, None)
    assert resolved == ("researcher", "do a")  # (agent, goal) — not the error string


def test_should_reject_duplicate_keys() -> None:
    with pytest.raises(PlanError, match="duplicate"):
        TaskPlan.from_dict({"subtasks": [_node("a"), _node("a")]})


def test_should_reject_dependency_on_unknown_key() -> None:
    with pytest.raises(PlanError, match="unknown key"):
        TaskPlan.from_dict({"subtasks": [_node("a", ["ghost"])]})


def test_should_reject_self_dependency() -> None:
    with pytest.raises(PlanError, match="itself"):
        TaskPlan.from_dict({"subtasks": [_node("a", ["a"])]})


def test_should_detect_dependency_cycle() -> None:
    with pytest.raises(PlanError, match="cycle"):
        TaskPlan.from_dict({"subtasks": [_node("a", ["b"]), _node("b", ["a"])]})


def test_should_reject_missing_key() -> None:
    with pytest.raises(PlanError, match="key"):
        TaskPlan.from_dict({"subtasks": [{"title": "no key"}]})


def test_should_reject_invalid_status() -> None:
    with pytest.raises(PlanError, match="status"):
        TaskPlan.from_dict({"subtasks": [_node("a", status="bogus")]})


def test_should_update_node_status() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a")]})
    for st in ("in_progress", "in_review", "done"):  # the happy-path chain
        plan.update_node("a", status=st)
    node = plan.get("a")
    assert node is not None and node.status == "done"


def test_update_node_enforces_transition_table() -> None:
    """Illegal jumps are refused at the one choke point every writer uses.

    The two that matter most: ``failed`` can never be WRITTEN (a failed-stamped
    node is not 'unresolved', so it silently passes the finish_task(completed)
    guard — planned work skipped by relabeling), and a never-dispatched node
    can't be approved straight to ``done``.
    """
    plan = TaskPlan.from_dict({"subtasks": [_node("a")]})
    with pytest.raises(PlanError, match="illegal subtask transition"):
        plan.update_node("a", status="done")  # planned → done (approve w/o dispatch)
    with pytest.raises(PlanError, match="illegal subtask transition"):
        plan.update_node("a", status="failed")  # failed is not a write target
    plan.update_node("a", status="in_progress")
    with pytest.raises(PlanError, match="illegal subtask transition"):
        plan.update_node("a", status="planned")  # no rewind
    # done is terminal
    plan.update_node("a", status="in_review")
    plan.update_node("a", status="done")
    with pytest.raises(PlanError, match="illegal subtask transition"):
        plan.update_node("a", status="rework")
    # same-status writes stay no-ops (feedback updates ride them)
    plan.update_node("a", status="done")


def test_update_node_allows_legacy_failed_revival() -> None:
    """The ONE edge out of ``failed`` — modify_plan can revive a legacy
    stranded node back to ``planned`` (dispatchable)."""
    node = _node("a")
    node["status"] = "failed"  # legacy row, written before enforcement
    plan = TaskPlan.from_dict({"subtasks": [node]})
    plan.update_node("a", status="planned")
    got = plan.get("a")
    assert got is not None and got.status == "planned"


def test_should_raise_when_updating_unknown_node() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a")]})
    with pytest.raises(PlanError, match="no subtask"):
        plan.update_node("ghost", status="done")


def test_should_add_nodes_and_revalidate() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a")]})
    plan.add([_node("b", ["a"])])
    assert [n.key for n in plan.nodes] == ["a", "b"]


def test_should_reject_update_that_creates_cycle() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a")]})
    plan.add([_node("b", ["a"])])  # b depends on a
    with pytest.raises(PlanError, match="cycle"):
        plan.update_node("a", depends_on=["b"])  # a→b→a cycle


def test_should_remove_node() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a"), _node("b")]})
    plan.remove("a")
    assert [n.key for n in plan.nodes] == ["b"]


def test_all_done_true_only_when_every_node_done() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a", status="done")]})
    assert plan.all_done()
    plan.add([_node("b")])
    assert not plan.all_done()


def test_unresolved_keys_covers_every_non_settled_status() -> None:
    plan = TaskPlan.from_dict(
        {
            "subtasks": [
                _node("planned", status="planned"),
                _node("running", status="in_progress"),
                _node("reviewing", status="in_review"),
                _node("redo", status="rework"),
                _node("parked", status="paused"),
                _node("finished", status="done"),
                _node("dead", status="failed"),
            ]
        }
    )
    assert plan.unresolved_keys() == ["planned", "running", "reviewing", "redo", "parked"]


def test_unresolved_keys_counts_paused_as_outstanding() -> None:
    """Regression: a node parked by a user pause/stop is NOT settled work.

    ``ready_keys`` has always treated ``paused`` as dispatchable. The three
    inline copies of this predicate (lead idle check / auto-finalize /
    finish_task guard) omitted it, so a task whose parked node was never
    re-dispatched closed as ``completed`` with a subtask that never ran.
    """
    plan = TaskPlan.from_dict({"subtasks": [_node("a", status="paused")]})
    assert plan.unresolved_keys() == ["a"]
    assert plan.ready_keys() == ["a"]  # the two views must agree
    assert not plan.all_done()


def test_unresolved_keys_empty_for_empty_plan() -> None:
    # A lead that satisfied a simple goal inline never planned anything and
    # must still be allowed to finish — unlike ``all_done()``, which is False.
    plan = TaskPlan.from_dict(None)
    assert plan.unresolved_keys() == []
    assert not plan.all_done()


def test_to_panel_maps_internal_status_to_four_panel_states() -> None:
    plan = TaskPlan.from_dict(
        {
            "subtasks": [
                _node("a", status="planned"),
                _node("b", status="in_progress"),
                _node("c", status="in_review"),
                _node("d", status="rework"),
                _node("e", status="done"),
                _node("f", status="failed"),
            ]
        }
    )
    statuses = {row["key"]: row["status"] for row in plan.to_panel()}
    assert statuses == {
        "a": "pending",
        "b": "active",
        "c": "active",
        "d": "active",
        "e": "completed",
        "f": "failed",
    }


def test_to_panel_uses_title_as_label_and_carries_deps() -> None:
    plan = TaskPlan.from_dict({"subtasks": [_node("a"), _node("b", ["a"])]})
    rows = {row["key"]: row for row in plan.to_panel()}
    assert rows["a"]["label"] == "node a"
    assert rows["b"]["depends_on"] == ["a"]


def test_panel_status_helper() -> None:
    assert panel_status("planned") == "pending"
    assert panel_status("done") == "completed"
    assert panel_status("unknown") == "active"  # defensive default


def test_subtask_from_dict_defaults_title_to_key() -> None:
    st = Subtask.from_dict({"key": "x"})
    assert st.title == "x" and st.status == "planned" and st.attempts == 0


def test_update_node_coerces_like_from_dict() -> None:
    """``modify_plan`` feeds MODEL-SUPPLIED patch dicts straight into
    ``update_node``, so the two ways a node gets built must agree on types.

    Without coercion ``attempts="many"`` went through ``setattr`` untouched,
    serialized into the persisted plan JSON, and detonated a turn later at
    ``mark_node_dispatched``'s ``attempts + 1`` — a TypeError raised by a
    dispatch on a plan that looks perfectly fine.
    """
    plan = TaskPlan.from_dict({"subtasks": [_node("a"), _node("b")]})

    with pytest.raises(PlanError, match="'attempts' must be an integer"):
        plan.update_node("a", attempts="many")
    with pytest.raises(PlanError, match="'depends_on' must be a list"):
        plan.update_node("a", depends_on="ab")
    # ``key`` cannot even be expressed as a patch — it is the positional
    # selector, so Python rejects the collision before the body runs.
    with pytest.raises(TypeError):
        plan.update_node("a", **{"key": "renamed"})

    # Valid patches still behave, and are normalized the same as from_dict:
    plan.update_node("a", attempts=3, depends_on=["b"], title=None, agent="")
    node = plan.get("a")
    assert node is not None
    assert node.attempts == 3
    assert node.depends_on == ["b"]
    assert node.title == ""  # str(value or "") — never None
    assert node.agent is None  # falsy → None, not ""
