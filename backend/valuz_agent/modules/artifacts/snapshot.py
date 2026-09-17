"""Taking an immutable copy of a delivered file.

Snapshots live in ``<scope_cwd>/.artifact/<artifact_id>/v<N>/<file_name>`` —
inside the working directory, not in a separate content store. Two consequences
drive most of what is here:

* The agent can read a past version with a plain ``Read`` on a path it can see,
  so no materialize tool is needed and no content-addressed naming may be used
  (a hash-named blob is unreadable, and the object mount has no hard links to
  bridge the two).
* The agent can also *delete* it. Immutability is a convention here, not a
  permission — the mount offers no per-file modes. ``content_hash`` is what
  makes a damaged snapshot detectable after the fact.

Paths recorded in the database are absolute, because an absolute path is the
file's identity for the unified resolver: the same string is what the model
reads inside the sandbox (which mounts the host path), what a ``valuz-file://``
link carries, and what ``/v1/files/resolve`` exchanges for a local path or a
presigned URL.
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from valuz_agent.infra.path_names import sanitize_segment

logger = logging.getLogger(__name__)

ARTIFACT_DIR_NAME = ".artifact"

_HASH_CHUNK = 1024 * 1024


def artifact_root(scope_cwd: Path) -> Path:
    return scope_cwd / ARTIFACT_DIR_NAME


def is_inside_artifact_root(abs_path: Path, scope_cwd: Path) -> bool:
    """Whether a path points into the snapshot store.

    Delivering out of ``.artifact`` is refused: it would either re-snapshot a
    snapshot (a version whose content is the previous version, endlessly) or let
    a caller present an existing revision's bytes as a fresh delivery.
    """
    root = artifact_root(scope_cwd)
    try:
        return abs_path == root or root in abs_path.parents
    except (OSError, ValueError):  # pragma: no cover — defensive
        return False


def format_for(file_name: str) -> str | None:
    """The file's extension. A fact about the name, not a judgement about it —
    unlike ``ArtifactKind``, which the caller supplies."""
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return suffix or None


def guess_mime(file_name: str) -> str | None:
    return mimetypes.guess_type(file_name)[0]


def snapshot_dir(scope_cwd: Path, artifact_id: str, version_no: int) -> Path:
    return artifact_root(scope_cwd) / artifact_id / f"v{version_no}"


#: One path component's ceiling. ext4 and overlayfs count 255 BYTES; APFS
#: counts 255 characters. A CJK name reaches the byte cap first, which is why a
#: name that fits on a developer's Mac can still fail in a Linux container.
_NAME_MAX_BYTES = 255
#: ``.<name>.<8 hex>.partial`` wraps the name while it is being written, so the
#: staging name — not the final one — is what has to fit.
_STAGING_DECORATION = len(".") + len(".") + 8 + len(".partial")


def snapshot_file_name(name: str) -> str:
    """``name``, made safe to use as the snapshot's one path component.

    Callers pass whatever they hold. For a copied file that is a basename and
    nothing here applies. For generated content it is a label a model wrote,
    and a model writes things like ``市场规模（全球 / 中国）``. Two things then
    go wrong, and both surface to the caller as the same bare ``OSError`` with
    nothing naming the file:

    * a separator makes ``dest_dir / name`` a *nested* path whose parent does
      not exist — ``FileNotFoundError``, at any length;
    * a long non-ASCII name exceeds the byte limit — ``ENAMETOOLONG``, only on
      the filesystems that count bytes, so it passes every local test and
      fails in the cluster.

    The extension is preserved when clipping, because it is not decoration
    here: :func:`format_for` and :func:`guess_mime` both read it, and the agent
    opens this file by name.
    """
    safe = sanitize_segment(name, fallback="snapshot")
    budget = _NAME_MAX_BYTES - _STAGING_DECORATION
    if len(safe.encode()) <= budget:
        return safe
    suffix = Path(safe).suffix
    if len(suffix.encode()) > budget // 2:
        suffix = ""  # not an extension, just a long tail after a dot
    stem = safe[: len(safe) - len(suffix)] if suffix else safe
    while stem and len(f"{stem}{suffix}".encode()) > budget:
        stem = stem[:-1]
    return f"{stem}{suffix}" if stem else sanitize_segment(suffix, fallback="snapshot")


@dataclass(frozen=True)
class StagedSnapshot:
    """A complete copy that is not yet the snapshot.

    Held at ``staging`` until the caller knows the delivery is going to be
    recorded; ``promote`` puts it at ``final``, ``discard`` removes it.
    """

    staging: Path
    final: Path
    content_hash: str
    byte_size: int


def stage_snapshot(
    src: Path, scope_cwd: Path, artifact_id: str, version_no: int, file_name: str
) -> StagedSnapshot:
    """Copy ``src`` into its version directory, hashing the bytes on the way.

    One pass, not two. The hash and the copy both have to read the whole file,
    and reading it twice doubles what the cloud deployment pulls across an
    object-storage mount for every delivery — so the digest is computed from the
    bytes already in flight.

    Lands on a staging name rather than the snapshot's, for two reasons. A
    snapshot is then either complete or absent, never a half-written file a
    later read would hash as corrupt. And the destination is shared: two
    deliveries racing on one deliverable compute the SAME version number, so
    both would otherwise write ``v2/report.md`` and the loser's bytes could
    end up under the winner's row. Only the delivery that wins the head
    promotes. The staging name carries a token for the same reason — the losing
    attempt must not be writing over the winner's staging file either.
    """
    dest_dir = snapshot_dir(scope_cwd, artifact_id, version_no)
    dest_dir.mkdir(parents=True, exist_ok=True)
    file_name = snapshot_file_name(file_name)
    staging = dest_dir / f".{file_name}.{secrets.token_hex(4)}.partial"
    digest = hashlib.sha256()
    size = 0
    try:
        with src.open("rb") as reader, staging.open("wb") as writer:
            while chunk := reader.read(_HASH_CHUNK):
                digest.update(chunk)
                writer.write(chunk)
                size += len(chunk)
    except OSError:
        staging.unlink(missing_ok=True)
        raise
    return StagedSnapshot(
        staging=staging,
        final=dest_dir / file_name,
        content_hash=f"sha256:{digest.hexdigest()}",
        byte_size=size,
    )


def stage_snapshot_bytes(
    data: bytes, scope_cwd: Path, artifact_id: str, version_no: int, file_name: str
) -> StagedSnapshot:
    """Same as ``stage_snapshot``, for a deliverable that arrives as content.

    Generated documents (A2UI JSON) have no source file to copy — the
    model produced them into a tool result. They still get a snapshot on disk
    for the reason at the top of this module: a version the agent cannot
    ``Read`` is a version it cannot revise, and "generate the next version of
    this page" is the whole point of a generated deliverable.

    Staging discipline is identical to the copy path — two deliveries racing on
    one deliverable compute the SAME version number, so only the one that wins
    the head may promote into the shared destination.
    """
    dest_dir = snapshot_dir(scope_cwd, artifact_id, version_no)
    dest_dir.mkdir(parents=True, exist_ok=True)
    file_name = snapshot_file_name(file_name)
    staging = dest_dir / f".{file_name}.{secrets.token_hex(4)}.partial"
    try:
        staging.write_bytes(data)
    except OSError:
        staging.unlink(missing_ok=True)
        raise
    return StagedSnapshot(
        staging=staging,
        final=dest_dir / file_name,
        content_hash=f"sha256:{hashlib.sha256(data).hexdigest()}",
        byte_size=len(data),
    )


def promote_snapshot(staged: StagedSnapshot) -> Path:
    """Make a staged copy the snapshot. On the object mount this is a
    server-side copy rather than a link swap, which is why it happens once and
    only for the delivery that is actually being recorded."""
    os.replace(staged.staging, staged.final)
    return staged.final


def discard_snapshot(staged: StagedSnapshot) -> None:
    """Drop a staged copy that will not be recorded — a replay, or a delivery
    that lost the head. Never raises: the delivery's outcome is already decided
    and leftover staging bytes are not worth failing it for."""
    try:
        staged.staging.unlink(missing_ok=True)
    except OSError:  # pragma: no cover — defensive
        logger.warning("deliver: could not remove staged snapshot %s", staged.staging)
