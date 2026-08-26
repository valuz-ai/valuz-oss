"""Task → notification PROJECTORS.

Mirror task failure events into the durable notification ledger (the
"强提醒" persistence: survives restart, drives the badge + OS notification,
clears on resume). Lives in the notifications module — this is notification
domain logic; task code calls it as a sibling service API.
"""

from __future__ import annotations

from valuz_agent.modules.notifications.service import notification_service


async def record_task_completion_notification(
    *,
    task_id: str,
    project_id: str,
    event_id: str,
    summary: str | None,
    task_title: str | None = None,
    user_id: str,
) -> None:
    """Mirror one completed task event into the durable notification ledger.

    Ingested ALREADY RESOLVED: a finished task asks nothing of the user, so it
    belongs in history (and in the OS toast that fires on arrival), not in the
    action inbox next to questions and blocked tasks. Left unresolved it sat
    in "未处理" wearing a Resume button, and a day of successful runs buried
    the failures that do need attention."""

    title = task_title
    if title is None:
        try:
            from valuz_agent.modules.tasks import service as task_queries

            task, _runs = await task_queries.get_task_with_runs(user_id, task_id)
            title = task.title if task is not None else task_id
        except Exception:  # noqa: BLE001
            title = task_id

    await notification_service.ingest(
        user_id or "",
        dedup_key=f"c:{event_id}",
        kind="task_completed",
        title=title or task_id,
        body=summary or "",
        route=f"/tasks/{task_id}",
        action="none",
        urgency="info",
        task_id=task_id,
        project_id=project_id,
        source_event_id=event_id,
        payload={"summary": summary or ""},
        resolved=True,
    )


async def record_task_failure_notification(
    *,
    task_id: str,
    project_id: str,
    event_id: str,
    event_type: str,
    reason: str | None,
    task_title: str | None = None,
    user_id: str,
) -> None:
    """Failure PROJECTOR: mirror a ``task_blocked`` / ``kickoff_failed`` event
    into the durable notification ledger (kind=``task_failed``, action=resume).

    This is the "强提醒" persistence: a failure is now a durable attention item
    that survives restart, drives the badge + OS notification, and clears when
    the user resumes (see ``notification_service.resolve_task`` on resume).
    Deduped by event id. Best-effort — never break the failure's own event flow.

    ``task_title`` is looked up if not supplied so call sites stay terse.
    """

    title = task_title
    if title is None:
        try:
            from valuz_agent.modules.tasks import service as task_queries

            task, _runs = await task_queries.get_task_with_runs(user_id, task_id)
            title = task.title if task is not None else task_id
        except Exception:  # noqa: BLE001
            title = task_id

    await notification_service.ingest(
        user_id or "",
        dedup_key=f"f:{event_id}",
        kind="task_failed",
        title=title or task_id,  # frontend builds "任务受阻: {title}"
        body=reason or "",
        route=f"/tasks/{task_id}",
        action="resume",
        task_id=task_id,
        project_id=project_id,
        source_event_id=event_id,
        payload={"reason": reason, "event_type": event_type},
    )

