"""One owner catalog, serialized across processes; Markdown is only a view.

This repository is synchronous filesystem work, like the existing MemoryStore.
Async entrypoints should call it through asyncio.to_thread; no database is opened.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from valuz_agent.modules.memory.models import (
    ENTRY_DELIMITER,
    MemoryMutationResult,
    MemoryRecord,
    MemoryUnavailable,
    Target,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class _WindowsFileLock(Protocol):
    LK_LOCK: int
    LK_UNLCK: int

    def locking(self, fd: int, mode: int, nbytes: int, /) -> None: ...


def _windows_lock(fd: int, *, unlock: bool = False) -> None:
    import msvcrt

    backend: object = msvcrt
    if not isinstance(backend, _WindowsFileLock):
        raise MemoryUnavailable("Windows file locking is unavailable")
    os.lseek(fd, 0, os.SEEK_SET)
    backend.locking(fd, backend.LK_UNLCK if unlock else backend.LK_LOCK, 1)


class OperationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str
    result: MemoryMutationResult


class Tombstone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record_id: str
    content_hash: str
    scope_key: str
    source_keys: tuple[str, ...] = ()
    revision: int
    record_revision: int
    target: Target
    project_id: str | None = None
    namespace: str = "core"


class MemoryCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: int = Field(default=1, strict=True)
    owner_user_id: str
    revision: int = Field(default=0, ge=0, strict=True)
    records: list[MemoryRecord] = Field(default_factory=list)
    operations: dict[str, OperationReceipt] = Field(default_factory=dict)
    tombstones: list[Tombstone] = Field(default_factory=list)
    forgotten_sources: dict[str, int] = Field(default_factory=dict)
    retired_scopes: dict[str, int] = Field(default_factory=dict)


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".memory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """A separate stable inode protects read/modify/replace of the catalog."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if os.name == "nt":
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            _windows_lock(fd)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                _windows_lock(fd, unlock=True)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class MemoryRepository:
    def __init__(self, root: Path, owner_user_id: str) -> None:
        self.root = root
        self.owner_user_id = owner_user_id
        self.path = root / "memory.json"

    @contextmanager
    def transaction(self) -> Iterator[MemoryCatalog]:
        try:
            with exclusive_file_lock(self.root / ".memory.lock"):
                yield self.load()
        except (OSError, ValidationError, UnicodeError, json.JSONDecodeError) as exc:
            raise MemoryUnavailable("Memory catalog is unavailable or invalid") from exc

    def load(self) -> MemoryCatalog:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            # Never adopt flat files, even if they happen to be inside the owner root.
            return MemoryCatalog(owner_user_id=self.owner_user_id)
        catalog = MemoryCatalog.model_validate_json(raw)
        if catalog.schema_version != 1 or catalog.owner_user_id != self.owner_user_id:
            raise MemoryUnavailable("Memory catalog owner or schema does not match")
        ids = [record.id for record in catalog.records]
        if len(set(ids)) != len(ids):
            raise MemoryUnavailable("Memory catalog has duplicate record identities")
        if any(
            not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", record.namespace)
            for record in catalog.records
        ):
            raise MemoryUnavailable("Memory catalog contains an unsupported namespace")
        if any(
            len(key) != 64 or any(character not in "0123456789abcdef" for character in key)
            for key in catalog.operations
        ):
            raise MemoryUnavailable("Memory catalog contains invalid operation keys")
        return catalog

    def save(self, catalog: MemoryCatalog) -> None:
        atomic_write(self.path, catalog.model_dump_json(indent=2))

    def export_views(self, catalog: MemoryCatalog, project_paths: dict[str, Path]) -> None:
        """Exports can be regenerated; their failure never rolls back a committed catalog."""
        paths: dict[tuple[str, str | None], Path] = {
            ("user", None): self.root / "USER.md",
            ("global", None): self.root / "MEMORY.md",
        }
        paths.update({("project", pid): path for pid, path in project_paths.items()})
        for (target, project_id), path in paths.items():
            entries = [
                record.content
                for record in catalog.records
                if record.namespace == "core"
                and record.target == target
                and record.project_id == project_id
            ]
            try:
                if entries:
                    atomic_write(
                        path,
                        "<!-- Generated memory view; memory.json is authoritative. -->\n"
                        + ENTRY_DELIMITER.join(entries),
                    )
                else:
                    path.unlink(missing_ok=True)
                    if target == "project":
                        # Only remove empty view directories, never arbitrary contents.
                        try:
                            path.parent.rmdir()
                        except OSError:
                            pass
            except OSError:
                logger.warning("Memory view export failed; catalog remains authoritative")
                raise MemoryUnavailable(
                    "Catalog committed, but a memory view could not be updated; "
                    "retry the same operation"
                ) from None
