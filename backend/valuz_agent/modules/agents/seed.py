"""Seed official Agents on first boot.

Pure-insert contract: idempotent upsert by slug. Existing rows are
updated to match the current canonical definition so official agents
stay fresh across upgrades. User-customised Project Member rows are
never touched here — they reference agents by slug only.

Wire into ``seeds/__init__.py:seed_all`` after the providers seeder.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.modules.agents.datastore import AgentDatastore
from valuz_agent.modules.agents.models import AgentRow

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Official agent definitions loaded from data file
# ---------------------------------------------------------------------------

_SEED_PATH = Path(__file__).resolve().parent.parent.parent / "resources" / "agents" / "seed.json"

# The always-present base agent backing the「新对话」default (09-assistant).
# Editable, not deletable; its brain mirrors the Settings global model default.
DEFAULT_ASSISTANT_SLUG = "default-assistant"

# The onboarding-created general assistant (Valuz 小助手). OSS ships an empty
# seed.json, so on real installs THIS is the de-facto default assistant; the
# slug lives here (next to DEFAULT_ASSISTANT_SLUG) so every consumer that
# needs "the default agent" resolves the same well-known candidates.
VALUZ_HELPER_SLUG = "valuz-helper"


def _load_agent_definitions() -> list[dict[str, Any]]:
    """Load official agent definitions from the bundled JSON seed file."""
    with _SEED_PATH.open(encoding="utf-8") as f:
        return cast(list[dict[str, Any]], json.load(f))


_OFFICIAL_AGENTS: list[dict[str, Any]] = _load_agent_definitions()


async def seed_official_agents(db: AsyncSession) -> None:
    from valuz_agent.infra.local_identity import resolve_local_user_id

    _owner = resolve_local_user_id()
    """Insert official Agent rows if absent.

    Insert-if-absent (not upsert): agents are now user-editable, so a boot
    must NOT clobber edits the user made to a seeded agent. New official
    slugs still get added on upgrade.

    Seeded agents are no longer locked: users can edit and delete them like
    any other agent. ``source="official"`` is preserved as provenance metadata
    only; the ``readonly`` / ``deletable`` flags are kept on the column so older
    rows continue to type-check, but new seeds default to fully mutable.
    """
    ds = AgentDatastore(db)
    created = 0
    for defn in _OFFICIAL_AGENTS:
        if await ds.get_agent(_owner, defn["slug"]) is not None:
            continue
        row = AgentRow(
            slug=defn["slug"],
            name=defn["name"],
            description=defn["description"],
            instructions=defn["instructions"],
            runtime=defn["runtime"],
            model=defn["model"],
            # Optional per-agent reasoning-effort default. ``None`` (key
            # absent) leaves it unset so the runtime SDK picks; the bundled
            # seed.json now ships ``"effort": "high"`` for every official
            # agent so they don't surface as ``—`` in the UI.
            effort=defn.get("effort"),
            skills=defn["skills"],
            connector_types=defn["connector_types"],
            source=defn.get("source", "official"),
            # Seeds are mutable by default; a seed may opt out of deletion
            # (the 默认助手 sets ``deletable=false`` — always-present base agent).
            readonly=defn.get("readonly", False),
            deletable=defn.get("deletable", True),
        )
        await ds.create(_owner, row)
        created += 1

    logger.info("seed_official_agents: inserted %d new official agent(s)", created)
