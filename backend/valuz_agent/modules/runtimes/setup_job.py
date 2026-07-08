"""User-authorized download of the pinned codex runtime binary.

Why this exists
---------------
The desktop bundle used to ship the ``openai-codex-cli-bin`` binary
(~217MB uncompressed, ~88MB of every DMG) even though most users never
select the Codex runtime. The PyInstaller spec now excludes that
package's data files; this setup job downloads the binary on demand
instead, through the same ``SetupJobController`` framework (progress
rows, cancellation, READY markers) the RapidOCR model download uses.

What we download
----------------
The exact platform wheel of ``openai-codex-cli-bin`` at the version the
SDK pins (``openai-codex`` hard-pins it, see ``backend/pyproject.toml``).
A wheel is a zip; we verify its sha256 and extract the single
``codex_cli_bin/bin/codex`` member. The wheel table below is derived
from ``backend/uv.lock`` and MUST stay in sync with it —
``tests/modules/runtimes/test_setup_job.py`` re-derives the table from
the lockfile and fails on drift (the SOP for an SDK bump is: uv lock →
run tests → update ``CODEX_CLI_BIN_VERSION`` + ``_WHEELS`` here).

Download sources: files.pythonhosted.org is canonical; the TUNA and
Aliyun PyPI mirrors serve the same ``/packages/…`` paths and are tried
next — the primary user base is behind networks where the canonical CDN
stalls. Integrity always comes from the pinned sha256, never the source.

Where it lands / how it activates
---------------------------------
``~/.valuz-oss/runtimes/codex/<version>/codex`` (via
``fs_registry.runtime_bin_dir``), plus ``NOTICE`` + a ``READY`` marker
carrying the version. Activation is the kernel's existing
``CODEX_BIN_OVERRIDE`` env (resolution order: override → bundled →
PATH): ``activate_codex_override()`` points it at the downloaded binary
— called at boot (``boot/steps.activate_downloaded_runtimes``) and again
when the job succeeds, so new sessions pick it up without a restart.
A version-mismatched leftover install (SDK pin moved on) is treated as
not-installed: the probe reports unavailable and the UI offers the new
download; ``run`` prunes other version dirs on success.

Authorization contract: like the RapidOCR job, the ``accept_license`` +
``confirmed_source`` gate lives at the HTTP endpoint, not here.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.ports.parser_plugin import SetupRequirement

logger = logging.getLogger(__name__)

CODEX_RUNTIME_SETUP_ID = "codex_runtime"

# Mirrors ``kernel/src/runtimes/codex/runtime.py::CODEX_BIN_OVERRIDE_ENV`` and
# ``adapters/runtime_registry`` (binary_env_override). Not imported from the
# kernel — the host↔kernel boundary forbids ``src.runtimes`` imports.
CODEX_BIN_OVERRIDE_ENV = "CODEX_BIN_OVERRIDE"

RUNTIME_ID = "codex"

# The openai-codex-cli-bin version the installed SDK is pinned against.
# MUST equal the version resolved in backend/uv.lock (test-enforced).
CODEX_CLI_BIN_VERSION = "0.137.0a4"


@dataclass(frozen=True)
class _Wheel:
    """One platform wheel of openai-codex-cli-bin, as locked in uv.lock."""

    platform_tag: str
    # Path under the package-index host ("packages/…/<filename>.whl") —
    # identical across files.pythonhosted.org and its mirrors.
    path: str
    sha256: str
    size_bytes: int


_PKG_PREFIX = f"openai_codex_cli_bin-{CODEX_CLI_BIN_VERSION}-py3-none"

_WHEELS: tuple[_Wheel, ...] = (
    _Wheel(
        platform_tag="macosx_10_9_x86_64",
        path=f"packages/bd/60/af73ef1676cd477fa83ed4b889bf3b57c63c47dd87025b2cc4262793cff6/{_PKG_PREFIX}-macosx_10_9_x86_64.whl",
        sha256="b33c3917e0b58d527ee11a11a78ad390f7d8e6aa25577dd21665ab3c8bf5cf9a",
        size_bytes=94300191,
    ),
    _Wheel(
        platform_tag="macosx_11_0_arm64",
        path=f"packages/92/8f/d1a5f8c87176e00ef6a85798794f4530f5eb04e5a1a13468b5b3c3a361f9/{_PKG_PREFIX}-macosx_11_0_arm64.whl",
        sha256="3d0f0bc5becc88c61952fbfa9bd792ac9d74fa78b3a6bd40f545b612048b07eb",
        size_bytes=83924479,
    ),
    _Wheel(
        platform_tag="manylinux_2_17_aarch64",
        path=f"packages/3e/3c/fc00bcdc0c302208317d5eb1d0bfaab3024f351cd0121400f19baa6b19aa/{_PKG_PREFIX}-manylinux_2_17_aarch64.whl",
        sha256="2f1656339e2736868c4cce59f6d9e5c633879123687169b03b1137d42bf2c11a",
        size_bytes=83363315,
    ),
    _Wheel(
        platform_tag="manylinux_2_17_x86_64",
        path=f"packages/ec/09/39362e944ebeb12fcbfb86881fbb4dd6e806f77f7541c1f1f993bb9351a0/{_PKG_PREFIX}-manylinux_2_17_x86_64.whl",
        sha256="6454f838d44c56c1ed07a29b391fa412785e5dd2ffd06db0b62e62478c19bb64",
        size_bytes=90611239,
    ),
    _Wheel(
        platform_tag="win_amd64",
        path=f"packages/9e/26/81e037066b9b8d312a6f9e09015e452ce17630d5ab88e02a4c1d9503e4e8/{_PKG_PREFIX}-win_amd64.whl",
        sha256="9e13bf68e18e36bd3a0efd51213281c83e9f6ec22bdb7a45bd2e0211822733a9",
        size_bytes=94744969,
    ),
    _Wheel(
        platform_tag="win_arm64",
        path=f"packages/0d/a3/952bc2a5d62373a51fea161effe3b338b3417c2f6e65fe467ed91b205e2b/{_PKG_PREFIX}-win_arm64.whl",
        sha256="5ec4303ca2dcb5f838e0de3ca7f44050b6bcdd41d281a178c3a1420a985a515d",
        size_bytes=86963504,
    ),
)


# Hosts that serve the same "/packages/…" paths. Canonical first; the TUNA and
# Aliyun mirrors cover networks where files.pythonhosted.org stalls. A source
# is only trusted for transport — the sha256 check below is the integrity gate.
_SOURCE_BASES: tuple[str, ...] = (
    "https://files.pythonhosted.org",
    "https://pypi.tuna.tsinghua.edu.cn",
    "https://mirrors.aliyun.com/pypi",
)

_READY_MARKER = "READY"
_NOTICE_FILE = "NOTICE"

_NOTICE_TEXT = """\
codex CLI — Copyright OpenAI. Licensed under Apache License 2.0.
https://github.com/openai/codex

