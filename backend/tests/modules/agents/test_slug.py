"""Unit tests for backend agent slug derivation and validation."""

from __future__ import annotations

from valuz_agent.modules.agents.slug import (
    MAX_SLUG_LENGTH,
    derive_slug,
    ensure_unique_slug,
    is_valid_slug,
)


class TestDeriveSlug:
    def test_should_drop_cjk(self) -> None:
        # A slug travels as an HTTP header value; httpx encodes those as ASCII
        # and raises on anything else. Keeping the ASCII part is what makes the
        # handle sendable at all.
        assert derive_slug("小R") == "R"

    def test_should_keep_only_the_ascii_run_of_a_mixed_name(self) -> None:
        assert derive_slug("GPT 行情 Bot") == "GPT-Bot"

    def test_should_convert_spaces_to_dash_and_keep_case(self) -> None:
        assert derive_slug("Data Analyst") == "Data-Analyst"

    def test_should_collapse_whitespace_runs(self) -> None:
        assert derive_slug("  Front   End  ") == "Front-End"

    def test_should_convert_underscores_to_dash(self) -> None:
        assert derive_slug("data_pipeline_v2") == "data-pipeline-v2"

    def test_should_drop_punctuation_and_symbols(self) -> None:
        assert derive_slug("Q&A / 复盘!") == "QA"

    def test_should_collapse_multiple_dashes(self) -> None:
        assert derive_slug("a -- b") == "a-b"

    def test_should_not_lowercase(self) -> None:
        # 大小写严格 — case is preserved exactly.
        assert derive_slug("TechLead") == "TechLead"

    def test_should_fall_back_to_agent_when_nothing_ascii_survives(self) -> None:
        # Empty / whitespace / pure ASCII punctuation carry nothing to tell
        # apart, so they share the bare fallback.
        assert derive_slug("") == "agent"
        assert derive_slug("   ") == "agent"
        assert derive_slug("!!!") == "agent"
        assert derive_slug("--") == "agent"

    def test_should_disambiguate_all_cjk_names_by_digest(self) -> None:
        # Without the digest every Chinese-named agent would derive ``agent``
        # and differ only by the -2/-3 suffix creation order hands out.
        a = derive_slug("行情分析师")
        b = derive_slug("研究员")
        assert a.startswith("agent-") and b.startswith("agent-")
        assert a != b

    def test_digest_fallback_should_be_stable_across_calls(self) -> None:
        assert derive_slug("行情分析师") == derive_slug("行情分析师")

    def test_should_fold_accented_latin_to_its_base_letter(self) -> None:
        assert derive_slug("Caf\u00e9") == "Cafe"
        assert derive_slug("M\u00fcller") == "Muller"

    def test_should_treat_both_unicode_spellings_alike(self) -> None:
        # Precomposed U+00E9 vs decomposed "e" + U+0301 is the same name; it
        # used to derive "agent-<digest>" vs "e".
        assert derive_slug("\u00e9") == derive_slug("e\u0301") == "e"

    def test_every_derived_slug_should_be_valid(self) -> None:
        for name in ("小R", "行情分析师", "GPT 行情 Bot", "", "!!!", "a -- b", "研究员"):
            assert is_valid_slug(derive_slug(name)), name

    def test_should_be_header_encodable(self) -> None:
        # The property this whole change exists for.
        for name in ("小R", "行情分析师", "GPT 行情 Bot", "A股市场热点雷达"):
            derive_slug(name).encode("ascii")

    def test_should_cap_length_without_leaving_a_trailing_dash(self) -> None:
        slug = derive_slug("a" * (MAX_SLUG_LENGTH + 50))
        assert len(slug) == MAX_SLUG_LENGTH
        slug = derive_slug("a" * (MAX_SLUG_LENGTH - 1) + " " + "b" * 10)
        assert len(slug) <= MAX_SLUG_LENGTH
        assert not slug.endswith("-")
        assert is_valid_slug(slug)


class TestIsValidSlug:
    def test_should_accept_ascii_handles(self) -> None:
        for slug in ("R", "Data-Analyst", "agent-7f3a", "v2", "TechLead"):
            assert is_valid_slug(slug), slug

    def test_should_reject_non_ascii(self) -> None:
        for slug in ("小R", "行情分析师", "GPT-行情-Bot", "café"):
            assert not is_valid_slug(slug), slug

    def test_should_reject_malformed_dashes(self) -> None:
        for slug in ("-lead", "lead-", "a--b", "-", "--"):
            assert not is_valid_slug(slug), slug

    def test_should_reject_empty_and_whitespace(self) -> None:
        for slug in ("", " ", "a b"):
            assert not is_valid_slug(slug), slug

    def test_should_reject_path_and_header_breaking_characters(self) -> None:
        for slug in ("a/b", "a:b", "a?b", "a%20b", "a\nb", "a b", "a.b", "a_b"):
            assert not is_valid_slug(slug), slug

    def test_should_reject_overlong(self) -> None:
        assert not is_valid_slug("a" * (MAX_SLUG_LENGTH + 1))
        assert is_valid_slug("a" * MAX_SLUG_LENGTH)


class TestEnsureUniqueSlug:
    def test_should_return_base_when_free(self) -> None:
        assert ensure_unique_slug("lead", ["front", "back"]) == "lead"

    def test_should_suffix_on_collision(self) -> None:
        assert ensure_unique_slug("lead", ["lead"]) == "lead-2"

    def test_should_skip_taken_suffixes(self) -> None:
        assert ensure_unique_slug("agent", ["agent", "agent-2", "agent-3"]) == "agent-4"

    def test_should_handle_empty_taken(self) -> None:
        assert ensure_unique_slug("x", []) == "x"
