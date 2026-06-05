"""Unit tests for backend agent slug derivation (VALUZ-AGENT-SLUG)."""

from __future__ import annotations

from valuz_agent.modules.agents.slug import derive_slug, ensure_unique_slug


class TestDeriveSlug:
    def test_should_preserve_cjk_verbatim(self) -> None:
        assert derive_slug("行情分析师") == "行情分析师"

    def test_should_convert_spaces_to_dash_and_keep_case(self) -> None:
        assert derive_slug("Data Analyst") == "Data-Analyst"

    def test_should_keep_mixed_cjk_and_ascii(self) -> None:
        assert derive_slug("GPT 行情 Bot") == "GPT-行情-Bot"

    def test_should_collapse_whitespace_runs(self) -> None:
        assert derive_slug("  Front   End  ") == "Front-End"

    def test_should_convert_underscores_to_dash(self) -> None:
        assert derive_slug("data_pipeline_v2") == "data-pipeline-v2"

    def test_should_drop_punctuation_and_symbols(self) -> None:
        assert derive_slug("Q&A / 复盘!") == "QA-复盘"

    def test_should_collapse_multiple_dashes(self) -> None:
        assert derive_slug("a -- b") == "a-b"

    def test_should_strip_leading_trailing_dashes(self) -> None:
        assert derive_slug("--研究员--") == "研究员"

    def test_should_fall_back_to_agent_on_empty(self) -> None:
        assert derive_slug("") == "agent"
        assert derive_slug("   ") == "agent"
        assert derive_slug("!!!") == "agent"

    def test_should_not_lowercase(self) -> None:
        # 大小写严格 — case is preserved exactly.
        assert derive_slug("TechLead") == "TechLead"


class TestEnsureUniqueSlug:
    def test_should_return_base_when_free(self) -> None:
        assert ensure_unique_slug("研究员", ["前端", "后端"]) == "研究员"

    def test_should_suffix_on_collision(self) -> None:
        assert ensure_unique_slug("研究员", ["研究员"]) == "研究员-2"

    def test_should_skip_taken_suffixes(self) -> None:
        assert ensure_unique_slug("agent", ["agent", "agent-2", "agent-3"]) == "agent-4"

    def test_should_handle_empty_taken(self) -> None:
        assert ensure_unique_slug("x", []) == "x"
