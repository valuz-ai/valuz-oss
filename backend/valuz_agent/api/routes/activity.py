"""Unified activity feed endpoint (see ``modules/activity``).

``GET /v1/activity`` — one cursor-paginated, time-merged list of chat sessions +
task entities. Serves both the project-home tabs (``project_id`` set) and the
global 动态 history list (``project_id`` omitted).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.modules.activity import service as activity_service
from valuz_agent.modules.activity.schemas import ActivityPage

router = APIRouter(prefix="/v1/activity", tags=["activity"])


@router.get("")
async def list_activity(
    project_id: str | None = Query(
        default=None, description="Scope to one project; omit for the global feed."
    ),
    tab: str = Query(default="all", description="all | chat | task | automation | playbook"),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None, description="Opaque keyset cursor."),
    user_id: str = Depends(get_current_user_id),
) -> ActivityPage:
    return await activity_service.list_activity(
        user_id, project_id=project_id, tab=tab, limit=limit, cursor=cursor
    )
