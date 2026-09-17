"""A name a caller supplies has to survive becoming a path component.

For a copied file the name is a basename and none of this applies. For
generated content it is whatever the caller had — often a label a model wrote,
which is free text. Two shapes of that text used to reach the filesystem
unchanged and come back as a bare ``OSError`` that named nothing:

* a separator, which turns ``dest_dir / name`` into a nested path whose parent
  does not exist — at any length;
* a long non-ASCII name, which exceeds the byte limit on the filesystems that
  count bytes (ext4, overlayfs) while passing on the ones that count
  characters (APFS) — so it fails only where it is not being developed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from valuz_agent.modules.artifacts.snapshot import (
    format_for,
    guess_mime,
    snapshot_dir,
    snapshot_file_name,
    stage_snapshot,
    stage_snapshot_bytes,
)

# The label that actually did this in a deployment, and its variants.
MODEL_TITLE = "人形机器人市场规模（全球 / 中国，亿元） · Automation output"


@pytest.mark.parametrize(
    "name",
    [
        MODEL_TITLE,
        "市场规模（全球/中国）",
        r"a\b\c.md",
        "../../etc/passwd",
        "长" * 200,
        "长" * 200 + ".json",
        "a" * 400 + ".json",
        "   ...   ",
        "CON",
    ],
)
def test_should_stage_any_name_a_caller_can_supply(name, tmp_path: Path) -> None:
    staged = stage_snapshot_bytes(b'{"a":1}', tmp_path, "artifact-1", 1, name)

    assert staged.staging.parent == snapshot_dir(tmp_path, "artifact-1", 1)
    assert staged.staging.exists()
    assert staged.final.parent == staged.staging.parent


@pytest.mark.parametrize("name", [MODEL_TITLE, "长" * 200 + ".json", "a" * 400])
def test_should_keep_the_staged_name_within_one_path_component(name) -> None:
    safe = snapshot_file_name(name)
    staged_name = f".{safe}.deadbeef.partial"

    # ext4/overlayfs count bytes, APFS counts characters. Both caps hold.
    assert len(staged_name.encode("utf-8")) <= 255
    assert len(staged_name) <= 255
    assert "/" not in safe and "\\" not in safe


def test_should_keep_the_extension_when_clipping_a_long_name() -> None:
    # The suffix is not decoration: format_for and guess_mime both read it, and
    # the agent opens this file by name.
    safe = snapshot_file_name("长" * 200 + ".json")

    assert format_for(safe) == "json"
    assert guess_mime(safe) == "application/json"


def test_should_keep_a_readable_name_rather_than_hashing_it() -> None:
    safe = snapshot_file_name(MODEL_TITLE)

    assert "人形机器人市场规模" in safe
    assert "Automation output" in safe


def test_should_never_reduce_a_name_to_nothing() -> None:
    assert snapshot_file_name("   ...   ")
    assert snapshot_file_name("///")
    assert snapshot_file_name("")


def test_should_stage_a_copied_file_under_a_safe_name(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text("# report", encoding="utf-8")

    staged = stage_snapshot(source, tmp_path, "artifact-1", 1, MODEL_TITLE)

    assert staged.staging.exists()
    assert staged.byte_size == len("# report")
