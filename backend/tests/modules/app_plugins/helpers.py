"""Shared builders for the third-party plugin tests."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

INDEX_JS = "export default { id: 'acme.dashboard', apply() {} };\n"


def manifest_of(
    plugin_id: str = "acme.dashboard", version: str = "1.0.0", **over: Any
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "manifestVersion": 1,
        "id": plugin_id,
        "version": version,
        "name": {"en-US": "Acme Dashboard", "zh-CN": "Acme 看板"},
        "description": {"en-US": "Acme data on project pages"},
        "publisher": {"name": "Acme", "url": "https://acme.example"},
        "engines": {"valuz-plugin-api": "^1.0.0"},
        "frontend": {"entry": "frontend/index.js", "styles": ["frontend/index.css"]},
        "permissions": ["projects:read", "storage"],
    }
    manifest.update(over)
    return manifest


def build_plugin(
    root: Path,
    plugin_id: str = "acme.dashboard",
    version: str = "1.0.0",
    *,
    files: dict[str, str] | None = None,
    **over: Any,
) -> Path:
    """A valid plugin source directory at ``root``."""
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    (root / "locales").mkdir(exist_ok=True)
    (root / "frontend" / "index.js").write_text(INDEX_JS, encoding="utf-8")
    (root / "frontend" / "index.css").write_text(".acme { color: red }\n", encoding="utf-8")
    (root / "locales" / "en-US.json").write_text(json.dumps({"title": "Dash"}), encoding="utf-8")
    (root / "locales" / "zh-CN.json").write_text(json.dumps({"title": "看板"}), encoding="utf-8")
    (root / "README.md").write_text("# Acme\n", encoding="utf-8")
    manifest = manifest_of(plugin_id, version, **over)
    (root / "valuz-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    for rel, content in (files or {}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def zip_dir(src: Path, out: Path, *, wrapper: str | None = None) -> Path:
    """A plain zip of a plugin directory (not the deterministic ``pack``)."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                rel = path.relative_to(src).as_posix()
                archive.write(path, f"{wrapper}/{rel}" if wrapper else rel)
    return out
