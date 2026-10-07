"""``PluginHost``: ordered, reversible, failure-isolated plugin activation.

Mirrors the DSH rules the design adopts (§6): activation order comes from
``needs`` / ``provides`` and not from list order; a required plugin that fails
aborts startup, an optional one is rolled back and recorded as ``failed``.
Backend toggles are always ``restart-required`` (§7).
"""

from __future__ import annotations

import heapq
import logging
from collections.abc import Callable, Collection, Iterable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any

from valuz_agent.plugin_host.context import AfterApp, AppApi, PluginContext
from valuz_agent.plugin_host.errors import (
    DuplicatePluginError,
    InvalidPluginError,
    MissingNeedError,
    PluginCycleError,
    PluginHostError,
    PluginStartupError,
)
from valuz_agent.plugin_host.locks import compute_locks
from valuz_agent.plugin_host.plugin import BackendPlugin, ChangeResult, PluginStatus
from valuz_agent.plugin_host.registry import HostRegistry

logger = logging.getLogger("valuz_agent.plugin_host")

#: ``PluginHost.list`` shadows the builtin inside the class body.
_list = list

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


@dataclass
class PluginRecord:
    """Live state of one registered plugin."""

    plugin: BackendPlugin
    index: int
    status: PluginStatus = "pending"
    error: str | None = None
    config: Any = None
    stack: ExitStack | None = None
    bound_ports: list[str] = field(default_factory=list)
    lifespan_hooks: list[Callable[[Any], Any]] = field(default_factory=list)
    after_app: list[AfterApp] = field(default_factory=list)
    #: Desired state recorded by ``set_enabled``; takes effect on next start.
    desired_enabled: bool = True
    #: Config edit recorded by ``edit_config``; takes effect on next start.
    pending_config: Any = None


@dataclass(frozen=True)
class PluginInfo:
    """Read-only projection for management surfaces (P3 ``/v1/extensions``)."""

    id: str
    status: PluginStatus
    required: bool
    needs: tuple[str, ...]
    provides: tuple[str, ...]
    entitlement: str | None
    error: str | None
    has_config: bool
    desired_enabled: bool
    bound_ports: tuple[str, ...]
    #: Ids of locked plugins that need what this one provides (see ``locks``).
    #: Empty for a plugin nothing locked depends on; ``required`` plugins are locked
    #: regardless.
    required_by: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "required": self.required,
            "needs": list(self.needs),
            "provides": list(self.provides),
            "entitlement": self.entitlement,
            "error": self.error,
            "hasConfig": self.has_config,
            "desiredEnabled": self.desired_enabled,
            "boundPorts": list(self.bound_ports),
            "requiredBy": list(self.required_by),
        }


def _validate_plugin(plugin: object) -> BackendPlugin:
    plugin_id = getattr(plugin, "id", None)
    if not isinstance(plugin_id, str) or not plugin_id:
        raise InvalidPluginError(f"plugin {plugin!r} must expose a non-empty string id")
    if not callable(getattr(plugin, "apply", None)):
        raise InvalidPluginError(f"plugin {plugin_id!r} has no apply(ctx, config)")
    for attr in ("needs", "provides"):
        value = getattr(plugin, attr, ())
        if not isinstance(value, tuple) or not all(isinstance(v, str) for v in value):
            raise InvalidPluginError(f"plugin {plugin_id!r}.{attr} must be a tuple of str")
    return plugin  # type: ignore[return-value]


