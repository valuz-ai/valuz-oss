"""User-authorized download of PP-OCRv5 ONNX models for RapidOCR.

What we download
----------------
RapidOCR ships three ONNX files for a Chinese-capable pipeline:

- ``det``  — text detection
- ``cls``  — orientation classifier
- ``rec``  — recognition (Chinese + English)

Plus the recognition character dictionary (``ppocrv5_dict.txt``). All
files come from the RapidAI-maintained ModelScope mirror at
``https://www.modelscope.cn/models/RapidAI/RapidOCR/``. The ONNX
bundles are derived from PaddleOCR PP-OCRv5 (Apache 2.0) and may be
redistributed.

Mobile variants are picked over server: ~20MB total vs ~100MB+, with
acceptable accuracy for desktop-class CPU inference. Users who need
server-grade accuracy can override via a future provider setting; that
is out of scope for the bundled bootstrap.

Where they land
---------------
``~/.valuz/app/models/light_local/rapidocr/`` (resolved via
``fs_registry.parser_model_dir``). When the job succeeds it also writes:

- ``LICENSE`` — Apache 2.0 license text (so the bundle is self-contained
  legally if a user inspects the directory)
- ``READY`` — a small marker file holding the ISO-format completion
  timestamp plus a ``model_version=PP-OCRv5`` line. ``is_complete()``
  parses the marker so a previous v4 directory is treated as
  "needs setup", forcing a re-download of the v5 bundle.

Authorization contract
----------------------
The job class itself does NOT check ``accept_license`` — that gate lives
at the HTTP endpoint. By the time the controller invokes ``run`` the
endpoint has already verified the user POSTed
``{accept_license: true, confirmed_source: ...}``.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.parser.setup_jobs.base import ProgressCallback

logger = logging.getLogger(__name__)

RAPIDOCR_SETUP_ID = "rapidocr_models"

PLUGIN_ID = "light_local"
SUBKIND = "rapidocr"


@dataclass(frozen=True)
class _ModelAsset:
    """One file to download. ``filename`` is the destination basename
    inside ``parser_model_dir``."""

    url: str
    filename: str


# Mirror: RapidAI/RapidOCR on ModelScope, pinned to git tag ``v3.8.0``
# which matches rapidocr>=3.3 model URLs. ModelScope is generally
# faster than HuggingFace from inside China; outside-China users can
# still reach it. Pinning a stable tag (not ``master``) means a model
# layout change upstream doesn't silently break our downloader.
_MODELSCOPE_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.8.0"

_ASSETS: tuple[_ModelAsset, ...] = (
    _ModelAsset(
        url=f"{_MODELSCOPE_BASE}/onnx/PP-OCRv5/det/ch_PP-OCRv5_det_mobile.onnx",
        filename="ch_PP-OCRv5_det_mobile.onnx",
    ),
    _ModelAsset(
        url=(
            f"{_MODELSCOPE_BASE}/onnx/PP-OCRv5/cls/ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx"
        ),
        filename="ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx",
    ),
    _ModelAsset(
        url=f"{_MODELSCOPE_BASE}/onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile.onnx",
        filename="ch_PP-OCRv5_rec_mobile.onnx",
    ),
    _ModelAsset(
        # rec dict lives under the ``paddle/`` tree (shared across
        # paddle / onnx / torch backends — it's just the character
        # mapping, not a model file).
        url=(f"{_MODELSCOPE_BASE}/paddle/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile/ppocrv5_dict.txt"),
        filename="ppocrv5_dict.txt",
    ),
)

_READY_MARKER = "READY"
_MODEL_VERSION = "PP-OCRv5"
_LICENSE_FILE = "LICENSE"
_LICENSE_TEXT = """Apache License 2.0

Models (PP-OCRv5): Copyright PaddlePaddle Authors. Licensed under
Apache License 2.0. https://github.com/PaddlePaddle/PaddleOCR

