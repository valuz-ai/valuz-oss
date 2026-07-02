"""WorkspaceHandle — the project-domain FS abstraction (⑤).

The local implementation is the terminal form for the local deployment;
these pin its path + async-IO behaviour and the FsRegistry factory.
"""

from __future__ import annotations

import pytest

from valuz_agent.ports.workspace import LocalWorkspaceHandle, WorkspaceHandle


def test_local_handle_satisfies_protocol(tmp_path) -> None:
    h = LocalWorkspaceHandle(tmp_path)
    assert isinstance(h, WorkspaceHandle)
    assert h.cwd() == tmp_path
    assert h.subpath("a", "b.txt") == tmp_path / "a" / "b.txt"


@pytest.mark.asyncio
async def test_local_handle_io_roundtrip(tmp_path) -> None:
    h = LocalWorkspaceHandle(tmp_path)
    assert await h.exists("x") is False
    await h.write_bytes("nested/x.txt", b"hi")  # parents created
    assert await h.exists("nested/x.txt") is True
    assert await h.read_bytes("nested/x.txt") == b"hi"


def test_fs_registry_factory_returns_handle_over_project_cwd(tmp_path, monkeypatch) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "data_dir", tmp_path)
    monkeypatch.setattr(fsr.settings, "user_project_root", tmp_path / "user-project")
    h = fsr.fs_registry.workspace_handle("user-A", "proj-1", "chat")
    assert isinstance(h, WorkspaceHandle)
    assert h.cwd() == tmp_path / "user-project" / "proj-1"


def test_fs_registry_data_dir_is_scoped_by_current_user(tmp_path, monkeypatch) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "data_dir", tmp_path)
    monkeypatch.setattr(fsr.settings, "db_filename", "valuz.db")
    monkeypatch.setattr(fsr.settings, "kernel_db_filename", "kernel.db")
    monkeypatch.setattr(fsr.settings, "database_url", None)
    monkeypatch.setattr(fsr.settings, "kernel_database_url", None)
    monkeypatch.setattr(fsr.settings, "user_project_root", tmp_path / "user-project")

    assert fsr.fs_registry.data_dir("user-A") == tmp_path / "user-A"
    assert fsr.fs_registry.db_url("user-A") == f"sqlite:///{tmp_path / 'user-A' / 'valuz.db'}"
    assert fsr.fs_registry.kernel_db_url("user-A") == (
        f"sqlite:///{tmp_path / 'user-A' / 'kernel.db'}"
    )
    assert fsr.fs_registry.project_cwd("user-A", "proj-1", "chat") == (
        tmp_path / "user-project" / "proj-1"
    )

    assert fsr.fs_registry.data_dir("user-B") == tmp_path / "user-B"
    assert fsr.fs_registry.db_url("user-B") == f"sqlite:///{tmp_path / 'user-B' / 'valuz.db'}"
    assert fsr.fs_registry.kernel_db_url("user-B") == (
        f"sqlite:///{tmp_path / 'user-B' / 'kernel.db'}"
    )
    assert fsr.fs_registry.project_cwd("user-B", "proj-1", "chat") == (
        tmp_path / "user-project" / "proj-1"
    )


def test_fs_registry_example_project_dir_uses_local_project_root(tmp_path, monkeypatch) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(fsr.settings, "user_project_root", tmp_path / "Valuz")

    assert fsr.fs_registry.example_project_dir("user-A") == tmp_path / "Valuz" / "示例项目"
    assert fsr.fs_registry.example_project_dir("org/user-B") == tmp_path / "Valuz" / "示例项目"


def test_fs_registry_example_project_dir_expands_user_placeholder(
    tmp_path, monkeypatch
) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(
        fsr.settings, "user_project_root", tmp_path / "Valuz" / "{user_id}"
    )

    assert fsr.fs_registry.example_project_dir("user-A") == (
        tmp_path / "Valuz" / "user-A" / "示例项目"
    )
    assert fsr.fs_registry.example_project_dir("org/user-B") == (
        tmp_path / "Valuz" / "org__user-B" / "示例项目"
    )


def test_fs_registry_project_cwd_expands_user_placeholder(tmp_path, monkeypatch) -> None:
    from valuz_agent.infra import fs_registry as fsr

    monkeypatch.setattr(
        fsr.settings, "user_project_root", tmp_path / "Valuz" / "{user_id}"
    )

    assert fsr.fs_registry.project_cwd("user-A", "proj-1", "chat") == (
        tmp_path / "Valuz" / "user-A" / "proj-1"
    )
    assert fsr.fs_registry.project_cwd("org/user-B", "proj-1", "chat") == (
        tmp_path / "Valuz" / "org__user-B" / "proj-1"
    )
