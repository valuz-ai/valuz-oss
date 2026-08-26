"""Port: factory model defaults (runtime / model / provider / effort).

These are the values used when a user has never explicitly chosen — new-agent
pre-selection, quick chat, and the last fallback of ``model_resolver``. They
were previously hardcoded literals scattered across the codebase; this port
makes them one replaceable seam:

- OSS binds ``SettingsModelDefaults``: reads the ``Settings`` factory fields
  (``default_runtime`` / ``default_model`` / ``default_provider_id`` /
  ``default_effort``), so a distribution build can override via plain env
  (``VALUZ_DEFAULT_RUNTIME=...``) or its startup path.
- The commercial overlay may bind an implementation that additionally layers
  cloud-delivered per-distribution defaults on top (cached per owner).

The port answers "factory default" only — the user's own Settings KV
(``model.default_*``) is resolved *above* this port by
``modules/settings/preferences``; an explicit user choice always wins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ModelDefaults:
    """One resolved set of factory defaults. Fields are never None except
    ``default_provider_id`` — absence of a provider means "let the provider
    seeds / ``is_default`` row decide"."""

    default_runtime: str
    default_model: str
    default_provider_id: str | None
    default_effort: str


class ModelDefaultsPort(Protocol):
    async def get(self, user_id: str | None = None) -> ModelDefaults:
        """Return the factory defaults for *user_id*.

        ``user_id`` lets overlay implementations key cloud-delivered defaults
        per owner (multi-account desktops); OSS ignores it."""
        ...


class SettingsModelDefaults:
    """OSS default implementation — reads the process ``Settings`` fields."""

    async def get(self, user_id: str | None = None) -> ModelDefaults:
        from valuz_agent.infra.config import settings

        return ModelDefaults(
            default_runtime=settings.default_runtime,
            default_model=settings.default_model,
            default_provider_id=settings.default_provider_id,
            default_effort=settings.default_effort,
        )


__all__ = ["ModelDefaults", "ModelDefaultsPort", "SettingsModelDefaults"]
