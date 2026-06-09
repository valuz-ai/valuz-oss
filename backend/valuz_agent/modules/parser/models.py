"""SQLAlchemy rows owned by the parser module.

Two long-lived tables:

- ``valuz_setup_job`` — one row per ``SetupRequirement.id`` (e.g.
  ``rapidocr_models``). Tracks one-time setup work that needs explicit
  user authorization (model download, etc.) and survives across
  restarts. Singleton row per setup id; status moves through pending →
  running → succeeded/failed/cancelled.
- ``valuz_polling_task`` — one row per remote async task submitted to a
  cloud parser (PaddleOCR / MinerU / future). Carries the external
  task_id, exponential-backoff poll schedule, and the result/error
  payload. The ``PollingScheduler`` background thread drives state
  transitions; ``ParserBackend.parse`` awaits completion via an
  in-process future keyed on ``id``.

Why these aren't reusing ``valuz_document_import_task``: that row
describes the user's batch ("import these 12 files"). One import-task
typically fans out into N polling-tasks (one per doc that needs cloud
parsing). Keeping them separate avoids overloading the user-facing
progress UI with handler-level retry detail.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, OwnedMixin


class SetupJobRow(Base, OwnedMixin):
    """Persistent state for a one-time setup operation (model download,
    license acceptance gate, etc.)."""

    __tablename__ = "valuz_setup_job"

    # ``setup_id`` is the stable key from ``SetupRequirement.id`` — e.g.
    # ``rapidocr_models``. There is at most one row per setup_id.
    setup_id: Mapped[str] = mapped_column(String(64), primary_key=True)

    # ``status`` ∈ {pending, running, succeeded, failed, cancelled}.
    # ``pending`` exists for symmetry with the polling table but a setup
    # job typically jumps straight to ``running`` when the user clicks
    # "Download". The row is created lazily on the first ``GET``.
    status: Mapped[str] = mapped_column(String(16), default="pending")

    # Progress for ``model_download`` kind. ``total_bytes`` may be None
    # while we are still HEAD-fetching the file list; ``downloaded_bytes``
    # is updated at roughly 1Hz from the worker thread.
    downloaded_bytes: Mapped[int] = mapped_column(Integer, default=0)
    total_bytes: Mapped[int | None] = mapped_column(Integer, default=None)

    # ``error`` is null on the happy path; populated with a short reason
    # string (network error, license rejection, ...) on failure. The full
    # traceback lives in the log, not here.
    error: Mapped[str | None] = mapped_column(Text, default=None)

    # ``source`` records which mirror / origin URL was actually used —
    # useful for debugging when a fallback kicks in (modelscope → HF).
    source: Mapped[str | None] = mapped_column(String(256), default=None)

    started_at: Mapped[int | None] = mapped_column(BigInteger, default=None)
    completed_at: Mapped[int | None] = mapped_column(BigInteger, default=None)
    updated_at: Mapped[int] = mapped_column(BigInteger)


class PollingTaskRow(Base, OwnedMixin):
    """Persistent state for one remote async parse task."""

    __tablename__ = "valuz_polling_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)

    # ``kind`` is the handler key — e.g. ``parser.mineru`` /
    # ``parser.paddleocr``. The ``PollingScheduler`` looks up the
    # matching ``PollingHandler`` to drive submit/poll/fetch.
    kind: Mapped[str] = mapped_column(String(64), index=True)

    # ID returned by the remote service. Populated after a successful
    # ``submit``; ``None`` while still in ``pending``.
    external_task_id: Mapped[str | None] = mapped_column(String(128), default=None)

    # Opaque JSON the handler stuffs in at submit time and reads on every
    # poll/fetch (typically ``{file_path, options, ...}``).
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    # ``status`` ∈ {pending, running, succeeded, failed, cancelled}.
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    attempt: Mapped[int] = mapped_column(Integer, default=0)

    # When the scheduler should poll this row next; the loop selects
    # ``status IN (pending, running) AND next_poll_at <= now()``. Set on
    # creation and on every poll outcome that returns ``Pending``.
    next_poll_at: Mapped[int | None] = mapped_column(BigInteger, default=None, index=True)

    # On success ``result_json`` holds the handler's ``ParseResult``
    # serialised (markdown + page_count + metadata). On failure ``error``
    # holds a short reason; ``result_json`` stays empty.
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[int] = mapped_column(BigInteger)
