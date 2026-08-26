"""``_build_rapidocr`` must pin rapidocr's model ROOT at our own download dir.

rapidocr defaults ``Global.model_root_dir`` to ``<its own package>/models``
(``RapidOCR._load_config``), and every engine session asserts that directory
exists *before* it looks at the explicit ``model_path`` values::

    model_root_dir = Path(cfg.get("model_root_dir"))
    if not model_root_dir.exists():
        raise FileNotFoundError(f"model_root_dir {model_root_dir} does not exist")

In the PyInstaller build that package directory does not exist — rapidocr's
``.py`` live in the PYZ archive and we deliberately don't ship its ~16 MB of
bundled weights — so image OCR died on the root-dir assert even for users who
had completed the model download. Source runs passed only because
site-packages happens to have the directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


class _RecordingRapidOCR:
    """Stands in for the ``RapidOCR`` class; captures the ``params`` dict."""

    last_params: dict[str, Any] | None = None

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        type(self).last_params = params


@pytest.fixture
def model_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``parser_model_dir`` at a throwaway dir — hermetic regardless of
    the session-wide data-dir fence in conftest."""
    from valuz_agent.infra.fs_registry import fs_registry

    target = tmp_path / "rapidocr-models"
    target.mkdir()
    monkeypatch.setattr(
        fs_registry, "parser_model_dir", lambda plugin, subkind: target, raising=True
    )
    _RecordingRapidOCR.last_params = None
    return target


def _write_authorized_bundle(target: Path) -> None:
    for name in (
        "READY",
        "PP-OCRv6_medium_det.onnx",
        "PP-LCNet_x1_0_textline_ori.onnx",
        "PP-OCRv6_medium_rec.onnx",
        "ppocrv6_dict.txt",
    ):
        (target / name).write_text("x", encoding="utf-8")


def test_authorized_bundle_pins_model_root_dir(model_dir: Path) -> None:
    from valuz_agent.integrations import parser_light_local as mod

    _write_authorized_bundle(model_dir)
    mod._build_rapidocr(_RecordingRapidOCR)

    params = _RecordingRapidOCR.last_params
    assert params is not None, "authorized branch must pass a params dict"
    assert params["Global.model_root_dir"] == str(model_dir), (
        "model_root_dir must point at our download dir, not rapidocr's package "
        "dir (which does not exist in the frozen build)"
    )
    # Pinning the root must not displace the explicit weights — rapidocr
    # resolves ``model_path`` independently of ``model_root_dir``.
    assert params["Det.model_path"].endswith("PP-OCRv6_medium_det.onnx")
    assert params["Rec.model_path"].endswith("PP-OCRv6_medium_rec.onnx")


def test_fallback_also_pins_model_root_dir(model_dir: Path) -> None:
    """No READY marker → rapidocr's auto-download path. That must not target
    rapidocr's package dir either: in a packaged app it would mean downloading
    weights INTO the signed app bundle."""
    from valuz_agent.integrations import parser_light_local as mod

    mod._build_rapidocr(_RecordingRapidOCR)  # dir is empty — no marker

    params = _RecordingRapidOCR.last_params
    assert params is not None, "fallback must still pin the model root"
    assert params["Global.model_root_dir"] == str(model_dir)
    assert "Det.model_path" not in params, "fallback must not fake weight paths"


def test_rapidocr_still_defaults_the_root_into_its_own_package() -> None:
    """Guard the upstream assumption this whole fix rests on: with no explicit
    ``Global.model_root_dir``, rapidocr resolves it inside its own package."""
    pytest.importorskip("rapidocr")
    import rapidocr
    from rapidocr.main import RapidOCR

    inst = object.__new__(RapidOCR)
    cfg = RapidOCR._load_config(inst, None, {"Det.model_path": "/tmp/x.onnx"})
    pkg_dir = Path(rapidocr.__file__).resolve().parent
    assert Path(cfg.Global.model_root_dir) == pkg_dir / "models", (
        "rapidocr changed how it defaults model_root_dir; re-check whether "
        "pinning it in _build_rapidocr is still required/sufficient"
    )
