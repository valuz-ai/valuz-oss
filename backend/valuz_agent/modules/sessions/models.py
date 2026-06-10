from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, OwnedMixin, PrimaryKeyMixin, TimestampMixin


class ProjectSessionRow(Base, PrimaryKeyMixin, TimestampMixin, OwnedMixin):
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


class SessionAttachmentRow(Base, PrimaryKeyMixin, TimestampMixin, OwnedMixin):
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
    # ``~/.valuz/app/docs/preview/{doc_id}.md``. No file copy ever
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
