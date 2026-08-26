"""PTC workspace client runtime — deployed INTO the session workspace.

This file is not imported by the backend at runtime: ``tool_generator``
composes its source verbatim (plus a JSON config epilogue) into the
workspace's ``tools/mcp_client.py``, which every generated wrapper imports
``_call_mcp_tool`` from. It therefore must:

- depend on the STDLIB ONLY (the packaged backend cannot provision
  third-party packages into the host ``python3`` that runs agent code);
- carry zero upstream URLs or credentials — every call POSTs to the
  kernel's loopback PTC endpoint, whose one-shot URL arrives in the
  ``VALUZ_PTC_CALL_URL`` env var injected by ``execute_code``;
- stay lintable and unit-testable standalone (config is seeded with
  defaults; the generated epilogue calls ``_apply_config_dict`` again
  with the real values).
"""

import json
import os
import urllib.error
import urllib.request

_CALL_URL_ENV = "VALUZ_PTC_CALL_URL"
_HTTP_TIMEOUT_SECONDS = 600.0
# Bounded replies: a flooding response should fail diagnosably, not OOM us.
_REPLY_MAX_BYTES = 64 * 1024 * 1024


class ToolCallError(RuntimeError):
    """A tool call failed. ``server`` / ``tool`` identify the failed call;
    ``str(error)`` is human-readable. try/except it to handle and continue."""

    def __init__(self, server: str, tool: str, message: str) -> None:
        super().__init__(message)
        self.server = server
        self.tool = tool


# ---------------------------------------------------------------------------
# Configuration — shape produced by tool_generator.generate_client_config():
#   {"servers": ["<server-name>", ...]}
# The server list exists only for a clearer early error; the kernel enforces
# the real allowlist per execution.
# ---------------------------------------------------------------------------

_SERVERS: tuple[str, ...] = ()


def _apply_config_dict(cfg: dict[str, object]) -> None:
    """(Re)initialize module state from a config dict (generated epilogue)."""
    global _SERVERS  # noqa: PLW0603
    raw = cfg.get("servers")
    names = raw if isinstance(raw, (list, tuple)) else ()
    _SERVERS = tuple(str(name) for name in names)


_apply_config_dict({})


def _call_url() -> str:
    url = os.environ.get(_CALL_URL_ENV, "")
    if not url:
        raise RuntimeError(
            "PTC tools are only callable from the execute_code tool — "
            f"{_CALL_URL_ENV} is not set in this process"
        )
    return url


def _call_mcp_tool(server_name: str, tool_name: str, arguments: dict[str, object]) -> object:
    """The single entry point every generated wrapper function calls.

    Raises ``ToolCallError`` on a failed call; returns the tool's canonical
    JSON value otherwise.
    """
    if _SERVERS and server_name not in _SERVERS:
        raise ToolCallError(
            server_name,
            tool_name,
            f"server {server_name!r} is not part of this workspace "
            f"(available: {', '.join(_SERVERS)})",
        )
    body = json.dumps({"server": server_name, "tool": tool_name, "arguments": arguments}).encode(
        "utf-8"
    )
    request = urllib.request.Request(
        _call_url(),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            raw = response.read(_REPLY_MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # The router answers errors with a JSON envelope on 4xx/5xx.
        raw = exc.read()
        try:
            reply = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise ToolCallError(
                server_name, tool_name, f"HTTP {exc.code} from PTC endpoint"
            ) from None
        _raise_from_envelope(server_name, tool_name, reply)
        raise ToolCallError(server_name, tool_name, f"HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise ToolCallError(
            server_name, tool_name, f"PTC endpoint unreachable: {exc.reason}"
        ) from None
    if len(raw) > _REPLY_MAX_BYTES:
        raise ToolCallError(server_name, tool_name, f"reply exceeded {_REPLY_MAX_BYTES} bytes")
    try:
        reply = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise ToolCallError(server_name, tool_name, "malformed reply from PTC endpoint") from None
    if not isinstance(reply, dict):
        raise ToolCallError(server_name, tool_name, "malformed reply from PTC endpoint")
    if reply.get("ok") is True:
        return reply.get("value")
    _raise_from_envelope(server_name, tool_name, reply)
    raise ToolCallError(server_name, tool_name, "malformed reply from PTC endpoint")


def _raise_from_envelope(server_name: str, tool_name: str, reply: object) -> None:
    if isinstance(reply, dict) and reply.get("ok") is False:
        error = reply.get("error")
        message = ""
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("code") or "")
        raise ToolCallError(server_name, tool_name, message or "tool call failed")
