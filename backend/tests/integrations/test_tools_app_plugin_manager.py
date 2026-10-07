"""``app_plugin_manager`` harness tool + the ``app_plugin.*`` operations behind its cards.

``app_plugin_service`` (agent BE-1's) is replaced by a recording fake: this suite
pins what the TOOL does — path rules, the confirmation-card envelope, what a
confirm hands the service — not how the service installs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.core.tools import ExecContext

import valuz_agent.boot.kernel  # noqa: F401
import valuz_agent.infra.db as db_mod
from valuz_agent.infra.config import settings
from valuz_agent.infra.database import Base
from valuz_agent.infra.errors import NotFoundError
from valuz_agent.integrations import tools_app_plugin_manager as tool
from valuz_agent.modules.app_plugins import operations as ext_ops
from valuz_agent.modules.app_plugins.errors import PluginNotFound, Sha256Mismatch
from valuz_agent.modules.app_plugins.service import app_plugin_service
from valuz_agent.modules.operations.models import ConfirmationDecisionRow, OperationRecordRow
from valuz_agent.modules.operations.service import OperationService
from valuz_agent.ports.extensions import ext

USER = "user-A"
SESSION = "sess-1"


# ── Fakes ────────────────────────────────────────────────────────────────


def _manifest(plugin_id: str = "acme.dash", version: str = "1.2.0") -> dict[str, Any]:
    return {
        "id": plugin_id,
        "version": version,
        "name": {"en-US": "Dash"},
        "publisher": {"name": "Acme"},
        "permissions": ["projects:read"],
    }


def _item(plugin_id: str = "acme.dash", **extra: Any) -> dict[str, Any]:
    return {
        "id": plugin_id,
        "version": "1.2.0",
        "name": {"en-US": "Dash"},
        "publisher": {"name": "Acme"},
        "source": {"kind": "dev", "path": "/somewhere"},
        "status": "enabled",
        "status_reason": None,
        "enabled": True,
        "permissions": ["projects:read"],
        "revision": 3,
        "locales": {"en-US": {"big": "x" * 50}},
        **extra,
    }


class FakeService:
    """Records every call; ``inspect`` answers with ``info`` unless it is a dict
    keyed by source path / url."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.info: dict[str, Any] = {
            "manifest": _manifest(),
            "sha256": "a" * 64,
            "size": 1234,
            "permissions": ["projects:read"],
            "requires": [],
            "unmet_requires": [],
            "errors": [],
            "warnings": [],
            "has_backend": False,
            "existing": None,
            "added_permissions": [],
        }
        self.plugins: list[dict[str, Any]] = [_item()]
        self.packed_to: Path | None = None
        self.validate_result: dict[str, Any] = {"ok": True, "errors": [], "warnings": []}

    def _rec(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]

    async def list(self, user_id: str) -> dict[str, Any]:
        self._rec("list", user_id)
        return {
            "api_version": "1.0.0",
            "safe_mode": False,
            "safe_mode_reason": None,
            "generation": 7,
            "plugins": self.plugins,
        }

    async def inspect(self, source: dict[str, Any]) -> dict[str, Any]:
        self._rec("inspect", source)
        return self.info

    async def install(self, user_id: str, source: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        self._rec("install", user_id, source, **kwargs)
        return {"plugin": _item(source="file"), "updated_from": None}

    async def dev_link(self, user_id: str, path: str) -> dict[str, Any]:
        self._rec("dev_link", user_id, path)
        return {"plugin": _item(dev_path=path)}

    async def reload(self, plugin_id: str) -> dict[str, Any]:
        self._rec("reload", plugin_id)
        return {"plugin": _item(revision=4)}

    async def uninstall(
        self, user_id: str, plugin_id: str, *, purge_data: bool = False
    ) -> dict[str, Any]:
        self._rec("uninstall", user_id, plugin_id, purge_data=purge_data)
        return {"removed": True, "automations_deleted": 2}

    def validate(self, path: str) -> dict[str, Any]:
        self._rec("validate", path)
        return self.validate_result

    def pack(self, path: str, out_dir: str | None = None) -> dict[str, Any]:
        self._rec("pack", path, out_dir)
        target = Path(out_dir) if out_dir else Path(path)
        target.mkdir(parents=True, exist_ok=True)
        archive = target / "acme.dash-1.2.0.zip"
        archive.write_bytes(b"PK-fake-zip-bytes")
        self.packed_to = archive
        return {
            "path": str(archive),
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size": archive.stat().st_size,
        }


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, user_id: str, **kwargs: Any) -> dict[str, Any]:
        self.published.append({"user_id": user_id, **kwargs})
        return {"id": "sub-1", "state": "pending"}

    async def submissions(self, user_id: str) -> list[dict[str, Any]]:
        return [{"id": "sub-1", "state": "pending", "user": user_id}]


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    for name in (
        "list",
        "inspect",
        "install",
        "dev_link",
        "reload",
        "uninstall",
        "validate",
        "pack",
    ):
        monkeypatch.setattr(app_plugin_service, name, getattr(fake, name))
    monkeypatch.setattr(settings, "deployment_type", "local")
    return fake


