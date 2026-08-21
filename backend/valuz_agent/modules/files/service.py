"""Owner-boundary validation + file metadata for the resolve endpoint.

The resolve flow is: parse ``valuz-file://<abs>`` -> ``assert_owned`` (this is
the multi-tenant isolation line) -> ``stat_meta`` -> the ``FileAddressResolverPort``
turns the owned absolute path into an access address. See
``docs/design/file-address-resolution.md``.
"""

from __future__ import annotations

import mimetypes
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
    )


__all__ = ["FileMeta", "assert_owned", "owner_allowed_roots", "stat_meta"]
