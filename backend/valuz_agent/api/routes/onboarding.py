"""Onboarding helpers — wire up the first-run sample project / assistant.

POST /v1/onboarding/example-project
  Body: { "team_id": "content" | "investment" | "development-engineering" }
  Creates the example project directory, binds it as a project, imports the
  chosen recommended pack's agents into the user's library, and deploys them
  into that project. Also ensures the Valuz Helper exists in the library so the
  user always has an agent ready for the no-project quick chat.

POST /v1/onboarding/assistant
  Creates (or reuses) only the Valuz Helper — backs TeamStep's "no team
  for now" choice. No project is created.

Team roles are created on demand here — NOT seeded globally — so a user who
skips onboarding has an empty library. All operations are idempotent.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from valuz_agent.api.deps import get_current_user_id
from valuz_agent.i18n import t
from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.agent_packs.loader import load_builtin_pack
from valuz_agent.modules.agent_packs.service import AgentPackService
from valuz_agent.modules.agents.seed import VALUZ_HELPER_SLUG
from valuz_agent.modules.agents.service import AgentService, MemberAlreadyExistsError
from valuz_agent.modules.connectors.service import ConnectorService
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.service import (
    _resolve_model_options,
    derive_runtime_provider,
)
from valuz_agent.modules.settings.preferences import (
    get_default_effort,
    get_default_model,
    get_default_provider_id,
    get_default_runtime,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])

# ---------------------------------------------------------------------------
# Recommended teams — onboarding surfaces a subset of the built-in Agent Packs
# (内容 / 投研 / 产研). Picking one imports that pack's agents into the user's
# library and deploys them into the example project. The pack manifests
# (``resources/agent_packs/<id>/manifest.json``) are the single source of truth
# for the rosters and their localized text — onboarding no longer carries its
# own roster i18n. The agents are still created on demand (not seeded globally),
# so a user who skips onboarding keeps an empty library.
# ---------------------------------------------------------------------------

TeamId = Literal["content", "investment", "development-engineering"]


def _resolve_project_name() -> str:
    """Localized name for the auto-created example project."""
    return t("onboarding.exampleProjectName")


# ---------------------------------------------------------------------------
# Deploy-target resolver
# ---------------------------------------------------------------------------


async def _resolve_deploy_target(
    db,
    user_id: str,
) -> tuple[str, str, str]:  # type: ignore[no-untyped-def]
    """Return ``(runtime, provider_id, model)`` to assign to onboarding's deployed agents.

    Resolution order (see endpoint comment for rationale):
      1. user's explicit defaults (``model.default_runtime`` +
         ``model.default_provider_id`` + ``model.default_model``) — all set
         together when the user picks a model in the ConnectStep or in
         Settings → Models. The runtime rides along so the deployed agent runs
         on the runtime the user actually chose, not a hard-coded
         ``claude_agent``.
      2. fallback: first enabled provider row, with that row's
         ``default_model`` (or the first id from its discovered options) and a
         runtime *derived from that provider's kind* so the
         ``(runtime, provider, model)`` triple stays internally consistent —
         Claude, OpenAI, GLM, anything — instead of forcing a hard-coded
         runtime/model that wouldn't match a non-Claude setup.
      3. 422 when no provider is configured at all.
    """
    default_provider_id = await get_default_provider_id(db, user_id=user_id)
    default_model = await get_default_model(db, user_id=user_id)
    if default_provider_id and default_model:
        # ConnectStep / Settings → Models always persist the runtime alongside
        # the provider+model, so the user's chosen runtime is authoritative
        # here. Without this the team's agents silently landed on claude_agent.
        runtime = await get_default_runtime(db, user_id=user_id)
        return runtime, default_provider_id, default_model

    # Fallback to the first enabled provider. Order is by created_at so we
    # pick the user's earliest deliberate choice, not whatever the seeder
    # happened to insert last.
    rows = await ProviderDatastore(db).list_providers(user_id)
    enabled = [r for r in rows if r.enabled]
    if not enabled:
        raise HTTPException(
            status_code=422,
            detail=(
                "no model channel configured — connect at least one model "
                "channel before deploying a team"
            ),
        )
    row = enabled[0]
    if row.default_model:
        model = row.default_model
    else:
        options = _resolve_model_options(row)
        if not options:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"provider {row.name!r} has no models available — pick a "
                    "default model in its edit dialog first"
                ),
            )
        model = options[0]
    # Derive a runtime compatible with the fallback provider's kind rather than
    # blindly using claude_agent (which would mismatch an OpenAI/Gemini channel).
    runtime = derive_runtime_provider(row.provider_kind)
    logger.warning(
        "onboarding: no explicit default set; falling back to provider %s (%s) "
        "with model %r on runtime %r",
        row.id,
        row.name,
        model,
        runtime,
    )
    return runtime, row.id, model


# ---------------------------------------------------------------------------
# Valuz Helper — the general assistant that backs the no-project quick chat
#
# Slug / skill / avatar are stable technical identifiers; name / description /
# instructions are localized via i18n at create time. Like the team rosters,
# the agent is baked in the user's current UI language and won't retranslate
# on locale switch.
# ---------------------------------------------------------------------------

_VALUZ_HELPER_SLUG = VALUZ_HELPER_SLUG
_VALUZ_HELPER_SKILL = "valuz-handbook"
_VALUZ_HELPER_AVATAR = "bot"
# Connectors the default assistant ships bound to (slugs from the connector
# catalog): the two Valuz research connectors. Firecrawl is deliberately not a
# default — valuz-search already covers search/scraping needs, and it remains
# in the catalog for users to add manually.
# A bound slug only resolves to a live MCP server once that connector is
# installed + enabled, so {@link _ensure_default_connectors} installs these from
# the catalog alongside the helper — they show up under「已添加」, not just bound
# on the agent.
_VALUZ_HELPER_CONNECTORS = ["valuz-search", "valuz-stock"]


async def _ensure_default_connectors(user_id: str, db) -> None:  # type: ignore[no-untyped-def]
    """Install the default assistant's connectors so they land under「已添加」.

    Idempotent: skips any slug that already has a connector row. Installs via the
    same path the connectors UI / MCP tool use (``create_connector``), so each
    one gets a real probe — a no-auth connector like Firecrawl connects right
    away, and an OAuth connector (Valuz) lands as ``pending_auth`` until the user
    logs in. One bad connector never sinks onboarding (per-slug try/except).
    """
    from valuz_agent.api.routes.connectors import (  # local: avoid route import cycle
        CONNECTOR_DIRECTORY,
        CreateConnectorRequest,
        create_connector,
    )

    def _text(v: object) -> str:
        # Catalog display_name / description may be a plain string or an
        # ``{"zh-CN": ..., "en-US": ...}`` i18n object.
        if isinstance(v, dict):
            return str(v.get("zh-CN") or v.get("en-US") or next(iter(v.values()), ""))
        return str(v or "")

    svc = ConnectorService.with_defaults(db)
    installed = {v.slug for v in await svc.list_connectors(user_id)}
    by_slug = {c["slug"]: c for c in CONNECTOR_DIRECTORY}

    for slug in _VALUZ_HELPER_CONNECTORS:
        if slug in installed:
            continue
        cat = by_slug.get(slug)
        if cat is None:
            logger.warning("onboarding: default connector %r missing from catalog", slug)
            continue
        try:
            await create_connector(
                body=CreateConnectorRequest(
                    slug=slug,
                    display_name=_text(cat.get("display_name")) or slug,
                    description=_text(cat.get("description")) or None,
                    transport=cat.get("transport", "http"),
                    url=cat.get("url") or None,
                    auth_type=cat.get("auth_type", "oauth"),
                    connector_type="recommended",
                    command=cat.get("command"),
                    args=cat.get("args", []),
                    working_dir=cat.get("working_dir"),
                ),
                svc=svc,
                user_id=user_id,
            )
            logger.info("onboarding: installed default connector %r", slug)
        except Exception:  # noqa: BLE001 — one bad connector shouldn't sink onboarding
            logger.exception("onboarding: failed to install default connector %r", slug)


async def _ensure_valuz_helper(user_id: str, db) -> str:  # type: ignore[no-untyped-def]
    """Idempotently create the Valuz Helper in the user's agent library.

    Returns its slug. Reuses the existing one on re-run (no model resolution
    needed then). On first creation, runtime / model / provider / effort follow
    the user's global defaults — the same resolver the team deploy uses, so a
    user with no model configured hits the same 422 guard.
    """
    # Install the helper's default connectors first so they exist under「已添加」
    # and the binding below resolves. Idempotent + best-effort.
    await _ensure_default_connectors(user_id, db)

    connector_svc = ConnectorService.with_defaults(db)
    agent_svc = AgentService(db=db, connector_service=connector_svc)

    for existing in await agent_svc.list_agents(user_id):
        if existing.slug == _VALUZ_HELPER_SLUG:
            return _VALUZ_HELPER_SLUG

    # The Helper should always exist after onboarding — including the skip
    # path, where the user may not have configured a model channel yet. Unlike
    # the multi-agent team deploy (which hard-requires a channel up front), fall
    # back to creating it *unpinned* when none is configured: no provider/model
    # is snapshotted, so the model resolver fills in the user's default (or the
    # global default) at session time and the user can set the model later.
    try:
            runtime, provider_id, model = await _resolve_deploy_target(db, user_id)
    except HTTPException:
        runtime, provider_id, model = "claude_agent", None, None
    effort = await get_default_effort(db, user_id=user_id)
    payload: dict[str, Any] = {
        "slug": _VALUZ_HELPER_SLUG,
        "name": t("onboarding.valuzHelper.name"),
        "description": t("onboarding.valuzHelper.description"),
        "instructions": t("onboarding.valuzHelper.instructions"),
        "runtime": runtime,
        "provider_id": provider_id,
        "effort": effort,
        "skills": [_VALUZ_HELPER_SKILL],
        "connector_types": _VALUZ_HELPER_CONNECTORS,
        "avatar": _VALUZ_HELPER_AVATAR,
    }
    # Omit ``model`` when unpinned so ``create_agent`` keeps its default and the
    # row isn't locked to a channel the user hasn't chosen yet.
    if model is not None:
        payload["model"] = model
    await agent_svc.create_agent(user_id, payload)
    logger.info(
        "onboarding: created Valuz Helper (%s, model=%s)", _VALUZ_HELPER_SLUG, model
    )
    return _VALUZ_HELPER_SLUG


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ExampleProjectRequest(BaseModel):
    team_id: TeamId


class ExampleProjectResponse(BaseModel):
    project_id: str
    project_name: str


class AssistantResponse(BaseModel):
    agent_slug: str


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/example-project", response_model=ExampleProjectResponse)
async def create_example_project(
    body: ExampleProjectRequest,
    user_id: str = Depends(get_current_user_id),
) -> ExampleProjectResponse:
    """Create (or reuse) the onboarding example project and its team's agents.

    1. Resolve the example project directory and bind it as a project.
    2. If the project is freshly created, create each of the team's roles as a
       user-owned agent (``source=custom``) deployed into that project. If the
       project already existed (re-run), skip creation so agents aren't
       duplicated. Either way the agent library stays empty for users who skip
       onboarding.
    """
    root_path = fs_registry.example_project_dir(user_id)
    # Match ProjectService.create_project's normalization (it stores the
    # resolved path) so the idempotent get_by_root_path lookup stays in sync —
    # on macOS /tmp resolves to /private/tmp.
    root_path_str = str(root_path.resolve())
    project_name = _resolve_project_name()

    async with async_unit_of_work() as db:
        project_svc = ProjectService(
            datastore=ProjectDatastore(db),
            event_bus=event_bus,
        )
        connector_svc = ConnectorService.with_defaults(db)
        agent_svc = AgentService(db=db, connector_service=connector_svc)  # type: ignore[arg-type]

        # Step 1: create or reuse the project.
        created_new = False
        try:
            project = await project_svc.create_project(
                user_id,
                name=project_name,
                root_path=root_path_str,
            )
            project_id = project.id
            created_new = True
            logger.info(
                "onboarding: created example project %s at %s",
                project_id,
                root_path_str,
            )
        except ValueError as exc:
            msg = str(exc)
            if "already bound" in msg:
                existing = await ProjectDatastore(db).get_by_root_path(user_id, root_path_str)
                if existing is None:
                    raise HTTPException(
                        status_code=500,
                        detail=f"path already bound but project not found: {root_path_str}",
                    ) from exc
                project_id = existing.id
                logger.info(
                    "onboarding: reusing existing example project %s (skipping agent creation)",
                    project_id,
                )
            else:
                raise HTTPException(status_code=422, detail=msg) from exc

        # Step 2: only on a fresh project, import the recommended pack's agents
        # into the user's library and deploy them into the project. A reused
        # project already has them — re-deploying would trip the dedup guard.
        created = 0
        if created_new:
            manifest = load_builtin_pack(body.team_id)
            if manifest is None:
                raise HTTPException(status_code=404, detail=f"unknown team: {body.team_id}")
            # Resolve the runtime + model + provider to deploy with. Three-tier:
            #   1. user's explicit defaults (set in ConnectStep / Settings) —
            #      runtime included, so the team runs on the runtime the user
            #      picked instead of a hard-coded claude_agent
            #   2. first enabled provider's first model + a runtime derived
            #      from that provider — picks whatever the user actually wired up
            #   3. 422 if no provider is configured at all (the TeamStep guard
            #      catches this first; this is the authoritative fallback)
            default_runtime, default_provider_id, default_model = await _resolve_deploy_target(
                db, user_id
            )
            effort = await get_default_effort(db, user_id=user_id)
            logger.info(
                "onboarding: importing team pack %r with runtime=%r model=%r provider=%r",
                body.team_id,
                default_runtime,
                default_model,
                default_provider_id,
            )
            # Import the pack into the library (creates the agents, materializes
            # bundled skills, de-dups by slug), then live-reference them into the
            # example project.
            pack_svc = AgentPackService(agent_svc)
            await pack_svc.import_manifest(
                user_id,
                manifest,
                runtime=default_runtime,
                provider_id=default_provider_id,
                model=default_model,
                effort=effort,
            )
            for agent in manifest.agents:
                try:
                    await agent_svc.deploy_agent(
                        user_id,
                        project_id,
                        source_agent_slug=agent.slug,
                    )
                    created += 1
                except MemberAlreadyExistsError:
                    pass
                except Exception:  # noqa: BLE001 — one bad role shouldn't sink the rest
                    logger.exception(
                        "onboarding: failed to deploy %r for team %r",
                        agent.slug,
                        body.team_id,
                    )

        logger.info(
            "onboarding: project %s — created %d agent(s) (new=%s)",
            project_id,
            created,
            created_new,
        )

        # Always ensure the Valuz Helper exists in the library so the
        # no-project quick chat has a ready default, even when the user
        # picked a project team.
        await _ensure_valuz_helper(user_id, db)

    return ExampleProjectResponse(
        project_id=project_id,
        project_name=project_name,
    )


@router.post("/assistant", response_model=AssistantResponse)
async def create_assistant(
    user_id: str = Depends(get_current_user_id),
) -> AssistantResponse:
    """Create (or reuse) only the Valuz Helper in the user's library.

    Backs TeamStep's "no team for now" choice — no project, just a ready-to-chat
    general assistant for the quick-chat surface. Idempotent.
    """
    async with async_unit_of_work() as db:
        slug = await _ensure_valuz_helper(user_id, db)
    return AssistantResponse(agent_slug=slug)
