"""Which API a plugin-originated request may reach (docs task card 04 §D).

A request that carries ``X-Valuz-App-Plugin-Id`` is checked against the ``permissions``
of that plugin's manifest. Anything not listed here is refused — a plugin reaches
the host's data only through the public surface below:

====================================================  ===============================
request                                               permission
====================================================  ===============================
``GET /v1/projects*``                                 ``projects:read``
``GET /v1/artifacts*``                                ``artifacts:read``
``POST /v1/docs/search``, ``GET /v1/docs/{id}``       ``knowledge:read``
``GET /v1/sessions/{id}/events``                      ``conversations:read``
``POST /v1/sessions``, ``POST …/{id}/messages``       ``conversations:write``
``GET /v1/connectors``, ``GET …/{id}/tools``          ``connectors:read``
``POST /v1/connectors/{id}/tools/{tool}/call``        ``connectors:call`` (+ ``connectors:write``
                                                      when the body sets ``allow_write``)
``…/app-plugins/{self}/storage*``                     ``storage``
``…/app-plugins/{self}/automations*``, ``…/automation-runs*``  ``automations:run``
``POST /v1/notifications``                            ``notifications``
``…/app-plugins/{self}/logs``, ``…/config``           always allowed
====================================================  ===============================

Paths are matched from ``/v1/`` on, so the same rules hold when the API is mounted
under a prefix (the commercial overlay serves it under ``/valuz-backend``). The
kernel surface (``/kernel/v1``) and ``/_internal`` are never reachable.
"""

from __future__ import annotations

from dataclasses import dataclass

SAFE_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True)
class Rule:
    #: The manifest permission required; ``None`` with ``always`` for open routes.
    permission: str | None
    #: A state-changing call (audited in the plugin log).
    write: bool = False
    #: Allowed without any declared permission (the plugin's own logs and config).
    always: bool = False
    #: A connector tool call: ``connectors:write`` is also needed when the body
    #: sets ``allow_write``.
    tool_call: bool = False


def api_path(path: str) -> str | None:
    """The part of ``path`` from ``/v1/`` on, or ``None`` when it is not a public
    ``/v1`` route (outside ``/v1``, the kernel surface, ``/_internal``)."""
    index = path.find("/v1/")
    if index == -1:
        return None
    prefix = path[:index].rstrip("/")
    if prefix.endswith("/kernel") or prefix == "/kernel" or "/_internal" in prefix:
        return None
    rel = path[index:]
    legacy = "/v1/extensions/third-party"
    if rel == legacy or rel.startswith(legacy + "/"):
        rel = "/v1/app-plugins" + rel[len(legacy) :]
    return rel


def classify(method: str, path: str, plugin_id: str) -> Rule | None:
    """The rule for one request, or ``None`` when plugins may not call it."""
    rel = api_path(path)
    if rel is None:
        return None
    segments = [s for s in rel.split("/")[2:] if s != ""]
    if not segments:
        return None
    method = method.upper()
    head, rest = segments[0], segments[1:]
    safe = method in SAFE_METHODS

    if head == "projects":
        return Rule("projects:read") if safe else Rule("projects:write", write=True)
    if head == "artifacts":
        return Rule("artifacts:read") if safe else Rule("artifacts:write", write=True)
    if head == "docs":
        if method == "POST" and rest == ["search"]:
            return Rule("knowledge:read")
        if safe and len(rest) == 1 and rest[0] != "search":
            return Rule("knowledge:read")
        return None
    if head == "sessions":
        if safe and len(rest) == 2 and rest[1] == "events":
            return Rule("conversations:read")
        if method == "POST" and not rest:
            return Rule("conversations:write", write=True)
        if method == "POST" and len(rest) == 2 and rest[1] == "messages":
            return Rule("conversations:write", write=True)
        return None
    if head == "connectors":
        if safe and not rest:
            return Rule("connectors:read")
        if safe and len(rest) == 2 and rest[1] == "tools":
            return Rule("connectors:read")
        if method == "POST" and len(rest) == 4 and rest[1] == "tools" and rest[3] == "call":
            return Rule("connectors:call", write=True, tool_call=True)
        return None
    if head == "notifications":
        if method == "POST" and not rest:
            return Rule("notifications", write=True)
        return None
    if head == "app-plugins" and len(rest) >= 2:
        owner, section = rest[0], rest[1]
        if owner != plugin_id:
            return None
        write = not safe
        if section == "logs" and len(rest) == 2:
            return Rule(None, always=True)  # writing a log line is its own record
        if section == "config" and len(rest) == 2:
            return Rule(None, write=write, always=True)
        if section == "storage":
            return Rule("storage", write=write)
        if section in ("automations", "automation-runs"):
            return Rule("automations:run", write=write)
    return None


__all__ = ["Rule", "api_path", "classify"]
