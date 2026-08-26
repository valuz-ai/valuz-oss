"""User-authorized download of PP-OCRv6 ONNX models for RapidOCR.

What we download
----------------
A Chinese/multilingual-capable RapidOCR pipeline needs three ONNX
models plus the recognition character dictionary:

- ``det``  — text detection         (PP-OCRv6_medium, PaddlePaddle official ONNX)
- ``rec``  — recognition            (PP-OCRv6_medium, PaddlePaddle official ONNX)
- ``cls``  — orientation classifier (PP-LCNet_x1_0_textline_ori, PaddlePaddle official ONNX)
- dict      — rec character map      (extracted from the rec ``inference.yml``)

All three ONNX models come from PaddlePaddle's own exports on ModelScope
(``PaddlePaddle/PP-OCRv6_medium_{det,rec}_onnx`` +
``PaddlePaddle/PP-LCNet_x1_0_textline_ori_onnx``), each a single-model
repo with a flat ``inference.onnx`` + ``inference.yml`` layout — so we no
longer depend on the RapidAI mirror at all. Unlike PP-OCRv5, the rec
dictionary is NOT shipped as a standalone file — it is embedded in the
rec model's ``inference.yml`` under ``PostProcess.character_dict``
(18,708 entries, 50 languages). We download that yaml and materialize
``ppocrv6_dict.txt`` from it.

The orientation classifier is loaded at construction but never invoked at
inference (its 80×160 fixed input shape doesn't match rapidocr's Cls
preprocessing — see ``parser_light_local``); it is still downloaded
because rapidocr loads ``Cls.model_path`` eagerly.

Size note: PP-OCRv6 ``medium`` is the mid accuracy tier and is much
heavier than the old PP-OCRv5 ``mobile`` bundle — det ~62MB + rec ~77MB
+ cls ~7MB ≈ ~145MB total (vs ~20MB before). The three sizes are summed
independently for a single aggregate progress bar. The models are
Apache 2.0 and may be redistributed.

Where they land
---------------
``~/.valuz-oss/models/light_local/rapidocr/`` (resolved via
``fs_registry.parser_model_dir``). When the job succeeds it also writes:

- ``LICENSE`` — Apache 2.0 license text (so the bundle is self-contained
  legally if a user inspects the directory)
- ``READY`` — a small marker file holding the ISO-format completion
  timestamp plus a ``model_version=PP-OCRv6`` line. ``is_complete()``
  parses the marker so a previous v4/v5 directory is treated as
  "needs setup", forcing a re-download of the v6 bundle.

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
import yaml

from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.parser.setup_jobs._apache_license import APACHE_LICENSE_2_0
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


# All three models are PaddlePaddle's own official ONNX exports on
# ModelScope. These single-model repos carry no release tags, so we pin
# ``master``; each has a flat ``inference.onnx`` + ``inference.yml``
# layout. ModelScope is generally faster than HuggingFace from inside
# China; outside-China users can still reach it.
_PADDLE_BASE = "https://www.modelscope.cn/models/PaddlePaddle"
_V6_DET_REPO = f"{_PADDLE_BASE}/PP-OCRv6_medium_det_onnx/resolve/master"
_V6_REC_REPO = f"{_PADDLE_BASE}/PP-OCRv6_medium_rec_onnx/resolve/master"
_CLS_REPO = f"{_PADDLE_BASE}/PP-LCNet_x1_0_textline_ori_onnx/resolve/master"

_DET_FILENAME = "PP-OCRv6_medium_det.onnx"
_REC_FILENAME = "PP-OCRv6_medium_rec.onnx"
_CLS_FILENAME = "PP-LCNet_x1_0_textline_ori.onnx"
_DICT_FILENAME = "ppocrv6_dict.txt"

_ASSETS: tuple[_ModelAsset, ...] = (
    _ModelAsset(
        url=f"{_V6_DET_REPO}/inference.onnx",
        filename=_DET_FILENAME,
    ),
    _ModelAsset(
        url=f"{_V6_REC_REPO}/inference.onnx",
        filename=_REC_FILENAME,
    ),
    _ModelAsset(
        url=f"{_CLS_REPO}/inference.onnx",
        filename=_CLS_FILENAME,
    ),
)

# The rec dict is embedded in the rec model's ``inference.yml`` (PaddleOCR
# stopped shipping a standalone dict file in v6). We fetch this yaml after
# the binary downloads and write ``ppocrv6_dict.txt`` from
# ``PostProcess.character_dict``.
_REC_DICT_YML_URL = f"{_V6_REC_REPO}/inference.yml"

# The bundle's on-disk layout, by role — the single source of truth for both
# this job (which writes it) and the parser integration (which loads it). The
# parser used to hardcode the same four basenames; the v4→v5→v6 cutovers each
# renamed them, so a second copy is a live drift hazard.
MODEL_FILENAMES: dict[str, str] = {
    "det": _DET_FILENAME,
    "rec": _REC_FILENAME,
    "cls": _CLS_FILENAME,
    "dict": _DICT_FILENAME,
}

# Every file a usable bundle must contain. ``is_complete`` checks presence of
# ALL of them, not just the READY marker: the marker records *that a download
# finished*, which stops being true the moment a file is deleted or a partial
# write is left behind. A marker outliving its files used to read as "complete"
# forever — the user was never re-prompted, and the parser silently fell back to
# a DIFFERENT model generation (rapidocr's own auto-download defaults).
REQUIRED_MODEL_FILENAMES: tuple[str, ...] = tuple(MODEL_FILENAMES.values())

_READY_MARKER = "READY"
_MODEL_VERSION = "PP-OCRv6"
_LICENSE_FILE = "LICENSE"
# Attribution NOTICE prepended to the bundled Apache 2.0 text. Satisfies
# Apache 2.0 §4(a) — the on-disk ``LICENSE`` carries the full license text
# verbatim (not just a link), plus per-component copyright attribution.
_LICENSE_NOTICE = """\
THIRD-PARTY MODEL & ENGINE NOTICE
=================================

