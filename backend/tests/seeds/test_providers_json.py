"""Pin the bundled ``providers.json`` against the runtime descriptor map.

The seed file declares which built-in provider IDs exist on first
boot and which ``provider_kind`` each one binds to. The actual
runtime metadata for each kind (default_model, base_url, auth_type,
...) lives in ``_PROVIDER_MAP`` in code. A JSON entry pointing at an
unregistered kind is a release-blocker, so we pin the invariant in
a test — broken JSON fails CI loudly instead of mid-turn at runtime.
"""

from __future__ import annotations

from valuz_agent.modules.providers.service import _PROVIDER_MAP
from valuz_agent.seeds._io import load_provider_seeds


def test_should_parse_bundled_providers_json() -> None:
    seed = load_provider_seeds()
    assert seed.schema_version >= 1
    assert seed.providers, "providers.json must declare at least one built-in"


def test_every_seed_entry_kind_has_a_registered_descriptor() -> None:
    seed = load_provider_seeds()
    unknown = [p for p in seed.providers if p.provider_kind not in _PROVIDER_MAP]
    assert not unknown, (
        "providers.json references provider_kind(s) with no registered "
        f"ProviderDescriptor: {[(p.id, p.provider_kind) for p in unknown]}. "
        "Either register the descriptor in modules.providers.service or "
        "correct the JSON."
    )


def test_seed_ids_are_unique() -> None:
    """Two entries with the same id would have the latter silently win
    on insert order — pin uniqueness so the JSON edit surface stays
    explicit."""
    seed = load_provider_seeds()
    ids = [p.id for p in seed.providers]
    assert len(ids) == len(set(ids)), f"duplicate id in providers.json: {ids}"
