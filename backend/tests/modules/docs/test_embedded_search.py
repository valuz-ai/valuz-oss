from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from valuz_agent.integrations.docs_embedded import (
    EmbeddedDocsRuntime,
    _detect_rg,
    _tokenize_query,
    sanitize_preview_filename,
)


@pytest.fixture()
def preview_dir(tmp_path: Path) -> Path:
    d = tmp_path / "previews"
    d.mkdir()
    return d


def _write_preview(preview_dir: Path, doc_id: str, content: str) -> None:
    (preview_dir / f"{doc_id}.md").write_text(content, encoding="utf-8")


# ── detection ─────────────────────────────────────────────────────────


class TestDetectRg:
    def test_finds_rg_in_path(self) -> None:
        result = _detect_rg()
        if shutil.which("rg"):
            assert result is not None
        else:
            assert result is None

    def test_respects_env_var(self, tmp_path: Path) -> None:
        fake_rg = tmp_path / "rg"
        fake_rg.write_text("#!/bin/sh\n")
        with patch.dict("os.environ", {"VALUZ_RG_PATH": str(fake_rg)}):
            assert _detect_rg() == str(fake_rg)

    def test_ignores_missing_env_path(self) -> None:
        with patch.dict("os.environ", {"VALUZ_RG_PATH": "/nonexistent/rg"}):
            result = _detect_rg()
            assert result is None or result != "/nonexistent/rg"


# ── query tokenization ───────────────────────────────────────────────


class TestTokenizeQuery:
    def test_single_keyword(self) -> None:
        assert _tokenize_query("revenue") == ["revenue"]

    def test_multi_keyword(self) -> None:
        assert _tokenize_query("九鼎 业绩 2025") == ["九鼎", "业绩", "2025"]

    def test_strips_whitespace(self) -> None:
        assert _tokenize_query("  foo   bar  ") == ["foo", "bar"]

    def test_empty(self) -> None:
        assert _tokenize_query("") == []
        assert _tokenize_query("   ") == []


# ── filename sanitization ────────────────────────────────────────────


class TestSanitizeFilename:
    def test_pdf(self) -> None:
        result = sanitize_preview_filename("九鼎投资(600053) - 2025 Q4 - 年度业绩预告.PDF")
        assert result == "九鼎投资(600053) - 2025 Q4 - 年度业绩预告_parsed.md"

    def test_markdown(self) -> None:
        assert sanitize_preview_filename("notes.md") == "notes_parsed.md"

    def test_unsafe_chars(self) -> None:
        result = sanitize_preview_filename('file<with>:bad"chars.txt')
        assert "<" not in result
        assert ">" not in result
        assert '"' not in result

    def test_dotfile(self) -> None:
        assert sanitize_preview_filename(".hidden") == ".hidden_parsed.md"


# ── Python fallback path ─────────────────────────────────────────────


