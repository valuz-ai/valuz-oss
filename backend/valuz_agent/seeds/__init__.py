"""First-boot seeders for the valuz host schema.

Seeders run after ``alembic upgrade head`` (post-V2) so every table the
seeder touches is guaranteed to exist with its current column shape.
Each seeder is **pure-insert**: ``if not exists: insert``. They never
update existing rows, never delete, never reconcile — that's the
explicit contract that lets seeds stay safe to re-run on every boot
without trampling user customisations.

If a built-in row's *defaults* change (e.g. a built-in provider's
``default_model`` needs to follow upstream), express that through a
new alembic revision that rewrites the existing row, not through the
seeder.

Add a new seeder by:
1. Writing ``seed_<topic>(ds_or_db)`` in ``seeds/<topic>.py``.
2. Calling it from ``seed_all`` below with whatever it needs.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.agents.seed import seed_official_agents
from valuz_agent.seeds.connectors import seed_builtin_connectors
from valuz_agent.seeds.providers import seed_builtin_providers


async def seed_all(db: AsyncSession, *, user_id: str) -> None:
    """Run every first-boot seeder. Safe to re-run on every startup.

    NOTE: ``seed.json`` only holds the structural system agent (the
    ``default-assistant`` that backs new conversations). The preset-team roles
    are NOT seeded — they enter the library only when the user picks a team in
    onboarding (created on deploy) or creates their own. So a user who skips
    onboarding sees just the default assistant, not a wall of roles.

    Built-in **model providers** are NOT seeded. OSS's contract is that you
    configure a model channel (via onboarding / Settings → Models) before use,
    so a fresh install legitimately starts with zero provider rows — the keyless
    default this used to seed only masked that contract (and, under a multi-user
    overlay, bred rows owned by a never-authenticating id plus a well-known-id
    primary-key collision). Built-ins are surfaced as virtual templates and
    materialized on configure (see ``modules.providers.service.list_providers``).
    ``seed_builtin_providers`` is retained for the admin reset path
    (``reset_providers`` / the ``reset-providers`` CLI).
    """
    await seed_official_agents(db, user_id)
    await seed_builtin_connectors(db, user_id=user_id)


__all__ = [
    "seed_all",
    "seed_builtin_connectors",
    "seed_builtin_providers",
    "seed_official_agents",
]