Packaging / engineering (rapidocr, ONNX repackaging):
Copyright RapidAI Maintainers. Licensed under Apache License 2.0.
https://github.com/RapidAI/RapidOCR

Full license text:
https://www.apache.org/licenses/LICENSE-2.0.txt
"""


class RapidOcrSetupJob:
    """Sequentially streams the PP-OCRv5 ONNX bundle into the local
    model directory, with progress updates and cooperative cancellation."""

    setup_id: str = RAPIDOCR_SETUP_ID

    # PP-OCRv5 mobile bundle sizes (HEAD-measured against ModelScope
    # v3.8.0): det ~5MB, cls ~0.6MB, rec ~14MB, dict ~0.05MB → ~20MB.
    # Round up to 24MB for the authorization dialog so we don't
    # under-promise; the real number lands via _compute_total_bytes.
    declared_size_bytes: int = 24 * 1024 * 1024

    def model_dir(self) -> Path:
        return fs_registry.parser_model_dir(PLUGIN_ID, SUBKIND)

    def is_complete(self) -> bool:
        """Return True iff the READY marker exists AND was written by a
        v5 run. A stale v4 READY marker (no ``model_version=`` line, or
        a different version string) reads as "not complete" so the
        first boot after the v4→v5 upgrade auto-prompts the user to
        re-download.
        """
        marker = self.model_dir() / _READY_MARKER
        if not marker.exists():
            return False
        try:
            content = marker.read_text(encoding="utf-8")
        except OSError:
            return False
        return f"model_version={_MODEL_VERSION}" in content

    # ----- SetupJob.run ----------------------------------------------

    def run(
        self,
        *,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        target_dir = self.model_dir()
        target_dir.mkdir(parents=True, exist_ok=True)

        # One-time prune of v4 artefacts: if a stale READY marker
        # without our v5 ``model_version`` line sits next to the v4
        # filenames, wipe everything before downloading so we don't
        # accumulate two model bundles. is_complete() returned False
        # for this path (different marker content), so we're already
        # on the re-download branch.
        _prune_legacy_v4_files(target_dir)

        # Phase 1: HEAD all files to compute the real total so the UI can
        # render a deterministic progress bar (vs. our conservative
        # declared_size_bytes). 4 round-trips, no body — cheap.
        total_bytes = _compute_total_bytes(_ASSETS)
        progress_cb(0, total_bytes)

        downloaded = 0

        try:
            for asset in _ASSETS:
                if cancel_event.is_set():
                    return
                downloaded = _download_one(
                    asset=asset,
                    target_dir=target_dir,
                    base_downloaded=downloaded,
                    total_bytes=total_bytes,
                    cancel_event=cancel_event,
                    progress_cb=progress_cb,
                )

            if cancel_event.is_set():
                return

            # Commit license + ready marker last so a partial run never
            # leaves the directory looking "ready". READY content
            # embeds ``model_version=`` so is_complete() can tell a v5
            # marker apart from a leftover v4 one.
            (target_dir / _LICENSE_FILE).write_text(_LICENSE_TEXT, encoding="utf-8")
            marker_text = (
                f"timestamp={datetime.now(UTC).isoformat()}\nmodel_version={_MODEL_VERSION}\n"
            )
            (target_dir / _READY_MARKER).write_text(marker_text, encoding="utf-8")
        finally:
            if cancel_event.is_set():
                _cleanup_partials(target_dir)


# ----- helpers --------------------------------------------------------


def _compute_total_bytes(assets: tuple[_ModelAsset, ...]) -> int | None:
    """HEAD each asset to learn its size. Falls back to None on any
    failure so the UI can render an indeterminate spinner instead of a
    wrong number."""
    total = 0
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for asset in assets:
                resp = client.head(asset.url)
                resp.raise_for_status()
                size = resp.headers.get("content-length")
                if size is None:
                    return None
                total += int(size)
    except (httpx.HTTPError, ValueError):
        return None
    return total


def _download_one(
    *,
    asset: _ModelAsset,
    target_dir: Path,
    base_downloaded: int,
    total_bytes: int | None,
    cancel_event: threading.Event,
    progress_cb: ProgressCallback,
) -> int:
    """Stream one file into ``target_dir/asset.filename`` with progress
    updates. Returns the new cumulative ``downloaded`` count."""
    final = target_dir / asset.filename
    partial = target_dir / f"{asset.filename}.partial"

    # If the user re-runs after a previous success on this file (rare —
    # is_complete() short-circuits earlier), skip the network.
    if final.exists() and not partial.exists():
        return base_downloaded + final.stat().st_size

    # Best-effort cleanup of stale partial files from a prior cancelled run.
    if partial.exists():
        partial.unlink()

    cumulative = base_downloaded
    last_publish_ts = 0.0
    publish_interval_s = 0.5

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True) as client:
        with client.stream("GET", asset.url) as resp:
            resp.raise_for_status()
            with partial.open("wb") as fh:
                for chunk in _iter_chunks(resp):
                    if cancel_event.is_set():
                        return cumulative
                    fh.write(chunk)
                    cumulative += len(chunk)
                    now = time.monotonic()
                    if now - last_publish_ts >= publish_interval_s:
                        progress_cb(cumulative, total_bytes)
                        last_publish_ts = now

    # Final progress write so the UI can settle to the real number.
    progress_cb(cumulative, total_bytes)
    partial.rename(final)
    return cumulative


def _iter_chunks(resp: httpx.Response, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
    yield from resp.iter_bytes(chunk_size=chunk_size)


def _cleanup_partials(target_dir: Path) -> None:
    """Remove ``*.partial`` files left by a cancelled run. We deliberately
    leave fully-renamed files in place — they are useful on a future
    resume even though we don't implement resume yet."""
    if not target_dir.exists():
        return
    for partial in target_dir.glob("*.partial"):
        try:
            partial.unlink()
        except OSError:
            logger.warning("failed to clean partial file %s", partial)
            continue
    # Also remove any half-written READY marker (shouldn't exist on a
    # cancellation path, but be paranoid).
    marker = target_dir / _READY_MARKER
    if marker.exists():
        try:
            marker.unlink()
        except OSError:
            pass


