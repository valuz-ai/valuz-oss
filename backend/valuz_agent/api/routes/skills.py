import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel

from valuz_agent.api.deps import (
    get_current_user_id,
    get_session_service,
    get_skill_service,
)
from valuz_agent.modules.sessions.schemas import SessionModelSelection
from valuz_agent.modules.sessions.service import SessionService
from valuz_agent.modules.skills.models import (
    SessionSkillImportConfirmRequest,
    SkillCopyRequest,
    SkillCreateChatStartResponse,
    SkillCreateRequest,
    SkillCreateStartRequest,
    SkillCreateStartResponse,
    SkillCreationContext,
    SkillDeletePreview,
    SkillDetail,
    SkillFileAction,
    SkillFileContent,
    SkillFileNode,
    SkillImportArchiveConfirmRequest,
    SkillImportArchivePreview,
    SkillImportDirectoryPreviewRequest,
    SkillImportUrlConfirmRequest,
    SkillImportUrlPreviewRequest,
    SkillLibraryStateRequest,
    SkillsCatalog,
    SkillSubmissionConfirmRequest,
    SkillSubmissionConfirmResponse,
    SkillSubmissionDismissResponse,
    SkillTagsResponse,
    SkillUpdateRequest,
    SkillView,
    StagingFileNodeView,
    StagingOptimizeRequest,
    StagingOptimizeResponse,
    StagingScanResponse,
    StagingSlugViewModel,
    StagingSyncItemResult,
    StagingSyncRequest,
    StagingSyncResponse,
)
from valuz_agent.modules.skills.service import SkillLibraryService

router = APIRouter(tags=["skills"])


# ------------------------------------------------------------------
# Skill library endpoints (static paths before {skill_id})
# ------------------------------------------------------------------


