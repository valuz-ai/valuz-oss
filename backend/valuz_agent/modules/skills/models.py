from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin
from valuz_agent.infra.time_utils import now_ms

# ---------------------------------------------------------------------------
# SQLAlchemy ORM models
# ---------------------------------------------------------------------------


class SkillIndexRow(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "valuz_skill_index"

    slug: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(32))
    source_path: Mapped[str] = mapped_column(Text)
    project_root: Mapped[str | None] = mapped_column(Text)
    manifest_filename: Mapped[str | None] = mapped_column(String(256))
    tags_json: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="available")
    readonly: Mapped[bool] = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    manifest_hash: Mapped[str | None] = mapped_column(String(64))
    # Filesystem birthtime of the skill folder — drives the DESC sort on
    # the skill management page. NULL on legacy rows seeded before this
    # field landed (rewritten on next startup_scan). See contracts.py
    # for the cross-platform read logic.
    folder_created_at: Mapped[int | None] = mapped_column(BigInteger, default=None)
    # Where the skill came from, from the user's POV — host-side
    # bookkeeping that lives ONLY here, never in SKILL.md:
    #   * ``created``    — UI dialog / skill-creator AI session / duplicate
    #   * ``imported``   — archive / URL / directory import
    #   * ``discovered`` — found on disk by the filesystem scan, not
    #                      originated by Valuz (the default for new rows)
    # ``startup_scan`` defaults fresh rows to ``"discovered"`` and never
    # clobbers an existing value; the create / import flows overwrite it
    # via ``SkillDatastore.set_creation_origin``. Physically nullable for
    # legacy rows seeded before this column landed — those are healed to
    # ``"discovered"`` on the next ``startup_scan``.
    creation_origin: Mapped[str | None] = mapped_column(String(32), default="discovered")
    deletable: Mapped[bool] = mapped_column(Boolean, default=True)


class ProjectSkillConfigRow(Base):
    __tablename__ = "valuz_project_skill_config"

    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_path: Mapped[str] = mapped_column(Text, primary_key=True)
    added_at: Mapped[int] = mapped_column(BigInteger, default=now_ms)


# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class SkillView(BaseModel):
    id: str
    name: str
    description: str
    scope: str
    source: str
    path: str
    enabled: bool = False
    tags: list[str] = Field(default_factory=list)
    slug: str = ""
    icon: str | None = None
    status: str = "available"
    readonly: bool = False
    deletable: bool = True
    is_locked: bool = False
    lock_reason: str | None = None
    project_root: str | None = None
    origin_label: str | None = None
    argument_hint: str | None = None
    context: str | None = None
    content_hash: str | None = None
    manifest_hash: str | None = None
    version: int | None = None
    # Folder birthtime drives the DESC sort on the skill management page
    # (newest folder on top, name ASC tiebreaker). ``None`` for legacy
    # rows scanned before the field landed — sort puts NULL last so old
    # skills don't push freshly-created ones down.
    folder_created_at: int | None = None
    # ``created`` (UI dialog / skill-creator) / ``imported`` (archive /
    # URL / directory import) / ``discovered`` (scanned in, not Valuz-
    # originated). Host bookkeeping sourced from ``valuz_skill_index``
    # — never SKILL.md. Drives the "创建" / "同步" badge in the .agents
    # group; ``discovered`` renders no badge.
    creation_origin: Literal["created", "imported", "discovered"] = "discovered"


class SkillDetail(SkillView):
    instructions_markdown: str | None = None
    file_count: int = 0
    root_path: str | None = None
    manifest_filename: str | None = None
    metadata: dict = Field(default_factory=dict)


class SkillsCatalog(BaseModel):
    workspace_id: str
    skills: list[SkillView]


class SkillScanResponse(BaseModel):
    discovered: int


class SkillStateRequest(BaseModel):
    path: str
    enabled: bool


class WorkspaceSkillsUpdateRequest(BaseModel):
    skills_enabled: list[str] = Field(default_factory=list)


SkillTargetScope = Literal["user", "project", "official", "tenant"]
SkillDeleteMode = Literal["dry_run", "confirm"]


class SkillCreateRequest(BaseModel):
    name: str
    description: str = ""
    target_scope: SkillTargetScope = "user"
    workspace_id: str | None = None
    instructions_markdown: str | None = None
    add_to_workspace: bool = False


class SkillUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions_markdown: str | None = None


class SkillCopyRequest(BaseModel):
    new_name: str
    workspace_id: str | None = None
    add_to_workspace: bool = False


class SessionSkillImportConfirmRequest(BaseModel):
    session_id: str
    name: str
    description: str = ""
    target_scope: SkillTargetScope = "user"
    workspace_id: str | None = None
    add_to_workspace: bool = False


class SkillCreateChatStartResponse(BaseModel):
    """Returned when the user opens the AI-create-skill panel.

    The frontend then drives a normal sessions-API conversation against
    `session_id`, and finalises with /v1/skills/create/chat/finalize.
    """

    session_id: str
    authoring_workspace_id: str


SkillCreationKind = Literal["chat", "project", "skills_library"]


class SkillCreationContext(BaseModel):
    """Where the user opened the skill-creator from.

    The skill-creator's ``submit_skill`` tool reads this off the session
    metadata to apply the right side-effects on confirmation:

    - ``chat``: write to user library only.
    - ``project``: write to user library + bind to the workspace.
    - ``skills_library``: write to user library only (entry from the
      skills page itself).
    """

    kind: SkillCreationKind
    workspace_id: str | None = None  # required when kind == "project"


