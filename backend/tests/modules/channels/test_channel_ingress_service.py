from __future__ import annotations

from dataclasses import dataclass

from valuz_agent.modules.channels import (
    AgentPlacement,
    ChannelMentionContext,
    ChannelRouteDecisionKind,
    ChannelRouteKey,
    ChannelThreadBinding,
)
from valuz_agent.modules.channels.adapters import InboundChannelMessage
from valuz_agent.modules.channels.service import ChannelIngressService


@dataclass
class _Session:
    id: str
    project_id: str = "project-a"


class _Placements:
    def __init__(self, placements: list[AgentPlacement]) -> None:
        self.placements = placements

    async def list_placements(self, user_id: str, source_agent_slug: str) -> list[AgentPlacement]:
        assert user_id == "u1"
        assert source_agent_slug == "developer"
        return self.placements


class _Bindings:
    def __init__(self, existing: ChannelThreadBinding | None = None) -> None:
        self.existing = existing
        self.upserts: list[tuple[str, ChannelRouteKey, str]] = []

    async def get_for_thread(
        self,
        *,
        user_id: str,
        channel_instance_id: str,
        external_chat_id: str,
        external_thread_id: str,
        agent_slug: str,
    ) -> ChannelThreadBinding | None:
        assert user_id == "u1"
        assert channel_instance_id == "feishu-main"
        assert external_chat_id == "chat-1"
        assert external_thread_id == "thread-1"
        assert agent_slug == "developer"
        return self.existing

    async def upsert(self, *, user_id: str, key: ChannelRouteKey, session_id: str) -> None:
        self.upserts.append((user_id, key, session_id))


class _Sessions:
    def __init__(self, *, statuses: dict[str, str] | None = None) -> None:
        self.statuses = statuses or {}
        self.created: list[dict[str, str]] = []
        self.sent: list[tuple[str, str, str]] = []
        self.queued: list[tuple[str, str, str]] = []

    async def create_session(
        self,
        *,
        user_id: str,
        project_id: str,
        agent_slug: str,
        origin: str,
        creation_context: dict[str, str],
    ) -> _Session:
        self.created.append(
            {
                "user_id": user_id,
                "project_id": project_id,
                "agent_slug": agent_slug,
                "origin": origin,
                **creation_context,
            }
        )
        materialized = (
            "chat-project-9" if project_id == "chat-default" else project_id
        )
        return _Session(id="session-new", project_id=materialized)

    async def send_message(self, *, user_id: str, session_id: str, content: str) -> None:
        self.sent.append((user_id, session_id, content))

    async def get_session_status(self, *, user_id: str, session_id: str) -> str | None:
        assert user_id == "u1"
        return self.statuses.get(session_id, "idle")

    async def enqueue_message(self, *, user_id: str, session_id: str, content: str) -> None:
        self.queued.append((user_id, session_id, content))


def _inbound(
    *,
    top_level: bool = True,
    explicit_continue_hint: bool = False,
    explicit_new_hint: bool = False,
) -> InboundChannelMessage:
    return InboundChannelMessage(
        text="修一下登录报错",
        context=ChannelMentionContext(
            user_id="",
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            external_thread_id="thread-1",
            mentioned_agent_slug="developer",
            is_top_level_mention=top_level,
            continuation_hint=not top_level,
            explicit_continue_hint=explicit_continue_hint,
            explicit_new_hint=explicit_new_hint,
            request_id="req-1",
            external_message_id="msg-1",
            external_user_id="external-user",
        ),
    )


async def test_ingress_service_creates_session_and_binds_single_deployment() -> None:
    bindings = _Bindings()
    sessions = _Sessions()
    service = ChannelIngressService(
        placements=_Placements(
            [
                AgentPlacement(
                    project_id="project-a",
                    project_name="Alpha",
                    agent_slug="developer-local",
                    source_agent_slug="developer",
                )
            ]
        ),
        bindings=bindings,
        sessions=sessions,
    )

    result = await service.handle_inbound_message(user_id="u1", inbound=_inbound())

    assert result.decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert result.session_id == "session-new"
    assert sessions.created[0]["project_id"] == "project-a"
    assert sessions.created[0]["agent_slug"] == "developer-local"
    assert sessions.sent == [("u1", "session-new", "修一下登录报错")]
    assert bindings.upserts[0][1] == ChannelRouteKey(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="project-a",
    )


async def test_ingress_service_reuses_existing_thread_binding() -> None:
    bindings = _Bindings(
        ChannelThreadBinding(
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            external_thread_id="thread-1",
            agent_slug="developer",
            project_id="project-a",
            session_id="session-old",
        )
    )
    sessions = _Sessions()
    service = ChannelIngressService(
        placements=_Placements(
            [
                AgentPlacement(
                    project_id="project-a",
                    project_name="Alpha",
                    agent_slug="developer-local",
                    source_agent_slug="developer",
                )
            ]
        ),
        bindings=bindings,
        sessions=sessions,
    )

    result = await service.handle_inbound_message(user_id="u1", inbound=_inbound(top_level=False))

    assert result.decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert result.session_id == "session-old"
    assert sessions.created == []
    assert sessions.sent == [("u1", "session-old", "修一下登录报错")]
    assert bindings.upserts == []


