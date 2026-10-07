"""Old application plugin installations resolve one canonical state without loss."""

import json
from pathlib import Path

import pytest

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.app_plugins.service import AppPluginService
from valuz_agent.modules.app_plugins.store import InstalledStore
from valuz_agent.plugin_host import load_plugin_prefs, register_plugin_id_alias, save_enabled
from valuz_agent.ports.app_plugins import AllowAllAppPluginPolicy
from valuz_agent.ports.extensions import Extensions


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_moves_installed_packages_data_logs_safe_mode_and_prefs(data_root: Path) -> None:
    _write(
        data_root / "extensions" / "installed.json",
        json.dumps(
            {
                "version": 1,
                "generation": 7,
                "plugins": {"acme.app": {"id": "acme.app", "version": "1.0.0"}},
            }
        ),
    )
    _write(data_root / "extensions" / "acme.app" / "1.0.0" / "frontend" / "index.js", "package")
    _write(data_root / "extensions" / "safe-mode.json", '{"reason":"migration"}')
    _write(data_root / "extensions-data" / "acme.app" / "state.json", "persistent data")
    _write(data_root / "logs" / "extensions" / "acme.app.log", "audit log")
    _write(
        data_root / "extensions.json",
        '{"disabled":["oss-third-party","acme.app"],"configs":{"oss-third-party":{"opt":1}}}',
    )
    store = InstalledStore()
    assert store.read()["generation"] == 7
    assert (
        fs_registry.app_plugin_version_dir("acme.app", "1.0.0") / "frontend" / "index.js"
    ).read_text() == "package"
    assert (
        fs_registry.app_plugin_data_dir("acme.app") / "state.json"
    ).read_text() == "persistent data"
    assert fs_registry.app_plugin_log_path("acme.app").read_text() == "audit log"
    assert store.safe_mode() == (True, "migration")
    prefs = load_plugin_prefs()
    assert prefs.disabled == {"oss-app-plugins", "acme.app"}
    assert prefs.configs == {"oss-app-plugins": {"opt": 1}}
    assert set(json.loads((data_root / "plugins.json").read_text())["disabled"]) == prefs.disabled
    for path in (
        data_root / "extensions",
        data_root / "extensions-data",
        data_root / "logs" / "extensions",
        data_root / "extensions.json",
    ):
        assert not path.exists()
    assert fs_registry.extensions_root() == fs_registry.app_plugins_root()
    assert fs_registry.extension_version_dir(
        "acme.app", "1.0.0"
    ) == fs_registry.app_plugin_version_dir("acme.app", "1.0.0")
    assert store.read()["generation"] == 7  # idempotent, no second registry


def test_merges_coexisting_registries_and_preserves_conflicting_files(data_root: Path) -> None:
    for directory, generation, plugins in [
        (
            "extensions",
            4,
            {"old.app": {"id": "old.app"}, "same.app": {"id": "same.app", "version": "old"}},
        ),
        (
            "app-plugins",
            8,
            {"new.app": {"id": "new.app"}, "same.app": {"id": "same.app", "version": "new"}},
        ),
    ]:
        _write(
            data_root / directory / "installed.json",
            json.dumps({"version": 1, "generation": generation, "plugins": plugins}),
        )
        _write(data_root / directory / "same.app" / "1.0.0" / "file.js", directory)
    state = InstalledStore().read()
    assert set(state["plugins"]) == {"old.app", "new.app", "same.app"}
    assert state["plugins"]["same.app"]["version"] == "new"
    assert state["generation"] == 9
    assert (
        data_root / "app-plugins" / "same.app" / "1.0.0" / "file.js"
    ).read_text() == "app-plugins"
    archive = data_root / "app-plugins" / ".legacy-migration"
    assert any(p.read_text() == "extensions" for p in archive.glob("file.js.*.legacy"))
    assert not (data_root / "extensions").exists()
    assert InstalledStore().read() == state


