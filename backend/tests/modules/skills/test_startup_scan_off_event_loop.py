"""A skill scan must not run on the event loop.

``startup_scan`` and the two ``reindex_*`` entry points all walk skill roots
off disk. That is cheap on a laptop and
not on a cloud owner's network-mounted root, where a stat costs tens of
milliseconds and a full scan runs for seconds. Run inline it stalls the whole
process — which is how the runtime-control work WS stopped answering the
control plane's 20s keepalive ping and got closed with 1011 mid-reconcile.
"""

from __future__ import annotations

import asyncio
import time

from valuz_agent.modules.skills.service import SkillLibraryService


class SlowSource:
    """A skill source whose scan blocks, standing in for a network mount."""

    name = "slow"
    DELAY_S = 0.25

    def __init__(self) -> None:
        self.calls = 0

    def list_skills(self, ctx, *, compute_content_hash=True, slugs=None):  # noqa: ANN001, ANN201
        self.calls += 1
        time.sleep(self.DELAY_S)
        return []


class NoProjects:
    async def list_projects(self, user_id: str):  # noqa: ANN201
        return []


class Datastore:
    session = object()

    async def list_skills(self, user_id: str):  # noqa: ANN201
        return []


async def test_startup_scan_keeps_the_event_loop_responsive(monkeypatch) -> None:
    svc = SkillLibraryService.__new__(SkillLibraryService)
    source = SlowSource()
    svc._source = source  # type: ignore[attr-defined]
    svc._extra_sources = []  # type: ignore[attr-defined]
    svc._projects = NoProjects()  # type: ignore[attr-defined]
    svc._ds = Datastore()  # type: ignore[attr-defined]

    worst = 0.0
    stop = False

    async def ticker() -> None:
        nonlocal worst
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.001)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.01)
    try:
        await svc._startup_scan_unlocked("owner-a")
    finally:
        stop = True
        await t

    assert source.calls == 1, "the scan did not run"
    assert worst < SlowSource.DELAY_S / 2, (
        f"event loop was unserviced for {worst * 1000:.0f}ms — the scan is "
        f"running inline instead of on a worker thread"
    )


async def test_reindex_official_skills_keeps_the_event_loop_responsive(monkeypatch) -> None:
    """The path a release takes, and the one that was still inline.

    ``startup_scan`` was moved off the loop after the 1011 incident; the two
    ``reindex_*`` entry points were written later and kept scanning inline.
    That is the shape a RELEASE has: changing any bundled package re-lands it
    for every owner, and every landing comes back through here — so the one
    scan that still blocked ran once per owner, back to back, on the process
    serving the runtime-control WS. Measured on qa 2026-09-02: the WS died on
    keepalive, connector reconciliation never completed a cycle, and every
    session spawn on the deployment answered 409 RESOURCE_REPLICA_NOT_READY.
    """
    import contextlib

    from valuz_agent.integrations import skills_official
    from valuz_agent.modules.skills import service as service_mod

    source = SlowSource()
    monkeypatch.setattr(skills_official, "OfficialSkillSource", lambda: source)

    @contextlib.asynccontextmanager
    async def uow(**_kwargs):  # noqa: ANN202
        yield object()

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", uow)
    monkeypatch.setattr(service_mod, "SkillDatastore", lambda db: object())

    worst = 0.0
    stop = False

    async def ticker() -> None:
        nonlocal worst
        last = time.monotonic()
        while not stop:
            await asyncio.sleep(0.001)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.01)
    try:
        await service_mod.reindex_official_skills("owner-a")
    finally:
        stop = True
        await t

    assert source.calls == 1, "the reindex did not run"
    assert worst < SlowSource.DELAY_S / 2, (
        f"event loop was unserviced for {worst * 1000:.0f}ms — reindex_official_skills "
        f"is scanning inline instead of on a worker thread"
    )


