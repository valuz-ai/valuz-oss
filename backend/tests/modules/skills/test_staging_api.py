"""End-to-end tests for the staging HTTP surface.

Boots the real FastAPI app under a tmp data dir and exercises the public
endpoints over httpx — proving the full route → service → filesystem
sync path is wired correctly.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Spin up a fresh FastAPI app rooted at tmp dirs.

    Each test gets a clean SQLite DB, an empty user skill library, an empty
    staging root, and an isolated official-skills dir (so the bundled
    skill-creator sync doesn't pollute the host home directory).
    """
    data_dir = tmp_path / "data"
    user_skills = tmp_path / "user-skills"
    staging_dir = tmp_path / "staging"
    official_skills = tmp_path / "official-skills"
    user_skills.mkdir()
    staging_dir.mkdir()

    monkeypatch.setenv("VALUZ_USER_SKILLS_DIR", str(user_skills))
    monkeypatch.setenv("VALUZ_OFFICIAL_SKILLS_DIR", str(official_skills))

    # The staging dir is read off `settings.skill_staging_dir`. Patch the
    # already-loaded singleton so reads pick up our tmp path.
    #
    # Patch the EXACT settings object the host modules hold, not a freshly
    # imported one. ``tests/modules/sessions/test_session_approval_e2e.py``
    # pops + reimports ``valuz_agent.infra.config`` (to rebind the kernel
    # env), swapping the module-level ``settings`` singleton for a new
    # instance. Modules imported earlier (``fs_registry``, ``staging``, …)
    # keep the OLD object, so a bare ``from …config import settings`` here
    # would patch the NEW one and miss what the host actually reads. All
    # pre-imported host modules share one ``settings`` binding, so patching
    # ``fs_registry.settings`` covers ``staging`` too.
    import valuz_agent.infra.fs_registry as fs_registry_mod

    live_settings = fs_registry_mod.settings
    monkeypatch.setattr(live_settings, "data_dir", data_dir)
    # Pin the DB filename too. ``test_session_approval_e2e`` sets
    # ``VALUZ_DB_FILENAME=approval-e2e.db`` and reimports the config module, so the
    # ``settings`` object the host modules now hold can carry that leaked filename.
    # Startup Alembic resolves the DB URL from ``settings.db_path``
    # (``data_dir / db_filename``), but our fresh aiosqlite engine below is pinned
    # to ``data_dir/valuz.db`` — a filename mismatch builds the schema in one file
    # and reads from another (``no such table: valuz_provider``). Force both to
    # ``valuz.db`` so migrations and data access target the same file.
    monkeypatch.setattr(live_settings, "db_filename", "valuz.db")
    monkeypatch.setattr(live_settings, "skill_staging_dir_override", staging_dir)

    # ``run_host_migrations`` resolves the DB URL via a fresh
    # ``from valuz_agent.infra.config import settings`` import. After the approval
    # test's pop+reimport, ``config.settings`` can be a DIFFERENT object than the
    # ``live_settings`` we just patched — startup Alembic would then build the
    # schema against an unpatched DB path while the app reads our tmp file. Pin the
    # config-module binding back to the patched object so they agree.
    import valuz_agent.infra.config as config_mod

    monkeypatch.setattr(config_mod, "settings", live_settings)

    # Force a brand-new SQLite engine pinned at our tmp data_dir so tests
    # don't share the developer's local DB. The host is fully async (one
    # aiosqlite engine; deps factories drive ``async_unit_of_work`` over
    # ``AsyncSessionLocal``). Startup Alembic migrations read ``settings.db_url_async``
    # — already pointed at the per-test tmp file via the ``data_dir`` patch above.
    data_dir.mkdir(parents=True, exist_ok=True)
    db_file = data_dir / "valuz.db"

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    fresh_async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    fresh_async_session = async_sessionmaker(bind=fresh_async_engine, expire_on_commit=False)

    import valuz_agent.api.deps as deps_mod
    import valuz_agent.infra.database as db_mod
    import valuz_agent.infra.db as db_helpers_mod

    monkeypatch.setattr(db_mod, "AsyncSessionLocal", fresh_async_session)
    # ``async_unit_of_work`` reads ``AsyncSessionLocal`` from its own module
    # namespace (imported at load time); rebind there so the async deps
    # factories bind to our tmp aiosqlite engine.
    monkeypatch.setattr(db_helpers_mod, "AsyncSessionLocal", fresh_async_session)

    # Module-level singletons (automation runner, automation failure
    # monitor, docs scheduler, file watcher) leak loop-bound state across
    # tests and would otherwise hang on the second startup. The staging
    # surface doesn't need any of them, so stub them out.
    import valuz_agent.infra.file_watcher as fw_mod
    import valuz_agent.modules.automations.failure_monitor as failure_monitor_mod
    import valuz_agent.modules.automations.in_process_runner as automation_runner_mod
    import valuz_agent.modules.docs.scheduler as docs_scheduler_mod

    class _NoopWatcher:
        def __init__(self, *_a, **_kw):
            pass

        def add_path(self, *_a, **_kw):  # noqa: D401
            return None

        async def start(self):
            return None

        async def stop(self):
            return None

    class _NoopRunner:
        async def startup(self):
            return None

        async def shutdown(self):
            return None

        def register(self, *_a, **_kw):  # parser scheduler surface
            return None

    monkeypatch.setattr(automation_runner_mod, "automation_runner", _NoopRunner())
    monkeypatch.setattr(failure_monitor_mod, "automation_failure_monitor", _NoopRunner())
    # The on-loop parser polling scheduler (process-wide ``lru_cache`` singleton)
    # is started by an app startup hook; stub it so per-test create_app() startup
    # doesn't reuse a cross-loop task. Staging doesn't touch cloud parsers.
    monkeypatch.setattr(deps_mod, "_polling_scheduler", _NoopRunner)
    monkeypatch.setattr(docs_scheduler_mod, "start_auto_discovery", lambda: None)
    monkeypatch.setattr(docs_scheduler_mod, "stop_auto_discovery", lambda: None)
    monkeypatch.setattr(fw_mod, "SkillFileWatcher", _NoopWatcher)

    # The three in-process MCP servers mounted + run at app startup (docs,
    # automations, connectors) use module-level FastMCP singletons whose
    # StreamableHTTPSessionManager raises if .run() is called more than once.
    # Stub all three so each test's create_app() doesn't blow up on the second
    # run. (Patch ``automations_mcp`` — schedules MCP was replaced by it in the
    # automation refactor, ADR-021.)
    import contextlib

    import valuz_agent.integrations.automations_mcp_server as automations_mcp_mod
    import valuz_agent.integrations.connectors_mcp_server as connectors_mcp_mod
    import valuz_agent.integrations.docs_mcp_server as docs_mcp_mod

    async def _noop_asgi(scope, receive, send):  # type: ignore[no-untyped-def]
        pass

    @contextlib.asynccontextmanager
    async def _noop_run():
        yield

    monkeypatch.setattr(docs_mcp_mod, "build_docs_mcp_asgi", lambda: _noop_asgi)
    monkeypatch.setattr(docs_mcp_mod, "docs_mcp_session_manager_run", _noop_run)
    monkeypatch.setattr(connectors_mcp_mod, "build_connectors_mcp_asgi", lambda: _noop_asgi)
    monkeypatch.setattr(connectors_mcp_mod, "connectors_mcp_session_manager_run", _noop_run)
    monkeypatch.setattr(automations_mcp_mod, "build_automations_mcp_asgi", lambda: _noop_asgi)
    monkeypatch.setattr(automations_mcp_mod, "automations_mcp_session_manager_run", _noop_run)

    from valuz_agent.api.app import create_app

    app = create_app()
    # `with TestClient(...)` triggers FastAPI startup events so the host +
    # kernel schema gets built via the alembic chain (`run_host_migrations` /
    # `run_kernel_migrations`) before the first request. Without the context
    # manager, startup never runs.
    with TestClient(app) as client:
        yield {
            "client": client,
            "user_skills": user_skills,
            "staging": staging_dir,
            "data_dir": data_dir,
        }


