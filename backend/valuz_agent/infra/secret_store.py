from pathlib import Path
from typing import Protocol


class SecretStorePort(Protocol):
    """Owner-scoped secret store.

    Every operation carries the owning ``user_id`` so a shared multi-user
    backend can isolate secrets by owner (the same ``user_id`` that scopes
    business-table rows). Single-user implementations may ignore it.
    """

    def get(self, user_id: str, ref: str) -> str | None: ...
    def put(self, user_id: str, ref: str, value: str) -> None: ...
    def delete(self, user_id: str, ref: str) -> None: ...


class InMemorySecretStore:
    """Dev/test fallback. Isolates by ``(user_id, ref)``."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get(self, user_id: str, ref: str) -> str | None:
        return self._store.get((user_id, ref))

    def put(self, user_id: str, ref: str, value: str) -> None:
        self._store[(user_id, ref)] = value

    def delete(self, user_id: str, ref: str) -> None:
        self._store.pop((user_id, ref), None)


class FileSecretStore:
    """Filesystem-backed secret store for the single-user desktop build.

    ``user_id`` is accepted for ``SecretStorePort`` parity but intentionally
    NOT used for isolation: a desktop install serves exactly one user, so
    secrets live in a flat dir keyed by ``ref`` — keeping the on-disk layout
    unchanged (no migration, existing keys keep working). Owner isolation for a
    shared multi-user backend is the job of the overlay's store (e.g. an
    S3-backed one keyed by ``{user_id}/{ref}``), bound via ``ext.secret_store``.
    """

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def _path(self, ref: str) -> Path:
        safe = ref.replace("/", "__").replace("\\", "__")
        return self._base / safe

    def get(self, user_id: str, ref: str) -> str | None:
        p = self._path(ref)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        return None

    def put(self, user_id: str, ref: str, value: str) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        p = self._path(ref)
        p.write_text(value, encoding="utf-8")

    def delete(self, user_id: str, ref: str) -> None:
        p = self._path(ref)
        if p.is_file():
            p.unlink()
