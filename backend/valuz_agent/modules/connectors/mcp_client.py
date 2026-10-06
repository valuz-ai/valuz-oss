"""MCP client for connectors — the one place that opens a session to one.

Two callers share it:

* the connector **probe** (``api/routes/connectors._probe_connector``) — connect,
  count the tools, record the result;
* the connector **tools API** (``GET /v1/connectors/{id}/tools`` and
  ``POST …/tools/{tool}/call``) used by third-party plugins (docs task card 04
  §D) — list tools with their full schemas, call one.

``open_connector_session`` owns everything about *reaching* a connector: the
stdio launch (login-shell ``PATH``, ``{mcp_dir}`` expansion, the stored env), the
HTTP / SSE request overrides (user-configured headers + params, OAuth bearer),
the ``http`` ↔ ``sse`` transport fallback, the transient-401 retry of anonymous
connectors and the 401 → refresh → retry path of OAuth ones
(``ext.connector_oauth_refresh``). The caller gets an initialised
``ClientSession`` and the transports are torn down when the block exits.

Every lookup is scoped to the ``user_id`` the caller passes: a connector row of
another user is simply "not found". A session is opened per operation — no
reuse across calls, so there is no shared state between users to get wrong.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
from mcp.client.session import ClientSession

from valuz_agent.integrations.mcp_http import mcp_request_headers
from valuz_agent.modules.connectors.service import (
    ConnectorService,
    build_request_overrides,
    merge_params_into_url,
)
from valuz_agent.modules.ptc.tool_generator import ToolInfo as _PtcToolInfo
from valuz_agent.modules.ptc.tool_generator import is_code_callable
from valuz_agent.ports.extensions import ext

logger = logging.getLogger(__name__)

#: Per-request / per-operation budget of the tools API (seconds).
DEFAULT_TIMEOUT_S = 30.0
#: Connect / initialize budget of the connector probe (seconds).
PROBE_TIMEOUT_S = 15.0
#: ``tools/list`` is paginated; stop following cursors after this many pages.
_MAX_TOOL_PAGES = 20
#: How long a resolved login-shell ``PATH`` is reused (seconds).
_SHELL_PATH_TTL_S = 300.0


# ── Errors ───────────────────────────────────────────────────────────────


class ConnectorClientError(Exception):
    """Base of the errors this module raises itself (transport errors from the
    MCP stack propagate as they are — ``unwrap_exception`` finds their cause)."""


class ConnectorNotFoundError(ConnectorClientError):
    """No such connector for this user."""


class ConnectorDisabledError(ConnectorClientError):
    """The connector exists but is switched off."""


class ConnectorConfigError(ConnectorClientError):
    """The connector row cannot be launched / reached as configured."""


class StdioUnavailableError(ConnectorClientError):
    """A stdio connector was asked for outside a local deployment."""


class ToolNotFoundError(ConnectorClientError):
    """The connector does not expose the requested tool."""


class WriteToolRequiresConfirmationError(ConnectorClientError):
    """The tool is not declared read-only and the caller did not allow writes."""


class ConnectorTimeoutError(ConnectorClientError):
    """The operation did not finish within its budget."""


# ── Wire shapes ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConnectorTool:
    """One MCP tool as the tools API returns it."""

    name: str
    description: str | None
    input_schema: dict[str, Any]
    annotations: dict[str, Any] = field(default_factory=dict, hash=False)

    @property
    def read_only(self) -> bool:
        """Same fail-closed rule as the PTC code face: ``readOnlyHint is True``."""
        return is_code_callable(
            _PtcToolInfo(
                name=self.name,
                description=self.description or "",
                input_schema=self.input_schema,
                server_name="",
                annotations=self.annotations,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "annotations": self.annotations,
            "read_only": self.read_only,
        }


@dataclass(frozen=True)
class ConnectorCallResult:
    content: list[dict[str, Any]]
    structured_content: dict[str, Any] | None
    is_error: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "structured_content": self.structured_content,
            "is_error": self.is_error,
        }


def _tool_from_mcp(tool: Any) -> ConnectorTool | None:
    name = getattr(tool, "name", None)
    if not name:
        return None
    raw_annotations = getattr(tool, "annotations", None)
    annotations: dict[str, Any] = {}
    if raw_annotations is not None:
        dumped = (
            raw_annotations.model_dump(mode="json", exclude_none=True)
            if hasattr(raw_annotations, "model_dump")
            else raw_annotations
        )
        if isinstance(dumped, dict):
            annotations = dumped
    schema = getattr(tool, "inputSchema", None)
    return ConnectorTool(
        name=str(name),
        description=getattr(tool, "description", None),
        input_schema=schema if isinstance(schema, dict) else {},
        annotations=annotations,
    )


# ── Small helpers (also used by the probe) ───────────────────────────────


def unwrap_exception(exc: BaseException) -> BaseException:
    """The leaf of an ``ExceptionGroup`` chain (anyio task groups wrap errors)."""
    inner = exc
    while hasattr(inner, "exceptions") and getattr(inner, "exceptions", None):
        inner = inner.exceptions[0]
    return inner


async def retry_async[T](
    fn: Callable[[], Awaitable[T]],
    *,
    retry_if: Callable[[BaseException], bool],
    delays: tuple[float, ...],
) -> T:
    """Await ``fn``; if it raises an exception matching ``retry_if``, back off and
    retry — one extra attempt per entry in ``delays``. Re-raises the last error
    once the retries are exhausted or the error doesn't match.
    """
    attempt = 0
    while True:
        try:
            return await fn()
        except BaseException as exc:
            if attempt >= len(delays) or not retry_if(exc):
                raise
            await asyncio.sleep(delays[attempt])
            attempt += 1


def is_unauthorized(exc: BaseException) -> bool:
    """Detect a 401 from the MCP/httpx stack — i.e. an expired access token.

    The MCP client may wrap the underlying error in an ``ExceptionGroup``; unwrap
    to the leaf, prefer the typed ``HTTPStatusError`` status, and fall back to a
    string match for transports that surface the 401 only in the message.
    """
    inner: BaseException = exc
    while isinstance(inner, BaseExceptionGroup) and inner.exceptions:
        inner = inner.exceptions[0]
    if isinstance(inner, httpx.HTTPStatusError):
        return inner.response.status_code == 401
    return "401" in str(inner)


def _is_timeout(exc: BaseException) -> bool:
    """A deadline expired: ``asyncio.timeout`` / anyio, or the MCP session's own
    per-request timeout (an ``McpError`` carrying HTTP 408)."""
    leaf = unwrap_exception(exc)
    if isinstance(leaf, TimeoutError):
        return True
    error = getattr(leaf, "error", None)
    return getattr(error, "code", None) == httpx.codes.REQUEST_TIMEOUT


_shell_path_cache: dict[str, tuple[float, str]] = {}


def _detect_shell_path(default: str) -> str:
    """The user's interactive ``PATH`` (blocking — run in a thread).

    Resolving it spawns a login shell per candidate (``zsh -l`` / ``bash -l``
    sources rc files), up to 5s each. The result is reused for a few minutes so
    a burst of tool calls does not pay for it every time.
    """
    cached = _shell_path_cache.get(default)
    if cached is not None and time.monotonic() - cached[0] < _SHELL_PATH_TTL_S:
        return cached[1]
    resolved = default
    for shell in ("zsh", "bash"):
        try:
            out = subprocess.check_output(
                [shell, "-l", "-c", "echo $PATH"],
                text=True,
                timeout=5,
                stderr=subprocess.DEVNULL,
            ).strip()
            lines = [line for line in out.splitlines() if line.strip()]
            if lines:
                resolved = lines[-1]
                break
        except Exception:  # noqa: BLE001
            continue
    _shell_path_cache[default] = (time.monotonic(), resolved)
    return resolved


# ── Opening a session ────────────────────────────────────────────────────


async def _enter_stdio(
    stack: AsyncExitStack,
    *,
    command: str,
    args: list[str],
    working_dir: str | None,
    stored_env: dict[str, str] | None,
    request_timeout: float | None,
) -> ClientSession:
    from mcp.client.stdio import StdioServerParameters, stdio_client

    # Run off the event loop so opening a connector never freezes the server
    # for other requests.
    shell_path_str: str = await asyncio.to_thread(_detect_shell_path, os.environ.get("PATH", ""))

    # Bundled stdio connectors reference their entry point with the
    # ``{mcp_dir}`` placeholder; expand it the same way the runtime resolver
    # does, or the server is spawned as ``python {mcp_dir}/...`` and exits with
    # "Connection closed".
    from valuz_agent.adapters.mcp_resolver import expand_mcp_dir

    raw_command = expand_mcp_dir(command)
    extra_args: list[str] = []
    if " " in raw_command:
        parts = shlex.split(raw_command)
        raw_command = parts[0]
        extra_args = parts[1:]

    resolved_command = shutil.which(raw_command, path=shell_path_str) or raw_command
    launch_args = extra_args + [expand_mcp_dir(str(a)) for a in args]
    launch_env: dict[str, str] = os.environ.copy()
    launch_env["PATH"] = shell_path_str
    if stored_env:
        launch_env.update(stored_env)

    params = StdioServerParameters(
        command=resolved_command, args=launch_args, env=launch_env, cwd=working_dir
    )
    read, write = await stack.enter_async_context(stdio_client(params))
    session = await stack.enter_async_context(
        ClientSession(
            read,
            write,
            read_timeout_seconds=(
                timedelta(seconds=request_timeout) if request_timeout is not None else None
            ),
        )
    )
    await session.initialize()
    return session


async def _enter_http(
    stack: AsyncExitStack,
    *,
    transport: str,
    url: str,
    headers: dict[str, str],
    timeout: float,
    request_timeout: float | None,
) -> ClientSession:
    read_timeout = timedelta(seconds=request_timeout) if request_timeout is not None else None
    if transport == "sse":
        from mcp.client.sse import sse_client

        r, w = await stack.enter_async_context(
            sse_client(url, headers=headers, timeout=timeout, sse_read_timeout=timeout)
        )
    else:
        from mcp.client.streamable_http import streamable_http_client

        http_client = await stack.enter_async_context(
            httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(timeout))
        )
        r, w, _ = await stack.enter_async_context(
            streamable_http_client(url, http_client=http_client)
        )
    session = await stack.enter_async_context(
        ClientSession(r, w, read_timeout_seconds=read_timeout)
    )
    await session.initialize()
    return session


@asynccontextmanager
async def open_connector_session(
    svc: ConnectorService,
    user_id: str,
    connector_id: str,
    *,
    timeout: float = PROBE_TIMEOUT_S,
    request_timeout: float | None = None,
    allow_stdio: bool = True,
    unauthorized_retry_delays: tuple[float, ...] = (1.5, 3.0),
) -> AsyncIterator[ClientSession]:
    """Connect to the connector ``connector_id`` of ``user_id``; yield an
    initialised MCP ``ClientSession``.

    ``timeout`` bounds the connect / transport reads (the probe's 15s);
    ``request_timeout`` additionally bounds each MCP request on the session.
    ``allow_stdio=False`` refuses stdio connectors (anywhere but a local
    deployment). Raises ``ConnectorNotFoundError`` / ``ConnectorConfigError`` /
    ``StdioUnavailableError`` for what is wrong with the connector, and lets the
    transport's own error propagate when it cannot be reached.

    Opening a session is not retried on a cancelled task: only ``Exception`` is
    caught for the transport fallback, so ``asyncio.timeout`` keeps working.
    """
    view = await svc.get_connector(user_id, connector_id)
    if view is None:
        raise ConnectorNotFoundError(connector_id)
    row = await svc._ds.get_by_id(user_id, connector_id)

    async with AsyncExitStack() as owner:
        # ── stdio ────────────────────────────────────────────────────────
        if view.transport == "stdio":
            if not allow_stdio:
                raise StdioUnavailableError(
                    "stdio connectors are only available on a local deployment"
                )
            if not view.command:
                raise ConnectorConfigError("Stdio connector has no command configured")
            stored_env: dict[str, str] | None = None
            if row and row.env_json:
                try:
                    parsed_env = json.loads(row.env_json)
                    if isinstance(parsed_env, dict):
                        stored_env = {str(k): str(v) for k, v in parsed_env.items()}
                except json.JSONDecodeError:
                    pass
            async with AsyncExitStack() as attempt:
                session = await _enter_stdio(
                    attempt,
                    command=view.command,
                    args=list(view.args or []),
                    working_dir=view.working_dir,
                    stored_env=stored_env,
                    request_timeout=request_timeout,
                )
                owner.push_async_exit(attempt.pop_all())
            yield session
            return

        # ── http / sse ───────────────────────────────────────────────────
        if not view.url:
            raise ConnectorConfigError("Connector has no URL configured")

        # Same injection truth as the runtime resolver (Acceptance #8).
        if row is None:
            ov_headers = mcp_request_headers()
            ov_params: dict[str, str] = {}
        else:
            ov_headers, ov_params = build_request_overrides(row)

        if view.auth_type == "oauth":
            # OAuth layers on AFTER build_request_overrides — mirrors the resolver.
            token_json = row.oauth_token_json if row is not None else None
            if token_json:
                try:
                    from mcp.shared.auth import OAuthToken

                    token = OAuthToken.model_validate_json(token_json)
                    ov_headers["Authorization"] = f"Bearer {token.access_token}"
                except Exception:  # noqa: BLE001
                    pass

        target_url = merge_params_into_url(view.url, ov_params)

        async def _connect_once(transport: str) -> tuple[ClientSession, AsyncExitStack]:
            async with AsyncExitStack() as attempt:
                session = await _enter_http(
                    attempt,
                    transport=transport,
                    url=target_url,
                    headers=ov_headers,
                    timeout=timeout,
                    request_timeout=request_timeout,
                )
                # Success: hand the open transports to the caller; on any error
                # above the ``async with`` closes whatever was entered.
                return session, attempt.pop_all()

        async def _attempt() -> tuple[ClientSession, AsyncExitStack]:
            primary = view.transport if view.transport in ("http", "sse") else "http"
            fallback = "sse" if primary == "http" else "http"
            try:
                return await _connect_once(primary)
            except Exception as first_exc:  # noqa: BLE001
                try:
                    return await _connect_once(fallback)
                except Exception:  # noqa: BLE001
                    raise unwrap_exception(first_exc) from None

        try:
            # A no-auth connector answering 401 is anomalous — almost always a
            # transient rate-limit on a free anonymous tier (Firecrawl throttles
            # bursts), so retry with a short backoff. OAuth 401s are real
            # (token) and handled by the refresh path below — never retried here.
            session, holder = await retry_async(
                _attempt,
                retry_if=lambda e: view.auth_type != "oauth" and is_unauthorized(e),
                delays=unauthorized_retry_delays,
            )
        except BaseException as exc:
            # An OAuth connector whose access token expired answers 401. Try a
            # silent refresh with the stored refresh_token, then retry once with
            # the fresh token before giving up (a hard failure leaves the caller
            # to re-authorize).
            if view.auth_type == "oauth" and row is not None and is_unauthorized(exc):
                refreshed_json = await ext.connector_oauth_refresh.refresh_after_unauthorized(
                    row=row,
                    connectors=svc._ds,
                    token_json=row.oauth_token_json,
                )
                if not refreshed_json:
                    raise
                from mcp.shared.auth import OAuthToken

                token = OAuthToken.model_validate_json(refreshed_json)
                ov_headers["Authorization"] = f"Bearer {token.access_token}"
                session, holder = await _attempt()
            else:
                raise

        owner.push_async_exit(holder)
        yield session


# ── Tools API ────────────────────────────────────────────────────────────


async def _list_all_tools(session: ClientSession) -> list[ConnectorTool]:
    tools: list[ConnectorTool] = []
    cursor: str | None = None
    for _ in range(_MAX_TOOL_PAGES):
        page = await session.list_tools(cursor)
        for raw in page.tools:
            tool = _tool_from_mcp(raw)
            if tool is not None:
                tools.append(tool)
        cursor = page.nextCursor
        if not cursor:
            break
    return tools


async def _resolve_enabled_connector(svc: ConnectorService, user_id: str, id_or_slug: str) -> str:
    """The row id of ``user_id``'s enabled connector named by id or slug."""
    row = await svc._ds.get_by_id(user_id, id_or_slug)
    if row is None:
        row = await svc._ds.get_by_slug(user_id, id_or_slug)
    if row is None:
        raise ConnectorNotFoundError(id_or_slug)
    if not row.enabled:
        raise ConnectorDisabledError(row.slug)
    return str(row.id)


def _local_deployment() -> bool:
    from valuz_agent.infra.config import settings

    return settings.deployment_type == "local"


async def list_connector_tools(
    svc: ConnectorService,
    user_id: str,
    id_or_slug: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[str, list[ConnectorTool]]:
    """``(connector_id, tools)`` — every tool with its full input schema."""
    connector_id = await _resolve_enabled_connector(svc, user_id, id_or_slug)
    try:
        async with asyncio.timeout(timeout):
            async with open_connector_session(
                svc,
                user_id,
                connector_id,
                timeout=timeout,
                request_timeout=timeout,
                allow_stdio=_local_deployment(),
            ) as session:
                tools = await _list_all_tools(session)
    except Exception as exc:
        if _is_timeout(exc):
            raise ConnectorTimeoutError(f"connector did not answer within {timeout:g}s") from exc
        raise
    return connector_id, tools


async def call_connector_tool(
    svc: ConnectorService,
    user_id: str,
    id_or_slug: str,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    *,
    allow_write: bool = False,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> ConnectorCallResult:
    """Call ``tool_name`` on the connector.

    One session does it all: list the tools (the unknown-tool and write-guard
    checks need the tool's annotations), then call. A tool that is not declared
    read-only (``annotations.readOnlyHint is True``) is refused unless
    ``allow_write`` — and it is refused *before* anything is sent to it.
    """
    connector_id = await _resolve_enabled_connector(svc, user_id, id_or_slug)
    verdict = ""
    result: Any = None
    try:
        async with asyncio.timeout(timeout):
            async with open_connector_session(
                svc,
                user_id,
                connector_id,
                timeout=timeout,
                request_timeout=timeout,
                allow_stdio=_local_deployment(),
            ) as session:
                tools = await _list_all_tools(session)
                tool = next((t for t in tools if t.name == tool_name), None)
                if tool is None:
                    verdict = "unknown"
                elif not tool.read_only and not allow_write:
                    verdict = "write"
                else:
                    result = await session.call_tool(
                        tool_name,
                        arguments or {},
                        read_timeout_seconds=timedelta(seconds=timeout),
                    )
    except Exception as exc:
        if _is_timeout(exc):
            raise ConnectorTimeoutError(f"tool call did not finish within {timeout:g}s") from exc
        raise
    # Raised outside the transports' task groups, so callers see these as they are.
    if verdict == "unknown":
        raise ToolNotFoundError(tool_name)
    if verdict == "write":
        raise WriteToolRequiresConfirmationError(tool_name)
    blocks = [
        block.model_dump(mode="json", exclude_none=True) if hasattr(block, "model_dump") else block
        for block in (result.content or [])
    ]
    structured = result.structuredContent
    return ConnectorCallResult(
        content=blocks,
        structured_content=structured if isinstance(structured, dict) else None,
        is_error=bool(result.isError),
    )


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "PROBE_TIMEOUT_S",
    "ConnectorCallResult",
    "ConnectorClientError",
    "ConnectorConfigError",
    "ConnectorDisabledError",
    "ConnectorNotFoundError",
    "ConnectorTimeoutError",
    "ConnectorTool",
    "StdioUnavailableError",
    "ToolNotFoundError",
    "WriteToolRequiresConfirmationError",
    "call_connector_tool",
    "is_unauthorized",
    "list_connector_tools",
    "open_connector_session",
    "retry_async",
    "unwrap_exception",
]
