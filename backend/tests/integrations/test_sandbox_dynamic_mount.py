"""Tests for the no-restart dynamic-mount path (macOS sandbox extensions).

Three layers, mirroring the production seam:

- ``build_seatbelt_profile`` pre-declares the rw extension class (pure).
- ``issue_extension`` produces a valid token on macOS (the host issue side).
- ``sandbox_runtime.ensure_workspace_granted`` is the live decision: no-op
  for static-root / inactive, one ``bind_workspace`` for an external path,
  degrade-on-failure. Exercised with a fake provider.
- End-to-end (macOS): a real sandbox-exec kernel with the control plane
  mounted consumes a host-issued token across the process boundary.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src/app
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from valuz_agent.integrations.sandbox_seatbelt import (
    SeatbeltSandboxProvider,
    build_seatbelt_profile,
)
from valuz_agent.ports.sandbox_provider import (
    MountGrant,
    MountSpec,
    SandboxSpec,
)

darwin_only = pytest.mark.skipif(
    sys.platform != "darwin", reason="sandbox extensions are macOS-only"
)


# ---- profile pre-declaration (pure) -----------------------------------


def test_profile_predeclares_rw_extension_class(tmp_path) -> None:
    """The load-bearing line: without it a consumed token is inert."""
    profile = build_seatbelt_profile(
        SandboxSpec(
            sandbox_id="t",
            kernel_db_path=str(tmp_path / "k.db"),
            mounts=(MountSpec(target=str(tmp_path), source=str(tmp_path), mode="rw"),),
        )
    )
    assert (
        '(allow file-read* file-write* (extension "com.apple.app-sandbox.read-write"))'
        in profile
    )


# ---- host issue side ---------------------------------------------------


@darwin_only
def test_issue_extension_returns_a_valid_token(tmp_path) -> None:
    from valuz_agent.integrations.sandbox_seatbelt import issue_extension

    token = issue_extension(str(tmp_path), "rw")
    # A real token is a long HMAC;...;path string — not the 7-byte "invalid"
    # the SPI returns for the wrong flags.
    assert len(token) > 50
    assert token != "invalid"


def test_issue_extension_off_macos_raises(monkeypatch, tmp_path) -> None:
    from valuz_agent.integrations import sandbox_seatbelt as sb
    from valuz_agent.ports.sandbox_provider import SandboxProvisionError

    # Force the lazy SPI binding to re-evaluate as non-macOS.
    monkeypatch.setattr(sb._IssueSpi, "_loaded", False)
    monkeypatch.setattr(sb._IssueSpi, "available", False)
    monkeypatch.setattr(sb.sys, "platform", "linux")
    with pytest.raises(SandboxProvisionError, match="unavailable"):
        sb.issue_extension(str(tmp_path), "rw")


# ---- the live decision: sandbox_runtime --------------------------------


class _FakeProvider:
    """Records bind/unbind calls; returns a binding with kernel_cwd==path."""

    def __init__(self) -> None:
        self.binds: list[tuple[str, str, str]] = []
        self.unbinds: list[tuple[str, str]] = []
        self.fail = False

    async def bind_workspace(self, sandbox_id, host_path, mode="rw"):
        if self.fail:
            from valuz_agent.ports.sandbox_provider import SandboxProvisionError

            raise SandboxProvisionError("boom")
        self.binds.append((sandbox_id, host_path, mode))
        return MountGrant(
            grant_id="7", kernel_cwd=host_path, host_path=host_path, mode=mode
        )

    async def unbind_workspace(self, sandbox_id, grant_id):
        self.unbinds.append((sandbox_id, grant_id))


@pytest.fixture
def fresh_runtime(monkeypatch):
    """Reset the module-global active-sandbox state around each test."""
    from valuz_agent.integrations import sandbox_runtime as sr

    monkeypatch.setattr(sr, "_state", None)
    monkeypatch.setattr(sr, "_lazy_tried", False)
    # No env → lazy activation stays off unless a test opts in.
    monkeypatch.delenv("VALUZ_SANDBOX_DRIVER", raising=False)
    monkeypatch.delenv("VALUZ_KERNEL_URL", raising=False)
    return sr


@pytest.mark.asyncio
async def test_ensure_granted_is_noop_when_inactive(fresh_runtime, tmp_path) -> None:
    sr = fresh_runtime
    assert sr.is_active() is False
    assert await sr.ensure_workspace_granted(str(tmp_path)) == str(tmp_path)


@pytest.mark.asyncio
async def test_ensure_granted_skips_paths_under_static_roots(fresh_runtime, tmp_path) -> None:
    sr = fresh_runtime
    root = tmp_path / "Valuz"
    (root / "proj").mkdir(parents=True)
    fake = _FakeProvider()
    sr.activate(fake, "host-kernel", (str(root),))

    cwd = str(root / "proj")
    assert await sr.ensure_workspace_granted(cwd) == cwd
    assert fake.binds == []  # under a static mount → no extension issued


@pytest.mark.asyncio
async def test_ensure_granted_binds_external_path_once(fresh_runtime, tmp_path) -> None:
    sr = fresh_runtime
    root = tmp_path / "Valuz"
    root.mkdir()
    external = tmp_path / "elsewhere" / "repo"
    external.mkdir(parents=True)
    fake = _FakeProvider()
    sr.activate(fake, "host-kernel", (str(root),))

    cwd = str(external)
    real = str(Path(external).resolve())
    assert await sr.ensure_workspace_granted(cwd) == cwd
    # Idempotent: a second call reuses the cached binding, no second issue.
    assert await sr.ensure_workspace_granted(cwd) == cwd
    assert fake.binds == [("host-kernel", real, "rw")]


@pytest.mark.asyncio
async def test_ensure_granted_degrades_on_bind_failure(fresh_runtime, tmp_path) -> None:
    sr = fresh_runtime
    external = tmp_path / "elsewhere"
    external.mkdir()
    fake = _FakeProvider()
    fake.fail = True
    sr.activate(fake, "host-kernel", ())

    # A grant failure must not block session creation — returns original cwd.
    assert await sr.ensure_workspace_granted(str(external)) == str(external)


# ---- end-to-end across the real sandbox boundary (macOS) ---------------


@darwin_only
@pytest.mark.asyncio
async def test_running_sandbox_consumes_host_issued_token(tmp_path) -> None:
    """The crown-jewel: a live sandbox-exec kernel, with the control plane
    mounted, consumes a token the host issued for a path OUTSIDE its static
    mounts — proving no-restart dynamic grant across the process boundary.
    """
    external = tmp_path / "external_project"
    external.mkdir()

    spec = SandboxSpec(
        sandbox_id="dyn",
        kernel_db_path=str(tmp_path / "kernel.db"),
        # external_project is deliberately NOT mounted.
        mounts=(MountSpec(target=str(tmp_path / "in"), source=str(tmp_path / "in"), mode="rw"),),
        deny_paths=(),
    )
    (tmp_path / "in").mkdir()
    provider = SeatbeltSandboxProvider()
    await provider.provision(spec)
    try:
        grant = await provider.bind_workspace("dyn", str(external), "rw")
        # The kernel consumed the token and returned a live handle.
        assert grant.grant_id.lstrip("-").isdigit()
        assert int(grant.grant_id) >= 0
        assert grant.kernel_cwd == str(external)  # unchanged locally
        # Revoke is accepted by the running kernel.
        await provider.unbind_workspace("dyn", grant.grant_id)
    finally:
        await provider.destroy("dyn")


@darwin_only
@pytest.mark.asyncio
async def test_control_plane_absent_without_env(tmp_path) -> None:
    """A sandbox provisioned by the provider always mounts the control plane
    (KERNEL_SANDBOX_CONTROL=1 in _spawn); a bare standalone kernel would
    404. Here we assert the provider's sandbox DOES expose grant."""
    import httpx

    spec = SandboxSpec(
        sandbox_id="cp",
        kernel_db_path=str(tmp_path / "kernel.db"),
        mounts=(MountSpec(target=str(tmp_path), source=str(tmp_path), mode="rw"),),
    )
    provider = SeatbeltSandboxProvider()
    endpoint = await provider.provision(spec)
    try:
        async with httpx.AsyncClient() as c:
            # A malformed grant (empty token) reaches the handler → 400, not
            # 404: the route is mounted.
            r = await c.post(
                f"{endpoint.base_url}/internal/sandbox/grant",
                headers={"Authorization": f"Bearer {endpoint.token}"},
                json={"token": "not-a-real-token"},
                timeout=5,
            )
            assert r.status_code == 400  # reached the consume, token rejected
    finally:
        await provider.destroy("cp")
