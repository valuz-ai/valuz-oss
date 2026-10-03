"""The OSS backend as plugins: inventory, ownership, toggle safety, disabled apps.

The bare-app goldens (``test_composition_goldens``) pin that the *enabled*
composition did not change. These tests pin what is new: every contribution has
exactly one owner, a disabled plugin really contributes nothing, locked plugins
cannot be switched off, and the app still boots with features off.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.boot.phases import SHUTDOWN_ORDER, STARTUP_ORDER, BootPlan
from valuz_agent.features import compose_oss_host, oss_capabilities, oss_plugins
from valuz_agent.features.order import MOUNT_SLOTS, ROUTE_SLOTS, TOOL_SLOTS
from valuz_agent.plugin_host import (
    BackendPluginBase,
    PluginHost,
    active_plugin_host,
    compute_locks,
    set_active_plugin_host,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = Path(__file__).parent / "snapshot_collector.py"

REQUIRED = {"oss-core", "oss-agents"}
OPTIONAL = [
    "oss-tasks",
    "oss-automations",
    "oss-activity",
    "oss-skills",
    "oss-connectors",
    "oss-knowledge",
    "oss-memory",
    "oss-browser",
    "oss-channels",
    "oss-backup",
    "oss-marketplace",
    "oss-agent-plugins",
    "oss-dsh-plugins",
    "oss-citations",
    "oss-notifications",
    "oss-feedback",
]


@pytest.fixture
def isolated_host_state() -> Iterator[None]:
    """Hosts apply onto process-wide ports (``ext``); put them back afterwards."""
    previous = active_plugin_host()
    yield
    set_active_plugin_host(previous)


def _loaded(disabled: set[str] = frozenset()) -> PluginHost:  # type: ignore[assignment]
    host = compose_oss_host()
    host.load_all(disabled=disabled)
    return host


# -- inventory ---------------------------------------------------------------------


def test_the_plugin_ids_are_the_agreed_ones() -> None:
    plugins = oss_plugins()
    assert [p.id for p in plugins if p.required] == ["oss-core", "oss-agents"]
    assert {p.id for p in plugins} == REQUIRED | set(OPTIONAL)
    assert len(plugins) == len({p.id for p in plugins}) == 18


def test_every_plugin_provides_its_capability_and_needs_the_shell() -> None:
    for plugin in oss_plugins():
        feature = plugin.id.removeprefix("oss-")
        assert plugin.provides == (f"oss.{feature}",)
        if plugin.id != "oss-core":
            assert "oss.core" in plugin.needs
    assert oss_capabilities() == {f"oss.{p.id.removeprefix('oss-')}" for p in oss_plugins()}


def test_declared_order_is_the_activation_order() -> None:
    host = compose_oss_host()
    assert host.order() == [p.id for p in oss_plugins()]


# -- ownership ---------------------------------------------------------------------


def test_every_contribution_has_one_owner_and_a_canonical_position(
    isolated_host_state: None,
) -> None:
    """Nothing is registered outside the canonical tables (an unlisted entry would
    silently sort last), and nothing the tables list is left without an owner."""
    host = _loaded()
    owners = host.registry.owners()
    seen: dict[str, set[str]] = {
        k: set() for k in ("routes", "startup", "shutdown", "mounts", "tools")
    }
    for kinds in owners.values():
        for kind, names in kinds.items():
            assert not seen[kind] & set(names), f"{kind} registered twice: {names}"
            seen[kind] |= set(names)

    assert seen["routes"] == set(ROUTE_SLOTS)
    assert seen["tools"] == set(TOOL_SLOTS)
    assert seen["mounts"] <= set(MOUNT_SLOTS)
    assert seen["startup"] == {n for names in STARTUP_ORDER.values() for n in names}
    assert seen["shutdown"] == {n for names in SHUTDOWN_ORDER.values() for n in names}
    host.unload_all()


def test_the_boot_plan_of_the_enabled_host_is_the_canonical_order(
    isolated_host_state: None,
) -> None:
    host = _loaded()
    plan = BootPlan.from_registry(host.registry)
    assert plan.startup_names == [n for names in STARTUP_ORDER.values() for n in names]
    assert plan.shutdown_names == [n for names in SHUTDOWN_ORDER.values() for n in names]
    host.unload_all()


# -- a disabled plugin contributes nothing -----------------------------------------


@pytest.mark.parametrize("plugin_id", OPTIONAL)
def test_a_disabled_plugin_registers_nothing(plugin_id: str, isolated_host_state: None) -> None:
    host = _loaded({plugin_id})
    assert host.get(plugin_id).status == "disabled"
    assert plugin_id not in host.registry.owners()
    # What it provides is unavailable, so its dependents are rolled back too -- softly.
    for info in host.list():
        if info.status == "failed":
            assert info.error and "unavailable" in info.error
            assert info.id not in host.registry.owners()
    host.unload_all()


def test_a_dependent_of_a_disabled_plugin_is_rolled_back_not_fatal(
    isolated_host_state: None,
) -> None:
    host = _loaded({"oss-skills"})
    states = {i.id: i.status for i in host.list()}
    assert states["oss-skills"] == "disabled"
    assert states["oss-agent-plugins"] == "failed"  # needs oss.skills
    assert states["oss-core"] == states["oss-agents"] == "active"
    assert {"oss-skills", "oss-agent-plugins"} <= set(host.inactive_ids())
    host.unload_all()


def test_the_dsh_bridge_leaves_the_always_on_specs_with_its_plugin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.ports.extensions import ext

    monkeypatch.setenv("VALUZ_DSH_MANAGER_ENABLED", "1")
    before = [s.name for s in ext.always_on_mcp_specs]
    host = _loaded()
    assert "valuz-dsh-plugins" in [s.name for s in ext.always_on_mcp_specs]
    host.unload("oss-dsh-plugins")
    assert [s.name for s in ext.always_on_mcp_specs] == before
    host.unload_all()


def _collect_disabled(*ids: str) -> dict[str, Any]:
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(BACKEND_ROOT)}
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    proc = subprocess.run(
        [sys.executable, str(COLLECTOR), "--kind", "disabled", "--disable", ",".join(ids)],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(BACKEND_ROOT),
    )
    marker = "<<SNAPSHOT>>\n"
    if proc.returncode != 0 or marker not in proc.stdout:
        pytest.fail(f"collector failed (exit {proc.returncode}):\n{proc.stderr[-4000:]}")
    result: dict[str, Any] = json.loads(proc.stdout.split(marker, 1)[1])
    return result


def test_an_app_without_browser_and_automations_has_none_of_their_parts() -> None:
    """The real prefs file switches the plugins off; the real app is built and its
    lifespan run (steps faked). Routes, steps, MCP mounts / managers / advertised
    servers and harness tools of the two features are all gone; the rest stays."""
    out = _collect_disabled("oss-browser", "oss-automations")

    assert out["inactive"] == ["oss-automations", "oss-browser"]
    routes = "\n".join(out["routes"])
    for gone in ("/v1/browser", "/v1/automations", "/v1/playbooks", "/v1/operations"):
        assert gone not in routes, gone
    assert "/_internal/mcp/automations" not in routes
    assert "/_internal/mcp/playbooks" not in routes
    # ... while the rest of the surface is untouched
    for kept in (
        "/v1/docs",
        "/v1/skills",
        "/v1/sessions",
        "/_internal/mcp/docs",
        "/_internal/data",
    ):
        assert kept in routes, kept

    assert "start_automation_runtime(app)" not in out["startup"]
    assert "stop_automation_runtime(app)" not in out["shutdown"]
    assert "stop_managed_browser()" not in out["shutdown"]
    assert "start_task_health_monitor(app)" in out["startup"]  # an unrelated feature

    assert "enter automations" not in out["managers"]
    assert "enter playbooks" not in out["managers"]
    assert "enter docs" in out["managers"]
    assert "valuz-automations" not in out["advertised"]
    assert "valuz-playbooks" not in out["advertised"]
    assert {"valuz-docs", "valuz-connectors", "harness"} <= set(out["advertised"])

    assert "browser" not in out["tools"]
    assert {"tasks-orchestration", "memory"} <= set(out["tools"])

    # the lifespan ran end to end
    assert out["startup"][-1] == "start_dsh_manager_if_plugins_installed()"
    assert out["shutdown"][-1] == "parent_watchdog.stop_parent_watchdog()"


def test_the_app_boots_with_every_optional_plugin_off() -> None:
    out = _collect_disabled(*OPTIONAL)
    assert set(out["inactive"]) == set(OPTIONAL)
    assert out["tools"] == [
        "project-instructions",
        "agent-proposal",
        "project",
        "deliver-artifacts",
        "genui",
    ]
    assert out["advertised"] == ["harness"]
    assert out["managers"] == [
        "enter kernel-mcp-router",
        "enter toolkit",
        "started",
        "exit toolkit",
        "exit kernel-mcp-router",
    ]
    assert out["startup"][-1] == "mark_boot_complete()"
    assert "/v1/sessions" in "\n".join(out["routes"])
    assert "/v1/extensions/backend/state" in "\n".join(out["routes"])


# -- toggle safety -----------------------------------------------------------------


class _Needy(BackendPluginBase):
    def __init__(self, pid: str, needs: tuple[str, ...], required: bool = True) -> None:
        self._id, self._needs, self._required = pid, needs, required

    @property
    def id(self) -> str:  # type: ignore[override]
        return self._id

    @property
    def needs(self) -> tuple[str, ...]:  # type: ignore[override]
        return self._needs

    @property
    def required(self) -> bool:  # type: ignore[override]
        return self._required

    def apply(self, ctx: Any, config: Any) -> None:
        return None


def _host_with(*extra: BackendPluginBase) -> PluginHost:
    return PluginHost([*oss_plugins(), *extra], facets={"routes"})


def test_a_required_plugin_locks_what_only_a_feature_provides() -> None:
    host = _host_with(_Needy("commercial-kb", ("oss.knowledge",)))
    locks = host.locks(set())
    assert locks["oss-knowledge"] == ["commercial-kb"]
    assert locks["oss-core"] == sorted(  # needed by every locked plugin below the shell
        p for p in locks if p != "oss-core" and "oss.core" in host.get(p).plugin.needs
    )
    assert "oss-browser" not in locks and "oss-memory" not in locks


def test_locks_follow_the_needs_of_locked_plugins_transitively() -> None:
    # oss-citations needs oss.knowledge and oss.feedback; locking it locks both.
    host = _host_with(_Needy("edition-citations", ("oss.citations",)))
    locks = host.locks(set())
    assert {"oss-citations", "oss-knowledge", "oss-feedback"} <= set(locks)
    assert locks["oss-citations"] == ["edition-citations"]
    assert locks["oss-knowledge"] == ["oss-citations"]


def test_a_locked_plugin_ignores_the_request_to_switch_it_off(isolated_host_state: None) -> None:
    host = _host_with(_Needy("commercial-kb", ("oss.knowledge",)))
    assert host.effective_disabled({"oss-knowledge", "oss-browser", "nope"}) == {"oss-browser"}
    # a hand-edited extensions.json must not become a startup failure
    host.load_all(disabled={"oss-knowledge", "oss-browser", "oss-core"})
    states = {i.id: i.status for i in host.list()}
    assert states["oss-knowledge"] == "active" and states["oss-core"] == "active"
    assert states["oss-browser"] == "disabled"
    host.unload_all()


def test_two_providers_of_one_need_may_not_both_go() -> None:
    class _P(_Needy):
        def __init__(self, pid: str, provides: tuple[str, ...]) -> None:
            super().__init__(pid, (), required=False)
            self._provides = provides

        @property
        def provides(self) -> tuple[str, ...]:  # type: ignore[override]
            return self._provides

    a, b = _P("a", ("svc",)), _P("b", ("svc",))
    user = _Needy("user", ("svc",))
    plugins = {p.id: p for p in (a, b, user)}
    assert compute_locks(plugins, set()) == {"user": []}  # either may still go
    assert compute_locks(plugins, {"a"}) == {"user": [], "b": ["user"]}  # a is off: b is pinned
    assert set(compute_locks(plugins, {"a", "b"})) == {"user", "a", "b"}  # never both


def test_requiredby_is_reported_in_the_plugin_listing() -> None:
    host = _host_with(_Needy("commercial-kb", ("oss.knowledge",)))
    rows = {i.id: i.to_dict() for i in host.list()}
    assert rows["oss-knowledge"]["requiredBy"] == ["commercial-kb"]
    assert rows["oss-browser"]["requiredBy"] == []
    assert rows["oss-core"]["required"] is True


# -- /v1/extensions ------------------------------------------------------------------


@pytest.fixture
def extensions_client(
    monkeypatch: pytest.MonkeyPatch, isolated_host_state: None
) -> Iterator[tuple[TestClient, PluginHost, dict[str, Any]]]:
    from valuz_agent.api.routes import extensions

    host = _host_with(_Needy("commercial-kb", ("oss.knowledge",)))
    host.load_all(disabled={"oss-browser"})
    set_active_plugin_host(host)

    saved: dict[str, Any] = {"disabled": {"oss-browser"}, "writes": []}

    class _Prefs:
        @property
        def disabled(self) -> set[str]:
            return set(saved["disabled"])

    monkeypatch.setattr(extensions, "load_extension_prefs", lambda: _Prefs())
    monkeypatch.setattr(
        extensions, "save_enabled", lambda pid, enabled: saved["writes"].append((pid, enabled))
    )
    app = FastAPI()
    app.include_router(extensions.router)
    yield TestClient(app), host, saved
    host.unload_all()


def test_state_is_public_and_lists_only_ids(
    extensions_client: tuple[TestClient, PluginHost, dict[str, Any]],
) -> None:
    client, _host, _saved = extensions_client
    # No auth override, no token: the owner dependency would 401, this must not.
    response = client.get("/v1/extensions/backend/state")
    assert response.status_code == 200
    assert response.json() == {"inactive": ["oss-browser"]}


def test_state_without_a_host_reports_nothing_inactive(isolated_host_state: None) -> None:
    from valuz_agent.api.routes import extensions

    set_active_plugin_host(None)
    app = FastAPI()
    app.include_router(extensions.router)
    assert TestClient(app).get("/v1/extensions/backend/state").json() == {"inactive": []}


def test_the_listing_needs_an_owner(
    extensions_client: tuple[TestClient, PluginHost, dict[str, Any]],
) -> None:
    client, _host, _saved = extensions_client
    client.app.dependency_overrides[get_current_user_id] = lambda: "u1"  # type: ignore[attr-defined]
    rows = {p["id"]: p for p in client.get("/v1/extensions/backend").json()["plugins"]}
    assert rows["oss-knowledge"]["requiredBy"] == ["commercial-kb"]
    assert rows["oss-browser"]["desiredEnabled"] is False


def test_switching_off_a_locked_plugin_is_a_409_naming_who_needs_it(
    extensions_client: tuple[TestClient, PluginHost, dict[str, Any]],
) -> None:
    client, _host, saved = extensions_client
    client.app.dependency_overrides[get_current_user_id] = lambda: "u1"  # type: ignore[attr-defined]

    locked = client.post("/v1/extensions/backend/oss-knowledge/enabled", json={"enabled": False})
    assert locked.status_code == 409
    assert "commercial-kb" in locked.json()["detail"]

    required = client.post("/v1/extensions/backend/oss-core/enabled", json={"enabled": False})
    assert required.status_code == 409 and "required" in required.json()["detail"]
    assert saved["writes"] == []  # nothing was persisted

    free = client.post("/v1/extensions/backend/oss-memory/enabled", json={"enabled": False})
    assert free.status_code == 200 and free.json() == {"application": "restart-required"}
    assert saved["writes"] == [("oss-memory", False)]

    # turning a locked plugin ON is never refused
    on = client.post("/v1/extensions/backend/oss-knowledge/enabled", json={"enabled": True})
    assert on.status_code == 200
