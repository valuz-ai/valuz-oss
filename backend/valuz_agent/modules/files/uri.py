"""The ``valuz-file://`` URI scheme.

A file's identity is its absolute path, carried as ``valuz-file://<absolute-path>``
(three-slash canonical, aligned with ``file://``). The URI encodes *only* the
absolute path — never a storage key/bucket/mount. Location (local vs cloud) is
decided when the URI is resolved. See ``docs/design/file-address-resolution.md``.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlsplit

SCHEME = "valuz-file"

# ``/C:/Users/...`` -> a Windows path smuggled behind the empty-authority slash.
_WIN_DRIVE = re.compile(r"^/[A-Za-z]:[\\/]")


def parse_valuz_file_uri(ref: str) -> str:
    """Extract the absolute path from a ``valuz-file://<abs>`` URI.

    Canonical form is three-slash (empty authority). A two-slash ref
    (``valuz-file://Users/…``) — whose producer dropped the authority separator,
    so the first path segment was mis-parsed as the host — is **tolerated**: the
    authority is folded back onto the front of the path, so ``//abs`` and
    ``///abs`` resolve to the same absolute path. Raises ``ValueError`` only for a
    wrong scheme or an empty path. Does not touch the filesystem or validate
    ownership.
    """
    parts = urlsplit(ref.strip())
    if parts.scheme != SCHEME:
        raise ValueError(f"not a {SCHEME}:// uri")
    raw = f"/{parts.netloc}{parts.path}" if parts.netloc else parts.path
    path = unquote(raw)
    if _WIN_DRIVE.match(path):
        path = path[1:]  # /C:/x -> C:/x
    if not path:
        raise ValueError("empty path")
    return path


def build_valuz_file_uri(abs_path: str) -> str:
    """Build a ``valuz-file://`` URI from an absolute path (POSIX or Windows)."""
    p = str(abs_path).replace("\\", "/")
    if not p.startswith("/"):
        p = "/" + p  # windows C:/x -> /C:/x so the result is three-slash
    return f"{SCHEME}://{quote(p)}"


__all__ = ["SCHEME", "build_valuz_file_uri", "parse_valuz_file_uri"]
