"""Read original run/revision identities without executing, exporting or ORM writes."""

from collections.abc import AsyncIterator
from dataclasses import asdict

import pytest
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from valuz_agent.facade.artifacts import ArtifactLibrary
from valuz_agent.facade.automations import AutomationLibrary
from valuz_agent.infra.database import Base
from valuz_agent.modules.artifacts.models import (
    ArtifactContentRow,
    ArtifactHeadRow,
    ArtifactRevisionRow,
    ArtifactRow,
)
from valuz_agent.modules.automations.models import AutomationRow, AutomationRunRow


@pytest.fixture
async def store() -> AsyncIterator[tuple[AsyncSession, list[str]]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(
                conn,
                tables=[
                    model.__table__
                    for model in (
                        ArtifactRow,
                        ArtifactHeadRow,
                        ArtifactRevisionRow,
                        ArtifactContentRow,
                        AutomationRow,
                        AutomationRunRow,
                    )
                ],
            )
        )
    queries = []
    event.listen(
        engine.sync_engine,
        "before_cursor_execute",
        lambda _c, _cu, sql, _p, _ctx, _m: queries.append(sql),
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            yield db, queries
    finally:
        await engine.dispose()


async def _artifacts(db):
    artifact = ArtifactRow(
        id="artifact", user_id="owner", project_id="project", display_name="Report"
    )
    head = ArtifactHeadRow(
        artifact_id="artifact", user_id="owner", revision_id="revision2", version_no=2
    )
    db.add_all([artifact, head])
    for version in (1, 2, 3):
        db.add_all(
            [
                ArtifactContentRow(
                    id=f"content{version}",
                    user_id="owner",
                    content_hash=f"hash{version}",
                    storage_kind="inline",
                    content_inline="not exported",
                    byte_size=12,
                ),
                ArtifactRevisionRow(
                    id=f"revision{version}",
                    user_id="owner",
                    artifact_id="artifact",
                    version_no=version,
                    file_name=f"v{version}.md",
                    content_id=f"content{version}",
                    content_hash=f"hash{version}",
                    abs_path="/private/source.md",
                    source_session_id="session",
                ),
            ]
        )
    await db.flush()
    return artifact, head


async def test_artifact_head_exact_revision_and_version_mismatch(store):
    db, queries = store
    await _artifacts(db)
    library = ArtifactLibrary(db)
    queries.clear()
    current = await library.get("owner", "artifact")
    assert current is not None and current[1].id == "revision2"
    assert len(queries) == 1 and queries[0].startswith("SELECT") and "LIMIT" in queries[0]
    old = await library.get("owner", "artifact", revision_id="revision1")
    assert old is not None and old[1].version_no == 1
    assert await library.get_revision("owner", "revision1") == old
    assert old[1].source_session_id == "session" and old[1].byte_size == 12
    assert not {"abs_path", "storage_key", "content_inline", "user_id"} & asdict(old[1]).keys()
    for revision in ("missing", "revision3", "1"):
        assert await library.get("owner", "artifact", revision_id=revision) is None
        assert await library.get_revision("owner", revision) is None
    assert await library.get("other", "artifact") is None
    assert await library.get_revision("other", "revision1") is None
    assert await library.get("owner", "missing", revision_id="revision1") is None


@pytest.mark.parametrize(
    "model,identity",
    [
        (ArtifactRow, "artifact"),
        (ArtifactHeadRow, "artifact"),
        (ArtifactRevisionRow, "revision1"),
        (ArtifactContentRow, "content1"),
        (ArtifactRevisionRow, "revision2"),
    ],
)
async def test_artifact_parent_head_revision_and_content_must_have_same_owner(
    store, model, identity
):
    db, _ = store
    await _artifacts(db)
    key = model.artifact_id if model is ArtifactHeadRow else model.id
    await db.execute(update(model).where(key == identity).values(user_id="foreign"))
    assert await ArtifactLibrary(db).get_revision("owner", "revision1") is None


async def test_artifact_missing_content_and_mismatched_hash_do_not_fall_back(store):
    db, _ = store
    await _artifacts(db)
    library = ArtifactLibrary(db)
    await db.execute(
        update(ArtifactContentRow)
        .where(ArtifactContentRow.id == "content1")
        .values(content_hash="corrupt")
    )
    assert await library.get_revision("owner", "revision1") is None
    await db.delete(await db.get(ArtifactContentRow, "content2"))
    await db.flush()
    assert await library.get("owner", "artifact") is None


async def test_artifact_reads_preserve_dirty_state_and_missing_file_metadata(store):
    db, queries = store
    artifact, head = await _artifacts(db)
    await db.execute(
        update(ArtifactRevisionRow)
        .where(ArtifactRevisionRow.id == "revision1")
        .values(status="missing")
    )
    artifact.display_name = "unflushed"
    head.revision_id = "revision3"
    head.version_no = 3
    queries.clear()
    library = ArtifactLibrary(db)
    result = await library.get("owner", "artifact")
    assert result[0].display_name == "Report" and result[1].id == "revision2"
    assert (await library.get_revision("owner", "revision1"))[1].status == "missing"
    assert all(query.startswith("SELECT") for query in queries)
    assert artifact in db.dirty and head in db.dirty


async def test_automation_definition_and_run_reads_are_exact_and_read_only(store):
    db, queries = store
    definition = AutomationRow(
        id="auto",
        user_id="owner",
        project_id="project",
        name="Monitor",
        agent_kind="project_member",
        agent_slug="agent",
        prompt_template="Do research",
        trigger_kind="manual",
    )
    run = AutomationRunRow(
        id="run",
        user_id="owner",
        automation_id="auto",
        project_id="project",
        trigger_type="manual",
        status="completed",
        triggered_at=100,
        completed_at=200,
        result_summary="Research result",
        playbook_run_id="playbook-run",
        session_id="session",
    )
    db.add_all([definition, run])
    await db.flush()
    definition.status = "disabled"
    run.status = "failed"
    library = AutomationLibrary(db)
    queries.clear()
    assert (await library.get("owner", "auto")).status == "enabled"
    result = await library.get_run("owner", "run")
    assert result.status == "completed" and result.result_summary == "Research result"
    assert result.playbook_run_id == "playbook-run" and result.session_id == "session"
    assert len(queries) == 2 and all(sql.startswith("SELECT") and "LIMIT" in sql for sql in queries)
    for owner, identity in (("other", "auto"), ("owner", "missing")):
        assert await library.get(owner, identity) is None
    assert await library.get_run("other", "run") is None
    assert await library.get_run("owner", "missing") is None
    assert definition in db.dirty and run in db.dirty


@pytest.mark.parametrize("user,identity", [("", "id"), ("  ", "id"), ("owner", "")])
async def test_missing_identity_never_queries(store, user, identity):
    db, queries = store
    for read in (
        ArtifactLibrary(db).get,
        ArtifactLibrary(db).get_revision,
        AutomationLibrary(db).get,
        AutomationLibrary(db).get_run,
    ):
        with pytest.raises(ValueError):
            await read(user, identity)
    assert queries == []
