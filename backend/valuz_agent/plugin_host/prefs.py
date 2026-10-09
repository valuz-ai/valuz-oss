"""Persisted plugin preferences — the backend's profile patch layer.

dsh records a disabled row or edited config in the profile patch and applies
it at the next composition; this is the same layer for Valuz's backend
plugins: ``<shared data root>/plugins.json``::

    {"disabled": ["commercial-sites"], "configs": {"<plugin id>": {...}}}

The composing process passes it to ``PluginHost.load_all`` (disabled ids are
filtered to unlocked plugins there — a required plugin, or one a required plugin
needs, can never be switched off). Deployment-wide, not per user.
"""

from __future__ import annotations

import json
import os
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from valuz_agent.plugin_host.host import PluginHost

PREFS_FILENAME = "plugins.json"


@dataclass(frozen=True)
class PluginPrefs:
    disabled: frozenset[str] = frozenset()
    configs: dict[str, dict[str, Any]] = field(default_factory=dict)


def prefs_path() -> Path:
    from valuz_agent.infra.fs_registry import fs_registry

    return fs_registry.shared_root() / PREFS_FILENAME


def _read(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, dict) else None


def _normalise(raw: dict[str, Any]) -> PluginPrefs:
    disabled, raw_configs = raw.get("disabled"), raw.get("configs")
    source_configs = raw_configs if isinstance(raw_configs, dict) else {}
    configs: dict[str, dict[str, Any]] = {}
    for plugin_id, value in source_configs.items():
        if not isinstance(plugin_id, str) or not isinstance(value, dict):
            continue
        configs[plugin_id] = value
    return PluginPrefs(
        disabled=frozenset(
            pid for pid in (disabled if isinstance(disabled, list) else []) if isinstance(pid, str)
        ),
        configs=configs,
    )


def load_plugin_prefs(path: Path | None = None) -> PluginPrefs:
    return _normalise(_read(path or prefs_path()) or {})


def _write(prefs: PluginPrefs, path: Path) -> None:
    payload = {"disabled": sorted(prefs.disabled), "configs": prefs.configs}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def save_enabled(plugin_id: str, enabled: bool, path: Path | None = None) -> PluginPrefs:
    target = path or prefs_path()
    current = load_plugin_prefs(target)
    disabled = set(current.disabled)
    if enabled:
        disabled.discard(plugin_id)
    else:
        disabled.add(plugin_id)
    updated = PluginPrefs(disabled=frozenset(disabled), configs=current.configs)
    _write(updated, target)
    return updated


def save_config(plugin_id: str, values: dict[str, Any], path: Path | None = None) -> PluginPrefs:
    target = path or prefs_path()
    current = load_plugin_prefs(target)
    updated = PluginPrefs(disabled=current.disabled, configs={**current.configs, plugin_id: values})
    _write(updated, target)
    return updated


def effective_disabled(prefs: PluginPrefs, locked_ids: Collection[str]) -> frozenset[str]:
    """Disabled ids a host may honour: never a locked plugin.

    ``locked_ids`` are the required plugins and the ones a locked plugin depends on
    (``PluginHost.locks()``). ``PluginHost.load_all`` applies the same filter itself,
    so passing only the required ids -- the original contract -- is still safe.
    """
    return frozenset(pid for pid in prefs.disabled if pid not in locked_ids)


def load_host_with_prefs(host: PluginHost) -> None:
    """``host.load_all`` with the persisted prefs: plugins the user switched off stay
    off, locked ones (required, or needed by one) always load, and per-plugin config
    edits apply."""
    prefs = load_plugin_prefs()
    host.load_all(configs=prefs.configs, disabled=prefs.disabled)