@pytest.fixture
async def ops_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    db_file = tmp_path / "ops.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(
        sync_engine, tables=[OperationRecordRow.__table__, ConfirmationDecisionRow.__table__]
    )
    sync_engine.dispose()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    sessions = async_sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", sessions)
    yield sessions
    await engine.dispose()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path.resolve()


def _ctx(workspace: Path | str = "", session_id: str = SESSION) -> ExecContext:
    return ExecContext(workspace=str(workspace), session_id=session_id, user_id=USER)


async def _call(ctx: ExecContext, **args: Any) -> tuple[dict[str, Any], bool]:
    result = await tool._handler(args, ctx)
    return json.loads(result.content), result.is_error


def _plugin_dir(base: Path, name: str = "acme-dash") -> Path:
    path = base / name
    (path / "frontend").mkdir(parents=True)
    (path / "valuz-plugin.json").write_text("{}")
    return path


async def _confirm(sessions: Any, envelope: dict[str, Any]) -> OperationRecordRow:
    op = envelope["operation"]
    async with sessions() as db:
        row = await OperationService(db, SimpleNamespace()).confirm(  # type: ignore[arg-type]
            USER, op["id"], expected_proposal_hash=op["proposal_hash"]
        )
        await db.commit()
        return row


# ── The tool definition ──────────────────────────────────────────────────


def test_tool_def_teaches_the_flow_and_is_in_the_toolkit() -> None:
    (tdef,) = tool.build_app_plugin_manager_tool_defs()
    assert tdef.name == "app_plugin_manager"
    assert set(tdef.parameters["properties"]["action"]["enum"]) == {
        "status",
        "validate",
        "pack",
        "dev_link",
        "install",
        "uninstall",
        "reload",
        "logs",
        "publish",
        "submissions",
    }
    for needle in ("valuz-plugin-dev", "validate", "dev_link", "pack", "install", "confirmation"):
        assert needle in tdef.description
    assert "logs" in tdef.description and "publish" in tdef.description

    from valuz_agent.features.toolkit import TOOL_GROUPS, app_plugin_manager_tool_defs

    assert "app-plugin-manager" in TOOL_GROUPS
    assert [t.name for t in app_plugin_manager_tool_defs()] == ["app_plugin_manager"]


async def test_unknown_action(service: FakeService) -> None:
    body, is_error = await _call(_ctx(), action="explode")
    assert is_error and body["ok"] is False and body["error_code"] == "invalid_action"


# ── Read-only actions ────────────────────────────────────────────────────


