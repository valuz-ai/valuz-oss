"""The explicit helper restores module and parent identities after reloads."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tests.module_reimports import reimported_modules


@pytest.mark.parametrize("raises", [False, True])
def test_reimport_restores_original_module_and_package_attribute(monkeypatch, raises):
    parent = ModuleType("_reload_test_package")
    original = ModuleType("_reload_test_package.settings")
    replacement = ModuleType(original.__name__)
    parent.settings = original
    monkeypatch.setitem(sys.modules, parent.__name__, parent)
    monkeypatch.setitem(sys.modules, original.__name__, original)

    def window():
        with reimported_modules(original.__name__):
            assert original.__name__ not in sys.modules
            sys.modules[original.__name__] = replacement
            parent.settings = replacement
            if raises:
                raise RuntimeError("synthetic test failure")

    if raises:
        with pytest.raises(RuntimeError, match="synthetic test failure"):
            window()
    else:
        window()
    assert sys.modules[original.__name__] is original
    assert parent.settings is original


def test_window_only_modules_are_removed_from_the_parent(monkeypatch):
    parent = ModuleType("_reload_test_package")
    transient = ModuleType("_reload_test_package.settings")
    monkeypatch.setitem(sys.modules, parent.__name__, parent)
    monkeypatch.delitem(sys.modules, transient.__name__, raising=False)
    with reimported_modules(transient.__name__):
        sys.modules[transient.__name__] = transient
        parent.settings = transient
    assert transient.__name__ not in sys.modules
    assert not hasattr(parent, "settings")
