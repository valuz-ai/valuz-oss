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


def test_oauth_subscription_rows_seed_null_model_ids() -> None:
    """Subscription rows must NOT snapshot the recommended list.

    ``model_ids IS NULL`` keeps ``_resolve_model_options`` falling back to
    the live descriptor (hydrated from ``subscription_models.json`` each
    boot), so a catalog bump reaches existing installs without a
    migration. A snapshot here would freeze the picker at seed time —
    the exact bug the former ``0002_null_subscription_model_snapshots``
    migration (since folded into the baseline) had to clean up.
    """
    from valuz_agent.seeds.providers import _row_for

    seed = load_provider_seeds()
    oauth_entries = [
        p for p in seed.providers if _PROVIDER_MAP[p.provider_kind].auth_type == "oauth"
    ]
    assert oauth_entries, "expected at least one OAuth subscription seed entry"
    for entry in oauth_entries:
        row = _row_for(entry)
        assert row.model_ids is None, (
            f"{entry.id}: subscription rows must seed model_ids=NULL "
            "(live recommended catalog), got a snapshot"
        )


def test_api_key_rows_still_seed_model_snapshot() -> None:
    """api_key kinds keep the seed snapshot — for them ``model_ids`` is a
    real cache that ``/v1/models`` discovery later replaces, and the
    pre-key-entry placeholder list keeps the picker non-empty."""
    from valuz_agent.seeds.providers import _row_for

    seed = load_provider_seeds()
    for entry in seed.providers:
        descriptor = _PROVIDER_MAP[entry.provider_kind]
        if descriptor.auth_type == "oauth" or not descriptor.model_options:
            continue
        row = _row_for(entry)
        assert row.model_ids is not None, f"{entry.id}: api_key snapshot unexpectedly dropped"
