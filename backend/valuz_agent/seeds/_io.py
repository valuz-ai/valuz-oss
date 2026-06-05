"""Bundled seed-file loader + Pydantic schemas.

Each business module owns one JSON file under
``valuz_agent/resources/seeds/`` (one file per module — see
``providers.json``). Loading a seed file always goes through the
matching ``load_*_seeds()`` here so:

1. JSON gets parsed once and validated against a strict Pydantic
   model — typos in keys, missing required fields, or wrong types
   fail loudly at boot rather than silently inserting bogus rows.
2. The file path resolution is centralised: production reads from
   the bundled ``resources/seeds/`` dir; tests can monkeypatch
   ``BUNDLED_SEEDS_DIR`` to load fixture variants.
3. The ``schema_version`` envelope is the hook the future remote
   delivery path will use — local bundled file is the offline
   fallback; a remote response that returns a higher
   ``schema_version`` for the same module replaces the bundled one
   at runtime (not implemented yet, deliberately — see release
   notes for the remote-delivery follow-up).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

BUNDLED_SEEDS_DIR: Path = Path(__file__).resolve().parents[1] / "resources" / "seeds"


class ProviderSeedEntry(BaseModel):
    """One built-in provider row, exactly as it appears in ``providers.json``.

    ``provider_kind`` must match a registered ``ProviderDescriptor``
    in ``modules.providers.service``; the seeder enforces this so a
    typo in the JSON surfaces as a boot error, not as a silent
    ``no descriptor for ...`` 500 mid-turn.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    provider_kind: str = Field(min_length=1)
    usage: str = ""
    source: str = "builtin"


class ProviderSeedFile(BaseModel):
    """Envelope for ``providers.json``. ``schema_version`` is single-
    file scoped: bumping it only affects providers-seed handling, not
    other ``resources/seeds/*.json`` files. That's the explicit
    business-axis split we agreed on so a connector-seed change
    doesn't have to bump every other seeder's version.
    """

    model_config = ConfigDict(extra="ignore")  # tolerate ``_comment`` etc.

    schema_version: int = Field(ge=1)
    providers: list[ProviderSeedEntry]


def load_provider_seeds(seeds_dir: Path | None = None) -> ProviderSeedFile:
    """Read + validate ``providers.json``. Raises ``pydantic.ValidationError``
    on a malformed file — that's intentional: a broken bundled seed
    file is a release-blocker, not something to silently fall back on.
    """
    path = (seeds_dir or BUNDLED_SEEDS_DIR) / "providers.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return ProviderSeedFile.model_validate(raw)


__all__ = [
    "BUNDLED_SEEDS_DIR",
    "ProviderSeedEntry",
    "ProviderSeedFile",
    "load_provider_seeds",
]