class TestPythonSearch:
    def test_basic_match(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Report\n\nRevenue grew 20%.\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("revenue", ["doc1"])
        assert len(results) == 1
        assert results[0].document_id == "doc1"
        assert results[0].score == 1.0
        assert results[0].filename == "Report"
        assert results[0].match_line == 3

    def test_multi_keyword(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Report\n\nRevenue grew 20%.\nProfit up.\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("revenue profit", ["doc1"])
        assert len(results) == 1
        assert results[0].score == 2.0

    def test_multi_keyword_partial_match(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# A\n\nfoo bar\n")
        _write_preview(preview_dir, "doc2", "# B\n\nfoo bar\nbaz foo\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("foo baz", ["doc1", "doc2"])
        assert len(results) == 2
        assert results[0].document_id == "doc2"

    def test_case_insensitive(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Title\n\nNVIDIA reported earnings.\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("nvidia", ["doc1"])
        assert len(results) == 1

    def test_no_match(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Title\n\nSome content.\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("nonexistent", ["doc1"])
        assert len(results) == 0

    def test_multiple_files_ranked(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# A\n\nfoo\n")
        _write_preview(preview_dir, "doc2", "# B\n\nfoo bar\nfoo baz\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("foo", ["doc1", "doc2"])
        assert len(results) == 2
        assert results[0].document_id == "doc2"
        assert results[0].score == 2.0
        assert results[1].document_id == "doc1"

    def test_top_k_limits(self, preview_dir: Path) -> None:
        for i in range(10):
            _write_preview(preview_dir, f"doc{i}", f"# Doc {i}\n\nkeyword\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("keyword", [f"doc{i}" for i in range(10)], top_k=3)
        assert len(results) == 3

    def test_missing_file_skipped(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# A\n\nfoo\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("foo", ["doc1", "doc_missing"])
        assert len(results) == 1

    def test_empty_query(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# A\n\nfoo\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("", ["doc1"])
        assert len(results) == 0

    def test_large_file_context(self, preview_dir: Path) -> None:
        lines = ["# Title"] + [f"line {i}" for i in range(200)]
        lines[100] = "TARGET keyword here"
        _write_preview(preview_dir, "doc1", "\n".join(lines))
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("TARGET", ["doc1"])
        assert len(results) == 1
        assert "[... lines" in results[0].snippet
        assert results[0].total_lines == 201

    def test_chinese_search(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# 英伟达研报\n\n数据中心收入达 184 亿美元\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = None

        results = rt.search_sync("数据中心", ["doc1"])
        assert len(results) == 1
        assert results[0].filename == "英伟达研报"

    def test_doc_paths_override(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom"
        custom.mkdir()
        (custom / "myfile.md").write_text("# Custom\n\nfoo bar\n")
        rt = EmbeddedDocsRuntime(None)
        rt._rg_path = None

        results = rt.search_sync(
            "foo", ["doc1"],
            doc_paths={"doc1": str(custom / "myfile.md")},
        )
        assert len(results) == 1
        assert results[0].document_id == "doc1"


# ── ripgrep path ──────────────────────────────────────────────────────


@pytest.mark.skipif(
    shutil.which("rg") is None,
    reason="ripgrep (rg) not installed",
)
class TestRipgrepSearch:
    def test_basic_match(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Report\n\nRevenue grew 20%.\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        assert rt.rg_available

        results = rt.search_sync("revenue", ["doc1"])
        assert len(results) == 1
        assert results[0].document_id == "doc1"
        assert results[0].score >= 1.0
        assert results[0].filename == "Report"
        assert results[0].match_line is not None

    def test_multi_keyword(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Report\n\nRevenue grew 20%.\nProfit up.\n")
        rt = EmbeddedDocsRuntime(preview_dir)

        results = rt.search_sync("revenue profit", ["doc1"])
        assert len(results) == 1
        assert results[0].score >= 2.0

    def test_case_insensitive(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Title\n\nNVIDIA reported earnings.\n")
        rt = EmbeddedDocsRuntime(preview_dir)

        results = rt.search_sync("nvidia", ["doc1"])
        assert len(results) == 1

    def test_no_match_returns_empty(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Title\n\nSome content.\n")
        rt = EmbeddedDocsRuntime(preview_dir)

        results = rt.search_sync("zzzznonexistent", ["doc1"])
        assert len(results) == 0

    def test_multiple_files_ranked_by_score(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# A\n\nfoo\n")
        _write_preview(preview_dir, "doc2", "# B\n\nfoo bar\nfoo baz\n")
        rt = EmbeddedDocsRuntime(preview_dir)

        results = rt.search_sync("foo", ["doc1", "doc2"])
        assert len(results) == 2
        assert results[0].document_id == "doc2"
        assert results[0].score >= results[1].score

    def test_special_chars_fixed_string(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# Code\n\nC++ is fast. Price is $100.\n")
        rt = EmbeddedDocsRuntime(preview_dir)

        results = rt.search_sync("C++", ["doc1"])
        assert len(results) == 1

        results = rt.search_sync("$100", ["doc1"])
        assert len(results) == 1

    def test_chinese_search(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# 英伟达研报\n\n数据中心收入达 184 亿美元\n")
        rt = EmbeddedDocsRuntime(preview_dir)

        results = rt.search_sync("数据中心", ["doc1"])
        assert len(results) == 1

    def test_top_k_limits(self, preview_dir: Path) -> None:
        for i in range(10):
            _write_preview(preview_dir, f"doc{i}", f"# Doc {i}\n\nkeyword\n")
        rt = EmbeddedDocsRuntime(preview_dir)

        results = rt.search_sync("keyword", [f"doc{i}" for i in range(10)], top_k=3)
        assert len(results) == 3

    def test_doc_paths_override(self, tmp_path: Path) -> None:
        custom = tmp_path / "custom"
        custom.mkdir()
        (custom / "九鼎_parsed.md").write_text("# 九鼎报告\n\n九鼎投资业绩\n")
        rt = EmbeddedDocsRuntime(None)

        results = rt.search_sync(
            "九鼎", ["doc1"],
            doc_paths={"doc1": str(custom / "九鼎_parsed.md")},
        )
        assert len(results) == 1


# ── fallback on rg failure ────────────────────────────────────────────


class TestFallback:
    def test_falls_back_when_rg_fails(self, preview_dir: Path) -> None:
        _write_preview(preview_dir, "doc1", "# A\n\nfoo bar\n")
        rt = EmbeddedDocsRuntime(preview_dir)
        rt._rg_path = "/nonexistent/rg"

        results = rt.search_sync("foo", ["doc1"])
        assert len(results) == 1
        assert results[0].document_id == "doc1"


# ── health check ──────────────────────────────────────────────────────


class TestHealth:
    @pytest.mark.asyncio
    async def test_healthy_when_dir_exists(self, preview_dir: Path) -> None:
        rt = EmbeddedDocsRuntime(preview_dir)
        snap = await rt.health()
        assert snap.status == "healthy"

    @pytest.mark.asyncio
    async def test_unavailable_when_no_dir(self) -> None:
        rt = EmbeddedDocsRuntime(None)
        snap = await rt.health()
        assert snap.status == "unavailable"
