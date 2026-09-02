"""Backup engine unit tests — snapshot, dedup, retention, restore apply.

All disk-only: the engine is synchronous and takes explicit paths, so no app
boot or DB fixture is needed. SQLite sources are real files created with the
stdlib driver (incl. WAL mode) to exercise VACUUM INTO for real.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from valuz_agent.modules.backup import manifest as mf
from valuz_agent.modules.backup.engine import (
    BackupPlan,
    SourceSpec,
    apply_retention,
    run_backup,
)
from valuz_agent.modules.backup.errors import BackupPreflightFailed
from valuz_agent.modules.backup.restore import (
    apply_pending_restore,
    read_pending_request,
    write_pending_request,
)
from valuz_agent.modules.backup.schemas import BackupRetention, BackupScope


def _make_sqlite(path: Path, table: str, rows: int, *, alembic_table: str | None = None) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany(f"INSERT INTO {table} (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
        if alembic_table:
            conn.execute(f"CREATE TABLE {alembic_table} (version_num TEXT)")
            conn.execute(f"INSERT INTO {alembic_table} VALUES ('0042')")
        conn.commit()
    finally:
        conn.close()


def _plan(tmp_path: Path, **overrides) -> BackupPlan:
    data = tmp_path / "data"
    (data / "memories").mkdir(parents=True, exist_ok=True)
    (data / "memories" / "MEMORY.md").write_text("hello", encoding="utf-8")
    (data / "installation.json").write_text('{"id": "orig"}', encoding="utf-8")
    host_db = tmp_path / "valuz.db"
    kernel_db = tmp_path / "kernel.db"
    if not host_db.exists():
        _make_sqlite(host_db, "valuz_project", 3, alembic_table="alembic_version_host")
    if not kernel_db.exists():
        _make_sqlite(kernel_db, "sessions", 5, alembic_table="alembic_version")
    defaults = dict(
        user_id="u1",
        destination=tmp_path / "backups",
        kind="manual",
        scope=BackupScope(),
        retention=BackupRetention(),
        host_db=host_db,
        kernel_db=kernel_db,
        sources=[
            SourceSpec(rel="memories", src=data / "memories"),
            # single-FILE source — regression: restore must unlink files,
            # not rmtree them
            SourceSpec(rel="installation.json", src=data / "installation.json"),
        ],
        exclude_roots=[tmp_path / "backups"],
    )
    defaults.update(overrides)
    return BackupPlan(**defaults)


def test_backup_roundtrip_and_manifest(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    result = run_backup(plan)

    vdir = mf.version_dir(plan.destination, result.version_id)
    assert (vdir / "manifest.json").is_file()
    assert (vdir / "summary.json").is_file()
    assert (vdir / "db" / "valuz.db").is_file()
    assert (vdir / "db" / "kernel.db").is_file()
    assert (vdir / "data" / "memories" / "MEMORY.md").read_text() == "hello"

    manifest = mf.load_manifest(vdir)
    assert manifest is not None
    assert manifest.host_alembic == "0042"
    assert manifest.kernel_alembic == "0042"
    assert manifest.file_count == len(manifest.files)
    assert any(f.path == "data/memories/MEMORY.md" for f in manifest.files)
    # restore map covers both DBs and the memories category
    rels = {rt.rel for rt in manifest.restore_targets}
    assert {"db/valuz.db", "db/kernel.db", "data/memories"} <= rels

    # the DB snapshot is a valid standalone sqlite file with the data
    conn = sqlite3.connect(str(vdir / "db" / "kernel.db"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 5
    finally:
        conn.close()


def test_second_version_hardlinks_unchanged_files(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = run_backup(plan)
    # mutate the DB so the run isn't skipped, keep the data file untouched
    _touch_db(plan.host_db)
    second = run_backup(plan, skip_if_unchanged=False)

    v1 = mf.version_dir(plan.destination, first.version_id) / "data/memories/MEMORY.md"
    v2 = mf.version_dir(plan.destination, second.version_id) / "data/memories/MEMORY.md"
    assert v1.stat().st_ino == v2.stat().st_ino  # hardlinked, not copied
    assert second.manifest.new_bytes < second.manifest.total_bytes


def _touch_db(path: Path | None) -> None:
    assert path is not None
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("INSERT INTO valuz_project (v) VALUES ('new')")
        conn.commit()
    finally:
        conn.close()


def test_skip_if_unchanged(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = run_backup(plan)
    second = run_backup(plan, skip_if_unchanged=True)
    assert second.skipped_no_change
    assert second.version_id == first.version_id
    assert len(mf.scan_versions(plan.destination)) == 1


def test_summary_counts_from_snapshot(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    result = run_backup(plan)
    vdir = mf.version_dir(plan.destination, result.version_id)
    summary = mf.load_summary(vdir)
    assert summary.counts.sessions == 5
    assert summary.counts.projects == 3


def test_incomplete_version_cleanup(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    stale = mf.versions_dir(plan.destination) / "19990101-000000"
    stale.mkdir(parents=True)
    (stale / "junk.txt").write_text("x", encoding="utf-8")
    run_backup(plan)
    assert not stale.exists()  # no manifest → garbage, removed by the next run


def test_preflight_rejects_unwritable_destination(tmp_path: Path) -> None:
    if os.geteuid() == 0:  # pragma: no cover — root ignores modes
        pytest.skip("running as root")
    dest = tmp_path / "ro"
    dest.mkdir()
    dest.chmod(0o500)
    try:
        with pytest.raises(BackupPreflightFailed):
            run_backup(_plan(tmp_path, destination=dest, exclude_roots=[dest]))
    finally:
        dest.chmod(0o700)


def test_retention_prunes_old_versions(tmp_path: Path) -> None:
    dest = tmp_path / "backups"
    day_ms = 86_400_000
    now = int(time.time() * 1000)
    # 12 fake versions, one per day going back (+1min slack so the age
    # computed at prune time stays strictly under the day boundary)
    for i in range(12):
        vdir = mf.versions_dir(dest) / f"v{i:02d}"
        vdir.mkdir(parents=True)
        mf.write_manifest(
            vdir,
            mf.BackupManifest(
                version_id=f"v{i:02d}", created_at=now - i * day_ms + 60_000, total_bytes=10
            ),
        )
    retention = BackupRetention(keep_recent=3, keep_daily_days=5, max_total_gb=0)
    pruned = apply_retention(dest, retention)
    remaining = {m.version_id for _, m in mf.scan_versions(dest)}
    # 3 newest always kept; days 3..5 kept by the daily ladder; rest pruned
    assert {"v00", "v01", "v02"} <= remaining
    assert {"v03", "v04", "v05"} <= remaining
    assert all(v not in remaining for v in ("v06", "v07", "v11"))
    assert set(pruned) == {f"v{i:02d}" for i in range(6, 12)}


def test_restore_apply_roundtrip(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    result = run_backup(plan)
    vdir = mf.version_dir(plan.destination, result.version_id)

    # mutate live state after the backup
    (tmp_path / "data" / "memories" / "MEMORY.md").write_text("changed", encoding="utf-8")
    (tmp_path / "data" / "installation.json").write_text('{"id": "mutated"}', encoding="utf-8")
    _touch_db(plan.host_db)

    pointer = tmp_path / "pending.json"
    result_file = tmp_path / "result.json"
    write_pending_request(pointer, vdir)
    assert read_pending_request(pointer) == vdir

    report = apply_pending_restore(pointer, result_file, "u1")
    assert report is not None and report["ok"], report
    assert not pointer.exists()  # one-shot
    assert json.loads(result_file.read_text())["ok"]

    # data file rolled back to backed-up content
    assert (tmp_path / "data" / "memories" / "MEMORY.md").read_text() == "hello"
    # single-file target rolled back too (regression: unlink, not rmtree)
    assert (tmp_path / "data" / "installation.json").read_text() == '{"id": "orig"}'
    # DB rolled back: the post-backup insert is gone
    conn = sqlite3.connect(str(plan.host_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM valuz_project").fetchone()[0] == 3
    finally:
        conn.close()
    # a pre_restore safety snapshot exists alongside the source version
    kinds = {m.kind for _, m in mf.scan_versions(plan.destination)}
    assert "pre_restore" in kinds


def test_replace_tree_failure_preserves_target(tmp_path: Path, monkeypatch) -> None:
    """A failed payload copy must leave the live directory untouched —
    materialize-then-swap, never delete-then-copy."""
    from valuz_agent.modules.backup import restore as restore_mod

    src = tmp_path / "payload"
    src.mkdir()
    (src / "new.txt").write_text("new", encoding="utf-8")
    target = tmp_path / "deep" / "live"
    target.mkdir(parents=True)
    (target / "precious.txt").write_text("precious", encoding="utf-8")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(restore_mod.shutil, "copytree", _boom)
    with pytest.raises(OSError):
        restore_mod._replace_tree(src, target)
    assert (target / "precious.txt").read_text() == "precious"


def test_replace_tree_recovers_interrupted_swap(tmp_path: Path) -> None:
    """Crash between the two swap renames leaves only ``.restore-old`` —
    the next attempt must resurrect it before staging anew."""
    from valuz_agent.modules.backup.restore import _replace_tree

    src = tmp_path / "payload"
    src.mkdir()
    (src / "new.txt").write_text("new", encoding="utf-8")
    # simulate the crash state: target gone, old content parked aside
    parked = tmp_path / "deep" / "live.restore-old"
    parked.mkdir(parents=True)
    (parked / "old.txt").write_text("old", encoding="utf-8")

    _replace_tree(src, tmp_path / "deep" / "live")
    assert (tmp_path / "deep" / "live" / "new.txt").read_text() == "new"
    assert not parked.exists()


def test_restore_missing_manifest_reports_error(tmp_path: Path) -> None:
    pointer = tmp_path / "pending.json"
    result_file = tmp_path / "result.json"
    write_pending_request(pointer, tmp_path / "nonexistent-version")
    report = apply_pending_restore(pointer, result_file, "u1")
    assert report is not None and not report["ok"]
    assert report["error"]
    assert not pointer.exists()  # still one-shot on failure


# ── restore safety: the version under restore must survive, and a damaged
# version must never be applied as "empty" ─────────────────────────────


def _many_versions(tmp_path: Path, count: int) -> tuple[BackupPlan, list[str]]:
    """``count`` versions of one plan, oldest first, none pruned during setup."""
    plan = _plan(tmp_path, retention=BackupRetention(keep_recent=100, max_total_gb=0))
    ids: list[str] = []
    for _ in range(count):
        _touch_db(plan.host_db)
        ids.append(run_backup(plan).version_id)
    return plan, ids


def test_restore_does_not_prune_the_version_being_restored(tmp_path: Path) -> None:
    """Regression: the pre-restore safety snapshot is a backup run into the
    same destination. With default retention (keep_recent=7, one survivor per
    older day) nine same-day versions lose their oldest — which is exactly
    the one the user picked. Restoring it then swapped EMPTY directories over
    live data and reported success."""
    plan, ids = _many_versions(tmp_path, 8)
    oldest = ids[0]
    vdir = mf.version_dir(plan.destination, oldest)
    (tmp_path / "data" / "memories" / "MEMORY.md").write_text("changed", encoding="utf-8")

    pointer = tmp_path / "pending.json"
    write_pending_request(pointer, vdir)
    report = apply_pending_restore(pointer, tmp_path / "result.json", "u1")

    assert report is not None and report["ok"], report
    assert vdir.is_dir() and mf.load_manifest(vdir) is not None  # still there
    assert (tmp_path / "data" / "memories" / "MEMORY.md").read_text() == "hello"
    kinds = {m.kind for _, m in mf.scan_versions(plan.destination)}
    assert "pre_restore" in kinds
    # nothing was pruned by the restore — every version is still listed
    assert len(mf.scan_versions(plan.destination)) == len(ids) + 1


def test_restore_refuses_a_version_whose_payload_is_missing(tmp_path: Path) -> None:
    """A manifest that lists files under a category whose payload directory
    is gone is a damaged version. It must fail loudly and leave live data
    alone — never mirror the absence as an empty directory."""
    plan = _plan(tmp_path)
    result = run_backup(plan)
    vdir = mf.version_dir(plan.destination, result.version_id)
    import shutil as _shutil

    _shutil.rmtree(vdir / "data" / "memories")

    live = tmp_path / "data" / "memories" / "MEMORY.md"
    live.write_text("still here", encoding="utf-8")
    pointer = tmp_path / "pending.json"
    write_pending_request(pointer, vdir)
    report = apply_pending_restore(pointer, tmp_path / "result.json", "u1")

    assert report is not None and not report["ok"]
    assert "data/memories" in (report["error"] or "")
    assert live.read_text() == "still here"
    assert (tmp_path / "data" / "installation.json").exists()
    # not even a pre_restore snapshot was minted for a damaged version
    kinds = {m.kind for _, m in mf.scan_versions(plan.destination)}
    assert "pre_restore" not in kinds


def test_restore_replays_a_category_that_was_empty_at_backup_time(tmp_path: Path) -> None:
    """The legitimate case for ``_replace_tree(None, …)``: the category
    existed but held nothing, so the manifest lists nothing under it and
    the restore makes the live directory empty again."""
    empty = tmp_path / "data" / "empty-cat"
    empty.mkdir(parents=True)
    plan = _plan(tmp_path)
    plan.sources.append(SourceSpec(rel="empty-cat", src=empty))
    result = run_backup(plan)
    vdir = mf.version_dir(plan.destination, result.version_id)

    (empty / "later.txt").write_text("added after backup", encoding="utf-8")
    pointer = tmp_path / "pending.json"
    write_pending_request(pointer, vdir)
    report = apply_pending_restore(pointer, tmp_path / "result.json", "u1")

    assert report is not None and report["ok"], report
    assert empty.is_dir() and not any(empty.iterdir())


def test_restore_recreates_symlinks(tmp_path: Path) -> None:
    """Links are recorded in the manifest and never copied, so the payload
    has no entry for them; the restore must recreate them from the record."""
    memories = tmp_path / "data" / "memories"
    plan = _plan(tmp_path)
    (memories / "nested").mkdir()
    os.symlink("MEMORY.md", memories / "alias.md")
    os.symlink("../MEMORY.md", memories / "nested" / "up.md")
    result = run_backup(plan)
    vdir = mf.version_dir(plan.destination, result.version_id)
    manifest = mf.load_manifest(vdir)
    assert manifest is not None
    assert {f.path for f in manifest.files if f.link} == {
        "data/memories/alias.md",
        "data/memories/nested/up.md",
    }

    (memories / "alias.md").unlink()
    pointer = tmp_path / "pending.json"
    write_pending_request(pointer, vdir)
    report = apply_pending_restore(pointer, tmp_path / "result.json", "u1")

    assert report is not None and report["ok"], report
    assert (memories / "alias.md").is_symlink()
    assert os.readlink(memories / "alias.md") == "MEMORY.md"
    assert (memories / "nested" / "up.md").is_symlink()
    assert (memories / "alias.md").read_text() == "hello"


def test_restore_rejects_a_newer_manifest_format(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    result = run_backup(plan)
    vdir = mf.version_dir(plan.destination, result.version_id)
    raw = json.loads((vdir / mf.MANIFEST_NAME).read_text(encoding="utf-8"))
    raw["format"] = mf.MANIFEST_FORMAT + 1
    (vdir / mf.MANIFEST_NAME).write_text(json.dumps(raw), encoding="utf-8")

    pointer = tmp_path / "pending.json"
    write_pending_request(pointer, vdir)
    report = apply_pending_restore(pointer, tmp_path / "result.json", "u1")
    assert report is not None and not report["ok"]
    assert "format" in (report["error"] or "")


def test_extra_dbs_ride_along_and_restore(tmp_path: Path) -> None:
    ckpt = tmp_path / "deepagents_checkpoints.db"
    _make_sqlite(ckpt, "checkpoints", 2)
    plan = _plan(tmp_path, extra_dbs=[SourceSpec(rel="deepagents_checkpoints.db", src=ckpt)])
    result = run_backup(plan)
    vdir = mf.version_dir(plan.destination, result.version_id)
    assert (vdir / "db" / "deepagents_checkpoints.db").is_file()
    assert any(rt.rel == "db/deepagents_checkpoints.db" for rt in result.manifest.restore_targets)

    conn = sqlite3.connect(str(ckpt))
    try:
        conn.execute("INSERT INTO checkpoints (v) VALUES ('after')")
        conn.commit()
    finally:
        conn.close()
    pointer = tmp_path / "pending.json"
    write_pending_request(pointer, vdir)
    report = apply_pending_restore(pointer, tmp_path / "result.json", "u1")
    assert report is not None and report["ok"], report
    conn = sqlite3.connect(str(ckpt))
    try:
        assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 2
    finally:
        conn.close()


def test_first_backup_preflight_counts_source_bytes(tmp_path: Path, monkeypatch) -> None:
    """The first version copies every byte; sizing it from the DBs alone
    let a large project tree fill the disk mid-run."""
    from valuz_agent.modules.backup import engine as engine_mod

    plan = _plan(tmp_path)
    big = tmp_path / "data" / "memories" / "big.bin"
    big.write_bytes(b"x" * 4096)
    seen: dict[str, int] = {}
    real = engine_mod.shutil.disk_usage

    class _Usage:
        def __init__(self, free: int) -> None:
            self.free = free

    def _fake_usage(path):  # type: ignore[no-untyped-def]
        seen["called"] = 1
        return _Usage(free=1024)  # smaller than the 4 KiB source alone

    monkeypatch.setattr(engine_mod.shutil, "disk_usage", _fake_usage)
    with pytest.raises(BackupPreflightFailed):
        run_backup(plan)
    monkeypatch.setattr(engine_mod.shutil, "disk_usage", real)
    assert seen
