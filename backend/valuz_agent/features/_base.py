"""Shared helpers for the OSS feature plugins."""

from __future__ import annotations

from typing import Any

from valuz_agent.boot.phases import BootPhase
from valuz_agent.features.order import ROUTE_REFS
from valuz_agent.plugin_host import BackendPluginBase, PluginContext
from valuz_agent.plugin_host.registry import BootStep

_STEPS = "valuz_agent.boot.steps"


class OssPlugin(BackendPluginBase):
    """An OSS backend feature.

    ``provides`` carries the feature's capability name (``oss.<feature>``); other
    plugins -- the commercial overlay and editions included -- ``need`` it when
    they cannot work without the feature. ``needs`` always includes ``oss.core``.
    Contributions are only *registered* (see ``plugin_host.registry``); the app
    assembles them in canonical order.
    """

    required: bool = False
    needs: tuple[str, ...] = ("oss.core",)

    def apply(self, ctx: PluginContext, config: Any) -> None:
        self.register(ctx)

    def register(self, ctx: PluginContext) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


def step(name: str, *, app: bool = False) -> BootStep:
    """A step that is the function ``name`` of ``boot.steps`` (resolved when run)."""
    return BootStep(name, f"{_STEPS}:{name}", takes_app=app)


def routes(ctx: PluginContext, *slots: str) -> None:
    for slot in slots:
        ctx.host_routes.include(ROUTE_REFS[slot], slot=slot)


def startup(ctx: PluginContext, phase: BootPhase, *steps: BootStep) -> None:
    for s in steps:
        ctx.boot.startup(phase, s)


def shutdown(ctx: PluginContext, phase: BootPhase, *steps: BootStep) -> None:
    for s in steps:
        ctx.boot.shutdown(phase, s)
