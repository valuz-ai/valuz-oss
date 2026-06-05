from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.db import get_async_session
from valuz_agent.modules.resources.service import ResourceFacade

router = APIRouter(tags=["resources"])


@router.post("/v1/resources/sync")
async def sync_resources(
    db: AsyncSession = Depends(get_async_session),
) -> dict[str, int]:
    """Trigger a remote resource manifest pull. No-op in OSS."""
    facade = ResourceFacade(db, remote_port=None)
    return await facade.sync_remote_manifest()
