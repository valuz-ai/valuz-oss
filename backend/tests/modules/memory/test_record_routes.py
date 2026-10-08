"""HTTP contract: ownership, CAS, provenance and recoverable forgetting."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.api.routes import memory as routes
from valuz_agent.modules.memory.service import memory_store


class _UOW:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> bool:
        return False


@pytest.fixture
def api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, dict[str, Any], Path]]:
    monkeypatch.setattr(memory_store._fs, "data_dir", lambda _owner: tmp_path)
    monkeypatch.setattr(routes, "async_unit_of_work", lambda **_kwargs: _UOW())
    state: dict[str, Any] = {"enabled": True, "owner": "alice", "project_reads": []}

    async def enabled(_db: object, *, user_id: str) -> bool:
        return bool(state["enabled"])

    async def auto(_db: object, *, user_id: str) -> bool:
        return True

    async def guidance(_db: object, *, user_id: str) -> str:
        return ""

    async def project(_self: object, user_id: str, project_id: str) -> object | None:
        state["project_reads"].append((user_id, project_id))
        return object() if user_id == "alice" and project_id == "alice-project" else None

    monkeypatch.setattr(routes, "get_memory_enabled", enabled)
    monkeypatch.setattr(routes, "get_memory_auto_extract", auto)
    monkeypatch.setattr(routes, "get_memory_custom_instructions", guidance)
    monkeypatch.setattr(routes.ProjectLibrary, "get", project)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user_id] = lambda: state["owner"]
    with TestClient(app) as client:
        yield client, state, tmp_path


def test_record_http_correction_and_conflict(api: tuple[TestClient, dict[str, Any], Path]) -> None:
    client, _, _ = api
    first = client.post(
        "/v1/memory/records",
        json={
            "target": "user",
            "kind": "preference",
            "content": "Use Chinese.",
            "operation_id": "remember-language",
            "base_revision": 0,
        },
    )
    assert first.status_code == 200
    receipt = first.json()
    record_id = receipt["record_id"]
    page = client.get("/v1/memory/records").json()
    record = page["records"][0]
    assert record["confirmed"] is True
    assert record["source_refs"] == [
        {
            "kind": "manual",
            "source_id": "remember-language",
            "revision": None,
            "origin": "owner",
        }
    ]
    corrected = client.patch(
        f"/v1/memory/records/{record_id}",
        json={
            "content": "Use English.",
            "operation_id": "correct-language",
            "base_revision": page["revision"],
        },
    )
    assert corrected.status_code == 200
    conflict = client.patch(
        f"/v1/memory/records/{record_id}",
        json={
            "content": "Use French.",
            "operation_id": "old-device-correction",
            "base_revision": page["revision"],
        },
    )
    assert conflict.status_code == 409
    current = client.get("/v1/memory/records").json()["records"][0]
    assert current["content"] == "Use English." and current["kind"] == "preference"
    assert current["id"] == record_id and current["revision"] == 2


def test_record_forgetting_has_idempotent_receipt(
    api: tuple[TestClient, dict[str, Any], Path],
) -> None:
    client, _, root = api
    created = client.post(
        "/v1/memory/records",
        json={
            "target": "global",
            "content": "Private background.",
            "operation_id": "save",
            "base_revision": 0,
        },
    ).json()
    payload = {"target": "global", "operation_id": "forget", "base_revision": created["revision"]}
    path = f"/v1/memory/records/{created['record_id']}"
    deleted = client.request("DELETE", path, json=payload)
    assert deleted.status_code == 200
    repeated = client.request("DELETE", path, json=payload)
    assert repeated.status_code == 200 and repeated.json()["replayed"] is True
    assert repeated.json()["revision"] == deleted.json()["revision"]
    assert client.get("/v1/memory/records").json()["records"] == []
    catalog = next(root.rglob("memory.json")).read_text()
    assert "Private background." not in catalog
    assert "original conversations" in deleted.json()["context_notice"]


def test_source_forgetting_prohibits_reusing_the_source(
    api: tuple[TestClient, dict[str, Any], Path],
) -> None:
    client, _, _ = api
    first = client.post(
        "/v1/memory/records",
        json={
            "target": "user",
            "content": "Private fact.",
            "operation_id": "source-note",
            "base_revision": 0,
        },
    ).json()
    forgotten = client.post(
        "/v1/memory/sources/forget",
        json={
            "kind": "manual",
            "source_id": "source-note",
            "operation_id": "forget-source",
            "base_revision": first["revision"],
        },
    )
    assert forgotten.status_code == 200
    assert client.get("/v1/memory/records").json()["records"] == []
    repeated = client.post(
        "/v1/memory/sources/forget",
        json={
            "kind": "manual",
            "source_id": "source-note",
            "operation_id": "forget-source",
            "base_revision": first["revision"],
        },
    )
    assert repeated.status_code == 200 and repeated.json()["replayed"] is True


def test_owner_and_project_authority_are_not_request_fields(
    api: tuple[TestClient, dict[str, Any], Path],
) -> None:
    client, state, _ = api
    rejected = client.post(
        "/v1/memory/records",
        json={
            "target": "user",
            "content": "Forged source.",
            "operation_id": "forge",
            "base_revision": 0,
            "source_refs": [{"kind": "manual", "source_id": "other", "origin": "owner"}],
        },
    )
    assert rejected.status_code == 422
    own = client.post(
        "/v1/memory/records",
        json={
            "target": "project",
            "project_id": "alice-project",
            "content": "Own project note.",
            "operation_id": "owned-note",
            "base_revision": 0,
        },
    )
    assert own.status_code == 200
    assert client.get("/v1/memory/records").json()["records"] == []
    assert client.get("/v1/memory/records?project_id=foreign").status_code == 404
    state["owner"] = "bob"
    assert client.get("/v1/memory/records").json()["records"] == []
    assert client.get("/v1/memory/records?project_id=alice-project").status_code == 404
    assert ("bob", "alice-project") in state["project_reads"]


def test_disabled_collection_keeps_management_and_forgetting(
    api: tuple[TestClient, dict[str, Any], Path],
) -> None:
    client, state, _ = api
    created = client.post(
        "/v1/memory/records",
        json={
            "target": "user",
            "content": "Existing preference.",
            "operation_id": "save",
            "base_revision": 0,
        },
    ).json()
    state["enabled"] = False
    assert client.get("/v1/memory/settings").json()["enabled"] is False
    assert client.get("/v1/memory/records").json()["total"] == 1
    assert (
        client.post(
            "/v1/memory/records",
            json={
                "target": "user",
                "content": "New input.",
                "operation_id": "disabled",
                "base_revision": created["revision"],
            },
        ).status_code
        == 403
    )
    assert (
        client.request(
            "DELETE",
            f"/v1/memory/records/{created['record_id']}",
            json={
                "target": "user",
                "operation_id": "delete-disabled",
                "base_revision": created["revision"],
            },
        ).status_code
        == 200
    )


def test_corrupt_authority_is_not_returned_as_empty(
    api: tuple[TestClient, dict[str, Any], Path],
) -> None:
    client, _, root = api
    client.post(
        "/v1/memory/records",
        json={
            "target": "user",
            "content": "A note.",
            "operation_id": "save",
            "base_revision": 0,
        },
    )
    next(root.rglob("memory.json")).write_text("corrupt private content")
    response = client.get("/v1/memory/records")
    assert response.status_code == 503
    assert "corrupt private content" not in response.text


def test_bound_backend_failure_does_not_expose_local_fallback(
    api: tuple[TestClient, dict[str, Any], Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from valuz_agent.integrations.memory_local import LocalMemoryBackend
    from valuz_agent.ports.extensions import ext
    from valuz_agent.ports.memory import MemoryProtected, MemorySnapshot

    client, _, _ = api
    client.post(
        "/v1/memory/records",
        json={
            "target": "user",
            "content": "Old local preference.",
            "operation_id": "local-note",
            "base_revision": 0,
        },
    )

    class Denied(LocalMemoryBackend):
        async def snapshot(self, user_id: str, *, namespace: str = "core") -> MemorySnapshot:
            raise MemoryProtected("Remote authority refused access")

    monkeypatch.setattr(ext, "memory_backend", Denied())
    denied = client.get("/v1/memory/records")
    assert denied.status_code == 403
    assert "Old local preference." not in denied.text