@router.get("/v1/skills")
async def list_skills(
    project_id: str | None = Query(default=None),
    library_enabled: bool | None = Query(default=None),
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> dict:
    target_project_id = project_id or "chat-default"
    try:
        catalog = await svc.list_catalog(user_id, target_project_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown project: {target_project_id}"
        ) from exc

    from valuz_agent.ports.extensions import ext

    data = catalog.model_dump()
    data["skills"] = await ext.resource_list_hook.apply(
        "skill", data.get("skills", []), user_id=user_id
    )
    if library_enabled is not None:
        data["skills"] = [
            skill
            for skill in data.get("skills", [])
            if (skill.get("library_enabled", True) is not False) == library_enabled
        ]
    return data


@router.post("/v1/skills", response_model=SkillView, status_code=status.HTTP_201_CREATED)
async def create_skill(
    payload: SkillCreateRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillView:
    try:
        return await svc.create_skill(user_id, payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/v1/skills/tags", response_model=SkillTagsResponse)
async def list_tags(
    project_id: str | None = Query(default=None),
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillTagsResponse:
    return SkillTagsResponse(tags=await svc.list_all_tags(user_id, project_id))


class SkillRescanResponse(BaseModel):
    indexed: int


@router.post("/v1/skills/scan", response_model=SkillRescanResponse)
async def rescan_skills(
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillRescanResponse:
    """Re-discover every skill on disk and refresh the index. The manual
    counterpart of the boot scan + periodic auto-scan — surfaced so a user who
    just added a team or dropped a skill folder can pick it up without
    restarting. Clients see the fresh index on their next fetch (the Skills
    page revalidates on navigate + window focus)."""
    indexed = await svc.startup_scan(user_id)
    return SkillRescanResponse(indexed=indexed)


# ------------------------------------------------------------------
# Import endpoints (static paths before {skill_id})
# ------------------------------------------------------------------


@router.post(
    "/v1/skills/import/session",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
async def import_from_session(
    payload: SessionSkillImportConfirmRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillView:
    try:
        return await svc.import_from_session_confirm(user_id, payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class SkillCreateChatStartRequest(SessionModelSelection):
    """Optional body for ``POST /v1/skills/create/chat/start``.

    Carries the same ``model_id`` / ``provider_id`` shape as
    ``SessionCreateRequest`` so the Skill Creator launcher honours the
    user's model pick. Empty body is allowed — falls through to the
    provider default the same way bare ``POST /v1/sessions`` does.
    """


def _resolve_project_for_creation(context: SkillCreationContext) -> str:
    """Pick the kernel project a skill-creator session should run in.

    - ``chat`` and ``skills_library`` both run in ``chat-default`` so the
      session has a usable cwd / channel / capability set, but the
      submission tool reads ``creation_context.kind`` to decide which
      side-effects to apply on confirmation.
    - ``project`` requires an explicit ``project_id`` and runs the
      session in that project so the agent has access to the project
      cwd, KB bindings, etc.
    """
    if context.kind == "project":
        if not context.project_id:
            raise HTTPException(
                status_code=422,
                detail="creation_context.project_id is required when kind='project'",
            )
        return context.project_id
    return "chat-default"


async def _default_assistant_slug_if_present(user_id: str) -> str | None:
    """Resolve the default assistant for the skill-creator launchers.

    Per 09-assistant every conversation binds an agent; the launchers
    previously minted sessions through the agentless raw-model path, which
    left the composer with no agent selected and — since a live session
    locks its binding — no way to pick one: a dead conversation.

    Candidates, in order: canonical Valurion, then the legacy default
    assistant. A historical ``valuz-helper`` is an ordinary user Agent and is
    not treated as the system default.
    """
    from valuz_agent.infra.db import async_unit_of_work
    from valuz_agent.modules.agents.builtin import VALURION_SLUG
    from valuz_agent.modules.agents.datastore import AgentDatastore
    from valuz_agent.modules.agents.seed import DEFAULT_ASSISTANT_SLUG

    try:
        async with async_unit_of_work(commit=False) as db:
            ds = AgentDatastore(db)
            for slug in (
                VALURION_SLUG,
                DEFAULT_ASSISTANT_SLUG,
            ):
                if await ds.get_agent(user_id, slug) is not None:
                    return slug
    except Exception:  # noqa: BLE001 — launcher must not die on a lookup hiccup
        return None
    return None


@router.post(
    "/v1/skills/create/start",
    response_model=SkillCreateStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_create(
    body: SkillCreateStartRequest,
    session_svc: SessionService = Depends(get_session_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillCreateStartResponse:
    """Unified launcher for the skill-creator agent loop.

    Opens a kernel session against the project appropriate for the
    caller's entry point and stamps ``creation_context`` onto session
    metadata so the ``submit_skill`` tool (registered alongside the
    ``skill-creator`` skill at session start) knows where the resulting
    skill should be filed: user library only, or library + project
    binding.

    The session binds the seeded default assistant when it exists (see
    ``_default_assistant_slug_if_present``) — the default assistant
    resolves as a global library agent in any project, and the
    skill-creator capabilities (skill + ``submit_skill`` tool) ride the
    always-on baseline, not the agent.
    """
    project_id = _resolve_project_for_creation(body.context)
    creation_context = body.context.model_dump(exclude_none=True)
    session = await session_svc.create_session(
        project_id=project_id,
        title=None,
        model_id=body.model_id,
        provider_id=body.provider_id,
        trigger_meta={"mode": "skill-creator"},
        creation_context=creation_context,
        agent_slug=body.agent_slug or await _default_assistant_slug_if_present(user_id),
        user_id=user_id,
    )
    return SkillCreateStartResponse(
        session_id=session.id,
        authoring_project_id=session.project_id,
        creation_context=body.context,
    )


@router.post(
    "/v1/skills/create/chat/start",
    response_model=SkillCreateChatStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_create_chat(
    body: SkillCreateChatStartRequest | None = None,
    session_svc: SessionService = Depends(get_session_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillCreateChatStartResponse:
    """Legacy chat-only launcher.

    Preserved as a shim over ``POST /v1/skills/create/start`` for any
    existing callers that don't yet pass ``creation_context``. New
    integrations should use the unified endpoint and supply the right
    ``kind`` so the submission tool can fire the correct side-effects.
    """
    payload = body or SkillCreateChatStartRequest()
    session = await session_svc.create_session(
        project_id="chat-default",
        title=None,
        model_id=payload.model_id,
        provider_id=payload.provider_id,
        trigger_meta={"mode": "skill-creator"},
        creation_context={"kind": "chat"},
        agent_slug=await _default_assistant_slug_if_present(user_id),
        user_id=user_id,
    )
    return SkillCreateChatStartResponse(
        session_id=session.id,
        authoring_project_id=session.project_id,
    )


# ── Skill staging (Scenario B + D3 accept) ────────────────────────────


def _slug_view_to_model(view) -> StagingSlugViewModel:  # type: ignore[no-untyped-def]
    return StagingSlugViewModel(
        slug=view.slug,
        name=view.name,
        description=view.description,
        file_count=view.file_count,
        total_bytes=view.total_bytes,
        files=[StagingFileNodeView(path=f.path, type=f.type, size=f.size) for f in view.files],
        conflict_kind=view.conflict_kind,
        suggested_strategy=view.suggested_strategy,
        suggested_new_slug=view.suggested_new_slug,
        source_skill_id=view.source_skill_id,
        version=view.version,
    )


@router.get(
    "/v1/skills/staging/{session_id}/scan",
    response_model=StagingScanResponse,
)
async def scan_staging_endpoint(
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    svc: SkillLibraryService = Depends(get_skill_service),
) -> StagingScanResponse:
    result = await svc.scan_staging(user_id, session_id)
    return StagingScanResponse(
        session_id=result.session_id,
        staging_path=result.staging_path,
        slugs=[_slug_view_to_model(v) for v in result.slugs],
    )


class StagingFileContent(BaseModel):
    path: str
    content: str


@router.get(
    "/v1/skills/staging/{session_id}/file",
    response_model=StagingFileContent,
)
async def read_staging_file_endpoint(
    session_id: str,
    slug: str = Query(..., description="Slug under the session staging dir"),
    path: str = Query(..., description="File path relative to the slug dir"),
    user_id: str = Depends(get_current_user_id),
) -> StagingFileContent:
    """Return the UTF-8 contents of a single file under a staging slug.

    Path traversal is blocked via Path.relative_to checks; binary files
    return a sentinel string so the UI can render a 'binary' placeholder
    rather than crashing on decode.
    """
    from valuz_agent.modules.skills.staging import staging_dir_for_session

    try:
        base = await staging_dir_for_session(user_id, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    slug_dir = (base / slug).resolve()
    try:
        slug_dir.relative_to(base.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid slug") from exc
    target = (slug_dir / path).resolve()
    try:
        target.relative_to(slug_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return StagingFileContent(path=path, content="<binary file>")
    return StagingFileContent(path=path, content=text)


@router.post(
    "/v1/skills/staging/{session_id}/sync",
    response_model=StagingSyncResponse,
    status_code=status.HTTP_201_CREATED,
)
async def sync_staging_endpoint(
    session_id: str,
    payload: StagingSyncRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> StagingSyncResponse:
    try:
        results = await svc.sync_staging(
            user_id,
            session_id=session_id,
            items=payload.items,
            target_scope=payload.target_scope,
            project_id=payload.project_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StagingSyncResponse(
        session_id=session_id,
        results=[
            StagingSyncItemResult(
                slug=r.slug,
                strategy=r.strategy,
                written_path=r.written_path,
                new_slug=r.new_slug,
                skipped=r.skipped,
            )
            for r in results
        ],
    )


@router.post(
    "/v1/skills/staging/{session_id}/optimize",
    response_model=StagingOptimizeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def optimize_staging_endpoint(
    session_id: str,
    payload: StagingOptimizeRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> StagingOptimizeResponse:
    """Pre-stage an existing skill into the session's staging dir for editing."""
    try:
        slug, staging_path = await svc.optimize_from_skill(
            user_id,
            session_id=session_id,
            source_skill_id=payload.source_skill_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return StagingOptimizeResponse(
        session_id=session_id,
        slug=slug,
        staging_path=staging_path,
    )


# ── Skill submissions (companion to ``submit_skill`` tool) ───────────


@router.post(
    "/v1/skills/submissions/{session_id}/{slug}/confirm",
    response_model=SkillSubmissionConfirmResponse,
)
async def confirm_skill_submission(
    session_id: str,
    slug: str,
    payload: SkillSubmissionConfirmRequest | None = None,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillSubmissionConfirmResponse:
    """User accepts the skill the agent submitted via ``submit_skill``.

    Promotes the staged slug into the user library at
    ``~/.agents/skills/{slug}/`` and applies the per-entry-point side
    effects encoded in the session's ``creation_context``: ``project``
    sessions also bind the new skill to their project; ``chat`` /
    ``skills_library`` write to the library only.

    Body fields (``summary``, ``change_kind``, ``files_touched``) are
    informational — the SKILL.md the agent wrote into staging is the
    source of truth. The frontend pulls them from the original
    ``tool_use`` event to render the confirmation card.
    """
    body = payload or SkillSubmissionConfirmRequest()
    try:
        skill, ctx_dict, bound_project_id = await svc.confirm_submission(
            user_id,
            session_id=session_id,
            slug=slug,
            summary=body.summary,
            change_kind=body.change_kind,
            files_touched=body.files_touched,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return SkillSubmissionConfirmResponse(
        skill=skill,
        creation_context=SkillCreationContext(**ctx_dict),
        bound_to_project_id=bound_project_id,
    )


@router.post(
    "/v1/skills/submissions/{session_id}/{slug}/dismiss",
    response_model=SkillSubmissionDismissResponse,
)
async def dismiss_skill_submission(
    session_id: str,
    slug: str,
    user_id: str = Depends(get_current_user_id),
    svc: SkillLibraryService = Depends(get_skill_service),
) -> SkillSubmissionDismissResponse:
    """User discards the agent's submission.

    Cleans up the staged slug; no library write. Idempotent — calling
    twice returns ``removed=False`` on the second call.
    """
    removed = await svc.dismiss_submission(user_id, session_id=session_id, slug=slug)
    return SkillSubmissionDismissResponse(session_id=session_id, slug=slug, removed=removed)


@router.post("/v1/skills/import/archive", response_model=SkillImportArchivePreview)
async def import_archive_preview(
    file: Annotated[UploadFile, File(...)],
    target_scope: Annotated[str, Form()] = "user",
    project_id: Annotated[str | None, Form()] = None,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillImportArchivePreview:
    suffix = Path(file.filename or "skill.zip").suffix or ".zip"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        temp_path = tmp.name
    try:
        return await svc.import_archive_preview(
            user_id,
            archive_path=temp_path,
            target_scope=target_scope,
            project_id=project_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/v1/skills/import/archive/confirm",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_archive_import(
    payload: SkillImportArchiveConfirmRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillView:
    try:
        return await svc.confirm_archive_import(user_id, payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/v1/skills/import/directory", response_model=SkillImportArchivePreview)
async def import_directory_preview(
    payload: SkillImportDirectoryPreviewRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillImportArchivePreview:
    try:
        return await svc.import_directory_preview(user_id, payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/v1/skills/import/url", response_model=SkillImportArchivePreview)
async def import_url_preview(
    payload: SkillImportUrlPreviewRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillImportArchivePreview:
    try:
        return await svc.import_url_preview(
            user_id,
            url=payload.url,
            target_scope=payload.target_scope,
            project_id=payload.project_id,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/v1/skills/import/url/confirm",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_url_import(
    payload: SkillImportUrlConfirmRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillView:
    try:
        return await svc.confirm_url_import(user_id, payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ------------------------------------------------------------------
# Parameterized skill endpoints
# ------------------------------------------------------------------


@router.get("/v1/skills/{skill_id}", response_model=SkillDetail)
async def get_skill(
    skill_id: str,
    project_id: str | None = Query(default=None),
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillDetail:
    try:
        return await svc.get_skill_detail(user_id, skill_id, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}") from exc


@router.patch("/v1/skills/{skill_id}", response_model=SkillView)
async def update_skill(
    skill_id: str,
    payload: SkillUpdateRequest,
    project_id: str | None = Query(default=None),
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillView:
    try:
        return await svc.update_skill(user_id, skill_id, payload, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}") from exc


@router.post(
    "/v1/skills/{skill_id}/copy",
    response_model=SkillView,
    status_code=status.HTTP_201_CREATED,
)
async def copy_skill(
    skill_id: str,
    payload: SkillCopyRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillView:
    try:
        return await svc.copy_skill(user_id, skill_id, payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/v1/skills/{skill_id}/library-state", response_model=SkillView)
async def set_skill_library_state(
    skill_id: str,
    payload: SkillLibraryStateRequest,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillView:
    # SkillNotFound (NotFoundError) maps to 404 via the error middleware.
    return await svc.set_library_enabled(user_id, skill_id, payload.enabled)


@router.delete(
    "/v1/skills/{skill_id}",
    response_model=SkillDeletePreview | None,
    status_code=status.HTTP_200_OK,
)
async def delete_skill(
    skill_id: str,
    project_id: str | None = Query(default=None),
    mode: str = Query(default="dry_run"),
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillDeletePreview | Response | None:
    if mode not in {"dry_run", "confirm"}:
        raise HTTPException(status_code=422, detail="Unsupported delete mode")
    try:
        result = await svc.delete_skill(user_id, skill_id, project_id=project_id, mode=mode)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}") from exc
    if mode == "confirm":
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return result


# ------------------------------------------------------------------
# File-level endpoints
# ------------------------------------------------------------------


@router.get("/v1/skills/{skill_id}/files", response_model=list[SkillFileNode])
async def list_skill_files(
    skill_id: str,
    project_id: str | None = Query(default=None),
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> list[SkillFileNode]:
    try:
        return await svc.list_skill_files(user_id, skill_id, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}") from exc


@router.get("/v1/skills/{skill_id}/files/{file_path:path}", response_model=SkillFileContent)
async def get_skill_file(
    skill_id: str,
    file_path: str,
    project_id: str | None = Query(default=None),
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillFileContent:
    try:
        return await svc.read_skill_file(user_id, skill_id, file_path, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}") from exc


@router.post(
    "/v1/skills/{skill_id}/files",
    response_model=SkillFileContent,
    status_code=status.HTTP_201_CREATED,
)
async def update_skill_file(
    skill_id: str,
    payload: SkillFileAction,
    project_id: str | None = Query(default=None),
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillFileContent:
    try:
        return await svc.write_skill_file(user_id, skill_id, payload, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {skill_id}") from exc


# ------------------------------------------------------------------
# Project skill config endpoints
# ------------------------------------------------------------------


@router.get("/v1/projects/{project_id}/skills", response_model=SkillsCatalog)
async def project_skills_catalog(
    project_id: str,
    svc: SkillLibraryService = Depends(get_skill_service),
    user_id: str = Depends(get_current_user_id),
) -> SkillsCatalog:
    try:
        return await svc.list_catalog(user_id, project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown project: {project_id}") from exc


# NOTE: project skill *binding* endpoints (PUT skills / scan / state) were
# removed — skills bind on the Agent now (08-agents-module). The GET above
# stays: it still feeds the conversation composer's skill-insert chips.
