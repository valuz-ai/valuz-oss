"""A skill name becomes a directory name — it must be creatable on Windows.

``react:components`` (a plugin-style namespaced name) crashed session start
with ``[WinError 267] The directory name is invalid`` because NTFS reads
``dir:stream`` as an alternate data stream. The rules are enforced on every
platform so one skill library behaves identically everywhere.
"""

from __future__ import annotations

import unicodedata

import pytest

from valuz_agent.infra.path_names import (
    SLUG_ADMIT_MAX_BYTES,
    SLUG_MAX_BYTES,
    SLUG_MAX_CHARS,
    is_portable_segment,
    is_slug_segment,
    sanitize_segment,
    slugify_segment,
)


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


# ── slugify_segment: the identifier rule ─────────────────────────────
#
# A slug is the skill's directory name. It used to be derived by an
# ASCII-only charset, which meant every name written in a non-Latin script
# reduced to the bare fallback: three Chinese skills all landed as
# ``skill`` / ``skill-2`` / ``skill-3`` and could be told apart only by the
# order they happened to be created in.


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # The regression this rule exists for.
        ("天气查询", "天气查询"),
        ("航锦盘后复盘", "航锦盘后复盘"),
        ("한글 스킬", "한글-스킬"),
        ("Café分析", "café分析"),
        # Mixed scripts keep both halves rather than only the ASCII one.
        ("周报生成 v2", "周报生成-v2"),
    ],
)
def test_should_keep_the_characters_when_the_name_is_not_latin(name: str, expected: str) -> None:
    assert slugify_segment(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("self-improving-agent", "self-improving-agent"),
        ("agent-3.0.6", "agent-3-0-6"),
        ("My Great Skill", "my-great-skill"),
        ("my_skill", "my-skill"),  # '_' is a separator, never kept
        ("My Skill.v2_final", "my-skill-v2-final"),
        ("a / b", "a-b"),
        ("  --skill--  ", "skill"),
        ("react:components", "react-components"),
        ("9lives", "9lives"),
        ("con", "con-skill"),  # Windows device name, still reserved
        ("", "skill"),
        ("!!!", "skill"),  # ASCII punctuation alone still has nothing to keep
    ],
)
def test_should_derive_exactly_what_the_ascii_rule_derived(name: str, expected: str) -> None:
    """ASCII input must be untouched by this change.

    Every one of these is a case the previous ``[^a-zA-Z0-9]+`` rule already
    handled; a difference here is a regression for existing users, not a
    feature for new ones.
    """
    assert slugify_segment(name) == expected


@pytest.mark.parametrize(
    "hostile",
    [
        "a#b",  # '#' truncates a URL path at the fragment
        "a b",  # whitespace has no delimiter in the composer's /mention token
        "a%b",
        "a&b",
        "a+b",
        "a=b",
        "a　b",  # ideographic space — whitespace the eye does not see
        "a‮b",  # bidi override: a directory name that renders as a lie
        "a😀b",
        "a½b",  # Nl/No — ``isalnum`` calls these numeric; a digit they are not
        "aⅧb",
    ],
)
def test_should_strip_characters_the_rest_of_the_chain_cannot_carry(hostile: str) -> None:
    """A filesystem accepts all of these; URLs and the prompt do not.

    This is why the slug rule is not ``sanitize_segment``: that one is the
    *filesystem* rule and lets every character here through.
    """
    assert slugify_segment(hostile) == "a-b"


def test_should_normalize_so_one_name_has_one_slug() -> None:
    """macOS stores a decomposed filename; Linux stores what it was given.

    Without NFC the two spellings derive different slugs — and the combining
    marks, not being alphanumeric, would each become a dash.
    """
    composed = unicodedata.normalize("NFC", "Café한글")
    decomposed = unicodedata.normalize("NFD", "Café한글")
    assert composed != decomposed  # guard: the fixture must actually differ
    assert slugify_segment(decomposed) == slugify_segment(composed)