async def test_reindex_narrows_to_the_packages_a_landing_changed(monkeypatch, tmp_path) -> None:
    """The hash is the cost; pay it only where content could have moved.

    A release re-lands ONE package for every owner. Re-reading every package's
    every file per owner is what turned that into minutes of degraded spawn,
    so the landing names what it touched and the walk skips the rest.
    """
    import contextlib

    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.integrations import skills_official
    from valuz_agent.modules.skills import service as service_mod

    root = tmp_path / "official-skills"
    for slug in ("moved", "untouched-a", "untouched-b"):
        (root / slug).mkdir(parents=True)
        (root / slug / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: d\n---\nbody\n", encoding="utf-8"
        )

    monkeypatch.setattr(fs_registry, "official_skill_root", lambda *, user_id: root)
    monkeypatch.setattr(fs_registry, "system_skill_roots", lambda: ())

    hashed: list[str] = []
    real_hash = skills_official._compute_dir_hash

    def spy(skill_dir):  # noqa: ANN001, ANN202
        hashed.append(skill_dir.name)
        return real_hash(skill_dir)

    monkeypatch.setattr(skills_official, "_compute_dir_hash", spy)

    @contextlib.asynccontextmanager
    async def uow(**_kwargs):  # noqa: ANN202
        yield object()

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", uow)
    monkeypatch.setattr(service_mod, "SkillDatastore", lambda db: object())

    async def noop_index(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        return 0

    monkeypatch.setattr(service_mod, "_index_manifests", noop_index)

    await service_mod.reindex_official_skills("owner-a", slugs={"moved"})
    assert hashed == ["moved"]

    hashed.clear()
    await service_mod.reindex_official_skills("owner-a")
    assert sorted(hashed) == ["moved", "untouched-a", "untouched-b"]


async def test_the_disk_snapshot_is_taken_under_the_scan_lock(monkeypatch, tmp_path) -> None:
    """Off the event loop, yes — but not out of the lock.

    Indexing is a read-modify-write: the snapshot taken from disk is what gets
    written. Take it outside ``_scan_lock`` and a pass that read early can win
    the write over a pass that read late, leaving the index on a superseded
    content hash while the newer landing sits on disk — and ``startup_scan``,
    which still reads inside the lock, cannot repair what it never saw move.
    Moving the walk to a worker thread is what made it easy to also move it out
    of the lock; this pins the half that must not move.
    """
    import contextlib

    from valuz_agent.infra.fs_registry import fs_registry
    from valuz_agent.modules.skills import service as service_mod

    root = tmp_path / "official-skills"
    (root / "moved").mkdir(parents=True)
    (root / "moved" / "SKILL.md").write_text(
        "---\nname: moved\ndescription: d\n---\nbody\n", encoding="utf-8"
    )
    monkeypatch.setattr(fs_registry, "official_skill_root", lambda *, user_id: root)
    monkeypatch.setattr(fs_registry, "system_skill_roots", lambda: ())
    monkeypatch.setattr(fs_registry, "user_skill_root", lambda *, user_id: root)

    @contextlib.asynccontextmanager
    async def uow(**_kwargs):  # noqa: ANN202
        yield object()

    monkeypatch.setattr("valuz_agent.infra.db.async_unit_of_work", uow)
    monkeypatch.setattr(service_mod, "SkillDatastore", lambda db: object())

    async def noop_index(*_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
        return 0

    monkeypatch.setattr(service_mod, "_index_manifests", noop_index)

    locked_while_reading: list[bool] = []

    def watch(source_cls):  # noqa: ANN001, ANN202
        real = source_cls.list_skills

        def spy(self, ctx, **kwargs):  # noqa: ANN001, ANN003, ANN202
            locked_while_reading.append(service_mod._scan_lock.locked())
            return real(self, ctx, **kwargs)

        return spy

    from valuz_agent.integrations.skills_filesystem import FilesystemSkillSource
    from valuz_agent.integrations.skills_official import OfficialSkillSource

    monkeypatch.setattr(OfficialSkillSource, "list_skills", watch(OfficialSkillSource))
    monkeypatch.setattr(FilesystemSkillSource, "list_skills", watch(FilesystemSkillSource))

    await service_mod.reindex_official_skills("owner-a", slugs={"moved"})
    await service_mod.reindex_user_skills("owner-a")

    assert locked_while_reading == [True, True], (
        "a reindex read the skill tree without holding _scan_lock — a slower pass "
        "can now overwrite a newer one's rows with an older content hash"
    )
    assert not service_mod._scan_lock.locked(), "the lock was not released"
