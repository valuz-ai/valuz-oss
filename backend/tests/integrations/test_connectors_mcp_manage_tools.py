"""The connector *management* tools of the in-process connectors MCP server.

They mirror the Connectors page: edit / delete / enable / disable / test, and
``get_mcp`` hands the current configuration (env included) back so an edit
starts from it. The service is faked; the tools' own logic is under test —
owner resolution, the recommended-stdio lock, env/args coercion, masking,
and the probe after a connection-parameter change.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

import valuz_agent.infra.db as infra_db
import valuz_agent.integrations.connectors_mcp_server as m
from valuz_agent.api import routes as _routes_pkg  # noqa: F401  (import side effects)
from valuz_agent.api.routes import connectors as routes


def _cred(key: str, value: str | None, *, secret: bool = False) -> SimpleNamespace:
    return SimpleNamespace(key=key, secret=secret, value=None if secret else value)


def _view(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(
        id="c1",
        slug="demo",
        display_name="Demo",
        description=None,
        connector_type="custom",
        transport="stdio",
        url=None,
        auth_type="none",
        command="npx",
        args=["-y", "demo-mcp"],
        working_dir=None,
        env=[_cred("API_URL", "https://x"), _cred("API_TOKEN", "s3cret", secret=True)],
        headers=[],
        params=[],
        enabled=True,
        status="connected",
        tool_count=3,
        error_message=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeService:
    def __init__(self, views: dict[str, SimpleNamespace]) -> None:
        self.views = views
        self.calls: list[tuple[str, Any]] = []

    async def list_connectors(self, user_id: str) -> list[SimpleNamespace]:
        self.calls.append(("list", user_id))
        return list(self.views.values())

    async def get_connector(self, user_id: str, connector_id: str) -> SimpleNamespace | None:
        self.calls.append(("get", (user_id, connector_id)))
        return self.views.get(connector_id)

    async def update_connector(self, user_id: str, connector_id: str, **fields: Any) -> Any:
        self.calls.append(("update", (user_id, connector_id, fields)))
        view = self.views.get(connector_id)
        if view is None:
            return None
        changed = any(
            fields.get(k) is not None for k in ("url", "command", "args", "working_dir", "env")
        )
        view.status = "connecting" if changed else view.status
        return view

    async def delete_connector(self, user_id: str, connector_id: str) -> bool:
        self.calls.append(("delete", (user_id, connector_id)))
        return self.views.pop(connector_id, None) is not None

    async def set_enabled(self, user_id: str, connector_id: str, *, enabled: bool) -> Any:
        self.calls.append(("set_enabled", (user_id, connector_id, enabled)))
        view = self.views.get(connector_id)
        if view is not None:
            view.enabled = enabled
        return view


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    service = FakeService({"c1": _view()})

    @asynccontextmanager
    async def _uow(**_kwargs: Any):
        yield object()

    async def _probe_ok(connector_id: str, _svc: Any, _user_id: str) -> Any:
        service.views[connector_id].status = "connected"
        return routes.TestConnectorResponse(ok=True, tool_count=1, tools=["t"])

    monkeypatch.setattr(infra_db, "async_unit_of_work", _uow)
    monkeypatch.setattr(m, "_make_connector_service", lambda _db: service)
    monkeypatch.setattr(m, "_current_user_id", lambda: "user-1")
    # A connection-parameter change re-probes; tests override this when the
    # probe outcome matters, the default just keeps the real probe out.
    monkeypatch.setattr(routes, "_probe_connector", _probe_ok)
    return service


@pytest.mark.asyncio
async def test_list_mcp_lists_every_status_for_the_session_owner(svc: FakeService) -> None:
    svc.views["c2"] = _view(id="c2", slug="broken", status="error", enabled=False)
    body = json.loads(await m.list_mcp())
    assert body["ok"] is True
    assert [(c["id"], c["status"], c["enabled"]) for c in body["connectors"]] == [
        ("c1", "connected", True),
        ("c2", "error", False),
    ]
    assert ("list", "user-1") in svc.calls


@pytest.mark.asyncio
async def test_get_mcp_returns_env_with_secret_values_masked(svc: FakeService) -> None:
    body = json.loads(await m.get_mcp("c1"))
    assert body["ok"] is True
    connector = body["connector"]
    assert connector["command"] == "npx" and connector["args"] == ["-y", "demo-mcp"]
    assert connector["env"] == [
        {"key": "API_URL", "secret": False, "value": "https://x"},
        {"key": "API_TOKEN", "secret": True, "value": None},
    ]
    assert json.loads(await m.get_mcp("missing")) == {"ok": False, "error": "Connector not found"}


@pytest.mark.asyncio
async def test_update_mcp_passes_env_and_args_through_and_probes(
    svc: FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed: list[str] = []

    async def _probe(connector_id: str, _svc: Any, user_id: str) -> Any:
        probed.append(f"{user_id}:{connector_id}")
        svc.views[connector_id].status = "connected"
        return routes.TestConnectorResponse(ok=True, tool_count=2, tools=["a", "b"])

    monkeypatch.setattr(routes, "_probe_connector", _probe)

    body = json.loads(
        await m.update_mcp("c1", env="API_URL=https://y API_TOKEN=t2", args=["-y", "demo-mcp@2"])
    )
    assert body["ok"] is True, body
    _, (user_id, connector_id, fields) = next(c for c in svc.calls if c[0] == "update")
    assert (user_id, connector_id) == ("user-1", "c1")
    # KEY=VAL pairs and a real array reach the service as dict / list.
    assert fields["env"] == {"API_URL": "https://y", "API_TOKEN": "t2"}
    assert fields["args"] == ["-y", "demo-mcp@2"]
    # Untouched fields stay None so the service leaves them alone.
    assert fields["display_name"] is None and fields["url"] is None
    assert probed == ["user-1:c1"]
    assert body["probe"]["ok"] is True and body["probe"]["tool_count"] == 2
    assert body["connector"]["status"] == "connected"


@pytest.mark.asyncio
async def test_update_mcp_locks_everything_but_env_on_a_recommended_stdio_connector(
    svc: FakeService,
) -> None:
    svc.views["c1"] = _view(connector_type="recommended", slug="github")

    body = json.loads(await m.update_mcp("c1", command="uvx"))
    assert body["ok"] is False and "only ``env``" in body["error"]
    assert not any(c[0] == "update" for c in svc.calls)

    body = json.loads(await m.update_mcp("c1", env={"GITHUB_TOKEN": "x"}))
    assert body["ok"] is True
    _, (_user, _cid, fields) = next(c for c in svc.calls if c[0] == "update")
    assert fields["env"] == {"GITHUB_TOKEN": "x"} and fields["command"] is None


@pytest.mark.asyncio
async def test_delete_mcp_reports_not_deletable(svc: FakeService) -> None:
    assert json.loads(await m.delete_mcp("c1")) == {"ok": True, "connector_id": "c1"}
    assert ("delete", ("user-1", "c1")) in svc.calls
    body = json.loads(await m.delete_mcp("c1"))
    assert body["ok"] is False and "cannot be deleted" in body["error"]


@pytest.mark.asyncio
async def test_enable_and_disable_mcp_flip_the_switch(svc: FakeService) -> None:
    body = json.loads(await m.disable_mcp("c1"))
    assert body["ok"] is True and body["connector"]["enabled"] is False
    body = json.loads(await m.enable_mcp("c1"))
    assert body["ok"] is True and body["connector"]["enabled"] is True
    assert [c for c in svc.calls if c[0] == "set_enabled"] == [
        ("set_enabled", ("user-1", "c1", False)),
        ("set_enabled", ("user-1", "c1", True)),
    ]
    assert json.loads(await m.enable_mcp("missing")) == {
        "ok": False,
        "error": "Connector not found",
    }


@pytest.mark.asyncio
async def test_test_mcp_returns_the_probe_result(
    svc: FakeService, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _probe(connector_id: str, _svc: Any, user_id: str) -> Any:
        return routes.TestConnectorResponse(ok=False, error=f"{user_id}/{connector_id}: boom")

    monkeypatch.setattr(routes, "_probe_connector", _probe)
    body = json.loads(await m.test_mcp("c1"))
    assert body["ok"] is False and body["error"] == "user-1/c1: boom"
    assert body["connector_id"] == "c1"
