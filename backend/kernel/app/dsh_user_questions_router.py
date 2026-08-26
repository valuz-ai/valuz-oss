"""Loopback endpoint for the dsh user-questions bridge.

``POST {KERNEL_API_PREFIX}/v1/dsh/user-questions/{token}/ask`` and
``GET  {KERNEL_API_PREFIX}/v1/dsh/user-questions/{token}/ask/{ask_id}`` —
the only surface the dsh subprocess's ``valuz-dsh-kernel-bridge`` plugin
talks to. The plugin forwards ``ctx.userQuestions.ask()`` here (the
``exit_plan_mode`` plan review and ``ask_user_question`` clarifying
batches); the kernel parks it as a standard ``requires_action`` and the
GET long-polls the decision, releasing the dsh tool call so the turn
continues natively.

Auth model mirrors ``ptc_router``: the bridge token IS the credential —
random, minted per subprocess spawn (``DeepSeekHarnessRuntime``), revoked
at close. The standalone kernel's bearer middleware exempts this path for
exactly that reason (the subprocess holds no kernel bearer); see
``app/main.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.routes import KERNEL_API_PREFIX
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from src.core.user_questions_bridge import get_user_questions_bridge

logger = logging.getLogger(__name__)

DSH_UQ_ROUTE_SEGMENT = "/v1/dsh/user-questions"

router = APIRouter(prefix=f"{KERNEL_API_PREFIX}{DSH_UQ_ROUTE_SEGMENT}", tags=["dsh-user-questions"])


class AskRequest(BaseModel):
    questions: list[dict[str, Any]] = Field(min_length=1)


@router.post("/{token}/ask")
async def start_ask(token: str, request: AskRequest) -> dict[str, Any]:
    record = get_user_questions_bridge(token)
    if record is None:
        # Unknown OR revoked token — same answer on purpose.
        raise HTTPException(status_code=404, detail="bridge token is not active")
    try:
        ask_id = await record.start_ask(request.questions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ask_id": ask_id}


@router.get("/{token}/ask/{ask_id}")
async def poll_ask(
    token: str,
    ask_id: str,
    wait_seconds: float = Query(default=0.0, ge=0.0, le=30.0),
) -> dict[str, Any]:
    record = get_user_questions_bridge(token)
    if record is None:
        raise HTTPException(status_code=404, detail="bridge token is not active")
    try:
        state = await record.wait_answer(ask_id, wait_seconds)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="unknown ask") from exc
    if state is None:
        return {"status": "pending"}
    return state
