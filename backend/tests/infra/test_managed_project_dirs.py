"""Managed project/chat directories: dated, unguessable, and never recomputed.

The layout these pin (``<project_root>/{projects,chats}/YYYY/MM/dd/<CODE>``)
replaces a flat ``<project_root>/<project_id>``. The important property is not
the shape but the LOSS of a property: a workspace path can no longer be derived
from a row id, so the stored ``root_path`` is the only way to reach one.
"""

from __future__ import annotations

import re
from datetime import datetime

import pytest

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import (
    MANAGED_CODE_ALPHABET,
    MANAGED_CODE_LENGTH,
    FsRegistry,
)


@pytest.fixture
def registry(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(settings, "user_project_root", tmp_path / "Valuz")
    return FsRegistry()


@pytest.mark.parametrize(("kind", "subdir"), [("project", "projects"), ("chat", "chats")])
def test_a_managed_directory_is_dated_and_kind_scoped(registry, kind, subdir) -> None:  # noqa: ANN001
    """Chats and projects sit in SIBLING trees: one chat directory is minted per
    quick chat and per automation run, so mixing them in would bury the handful
    a user actually created under thousands of ephemeral ones."""
    path = registry.allocate_managed_project_dir("u1", kind)

    parts = path.relative_to(registry.project_root("u1")).parts
    assert parts[0] == subdir
    assert re.fullmatch(r"\d{4}", parts[1])
    assert re.fullmatch(r"\d{2}", parts[2])
    assert re.fullmatch(r"\d{2}", parts[3])
    assert len(parts) == 5
    assert path.is_dir()


def test_the_directory_name_is_not_derivable_from_any_row(registry) -> None:  # noqa: ANN001
    """The whole point. Two calls with identical inputs must differ, or the old
    'recompute it from the id' habit stays possible."""
    a = registry.allocate_managed_project_dir("u1", "project")
    b = registry.allocate_managed_project_dir("u1", "project")

    assert a != b


def test_codes_within_one_day_directory_never_collide(registry) -> None:  # noqa: ANN001
    """mkdir(exist_ok=False) is the collision check, so a repeat draw retries
    rather than handing two projects the same directory."""
    names = {registry.allocate_managed_project_dir("u1", "chat").name for _ in range(300)}

    assert len(names) == 300


def test_the_code_alphabet_cannot_spell_a_word() -> None:
    """The code is user-visible in a file browser. Vowel-free means a draw can
    never land on a real word — or an obscenity — at this length."""
    assert not set("AEIOU") & set(MANAGED_CODE_ALPHABET)
    assert MANAGED_CODE_ALPHABET.isupper()
    assert MANAGED_CODE_LENGTH == 8


def test_the_date_comes_from_local_time_and_is_frozen_at_creation(registry) -> None:  # noqa: ANN001
    """The stamp is honoured verbatim so the path can be reproduced in a test —
    and, in production, so a later timezone change cannot move an existing
    workspace: the value is written to root_path once and never recomputed."""
    path = registry.allocate_managed_project_dir("u1", "project", now=datetime(2019, 3, 7, 23, 30))

    assert path.relative_to(registry.project_root("u1")).parts[:4] == (
        "projects",
        "2019",
        "03",
        "07",
    )


def test_a_chat_cwd_uses_its_stored_root_path(registry) -> None:  # noqa: ANN001
    allocated = registry.allocate_managed_project_dir("u1", "chat")

    resolved = registry.project_cwd("u1", "any-row-id", "chat", str(allocated))

    assert resolved == allocated


def test_a_chat_row_without_a_root_path_keeps_the_legacy_flat_cwd(registry) -> None:  # noqa: ANN001
    """root_path IS NULL is exactly the pre-cutover discriminator. Those rows'
    directories are still on disk under the flat layout, so the legacy
    derivation is load-bearing, not dead code — there is no migration."""
    resolved = registry.project_cwd("u1", "5185ea2d41f04f938bfbe5e308e0c4b0", "chat", None)

    assert resolved == registry.project_root("u1") / "5185ea2d41f04f938bfbe5e308e0c4b0"


def test_a_relative_chat_root_path_is_refused(registry) -> None:  # noqa: ANN001
    """A stored cwd is absolute by construction; a relative one would resolve
    against the process cwd and silently escape the tenant's tree."""
    with pytest.raises(ValueError, match="absolute"):
        registry.project_cwd("u1", "row", "chat", "relative/path")