def test_should_not_let_a_lowercasing_decomposition_become_a_dash() -> None:
    """``lower()`` runs after NFC and can re-decompose what NFC just composed.

    ``İ`` (U+0130) lowercases to ``i`` + U+0307. The combining mark is not a
    letter, so a naive charset filter turns it into a separator and
    ``İstanbul`` comes out as ``i-stanbul``.
    """
    assert slugify_segment("İstanbul") == "istanbul"


def test_should_keep_a_precomposed_accent_as_a_letter() -> None:
    """Dropping combining marks must not degrade ``é`` to ``e``.

    The sibling rule in ``modules/agents/slug.py`` folds accents on purpose —
    its output has to be ASCII. This one does not: ``é`` is a letter and a
    perfectly good directory name, so only marks left stranded by ``lower()``
    are dropped.
    """
    assert slugify_segment("Café") == "café"


def test_should_cap_a_cjk_name_at_the_filesystem_byte_limit() -> None:
    """255 bytes was unreachable for ASCII slugs; at 3 bytes a character it is not."""
    slug = slugify_segment("查" * 200)
    assert len(slug) <= SLUG_MAX_CHARS
    assert len(slug.encode("utf-8")) <= SLUG_MAX_BYTES


def test_slugify_output_is_always_an_acceptable_slug() -> None:
    for name in ("天气查询", "a#b", "con", "", "查" * 200, "  --  ", "My Skill.v2"):
        assert is_slug_segment(slugify_segment(name)), name


def test_should_still_suffix_a_device_name_when_the_caller_wants_no_fallback() -> None:
    """``fallback`` answers "what if nothing survived", not "how to escape ``con``".

    ``sanitize_segment`` uses one argument for both jobs, so forwarding an
    empty fallback produced ``"con-"`` — a trailing dash that ``is_slug_segment``
    (and the commercial upload dialog downstream of it) rejects.
    """
    assert slugify_segment("con", fallback="") == "con-skill"
    assert slugify_segment("!!!", fallback="") == ""


# ── is_slug_segment: the admission rule ──────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "天气查询",
        "weather-query",
        "my_skill",  # legacy slug minted by the ASCII-era rule
        "agent-3.0.6",  # ditto
        "9lives",
        "a½b",  # numeric-but-not-digit: derivation strips it, disk may hold it
    ],
)
def test_should_admit_slugs_that_already_exist_on_disk(name: str) -> None:
    """Admission is deliberately wider than derivation.

    A rule that only accepted its own output would orphan every slug the
    previous rule minted with an underscore or a dot — including, after this
    change, every skill a user is currently living with.
    """
    assert is_slug_segment(name)


def test_should_admit_a_slug_longer_than_anything_we_would_mint() -> None:
    """The derivation cap is younger than the slugs it would reject.

    The ASCII rule that preceded it had no length cap at all, so a skill whose
    directory name runs to 250 characters is on someone's disk right now.
    Applying the mint cap to admission would make that skill uneditable by
    skill-creator and warn on every staging poll — the exact orphaning this
    predicate exists to avoid.
    """
    legacy = "a" * 250
    assert len(legacy) > SLUG_MAX_CHARS
    assert is_slug_segment(legacy)
    # The filesystem's own ceiling is still the ceiling: nothing can be holding
    # a name longer than one, so nothing is orphaned by rejecting it.
    assert not is_slug_segment("a" * (SLUG_ADMIT_MAX_BYTES + 1))


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "a b",
        "a#b",
        "a/b",
        "a　b",
        "con",
        "trailing-",
        # Neither shape has ever been minted, and ``^[a-z0-9][a-z0-9_-]*$``
        # rejected both. On a case-insensitive filesystem ``MySkill`` and
        # ``myskill`` are one directory but two index rows.
        "MySkill",
        "_leading-underscore",
    ],
)
def test_should_reject_names_that_cannot_be_a_slug(name: str) -> None:
    assert not is_slug_segment(name)
