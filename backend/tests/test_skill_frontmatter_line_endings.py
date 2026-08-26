"""SKILL.md frontmatter must survive CRLF manifests.

A skill authored on Windows (or repackaged by a tool that rewrites newlines)
starts with ``---\r\n``. The parser used to require exactly ``---\n``, so the
whole file fell through as the body and the summary fallback returned the first
non-heading line — the leftover ``---`` delimiter. The library then showed
"---" where the description belongs (observed on 30 installed skills under the
agents and claude groups).
"""

from __future__ import annotations

from valuz_agent.integrations.skills_filesystem import (
    FilesystemSkillSource,
    _extract_frontmatter,
)


def test_crlf_frontmatter_parses_like_lf() -> None:
    crlf = (
        "---\r\n"
        "name: browser\r\n"
        "description: Drive a real Chrome to browse.\r\n"
        "---\r\n"
        "\r\n"
        "# Browser\r\n"
        "\r\n"
        "Body line.\r\n"
    )

    metadata, _body = _extract_frontmatter(crlf)

    assert metadata["description"] == "Drive a real Chrome to browse."


def test_lf_frontmatter_still_parses() -> None:
    lf = "---\nname: x\ndescription: LF works.\n---\n\nbody\n"

    metadata, _body = _extract_frontmatter(lf)

    assert metadata["description"] == "LF works."


def test_crlf_manifest_does_not_leak_the_delimiter_as_summary() -> None:
    crlf = "---\r\nname: y\r\ndescription: Real summary.\r\n---\r\n\r\nBody.\r\n"

    metadata, body = _extract_frontmatter(crlf)
    shown = str(metadata.get("description") or "") or (
        FilesystemSkillSource._summary_from_body(body)
    )

    assert shown != "---"


def test_summary_fallback_skips_a_stray_document_marker() -> None:
    # Unparseable frontmatter keeps the delimiters in the body; the summary
    # must skip them instead of surfacing a bare dash row.
    body = "---\n\nActual first sentence.\n"

    assert FilesystemSkillSource._summary_from_body(body) == "Actual first sentence."