class PluginHost:
    """Compose a set of :class:`BackendPlugin` objects into one process."""

    def __init__(
        self,
        plugins: Iterable[BackendPlugin] = (),
        *,
        role: str = "web",
        facets: Iterable[str] = (),
        settings: Any = None,
        available: Iterable[str] | None = None,
        extensions: Any = None,
        module_registry: Any = None,
        middleware_registry: Any = None,
        registry: HostRegistry | None = None,
    ) -> None:
        """
        ``registry``: the host-composition registries (routes / boot steps / mounts /
        toolkit, see :mod:`~valuz_agent.plugin_host.registry`); one per host.
        ``available``: names the *base* provides (default: every public port on
        the extension container, plus the OSS feature capabilities ``oss.*``) so a
        plugin may ``need`` an OSS-default port or feature without a provider plugin.
        ``extensions`` / the registries default to the process-wide OSS singletons,
        resolved lazily.
        """
        self.role = role
        self.facets = frozenset(facets)
        self.settings = settings
        self._extensions = extensions
        self._module_registry = module_registry
        self._middleware_registry = middleware_registry
        self.registry = registry if registry is not None else HostRegistry()
        self._available_override = None if available is None else frozenset(available)
        self._records: dict[str, PluginRecord] = {}
        self._order: list[str] = []
        self.register(*plugins)

    # -- singletons (lazy) ------------------------------------------------

    def _ext(self) -> Any:
        if self._extensions is None:
            from valuz_agent.ports.extensions import ext

            return ext
        return self._extensions

    def _module_registry_now(self) -> Any:
        if self._module_registry is not None:
            return self._module_registry
        from valuz_agent.infra import module_registry as module

        return module.module_registry

    def _middleware_registry_now(self) -> Any:
        if self._middleware_registry is not None:
            return self._middleware_registry
        from valuz_agent.infra import middleware_registry as module

        return module.middleware_registry

    def _available(self) -> frozenset[str]:
        if self._available_override is not None:
            return self._available_override
        ports = frozenset(n for n in vars(self._ext()) if not n.startswith("_"))
        # The OSS features (``oss.<feature>``) are part of the base too: the OSS
        # plugins only switch them on. A host that does not include them -- a worker,
        # a compat entry point, an edition loaded on its own -- may still ``need`` one.
        from valuz_agent.features import oss_capabilities

        return ports | oss_capabilities()

    # -- registration -----------------------------------------------------

    def register(self, *plugins: BackendPlugin) -> None:
        for raw in plugins:
            plugin = _validate_plugin(raw)
            if plugin.id in self._records:
                raise DuplicatePluginError(plugin.id)
            self._records[plugin.id] = PluginRecord(plugin=plugin, index=len(self._records))

    def get(self, plugin_id: str) -> PluginRecord:
        try:
            return self._records[plugin_id]
        except KeyError:
            raise PluginHostError(f"unknown plugin {plugin_id!r}") from None

    # -- ordering ---------------------------------------------------------

    def _providers(self) -> dict[str, list[str]]:
        providers: dict[str, list[str]] = {}
        for record in self._records.values():
            for name in record.plugin.provides:
                providers.setdefault(name, []).append(record.plugin.id)
        return providers

    def order(self) -> list[str]:
        """Topological activation order; ties keep registration order.

        Raises ``MissingNeedError`` / ``PluginCycleError``.
        """
        providers = self._providers()
        available = self._available()
        incoming: dict[str, set[str]] = {pid: set() for pid in self._records}
        for pid, record in self._records.items():
            for need in record.plugin.needs:
                makers = [m for m in providers.get(need, []) if m != pid]
                if makers:
                    incoming[pid].update(makers)
                elif need in providers or need in available:
                    continue  # self-provided, or satisfied by the base
                else:
                    raise MissingNeedError(pid, need)

        dependents: dict[str, list[str]] = {pid: [] for pid in self._records}
        for pid, deps in incoming.items():
            for dep in deps:
                dependents[dep].append(pid)
        ready = [self._records[pid].index for pid, deps in incoming.items() if not deps]
        heapq.heapify(ready)
        by_index = {r.index: pid for pid, r in self._records.items()}
        order: list[str] = []
        remaining = {pid: len(deps) for pid, deps in incoming.items()}
        while ready:
            pid = by_index[heapq.heappop(ready)]
            order.append(pid)
            for child in dependents[pid]:
                remaining[child] -= 1
                if remaining[child] == 0:
                    heapq.heappush(ready, self._records[child].index)
        if len(order) != len(self._records):
            raise PluginCycleError([pid for pid in self._records if pid not in set(order)])
        return order

    # -- loading ----------------------------------------------------------

    def load_all(
        self,
        configs: Mapping[str, Any] | None = None,
        *,
        disabled: Collection[str] = (),
    ) -> list[PluginInfo]:
        """Apply every pending plugin in dependency order.

        A required plugin that fails (or whose need cannot be met) rolls the
        whole host back and raises :class:`PluginStartupError`; an optional one
        is rolled back alone and recorded as ``failed``.
        """
        order = self.order()
        providers = self._providers()
        configs = configs or {}
        # Never honour a request to switch off a locked plugin (a hand-edited
        # plugins.json must not turn into a startup failure).
        disabled = self.effective_disabled(disabled)
        for pid in order:
            record = self._records[pid]
            if record.status != "pending":
                continue
            if pid in disabled or not record.desired_enabled:
                record.status = "disabled"
                continue
            unmet = self._unmet_need(record, providers)
            if unmet is not None:
                self._fail(record, f"need {unmet!r} is unavailable", cause=None)
                continue
            self._apply(record, configs)
        self._order = [pid for pid in order if self._records[pid].status == "active"]
        return self.list()

    def _unmet_need(self, record: PluginRecord, providers: dict[str, list[str]]) -> str | None:
        for need in record.plugin.needs:
            makers = [m for m in providers.get(need, []) if m != record.plugin.id]
            if makers and not any(self._records[m].status == "active" for m in makers):
                return need
        return None

    def _apply(self, record: PluginRecord, configs: Mapping[str, Any]) -> None:
        plugin = record.plugin
        stack = ExitStack()
        ctx = PluginContext(
            plugin_id=plugin.id,
            stack=stack,
            role=self.role,
            facets=self.facets,
            settings=self.settings,
            extensions=self._ext(),
            module_registry_getter=self._module_registry_now,
            middleware_registry_getter=self._middleware_registry_now,
            registry=self.registry,
        )
        try:
            config = self._build_config(record, configs)
            plugin.apply(ctx, config)
        except Exception as exc:
            self._rollback(plugin.id, stack)
            self._fail(record, f"{type(exc).__name__}: {exc}", cause=exc)
            return
        record.config = config
        record.stack = stack
        record.status = "active"
        record.error = None
        record.bound_ports = list(ctx._bound_ports)
        record.lifespan_hooks = ctx._lifespan_hooks
        record.after_app = ctx._after_app
        record._ctx = ctx  # type: ignore[attr-defined]

    @staticmethod
    def _build_config(record: PluginRecord, configs: Mapping[str, Any]) -> Any:
        config_cls = getattr(record.plugin, "Config", None)
        if config_cls is None:
            return None
        raw = record.pending_config
        if raw is None:
            raw = configs.get(record.plugin.id, {})
        return config_cls.model_validate(raw)

    def _fail(self, record: PluginRecord, reason: str, *, cause: BaseException | None) -> None:
        record.status = "failed"
        record.error = reason
        plugin = record.plugin
        if getattr(plugin, "required", False):
            logger.error("required plugin %s failed: %s", plugin.id, reason, exc_info=cause)
            self.unload_all()
            error = PluginStartupError(plugin.id, reason)
            if cause is not None:
                raise error from cause
            raise error
        logger.warning("optional plugin %s failed and was rolled back: %s", plugin.id, reason)

    @staticmethod
    def _rollback(plugin_id: str, stack: ExitStack) -> None:
        try:
            stack.close()
        except Exception:  # noqa: BLE001 - a broken disposer must not mask the cause
            logger.warning("rollback of plugin %s raised", plugin_id, exc_info=True)

    # -- app phase --------------------------------------------------------

    def attach_app(self, app: Any) -> None:
        """Run every active plugin's ``after_app`` callbacks, in load order."""
        for pid in list(self._order):
            record = self._records[pid]
            if record.status != "active" or record.stack is None:
                continue
            ctx: PluginContext = record._ctx  # type: ignore[attr-defined]
            api = AppApi(app, ctx)
            try:
                for callback in record.after_app:
                    callback(api)
            except Exception as exc:
                self._rollback_partial(record)
                self._fail(record, f"{type(exc).__name__}: {exc}", cause=exc)

    def _rollback_partial(self, record: PluginRecord) -> None:
        if record.stack is not None:
            self._rollback(record.plugin.id, record.stack)
            record.stack = None

    # -- unloading --------------------------------------------------------

    def _dependents_of(self, plugin_id: str) -> list[str]:
        provided = set(self._records[plugin_id].plugin.provides)
        if not provided:
            return []
        return [
            pid
            for pid, record in self._records.items()
            if pid != plugin_id and provided & set(record.plugin.needs)
        ]

    def unload(self, plugin_id: str) -> list[str]:
        """Dispose a plugin and, first, every active plugin that needs it.

        Returns the unloaded ids (dependents first). Contributions are undone in
        reverse registration order; the plugin's tables are never touched.
        """
        self.get(plugin_id)
        done: list[str] = []
        self._unload(plugin_id, done, seen=set())
        return done

    def _unload(self, plugin_id: str, done: list[str], seen: set[str]) -> None:
        if plugin_id in seen:
            return
        seen.add(plugin_id)
        for dependent in self._dependents_of(plugin_id):
            if self._records[dependent].status == "active":
                self._unload(dependent, done, seen)
        record = self._records[plugin_id]
        if record.status != "active":
            return
        if record.stack is not None:
            self._rollback(plugin_id, record.stack)
        record.stack = None
        record.status = "disposed"
        record.lifespan_hooks = []
        record.after_app = []
        self._order = [pid for pid in self._order if pid != plugin_id]
        done.append(plugin_id)

    def unload_all(self) -> list[str]:
        done: list[str] = []
        for pid in reversed(list(self._records)):
            self._unload(pid, done, seen=set())
        return done

    # -- management projection -------------------------------------------

    def locks(self, disabled: Collection[str] | None = None) -> dict[str, list[str]]:
        """Locked plugins (``required`` or needed by one) -> the locked ids needing them.

        ``disabled`` is the requested switched-off set (default: what this host was
        asked to leave off); see :func:`~valuz_agent.plugin_host.locks.compute_locks`.
        """
        requested = self._requested_disabled() if disabled is None else disabled
        return compute_locks({pid: r.plugin for pid, r in self._records.items()}, requested)

    def effective_disabled(self, requested: Collection[str]) -> frozenset[str]:
        """``requested`` minus the locked plugins and ids this host does not know."""
        locked = self.locks(requested)
        return frozenset(p for p in requested if p in self._records and p not in locked)

    def _requested_disabled(self) -> set[str]:
        return {
            pid
            for pid, record in self._records.items()
            if record.status == "disabled" or not record.desired_enabled
        }

    def is_active(self, plugin_id: str) -> bool:
        record = self._records.get(plugin_id)
        return record is not None and record.status == "active"

    def inactive_ids(self) -> _list[str]:
        """Plugins that are not active in this process (disabled, failed, unloaded)."""
        return sorted(pid for pid, r in self._records.items() if r.status != "active")

    def list(self, disabled: Collection[str] | None = None) -> list[PluginInfo]:
        locks = self.locks(disabled)
        return [
            PluginInfo(
                id=pid,
                status=record.status,
                required=bool(getattr(record.plugin, "required", False)),
                needs=tuple(record.plugin.needs),
                provides=tuple(record.plugin.provides),
                entitlement=getattr(record.plugin, "entitlement", None),
                error=record.error,
                has_config=getattr(record.plugin, "Config", None) is not None,
                desired_enabled=record.desired_enabled,
                bound_ports=tuple(record.bound_ports),
                required_by=tuple(locks.get(pid, ())),
            )
            for pid, record in self._records.items()
        ]

    def active_ids(self) -> _list[str]:
        return list(self._order)

    def lifespan_hooks(self) -> _list[Callable[[Any], Any]]:
        """Hooks of active plugins, in load order, exactly as contributed."""
        hooks: _list[Callable[[Any], Any]] = []
        for pid in self._order:
            record = self._records[pid]
            if record.status == "active":
                hooks.extend(record.lifespan_hooks)
        return hooks

    def migration_chains(self, *, only_active: bool = False) -> _list[tuple[str, Any]]:
        """``(plugin_id, chain)`` for every plugin that declares migrations.

        Static declarations: works on a host that was never loaded (and does not
        need a valid ``needs`` graph), which is how the ``migrate`` entry point
        uses it. Registration order.
        """
        chains: _list[tuple[str, Any]] = []
        for pid in self._order if only_active else self._records:
            record = self._records[pid]
            declare = getattr(record.plugin, "migrations", None)
            if declare is None:
                continue
            chains.extend((pid, chain) for chain in declare())
        return chains

    def config_schemas(self) -> dict[str, dict[str, Any]]:
        """JSON Schema 2020-12 of each plugin's ``Config`` (DSH ``--dump-config-schema`` shape)."""
        schemas: dict[str, dict[str, Any]] = {}
        for pid, record in self._records.items():
            config_cls = getattr(record.plugin, "Config", None)
            if config_cls is not None:
                schemas[pid] = {"$schema": JSON_SCHEMA_DIALECT, **config_cls.model_json_schema()}
        return schemas

    # -- toggles (always restart-required) --------------------------------

    def set_enabled(self, plugin_id: str, enabled: bool) -> ChangeResult:
        """Record the desired state. A running FastAPI process cannot drop routes
        or ports safely, so the answer is always ``restart-required``."""
        self.get(plugin_id).desired_enabled = enabled
        return "restart-required"

    def edit_config(self, plugin_id: str, values: Mapping[str, Any]) -> ChangeResult:
        """Validate ``values`` against the plugin's ``Config`` and record them for
        the next start. Raises ``pydantic.ValidationError`` on a bad edit."""
        record = self.get(plugin_id)
        config_cls = getattr(record.plugin, "Config", None)
        if config_cls is None:
            raise PluginHostError(f"plugin {plugin_id!r} has no Config")
        config_cls.model_validate(dict(values))
        record.pending_config = dict(values)
        return "restart-required"