Downloaded as the unmodified `openai-codex-cli-bin` wheel from PyPI and
extracted locally. Full license text ships inside the codex binary's
distribution; see the repository link above.
"""

ProgressCallback = Callable[[int, int | None], None]


def _wheel_for_current_platform() -> _Wheel:
    """Pick the wheel matching this host, mirroring how pip would.

    Desktop targets are mac (arm64/x86_64), glibc Linux, and Windows —
    the same matrix the release workflow builds. No musl wheels in the
    table: Alpine isn't a desktop target, and adding one is a one-line
    table entry if that ever changes.
    """
    machine = platform.machine().lower()
    is_arm = machine in ("arm64", "aarch64")
    if sys.platform == "darwin":
        tag = "macosx_11_0_arm64" if is_arm else "macosx_10_9_x86_64"
    elif os.name == "nt":
        tag = "win_arm64" if is_arm else "win_amd64"
    else:
        tag = "manylinux_2_17_aarch64" if is_arm else "manylinux_2_17_x86_64"
    for wheel in _WHEELS:
        if wheel.platform_tag == tag:
            return wheel
    raise RuntimeError(f"no codex wheel for platform tag {tag!r}")


def codex_setup_requirement() -> SetupRequirement:
    """Static card metadata for the settings/authorization dialog.

    ``size_bytes`` is the current platform's wheel (the actual network
    transfer), not the ~217MB extracted binary.
    """
    try:
        size: int | None = _wheel_for_current_platform().size_bytes
    except RuntimeError:
        size = None
    return SetupRequirement(
        id=CODEX_RUNTIME_SETUP_ID,
        label_zh="OpenAI Codex 运行时",
        kind="binary_download",
        network_required=True,
        size_bytes=size,
        source="PyPI (openai-codex-cli-bin)",
        license_name="Apache-2.0",
        license_url="https://github.com/openai/codex/blob/main/LICENSE",
        label_key="settings.runtimes.codexSetupLabel",
    )


# Runtime ids the host can install on demand, mapped to their setup job.
# ``api/routes/runtimes.py`` reads this to stamp ``installable`` +
# ``setup_id`` on the picker payload.
INSTALLABLE_RUNTIME_SETUP_IDS: dict[str, str] = {RUNTIME_ID: CODEX_RUNTIME_SETUP_ID}


def _binary_name() -> str:
    return "codex.exe" if os.name == "nt" else "codex"


def installed_codex_path() -> Path | None:
    """The downloaded binary, or ``None`` unless the install is complete
    AND matches the version the current SDK pin requires."""
    install_dir = fs_registry.runtime_bin_dir(RUNTIME_ID, CODEX_CLI_BIN_VERSION)
    marker = install_dir / _READY_MARKER
    binary = install_dir / _binary_name()
    if not (marker.exists() and binary.is_file()):
        return None
    try:
        content = marker.read_text(encoding="utf-8")
    except OSError:
        return None
    if f"version={CODEX_CLI_BIN_VERSION}" not in content:
        return None
    return binary


def activate_codex_override() -> bool:
    """Point ``CODEX_BIN_OVERRIDE`` at the downloaded binary for this process.

    The kernel's codex resolution and the host availability probe both
    honour the override first, so setting it is the entire activation.
    A pre-existing override pointing elsewhere is the user's own pin —
    leave it alone. Returns True when the override is (now) ours.
    """
    binary = installed_codex_path()
    if binary is None:
        return False
    current = os.environ.get(CODEX_BIN_OVERRIDE_ENV, "").strip()
    if current and current != str(binary):
        logger.info(
            "codex runtime: %s already set to %s — leaving the user override in place",
            CODEX_BIN_OVERRIDE_ENV,
            current,
        )
        return False
    os.environ[CODEX_BIN_OVERRIDE_ENV] = str(binary)
    return True


class CodexRuntimeSetupJob:
    """Streams the pinned codex wheel, verifies its sha256, extracts the
    binary, and activates the override. Cooperative cancellation via the
    controller's ``cancel_event``; progress at ~2Hz."""

    setup_id: str = CODEX_RUNTIME_SETUP_ID

    def install_dir(self) -> Path:
        return fs_registry.runtime_bin_dir(RUNTIME_ID, CODEX_CLI_BIN_VERSION)

    def is_complete(self) -> bool:
        return installed_codex_path() is not None

    # ----- SetupJob.run ----------------------------------------------

    def run(
        self,
        *,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        wheel = _wheel_for_current_platform()
        target_dir = self.install_dir()

        progress_cb(0, wheel.size_bytes)

        wheel_path = target_dir / "wheel.zip.partial"
        try:
            downloaded = _download_wheel(
                wheel=wheel,
                dest=wheel_path,
                cancel_event=cancel_event,
                progress_cb=progress_cb,
            )
            if cancel_event.is_set():
                return

            _extract_codex_binary(wheel_path, target_dir)
            (target_dir / _NOTICE_FILE).write_text(_NOTICE_TEXT, encoding="utf-8")
            marker_text = (
                f"timestamp={datetime.now(UTC).isoformat()}\n"
                f"version={CODEX_CLI_BIN_VERSION}\n"
            )
            (target_dir / _READY_MARKER).write_text(marker_text, encoding="utf-8")
            progress_cb(downloaded, wheel.size_bytes)

            _prune_stale_versions(keep=CODEX_CLI_BIN_VERSION)
            activate_codex_override()
        finally:
            wheel_path.unlink(missing_ok=True)
            if cancel_event.is_set():
                _cleanup_cancelled(target_dir)


# ----- helpers --------------------------------------------------------


def _download_wheel(
    *,
    wheel: _Wheel,
    dest: Path,
    cancel_event: threading.Event,
    progress_cb: ProgressCallback,
) -> int:
    """Stream the wheel to ``dest``, trying each source base in order.

    A source failure (connect/read error, HTTP error, digest mismatch)
    moves on to the next base; the download restarts from zero there —
    integrity comes from the pinned sha256, so a truncated or tampered
    transfer can never land. Raises the last error when every source
    fails. Returns the byte count on success.
    """
    last_error: Exception | None = None
    for base in _SOURCE_BASES:
        if cancel_event.is_set():
            return 0
        url = f"{base}/{wheel.path}"
        try:
            downloaded = _stream_one(
                url=url,
                dest=dest,
                expected_sha256=wheel.sha256,
                total_bytes=wheel.size_bytes,
                cancel_event=cancel_event,
                progress_cb=progress_cb,
            )
            if cancel_event.is_set():
                return downloaded
            logger.info("codex runtime: downloaded %s (%d bytes)", url, downloaded)
            return downloaded
        except (httpx.HTTPError, _DigestMismatchError) as exc:
            logger.warning("codex runtime: source failed, trying next: %s (%s)", url, exc)
            last_error = exc
            dest.unlink(missing_ok=True)
    raise last_error if last_error is not None else RuntimeError("no download sources")


class _DigestMismatchError(RuntimeError):
    pass


def _stream_one(
    *,
    url: str,
    dest: Path,
    expected_sha256: str,
    total_bytes: int,
    cancel_event: threading.Event,
    progress_cb: ProgressCallback,
) -> int:
    digest = hashlib.sha256()
    downloaded = 0
    last_publish_ts = 0.0
    publish_interval_s = 0.5

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=256 * 1024):
                    if cancel_event.is_set():
                        return downloaded
                    fh.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    now = time.monotonic()
                    if now - last_publish_ts >= publish_interval_s:
                        progress_cb(downloaded, total_bytes)
                        last_publish_ts = now

    if digest.hexdigest() != expected_sha256:
        raise _DigestMismatchError(f"sha256 mismatch for {url}")
    progress_cb(downloaded, total_bytes)
    return downloaded


