from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.analytics.datastore import AnalyticsDatastore
from valuz_agent.modules.analytics.service import AnalyticsService

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get("/usage")
async def get_usage(
    year: int = Query(default_factory=lambda: date.today().year),
    month: int = Query(default_factory=lambda: date.today().month, ge=1, le=12),
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, Any]:
    return await AnalyticsService(AnalyticsDatastore(db)).get_monthly_usage(year, month)
