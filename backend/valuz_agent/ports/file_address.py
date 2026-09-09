"""Port: resolve a file's absolute path into a client-usable access address.

A file's identity is a ``valuz-file://<absolute-path>`` URI (see
``docs/design/file-address-resolution.md``). When the client opens or renders a
file it exchanges that URI at ``POST /v1/files/resolve`` for a
``ResolvedAddress``:

- **local** (bundled desktop): the absolute path, read client-side via the
  desktop ``valuz-local://`` protocol / IPC.
- **remote** (cloud): a presigned object-storage URL the client fetches directly,
  optionally paired with a ``download_url`` that answers with
  ``Content-Disposition: attachment`` so the client can *save* the same bytes.

The backend never proxies file bytes. OSS ships ``LocalFileAddressResolver`` (the
``local`` case); a commercial overlay binds a storage-specific resolver (e.g. COS
presigned URLs) via ``set_file_address_resolver()`` at app startup.

``to_address`` is async because overlay implementations may do network I/O
(signing calls) and every call site lives on the event loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class ResolvedAddress:
    """How the client should reach a file.

    ``kind == "local"`` carries ``abs_path`` (client reads it directly).
    ``kind == "remote"`` carries a presigned ``url`` (client fetches it) and its
    ``expires_at`` (epoch seconds).

    ``download_url`` is the SAME bytes addressed for *saving* rather than for
    *rendering* — an address whose response carries
    ``Content-Disposition: attachment``. It exists because the two intents
    cannot share one URL: a cross-origin ``<a download>`` has its ``download``
    attribute ignored by the browser, so a plain presigned URL opens the file in
    a tab instead of saving it, under the object key's name rather than the
    file's. Optional and additive — a resolver that does not implement it leaves
    it ``None`` and the client falls back to ``url``.
    """

    kind: str  # "local" | "remote"
    abs_path: Path | None = None
    url: str | None = None
    expires_at: int | None = None
    download_url: str | None = None


class FileAddressResolverPort(Protocol):
    """Turn an owned absolute path into a ``ResolvedAddress``."""

    async def to_address(
        self,
        *,
        owner_user_id: str,
        abs_path: Path,
    ) -> ResolvedAddress:
        """Resolve ``abs_path`` for ``owner_user_id``.

        The endpoint has already validated that ``abs_path`` is owned by
        ``owner_user_id`` (prefix check + symlink guard). An implementation MAY
        re-check its own storage boundary and raise ``PermissionError`` for
        defense in depth.
        """
        ...


class LocalFileAddressResolver:
    """OSS default: bundled/local deployment returns the absolute path as-is."""

    async def to_address(
        self,
        *,
        owner_user_id: str,
        abs_path: Path,
    ) -> ResolvedAddress:
        return ResolvedAddress(kind="local", abs_path=abs_path)


def get_file_address_resolver() -> FileAddressResolverPort:
    from valuz_agent.ports.extensions import ext

    return ext.file_address_resolver


def set_file_address_resolver(port: FileAddressResolverPort) -> None:
    """Replace the resolver (called by the commercial app at startup)."""
    from valuz_agent.ports.extensions import ext

    ext.file_address_resolver = port


__all__ = [
    "FileAddressResolverPort",
    "LocalFileAddressResolver",
    "ResolvedAddress",
    "get_file_address_resolver",
    "set_file_address_resolver",
]
