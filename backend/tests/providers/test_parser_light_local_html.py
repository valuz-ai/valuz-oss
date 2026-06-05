"""Coverage for the LightLocalParser HTML routing fix (PR-1).

Prior to this PR ``.html`` was routed into the plain-text branch, which
returned the raw HTML source as "markdown". Chinese-containing pages
either crashed (MarkItDown ASCII path) or surfaced raw tags to the user.
The fix routes HTML through ``markdownify`` and preserves UTF-8 content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.integrations.parser_light_local import LightLocalParser


@pytest.fixture
def parser() -> LightLocalParser:
    return LightLocalParser()


def test_parses_simple_html_to_markdown(parser: LightLocalParser, tmp_path: Path) -> None:
    html_path = tmp_path / "simple.html"
    html_path.write_text("<h1>Hello</h1><p>world</p>", encoding="utf-8")

    result = parser.parse_sync(str(html_path))

    assert result.metadata["engine"] == "html_to_markdown"
    assert "Hello" in result.markdown
    assert "world" in result.markdown
    # markdownify with ATX style emits "# Hello", not "<h1>"
    assert "<h1>" not in result.markdown


def test_preserves_chinese_content_in_html(parser: LightLocalParser, tmp_path: Path) -> None:
    # The regression we are fixing: routing HTML through MarkItDown
    # corrupts CJK characters; markdownify must keep them intact.
    html_path = tmp_path / "zh.html"
    html_path.write_text(
        "<html><body><h1>你好,世界</h1><p>这是一段中文段落。</p></body></html>",
        encoding="utf-8",
    )

    result = parser.parse_sync(str(html_path))

    assert "你好,世界" in result.markdown
    assert "这是一段中文段落。" in result.markdown
    assert result.metadata["engine"] == "html_to_markdown"
    assert "error" not in result.metadata


def test_html_routes_through_markdownify_not_plain_text(
    parser: LightLocalParser, tmp_path: Path
) -> None:
    """Regression guard: ``.html`` must not fall back into the plain-text
    branch (which would return raw HTML tags verbatim)."""
    html_path = tmp_path / "tagged.html"
    html_path.write_text("<p>text</p>", encoding="utf-8")

    result = parser.parse_sync(str(html_path))

    # plain_text would set engine="plain_text" and return "<p>text</p>"
    assert result.metadata["engine"] != "plain_text"
    assert "<p>" not in result.markdown


def test_htm_extension_also_recognized(parser: LightLocalParser, tmp_path: Path) -> None:
    htm_path = tmp_path / "legacy.htm"
    htm_path.write_text("<b>bold</b>", encoding="utf-8")

    result = parser.parse_sync(str(htm_path))

    assert result.metadata["engine"] == "html_to_markdown"
