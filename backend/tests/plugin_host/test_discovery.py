"""Discovery: ``valuz.bundles`` entry points and explicit lists."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from valuz_agent.plugin_host import (
    BUNDLE_ENTRY_POINT_GROUP,
    BackendPluginBase,
    InvalidPluginError,
    coerce_plugins,
    collect_plugins,
    discover_bundles,
)


def _cls(pid: str) -> type[BackendPluginBase]:
    class _P(BackendPluginBase):
        def apply(self, ctx: Any, config: Any) -> None: ...

    _P.id = pid
    return _P


def _ep(name: str, payload: Any = None, error: Exception | None = None) -> SimpleNamespace:
    def load() -> Any:
        if error is not None:
            raise error
        return payload

    return SimpleNamespace(name=name, load=load)


def _finder(*eps: SimpleNamespace) -> Any:
    seen: dict[str, Any] = {}

    def finder(*, group: str) -> list[SimpleNamespace]:
        seen["group"] = group
        return list(eps)

    finder.seen = seen  # type: ignore[attr-defined]
    return finder


def test_the_group_name_is_valuz_bundles() -> None:
    assert BUNDLE_ENTRY_POINT_GROUP == "valuz.bundles"
    finder = _finder()
    discover_bundles(entry_points=finder)
    assert finder.seen["group"] == "valuz.bundles"


def test_entry_point_payloads_may_be_classes_instances_factories_or_iterables() -> None:
    result = discover_bundles(
        entry_points=_finder(
            _ep("a-class", _cls("from-class")),
            _ep("b-instance", _cls("from-instance")()),
            _ep("c-factory", lambda: [_cls("f1")(), _cls("f2")]),
        )
    )
    assert [p.id for p in result.plugins] == ["from-class", "from-instance", "f1", "f2"]
    assert result.failures == {}


def test_one_broken_bundle_does_not_hide_the_others() -> None:
    result = discover_bundles(
        entry_points=_finder(
            _ep("good", _cls("good")),
            _ep("broken", error=ImportError("no module")),
            _ep("junk", 42),
        )
    )
    assert [p.id for p in result.plugins] == ["good"]
    assert set(result.failures) == {"broken", "junk"}
    assert "no module" in result.failures["broken"]


def test_explicit_plugins_come_first_and_shadow_discovered_duplicates() -> None:
    explicit = _cls("shared")()
    result = collect_plugins(
        [explicit, _cls("only-explicit")],
        discover=True,
        entry_points=_finder(_ep("dup", _cls("shared")), _ep("extra", _cls("extra"))),
    )
    assert [p.id for p in result.plugins] == ["shared", "only-explicit", "extra"]
    assert result.plugins[0] is explicit


def test_discovery_is_off_unless_asked_for() -> None:
    result = collect_plugins([_cls("x")], entry_points=_finder(_ep("e", _cls("never"))))
    assert [p.id for p in result.plugins] == ["x"]


def test_coerce_rejects_non_plugins_and_bad_declarations() -> None:
    with pytest.raises(InvalidPluginError):
        coerce_plugins("not a plugin")
    bad = _cls("bad")
    bad.needs = ["list-not-tuple"]  # type: ignore[assignment]
    with pytest.raises(InvalidPluginError):
        coerce_plugins(bad)
    empty = _cls("")
    with pytest.raises(InvalidPluginError):
        coerce_plugins(empty)
