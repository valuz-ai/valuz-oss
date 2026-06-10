"""Per-session staging directory for skill-creator (Scenario B + D3 accept path).

The skill-creator skill is instructed (via system-prompt convention injected
by the runtime) to write all draft skills under a session-scoped scratch dir:

    <staging-root>/<session_id>/<slug>/
        SKILL.md
        scripts/...
        references/...
        assets/...
        .staging-meta.json   # (optional) edit/optimize provenance

This module owns:
  - resolution of the staging root + session dir
  - scanning the session dir to enumerate slugs and detect conflicts against
    the user's skill library
  - syncing slug directories into the user skill library with per-slug strategy
    (overwrite | fork | abort) and automatic -vN slug suggestion for forks
  - bootstrapping a skill into staging when the user wants to optimize an
    existing one (cp -r + write .staging-meta.json)

Nothing in here mutates rows in skill_index — actual skill registration goes
through SkillLibraryService.create_skill via filesystem scan triggered after
the directory is in place.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from valuz_agent.infra.config import settings
from valuz_agent.integrations.skills_filesystem import (
    _default_user_skill_root,
    _detect_manifest,
    _extract_frontmatter,
    _read_text,
)

STAGING_META_FILENAME = ".staging-meta.json"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
VERSION_SUFFIX_RE = re.compile(r"^(?P<base>.+?)(?:-v(?P<n>\d+))?$")


# ── Data shapes ───────────────────────────────────────────────────────


ConflictKind = Literal["none", "same_source", "diverged"]
SyncStrategy = Literal["overwrite", "fork", "abort"]


@dataclass
class StagingFileNode:
    path: str
    type: Literal["file", "directory"]
    size: int | None = None


@dataclass
class StagingSlugView:
    slug: str
    name: str
    description: str
    file_count: int
    total_bytes: int
    files: list[StagingFileNode]
    conflict_kind: ConflictKind
    suggested_strategy: SyncStrategy
    suggested_new_slug: str | None = None
    source_skill_id: str | None = None  # if this slug originated from "optimize"
    version: int | None = None  # parsed from SKILL.md frontmatter `version:`


@dataclass
class StagingScanResult:
    session_id: str
    staging_path: str
    slugs: list[StagingSlugView]


@dataclass
class SyncItemResult:
    slug: str
    strategy: SyncStrategy
    written_path: str | None
    new_slug: str | None
    skipped: bool


@dataclass
class StagingMeta:
    source_skill_id: str | None = None
    source_path: str | None = None
    source_content_hash: str | None = None
    intent: Literal["create", "optimize"] = "create"
    created_at: str | None = None


# ── Path resolution ───────────────────────────────────────────────────
#
# Staging is rooted **inside the project cwd** under
# ``.skill-staging/``. The agent writes there using a relative path it
# computes from ``$PWD`` — no session_id appears in the path. This works
# uniformly for sessions launched via ``/v1/skills/create/start`` and for
# organic chat sessions that incidentally trigger the skill-creator skill
# (which previously fell through the cracks of the per-session prompt
# injection scheme).
#
# ``staging_dir_for_session(session_id)`` is preserved as the public
# entry point so the existing scan / sync / optimize / cleanup flows
# don't have to change at every call site — internally it now resolves
# the project cwd from the session row and points at
# ``{project_cwd}/.skill-staging/``. When the kernel session can't be
# loaded (e.g. the legacy staging endpoint is called for a session that
# was already cleaned up), we fall back to the pre-refactor
# ``{settings.skill_staging_dir}/{session_id}/`` path so legacy content
# stays inspectable.


def staging_root() -> Path:
    """Legacy host-managed staging root.

    Pre-refactor sessions wrote here as ``{root}/{session_id}/{slug}/``.
    Kept readable so old content surfaces in scans during the transition;
    new staged content always lives under the project cwd instead.
    """
    return settings.skill_staging_dir.expanduser()


async def _resolve_project_cwd_for_session(session_id: str) -> Path | None:
    """Look up the project cwd a session is running in.

    Returns ``None`` when the kernel session row is missing — the caller
    decides whether to fall back to the legacy session-keyed staging
    path or to error out.
    """
    from valuz_agent.adapters import kernel_client
    from valuz_agent.infra.fs_registry import fs_registry

    try:
        session = await kernel_client.get_session(session_id)
    except Exception:  # noqa: BLE001 — kernel store transient failures are non-fatal here
        return None
    if session is None:
        return None

    project_id = str(((session.metadata or {}).get("valuz", {}) or {}).get("project_id") or "")
    project_kind = "chat"
    project_root_path: str | None = None
    try:
        from valuz_agent.modules.projects.datastore import ProjectDatastore

        async def _read_ws():  # type: ignore[no-untyped-def]
            from valuz_agent.infra.db import async_unit_of_work

            async with async_unit_of_work(commit=False) as db:
                return await ProjectDatastore(db).get_by_id(str(project_id))

        row = await _read_ws()
        if row is not None:
            project_kind = row.kind if row.kind in ("chat", "project") else "chat"
            project_root_path = row.root_path
    except Exception:  # noqa: BLE001 — project lookup failure → fall through to chat default
        pass

    try:
        return fs_registry.project_cwd(
            str(project_id),
            project_kind,  # type: ignore[arg-type]
            project_root_path,
        )
    except (ValueError, OSError):
        return None


async def staging_dir_for_session(session_id: str, *, mkdir: bool = False) -> Path:
    """Return the staging directory for a session.

    The returned path is ``{project_cwd}/.skill-staging/`` for the
    project the session lives in. Multiple sessions in the same
    project share this directory — slug uniqueness is the
    differentiator, enforced by ``submit_skill``'s validator and the
    confirm-time conflict check.

    Falls back to the legacy ``{staging_root}/{session_id}/`` path when
    the kernel session can't be resolved, so already-staged content from
    before this refactor stays reachable.
    """
    if not session_id or "/" in session_id or session_id.startswith("."):
        raise ValueError(f"invalid session_id: {session_id!r}")

    from valuz_agent.infra.fs_registry import fs_registry

    project_cwd = await _resolve_project_cwd_for_session(session_id)
    if project_cwd is None:
        # Legacy fallback — keeps in-flight staging content discoverable.
        path = staging_root() / session_id
    else:
        path = fs_registry.skill_staging_root_for_project(project_cwd)
    if mkdir:
        path.mkdir(parents=True, exist_ok=True)
    return path


# ── Hashing & meta ────────────────────────────────────────────────────


def hash_skill_directory(skill_dir: Path) -> str:
    """Stable hash of all files under a skill directory, excluding the
    staging meta marker. Used to detect 'diverged' conflicts."""
    h = hashlib.sha256()
    files = sorted(
        p for p in skill_dir.rglob("*") if p.is_file() and p.name != STAGING_META_FILENAME
    )
    for path in files:
        rel = path.relative_to(skill_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def read_staging_meta(slug_dir: Path) -> StagingMeta | None:
    meta_path = slug_dir / STAGING_META_FILENAME
    if not meta_path.is_file():
        return None
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return StagingMeta(
        source_skill_id=raw.get("source_skill_id"),
        source_path=raw.get("source_path"),
        source_content_hash=raw.get("source_content_hash"),
        intent=raw.get("intent") or "create",
        created_at=raw.get("created_at"),
    )


def write_staging_meta(slug_dir: Path, meta: StagingMeta) -> None:
    payload = {
        "source_skill_id": meta.source_skill_id,
        "source_path": meta.source_path,
        "source_content_hash": meta.source_content_hash,
        "intent": meta.intent,
        "created_at": meta.created_at or datetime.now(UTC).isoformat(),
    }
    (slug_dir / STAGING_META_FILENAME).write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )


# ── Scan ──────────────────────────────────────────────────────────────


def _list_files(slug_dir: Path) -> tuple[list[StagingFileNode], int, int]:
    files: list[StagingFileNode] = []
    total = 0
    for path in sorted(slug_dir.rglob("*")):
        if path.name == STAGING_META_FILENAME:
            continue
        rel = path.relative_to(slug_dir).as_posix()
        if path.is_dir():
            files.append(StagingFileNode(path=rel, type="directory"))
        elif path.is_file():
            size = path.stat().st_size
            total += size
            files.append(StagingFileNode(path=rel, type="file", size=size))
    file_count = sum(1 for n in files if n.type == "file")
    return files, file_count, total


def _next_versioned_slug(target_root: Path, base_slug: str) -> str:
    """Find an unused -vN suffix above the highest existing version."""
    match = VERSION_SUFFIX_RE.match(base_slug)
    base = match.group("base") if match else base_slug
    highest = 1
    for entry in target_root.iterdir() if target_root.exists() else []:
        if not entry.is_dir():
            continue
        m = VERSION_SUFFIX_RE.match(entry.name)
        if not m or m.group("base") != base:
            continue
        n = int(m.group("n")) if m.group("n") else 1
        highest = max(highest, n)
    return f"{base}-v{highest + 1}"


def _read_manifest_meta(slug_dir: Path) -> tuple[str, str, int | None]:
    """(name, description, version) extracted from SKILL.md frontmatter."""
    from valuz_agent.integrations.skills_filesystem import _coerce_version

    manifest_path = _detect_manifest(slug_dir)
    if manifest_path is None:
        return slug_dir.name, "", None
    raw = _read_text(manifest_path)
    metadata, _ = _extract_frontmatter(raw)
    name = str(metadata.get("name") or slug_dir.name)
    description = str(metadata.get("description") or "")
    version = _coerce_version(metadata.get("version"))
    return name, description, version


async def scan_staging(session_id: str) -> StagingScanResult:
    """Enumerate slugs in this session's staging dir.

    For each slug we compute the conflict kind against the user skill library:
      - none: nothing at the same slug in the user library
      - same_source: target exists and matches the staging meta's recorded
        source_content_hash → the user is editing this skill in place
      - diverged: target exists but differs from what we forked from →
        user modified the original elsewhere; suggest fork-as-vN
    """
    session_dir = await staging_dir_for_session(session_id)
    user_skill_root = _default_user_skill_root()

    if not session_dir.exists():
        return StagingScanResult(session_id=session_id, staging_path=str(session_dir), slugs=[])

    slugs: list[StagingSlugView] = []
    for entry in sorted(session_dir.iterdir()):
        if not entry.is_dir():
            continue
        if not SLUG_RE.match(entry.name):
            continue  # ignore stray dirs that don't look like slugs
        if _detect_manifest(entry) is None:
            continue  # no SKILL.md → not a real skill yet, skip silently

        files, file_count, total_bytes = _list_files(entry)
        name, description, version = _read_manifest_meta(entry)
        meta = read_staging_meta(entry)

        target = user_skill_root / entry.name
        if not target.exists():
            kind: ConflictKind = "none"
            suggested: SyncStrategy = "overwrite"
            new_slug: str | None = None
        else:
            target_hash = hash_skill_directory(target)
            if meta and meta.source_content_hash and meta.source_content_hash == target_hash:
                kind = "same_source"
                suggested = "overwrite"
                new_slug = None
            else:
                kind = "diverged"
                suggested = "fork"
                new_slug = _next_versioned_slug(user_skill_root, entry.name)

        slugs.append(
            StagingSlugView(
                slug=entry.name,
                name=name,
                description=description,
                file_count=file_count,
                total_bytes=total_bytes,
                files=files,
                conflict_kind=kind,
                suggested_strategy=suggested,
                suggested_new_slug=new_slug,
                source_skill_id=meta.source_skill_id if meta else None,
                version=version,
            )
        )

    return StagingScanResult(session_id=session_id, staging_path=str(session_dir), slugs=slugs)


# ── Sync ──────────────────────────────────────────────────────────────


def _copy_clean(src: Path, dest: Path) -> None:
    """Copy src to dest, replacing dest if it exists. Skip the meta marker."""
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(STAGING_META_FILENAME))


async def sync_slug(
    session_id: str,
    slug: str,
    strategy: SyncStrategy,
    *,
    new_slug: str | None = None,
    target_root: Path | None = None,
) -> SyncItemResult:
    """Copy one staging slug into the user skill library per the given strategy.

    target_root defaults to _default_user_skill_root(); pass an alternative
    when the caller routes by target_scope (e.g. project skills root).
    """
    if strategy == "abort":
        return SyncItemResult(
            slug=slug, strategy=strategy, written_path=None, new_slug=None, skipped=True
        )

    src = await staging_dir_for_session(session_id) / slug
    if not src.is_dir() or _detect_manifest(src) is None:
        raise FileNotFoundError(f"no staging slug {slug!r} for session {session_id!r}")

    root = (target_root or _default_user_skill_root()).expanduser()
    root.mkdir(parents=True, exist_ok=True)

    if strategy == "fork":
        chosen = (new_slug or _next_versioned_slug(root, slug)).strip()
        if not SLUG_RE.match(chosen):
            raise ValueError(f"invalid fork slug: {chosen!r}")
        dest = root / chosen
        if dest.exists():
            raise FileExistsError(f"fork target already exists: {dest}")
        _copy_clean(src, dest)
        # Bump the version field in SKILL.md frontmatter so downstream
        # consumers (catalog, scan) can show the new version cleanly.
        _bump_skill_md_version(dest, chosen)
        return SyncItemResult(
            slug=slug, strategy=strategy, written_path=str(dest), new_slug=chosen, skipped=False
        )

    # strategy == "overwrite"
    dest = root / slug
    _copy_clean(src, dest)
    return SyncItemResult(
        slug=slug, strategy=strategy, written_path=str(dest), new_slug=None, skipped=False
    )


# ── Optimize: prepare staging from an existing skill ─────────────────


async def prepare_optimize(session_id: str, source_skill_dir: Path, source_skill_id: str) -> Path:
    """Copy an existing skill into staging so the agent can edit it in place.

    Writes a .staging-meta.json so a later sync can detect 'same_source' vs
    'diverged' against whatever lives at the original path.
    """
    if not source_skill_dir.is_dir():
        raise FileNotFoundError(f"source skill not found: {source_skill_dir}")
    if _detect_manifest(source_skill_dir) is None:
        raise ValueError(f"source dir is not a valid skill (no SKILL.md): {source_skill_dir}")

    session_dir = await staging_dir_for_session(session_id, mkdir=True)
    slug = source_skill_dir.name
    if not SLUG_RE.match(slug):
        raise ValueError(f"source skill slug is not a valid identifier: {slug!r}")
    dest = session_dir / slug
    _copy_clean(source_skill_dir, dest)

    write_staging_meta(
        dest,
        StagingMeta(
            source_skill_id=source_skill_id,
            source_path=str(source_skill_dir.resolve()),
            source_content_hash=hash_skill_directory(source_skill_dir),
            intent="optimize",
            created_at=datetime.now(UTC).isoformat(),
        ),
    )
    return dest


# ── Cleanup ───────────────────────────────────────────────────────────


async def remove_slug(session_id: str, slug: str) -> None:
    """Best-effort delete of a single slug under the session's staging dir."""
    target = await staging_dir_for_session(session_id) / slug
    if target.is_dir():
        shutil.rmtree(target)


