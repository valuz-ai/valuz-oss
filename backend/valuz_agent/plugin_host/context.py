"""``PluginContext``: the only handle a plugin gets on the host.

Every helper registers its undo on the plugin's ``ExitStack`` *and* returns it,
so callers may dispose early. Registries and the ``ext`` container are looked up
at call time (not import time) so a test that swaps them is honoured.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any

from valuz_agent.plugin_host.errors import UnknownPortError
from valuz_agent.plugin_host.hooks_api import CommandsApi, HooksApi
from valuz_agent.plugin_host.registry import (
    BootStep,
    HostRegistry,
    InternalMount,
    Ref,
    ToolGroup,
)
from valuz_agent.plugin_host.ui_api import UiApi

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("valuz_agent.plugin_host")

Disposer = Callable[[], None]
AfterApp = Callable[["AppApi"], None]


class PortsApi:
    """Bind / restore the replaceable ports on the ``ext`` container."""

    def __init__(self, ctx: PluginContext, extensions: Any) -> None:
        self._ctx = ctx
        self._ext = extensions

    def names(self) -> list[str]:
        return sorted(n for n in vars(self._ext) if not n.startswith("_"))

    def has(self, name: str) -> bool:
        return hasattr(self._ext, name)

    def get(self, name: str) -> Any:
        return getattr(self._ext, name)

    def bind(self, name: str, impl: Any) -> Disposer:
        """Replace port ``name``; dispose restores the previous binding.

        Unknown ports raise ``UnknownPortError`` rather than silently creating
        an attribute nothing reads (a typo guard). Guard with :meth:`has` where
        an older OSS pin may lack the port. If a later plugin has since rebound
        the port, dispose leaves that binding alone.
        """
        if not self.has(name):
            raise UnknownPortError(name)
        previous = getattr(self._ext, name)
        setattr(self._ext, name, impl)
        self._ctx._bound_ports.append(name)

        def _restore() -> None:
            if getattr(self._ext, name, None) is impl:
                setattr(self._ext, name, previous)

        return self._ctx._on_stack(_restore)

    def append(self, name: str, item: Any, *, unique: bool = False) -> Disposer:
        """Append to a list-valued port (``always_on_mcp_specs``, ...).

        ``unique=True`` skips an item already present (equality) and then has
        nothing to undo. Dispose removes this exact item (identity).
        """
        if not self.has(name):
            raise UnknownPortError(name)
        target: list[Any] = getattr(self._ext, name)
        if unique and item in target:
            return self._ctx._on_stack(lambda: None)
        target.append(item)

        def _remove() -> None:
            for index in range(len(target) - 1, -1, -1):
                if target[index] is item:
                    del target[index]
                    return

        return self._ctx._on_stack(_remove)

    def restore_on_dispose(self, *names: str) -> Disposer:
        """Snapshot ports that a *legacy helper* is about to mutate directly.

        Some wiring functions assign onto ``ext`` themselves. Call this first and
        the previous values (list contents are copied) come back on dispose.
        """
        saved: dict[str, Any] = {}
        for name in names:
            if not self.has(name):
                continue
            value = getattr(self._ext, name)
            saved[name] = list(value) if isinstance(value, list) else value

        def _restore() -> None:
            for name, value in saved.items():
                current = getattr(self._ext, name, None)
                if isinstance(value, list) and isinstance(current, list):
                    current[:] = value
                else:
                    setattr(self._ext, name, value)

        return self._ctx._on_stack(_restore)


class RoutesApi:
    """HTTP routers, through the OSS ``module_registry``."""

    def __init__(self, ctx: PluginContext, registry_getter: Callable[[], Any]) -> None:
        self._ctx = ctx
        self._registry_getter = registry_getter

    def include(
        self,
        router: Any,
        prefix: str,
        tags: list[str] | None = None,
        *,
        name: str | None = None,
    ) -> Disposer:
        """Mount ``router`` under ``prefix`` when the OSS app is created.

        Must run before ``create_app`` (that is when ``module_registry`` is
        applied to the aggregate router, so the router rides ``api_prefix``).
        ``name`` is the registry key (default: first tag, else the plugin id).
        """
        registry = self._registry_getter()
        entry_name = name or (tags[0] if tags else self._ctx.plugin_id)
        entry = registry.register(entry_name, router, prefix, tags=tags)

        def _unregister() -> None:
            if entry is not None and hasattr(registry, "unregister"):
                registry.unregister(entry)

        return self._ctx._on_stack(_unregister)


class MiddlewareApi:
    """Ordered middleware, through the OSS ``middleware_registry``."""

    def __init__(self, ctx: PluginContext, registry_getter: Callable[[], Any]) -> None:
        self._ctx = ctx
        self._registry_getter = registry_getter

    def add(self, cls: type, order: int, **kwargs: Any) -> Disposer:
        registry = self._registry_getter()
        entry = registry.register(cls, order, **kwargs)

        def _unregister() -> None:
            if entry is not None and hasattr(registry, "unregister"):
                registry.unregister(entry)

        return self._ctx._on_stack(_unregister)


class HostRoutesApi:
    """Routers of the aggregate ``api`` router (see :mod:`registry`).

    Distinct from :class:`RoutesApi`: that one feeds the overlay ``module_registry``,
    which is applied *after* these, so overlay routes still follow the host's.
    """

    def __init__(self, ctx: PluginContext, registry: HostRegistry) -> None:
        self._ctx = ctx
        self._registry = registry

    def include(self, router: Ref, *, slot: str | None = None) -> Disposer:
        """Add ``router`` (an ``APIRouter`` or a ``"module:attr"`` ref) at ``slot``.

        ``slot`` is the router's name in the canonical order
        (``valuz_agent.features.order.ROUTE_SLOTS``); an unlisted slot sorts last.
        """
        entry = self._registry.add_route(self._ctx.plugin_id, router, slot)
        return self._ctx._on_stack(lambda: self._registry.remove(entry))


class BootApi:
    """Startup / shutdown steps, per phase (``valuz_agent.boot.phases``)."""

    def __init__(self, ctx: PluginContext, registry: HostRegistry) -> None:
        self._ctx = ctx
        self._registry = registry

    def startup(self, phase: str, step: BootStep) -> Disposer:
        entry = self._registry.add_step(self._ctx.plugin_id, "startup", str(phase), step)
        return self._ctx._on_stack(lambda: self._registry.remove(entry))

    def shutdown(self, phase: str, step: BootStep) -> Disposer:
        entry = self._registry.add_step(self._ctx.plugin_id, "shutdown", str(phase), step)
        return self._ctx._on_stack(lambda: self._registry.remove(entry))


class InternalMountsApi:
    """Internal ASGI mounts and always-on MCP servers (``/_internal/...``)."""

    def __init__(self, ctx: PluginContext, registry: HostRegistry) -> None:
        self._ctx = ctx
        self._registry = registry

    def mount(self, mount: InternalMount) -> Disposer:
        entry = self._registry.add_mount(self._ctx.plugin_id, mount)
        return self._ctx._on_stack(lambda: self._registry.remove(entry))


class ToolkitApi:
    """Harness toolkit tool groups (served by the ``harness`` MCP server)."""

    def __init__(self, ctx: PluginContext, registry: HostRegistry) -> None:
        self._ctx = ctx
        self._registry = registry

    def add(self, tool: ToolGroup) -> Disposer:
        entry = self._registry.add_tool_group(self._ctx.plugin_id, tool)
        return self._ctx._on_stack(lambda: self._registry.remove(entry))


class AppApi:
    """What a plugin may do once the FastAPI app exists (``after_app`` hooks).

    Used for the contributions that cannot be expressed before ``create_app``:
    middleware positioned relative to the OSS stack, ASGI mounts, routes that
    must stay outside ``api_prefix``.
    """

    def __init__(self, app: FastAPI, ctx: PluginContext) -> None:
        self.app = app
        self._ctx = ctx

    def guard_middleware(self) -> Disposer:
        """Remember the middleware stack; dispose restores it.

        Call before mutating ``app.user_middleware`` by hand (e.g. swapping a
        layer through a legacy helper).
        """
        app = self.app
        saved = list(app.user_middleware)

        def _restore() -> None:
            app.user_middleware = saved
            app.middleware_stack = None

        return self._ctx._on_stack(_restore)

    def add_middleware(self, cls: type, **kwargs: Any) -> Disposer:
        """``app.add_middleware`` (last added = outermost), undoable."""
        dispose = self.guard_middleware()
        self.app.add_middleware(cls, **kwargs)  # type: ignore[arg-type]
        return dispose

    def mount(self, path: str, sub_app: Any, name: str | None = None) -> Disposer:
        self.app.mount(path, sub_app, name=name)
        route = self.app.routes[-1]
        return self._ctx._on_stack(lambda: _remove_route(self.app, route))

    def add_api_route(self, path: str, endpoint: Callable[..., Any], **kwargs: Any) -> Disposer:
        self.app.add_api_route(path, endpoint, **kwargs)
        route = self.app.routes[-1]
        return self._ctx._on_stack(lambda: _remove_route(self.app, route))

    def include_router(self, router: Any, **kwargs: Any) -> Disposer:
        before = len(self.app.routes)
        self.app.include_router(router, **kwargs)
        added = list(self.app.routes[before:])

        def _remove() -> None:
            for route in added:
                _remove_route(self.app, route)

        return self._ctx._on_stack(_remove)

    def set_state(self, name: str, value: Any) -> Disposer:
        """Set ``app.state.<name>``; dispose deletes it."""
        setattr(self.app.state, name, value)

        def _delete() -> None:
            if getattr(self.app.state, name, None) is value:
                delattr(self.app.state, name)

        return self._ctx._on_stack(_delete)


def _remove_route(app: Any, route: Any) -> None:
    try:
        app.router.routes.remove(route)
    except ValueError:
        pass


class PluginContext:
    """Per-plugin view of the host handed to ``BackendPlugin.apply``."""

    def __init__(
        self,
        *,
        plugin_id: str,
        stack: ExitStack,
        role: str,
        facets: frozenset[str],
        settings: Any,
        extensions: Any,
        module_registry_getter: Callable[[], Any],
        middleware_registry_getter: Callable[[], Any],
        registry: HostRegistry | None = None,
    ) -> None:
        self.plugin_id = plugin_id
        #: Free-form label of the composing process ("web", "worker", ...).
        self.role = role
        #: Contribution kinds this composition asks for ("ports", "routes", ...).
        #: The host never interprets them; plugins consult :meth:`wants`.
        self.facets = facets
        self.settings = settings
        self._stack = stack
        self._bound_ports: list[str] = []
        self._lifespan_hooks: list[Callable[[Any], Any]] = []
        self._after_app: list[AfterApp] = []
        self.ports = PortsApi(self, extensions)
        self.routes = RoutesApi(self, module_registry_getter)
        self.middleware = MiddlewareApi(self, middleware_registry_getter)
        #: The host's own composition registries (routes / boot steps / mounts /
        #: toolkit). A context built without a host gets a private, throwaway one.
        self.registry = registry if registry is not None else HostRegistry()
        self.host_routes = HostRoutesApi(self, self.registry)
        self.boot = BootApi(self, self.registry)
        self.internal_mounts = InternalMountsApi(self, self.registry)
        self.toolkit = ToolkitApi(self, self.registry)
        #: The Valuz hook bus (kernel): handlers and ``/commands`` for every runtime.
        self.hooks = HooksApi(self)
        self.commands = CommandsApi(self)
        #: The UI bus: draw element trees into UI slots, push toasts / status.
        self.ui = UiApi(self)

    # -- plumbing ---------------------------------------------------------

    def _on_stack(self, undo: Disposer) -> Disposer:
        """Register ``undo`` once on the stack and return an idempotent disposer."""
        state = {"done": False}

        def _dispose() -> None:
            if state["done"]:
                return
            state["done"] = True
            undo()

        self._stack.callback(_dispose)
        return _dispose

    # -- generic helpers --------------------------------------------------

    def wants(self, facet: str) -> bool:
        return facet in self.facets

    def on_dispose(self, undo: Disposer) -> Disposer:
        """Register an arbitrary undo (for legacy global registrations)."""
        return self._on_stack(undo)

    def lifespan(self, hook: Callable[[Any], Any]) -> Disposer:
        """Contribute an app-lifespan hook (``hook(app)`` -> async context manager).

        The host hands hooks to ``create_app(lifespan_hooks=...)`` exactly as
        given and in plugin order.
        """
        self._lifespan_hooks.append(hook)

        def _remove() -> None:
            try:
                self._lifespan_hooks.remove(hook)
            except ValueError:
                pass

        return self._on_stack(_remove)

    def mcp_always_on(self, spec: Any, *, unique: bool = False) -> Disposer:
        """Advertise an always-on MCP server to sessions (``ext.always_on_mcp_specs``)."""
        return self.ports.append("always_on_mcp_specs", spec, unique=unique)

    def after_app(self, callback: AfterApp) -> None:
        """Run ``callback(AppApi)`` once the FastAPI app exists.

        Callbacks run in plugin order via ``PluginHost.attach_app``; whatever
        they register through ``AppApi`` lands on this plugin's stack.
        """
        self._after_app.append(callback)
