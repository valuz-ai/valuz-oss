from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin


class SessionAttachmentRow(Base, PrimaryKeyMixin, TimestampMixin):
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
