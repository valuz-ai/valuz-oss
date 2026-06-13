from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession


class ResourceFacade:
    """Unified coordination layer for built-in resource guard + remote sync.

    Does NOT own data -- delegates to per-type services.
    """

    def __init__(
        self,
        db: AsyncSession,
        remote_port: Any | None = None,
    ):
        self._db = db
        self._remote = remote_port

    async def delete_resource(self, resource_id: str, resource_type: str) -> None:
        if resource_type == "agent":
            from valuz_agent.infra.auth_context import require_current_user_id
            from valuz_agent.modules.agents.service import AgentService

            agent_svc = AgentService(self._db)  # type: ignore[arg-type]
            await agent_svc.delete_agent(require_current_user_id(), resource_id)
        elif resource_type == "connector":
            from valuz_agent.infra.config import settings
            from valuz_agent.infra.secret_store import FileSecretStore
            from valuz_agent.modules.connectors.datastore import ConnectorDatastore
            from valuz_agent.modules.connectors.service import ConnectorService

            conn_svc = ConnectorService(
                ConnectorDatastore(self._db),
                FileSecretStore(settings.secrets_dir),
            )
            from valuz_agent.infra.auth_context import require_current_user_id

            await conn_svc.delete_connector(require_current_user_id(), resource_id)
        elif resource_type == "skill":
            from valuz_agent.infra.auth_context import require_current_user_id
            from valuz_agent.modules.skills.datastore import SkillDatastore

            ds = SkillDatastore(self._db)
            await ds.delete(require_current_user_id(), resource_id)
        else:
            raise ValueError(f"Unknown resource type: {resource_type}")

    async def sync_remote_manifest(self) -> dict[str, int]:
        """Pull and apply the remote resource manifest.

        Returns {created: N, updated: N, deleted: N, errors: N}.
        """
        if self._remote is None:
            return {"created": 0, "updated": 0, "deleted": 0, "errors": 0}

        manifest = await self._remote.fetch_manifest()
        if manifest is None:
            return {"created": 0, "updated": 0, "deleted": 0, "errors": 1}

        created, updated, deleted, errors = 0, 0, 0, 0
        for entry in manifest.get("resources", []):
            try:
                action = entry.get("action", "upsert")
                if action == "upsert":
                    created += 1
                elif action == "delete":
                    deleted += 1
            except Exception:
                errors += 1

        return {"created": created, "updated": updated, "deleted": deleted, "errors": errors}
