"""Hook handlers an overlay ships inside the kernel process itself.

A backend plugin's ``ctx.hooks`` / ``ctx.commands`` register in the host
process, so they only reach a kernel running in that process. Where sessions
run in a separate kernel (a sandbox per owner, ``VALUZ_KERNEL_MODE=http``),
an overlay that needs its handlers there builds them into the kernel image as
a module with ``install(registry)`` and names it in ``VALUZ_KERNEL_HOOK_MODULES``
(comma separated). They load right after the builtins, at kernel import.

Fail-open: a module that is missing (an image older than the host) or raises
is logged and skipped; the kernel still boots with the builtins.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.hooks.registry import HookRegistry

logger = logging.getLogger(__name__)

ENV = "VALUZ_KERNEL_HOOK_MODULES"


def module_names(raw: str | None = None) -> list[str]:
    value = os.environ.get(ENV, "") if raw is None else raw
    return [name.strip() for name in value.split(",") if name.strip()]


def install_overlay_modules(registry: HookRegistry, raw: str | None = None) -> list[str]:
    """Import every named module and call its ``install(registry)``.

    Returns the names that installed.
    """
    loaded: list[str] = []
    for name in module_names(raw):
        try:
            module = importlib.import_module(name)
            install = getattr(module, "install", None)
            if not callable(install):
                logger.warning("kernel hook module %s has no install(registry); skipped", name)
                continue
            install(registry)
        except Exception:  # noqa: BLE001 — an overlay module never blocks the kernel
            logger.exception("kernel hook module %s failed to install; skipped", name)
            continue
        loaded.append(name)
    if loaded:
        logger.info("kernel hook modules installed: %s", ", ".join(loaded))
    return loaded


__all__ = ["ENV", "install_overlay_modules", "module_names"]
