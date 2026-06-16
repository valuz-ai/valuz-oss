"""Skill materialization links skill source dirs into a project's cwd.

On Windows a normal account lacks the symlink privilege, so materialization
creates directory junctions (``mklink /J``) instead of ``os.symlink``. These
tests pin the two platform branches of ``_create_dir_link`` and confirm
``_remove_managed_entry`` cleans up junctions (``os.rmdir``) as well as
symlinks (``os.unlink``).

CI runs on macOS/Linux, so the Windows branch is exercised by monkeypatching
``sys.platform`` and ``subprocess.run`` — no real Windows FS needed.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

# Side-effect import: puts the kernel ``src/`` on sys.path at module load,
# before any ``from src.*`` below resolves. Mirrors tests/runtimes/test_claude_buffer_size.py.
import kernel  # noqa: F401


def test_create_then_remove_roundtrip_posix(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX symlink round-trip; Windows uses junctions")
    from src.runtimes.skills_materialize import _create_dir_link, _remove_managed_entry

    src = tmp_path / "skill-src"
    src.mkdir()
    (src / "SKILL.md").write_text("hello", encoding="utf-8")
    dst = tmp_path / "skills-root" / "skill-src"
    dst.parent.mkdir()

    _create_dir_link(str(src), str(dst))

    assert os.path.islink(str(dst))
    assert os.path.isdir(str(dst))
    assert (dst / "SKILL.md").read_text(encoding="utf-8") == "hello"

    _remove_managed_entry(str(dst))
    assert not os.path.lexists(str(dst))


def test_create_dir_link_uses_mklink_junction_on_windows(tmp_path):
    from src.runtimes.skills_materialize import _create_dir_link

    src = tmp_path / "skill-src"
    src.mkdir()
    dst = tmp_path / "skills-root" / "skill-src"

    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        captured["kwargs"] = kwargs

        class _Result:
            returncode = 0

        return _Result()

    with (
        patch("src.runtimes.skills_materialize.sys.platform", "win32"),
        patch("src.runtimes.skills_materialize.subprocess.run", side_effect=fake_run),
    ):
        _create_dir_link(str(src), str(dst))

    assert captured["args"][:4] == ["cmd", "/c", "mklink", "/J"]
    # mklink /J takes <Link> <Target> — link first, target second.
    assert captured["args"][4] == str(dst)
    assert captured["args"][5] == os.path.abspath(str(src))
    assert captured["kwargs"].get("check") is True
    assert captured["kwargs"].get("capture_output") is True


def test_remove_managed_entry_handles_junction(tmp_path):
    from src.runtimes.skills_materialize import _remove_managed_entry

    junction_path = str(tmp_path / "a-junction")

    calls = {"rmdir": [], "unlink": []}

    with (
        patch("src.runtimes.skills_materialize.sys.platform", "win32"),
        patch("src.runtimes.skills_materialize.os.path.isjunction", return_value=True),
        patch("src.runtimes.skills_materialize.os.path.islink", return_value=False),
        patch(
            "src.runtimes.skills_materialize.os.rmdir",
            side_effect=lambda p: calls["rmdir"].append(p),
        ),
        patch(
            "src.runtimes.skills_materialize.os.unlink",
            side_effect=lambda p: calls["unlink"].append(p),
        ),
    ):
        _remove_managed_entry(junction_path)

    assert calls["rmdir"] == [junction_path]
    assert calls["unlink"] == []


def test_remove_managed_entry_falls_through_to_symlink(tmp_path):
    from src.runtimes.skills_materialize import _remove_managed_entry

    link_path = str(tmp_path / "a-symlink")

    calls = {"rmdir": [], "unlink": []}

    with (
        patch("src.runtimes.skills_materialize.os.path.isjunction", return_value=False),
        patch("src.runtimes.skills_materialize.os.path.islink", return_value=True),
        patch(
            "src.runtimes.skills_materialize.os.rmdir",
            side_effect=lambda p: calls["rmdir"].append(p),
        ),
        patch(
            "src.runtimes.skills_materialize.os.unlink",
            side_effect=lambda p: calls["unlink"].append(p),
        ),
    ):
        _remove_managed_entry(link_path)

    assert calls["rmdir"] == []
    assert calls["unlink"] == [link_path]
