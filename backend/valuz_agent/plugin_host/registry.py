"""Ordered host-composition registries, owned by one :class:`PluginHost`.

``PluginContext`` already lets a plugin bind ports, add routers to the overlay
``module_registry`` and hook the app lifespan. The *host's own* features (the OSS
backend is a set of plugins too) need four more kinds of contribution, and all of
them have the same shape -- a plugin only **registers**, and the process
**assembles** later, from the registry, in a canonical order:

``routes``      the routers of the aggregate ``api`` router (before the overlay
                ``module_registry`` routes)
``steps``       boot steps, per phase, for startup and shutdown (``boot/lifespan``)
``mounts``      internal ASGI mounts (the ``/_internal`` DataService + always-on MCP
                servers) and the session managers that serve them
``tools``       harness toolkit tool groups (``init_kernel`` builds the toolsets)

A registry belongs to one ``PluginHost`` (no process-wide state): composing the app
twice builds two independent registries, and a plugin that is disabled or rolled
back leaves no entry behind, because every ``add_*`` returns a disposer that the
plugin's ``ExitStack`` calls.

**Ordering.** Order is *canonical, not registration order*: the caller passes the
ordered slot names (``valuz_agent.features.order`` / ``valuz_agent.boot.phases``)
and an entry sorts by ``(slot index, registration seq)``. An entry whose slot is
not listed (an overlay's own contribution) sorts after every listed one, in
registration order. That is what lets the plugin split keep the historical,
load-bearing order of the hard-coded composition byte for byte while each entry is
owned -- and removable -- by exactly one plugin.

References (``Ref``) are either a callable / object, or a ``"module:attr"`` string
resolved only when used. Lazy refs matter twice: a disabled plugin never imports
its route modules, and an overlay contribution that must precede an OSS module's
import-time snapshot (the connector catalog) still can, because OSS modules are
imported at assembly, after every plugin has applied.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: A callable / object, or ``"package.module:attr.path"`` resolved on first use.
Ref = Any


def resolve_ref(ref: Ref) -> Any:
    """Return the object a ``Ref`` points at (strings are imported lazily)."""
    if not isinstance(ref, str):
        return ref
    module_name, _, attr = ref.partition(":")
    if not module_name or not attr:
        raise ValueError(f"bad ref {ref!r}; expected 'package.module:attr'")
    obj: Any = importlib.import_module(module_name)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


@dataclass(frozen=True)
class BootStep:
    """One named startup / shutdown step.

    ``run`` is resolved **each time the step runs** (not at registration), so a
    string ref always sees the current attribute -- a test that replaces a step
    function is honoured. ``takes_app`` says whether the step is called with the
    FastAPI app; sync and async steps are both fine.
    """

    name: str
    run: Ref
    takes_app: bool = False

    async def execute(self, app: Any) -> None:
        fn = resolve_ref(self.run)
        result = fn(app) if self.takes_app else fn()
        if inspect.isawaitable(result):
            await result


@dataclass(frozen=True)
class InternalMount:
    """One internal ASGI mount, optionally an always-on MCP server.

    ``name`` is the canonical slot (``"docs"``, ``"data"``, ``"toolkit-base"``).
    A *built-in* mount has ``build`` (an ASGI app factory, called with the app when
    ``takes_app``) and, if sessions are handed it, the model-visible ``server`` name
    (``"valuz-docs"``). A *spec* mount carries an ``AlwaysOnMcpServerSpec`` instead:
    the assembly appends it to ``ext.always_on_mcp_specs`` (so sessions advertise it
    like any edition server) and the generic spec loop mounts it.
    ``session_manager`` (a zero-arg callable returning an async context manager) is
    entered by the ``start_mcp_session_managers`` step, in slot order.
    """

    name: str
    path: str
    build: Ref | None = None
    takes_app: bool = False
    session_manager: Ref | None = None
    server: str | None = None
    spec: Any | None = None

    def __post_init__(self) -> None:
        if (self.build is None) == (self.spec is None):
            raise ValueError(f"mount {self.name!r} needs exactly one of build / spec")


@dataclass(frozen=True)
class ToolGroup:
    """A group of harness tools: ``orchestration`` (base toolset), ``dispatch``
    (lead toolset) or ``shared`` (both). ``build`` returns a sequence of tool defs."""

    group: str
    slot: str
    build: Ref


@dataclass(eq=False)
class _Entry:
    plugin_id: str
    seq: int


@dataclass(eq=False)
class RouteEntry(_Entry):
    router: Ref
    slot: str | None

    def resolve(self) -> Any:
        return resolve_ref(self.router)


@dataclass(eq=False)
class StepEntry(_Entry):
    kind: str  # "startup" | "shutdown"
    phase: str
    step: BootStep


@dataclass(eq=False)
class MountEntry(_Entry):
    mount: InternalMount


@dataclass(eq=False)
class ToolEntry(_Entry):
    tool: ToolGroup


def _sort_key(slots: Sequence[str]) -> Callable[[Any, str | None], tuple[int, int]]:
    index = {slot: i for i, slot in enumerate(slots)}
    unknown = len(index)

    def key(entry: Any, slot: str | None) -> tuple[int, int]:
        return (index.get(slot, unknown) if slot is not None else unknown, entry.seq)

    return key


class HostRegistry:
    """The ordered contributions of one plugin host (see the module docstring)."""

    def __init__(self) -> None:
        self._seq = 0
        self._routes: list[RouteEntry] = []
        self._steps: list[StepEntry] = []
        self._mounts: list[MountEntry] = []
        self._tools: list[ToolEntry] = []

    def _next(self) -> int:
        self._seq += 1
        return self._seq

    # -- routes -----------------------------------------------------------

    def add_route(self, plugin_id: str, router: Ref, slot: str | None) -> RouteEntry:
        entry = RouteEntry(plugin_id, self._next(), router, slot)
        self._routes.append(entry)
        return entry

    def routes(self, slots: Sequence[str]) -> list[RouteEntry]:
        key = _sort_key(slots)
        return sorted(self._routes, key=lambda e: key(e, e.slot))

    # -- boot steps ---------------------------------------------------------

    def add_step(self, plugin_id: str, kind: str, phase: str, step: BootStep) -> StepEntry:
        if kind not in ("startup", "shutdown"):
            raise ValueError(f"unknown step kind {kind!r}")
        for other in self._steps:
            if other.kind == kind and other.step.name == step.name:
                raise ValueError(
                    f"{kind} step {step.name!r} is already registered by plugin {other.plugin_id!r}"
                )
        entry = StepEntry(plugin_id, self._next(), kind, phase, step)
        self._steps.append(entry)
        return entry

    def steps(self, kind: str, phases: Mapping[str, Sequence[str]]) -> list[StepEntry]:
        """Steps of ``kind`` in phase order; inside a phase, canonical name order."""
        known = {s.phase for s in self._steps if s.kind == kind}
        unknown = known - set(phases)
        if unknown:
            raise ValueError(f"unknown boot phase(s) {sorted(unknown)} for {kind} steps")
        ordered: list[StepEntry] = []
        for phase, names in phases.items():
            key = _sort_key(names)
            in_phase = [e for e in self._steps if e.kind == kind and e.phase == phase]
            ordered.extend(sorted(in_phase, key=lambda e: key(e, e.step.name)))
        return ordered

    # -- internal mounts ------------------------------------------------------

    def add_mount(self, plugin_id: str, mount: InternalMount) -> MountEntry:
        for other in self._mounts:
            if other.mount.name == mount.name or other.mount.path == mount.path:
                raise ValueError(
                    f"internal mount {mount.name!r} ({mount.path}) is already registered "
                    f"by plugin {other.plugin_id!r}"
                )
        entry = MountEntry(plugin_id, self._next(), mount)
        self._mounts.append(entry)
        return entry

    def mounts(self, slots: Sequence[str]) -> list[MountEntry]:
        key = _sort_key(slots)
        return sorted(self._mounts, key=lambda e: key(e, e.mount.name))

    # -- harness tools --------------------------------------------------------

    def add_tool_group(self, plugin_id: str, tool: ToolGroup) -> ToolEntry:
        for other in self._tools:
            if other.tool.slot == tool.slot:
                raise ValueError(
                    f"tool group {tool.slot!r} is already registered by plugin {other.plugin_id!r}"
                )
        entry = ToolEntry(plugin_id, self._next(), tool)
        self._tools.append(entry)
        return entry

    def tool_groups(self, slots: Sequence[str]) -> list[ToolEntry]:
        key = _sort_key(slots)
        return sorted(self._tools, key=lambda e: key(e, e.tool.slot))

    # -- removal (plugin rollback / unload) ---------------------------------

    def remove(self, entry: _Entry) -> None:
        for bucket in (self._routes, self._steps, self._mounts, self._tools):
            for i, existing in enumerate(bucket):
                if existing is entry:
                    del bucket[i]
                    return

    def owners(self) -> dict[str, dict[str, list[str]]]:
        """``plugin id -> {kind: [names]}`` -- introspection for tests / tooling."""
        out: dict[str, dict[str, list[str]]] = {}

        def put(pid: str, kind: str, name: str) -> None:
            out.setdefault(pid, {}).setdefault(kind, []).append(name)

        for r in self._routes:
            put(r.plugin_id, "routes", r.slot or str(r.router))
        for s in self._steps:
            put(s.plugin_id, s.kind, s.step.name)
        for m in self._mounts:
            put(m.plugin_id, "mounts", m.mount.name)
        for t in self._tools:
            put(t.plugin_id, "tools", t.tool.slot)
        return out
