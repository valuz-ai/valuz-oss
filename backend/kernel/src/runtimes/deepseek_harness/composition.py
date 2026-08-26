"""Launch resolution + per-session Cordis composition for the dsh runtime.

The dsh runtime binary always demands an explicit plugin composition
(``DSH_CORDIS_CONFIG``); the adapter generates one file per runtime instance
so persona, tools, skills roots, and MCP servers are exactly what the kernel
Session declares — nothing is inherited from a user-level dsh install.

Launch resolution (mirrored by ``availability.probe_runtime_availability``):

1. ``VALUZ_DSH_RUNTIME_BIN`` — path to a single-file ``dsh-jsonrpc-agent``
   executable (explicit override).
2. ``VALUZ_DSH_RUNTIME_ENTRY`` — path to a ``packaged-bin.js`` inside an
   installed runtime closure, run on Node. The packaged desktop's sidecar
   points this at the staged ``libexec/dsh-runtime`` tree and supplies
   ``VALUZ_NODE_PATH`` (+ ``VALUZ_NODE_IS_ELECTRON=1`` → the spawn gets
   ``ELECTRON_RUN_AS_NODE=1``) — the same Electron-as-node contract the
   browser engine uses.
3. Vendored closure auto-detect — ``backend/vendor/dsh-runtime`` after
   ``npm ci`` (dev checkouts; refresh with ``scripts/vendor-dsh-runtime.sh``).
4. ``VALUZ_DSH_ROOT`` — a deepseek-harness source checkout; launches
   ``node --import tsx <root>/packages/examples/jsonrpc-demo/src/bin.ts``
   with the checkout as process cwd (contributor carrier; needs
   ``pnpm install`` in the checkout).

Composition-file placement follows bare-plugin resolution. ``packaged-bin``
carriers (tiers 1-3) resolve ``@deepseek-ai/dsh-*`` names from their own
installed closure, so the config lives in a temp dir. The source carrier's
``bin.js`` resolves relative to the config file's directory, so its file
lives under ``<root>/examples/.valuz-dsh/`` (the ``examples`` project's
node_modules carries every plugin we mount).

The generated file is JSON — a JSON document is valid YAML, which sidesteps
quoting/injection concerns for persona text and MCP header values.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.types import (
    McpHttpServerConfig,
    McpStdioServerConfig,
    ModelSettings,
    Session,
)
from src.runtimes.mcp_env import resolve_stdio_env

DSH_RUNTIME_BIN_ENV = "VALUZ_DSH_RUNTIME_BIN"
DSH_RUNTIME_ENTRY_ENV = "VALUZ_DSH_RUNTIME_ENTRY"
DSH_ROOT_ENV = "VALUZ_DSH_ROOT"
# Node resolution for JS-entry carriers — the same env contract the host's
# browser engine uses (packaged desktop: the sidecar sets VALUZ_NODE_PATH to
# its own Electron binary + VALUZ_NODE_IS_ELECTRON=1).
NODE_PATH_ENV = "VALUZ_NODE_PATH"
NODE_IS_ELECTRON_ENV = "VALUZ_NODE_IS_ELECTRON"
_SOURCE_ENTRY = "packages/examples/jsonrpc-demo/src/bin.ts"
_PACKAGED_BIN_REL = Path("@deepseek-ai") / "dsh-sdk-jsonrpc-demo" / "lib" / "packaged-bin.js"
# Dev-checkout vendored closure (backend/vendor/dsh-runtime after `npm ci`).
_VENDOR_DIR = Path(__file__).resolve().parents[4] / "vendor" / "dsh-runtime"

# ``EffortLevel`` -> the dsh DeepSeek adapter's ``reasoningEffort`` values
# (low/medium/high/max observed; ``xhigh`` has no dsh spelling -> ``max``).
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "max", "max": "max"}


@dataclass(frozen=True)
class DshLaunchSpec:
    argv: tuple[str, ...]
    cwd: str | None
    config_parent_dir: str
    # Extra environment for the subprocess (e.g. ELECTRON_RUN_AS_NODE=1 when
    # the Node carrier is the desktop's own Electron binary).
    env: dict[str, str] = field(default_factory=dict)
    # Whether the resolved closure carries the plan-mode plugin set
    # (dsh-plan-mode + dsh-user-questions + dsh-tool-ask-user +
    # valuz-dsh-kernel-bridge). Compositions referencing a missing bare
    # plugin fail to boot, so the plan rows are only emitted when the
    # closure actually has them — see ``_probe_plan_capable``.
    plan_capable: bool = False


def _resolve_node() -> tuple[str, dict[str, str]] | None:
    """The Node executable for JS-entry carriers, plus its extra env.

    ``VALUZ_NODE_PATH`` wins (the packaged desktop's sidecar points it at the
    app's own Electron binary and flags ``VALUZ_NODE_IS_ELECTRON=1``, which
    requires ``ELECTRON_RUN_AS_NODE=1`` on the spawn — without it Electron
    opens as a second GUI instance); otherwise a plain ``node`` from PATH.
    """
    override = os.environ.get(NODE_PATH_ENV, "").strip()
    if override and os.path.isfile(override):
        extra = (
            {"ELECTRON_RUN_AS_NODE": "1"}
            if os.environ.get(NODE_IS_ELECTRON_ENV, "").strip() == "1"
            else {}
        )
        return override, extra
    node = shutil.which("node")
    if node is not None:
        return node, {}
    return None


def _packaged_entry() -> Path | None:
    """The ``packaged-bin.js`` of an installed runtime closure, or None.

    ``VALUZ_DSH_RUNTIME_ENTRY`` (staged libexec tree in the packaged desktop)
    wins over the dev checkout's ``backend/vendor/dsh-runtime`` auto-detect.
    """
    entry_override = os.environ.get(DSH_RUNTIME_ENTRY_ENV, "").strip()
    if entry_override:
        path = Path(entry_override)
        return path if path.is_file() else None
    vendored = _VENDOR_DIR / "node_modules" / _PACKAGED_BIN_REL
    return vendored if vendored.is_file() else None


# The bare-plugin names the plan feature composes on top of the base set.
# All four must resolve from the closure or the subprocess fails to boot.
_PLAN_PLUGIN_PROBES = (
    Path("@deepseek-ai") / "dsh-plan-mode" / "lib" / "index.js",
    Path("@deepseek-ai") / "dsh-user-questions" / "lib" / "index.js",
    Path("@deepseek-ai") / "dsh-tool-ask-user" / "lib" / "index.js",
    Path("valuz-dsh-kernel-bridge") / "lib" / "index.js",
)


def _probe_plan_capable(entry: Path) -> bool:
    """Whether the packaged closure around ``entry`` carries the plan set.

    ``entry`` is ``<closure>/node_modules/@deepseek-ai/dsh-sdk-jsonrpc-demo/
    lib/packaged-bin.js``; bare plugins resolve from that ``node_modules``.
    """
    node_modules = entry.parents[3]
    return all((node_modules / probe).is_file() for probe in _PLAN_PLUGIN_PROBES)


def resolve_launch() -> DshLaunchSpec | None:
    """Resolve how to spawn the dsh runtime on this machine, or None."""
    bin_override = os.environ.get(DSH_RUNTIME_BIN_ENV, "").strip()
    if bin_override:
        if not (shutil.which(bin_override) or os.path.isfile(bin_override)):
            return None
        # A single-file executable's embedded plugin set is opaque — assume
        # no plan plugins rather than emit a composition that cannot boot.
        return DshLaunchSpec(
            argv=(bin_override,),
            cwd=None,
            config_parent_dir=tempfile.gettempdir(),
        )
    entry = _packaged_entry()
    if entry is not None:
        node = _resolve_node()
        if node is not None:
            node_bin, extra_env = node
            return DshLaunchSpec(
                argv=(node_bin, str(entry)),
                cwd=None,
                config_parent_dir=tempfile.gettempdir(),
                env=extra_env,
                plan_capable=_probe_plan_capable(entry),
            )
    root = os.environ.get(DSH_ROOT_ENV, "").strip()
    if root:
        source_entry = Path(root) / _SOURCE_ENTRY
        node = shutil.which("node")
        if source_entry.is_file() and node is not None:
            # The source checkout resolves plugins from the examples
            # project's node_modules, which has no valuz-dsh-kernel-bridge —
            # plan rows would fail the boot, so the contributor carrier
            # stays plan-incapable.
            return DshLaunchSpec(
                argv=(node, "--import", "tsx", str(source_entry)),
                cwd=root,
                config_parent_dir=str(Path(root) / "examples" / ".valuz-dsh"),
            )
    return None


def launch_unavailable_reason() -> str | None:
    """Human-readable availability diagnosis, None when launchable."""
    bin_override = os.environ.get(DSH_RUNTIME_BIN_ENV, "").strip()
    if bin_override:
        if shutil.which(bin_override) or os.path.isfile(bin_override):
            return None
        return f"{DSH_RUNTIME_BIN_ENV}={bin_override!r} is not executable"
    if _packaged_entry() is not None:
        if _resolve_node() is not None:
            return None
        return "node (>= 22.19) not found for the installed dsh runtime closure"
    root = os.environ.get(DSH_ROOT_ENV, "").strip()
    if root:
        if not (Path(root) / _SOURCE_ENTRY).is_file():
            return f"{DSH_ROOT_ENV}={root!r} has no {_SOURCE_ENTRY}"
        if shutil.which("node") is None:
            return "node (>= 22.19) not found on PATH"
        return None
    return (
        "install the vendored dsh runtime (scripts/vendor-dsh-runtime.sh), or set "
        f"{DSH_RUNTIME_BIN_ENV} to a dsh-jsonrpc-agent executable, or "
        f"{DSH_ROOT_ENV} to a deepseek-harness checkout"
    )


def write_composition(
    session: Session,
    *,
    config_parent_dir: str,
    workspace_root: str,
    skills_root: str | None,
    model_settings: ModelSettings | None,
    kernel_toolkit: bool = False,
    plan_capable: bool = False,
    user_questions_url: str | None = None,
) -> str:
    """Write this session's composition file; returns the ``cordis.yml`` path.

    The caller owns cleanup of the returned file's parent directory
    (``cleanup_composition``).
    """
    config_dir = Path(config_parent_dir) / f"valuz-dsh-{session.id}-{uuid.uuid4().hex[:8]}"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / "cordis.yml"
    rows = build_composition_rows(
        session,
        workspace_root=workspace_root,
        skills_root=skills_root,
        model_settings=model_settings,
        kernel_toolkit=kernel_toolkit,
        plan_capable=plan_capable,
        user_questions_url=user_questions_url,
    )
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    path.chmod(0o600)
    return str(path)


def cleanup_composition(config_path: str | None) -> None:
    if not config_path:
        return
    shutil.rmtree(Path(config_path).parent, ignore_errors=True)


# The kernel's ``/mcp/toolkit/{session_id}`` bridge — kernel-owned ToolDefs
# (e.g. PTC's execute_code). The env name keeps the legacy codex spelling:
# the sandbox provisioner already exports it (sandbox_seatbelt), so dsh
# inherits the correct callback base in every deployment for free.
KERNEL_TOOLKIT_BASE_URL_ENV = "CODEX_TOOLKIT_BASE_URL"
KERNEL_TOOLKIT_BASE_URL_DEFAULT = "http://127.0.0.1:8000"
KERNEL_TOOLKIT_SERVER_NAME = "harness_toolkit"


def kernel_toolkit_url(session_id: str) -> str:
    base = (
        os.environ.get(KERNEL_TOOLKIT_BASE_URL_ENV, "").strip() or KERNEL_TOOLKIT_BASE_URL_DEFAULT
    )
    return f"{base.rstrip('/')}/mcp/toolkit/{session_id}"


# Base of the kernel's dsh user-questions route as reachable from a local
# subprocess. Same convention as ``VALUZ_PTC_ENDPOINT``: the default
# matches the in-process desktop backend and includes the frozen route
# prefix (``src`` must not import ``app.routes.KERNEL_API_PREFIX``).
USER_QUESTIONS_ENDPOINT_ENV = "VALUZ_DSH_UQ_ENDPOINT"
USER_QUESTIONS_ENDPOINT_DEFAULT = "http://127.0.0.1:8000/kernel/v1/dsh/user-questions"


def user_questions_endpoint(token: str) -> str:
    base = (
        os.environ.get(USER_QUESTIONS_ENDPOINT_ENV, "").strip() or USER_QUESTIONS_ENDPOINT_DEFAULT
    )
    return f"{base.rstrip('/')}/{token}"


# ``dsh-plan-mode``'s mandatory ``section`` — the deployment-owned plan
# discipline the model sees (prompt order 50) while plan mode is active.
# Same product contract as the Claude runtime's ``PLAN_MODE_DISCIPLINE``:
# plan-first applies to EVERY task type, not just code changes. dsh's plan
# mode is soft guidance by design (sandbox/approval enforce independently),
# so this text carries the whole behavioral contract.
PLAN_MODE_SECTION = """\
You are in plan mode: the user wants to align on a plan BEFORE you produce
anything. This applies to EVERY kind of task — research, analysis, and
writing included, not just code changes.

