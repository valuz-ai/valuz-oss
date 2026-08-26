"""Owner-aware distribution prompt contract."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

from collections.abc import Iterator

import pytest

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters.system_prompt_builder import (
    AUTHORIZATION_BOUNDARY_INSTRUCTIONS,
    assemble_session_instructions,
    prepend_global_instructions,
)
from valuz_agent.ports.extensions import Extensions, ext
from valuz_agent.ports.instructions import (
    GlobalInstructionsConfigurationError,
    OSS_GLOBAL_INSTRUCTIONS,
    OSSGlobalInstructionsProvider,
    PromptSnapshot,
    agent_inherits_global_instructions,
    global_instructions_preamble,
    resolve_global_instructions,
)

OWNER = "owner-a"


@pytest.fixture
def restore_provider() -> Iterator[None]:
    original = ext.global_instructions
    try:
        yield
    finally:
        ext.global_instructions = original


class _OwnerProvider:
    def __init__(self) -> None:
        self.owners: list[str] = []

    async def resolve(self, user_id: str) -> PromptSnapshot:
        self.owners.append(user_id)
        return PromptSnapshot(
            content=f"prompt for {user_id}",
            revision=f"rev-{user_id}",
            distribution=f"dist-{user_id}",
        )


def test_oss_default_is_a_complete_provider() -> None:
    provider = Extensions().global_instructions
    assert isinstance(provider, OSSGlobalInstructionsProvider)


@pytest.mark.parametrize(
    ("kind", "inherit", "expected"),
    [
        ("system", True, True),
        ("system", False, True),
        ("standard", True, True),
        ("standard", False, False),
    ],
)
def test_agent_prompt_inheritance_contract(
    kind: str,
    inherit: bool,
    expected: bool,
) -> None:
    assert (
        agent_inherits_global_instructions(
            kind=kind,
            inherit_global_instructions=inherit,
        )
        is expected
    )


async def test_oss_prompt_is_nonempty_and_versioned() -> None:
    snapshot = await OSSGlobalInstructionsProvider().resolve(OWNER)
    assert snapshot.content == OSS_GLOBAL_INSTRUCTIONS
    assert snapshot.revision
    assert snapshot.distribution == "oss"


async def test_owner_is_explicit_and_snapshots_do_not_cross(
    restore_provider: None,
) -> None:
    provider = _OwnerProvider()
    ext.global_instructions = provider

    a = await resolve_global_instructions("owner-a")
    b = await resolve_global_instructions("owner-b")

    assert provider.owners == ["owner-a", "owner-b"]
    assert a.content == "prompt for owner-a"
    assert b.content == "prompt for owner-b"
    assert a.distribution != b.distribution


async def test_empty_distribution_snapshot_fails_closed(
    restore_provider: None,
) -> None:
    class _Invalid:
        async def resolve(self, user_id: str) -> PromptSnapshot:
            return PromptSnapshot(content="", revision="", distribution="")

    ext.global_instructions = _Invalid()
    with pytest.raises(GlobalInstructionsConfigurationError):
        await resolve_global_instructions(OWNER)


async def test_preamble_lands_first_in_assembled_prompt(
    restore_provider: None,
) -> None:
    ext.global_instructions = _OwnerProvider()
    prompt = assemble_session_instructions(
        [
            ("global-instructions", await global_instructions_preamble(OWNER)),
            ("agent-instructions", "dig deep"),
        ]
    )
    expected_head = "<global-instructions>\nprompt for owner-a\n</global-instructions>"
    assert prompt.startswith(expected_head)
    assert prompt.index("global-instructions") < prompt.index("agent-instructions")


async def test_raw_path_prepends_prompt_and_accepts_existing_snapshot(
    restore_provider: None,
) -> None:
    provider = _OwnerProvider()
    ext.global_instructions = provider
    snapshot = await resolve_global_instructions(OWNER)

    prompt = await prepend_global_instructions(
        "You are working in project X.",
        user_id=OWNER,
        snapshot=snapshot,
    )

    assert prompt == (
        "<global-instructions>\nprompt for owner-a\n</global-instructions>\n\n"
        "<authorization-boundary>\n"
        f"{AUTHORIZATION_BOUNDARY_INSTRUCTIONS}\n"
        "</authorization-boundary>\n\nYou are working in project X."
    )
    # The already-resolved snapshot prevents a second provider read.
    assert provider.owners == [OWNER]


async def test_raw_path_prompt_alone_when_no_project_prompt(
    restore_provider: None,
) -> None:
    ext.global_instructions = _OwnerProvider()
    assert await prepend_global_instructions("", user_id=OWNER) == (
        "<global-instructions>\nprompt for owner-a\n</global-instructions>\n\n"
        "<authorization-boundary>\n"
        f"{AUTHORIZATION_BOUNDARY_INSTRUCTIONS}\n"
        "</authorization-boundary>"
    )
