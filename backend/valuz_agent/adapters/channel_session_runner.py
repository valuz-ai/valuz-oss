"""Adapter from channel ingress to SessionService."""

from __future__ import annotations

from valuz_agent.modules.channels.service import ChannelSessionRef
from valuz_agent.modules.sessions.service import SessionService


class SessionServiceChannelRunner:
    def __init__(self, session_service: SessionService) -> None:
        self._session_service = session_service

    async def create_session(
        self,
        *,
        user_id: str,
        project_id: str,
        agent_slug: str,
        origin: str,
        creation_context: dict[str, str],
    ) -> ChannelSessionRef:
        return await self._session_service.create_session(
            project_id=project_id,
            agent_slug=agent_slug,
            origin=origin,
            creation_context=creation_context,
            user_id=user_id,
        )

    async def send_message(self, *, user_id: str, session_id: str, content: str) -> None:
        await self._session_service.send_message(session_id, content, user_id=user_id)

    async def get_session_status(self, *, user_id: str, session_id: str) -> str | None:
        session = await self._session_service.get_session(session_id, user_id=user_id)
        return session.status

    async def enqueue_message(self, *, user_id: str, session_id: str, content: str) -> None:
        await self._session_service.enqueue(session_id, content, user_id=user_id)


__all__ = ["SessionServiceChannelRunner"]
