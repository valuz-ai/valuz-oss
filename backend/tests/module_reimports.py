"""Explicit test helper for settings reloads, independent of conftest resolution."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType


def _rebind_on_parent(name: str, module: ModuleType | None) -> None:
    """Restore both sys.modules and the containing package's module attribute."""
    parent_name, _, leaf = name.rpartition(".")
    if not parent_name:
        return
    parent = sys.modules.get(parent_name)
    if parent is None:
        return
    if module is None:
        if hasattr(parent, leaf):
            try:
                delattr(parent, leaf)
            except AttributeError:  # pragma: no cover - defensive
                pass
        return
    setattr(parent, leaf, module)


@contextmanager
def reimported_modules(*prefixes: str) -> Iterator[None]:
    """Temporarily reload matching modules and restore their original identities.

    Both sys.modules entries and package attributes are restored on exit, also
    when the test raises. Modules first imported in the window are removed.
    """
    matcher = tuple(prefixes)
    saved = {name: mod for name, mod in sys.modules.items() if name.startswith(matcher)}
    for name in saved:
        sys.modules.pop(name, None)
    try:
        yield
    finally:
        window_names = [name for name in sys.modules if name.startswith(matcher)]
        for name in window_names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
        for name in set(window_names) | set(saved):
            _rebind_on_parent(name, saved.get(name))
