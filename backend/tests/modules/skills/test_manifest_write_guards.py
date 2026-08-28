"""A SKILL.md write must not be able to destroy the manifest it came from.

Three bundled skills shipped with ``description: "---"`` because a read fell
through to the frontmatter delimiter and a write then persisted that value,
wrapping the original file — frontmatter and all — as the new body. Readers
only ever look at the FIRST block, so the real description was gone.

The read side was fixed once, for one parser. These cover the write side, which
is what makes the damage permanent.
"""

from __future__ import annotations

import pytest

from valuz_agent.infra.frontmatter import (
    is_placeholder_description,
    split_frontmatter,
)
from valuz_agent.modules.skills.errors import SkillManifestDescriptionInvalid
from valuz_agent.modules.skills.service import SkillLibraryService

render = SkillLibraryService._render_manifest


class TestPlaceholderDescription:
    @pytest.mark.parametrize("value", ["---", "", "   ", "--", " - - ", None])
    def test_recognises_a_delimiter_or_empty_value(self, value):
        assert is_placeholder_description(value) is True

    @pytest.mark.parametrize("value", ["Drive a real browser", "-", "a-b"])
    def test_leaves_an_authored_description_alone(self, value):
        # A single dash is a legitimate (if terse) description; only a value
        # made ENTIRELY of dashes/space is the parse-failure signature.
        assert is_placeholder_description(value) is (value == "-")

    def test_write_refuses_the_delimiter(self):
        with pytest.raises(SkillManifestDescriptionInvalid):
            render(
                None,
                name="browser",
                description="---",
                instructions_markdown="# Browser\n\nbody",
            )

    def test_write_refuses_an_empty_description(self):
        with pytest.raises(SkillManifestDescriptionInvalid):
            render(None, name="browser", description="", instructions_markdown="body")


class TestNestedFrontmatter:
    """The exact shape that corrupted ``browser`` / ``skill-creator``."""

    WHOLE_MANIFEST = (
        "---\n"
        "name: browser\n"
        "description: Drive a real, visible Chrome.\n"
        "---\n"
        "\n"
        "# Browser\n"
        "\n"
        "Snapshot first, act on stable uids.\n"
    )

    def test_a_corrupting_round_trip_now_heals(self):
        # Caller read a manifest with a broken parser (description came back as
        # the delimiter) and handed the whole file back as the body.
        out = render(
            None,
            name="browser",
            description="---",
            instructions_markdown=self.WHOLE_MANIFEST,
        )

        block, body = split_frontmatter(out)
        assert block is not None
        assert "Drive a real, visible Chrome." in block
        # One block only — the body must not carry a second one.
        assert split_frontmatter(body)[0] is None

    def test_a_crlf_manifest_heals_too(self):
        # CRLF is what defeated the delimiter check in the first place.
        out = render(
            None,
            name="browser",
            description="---",
            instructions_markdown=self.WHOLE_MANIFEST.replace("\n", "\r\n"),
        )

        block, body = split_frontmatter(out)
        assert block is not None and "Drive a real, visible Chrome." in block
        assert split_frontmatter(body)[0] is None

    def test_an_explicit_description_outranks_the_carried_one(self):
        out = render(
            None,
            name="browser",
            description="A deliberate new description.",
            instructions_markdown=self.WHOLE_MANIFEST,
        )

        block, _ = split_frontmatter(out)
        assert "A deliberate new description." in block
        assert "Drive a real, visible Chrome." not in block

    def test_a_body_opening_with_a_horizontal_rule_is_not_frontmatter(self):
        # ``---`` is legal markdown. Only a block that parses as a mapping AND
        # carries skill keys is treated as a manifest to unwrap.
        out = render(
            None,
            name="notes",
            description="Real description.",
            instructions_markdown="---\n\nsome prose\n",
        )

        assert "some prose" in out
        block, _ = split_frontmatter(out)
        assert "Real description." in block


class TestDescriptionQuoting:
    def test_a_quote_in_the_description_still_parses(self):
        out = render(
            None,
            name="quoter",
            description='Use when the user says "deck" or "slides".',
            instructions_markdown="body",
        )

        block, _ = split_frontmatter(out)
        import yaml

        parsed = yaml.safe_load(block)
        assert parsed["description"] == 'Use when the user says "deck" or "slides".'


class TestVersionBumpKeepsOneFrontmatter:
    """``_bump_skill_md_version`` must never turn one block into two.

    It is a WRITE path whose "no frontmatter — wrap the file" branch produces
    exactly the nested-block corruption, and it reached that branch from a
    literal ``"---\\n"`` test. Today the file is read with ``read_text``, whose
    universal newlines make that test safe — but nothing says so, and the skill
    catalog next door reads the same files as BYTES. These pin the invariant so
    the guarantee stops depending on which reader happens to be used.
    """

    MANIFEST = (
        "---\nname: forked\ndescription: A real description.\n---\n\n# Forked\n\nbody\n"
    )

    def _bump(self, tmp_path, raw: str) -> str:
        from valuz_agent.modules.skills.staging import _bump_skill_md_version

        slug_dir = tmp_path / "forked"
        slug_dir.mkdir()
        (slug_dir / "SKILL.md").write_text(raw, encoding="utf-8", newline="")
        _bump_skill_md_version(slug_dir, "forked-v3")
        return (slug_dir / "SKILL.md").read_text(encoding="utf-8")

    def test_crlf_manifest_keeps_a_single_block(self, tmp_path):
        out = self._bump(tmp_path, self.MANIFEST.replace("\n", "\r\n"))

        block, body = split_frontmatter(out)
        assert block is not None
        assert "A real description." in block
        assert split_frontmatter(body)[0] is None, "wrapped instead of edited"

    def test_lf_manifest_is_unchanged_in_shape(self, tmp_path):
        out = self._bump(tmp_path, self.MANIFEST)

        block, body = split_frontmatter(out)
        assert "A real description." in block
        assert split_frontmatter(body)[0] is None

    def test_a_file_without_frontmatter_still_gets_one(self, tmp_path):
        out = self._bump(tmp_path, "# Forked\n\nbody\n")

        block, body = split_frontmatter(out)
        assert block is not None and "version:" in block
        assert "# Forked" in body
