"""Owner-boundary validation + file metadata for the resolve endpoint.

The resolve flow is: parse ``valuz-file://<abs>`` -> ``assert_owned`` (this is
the multi-tenant isolation line) -> ``stat_meta`` -> the ``FileAddressResolverPort``
turns the owned absolute path into an access address. See
``docs/design/file-address-resolution.md``.
"""

from __future__ import annotations

import mimetypes
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.projects.service import _preview_kind, _root_path


@dataclass(frozen=True)
class FileMeta:
    name: str
    mime_type: str | None
    size: int | None
    exists: bool
    preview_kind: str
    #: Opaque change token — see ``_revision``. ``None`` when the file is gone.
    revision: str | None = None


def _is_within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


async def owner_allowed_roots(user_id: str) -> list[Path]:
    """Prefix allowlist for ``user_id``'s files (the isolation boundary).

    - ``fs_registry.project_root(user_id)`` covers managed cwds and, in cloud
      deployments (``VALUZ_USER_PROJECT_ROOT`` with a ``{user_id}`` template),
      the entire ``.../workspace/{owner}/`` subtree — one prefix.
    - Plus each ``project``-kind project's explicit ``root_path`` for the
      bundled desktop, where users pick arbitrary folders outside the managed
      root. (``chat``-kind cwds live under ``project_root`` already.)
    """
    roots: list[Path] = []
    try:
        roots.append(fs_registry.project_root(user_id).resolve())
    except Exception:  # noqa: BLE001 — a broken managed root must not 500 resolve
        pass

    from valuz_agent.modules.projects.service import project_root_paths

    for _project_id, kind, root_path in await project_root_paths(user_id):
        if kind == "project" and root_path:
            try:
                roots.append(_root_path(user_id, root_path).resolve())
            except Exception:  # noqa: BLE001
                continue

    # The owner's knowledge-base tree. KB files are the owner's own uploads,
    # but they live under ``<data_dir>/kb`` — outside the project root — so
    # resolving one for "view the original file" answered ``forbidden`` for a
    # document the caller had just uploaded themselves.
    try:
        roots.append(fs_registry.kb_root(user_id).resolve())
    except Exception:  # noqa: BLE001 — same posture as the managed root above
        pass

    # Plus each knowledge base's OWN root. The line above covers the managed
    # tree; on the desktop a knowledge base can point at any folder the user
    # picked, exactly like a ``project``-kind project — and for those every
    # document sits outside all of the prefixes collected so far. "Open the
    # original file" then answered ``forbidden`` for a library the user had
    # just built, which reads as the button being broken rather than as a
    # boundary doing its job.
    from valuz_agent.modules.docs.service import owner_kb_root_paths

    for kb_root_path in await owner_kb_root_paths(user_id):
        try:
            roots.append(_root_path(user_id, kb_root_path).resolve())
        except Exception:  # noqa: BLE001 — one unreadable library must not sink the batch
            continue
    return roots


def assert_owned(abs_path: Path, roots: list[Path]) -> Path:
    """Return the symlink-resolved path if it is under one of ``roots``.

    Resolving first defeats symlink escape (a link inside the owner's root that
    points elsewhere resolves out and fails the prefix check). Raises
    ``PermissionError`` if the path is not owned.
    """
    real = abs_path.resolve()
    for root in roots:
        if _is_within(real, root):
            return real
    raise PermissionError(str(abs_path))


def _revision(st: os.stat_result | None) -> str | None:
    """Opaque token that changes whenever the bytes might have.

    ``mtime_ns`` alone is not enough — a write that lands inside the same
    filesystem timestamp tick reuses it — so size rides along. Together they
    miss only a same-length rewrite within one tick, which no editor or agent
    produces in practice.

    Returned as one opaque string on purpose: an open preview compares it for
    equality and nothing else. Exposing the two numbers separately would invite
    a client to treat mtime as an ordering, which it is not across machines
    (a cloud execution plane stats a different filesystem than the desktop).
    """
    if st is None:
        return None
    return f"{st.st_mtime_ns}-{st.st_size}"


def stat_meta(abs_path: Path) -> FileMeta:
    """File metadata for the descriptor. Missing files return ``exists=False``
    rather than raising (the click surfaces a toast, rendering isn't blocked)."""
    name = abs_path.name
    mime_type, _ = mimetypes.guess_type(name)
    # ONE stat, not is_file() + stat(): a file deleted between the two calls
    # (agent overwrite, cleanup job) raised FileNotFoundError out of here and
    # failed the WHOLE resolve batch — the opposite of this function's contract.
    try:
        st = abs_path.stat()
    except OSError:
        st = None
    exists = st is not None and stat.S_ISREG(st.st_mode)
    size = st.st_size if exists else None
    return FileMeta(
        name=name,
        mime_type=mime_type,
        size=size,
        exists=exists,
        preview_kind=_preview_kind(name, mime_type),
        revision=_revision(st) if exists else None,
    )


__all__ = ["FileMeta", "assert_owned", "owner_allowed_roots", "stat_meta"]
