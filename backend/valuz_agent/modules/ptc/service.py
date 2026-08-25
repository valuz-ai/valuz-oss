"""PTC skill assembly — code-face selection, discovery, and generation.

Owns two questions:

1. **Which of a session's MCP servers enter the code face** —
   ``code_face_server_names``. P2 scope is the Valuz Data service:
   the builtin ``valuz-search`` / ``valuz-data`` slugs plus any manually
   configured connector pointing at the same host (auth workarounds create
   suffixed copies like ``valuz-data-67b487``). The interactive built-ins
   (``harness`` / ``valuz_*`` loopback servers) never qualify — their URLs
   are not the data host. P3 replaces this with a per-connector
   ``code_callable`` flag.
2. **Materializing the generated skill** — ``ensure_ptc_skill``. One
   directory per (user, server-set), named ``ptc-tools-<hash>`` with a
   SKILL.md whose frontmatter ``name: ptc-tools`` makes the kernel's skills
   materializer link it under the stable name the executor's PYTHONPATH
   expects. Discovery (tools/list with the session's live credentials) runs
   only when the manifest is missing or the codegen version moved — never
   per turn.

Tool-level eligibility stays fail-closed in the generator
(``is_code_callable``: ``annotations.readOnlyHint is True``).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.ptc.tool_generator import (
    ToolFunctionGenerator,
    ToolInfo,
    codegen_version,
    is_code_callable,
    sanitize_name,
)

logger = logging.getLogger(__name__)

# P2 code-face scope: the Valuz Data service (see module docstring).
CODE_FACE_BUILTIN_SLUGS = ("valuz-search", "valuz-data")
CODE_FACE_HOSTS = ("data.valuz.cn",)

_DISCOVERY_TIMEOUT_SECONDS = 20.0

SKILL_DIR_PREFIX = "ptc-tools"


def code_face_server_names(mcp_servers: Any) -> list[str]:
    """Names of the session MCP entries that qualify for the code face.

    HTTP entries only (the kernel forwarder speaks HTTP/SSE); qualification
    is by builtin slug or by upstream host. Sorted for stable downstream
    hashing/compares.
    """
    names: set[str] = set()
    for cfg in mcp_servers or ():
        url = getattr(cfg, "url", None)
        if not url:  # stdio entries carry no url
            continue
        name = getattr(cfg, "name", "") or ""
        host = urlsplit(url).hostname or ""
        if name in CODE_FACE_BUILTIN_SLUGS or host in CODE_FACE_HOSTS:
            names.add(name)
    return sorted(names)


def is_ptc_skill_path(path: str) -> bool:
    """True when a ``session.skills`` entry is a generated PTC skill dir."""
    return Path(path).name.startswith(SKILL_DIR_PREFIX)


def _server_set_hash(names: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()[:10]


async def _discover_tools(cfg: Any) -> list[dict[str, Any]]:
    """tools/list against one server with the session's live credentials.

    Returns raw schema dicts (name / description / inputSchema /
    outputSchema / annotations) — the exact shape ``ToolInfo.from_dict``
    consumes. Raises on any transport/protocol failure; the caller decides
    the all-or-nothing policy.
    """
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = dict(getattr(cfg, "headers", None) or {})
    async with httpx.AsyncClient(
        headers=headers or None, timeout=_DISCOVERY_TIMEOUT_SECONDS, trust_env=False
    ) as http_client:
        async with streamable_http_client(cfg.url, http_client=http_client) as (read, write, _):
            async with ClientSession(
                read,
                write,
                read_timeout_seconds=timedelta(seconds=_DISCOVERY_TIMEOUT_SECONDS),
            ) as session:
                await session.initialize()
                result = await session.list_tools()
    return [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True)
        for tool in (result.tools or [])
    ]


async def ensure_ptc_skill(user_id: str, configs: list[Any]) -> Path | None:
    """Materialize (or reuse) the generated PTC skill for this server set.

    Returns the skill directory to attach to ``session.skills``, or ``None``
    when the code face cannot be built (discovery failure, or no eligible
    tools) — the caller then treats PTC as off for the turn, fail-closed.
    """
    names = sorted(getattr(cfg, "name", "") for cfg in configs)
    if not names:
        return None
    skill_dir = (
        fs_registry.ptc_skill_root(user_id) / f"{SKILL_DIR_PREFIX}-{_server_set_hash(names)}"
    )
    manifest_path = skill_dir / "manifest.json"

    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("codegen_version") == codegen_version():
                return skill_dir
        except (json.JSONDecodeError, OSError):
            pass  # unreadable manifest → regenerate

    # Discovery is all-or-nothing: a half-built skill would advertise tools
    # whose wrappers are missing, which is worse than no code face.
    tools_by_server: dict[str, list[ToolInfo]] = {}
    schema_hashes: dict[str, str] = {}
    for cfg in configs:
        try:
            raw_tools = await _discover_tools(cfg)
        except Exception as exc:  # noqa: BLE001 — degrade to "no code face"
            logger.warning(
                "ptc: tools/list failed for server %r — skill not built: %s", cfg.name, exc
            )
            return None
        eligible = [
            info for raw in raw_tools if is_code_callable(info := ToolInfo.from_dict(raw, cfg.name))
        ]
        if not eligible:
            logger.info("ptc: server %r has no code-callable tools — skipped", cfg.name)
            continue
        tools_by_server[cfg.name] = eligible
        canonical = json.dumps(raw_tools, sort_keys=True, ensure_ascii=False)
        schema_hashes[cfg.name] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    if not tools_by_server:
        return None

    _write_skill_tree(skill_dir, tools_by_server, schema_hashes)
    logger.info(
        "ptc: generated skill %s (%d server(s), %d tool(s))",
        skill_dir.name,
        len(tools_by_server),
        sum(len(v) for v in tools_by_server.values()),
    )
    return skill_dir


def _write_skill_tree(
    skill_dir: Path,
    tools_by_server: dict[str, list[ToolInfo]],
    schema_hashes: dict[str, str],
) -> None:
    generator = ToolFunctionGenerator()
    tools_dir = skill_dir / "tools"
    docs_root = tools_dir / "docs"
    tools_dir.mkdir(parents=True, exist_ok=True)

    (skill_dir / "SKILL.md").write_text(
        generator.generate_skill_markdown(tools_by_server), encoding="utf-8"
    )
    (tools_dir / "__init__.py").write_text(
        '"""Auto-generated PTC tool modules."""\n', encoding="utf-8"
    )
    (tools_dir / "mcp_client.py").write_text(
        generator.generate_mcp_client_code(sorted(tools_by_server)), encoding="utf-8"
    )
    for server_name, tools in tools_by_server.items():
        module = sanitize_name(server_name) or "server"
        (tools_dir / f"{module}.py").write_text(
            generator.generate_tool_module(server_name, tools), encoding="utf-8"
        )
        server_docs = docs_root / server_name
        server_docs.mkdir(parents=True, exist_ok=True)
        for tool in tools:
            doc_name = sanitize_name(tool.name) or "_invalid_tool"
            (server_docs / f"{doc_name}.md").write_text(
                generator.generate_tool_documentation(tool), encoding="utf-8"
            )

    # Manifest last: its presence marks a complete tree.
    (skill_dir / "manifest.json").write_text(
        json.dumps(
            {
                "codegen_version": codegen_version(),
                "servers": schema_hashes,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            indent=1,
        ),
        encoding="utf-8",
    )
