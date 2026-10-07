"""Stable owner-explicit Session commands for overlays and application plugins."""

from __future__ import annotations

import builtins
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from valuz_agent.modules.sessions.input_receipts import SessionInputReceipt, get_input, receipt_of


@asynccontextmanager
async def _session_service() -> AsyncGenerator[Any, None]:
    from valuz_agent.api.deps import get_session_service

    generator = get_session_service()
    service = await generator.__anext__()
    try:
        yield service
    finally:
        await generator.aclose()


class SessionLibrary:
    """A normal chat remains the conversation container; the caller supplies its owner."""

    def __init__(self, user_id: str) -> None:
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id is required")
        self.user_id = user_id

    async def create(
        self,
        *,
        name: str | None = None,
        project_id: str | None = None,
        agent_slug: str = "valurion",
        provider_id: str | None = None,
        model_id: str | None = None,
        runtime_id: str | None = None,
    ) -> Any:
        from valuz_agent.facade.projects import ProjectLibrary

        if project_id is None:
            project = await ProjectLibrary().create_chat(self.user_id, name=name or "Chat")
            project_id = project.id
        async with _session_service() as service:
            return await service.create_session(
                project_id=project_id,
                title=name,
                agent_slug=agent_slug,
                provider_id=provider_id,
                model_id=model_id,
                runtime_id=runtime_id,
                user_id=self.user_id,
            )

    async def get(self, session_id: str) -> Any | None:
        from valuz_agent.modules.sessions.errors import SessionNotFound

        async with _session_service() as service:
            try:
                return await service.get_session(session_id, user_id=self.user_id)
            except SessionNotFound:
                return None

    async def list(self, *, project_id: str | None = None) -> list[Any]:
        async with _session_service() as service:
            return list(await service.list_sessions(project_id=project_id, user_id=self.user_id))

    async def events(
        self, session_id: str, *, after_seq: int = 0, limit: int = 100
    ) -> builtins.list[Any]:
        """Read a bounded event history through the normal owner-checked session service."""
        if after_seq < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid event window")
        async with _session_service() as service:
            if after_seq:
                events = await service.list_events(session_id, self.user_id, after_seq=after_seq)
            else:
                events, _ = await service.list_events_window(session_id, self.user_id, turn_limit=5)
        return builtins.list(events[-limit:])

    async def enqueue_background(
        self,
        session_id: str,
        text: str,
        *,
        input_id: str | None = None,
        task_check_config: Any | None = None,
        presentation: dict[str, str] | None = None,
    ) -> SessionInputReceipt:
        """Idempotently append background work without claiming the human's staged files.

        ``input_id`` is a globally unique caller-owned operation id. Retries must
        use the same owner, session and text; an id reused for different work fails.
        A paused queue stays paused until its owner resumes it.
        """
        from valuz_agent.adapters.data_reader import data_reader
        from valuz_agent.infra.db import async_unit_of_work
        from valuz_agent.infra.eventbus import event_bus
        from valuz_agent.modules.sessions.datastore import SessionDatastore
        from valuz_agent.modules.sessions.errors import (
            QueueFull,
            SessionNotFound,
            SessionNotRunnable,
        )
        from valuz_agent.modules.sessions.models import QueuedInputRow
        from valuz_agent.modules.sessions.presentation import validate_presentation
        from valuz_agent.modules.sessions.run_orchestrator import schedule_drain
        from valuz_agent.modules.sessions.service import QUEUE_SOFT_CAP
        from valuz_agent.modules.sessions.task_checks import CONFIG_KEY, fresh_config

        presentation = validate_presentation(presentation)
        if not text.strip():
            raise ValueError("background input must not be empty")
        key = input_id or uuid4().hex
        if len(key) > 36:
            raise ValueError("input_id must be at most 36 characters")
        session = await data_reader().get_session(self.user_id, session_id)
        if session is None:
            raise SessionNotFound()
        if str(session.status) in {"cancelled", "archived", "terminated"}:
            raise SessionNotRunnable("Session cannot accept background input")
        meta = (session.metadata or {}).get("valuz") or {}
        if meta.get("task_id"):
            raise SessionNotRunnable("Background inputs require a chat session")

        check_config = fresh_config(task_check_config).model_dump(mode="json")
        async with async_unit_of_work() as db:
            ds = SessionDatastore(db)
            existing = await ds.get_queued(self.user_id, session_id, key)
            if existing is None:
                if await ds.count_queued(self.user_id, session_id) >= QUEUE_SOFT_CAP:
                    raise QueueFull()
                row = QueuedInputRow(
                    id=key,
                    session_id=session_id,
                    project_id=meta.get("project_id"),
                    input={
                        "text": text,
                        "attachments": [],
                        "source": "background",
                        **({"presentation": presentation} if presentation is not None else {}),
                        CONFIG_KEY: check_config,
                    },
                    status="queued",
                )
                try:
                    existing = await ds.create_queued(self.user_id, row)
                except IntegrityError:
                    await db.rollback()
                    existing = await ds.get_queued(self.user_id, session_id, key)
                    if existing is None:
                        raise ValueError("input_id belongs to another operation") from None
            if (existing.input or {}).get("text") != text or (existing.input or {}).get(
                "source"
            ) != "background":
                raise ValueError("input_id was reused for different content")
            if (existing.input or {}).get("presentation") != presentation:
                raise ValueError("input_id was reused for different presentation")
            previous_config = (existing.input or {}).get(CONFIG_KEY) or {}
            stable_config = {
                k: v for k, v in check_config.items() if k not in {"run_id", "revision"}
            }
            previous_stable = {
                k: v for k, v in previous_config.items() if k not in {"run_id", "revision"}
            }
            if stable_config != previous_stable:
                raise ValueError("input_id was reused for different execution policy")
            receipt = receipt_of(existing)
        if receipt.status == "queued":
            # Always schedule even when busy: the existing drain waits for true
            # idle, covering turns started by a remote kernel or another process.
            schedule_drain(session_id, event_bus, user_id=self.user_id)
        return receipt

    async def wake_input(self, session_id: str, input_id: str) -> bool:
        """Re-kick an owned pending background input without rewriting/replaying it.

        A transient worker wake can be lost. Durable reconciliation may safely
        call this again; the existing drain lease, FIFO/CAS and pause/admission
        checks remain the execution authority.
        """
        from valuz_agent.adapters import kernel_client
        from valuz_agent.infra.eventbus import event_bus
        from valuz_agent.modules.sessions.errors import SessionNotFound
        from valuz_agent.modules.sessions.run_orchestrator import schedule_drain

        receipt = await get_input(self.user_id, session_id, input_id)
        if receipt is None or receipt.status != "queued" or receipt.source != "background":
            return False
        session = await kernel_client.get_session(self.user_id, session_id)
        if session is None or session.user_id != self.user_id:
            raise SessionNotFound()
        if str(session.status) in {"cancelled", "archived", "terminated"}:
            return False
        schedule_drain(session_id, event_bus, user_id=self.user_id)
        return True

    async def cancel_input(self, session_id: str, input_id: str) -> bool:
        """Cancel pending input; an already dispatched turn needs interrupt()."""
        from valuz_agent.modules.sessions.errors import QueuedInputNotFound

        async with _session_service() as service:
            try:
                await service.delete_queued(session_id, input_id, user_id=self.user_id)
            except QueuedInputNotFound:
                return False
        return True

    async def interrupt(self, session_id: str) -> Any:
        async with _session_service() as service:
            return await service.interrupt(session_id, user_id=self.user_id)

    async def get_input(self, session_id: str, input_id: str) -> SessionInputReceipt | None:
        return await get_input(self.user_id, session_id, input_id)


__all__ = ["SessionLibrary", "SessionInputReceipt"]
