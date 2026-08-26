"""Typed errors raised out of the tasks module."""

from __future__ import annotations

from valuz_agent.infra.errors import ConflictError


class TaskLeadSessionInUse(ConflictError):
    """Refuses to delete the conversation that is driving a live task.

    A task's lead session is not an ordinary chat: it *is* the task's execution.
    Deleting it leaves the task with no lead run, which nothing can repair —
    ``pick_lead_run`` returns None, so recovery declines the task and the health
    monitor's "no lead run at all" branch deliberately does nothing. The task
    would sit `active` forever with no actor and no way back.
    """

    error_code = 409_760
    message = "This conversation is running a task. Stop the task before deleting it."


__all__ = ["TaskLeadSessionInUse"]
