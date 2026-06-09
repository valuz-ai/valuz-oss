from pydantic import BaseModel
from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, OwnedMixin, PrimaryKeyMixin, TimestampMixin


class WorkspaceRow(Base, PrimaryKeyMixin, TimestampMixin, OwnedMixin):
    __tablename__ = "valuz_workspace"

    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))  # chat | project
    root_path: Mapped[str | None] = mapped_column(Text)
    icon: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class WorkspaceContextRow(Base, OwnedMixin):
    __tablename__ = "valuz_workspace_context"

    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    instructions_md: Mapped[str | None] = mapped_column(Text)
    memory_summary: Mapped[str | None] = mapped_column(Text)
    memory_version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class ProjectCreateRequest(BaseModel):
    name: str
    root_path: str
