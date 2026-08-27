"""Port: the builtin resource declaration set.

What counts as "builtin" — the skills / connectors / agent templates /
plugins a distribution ships out of the box — is a *declaration*, separate
from where the assets live (docs/design/builtin-resources in the commercial
repo). OSS resolves the declaration from the packaged manifest files only
(``resources/builtin_manifest.json`` plus any edition manifests registered at
startup); the commercial overlay replaces the port with a cloud-backed
resolver that falls back to the same packaged set when the cloud is
unreachable.

The packaged set's ``generation`` is always ``0`` — it never counts as
"newer" than a cloud declaration, so an offline boot can never demote
anything (fallback reads are strictly read-only).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)

BUILTIN_KINDS = ("skill", "connector", "agent_template", "agent_team_template", "plugin")


@dataclass(frozen=True)
class BuiltinDeclaration:
    """One declared builtin item.

    ``asset`` is the packaged-manifest pointer (repo-relative, e.g.
    ``builtin_skills/citation``); cloud declarations carry ``install`` /
    ``connector_config`` / ``manifest`` payloads instead. ``min_version`` is
    the single asset gate: a packaged asset at or above it is used as-is
    (design §5.2).
    """

    kind: str
    slug: str
    version: str = "0"
    min_version: str = "0"
    provisioning: str = "provisioned"  # "provisioned" | "available"
    auto_authorize: bool = False
    onboarding_default: bool = False
    runtime_scope: str = "any"  # "local_only" | "any"
    display_order: int = 0
    asset: str | None = None
    install: dict[str, Any] | None = None
    connector_config: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None


@dataclass(frozen=True)
class BuiltinDeclarationSet:
    """The resolved declaration set one provisioning pass works from."""

    generation: int
    source: str  # "packaged" | "cloud"
    items: tuple[BuiltinDeclaration, ...] = field(default_factory=tuple)

    def by_kind(self, kind: str) -> tuple[BuiltinDeclaration, ...]:
        return tuple(i for i in self.items if i.kind == kind)

    def get(self, kind: str, slug: str) -> BuiltinDeclaration | None:
        for item in self.items:
            if item.kind == kind and item.slug == slug:
                return item
        return None

    def slugs(self, kind: str) -> tuple[str, ...]:
        return tuple(i.slug for i in self.by_kind(kind))


class BuiltinResourceDeclarationPort(Protocol):
    """Resolve the current builtin declaration set for this install."""

    async def declarations(self) -> BuiltinDeclarationSet: ...


# ─── Packaged manifests ──────────────────────────────────────────────────

_OSS_MANIFEST = (
    Path(__file__).resolve().parent.parent / "resources" / "builtin_manifest.json"
)

# Edition manifests registered by overlays at startup; merged over the OSS
# manifest by (kind, slug) — the edition entry wins (more specific).
_extra_manifests: list[Path] = []


def register_builtin_manifest(path: Path) -> None:
    """Register an edition's packaged builtin manifest (idempotent)."""
    resolved = Path(path).resolve()
    if resolved not in _extra_manifests:
        _extra_manifests.append(resolved)


def registered_builtin_manifests() -> tuple[Path, ...]:
    return tuple(_extra_manifests)


def clear_registered_builtin_manifests() -> None:
    """Test hook — the registry is process-global."""
    _extra_manifests.clear()


def _parse_manifest(path: Path) -> list[BuiltinDeclaration]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError(f"unsupported builtin manifest schema_version in {path}")
    items: list[BuiltinDeclaration] = []
    for entry in raw.get("items", []):
        kind = entry.get("type")
        slug = entry.get("slug")
        if kind not in BUILTIN_KINDS or not slug:
            raise ValueError(f"malformed builtin manifest entry in {path}: {entry!r}")
        items.append(
            BuiltinDeclaration(
                kind=kind,
                slug=str(slug),
                version=str(entry.get("version", "0")),
                min_version=str(entry.get("min_version", entry.get("version", "0"))),
                provisioning=str(entry.get("provisioning", "provisioned")),
                auto_authorize=bool(entry.get("auto_authorize", False)),
                onboarding_default=bool(entry.get("onboarding_default", False)),
                runtime_scope=str(entry.get("runtime_scope", "any")),
                display_order=int(entry.get("display_order", 0)),
                asset=entry.get("asset"),
                install=entry.get("install"),
                connector_config=entry.get("connector_config"),
                manifest=entry.get("manifest"),
            )
        )
    return items


def load_packaged_declarations() -> BuiltinDeclarationSet:
    """The merged packaged declaration set (OSS ∪ registered editions).

    Merge is by ``(kind, slug)``; a later (edition) entry replaces the OSS
    one. A duplicate within one manifest is a packaging error and raises —
    the CI check mirrors this rule.
    """
    merged: dict[tuple[str, str], BuiltinDeclaration] = {}
    for path in (_OSS_MANIFEST, *_extra_manifests):
        if not path.is_file():
            if path is _OSS_MANIFEST:
                logger.warning("packaged builtin manifest missing: %s", path)
            continue
        seen: set[tuple[str, str]] = set()
        for item in _parse_manifest(path):
            key = (item.kind, item.slug)
            if key in seen:
                raise ValueError(
                    f"duplicate builtin manifest entry {key[0]}/{key[1]} in {path}"
                )
            seen.add(key)
            merged[key] = item
    return BuiltinDeclarationSet(
        generation=0, source="packaged", items=tuple(merged.values())
    )


class PackagedBuiltinDeclarations:
    """OSS default implementation — reads only the packaged manifests."""

    async def declarations(self) -> BuiltinDeclarationSet:
        return load_packaged_declarations()


def get_builtin_declarations_port() -> BuiltinResourceDeclarationPort:
    from valuz_agent.ports.extensions import ext

    return ext.builtin_declarations


def set_builtin_declarations_port(port: BuiltinResourceDeclarationPort) -> None:
    """Replace the declaration resolver (called by the commercial overlay)."""
    from valuz_agent.ports.extensions import ext

    ext.builtin_declarations = port


__all__ = [
    "BUILTIN_KINDS",
    "BuiltinDeclaration",
    "BuiltinDeclarationSet",
    "BuiltinResourceDeclarationPort",
    "PackagedBuiltinDeclarations",
    "clear_registered_builtin_manifests",
    "get_builtin_declarations_port",
    "load_packaged_declarations",
    "register_builtin_manifest",
    "registered_builtin_manifests",
    "set_builtin_declarations_port",
]
