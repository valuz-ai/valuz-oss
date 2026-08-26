"""Regression guard: the PyInstaller spec must bundle the sibling top-level
packages the frozen build imports via ``sys.path``.

``valuz_agent`` / ``kernel`` / ``plugins`` are shipped as raw ``.py`` trees under
``_internal/`` (the entry script adds ``_internal`` to ``sys.path``). PyInstaller
collects them via explicit ``datas`` entries — its static analysis can't find
them on its own (``plugins.parser`` in particular is imported by a dynamic
``importlib.import_module`` string, so dropping its datas entry silently yields a
frozen build with ZERO parser plugins). These checks fail loudly if any entry is
removed, instead of waiting for a runtime ``ModuleNotFoundError`` in a packaged
build nobody runs in CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_SPEC = Path(__file__).resolve().parents[1] / "scripts" / "valuz_agent.spec"


@pytest.fixture(scope="module")
def spec_source() -> str:
    return _SPEC.read_text(encoding="utf-8")


@pytest.mark.parametrize("package", ["valuz_agent", "plugins", "kernel"])
def test_spec_bundles_sibling_package(spec_source: str, package: str) -> None:
    # The canonical raw-.py-via-sys.path datas entry: ``(str(HERE / "x"), "x")``.
    entry = f'(str(HERE / "{package}"), "{package}")'
    assert entry in spec_source, (
        f"PyInstaller spec no longer bundles the {package!r} package "
        f"(expected datas entry {entry!r}). The frozen build will fail to "
        f"import it."
    )


def test_spec_bundles_the_office_parser(spec_source: str) -> None:
    """``anydoc`` is the ONLY backend for every office/spreadsheet/ODF/RTF/EPUB
    format (LightLocal ``_OFFICE_EXTS``). It is imported lazily inside
    ``_parse_office``, so it is named explicitly rather than left to static
    analysis — dropping it yields a frozen build where every one of those
    formats returns "*anydoc not installed*" while PDF/text keep working.

    This replaces the former magika guard: magika existed only because
    ``MarkItDown.__init__`` loaded it eagerly, and a build that missed its
    package data shipped with all office parsing broken (PR #231). anydoc is a
    compiled extension with no data files, so that whole failure mode is gone.
    """
    assert '"anydoc"' in spec_source, (
        "PyInstaller spec no longer lists 'anydoc' in hiddenimports. The frozen "
        "build will fail every .docx/.xlsx/.pptx/.doc/.odt/.rtf/.epub parse."
    )


def test_spec_no_longer_carries_markitdown(spec_source: str) -> None:
    """The markitdown tail (markitdown, mammoth, magika, markdownify, openpyxl,
    lxml, et_xmlfile — and pandas transitively) was removed with it. Naming any
    of them again means either a real new requester or a stale copy-paste;
    either way it should be a deliberate edit, not a silent one."""
    for pkg in (
        "markitdown",
        "mammoth",
        "magika",
        "markdownify",
        "et_xmlfile",
        # Transitive passengers, uninstalled by the same `uv sync`.
        "pandas",
        "cobble",
        "defusedxml",
    ):
        assert f'"{pkg}"' not in spec_source, (
            f"PyInstaller spec names {pkg!r} again. It came in with markitdown, "
            f"which anydoc replaced — bundling it costs size for nothing unless "
            f"something genuinely imports it now."
        )


def test_spec_collects_rapidocr_yaml_data(spec_source: str) -> None:
    """rapidocr reads two YAMLs out of its own package dir by
    ``Path(__file__).parent`` — a path that doesn't exist on disk in a frozen
    build. Without them every image parse dies with ``FileNotFoundError:
    .../rapidocr/config.yaml``, including for users who completed the model
    download (the config load happens before ``params`` overrides merge)."""
    assert '"rapidocr": ["config.yaml", "default_models.yaml"]' in spec_source, (
        "PyInstaller spec no longer collects rapidocr's config.yaml / "
        "default_models.yaml. The frozen build will raise FileNotFoundError on "
        "every image OCR parse."
    )


def test_rapidocr_reads_exactly_the_yamls_the_spec_collects() -> None:
    """Tie the spec's include list to what rapidocr actually resolves, so a
    layout/rename upstream fails here instead of in a packaged build."""
    pytest.importorskip("rapidocr")
    from rapidocr.inference_engine.base import MODEL_URL_PATH
    from rapidocr.main import DEFAULT_CFG_PATH

    collected = {"config.yaml", "default_models.yaml"}
    assert DEFAULT_CFG_PATH.name in collected, (
        f"rapidocr's default config is now {DEFAULT_CFG_PATH.name!r}; update "
        f"_filtered_data_pkgs in the spec."
    )
    assert MODEL_URL_PATH.name in collected, (
        f"rapidocr's model manifest is now {MODEL_URL_PATH.name!r}; update "
        f"_filtered_data_pkgs in the spec."
    )


def test_rapidocr_yamls_are_collectable_without_the_onnx_weights() -> None:
    """The filtered collection must yield the two YAMLs and NOT the ~16 MB of
    bundled .onnx weights (those ship via the user-authorized model download)."""
    pytest.importorskip("PyInstaller")
    pytest.importorskip("rapidocr")
    from PyInstaller.utils.hooks import collect_data_files

    collected = collect_data_files(
        "rapidocr",
        include_py_files=False,
        includes=["config.yaml", "default_models.yaml"],
    )
    names = sorted(Path(src).name for src, _dest in collected)
    assert names == ["config.yaml", "default_models.yaml"], (
        f"rapidocr filtered data collection changed shape: {names}"
    )
