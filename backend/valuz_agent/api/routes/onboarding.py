"""Onboarding helpers — wire up the first-run sample project / assistant.

POST /v1/onboarding/example-project
  Body: { "team_id": "general" | "investment" | "product" }
  Creates the example project directory, binds it as a project, and
  CREATES the chosen team's agents (as the user's own) deployed into that
  project. Also ensures the Valuz Helper exists in the library so the
  user always has an agent ready for the no-project quick chat.

POST /v1/onboarding/assistant
  Creates (or reuses) only the Valuz Helper — backs TeamStep's "no team
  for now" choice. No project is created.

Team roles are created on demand here — NOT seeded globally — so a user who
skips onboarding has an empty library. All operations are idempotent.
"""

from __future__ import annotations

import logging
from typing import Literal, TypedDict, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from valuz_agent.generated.i18n_keys import I18nKey
from valuz_agent.i18n import t
from valuz_agent.modules.agents.seed import VALUZ_HELPER_SLUG


def _ti(key: str) -> str:
    """Resolve a dynamically-built i18n key.

    ``t()`` declares ``key: I18nKey`` (a closed Literal union) so mypy can't
    accept f-string keys. The team roster + helper text use stable, codegen'd
    keys built from ``_TEAM_ROLE_KEYS`` — equally safe at runtime, just not
    statically narrowable. This helper localizes the cast to one spot.
    """
    return t(cast(I18nKey, key))


from valuz_agent.infra.db import async_unit_of_work
from valuz_agent.infra.eventbus import event_bus
from valuz_agent.infra.fs_registry import fs_registry
from valuz_agent.modules.agents.service import AgentService
from valuz_agent.modules.connectors.datastore import ConnectorDatastore
from valuz_agent.modules.connectors.service import ConnectorService
from valuz_agent.modules.projects.datastore import ProjectDatastore
from valuz_agent.modules.projects.service import ProjectService
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.service import _resolve_model_options
from valuz_agent.modules.settings.preferences import (
    get_default_effort,
    get_default_model,
    get_default_provider_id,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])

# ---------------------------------------------------------------------------
# Team rosters — the agents created (as the user's own) when a preset team is
# chosen. These live here, NOT in the official-agent seed, so the agent library
# is empty until onboarding (or the user) puts something in it.
#
# Each role's display name / description / detailed instructions are resolved
# from i18n at create time, using the user's currently configured UI locale
# (pushed at startup from preferences.default_locale). Agents are "baked" in
# the user's language at create time — switching locales afterwards won't
# retro-translate already-deployed agents.
# ---------------------------------------------------------------------------

TeamId = Literal["general", "investment", "product"]


class RoleDef(TypedDict):
    name: str
    description: str
    instructions: str


# Role keys per team. Drives both the i18n lookup
# (``onboarding.roles.<team>.<role>.{name,description,instructions}``) and the
# deploy order — the first role becomes the team lead.
_TEAM_ROLE_KEYS: dict[TeamId, list[str]] = {
    "general": ["researcher", "writer", "reviewer", "archivist"],
    "investment": ["analyst", "modeler", "tracker", "compliance"],
    "product": ["pm", "designer", "engineer", "qa"],
}


def _get_team_roster(team_id: TeamId) -> list[RoleDef]:
    """Resolve a team's roles into localized RoleDefs.

    Reads name / description / instructions from the i18n catalog under
    ``onboarding.roles.<team>.<role>.*``. Each entry on the returned list is
    ready to feed into ``AgentService.create_blank_agent``.
    """
    base = f"onboarding.roles.{team_id}"
    return [
        {
            "name": _ti(f"{base}.{role_key}.name"),
            "description": _ti(f"{base}.{role_key}.description"),
            "instructions": _ti(f"{base}.{role_key}.instructions"),
        }
        for role_key in _TEAM_ROLE_KEYS[team_id]
    ]


def _resolve_project_name() -> str:
    """Localized name for the auto-created example project."""
    return t("onboarding.exampleProjectName")


# ---------------------------------------------------------------------------
# Deploy-target resolver
# ---------------------------------------------------------------------------