def _session_staging_root(session_id: str) -> Path:
    """Resolve the staging dir the product actually scans for a session.

    The host now keys staging off the session's *workspace cwd*
    (``data_dir/workspaces/{project_id}/.skill-staging/``) rather than the
    legacy ``{staging_root}/{session_id}/`` layout, so tests must write the
    agent's staged slugs to the same place the route reads them from.
    """
    from valuz_agent.modules.skills.staging import staging_dir_for_session

    return staging_dir_for_session(session_id, mkdir=True)


def _write_staging_skill(
    staging: Path, session_id: str, slug: str, *, name: str | None = None
) -> Path:
    skill_dir = _session_staging_root(session_id) / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    fm_name = name or slug
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {fm_name}\ndescription: Test skill.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return skill_dir


# ──────────────────────────────────────────────────────────────────────


def test_start_create_chat_returns_session_id(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    res = client.post("/v1/skills/create/chat/start")
    assert res.status_code == 201, res.text
    body = res.json()
    assert "session_id" in body
    assert "authoring_workspace_id" in body


def test_scan_returns_empty_slugs_for_fresh_session(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]

    res = client.get(f"/v1/skills/staging/{sid}/scan")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["session_id"] == sid
    assert body["slugs"] == []


def test_full_scan_then_sync_overwrite_lands_in_user_dir(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    user_skills: Path = isolated_app["user_skills"]
    staging: Path = isolated_app["staging"]

    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]

    # Simulate the agent producing two slugs.
    _write_staging_skill(staging, sid, "weekly-report", name="weekly-report")
    _write_staging_skill(staging, sid, "company-compare", name="company-compare")

    scan = client.get(f"/v1/skills/staging/{sid}/scan").json()
    slugs = sorted(s["slug"] for s in scan["slugs"])
    assert slugs == ["company-compare", "weekly-report"]
    for s in scan["slugs"]:
        assert s["conflict_kind"] == "none"

    sync = client.post(
        f"/v1/skills/staging/{sid}/sync",
        json={
            "items": [
                {"slug": "weekly-report", "strategy": "overwrite"},
                {"slug": "company-compare", "strategy": "overwrite"},
            ],
        },
    )
    assert sync.status_code == 201, sync.text
    body = sync.json()
    assert {r["slug"] for r in body["results"]} == {
        "weekly-report",
        "company-compare",
    }
    assert (user_skills / "weekly-report" / "SKILL.md").is_file()
    assert (user_skills / "company-compare" / "SKILL.md").is_file()


def test_sync_fork_creates_versioned_slug_and_bumps_version(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    user_skills: Path = isolated_app["user_skills"]
    staging: Path = isolated_app["staging"]

    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]

    # Pre-existing skill in the user library.
    target = user_skills / "weekly-report"
    target.mkdir()
    (target / "SKILL.md").write_text(
        "---\nname: weekly-report\ndescription: Original.\nversion: 1\n---\n\nOriginal body.\n",
        encoding="utf-8",
    )

    _write_staging_skill(staging, sid, "weekly-report")

    scan = client.get(f"/v1/skills/staging/{sid}/scan").json()
    s = scan["slugs"][0]
    # No staging-meta → not "same_source"; target exists → must be diverged.
    assert s["conflict_kind"] == "diverged"
    assert s["suggested_strategy"] == "fork"
    assert s["suggested_new_slug"] == "weekly-report-v2"

    sync = client.post(
        f"/v1/skills/staging/{sid}/sync",
        json={"items": [{"slug": "weekly-report", "strategy": "fork"}]},
    )
    assert sync.status_code == 201, sync.text
    result = sync.json()["results"][0]
    assert result["new_slug"] == "weekly-report-v2"

    md = (user_skills / "weekly-report-v2" / "SKILL.md").read_text("utf-8")
    assert "version: 2" in md
    # Original is preserved.
    assert "Original." in (user_skills / "weekly-report" / "SKILL.md").read_text("utf-8")


def test_sync_fork_clash_returns_409(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    user_skills: Path = isolated_app["user_skills"]
    staging: Path = isolated_app["staging"]

    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]

    (user_skills / "weekly-report-v2").mkdir()
    (user_skills / "weekly-report-v2" / "SKILL.md").write_text(
        "---\nname: occupied\n---\n", encoding="utf-8"
    )
    _write_staging_skill(staging, sid, "weekly-report")

    res = client.post(
        f"/v1/skills/staging/{sid}/sync",
        json={
            "items": [{"slug": "weekly-report", "strategy": "fork", "new_slug": "weekly-report-v2"}]
        },
    )
    assert res.status_code == 409


def test_sync_unknown_slug_returns_404(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]

    res = client.post(
        f"/v1/skills/staging/{sid}/sync",
        json={"items": [{"slug": "ghost", "strategy": "overwrite"}]},
    )
    assert res.status_code == 404


def test_sync_project_scope_requires_workspace_id(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    staging: Path = isolated_app["staging"]
    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]
    _write_staging_skill(staging, sid, "weekly-report")

    res = client.post(
        f"/v1/skills/staging/{sid}/sync",
        json={
            "items": [{"slug": "weekly-report", "strategy": "overwrite"}],
            "target_scope": "project",
        },
    )
    assert res.status_code == 422


def test_optimize_copies_existing_skill_into_staging(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    user_skills: Path = isolated_app["user_skills"]

    # Create a skill the user already owns and rescan so it lands in the index.
    src_dir = user_skills / "old-skill"
    src_dir.mkdir()
    (src_dir / "SKILL.md").write_text(
        "---\nname: old-skill\ndescription: Old.\nversion: 1\n---\n\nOld body.\n",
        encoding="utf-8",
    )
    # Force an index refresh so the lookup by id works. ``get_skill_service``
    # is now an async generator dependency and ``startup_scan`` is async, so
    # drive both through ``asyncio.run``.
    import asyncio

    from valuz_agent.api.deps import get_skill_service

    async def _refresh_index() -> None:
        gen = get_skill_service()
        svc = await gen.__anext__()
        try:
            await svc.startup_scan()
        finally:
            with contextlib.suppress(StopAsyncIteration):
                await gen.__anext__()

    asyncio.run(_refresh_index())

    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]

    res = client.post(
        f"/v1/skills/staging/{sid}/optimize",
        json={"source_skill_id": "user:old-skill"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["slug"] == "old-skill"

    staged = _session_staging_root(sid) / "old-skill"
    assert (staged / "SKILL.md").is_file()
    assert (staged / ".staging-meta.json").is_file()

    # Subsequent scan should report same_source, suggesting a safe overwrite.
    scan = client.get(f"/v1/skills/staging/{sid}/scan").json()
    s = scan["slugs"][0]
    assert s["conflict_kind"] == "same_source"
    assert s["suggested_strategy"] == "overwrite"
    assert s["source_skill_id"] == "user:old-skill"
    assert s["version"] == 1


def test_optimize_unknown_skill_returns_404(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]

    res = client.post(
        f"/v1/skills/staging/{sid}/optimize",
        json={"source_skill_id": "user:does-not-exist"},
    )
    assert res.status_code == 404


def test_per_session_extra_skills_round_trip(isolated_app):  # type: ignore[no-untyped-def]
    client = isolated_app["client"]
    sid = client.post("/v1/skills/create/chat/start").json()["session_id"]

    # Default empty.
    res = client.get(f"/v1/sessions/{sid}/skills")
    assert res.status_code == 200, res.text
    assert res.json()["skill_ids"] == []

    # Set.
    res = client.put(
        f"/v1/sessions/{sid}/skills",
        json={"skill_ids": ["user:foo", "official:bar"]},
    )
    assert res.status_code == 200, res.text
    assert sorted(res.json()["skill_ids"]) == ["official:bar", "user:foo"]

    # Re-fetch confirms persistence.
    again = client.get(f"/v1/sessions/{sid}/skills").json()
    assert sorted(again["skill_ids"]) == ["official:bar", "user:foo"]
