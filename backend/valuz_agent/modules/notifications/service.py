"""NotificationService — the single writer for the notification ledger
(docs/design/notifications.md §0.2).

Every source (question projector, failure projector, …) calls ``ingest`` /
``resolve``; every UI delivery surface reads the durable ``valuz_notification``
table. The SSE stream (``/v1/notifications/stream``) reconciles by re-reading it
on an interval, so cross-pod UI delivery never depends on an in-process bus.

New rows also publish the additive ``notification.created`` extension event.
It is a best-effort side-effect hook for overlays (for example an external push
gateway), not a replacement for the durable ledger or DB-poll SSE.
"""

from __future__ import annotations

import logging
from typing import Any

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.notifications.datastore import NotificationDatastore
from valuz_agent.modules.notifications.models import NotificationRow
from valuz_agent.modules.notifications.schemas import NotificationEntry

logger = logging.getLogger(__name__)

NOTIFICATION_CREATED = "notification.created"


def _entry(row: NotificationRow) -> NotificationEntry:
    return NotificationEntry.model_validate(row)


class NotificationService:
    """Durable-only notification ledger. Writes go to ``valuz_notification``;
    UI delivery is by DB-poll SSE; the created hook is side-effect-only."""

    # ---- Sources (projectors call these) ----------------------------

    async def ingest(
        self,
        user_id: str,
        *,
        dedup_key: str,
        kind: str,
        title: str,
        body: str = "",
        route: str | None = None,
        action: str = "none",
        urgency: str = "actionable",
        task_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        pending_id: str | None = None,
        source_event_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> NotificationEntry | None:
        """Idempotent create — a re-fire for the same subject upserts the same
        row. Best-effort: a store failure is logged, not raised, so a projector
        never breaks its own event flow. The open stream surfaces the new row on
        its next poll."""
        try:
            async with async_unit_of_work() as db:
                datastore = NotificationDatastore(db)
                row, created = await datastore.upsert(
                    user_id,
                    dedup_key=dedup_key,
                    kind=kind,
                    title=title,
                    body=body,
                    route=route,
                    action=action,
                    urgency=urgency,
                    task_id=task_id,
                    project_id=project_id,
                    session_id=session_id,
                    pending_id=pending_id,
                    source_event_id=source_event_id,
                    payload=payload,
                )
                entry = _entry(row)
                unread = await datastore.count_unread(user_id) if created else None
            if created:
                try:
                    from valuz_agent.infra.eventbus import event_bus

                    event_bus.publish(
                        NOTIFICATION_CREATED,
                        owner_user_id=user_id,
                        notification=entry.model_dump(mode="json"),
                        unread=unread,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "notifications: notification.created publish failed for %s",
                        dedup_key,
                        exc_info=True,
                    )
            return entry
        except Exception:  # noqa: BLE001
            logger.warning("notifications: ingest failed for %s", dedup_key, exc_info=True)
            return None

    async def resolve(self, user_id: str, dedup_key: str) -> None:
        """Mark the notification behind ``dedup_key`` resolved. Best-effort."""
        try:
            async with async_unit_of_work() as db:
                await NotificationDatastore(db).resolve_by_dedup(user_id, dedup_key)
        except Exception:  # noqa: BLE001
            logger.warning("notifications: resolve failed for %s", dedup_key, exc_info=True)

    async def resolve_pending(self, pending_id: str) -> None:
        """Resolve a question notification by its (globally-unique) ``pending_id``
        without the owner up front — the decisions aggregator calls this on
        ``action_resolved`` for conversation questions, which it never tracks in
        memory (so no owner is known). Idempotent + best-effort."""
        try:
            async with async_unit_of_work() as db:
                await NotificationDatastore(db).resolve_by_pending_id(pending_id)
        except Exception:  # noqa: BLE001
            logger.warning(
                "notifications: resolve_pending failed for %s", pending_id, exc_info=True
            )

    async def resolve_task(
        self, user_id: str, task_id: str, kinds: tuple[str, ...] = ("task_failed",)
    ) -> None:
        """Resolve every open ``kinds`` notification for a task — called when the
        task is resumed / abandoned so a stale "failed" item doesn't keep the
        badge lit after the user has dealt with it. Best-effort."""
        try:
            async with async_unit_of_work() as db:
                await NotificationDatastore(db).resolve_open_by_task(user_id, task_id, kinds)
        except Exception:  # noqa: BLE001
            logger.warning("notifications: resolve_task failed for %s", task_id, exc_info=True)

    async def resolve_session_failures(
        self, user_id: str, session_id: str, kinds: tuple[str, ...] = ("run_failed",)
    ) -> None:
        """Resolve every open ``kinds`` notification for a conversation session —
        called on a clean turn so a recovered conversation doesn't keep the badge
        lit with a stale failure. Best-effort."""
        try:
            async with async_unit_of_work() as db:
                await NotificationDatastore(db).resolve_open_by_session(
                    user_id, session_id, kinds
                )
        except Exception:  # noqa: BLE001
            logger.warning(
                "notifications: resolve_session_failures failed for %s", session_id, exc_info=True
            )

    # ---- Reads / user actions (routes call these) -------------------

    async def snapshot(self, user_id: str) -> tuple[list[NotificationEntry], int]:
        async with async_unit_of_work(commit=False) as db:
            ds = NotificationDatastore(db)
            rows = await ds.list_open(user_id)
            unread = await ds.count_unread(user_id)
        return [_entry(r) for r in rows], unread

    async def mark_read(self, user_id: str, notification_id: str) -> None:
        async with async_unit_of_work() as db:
            await NotificationDatastore(db).mark_read(user_id, notification_id)

    async def mark_all_read(self, user_id: str) -> None:
        async with async_unit_of_work() as db:
            await NotificationDatastore(db).mark_all_read(user_id)

    async def dismiss(self, user_id: str, notification_id: str) -> None:
        """User-driven resolve (e.g. swiping away a failure they acknowledge)."""
        async with async_unit_of_work() as db:
            await NotificationDatastore(db).resolve_by_id(user_id, notification_id)


# Process singleton.
notification_service = NotificationService()
