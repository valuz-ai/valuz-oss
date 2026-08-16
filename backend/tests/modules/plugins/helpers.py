"""Shared fixtures-as-functions for the plugin tests: build Agent Plugins /
legacy plugin trees and zips on disk."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

from valuz_agent.modules.plugins.manifest import MCP_SCHEMA_ID, PLUGIN_SCHEMA_ID


def write_skill(
    root: Path,
    slug: str,
    *,
    name: str | None = None,
    description: str = "Does a thing. Use when asked.",
    body: str = "# Instructions\n\nDo the thing.\n",
    extra: dict[str, str] | None = None,
    metadata: dict[str, str] | None = None,
) -> Path:
    """``root/<slug>/SKILL.md`` (+ ``extra`` relative files)."""
    skill_dir = root / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name or slug}\ndescription: {description}\n"
    if metadata:
        fm += "metadata:\n" + "".join(f'  {k}: "{v}"\n' for k, v in metadata.items())
    fm += "---\n\n"
    (skill_dir / "SKILL.md").write_text(fm + body, encoding="utf-8")
    for rel, content in (extra or {}).items():
        target = skill_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return skill_dir


def manifest_dict(name: str = "demo-plugin", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"$schema": PLUGIN_SCHEMA_ID, "name": name, "version": "1.0.0"}
    data.update(overrides)
    return data


def mcp_dict(servers: dict[str, Any]) -> dict[str, Any]:
    return {"$schema": MCP_SCHEMA_ID, "mcpServers": servers}


def build_agent_plugin(
    root: Path,
    *,
    name: str = "demo-plugin",
    skills: dict[str, dict[str, Any]] | None = None,
    servers: dict[str, Any] | None = None,
    manifest_extra: dict[str, Any] | None = None,
) -> Path:
    """Write an Agent Plugins layout under ``root`` (created) and return it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(
        json.dumps(manifest_dict(name, **(manifest_extra or {})), indent=2), encoding="utf-8"
    )
    for slug, spec in (skills or {"alpha": {}}).items():
        write_skill(root / "skills", slug, **spec)
    if servers is not None:
        (root / "mcp.json").write_text(json.dumps(mcp_dict(servers), indent=2), encoding="utf-8")
    return root


def build_legacy_plugin(
    root: Path,
    *,
    fmt: str = "codebuddy_plugin",
    name: str = "legacy-plugin",
    manifest_extra: dict[str, Any] | None = None,
    skills: dict[str, dict[str, Any]] | None = None,
    root_skill: bool = False,
    mcp_json: dict[str, Any] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest_dir = ".codebuddy-plugin" if fmt == "codebuddy_plugin" else ".claude-plugin"
    (root / manifest_dir).mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {"name": name, "version": "2.0.0", "description": "Legacy plugin"}
    manifest.update(manifest_extra or {})
    (root / manifest_dir / "plugin.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    for slug, spec in (skills or {}).items():
        write_skill(root / "skills", slug, **spec)
    if root_skill:
        (root / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: The plugin itself is one skill.\n---\n\n# Root\n",
            encoding="utf-8",
        )
        (root / "scripts").mkdir(exist_ok=True)
        (root / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    if mcp_json is not None:
        (root / ".mcp.json").write_text(json.dumps(mcp_json, indent=2), encoding="utf-8")
    return root


def zip_dir(root: Path, *, wrap: str | None = None) -> bytes:
    """Zip ``root``'s contents (optionally under a ``wrap/`` top-level folder)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                rel = path.relative_to(root).as_posix()
                zf.write(path, f"{wrap}/{rel}" if wrap else rel)
    return buffer.getvalue()
