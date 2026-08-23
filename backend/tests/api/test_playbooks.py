"""Playbook REST contract keeps project containers and exact run versions visible."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.routes import playbooks as routes
from valuz_agent.facade.projects import ProjectRef
from valuz_agent.infra.database import Base
from valuz_agent.modules.playbooks.service import PlaybookService

USER = "owner-1"


class Projects:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], ProjectRef] = {}
        self.chat_count = 0

    async def get(self, user_id: str, project_id: str) -> ProjectRef | None:
        return self.rows.get((user_id, project_id))

    async def create_chat(self, user_id: str, *, name: str = "Chat") -> ProjectRef:
        self.chat_count += 1
        row = ProjectRef(id=f"chat-{self.chat_count}", name=name, kind="chat")
        self.rows[(user_id, row.id)] = row
        return row

    async def delete(self, user_id: str, project_id: str) -> bool:
        return self.rows.pop((user_id, project_id), None) is not None


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'playbook.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    projects = Projects()

    async def service() -> AsyncGenerator[PlaybookService, None]:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions.begin() as db:
            yield PlaybookService(db, projects)

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: USER
    app.dependency_overrides[routes.get_playbook_service] = service
    with TestClient(app) as value:
        yield value


def test_definition_version_and_run_roundtrip(client: TestClient) -> None:
    created = client.post(
        "/v1/playbooks",
        json={
            "name": "Earnings review",
            "content": "Use /earnings-analysis to review earnings and update research context.",
            "reference_metadata": [{"kind": "skill", "ref": "earnings-analysis"}],
        },
    )
    assert created.status_code == 201
    body = created.json()
    definition = body["definition"]
    assert "created_project" not in body
    assert definition["project_id"] is None

    revised = client.post(
        f"/v1/playbooks/{definition['id']}/versions",
        json={
            "base_version": 1,
            "content": "Review earnings and explicitly test disconfirming evidence.",
            "status": "active",
        },
    )
    assert revised.status_code == 201
    assert revised.json()["version"]["version"] == 2

    run = client.post(
        "/v1/playbooks/runs",
        json={
            "definition_id": definition["id"],
            "definition_version": 1,
            "trigger_kind": "automation",
            "trigger_ref": "automation-1",
            "context_snapshot": {"thesis": {"id": "t1", "version": 4}},
        },
    )
    assert run.status_code == 201
    run_body = run.json()
    assert run_body["definition_version"] == 1
    assert run_body["project_id"] is None
    assert run_body["content_snapshot"].startswith("Use /earnings-analysis")

    completed = client.patch(
        f"/v1/playbooks/runs/{run_body['id']}",
        json={
            "status": "running",
            "plan": [{"step": "read filings"}],
        },
    )
    assert completed.status_code == 200
    completed = client.patch(
        f"/v1/playbooks/runs/{run_body['id']}",
        json={
            "status": "completed",
            "artifact_refs": ["artifact-1"],
            "change_set_refs": ["change-set-1"],
        },
    )
    assert completed.status_code == 200
    assert completed.json()["change_set_refs"] == ["change-set-1"]

    listed = client.get("/v1/playbooks/runs/list")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == run_body["id"]

    deleted = client.delete(
        f"/v1/playbooks/{definition['id']}",
        params={"expected_revision": revised.json()["definition"]["revision"]},
    )
    assert deleted.status_code == 204
    assert client.get(f"/v1/playbooks/{definition['id']}").status_code == 404