OCR models — text detection (PP-OCRv6_medium), recognition
(PP-OCRv6_medium) and text-line orientation (PP-LCNet_x1_0_textline_ori):
Copyright PaddlePaddle Authors. Licensed under Apache License 2.0.
Redistributed unmodified as PaddlePaddle's official ONNX exports via
ModelScope. https://github.com/PaddlePaddle/PaddleOCR

Inference engine — rapidocr (RapidAI): Copyright RapidAI Maintainers.
Licensed under Apache License 2.0. Used as the runtime library only; the
models above are PaddlePaddle's own ONNX exports, not RapidAI-repackaged.
https://github.com/RapidAI/RapidOCR

The complete Apache License 2.0 text under which all of the above are
licensed follows below.
"""

_LICENSE_TEXT = f"{_LICENSE_NOTICE}\n{'=' * 70}\n\n{APACHE_LICENSE_2_0}\n"


class RapidOcrSetupJob:
    """Sequentially streams the PP-OCRv6 ONNX bundle into the local
    model directory, with progress updates and cooperative cancellation."""

    setup_id: str = RAPIDOCR_SETUP_ID

    # PP-OCRv6 medium bundle sizes (measured against ModelScope):
    # det ~62MB, rec ~77MB, cls ~7MB → ~145MB. The rec dict is a tiny
    # text file materialized from the rec yaml (not counted here). Round
    # up to 150MB for the authorization dialog so we don't under-promise;
    # the real number lands via _compute_total_bytes.
    declared_size_bytes: int = 150 * 1024 * 1024

    def model_dir(self) -> Path:
        return fs_registry.parser_model_dir(PLUGIN_ID, SUBKIND)

    def is_complete(self) -> bool:
        """Return True iff the READY marker exists, was written by a v6 run,
        AND every model file it vouches for is still on disk.

        A stale v4/v5 READY marker (no ``model_version=`` line, or a different
        version string) reads as "not complete" so the first boot after the
        upgrade auto-prompts the user to re-download.

        The file check matters just as much: the marker records that a download
        once finished, not that the bundle is still usable. A marker outliving
        its files (manual delete, interrupted cleanup, disk repair) used to read
        as complete forever — the setup UI never re-prompted, and the parser
        quietly fell back to auto-downloading rapidocr's OWN default weights, so
        OCR ran on a different model generation than the user authorized. An
        incomplete bundle must surface as "needs setup" instead.
        """
        model_dir = self.model_dir()
        marker = model_dir / _READY_MARKER
        if not marker.exists():
            return False
        try:
            content = marker.read_text(encoding="utf-8")
        except OSError:
            return False
        if f"model_version={_MODEL_VERSION}" not in content:
            return False
        return all((model_dir / name).exists() for name in REQUIRED_MODEL_FILENAMES)

    # ----- SetupJob.run ----------------------------------------------

    def run(
        self,
        *,
        progress_cb: ProgressCallback,
        cancel_event: threading.Event,
    ) -> None:
        target_dir = self.model_dir()
        target_dir.mkdir(parents=True, exist_ok=True)

        # One-time prune of v4/v5 artefacts: if a stale READY marker
        # without our v6 ``model_version`` line sits next to older
        # filenames, drop them before downloading so we don't accumulate
        # multiple model bundles. is_complete() returned False for this
        # path (different marker content), so we're already on the
        # re-download branch.
        _prune_legacy_files(target_dir)

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

            # Materialize the rec character dictionary from the rec
            # model's inference.yml (v6 no longer ships a standalone dict
            # file). Done after the binary downloads so a cancelled run
            # never leaves a dict orphaned from its models.
            _materialize_rec_dict(target_dir, cancel_event)
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


def _asset_size(client: httpx.Client, url: str) -> int | None:
    """Learn one asset's byte size. Prefer a cheap HEAD ``content-length``;
    fall back to a 1-byte ranged GET and read the total from
    ``content-range`` (``bytes 0-0/<total>``).

    ModelScope's ``resolve`` endpoint answers HEAD with ``200`` but no
    ``content-length``, so HEAD alone yields nothing for these repos — the
    ranged-GET path is what actually produces a number."""
    head = client.head(url)
    head.raise_for_status()
    size = head.headers.get("content-length")
    if size is not None:
        return int(size)

    ranged = client.get(url, headers={"Range": "bytes=0-0"})
    ranged.raise_for_status()
    content_range = ranged.headers.get("content-range")  # "bytes 0-0/<total>"
    if content_range and "/" in content_range:
        tail = content_range.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    return None


def _compute_total_bytes(assets: tuple[_ModelAsset, ...]) -> int | None:
    """Sum each asset's size for a deterministic progress bar. Falls back
    to None on any failure (or any single unknown size) so the UI renders
    an indeterminate spinner instead of a wrong number."""
    total = 0
    try:
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            for asset in assets:
                size = _asset_size(client, asset.url)
                if size is None:
                    return None
                total += size
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


def _materialize_rec_dict(target_dir: Path, cancel_event: threading.Event) -> None:
    """Download the rec model's ``inference.yml`` and write the embedded
    character dictionary to ``ppocrv6_dict.txt`` (one token per line).

    PP-OCRv6 dropped the standalone dict file; the 18,708-entry
    multilingual dictionary lives under ``PostProcess.character_dict`` in
    the rec model's yaml. rapidocr's ``Rec.rec_keys_path`` expects the
    classic PaddleOCR dict format (one token per line), which is exactly
    ``"\\n".join(character_dict)``.
    """
    if cancel_event.is_set():
        return
    final = target_dir / _DICT_FILENAME
    partial = target_dir / f"{_DICT_FILENAME}.partial"
    if partial.exists():
        partial.unlink()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True) as client:
        resp = client.get(_REC_DICT_YML_URL)
        resp.raise_for_status()
        spec = yaml.safe_load(resp.text)

    chars = (spec or {}).get("PostProcess", {}).get("character_dict")
    if not isinstance(chars, list) or not chars:
        raise ValueError(
            f"rec inference.yml missing PostProcess.character_dict (got {type(chars).__name__})"
        )
    if any(not isinstance(tok, str) for tok in chars):
        raise ValueError("rec character_dict contains a non-string token")

    partial.write_text("\n".join(chars) + "\n", encoding="utf-8")
    partial.rename(final)


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


_LEGACY_FILENAMES: tuple[str, ...] = (
    # PP-OCRv4
    "ch_PP-OCRv4_det_infer.onnx",
    "ch_ppocr_mobile_v2.0_cls_infer.onnx",
    "ch_PP-OCRv4_rec_infer.onnx",
    "ppocr_keys_v1.txt",
    # PP-OCRv5 — incl. the old RapidAI-mirror orientation classifier,
    # now superseded by PaddlePaddle's PP-LCNet_x1_0_textline_ori.
    "ch_PP-OCRv5_det_mobile.onnx",
    "ch_PP-OCRv5_rec_mobile.onnx",
    "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx",
    "ppocrv5_dict.txt",
)


def _prune_legacy_files(target_dir: Path) -> None:
    """Delete leftover PP-OCRv4 / PP-OCRv5 model files from a previous
    install so the directory ends up with v6 artefacts only.

    The det / rec / cls / dict filenames all changed for the PP-OCRv6
    bundle, so without this prune the directory would accumulate dead
    models. Safe to call on a fresh dir (the legacy filenames just won't
    exist).
    """
    if not target_dir.exists():
        return
    pruned: list[str] = []
    for name in _LEGACY_FILENAMES:
        legacy = target_dir / name
        if legacy.exists():
            try:
                legacy.unlink()
                pruned.append(name)
            except OSError as exc:
                logger.warning("failed to prune legacy file %s: %s", legacy, exc)
    if pruned:
        logger.info(
            "rapidocr: pruned %d legacy PP-OCRv4/v5 file(s) before v6 download: %s",
            len(pruned),
            pruned,
        )


def reset_local_state() -> None:
    """Test helper: wipe the rapidocr model directory so the next run
    triggers a full re-download. Not exposed via HTTP."""
    target = fs_registry.parser_model_dir(PLUGIN_ID, SUBKIND)
    if target.exists():
        shutil.rmtree(target)
