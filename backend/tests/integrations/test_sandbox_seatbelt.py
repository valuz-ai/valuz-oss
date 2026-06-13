"""Tests for the minimal local Seatbelt sandbox driver.

Two layers:

- ``build_seatbelt_profile`` is pure — unit-tested for the security-load-
  bearing rules (RED-LINE denies after the broad read-allow, write
  allowlist, CLI login read, loopback bind/network).
- ``SeatbeltSandboxProvider`` is exercised end-to-end (macOS only): a real
  ``sandbox-exec``-confined kernel is provisioned on its own migrated DB,
  driven over HTTP, and the RED LINE is verified by trying to ``cat`` a
  denied path through the same profile.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from valuz_agent.integrations.sandbox_seatbelt import (
    SeatbeltSandboxProvider,
    build_seatbelt_profile,
)
from valuz_agent.ports.sandbox_provider import (
    MountSpec,
    SandboxProvisionError,
    SandboxSpec,
)


def _spec(tmp: Path, *, deny=(), mounts=()) -> SandboxSpec:
    return SandboxSpec(
        sandbox_id="t",
        kernel_db_path=str(tmp / "kernel.db"),
        mounts=mounts or (MountSpec(target=str(tmp), source=str(tmp), mode="rw"),),
        deny_paths=tuple(deny),
        host_callback_url="http://127.0.0.1:8000",
    )


# ---- pure profile generation ------------------------------------------


def test_profile_denies_come_after_read_allow(tmp_path) -> None:
    """RED-LINE: deny rules must follow the broad ``allow file-read*`` so
    they win (Seatbelt is last-match-wins within an operation)."""
    secret = tmp_path / "secrets"
    profile = build_seatbelt_profile(_spec(tmp_path, deny=[str(secret)]))
    lines = profile.splitlines()
    read_allow_idx = next(i for i, x in enumerate(lines) if x == "(allow file-read*)")
    deny_idx = next(i for i, x in enumerate(lines) if "deny file-read*" in x and "secrets" in x)
    assert deny_idx > read_allow_idx


def test_profile_canonicalises_symlinked_paths(tmp_path) -> None:
    """macOS /var → /private/var: rules must use the real path or the
    sandbox silently fails to match (the 'unable to open database file'
    class of bug)."""
    # tmp_path under pytest is already a realpath, so assert the rule
    # contains the resolved form of a known symlink instead.
    profile = build_seatbelt_profile(
        _spec(tmp_path, mounts=(MountSpec(target="/tmp/x", source="/tmp/x", mode="rw"),))
    )
    import os

    assert os.path.realpath("/tmp") in profile  # /private/tmp on macOS


def test_profile_keeps_cli_login_state_readable(tmp_path) -> None:
    profile = build_seatbelt_profile(_spec(tmp_path))
    assert ".claude" in profile
    assert ".codex" in profile


def test_profile_allows_loopback_bind_and_network(tmp_path) -> None:
    profile = build_seatbelt_profile(_spec(tmp_path))
    assert "network-bind" in profile
    assert "network-inbound" in profile
    assert "network-outbound" in profile


def test_profile_write_allowlist_covers_rw_mounts_not_ro(tmp_path) -> None:
    rw = tmp_path / "rw"
    ro = tmp_path / "ro"
    profile = build_seatbelt_profile(
        _spec(
            tmp_path,
            mounts=(
                MountSpec(target=str(rw), source=str(rw), mode="rw"),
                MountSpec(target=str(ro), source=str(ro), mode="ro"),
            ),
        )
    )
    import os

    assert f'(allow file-write* (subpath "{os.path.realpath(str(rw))}"))' in profile
    assert f'file-write* (subpath "{os.path.realpath(str(ro))}")' not in profile


# ---- Seatbelt enforcement (macOS) -------------------------------------

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is macOS-only")


@darwin_only
def test_seatbelt_enforces_red_line(tmp_path) -> None:
    """The generated profile actually denies a secret path while keeping
    a project file readable — proven by running ``cat`` under it."""
    secret_dir = tmp_path / "host-secrets"
    secret_dir.mkdir()
    (secret_dir / "api_key").write_text("SUPER_SECRET")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "ok.txt").write_text("readable")

    profile = build_seatbelt_profile(
        SandboxSpec(
            sandbox_id="rl",
            kernel_db_path=str(proj / "k.db"),
            mounts=(MountSpec(target=str(proj), source=str(proj), mode="rw"),),
            deny_paths=(str(secret_dir),),
        )
    )

    def _cat(path: str) -> int:
        return subprocess.run(
            ["sandbox-exec", "-p", profile, "/bin/cat", path],
            capture_output=True,
        ).returncode

    assert _cat(str(secret_dir / "api_key")) != 0  # RED LINE: denied
    assert _cat(str(proj / "ok.txt")) == 0  # project file: readable


@darwin_only
@pytest.mark.asyncio
async def test_provision_runs_a_sandboxed_kernel_end_to_end(tmp_path) -> None:
    """A real sandbox-exec-confined kernel: provision → migrated DB →
    HTTP-reachable → auth-gated → destroyable."""
    import httpx

    proj = tmp_path / "proj"
    proj.mkdir()
    spec = SandboxSpec(
        sandbox_id="e2e",
        kernel_db_path=str(tmp_path / "kernel.db"),
        mounts=(MountSpec(target=str(tmp_path), source=str(tmp_path), mode="rw"),),
        deny_paths=(),
    )
    provider = SeatbeltSandboxProvider()
    endpoint = await provider.provision(spec)
    try:
        assert await provider.health("e2e") is True
        async with httpx.AsyncClient() as c:
            ok = await c.get(
                f"{endpoint.base_url}/api/v1/sessions",
                headers={
                    "Authorization": f"Bearer {endpoint.token}",
                    "X-Valuz-Owner-Id": "owner-a",
                },
                timeout=5,
            )
            assert ok.status_code == 200  # migrated DB readable in-sandbox
            unauth = await c.get(f"{endpoint.base_url}/api/v1/sessions", timeout=5)
            assert unauth.status_code == 401
    finally:
        await provider.destroy("e2e")
    assert await provider.health("e2e") is False


@pytest.mark.asyncio
async def test_provision_rejects_non_macos(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("valuz_agent.integrations.sandbox_seatbelt.sys.platform", "linux")
    provider = SeatbeltSandboxProvider()
    with pytest.raises(SandboxProvisionError, match="macOS"):
        await provider.provision(_spec(tmp_path))


# ---- preflight ---------------------------------------------------------


def test_preflight_clean_on_this_host() -> None:
    """On the CI/dev macOS host the three hard requirements hold."""
    from valuz_agent.integrations.sandbox_seatbelt import seatbelt_preflight

    if sys.platform == "darwin":
        assert seatbelt_preflight() == []


def test_preflight_flags_non_macos(monkeypatch) -> None:
    from valuz_agent.integrations import sandbox_seatbelt as sb

    monkeypatch.setattr(sb.sys, "platform", "linux")
    problems = sb.seatbelt_preflight()
    assert any("not macOS" in p for p in problems)


def test_preflight_flags_missing_sandbox_exec(monkeypatch) -> None:
    from valuz_agent.integrations import sandbox_seatbelt as sb

    monkeypatch.setattr(sb.sys, "platform", "darwin")
    monkeypatch.setattr(sb.shutil, "which", lambda _name: None)
    problems = sb.seatbelt_preflight()
    assert any("sandbox-exec not found" in p for p in problems)


@pytest.mark.asyncio
async def test_provision_raises_on_preflight_failure(monkeypatch, tmp_path) -> None:
    from valuz_agent.integrations import sandbox_seatbelt as sb

    monkeypatch.setattr(sb, "seatbelt_preflight", lambda: ["not macOS (test)"])
    provider = sb.SeatbeltSandboxProvider()
    with pytest.raises(SandboxProvisionError, match="preflight failed"):
        await provider.provision(_spec(tmp_path))


# ---- skill dependency mounts (the "Operation not permitted" bug) -------


def test_host_rw_mounts_cover_project_and_skill_roots(monkeypatch, tmp_path) -> None:
    """The writable manifest must include the user project root and every
    skill root — skill materialization writes symlinks into a project's
    .agents/.claude/skills, and skill creation writes to the skill roots."""
    from valuz_agent.infra.config import settings
    from valuz_agent.integrations.sandbox_seatbelt import host_sandbox_rw_mounts

    monkeypatch.setattr(settings, "data_dir", tmp_path / "app")
    monkeypatch.setattr(settings, "user_project_root", tmp_path / "Valuz")

    sources = {m.source for m in host_sandbox_rw_mounts()}
    assert all(m.mode == "rw" for m in host_sandbox_rw_mounts())
    # The user project root (where real projects + their .agents/skills live).
    assert str(tmp_path / "Valuz") in sources
    # The managed chat-cwd root.
    assert str(tmp_path / "app" / "projects") in sources
    # The kernel's private DB dir.
    assert str(tmp_path / "app" / "sandbox") in sources


@darwin_only
def test_skill_materialization_path_is_writable_under_profile(tmp_path, monkeypatch) -> None:
    """Live proof of the fix: a process under the profile can create the
    exact path that failed — <project>/.agents/skills/<name> — while the
    host business DB stays denied."""
    from valuz_agent.infra.config import settings
    from valuz_agent.integrations.sandbox_seatbelt import (
        build_seatbelt_profile,
        host_sandbox_rw_mounts,
    )

    monkeypatch.setattr(settings, "data_dir", tmp_path / "app")
    monkeypatch.setattr(settings, "user_project_root", tmp_path / "Valuz")
    host_db = tmp_path / "app" / "valuz.db"
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    host_db.write_text("SECRET")

    profile = build_seatbelt_profile(
        SandboxSpec(
            sandbox_id="h",
            kernel_db_path=str(tmp_path / "app" / "sandbox" / "kernel.db"),
            mounts=host_sandbox_rw_mounts(),
            deny_paths=(str(host_db),),
        )
    )
    skill_dir = tmp_path / "Valuz" / "proj" / ".agents" / "skills" / "valuz-project-docs"
    make = subprocess.run(
        ["sandbox-exec", "-p", profile, "/bin/sh", "-c", f"mkdir -p {skill_dir}"],
        capture_output=True,
    )
    assert make.returncode == 0  # the reported bug is fixed
    deny = subprocess.run(
        ["sandbox-exec", "-p", profile, "/bin/cat", str(host_db)], capture_output=True
    )
    assert deny.returncode != 0  # RED LINE still holds