async def test_status_lists_trimmed_summaries_and_one_by_id(service: FakeService) -> None:
    service.plugins = [_item(), _item("beta.tool")]
    body, is_error = await _call(_ctx(), action="status")
    assert not is_error and body["ok"] is True and body["action"] == "status"
    assert body["generation"] == 7 and body["safe_mode"] is False
    assert [p["id"] for p in body["plugins"]] == ["acme.dash", "beta.tool"]
    assert "locales" not in body["plugins"][0]
    assert body["plugins"][0]["status"] == "enabled" and body["plugins"][0]["revision"] == 3

    one, _ = await _call(_ctx(), action="status", id="beta.tool")
    assert [p["id"] for p in one["plugins"]] == ["beta.tool"]
    missing, is_error = await _call(_ctx(), action="status", id="ghost.none")
    assert is_error and missing["error_code"] == "not_found"


async def test_service_errors_carry_their_stable_code(
    service: FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(user_id: str) -> dict[str, Any]:
        raise PluginNotFound("Plugin not found")

    monkeypatch.setattr(app_plugin_service, "list", boom)
    body, is_error = await _call(_ctx(), action="status")
    assert is_error and body["error_code"] == "not_found"


async def test_validate_resolves_relative_paths_against_the_workspace(
    service: FakeService, workspace: Path
) -> None:
    _plugin_dir(workspace)
    service.validate_result = {"ok": False, "errors": ["frontend.entry missing"], "warnings": []}
    body, is_error = await _call(_ctx(workspace), action="validate", path="acme-dash")
    assert not is_error and body["ok"] is True  # the tool call worked...
    assert body["valid"] is False  # ...the plugin did not validate
    assert body["errors"] == ["frontend.entry missing"]
    assert service.calls[-1] == ("validate", (str(workspace / "acme-dash"),), {})

    absolute, _ = await _call(_ctx(workspace), action="validate", path=str(workspace / "x"))
    assert absolute["path"] == str(workspace / "x")


async def test_relative_path_without_a_workspace_is_refused(service: FakeService) -> None:
    body, is_error = await _call(_ctx(""), action="validate", path="acme-dash")
    assert is_error and body["error_code"] == "no_workspace"


async def test_pack_passes_the_resolved_out_dir(service: FakeService, workspace: Path) -> None:
    _plugin_dir(workspace)
    body, is_error = await _call(
        _ctx(workspace), action="pack", path="acme-dash", out_dir="dist-out"
    )
    assert not is_error and body["path"].endswith("acme.dash-1.2.0.zip")
    assert service.calls[-1] == (
        "pack",
        (str(workspace / "acme-dash"), str(workspace / "dist-out")),
        {},
    )


async def test_logs_tail_the_plugin_log(service: FakeService) -> None:
    from valuz_agent.modules.app_plugins.logs import append_log, delete_log

    plugin_id = "acme.logtest"
    delete_log(plugin_id)
    try:
        for i in range(5):
            append_log(plugin_id, "error" if i == 4 else "info", f"line {i}", "frontend")
        body, is_error = await _call(_ctx(), action="logs", id=plugin_id, limit=3)
        assert not is_error and body["count"] == 3
        assert [e["message"] for e in body["entries"]] == ["line 2", "line 3", "line 4"]
        assert body["entries"][-1]["level"] == "error"
        empty, _ = await _call(_ctx(), action="logs", id="acme.nologs")
        assert empty["entries"] == []
        bad, is_error = await _call(_ctx(), action="logs", id="../escape")
        assert is_error and bad["error_code"] == "invalid_id"
    finally:
        delete_log(plugin_id)


async def test_reload_calls_the_service_without_a_card(service: FakeService) -> None:
    body, is_error = await _call(_ctx(), action="reload", id="acme.dash")
    assert not is_error and body["plugin"]["revision"] == 4
    assert service.calls[-1] == ("reload", ("acme.dash",), {})
    assert "operation" not in body


# ── Local-only gate ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        {"action": "dev_link", "path": "p"},
        {"action": "install", "source_path": "p"},
        {"action": "uninstall", "id": "acme.dash"},
        {"action": "reload", "id": "acme.dash"},
    ],
)
async def test_install_type_actions_are_refused_off_a_local_deployment(
    service: FakeService, workspace: Path, monkeypatch: pytest.MonkeyPatch, args: dict[str, Any]
) -> None:
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    body, is_error = await _call(_ctx(workspace), **args)
    assert is_error and body["error_code"] == "local_only"
    assert not [n for n in service.names() if n not in ("inspect", "list")]
    assert "operation" not in body


