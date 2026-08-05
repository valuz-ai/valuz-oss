"""Artifact tables, split by mutability.

Five tables rather than three, because "what a deliverable IS" and "which
version is current" change on completely different clocks and only one of them
needs concurrency control:

    valuz_artifact           immutable identity   (display_name / archived_at aside)
    valuz_artifact_key       mutable index        how a delivery finds its artifact
    valuz_artifact_head      mutable pointer      the single row CAS lands on
    valuz_artifact_revision  immutable event      never rewritten (but see ``status``)
    valuz_artifact_content   immutable bytes      INSERT only, never UPDATE

Keeping the head pointer OFF ``valuz_artifact`` is what lets the identity row
stay a pure identity, and it gives compare-and-set a single narrow row to fight
over instead of the artifact itself. It is also where a ``branch`` column would
go if the linear-history policy is ever relaxed — the DAG shape is already in
``valuz_artifact_revision.parent_revision_id``; only the write policy is linear.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from valuz_agent.infra.database import Base, PrimaryKeyMixin, TimestampMixin, UserMixin

# ``worktree`` is part of every scope key. Empty string, not NULL: it is a
# component of UNIQUE constraints, and SQL treats NULLs as distinct — a nullable
# column would let the same shared-cwd key be inserted twice.
SHARED_CWD = ""

# Delivery scope: the cwd a session actually runs in. Callers pass these three
# together everywhere, so the column set is spelled once here.
_SCOPE_COLUMNS = ("user_id", "project_id", "worktree")


class ArtifactKind(StrEnum):
    """What family of thing a deliverable is.

    Supplied by the agent, not inferred. An extension says what a file is
    encoded as, not what it is *for* — the same ``.html`` is a one-page report,
    an interactive tool, or a chart depending on intent, and a md -> pdf export
    would flip a derived value on a product whose kind never changed. The caller
    is the only party that knows; when it says nothing, ``FILE`` is the honest
    answer rather than a guess dressed up as one.

    Purely a label: it groups and captions, and is deliberately excluded from
    identity matching (see the key table). Nothing branches on it, so a wrong
    value costs a misfiled icon, not a split history.
    """

    FILE = "file"
    DOCUMENT = "document"
    PRESENTATION = "presentation"
    SPREADSHEET = "spreadsheet"
    UI = "ui"
    MEDIA = "media"


#: Model-facing descriptions, kept beside the enum so the tool schema and the
#: type cannot drift apart.
ARTIFACT_KIND_HINTS: dict[ArtifactKind, str] = {
    ArtifactKind.DOCUMENT: "prose to read — a report, memo, analysis, notes",
    ArtifactKind.PRESENTATION: "slides",
    ArtifactKind.SPREADSHEET: "tabular data — a model, dataset, comparison table",
    ArtifactKind.UI: "something to interact with or view — a page, dashboard, chart",
    ArtifactKind.MEDIA: "an image, video, or audio file",
    ArtifactKind.FILE: "anything else, or when unsure",
}


def coerce_kind(raw: object) -> ArtifactKind:
    """A caller-supplied kind, or ``FILE``. Never raises, never guesses."""
    if isinstance(raw, str):
        try:
            return ArtifactKind(raw.strip().lower())
        except ValueError:
            pass
    return ArtifactKind.FILE


KEY_KIND_PATH = "path"
KEY_KIND_NAME = "name"

STORAGE_KIND_FILE = "file"
STORAGE_KIND_INLINE = "inline"

REVISION_STATUS_READY = "ready"
REVISION_STATUS_MISSING = "missing"


class ArtifactRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """A deliverable's stable identity — "the quarterly report", not any one file.

    Survives renames, format changes and hand-offs between sessions. Deliberately
    holds no current-version pointer (that is ``ArtifactHeadRow``), no path, no
    session id and no bytes: everything here is either fixed at creation or a
    label the user controls.

    ``id`` overrides the 32-hex ``PrimaryKeyMixin`` default with a short base32
    handle, because it appears in the on-disk layout
    (``<scope_cwd>/.artifact/<id>/v3/report.pdf``) and a path a human has to read
    in a file tree should not carry a UUID.
    """

    __tablename__ = "valuz_artifact"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    # Business key, NO FK constraint (house rule). Every session belongs to a
    # project — quick chats get their own ``kind="chat"`` project — so this is
    # never empty in practice.
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    # ``SHARED_CWD`` for the project's own cwd, else the worktree name. Artifacts
    # are scoped to the cwd they were delivered into, so a worktree's
    # ``report.md`` and the main line's ``report.md`` are two artifacts, not two
    # versions of one — a worktree is an independent line of work.
    worktree: Mapped[str] = mapped_column(String(128), default=SHARED_CWD)
    # ``ArtifactKind``, supplied by the agent at creation and fixed thereafter.
    # Fixed because it labels the product, not the file: a deliverable exported
    # to a second format is the same product, and re-reading the kind from each
    # delivery would let it flap.
    kind: Mapped[str] = mapped_column(String(32), default=ArtifactKind.FILE.value)
    # Required at creation (the tool defaults it to the file's basename) because
    # the first delivery derives the identity from it. Freely editable after —
    # renaming adds a ``name`` key, it does not create a revision.
    display_name: Mapped[str] = mapped_column(String(512))
    # Directory (relative to the scope cwd, ``""`` at the root) the deliverable
    # lives in, from its first delivery. Carried so a *rename* can register the
    # new name in the same directory the artifact already occupies — name keys
    # are directory-qualified, and a rename has no file path to derive one from.
    rel_dir: Mapped[str] = mapped_column(String(1024), default="")
    archived_at: Mapped[int | None] = mapped_column(BigInteger)

    __table_args__ = (Index("ix_artifact_scope_recent", *_SCOPE_COLUMNS, "updated_at"),)


class ArtifactKeyRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """How an incoming delivery finds the artifact it belongs to.

    The identity is *derived once* — first delivery computes it from the scope,
    the relative path and the display name — and from then on lookup goes
    through this table. That indirection is the whole point: a pure function of
    ``(scope, name)`` would change the artifact id the moment a user renamed the
    deliverable, splitting its history in two.

    Two kinds, checked in order:

    ``path``  the delivered file's path relative to the scope cwd. Primary,
              because it is a fact about where the agent actually wrote, not a
              title it improvised.
    ``name``  the normalized display name, **qualified by the containing
              directory**, so re-delivering the same deliverable under a new
              file name still lands on it.

    Path-first matters for the failure mode, not the hit rate: a name that
    drifts (``Q3报告.pdf`` / ``Q3 报告.pdf``) *forks*, which a user can merge,
    while a wrong match *merges* — silently turning one deliverable into
    another's v2, which corrupts the history rather than just splitting it.

    The directory qualifier on the name key exists for exactly that reason. A
    bare display name is not unique inside a scope: ``marketing/report.md`` and
    ``finance/report.md`` are two unrelated deliverables that share a basename,
    and a global name key would fold the second into the first's history. The
    qualifier keeps the case the fallback is actually for — the agent rewriting
    ``report.md`` as ``report-final.md`` beside it — and gives up the rarer
    cross-folder rename, which forks instead. Fork is the safe direction.
    """

    __tablename__ = "valuz_artifact_key"

    project_id: Mapped[str] = mapped_column(String(36))
    worktree: Mapped[str] = mapped_column(String(128), default=SHARED_CWD)
    key_kind: Mapped[str] = mapped_column(String(8))
    key_value: Mapped[str] = mapped_column(String(1024))
    artifact_id: Mapped[str] = mapped_column(String(16), index=True)

    __table_args__ = (
        UniqueConstraint(*_SCOPE_COLUMNS, "key_kind", "key_value", name="ux_artifact_key"),
    )


class ArtifactHeadRow(Base, TimestampMixin, UserMixin):
    """The current revision of an artifact — the only mutable pointer in the set.

    One row per artifact, keyed by ``artifact_id`` so a delivery's
    compare-and-set (``UPDATE … WHERE artifact_id=? AND revision_id=?``) touches
    exactly one narrow row. Row count, not affected-rows plumbing, is what
    detects a concurrent delivery: a runtime can emit several ``tool_use`` blocks
    in one turn, and two deliveries racing on the same artifact must not both
    win. Works identically on SQLite (desktop) and PostgreSQL (cloud) — no
    ``SELECT … FOR UPDATE`` needed.
    """

    __tablename__ = "valuz_artifact_head"

    artifact_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    revision_id: Mapped[str] = mapped_column(String(16))
    # Denormalized from the head revision so the common "list artifacts with
    # their version" read does not need to join the revision table.
    version_no: Mapped[int] = mapped_column(Integer, default=1)


class ArtifactRevisionRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """One generation of an artifact.

    Append-only in the sense that matters: what a generation WAS — its parent,
    its version, its content, where it came from — is never rewritten and never
    deleted. ``status`` is the one exception and is not part of that record: it
    tracks whether the bytes are still on disk, which genuinely changes when a
    worktree is removed out from under them.

    ``source_tool_call_id`` is best-effort audit data and is deliberately NOT
    part of any unique constraint. The MCP server hands tool handlers
    ``(name, arguments)`` and drops ``_meta``/``progressToken``, so the calling
    runtime's tool_use id is not recoverable at the point of writing (see
    ``modules/genui/ids.py``, which reconstructs it heuristically for streaming
    and could not do so reliably here — a replay and a genuine second delivery
    of the same path carry identical arguments). Idempotency therefore keys on
    the content: ``ux_artifact_revision_content``. That also absorbs a transport
    retry, which would otherwise mint a phantom version every time a response
    was lost after the handler committed.
    """

    __tablename__ = "valuz_artifact_revision"

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    artifact_id: Mapped[str] = mapped_column(String(16), index=True)
    # NULL for v1. The column carries the full DAG shape even though the first
    # write policy is strictly linear (a delivery whose base is not the current
    # head is refused rather than branched).
    parent_revision_id: Mapped[str | None] = mapped_column(String(16))
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    # Where this generation came from. A reference, NOT an ownership link:
    # deleting the conversation must not take the deliverable with it.
    source_session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    source_tool_call_id: Mapped[str | None] = mapped_column(String(64))
    file_name: Mapped[str] = mapped_column(String(512))
    file_format: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    renderer_version: Mapped[str | None] = mapped_column(String(32))
    content_id: Mapped[str] = mapped_column(String(36), index=True)
    # Absolute path of this generation's snapshot. Absolute, not relative,
    # because it is the file's identity for the unified resolver: the same
    # string is what the model reads inside the sandbox (which mounts the host
    # path), what a ``valuz-file://`` link in prose carries, and what
    # ``/v1/files/resolve`` exchanges for a local path or a presigned URL.
    # Desktop data-dir moves are handled by the existing whole-DB prefix rewrite
    # in ``boot/migrate_data_dir.py``; the scope-relative *key* above is what
    # stays stable across such a move.
    abs_path: Mapped[str | None] = mapped_column(Text)
    # Denormalized from the content row purely so the idempotency constraint
    # below can exist without a join.
    content_hash: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(16), default=REVISION_STATUS_READY)
    # Reserved for a backfill from ``valuz_session_artifact``: the legacy row a
    # revision was built from, which is what would make such a job re-runnable.
    #
    # Nothing writes it today — the backfill was written, measured against real
    # data (one row in production), and removed as not worth its weight. The
    # column stays because it is the ONLY part of that job that costs a
    # migration; keeping it means a later backfill is code alone, on a schedule
    # of its own choosing. The old table is not dropped either, so the data is
    # still there to migrate whenever it becomes worth doing.
    legacy_row_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_by: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_artifact_revision_chain", "artifact_id", "version_no"),
        # Idempotency, per the class docstring.
        UniqueConstraint(
            "user_id", "artifact_id", "content_hash", name="ux_artifact_revision_content"
        ),
    )


class ArtifactContentRow(Base, PrimaryKeyMixin, TimestampMixin, UserMixin):
    """An immutable snapshot's identity and canonical location.

    NOT a deduplicating blob store, and the distinction is load-bearing for
    anyone sizing storage: rows carry ``content_hash`` but identical bytes are
    still written once per revision. Content-addressed storage and an
    agent-readable layout are mutually exclusive on the deployment's object
    mount (no hard links), and the readable layout won — the agent reads a past
    version with a plain ``Read`` on ``.artifact/<id>/v1/<name>`` instead of
    needing a materialize tool.

    What the table still earns its place for:

    1. ``storage_key`` is a location indirection — the escape hatch if content
       ever has to move out of the working directory (for true immutability),
       with no change to the revision table;
    2. ``content_inline`` gives small generated payloads (OpenUI/A2UI JSON,
       short markdown) somewhere to live that is not a pile of mostly-NULL
       columns on every revision;
    3. it answers "are these two versions the same bytes" without reading files.
    """

    __tablename__ = "valuz_artifact_content"

    storage_kind: Mapped[str] = mapped_column(String(16), default=STORAGE_KIND_FILE)
    # Absolute path where the bytes were first written (see the note on
    # ``ArtifactRevisionRow.abs_path``). NULL when ``storage_kind="inline"``.
    storage_key: Mapped[str | None] = mapped_column(Text)
    content_inline: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(80))
    byte_size: Mapped[int] = mapped_column(BigInteger, default=0)
    mime_type: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (Index("ix_artifact_content_hash", "user_id", "content_hash"),)
