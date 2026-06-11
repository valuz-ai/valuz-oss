"""Regression tests for kernel route-level payload validators.

Focus: ``validate_skills`` must accept absolute paths on the OS the kernel
actually runs on. A prior ``path.startswith("/")`` check rejected every valid
Windows skill path (e.g. ``C:\\Users\\...``) with a 400, breaking skills on
Windows hosts.
"""

from __future__ import annotations

import ntpath
import os
import posixpath

import pytest
from fastapi import HTTPException

# Side-effect import: puts the kernel root on sys.path so ``from app.*`` below
# resolves. Mirrors tests/runtimes/test_claude_buffer_size.py. The ``from
# app.*`` import is deferred into the fixture so isort does not hoist it above
# this side-effect import.
import kernel  # noqa: F401

WINDOWS_SKILL = r"C:\Users\Administrator\.valuz\app\official-skills\valuz-handbook"
POSIX_SKILL = "/Users/me/.valuz/app/official-skills/valuz-handbook"


@pytest.fixture
def validate_skills():
    from app._validators import validate_skills as _validate_skills

    return _validate_skills


def test_posix_absolute_path_accepted(validate_skills) -> None:
    validate_skills([POSIX_SKILL])  # no raise


def test_relative_path_rejected_with_400(validate_skills) -> None:
    with pytest.raises(HTTPException) as exc:
        validate_skills(["official-skills/valuz-handbook"])
    assert exc.value.status_code == 400
    assert "must be absolute paths" in exc.value.detail


@pytest.mark.parametrize(
    "isabs",
    [pytest.param(ntpath.isabs, id="windows"), pytest.param(posixpath.isabs, id="posix")],
)
def test_absolute_path_accepted_under_host_semantics(monkeypatch, validate_skills, isabs) -> None:
    # ``validate_skills`` delegates to ``os.path.isabs``; pin it to a specific
    # platform's semantics so both Windows and POSIX behaviour are proven on
    # any CI host. monkeypatch restores the original afterwards.
    monkeypatch.setattr(os.path, "isabs", isabs)
    skill = WINDOWS_SKILL if isabs is ntpath.isabs else POSIX_SKILL
    validate_skills([skill])  # no raise


def test_windows_path_rejected_under_posix_semantics(monkeypatch, validate_skills) -> None:
    # The inverse guard: a Windows path is correctly meaningless on a POSIX
    # kernel and stays rejected there (downstream materialization would fail).
    monkeypatch.setattr(os.path, "isabs", posixpath.isabs)
    with pytest.raises(HTTPException) as exc:
        validate_skills([WINDOWS_SKILL])
    assert exc.value.status_code == 400
