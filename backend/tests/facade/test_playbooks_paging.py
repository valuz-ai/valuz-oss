"""SQL bounds, temporal version selection and scope-bound Playbook cursors."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from valuz_agent.facade.playbooks import MAX_PLAYBOOK_PAGE_SIZE, PlaybookLibrary
from valuz_agent.infra.database import Base
from valuz_agent.modules.playbooks.models import (
    PlaybookDefinitionRow,
    PlaybookRunRow,
    PlaybookVersionRow,
)


@pytest.fixture
async def store() -> AsyncIterator[tuple[AsyncSession, list[tuple[str, Any]]]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda db: Base.metadata.create_all(
                db,
                tables=[
                    PlaybookDefinitionRow.__table__,
                    PlaybookVersionRow.__table__,
                    PlaybookRunRow.__table__,
                ],
            )
        )
    queries: list[tuple[str, Any]] = []

    def record(_connection, _cursor, statement, parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            yield db, queries
    finally:
        await engine.dispose()


def _definition(
    db: AsyncSession,
    row_id: str,
    *,
    user_id: str = "owner",
    project_id: str = "project",
    created_at: int = 100,
    current_version: int = 2,
    versions: tuple[tuple[int, int], ...] = ((1, 100), (2, 200), (3, 300)),
    version_owner: str | None = None,
) -> None:
    db.add(
        PlaybookDefinitionRow(
            id=row_id,
            user_id=user_id,
            project_id=project_id,
            name=row_id,
            status="active",
            current_version=current_version,
            revision=4,
            created_at=created_at,
            updated_at=400,
        )
    )
    for number, recorded_at in versions:
        db.add(
            PlaybookVersionRow(
                user_id=version_owner or user_id,
                definition_id=row_id,
                version=number,
                content=f"{row_id} version {number}",
                goal="Legacy compatibility",
                reference_metadata=[{"kind": "skill", "ref": "research"}],
                default_executor={"agent_id": "agent"},
                created_at=recorded_at,
                updated_at=recorded_at,
            )
        )


def _run(
    db: AsyncSession,
    row_id: str,
    *,
    user_id: str = "owner",
    project_id: str = "project",
    scope: str | None = "scope",
    created_at: int = 100,
) -> None:
    db.add(
        PlaybookRunRow(
            id=row_id,
            user_id=user_id,
            project_id=project_id,
            research_scope_id=scope,
            definition_id="definition",
            definition_version=1,
            status="completed",
            trigger_kind="user",
            content_snapshot="Run the chosen method",
            output_refs=[{"type": "artifact", "id": "output"}],
            created_at=created_at,
            updated_at=created_at,
        )
    )


def _assert_single_bounded_query(queries: list[tuple[str, Any]], limit: int) -> None:
    assert len(queries) == 1
    sql, parameters = queries[0]
    assert "LIMIT ? OFFSET ?" in sql
    assert parameters[-2:] == (limit + 1, 0)


async def test_definition_pages_use_exact_versions_and_keyset_ties(store) -> None:
    db, queries = store
    _definition(db, "a-missing", versions=((1, 100),))
    _definition(db, "b", created_at=100)
    _definition(db, "c", created_at=100)
    _definition(db, "d", created_at=200)
    _definition(db, "foreign-owner", user_id="other")
    _definition(db, "foreign-project", project_id="other")
    _definition(db, "wrong-version-owner", version_owner="other")
    await db.flush()
    library = PlaybookLibrary(db)

    first = await library.list_project_page("owner", "project", limit=2)
    _assert_single_bounded_query(queries, 2)
    assert [(definition.id, version.version) for definition, version in first.items] == [
        ("b", 2),
        ("c", 2),
    ]
    assert isinstance(first.items, tuple)
    with pytest.raises(FrozenInstanceError):
        first.next_cursor = None
    with pytest.raises(FrozenInstanceError):
        first.items[0][0].name = "changed"
    assert first.next_cursor is not None

    queries.clear()
    # Deleting the cursor row must not break keyset continuation.
    await db.delete(await db.get(PlaybookDefinitionRow, "c"))
    await db.flush()
    queries.clear()
    second = await library.list_project_page("owner", "project", limit=1, cursor=first.next_cursor)
    _assert_single_bounded_query(queries, 1)
    assert [definition.id for definition, _ in second.items] == ["d"]
    assert second.next_cursor is None


async def test_historical_definition_page_selects_latest_recorded_version_in_sql(store) -> None:
    db, queries = store
    _definition(db, "a", created_at=100)
    _definition(db, "b", created_at=100)
    _definition(db, "late-version", versions=((1, 300),), current_version=1)
    _definition(db, "future-definition", created_at=300)
    _definition(db, "foreign-owner", user_id="other")
    _definition(db, "foreign-version-owner", version_owner="other")
    await db.flush()
    library = PlaybookLibrary(db)

    first = await library.list_project_page("owner", "project", limit=1, as_of_ms=200)
    _assert_single_bounded_query(queries, 1)
    assert [(definition.id, version.version) for definition, version in first.items] == [("a", 2)]
    assert first.items[0][1].created_at == 200
    assert first.next_cursor is not None
    queries.clear()
    second = await library.list_project_page(
        "owner", "project", limit=1, as_of_ms=200, cursor=first.next_cursor
    )
    _assert_single_bounded_query(queries, 1)
    assert [(definition.id, version.version) for definition, version in second.items] == [("b", 2)]
    assert second.next_cursor is None


async def test_run_pages_apply_owner_project_scope_and_cutoff_before_limit(store) -> None:
    db, queries = store
    for index in range(15):
        _run(db, f"excluded-{index:02}", scope="other", created_at=1)
    _run(db, "a", scope="scope", created_at=100)
    _run(db, "b", scope="scope", created_at=100)
    _run(db, "c-unscoped", scope=None, created_at=100)
    _run(db, "future", created_at=300)
    _run(db, "foreign-owner", user_id="other", created_at=1)
    _run(db, "foreign-project", project_id="other", created_at=1)
    await db.flush()
    library = PlaybookLibrary(db)

    first = await library.list_runs_page(
        "owner", "project", limit=1, research_scope_id="scope", as_of_ms=200
    )
    _assert_single_bounded_query(queries, 1)
    assert [row.id for row in first.items] == ["a"]
    assert first.next_cursor is not None
    second = await library.list_runs_page(
        "owner",
        "project",
        limit=1,
        research_scope_id="scope",
        as_of_ms=200,
        cursor=first.next_cursor,
    )
    assert [row.id for row in second.items] == ["b"]
    assert second.next_cursor is None
    included = await library.list_runs_page(
        "owner", "project", research_scope_id="scope", include_unscoped=True, as_of_ms=200
    )
    assert [row.id for row in included.items] == ["a", "b", "c-unscoped"]
    assert isinstance(included.items[0].output_refs, tuple)
    all_scopes = await library.list_runs_page("owner", "project")
    assert len(all_scopes.items) == 19


@pytest.mark.parametrize("method", ["list_project_page", "list_runs_page"])
@pytest.mark.parametrize("limit", [0, -1, MAX_PLAYBOOK_PAGE_SIZE + 1, True, 1.5, "2"])
async def test_page_size_bounds_fail_before_database_read(store, method, limit) -> None:
    db, queries = store
    with pytest.raises(ValueError, match="limit must be an integer"):
        await getattr(PlaybookLibrary(db), method)("owner", "project", limit=limit)
    assert queries == []


@pytest.mark.parametrize("method", ["list_project_page", "list_runs_page"])
@pytest.mark.parametrize("cursor", ["", "!invalid!", "A" * 2049, "W10"])
async def test_malformed_cursor_fails_before_database_read(store, method, cursor) -> None:
    db, queries = store
    with pytest.raises(ValueError, match="cursor"):
        await getattr(PlaybookLibrary(db), method)("owner", "project", cursor=cursor)
    assert queries == []


async def test_cursor_is_bound_to_kind_owner_project_scope_and_cutoff(store) -> None:
    db, queries = store
    _run(db, "a")
    _run(db, "b")
    await db.flush()
    library = PlaybookLibrary(db)
    first = await library.list_runs_page(
        "owner", "project", limit=1, research_scope_id="scope", as_of_ms=200
    )
    assert first.next_cursor is not None
    for overrides in (
        {"user_id": "other"},
        {"project_id": "other"},
        {"research_scope_id": "other"},
        {"research_scope_id": None},
        {"as_of_ms": 201},
        {"as_of_ms": None},
        {"include_unscoped": True},
    ):
        queries.clear()
        arguments = {
            "user_id": "owner",
            "project_id": "project",
            "research_scope_id": "scope",
            "as_of_ms": 200,
            "cursor": first.next_cursor,
            **overrides,
        }
        with pytest.raises(ValueError, match="cursor does not match"):
            await library.list_runs_page(**arguments)
        assert queries == []
    with pytest.raises(ValueError, match="cursor does not match"):
        await library.list_project_page("owner", "project", cursor=first.next_cursor)


async def test_edited_cursor_position_cannot_bypass_query_ownership(store) -> None:
    db, _ = store
    _run(db, "a")
    _run(db, "b")
    _run(db, "secret-owner", user_id="other")
    _run(db, "secret-project", project_id="other")
    await db.flush()
    library = PlaybookLibrary(db)
    first = await library.list_runs_page("owner", "project", limit=1)
    assert first.next_cursor is not None
    payload = json.loads(base64.urlsafe_b64decode(first.next_cursor + "=="))
    payload["after"] = [-1, "edited-position"]
    edited = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    replayed = await library.list_runs_page("owner", "project", cursor=edited)
    assert [row.id for row in replayed.items] == ["a", "b"]


@pytest.mark.parametrize(
    "position",
    [[True, "a"], [2**63, "a"], [100, ""], [100, "a" * 37], [100, 42], [100]],
)
async def test_malformed_keyset_positions_are_rejected_without_queries(store, position) -> None:
    db, queries = store
    _run(db, "a")
    _run(db, "b")
    await db.flush()
    library = PlaybookLibrary(db)
    first = await library.list_runs_page("owner", "project", limit=1)
    assert first.next_cursor is not None
    payload = json.loads(base64.urlsafe_b64decode(first.next_cursor + "=="))
    payload["after"] = position
    edited = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    queries.clear()
    with pytest.raises(ValueError, match="cursor position"):
        await library.list_runs_page("owner", "project", cursor=edited)
    assert queries == []


@pytest.mark.parametrize("method", ["list_project_page", "list_runs_page"])
@pytest.mark.parametrize("as_of_ms", [True, "100", 1.5, 2**63])
async def test_invalid_cutoffs_are_rejected_without_queries(store, method, as_of_ms) -> None:
    db, queries = store
    with pytest.raises(ValueError, match="as_of_ms must be"):
        await getattr(PlaybookLibrary(db), method)("owner", "project", as_of_ms=as_of_ms)
    assert queries == []


async def test_empty_pages_and_legacy_unbounded_lists_remain_compatible(store) -> None:
    db, queries = store
    library = PlaybookLibrary(db)
    assert (await library.list_project_page("owner", "project")).items == ()
    assert (await library.list_runs_page("owner", "project")).next_cursor is None
    for index in range(MAX_PLAYBOOK_PAGE_SIZE + 1):
        _definition(db, f"definition-{index:03}", versions=((1, 100),), current_version=1)
        _run(db, f"run-{index:03}")
    await db.flush()
    queries.clear()
    page = await library.list_project_page("owner", "project", limit=MAX_PLAYBOOK_PAGE_SIZE)
    _assert_single_bounded_query(queries, MAX_PLAYBOOK_PAGE_SIZE)
    assert len(page.items) == MAX_PLAYBOOK_PAGE_SIZE
    assert page.next_cursor is not None
    assert len(await library.list_project("owner", "project")) == MAX_PLAYBOOK_PAGE_SIZE + 1
    assert len(await library.list_runs("owner", "project")) == MAX_PLAYBOOK_PAGE_SIZE + 1


async def test_exact_definition_reads_are_owner_version_scoped_and_bounded(store) -> None:
    db, queries = store
    _definition(db, "target", project_id="elsewhere")
    _definition(db, "foreign", user_id="other")
    _definition(db, "wrong-version-owner", version_owner="other")
    _definition(db, "missing-current", versions=((1, 100),))
    await db.flush()
    library = PlaybookLibrary(db)
    queries.clear()
    result = await library.get_definition("owner", "target")
    assert result is not None
    definition, version = result
    assert definition.project_id == "elsewhere"
    assert version.version == 2
    assert len(queries) == 2
    assert all("LIMIT" in sql for sql, _ in queries)
    assert all("user_id =" in sql for sql, _ in queries)
    old = await library.get_definition("owner", "target", version=1)
    assert old is not None and old[1].content == "target version 1"
    # A row above the published head must not be exposed.
    assert await library.get_definition("owner", "target", version=3) is None
    assert await library.get_definition("owner", "target", version=99) is None
    for row_id in ("foreign", "wrong-version-owner", "missing-current", "missing"):
        assert await library.get_definition("owner", row_id) is None
    assert await library.get_definition("other", "target", version=1) is None


@pytest.mark.parametrize("version", [True, 0, -1, "1", 1.5, 2**63])
async def test_exact_definition_invalid_versions_never_query(store, version) -> None:
    db, queries = store
    with pytest.raises(ValueError, match="version must be"):
        await PlaybookLibrary(db).get_definition("owner", "target", version=version)
    assert queries == []


async def test_exact_reads_do_not_flush_or_reuse_dirty_identity_map(store) -> None:
    db, queries = store
    _definition(db, "target")
    _run(db, "run")
    _run(db, "foreign", user_id="other")
    await db.flush()
    definition = await db.get(PlaybookDefinitionRow, "target")
    run = await db.get(PlaybookRunRow, "run")
    definition.name = "unflushed"
    definition.current_version = 3
    run.status = "failed"
    library = PlaybookLibrary(db)
    result = await library.get_definition("owner", "target")
    assert result is not None
    assert result[0].name == "target" and result[1].version == 2
    queries.clear()
    result_run = await library.get_run("owner", "run")
    assert result_run is not None and result_run.status == "completed"
    assert len(queries) == 1 and "LIMIT" in queries[0][0]
    assert await library.get_run("owner", "foreign") is None
    assert await library.get_run("other", "run") is None
    assert await library.get_run("owner", "missing") is None
    # Reading must leave the caller's pending edits intact, not refresh them away.
    assert definition in db.dirty and definition.name == "unflushed"
    assert run in db.dirty and run.status == "failed"
    result[1].reference_metadata[0]["ref"] = "changed"
    result[1].default_executor["agent_id"] = "changed"
    result_run.output_refs[0]["id"] = "changed"
    again = await library.get_definition("owner", "target")
    again_run = await library.get_run("owner", "run")
    assert again[1].reference_metadata[0]["ref"] == "research"
    assert again[1].default_executor["agent_id"] == "agent"
    assert again_run.output_refs[0]["id"] == "output"
