"""Task lead selection: explicit > project default > conversation agent.

Both launchers (``create_task`` / ``draft_task``) share ``_resolve_task_lead``
on purpose — two entry points disagreeing about who leads is the kind of
inconsistency nobody can diagnose from a chat transcript
(docs/design/channel-project-binding-and-default-lead.md §4.3).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.modules.tasks.tools import handlers


class _FakeResolver:
    """Stands in for ``task_session_resolver``: a project row plus membership."""

    def __init__(self, default_lead: str | None, members: set[str]) -> None:
        self.default_lead = default_lead
        self.members = members
        self.env_calls = 0

    async def resolve_project_env(self, _db: Any, *, user_id: str, project_id: str) -> Any:
        self.env_calls += 1
        return SimpleNamespace(
            project_row=SimpleNamespace(default_lead_agent_slug=self.default_lead)
        )

    async def member_exists(
        self, _db: Any, *, user_id: str, project_id: str, agent_slug: str
    ) -> bool:
        return agent_slug in self.members


@pytest.fixture
def resolver(monkeypatch):
    """Patch the resolution seam and the unit of work the helper opens."""

    @asynccontextmanager
    async def fake_uow(*_args, **_kwargs):
        yield object()

    import valuz_agent.infra.db as db_mod
    import valuz_agent.modules.tasks.resolution as resolution_mod

    monkeypatch.setattr(db_mod, "async_unit_of_work", fake_uow)

    def _install(default_lead: str | None, members: set[str]) -> _FakeResolver:
        fake = _FakeResolver(default_lead, members)
        monkeypatch.setattr(resolution_mod, "task_session_resolver", fake)
        return fake

    return _install


async def test_explicit_lead_wins_and_skips_the_lookup(resolver) -> None:
    fake = resolver("analyst", {"analyst", "helper"})
    lead = await handlers._resolve_task_lead(
        user_id="u1",
        project_id="p1",
        explicit_slug="  researcher ",
        conversation_agent_slug="helper",
    )
    assert lead == "researcher"
    assert fake.env_calls == 0  # naming a lead must not cost a DB round trip


async def test_project_default_lead_beats_the_conversation_agent(resolver) -> None:
    """The whole point: the helper relaying work must not become the lead."""
    resolver("analyst", {"analyst", "helper"})
    lead = await handlers._resolve_task_lead(
        user_id="u1",
        project_id="p1",
        explicit_slug=None,
        conversation_agent_slug="helper",
    )
    assert lead == "analyst"


async def test_dangling_default_lead_falls_through(resolver) -> None:
    """The default lead was undeployed — fall through instead of failing the
    launch with an agent that is no longer on the team."""
    resolver("analyst", {"helper"})
    lead = await handlers._resolve_task_lead(
        user_id="u1",
        project_id="p1",
        explicit_slug=None,
        conversation_agent_slug="helper",
    )
    assert lead == "helper"


async def test_unset_default_lead_keeps_todays_behaviour(resolver) -> None:
    resolver(None, {"helper"})
    lead = await handlers._resolve_task_lead(
        user_id="u1",
        project_id="p1",
        explicit_slug="",
        conversation_agent_slug="helper",
    )
    assert lead == "helper"


async def test_lookup_failure_does_not_block_the_launch(monkeypatch) -> None:
    """A broken project read degrades to the conversation agent — refusing here
    would break "pull the bot in and ask something" for an unrelated reason."""

    @asynccontextmanager
    async def exploding_uow(*_args, **_kwargs):
        raise RuntimeError("db is down")
        yield  # pragma: no cover

    import valuz_agent.infra.db as db_mod

    monkeypatch.setattr(db_mod, "async_unit_of_work", exploding_uow)
    lead = await handlers._resolve_task_lead(
        user_id="u1",
        project_id="p1",
        explicit_slug=None,
        conversation_agent_slug="helper",
    )
    assert lead == "helper"
