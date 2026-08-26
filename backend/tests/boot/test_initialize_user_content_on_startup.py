from __future__ import annotations

from types import SimpleNamespace

import pytest

from valuz_agent.boot import steps
from valuz_agent.infra.config import settings


@pytest.fixture(autouse=True)
def _reset_flag():
    previous = settings.initialize_user_content_on_startup
    yield
    settings.initialize_user_content_on_startup = previous


def test_writer_lock_uses_shared_root_when_startup_user_content_disabled(
    monkeypatch, tmp_path
) -> None:
    settings.initialize_user_content_on_startup = False
    monkeypatch.setattr("valuz_agent.infra.db_urls.is_sqlite_runtime", lambda: True)
    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id",
        lambda: (_ for _ in ()).throw(AssertionError("local identity must not resolve")),
    )
    monkeypatch.setattr("valuz_agent.infra.fs_registry.fs_registry.shared_root", lambda: tmp_path)

    lock_paths = []
    monkeypatch.setattr(
        "valuz_agent.infra.single_writer.acquire_single_writer_lock",
        lambda path: lock_paths.append(path),
    )

    steps.acquire_single_writer_lock()

    assert lock_paths == [tmp_path / ".single-writer.lock"]


def test_oss_main_skips_local_identity_data_dir_when_startup_user_content_disabled(
    monkeypatch, tmp_path
) -> None:
    settings.initialize_user_content_on_startup = False
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.delenv("VALUZ_SANDBOX_DRIVER", raising=False)
    monkeypatch.delenv("VALUZ_BACKEND_BASE_URL", raising=False)
    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id",
        lambda: (_ for _ in ()).throw(AssertionError("local identity must not resolve")),
    )
    monkeypatch.setattr("valuz_agent.modules.system.service.record_boot_started", lambda: None)
    monkeypatch.setattr(
        "valuz_agent.modules.system.service.record_listen_port", lambda _port: None
    )

    from valuz_agent import main as main_mod

    uvicorn_calls = []
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda *args, **kwargs: uvicorn_calls.append(1))

    assert main_mod.main(["--port", "9123"]) == 0
    assert (tmp_path / "data").is_dir()
    assert uvicorn_calls == [1]


def test_oss_main_skips_boot_sandbox_when_startup_user_content_disabled(
    monkeypatch,
) -> None:
    settings.initialize_user_content_on_startup = False
    monkeypatch.setenv("VALUZ_SANDBOX_DRIVER", "seatbelt")
    monkeypatch.setattr(
        "valuz_agent.integrations.sandbox_registry.get",
        lambda _name: (_ for _ in ()).throw(AssertionError("sandbox must not resolve")),
    )

    from valuz_agent import main as main_mod

    main_mod._provision_sandboxed_kernel(SimpleNamespace(host="127.0.0.1", port=8000))


def test_browser_cli_prefix_does_not_create_bin_dir_for_read(monkeypatch, tmp_path) -> None:
    settings.initialize_user_content_on_startup = False
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setenv("PATH", "/usr/bin")

    from valuz_agent.modules.browser import service

    assert "chrome-devtools" in service.cli_prefix()
    assert not (tmp_path / "data" / "bin").exists()


def test_local_identity_skipped_when_startup_user_content_disabled(monkeypatch) -> None:
    settings.initialize_user_content_on_startup = False
    calls: list[str | None] = []

    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id",
        lambda: (_ for _ in ()).throw(AssertionError("local identity must not resolve")),
    )
    monkeypatch.setattr("valuz_agent.infra.auth_context.set_current_user_id", calls.append)

    steps.ensure_local_identity()

    assert calls == [None]


def test_local_identity_uses_startup_flag_not_deployment_type(monkeypatch) -> None:
    settings.initialize_user_content_on_startup = True
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    calls: list[str | None] = []

    monkeypatch.setattr("valuz_agent.infra.local_identity.resolve_local_user_id", lambda: "u-1")
    monkeypatch.setattr("valuz_agent.infra.auth_context.set_current_user_id", calls.append)

    steps.ensure_local_identity()

    assert calls == ["u-1"]


@pytest.mark.asyncio
async def test_bootstrap_schema_skips_seed_and_backfill_when_startup_user_content_disabled(
    monkeypatch,
) -> None:
    settings.initialize_user_content_on_startup = False
    calls: list[str] = []

    monkeypatch.setattr(settings, "deployment_type", "local")
    monkeypatch.setattr(settings, "kernel_mode", "http")
    monkeypatch.setattr("valuz_agent.boot.schema.run_host_migrations", lambda: calls.append("host"))
    monkeypatch.setattr("valuz_agent.infra.logging.configure_logging", lambda: calls.append("log"))
    monkeypatch.setattr(
        "valuz_agent.seeds.seed_all",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("seed_all must not run")
        ),
    )
    monkeypatch.setattr(
        "valuz_agent.boot.backfill_connector_fs.backfill_connector_fs",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("backfill must not run")
        ),
    )

    await steps.bootstrap_schema()

    assert calls == ["host", "log"]


