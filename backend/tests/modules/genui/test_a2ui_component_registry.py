"""Dynamic edition components extend the compiler catalog without drift."""

from __future__ import annotations

import pytest

from valuz_agent.modules.genui import protocol
from valuz_agent.ports.a2ui_components import A2UIComponentRegistry
from valuz_agent.ports.extensions import ext

FINANCE_ENTRIES = [
    ("SecuritySnapshot", "  - SecuritySnapshot(title: string) — security snapshot."),
    ("PriceVolumeChart", "  - PriceVolumeChart(data: array) — price and volume."),
]


@pytest.fixture(autouse=True)
def fresh_registry(monkeypatch: pytest.MonkeyPatch) -> A2UIComponentRegistry:
    registry = A2UIComponentRegistry()
    monkeypatch.setattr(ext, "a2ui_components", registry)
    return registry


def _register(**kwargs: object) -> object:
    return ext.a2ui_components.register(
        kwargs.pop("layer", "distribution"),  # type: ignore[arg-type]
        group=kwargs.pop("group", "Finance"),  # type: ignore[arg-type]
        entries=kwargs.pop("entries", FINANCE_ENTRIES),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def test_registered_components_reach_catalog_per_call() -> None:
    assert "SecuritySnapshot" not in protocol.build_a2ui_catalog("all")
    result = _register(notes=("Every data-bearing component includes source and asOf.",))
    assert result.rejected == []  # type: ignore[attr-defined]
    catalog = protocol.build_a2ui_catalog("all")
    assert "- Finance components:" in catalog
    assert "SecuritySnapshot(title: string)" in catalog
    assert "source and asOf" in catalog
    assert "MetricGroup" in catalog


def test_scope_split_tracks_component_origin() -> None:
    protocol.build_a2ui_catalog("all")
    _register()
    edition = protocol.build_a2ui_catalog("edition")
    base = protocol.build_a2ui_catalog("atoms")
    assert "SecuritySnapshot" in edition
    assert "MetricGroup" not in edition
    assert "Stack" in edition
    assert "SecuritySnapshot" not in base
    assert "MetricGroup" in base


def test_collision_with_base_component_is_refused() -> None:
    protocol.build_a2ui_catalog("all")
    result = _register(entries=[("Metric", "  - Metric(label: string) — impostor.")])
    assert result.accepted == []  # type: ignore[attr-defined]
    assert result.rejected[0][0] == "Metric"  # type: ignore[attr-defined]


def test_registration_before_baseline_bind_is_revalidated() -> None:
    result = _register(entries=[("Metric", "  - Metric(label) — impostor."), *FINANCE_ENTRIES])
    assert result.rejected == []  # type: ignore[attr-defined]
    catalog = protocol.build_a2ui_catalog("all")
    assert "impostor" not in catalog
    assert "SecuritySnapshot" in catalog
    assert ext.a2ui_components.rejected_at_bind()[0][0] == "Metric"


def test_layer_order_reregistration_and_unregister_are_deterministic() -> None:
    protocol.build_a2ui_catalog("all")
    _register(layer="commercial", group="Commercial", entries=[FINANCE_ENTRIES[0]])
    result = _register()
    assert result.accepted == ["PriceVolumeChart"]  # type: ignore[attr-defined]
    assert result.rejected[0][0] == "SecuritySnapshot"  # type: ignore[attr-defined]

    _register(entries=[FINANCE_ENTRIES[0]])
    assert "PriceVolumeChart" not in protocol.build_a2ui_catalog("all")
    ext.a2ui_components.unregister("distribution")
    assert protocol.resolve_component_scope("edition") == "edition"


def test_replace_suppresses_base_but_never_the_root() -> None:
    protocol.build_a2ui_catalog("all")
    result = _register(mode="replace")
    assert result.rejected == []  # type: ignore[attr-defined]
    for scope in ("all", "edition", "atoms"):
        catalog = protocol.build_a2ui_catalog(scope)  # type: ignore[arg-type]
        assert "SecuritySnapshot" in catalog
        assert "MetricGroup" not in catalog
        assert "Stack" in catalog

    rooted = _register(
        entries=[("Stack", "  - Stack(children) — impostor."), *FINANCE_ENTRIES],
        mode="replace",
    )
    assert rooted.rejected[0][0] == "Stack"  # type: ignore[attr-defined]


def test_duplicate_names_in_one_registration_are_refused() -> None:
    protocol.build_a2ui_catalog("all")
    result = _register(entries=[FINANCE_ENTRIES[0], FINANCE_ENTRIES[0]])
    assert result.accepted == ["SecuritySnapshot"]  # type: ignore[attr-defined]
    assert result.rejected[0][1] == "duplicate name within this registration"  # type: ignore[attr-defined]
