"""Chat ↔ project binding: "this group is that project".

Replaces inferring a chat's project from whichever session lineage happened to
be touched last — a guess that could not be inspected, changed, or reasoned
about. See docs/design/channel-project-binding-and-default-lead.md §3.2, §4.1.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.infra.database import Base
from valuz_agent.modules.channels import (
    AgentChannelResolver,
    AgentPlacement,
    ChannelMentionContext,
    ChannelRouteDecisionKind,
    ChannelThreadBinding,
)
from valuz_agent.modules.channels.datastore import ChannelChatBindingDatastore
from valuz_agent.modules.channels.models import ChannelChatBindingRow


@pytest.fixture
def sessionmaker_(tmp_path):
    db_file = tmp_path / "chat_bindings.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ChannelChatBindingRow.__table__])
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    return async_sessionmaker(bind=async_engine, expire_on_commit=False)


def _placement(project_id: str, *, agent_slug: str = "helper") -> AgentPlacement:
    return AgentPlacement(
        project_id=project_id,
        project_name=project_id,
        agent_slug=agent_slug,
        source_agent_slug=agent_slug,
    )


def _context(**overrides) -> ChannelMentionContext:
    base = dict(
        user_id="u1",
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id=None,
        mentioned_agent_slug="helper",
        is_top_level_mention=True,
    )
    base.update(overrides)
    return ChannelMentionContext(**base)  # type: ignore[arg-type]


# ------------------------------------------------------------------ #
# datastore
# ------------------------------------------------------------------ #


async def test_bind_rebind_and_unbind(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        ds = ChannelChatBindingDatastore(db)
        await ds.upsert(
            user_id="u1",
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            project_id="proj-a",
            external_chat_name="研究群",
        )
        # A chat holds exactly one project — rebinding overwrites.
        rebound = await ds.upsert(
            user_id="u1",
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            project_id="proj-b",
        )
        assert rebound.project_id == "proj-b"
        assert rebound.external_chat_name == "研究群"  # kept when not resupplied

        rows = await ds.list_all(user_id="u1")
        assert len(rows) == 1

        assert (
            await ds.delete(
                user_id="u1",
                channel_instance_id="feishu-main",
                external_chat_id="chat-1",
            )
            is True
        )
        assert (
            await ds.get(
                user_id="u1",
                channel_instance_id="feishu-main",
                external_chat_id="chat-1",
            )
            is None
        )


async def test_bindings_are_owner_scoped(sessionmaker_) -> None:
    async with sessionmaker_() as db:
        ds = ChannelChatBindingDatastore(db)
        await ds.upsert(
            user_id="u1",
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            project_id="proj-a",
        )
        assert (
            await ds.get(
                user_id="u2",
                channel_instance_id="feishu-main",
                external_chat_id="chat-1",
            )
            is None
        )


async def test_a_project_may_be_bound_from_several_chats(sessionmaker_) -> None:
    """Allowed by design (an internal group and a client group), while a chat
    still holds exactly one project."""
    async with sessionmaker_() as db:
        ds = ChannelChatBindingDatastore(db)
        for chat in ("chat-1", "chat-2"):
            await ds.upsert(
                user_id="u1",
                channel_instance_id="feishu-main",
                external_chat_id=chat,
                project_id="proj-a",
            )
        rows = await ds.list_for_project(user_id="u1", project_id="proj-a")
        assert {row.external_chat_id for row in rows} == {"chat-1", "chat-2"}


# ------------------------------------------------------------------ #
# resolution order (§4.1)
# ------------------------------------------------------------------ #


def test_binding_decides_the_project_instead_of_asking() -> None:
    """With several placements the resolver would normally ask which project.
    A bound group has already answered that."""
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("proj-a"), _placement("proj-b")],
        chat_project_id="proj-b",
    )
    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "proj-b"
    assert decision.reason == "chat_project_binding"


def test_explicit_hint_still_outranks_the_binding() -> None:
    decision = AgentChannelResolver().resolve(
        _context(explicit_project_name="proj-a"),
        placements=[_placement("proj-a"), _placement("proj-b")],
        chat_project_id="proj-b",
    )
    assert decision.project_id == "proj-a"
    assert decision.reason == "explicit_project_match"


def test_bound_chat_continues_only_its_own_project_lineage() -> None:
    """A lineage left over from before the binding must not answer for the
    project the group now stands for."""
    stale = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="chat-1",
        agent_slug="helper",
        project_id="proj-a",
        session_id="session-a",
    )
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("proj-a"), _placement("proj-b")],
        existing_binding=stale,
        chat_project_id="proj-b",
    )
    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "proj-b"


def test_bound_chat_reuses_its_own_lineage() -> None:
    live = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="chat-1",
        agent_slug="helper",
        project_id="proj-b",
        session_id="session-b",
    )
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("proj-a"), _placement("proj-b")],
        existing_binding=live,
        chat_project_id="proj-b",
    )
    assert decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert decision.session_id == "session-b"


def test_binding_to_a_project_the_agent_is_not_on_degrades() -> None:
    """A binding pointing at a project the agent was never deployed to is a
    misconfiguration — fall through rather than fail session creation later."""
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("proj-a")],
        chat_project_id="proj-zzz",
    )
    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "proj-a"
    assert decision.reason == "single_deployment"


# ------------------------------------------------------------------ #
# chat commands through the ingress service (flow B)
# ------------------------------------------------------------------ #


class _FakeChatBindings:
    def __init__(self, project_id: str | None = None) -> None:
        self.project_id = project_id
        self.upserts: list[dict] = []
        self.deleted = False

    async def get(self, **_keys):
        from types import SimpleNamespace

        return SimpleNamespace(project_id=self.project_id) if self.project_id else None

    async def upsert(self, **kwargs):
        self.upserts.append(kwargs)
        self.project_id = kwargs["project_id"]
        return None

    async def delete(self, **_keys) -> bool:
        removed = self.project_id is not None
        self.project_id = None
        self.deleted = removed
        return removed


class _FakePlacements:
    def __init__(self, placements: list[AgentPlacement]) -> None:
        self.placements = placements

    async def list_placements(self, _user_id: str, _slug: str) -> list[AgentPlacement]:
        return self.placements


class _NoopBindings:
    async def get_for_thread(self, **_kwargs):
        return None

    async def upsert(self, **_kwargs) -> None:
        return None


class _NoopSessions:
    async def create_session(self, **_kwargs):  # pragma: no cover - never reached
        raise AssertionError("a command must not start a session")

    async def send_message(self, **_kwargs) -> None:  # pragma: no cover
        raise AssertionError("a command must not send a message")

    async def get_session_status(self, **_kwargs) -> str | None:
        return None

    async def enqueue_message(self, **_kwargs) -> None:  # pragma: no cover
        raise AssertionError("a command must not enqueue")


def _inbound(text: str):
    from valuz_agent.modules.channels.adapters import InboundChannelMessage

    return InboundChannelMessage(
        text=text,
        context=_context(user_id="u1"),
        params={},
        channel_context={},
    )


def _service(chat_bindings, placements: list[AgentPlacement] | None = None):
    from valuz_agent.modules.channels.service import ChannelIngressService

    return ChannelIngressService(
        placements=_FakePlacements(placements or []),
        bindings=_NoopBindings(),
        sessions=_NoopSessions(),
        chat_bindings=chat_bindings,
    )


async def test_bind_command_binds_and_answers_directly() -> None:
    """A command is configuration: answered directly, never routed to an agent
    (a model answering would make the outcome non-deterministic)."""
    store = _FakeChatBindings()
    service = _service(store, [_placement("proj-a")])
    placement_named = AgentPlacement(
        project_id="proj-a", project_name="研究", agent_slug="helper"
    )
    service._placements = _FakePlacements([placement_named])

    result = await service.handle_inbound_message(
        user_id="u1", inbound=_inbound("绑定项目 研究")
    )

    assert result.direct_reply and "研究" in result.direct_reply
    assert result.session_id is None
    assert store.upserts[0]["project_id"] == "proj-a"


async def test_unknown_project_lists_the_candidates() -> None:
    store = _FakeChatBindings()
    service = _service(
        store,
        [AgentPlacement(project_id="proj-a", project_name="研究", agent_slug="helper")],
    )

    result = await service.handle_inbound_message(
        user_id="u1", inbound=_inbound("绑定项目 不存在")
    )

    assert result.direct_reply and "研究" in result.direct_reply
    assert store.upserts == []


async def test_unbind_command_clears_the_binding() -> None:
    store = _FakeChatBindings(project_id="proj-a")
    service = _service(store)

    result = await service.handle_inbound_message(
        user_id="u1", inbound=_inbound("解绑")
    )

    assert result.direct_reply is not None
    assert store.project_id is None


# ------------------------------------------------------------------ #
# who answers (§4.2)
# ------------------------------------------------------------------ #


class _FakeMemberReader:
    def __init__(self, slugs: list[str]) -> None:
        self.slugs = slugs

    async def list_member_slugs(self, _user_id: str, _project_id: str) -> list[str]:
        return self.slugs


class _RecordingSessions(_NoopSessions):
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create_session(self, **kwargs):
        from types import SimpleNamespace

        self.created.append(kwargs)
        return SimpleNamespace(id="session-new", project_id=kwargs["project_id"])

    async def send_message(self, **_kwargs) -> None:
        return None


async def _route(text: str, members: list[str]):
    from valuz_agent.modules.channels.service import ChannelIngressService

    sessions = _RecordingSessions()
    service = ChannelIngressService(
        placements=_FakePlacements(
            [
                AgentPlacement(project_id="proj-a", project_name="研究", agent_slug=slug)
                for slug in members
            ]
        ),
        bindings=_NoopBindings(),
        sessions=sessions,
        chat_bindings=_FakeChatBindings(project_id="proj-a"),
        project_members=_FakeMemberReader(members),
    )
    await service.handle_inbound_message(user_id="u1", inbound=_inbound(text))
    return sessions


async def test_naming_a_member_switches_who_answers() -> None:
    sessions = await _route("让分析师看看这个报表", ["helper", "分析师"])
    assert sessions.created[0]["agent_slug"] == "分析师"


async def test_an_unmatched_name_is_ignored_not_guessed() -> None:
    """Handing work to the wrong member is worse than answering as the
    default, so a name nobody on the team carries falls through."""
    sessions = await _route("让某个不存在的人看看这个", ["helper"])
    assert sessions.created[0]["agent_slug"] == "helper"


async def test_plain_message_uses_the_app_binding_agent() -> None:
    sessions = await _route("这个季度收入怎么样", ["helper", "分析师"])
    assert sessions.created[0]["agent_slug"] == "helper"


# ------------------------------------------------------------------ #
# a 1:1 chat is not a project (§2)
# ------------------------------------------------------------------ #


def test_direct_chat_stays_a_quick_chat_even_when_the_agent_is_deployed() -> None:
    """Deploying an agent to a project is not consent to route every private
    chat with it into that project — which is what the single-deployment
    heuristic did, dragging DMs into the project's context and files."""
    decision = AgentChannelResolver().resolve(
        _context(is_direct_chat=True),
        placements=[_placement("proj-a")],
    )
    assert decision.project_id == "chat-default"
    assert decision.reason == "direct_chat_quick_chat"


