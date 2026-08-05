from typing import Any

from sqlalchemy import JSON, BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin


class ProjectSessionRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """Host-side project↔session index.

    One row per kernel session, written at session-creation time. This is
    the host's own record of which project a session belongs to and what
    role it plays — the kernel itself is project-agnostic (its
    ``sessions.project_id`` column is being retired). All project-scoped
    session queries (sidebar list, delete-project cascade, runs overview)
    resolve ids here first, then bulk-fetch the rows from the kernel.
    """

    __tablename__ = "valuz_project_session"

    project_id: Mapped[str] = mapped_column(String(36), index=True)
    # References kernel ``sessions.id`` — business key, NO FK constraint.
    session_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    # 'chat' — user-visible conversation (quick chat / project chat).
    # 'task_lead' / 'task_subtask' — task-internal runs, hidden from the
    # conversation list (replaces the json_extract task_id filter).
    kind: Mapped[str] = mapped_column(String(16), default="chat")
    # Mirror of metadata.valuz.origin at creation: user | automation | task…
    origin: Mapped[str] = mapped_column(String(32), default="user")
    # Auto-drain pause marker for the session input queue. ``NULL`` = not
    # paused; a timestamp (ms) means an interrupt soft-paused draining and the
    # queue awaits an explicit resume. See docs/design/session-input-queue.md §9.
    queue_paused_at: Mapped[int | None] = mapped_column(BigInteger)


class SessionAttachmentRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    __tablename__ = "valuz_session_attachment"

    session_id: Mapped[str] = mapped_column(String(36), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(128))
    parse_status: Mapped[str] = mapped_column(String(32), default="uploaded")
    parse_mode: Mapped[str | None] = mapped_column(String(32))
    parsed_path: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    # Origin of the attachment. ``local`` is the historical multipart
    # upload path — ``stored_path`` is a per-session file on disk that
    # we own. ``kb_doc`` means the user picked the file from the
    # global knowledge base via the conversation attachment menu —
    # ``stored_path`` points at the KB document's ``source_path``
    # (the deterministic on-disk location the KB owns) and
    # ``parsed_path`` reuses the KB's existing preview markdown at
    # ``~/.valuz-oss/docs/preview/{doc_id}.md``. No file copy ever
    # happens for ``kb_doc`` rows; re-parses of the KB document
    # propagate to the session attachment automatically because the
    # paths are live references rather than snapshots.
    source_kind: Mapped[str] = mapped_column(String(16), default="local")
    # When ``source_kind="kb_doc"``, the originating KB and document
    # ids — used both for UI affordances (icon, source label) and
    # for the deletion path (KB doc removal needs to mark this row
    # ``parse_status="missing"``). Always ``NULL`` for ``local``.
    source_kb_id: Mapped[str | None] = mapped_column(String(36))
    source_kb_doc_id: Mapped[str | None] = mapped_column(String(36))
    # Per-turn lifecycle marker. Attachments are *staged* — uploaded /
    # picked for the **next** message, not for the whole session.
    # ``NULL`` means "pending: belongs to the next turn"; a timestamp
    # means "already shipped with a turn and consumed". Each turn's
    # ``UserMessage.attachments`` is built from the pending set only,
    # and the rows are stamped ``consumed_at`` once that turn runs, so
    # a file uploaded for turn 1 does not silently tag along on turns
    # 2, 3, …. The side panel + composer chips also show only the
    # pending set, so the "uploaded files" bar reads as a staging
    # area that clears after each send.
    consumed_at: Mapped[int | None] = mapped_column(BigInteger)


class SessionArtifactRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """SUPERSEDED. The pre-versioning "生成文件" list: one mutable row per
    ``(session_id, file_path)``, holding a live reference rather than a copy.

    Nothing reads or writes it. Deliveries now go to the Artifact / Revision /
    Content tables (``modules/artifacts``), where re-delivering a file appends a
    version instead of overwriting the row, and the recorded path points at an
    immutable snapshot rather than a file the agent can still edit.

    Kept, table and rows both, because installs that delivered before the switch
    still have their history here and no backfill has been run — deliberately,
    the measured volume did not justify one. Dropping this would turn "not yet
    migrated" into "gone". The declaration stays so alembic keeps seeing the
    table as part of the schema rather than proposing to drop it.
    """

    __tablename__ = "valuz_session_artifact"

    session_id: Mapped[str] = mapped_column(String(36), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(512))
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(128))


class QueuedInputRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """A user follow-up message queued while a session's turn was running.

    Drained FIFO by the host after the active turn completes (host-driven,
    budget-checked). Durable so long-running tasks survive client disconnect
    and backend restart. See docs/design/session-input-queue.md.
    """

    __tablename__ = "valuz_queued_input"

    # References kernel ``sessions.id`` — business key, NO FK constraint.
    session_id: Mapped[str] = mapped_column(String(36), index=True)
    # ``NULL`` for project-less quick chats.
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # User-authored part frozen at enqueue, a subset of kernel ``UserMessage``
    # (core/types.py): ``{"text": str, "attachments": [{"source_path": str,
    # "parsed_path": str | None}]}``. ``additional_context`` is intentionally
    # NOT frozen here — it is rebuilt per-turn at dispatch (project memory +
    # bound KB scope), see the drain path.
    input: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # queued (待发) | dispatched (执行中/已派发) | blocked (预检失败) | cancelled (删除)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    # FIFO order within a session; ``MAX(position)+1`` at enqueue.
    position: Mapped[int] = mapped_column(Integer, default=0)
    # Turn-level overrides mirrored from send_message (NOT part of UserMessage).
    provider_id: Mapped[str | None] = mapped_column(String(36))
    model_id: Mapped[str | None] = mapped_column(String(128))
    # Why a ``blocked`` item could not run (e.g. budget), surfaced to the UI.
    error_message: Mapped[str | None] = mapped_column(Text)
