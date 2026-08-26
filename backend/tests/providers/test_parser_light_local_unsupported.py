"""Coverage for LightLocalParser's "cannot read this" path.

The regression being guarded: RTF source is ASCII, so ``.rtf`` slipped
through the unknown-extension UTF-8 fallback and got indexed as its own
markup — ``{\\rtf1\\ansi\\deff3...}`` stored in the knowledge base as if the
control words were prose. Silent, and worse than the "unsupported" answer a
binary ``.doc`` gets.

anydoc now converts RTF properly, so the invariant these tests hold is the
durable one: whatever the backend situation, control words must never reach
the caller as markdown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.integrations.parser_light_local import LightLocalParser

# A minimal but structurally real RTF document: control words, a font
# table, and one line of prose. Pure ASCII — ``read_text("utf-8")``
# decodes it without raising, which is exactly what defeated the strict
# decode guard.
_RTF_SOURCE = (
    r"{\rtf1\ansi\deff0"
    r"{\fonttbl{\f0\froman\fcharset0 Times New Roman;}}"
    r"\pard\f0\fs24 Hello from RTF.\par"
    r"}"
)


@pytest.fixture
def parser() -> LightLocalParser:
    return LightLocalParser()


def test_rtf_never_leaks_control_words(parser: LightLocalParser, tmp_path: Path) -> None:
    """The durable invariant, independent of which backend answers."""
    rtf_path = tmp_path / "doc.rtf"
    rtf_path.write_text(_RTF_SOURCE, encoding="utf-8")

    result = parser.parse_sync(str(rtf_path))

    assert "\\rtf1" not in result.markdown
    assert "fonttbl" not in result.markdown
    assert result.metadata["engine"] != "plain_text"


def test_rtf_is_converted_by_anydoc(parser: LightLocalParser, tmp_path: Path) -> None:
    """With anydoc present, RTF is a supported format rather than a
    politely-refused one — the prose comes through."""
    pytest.importorskip("anydoc")
    rtf_path = tmp_path / "doc.rtf"
    rtf_path.write_text(_RTF_SOURCE, encoding="utf-8")

    result = parser.parse_sync(str(rtf_path))

    assert result.metadata["engine"] == "anydoc"
    assert "Hello from RTF." in result.markdown


def test_office_failure_reports_the_reason_and_leaks_nothing(
    parser: LightLocalParser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """anydoc is the sole office backend, so a failure has nowhere to fall
    back to. It must still fail loudly and carry anydoc's own reason — never
    degrade into echoing the source, which is the mode that polluted the KB."""
    pytest.importorskip("anydoc")
    import anydoc

    rtf_path = tmp_path / "doc.rtf"
    rtf_path.write_text(_RTF_SOURCE, encoding="utf-8")

    def _boom(_path: str) -> str:
        raise RuntimeError("simulated anydoc failure")

    monkeypatch.setattr(anydoc, "to_markdown", _boom)

    result = parser._parse_sync_impl(str(rtf_path))

    assert "\\rtf1" not in result.markdown
    assert result.metadata["engine"] == "anydoc"
    assert result.metadata["error"] == "simulated anydoc failure"
    assert "simulated anydoc failure" in result.markdown


def test_binary_unknown_extension_still_unsupported(
    parser: LightLocalParser, tmp_path: Path
) -> None:
    """The pre-existing strict-decode guard is untouched."""
    blob = tmp_path / "payload.bin"
    blob.write_bytes(b"\x00\x01\x02\xff\xfe")

    result = parser.parse_sync(str(blob))

    assert result.metadata["engine"] == "none"
    assert result.metadata["error"] == "unsupported extension .bin"
    assert result.page_count == 0


def test_utf8_source_file_still_kept_verbatim(parser: LightLocalParser, tmp_path: Path) -> None:
    """The fallback must keep doing its job: an undeclared plain-text
    extension (source code) is still returned as-is, including CJK."""
    src = tmp_path / "script.py"
    src.write_text("# 计算估值\ndef pe(price, eps):\n    return price / eps\n", encoding="utf-8")

    result = parser.parse_sync(str(src))

    assert result.metadata["engine"] == "plain_text"
    assert "# 计算估值" in result.markdown
    assert "return price / eps" in result.markdown
    assert result.page_count == 1


def test_legacy_and_odf_formats_are_declared_supported(parser: LightLocalParser) -> None:
    """Formats that previously returned "*Unsupported file type*" from the
    office branch (or, for .rtf, raw source) are now first-class."""
    caps = parser.capabilities
    for ext in ("doc", "ppt", "xls", "odt", "ods", "odp", "rtf", "epub"):
        assert ext in caps, f".{ext} is not declared in LightLocalParser.capabilities"


def test_xlsx_numbers_survive_without_pandas_artifacts(
    parser: LightLocalParser, tmp_path: Path
) -> None:
    """The concrete quality win over MarkItDown: it routes sheets through
    pandas, so a large revenue cell renders as ``8.630000e+10`` and every
    empty cell as ``NaN`` — both reach the model verbatim."""
    pytest.importorskip("anydoc")
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    path = tmp_path / "模型.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "模型"
    sheet.append(["科目", "2025A", "备注"])
    sheet.append(["营业收入", 86300000000, None])
    workbook.save(str(path))

    result = parser.parse_sync(str(path))

    assert result.metadata["engine"] == "anydoc"
    assert "86300000000" in result.markdown
    assert "8.63" not in result.markdown  # scientific notation
    assert "NaN" not in result.markdown
    assert "营业收入" in result.markdown
