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
    h = fsr.fs_registry.workspace_handle("proj-1", "chat")
    assert isinstance(h, WorkspaceHandle)
    assert h.cwd() == tmp_path / "projects" / "proj-1"