_LEGACY_V4_FILENAMES: tuple[str, ...] = (
    "ch_PP-OCRv4_det_infer.onnx",
    "ch_ppocr_mobile_v2.0_cls_infer.onnx",
    "ch_PP-OCRv4_rec_infer.onnx",
    "ppocr_keys_v1.txt",
)


def _prune_legacy_v4_files(target_dir: Path) -> None:
    """Delete leftover PP-OCRv4 files from a previous install so the
    directory ends up with v5 artefacts only.

    The v5 filenames are entirely different from v4 (det / cls / rec
    all changed; even the dict went from ``ppocr_keys_v1.txt`` to
    ``ppocrv5_dict.txt``), so without this prune the directory would
    accumulate ~16MB of dead v4 files. Safe to call on a fresh dir
    (the legacy filenames just won't exist).
    """
    if not target_dir.exists():
        return
    pruned: list[str] = []
    for name in _LEGACY_V4_FILENAMES:
        legacy = target_dir / name
        if legacy.exists():
            try:
                legacy.unlink()
                pruned.append(name)
            except OSError as exc:
                logger.warning("failed to prune legacy v4 file %s: %s", legacy, exc)
    if pruned:
        logger.info(
            "rapidocr: pruned %d legacy PP-OCRv4 file(s) before v5 download: %s",
            len(pruned),
            pruned,
        )


def reset_local_state() -> None:
    """Test helper: wipe the rapidocr model directory so the next run
    triggers a full re-download. Not exposed via HTTP."""
    target = fs_registry.parser_model_dir(PLUGIN_ID, SUBKIND)
    if target.exists():
        shutil.rmtree(target)
