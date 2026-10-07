"""Execution gate for managed jobs: immutable source, live job-owner authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from valuz_agent.infra.errors import ForbiddenError
from valuz_agent.ports.extensions import ext


class AppPluginAutomationForbiddenError(ForbiddenError):
    error_code = 403_719
    message = "Application plugin automation source is not authorized"


async def authorize_managed_automation(row: Any, *, user_id: str) -> None:
    if not getattr(row, "app_plugin_id", None):
        return
    if getattr(row, "user_id", user_id) != user_id:
        raise AppPluginAutomationForbiddenError("automation owner mismatch")
    kind = getattr(row, "app_plugin_source_kind", None)
    if kind in {"file", "url", "dev"}:
        return
    binding = getattr(row, "app_plugin_catalog_binding", None)
    if (
        kind != "catalog"
        or not isinstance(binding, Mapping)
        or binding.get("app_plugin_id") != row.app_plugin_id
    ):
        raise AppPluginAutomationForbiddenError(
            "Plugin source is unconfirmed; reinstall it as the current owner"
        )
    fields = (
        "app_plugin_id",
        "scope",
        "owner_key",
        "version",
        "sha256",
        "origin_scope",
        "origin_owner_key",
    )
    if any(not isinstance(binding.get(field), str) or not binding[field] for field in fields):
        raise AppPluginAutomationForbiddenError("Catalogue source binding is incomplete")
    if binding["scope"] not in {"personal", "org", "global"} or binding["origin_scope"] not in {
        "personal",
        "org",
        "global",
    }:
        raise AppPluginAutomationForbiddenError("Catalogue source scope is invalid")
    import re

    if re.fullmatch(r"[0-9a-f]{64}", binding["sha256"]) is None:
        raise AppPluginAutomationForbiddenError("Catalogue source digest is invalid")
    authorize = getattr(ext.app_plugin_policy, "authorize_catalog_automation", None)
    if authorize is None:
        raise AppPluginAutomationForbiddenError("Catalogue authorization is unavailable")
    try:
        verdict = await authorize(user_id, dict(binding))
    except Exception as exc:
        raise AppPluginAutomationForbiddenError("Catalogue identity could not be verified") from exc
    if not verdict.allowed:
        raise AppPluginAutomationForbiddenError(
            verdict.reason or "Catalogue version is unavailable or revoked"
        )
