"""Market fallback for a pack's ``bundled`` skill dependencies.

The packaged ``template_skills/`` tree is retired
(docs/design/builtin-resources in the commercial repo, §5.4): a built-in
pack's ``source: "bundled"`` skills live on the market as their only source.
``materialize_template_skills`` still lands anything the packaged tree does
carry (transition builds); whatever is left over resolves here — fetched from
the market index by slug and installed through the ordinary skill URL-import
pipeline, exactly like a market team template's ``source: "url"``
dependencies.

Installed skills land in the *user* library (a market install), not the
official root — the official root remains reserved for the packaged baseline.
Failures are logged and skipped: a missing optional skill must not sink the
pack import (same stance as ``materialize_template_skills``).
"""

from __future__ import annotations

import logging

from valuz_agent.i18n import get_locale
from valuz_agent.infra.config import settings
from valuz_agent.modules.marketplace.market_index import (
    MarketIndexClient,
    MarketIndexUnavailableError,
)

logger = logging.getLogger(__name__)


def _index_client() -> MarketIndexClient:
    # Deliberately NOT process-cached: this path runs rarely (a pack import
    # with retired bundled skills), and a cached client would pin an httpx
    # AsyncClient to whatever event loop first touched it.
    return MarketIndexClient(
        settings.marketplace_index_base_url or None, settings.marketplace_index_channel
    )


async def install_missing_bundled_skills(user_id: str, slugs: list[str]) -> list[str]:
    """Install ``slugs`` from the market; returns the slugs that landed."""
    if not slugs:
        return []
    from valuz_agent.api.deps import get_skill_service_for_user
    from valuz_agent.modules.skills.errors import SkillImportFailed
    from valuz_agent.modules.skills.models import SkillImportUrlConfirmRequest

    installed: list[str] = []
    index = _index_client()
    for slug in slugs:
        item_id = f"market:skill:{slug}"
        try:
            raw = await index.item_detail(item_id, get_locale())
        except MarketIndexUnavailableError:
            logger.warning("market unavailable; bundled skill %s not installed", slug)
            continue
        except Exception:  # noqa: BLE001 — a fallback lookup must never sink the import
            logger.exception("market lookup failed for bundled skill %s", slug)
            continue
        manifest = raw.get("install_manifest") if isinstance(raw, dict) else None
        download_url = (manifest or {}).get("download_url")
        if not download_url:
            logger.warning("market has no download for bundled skill %s; skipped", slug)
            continue
        try:
            async for skills in get_skill_service_for_user(user_id):
                existing = await skills.get_indexed_skill(user_id, slug)
                if existing is not None:
                    installed.append(slug)
                    break
                preview = await skills.import_url_preview(user_id, str(download_url))
                name = slug
                if preview.name_conflict and preview.suggested_name:
                    name = preview.suggested_name
                await skills.confirm_url_import(
                    user_id,
                    SkillImportUrlConfirmRequest(preview_id=preview.preview_id, name=name),
                )
                installed.append(slug)
                logger.info("installed bundled pack skill %s from market", slug)
        except SkillImportFailed:
            logger.warning("market import failed for bundled skill %s; skipped", slug)
        except Exception:  # noqa: BLE001 — one bad skill must not sink the import
            logger.exception("unexpected failure installing bundled skill %s", slug)
    return installed


__all__ = ["install_missing_bundled_skills"]
