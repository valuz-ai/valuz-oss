"""Older frontend pairs consume legacy IDs without creating another plugin host."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.routes.builtin_plugins import router
from valuz_agent.plugin_host import (
    BackendPluginBase,
    PluginContext,
    PluginHost,
    active_plugin_host,
    load_plugin_prefs,
    register_plugin_id_alias,
    set_active_plugin_host,
)


class _Config(BaseModel):
    label: str = "author.extensions"


class _Plugin(BackendPluginBase):
    Config = _Config

    def __init__(
        self,
        plugin_id: str,
        *,
        required: bool = False,
        needs: tuple[str, ...] = (),
        provides: tuple[str, ...] = (),
    ) -> None:
        self.id = plugin_id
        self.required = required
        self.needs = needs
        self.provides = provides

    def apply(self, ctx: PluginContext, config: Any) -> None:
        return None


@pytest.fixture
def legacy_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from valuz_agent.infra.config import settings
    from valuz_agent.plugin_host.prefs import PLUGIN_ID_ALIASES

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr("valuz_agent.plugin_host.prefs.PLUGIN_ID_ALIASES", dict(PLUGIN_ID_ALIASES))
    register_plugin_id_alias("commercial-extensions", "commercial-app-plugins")
    previous = active_plugin_host()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    yield TestClient(app)
    set_active_plugin_host(previous)


def test_legacy_inactive_ids_match_older_frontend_pairs(legacy_client: TestClient) -> None:
    host = PluginHost(
        [
            _Plugin("oss-app-plugins"),
            _Plugin("commercial-app-plugins"),
            _Plugin("author.extensions"),
        ]
    )
    host.load_all(disabled={"oss-app-plugins", "commercial-app-plugins", "author.extensions"})
    set_active_plugin_host(host)
    legacy_client.app.dependency_overrides.clear()  # state stays public
    assert legacy_client.get("/v1/builtin-plugins/state").json() == {
        "inactive": ["author.extensions", "commercial-app-plugins", "oss-app-plugins"]
    }
    assert legacy_client.get("/v1/extensions/backend/state").json() == {
        "inactive": ["author.extensions", "commercial-extensions", "oss-third-party"]
    }
    assert active_plugin_host() is host
    assert [plugin.id for plugin in host.list()] == [
        "oss-app-plugins",
        "commercial-app-plugins",
        "author.extensions",
    ]


def test_legacy_listing_projects_ids_locks_capabilities_and_schema_keys(
    legacy_client: TestClient,
) -> None:
    host = PluginHost(
        [
            _Plugin("oss-app-plugins", provides=("oss.app-plugins",)),
            _Plugin("commercial-app-plugins", required=True, needs=("oss.app-plugins",)),
            _Plugin("author.extensions"),
        ]
    )
    host.load_all()
    set_active_plugin_host(host)
    canonical = legacy_client.get("/v1/builtin-plugins").json()
    legacy = legacy_client.get("/v1/extensions/backend").json()
    rows = {row["id"]: row for row in canonical["plugins"]}
    old_rows = {row["id"]: row for row in legacy["plugins"]}
    assert rows["oss-app-plugins"]["requiredBy"] == ["commercial-app-plugins"]
    assert old_rows["oss-third-party"]["requiredBy"] == ["commercial-extensions"]
    assert rows["oss-app-plugins"]["provides"] == ["oss.app-plugins"]
    assert old_rows["oss-third-party"]["provides"] == ["oss.third-party"]
    assert old_rows["commercial-extensions"]["needs"] == ["oss.third-party"]
    assert rows["author.extensions"] == old_rows["author.extensions"]
    assert set(legacy["config_schemas"]) == {
        "oss-third-party",
        "commercial-extensions",
        "author.extensions",
    }
    assert set(canonical["config_schemas"]) == {
        "oss-app-plugins",
        "commercial-app-plugins",
        "author.extensions",
    }
    assert (
        legacy["config_schemas"]["commercial-extensions"]
        == (canonical["config_schemas"]["commercial-app-plugins"])
    )
    assert legacy["config_schemas"]["author.extensions"]["properties"]["label"]["default"] == (
        "author.extensions"
    )
    # Producing the legacy view must not mutate canonical IDs or capabilities.
    assert legacy_client.get("/v1/builtin-plugins").json() == canonical
    assert active_plugin_host() is host


@pytest.mark.parametrize(
    "legacy_id,canonical_id",
    [
        ("oss-third-party", "oss-app-plugins"),
        ("commercial-extensions", "commercial-app-plugins"),
    ],
)
def test_old_id_toggle_uses_canonical_preferences_and_the_same_host(
    legacy_client: TestClient,
    legacy_id: str,
    canonical_id: str,
) -> None:
    host = PluginHost([_Plugin(canonical_id)])
    host.load_all()
    set_active_plugin_host(host)
    response = legacy_client.post(
        f"/v1/extensions/backend/{legacy_id}/enabled", json={"enabled": False}
    )
    assert response.status_code == 200 and response.json() == {"application": "restart-required"}
    assert load_plugin_prefs().disabled == {canonical_id}
    assert active_plugin_host() is host and host.get(canonical_id).desired_enabled is False
    canonical = legacy_client.get("/v1/builtin-plugins").json()["plugins"][0]
    legacy = legacy_client.get("/v1/extensions/backend").json()["plugins"][0]
    assert canonical["id"] == canonical_id and canonical["desiredEnabled"] is False
    assert legacy["id"] == legacy_id and legacy["desiredEnabled"] is False
    # Neither URL claims the running plugin stopped before the required restart.
    assert legacy_client.get("/v1/builtin-plugins/state").json() == {"inactive": []}
    assert legacy_client.get("/v1/extensions/backend/state").json() == {"inactive": []}
    assert [plugin.id for plugin in host.list()] == [canonical_id]
