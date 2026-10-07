"""Owner-scoped durable input receipts; no inference from a session's latest turn."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.modules.sessions.datastore import SessionDatastore

TERMINAL_INPUT_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class SessionInputReceipt:
    id: str
    session_id: str
    status: str
    completed_at: int | None = None
    output_message_id: str | None = None
    result_summary: str | None = None
    error_message: str | None = None
    source: str = "user"
    input: dict[str, Any] = field(default_factory=dict)


def receipt_of(row: Any) -> SessionInputReceipt:
    values: dict[str, Any] = {
        name: getattr(row, name, None) for name in SessionInputReceipt.__dataclass_fields__
    }
    values["input"] = dict(row.input or {})
    values["source"] = values["input"].get("source", "user")
    return SessionInputReceipt(**values)


async def get_input(user_id: str, session_id: str, input_id: str) -> SessionInputReceipt | None:
    if not user_id:
        raise ValueError("user_id is required")
    async with async_unit_of_work(commit=False) as db:
        row = await SessionDatastore(db).get_queued(user_id, session_id, input_id)
        return receipt_of(row) if row is not None else None


async def complete_input(
    input_id: str,
    final_status: str,
    message: Any,
    error: BaseException | None,
) -> None:
    reason = getattr(message, "stop_reason", None)
    reason_message = (
        reason.get("message") if isinstance(reason, dict) else getattr(reason, "message", None)
    )
    status = (
        "cancelled"
        if final_status == "interrupted" or isinstance(error, asyncio.CancelledError)
        else "failed"
        if error is not None
        or final_status not in {"idle", "created"}
        or str(getattr(message, "status", "")) in {"errored", "failed"}
        else "completed"
    )
    text = getattr(message, "assistant_message", None) or getattr(message, "text", None)
    if not text:
        output = getattr(message, "output", None)
        if isinstance(output, dict):
            text = output.get("text")
        elif isinstance(output, str):
            text = output
    async with async_unit_of_work() as db:
        await SessionDatastore(db).mark_queued_status(
            input_id,
            status,
            error_message=(str(error) or type(error).__name__) if error else reason_message,
            output_message_id=getattr(message, "id", None),
            result_summary=str(text)[:2000] if text else None,
        )
