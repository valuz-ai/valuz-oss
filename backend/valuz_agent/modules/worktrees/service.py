"""Project worktree service — policy over the ``infra/git_worktree`` primitives.

No table: git itself is the source of truth (``git worktree list``), a sidecar
``<slug>.meta.json`` next to each worktree carries the few facts git doesn't
hold (origin, base_sha anchor, created_at), and session attribution rides the
immutable ``sessions.metadata["valuz"]["worktree"]`` snapshot written at
session creation. See docs/design/project-worktree-design.md §6.

Concurrency: ``git worktree add``/``remove`` on the same repo contend on git's
own lock files, so all mutations serialize through a per-git-root asyncio
lock. Every git call runs off the event loop via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from valuz_agent.infra import git_worktree as gw
from valuz_agent.modules.worktrees.errors import (
    InvalidWorktreeName,
    WorktreeDirty,
    WorktreeNotAvailable,
    WorktreeNotFound,
    WorktreeOperationFailed,
)
from valuz_agent.modules.worktrees.slug_words import SLUG_ADJECTIVES, SLUG_NOUNS

logger = logging.getLogger(__name__)

_git_root_locks: dict[str, asyncio.Lock] = {}


# Friendly auto-names (design D11): ``fervent-bohr-14379d``-style
# adjective-surname pairs, using Docker's names-generator vocabulary
# (see slug_words.py; ~97 adjectives × 237 surnames ≈ 23k combos) — readable
# in the panel, the branch name (``valuz/u-fervent-bohr-14379d``), and chat.
# Task worktrees deliberately do NOT use this — they stay the deterministic
# ``task-<task_id>`` so each task addresses its own worktree and the stale
# sweeper can key on the ``valuz/task-`` branch prefix.
def _generate_slug(git_root: Path) -> str:
    """A fresh friendly slug for an unnamed worktree.

    The existence check matters: ``get_or_create`` fast-resumes an existing
    path, so an unlucky collision would silently drop the new session into
    someone else's worktree. Retry a few times, then fall back to plain hex.
    """
    for _ in range(8):
        # 3 bytes → 6 hex chars, matching the harness's own worktree names
        # (fervent-bohr-14379d) that this scheme is borrowed from.
        slug = (
            f"{secrets.choice(SLUG_ADJECTIVES)}-{secrets.choice(SLUG_NOUNS)}-{secrets.token_hex(3)}"
        )
        if not gw.worktree_path(git_root, slug).exists():
            return slug
    return f"wt-{secrets.token_hex(4)}"


def _lock_for(git_root: Path) -> asyncio.Lock:
    key = str(git_root)
    lock = _git_root_locks.get(key)
    if lock is None:
        lock = _git_root_locks.setdefault(key, asyncio.Lock())
    return lock


class ProjectRowLike(Protocol):
    """The three project fields this service needs — satisfied by both the
    ORM ``ProjectRow`` and the API-layer ``ProjectDetail`` dataclass."""

    @property
    def id(self) -> str: ...
    @property
    def kind(self) -> str: ...
    @property
    def root_path(self) -> str | None: ...


@dataclass(frozen=True)
class ProjectGitInfo:
    """Computed-on-read git facts for feature gating (never persisted)."""

    git_available: bool
    is_repo: bool
    git_root: str | None = None
    # Relative path of the project cwd inside the repo when the bound
    # root_path is a subdirectory of it ("" when they coincide).
    subdir: str | None = None


@dataclass(frozen=True)
class WorktreeHandle:
    """A live worktree bound to a session at creation time."""

    name: str
    path: str
    branch: str
    git_root: str
    # Change-detection anchor; ``None`` when resuming a worktree whose
    # sidecar was lost — cleanup then fail-closes to "keep".
    base_sha: str | None
    # The cwd the kernel session should run in: the worktree path plus the
    # project's subdir inside the repo (design D7).
    session_cwd: str
    created: bool
    submodules_ok: bool


@dataclass(frozen=True)
class WorktreeItem:
    """One row for the worktrees panel — git state computed on read."""

    name: str
    branch: str | None
    path: str
    origin: str
    base_sha: str | None
    created_at: int | None  # Unix epoch ms
    # ``None`` = could not verify (missing anchor or git failure); the UI
    # renders that as "unknown", and discard requires force.
    dirty_files: int | None
    ahead_commits: int | None


def _sidecar_path(git_root: Path, flat: str) -> Path:
    return gw.worktrees_dir(git_root) / f"{flat}.meta.json"


def _read_sidecar(git_root: Path, flat: str) -> dict[str, object] | None:
    try:
        raw = _sidecar_path(git_root, flat).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_sidecar(git_root: Path, flat: str, payload: dict[str, object]) -> None:
    try:
        path = _sidecar_path(git_root, flat)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        # Losing the sidecar only degrades auto-cleanup to fail-closed "keep".
        logger.warning("worktrees: could not write sidecar for %s: %s", flat, exc)


def _remove_sidecar(git_root: Path, flat: str) -> None:
    try:
        _sidecar_path(git_root, flat).unlink(missing_ok=True)
    except OSError:
        pass


async def _scope_artifact_count(user_id: str, project_id: str, worktree: str) -> int:
    """How many live deliverables a worktree holds. ``0`` if it cannot be told.

    Best-effort by design: this gates *automatic* teardown, and a lookup failure
    must not wedge cleanup forever. Failing open risks removing a worktree whose
    deliverables the DB could not be read for; failing closed would leak
    worktrees on every transient DB error. The explicit ``discard`` path does
    not consult this at all — there the user has already decided.
    """
    if not project_id:
        return 0
    try:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope

        async with async_unit_of_work(commit=False) as db:
            return await ArtifactDatastore(db).count_scope_artifacts(
                Scope(user_id=user_id, project_id=project_id, worktree=worktree)
            )
    except Exception:  # noqa: BLE001 — never block teardown on a lookup
        logger.warning("worktrees: artifact lookup failed for '%s'", worktree, exc_info=True)
        return 0


async def _archive_scope_artifacts(user_id: str, project_id: str, worktree: str) -> int:
    """Retire the worktree's deliverables — their snapshots died with it.

    Rows are kept and marked, never deleted: a link the user still holds should
    explain itself rather than 404.
    """
    if not project_id:
        return 0
    try:
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.modules.artifacts.datastore import ArtifactDatastore, Scope

        async with async_unit_of_work() as db:
            return await ArtifactDatastore(db).archive_scope(
                Scope(user_id=user_id, project_id=project_id, worktree=worktree)
            )
    except Exception:  # noqa: BLE001 — the worktree is already gone; do not raise
        logger.warning("worktrees: could not archive artifacts for '%s'", worktree, exc_info=True)
        return 0


class WorktreeService:
    """Stateless orchestration — safe to construct per call site."""

    # ---- feature gating ------------------------------------------------

    async def project_git(self, user_id: str, project_row: ProjectRowLike) -> ProjectGitInfo:
        """Git facts for the project's resolved cwd (route-level gate)."""
        cwd = self._resolve_project_cwd(user_id, project_row)
        available = await asyncio.to_thread(gw.git_available)
        if not available:
            return ProjectGitInfo(git_available=False, is_repo=False)
        info = await asyncio.to_thread(gw.detect_git, cwd)
        if info is None:
            return ProjectGitInfo(git_available=True, is_repo=False)
        try:
            subdir = str(Path(cwd).resolve().relative_to(info.git_root))
        except ValueError:
            subdir = ""
        return ProjectGitInfo(
            git_available=True,
            is_repo=True,
            git_root=str(info.git_root),
            subdir="" if subdir == "." else subdir,
        )

    # ---- lifecycle -----------------------------------------------------

    async def get_or_create(
        self,
        user_id: str,
        project_row: ProjectRowLike,
        name: str | None = None,
        origin: str = "u",
    ) -> WorktreeHandle:
        """Create (or fast-resume) a worktree for the project.

        Raises ``WorktreeNotAvailable`` when git is missing or the project
        isn't a repo — worktree semantics are "an isolated, mergeable copy",
        so there is deliberately no mkdir fallback (design D2).
        """
        cwd = self._resolve_project_cwd(user_id, project_row)
        if not await asyncio.to_thread(gw.git_available):
            raise WorktreeNotAvailable("git is not installed or not usable on this machine")
        info = await asyncio.to_thread(gw.detect_git, cwd)
        if info is None:
            raise WorktreeNotAvailable(f"project directory {cwd} is not inside a git repository")

        if name:
            slug = name
        else:
            slug = await asyncio.to_thread(_generate_slug, info.git_root)
        try:
            gw.validate_slug(slug)
        except gw.InvalidWorktreeSlugError as exc:
            raise InvalidWorktreeName(str(exc)) from exc

        async with _lock_for(info.git_root):
            try:
                wt = await asyncio.to_thread(gw.get_or_create, info.git_root, slug, origin)
            except gw.GitWorktreeError as exc:
                raise WorktreeOperationFailed(str(exc)) from exc

            flat = gw.flatten_slug(slug)
            submodules_ok = True
            if wt.created:
                _write_sidecar(
                    info.git_root,
                    flat,
                    {
                        "name": slug,
                        "origin": origin,
                        "base_sha": wt.head_sha,
                        "created_at": int(time.time() * 1000),
                    },
                )
                await asyncio.to_thread(gw.ensure_info_exclude, info.common_dir)
                submodules_ok = await asyncio.to_thread(gw.init_submodules, wt.path)
                base_sha: str | None = wt.head_sha
            else:
                meta = _read_sidecar(info.git_root, flat) or {}
                raw_base = meta.get("base_sha")
                base_sha = raw_base if isinstance(raw_base, str) and raw_base else None

        session_cwd = self._session_cwd(wt.path, cwd, info.git_root)
        return WorktreeHandle(
            name=slug,
            path=str(wt.path),
            branch=wt.branch,
            git_root=str(info.git_root),
            base_sha=base_sha,
            session_cwd=session_cwd,
            created=wt.created,
            submodules_ok=submodules_ok,
        )

    async def list_for_project(
        self, user_id: str, project_row: ProjectRowLike
    ) -> list[WorktreeItem]:
        git = await self.project_git(user_id, project_row)
        if not git.is_repo or git.git_root is None:
            return []
        git_root = Path(git.git_root)
        try:
            listed = await asyncio.to_thread(gw.list_worktrees, git_root)
        except gw.GitWorktreeError as exc:
            raise WorktreeOperationFailed(str(exc)) from exc

        items: list[WorktreeItem] = []
        for entry in listed:
            flat = entry.path.name
            meta = _read_sidecar(git_root, flat) or {}
            raw_base = meta.get("base_sha")
            base_sha = raw_base if isinstance(raw_base, str) and raw_base else None
            raw_created = meta.get("created_at")
            created_at = raw_created if isinstance(raw_created, int) else None
            status = (
                await asyncio.to_thread(gw.status_counts, entry.path, base_sha)
                if base_sha
                else None
            )
            items.append(
                WorktreeItem(
                    name=str(meta.get("name") or flat),
                    branch=entry.branch,
                    path=str(entry.path),
                    origin=str(meta.get("origin") or _origin_from_branch(entry.branch)),
                    base_sha=base_sha,
                    created_at=created_at,
                    dirty_files=status.dirty_files if status else None,
                    ahead_commits=status.ahead_commits if status else None,
                )
            )
        return items

    async def discard(
        self,
        user_id: str,
        project_row: ProjectRowLike,
        name: str,
        force: bool = False,
    ) -> None:
        """Remove a worktree. Fail-closed: unverifiable state requires *force*."""
        git = await self.project_git(user_id, project_row)
        if not git.is_repo or git.git_root is None:
            raise WorktreeNotFound(f"worktree '{name}' not found")
        git_root = Path(git.git_root)
        flat = gw.flatten_slug(name)
        target = None
        for entry in await asyncio.to_thread(gw.list_worktrees, git_root):
            if entry.path.name == flat:
                target = entry
                break
        if target is None:
            raise WorktreeNotFound(f"worktree '{name}' not found")

        if not force:
            meta = _read_sidecar(git_root, flat) or {}
            raw_base = meta.get("base_sha")
            base_sha = raw_base if isinstance(raw_base, str) and raw_base else None
            dirty = (
                await asyncio.to_thread(gw.has_changes, target.path, base_sha)
                if base_sha
                else True  # no anchor → cannot verify → fail-closed
            )
            if dirty:
                status = (
                    await asyncio.to_thread(gw.status_counts, target.path, base_sha)
                    if base_sha
                    else None
                )
                detail = (
                    f"{status.dirty_files} uncommitted file(s), "
                    f"{status.ahead_commits} unmerged commit(s)"
                    if status
                    else "state could not be verified"
                )
                raise WorktreeDirty(
                    f"worktree '{name}' has work worth keeping ({detail}); "
                    "pass force=true to discard anyway"
                )

        async with _lock_for(git_root):
            try:
                await asyncio.to_thread(gw.remove, git_root, target.path, target.branch)
            except gw.GitWorktreeError as exc:
                raise WorktreeOperationFailed(str(exc)) from exc
            _remove_sidecar(git_root, flat)

        # The snapshots lived inside the worktree, so they went with it. Retire
        # the rows AFTER the removal actually succeeded — archiving first would
        # mark deliverables gone that are still sitting there if git refuses.
        retired = await _archive_scope_artifacts(user_id, project_row.id, name)
        if retired:
            logger.info("worktrees: retired %d deliverable(s) with worktree '%s'", retired, name)

    async def cleanup_if_clean(
        self,
        snapshot: dict[str, object],
        *,
        user_id: str = "",
        project_id: str = "",
    ) -> bool:
        """Session-teardown hook: remove the session's worktree iff clean.

        *snapshot* is the immutable ``metadata["valuz"]["worktree"]`` blob.
        Never raises — teardown must not fail the caller. Returns True when
        the worktree was removed.

        A worktree holding delivered artifacts is never removed here. Git no
        longer counts the snapshot store as dirty (it is excluded from
        ``info/exclude``), so without this check an automatic teardown would
        quietly destroy the very outputs the session was asked to produce.
        Deliverables are the point of the work; discarding them has to be
        something the user asks for, which is what ``discard`` is.

        ``user_id`` / ``project_id`` identify the artifact scope. Callers that
        cannot supply them get the old behaviour — clean means removable.
        """
        try:
            git_root = Path(str(snapshot["git_root"]))
            path = Path(str(snapshot["path"]))
            name = str(snapshot["name"])
            branch = snapshot.get("branch")
            base_sha = snapshot.get("base_sha")
        except (KeyError, TypeError):
            return False
        if not isinstance(base_sha, str) or not base_sha:
            return False  # no anchor → fail-closed keep
        # Defense against a tampered snapshot: only ever remove paths that
        # live under the managed worktrees dir of the recorded repo.
        try:
            path.resolve().relative_to(gw.worktrees_dir(git_root).resolve())
        except (ValueError, OSError):
            return False
        if not (path / ".git").exists():
            return False  # already gone (or never materialized)

        if user_id and await _scope_artifact_count(user_id, project_id, name):
            logger.info("worktrees: keeping '%s' — it holds delivered artifacts", name)
            return False

        try:
            async with _lock_for(git_root):
                if await asyncio.to_thread(gw.has_changes, path, base_sha):
                    return False
                await asyncio.to_thread(
                    gw.remove,
                    git_root,
                    path,
                    str(branch) if isinstance(branch, str) else None,
                )
                _remove_sidecar(git_root, gw.flatten_slug(name))
                return True
        except Exception:  # noqa: BLE001 — cleanup must never fail the caller
            logger.warning("worktrees: clean-teardown failed for %s", path, exc_info=True)
            return False

    async def heal_from_snapshot(self, snapshot: dict[str, object]) -> dict[str, object] | None:
        """Recreate a session's worktree that was removed since creation.

        Re-entry path (design §4-R): a historical session's cwd is frozen to
        the worktree path; when that worktree was discarded (panel, manual
        ``git worktree remove``, or sibling-session teardown), sending a new
        message would otherwise die in the runtime with a missing-cwd error.
        The path is deterministic (same slug → same path), so recreating from
        the snapshot restores the exact cwd the kernel session points at —
        based on the repo's CURRENT HEAD, with a fresh ``base_sha`` anchor.

        Returns the refreshed snapshot (caller persists it into the session
        metadata) when a recreation happened; ``None`` when the worktree is
        still alive. Raises ``WorktreeNotAvailable`` when the recorded repo
        itself is gone — the caller surfaces that as an actionable error
        instead of a cryptic runtime failure.
        """
        path = Path(str(snapshot.get("path") or ""))
        name = str(snapshot.get("name") or "")
        git_root = Path(str(snapshot.get("git_root") or ""))
        if not name or not str(path):
            return None
        if (path / ".git").exists():
            return None  # alive — nothing to heal

        if not git_root.is_dir() or await asyncio.to_thread(gw.detect_git, git_root) is None:
            raise WorktreeNotAvailable(
                f"worktree '{name}' was removed and its repository "
                f"({git_root}) is no longer a git repository"
            )
        branch = snapshot.get("branch")
        origin = _origin_from_branch(str(branch) if isinstance(branch, str) else None)

        async with _lock_for(git_root):
            try:
                wt = await asyncio.to_thread(gw.get_or_create, git_root, name, origin)
            except gw.GitWorktreeError as exc:
                raise WorktreeOperationFailed(
                    f"worktree '{name}' was removed and could not be recreated: {exc}"
                ) from exc
            flat = gw.flatten_slug(name)
            if wt.created:
                _write_sidecar(
                    git_root,
                    flat,
                    {
                        "name": name,
                        "origin": origin,
                        "base_sha": wt.head_sha,
                        "created_at": int(time.time() * 1000),
                    },
                )
                await asyncio.to_thread(gw.init_submodules, wt.path)

        logger.info("worktrees: recreated missing worktree '%s' at %s", name, wt.path)
        return {
            "name": name,
            "branch": wt.branch,
            "path": str(wt.path),
            "git_root": str(git_root),
            "base_sha": wt.head_sha,
        }

    async def resolve_session_cwd(
        self, user_id: str, project_row: ProjectRowLike, name: str
    ) -> str | None:
        """On-disk session cwd for an EXISTING managed worktree, or ``None``.

        Read-only counterpart to ``get_or_create`` used by the file-tree /
        artifact-read path: given a worktree name (from a session's snapshot),
        resolve the directory the session actually runs in (worktree path +
        project subdir, design D7). Returns ``None`` — never creates — when git
        is unavailable, the project isn't a repo, the name is invalid, or the
        worktree no longer exists on disk. The caller decides what a ``None``
        means (empty tree / 404).
        """
        cwd = self._resolve_project_cwd(user_id, project_row)
        if not await asyncio.to_thread(gw.git_available):
            return None
        info = await asyncio.to_thread(gw.detect_git, cwd)
        if info is None:
            return None
        try:
            gw.validate_slug(name)
        except gw.InvalidWorktreeSlugError:
            return None
        wt_path = gw.worktree_path(info.git_root, name)
        if not await asyncio.to_thread(wt_path.is_dir):
            return None
        return self._session_cwd(wt_path, cwd, info.git_root)

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _resolve_project_cwd(user_id: str, row: ProjectRowLike) -> str:
        from valuz_agent.infra.fs_registry import fs_registry

        kind: Literal["chat", "project"] = "project" if row.kind == "project" else "chat"
        return str(fs_registry.project_cwd(user_id, row.id, kind, row.root_path))

    @staticmethod
    def _session_cwd(worktree_path: Path, project_cwd: str, git_root: Path) -> str:
        """Project cwd projected into the worktree (design D7)."""
        try:
            rel = Path(project_cwd).resolve().relative_to(git_root.resolve())
        except (ValueError, OSError):
            return str(worktree_path)
        candidate = worktree_path / rel
        # A sparse/odd checkout may not contain the subdir; fall back to the
        # worktree root rather than handing the kernel a missing cwd.
        return str(candidate) if candidate.is_dir() else str(worktree_path)


def _origin_from_branch(branch: str | None) -> str:
    """Fallback origin classification when the sidecar is missing."""
    if branch and branch.startswith(f"{gw.BRANCH_NAMESPACE}/task-"):
        return "task"
    return "u"


worktree_service = WorktreeService()