def _extract_codex_binary(wheel_path: Path, target_dir: Path) -> None:
    """Extract ``codex_cli_bin/bin/codex`` from the verified wheel.

    Only the binary is taken — the wheel's ``codex-path/rg`` helper and
    package metadata are unused by the harness (nothing calls
    ``bundled_path_dir``), matching what codex sessions already ran with.
    """
    member = f"codex_cli_bin/bin/{_binary_name()}"
    final = target_dir / _binary_name()
    partial = target_dir / f"{_binary_name()}.partial"
    with zipfile.ZipFile(wheel_path) as zf:
        try:
            info = zf.getinfo(member)
        except KeyError as exc:
            raise RuntimeError(f"wheel is missing {member!r} — layout changed upstream") from exc
        with zf.open(info) as src, partial.open("wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
    if os.name != "nt":
        partial.chmod(0o755)
    partial.replace(final)


def _prune_stale_versions(*, keep: str) -> None:
    """Drop other version dirs under ``runtimes/codex/`` so an SDK bump
    doesn't accumulate ~217MB per old version."""
    codex_root = fs_registry.runtime_bin_dir(RUNTIME_ID, keep).parent
    for entry in codex_root.iterdir():
        if entry.is_dir() and entry.name != keep:
            try:
                shutil.rmtree(entry)
                logger.info("codex runtime: pruned stale version dir %s", entry)
            except OSError:
                logger.warning("codex runtime: failed to prune %s", entry, exc_info=True)


def _cleanup_cancelled(target_dir: Path) -> None:
    """Drop partial files after a cancelled run. The READY marker needs no
    handling: it is written only after extraction fully succeeds, and a
    complete previous install short-circuits in ``controller.start`` before
    ``run`` is ever invoked."""
    for partial in target_dir.glob("*.partial"):
        partial.unlink(missing_ok=True)
