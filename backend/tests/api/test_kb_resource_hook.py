"""Knowledge-base lists reach overlay hooks as owner-scoped wire dictionaries."""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from valuz_agent.api.deps import get_current_user_id, get_document_service
from valuz_agent.api.routes.docs import router
from valuz_agent.modules.docs.service import KbListItem
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.resource_list_hook import ResourceListHook


class Documents:
    async def list_kbs(self, user_id: str) -> list[KbListItem]:
        assert user_id == "kb-owner"
        return [
            KbListItem(
                id="kb-1",
                name="Research",
                root_path="/owned/kb",
                parser_routing="auto",
                document_count=3,
                status="all_ready",
                created_at=123,
            )
        ]


class AddSyncStatus(ResourceListHook):
    async def apply(
        self, resource_type: str, items: list[dict[str, Any]], *, user_id: str
    ) -> list[dict[str, Any]]:
        assert resource_type == "kb"
        assert user_id == "kb-owner"
        assert items[0]["id"] == "kb-1"
        return [{**item, "sync_status": "local"} for item in items]


@pytest.mark.parametrize("with_overlay", [False, True])
async def test_kb_list_serializes_service_dataclasses_before_hook(
    monkeypatch: pytest.MonkeyPatch, with_overlay: bool
) -> None:
    if with_overlay:
        monkeypatch.setattr(ext, "resource_list_hook", AddSyncStatus())
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "kb-owner"
    app.dependency_overrides[get_document_service] = Documents
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/v1/kb")
    assert response.status_code == 200
    expected: dict[str, Any] = {
        "id": "kb-1",
        "name": "Research",
        "root_path": "/owned/kb",
        "parser_routing": "auto",
        "document_count": 3,
        "status": "all_ready",
        "created_at": 123,
    }
    if with_overlay:
        expected["sync_status"] = "local"
    assert response.json() == {"knowledge_bases": [expected]}
