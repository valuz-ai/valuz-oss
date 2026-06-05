"""Coverage for the generic PDF splitter."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pymupdf
import pytest

from valuz_agent.modules.parser.splitter import (
    SplitPolicy,
    merge_parse_results,
    pdf_page_count,
    should_split,
    split_and_parse,
    split_and_parse_async,
    split_pdf_by_pages,
)
from valuz_agent.ports.parser_backend import ParseResult


def _make_pdf(path: Path, pages: int) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"Page {i + 1}", fontsize=12)
    doc.save(str(path))
    doc.close()


# ── pdf_page_count ──────────────────────────────────────────────


def test_pdf_page_count_returns_count_for_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 5)
    assert pdf_page_count(pdf) == 5


def test_pdf_page_count_returns_none_for_non_pdf(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("hi", encoding="utf-8")
    assert pdf_page_count(txt) is None


# ── should_split ────────────────────────────────────────────────


def test_should_split_false_when_no_limit(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 500)
    assert should_split(pdf, SplitPolicy()) is False  # max_pages=None


def test_should_split_false_when_under_limit(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 50)
    assert should_split(pdf, SplitPolicy(max_pages=100)) is False


def test_should_split_true_when_over_limit(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 150)
    assert should_split(pdf, SplitPolicy(max_pages=100)) is True


def test_should_split_false_for_non_pdf_even_above_limit(tmp_path: Path) -> None:
    """Splitting non-PDF files isn't supported — return False so the
    plugin parses the file directly. Page-count semantics don't apply."""
    txt = tmp_path / "big.txt"
    txt.write_text("x" * 10_000, encoding="utf-8")
    assert should_split(txt, SplitPolicy(max_pages=1)) is False


# ── split_pdf_by_pages ──────────────────────────────────────────


def test_split_pdf_by_pages_produces_chunks(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 7)
    out = tmp_path / "parts"
    parts = split_pdf_by_pages(pdf, max_pages=3, out_dir=out)
    # 7 pages, 3 per part → ceil(7/3) = 3 parts
    assert len(parts) == 3
    # Validate each part has at most 3 pages
    sizes = [pdf_page_count(p) for p in parts]
    assert sizes == [3, 3, 1]


def test_split_pdf_by_pages_below_limit_returns_single_copy(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 4)
    out = tmp_path / "parts"
    parts = split_pdf_by_pages(pdf, max_pages=10, out_dir=out)
    assert len(parts) == 1
    # The single returned path is in the out_dir (not the original) so
    # callers can uniformly clean up by removing the dir.
    assert parts[0].parent == out


def test_split_pdf_by_pages_rejects_invalid_max_pages(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf, 2)
    with pytest.raises(ValueError, match="max_pages must be >= 1"):
        split_pdf_by_pages(pdf, max_pages=0)


# ── merge_parse_results ─────────────────────────────────────────


def test_merge_empty_returns_blank() -> None:
    r = merge_parse_results([])
    assert r.markdown == ""
    assert r.page_count == 0


def test_merge_single_returns_input() -> None:
    one = ParseResult(markdown="hello", page_count=2, metadata={"engine": "x"})
    assert merge_parse_results([one]) is one


def test_merge_concatenates_markdown_and_sums_pages() -> None:
    a = ParseResult(markdown="# A", page_count=3, metadata={"engine": "mineru"})
    b = ParseResult(markdown="# B", page_count=4, metadata={"engine": "mineru"})
    r = merge_parse_results([a, b])
    assert "# A" in r.markdown and "# B" in r.markdown
    assert r.page_count == 7
    assert r.metadata["engine"] == "mineru"
    assert r.metadata["parts"] == "2"


# ── split_and_parse ─────────────────────────────────────────────


def test_split_and_parse_skips_split_when_under_limit(tmp_path: Path) -> None:
    pdf = tmp_path / "small.pdf"
    _make_pdf(pdf, 5)
    calls: list[Path] = []

    def _fake_parse(p: Path) -> ParseResult:
        calls.append(p)
        return ParseResult(markdown="ok", page_count=5, metadata={"engine": "fake"})

    r = split_and_parse(pdf, SplitPolicy(max_pages=10), _fake_parse)
    assert len(calls) == 1
    assert calls[0] == pdf
    assert r.markdown == "ok"


def test_split_and_parse_splits_and_merges_when_over_limit(tmp_path: Path) -> None:
    pdf = tmp_path / "big.pdf"
    _make_pdf(pdf, 10)
    parsed_pages: list[int] = []

    def _fake_parse(p: Path) -> ParseResult:
        pc = pdf_page_count(p) or 0
        parsed_pages.append(pc)
        return ParseResult(markdown=f"part-{pc}", page_count=pc, metadata={"engine": "fake"})

    r = split_and_parse(pdf, SplitPolicy(max_pages=4), _fake_parse)
    # 10 pages / 4 per part → 4 + 4 + 2 = 3 parts
    assert parsed_pages == [4, 4, 2]
    assert r.page_count == 10
    assert "part-4" in r.markdown and "part-2" in r.markdown
    assert r.metadata["parts"] == "3"


# ── split_and_parse_async ───────────────────────────────────────


@pytest.mark.asyncio
async def test_split_and_parse_async_handles_split(tmp_path: Path) -> None:
    pdf = tmp_path / "big.pdf"
    _make_pdf(pdf, 8)

    async def _fake_parse(p: Path) -> ParseResult:
        pc = pdf_page_count(p) or 0
        # Simulate async work
        await asyncio.sleep(0)
        return ParseResult(markdown=f"part-{pc}", page_count=pc, metadata={"engine": "fake"})

    r = await split_and_parse_async(pdf, SplitPolicy(max_pages=3), _fake_parse)
    # 8 pages / 3 → 3 + 3 + 2 = 3 parts
    assert r.page_count == 8
    assert r.metadata["parts"] == "3"
    assert "part-3" in r.markdown and "part-2" in r.markdown


@pytest.mark.asyncio
async def test_split_and_parse_async_skips_split_when_under_limit(tmp_path: Path) -> None:
    pdf = tmp_path / "small.pdf"
    _make_pdf(pdf, 5)
    calls: list[Path] = []

    async def _fake_parse(p: Path) -> ParseResult:
        calls.append(p)
        return ParseResult(markdown="ok", page_count=5, metadata={"engine": "fake"})

    r = await split_and_parse_async(pdf, SplitPolicy(max_pages=100), _fake_parse)
    assert len(calls) == 1
    assert r.markdown == "ok"
