"""Seed the built-in provider rows on first boot.

Pure-insert contract: if the provider id already exists in
``valuz_provider`` the seeder skips that row entirely. Renames,
default-model bumps, auth_type changes, and credential-source
migrations are out of scope here — they must land as alembic
revisions that ``UPDATE`` the row by id, so the change is versioned
and reversible.

The catalog of which IDs to seed lives in
``valuz_agent/resources/seeds/providers.json`` (read via
``seeds._io.load_provider_seeds``). The Python-side
``ProviderDescriptor`` map in ``modules.providers.service`` still
owns the *rich* metadata each kind needs at runtime (default_model,
model_options, base_url, supports_protocol_selection, …) — JSON
only declares the minimal "which IDs exist + which kind they bind
to" set. Each JSON entry's ``provider_kind`` must resolve to a
registered descriptor; an unknown kind is a release-blocker and is
surfaced via a ``ValueError`` at seed time.

Accepts a ``ProviderDatastore`` rather than a raw ``Session`` so the
admin reset path (``reset_providers``) can call the same seeder
without an extra adapter wrapper.
"""

from __future__ import annotations

import json
import logging

from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.models import ProviderRow
from valuz_agent.modules.providers.service import _PROVIDER_MAP
from valuz_agent.seeds._io import ProviderSeedEntry, load_provider_seeds

logger = logging.getLogger(__name__)


def _row_for(entry: ProviderSeedEntry) -> ProviderRow:
    """Translate a JSON seed entry into a ``ProviderRow``.

    Fills the runtime-derived fields (base_url, default_model,
    model_ids, auth_type) from the registered ``ProviderDescriptor``
    so JSON only has to declare the stable identity of the row.
    """
    descriptor = _PROVIDER_MAP.get(entry.provider_kind)
    if descriptor is None:
        raise ValueError(
            f"providers.json references unknown provider_kind "
            f"{entry.provider_kind!r}; register a ProviderDescriptor in "
            f"modules.providers.service or correct the JSON."
        )

    model_ids = json.dumps(list(descriptor.model_options)) if descriptor.model_options else None
    auth_type = descriptor.auth_type or "api_key"

    return ProviderRow(
        id=entry.id,
        name=entry.name,
        provider_kind=entry.provider_kind,
        source=entry.source,
        credential_source="none",
        base_url=descriptor.default_base_url,
        default_model=descriptor.default_model,
        model_ids=model_ids,
        account_provider_id=None,
        enabled=True,
        is_default=(entry.id == "ch-anthropic"),
        deletable=False,
        test_status="never",
        auth_type=auth_type,
    )


async def seed_builtin_providers(ds: ProviderDatastore) -> None:
    """Insert any missing built-in provider rows. Safe to re-run."""
    seed = load_provider_seeds()
    existing_ids = {r.id for r in await ds.list_providers()}
    inserted = 0
    for entry in seed.providers:
        if entry.id in existing_ids:
            continue
        await ds.create(_row_for(entry))
        inserted += 1

    if inserted:
        logger.info(
            "seed_builtin_providers: inserted %d new provider row(s) from "
            "providers.json (schema_version=%d)",
            inserted,
            seed.schema_version,
        )
