"""Port: keep a deployment's workspace-sync log in step with host file I/O.

A deployment whose agents run in remote sandboxes may keep each project
directory as a replicated log (the sandbox writes locally and commits; the host
reads and writes the canonical copy on shared storage). Two moments need the
host's cooperation, and this port is where it gives it:

- ``after_write`` — the host has just created, modified, moved or deleted
  files inside a project directory (an upload, a plan markdown, a skill copied
  into ``.claude/skills/``, a ``git worktree add``). The implementation records
  the change so a sandbox attached to that workspace sees it without waiting
  for a periodic reconcile.
- ``before_read`` — the host is about to read files an agent may just have
  written in its sandbox (a delivered artifact, a staged skill, a member's run
  directory). The implementation asks the attached sandboxes to commit those
  paths first, so the host does not read a stale canonical copy.

Contract (both methods):

- ``paths`` are ABSOLUTE host paths. A path may be a file or a directory; a
  directory means its whole subtree, deletions under it included. A path that
  no longer exists means it was deleted (or moved away) — the host reports a
  move as both its old and its new path.
- Paths outside any synced workspace are simply ignored, so a caller never has
  to decide whether a path is "inside a project"; it reports what it touched.
- Implementations must be idempotent: the same path may be reported more than
  once for one change, and callers do not deduplicate across calls.
- ``owner_user_id`` is always passed explicitly by the caller — the owner of
  the files, never read from ambient request context (the same rule every
  owner-scoped port follows). ``project_id`` is an optional hint naming the
  project scope the caller was working in; implementations still resolve the
  workspace from the path and must not trust the hint over it (a scope id is
  not always a synced project — the skill-version store has a pseudo one).
- Callers call ``after_write`` AFTER the write / move / delete has completed,
  and ``before_read`` BEFORE reading. Neither is called while holding a
  database transaction when that can be avoided — a caller batches its paths
  and calls once, before (or after) its unit of work. Where a caller cannot
  avoid it, it bounds ``before_read`` with a short timeout and says so.
- Both run inline in host operations: implementations must bound their own
  latency and must not assume they will be retried.

OSS binds ``NoopWorkspaceSync``: the desktop and every single-machine
deployment read and write one filesystem, so there is nothing to keep in step
and host behaviour is unchanged. A deployment binds its own implementation at
startup via ``set_workspace_sync()`` / ``ext.workspace_sync``.

Call sites use ``notify_written`` / ``ensure_readable`` rather than the port
directly: both resolve the binding lazily, skip an empty path list, and are
fail-open — a sync failure is logged and the host operation proceeds, because
the deployment's reconcile loop converges the log on its own and a host write
must never fail on a replication side channel.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: How long ``ensure_readable`` waits by default before reading anyway.
DEFAULT_READ_TIMEOUT_S = 5.0


@runtime_checkable
class WorkspaceSyncPort(Protocol):
    """Keep a replicated workspace log in step with host-side file I/O."""

    async def after_write(
        self,
        *,
        owner_user_id: str,
        paths: Sequence[Path],
        project_id: str | None = None,
    ) -> None:
        """Record host-side writes / moves / deletions under ``paths``."""
        ...

    async def before_read(
        self,
        *,
        owner_user_id: str,
        paths: Sequence[Path],
        project_id: str | None = None,
    ) -> None:
        """Make sandbox-side writes under ``paths`` visible to the host."""
        ...


class NoopWorkspaceSync:
    """OSS default: one filesystem, nothing to keep in step."""

    async def after_write(
        self,
        *,
        owner_user_id: str,
        paths: Sequence[Path],
        project_id: str | None = None,
    ) -> None:
        return None

    async def before_read(
        self,
        *,
        owner_user_id: str,
        paths: Sequence[Path],
        project_id: str | None = None,
    ) -> None:
        return None


def get_workspace_sync() -> WorkspaceSyncPort:
    from valuz_agent.ports.extensions import ext

    return ext.workspace_sync


def set_workspace_sync(port: WorkspaceSyncPort) -> None:
    """Replace the binding (called by a deployment at startup)."""
    from valuz_agent.ports.extensions import ext

    ext.workspace_sync = port


def _bound_port() -> WorkspaceSyncPort | None:
    """The active binding, or ``None`` when it is the OSS no-op.

    Checked by exact type so the default costs a call site nothing — not even
    normalising its path list.
    """
    port = get_workspace_sync()
    return None if type(port) is NoopWorkspaceSync else port


def _absolute_paths(paths: Iterable[Path | str]) -> tuple[Path, ...]:
    """Absolute, de-duplicated (order kept). Relative / empty entries are
    dropped: the contract is absolute host paths, and a relative one would be
    resolved against the host process's cwd — never a workspace."""
    out: dict[Path, None] = {}
    for raw in paths:
        if isinstance(raw, str) and not raw.strip():
            continue
        path = Path(raw)
        if not path.is_absolute():
            logger.debug("workspace_sync: ignoring non-absolute path %r", raw)
            continue
        out.setdefault(path, None)
    return tuple(out)


async def notify_written(
    owner_user_id: str,
    paths: Iterable[Path | str],
    *,
    project_id: str | None = None,
) -> None:
    """Report host-side writes / moves / deletions. Never raises.

    Call AFTER the write completed, once per unit of work with every path it
    touched (a move is its old and its new path).
    """
    port = _bound_port()
    if port is None or not owner_user_id:
        return
    normalized: tuple[Path, ...] = ()
    try:
        # Inside the fail-open block: a caller may pass a lazy iterable that
        # touches the filesystem, and its error must not reach the host op.
        normalized = _absolute_paths(paths)
        if not normalized:
            return
        await port.after_write(owner_user_id=owner_user_id, paths=normalized, project_id=project_id)
    except Exception:  # noqa: BLE001 — fail-open: the reconcile loop converges
        logger.warning(
            "workspace_sync: after_write failed for %d path(s) (owner=%s); the host write stands",
            len(normalized),
            owner_user_id,
            exc_info=True,
        )


async def ensure_readable(
    owner_user_id: str,
    paths: Iterable[Path | str],
    *,
    project_id: str | None = None,
    timeout_s: float = DEFAULT_READ_TIMEOUT_S,
) -> None:
    """Wait (bounded) for sandbox-side writes under ``paths``. Never raises.

    Call BEFORE reading files an agent may just have written in its sandbox,
    and outside any database transaction where possible. On timeout or error
    the caller reads anyway — what it sees is what it would have seen without
    the port.
    """
    port = _bound_port()
    if port is None or not owner_user_id:
        return
    normalized: tuple[Path, ...] = ()
    try:
        # Inside the fail-open block, as in ``notify_written``.
        normalized = _absolute_paths(paths)
        if not normalized:
            return
        await asyncio.wait_for(
            port.before_read(owner_user_id=owner_user_id, paths=normalized, project_id=project_id),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning(
            "workspace_sync: before_read timed out after %.1fs for %d path(s) "
            "(owner=%s); reading anyway",
            timeout_s,
            len(normalized),
            owner_user_id,
        )
    except Exception:  # noqa: BLE001 — fail-open: read what is there
        logger.warning(
            "workspace_sync: before_read failed for %d path(s) (owner=%s); reading anyway",
            len(normalized),
            owner_user_id,
            exc_info=True,
        )


__all__ = [
    "DEFAULT_READ_TIMEOUT_S",
    "NoopWorkspaceSync",
    "WorkspaceSyncPort",
    "ensure_readable",
    "get_workspace_sync",
    "notify_written",
    "set_workspace_sync",
]
