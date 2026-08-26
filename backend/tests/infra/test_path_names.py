"""A skill name becomes a directory name — it must be creatable on Windows.

``react:components`` (a plugin-style namespaced name) crashed session start
with ``[WinError 267] The directory name is invalid`` because NTFS reads
``dir:stream`` as an alternate data stream. The rules are enforced on every
platform so one skill library behaves identically everywhere.
"""

from __future__ import annotations

import pytest

from valuz_agent.infra.path_names import is_portable_segment, sanitize_segment


@pytest.mark.parametrize(
    "name",
    [
        "price-audit",
        "weekly report",
        "v1.2.3-report",
        "周报",
        "console",  # only the exact device name is reserved, not a superstring
        "Skill_Name",
    ],
)
def test_portable_names_are_accepted(name: str) -> None:
    assert is_portable_segment(name)
    assert sanitize_segment(name) == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("react:components", "react-components"),
        ('quote"name', "quote-name"),
        ("pipe|name", "pipe-name"),
        ("star*name", "star-name"),
        ("question?name", "question-name"),
        ("less<greater>", "less-greater"),  # trailing '-' from '>' survives
        ("a/b", "a-b"),
        ("a\\b", "a-b"),
        ("nul\x00byte", "nul-byte"),
        ("multi:::colon", "multi-colon"),  # runs collapse to one dash
        ("trailing-dot.", "trailing-dot"),
        ("trailing-space ", "trailing-space"),
        (".leading-dot", "leading-dot"),
        ("con", "con-skill"),  # Windows device names, reserved with or…
        ("COM1.md", "COM1.md-skill"),  # …without an extension, any case
        ("", "skill"),
        (":", "skill"),
        ("..", "skill"),
    ],
)
def test_unportable_names_are_rewritten(name: str, expected: str) -> None:
    assert not is_portable_segment(name)
    assert sanitize_segment(name) == expected


def test_sanitize_output_is_always_portable() -> None:
    for name in ("react:components", "con", "..", "", "  ...  ", "a<b>c|d"):
        assert is_portable_segment(sanitize_segment(name)), name
