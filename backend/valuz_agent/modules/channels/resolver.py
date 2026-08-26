"""Resolve external channel mentions into project-bound agent sessions."""

from __future__ import annotations

from collections.abc import Iterable

from valuz_agent.modules.channels.schemas import (
    AgentChannelRouteDecision,
    AgentPlacement,
    ChannelMentionContext,
    ChannelRouteDecisionKind,
    ChannelRouteKey,
    ChannelThreadBinding,
)

# Sentinel project id understood by ``SessionService``: it materializes a fresh
# isolated ``kind="chat"`` project per session. Used when the mentioned agent
# has no project placement — a channel turn then behaves like a quick chat.
CHAT_PROJECT_SENTINEL = "chat-default"


class AgentChannelResolver:
    """Pure router for IM channel mentions.

    Platform adapters should normalize inbound webhook payloads into
    ``ChannelMentionContext``. Persistence/services provide the current agent
    placements and any thread binding. This resolver then decides whether the
    turn can continue an existing session, must open a new one, or needs a
    project clarification from the human.

    **Session model** — one chat conversation maps to one long-lived session:

    - A bound conversation continues by default, for as long as the user keeps
      talking. There is no idle expiry; a session ends only when the user says
      so. (The earlier rule — every top-level message starts fresh — matched
      group ``@`` semantics, but read as amnesia in an ordinary IM chat.)
    - ``explicit_new_hint`` ("新开" / "重开" / "new session" …) is the one reset
      switch, and it is always user-initiated, so the context reset is never a
      surprise.
    - A platform thread (a Feishu topic the *user* opens) carries its own
      binding through the route key, so it branches off without disturbing the
      main conversation.
    - Group chats share one session per (chat, agent): the sender is not part
      of the route key, so everyone contributes to the same context.
    """

    def resolve(
        self,
        context: ChannelMentionContext,
        *,
        placements: Iterable[AgentPlacement],
        existing_binding: ChannelThreadBinding | None = None,
        recent_binding: ChannelThreadBinding | None = None,
        chat_project_id: str | None = None,
    ) -> AgentChannelRouteDecision:
        """``chat_project_id`` is the chat's bound project ("this group is that
        project"). It outranks the placement heuristics: once a group is bound,
        placement ambiguity stops being a question anyone is asked (§4.1)."""
        active_placements = tuple(
            placement
            for placement in placements
            if self._placement_matches_agent(placement, context.mentioned_agent_slug)
        )
        by_project_id = {placement.project_id: placement for placement in active_placements}
        wants_continuation = self._wants_continuation(context)

        explicit = self._select_explicit_project(context, active_placements)
        # The chat's bound project, when the mentioned agent is actually on that
        # team. A binding pointing at a project the agent was never deployed to
        # is a misconfiguration; falling through to the placement heuristics
        # degrades gracefully instead of failing session creation later.
        bound = by_project_id.get(chat_project_id) if chat_project_id else None

        # Quick chat covers two cases:
        #  - the agent is deployed nowhere, so there is no project to run in;
        #  - a 1:1 chat with nothing said about a project. Deployment is not
        #    consent to route someone's private chat into a project: deploying
        #    an agent to one project would otherwise silently move every DM
        #    with it into that project's context and files.
        # Naming a project, or binding this chat on purpose, still wins.
        if not active_placements or (
            context.is_direct_chat and explicit is None and bound is None
        ):
            return self._quick_chat_decision(
                context, existing_binding, project_placements=set(by_project_id)
            )

        if explicit is not None:
            return self._new_session(context, explicit, reason="explicit_project_match")

        if not context.explicit_new_hint:
            for binding, reason in (
                (existing_binding, "thread_binding"),
                (recent_binding, "recent_continuation"),
            ):
                if binding is None:
                    continue
                if not self._binding_matches_context(context, binding):
                    continue
                if binding.project_id is None or binding.session_id is None:
                    continue
                if binding.project_id not in by_project_id:
                    continue
                # A bound chat continues only its own project's lineage — a
                # leftover lineage from before the binding must not answer for
                # the project the group now stands for.
                if bound is not None and binding.project_id != chat_project_id:
                    continue
                if binding is recent_binding and not wants_continuation:
                    continue
                if not binding.session_accepts_turn:
                    if binding.session_status == "running":
                        return self._decision(
                            context,
                            ChannelRouteDecisionKind.QUEUE_SESSION,
                            project_id=binding.project_id,
                            session_id=binding.session_id,
                            reason=f"{reason}_running",
                        )
                    continue
                return self._decision(
                    context,
                    ChannelRouteDecisionKind.REUSE_SESSION,
                    project_id=binding.project_id,
                    session_id=binding.session_id,
                    reason=reason,
                )

        if bound is not None:
            return self._new_session(context, bound, reason="chat_project_binding")

        if len(active_placements) == 1:
            return self._new_session(context, active_placements[0], reason="single_deployment")

        return self._decision(
            context,
            ChannelRouteDecisionKind.ASK_PROJECT,
            reason="multiple_deployments",
            candidates=active_placements,
        )

    def _quick_chat_decision(
        self,
        context: ChannelMentionContext,
        existing_binding: ChannelThreadBinding | None,
        project_placements: set[str] | None = None,
    ) -> AgentChannelRouteDecision:
        """Run the turn as a quick chat in an ephemeral project.

        ``CHAT_PROJECT_SENTINEL`` is materialized per session by
        SessionService. Continuation works through the thread binding, whose
        project id is a concrete chat project and therefore never appears in
        ``placements`` — which is also how a *project* lineage is told apart
        from a quick-chat one: a chat that used to run in a project keeps that
        lineage, and continuing it would drop the turn straight back into the
        project this branch just decided against.
        """
        if not context.explicit_new_hint and existing_binding is not None:
            in_project = (
                existing_binding.project_id in project_placements
                if project_placements
                else False
            )
            reuse = (
                None if in_project else self._reuse_chat_binding(context, existing_binding)
            )
            if reuse is not None:
                return reuse
        return self._decision(
            context,
            ChannelRouteDecisionKind.NEW_SESSION,
            project_id=CHAT_PROJECT_SENTINEL,
            reason="direct_chat_quick_chat"
            if context.is_direct_chat
            else "no_deployment_quick_chat",
        )

    def _reuse_chat_binding(
        self,
        context: ChannelMentionContext,
        binding: ChannelThreadBinding,
    ) -> AgentChannelRouteDecision | None:
        """Continue a quick-chat thread bound to an ephemeral chat project.

        Placement-based continuation cannot see these bindings: the project id
        they carry is a per-session chat project, never a deployment.
        """
        if not self._binding_matches_context(context, binding):
            return None
        if binding.project_id is None or binding.session_id is None:
            return None
        if not binding.session_accepts_turn:
            if binding.session_status == "running":
                return self._decision(
                    context,
                    ChannelRouteDecisionKind.QUEUE_SESSION,
                    project_id=binding.project_id,
                    session_id=binding.session_id,
                    reason="quick_chat_binding_running",
                )
            return None
        return self._decision(
            context,
            ChannelRouteDecisionKind.REUSE_SESSION,
            project_id=binding.project_id,
            session_id=binding.session_id,
            reason="quick_chat_binding",
        )

    @staticmethod
    def route_key(context: ChannelMentionContext, *, project_id: str) -> ChannelRouteKey:
        external_thread_id = context.external_thread_id or context.external_chat_id
        return ChannelRouteKey(
            channel_instance_id=context.channel_instance_id,
            external_chat_id=context.external_chat_id,
            external_thread_id=external_thread_id,
            agent_slug=context.mentioned_agent_slug,
            project_id=project_id,
        )

    @staticmethod
    def _placement_matches_agent(placement: AgentPlacement, mentioned_agent_slug: str) -> bool:
        return mentioned_agent_slug in {placement.agent_slug, placement.source_agent_slug}

    @staticmethod
    def _binding_matches_context(
        context: ChannelMentionContext,
        binding: ChannelThreadBinding,
    ) -> bool:
        if binding.channel_instance_id != context.channel_instance_id:
            return False
        if binding.external_chat_id != context.external_chat_id:
            return False
        if binding.agent_slug != context.mentioned_agent_slug:
            return False
        if context.external_thread_id is None:
            return True
        return binding.external_thread_id == context.external_thread_id

    @staticmethod
    def _select_explicit_project(
        context: ChannelMentionContext,
        placements: tuple[AgentPlacement, ...],
    ) -> AgentPlacement | None:
        explicit_name = _normalize_project_name(context.explicit_project_name)
        for placement in placements:
            if context.explicit_project_id and placement.project_id == context.explicit_project_id:
                return placement
            if explicit_name and _normalize_project_name(placement.project_name) == explicit_name:
                return placement
        return None

    def _new_session(
        self,
        context: ChannelMentionContext,
        placement: AgentPlacement,
        *,
        reason: str,
    ) -> AgentChannelRouteDecision:
        return self._decision(
            context,
            ChannelRouteDecisionKind.NEW_SESSION,
            project_id=placement.project_id,
            reason=reason,
        )

    @staticmethod
    def _decision(
        context: ChannelMentionContext,
        kind: ChannelRouteDecisionKind,
        *,
        reason: str,
        project_id: str | None = None,
        session_id: str | None = None,
        candidates: tuple[AgentPlacement, ...] = (),
    ) -> AgentChannelRouteDecision:
        return AgentChannelRouteDecision(
            kind=kind,
            agent_slug=context.mentioned_agent_slug,
            project_id=project_id,
            session_id=session_id,
            reason=reason,
            candidates=candidates,
        )

    @staticmethod
    def _wants_continuation(context: ChannelMentionContext) -> bool:
        return context.continuation_hint or context.explicit_continue_hint


def _normalize_project_name(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().casefold().split())
    return normalized or None


__all__ = ["AgentChannelResolver"]