async def _resolve_deploy_target(db) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Return ``(provider_id, model)`` to assign to onboarding's deployed agents.

    Resolution order (see endpoint comment for rationale):
      1. user's explicit defaults (``model.default_provider_id`` +
         ``model.default_model``) — set when the user picks a model in the
         ConnectStep or in Settings → Models.
      2. fallback: first enabled provider row, with that row's
         ``default_model`` (or the first id from its discovered options).
         This keeps the deploy aligned with what the user has actually
         configured — Claude, OpenAI, GLM, anything — instead of forcing a
         hard-coded model id that wouldn't match a non-Claude setup.
      3. 422 when no provider is configured at all.
    """
    default_provider_id = await get_default_provider_id(db)
    default_model = await get_default_model(db)
    if default_provider_id and default_model:
        return default_provider_id, default_model

    # Fallback to the first enabled provider. Order is by created_at so we
    # pick the user's earliest deliberate choice, not whatever the seeder
    # happened to insert last.
    rows = await ProviderDatastore(db).list_providers()
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
    logger.warning(
        "onboarding: no explicit default set; falling back to provider %s (%s) with model %r",
        row.id,
        row.name,
        model,
    )
    return row.id, model


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


async def _ensure_valuz_helper(db) -> str:  # type: ignore[no-untyped-def]
    """Idempotently create the Valuz Helper in the user's agent library.

    Returns its slug. Reuses the existing one on re-run (no model resolution
    needed then). On first creation, model / provider / effort follow the
    user's global defaults — the same resolver the team deploy uses, so a
    user with no model configured hits the same 422 guard.
    """
    connector_svc = ConnectorService(
        datastore=ConnectorDatastore(db),
        secrets=None,  # type: ignore[arg-type]
    )
    agent_svc = AgentService(db=db, connector_service=connector_svc)

    for existing in await agent_svc.list_agents():
        if existing.slug == _VALUZ_HELPER_SLUG:
            return _VALUZ_HELPER_SLUG

    provider_id, model = await _resolve_deploy_target(db)
    effort = await get_default_effort(db)
    await agent_svc.create_agent(
        {
            "slug": _VALUZ_HELPER_SLUG,
            "name": t("onboarding.valuzHelper.name"),
            "description": t("onboarding.valuzHelper.description"),
            "instructions": t("onboarding.valuzHelper.instructions"),
            "model": model,
            "provider_id": provider_id,
            "effort": effort,
            "skills": [_VALUZ_HELPER_SKILL],
            "avatar": _VALUZ_HELPER_AVATAR,
        }
    )
    logger.info("onboarding: created Valuz Helper (%s)", _VALUZ_HELPER_SLUG)
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
) -> ExampleProjectResponse:
    """Create (or reuse) the onboarding example project and its team's agents.

    1. Resolve the example project directory and bind it as a project.
    2. If the project is freshly created, create each of the team's roles as a
       user-owned agent (``source=custom``) deployed into that project. If the
       project already existed (re-run), skip creation so agents aren't
       duplicated. Either way the agent library stays empty for users who skip
       onboarding.
    """
    root_path = fs_registry.example_project_dir()
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
        connector_svc = ConnectorService(
            datastore=ConnectorDatastore(db),
            secrets=None,  # type: ignore[arg-type]
        )
        agent_svc = AgentService(db=db, connector_service=connector_svc)  # type: ignore[arg-type]

        # Step 1: create or reuse the project.
        created_new = False
        try:
            project = await project_svc.create_project(
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
                existing = await ProjectDatastore(db).get_by_root_path(root_path_str)
                if existing is None:
                    raise HTTPException(
                        status_code=500,
                        detail=f"path already bound but project not found: {root_path_str}",
                    ) from exc
                project_id = existing.id
                logger.info(
                    "onboarding: reusing existing example project %s "
                    "(skipping agent creation)",
                    project_id,
                )
            else:
                raise HTTPException(status_code=422, detail=msg) from exc

        # Step 2: only on a fresh project, create the team's agents (as the
        # user's own) and deploy them into it. A reused project already has
        # them — re-creating would duplicate.
        created = 0
        if created_new:
            # Resolve the model + provider to deploy with. Three-tier:
            #   1. user's explicit defaults (set in ConnectStep / Settings)
            #   2. first enabled provider's first model — picks whatever the
            #      user actually wired up (GPT / DeepSeek / GLM / …),
            #      avoiding the old hard-coded Claude id that silently broke
            #      non-Claude users
            #   3. 422 if no provider is configured at all (the frontend
            #      TeamStep guard banner catches this first; this is the
            #      authoritative fallback when the guard is bypassed)
            default_provider_id, default_model = await _resolve_deploy_target(db)
            logger.info(
                "onboarding: deploying team %r with model=%r provider=%r",
                body.team_id,
                default_model,
                default_provider_id,
            )
            for role in _get_team_roster(body.team_id):
                try:
                    await agent_svc.create_blank_agent(
                        project_id=project_id,
                        agent_slug=None,
                        name=role["name"],
                        description=role["description"],
                        instructions=role["instructions"],
                        model=default_model,
                        provider_id=default_provider_id,
                    )
                    created += 1
                except Exception:  # noqa: BLE001 — one bad role shouldn't sink the rest
                    logger.exception(
                        "onboarding: failed to create role %r for team %r",
                        role["name"],
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
        await _ensure_valuz_helper(db)

    return ExampleProjectResponse(
        project_id=project_id,
        project_name=project_name,
    )


@router.post("/assistant", response_model=AssistantResponse)
async def create_assistant() -> AssistantResponse:
    """Create (or reuse) only the Valuz Helper in the user's library.

    Backs TeamStep's "no team for now" choice — no project, just a ready-to-chat
    general assistant for the quick-chat surface. Idempotent.
    """
    async with async_unit_of_work() as db:
        slug = await _ensure_valuz_helper(db)
    return AssistantResponse(agent_slug=slug)
