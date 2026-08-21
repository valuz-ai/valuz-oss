"""``create_app(api_prefix=...)`` route-mounting behaviour.

Inspects the route table built by ``create_app()`` (no DB needed): the whole
public HTTP surface — host routers, overlay ``module_registry`` routes, and the
in-process kernel routers — plus the internal ``/_internal/data`` +
``/_internal/mcp/*`` ASGI sub-apps are mounted under each configured base path, so
a kernel reaching the host through the prefixed ingress (a cloud sandbox) can
resolve ``{backend_base_url}/_internal/*`` too. ADR-013 renamed these from
``/internal/*`` with NO legacy mount — stale session snapshots are self-healed
by the always-on MCP re-stamp (``refresh_always_on_mcp_for_session``).
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from starlette.routing import Mount

from valuz_agent.api.app import create_app
from valuz_agent.infra.config import Settings, settings

# A representative, parameter-free host route — the one that 404'd behind the
# shared-host ingress when the seam was missing.
_HOST_PATH = "/v1/notifications"
# A representative in-process kernel route (ADR-013: the kernel package's own
# default prefix is /kernel — see kernel/app/routes/__init__.py).
_KERNEL_PATH = "/kernel/v1/sessions"
# A representative internal ASGI mount — now mounted under each base path too.
_MCP_MOUNT = "/_internal/mcp/docs"
# The pre-ADR-013 spelling — must NOT be mounted (clean cut; re-stamp heals
# stale session snapshots).
_LEGACY_MCP_MOUNT = "/internal/mcp/docs"


def _api_paths(app) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, APIRoute)}


def _mount_paths(app) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, Mount)}


def test_default_is_a_noop() -> None:
    """No argument (and empty settings) → routes served at native paths."""
    paths = _api_paths(create_app())
    assert _HOST_PATH in paths
    assert _KERNEL_PATH in paths
    mounts = _mount_paths(create_app())
    assert _MCP_MOUNT in mounts
    # ADR-013 clean cut: the pre-rename spelling is gone.
    assert _LEGACY_MCP_MOUNT not in mounts


def test_single_prefix_shifts_the_whole_surface() -> None:
    """One prefix moves host + kernel routes; the bare paths stop being served."""
    paths = _api_paths(create_app(api_prefix=["/valuz-backend"]))

    assert "/valuz-backend" + _HOST_PATH in paths
    assert "/valuz-backend" + _KERNEL_PATH in paths
    assert _HOST_PATH not in paths
    assert _KERNEL_PATH not in paths


def test_internal_mounts_follow_each_base_path() -> None:
    """Internal sub-apps (``/_internal/data`` + ``/_internal/mcp/*``) mount under
    EACH configured base path — so a kernel whose ``backend_base_url`` carries the
    ingress sub-path (a cloud sandbox reachable only through it) resolves them
    too. Updated from the old root-only contract."""
    # prefix-only → the internal mounts live under that prefix.
    mounts = _mount_paths(create_app(api_prefix=["/valuz-backend"]))
    assert "/valuz-backend" + _MCP_MOUNT in mounts
    assert "/valuz-backend/_internal/data" in mounts
    # ADR-013 clean cut: no legacy spelling under any base path.
    assert "/valuz-backend" + _LEGACY_MCP_MOUNT not in mounts
    assert "/valuz-backend/internal/data" not in mounts

    # native + prefixed → served at BOTH, so internal ``backend_base_url`` callers
    # keep resolving the root mounts while the prefixed ingress exposes them too.
    both = _mount_paths(create_app(api_prefix=["", "/valuz-backend"]))
    assert _MCP_MOUNT in both and "/valuz-backend" + _MCP_MOUNT in both
    assert "/_internal/data" in both and "/valuz-backend/_internal/data" in both
    assert _LEGACY_MCP_MOUNT not in both and "/internal/data" not in both


def test_no_legacy_internal_mount_survives() -> None:
    """ADR-013 clean cut: no ``/internal/...`` spelling is mounted anywhere —
    stale session snapshots are healed by the always-on MCP re-stamp, not by
    keeping the old path alive."""
    app = create_app()
    mounts = _mount_paths(app)
    assert not any("/internal/" in m and "/_internal/" not in m for m in mounts)


def test_dual_mount_serves_native_and_prefixed() -> None:
    """``["", "/valuz-backend"]`` → the surface is served at BOTH paths.

    This is the shared-backend deploy shape (env ``VALUZ_API_PREFIX=,/valuz-backend``):
    native paths keep internal/probe callers working while the ingress sees the
    prefixed surface.
    """
    paths = _api_paths(create_app(api_prefix=["", "/valuz-backend"]))

    assert _HOST_PATH in paths
    assert "/valuz-backend" + _HOST_PATH in paths
    assert "/valuz-backend" + _KERNEL_PATH in paths


def test_none_falls_back_to_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """``api_prefix=None`` (default) → uses ``settings.api_prefix``."""
    monkeypatch.setattr(settings, "api_prefix", ["/valuz-backend"])
    paths = _api_paths(create_app())

    assert "/valuz-backend" + _HOST_PATH in paths
    assert _HOST_PATH not in paths


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", []),
        ("/valuz-backend", ["/valuz-backend"]),
        ("valuz-backend", ["/valuz-backend"]),
        ("/valuz-backend/", ["/valuz-backend"]),
        ("/a,/b", ["/a", "/b"]),
        (" /a , b/ ", ["/a", "/b"]),
        (",/valuz-backend", ["", "/valuz-backend"]),  # leading comma → native + prefix
        (["/a", "/a", "/b"], ["/a", "/b"]),  # dedup, order preserved
        (["", "/gw"], ["", "/gw"]),
    ],
)
def test_prefix_is_parsed_and_normalized(raw: object, expected: list[str]) -> None:
    """Accepts a comma-separated string or a list; normalises + dedups entries."""
    assert Settings(api_prefix=raw).api_prefix == expected


def test_edition_always_on_mcp_mounts_under_each_base_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spec that carries its app is mounted through the built-ins' own seam.

    The resolver advertises an edition server as ``{backend_base_url}{path}/mcp``
    — the same shape as the built-ins — so it needs the same mounting. An
    edition that mounted by hand in ``register_api`` and covered only the bare
    path shipped a spec whose advertised URL 404'd under every prefixed
    deployment, which is the whole reason the factory lives on the spec.
    """
    from valuz_agent.ports.extensions import ext
    from valuz_agent.ports.mcp_always_on import AlwaysOnMcpServerSpec

    async def _app(scope, receive, send) -> None:  # pragma: no cover - never called
        raise AssertionError("route table inspection only")

    monkeypatch.setattr(
        ext,
        "always_on_mcp_specs",
        [
            AlwaysOnMcpServerSpec(
                name="valuz_finance",
                path="/_internal/mcp/finance",
                app_factory=lambda: _app,
            )
        ],
    )

    mounts = _mount_paths(create_app(api_prefix=["", "/valuz-backend"]))

    assert "/_internal/mcp/finance" in mounts
    assert "/valuz-backend/_internal/mcp/finance" in mounts


def test_edition_spec_without_a_factory_mounts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Back-compat: an edition still mounting its own app in ``register_api``
    keeps that arrangement — the spec alone must not conjure a mount."""
    from valuz_agent.ports.extensions import ext
    from valuz_agent.ports.mcp_always_on import AlwaysOnMcpServerSpec

    monkeypatch.setattr(
        ext,
        "always_on_mcp_specs",
        [AlwaysOnMcpServerSpec(name="valuz_finance", path="/_internal/mcp/finance")],
    )

    assert "/_internal/mcp/finance" not in _mount_paths(create_app())
