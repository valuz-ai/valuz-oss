"""Bare-OSS composition collector (backend pluginization safety net).

Builds the real bare OSS app in a **fresh interpreter** and prints a JSON
description of what the composition contributes. The refactor that moves OSS
features onto the plugin host must leave every section identical (modulo the
documented, deliberate splits in ``test_composition_goldens.py``).

Sections (``--kind``):

``routes``    every route / mount of ``app.routes`` **in order**: ``METHODS path name``
              for routes, ``MOUNT path <app type>`` for ASGI mounts
``boot``      the startup and shutdown step sequence: every public function of
              ``valuz_agent.boot.steps`` (plus the parent watchdog and the draining
              flag) is replaced by a recorder, then the app lifespan is run
``mcp``       ``ext.always_on_mcp_specs`` after ``create_app`` + the always-on servers
              a session is handed (names / urls / header names) + the order the
              in-process MCP session managers are entered and left in

Why a subprocess: composing mutates process-wide singletons (``ext``, the
registries, settings) and the suite's conftest stubs; only a clean interpreter is
an honest reference. Run directly::

    python tests/plugin_host/snapshot_collector.py --kind routes [--prefix ,/p]
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]

KINDS = ("routes", "boot", "mcp", "disabled")


def scenario_env(home: str) -> dict[str, str]:
    """Environment built from scratch: no ambient ``VALUZ_*``."""
    return {
        "HOME": home,
        "VALUZ_DATA_DIR": str(Path(home) / "data"),
        "VALUZ_LOG_FILE_PATH": str(Path(home) / "logs" / "backend.log"),
        "VALUZ_DEPLOYMENT_TYPE": "local",
        "KERNEL_STORE": "local",
        "VALUZ_PARSE_POOL_DISABLED": "1",
        # Deterministic: the dsh bridge is advertised/mounted only when the manager
        # is enabled, which otherwise depends on the host platform.
        "VALUZ_DSH_MANAGER_ENABLED": "1",
    }


def _route_line(route: Any) -> str:
    if type(route).__name__ == "Mount":
        return f"MOUNT {route.path} {type(route.app).__name__}"
    methods = ",".join(sorted(getattr(route, "methods", None) or ["WS"]))
    return f"{methods} {route.path} {getattr(route, 'name', '')}"


def _build_app(prefix: str | None) -> Any:
    import dotenv

    dotenv.load_dotenv = lambda *a, **k: False  # type: ignore[assignment]
    from valuz_agent.api.app import create_app

    api_prefix = None if prefix is None else prefix.split(",")
    return create_app(api_prefix=api_prefix)


def collect_routes(prefix: str | None) -> dict[str, Any]:
    app = _build_app(prefix)
    return {"routes": [_route_line(r) for r in app.routes]}


# -- boot sequence -------------------------------------------------------------


def _install_recorders(calls: list[str]) -> None:
    """Replace every public step function with a recorder (same sync / async shape)."""
    from valuz_agent.boot import parent_watchdog, steps
    from valuz_agent.infra import lifecycle

    def recorder(label: str, original: Any) -> Any:
        def _label(args: tuple[Any, ...]) -> str:
            return f"{label}({'app' if args else ''})"

        if inspect.iscoroutinefunction(original):

            async def _async(*args: Any, **kwargs: Any) -> None:
                calls.append(_label(args))

            return _async

        def _sync(*args: Any, **kwargs: Any) -> None:
            calls.append(_label(args))

        return _sync

    for name, fn in list(vars(steps).items()):
        if inspect.isfunction(fn) and fn.__module__ == steps.__name__ and not name.startswith("_"):
            setattr(steps, name, recorder(name, fn))
    for module, name in (
        (parent_watchdog, "start_parent_watchdog"),
        (parent_watchdog, "stop_parent_watchdog"),
        (lifecycle, "set_draining"),
    ):
        setattr(
            module,
            name,
            recorder(f"{module.__name__.rsplit('.', 1)[-1]}.{name}", getattr(module, name)),
        )


def collect_boot(prefix: str | None) -> dict[str, Any]:
    calls: list[str] = []
    _install_recorders(calls)
    app = _build_app(prefix)
    snapshot: dict[str, Any] = {}

    async def run() -> None:
        async with app.router.lifespan_context(app):
            snapshot["startup"] = list(calls)
            calls.clear()
        snapshot["shutdown"] = list(calls)

    asyncio.run(run())
    return snapshot


# -- MCP -------------------------------------------------------------------------


def _install_manager_recorders(events: list[str]) -> None:
    """Make every in-process MCP session manager a recorder (enter / exit)."""
    import importlib

    __import__("kernel")  # sys.path side effect: makes ``app`` / ``src`` importable

    def recorder(label: str) -> Any:
        @contextlib.asynccontextmanager
        async def _run() -> Any:
            events.append(f"enter {label}")
            try:
                yield
            finally:
                events.append(f"exit {label}")

        return _run

    targets = (
        ("valuz_agent.integrations.docs_mcp_server", "docs_mcp_session_manager_run", "docs"),
        (
            "valuz_agent.integrations.automations_mcp_server",
            "automations_mcp_session_manager_run",
            "automations",
        ),
        (
            "valuz_agent.integrations.playbooks_mcp_server",
            "playbooks_mcp_session_manager_run",
            "playbooks",
        ),
        (
            "valuz_agent.integrations.connectors_mcp_server",
            "connectors_mcp_session_manager_run",
            "connectors",
        ),
        (
            "valuz_agent.integrations.toolkit_mcp_server",
            "toolkit_mcp_session_managers_run",
            "toolkit",
        ),
        (
            "valuz_agent.modules.dsh_plugins.mcp_server",
            "dsh_plugins_mcp_session_manager_run",
            "dsh-plugins",
        ),
        ("app.mcp_toolkit_router", "mcp_router_lifespan", "kernel-mcp-router"),
    )
    for module_name, attr, label in targets:
        module = importlib.import_module(module_name)
        setattr(module, attr, recorder(label))


def collect_mcp(prefix: str | None) -> dict[str, Any]:
    events: list[str] = []
    _install_manager_recorders(events)
    app = _build_app(prefix)

    from valuz_agent.ports.extensions import ext

    specs = [
        {"name": s.name, "path": s.path, "has_factory": s.app_factory is not None}
        for s in ext.always_on_mcp_specs
    ]

    async def run() -> list[dict[str, Any]]:
        from valuz_agent.adapters.capability_resolver import always_on_http_mcp_servers
        from valuz_agent.boot import steps
        from valuz_agent.infra.local_identity import resolve_local_user_id

        owner = resolve_local_user_id()
        servers = await always_on_http_mcp_servers("sess-1", owner_user_id=owner)
        advertised = [{"name": s.name, "url": s.url, "headers": sorted(s.headers)} for s in servers]
        await steps.start_mcp_session_managers(app)
        events.append("started")
        await steps.stop_mcp_session_managers(app)
        return advertised

    advertised = asyncio.run(run())
    return {"specs": specs, "advertised": advertised, "managers": events}


def _write_prefs(disabled: list[str]) -> None:
    from valuz_agent.plugin_host import save_enabled

    for plugin_id in disabled:
        save_enabled(plugin_id, False)


def collect_disabled(prefix: str | None, disabled: list[str]) -> dict[str, Any]:
    """One composition with ``disabled`` switched off in the real prefs file; the
    MCP session managers are driven for real, then the lifespan runs on recorders."""
    _write_prefs(disabled)
    events: list[str] = []
    _install_manager_recorders(events)
    app = _build_app(prefix)

    from valuz_agent.features.order import TOOL_SLOTS
    from valuz_agent.plugin_host import active_plugin_host
    from valuz_agent.ports.extensions import ext

    host = active_plugin_host()
    assert host is not None
    out: dict[str, Any] = {
        "routes": [_route_line(r) for r in app.routes],
        "specs": [s.name for s in ext.always_on_mcp_specs],
        "tools": [e.tool.slot for e in host.registry.tool_groups(TOOL_SLOTS)],
        "inactive": host.inactive_ids(),
        "status": {i.id: i.status for i in host.list()},
        "owners": host.registry.owners(),
    }

    async def run() -> None:
        from valuz_agent.adapters.capability_resolver import always_on_http_mcp_servers
        from valuz_agent.boot import steps
        from valuz_agent.infra.local_identity import resolve_local_user_id

        servers = await always_on_http_mcp_servers("sess-1", owner_user_id=resolve_local_user_id())
        out["advertised"] = [s.name for s in servers]
        await steps.start_mcp_session_managers(app, _managers_of(host))
        events.append("started")
        await steps.stop_mcp_session_managers(app)

    asyncio.run(run())
    out["managers"] = events

    calls: list[str] = []
    _install_recorders(calls)

    async def lifespan() -> None:
        async with app.router.lifespan_context(app):
            out["startup"] = list(calls)
            calls.clear()
        out["shutdown"] = list(calls)

    asyncio.run(lifespan())
    return out


def _managers_of(host: Any) -> list[Any]:
    from valuz_agent.features.order import MOUNT_SLOTS

    return [
        e.mount.session_manager
        for e in host.registry.mounts(MOUNT_SLOTS)
        if e.mount.session_manager is not None
    ]


def collect(kind: str, prefix: str | None, disabled: list[str] | None = None) -> dict[str, Any]:
    if kind == "disabled":
        return collect_disabled(prefix, disabled or [])
    if kind == "routes":
        return collect_routes(prefix)
    if kind == "boot":
        return collect_boot(prefix)
    if kind == "mcp":
        return collect_mcp(prefix)
    raise ValueError(kind)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=KINDS, required=True)
    parser.add_argument(
        "--prefix", default=None, help="comma-separated api_prefix list (default: none)"
    )
    parser.add_argument("--disable", default="", help="comma-separated plugin ids (kind=disabled)")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(BACKEND_ROOT))
    with tempfile.TemporaryDirectory(prefix="valuz-oss-golden-") as home:
        for key in [k for k in os.environ if k.startswith("VALUZ_") or k == "KERNEL_STORE"]:
            del os.environ[key]
        os.environ.update(scenario_env(home))
        os.chdir(home)  # never pick up backend/.env through the cwd
        snapshot = collect(args.kind, args.prefix, [d for d in args.disable.split(",") if d])
    sys.stdout.write("\n<<SNAPSHOT>>\n" + json.dumps(snapshot, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
