"""Manifest ``automations[]`` -> ``AppPluginAutomationSpec`` (docs plugin-development/12 §6.3).

The rows themselves are created by ``modules/automations/app_plugin_support`` (see its
docstring for the managed-project design); this module only translates what the
manifest declares.
"""

from __future__ import annotations

from typing import Any

from valuz_agent.modules.app_plugins.manifest import pick_text
from valuz_agent.modules.automations.app_plugin_support import AppPluginAutomationSpec


def specs_from_manifest(manifest: dict[str, Any]) -> list[AppPluginAutomationSpec]:
    specs: list[AppPluginAutomationSpec] = []
    for item in manifest.get("automations") or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        schema = item.get("input")
        trigger = item.get("trigger", "manual")
        timeout = item.get("timeoutSec")
        specs.append(
            AppPluginAutomationSpec(
                name=item["name"],
                title=pick_text(item.get("title")) or item["name"],
                runtime="shell" if item.get("runtime") == "shell" else "python",
                entry=str(item["entry"]),
                input_schema=dict(schema) if isinstance(schema, dict) else None,
                result="conversation" if item.get("result") == "conversation" else "artifact",
                timeout_s=int(timeout) if isinstance(timeout, int) else None,
                trigger=trigger if isinstance(trigger, (str, dict)) else "manual",
            )
        )
    return specs


__all__ = ["specs_from_manifest"]
