from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin


class KnowledgeBaseRow(Base, PrimaryKeyMixin, TimestampMixin):
    """Global knowledge base — each KB maps to one local root directory (D1)."""

    __tablename__ = "valuz_knowledge_base"

    name: Mapped[str] = mapped_column(String(256))
    root_path: Mapped[str] = mapped_column(Text)
    parser_routing: Mapped[str] = mapped_column(String(32), default="local_only")
    auto_discover: Mapped[bool] = mapped_column(default=False)
    last_full_scan_at: Mapped[int | None] = mapped_column(BigInteger, default=None)

    __table_args__ = (Index("ux_kb_root_path", "root_path", unique=True),)


class KbFolderRow(Base, PrimaryKeyMixin, TimestampMixin):
    """Directory node in a KB tree — preserves folder structure from disk."""

    __tablename__ = "valuz_kb_folder"

    kb_id: Mapped[str] = mapped_column(String(36))
    parent_folder_id: Mapped[str | None] = mapped_column(String(36), default=None)
    relative_path: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | missing (D6)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    descendant_document_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        Index("ix_kb_folder_kb_parent", "kb_id", "parent_folder_id"),
        Index("ux_kb_folder_path", "kb_id", "relative_path", unique=True),
        Index("ix_kb_folder_status", "kb_id", "status"),
    )


class DocumentRecordRow(Base, PrimaryKeyMixin, TimestampMixin):
    """Single document within a KB — indexed in-place, no file copy (D2)."""

    __tablename__ = "valuz_document_record"

    kb_id: Mapped[str] = mapped_column(String(36))
    kb_folder_id: Mapped[str] = mapped_column(String(36))
    relative_path: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)
    source_filename: Mapped[str] = mapped_column(String(512))
    title: Mapped[str | None] = mapped_column(String(512))
    mime_type: Mapped[str | None] = mapped_column(String(128))
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    discovery_source: Mapped[str] = mapped_column(String(32), default="initial_scan")
    status: Mapped[str] = mapped_column(String(32), default="queued")
    preview_text_path: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    parser_mode: Mapped[str | None] = mapped_column(String(32))
    docs_runtime_id: Mapped[str | None] = mapped_column(String(64))
    runtime_doc_ref: Mapped[str | None] = mapped_column(String(256))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    last_error_message: Mapped[str | None] = mapped_column(Text)
    # JSON list[{plugin_id, error, occurred_at}] recording every plugin
    # attempt for this doc — including ones that failed-but-fellback to
    # local. Stays populated even when ``status="ready"`` so the doc
    # detail panel can show "MinerU 失败 → 自动用 LightLocal 解析" with
    # the specific upstream error message (e.g. "200 pages exceeded").
    parser_attempts_json: Mapped[str | None] = mapped_column(Text, default=None)

    __table_args__ = (
        Index("ix_doc_kb_folder", "kb_id", "kb_folder_id"),
        Index("ix_doc_status", "kb_id", "status"),
        Index("ux_doc_relative_path", "kb_id", "relative_path", unique=True),
    )


class DocumentImportTaskRow(Base, PrimaryKeyMixin, TimestampMixin):
    __tablename__ = "valuz_document_import_task"

    task_type: Mapped[str] = mapped_column(String(32))  # import_files | rescan | reindex
    kb_id: Mapped[str | None] = mapped_column(String(36))
    source_label: Mapped[str | None] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    workspace_id: Mapped[str | None] = mapped_column(String(36))
    # JSON list[{doc_id, filename, plugin_id, error, occurred_at}] —
    # populated when a plugin attempt fails (whether the doc later
    # succeeds via fallback or not). Surfaces "this rescan had N
    # documents that needed fallback because …" in the task view.
    errors_json: Mapped[str | None] = mapped_column(Text, default=None)


class ProjectKbBindingRow(Base):
    """Three-level project binding (D3) — minimal-cover include set."""

    __tablename__ = "valuz_project_kb_binding"

    workspace_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    binding_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    target_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=None)

    __table_args__ = (Index("ix_binding_workspace", "workspace_id"),)
