"""Login-shell PATH enrichment (boot/login_path.py).

The probe runs the user's shell; tests point ``$SHELL`` at stub scripts so
they exercise the real subprocess + delimiter-extraction path without
depending on the developer's dotfiles.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from valuz_agent.boot.login_path import (
    _DELIM,
    _login_shell_path,
    enrich_login_shell_path,
    merge_paths,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="posix-only step")


# --- merge_paths (pure) ------------------------------------------------------


def test_should_append_missing_entries_preserving_current_precedence() -> None:
    merged = merge_paths("/usr/bin:/bin", "/fake/nvm/bin:/usr/bin:/opt/x")
    assert merged == "/usr/bin:/bin:/fake/nvm/bin:/opt/x"


def test_should_be_a_noop_when_login_adds_nothing() -> None:
    assert merge_paths("/usr/bin:/bin", "/bin:/usr/bin") == "/usr/bin:/bin"


def test_should_drop_empty_segments_and_dedupe_login_entries() -> None:
    merged = merge_paths("/usr/bin::/bin:", "/x::/x:/y")
    assert merged == "/usr/bin:/bin:/x:/y"


# --- _login_shell_path (subprocess + extraction) -----------------------------


def _stub_shell(tmp_path: Path, body: str) -> str:
    script = tmp_path / "stub-shell"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


async def test_should_extract_path_between_delimiters_ignoring_dotfile_noise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = _stub_shell(
        tmp_path,
        f'echo "banner from zshrc"\n'
        f'echo "{_DELIM}"\n'
        f'echo "/fake/nvm/bin:/usr/bin"\n'
        f'echo "{_DELIM}"\n'
        f'echo "trailing noise"\n',
    )
    monkeypatch.setenv("SHELL", shell)

    assert await _login_shell_path() == "/fake/nvm/bin:/usr/bin"


async def test_should_return_none_when_output_has_no_delimiters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = _stub_shell(tmp_path, 'echo "no delimiters here"\n')
    monkeypatch.setenv("SHELL", shell)

    assert await _login_shell_path() is None


async def test_should_return_none_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = _stub_shell(tmp_path, "sleep 5\n")
    monkeypatch.setenv("SHELL", shell)

    assert await _login_shell_path(timeout_s=0.2) is None


async def test_should_return_none_when_shell_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHELL", "/nonexistent/valuz-test-shell")

    assert await _login_shell_path() is None


# --- enrich_login_shell_path (end to end on os.environ) ----------------------


async def test_should_append_login_entries_to_process_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = _stub_shell(
        tmp_path,
        f'echo "{_DELIM}"\necho "/fake/nvm/bin:/usr/bin"\necho "{_DELIM}"\n',
    )
    monkeypatch.setenv("SHELL", shell)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("VALUZ_DISABLE_LOGIN_PATH", raising=False)

    await enrich_login_shell_path()

    assert os.environ["PATH"] == "/usr/bin:/bin:/fake/nvm/bin"


async def test_should_do_nothing_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shell = _stub_shell(
        tmp_path,
        f'echo "{_DELIM}"\necho "/fake/nvm/bin"\necho "{_DELIM}"\n',
    )
    monkeypatch.setenv("SHELL", shell)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv("VALUZ_DISABLE_LOGIN_PATH", "1")

    await enrich_login_shell_path()

    assert os.environ["PATH"] == "/usr/bin:/bin"