def test_direct_chat_honours_an_explicit_project() -> None:
    decision = AgentChannelResolver().resolve(
        _context(is_direct_chat=True, explicit_project_name="proj-a"),
        placements=[_placement("proj-a")],
    )
    assert decision.project_id == "proj-a"


def test_direct_chat_honours_a_deliberate_binding() -> None:
    """Binding a DM is not offered in the UI, but someone who typed
    绑定项目 X in one meant it."""
    decision = AgentChannelResolver().resolve(
        _context(is_direct_chat=True),
        placements=[_placement("proj-a")],
        chat_project_id="proj-a",
    )
    assert decision.project_id == "proj-a"
    assert decision.reason == "chat_project_binding"


def test_group_chat_still_uses_the_single_deployment_heuristic() -> None:
    decision = AgentChannelResolver().resolve(
        _context(is_direct_chat=False),
        placements=[_placement("proj-a")],
    )
    assert decision.project_id == "proj-a"
    assert decision.reason == "single_deployment"


def test_direct_chat_continues_its_own_quick_chat_session() -> None:
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="chat-1",
        agent_slug="helper",
        project_id="chat-project-7",
        session_id="session-7",
    )
    decision = AgentChannelResolver().resolve(
        _context(is_direct_chat=True, is_top_level_mention=False),
        placements=[_placement("proj-a")],
        existing_binding=existing,
    )
    assert decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert decision.session_id == "session-7"


def test_direct_chat_does_not_resume_a_leftover_project_session() -> None:
    """The DM used to run in the project, so its newest lineage points there.
    Continuing it would drop the turn straight back into the project this
    branch just decided against — observed live before this guard."""
    leftover = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="chat-1",
        agent_slug="helper",
        project_id="proj-a",
        session_id="session-in-project",
    )
    decision = AgentChannelResolver().resolve(
        _context(is_direct_chat=True, is_top_level_mention=False),
        placements=[_placement("proj-a")],
        existing_binding=leftover,
    )
    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "chat-default"
    assert decision.reason == "direct_chat_quick_chat"