class SkillCreateStartRequest(BaseModel):
    """Unified entry-point for launching the skill-creator agent loop.

    Replaces ``POST /v1/skills/create/chat/start`` (which is preserved as
    a thin shim) and consolidates the three product entry points (chat /
    project / skills_library) behind one endpoint. The session is created
    against the workspace appropriate for the kind, and the
    ``creation_context`` is stamped onto session metadata so the
    downstream ``submit_skill`` tool can apply the right side-effects.
    """

    context: SkillCreationContext
    model_id: str | None = None
    provider_id: str | None = None


class SkillCreateStartResponse(BaseModel):
    session_id: str
    authoring_workspace_id: str
    creation_context: SkillCreationContext


class SkillSubmissionConfirmRequest(BaseModel):
    """User confirmation of a skill the agent submitted via ``submit_skill``.

    Frontend pulls ``summary`` / ``change_kind`` / ``files_touched`` from
    the original ``tool_use`` event payload and replays them here so the
    audit log in ``SKILL_CHANGED`` carries the agent-supplied metadata.
    The actual content lives on disk in the staging dir; the body is
    informational only.
    """

    summary: str | None = None
    change_kind: Literal["create", "update"] = "create"
    files_touched: list[str] = Field(default_factory=list)


class SkillSubmissionConfirmResponse(BaseModel):
    skill: SkillView
    creation_context: SkillCreationContext
    bound_to_workspace_id: str | None = None


class SkillSubmissionDismissResponse(BaseModel):
    session_id: str
    slug: str
    removed: bool


class SkillDeleteAffectedProject(BaseModel):
    workspace_id: str
    name: str


class SkillDeletePreview(BaseModel):
    affected_projects: list[SkillDeleteAffectedProject] = Field(default_factory=list)
    count: int = 0


class SkillImportPreviewFile(BaseModel):
    path: str
    type: Literal["file", "directory"]
    size: int | None = None


class SkillImportArchivePreview(BaseModel):
    preview_id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)
    file_tree: list[SkillImportPreviewFile] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    name_conflict: bool = False
    suggested_name: str | None = None


class SkillImportDirectoryPreviewRequest(BaseModel):
    directory_path: str
    target_scope: SkillTargetScope = "user"
    workspace_id: str | None = None


class SkillImportArchiveConfirmRequest(BaseModel):
    preview_id: str
    name: str | None = None
    target_scope: SkillTargetScope = "user"
    workspace_id: str | None = None
    add_to_workspace: bool = False


# ---------------------------------------------------------------------------
# File-level models (T1.1)
# ---------------------------------------------------------------------------


class SkillFileNode(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int | None = None
    children: list["SkillFileNode"] = Field(default_factory=list)


class SkillFileContent(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"


class SkillFileAction(BaseModel):
    action: Literal["create", "rename", "delete"]
    path: str
    new_path: str | None = None
    content: str | None = None


# ---------------------------------------------------------------------------
# Tags aggregation (T1.2)
# ---------------------------------------------------------------------------


class SkillTagsResponse(BaseModel):
    tags: list[str]


# ---------------------------------------------------------------------------
# Skill staging (Scenario B + D3 accept path)
# ---------------------------------------------------------------------------


class StagingFileNodeView(BaseModel):
    path: str
    type: Literal["file", "directory"]
    size: int | None = None


class StagingSlugViewModel(BaseModel):
    slug: str
    name: str
    description: str
    file_count: int
    total_bytes: int
    files: list[StagingFileNodeView]
    conflict_kind: Literal["none", "same_source", "diverged"]
    suggested_strategy: Literal["overwrite", "fork", "abort"]
    suggested_new_slug: str | None = None
    source_skill_id: str | None = None
    version: int | None = None


class StagingScanResponse(BaseModel):
    session_id: str
    staging_path: str
    slugs: list[StagingSlugViewModel]


class StagingSyncItem(BaseModel):
    slug: str
    strategy: Literal["overwrite", "fork", "abort"] = "overwrite"
    new_slug: str | None = None  # used when strategy="fork"


class StagingSyncRequest(BaseModel):
    items: list[StagingSyncItem]
    target_scope: SkillTargetScope = "user"
    workspace_id: str | None = None  # required for target_scope="project"


class StagingSyncItemResult(BaseModel):
    slug: str
    strategy: Literal["overwrite", "fork", "abort"]
    written_path: str | None = None
    new_slug: str | None = None
    skipped: bool = False


class StagingSyncResponse(BaseModel):
    session_id: str
    results: list[StagingSyncItemResult]


class StagingOptimizeRequest(BaseModel):
    source_skill_id: str  # e.g. "user:my-skill" or "official:foo"


class StagingOptimizeResponse(BaseModel):
    session_id: str
    slug: str
    staging_path: str


# ---------------------------------------------------------------------------
# URL import (T1.3)
# ---------------------------------------------------------------------------


class SkillImportUrlPreviewRequest(BaseModel):
    url: str
    target_scope: SkillTargetScope = "user"
    workspace_id: str | None = None


class SkillImportUrlConfirmRequest(BaseModel):
    preview_id: str
    name: str | None = None
    target_scope: SkillTargetScope = "user"
    workspace_id: str | None = None
    add_to_workspace: bool = False
