"""Persisted extension prefs (the backend profile-patch layer) and /v1/extensions."""

from __future__ import annotations

from pathlib import Path

from valuz_agent.plugin_host import (
    BackendPluginBase,
    PluginHost,
    effective_disabled,
    load_extension_prefs,
    save_config,
    save_enabled,
)


class _Plugin(BackendPluginBase):
    def __init__(self, pid: str, *, required: bool = False) -> None:
        self._id = pid
        self._required = required
        self.applied = False

    @property
    def id(self) -> str:
        return self._id

    @property
    def required(self) -> bool:
        return self._required

    def apply(self, ctx, config) -> None:  # noqa: ANN001
        self.applied = True


def test_prefs_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "extensions.json"
    assert load_extension_prefs(path).disabled == frozenset()
    save_enabled("sites", False, path)
    save_config("sites", {"mode": "x"}, path)
    prefs = load_extension_prefs(path)
    assert prefs.disabled == {"sites"}
    assert prefs.configs == {"sites": {"mode": "x"}}
    save_enabled("sites", True, path)
    assert load_extension_prefs(path).disabled == frozenset()


def test_a_disabled_optional_plugin_is_not_loaded_but_required_ones_are(tmp_path: Path) -> None:
    path = tmp_path / "extensions.json"
    save_enabled("optional", False, path)
    save_enabled("core", False, path)  # a stale or hand-edited entry
    optional, core = _Plugin("optional"), _Plugin("core", required=True)
    host = PluginHost([core, optional])
    prefs = load_extension_prefs(path)
    host.load_all(disabled=effective_disabled(prefs, {"core"}))
    assert core.applied is True
    assert optional.applied is False
    status = {info.id: info.status for info in host.list()}
    assert status["core"] == "active"
