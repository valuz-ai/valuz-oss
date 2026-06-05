"""Activity overview endpoint — `GET /v1/runs`."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query

from valuz_agent.api.deps import get_runs_service
from valuz_agent.modules.runs.service import RunsService

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.get("")
async def list_runs(
    status: str = Query("running", pattern="^(running|finished)$"),
    svc: RunsService = Depends(get_runs_service),
) -> dict[str, list[dict[str, Any]]]:
    """List runs for the activity overview.

    ``status=running`` (default) returns in-flight runs; ``finished`` returns
    recently completed/failed runs.
    """
    runs = await svc.list_runs(status=status)
    return {"runs": [asdict(r) for r in runs]}