async def test_validate_and_publish_still_work_off_a_local_deployment(
    service: FakeService, workspace: Path, monkeypatch: pytest.MonkeyPatch, ops_db: Any
) -> None:
    monkeypatch.setattr(settings, "deployment_type", "cloud")
    monkeypatch.setattr(ext, "app_plugin_publisher", FakePublisher())
    _plugin_dir(workspace)
    validated, _ = await _call(_ctx(workspace), action="validate", path="acme-dash")
    assert validated["ok"] is True and validated["valid"] is True
    published, is_error = await _call(
        _ctx(workspace), action="publish", path="acme-dash", scope="personal"
    )
    assert not is_error and published["operation"]["operation_type"] == "app_plugin.publish"


# ── dev_link ─────────────────────────────────────────────────────────────


async def test_dev_link_proposes_a_card_and_only_the_confirm_links(
    service: FakeService, workspace: Path, ops_db: Any
) -> None:
    plugin = _plugin_dir(workspace)
    body, is_error = await _call(_ctx(workspace), action="dev_link", path="acme-dash")
    assert not is_error and body["ok"] is True and body["action"] == "dev_link"
    assert "confirmation card" in body["message"]
    op = body["operation"]
    assert op["operation_type"] == "app_plugin.dev_link"
    assert op["state"] == "awaiting_confirmation"
    assert op["risk_level"] == "external" and op["confirmation_policy"] == "confirm"
    assert op["origin_session_id"] == SESSION and op["actor_kind"] == "agent"
    assert op["target_refs"] == [{"type": "app_plugin", "id": "acme.dash"}]
    preview = op["preview"]
    assert preview["id"] == "acme.dash" and preview["version"] == "1.2.0"
    assert preview["publisher"] == "Acme" and preview["permissions"] == ["projects:read"]
    assert preview["source"] == {"kind": "dev", "path": str(plugin)}
    assert preview["path"] == str(plugin)
    assert preview["existing"] is None and preview["has_backend"] is False
    assert preview["requires"] == [] and preview["unmet_requires"] == []
    assert preview["live_reload"] is True and preview["sha256"] == "a" * 64
    assert "dev_link" not in service.names(), "nothing is linked before the user confirms"

    # The same request is the same card.
    again, _ = await _call(_ctx(workspace), action="dev_link", path=str(plugin))
    assert again["operation"]["id"] == op["id"]

    done = await _confirm(ops_db, body)
    assert done.state == "succeeded", done.error_message
    assert service.calls[-1] == ("dev_link", (USER, str(plugin)), {})
    assert done.result_payload["plugin_id"] == "acme.dash"
    assert done.canonical_result_refs == [
        {"type": "app_plugin", "id": "acme.dash", "version": "1.2.0"}
    ]

    # After a terminal state the same request is a NEW card.
    fresh, _ = await _call(_ctx(workspace), action="dev_link", path="acme-dash")
    assert fresh["operation"]["id"] != op["id"]
    assert fresh["operation"]["state"] == "awaiting_confirmation"


async def test_dev_link_refuses_a_directory_outside_the_workspace(
    service: FakeService, workspace: Path, tmp_path: Path, ops_db: Any
) -> None:
    outside = _plugin_dir(tmp_path, "elsewhere")
    body, is_error = await _call(_ctx(workspace), action="dev_link", path=str(outside))
    assert is_error and body["ok"] is False and body["error_code"] == "outside_workspace"
    assert str(workspace) in body["message"]

    dotdot, is_error = await _call(_ctx(workspace), action="dev_link", path="../elsewhere")
    assert is_error and dotdot["error_code"] == "outside_workspace"
    assert not service.calls, "no service call for a refused path"


