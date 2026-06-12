"""Skill discovery contracts — small, stable Pydantic models the skill source
implementations and the skill service share.

These types previously lived in the (now deleted) ``valuz_agent.contracts``
runtime DTO bundle, alongside a much larger surface that belonged to the
self-built harness. The harness is gone; the only types still in active
use are these three, so they live next to the skills domain that owns them.

If we ever need to expose them outside the host (e.g. to an external skill
catalog plugin), this file can be promoted to its own package — but until
then keeping it scoped here avoids resurrecting the cross-cutting "contracts"
catch-all.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProjectRef(BaseModel):
    """Identity bundle the skill sources receive when listing skills.

    ``slug`` is the stable project key in URLs and project filesystems
    (``root_path/.claude/skills``). ``kind`` lets sources gate their
    behaviour for chat vs. projects.
    """

    id: str
    slug: str
    kind: str | None = None
    root_path: str | None = None


class RuntimeContext(BaseModel):
    """Per-request context passed into skill sources.

    The fields are intentionally narrow: skill discovery in valuz is a
    function of ``(user_id, project, edition)`` only — model selection,
    auth, channels, and tools are *not* the skill source's concern.
    """

    user_id: str = ""
    org_id: str | None = None
    project: ProjectRef | None = None
    edition: str = "personal"


class SkillManifest(BaseModel):
    """View model returned by a skill source — superset of what the UI shows.

    The optional ``version`` field is read from ``SKILL.md`` frontmatter and
    bumped by the staging fork flow; legacy skills omit it.
    """

    id: str
    name: str
    description: str
    scope: str
    source: str
    path: str = ""
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
    # Skill 管理 UI 的排序字段（kernel-upgrade-cozy-rose 计划）：
    # folder_created_at 是文件夹本身的 birthtime（macOS st_birthtime;
    # Linux statx → fallback st_mtime）。语义上是「这个 skill 文件夹
    # 何时存在」，重装/搬迁设备时也保留单条的相对时间序——避免重启
    # 时所有 skill 集中显示成同一天。
    #
    # 注意：``creation_origin`` 不在这里——它是 host 侧记账，只存在于
    # ``valuz_skill_index`` 表，不写进 SKILL.md，因此不属于「文件系统
    # 视图」的 manifest。见 ``SkillLibraryService.list_catalog`` 的叠加。
    folder_created_at: int | None = None


__all__ = ["ProjectRef", "RuntimeContext", "SkillManifest"]
