"""A rapidocr bundle whose READY marker outlives its model files must surface
as "needs setup" — never as a silent substitution.

The READY marker records that a download once *finished*, not that the bundle
is still usable. When a model file goes missing (manual delete, interrupted
cleanup, disk repair) the marker keeps claiming health, and the old code then
fell through to rapidocr's own auto-download: OCR came back up on rapidocr's
DEFAULT weights — a different model generation than the user authorized — while
the setup UI still reported the install as complete, so nothing ever prompted a
re-download.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from valuz_agent.modules.parser.setup_jobs.rapidocr import (
    MODEL_FILENAMES,
    REQUIRED_MODEL_FILENAMES,
    RapidOcrSetupJob,
)

_MARKER_TEXT = "timestamp=2026-01-01T00:00:00+00:00\nmodel_version=PP-OCRv6\n"


class _RecordingRapidOCR:
    """Stands in for the ``RapidOCR`` class; captures the ``params`` dict."""

    last_params: dict[str, Any] | None = None

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        type(self).last_params = params


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every ``parser_model_dir`` consumer at a throwaway directory."""
    from valuz_agent.infra import fs_registry as fs_mod

    target = tmp_path / "rapidocr-models"
    target.mkdir()
    monkeypatch.setattr(
        fs_mod.fs_registry, "parser_model_dir", lambda plugin, subkind: target, raising=True
    )
    _RecordingRapidOCR.last_params = None
    return target


def _write_complete_bundle(target: Path) -> None:
    (target / "READY").write_text(_MARKER_TEXT, encoding="utf-8")
    for name in REQUIRED_MODEL_FILENAMES:
        (target / name).write_text("x", encoding="utf-8")


def test_filenames_cover_every_downloaded_asset() -> None:
    """``MODEL_FILENAMES`` is the single source of truth the parser reads —
    it must name every file a usable bundle needs."""
    assert set(REQUIRED_MODEL_FILENAMES) == set(MODEL_FILENAMES.values())
    assert set(MODEL_FILENAMES) == {"det", "rec", "cls", "dict"}


def test_is_complete_true_for_a_whole_bundle(model_dir: Path) -> None:
    _write_complete_bundle(model_dir)
    assert RapidOcrSetupJob().is_complete() is True


@pytest.mark.parametrize("missing", REQUIRED_MODEL_FILENAMES)
def test_is_complete_false_when_any_model_file_is_gone(model_dir: Path, missing: str) -> None:
    """The marker alone must not vouch for the bundle — otherwise the setup UI
    shows "installed" for an install that cannot run."""
    _write_complete_bundle(model_dir)
    (model_dir / missing).unlink()

    assert RapidOcrSetupJob().is_complete() is False, (
        f"a bundle missing {missing!r} still reported complete"
    )


def test_stale_version_marker_still_reads_incomplete(model_dir: Path) -> None:
    """Pre-existing v4/v5 behaviour must survive the file check."""
    _write_complete_bundle(model_dir)
    (model_dir / "READY").write_text("timestamp=...\nmodel_version=PP-OCRv5\n", encoding="utf-8")

    assert RapidOcrSetupJob().is_complete() is False


def test_parse_image_reports_needs_setup_for_a_broken_bundle(model_dir: Path) -> None:
    """The user-visible half: a broken bundle routes to the setup prompt rather
    than silently OCR'ing with substituted models."""
    from valuz_agent.integrations.parser_light_local import LightLocalParser

    _write_complete_bundle(model_dir)
    (model_dir / MODEL_FILENAMES["det"]).unlink()

    result = LightLocalParser()._parse_image(Path("/nonexistent/image.png"))

    assert result.metadata.get("setup_id") == "rapidocr_models"
    assert result.metadata.get("error") == "rapidocr_models setup required"


def test_build_rapidocr_raises_instead_of_substituting(model_dir: Path) -> None:
    """The backstop for callers that bypass the gate: a broken bundle must
    raise, not quietly fall through to rapidocr's auto-download defaults."""
    from valuz_agent.integrations import parser_light_local as mod

    _write_complete_bundle(model_dir)
    (model_dir / MODEL_FILENAMES["rec"]).unlink()

    with pytest.raises(FileNotFoundError, match="incomplete"):
        mod._build_rapidocr(_RecordingRapidOCR)

    assert _RecordingRapidOCR.last_params is None, (
        "a broken bundle must not construct RapidOCR at all"
    )


def test_no_marker_still_allows_the_auto_download_path(model_dir: Path) -> None:
    """No bundle was ever authorized — keep the documented direct-test
    affordance, with the root still pinned away from the app bundle."""
    from valuz_agent.integrations import parser_light_local as mod

    mod._build_rapidocr(_RecordingRapidOCR)

    params = _RecordingRapidOCR.last_params
    assert params == {"Global.model_root_dir": str(model_dir)}


def test_complete_bundle_builds_on_the_authorized_weights(model_dir: Path) -> None:
    from valuz_agent.integrations import parser_light_local as mod

    _write_complete_bundle(model_dir)
    mod._build_rapidocr(_RecordingRapidOCR)

    params = _RecordingRapidOCR.last_params
    assert params is not None
    assert params["Det.model_path"] == str(model_dir / MODEL_FILENAMES["det"])
    assert params["Global.model_root_dir"] == str(model_dir)
