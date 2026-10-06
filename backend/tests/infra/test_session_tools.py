"""Session command wrappers: ``valuz-python`` and ``dsoffice`` on the agent PATH."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from valuz_agent.infra import session_tools
from valuz_agent.infra.session_tools import (
    OFFICE_COMMAND,
    PYTHON_COMMAND,
    ensure_session_tools_on_path,
    office_kit_cli,
    python_executable,
    wrapper_body,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX wrapper layout")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path: Path):
    """Neutralize every channel so each test opts in explicitly."""
    for env in (
        session_tools.PYTHON_RUNTIME_ENV,
        session_tools.DSH_RUNTIME_ENTRY_ENV,
        session_tools.NODE_PATH_ENV,
        session_tools.NODE_IS_ELECTRON_ENV,
    ):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(session_tools, "_VENDORED_PYTHON_RUNTIME", tmp_path / "no-python")
    monkeypatch.setattr(session_tools, "_VENDORED_DSH_NODE_MODULES", tmp_path / "no-dsh")
    monkeypatch.setattr(session_tools.shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")


def _python_runtime(root: Path) -> Path:
    exe = root / "bin" / "python3"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    return exe


def _dsh_closure(root: Path) -> tuple[Path, Path]:
    """A closure with the launcher and the kit CLI where the real one has them."""
    entry = root / "node_modules" / "valuz-dsh-bundle" / "bin" / "dsh.mjs"
    cli = root / "node_modules" / "@deepseek-ai" / "libreoffice-kit" / "lib" / "cli.js"
    for path in (entry, cli):
        path.parent.mkdir(parents=True)
        path.write_text("")
    return entry, cli


class TestResolution:
    def test_packaged_python_runtime_wins(self, monkeypatch, tmp_path: Path) -> None:
        packaged = _python_runtime(tmp_path / "libexec" / "python-runtime")
        _python_runtime(tmp_path / "no-python")  # the dev copy is ignored
        monkeypatch.setenv(session_tools.PYTHON_RUNTIME_ENV, str(packaged.parents[1]))
        assert python_executable() == packaged

    def test_dev_checkout_uses_the_vendored_runtime(self, tmp_path: Path) -> None:
        vendored = _python_runtime(tmp_path / "no-python")
        assert python_executable() == vendored

    def test_windows_layout(self, monkeypatch, tmp_path: Path) -> None:
        root = tmp_path / "python-runtime"
        root.mkdir()
        (root / "python.exe").write_text("")
        monkeypatch.setenv(session_tools.PYTHON_RUNTIME_ENV, str(root))
        assert python_executable() == root / "python.exe"

    def test_no_python_runtime(self) -> None:
        assert python_executable() is None

    def test_kit_cli_follows_the_packaged_dsh_closure(self, monkeypatch, tmp_path: Path) -> None:
        entry, cli = _dsh_closure(tmp_path / "libexec" / "dsh-runtime")
        monkeypatch.setenv(session_tools.DSH_RUNTIME_ENTRY_ENV, str(entry))
        assert office_kit_cli() == cli

    def test_kit_cli_missing(self, monkeypatch, tmp_path: Path) -> None:
        entry = tmp_path / "node_modules" / "valuz-dsh-bundle" / "bin" / "dsh.mjs"
        monkeypatch.setenv(session_tools.DSH_RUNTIME_ENTRY_ENV, str(entry))
        assert office_kit_cli() is None


class TestWrapperBody:
    def test_forwards_arguments(self) -> None:
        body = wrapper_body(["/opt/py/bin/python3"])
        assert body == '#!/bin/sh\nexec /opt/py/bin/python3 "$@"\n'

    def test_electron_as_node_is_set_inside_the_wrapper(self) -> None:
        body = wrapper_body(["/Apps/Valuz", "/k/cli.js"], electron_as_node=True)
        assert "export ELECTRON_RUN_AS_NODE=1\n" in body
        assert body.endswith('exec /Apps/Valuz /k/cli.js "$@"\n')

    def test_paths_with_spaces_are_quoted(self) -> None:
        body = wrapper_body(["/Applications/Valuz App/node", "/k/cli.js"])
        assert "exec '/Applications/Valuz App/node' /k/cli.js" in body


class TestInstall:
    def test_installs_both_and_prepends_path(self, monkeypatch, tmp_path: Path) -> None:
        python = _python_runtime(tmp_path / "py")
        entry, cli = _dsh_closure(tmp_path / "dsh")
        monkeypatch.setenv(session_tools.PYTHON_RUNTIME_ENV, str(python.parents[1]))
        monkeypatch.setenv(session_tools.DSH_RUNTIME_ENTRY_ENV, str(entry))
        monkeypatch.setenv(session_tools.NODE_PATH_ENV, "/Apps/Valuz.app/MacOS/Valuz")
        monkeypatch.setenv(session_tools.NODE_IS_ELECTRON_ENV, "1")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()

        assert ensure_session_tools_on_path(bin_dir) == [PYTHON_COMMAND, OFFICE_COMMAND]
        assert os.environ["PATH"].split(os.pathsep)[0] == str(bin_dir)
        office = (bin_dir / OFFICE_COMMAND).read_text()
        assert "ELECTRON_RUN_AS_NODE=1" in office and str(cli) in office
        assert os.access(bin_dir / PYTHON_COMMAND, os.X_OK)

        # Idempotent: a second boot does not duplicate the PATH entry.
        ensure_session_tools_on_path(bin_dir)
        assert os.environ["PATH"].split(os.pathsep).count(str(bin_dir)) == 1

    def test_missing_tools_remove_stale_wrappers(self, tmp_path: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        for name in (PYTHON_COMMAND, OFFICE_COMMAND):
            (bin_dir / name).write_text('#!/bin/sh\nexec /gone "$@"\n')
        assert ensure_session_tools_on_path(bin_dir) == []
        assert not (bin_dir / PYTHON_COMMAND).exists()
        assert not (bin_dir / OFFICE_COMMAND).exists()

    def test_office_needs_node(self, monkeypatch, tmp_path: Path) -> None:
        entry, _ = _dsh_closure(tmp_path / "dsh")
        monkeypatch.setenv(session_tools.DSH_RUNTIME_ENTRY_ENV, str(entry))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        assert OFFICE_COMMAND not in ensure_session_tools_on_path(bin_dir)


_VENDOR = session_tools._BACKEND_DIR / "vendor"
_REAL_PYTHON = _VENDOR / "python-runtime" / "dist" / "bin" / "python3"
_REAL_KIT = _VENDOR / "dsh-runtime" / "node_modules" / session_tools._KIT_CLI_REL


@pytest.mark.skipif(
    not _REAL_PYTHON.is_file() or not _REAL_KIT.is_file(),
    reason="vendored python runtime / dsh closure not installed",
)
def test_real_wrappers_run(monkeypatch, tmp_path: Path) -> None:
    """The installed wrappers run the real bundled tools from any directory."""
    monkeypatch.setattr(session_tools, "_VENDORED_PYTHON_RUNTIME", _REAL_PYTHON.parents[1])
    monkeypatch.setattr(session_tools, "_VENDORED_DSH_NODE_MODULES", _REAL_KIT.parents[3])
    # The autouse fixture hid node; this test runs the real kit on the real one.
    monkeypatch.setattr(session_tools.shutil, "which", __import__("shutil").which)
    extra = "/opt/homebrew/bin:/usr/local/bin"  # where node usually lives on dev machines
    monkeypatch.setenv("PATH", os.environ.get("PATH", "") + os.pathsep + extra)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    installed = ensure_session_tools_on_path(bin_dir)
    assert PYTHON_COMMAND in installed

    out = subprocess.run(
        [str(bin_dir / PYTHON_COMMAND), "-c", "import docx, openpyxl, pptx, pandas; print('ok')"],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    ).stdout
    assert out.strip() == "ok"
    if OFFICE_COMMAND in installed:
        caps = subprocess.run(
            [str(bin_dir / OFFICE_COMMAND), "capabilities"],
            capture_output=True,
            text=True,
            check=True,
            cwd=tmp_path,
        ).stdout
        assert '"recalculation"' in caps
