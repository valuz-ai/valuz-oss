from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from valuz_agent.modules.sessions import capabilities, pre_turn, task_checks
from valuz_agent.ports.capability_policy import OptionalCheckOverrides, TaskCheckConfig
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.message_context import HostRef


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    session = SimpleNamespace(
        id="session",
        user_id="owner",
        status="idle",
        skills=[],
        instructions="Test",
        metadata={"other": {"keep": True}, "valuz": {"project_id": "project"}},
    )
    path = tmp_path / "citation"
    path.mkdir()
    monkeypatch.setattr(
        "valuz_agent.adapters.capability_resolver.citation_skill_dir", lambda _: path
    )
    monkeypatch.setattr(capabilities.kernel_client, "get_session", AsyncMock(return_value=session))

    async def update(user_id, session_id, body):
        assert (user_id, session_id) == ("owner", "session")
        session.metadata = body.metadata
        session.instructions = body.instructions
        session.skills = body.skills
        return session

    monkeypatch.setattr(capabilities.kernel_client, "update_session", update)

    @asynccontextmanager
    async def uow(**kwargs):
        yield None

    monkeypatch.setattr(capabilities, "async_unit_of_work", uow)
    preferences = {"citation": True, "verification": True, "coverage": True}
    for fn, key in (
        ("citations", "citation"),
        ("verification", "verification"),
        ("task_coverage", "coverage"),
    ):

        async def pref(db, user_id, key=key):
            assert user_id == "owner"
            return preferences[key]

        monkeypatch.setattr(
            f"valuz_agent.modules.settings.preferences.get_conversation_{fn}_enabled", pref
        )
    monkeypatch.setattr(ext, "task_check_policies", [])
    monkeypatch.setattr(ext, "host_capability_policies", [])
    return session, preferences


class ConfigPolicy:
    async def resolve(self, context):
        assert context.user_id == "owner"
        assert context.project_id == "project"
        if context.config.operation == "layout.configure":
            return OptionalCheckOverrides(
                citation_enabled=False,
                verification_enabled=False,
                task_coverage_enabled=False,
            )
        return None


async def refresh(config=None, **kwargs):
    await capabilities.refresh_citation_policy_for_session(
        "session", "owner", task_check_config=config, **kwargs
    )


async def test_fresh_operation_does_not_inherit_previous_session_policy(runtime):
    session, _ = runtime
    ext.task_check_policies.append(ConfigPolicy())
    for operation, expected in (
        ("layout.configure", False),
        ("conversation", True),
        ("layout.configure", False),
    ):
        config = task_checks.fresh_config(TaskCheckConfig(operation=operation))
        await refresh(config)
        valuz = session.metadata["valuz"]
        assert valuz["task_coverage_enabled"] is expected
        assert valuz["citation_enabled"] is expected
        assert valuz["citation_verification_enabled"] is expected
        assert valuz[task_checks.SNAPSHOT_KEY]["context"]["config"]["run_id"] == config.run_id
        assert "task_coverage_host_override" not in valuz
    assert session.metadata["other"] == {"keep": True}