async def test_dev_link_resolves_symlinks_before_judging_the_path(
    service: FakeService, workspace: Path, tmp_path: Path, ops_db: Any
) -> None:
    outside = _plugin_dir(tmp_path, "elsewhere")
    (workspace / "innocent").symlink_to(outside, target_is_directory=True)
    body, is_error = await _call(_ctx(workspace), action="dev_link", path="innocent")
    assert is_error and body["error_code"] == "outside_workspace"

    # A link that stays inside the workspace is fine.
    real = _plugin_dir(workspace, "real")
    (workspace / "alias").symlink_to(real, target_is_directory=True)
    ok, is_error = await _call(_ctx(workspace), action="dev_link", path="alias")
    assert not is_error and ok["operation"]["preview"]["source"]["path"] == str(real)


async def test_dev_link_needs_a_workspace_and_a_directory(
    service: FakeService, workspace: Path, ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.adapters import kernel_client

    async def no_session(user_id: str, session_id: str) -> None:
        return None

    monkeypatch.setattr(kernel_client, "get_session", no_session)
    none, is_error = await _call(_ctx(""), action="dev_link", path=str(workspace))
    assert is_error and none["error_code"] == "no_workspace"

    (workspace / "a-file").write_text("x")
    not_dir, is_error = await _call(_ctx(workspace), action="dev_link", path="a-file")
    assert is_error and not_dir["error_code"] == "not_a_directory"


async def test_the_workspace_falls_back_to_the_kernel_sessions_cwd(
    service: FakeService, workspace: Path, ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The toolkit MCP path builds ``ExecContext`` with only session and user."""
    from valuz_agent.adapters import kernel_client

    async def get_session(user_id: str, session_id: str) -> Any:
        assert (user_id, session_id) == (USER, SESSION)
        return SimpleNamespace(cwd=str(workspace))

    monkeypatch.setattr(kernel_client, "get_session", get_session)
    _plugin_dir(workspace)
    body, is_error = await _call(_ctx(""), action="dev_link", path="acme-dash")
    assert not is_error and body["operation"]["operation_type"] == "app_plugin.dev_link"


async def test_a_swapped_in_symlink_makes_the_confirm_stale(
    service: FakeService, workspace: Path, tmp_path: Path, ops_db: Any
) -> None:
    """The handler re-checks the workspace boundary at confirm time."""
    import shutil

    plugin = _plugin_dir(workspace)
    body, _ = await _call(_ctx(workspace), action="dev_link", path="acme-dash")
    outside = _plugin_dir(tmp_path, "elsewhere")
    shutil.rmtree(plugin)
    plugin.symlink_to(outside, target_is_directory=True)

    done = await _confirm(ops_db, body)
    assert done.state == "stale" and "workspace" in (done.error_message or "")
    assert "dev_link" not in service.names()


async def test_an_invalid_plugin_gets_no_card(
    service: FakeService, workspace: Path, ops_db: Any
) -> None:
    _plugin_dir(workspace)
    service.info = {**service.info, "errors": ["frontend.entry is missing"]}
    body, is_error = await _call(_ctx(workspace), action="dev_link", path="acme-dash")
    assert is_error and body["error_code"] == "invalid_manifest"
    assert body["errors"] == ["frontend.entry is missing"]
    assert "operation" not in body


async def test_no_session_means_no_card(service: FakeService, workspace: Path, ops_db: Any) -> None:
    _plugin_dir(workspace)
    body, is_error = await _call(
        _ctx(workspace, session_id=""), action="dev_link", path="acme-dash"
    )
    assert is_error and body["error_code"] == "no_session"


# ── install ──────────────────────────────────────────────────────────────


async def test_install_card_is_bound_to_the_reviewed_sha256(
    service: FakeService, workspace: Path, ops_db: Any
) -> None:
    archive = workspace / "dist" / "acme.dash-1.2.0.zip"
    archive.parent.mkdir()
    archive.write_bytes(b"zip")
    service.info = {
        **service.info,
        "existing": {"version": "1.1.0"},
        "added_permissions": ["connectors:call"],
        "unmet_requires": ["connector:acme-data"],
        "permissions": ["projects:read", "connectors:call"],
    }
    body, is_error = await _call(
        _ctx(workspace), action="install", source_path="dist/acme.dash-1.2.0.zip"
    )
    assert not is_error and body["action"] == "install"
    op = body["operation"]
    assert op["operation_type"] == "app_plugin.install" and op["risk_level"] == "external"
    preview = op["preview"]
    assert preview["existing"] == {"version": "1.1.0"} and preview["existing_version"] == "1.1.0"
    assert preview["path"] == str(archive)
    assert preview["added_permissions"] == ["connectors:call"]
    assert preview["unmet_requires"] == ["connector:acme-data"]
    assert preview["source"] == {"kind": "file", "path": str(archive)}
    assert preview["sha256"] == "a" * 64
    assert "updating from 1.1.0" in body["message"]
    assert "install" not in service.names()

    done = await _confirm(ops_db, body)
    assert done.state == "succeeded", done.error_message
    assert service.calls[-1] == (
        "install",
        (USER, {"source_path": str(archive)}),
        {"expected_sha256": "a" * 64, "enable": True},
    )


async def test_install_from_a_url_and_argument_checks(
    service: FakeService, workspace: Path, ops_db: Any
) -> None:
    body, is_error = await _call(_ctx(workspace), action="install", url="https://example.com/p.zip")
    assert not is_error
    assert body["operation"]["preview"]["source"] == {
        "kind": "url",
        "url": "https://example.com/p.zip",
    }
    done = await _confirm(ops_db, body)
    assert done.state == "succeeded"
    assert service.calls[-1][1] == (USER, {"url": "https://example.com/p.zip"})

    both, is_error = await _call(
        _ctx(workspace), action="install", url="https://x/y.zip", source_path="z"
    )
    assert is_error and both["error_code"] == "missing_argument"
    neither, is_error = await _call(_ctx(workspace), action="install")
    assert is_error and neither["error_code"] == "missing_argument"
    ftp, is_error = await _call(_ctx(workspace), action="install", url="ftp://x/y.zip")
    assert is_error and ftp["error_code"] == "invalid_url"


async def test_install_may_point_anywhere_readable(
    service: FakeService, workspace: Path, tmp_path: Path, ops_db: Any
) -> None:
    outside = tmp_path / "downloads" / "p.zip"
    outside.parent.mkdir()
    outside.write_bytes(b"zip")
    body, is_error = await _call(_ctx(workspace), action="install", source_path=str(outside))
    assert not is_error and body["operation"]["preview"]["source"]["path"] == str(outside)


async def test_a_sha256_mismatch_at_install_marks_the_operation_stale(
    service: FakeService,
    workspace: Path,
    ops_db: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (workspace / "p.zip").write_bytes(b"zip")
    body, _ = await _call(_ctx(workspace), action="install", source_path="p.zip")

    async def mismatch(user_id: str, source: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        raise Sha256Mismatch()

    monkeypatch.setattr(app_plugin_service, "install", mismatch)
    done = await _confirm(ops_db, body)
    assert done.state == "stale" and done.error_code == "OPERATION_STALE"


# ── uninstall ────────────────────────────────────────────────────────────


async def test_uninstall_is_a_destructive_card(service: FakeService, ops_db: Any) -> None:
    service.plugins = [_item(automations=[{"name": "risk-summary"}])]
    gone, is_error = await _call(_ctx(), action="uninstall", id="ghost.none")
    assert is_error and gone["error_code"] == "not_found"

    body, is_error = await _call(_ctx(), action="uninstall", id="acme.dash", purge_data=True)
    assert not is_error
    op = body["operation"]
    assert op["operation_type"] == "app_plugin.uninstall" and op["risk_level"] == "destructive"
    assert op["preview"]["purge_data"] is True
    assert op["preview"]["automations"] == ["risk-summary"]
    assert "uninstall" not in service.names()

    done = await _confirm(ops_db, body)
    assert done.state == "succeeded"
    assert service.calls[-1] == ("uninstall", (USER, "acme.dash"), {"purge_data": True})
    assert done.result_payload["automations_deleted"] == 2


async def test_service_failures_at_confirm_become_a_failed_operation(
    service: FakeService, ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    body, _ = await _call(_ctx(), action="uninstall", id="acme.dash")

    async def gone(user_id: str, plugin_id: str, **kwargs: Any) -> dict[str, Any]:
        raise NotFoundError("Plugin not found")

    monkeypatch.setattr(app_plugin_service, "uninstall", gone)
    done = await _confirm(ops_db, body)
    assert done.state == "stale" and done.error_code == "OPERATION_TARGET_MISSING"


# ── publish ──────────────────────────────────────────────────────────────


async def test_publish_without_a_publisher_says_it_needs_an_account(
    service: FakeService, workspace: Path, ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ext, "app_plugin_publisher", None)
    _plugin_dir(workspace)
    body, is_error = await _call(
        _ctx(workspace), action="publish", path="acme-dash", scope="personal"
    )
    assert is_error and body["error_code"] == "publish_unavailable"
    assert "Valuz account" in body["message"]
    assert "pack" not in service.names(), "nothing is packed when it cannot be published"
    subs, is_error = await _call(_ctx(), action="submissions")
    assert is_error and subs["error_code"] == "publish_unavailable"


async def test_publish_packs_shows_scope_and_uploads_on_confirm(
    service: FakeService, workspace: Path, ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = FakePublisher()
    monkeypatch.setattr(ext, "app_plugin_publisher", publisher)
    _plugin_dir(workspace)

    body, is_error = await _call(
        _ctx(workspace),
        action="publish",
        path="acme-dash",
        scope="global",
        distribution_ids=["dist-1", " dist-2 "],
        notes="first release",
    )
    assert not is_error and body["action"] == "publish"
    assert service.names().count("pack") == 1 and service.packed_to is not None
    op = body["operation"]
    assert op["operation_type"] == "app_plugin.publish" and op["risk_level"] == "external"
    preview = op["preview"]
    assert preview["scope"] == "global" and preview["distribution_ids"] == ["dist-1", "dist-2"]
    assert preview["id"] == "acme.dash" and preview["notes"] == "first release"
    archive_sha = hashlib.sha256(service.packed_to.read_bytes()).hexdigest()
    assert preview["sha256"] == archive_sha
    assert publisher.published == [], "nothing is uploaded before the user confirms"

    done = await _confirm(ops_db, body)
    assert done.state == "succeeded", done.error_message
    (call,) = publisher.published
    assert call == {
        "user_id": USER,
        "archive_path": str(service.packed_to),
        "scope": "global",
        "distribution_ids": ["dist-1", "dist-2"],
        "origin_session_id": SESSION,
        "notes": "first release",
    }
    assert done.result_payload["submission"] == {"id": "sub-1", "state": "pending"}

    subs, _ = await _call(_ctx(), action="submissions")
    assert subs["submissions"] == [{"id": "sub-1", "state": "pending", "user": USER}]


async def test_publish_refuses_an_archive_changed_after_the_card(
    service: FakeService, workspace: Path, ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    publisher = FakePublisher()
    monkeypatch.setattr(ext, "app_plugin_publisher", publisher)
    _plugin_dir(workspace)
    body, _ = await _call(_ctx(workspace), action="publish", path="acme-dash", scope="org")
    assert service.packed_to is not None
    service.packed_to.write_bytes(b"swapped after review")

    done = await _confirm(ops_db, body)
    assert done.state == "stale" and "changed after" in (done.error_message or "")
    assert publisher.published == []


async def test_publish_of_a_zip_by_id_and_validation(
    service: FakeService, workspace: Path, ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ext, "app_plugin_publisher", FakePublisher())
    # A zip is published as it is: no validate / pack of a directory.
    (workspace / "p.zip").write_bytes(b"zip")
    zipped, is_error = await _call(
        _ctx(workspace), action="publish", path="p.zip", scope="personal"
    )
    assert not is_error and zipped["operation"]["preview"]["archive_path"] == str(
        workspace / "p.zip"
    )
    assert "pack" not in service.names()

    # By id: the dev-linked directory is packed.
    dev_dir = _plugin_dir(workspace, "dev-copy")
    service.plugins = [_item(dev_path=str(dev_dir))]
    by_id, is_error = await _call(_ctx(workspace), action="publish", id="acme.dash", scope="org")
    assert not is_error
    assert service.calls[[c[0] for c in service.calls].index("pack")][1][0] == str(dev_dir)

    bad_scope, is_error = await _call(
        _ctx(workspace), action="publish", id="acme.dash", scope="world"
    )
    assert is_error and bad_scope["error_code"] == "invalid_scope"
    both, is_error = await _call(_ctx(workspace), action="publish", scope="org")
    assert is_error and both["error_code"] == "missing_argument"

    service.validate_result = {"ok": False, "errors": ["bad manifest"], "warnings": []}
    invalid, is_error = await _call(_ctx(workspace), action="publish", path="dev-copy", scope="org")
    assert is_error and invalid["error_code"] == "invalid_manifest"


async def test_publish_at_confirm_time_without_a_publisher_fails_clearly(
    service: FakeService, workspace: Path, ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ext, "app_plugin_publisher", FakePublisher())
    (workspace / "p.zip").write_bytes(b"zip")
    body, _ = await _call(_ctx(workspace), action="publish", path="p.zip", scope="personal")
    monkeypatch.setattr(ext, "app_plugin_publisher", None)
    done = await _confirm(ops_db, body)
    assert done.state == "failed" and "Valuz account" in (done.error_message or "")


# ── How a handler reaches the service ────────────────────────────────────


async def test_a_service_taking_db_gets_the_operations_session(
    ops_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def install(
        user_id: str, source: dict[str, Any], *, db: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        seen["db"] = db
        return {"plugin": _item()}

    monkeypatch.setattr(app_plugin_service, "install", install)
    monkeypatch.setattr(settings, "deployment_type", "local")
    context = SimpleNamespace(db="THE-SESSION", user_id=USER)
    await ext_ops._call_service(context, app_plugin_service.install, USER, {"url": "u"})  # type: ignore[arg-type]
    assert seen["db"] == "THE-SESSION"


async def test_a_service_without_db_runs_outside_the_deferred_commit_scope() -> None:
    from valuz_agent.infra.db import commits_deferred, defer_commits

    async def probe() -> bool:
        return commits_deferred()

    async with defer_commits():
        assert commits_deferred() is True
        assert await ext_ops._fresh_context_call(probe) is False
        assert commits_deferred() is True


def test_the_confirm_route_knows_the_extension_operations() -> None:
    """``POST /v1/operations/{id}/confirm`` can only run what the registry holds."""
    from valuz_agent.api.routes import operations  # noqa: F401  (side-effect import)
    from valuz_agent.modules.operations.registry import operation_registry

    for operation_type in (
        "app_plugin.install",
        "app_plugin.dev_link",
        "app_plugin.uninstall",
        "app_plugin.publish",
    ):
        assert operation_registry.get(operation_type, 1).handler is not None