async def remove_session_staging(session_id: str) -> None:
    target = await staging_dir_for_session(session_id)
    if target.is_dir():
        shutil.rmtree(target)


def _version_from_chosen_slug(chosen: str) -> int:
    """Pull the integer N out of a `<base>-vN` slug; default to 2."""
    match = VERSION_SUFFIX_RE.match(chosen)
    if match and match.group("n"):
        try:
            return int(match.group("n"))
        except (TypeError, ValueError):
            return 2
    return 2


_FRONTMATTER_VERSION_RE = __import__("re").compile(
    r"^(?P<key>version)\s*:\s*.*$", flags=__import__("re").MULTILINE
)


def _bump_skill_md_version(slug_dir: Path, chosen_slug: str) -> None:
    """Rewrite SKILL.md so its frontmatter `version:` matches the fork.

    If the frontmatter already has a version line, replace it. If not,
    inject one after the opening `---`. Failures are swallowed — the file
    on disk is the source of truth either way.
    """
    manifest = _detect_manifest(slug_dir)
    if manifest is None:
        return
    try:
        raw = manifest.read_text(encoding="utf-8")
    except OSError:
        return

    target = _version_from_chosen_slug(chosen_slug)
    new_line = f"version: {target}"

    if _FRONTMATTER_VERSION_RE.search(raw):
        rewritten = _FRONTMATTER_VERSION_RE.sub(new_line, raw, count=1)
    elif raw.startswith("---\n"):
        # Insert just after the opening fence so the field sits with the rest
        # of the metadata.
        rewritten = "---\n" + new_line + "\n" + raw[len("---\n") :]
    else:
        # No frontmatter at all — wrap the file in one.
        rewritten = f"---\n{new_line}\n---\n\n{raw}"

    try:
        manifest.write_text(rewritten, encoding="utf-8")
    except OSError:
        pass
