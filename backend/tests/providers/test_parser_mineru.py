"""Coverage for ``MineruPollingHandler`` pure helpers (PR-3).

The HTTP-going methods (``submit`` / ``poll`` / ``fetch_result``) are
verified manually during the PR-3 E2E with real tokens against
``mineru.net``. The unit tests here pin only the pieces that are pure
functions of inputs:

- ``_model_version_for`` extension → model_version mapping
- ``_extract_markdown_from_zip`` ZIP shape handling

These two helpers carry the locked decisions from the plan; if they
drift the user-visible behavior changes silently.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from plugins.parser.mineru.handler import (
    _extract_markdown_from_zip,
    _model_version_for,
)


def _make_zip(markdown: str = "# hi\nhello") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", markdown)
        zf.writestr("images/placeholder.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


# ── _model_version_for ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("report.pdf", "vlm"),
        ("photo.png", "vlm"),
        ("sheet.xlsx", "vlm"),
        ("page.html", "MinerU-HTML"),
        ("legacy.HTM", "MinerU-HTML"),
    ],
)
def test_model_version_locked_per_extension(filename: str, expected: str) -> None:
    assert _model_version_for(Path(filename)) == expected


# ── _extract_markdown_from_zip ─────────────────────────────────


def test_zip_extracts_full_md() -> None:
    md, pages = _extract_markdown_from_zip(_make_zip("# A\n\n# B"))
    assert md.startswith("# A")
    assert pages >= 1


def test_zip_ignores_images_subdir() -> None:
    md, _ = _extract_markdown_from_zip(_make_zip("# only text"))
    assert "PNG" not in md  # PNG bytes from images/ must not leak into md
    assert md.startswith("# only text")


def test_zip_with_renamed_md_falls_back_to_first_md() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("results/something_else.md", "# only")
    md, pages = _extract_markdown_from_zip(buf.getvalue())
    assert md == "# only"
    assert pages >= 1


def test_zip_without_any_md_raises() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "no md here")
    with pytest.raises(RuntimeError, match="no markdown"):
        _extract_markdown_from_zip(buf.getvalue())
