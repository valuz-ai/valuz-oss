"""Plugin-declared automations: created on install, run by name, deleted on uninstall.

Against a real (temporary) managed project and the real automation service; only the
runtime that executes a run (``ext.automation_runtime``) is replaced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.modules.app_plugins.helpers import build_plugin
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.app_plugins.errors import (
    AutomationBusy,
    InvalidAutomationInput,
    PluginAutomationNotFound,
    PluginRunNotFound,
)
from valuz_agent.modules.app_plugins.service import AppPluginService
from valuz_agent.modules.automations.app_plugin_support import (
    APP_PLUGINS_DIRNAME,
    origin_key,
    row_name,
)
from valuz_agent.modules.automations.code_runner import resolve_paths
from valuz_agent.modules.automations.datastore import AutomationDatastore
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.ports.extensions import ext

pytestmark = pytest.mark.usefixtures("db")

USER = "user-1"
PID = "acme.dashboard"
INPUT_SCHEMA = {
    "type": "object",
    "properties": {"portfolioId": {"type": "string"}},
    "required": ["portfolioId"],
}
DECLARED = [
    {
        "name": "risk-summary",
        "title": {"en-US": "Risk summary"},
        "runtime": "python",
        "entry": "automations/risk_summary.py",
        "input": INPUT_SCHEMA,
        "result": "artifact",
        "timeoutSec": 120,
        "trigger": {"cron": "0 8 * * 1-5", "timezone": "Asia/Shanghai"},
    },
    {
        "name": "hourly",
        "runtime": "shell",
        "entry": "automations/hourly.sh",
        "trigger": {"intervalSec": 3600},
    },
    {"name": "on-demand", "runtime": "python", "entry": "automations/on_demand.py"},
]
FILES = {
    "automations/risk_summary.py": "def run(ctx):\n    return {'summary': 'v1'}\n",
    "automations/hourly.sh": "echo hi\n",
    "automations/on_demand.py": "def run(ctx):\n    return {}\n",
    "automations/lib/helper.py": "VALUE = 1\n",
}


@pytest.fixture(autouse=True)
def runtime(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    enqueue = AsyncMock()
    monkeypatch.setattr(ext.automation_runtime, "enqueue", enqueue)
    return enqueue


def plugin_dir(tmp_path: Path, version: str = "1.0.0", **over: Any) -> Path:
    over.setdefault("permissions", ["automations:run"])
    over.setdefault("automations", DECLARED)
    files = {**FILES, **over.pop("files", {})}
    return build_plugin(tmp_path / f"src-{version}", PID, version, files=files, **over)


async def rows(user_id: str = USER, plugin_id: str = PID) -> list[Any]:
    async with async_unit_of_work(commit=False) as db:
        return await AutomationDatastore(db).list_by_app_plugin(user_id, plugin_id)


async def project_cwd(project_id: str) -> Path:
    async with async_unit_of_work(commit=False) as db:
        row = await ProjectDatastore(db).get_by_id(USER, project_id)
    assert row is not None and row.root_path
    return Path(row.root_path)


async def test_install_creates_the_declared_automations_in_a_managed_project(
    svc: AppPluginService, tmp_path: Path
) -> None:
    item = (await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))}))["plugin"]
    ids = {a["name"]: a["automation_id"] for a in item["automations"]}
    assert set(ids) == {"risk-summary", "hourly", "on-demand"} and all(ids.values())
    assert item["automations"][0]["runtime"] == "python"
    assert item["automations"][0]["trigger"] == DECLARED[0]["trigger"]

    created = {r.id: r for r in await rows()}
    assert set(created) == set(ids.values())
    risk = created[ids["risk-summary"]]
    assert risk.app_plugin_id == PID and risk.app_plugin_name == "Acme Dashboard"
    assert risk.name == "Acme Dashboard · Risk summary"
    assert risk.origin_tool_call_id == origin_key(PID, "risk-summary")
    assert risk.execution_kind == "code" and risk.code_runtime == "python"
    assert risk.code_entry == f"{APP_PLUGINS_DIRNAME}/{PID}/automations/risk_summary.py"
    assert risk.code_timeout_s == 120
    assert (risk.trigger_kind, risk.cron_expr, risk.timezone) == (
        "cron",
        "0 8 * * 1-5",
        "Asia/Shanghai",
    )
    assert risk.input_kind == "json" and risk.input_schema == INPUT_SCHEMA
    assert risk.result_kind == "artifact" and risk.status == "enabled"
    hourly = created[ids["hourly"]]
    assert (hourly.trigger_kind, hourly.interval_seconds, hourly.code_runtime) == (
        "interval",
        3600,
        "shell",
    )
    assert created[ids["on-demand"]].trigger_kind == "manual"
    assert created[ids["on-demand"]].input_kind == "none"

    # one visible managed project holds all of them, with the scripts copied in
    assert len({r.project_id for r in created.values()}) == 1
    async with async_unit_of_work(commit=False) as db:
        project = await ProjectDatastore(db).get_by_id(USER, risk.project_id)
    assert project is not None and project.kind == "project"
    assert project.name == "Plugin: Acme Dashboard"
    cwd = await project_cwd(risk.project_id)
    script = cwd / APP_PLUGINS_DIRNAME / PID / "automations" / "risk_summary.py"
    assert script.read_text() == FILES["automations/risk_summary.py"]
    assert (cwd / APP_PLUGINS_DIRNAME / PID / "automations" / "lib" / "helper.py").is_file()
    # the existing code runner accepts the entry as is
    paths = resolve_paths(
        project_cwd=cwd, automation_id=risk.id, run_id="r1", entry=risk.code_entry
    )
    assert paths.entry == script.resolve()


async def test_another_user_gets_their_own_automations(
    svc: AppPluginService, tmp_path: Path
) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    assert len(await rows("user-2")) == 0
    assert (await svc.automations("user-2", PID))["automations"][0]["automation_id"] is None


async def test_list_and_latest_run(svc: AppPluginService, tmp_path: Path) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    listing = (await svc.automations(USER, PID))["automations"]
    assert [a["name"] for a in listing] == ["risk-summary", "hourly", "on-demand"]
    first = listing[0]
    assert first["status"] == "enabled" and first["latest_run"] is None
    assert first["runtime"] == "python" and first["trigger"] == DECLARED[0]["trigger"]
    assert listing[2]["trigger"] == "manual"
    with pytest.raises(PluginRunNotFound):
        await svc.latest_automation_run(USER, PID, "risk-summary")
    with pytest.raises(PluginAutomationNotFound):
        await svc.latest_automation_run(USER, PID, "nope")


async def test_run_by_name(svc: AppPluginService, tmp_path: Path, runtime: AsyncMock) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    with pytest.raises(PluginAutomationNotFound):
        await svc.run_automation(USER, PID, "nope")
    with pytest.raises(InvalidAutomationInput):
        await svc.run_automation(USER, PID, "risk-summary", input={"portfolioId": 7})
    runtime.assert_not_awaited()

    started = await svc.run_automation(USER, PID, "risk-summary", input={"portfolioId": "p1"})
    assert started["status"] == "queued" and started["run_id"] and started["automation_id"]
    command = runtime.await_args.args[0]
    assert (command.user_id, command.automation_id, command.run_id) == (
        USER,
        started["automation_id"],
        started["run_id"],
    )
    # a second start while the first is queued / running is a conflict
    with pytest.raises(AutomationBusy) as busy:
        await svc.run_automation(USER, PID, "risk-summary", input={"portfolioId": "p1"})
    assert busy.value.status_code == 409 and busy.value.code == "automation_already_running"

    latest = await svc.latest_automation_run(USER, PID, "risk-summary")
    assert latest["run_id"] == started["run_id"] and latest["status"] == "queued"
    assert latest["input"] == {"portfolioId": "p1"} and latest["trigger_type"] == "api"
    assert (await svc.automation_run(USER, PID, started["run_id"]))["run_id"] == started["run_id"]
    listing = (await svc.automations(USER, PID))["automations"]
    assert listing[0]["latest_run"]["run_id"] == started["run_id"]

    other = await svc.run_automation(USER, PID, "on-demand")
    assert other["status"] == "queued"
    with pytest.raises(InvalidAutomationInput):  # a 'none' automation takes no input
        await svc.run_automation(USER, PID, "hourly", input={"x": 1})


async def test_wait_seconds_returns_the_run_as_it_stands(
    svc: AppPluginService, tmp_path: Path
) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    result = await svc.run_automation(USER, PID, "on-demand", wait_seconds=1)
    assert result["run"]["run_id"] == result["run_id"]
    assert result["status"] == result["run"]["status"] == "queued"  # nothing executes it here


async def test_a_run_must_belong_to_one_of_the_plugins_automations(
    svc: AppPluginService, tmp_path: Path
) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    other_src = build_plugin(
        tmp_path / "other",
        "acme.other",
        "1.0.0",
        files=FILES,
        permissions=["automations:run"],
        automations=[DECLARED[2]],
    )
    await svc.install(USER, {"source_path": str(other_src)})
    foreign = await svc.run_automation(USER, "acme.other", "on-demand")
    with pytest.raises(PluginRunNotFound):
        await svc.automation_run(USER, PID, foreign["run_id"])
    with pytest.raises(PluginRunNotFound):
        await svc.automation_run(USER, PID, "no-such-run")
    assert (await svc.automation_run(USER, "acme.other", foreign["run_id"]))["run_id"] == (
        foreign["run_id"]
    )


async def test_update_changes_rows_in_place_and_deletes_dropped_ones(
    svc: AppPluginService, tmp_path: Path
) -> None:
    first = (await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))}))["plugin"]
    ids = {a["name"]: a["automation_id"] for a in first["automations"]}
    started = await svc.run_automation(USER, PID, "risk-summary", input={"portfolioId": "p1"})

    declared = [
        {**DECLARED[0], "trigger": {"cron": "0 9 * * *", "timezone": "UTC"}, "timeoutSec": 60},
        DECLARED[2],
        {"name": "added", "runtime": "python", "entry": "automations/added.py"},
    ]
    second = plugin_dir(
        tmp_path,
        "1.1.0",
        automations=declared,
        files={
            "automations/added.py": "def run(ctx): return {}\n",
            "automations/risk_summary.py": "def run(ctx):\n    return {'summary': 'v2'}\n",
        },
    )
    result = (await svc.install(USER, {"source_path": str(second)}))["plugin"]
    new_ids = {a["name"]: a["automation_id"] for a in result["automations"]}
    assert new_ids["risk-summary"] == ids["risk-summary"]  # updated in place
    assert new_ids["on-demand"] == ids["on-demand"]
    assert new_ids["added"] and new_ids["added"] not in ids.values()

    current = {r.id: r for r in await rows()}
    assert set(current) == set(new_ids.values())  # "hourly" is gone
    risk = current[ids["risk-summary"]]
    assert (risk.cron_expr, risk.timezone, risk.code_timeout_s) == ("0 9 * * *", "UTC", 60)
    cwd = await project_cwd(risk.project_id)
    scripts = cwd / APP_PLUGINS_DIRNAME / PID / "automations"
    assert "v2" in (scripts / "risk_summary.py").read_text()
    assert (scripts / "added.py").is_file()
    # run history survives the update
    latest = await svc.latest_automation_run(USER, PID, "risk-summary")
    assert latest["run_id"] == started["run_id"]


async def test_disable_pauses_and_enable_resumes(svc: AppPluginService, tmp_path: Path) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    await svc.set_enabled(USER, PID, False)
    assert {r.status for r in await rows()} == {"paused"}
    await svc.set_enabled(USER, PID, True)
    assert {r.status for r in await rows()} == {"enabled"}
    # installing a plugin disabled creates its automations paused
    await svc.uninstall(USER, PID)
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))}, enable=False)
    assert {r.status for r in await rows()} == {"paused"}


async def test_uninstall_deletes_the_automations_and_their_scripts(
    svc: AppPluginService, tmp_path: Path
) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    any_row = (await rows())[0]
    cwd = await project_cwd(any_row.project_id)
    assert (cwd / APP_PLUGINS_DIRNAME / PID).is_dir()
    result = await svc.uninstall(USER, PID)
    assert result == {"removed": True, "automations_deleted": 3}
    assert await rows() == []
    assert not (cwd / APP_PLUGINS_DIRNAME / PID).exists()


async def test_a_deleted_project_is_replaced_on_the_next_sync(
    svc: AppPluginService, tmp_path: Path
) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    before = (await rows())[0].project_id
    async with async_unit_of_work() as db:  # what deleting the project does to its automations
        ds = AutomationDatastore(db)
        await ds.delete_all_for_project(USER, before)
        await ProjectDatastore(db).delete(USER, before)
    assert await rows() == []
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path, "1.1.0"))})
    after = await rows()
    assert len(after) == 3 and after[0].project_id != before


async def test_a_plugin_that_drops_all_automations_cleans_up(
    svc: AppPluginService, tmp_path: Path
) -> None:
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path, "1.1.0", automations=[]))})
    assert await rows() == []


async def test_a_failed_sync_is_logged_and_does_not_fail_the_install(
    svc: AppPluginService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.modules.app_plugins import logs
    from valuz_agent.modules.automations import app_plugin_support

    async def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("no project for you")

    monkeypatch.setattr(app_plugin_support.AppPluginAutomations, "sync", boom)
    item = (await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))}))["plugin"]
    assert item["status"] == "enabled"
    assert all(a["automation_id"] is None for a in item["automations"])
    entry = logs.read_log(PID)[-1]
    assert entry["level"] == "error" and "no project for you" in entry["message"]
    assert entry["source"] == "backend"


def test_row_names_fit_the_automation_name_limit() -> None:
    assert row_name("Acme", "Risk") == "Acme · Risk"
    long = row_name("A" * 80, "Risk summary")
    assert len(long) <= 50 and long.endswith(" · Risk summary")
    assert len(row_name("Acme", "t" * 80)) == 50


async def test_catalog_job_uses_its_saved_namespace_and_job_owner_after_another_install(
    svc: AppPluginService,
    tmp_path: Path,
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import hashlib

    from tests.modules.app_plugins.helpers import zip_dir
    from valuz_agent.ports.app_plugins import PolicyVerdict

    user_a, user_b = USER, "user-2"
    root = plugin_dir(tmp_path / "source")
    archive = zip_dir(root, tmp_path / "catalog.zip")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    source_a = {
        "kind": "catalog",
        "scope": "org",
        "owner_key": "org-a",
        "origin_scope": "org",
        "origin_owner_key": "org-a",
        "item_id": PID,
        "installed_by_user_id": user_a,
    }
    source_b = {
        "kind": "catalog",
        "scope": "org",
        "owner_key": "org-b",
        "origin_scope": "org",
        "origin_owner_key": "org-b",
        "item_id": PID,
        "installed_by_user_id": user_b,
    }
    await svc.install_archive(
        user_a, str(archive), source=source_a, expected_sha256=sha, verified_catalog=True
    )
    first = await rows(user_a)
    await svc.install_archive(
        user_b,
        str(archive),
        source=source_b,
        expected_sha256=sha,
        verified_catalog=True,
        allow_reinstall=True,
    )
    second = await rows(user_b)
    assert first and second
    assert first[0].app_plugin_catalog_binding["owner_key"] == "org-a"
    assert second[0].app_plugin_catalog_binding["owner_key"] == "org-b"
    calls = []

    class ScopedPolicy:
        async def evaluate(self, user_id, item):
            return PolicyVerdict(allowed=True)

        async def authorize_catalog_automation(self, user_id, binding):
            calls.append((user_id, binding["owner_key"]))
            return PolicyVerdict(allowed=binding["owner_key"] == "org-b", reason="recalled")

    monkeypatch.setattr(ext, "app_plugin_policy", ScopedPolicy())
    async with async_unit_of_work() as session:
        from valuz_agent.modules.automations.app_plugin_support import build_service

        service = await build_service(session, user_a)
        on_demand = next(row for row in first if row.code_entry.endswith("on_demand.py"))
        with pytest.raises(Exception, match="recalled"):
            await service.run_now(on_demand.id, user_id=user_a)
    assert calls == [(user_a, "org-a")]

    async with async_unit_of_work() as session:
        service = await build_service(session, user_b)
        on_demand = next(row for row in second if row.code_entry.endswith("on_demand.py"))
        accepted = await service.run_now(on_demand.id, user_id=user_b)
        assert accepted.run_id
    assert calls == [(user_a, "org-a"), (user_b, "org-b")]


async def test_queued_catalog_code_rechecks_owner_authority_before_executor(
    svc, tmp_path, monkeypatch
) -> None:
    import hashlib

    from tests.modules.app_plugins.helpers import zip_dir
    from valuz_agent.modules.automations import code_runner
    from valuz_agent.modules.automations.app_plugin_support import build_service
    from valuz_agent.ports.app_plugins import PolicyVerdict

    root = plugin_dir(tmp_path)
    archive = zip_dir(root, tmp_path / "signed.zip")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    source = {
        "kind": "catalog",
        "scope": "org",
        "owner_key": "org-a",
        "origin_scope": "global",
        "origin_owner_key": "global",
        "item_id": PID,
        "installed_by_user_id": USER,
    }
    await svc.install_archive(
        USER, str(archive), source=source, expected_sha256=sha, verified_catalog=True
    )
    authorized = True
    calls = []

    class LivePolicy:
        async def evaluate(self, user_id, plugin):
            return PolicyVerdict(allowed=True)

        async def authorize_catalog_automation(self, user_id, binding):
            calls.append((user_id, binding["owner_key"], binding["origin_scope"]))
            return PolicyVerdict(allowed=authorized, reason="origin revoked")

    monkeypatch.setattr(ext, "app_plugin_policy", LivePolicy())
    async with async_unit_of_work() as db:
        row = next(
            row
            for row in await AutomationDatastore(db).list_by_app_plugin(USER, PID)
            if row.code_entry.endswith("on_demand.py")
        )
        service = await build_service(db, USER)
        run = await service.run_now(row.id, user_id=USER)
        saved_run = await AutomationDatastore(db).get_run(USER, row.id, run.run_id)
        authorized = False
        paths = AsyncMock()
        monkeypatch.setattr(code_runner, "resolve_project_cwd", paths)
        result = await code_runner.run_code_automation(
            user_id=USER,
            row=row,
            run=saved_run,
            effective_input=None,
            previous=None,
            timezone="UTC",
            locale="en-US",
        )
        assert result.status == "failed" and result.error_code == "app_plugin_source_unavailable"
        assert "origin revoked" in result.error_message
        paths.assert_not_called()
    assert calls == [(USER, "org-a", "global"), (USER, "org-a", "global")]


async def test_unknown_managed_source_refuses_but_known_file_job_runs(svc, tmp_path) -> None:
    from valuz_agent.modules.automations.app_plugin_authorization import (
        AppPluginAutomationForbiddenError,
    )
    from valuz_agent.modules.automations.app_plugin_support import build_service

    await svc.install(USER, {"source_path": str(plugin_dir(tmp_path))})
    async with async_unit_of_work() as db:
        row = next(
            row
            for row in await AutomationDatastore(db).list_by_app_plugin(USER, PID)
            if row.code_entry.endswith("on_demand.py")
        )
        service = await build_service(db, USER)
        row.app_plugin_source_kind = None
        await db.flush()
        with pytest.raises(AppPluginAutomationForbiddenError, match="unconfirmed"):
            await service.run_now(row.id, user_id=USER)
        row.app_plugin_source_kind = "file"
        await db.flush()
        assert (await service.run_now(row.id, user_id=USER)).run_id


async def test_verified_reinstall_keeps_off_state_and_paused_jobs(svc, tmp_path) -> None:
    import hashlib
    import json

    from tests.modules.app_plugins.helpers import zip_dir
    from valuz_agent.modules.app_plugins.errors import InvalidSource, VersionNotNewer

    config_schema = {"type": "object", "properties": {"region": {"type": "string"}}}
    root = plugin_dir(tmp_path, config=config_schema, permissions=["automations:run", "storage"])
    archive = zip_dir(root, tmp_path / "signed.zip")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    source = {
        "kind": "catalog",
        "scope": "org",
        "owner_key": "org-a",
        "origin_scope": "org",
        "origin_owner_key": "org-a",
        "item_id": PID,
        "installed_by_user_id": USER,
    }
    await svc.install_archive(
        USER, str(archive), source=source, expected_sha256=sha, verified_catalog=True
    )
    await svc.storage_put(USER, PID, "saved", {"value": "retained"})
    await svc.put_config(USER, PID, {"region": "hk"})
    before = await rows()
    await svc.set_enabled(USER, PID, False)
    new_source = {**source, "owner_key": "org-b", "origin_owner_key": "org-b"}
    result = await svc.install_archive(
        USER,
        str(archive),
        source=new_source,
        expected_sha256=sha,
        verified_catalog=True,
        allow_reinstall=True,
    )
    assert result["plugin"]["enabled"] is False
    after = await rows()
    assert {row.id for row in before} == {row.id for row in after}
    assert {row.project_id for row in before} == {row.project_id for row in after}
    assert all(
        row.status == "paused" and row.app_plugin_catalog_binding["owner_key"] == "org-b"
        for row in after
    )
    from valuz_agent.modules.app_plugins.models import AppPluginConfigRow, AppPluginStorageRow

    async with async_unit_of_work(commit=False) as db:
        assert json.loads((await db.get(AppPluginConfigRow, (USER, PID))).values_json) == {
            "region": "hk"
        }
        assert (
            await db.get(AppPluginStorageRow, (USER, PID, "saved"))
        ).value_json == '{"value":"retained"}'
    with pytest.raises(InvalidSource):
        await svc.install_archive(
            USER, str(archive), source=new_source, expected_sha256=sha, allow_reinstall=True
        )
    with pytest.raises(VersionNotNewer):
        await svc.install(
            USER, {"source_path": str(archive), "verified_catalog": True, "allow_reinstall": True}
        )
    other = zip_dir(
        plugin_dir(tmp_path / "other", files={"automations/on_demand.py": "print('different')"}),
        tmp_path / "other.zip",
    )
    other_sha = hashlib.sha256(other.read_bytes()).hexdigest()
    assert other_sha != sha
    with pytest.raises(VersionNotNewer):
        await svc.install_archive(
            USER,
            str(other),
            source=new_source,
            expected_sha256=other_sha,
            verified_catalog=True,
            allow_reinstall=True,
        )


async def test_stale_catalog_recall_cannot_remove_new_namespace(svc, tmp_path) -> None:
    import hashlib

    from tests.modules.app_plugins.helpers import zip_dir

    root = plugin_dir(tmp_path)
    archive = zip_dir(root, tmp_path / "signed.zip")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    a = {
        "kind": "catalog",
        "scope": "org",
        "owner_key": "org-a",
        "origin_scope": "org",
        "origin_owner_key": "org-a",
        "item_id": PID,
        "installed_by_user_id": USER,
    }
    await svc.install_archive(
        USER, str(archive), source=a, expected_sha256=sha, verified_catalog=True
    )
    expected = {
        "app_plugin_id": PID,
        "version": "1.0.0",
        "sha256": sha,
        "scope": "org",
        "owner_key": "org-a",
        "origin_scope": "org",
        "origin_owner_key": "org-a",
    }
    b = {**a, "owner_key": "org-b", "origin_owner_key": "org-b", "installed_by_user_id": "user-b"}
    await svc.install_archive(
        "user-b",
        str(archive),
        source=b,
        expected_sha256=sha,
        verified_catalog=True,
        allow_reinstall=True,
    )
    before = await rows("user-b")
    reply = await svc.uninstall(USER, PID, expected_catalog_binding=expected)
    assert reply == {"removed": False, "automations_deleted": 0}
    current = (await svc.list("user-b"))["plugins"][0]
    assert current["source"]["owner_key"] == "org-b"
    assert {row.id for row in before} == {row.id for row in await rows("user-b")}
    assert (
        Path(current["root_path"]).exists()
        if "root_path" in current
        else bool(current["entry_url"])
    )


@pytest.mark.parametrize(
    "field",
    ["app_plugin_id", "app_plugin_name", "app_plugin_source_kind", "app_plugin_catalog_binding"],
)
def test_public_automation_dtos_refuse_forged_managed_source(field) -> None:
    from valuz_agent.modules.automations.schemas import (
        AutomationCreatePayload,
        AutomationUpdatePayload,
    )

    body = {
        "name": "Ordinary",
        "project_kind": "project",
        "project_id": "p",
        "execution": {"kind": "code", "runtime": "python", "entry": "x.py"},
        "trigger": {"kind": "manual"},
    }
    assert AutomationCreatePayload.model_validate(body)
    assert AutomationUpdatePayload.model_validate({"name": "Ordinary"})
    for model, payload in (
        (AutomationCreatePayload, body),
        (AutomationUpdatePayload, {"name": "Ordinary"}),
    ):
        with pytest.raises(ValueError, match="ownership is internal"):
            model.model_validate({**payload, field: {"owner_key": "attacker"}})


async def test_matching_disk_recall_preserves_same_users_other_publication_jobs_and_scripts(
    svc, tmp_path, monkeypatch
) -> None:
    import hashlib

    from tests.modules.app_plugins.helpers import zip_dir
    from valuz_agent.modules.automations.app_plugin_support import build_service
    from valuz_agent.ports.app_plugins import PolicyVerdict

    archive = zip_dir(plugin_dir(tmp_path), tmp_path / "signed.zip")
    sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    source_b = {
        "kind": "catalog",
        "scope": "org",
        "owner_key": "org-b",
        "origin_scope": "org",
        "origin_owner_key": "org-b",
        "item_id": PID,
        "installed_by_user_id": USER,
    }
    b = {
        "app_plugin_id": PID,
        "version": "1.0.0",
        "sha256": sha,
        "scope": "org",
        "owner_key": "org-b",
        "origin_scope": "org",
        "origin_owner_key": "org-b",
    }
    a = {
        **b,
        "scope": "global",
        "owner_key": "global",
        "origin_scope": "global",
        "origin_owner_key": "global",
    }
    await svc.install_archive(
        USER, str(archive), source=source_b, expected_sha256=sha, verified_catalog=True
    )
    async with async_unit_of_work() as db:
        saved = await AutomationDatastore(db).list_by_app_plugin(USER, PID)
        preserved = next(row for row in saved if row.code_entry.endswith("on_demand.py"))
        preserved.app_plugin_catalog_binding = a
        await db.flush()
        preserved_id, project_id, entry = preserved.id, preserved.project_id, preserved.code_entry
    cwd = await project_cwd(project_id)
    before_script = (cwd / entry).read_bytes()
    reply = await svc.uninstall(USER, PID, expected_catalog_binding=b)
    assert reply == {"removed": True, "automations_deleted": len(saved) - 1}
    remaining = await rows()
    assert [row.id for row in remaining] == [preserved_id]
    assert remaining[0].app_plugin_catalog_binding == a
    assert (cwd / entry).read_bytes() == before_script

    class Policy:
        async def authorize_catalog_automation(self, user_id, binding):
            assert user_id == USER and binding == a
            return PolicyVerdict(allowed=True)

    monkeypatch.setattr(ext, "app_plugin_policy", Policy())
    async with async_unit_of_work() as db:
        service = await build_service(db, USER)
        assert (await service.run_now(preserved_id, user_id=USER)).run_id