async def test_resume_keeps_original_flags_but_new_input_reads_current_preferences(runtime):
    session, preferences = runtime
    config = task_checks.fresh_config()
    await refresh(config)
    preferences.update(citation=False, verification=False, coverage=False)
    await refresh(resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is True
    assert session.metadata["valuz"][task_checks.SNAPSHOT_KEY]["context"]["config"] == (
        config.model_dump(mode="json")
    )
    await refresh(task_checks.fresh_config())
    assert session.metadata["valuz"]["task_coverage_enabled"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_id", "other"),
        ("session_id", "fork"),
        ("project_id", "other"),
        ("task_id", "other"),
    ],
)
async def test_foreign_or_forked_snapshot_never_disables_checks(runtime, field, value):
    session, _ = runtime
    await refresh(
        task_checks.fresh_config(
            TaskCheckConfig(
                overrides=OptionalCheckOverrides(
                    citation_enabled=False,
                    verification_enabled=False,
                    task_coverage_enabled=False,
                )
            )
        )
    )
    session.metadata["valuz"][task_checks.SNAPSHOT_KEY]["context"][field] = value
    await refresh(resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is True


@pytest.mark.parametrize("raw", [False, {}, {"version": 2}, {"version": 1, "resolved": {}}])
async def test_invalid_snapshot_falls_back_to_current_preferences(runtime, raw):
    session, _ = runtime
    session.metadata["valuz"][task_checks.SNAPSHOT_KEY] = raw
    session.metadata["valuz"]["task_coverage_host_override"] = False
    await refresh(resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is True
    assert "task_coverage_host_override" not in session.metadata["valuz"]


async def test_different_explicit_run_cannot_reuse_snapshot(runtime):
    session, _ = runtime
    await refresh(
        TaskCheckConfig(
            run_id="first",
            overrides=OptionalCheckOverrides(
                task_coverage_enabled=False,
            ),
        )
    )
    await refresh(TaskCheckConfig(run_id="second"), resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is True


async def test_explicit_flags_win_over_registered_policy_and_persist(runtime):
    session, _ = runtime
    ext.task_check_policies.append(ConfigPolicy())
    await refresh(
        TaskCheckConfig(operation="layout.configure"), task_coverage_enabled_override=True
    )
    assert session.metadata["valuz"]["task_coverage_enabled"] is True
    await refresh(resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is True


async def test_first_opinion_per_check_wins_and_bad_provider_grants_nothing(runtime):
    session, _ = runtime

    class Coverage:
        async def resolve(self, context):
            return OptionalCheckOverrides(task_coverage_enabled=False)

    class Broken:
        async def resolve(self, context):
            return {"citation_enabled": "false"}

    ext.task_check_policies.extend([Coverage(), Broken(), ConfigPolicy()])
    await refresh(TaskCheckConfig(operation="conversation"))
    assert session.metadata["valuz"]["task_coverage_enabled"] is False
    assert session.metadata["valuz"]["citation_enabled"] is True


async def test_live_other_host_policy_is_reevaluated_after_retired_stamp(runtime):
    session, _ = runtime
    session.metadata["valuz"]["task_coverage_host_override"] = False

    class OtherPolicy:
        def task_coverage_override(self, host_ref):
            return host_ref.host_type != "other.disable"

    ext.host_capability_policies.append(OtherPolicy())
    await refresh(TaskCheckConfig(), host_ref=HostRef("other.enable", "x"))
    assert session.metadata["valuz"]["task_coverage_enabled"] is True
    await refresh(TaskCheckConfig(), host_ref=HostRef("other.disable", "x"))
    assert session.metadata["valuz"]["task_coverage_enabled"] is False
    assert "task_coverage_host_override" not in session.metadata["valuz"]


async def test_task_resume_rechecks_revision_but_retains_matching_snapshot(runtime, monkeypatch):
    session, _ = runtime
    session.metadata["valuz"]["task_id"] = "task"
    task = SimpleNamespace(
        project_id="project",
        metadata_={
            task_checks.CONFIG_KEY: TaskCheckConfig(
                origin="automation",
                run_id="run",
                automation_id="automation",
                overrides=OptionalCheckOverrides(task_coverage_enabled=False),
            ).model_dump(mode="json"),
        },
    )
    lookup = AsyncMock(return_value=(task, []))
    monkeypatch.setattr("valuz_agent.modules.tasks.service.get_task_with_runs", lookup)
    await refresh(resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is False
    lookup.assert_awaited_once_with("owner", "task")
    await refresh(resume_task_checks=True)
    assert lookup.await_count == 2
    assert session.metadata["valuz"]["task_coverage_enabled"] is False


async def test_invalid_owned_task_config_is_safe(runtime, monkeypatch):
    session, _ = runtime
    session.metadata["valuz"]["task_id"] = "task"
    lookup = AsyncMock(
        return_value=(
            SimpleNamespace(
                project_id="project",
                metadata_={
                    task_checks.CONFIG_KEY: {"overrides": {"task_coverage_enabled": "false"}},
                },
            ),
            [],
        )
    )
    monkeypatch.setattr("valuz_agent.modules.tasks.service.get_task_with_runs", lookup)
    await refresh(resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is True


@pytest.mark.parametrize("run_kind", ["lead", "subtask"])
async def test_revised_goal_invalidates_lead_and_member_policy(runtime, monkeypatch, run_kind):
    from valuz_agent.modules.tasks.service import TaskService

    session, _ = runtime
    session.metadata["valuz"].update(task_id="task", run_kind=run_kind)
    task = SimpleNamespace(
        id="task",
        project_id="project",
        goal="layout",
        metadata_={
            "keep": True,
            task_checks.CONFIG_KEY: task_checks.fresh_config(
                TaskCheckConfig(
                    origin="task",
                    run_id="task",
                    operation="layout.configure",
                )
            ).model_dump(mode="json"),
        },
    )
    lookup = AsyncMock(return_value=(task, []))
    monkeypatch.setattr("valuz_agent.modules.tasks.service.get_task_with_runs", lookup)
    ext.task_check_policies.append(ConfigPolicy())
    await refresh(resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is False
    old_revision = task.metadata_[task_checks.CONFIG_KEY]["revision"]

    svc = TaskService.__new__(TaskService)
    svc._db = None
    svc._tasks = SimpleNamespace(update_task=AsyncMock())
    svc._events = SimpleNamespace(append_event=AsyncMock())
    monkeypatch.setattr(
        "valuz_agent.modules.tasks.messaging.notify_lead_goal_revised",
        AsyncMock(return_value={"delivered": True}),
    )
    await svc.revise_goal("owner", task, "research company")
    assert task.metadata_["keep"] is True
    assert task.metadata_[task_checks.CONFIG_KEY]["run_id"] == "task"
    assert task.metadata_[task_checks.CONFIG_KEY]["revision"] != old_revision
    await refresh(resume_task_checks=True)
    assert session.metadata["valuz"]["task_coverage_enabled"] is True


@pytest.mark.parametrize("failure", ["preferences", "write"])
async def test_failed_convergence_cannot_run_with_stale_disabled_flags(
    runtime, monkeypatch, failure
):
    from valuz_agent.adapters.kernel_client import RequiredPreTurnError

    session, _ = runtime
    await refresh(TaskCheckConfig(overrides=OptionalCheckOverrides(task_coverage_enabled=False)))
    broken = AsyncMock(side_effect=RuntimeError("store unavailable"))
    if failure == "preferences":
        monkeypatch.setattr(
            "valuz_agent.modules.settings.preferences.get_conversation_task_coverage_enabled",
            broken,
        )
    else:
        monkeypatch.setattr(capabilities.kernel_client, "update_session", broken)
    hook = pre_turn.chat_capability_hook("session", "owner")
    with pytest.raises(RequiredPreTurnError, match="current task check policy"):
        await hook()
    # Old storage is not fabricated away; the allocation hook must refuse
    # model dispatch until a later successful convergence replaces it.
    assert session.metadata["valuz"]["task_coverage_enabled"] is False


async def test_recovered_actor_converges_after_allocation_and_reuses_saved_run(
    runtime, monkeypatch
):
    from valuz_agent.modules.tasks.actor_runner import ActorRunner

    session, preferences = runtime
    session.metadata["valuz"].update(task_id="task", run_kind="lead")
    task = SimpleNamespace(
        project_id="project",
        metadata_={
            task_checks.CONFIG_KEY: TaskCheckConfig(
                origin="task",
                run_id="run",
                overrides=OptionalCheckOverrides(
                    task_coverage_enabled=False,
                ),
            ).model_dump(mode="json")
        },
    )
    lookup = AsyncMock(return_value=(task, []))
    monkeypatch.setattr("valuz_agent.modules.tasks.service.get_task_with_runs", lookup)
    order = []

    async def restamp(*args):
        order.append("restamp")

    monkeypatch.setattr(pre_turn, "restamp_always_on_mcp", restamp)

    async def execute(*args, pre_turn=None, **kwargs):
        order.append("allocate")
        await pre_turn()
        snapshot = session.metadata["valuz"][task_checks.SNAPSHOT_KEY]
        assert snapshot["context"]["config"]["run_id"] == "run"
        assert snapshot["resolved"]["task_coverage_enabled"] is False
        order.append("run")
        return SimpleNamespace(status="completed", stop_reason="end_turn")

    monkeypatch.setattr(capabilities.kernel_client, "run_turn", execute)
    await ActorRunner().run_turn("session", "start", "owner")
    preferences["coverage"] = True
    # A newly constructed runner stands in for process recovery; only the
    # durable session snapshot, not in-process state, retains the decision.
    await ActorRunner().run_turn("session", "continue", "owner")
    assert order == ["allocate", "restamp", "run"] * 2
    assert lookup.await_count == 2
    assert lookup.await_args.args == ("owner", "task")


def test_config_with_existing_run_id_is_deep_copied():
    original = TaskCheckConfig(run_id="run", configuration={"nested": {"enabled": True}})
    frozen = task_checks.fresh_config(original)
    original.configuration["nested"]["enabled"] = False
    assert frozen.configuration == {"nested": {"enabled": True}}


async def test_hook_snapshots_before_allocation_and_keeps_explicit_flags(monkeypatch):
    captured = []

    async def capture(*args, **kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(pre_turn, "_refresh_citation_policy", capture)
    for fn in (
        "_refresh_bundled_skills",
        "_refresh_docs_capabilities",
        "restamp_always_on_mcp",
        "_refresh_ptc",
    ):
        monkeypatch.setattr(pre_turn, fn, AsyncMock())
    config = TaskCheckConfig(configuration={"name": "original"})
    hook = pre_turn.chat_capability_hook(
        "session", "owner", task_check_config=config, task_coverage_enabled_override=False
    )
    config.configuration["name"] = "changed"
    assert captured == []
    await hook()
    assert captured[0]["task_check_config"].configuration == {"name": "original"}
    assert captured[0]["task_check_config"].overrides.task_coverage_enabled is False


def test_queued_input_keeps_per_item_config_and_handles_legacy_rows():
    first = TaskCheckConfig(
        operation="layout.configure",
        overrides=OptionalCheckOverrides(
            task_coverage_enabled=False,
        ),
    )
    for index, config in enumerate((first, TaskCheckConfig(), first)):
        payload = {task_checks.CONFIG_KEY: config.model_dump(mode="json")}
        restored, _ = task_checks.queued_check_input(payload, str(index))
        assert restored.run_id == str(index)
        assert restored.operation == config.operation
        assert restored.overrides == config.overrides
    safe, _ = task_checks.queued_check_input({task_checks.CONFIG_KEY: {"version": 999}}, "old")
    assert safe.overrides.task_coverage_enabled is None
    assert safe.run_id == "old"
