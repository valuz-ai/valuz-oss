"""Launch resolution + per-session Cordis composition for the dsh runtime.

The dsh runtime binary always demands an explicit plugin composition
(``DSH_CORDIS_CONFIG``); the adapter generates one file per runtime instance
so persona, tools, skills roots, and MCP servers are exactly what the kernel
Session declares — nothing is inherited from a user-level dsh install.

Launch resolution (mirrored by ``availability.probe_runtime_availability``):

1. ``VALUZ_DSH_RUNTIME_BIN`` — path to the single-file ``dsh-jsonrpc-agent``
   executable (packaged/production carrier).
2. ``VALUZ_DSH_ROOT`` — a deepseek-harness source checkout; launches
   ``node --import tsx <root>/packages/examples/jsonrpc-demo/src/bin.ts``
   with the checkout as process cwd (dev carrier; needs ``pnpm install``
   in the checkout and ``node`` >= 22.19 on PATH).

Composition-file placement follows bare-plugin resolution: the loader
resolves ``@deepseek-ai/dsh-*`` names relative to the config file's
directory. In source mode the file therefore lives under
``<root>/examples/.valuz-dsh/`` (the ``examples`` project's node_modules
carries every plugin we mount); in exe mode the closed runtime tree
resolves names regardless of file location, so a temp dir is used.

The generated file is JSON — a JSON document is valid YAML, which sidesteps
quoting/injection concerns for persona text and MCP header values.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
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
DSH_ROOT_ENV = "VALUZ_DSH_ROOT"
_SOURCE_ENTRY = "packages/examples/jsonrpc-demo/src/bin.ts"

# ``EffortLevel`` -> the dsh DeepSeek adapter's ``reasoningEffort`` values
# (low/medium/high/max observed; ``xhigh`` has no dsh spelling -> ``max``).
_EFFORT_MAP = {"low": "low", "medium": "medium", "high": "high", "xhigh": "max", "max": "max"}


@dataclass(frozen=True)
class DshLaunchSpec:
    argv: tuple[str, ...]
    cwd: str | None
    config_parent_dir: str


def resolve_launch() -> DshLaunchSpec | None:
    """Resolve how to spawn the dsh runtime on this machine, or None."""
    bin_override = os.environ.get(DSH_RUNTIME_BIN_ENV, "").strip()
    if bin_override:
        if not (shutil.which(bin_override) or os.path.isfile(bin_override)):
            return None
        return DshLaunchSpec(
            argv=(bin_override,),
            cwd=None,
            config_parent_dir=tempfile.gettempdir(),
        )
    root = os.environ.get(DSH_ROOT_ENV, "").strip()
    if root:
        entry = Path(root) / _SOURCE_ENTRY
        node = shutil.which("node")
        if entry.is_file() and node is not None:
            return DshLaunchSpec(
                argv=(node, "--import", "tsx", str(entry)),
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
    root = os.environ.get(DSH_ROOT_ENV, "").strip()
    if root:
        if not (Path(root) / _SOURCE_ENTRY).is_file():
            return f"{DSH_ROOT_ENV}={root!r} has no {_SOURCE_ENTRY}"
        if shutil.which("node") is None:
            return "node (>= 22.19) not found on PATH"
        return None
    return (
        f"set {DSH_RUNTIME_BIN_ENV} to a dsh-jsonrpc-agent executable or "
        f"{DSH_ROOT_ENV} to a deepseek-harness checkout"
    )


def write_composition(
    session: Session,
    *,
    config_parent_dir: str,
    workspace_root: str,
    skills_root: str | None,
    model_settings: ModelSettings | None,
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
    )
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    path.chmod(0o600)
    return str(path)


def cleanup_composition(config_path: str | None) -> None:
    if not config_path:
        return
    shutil.rmtree(Path(config_path).parent, ignore_errors=True)


def build_composition_rows(
    session: Session,
    *,
    workspace_root: str,
    skills_root: str | None,
    model_settings: ModelSettings | None,
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
    rows.extend(_mcp_rows(session))
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
