from __future__ import annotations

from valuz_agent.modules.channels import (
    AgentChannelResolver,
    AgentPlacement,
    ChannelMentionContext,
    ChannelRouteDecisionKind,
    ChannelRouteKey,
    ChannelThreadBinding,
)
from valuz_agent.modules.channels.resolver import CHAT_PROJECT_SENTINEL


def _placement(
    project_id: str,
    *,
    project_name: str | None = None,
    agent_slug: str = "developer",
    source_agent_slug: str = "developer",
) -> AgentPlacement:
    return AgentPlacement(
        project_id=project_id,
        project_name=project_name or project_id,
        agent_slug=agent_slug,
        source_agent_slug=source_agent_slug,
    )


def _context(
    *,
    mentioned_agent_slug: str = "developer",
    external_thread_id: str | None = "thread-1",
    is_top_level_mention: bool = True,
    continuation_hint: bool = False,
    explicit_continue_hint: bool = False,
    explicit_new_hint: bool = False,
    explicit_project_id: str | None = None,
    explicit_project_name: str | None = None,
) -> ChannelMentionContext:
    return ChannelMentionContext(
        user_id="u1",
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id=external_thread_id,
        mentioned_agent_slug=mentioned_agent_slug,
        is_top_level_mention=is_top_level_mention,
        continuation_hint=continuation_hint,
        explicit_continue_hint=explicit_continue_hint,
        explicit_new_hint=explicit_new_hint,
        explicit_project_id=explicit_project_id,
        explicit_project_name=explicit_project_name,
    )


def test_reply_to_bound_agent_answer_reuses_the_bound_session() -> None:
    resolver = AgentChannelResolver()
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="project-a",
        session_id="session-a",
    )

    decision = resolver.resolve(
        _context(is_top_level_mention=False, continuation_hint=True),
        placements=[_placement("project-a")],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert decision.project_id == "project-a"
    assert decision.session_id == "session-a"


def test_top_level_mention_with_one_deployment_opens_new_session() -> None:
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[_placement("project-a")],
    )

    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "project-a"
    assert decision.session_id is None


def test_top_level_continue_hint_reuses_existing_thread_binding() -> None:
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="project-a",
        session_id="session-a",
    )

    decision = AgentChannelResolver().resolve(
        _context(is_top_level_mention=True, explicit_continue_hint=True),
        placements=[_placement("project-a")],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert decision.project_id == "project-a"
    assert decision.session_id == "session-a"


def test_explicit_new_hint_ignores_existing_thread_binding() -> None:
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="project-a",
        session_id="session-a",
    )

    decision = AgentChannelResolver().resolve(
        _context(
            is_top_level_mention=False,
            continuation_hint=True,
            explicit_new_hint=True,
        ),
        placements=[_placement("project-a")],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "project-a"
    assert decision.session_id is None


def test_route_key_separates_agent_project_and_external_thread() -> None:
    key = AgentChannelResolver.route_key(
        _context(mentioned_agent_slug="developer"),
        project_id="project-a",
    )

    assert key == ChannelRouteKey(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="project-a",
    )


def test_route_key_falls_back_to_chat_when_platform_has_no_thread() -> None:
    key = AgentChannelResolver.route_key(
        _context(external_thread_id=None),
        project_id="project-a",
    )

    assert key.external_thread_id == "chat-1"


def test_top_level_mention_with_multiple_deployments_asks_for_project() -> None:
    decision = AgentChannelResolver().resolve(
        _context(),
        placements=[
            _placement("project-a", project_name="Alpha"),
            _placement("project-b", project_name="Beta"),
        ],
    )

    assert decision.kind == ChannelRouteDecisionKind.ASK_PROJECT
    assert decision.project_id is None
    assert [candidate.project_id for candidate in decision.candidates] == [
        "project-a",
        "project-b",
    ]


def test_explicit_project_hint_selects_matching_deployment() -> None:
    decision = AgentChannelResolver().resolve(
        _context(explicit_project_name="Beta"),
        placements=[
            _placement("project-a", project_name="Alpha"),
            _placement("project-b", project_name="Beta"),
        ],
    )

    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "project-b"


def test_stale_thread_binding_is_not_reused_when_agent_left_that_project() -> None:
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="old-project",
        session_id="old-session",
    )

    decision = AgentChannelResolver().resolve(
        _context(is_top_level_mention=False, continuation_hint=True),
        placements=[
            _placement("project-a", project_name="Alpha"),
            _placement("project-b", project_name="Beta"),
        ],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.ASK_PROJECT
    assert decision.session_id is None


def test_not_runnable_thread_binding_opens_new_session_for_single_deployment() -> None:
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="project-a",
        session_id="archived-session",
        session_accepts_turn=False,
    )

    decision = AgentChannelResolver().resolve(
        _context(is_top_level_mention=False, continuation_hint=True),
        placements=[_placement("project-a")],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "project-a"
    assert decision.session_id is None


def test_running_thread_binding_queues_continuation() -> None:
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="project-a",
        session_id="running-session",
        session_accepts_turn=False,
        session_status="running",
    )

    decision = AgentChannelResolver().resolve(
        _context(is_top_level_mention=False, continuation_hint=True),
        placements=[_placement("project-a")],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.QUEUE_SESSION
    assert decision.project_id == "project-a"
    assert decision.session_id == "running-session"


def test_missing_deployment_falls_back_to_quick_chat() -> None:
    """An agent with no project placement still answers — the product supports
    project-less conversations, so a channel turn opens a quick chat instead of
    refusing with "not deployed"."""
    decision = AgentChannelResolver().resolve(_context(), placements=[])

    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == CHAT_PROJECT_SENTINEL
    assert decision.reason == "no_deployment_quick_chat"
    assert decision.candidates == ()


def test_quick_chat_thread_continues_its_ephemeral_project() -> None:
    """The binding stores the materialized chat project id, which never appears
    in ``placements`` — continuation must not fall back to a brand-new chat."""
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="chat-project-1",
        session_id="session-chat-1",
    )

    decision = AgentChannelResolver().resolve(
        _context(is_top_level_mention=False),
        placements=[],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert decision.project_id == "chat-project-1"
    assert decision.session_id == "session-chat-1"


def test_quick_chat_explicit_new_hint_opens_a_fresh_chat() -> None:
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="chat-project-1",
        session_id="session-chat-1",
    )

    decision = AgentChannelResolver().resolve(
        _context(is_top_level_mention=False, explicit_new_hint=True),
        placements=[],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == CHAT_PROJECT_SENTINEL


def test_quick_chat_running_session_queues() -> None:
    existing = ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id="thread-1",
        agent_slug="developer",
        project_id="chat-project-1",
        session_id="session-chat-1",
        session_accepts_turn=False,
        session_status="running",
    )

    decision = AgentChannelResolver().resolve(
        _context(is_top_level_mention=False),
        placements=[],
        existing_binding=existing,
    )

    assert decision.kind == ChannelRouteDecisionKind.QUEUE_SESSION
    assert decision.session_id == "session-chat-1"


