"""Plugin package I/O: safe unzip, deterministic pack, sha256, https download.

A package is a zip with ``valuz-plugin.json`` at its root (one wrapping directory
is tolerated). Unpacking refuses anything that could write outside the target:
absolute paths, ``..`` segments, symlinks, encrypted members, more than
``MAX_FILES`` files or ``MAX_UNCOMPRESSED_BYTES`` of content.

``pack_directory`` writes the deterministic zip the three ``pack`` implementations
(this backend, the SDK CLI, the Go CLI) agree on: entries sorted by name, fixed
timestamps, mode 0644, no directory entries, only ``valuz-plugin.json``, ``frontend/``,
the locales directory, ``automations/``, the icon file, ``README.md`` and top-level
``LICENSE*``; ``node_modules``, ``.git`` and ``src`` directories (any depth) and
dot-files / dot-directories are left out.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from valuz_agent.modules.app_plugins.errors import InvalidManifest, InvalidSource, SourceTooLarge
from valuz_agent.modules.app_plugins.manifest import MANIFEST_NAME, locales_dir_name

MAX_FILES = 2000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
_CHUNK = 64 * 1024
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)
_SKIP_DIRS = frozenset({"node_modules", ".git", "src"})
_JUNK_PREFIXES = ("__MACOSX/",)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- unpack ---------------------------------------------------------------------


def _member_name(info: zipfile.ZipInfo) -> str | None:
    """The safe, normalised member path; ``None`` for junk to skip; raises when unsafe."""
    raw = info.filename.replace("\\", "/")
    if any(raw.startswith(p) for p in _JUNK_PREFIXES) or raw.endswith("/.DS_Store"):
        return None
    if "\x00" in raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise InvalidSource(f"unsafe path in the archive: {info.filename!r}")
    parts = [p for p in raw.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise InvalidSource(f"unsafe path in the archive: {info.filename!r}")
    if not parts:
        return None
    return "/".join(parts)


def extract_zip(zip_path: Path, dest: Path) -> Path:
    """Unpack ``zip_path`` into ``dest`` (created) and return the package root —
    ``dest``, or the single wrapping directory that holds ``valuz-plugin.json``."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise InvalidSource(f"not a readable zip archive: {exc}") from exc
    dest.mkdir(parents=True, exist_ok=True)
    real_dest = dest.resolve()
    with archive:
        members: list[tuple[zipfile.ZipInfo, str]] = []
        total = 0
        for info in archive.infolist():
            name = _member_name(info)
            if name is None:
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise InvalidSource(f"symbolic links are not allowed: {info.filename!r}")
            if info.flag_bits & 0x1:
                raise InvalidSource(f"encrypted archive member: {info.filename!r}")
            if info.is_dir():
                continue
            members.append((info, name))
            total += info.file_size
        if len(members) > MAX_FILES:
            raise InvalidSource(f"the archive holds more than {MAX_FILES} files")
        if total > MAX_UNCOMPRESSED_BYTES:
            raise SourceTooLarge(
                f"the archive expands to more than {MAX_UNCOMPRESSED_BYTES // (1024 * 1024)} MiB"
            )
        written = 0
        for info, name in members:
            target = (dest / name).resolve()
            if real_dest not in target.parents:
                raise InvalidSource(f"unsafe path in the archive: {info.filename!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as src, target.open("wb") as out:
                while True:
                    chunk = src.read(_CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_UNCOMPRESSED_BYTES:  # the header lied
                        raise SourceTooLarge("the archive expands beyond the size limit")
                    out.write(chunk)
    return _package_root(dest)


def _package_root(dest: Path) -> Path:
    if (dest / MANIFEST_NAME).is_file():
        return dest
    children = [c for c in dest.iterdir() if not c.name.startswith(".")]
    if len(children) == 1 and children[0].is_dir() and (children[0] / MANIFEST_NAME).is_file():
        return children[0]
    return dest


# ---- pack -----------------------------------------------------------------------


def _hidden(name: str) -> bool:
    return name.startswith(".") and name not in (".", "..")


def skipped(rel: str) -> bool:
    """Does a slash path cross a skipped (``node_modules``, ``.git``, ``src``) or hidden
    (dot-file / dot-directory) segment?"""
    return any(seg in _SKIP_DIRS or _hidden(seg) for seg in rel.split("/") if seg not in ("", "."))


def _check_inside(root: Path, path: Path) -> None:
    real_root = root.resolve()
    real = path.resolve()
    if real != real_root and real_root not in real.parents:
        rel = path.relative_to(root).as_posix()
        raise InvalidManifest(f"{rel} is a symlink pointing outside the plugin directory")


def _regular_file(root: Path, rel: str) -> bool:
    """Is ``rel`` a regular file inside ``root`` (a symlink must stay inside)?"""
    path = root / rel
    if not path.is_file():
        return False
    _check_inside(root, path)
    return True


def _walk(directory: Path, root: Path, exclude: Path | None) -> list[str]:
    out: list[str] = []
    for child in sorted(directory.iterdir(), key=lambda p: p.name):
        name = child.name
        if _hidden(name) or (name in _SKIP_DIRS and child.is_dir() and not child.is_symlink()):
            continue
        rel = child.relative_to(root).as_posix()
        if child.is_symlink():
            if not child.exists():
                raise InvalidManifest(f"{rel}: broken symlink")
            if child.is_dir():
                raise InvalidManifest(
                    f"{rel}: symlinked directories are not packed; copy the files instead"
                )
            _check_inside(root, child)
            out.append(rel)
        elif child.is_dir():
            out.extend(_walk(child, root, exclude))
        elif child.is_file():
            if exclude is not None and child.resolve() == exclude:
                continue
            out.append(rel)
    return out


def collect_pack_files(
    root: Path, manifest: dict[str, Any] | None, exclude: Path | None = None
) -> list[tuple[str, Path]]:
    """``(archive name, file)`` pairs the package contains, sorted by name.

    The file set every ``pack`` shares (SDK CLI, Go CLI): ``valuz-plugin.json``,
    ``README.md``, top-level ``LICENSE*``, the icon, and the ``frontend/``, locales and
    ``automations/`` trees; ``node_modules`` / ``.git`` / ``src`` directories and
    dot-files are skipped. ``exclude`` is a file never packed (the output zip).
    """
    manifest = manifest or {}
    names: set[str] = set()
    if not _regular_file(root, MANIFEST_NAME):
        raise InvalidManifest(f"{MANIFEST_NAME} not found in {root}")
    names.add(MANIFEST_NAME)
    for child in sorted(root.iterdir()):
        if (child.name == "README.md" or child.name.startswith("LICENSE")) and _regular_file(
            root, child.name
        ):
            names.add(child.name)
    icon = manifest.get("icon")
    if isinstance(icon, str) and icon and ".." not in icon.split("/") and not skipped(icon):
        if _regular_file(root, icon):
            names.add(PurePosixPath(icon).as_posix())
    for sub in ("frontend", locales_dir_name(manifest), "automations"):
        if ".." in sub.split("/") or skipped(sub):
            continue
        directory = root / sub
        if directory.is_dir():
            _check_inside(root, directory)
            names.update(_walk(directory, root, exclude))
    return [(name, root / name) for name in sorted(names)]


def pack_directory(
    root: Path,
    out_zip: Path,
    manifest: dict[str, Any] | None,
    *,
    files: list[str] | None = None,
    exclude: Path | None = None,
) -> tuple[str, int]:
    """Write the deterministic zip of ``root`` to ``out_zip``; ``(sha256, size)``.

    Entries are sorted, carry the 1980-01-01 timestamp and mode 0644, and there are no
    directory entries. ``files`` receives the archive names written; ``exclude`` is a file
    never packed (default: the zip itself).
    """
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    pairs = collect_pack_files(root, manifest, exclude=exclude or out_zip.resolve())
    tmp = out_zip.with_name(f".{out_zip.name}.{os.getpid()}.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in pairs:
            info = zipfile.ZipInfo(name, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, path.read_bytes(), compresslevel=6)
    os.replace(tmp, out_zip)
    if files is not None:
        files.extend(name for name, _ in pairs)
    return sha256_file(out_zip), out_zip.stat().st_size


def copy_tree(src: Path, dst: Path) -> None:
    """Copy a directory tree, refusing nothing (callers pass a vetted tree)."""
    shutil.copytree(src, dst, symlinks=False)


# ---- download -------------------------------------------------------------------


def make_http_client() -> httpx.AsyncClient:
    """The client plugin downloads use (tests replace this)."""
    return httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30.0, read=60.0))


def _check_url(url: str) -> None:
    parsed = httpx.URL(url)
    local = parsed.host in ("localhost", "127.0.0.1", "::1")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and local):
        raise InvalidSource("only https URLs can be installed from")


async def download(url: str, dest: Path, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> int:
    """Stream ``url`` to ``dest``; ``SourceTooLarge`` past ``max_bytes``."""
    try:
        _check_url(url)
    except httpx.InvalidURL as exc:
        raise InvalidSource(f"not a valid URL: {exc}") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    received = 0
    try:
        async with make_http_client() as client, client.stream("GET", url) as response:
            if response.status_code != 200:
                raise InvalidSource(f"download failed with HTTP {response.status_code}")
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise SourceTooLarge("the package is larger than the 50 MiB download limit")
            with dest.open("wb") as out:
                async for chunk in response.aiter_bytes(_CHUNK):
                    received += len(chunk)
                    if received > max_bytes:
                        raise SourceTooLarge("the package is larger than the 50 MiB download limit")
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise InvalidSource(f"download failed: {exc}") from exc
    return received


__all__ = [
    "MAX_DOWNLOAD_BYTES",
    "MAX_FILES",
    "MAX_UNCOMPRESSED_BYTES",
    "collect_pack_files",
    "copy_tree",
    "skipped",
    "download",
    "extract_zip",
    "make_http_client",
    "pack_directory",
    "sha256_file",
]
