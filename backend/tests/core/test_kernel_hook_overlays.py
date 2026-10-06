"""Overlay hook modules load into the kernel's registry (``VALUZ_KERNEL_HOOK_MODULES``)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from src.core.hooks import TOOL_CHECK, HookRegistry
from src.core.hooks.overlays import install_overlay_modules, module_names


def _module(name: str, install: Any) -> None:
    module = types.ModuleType(name)
    if install is not None:
        module.install = install  # type: ignore[attr-defined]
    sys.modules[name] = module


@pytest.fixture(autouse=True)
def _cleanup() -> Any:
    yield
    for name in [n for n in sys.modules if n.startswith("overlay_test_")]:
        del sys.modules[name]


def test_names_are_comma_separated_and_trimmed() -> None:
    assert module_names(" a.b , ,c ") == ["a.b", "c"]
    assert module_names("") == []


def test_each_module_installs_into_the_registry() -> None:
    registry = HookRegistry()

    async def handler(ctx: Any, event: Any, next_: Any) -> Any:
        return await next_()

    def install(reg: HookRegistry) -> None:
        reg.register(TOOL_CHECK, handler, owner="overlay.policy", tier="prepend")

    _module("overlay_test_ok", install)
    assert install_overlay_modules(registry, "overlay_test_ok") == ["overlay_test_ok"]
    assert [row["owner"] for row in registry.owners()] == ["overlay.policy"]


def test_a_missing_or_broken_module_is_skipped(caplog: pytest.LogCaptureFixture) -> None:
    registry = HookRegistry()

    def boom(reg: HookRegistry) -> None:
        raise RuntimeError("nope")

    _module("overlay_test_broken", boom)
    _module("overlay_test_noinstall", None)
    loaded = install_overlay_modules(
        registry, "overlay_test_missing_xyz,overlay_test_broken,overlay_test_noinstall"
    )
    assert loaded == []
    assert registry.owners() == []
    assert "overlay_test_missing_xyz" in caplog.text
    assert "overlay_test_broken" in caplog.text
