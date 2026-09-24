"""Lifecycle tests for ``modules/worktrees/service`` (no table — git + sidecar)."""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from valuz_agent.infra import git_worktree as gw
from valuz_agent.modules.worktrees.errors import (
    InvalidWorktreeName,
    WorktreeDirty,
    WorktreeNotAvailable,
)
from valuz_agent.modules.worktrees.service import WorktreeService

pytestmark = pytest.mark.skipif(
    not gw.git_available(), reason="git not available on this machine"
)

USER = "local-test-owner"


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@dataclass
class FakeProjectRow:
    id: str
    kind: str
    root_path: str | None


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    (root / "README.md").write_text("hello\n")
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("x = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "init")
    return root


@pytest.fixture()
def project(repo: Path) -> FakeProjectRow:
    return FakeProjectRow(id="p1", kind="project", root_path=str(repo))


@pytest.fixture()
def svc() -> WorktreeService:
    return WorktreeService()


# ---- gating ----------------------------------------------------------------


async def test_project_git_info(svc: WorktreeService, project: FakeProjectRow, repo: Path):
    info = await svc.project_git(USER, project)
    assert info.git_available and info.is_repo
    assert info.git_root == str(repo.resolve())
    assert info.subdir == ""


async def test_project_git_subdir(svc: WorktreeService, repo: Path):
    sub_project = FakeProjectRow(id="p2", kind="project", root_path=str(repo / "pkg"))
    info = await svc.project_git(USER, sub_project)
    assert info.is_repo and info.subdir == "pkg"


async def test_non_git_project_rejected(svc: WorktreeService, tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    row = FakeProjectRow(id="p3", kind="project", root_path=str(plain))
    assert (await svc.project_git(USER, row)).is_repo is False
    with pytest.raises(WorktreeNotAvailable):
        await svc.get_or_create(USER, row)


# ---- lifecycle -------------------------------------------------------------


async def test_get_or_create_writes_sidecar_and_resumes(
    svc: WorktreeService, project: FakeProjectRow, repo: Path
):
    handle = await svc.get_or_create(USER, project, name="feat-a")
    assert handle.created is True
    assert handle.branch == "valuz/u-feat-a"
    assert handle.session_cwd == handle.path
    sidecar = repo / ".valuz" / "worktrees" / "feat-a.meta.json"
    meta = json.loads(sidecar.read_text())
    assert meta["origin"] == "u" and meta["base_sha"] == handle.base_sha

    resumed = await svc.get_or_create(USER, project, name="feat-a")
    assert resumed.created is False
    assert resumed.base_sha == handle.base_sha  # anchor survives resume


async def test_auto_generated_name_is_friendly(
    svc: WorktreeService, project: FakeProjectRow
):
    """D11: unnamed worktrees get a pronounceable adjective-noun-hex slug."""
    import re

    handle = await svc.get_or_create(USER, project)
    assert re.fullmatch(r"[a-z]+-[a-z]+-[0-9a-f]{6}", handle.name), handle.name
    gw.validate_slug(handle.name)  # generator output must satisfy slug rules
    assert handle.branch == f"valuz/u-{handle.name}"
    await svc.discard(USER, project, handle.name)


async def test_invalid_name_rejected(svc: WorktreeService, project: FakeProjectRow):
    with pytest.raises(InvalidWorktreeName):
        await svc.get_or_create(USER, project, name="../escape")


async def test_session_cwd_projects_subdir(svc: WorktreeService, repo: Path):
    sub_project = FakeProjectRow(id="p2", kind="project", root_path=str(repo / "pkg"))
    handle = await svc.get_or_create(USER, sub_project, name="feat-sub")
    assert handle.session_cwd == str(Path(handle.path) / "pkg")


async def test_list_for_project(svc: WorktreeService, project: FakeProjectRow):
    handle = await svc.get_or_create(USER, project, name="feat-l")
    (Path(handle.path) / "dirty.txt").write_text("x")
    items = await svc.list_for_project(USER, project)
    assert len(items) == 1
    item = items[0]
    assert item.name == "feat-l" and item.origin == "u"
    assert item.base_sha == handle.base_sha
    assert item.dirty_files == 1 and item.ahead_commits == 0


async def test_discard_fail_closed_then_force(
    svc: WorktreeService, project: FakeProjectRow
):
    handle = await svc.get_or_create(USER, project, name="feat-d")
    (Path(handle.path) / "dirty.txt").write_text("x")

    with pytest.raises(WorktreeDirty):
        await svc.discard(USER, project, "feat-d")
    assert Path(handle.path).exists()

    await svc.discard(USER, project, "feat-d", force=True)
    assert not Path(handle.path).exists()
    assert await svc.list_for_project(USER, project) == []


async def test_discard_clean_needs_no_force(svc: WorktreeService, project: FakeProjectRow):
    handle = await svc.get_or_create(USER, project, name="feat-c")
    await svc.discard(USER, project, "feat-c")
    assert not Path(handle.path).exists()


# ---- session-teardown hook -------------------------------------------------


def _snapshot(handle) -> dict[str, object]:  # noqa: ANN001
    return {
        "name": handle.name,
        "branch": handle.branch,
        "path": handle.path,
        "git_root": handle.git_root,
        "base_sha": handle.base_sha,
    }


async def test_cleanup_if_clean_removes(svc: WorktreeService, project: FakeProjectRow):
    handle = await svc.get_or_create(USER, project, name="auto-1")
    assert await svc.cleanup_if_clean(_snapshot(handle)) is True
    assert not Path(handle.path).exists()
    # Sidecar removed alongside.
    assert not (Path(handle.git_root) / ".valuz" / "worktrees" / "auto-1.meta.json").exists()


async def test_cleanup_if_clean_keeps_dirty(svc: WorktreeService, project: FakeProjectRow):
    handle = await svc.get_or_create(USER, project, name="auto-2")
    (Path(handle.path) / "keep-me.txt").write_text("important")
    assert await svc.cleanup_if_clean(_snapshot(handle)) is False
    assert Path(handle.path).exists()


async def test_cleanup_fail_closed_without_anchor(
    svc: WorktreeService, project: FakeProjectRow
):
    handle = await svc.get_or_create(USER, project, name="auto-3")
    snap = _snapshot(handle)
    snap["base_sha"] = None
    assert await svc.cleanup_if_clean(snap) is False
    assert Path(handle.path).exists()


async def test_heal_recreates_missing_worktree(
    svc: WorktreeService, project: FakeProjectRow, repo: Path
):
    handle = await svc.get_or_create(USER, project, name="heal-1")
    snap = _snapshot(handle)
    # Simulate the worktree being discarded after the session was created.
    await svc.discard(USER, project, "heal-1")
    assert not Path(handle.path).exists()

    refreshed = await svc.heal_from_snapshot(snap)
    assert refreshed is not None
    assert refreshed["path"] == handle.path  # deterministic path restored
    assert refreshed["branch"] == handle.branch
    assert (Path(handle.path) / ".git").exists()
    # Fresh anchor points at the repo's current HEAD.
    assert refreshed["base_sha"] == _git(repo, "rev-parse", "HEAD")


async def test_heal_noop_when_alive(svc: WorktreeService, project: FakeProjectRow):
    handle = await svc.get_or_create(USER, project, name="heal-2")
    assert await svc.heal_from_snapshot(_snapshot(handle)) is None


async def test_heal_raises_when_repo_gone(
    svc: WorktreeService, project: FakeProjectRow, tmp_path: Path
):
    from valuz_agent.modules.worktrees.errors import WorktreeNotAvailable

    handle = await svc.get_or_create(USER, project, name="heal-3")
    snap = _snapshot(handle)
    snap["git_root"] = str(tmp_path / "vanished")
    snap["path"] = str(tmp_path / "vanished" / ".valuz" / "worktrees" / "heal-3")
    with pytest.raises(WorktreeNotAvailable):
        await svc.heal_from_snapshot(snap)


async def test_cleanup_rejects_path_outside_managed_dir(
    svc: WorktreeService, project: FakeProjectRow, repo: Path
):
    handle = await svc.get_or_create(USER, project, name="auto-4")
    snap = _snapshot(handle)
    snap["path"] = str(repo)  # tampered: points at the main workspace
    assert await svc.cleanup_if_clean(snap) is False
    assert repo.exists()


# ---- workspace sync --------------------------------------------------------


@pytest.fixture
def sync_port(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from valuz_agent.ports.extensions import ext

    port = SimpleNamespace(after_write=AsyncMock(), before_read=AsyncMock())
    monkeypatch.setattr(ext, "workspace_sync", port)
    return port


def _expected_paths(handle) -> tuple[Path, ...]:  # noqa: ANN001
    git_root = Path(handle.git_root)
    common = git_root / ".git"
    flat = gw.flatten_slug(handle.name)
    return (
        Path(handle.path),
        git_root / ".valuz" / "worktrees" / f"{flat}.meta.json",
        common / "worktrees",
        common / "info" / "exclude",
        common / "packed-refs",
        common / "refs" / "heads" / handle.branch,
        common / "logs" / "refs" / "heads" / handle.branch,
    )


async def test_a_created_worktree_is_reported_a_resumed_one_is_not(
    svc: WorktreeService,
    project: FakeProjectRow,
    sync_port,  # noqa: ANN001
):
    handle = await svc.get_or_create(USER, project, name="sync-1")

    sync_port.after_write.assert_awaited_once_with(
        owner_user_id=USER, paths=_expected_paths(handle), project_id="p1"
    )
    # the admin dir a ``git`` run inside the checkout needs is what is covered
    assert (Path(handle.git_root) / ".git" / "worktrees").is_dir()

    await svc.get_or_create(USER, project, name="sync-1")  # fast-resume
    assert sync_port.after_write.await_count == 1


async def test_discard_and_clean_teardown_report_the_removal(
    svc: WorktreeService,
    project: FakeProjectRow,
    sync_port,  # noqa: ANN001
):
    first = await svc.get_or_create(USER, project, name="sync-2")
    second = await svc.get_or_create(USER, project, name="sync-3")
    sync_port.after_write.reset_mock()

    await svc.discard(USER, project, "sync-2")
    assert await svc.cleanup_if_clean(_snapshot(second), user_id=USER, project_id="p1")

    assert [c.kwargs for c in sync_port.after_write.await_args_list] == [
        {"owner_user_id": USER, "paths": _expected_paths(first), "project_id": "p1"},
        {"owner_user_id": USER, "paths": _expected_paths(second), "project_id": "p1"},
    ]
    assert not Path(first.path).exists() and not Path(second.path).exists()


async def test_teardown_without_an_owner_reports_nothing(
    svc: WorktreeService,
    project: FakeProjectRow,
    sync_port,  # noqa: ANN001
):
    handle = await svc.get_or_create(USER, project, name="sync-4")
    sync_port.after_write.reset_mock()

    assert await svc.cleanup_if_clean(_snapshot(handle)) is True

    sync_port.after_write.assert_not_awaited()


async def test_heal_reports_the_recreated_worktree(
    svc: WorktreeService,
    project: FakeProjectRow,
    sync_port,  # noqa: ANN001
):
    handle = await svc.get_or_create(USER, project, name="sync-5")
    snap = _snapshot(handle)
    await svc.discard(USER, project, "sync-5")
    sync_port.after_write.reset_mock()

    assert await svc.heal_from_snapshot(snap, user_id=USER, project_id="p1") is not None

    sync_port.after_write.assert_awaited_once_with(
        owner_user_id=USER, paths=_expected_paths(handle), project_id="p1"
    )
