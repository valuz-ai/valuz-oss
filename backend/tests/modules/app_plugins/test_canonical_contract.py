"""The unreleased application plugin feature exposes canonical names only."""

import importlib
from pathlib import Path

import pytest

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.app_plugins import operations, service
from valuz_agent.modules.app_plugins.service import AppPluginService, _Env
from valuz_agent.modules.app_plugins.store import InstalledStore
from valuz_agent.modules.operations.registry import operation_registry
from valuz_agent.plugin_host import load_plugin_prefs, save_enabled
from valuz_agent.ports.extensions import Extensions


@pytest.mark.parametrize(
    "module",
    [
        "valuz_agent.modules.third_party",
        "valuz_agent.api.routes.third_party",
        "valuz_agent.api.routes.extensions",
        "valuz_agent.api.third_party_middleware",
        "valuz_agent.ports.third_party",
        "valuz_agent.features.third_party",
        "valuz_agent.integrations.tools_extension_manager",
        "valuz_agent.modules.automations.extension_support",
    ],
)
def test_removed_import_paths_are_unavailable(module: str) -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_only_canonical_service_ports_and_operations_are_exposed() -> None:
    assert service.app_plugin_service is not None
    assert not hasattr(service, "ThirdPartyService")
    assert not hasattr(service, "third_party_service")
    ports = Extensions()
    assert ports.app_plugin_policy is not None
    assert ports.app_plugin_publisher is None
    assert not hasattr(ports, "third_party_policy")
    assert not hasattr(ports, "extension_publisher")
    for action in ("install", "dev_link", "uninstall", "publish"):
        assert operation_registry.get(f"app_plugin.{action}", 1).handler is not None
        with pytest.raises(LookupError, match="operation_type_unavailable"):
            operation_registry.get(f"extension.{action}", 1)
    assert not hasattr(operations, "build_extension_proposal")


def test_paths_and_preferences_use_canonical_storage_only(data_root: Path) -> None:
    store = InstalledStore()
    assert store.path() == data_root / "app-plugins" / "installed.json"
    assert fs_registry.app_plugin_version_dir("acme.app", "1.0.0") == (
        data_root / "app-plugins" / "acme.app" / "1.0.0"
    )
    assert fs_registry.app_plugin_data_dir("acme.app") == data_root / "app-plugin-data" / "acme.app"
    assert (
        fs_registry.app_plugin_log_path("acme.app")
        == data_root / "logs" / "app-plugins" / "acme.app.log"
    )
    save_enabled("oss-app-plugins", False)
    assert load_plugin_prefs().disabled == {"oss-app-plugins"}
    assert (data_root / "plugins.json").is_file()
    assert not (data_root / "extensions.json").exists()
    assert not (data_root / "extensions").exists()
    assert not (data_root / "extensions-data").exists()
    for name in (
        "extensions_root",
        "extension_version_dir",
        "extensions_data_dir",
        "extension_log_path",
    ):
        assert not hasattr(fs_registry, name)


async def test_only_the_canonical_capability_satisfies_requirements(data_root: Path) -> None:
    svc = AppPluginService(InstalledStore())
    env = _Env(
        user_id="u",
        edition="oss",
        deployment="local",
        capabilities=frozenset({"oss.app-plugins"}),
        disabled=frozenset(),
    )
    assert await svc._unmet(["capability:oss.app-plugins"], env) == []
    assert await svc._unmet(["capability:oss.third-party"], env) == ["capability:oss.third-party"]


def test_only_the_canonical_safe_mode_environment_is_read(
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InstalledStore()
    monkeypatch.setenv("VALUZ_EXTENSIONS_SAFE_MODE", "1")
    assert store.safe_mode() == (False, None)
    monkeypatch.setenv("VALUZ_APP_PLUGINS_SAFE_MODE", "1")
    assert store.safe_mode() == (True, "VALUZ_APP_PLUGINS_SAFE_MODE=1")