def test_prefs_merge_is_once_and_old_disable_does_not_return(data_root: Path) -> None:
    _write(
        data_root / "extensions.json",
        '{"disabled":["old.app","oss-third-party"],"configs":{"old.app":{"a":1}}}',
    )
    _write(data_root / "plugins.json", '{"disabled":["new.app"],"configs":{"new.app":{"b":2}}}')
    assert load_plugin_prefs().disabled == {"old.app", "new.app", "oss-app-plugins"}
    assert load_plugin_prefs().configs == {"old.app": {"a": 1}, "new.app": {"b": 2}}
    save_enabled("oss-third-party", True)
    assert load_plugin_prefs().disabled == {"old.app", "new.app"}
    assert not (data_root / "extensions.json").exists()
    assert len(list(data_root.glob(".extensions-prefs.*.migrated"))) == 1


def test_valid_legacy_registry_survives_corrupt_canonical_state(data_root: Path) -> None:
    _write(
        data_root / "extensions" / "installed.json",
        '{"version":1,"generation":3,"plugins":{"old.app":{"id":"old.app"}}}',
    )
    _write(data_root / "app-plugins" / "installed.json", "corrupt but retained")
    assert set(InstalledStore().read()["plugins"]) == {"old.app"}
    assert any(
        path.read_text() == "corrupt but retained"
        for path in (data_root / "app-plugins").glob("installed.json.*.corrupt")
    )


def test_embedding_host_can_register_its_renamed_pref_ids(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from valuz_agent.plugin_host.prefs import PLUGIN_ID_ALIASES

    monkeypatch.setattr("valuz_agent.plugin_host.prefs.PLUGIN_ID_ALIASES", dict(PLUGIN_ID_ALIASES))
    register_plugin_id_alias("edition-legacy", "edition-app-plugins")
    _write(
        data_root / "plugins.json",
        '{"disabled":["edition-legacy"],"configs":{"edition-legacy":{"scope":"old"},"edition-app-plugins":{"scope":"new"}}}',
    )
    prefs = load_plugin_prefs()
    assert prefs.disabled == {"edition-app-plugins"}
    assert prefs.configs == {"edition-app-plugins": {"scope": "new"}}


def test_legacy_imports_and_port_bindings_share_the_canonical_objects() -> None:
    from valuz_agent.modules.app_plugins import service
    from valuz_agent.modules.third_party import service as legacy_service
    from valuz_agent.ports.app_plugins import AppPluginPolicyPort
    from valuz_agent.ports.third_party import ThirdPartyPolicyPort

    assert legacy_service is service
    assert legacy_service.third_party_service is service.app_plugin_service
    assert ThirdPartyPolicyPort is AppPluginPolicyPort
    ports = Extensions()
    policy = AllowAllAppPluginPolicy()
    ports.third_party_policy = policy
    assert ports.app_plugin_policy is policy
    assert ports.extension_publisher is ports.app_plugin_publisher is None


async def test_legacy_manifest_capability_requirement_still_loads(data_root: Path) -> None:
    from valuz_agent.modules.app_plugins.service import _Env

    service = AppPluginService(InstalledStore())
    env = _Env(
        user_id="u",
        edition="oss",
        deployment="local",
        capabilities=frozenset({"oss.app-plugins"}),
        disabled=frozenset(),
    )
    assert await service._unmet(["capability:oss.third-party", "capability:third-party"], env) == []


def test_both_safe_mode_environment_names_are_supported(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InstalledStore()
    monkeypatch.setenv("VALUZ_EXTENSIONS_SAFE_MODE", "1")
    assert store.safe_mode() == (True, "VALUZ_EXTENSIONS_SAFE_MODE=1")
    monkeypatch.setenv("VALUZ_APP_PLUGINS_SAFE_MODE", "1")
    assert store.safe_mode() == (True, "VALUZ_APP_PLUGINS_SAFE_MODE=1")