@pytest.mark.asyncio
async def test_configure_i18n_uses_startup_flag_not_deployment_type(monkeypatch) -> None:
    settings.initialize_user_content_on_startup = True
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    calls: list[str] = []

    class Uow:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    async def get_default_locale(_db, *, user_id: str) -> str:
        calls.append(f"locale:{user_id}")
        return "zh-CN"

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", lambda **_kwargs: Uow())
    monkeypatch.setattr("valuz_agent.infra.local_identity.resolve_local_user_id", lambda: "u-1")
    monkeypatch.setattr(
        "valuz_agent.modules.settings.preferences.get_default_locale", get_default_locale
    )
    monkeypatch.setattr("valuz_agent.i18n.set_locale", lambda locale: calls.append(locale))

    await steps.configure_i18n()

    assert calls == ["locale:u-1", "zh-CN"]


@pytest.mark.asyncio
async def test_init_kernel_skips_browser_cli_bootstrap_when_startup_user_content_disabled(
    monkeypatch,
) -> None:
    settings.initialize_user_content_on_startup = False
    monkeypatch.setattr(settings, "kernel_mode", "http")

    monkeypatch.setattr(
        "valuz_agent.modules.browser.service.node_available",
        lambda: True,
    )
    monkeypatch.setattr(
        "valuz_agent.modules.browser.service.ensure_cli_on_path",
        lambda: (_ for _ in ()).throw(AssertionError("browser CLI must not install")),
    )
    monkeypatch.setattr(
        "valuz_agent.integrations.toolkit_mcp_server.install_toolkit_toolsets",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        "valuz_agent.integrations.tools_agent_proposal.build_agent_proposal_tool_defs",
        lambda: (),
    )
    monkeypatch.setattr(
        "valuz_agent.integrations.tools_skill_creator.build_submit_skill_tool_defs",
        lambda: (),
    )
    monkeypatch.setattr("valuz_agent.modules.browser.tools.build_browser_tool_defs", lambda: ())
    monkeypatch.setattr("valuz_agent.modules.memory.tools.build_memory_tool_defs", lambda: ())
    monkeypatch.setattr(
        "valuz_agent.modules.projects.tools.build_project_instructions_tool_defs",
        lambda: (),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.sessions.artifacts_tool.build_deliver_artifacts_tool_defs",
        lambda: (),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.tasks.tools.handlers.build_task_tool_defs",
        lambda _orchestrator: (),
    )

    app = SimpleNamespace(state=SimpleNamespace())

    await steps.init_kernel(app)


@pytest.mark.asyncio
async def test_start_skills_skipped_when_startup_user_content_disabled(monkeypatch) -> None:
    settings.initialize_user_content_on_startup = False
    monkeypatch.setattr(
        "valuz_agent.api.deps.get_skill_service_for_user",
        lambda: (_ for _ in ()).throw(AssertionError("skill service must not resolve")),
    )

    app = SimpleNamespace(state=SimpleNamespace())
    await steps.start_skills(app)

    assert not hasattr(app.state, "skill_watcher")


@pytest.mark.asyncio
async def test_start_skills_uses_startup_flag_not_deployment_type(monkeypatch) -> None:
    settings.initialize_user_content_on_startup = True
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    calls: list[str] = []

    class SkillSvc:
        async def index_official_skills(self, owner: str) -> int:
            calls.append(f"official:{owner}")
            return 1

        async def startup_scan(self, owner: str) -> int:
            calls.append(f"scan:{owner}")
            return 1

    async def get_skill_service(user_id: str):
        del user_id
        yield SkillSvc()

    class SkillFileWatcher:
        def __init__(self, _reindex) -> None:
            calls.append("watcher")

        def add_path(self, _path) -> None:  # noqa: ANN001
            calls.append("watch-path")

        async def start(self) -> None:
            calls.append("watch-start")

    monkeypatch.setattr("valuz_agent.infra.local_identity.resolve_local_user_id", lambda: "u-1")
    monkeypatch.setattr(
        "valuz_agent.integrations.skills_official_bootstrap.sync_bundled_official_skills",
        lambda owner: calls.append(f"sync:{owner}"),
    )
    monkeypatch.setattr("valuz_agent.api.deps.get_skill_service_for_user", get_skill_service)
    monkeypatch.setattr("valuz_agent.infra.file_watcher.SkillFileWatcher", SkillFileWatcher)
    monkeypatch.setattr(
        "valuz_agent.integrations.skills_filesystem._default_user_skill_root",
        lambda owner: SimpleNamespace(exists=lambda: False),
    )

    app = SimpleNamespace(state=SimpleNamespace())
    await steps.start_skills(app)

    assert calls[:4] == ["sync:u-1", "official:u-1", "scan:u-1", "watcher"]
    assert hasattr(app.state, "skill_watcher")


