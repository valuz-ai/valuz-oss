"""Boot mode-awareness (B2–B5): http mode must skip the in-process kernel
bootstrap so a separate-process kernel isn't shadowed by a ghost.

These pin the guards added for the minimal sandbox form
(``docs/design/kernel-sandbox-deployment.md`` §B.6): in http mode the
host neither migrates the kernel DB, nor creates store/orchestrator
singletons, nor mounts the kernel routers, nor runs the orphan scans —
the standalone kernel owns all of that.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede app.*
from __future__ import annotations

import pytest

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect

from valuz_agent.boot import steps
from valuz_agent.infra.config import settings


def test_is_http_kernel_property(monkeypatch) -> None:
    monkeypatch.setattr(settings, "kernel_mode", "inprocess")
    assert settings.is_http_kernel is False
    monkeypatch.setattr(settings, "kernel_mode", "http")
    assert settings.is_http_kernel is True


@pytest.mark.asyncio
async def test_orphan_scans_skipped_in_http_mode(monkeypatch) -> None:
    """The orphan scans go through the in-process store; in http mode the
    HttpKernelClient has no scan_orphan_* methods, so the steps must
    short-circuit before touching the client (else AttributeError)."""
    monkeypatch.setattr(settings, "kernel_mode", "http")

    called = {"recover": False, "seal": False}

    def _boom(*a, **k):
        raise AssertionError("kernel access must not happen in http mode")

    import valuz_agent.adapters.kernel_client as kc

    monkeypatch.setattr(kc, "scan_orphan_pendings", _boom)
    monkeypatch.setattr(
        "valuz_agent.modules.sessions.recovery.recover_running_sessions",
        lambda *a, **k: called.__setitem__("recover", True),
    )

    # Both return early — no kernel access, no exception.
    await steps.recover_stranded_sessions()
    await steps.seal_orphan_pendings()
    assert called["recover"] is False


def test_kernel_routers_not_mounted_in_http_mode(monkeypatch) -> None:
    """The host app must NOT mount /api/v1/* in http mode — the standalone
    kernel serves it; mounting a ghost would bind the host's own DB."""
    monkeypatch.setattr(settings, "kernel_mode", "http")
    from valuz_agent.api.app import create_app

    app = create_app()
    kernel_paths = [r.path for r in app.routes if r.path.startswith("/api/v1/")]
    assert kernel_paths == []


def test_kernel_routers_mounted_in_inprocess_mode(monkeypatch) -> None:
    monkeypatch.setattr(settings, "kernel_mode", "inprocess")
    from valuz_agent.api.app import create_app

    app = create_app()
    kernel_paths = [r.path for r in app.routes if r.path.startswith("/api/v1/")]
    assert any("/sessions" in p for p in kernel_paths)
