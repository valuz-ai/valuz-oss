"""The handlers Valuz itself puts on the hook bus.

``builtin`` tier, plus the classic-hooks executor, which runs the user's own
workspace hooks and so sits in the ``user`` tier.
"""

from __future__ import annotations

from src.core.hooks.registry import HookRegistry


def install_builtins(registry: HookRegistry) -> None:
    from src.core.hooks.builtin import citation_projection, image_gate, plan_gate
    from src.core.hooks.classic import executor as classic_hooks

    image_gate.install(registry)
    plan_gate.install(registry)
    citation_projection.install(registry)
    classic_hooks.install(registry)


__all__ = ["install_builtins"]