async def test_ingress_service_top_level_continue_hint_reuses_existing_thread_binding() -> None:
    bindings = _Bindings(
        ChannelThreadBinding(
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            external_thread_id="thread-1",
            agent_slug="developer",
            project_id="project-a",
            session_id="session-old",
        )
    )
    sessions = _Sessions()
    service = ChannelIngressService(
        placements=_Placements(
            [
                AgentPlacement(
                    project_id="project-a",
                    project_name="Alpha",
                    agent_slug="developer-local",
                    source_agent_slug="developer",
                )
            ]
        ),
        bindings=bindings,
        sessions=sessions,
    )

    result = await service.handle_inbound_message(
        user_id="u1",
        inbound=_inbound(top_level=True, explicit_continue_hint=True),
    )

    assert result.decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert result.session_id == "session-old"
    assert sessions.created == []
    assert sessions.sent == [("u1", "session-old", "修一下登录报错")]
    assert sessions.queued == []


async def test_ingress_service_queues_existing_running_session() -> None:
    bindings = _Bindings(
        ChannelThreadBinding(
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            external_thread_id="thread-1",
            agent_slug="developer",
            project_id="project-a",
            session_id="session-old",
        )
    )
    sessions = _Sessions(statuses={"session-old": "running"})
    service = ChannelIngressService(
        placements=_Placements(
            [
                AgentPlacement(
                    project_id="project-a",
                    project_name="Alpha",
                    agent_slug="developer-local",
                    source_agent_slug="developer",
                )
            ]
        ),
        bindings=bindings,
        sessions=sessions,
    )

    result = await service.handle_inbound_message(user_id="u1", inbound=_inbound(top_level=False))

    assert result.decision.kind == ChannelRouteDecisionKind.QUEUE_SESSION
    assert result.session_id == "session-old"
    assert sessions.created == []
    assert sessions.sent == []
    assert sessions.queued == [("u1", "session-old", "修一下登录报错")]
    assert bindings.upserts == []


async def test_ingress_service_opens_new_session_when_existing_session_failed() -> None:
    bindings = _Bindings(
        ChannelThreadBinding(
            channel_instance_id="feishu-main",
            external_chat_id="chat-1",
            external_thread_id="thread-1",
            agent_slug="developer",
            project_id="project-a",
            session_id="session-old",
        )
    )
    sessions = _Sessions(statuses={"session-old": "failed"})
    service = ChannelIngressService(
        placements=_Placements(
            [
                AgentPlacement(
                    project_id="project-a",
                    project_name="Alpha",
                    agent_slug="developer-local",
                    source_agent_slug="developer",
                )
            ]
        ),
        bindings=bindings,
        sessions=sessions,
    )

    result = await service.handle_inbound_message(user_id="u1", inbound=_inbound(top_level=False))

    assert result.decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert result.session_id == "session-new"
    assert sessions.created[0]["project_id"] == "project-a"
    assert sessions.sent == [("u1", "session-new", "修一下登录报错")]
    assert sessions.queued == []


async def test_ingress_service_asks_project_for_multiple_deployments() -> None:
    sessions = _Sessions()
    service = ChannelIngressService(
        placements=_Placements(
            [
                AgentPlacement("project-a", "Alpha", "developer-a", "developer"),
                AgentPlacement("project-b", "Beta", "developer-b", "developer"),
            ]
        ),
        bindings=_Bindings(),
        sessions=sessions,
    )

    result = await service.handle_inbound_message(user_id="u1", inbound=_inbound())

    assert result.decision.kind == ChannelRouteDecisionKind.ASK_PROJECT
    assert result.session_id is None
    assert sessions.created == []
    assert sessions.sent == []


async def test_agent_without_placement_opens_a_quick_chat() -> None:
    """No deployment is not an error: the turn runs as a project-less chat,
    and the binding records the materialized chat project (never the sentinel,
    which would make every follow-up start over)."""
    sessions = _Sessions()
    bindings = _Bindings()
    service = ChannelIngressService(
        placements=_Placements([]),
        bindings=bindings,
        sessions=sessions,
    )

    result = await service.handle_inbound_message(user_id="u1", inbound=_inbound())

    assert result.decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert result.session_id == "session-new"
    assert sessions.created[0]["project_id"] == "chat-default"
    assert sessions.created[0]["agent_slug"] == "developer"
    assert sessions.sent == [("u1", "session-new", "修一下登录报错")]
    assert bindings.upserts[0][1].project_id == "chat-project-9"