# --- session model: one chat ⇒ one long-lived session --------------------- #


def _chat_binding(
    *,
    project_id: str = "project-a",
    session_id: str = "session-a",
    external_thread_id: str | None = None,
    session_accepts_turn: bool = True,
    session_status: str | None = None,
) -> ChannelThreadBinding:
    return ChannelThreadBinding(
        channel_instance_id="feishu-main",
        external_chat_id="chat-1",
        external_thread_id=external_thread_id or "chat-1",
        agent_slug="developer",
        project_id=project_id,
        session_id=session_id,
        session_accepts_turn=session_accepts_turn,
        session_status=session_status,
    )


def test_plain_chat_message_continues_the_bound_session() -> None:
    """The core of the session model: an ordinary message in a bound chat is a
    follow-up, not a fresh start. (Top-level-starts-new matched group @-mention
    semantics but reads as amnesia in an IM conversation.)"""
    decision = AgentChannelResolver().resolve(
        _context(external_thread_id=None),
        placements=[_placement("project-a")],
        existing_binding=_chat_binding(),
    )

    assert decision.kind == ChannelRouteDecisionKind.REUSE_SESSION
    assert decision.session_id == "session-a"


def test_explicit_new_hint_is_the_only_reset_switch() -> None:
    decision = AgentChannelResolver().resolve(
        _context(external_thread_id=None, explicit_new_hint=True),
        placements=[_placement("project-a")],
        existing_binding=_chat_binding(),
    )

    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
    assert decision.project_id == "project-a"


def test_bound_session_still_running_queues_instead_of_forking() -> None:
    decision = AgentChannelResolver().resolve(
        _context(external_thread_id=None),
        placements=[_placement("project-a")],
        existing_binding=_chat_binding(
            session_accepts_turn=False, session_status="running"
        ),
    )

    assert decision.kind == ChannelRouteDecisionKind.QUEUE_SESSION
    assert decision.session_id == "session-a"


def test_topic_branch_does_not_match_the_main_chat_binding() -> None:
    """A user-opened topic carries its own thread id, so the main chat's
    binding must not answer for it — the branch gets its own session."""
    decision = AgentChannelResolver().resolve(
        _context(external_thread_id="topic-1", is_top_level_mention=False),
        placements=[_placement("project-a")],
        existing_binding=_chat_binding(external_thread_id="chat-1"),
    )

    assert decision.kind == ChannelRouteDecisionKind.NEW_SESSION
