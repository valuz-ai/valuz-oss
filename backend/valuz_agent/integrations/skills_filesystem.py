from __future__ import annotations

import ast
import hashlib
import logging
import os
from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.skills.contracts import RuntimeContext, SkillManifest

logger = logging.getLogger(__name__)


def _folder_birthtime(path: Path) -> int | None:
    """Best-effort cross-platform "when was this folder created" read.

    Resolution order:
      1. ``st_birthtime`` — macOS/BSD always; Linux on ext4/btrfs with
         kernel ≥ 4.11 + glibc that wires ``statx`` into ``os.stat``;
         Windows native creation time.
      2. ``st_mtime`` fallback — when birthtime isn't reported (older
         kernels, NFS / SMB shares, tmpfs). Slightly degraded semantics
         (it's the last-modified time of the dir entry, but in practice
         skill folders are written-once so the values almost always
         coincide).
      3. ``None`` on OSError (stat() failure / disappeared race).

    Returns Unix epoch milliseconds (UTC) as an ``int`` — the host-wide
    instant representation — which SQLAlchemy stores as a plain BIGINT and
    the frontend parses with ``new Date(ms)``.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    raw = getattr(st, "st_birthtime", None)
    if raw is None or raw <= 0:
        raw = st.st_mtime
    try:
        return int(raw * 1000)
    except (OverflowError, OSError, ValueError):
        return None


def _default_user_skill_root() -> Path:
    """Canonical write-target for promoted user skills.

    Delegates to ``FsRegistry.user_skill_root()`` so the destination obeys
    ADR-004 (host writes go to ``~/.valuz/app/`` by default).
    """
    return fs_registry.user_skill_root()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compute_dir_hash(skill_dir: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(skill_dir.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(skill_dir)).encode())
            h.update(f.read_bytes())
    return h.hexdigest()


def _extract_frontmatter(raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---\n"):
        return {}, raw

    closing = raw.find("\n---\n", 4)
    if closing < 0:
        return {}, raw

    meta_block = raw[4:closing]
    body = raw[closing + 5 :]
    metadata: dict[str, object] = {}
    for line in meta_block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        raw_value = value.strip()
        if not raw_value:
            metadata[key] = ""
            continue
        if raw_value.startswith("[") and raw_value.endswith("]"):
            try:
                parsed = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError):
                parsed = []
            metadata[key] = parsed if isinstance(parsed, list) else []
            continue
        metadata[key] = raw_value.strip("\"'")
    return metadata, body


def _detect_manifest(skill_dir: Path) -> Path | None:
    for name in ("SKILL.md", "skill.md"):
        manifest = skill_dir / name
        if manifest.exists():
            return manifest
    return None


def _coerce_version(raw: object) -> int | None:
    """Best-effort coerce a frontmatter `version:` value to a positive int.

    Accepts ints and numeric-string forms. Returns None for missing, empty,
    or unparseable values so the field gracefully degrades on legacy skills.
    """
    if raw is None:
        return None
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _discover_roots(ctx: RuntimeContext) -> list[tuple[str, Path, str]]:
    """Enumerate every directory we should scan for user/project skills.

    The canonical user write-target is ``FsRegistry.user_skill_root()`` —
    ``~/.agents/skills/`` (the agentskills.io standard location).
    Legacy ``~/.claude/skills`` and ``~/.codex/skills`` are still
    surfaced as read-only sources so skills authored in those CLIs
    don't disappear. Source labels stay distinct so the UI can tell
    the user where a skill came from.
    """
    roots: list[tuple[str, Path, str]] = []
    seen: set[Path] = set()

    has_override = bool(os.environ.get("VALUZ_USER_SKILLS_DIR"))

    valuz_root = _default_user_skill_root()
    if valuz_root.exists():
        roots.append(("user", valuz_root, "valuz"))
        seen.add(valuz_root)

    if not has_override:
        # The leaf-and-parent shape check below maps each legacy root to
        # the right source label, which drives the .claude / .codex
        # top-level group on the skill management page.
        for legacy in fs_registry.legacy_user_skill_roots():
            if legacy in seen:
                continue
            if legacy.name == "skills" and legacy.parent.name == ".claude":
                label = "claude"
            elif legacy.name == "skills" and legacy.parent.name == ".codex":
                label = "codex"
            else:
                # Defensive: if ``legacy_user_skill_roots`` ever adds a
                # new path, fall through to the Valuz bucket rather than
                # silently mislabel it.
                label = "valuz"
            roots.append(("user", legacy, label))
            seen.add(legacy)

    if not roots:
        roots.append(("user", valuz_root, "valuz"))

    project = ctx.project
    if project and project.kind == "project" and project.root_path:
        project_root = Path(project.root_path)
        roots.append(("project", project_root / ".claude" / "skills", "project"))

    return roots


class FilesystemSkillSource:
    name = "filesystem"

    def list_skills(self, ctx: RuntimeContext) -> list[SkillManifest]:
        manifests: list[SkillManifest] = []
        for scope, root, source_label in _discover_roots(ctx):
            if not root.exists():
                continue
            for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
                manifest_path = _detect_manifest(skill_dir)
                if manifest_path is None:
                    continue

                raw_manifest = _read_text(manifest_path)
                metadata, body = _extract_frontmatter(raw_manifest)
                title = str(metadata.get("name") or skill_dir.name)
                summary = str(metadata.get("description") or self._summary_from_body(body))
                tags = metadata.get("tags")
                icon = metadata.get("icon")
                argument_hint = metadata.get("argument-hint") or metadata.get("argument_hint")
                context = metadata.get("context")
                origin_label = metadata.get("origin-label") or metadata.get("origin_label")
                version = _coerce_version(metadata.get("version"))
                folder_created_at = _folder_birthtime(skill_dir)
                manifest_hash = hashlib.sha256(raw_manifest.encode()).hexdigest()
                content_hash = _compute_dir_hash(skill_dir)
                manifests.append(
                    SkillManifest(
                        id=f"{scope}:{skill_dir.name}",
                        name=title,
                        description=summary,
                        scope=scope,
                        source=source_label,
                        path=str(skill_dir.resolve(strict=False)),
                        slug=skill_dir.name,
                        icon=str(icon) if icon else None,
                        argument_hint=str(argument_hint) if argument_hint else None,
                        context=str(context) if context else None,
                        origin_label=str(origin_label) if origin_label else None,
                        tags=[str(tag) for tag in tags] if isinstance(tags, list) else [],
                        content_hash=content_hash,
                        manifest_hash=manifest_hash,
                        version=version,
                        folder_created_at=folder_created_at,
                    )
                )
        return manifests

    def materialize(self, skill_id: str, target_dir: Path) -> Path:
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / f"{skill_id}.md"

    @staticmethod
    def _summary_from_body(body: str) -> str:
        for line in body.splitlines():
            candidate = line.strip()
            if candidate and not candidate.startswith("#"):
                return candidate[:180]
        return "Skill discovered from the local filesystem."