1. Keep reconnaissance lightweight and read-only: check which data sources /
   files / tools exist and what shape they have. Do NOT run the full
   analysis, mutate files, or draft the deliverable yet.
2. If key decisions are unclear, ask the user with the ask_user_question
   tool first.
3. Present a concise execution plan — goal, steps, data sources, deliverable
   format, and any open choices — by calling exit_plan_mode with the
   complete plan as markdown (starting with a # heading). Then stop and
   wait for the review.
4. Execute only after the plan is approved.

Exception: a trivial exchange that plainly needs no plan (a greeting, a
one-line factual question) may be answered directly."""


def build_composition_rows(
    session: Session,
    *,
    workspace_root: str,
    skills_root: str | None,
    model_settings: ModelSettings | None,
    kernel_toolkit: bool = False,
    plan_capable: bool = False,
    user_questions_url: str | None = None,
) -> list[dict[str, Any]]:
    """The plugin tree for one kernel session (pure; unit-testable).

    No dsh-side session persistence is mounted: the kernel events table is
    the system of record and cross-process continuation is replayed by the
    adapter's transcript sidecar (the SDK server cannot rehydrate persisted
    logs anyway — see docs/references/deepseek-harness/runtime-gap-analysis.md).
    """
    llm_config: dict[str, Any] = {}
    effort = model_settings.effort if model_settings is not None else None
    if effort is not None:
        llm_config["thinking"] = "enabled"
        llm_config["reasoningEffort"] = _EFFORT_MAP[effort]

    skills_config: dict[str, Any]
    if skills_root is not None:
        skills_config = {
            "enabled": True,
            "filesystem": {
                "includeDefaultRoots": False,
                "customSkillDirs": [skills_root],
                "watch": False,
            },
        }
    else:
        skills_config = {"enabled": False}

    spine_config: dict[str, Any] = {
        "workspaceContext": False,
        "skills": skills_config,
        "toolJobs": False,
    }
    if session.instructions.strip():
        spine_config["persona"] = session.instructions

    rows: list[dict[str, Any]] = [
        {"id": "sdk-jsonrpc-server", "name": "@deepseek-ai/dsh-sdk-jsonrpc-server"},
        {
            "id": "agent-core",
            "name": "@deepseek-ai/dsh-agent-spine-demo",
            "config": spine_config,
        },
        {"id": "llm-deepseek", "name": "@deepseek-ai/dsh-llm-deepseek", "config": llm_config},
        {"id": "subprocess", "name": "@deepseek-ai/dsh-subprocess-local"},
        {
            "id": "bash",
            "name": "@deepseek-ai/dsh-bash-local",
            "config": {"cwd": workspace_root},
        },
        {
            "id": "fs-local",
            "name": "@deepseek-ai/dsh-fs-local",
            "config": {"cwd": workspace_root},
        },
        {"id": "fs-observation-policy", "name": "@deepseek-ai/dsh-fs-observation-policy"},
        {"id": "tool-fs", "name": "@deepseek-ai/dsh-tool-fs"},
        {
            "id": "tool-todo",
            "name": "@deepseek-ai/dsh-tool-todo",
            # Required field (dsh's no-hardcoded-tunables doctrine): matches
            # the kernel's own TaskCreate semantics (parallel in-progress ok).
            "config": {"allowParallelInProgress": True},
        },
        {"id": "token-meter", "name": "@deepseek-ai/dsh-token-meter"},
        {"id": "compaction-basic", "name": "@deepseek-ai/dsh-compaction-basic"},
    ]
    if plan_capable:
        # Plan set — ALWAYS composed on a capable closure, never gated on
        # ``session.mode``: dsh-plan-mode keeps ``exit_plan_mode`` registered
        # in both states (stable tool catalog), plan state is per-session
        # durable (``plan/mode`` log events, default inactive), and the
        # bridge plugin converges it to the kernel-desired value at the
        # first pre-step. ``ask_user_question`` (dsh-tool-ask-user) rides
        # along in every mode — parity with Claude's AskUserQuestion /
        # codex's request_user_input clarifying path.
        rows.append({"id": "user-questions", "name": "@deepseek-ai/dsh-user-questions"})
        rows.append({"id": "tool-ask-user", "name": "@deepseek-ai/dsh-tool-ask-user"})
        rows.append(
            {
                "id": "plan-mode",
                "name": "@deepseek-ai/dsh-plan-mode",
                "config": {"section": PLAN_MODE_SECTION},
            }
        )
        bridge_config: dict[str, Any] = {"planActive": session.mode == "plan"}
        if user_questions_url:
            bridge_config["userQuestionsEndpoint"] = user_questions_url
        rows.append(
            {
                "id": "valuz-kernel-bridge",
                "name": "valuz-dsh-kernel-bridge",
                "config": bridge_config,
            }
        )
    rows.extend(_mcp_rows(session))
    if kernel_toolkit:
        # Kernel ToolDefs (registered by the runtime in mcp_bridge) surface
        # like any other MCP server: ``mcp__harness_toolkit__<tool>``.
        rows.append(
            {
                "id": "kernel-toolkit",
                "name": "@deepseek-ai/dsh-mcp-client",
                "config": {
                    "serverName": KERNEL_TOOLKIT_SERVER_NAME,
                    "transport": "streamable-http",
                    "url": kernel_toolkit_url(session.id),
                },
            }
        )
    return rows


def _mcp_rows(session: Session) -> list[dict[str, Any]]:
    """One ``dsh-mcp-client`` row per session MCP server.

    dsh registers each server's tools as ``mcp__<serverName>__<tool>`` — the
    same shape the other runtimes consume. NOTE: ``dsh-mcp-client`` is not yet
    part of the stock runtime closures (neither the examples project nor the
    sdk-runtime deploy root lists it); until the upstream dependency lands,
    a composition carrying these rows fails to boot. Sessions without MCP
    servers are unaffected.
    """
    rows: list[dict[str, Any]] = []
    for index, server in enumerate(session.mcp_servers):
        if isinstance(server, McpHttpServerConfig):
            config: dict[str, Any] = {
                "serverName": _server_name(server.name, index),
                "transport": "streamable-http",
                "url": server.url,
            }
            if server.headers:
                config["headers"] = dict(server.headers)
            rows.append(
                {"id": f"mcp-{index}", "name": "@deepseek-ai/dsh-mcp-client", "config": config}
            )
        elif isinstance(server, McpStdioServerConfig):
            config = {
                "serverName": _server_name(server.name, index),
                "transport": "stdio",
                "command": server.command,
                "args": list(server.args),
            }
            env = resolve_stdio_env(server)
            if env is not None:
                config["env"] = env
            rows.append(
                {"id": f"mcp-{index}", "name": "@deepseek-ai/dsh-mcp-client", "config": config}
            )
    return rows


def _server_name(name: str, index: int) -> str:
    """dsh requires ``[A-Za-z0-9_-]{1,32}`` server names, unique per instance."""
    cleaned = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in name)[:32]
    return cleaned or f"server_{index}"
