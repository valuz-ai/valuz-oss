from pathlib import Path
from typing import Protocol


class SecretStorePort(Protocol):
    def get(self, ref: str) -> str | None: ...
    def put(self, ref: str, value: str) -> None: ...
    def delete(self, ref: str) -> None: ...


class InMemorySecretStore:
    """Dev/test fallback — production uses OS keychain via Electron bridge."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def get(self, ref: str) -> str | None:
        return self._store.get(ref)

    def put(self, ref: str, value: str) -> None:
        self._store[ref] = value

    def delete(self, ref: str) -> None:
        self._store.pop(ref, None)


class FileSecretStore:
    """Filesystem-backed secret store for desktop development."""

    def __init__(self, base_dir: Path) -> None:
        self._base = base_dir

    def _path(self, ref: str) -> Path:
        safe = ref.replace("/", "__").replace("\\", "__")
        return self._base / safe

    def get(self, ref: str) -> str | None:
        p = self._path(ref)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
        return None

    def put(self, ref: str, value: str) -> None:
        self._base.mkdir(parents=True, exist_ok=True)
        p = self._path(ref)
        p.write_text(value, encoding="utf-8")

    def delete(self, ref: str) -> None:
        p = self._path(ref)
        if p.is_file():
            p.unlink()

