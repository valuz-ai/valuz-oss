"""The ``execute_code`` ToolDef — PTC's single execution entry point.

The handler runs agent-authored Python in a fresh subprocess anchored at the
session cwd. The subprocess env carries a one-shot execution token and the
kernel's loopback PTC endpoint — never upstream URLs or credentials; the
generated wrappers in the materialized skill POST every tool call back
through ``app/ptc_router.py``. Only stdout (truncated) and the created-file
list return to the model.

Registration: ``app/dependencies.init_dependencies`` calls
``register_execute_code_tool(get_store)`` once the store singleton exists —
sessions opt in by declaring ``ToolDef(name="execute_code")`` in their
``agent_config.tools`` (resolved by ``build_toolkit_for_config``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.tools import ExecContext, ToolDef, ToolResult
from src.core.types import McpServerConfig, McpStdioServerConfig
from src.ptc.execution_registry import (
    ExecutionRecord,
    register_execution,
    revoke_execution,
)
from src.ptc.interpreter import python3_path, python3_unavailable_reason
from src.ptc.upstream import UpstreamPool

if TYPE_CHECKING:
    from src.core.store_port import StorePort

logger = logging.getLogger(__name__)

EXECUTE_CODE_TOOL_NAME = "execute_code"

# Session.metadata key the host stamps to opt a session into PTC:
#   {"servers": ["valuz-data-67b487", ...]}
# Names must match ``session.mcp_servers`` entries; stdio servers are
# ignored (PTC v1 forwards to HTTP/SSE upstreams only).
PTC_METADATA_KEY = "ptc"

# Subprocess env contract consumed by the generated ``mcp_client.py``.
PTC_CALL_URL_ENV = "VALUZ_PTC_CALL_URL"

# Base of the kernel's PTC route as reachable from a local subprocess.
# Override for non-default ports / split-kernel deployments; the default
# matches the in-process desktop backend (same convention as
# ``CODEX_TOOLKIT_BASE_URL``). Includes the frozen route prefix.
PTC_ENDPOINT_ENV = "VALUZ_PTC_ENDPOINT"
PTC_ENDPOINT_DEFAULT = "http://127.0.0.1:8000/kernel/v1/ptc"

PTC_TIMEOUT_ENV = "VALUZ_PTC_TIMEOUT_SECONDS"
_DEFAULT_TIMEOUT_SECONDS = 180.0

_STDOUT_MAX_CHARS = 6_000
_STDERR_MAX_CHARS = 6_000

# Parent-env keys the subprocess inherits. Everything else — most notably
# provider API keys living in the kernel process env — is withheld.
_ENV_PASSTHROUGH = ("PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "TZ", "SYSTEMROOT")

# Materialized-skill directory name (see skills_materialize consumers); the
# generated ``tools/`` package lives inside. The cwd itself is appended so a
# workspace with a bare ``tools/`` tree (tests, hand-made setups) works too.
PTC_SKILL_DIRNAME = "ptc-tools"
_SKILL_ROOT_CANDIDATES = (
    os.path.join(".agents", "skills", PTC_SKILL_DIRNAME),
    os.path.join(".claude", "skills", PTC_SKILL_DIRNAME),
)

# PTC's private cwd namespace. A DOT name so it stays out of the user-facing
# file tree and cannot collide with a user's own directory; deliberately NOT
# under ``.agents`` (that tree belongs to the Open Agent Skills standard, and
# the files_created snapshot skips it while dumped files must stay reported).
# Layout: ``.ptc/runs/`` archives each executed program (written BEFORE the
# before-snapshot, so archives are never reported as created files);
# ``.ptc/work/`` is the dump-first scratch space, pre-created per run.
PTC_DIRNAME = ".ptc"
_RUNS_SUBDIR = os.path.join(PTC_DIRNAME, "runs")
PTC_WORK_DIRNAME = os.path.join(PTC_DIRNAME, "work")

# ``files_created`` snapshot guardrails: never let a huge project tree turn
# the bookkeeping into the expensive part of a run.
_SNAPSHOT_SKIP_DIRS = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__", ".agents", ".claude", ".codex"}
)
_SNAPSHOT_MAX_ENTRIES = 50_000

_EXECUTE_CODE_DESCRIPTION = (
    "Execute a Python program in the session workspace. The program imports "
    "generated data-tool wrappers (`from tools.<server> import <tool>`) and "
    "chains multiple tool calls, computation (loops, pandas if installed), "
    "and file output in one run. Only what the program prints returns to "
    "you — save sizeable raw results under `.ptc/work/` and print compact "
    "summaries."
)

_EXECUTE_CODE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": "Python source to execute. Print only compact summaries.",
        },
        "description": {
            "type": "string",
            "description": "What the program does (5-10 words, active voice).",
        },
    },
    "required": ["code"],
}


def ptc_call_url(token: str) -> str:
    base = (os.environ.get(PTC_ENDPOINT_ENV) or PTC_ENDPOINT_DEFAULT).rstrip("/")
    return f"{base}/exec/{token}/call"


def eligible_ptc_servers(session: Any) -> dict[str, McpServerConfig]:
    """The session's PTC allowlist resolved against its live MCP configs.

    Names come from ``metadata["ptc"]["servers"]`` (host-stamped, refreshed
    per turn alongside the MCP re-stamp); configs come from
    ``session.mcp_servers`` so credentials are always the current ones.
    Stdio entries are dropped — the forwarder speaks HTTP/SSE only.
    """
    meta = session.metadata.get(PTC_METADATA_KEY) if isinstance(session.metadata, dict) else None
    names = meta.get("servers") if isinstance(meta, dict) else None
    if not isinstance(names, list):
        return {}
    wanted = {str(n) for n in names}
    out: dict[str, McpServerConfig] = {}
    for cfg in session.mcp_servers or ():
        if cfg.name in wanted and not isinstance(cfg, McpStdioServerConfig):
            out[cfg.name] = cfg
    return out


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head, tail = int(max_chars * 0.7), int(max_chars * 0.2)
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [{omitted} chars omitted] ...\n{text[-tail:]}"


def _snapshot_files(cwd: Path) -> set[str] | None:
    """Relative paths of files under *cwd*, or ``None`` when the tree is too
    large to snapshot cheaply (created-file reporting then degrades)."""
    found: set[str] = set()
    stack = [cwd]
    entries = 0
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            entries += 1
            if entries > _SNAPSHOT_MAX_ENTRIES:
                return None
            name = child.name
            if child.is_dir():
                if name not in _SNAPSHOT_SKIP_DIRS:
                    stack.append(child)
            elif child.is_file():
                try:
                    found.add(str(child.relative_to(cwd)))
                except ValueError:
                    continue
    return found


def _build_subprocess_env(cwd: Path, token: str) -> dict[str, str]:
    env = {key: os.environ[key] for key in _ENV_PASSTHROUGH if key in os.environ}
    python_path = [
        str(cwd / candidate) for candidate in _SKILL_ROOT_CANDIDATES if (cwd / candidate).is_dir()
    ]
    python_path.append(str(cwd))
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    env[PTC_CALL_URL_ENV] = ptc_call_url(token)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _archive_code(cwd: Path, code: str, token: str) -> Path:
    runs_dir = cwd / _RUNS_SUBDIR
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"exec_{int(time.time() * 1000)}_{token[:8]}.py"
    path.write_text(code, encoding="utf-8")
    return path


def _kill_process(proc: asyncio.subprocess.Process) -> None:
    """Terminate the subprocess and (POSIX) its whole process group."""
    if proc.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def _timeout_seconds() -> float:
    raw = os.environ.get(PTC_TIMEOUT_ENV, "")
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_TIMEOUT_SECONDS


def _settle(record: ExecutionRecord) -> None:
    """Revoke the token and close the upstream pool, cancellation-safe.

    Revocation is synchronous (later calls 404 immediately). Pool teardown
    is fire-and-forget: it only cancels worker tasks and closes transports,
    and must not be lost when the handler itself is being cancelled.
    """
    revoke_execution(record.token)
    pool = record.upstream_pool
    if pool is None:
        return
    try:
        task = asyncio.get_running_loop().create_task(pool.close())
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    except RuntimeError:  # no running loop (sync test contexts)
        pass


def build_execute_code_tool(store_getter: Callable[[], StorePort]) -> ToolDef:
    """Construct the ToolDef; the store getter is resolved per call so the
    registration can happen before/independently of store rebuilds."""

    async def _handler(args: dict[str, Any], ctx: ExecContext) -> ToolResult:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return ToolResult(content="ERROR\n`code` must be a non-empty string", is_error=True)

        reason = python3_unavailable_reason()
        if reason is not None:
            return ToolResult(content=f"ERROR\nPTC execution unavailable: {reason}", is_error=True)

        session = await store_getter().load_session(ctx.user_id, ctx.session_id)
        if session is None:
            return ToolResult(content=f"ERROR\nsession {ctx.session_id!r} not found", is_error=True)

        servers = eligible_ptc_servers(session)
        if not servers:
            return ToolResult(
                content=(
                    "ERROR\nno PTC-enabled data servers on this session — "
                    "call the tools directly instead"
                ),
                is_error=True,
            )

        cwd = Path(session.cwd or ctx.workspace)
        if not cwd.is_dir():
            return ToolResult(content=f"ERROR\nworkspace {cwd} does not exist", is_error=True)

        record = register_execution(
            session_id=session.id,
            user_id=session.user_id,
            cwd=str(cwd),
            servers=servers,
        )
        record.upstream_pool = UpstreamPool(servers)
        try:
            return await _run(record, cwd, code)
        finally:
            _settle(record)

    async def _run(record: ExecutionRecord, cwd: Path, code: str) -> ToolResult:
        (cwd / PTC_WORK_DIRNAME).mkdir(parents=True, exist_ok=True)
        code_path = _archive_code(cwd, code, record.token)
        env = _build_subprocess_env(cwd, record.token)
        before = _snapshot_files(cwd)
        timeout = _timeout_seconds()

        spawn_kwargs: dict[str, Any] = {}
        if os.name == "posix":
            spawn_kwargs["start_new_session"] = True

        interpreter = python3_path()
        assert interpreter is not None  # guarded by the probe above
        proc = await asyncio.create_subprocess_exec(
            interpreter,
            str(code_path),
            cwd=str(cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **spawn_kwargs,
        )
        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            timed_out = True
            _kill_process(proc)
            stdout_b, stderr_b = await proc.communicate()
        except asyncio.CancelledError:
            _kill_process(proc)
            raise

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        after = _snapshot_files(cwd)
        created: list[str] = []
        if before is not None and after is not None:
            created = sorted(after - before)

        calls = record.sub_calls
        logger.info(
            "ptc: execute_code settled (session=%s exit=%s timed_out=%s calls=%d created=%d)",
            record.session_id,
            proc.returncode,
            timed_out,
            calls,
            len(created),
        )

        if timed_out:
            return ToolResult(
                content=(
                    f"ERROR\nExecution timed out after {int(timeout)}s and was killed. "
                    "Split the work into smaller programs."
                ),
                is_error=True,
            )
        if proc.returncode != 0:
            detail = _truncate(stderr or stdout, _STDERR_MAX_CHARS)
            return ToolResult(content=f"ERROR\n{detail}", is_error=True)

        parts = ["SUCCESS"]
        if stdout.strip():
            parts.append(_truncate(stdout, _STDOUT_MAX_CHARS))
        if created:
            parts.append("Files created: " + ", ".join(created))
        return ToolResult(content="\n".join(parts))

    return ToolDef(
        name=EXECUTE_CODE_TOOL_NAME,
        description=_EXECUTE_CODE_DESCRIPTION,
        parameters=_EXECUTE_CODE_PARAMETERS,
        handler=_handler,
        read_only=False,
    )


def register_execute_code_tool(store_getter: Callable[[], StorePort]) -> None:
    """Install the ``execute_code`` implementation into the global tool
    registry — called once from ``app.dependencies.init_dependencies``."""
    from src.core.tool_registry import register_tool

    register_tool(build_execute_code_tool(store_getter))


def maybe_expose_execute_code(toolkit: Any, session: Any) -> bool:
    """Expose ``execute_code`` on a session's toolkit when the session
    opted into PTC (``metadata["ptc"].servers`` resolves to ≥1 server).

    Called by the runtime factory for every session; a no-op unless the
    implementation was registered at boot and the session qualifies.
    Returns ``True`` when the tool was added.
    """
    from src.core.tool_registry import get_registered_tool

    if toolkit.get(EXECUTE_CODE_TOOL_NAME) is not None:
        return False
    if not eligible_ptc_servers(session):
        return False
    registered = get_registered_tool(EXECUTE_CODE_TOOL_NAME)
    if registered is None or registered.handler is None:
        return False
    toolkit.register(registered)
    return True
