"""Valuz's own handlers on the hook bus (``builtin`` tier)."""

from __future__ import annotations

from src.core.hooks.registry import HookRegistry


def install_builtins(registry: HookRegistry) -> None:
    from src.core.hooks.builtin import image_gate, plan_gate

    image_gate.install(registry)
    plan_gate.install(registry)


__all__ = ["install_builtins"]
