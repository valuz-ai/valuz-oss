from pydantic import BaseModel
from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, OwnedMixin, PrimaryKeyMixin, TimestampMixin


class ProjectRow(Base, PrimaryKeyMixin, TimestampMixin, OwnedMixin):
    __tablename__ = "valuz_project"

    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))  # chat | project
    root_path: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # Context fields — formerly the 1:1 ``valuz_workspace_context`` table,
    # folded into the main row. ``instructions_md`` is the user-authored
    # prompt source; ``memory_summary``/``memory_version`` carry the
    # background-accumulated memory with optimistic-lock versioning.
    instructions_md: Mapped[str | None] = mapped_column(Text)
    memory_summary: Mapped[str | None] = mapped_column(Text)
    memory_version: Mapped[int] = mapped_column(Integer, default=0)


class ProjectCreateRequest(BaseModel):
    name: str
    root_path: str
