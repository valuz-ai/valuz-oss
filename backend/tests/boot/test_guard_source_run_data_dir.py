"""A source-run (non-frozen) backend refuses the packaged app's data dir.

Regression guard for the recurring incident class where a dev/test backend
pointed at the real ``~/.valuz-oss`` migrates ``alembic_version_host`` ahead
of the released build, which then fail-louds at its next boot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from valuz_agent.boot import steps
from valuz_agent.infra.config import PACKAGED_DATA_DIR, settings


@pytest.fixture(autouse=True)
def _sandboxed_dirs(monkeypatch, tmp_path: Path):
    """Default both roots to a sandbox; each test overrides what it probes."""
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(
        settings, "log_file_path", tmp_path / "data" / "logs" / "backend.log"
    )
    monkeypatch.delenv("VALUZ_ALLOW_PACKAGED_DATA_DIR", raising=False)


def test_passes_on_non_packaged_root() -> None:
    steps.guard_source_run_data_dir()  # must not raise


def test_refuses_packaged_data_dir(monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", PACKAGED_DATA_DIR)
    with pytest.raises(RuntimeError, match="data dir"):
        steps.guard_source_run_data_dir()


def test_refuses_packaged_log_dir(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "log_file_path", PACKAGED_DATA_DIR / "logs" / "backend.log"
    )
    with pytest.raises(RuntimeError, match="log dir"):
        steps.guard_source_run_data_dir()


def test_escape_hatch_allows_packaged_root(monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", PACKAGED_DATA_DIR)
    monkeypatch.setattr(
        settings, "log_file_path", PACKAGED_DATA_DIR / "logs" / "backend.log"
    )
    monkeypatch.setenv("VALUZ_ALLOW_PACKAGED_DATA_DIR", "1")
    steps.guard_source_run_data_dir()  # must not raise


def test_frozen_build_is_exempt(monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", PACKAGED_DATA_DIR)
    monkeypatch.setattr(
        settings, "log_file_path", PACKAGED_DATA_DIR / "logs" / "backend.log"
    )
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    steps.guard_source_run_data_dir()  # must not raise


def test_user_template_root_matching_packaged_root_refused(monkeypatch) -> None:
    """A ``{user_id}``-templated root still trips when it collapses to the
    packaged root (the shared-root form strips the placeholder)."""
    monkeypatch.setattr(settings, "data_dir", Path(str(PACKAGED_DATA_DIR) + "/{user_id}"))
    with pytest.raises(RuntimeError, match="data dir"):
        steps.guard_source_run_data_dir()
