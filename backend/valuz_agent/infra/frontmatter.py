"""One place that knows where a Markdown frontmatter block starts and ends.

Every SKILL.md reader and writer used to carry its own copy of the delimiter
check, and they did not agree. A manifest with CRLF line endings opens with
``---\r\n``, which fails a literal ``"---\n"`` test — the block is then missed
and the WHOLE file is treated as body. Readers degraded to surfacing the
leftover ``---`` as the skill's description (fixed once in
``skills_filesystem`` — 30 installed skills were showing "---"); writers did
something worse and wrapped the file, frontmatter and all, in a second block,
permanently burying the real metadata. Three bundled skills shipped that way.

The delimiter check is the part that keeps breaking, so it lives here now and
every caller shares it. Parsing stays with the callers — they want different
things (typed metadata, one field, a textual rewrite) and only agree on where
the block is.
"""

from __future__ import annotations

import yaml

__all__ = [
    "is_placeholder_description",
    "normalize_newlines",
    "parse_frontmatter_mapping",
    "split_frontmatter",
]


def normalize_newlines(text: str) -> str:
    """CRLF / CR → LF. Always the first step: every delimiter below assumes LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_frontmatter(raw: str) -> tuple[str | None, str]:
    """``(yaml_block, body)`` for a leading frontmatter block.

    ``yaml_block`` is ``None`` when the text does not open with one, in which
    case ``body`` is the whole (newline-normalized) text. Both halves come back
    with LF endings — a caller rewriting the file in place should keep that in
    mind, since it normalizes the result.
    """
    text = normalize_newlines(raw)
    if not text.startswith("---\n"):
        return None, text
    closing = text.find("\n---\n", 4)
    if closing < 0:
        return None, text
    return text[4:closing], text[closing + 5 :]


def parse_frontmatter_mapping(yaml_block: str) -> dict[str, object] | None:
    """The block as a mapping, or ``None`` when it is not valid YAML mapping.

    Used to tell a real metadata block from a bare ``---`` horizontal rule that
    happens to sit at the top of a body.
    """
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def is_placeholder_description(description: str | None) -> bool:
    """True for a description that is empty or nothing but dashes/whitespace.

    ``"---"`` is the signature of a frontmatter parse that fell through and
    handed back the delimiter. It is never something an author typed, and
    persisting it destroys the real description, so writers reject it.
    """
    return not (description or "").strip(" \t-")