@pytest.mark.asyncio
async def test_start_automation_runtime_uses_bound_port(monkeypatch) -> None:
    settings.initialize_user_content_on_startup = False
    calls: list[str] = []

    from valuz_agent.ports.extensions import ext

    monkeypatch.setattr(
        ext.automation_runtime,
        "startup",
        lambda: _async_call(calls, "automation-runtime"),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.docs.scheduler.start_auto_discovery",
        lambda: (_ for _ in ()).throw(AssertionError("docs scanner must not start")),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.skills.scheduler.start_skill_auto_scan",
        lambda: (_ for _ in ()).throw(AssertionError("skill scanner must not start")),
    )

    await steps.start_automation_runtime(SimpleNamespace())

    assert calls == ["automation-runtime"]


@pytest.mark.asyncio
async def test_host_background_services_skip_scanners_when_disabled(monkeypatch) -> None:
    settings.initialize_user_content_on_startup = False
    calls: list[str] = []

    monkeypatch.setattr(
        "valuz_agent.modules.tasks.recovery.task_health_monitor.startup",
        lambda: _async_call(calls, "task-health"),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.docs.scheduler.start_auto_discovery",
        lambda: (_ for _ in ()).throw(AssertionError("docs scanner must not start")),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.skills.scheduler.start_skill_auto_scan",
        lambda: (_ for _ in ()).throw(AssertionError("skill scanner must not start")),
    )

    await steps.start_host_background_services(SimpleNamespace())

    assert calls == ["task-health"]


@pytest.mark.asyncio
async def test_host_background_services_do_not_start_agent_channels(monkeypatch) -> None:
    settings.initialize_user_content_on_startup = True
    calls: list[str] = []

    monkeypatch.setattr(
        "valuz_agent.modules.tasks.recovery.task_health_monitor.startup",
        lambda: _async_call(calls, "task-health"),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.docs.scheduler.start_auto_discovery",
        lambda: calls.append("docs-scan"),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.skills.scheduler.start_skill_auto_scan",
        lambda: calls.append("skill-scan"),
    )
    monkeypatch.setattr(
        "valuz_agent.modules.backup.scheduler.start_backup_scheduler",
        lambda: calls.append("backup"),
    )
    monkeypatch.setattr(
        "valuz_agent.integrations.wecom_aibot_long_connection.wecom_aibot_supervisor.startup",
        lambda: (_ for _ in ()).throw(AssertionError("channels start only post boot")),
    )
    monkeypatch.setattr(
        "valuz_agent.integrations.feishu_long_connection.feishu_supervisor.startup",
        lambda: (_ for _ in ()).throw(AssertionError("channels start only post boot")),
    )

    await steps.start_host_background_services(SimpleNamespace())

    assert calls == ["task-health", "docs-scan", "skill-scan", "backup"]


@pytest.mark.asyncio
async def test_post_boot_agent_channels_starts_after_boot(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "valuz_agent.integrations.wecom_aibot_long_connection.wecom_aibot_supervisor.startup",
        lambda: _async_call(calls, "wecom-aibot"),
    )
    monkeypatch.setattr(
        "valuz_agent.integrations.feishu_long_connection.feishu_supervisor.startup",
        lambda: _async_call(calls, "feishu"),
    )

    await steps.start_post_boot_agent_channels(SimpleNamespace())

    assert calls == ["wecom-aibot", "feishu"]


@pytest.mark.asyncio
async def test_bind_data_service_skips_local_owner_secret_when_startup_user_content_disabled(
    monkeypatch,
) -> None:
    settings.initialize_user_content_on_startup = False
    calls: list[str] = []

    monkeypatch.setenv("KERNEL_STORE", "pg")
    monkeypatch.setenv("VALUZ_DURABLE_DATABASE_URL", "postgresql+asyncpg://example/db")
    monkeypatch.setattr(
        "valuz_agent.infra.local_identity.resolve_local_user_id",
        lambda: (_ for _ in ()).throw(AssertionError("local identity must not resolve")),
    )
    monkeypatch.setattr(
        "valuz_agent.infra.data_service_secret.get_or_create_ds_secret",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("data-service secret must not be pre-created")
        ),
    )
    monkeypatch.setattr(
        "valuz_agent.boot.kernel.build_host_data_service_store",
        lambda dsn: (object(), object()),
    )
    monkeypatch.setattr(
        "valuz_agent.boot.kernel.ensure_host_data_service_schema",
        lambda _engine: _async_call(calls, "schema"),
    )
    monkeypatch.setattr(
        "valuz_agent.ports.sandbox_credential.get_sandbox_credential_verifier",
        lambda: "verifier",
    )
    monkeypatch.setattr("valuz_agent.adapters.data_reader.bind_data_reader", lambda _reader: None)

    app = SimpleNamespace(
        state=SimpleNamespace(data_service_app=SimpleNamespace(state=SimpleNamespace()))
    )

    await steps.bind_data_service(app)

    assert calls == ["schema"]
    assert app.state.data_service_app.state.verifier == "verifier"


async def _async_call(calls: list[str], value: str) -> None:
    calls.append(value)
