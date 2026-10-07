"""Permission gate for requests a third-party plugin makes (docs task card 04 §D).

Pure ASGI, so a request without ``X-Valuz-App-Plugin-Id`` (every host request) costs one
header scan and passes through untouched. A request that carries the header must name
an installed, enabled, loadable plugin and stay inside the manifest ``permissions``
(``modules/app_plugins/permissions.py`` has the route map); otherwise it is answered
``403 {"detail": {"code": "plugin_permission_denied", "permission": ...}}``. Denials and
state-changing calls are appended to the plugin's log (``source: audit``).

The registry mounts this outside the host's own middleware, so the denial response
adds the CORS header itself — without it the renderer would see an opaque network
error instead of the 403.
"""

from __future__ import annotations

import json
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from valuz_agent.modules.app_plugins import logs
from valuz_agent.modules.app_plugins.permissions import api_path, classify
from valuz_agent.modules.app_plugins.service import app_plugin_service

PLUGIN_HEADER = b"x-valuz-app-plugin-id"
LEGACY_PLUGIN_HEADER = b"x-valuz-plugin-id"
MAX_INSPECTED_BODY = 1024 * 1024


class PluginPermissionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        plugin_id = _plugin_header(scope)
        if not plugin_id or scope["method"] == "OPTIONS":  # preflight carries no identity
            await self.app(scope, receive, send)
            return

        method, path = scope["method"], scope["path"]
        shown = api_path(path) or path
        granted = app_plugin_service.plugin_access(plugin_id)
        if granted is None:
            await self._deny(scope, receive, send, plugin_id, None, "plugin_not_enabled", shown)
            return
        rule = classify(method, path, plugin_id)
        if rule is None:
            await self._deny(scope, receive, send, plugin_id, None, "not_available", shown)
            return
        required: list[str] = []
        if rule.permission and not rule.always:
            required.append(rule.permission)
        replay = receive
        if rule.tool_call:
            body, replay = await _buffer_body(receive)
            if _allow_write(body):
                required.append("connectors:write")
        missing = next((p for p in required if p not in granted), None)
        if missing is not None:
            await self._deny(scope, receive, send, plugin_id, missing, "permission", shown)
            return
        if rule.write:
            logs.append_log(plugin_id, "info", f"{method} {shown}", "audit")
        await self.app(scope, replay, send)

    @staticmethod
    async def _deny(
        scope: Scope,
        receive: Receive,
        send: Send,
        plugin_id: str,
        permission: str | None,
        reason: str,
        shown: str,
    ) -> None:
        logs.append_log(
            plugin_id,
            "warn",
            f"denied {scope['method']} {shown}"
            + (f" (needs {permission})" if permission else f" ({reason})"),
            "audit",
        )
        response = JSONResponse(
            {
                "detail": {
                    "code": "plugin_permission_denied",
                    "permission": permission,
                    "reason": reason,
                    "plugin_id": plugin_id,
                    "message": "The plugin is not allowed to make this request",
                }
            },
            status_code=403,
            headers={"access-control-allow-origin": "*"},
        )
        await response(scope, receive, send)


def _plugin_header(scope: Scope) -> str:
    headers = dict(scope.get("headers") or [])
    value = headers.get(PLUGIN_HEADER)
    if value is None:
        value = headers.get(LEGACY_PLUGIN_HEADER, b"")
    return str(value.decode("latin-1")).strip()


async def _buffer_body(receive: Receive) -> tuple[bytes, Receive]:
    """Read the whole request body (capped) and return it with a ``receive`` that
    replays it to the application."""
    messages: list[Message] = []
    body = b""
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        body += message.get("body", b"")
        if not message.get("more_body", False) or len(body) > MAX_INSPECTED_BODY:
            break
    pending = list(messages)

    async def replay() -> Message:
        if pending:
            return pending.pop(0)
        return await receive()

    return body, replay


def _allow_write(body: bytes) -> bool:
    try:
        parsed: Any = json.loads(body or b"{}")
    except ValueError:
        return False
    return isinstance(parsed, dict) and bool(parsed.get("allow_write"))


__all__ = ["PluginPermissionMiddleware"]
