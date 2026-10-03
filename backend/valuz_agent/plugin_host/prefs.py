"""Persisted extension preferences — the backend's profile patch layer.

dsh records a disabled row or edited config in the profile patch and applies
it at the next composition; this is the same layer for Valuz's backend
plugins: ``<shared data root>/extensions.json``::

    {"disabled": ["commercial-sites"], "configs": {"<plugin id>": {...}}}

The composing process passes it to ``PluginHost.load_all`` (disabled ids are
filtered to optional plugins there — a required plugin can never be switched
off). Deployment-wide, not per user.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PREFS_FILENAME = "extensions.json"


@dataclass(frozen=True)
class ExtensionPrefs:
    disabled: frozenset[str] = frozenset()
    configs: dict[str, dict[str, Any]] = field(default_factory=dict)


def prefs_path() -> Path:
    from valuz_agent.infra.fs_registry import fs_registry

    return fs_registry.shared_root() / PREFS_FILENAME


def load_extension_prefs(path: Path | None = None) -> ExtensionPrefs:
    try:
        raw = json.loads((path or prefs_path()).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ExtensionPrefs()
    disabled = raw.get("disabled") if isinstance(raw, dict) else None
    configs = raw.get("configs") if isinstance(raw, dict) else None
    return ExtensionPrefs(
        disabled=frozenset(d for d in (disabled or []) if isinstance(d, str)),
        configs={k: v for k, v in (configs or {}).items() if isinstance(v, dict)},
    )


def _write(prefs: ExtensionPrefs, path: Path) -> None:
    payload = {"disabled": sorted(prefs.disabled), "configs": prefs.configs}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def save_enabled(plugin_id: str, enabled: bool, path: Path | None = None) -> ExtensionPrefs:
    target = path or prefs_path()
    current = load_extension_prefs(target)
    disabled = set(current.disabled)
    if enabled:
        disabled.discard(plugin_id)
    else:
        disabled.add(plugin_id)
    updated = ExtensionPrefs(disabled=frozenset(disabled), configs=current.configs)
    _write(updated, target)
    return updated


def save_config(plugin_id: str, values: dict[str, Any], path: Path | None = None) -> ExtensionPrefs:
    target = path or prefs_path()
    current = load_extension_prefs(target)
    updated = ExtensionPrefs(
        disabled=current.disabled, configs={**current.configs, plugin_id: values}
    )
    _write(updated, target)
    return updated


def effective_disabled(prefs: ExtensionPrefs, required_ids: set[str]) -> frozenset[str]:
    """Disabled ids a host may honour: never a required plugin."""
    return frozenset(pid for pid in prefs.disabled if pid not in required_ids)
