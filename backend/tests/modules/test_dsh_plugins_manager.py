"""The resident dsh manager host: deployment gating and launch inputs.

The live install → session path is covered end to end by
tests/runtimes/test_dsh_upstream_compat.py (real closure).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.modules.dsh_plugins import manager as dsh_manager
from valuz_agent.modules.dsh_plugins.manager import (
    PLUGIN_MANAGER_METHODS,
    DshManagedBundleError,
    DshManagerHost,
    DshManagerUnavailableError,
    manager_enabled,
    resolve_dsh_home,
    resolve_launcher,
    unavailable_reason,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "VALUZ_DSH_MANAGER_ENABLED",
        "KERNEL_STORE",
        "VALUZ_DSH_RUNTIME_ENTRY",
        "VALUZ_DSH_HOME",
        "VALUZ_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_local_workstations_may_manage_plugins(monkeypatch) -> None:
    assert manager_enabled() is True
    monkeypatch.setenv("KERNEL_STORE", "local")
    assert manager_enabled() is True


def test_cloud_deployments_may_not(monkeypatch) -> None:
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "deployment_type", "cloud")
    assert manager_enabled() is False


def test_shared_deployments_may_not(monkeypatch) -> None:
    # Plugin code runs unsandboxed with the user's privileges.
    for store in ("pg", "remote"):
        monkeypatch.setenv("KERNEL_STORE", store)
        assert manager_enabled() is False
    monkeypatch.setenv("VALUZ_DSH_MANAGER_ENABLED", "1")
    assert manager_enabled() is True
    monkeypatch.setenv("KERNEL_STORE", "local")
    monkeypatch.setenv("VALUZ_DSH_MANAGER_ENABLED", "0")
    assert manager_enabled() is False


def test_launcher_and_home_resolution(monkeypatch, tmp_path: Path) -> None:
    launcher = tmp_path / "dsh.mjs"
    launcher.write_text("// launcher")
    monkeypatch.setenv("VALUZ_DSH_RUNTIME_ENTRY", str(launcher))
    assert resolve_launcher() == launcher
    monkeypatch.setenv("VALUZ_DSH_RUNTIME_ENTRY", str(tmp_path / "absent.mjs"))
    assert resolve_launcher() is None
    monkeypatch.setenv("VALUZ_DATA_DIR", str(tmp_path / "data"))
    assert resolve_dsh_home() == tmp_path / "data" / "dsh-home"
    monkeypatch.setenv("VALUZ_DSH_HOME", str(tmp_path / "home"))
    assert resolve_dsh_home() == tmp_path / "home"


async def test_unavailable_host_refuses_with_a_reason(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VALUZ_DSH_RUNTIME_ENTRY", str(tmp_path / "absent.mjs"))
    reason = unavailable_reason()
    assert reason is not None and "closure" in reason
    host = DshManagerHost()
    assert host.status().available is False
    with pytest.raises(DshManagerUnavailableError):
        await host.ensure_started()


async def test_only_plugin_manager_methods_are_proxied() -> None:
    assert {
        "installBundle",
        "removeBundle",
        "setBundleEnabled",
        "inspect",
    } <= PLUGIN_MANAGER_METHODS
    with pytest.raises(ValueError):
        await DshManagerHost().call("shutdown")
    assert dsh_manager.PROFILE_NAME == "valuz"


class _FakeRemote:
    """Records forwarded pluginManager calls; listBundles reports Valuz's rows."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, method: str, **args):
        self.calls.append((method, args))
        if method == "pluginManager/listBundles":
            return [
                {
                    "name": "valuz-dsh-bundle",
                    "rows": [{"rowId": "valuz-kernel-bridge", "entryId": "e-valuz"}],
                },
                {"name": "dsh-hello-tool", "rows": [{"rowId": "hello", "entryId": "e-hello"}]},
            ]
        return {"application": "applied"}


def _host_with_fake_remote(monkeypatch) -> tuple[DshManagerHost, _FakeRemote]:
    host = DshManagerHost()
    remote = _FakeRemote()

    async def _started() -> str:
        host._remote = remote  # type: ignore[assignment]
        return "http://127.0.0.1:1/"

    monkeypatch.setattr(host, "ensure_started", _started)
    return host, remote


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("removeBundle", {"name": "valuz-dsh-bundle"}),
        ("removeBundle", {"name": "@deepseek-ai/dsh-sdk-app"}),
        ("setBundleEnabled", {"name": "@deepseek-ai/dsh-base", "enabled": False}),
        ("setPluginEnabled", {"id": "e-valuz", "enabled": False}),
    ],
)
async def test_what_valuz_sessions_run_on_cannot_be_switched_off(monkeypatch, method, args) -> None:
    host, remote = _host_with_fake_remote(monkeypatch)
    with pytest.raises(DshManagedBundleError):
        await host.call(method, args)
    assert all(m != f"pluginManager/{method}" for m, _ in remote.calls)


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("removeBundle", {"name": "dsh-hello-tool"}),
        ("setBundleEnabled", {"name": "valuz-dsh-bundle", "enabled": True}),
        ("setPluginEnabled", {"id": "e-hello", "enabled": False}),
        ("setPluginEnabled", {"id": "e-valuz", "enabled": True}),
    ],
)
async def test_user_bundles_and_re_enabling_pass_through(monkeypatch, method, args) -> None:
    host, remote = _host_with_fake_remote(monkeypatch)
    assert await host.call(method, args) == {"application": "applied"}
    assert remote.calls[-1] == (f"pluginManager/{method}", args)
