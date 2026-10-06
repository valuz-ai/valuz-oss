"""Shared fixtures for the bundled Office skills' scripts.

The scripts run the way a session runs them: ``valuz-python`` and ``dsoffice``
resolved from PATH. ``office_env`` installs both wrappers into a temp bin dir
with the session-tools code the backend uses at boot and returns an
environment with that dir first on PATH. Tests that need a tool skip when the
vendored runtime is not built (``scripts/vendor-python-runtime.sh``) or the dsh
closure is not installed (``scripts/vendor-dsh-runtime.sh``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from valuz_agent.infra import session_tools

SKILLS_DIR = (
    Path(session_tools.__file__).resolve().parents[1]
    / "resources"
    / "bundled_plugins"
    / "office"
    / "skills"
)


@pytest.fixture(scope="session")
def office_env(tmp_path_factory) -> dict[str, str]:
    bin_dir = tmp_path_factory.mktemp("session-bin")
    saved = os.environ.get("PATH", "")
    try:
        installed = session_tools.ensure_session_tools_on_path(bin_dir)
    finally:
        os.environ["PATH"] = saved
    if session_tools.PYTHON_COMMAND not in installed:
        pytest.skip("bundled session python not built (scripts/vendor-python-runtime.sh)")
    env = {**os.environ, "PATH": os.pathsep.join([str(bin_dir), saved])}
    # The skill directories ship as-is: no bytecode caches next to the scripts.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["VALUZ_TEST_HAS_DSOFFICE"] = "1" if session_tools.OFFICE_COMMAND in installed else ""
    return env


@pytest.fixture
def needs_dsoffice(office_env: dict[str, str]) -> None:
    if not office_env["VALUZ_TEST_HAS_DSOFFICE"]:
        pytest.skip("dsoffice unavailable (dsh closure or node missing)")


def run(argv: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command the way an agent shell would; raises on a non-zero exit."""
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, check=True)
