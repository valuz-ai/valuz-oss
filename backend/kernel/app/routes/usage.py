"""Usage aggregate routes — /api/v1/usage.

Public read API over the kernel's ``messages`` + ``sessions`` tables so
hosts never query kernel storage directly (the TD-007 boundary debt this
endpoint retires).
"""

from __future__ import annotations

from typing import Annotated, Any

from app.dependencies import get_owner_id, get_store
from app.schemas import UsageRollupResponse
from app.serializers import usage_row_to_data
from fastapi import APIRouter, Depends, Query
from src.core import StorePort

router = APIRouter(prefix="/api/v1/usage", tags=["usage"])

StoreDep = Annotated[StorePort, Depends(get_store)]
OwnerDep = Annotated[str, Depends(get_owner_id)]


@router.get("", response_model=UsageRollupResponse)
async def get_usage_rollup(
    store: StoreDep,
    owner: OwnerDep,
    start_ms: Annotated[int, Query(ge=0)],
    end_ms: Annotated[int, Query(ge=0)],
) -> dict[str, Any]:
    """Token/turn usage per (UTC day, model) for ``owner``'s completed messages
    whose ``started_at`` falls in the half-open ``[start_ms, end_ms)`` window."""
    rows = await store.usage_rollup(owner, start_ms, end_ms)
    return {"data": [usage_row_to_data(r) for r in rows]}
