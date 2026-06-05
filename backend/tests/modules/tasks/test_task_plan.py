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
    plan.update_node("a", status="done")
    node = plan.get("a")
    assert node is not None and node.status == "done"


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
