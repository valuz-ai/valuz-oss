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
import shutil
from pathlib import Path

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


def hash_and_size(src: Path) -> tuple[str, int]:
    """Stream ``src`` once for its sha256 and byte count.

    Streamed rather than read whole: deliverables are routinely tens of
    megabytes, and on the cloud deployment this read crosses an object-storage
    mount.
    """
    digest = hashlib.sha256()
    size = 0
    with src.open("rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return f"sha256:{digest.hexdigest()}", size


def snapshot_dir(scope_cwd: Path, artifact_id: str, version_no: int) -> Path:
    return artifact_root(scope_cwd) / artifact_id / f"v{version_no}"


def write_snapshot(
    src: Path, scope_cwd: Path, artifact_id: str, version_no: int, file_name: str
) -> Path:
    """Copy ``src`` into its version directory and return the absolute path.

    Staged beside the destination and renamed into place, so a snapshot is
    either complete or absent — never a half-written file that a later read
    would hash as corrupt. On the object mount a rename is a server-side copy,
    so this costs nothing extra there.
    """
    dest_dir = snapshot_dir(scope_cwd, artifact_id, version_no)
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / file_name
    staging = dest_dir / f".{file_name}.partial"
    try:
        shutil.copyfile(src, staging)
        os.replace(staging, final)
    except OSError:
        staging.unlink(missing_ok=True)
        raise
    return final
