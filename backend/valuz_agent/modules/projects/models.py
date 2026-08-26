from pydantic import BaseModel
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class ProjectRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_project"

    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))  # chat | project
    root_path: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # ``instructions_md`` (formerly the 1:1 ``valuz_workspace_context`` table,
    # folded into the main row) is the user-authored prompt source.
    instructions_md: Mapped[str | None] = mapped_column(Text)
    # Project-local member slug that leads a task when the caller names no lead.
    # Lives on the project (not as a flag on ``valuz_project_member``) so "at
    # most one default lead" holds by construction. May dangle if the member is
    # removed — readers verify membership and fall through
    # (docs/design/channel-project-binding-and-default-lead.md §3.1).
    default_lead_agent_slug: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # DEPRECATED / inert: the early single-blob project memory. Superseded by the
    # file-based project memory at ``~/.valuz-oss/memories/projects/<id>/`` (see
    # modules/memory). Nothing reads or writes these anymore; they remain only so
    # the live schema is undisturbed. Drop them in a future alembic migration
    # (a SQLite ``batch_alter_table`` column drop) when convenient.
    memory_summary: Mapped[str | None] = mapped_column(Text)
    memory_version: Mapped[int] = mapped_column(Integer, default=0)


class ProjectCreateRequest(BaseModel):
    name: str
    root_path: str | None = None
