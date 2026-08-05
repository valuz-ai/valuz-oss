"""Artifact persistence. Owns the identity lookup and the head compare-and-set.

Deliberately does no filesystem work: hashing, snapshotting and scope
resolution belong to the caller (the delivery handler), which has to do them
before it knows whether a write is even needed. What lives here is everything
that must be decided against the database — which artifact a delivery belongs
to, whether this exact content is already recorded, and who wins when two
deliveries race.

Every method takes ``user_id`` explicitly. Nothing reads an ambient context.
"""

from __future__ import annotations

import posixpath
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from valuz_agent.infra.time_utils import now_ms
from valuz_agent.modules.artifacts.models import (
    KEY_KIND_NAME,
    KEY_KIND_PATH,
    REVISION_STATUS_MISSING,
    REVISION_STATUS_READY,
    SHARED_CWD,
    STORAGE_KIND_FILE,
    ArtifactContentRow,
    ArtifactHeadRow,
    ArtifactKeyRow,
    ArtifactRevisionRow,
    ArtifactRow,
)

# Crockford-ish base32 without I/L/O/U: these ids end up in filesystem paths a
# human reads in a file tree, so drop the glyphs that get misread aloud or
# mistyped. 8 chars = 40 bits, scoped per owner+project — collisions are not a
# practical concern, and ``new_artifact`` retries on the unique index anyway.
_ID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _short_id(length: int) -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(length))


def normalize_name_key(display_name: str) -> str:
    """Fold a display name to its lookup form.

    Case and whitespace only. Deliberately NOT unicode-normalizing or stripping
    punctuation: the wider the folding, the more distinct deliverables collapse
    onto one artifact, and a wrong *merge* corrupts version history in a way a
    wrong *fork* does not.
    """
    return re.sub(r"\s+", " ", display_name.strip()).casefold()


def rel_dir_of(rel_path: str) -> str:
    """The directory part of a scope-relative path (``""`` at the root)."""
    return posixpath.dirname(rel_path.strip().lstrip("/"))


def name_key_value(rel_dir: str, display_name: str) -> str:
    """The name key: a normalized display name, qualified by its directory.

    Unqualified names are not unique inside a scope — ``marketing/report.md``
    and ``finance/report.md`` share a basename while being unrelated
    deliverables — and a name collision does not fork, it *merges*, appending
    one deliverable to another's history. Qualifying keeps the case this
    fallback is for (a rewrite renamed beside its predecessor) and lets the
    rarer cross-directory rename fork instead.
    """
    folded = normalize_name_key(display_name)
    if not folded:
        return ""
    return f"{rel_dir}/{folded}" if rel_dir else folded


@dataclass(frozen=True)
class Scope:
    """The cwd a delivery happened in — a project cwd, or a worktree's.

    Artifact identity is scoped to it, so the same file name in a worktree and
    on the main line are separate artifacts.
    """

    user_id: str
    project_id: str
    worktree: str = SHARED_CWD


class ArtifactDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ---- Identity ----

    async def find_by_keys(
        self, scope: Scope, *, rel_path: str, display_name: str
    ) -> ArtifactRow | None:
        """Resolve a delivery to its artifact: relative path first, then name.

        Archived artifacts are invisible here, so re-delivering a name that was
        archived starts a fresh artifact instead of resurrecting a history the
        user already put away.
        """
        for kind, value in (
            (KEY_KIND_PATH, rel_path),
            (KEY_KIND_NAME, name_key_value(rel_dir_of(rel_path), display_name)),
        ):
            if not value:
                continue
            row = (
                await self._db.execute(
                    select(ArtifactRow)
                    .join(ArtifactKeyRow, ArtifactKeyRow.artifact_id == ArtifactRow.id)
                    .where(
                        ArtifactKeyRow.user_id == scope.user_id,
                        ArtifactKeyRow.project_id == scope.project_id,
                        ArtifactKeyRow.worktree == scope.worktree,
                        ArtifactKeyRow.key_kind == kind,
                        ArtifactKeyRow.key_value == value,
                        ArtifactRow.archived_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                return row
        return None

    async def create_artifact(
        self, scope: Scope, *, kind: str, display_name: str, rel_path: str
    ) -> ArtifactRow:
        """Create an artifact and its two lookup keys.

        The path key **takes over** any existing one for that path rather than
        being inserted blindly. A path holds one artifact at a time — the one
        currently living there — and a caller reaches this method for an
        already-keyed path exactly when it means "same file, different
        deliverable" (the ``asNewArtifact`` escape hatch). Inserting would
        violate the key's uniqueness and fail the whole batch; taking over
        leaves the previous artifact its history and its name key, and points
        future deliveries of that path at the new one.

        The name key is best-effort by contrast: another artifact in the same
        directory may already hold that name, and losing a *fallback* key is not
        a reason to fail a delivery whose path key is settled.
        """
        rel_dir = rel_dir_of(rel_path)
        row = ArtifactRow(
            id=_short_id(8),
            user_id=scope.user_id,
            project_id=scope.project_id,
            worktree=scope.worktree,
            kind=kind,
            display_name=display_name,
            rel_dir=rel_dir,
        )
        self._db.add(row)
        await self._db.flush()

        existing_path_key = (
            await self._db.execute(
                select(ArtifactKeyRow).where(
                    ArtifactKeyRow.user_id == scope.user_id,
                    ArtifactKeyRow.project_id == scope.project_id,
                    ArtifactKeyRow.worktree == scope.worktree,
                    ArtifactKeyRow.key_kind == KEY_KIND_PATH,
                    ArtifactKeyRow.key_value == rel_path,
                )
            )
        ).scalar_one_or_none()
        if existing_path_key is not None:
            existing_path_key.artifact_id = row.id
            existing_path_key.updated_at = now_ms()
        else:
            self._db.add(
                ArtifactKeyRow(
                    user_id=scope.user_id,
                    project_id=scope.project_id,
                    worktree=scope.worktree,
                    key_kind=KEY_KIND_PATH,
                    key_value=rel_path,
                    artifact_id=row.id,
                )
            )
        await self._db.flush()
        await self.try_add_name_key(
            scope, artifact_id=row.id, display_name=display_name, rel_dir=rel_dir
        )
        return row

    async def get_artifact_in_scope(self, scope: Scope, artifact_id: str) -> ArtifactRow | None:
        """An artifact by id, but only if it belongs to this exact scope.

        Guards the caller-supplied ``artifactId``: an id is a bare string the
        agent could have carried over from another project, another worktree, or
        an artifact it was shown but does not own. Resolving it without the
        scope check would let a delivery append a version to somebody else's
        deliverable.
        """
        return (
            await self._db.execute(
                select(ArtifactRow).where(
                    ArtifactRow.id == artifact_id,
                    ArtifactRow.user_id == scope.user_id,
                    ArtifactRow.project_id == scope.project_id,
                    ArtifactRow.worktree == scope.worktree,
                    ArtifactRow.archived_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def adopt_delivery(
        self, scope: Scope, artifact: ArtifactRow, *, rel_path: str, display_name: str
    ) -> None:
        """Point an existing artifact at where this delivery actually landed.

        A deliverable that gets renamed or moved is still that deliverable, and
        the next delivery has to find it at its NEW path — so the path key
        follows, the label follows, and the directory follows (name keys are
        directory-qualified, so a move without it would register the next name
        against a directory the file has left).

        Only writes what changed. The common case is a re-delivery to the same
        path under the same name, where this does nothing at all.
        """
        rel_dir = rel_dir_of(rel_path)
        if artifact.display_name != display_name:
            artifact.display_name = display_name
            artifact.updated_at = now_ms()
        if artifact.rel_dir != rel_dir:
            artifact.rel_dir = rel_dir
            artifact.updated_at = now_ms()

        existing = (
            await self._db.execute(
                select(ArtifactKeyRow).where(
                    ArtifactKeyRow.user_id == scope.user_id,
                    ArtifactKeyRow.project_id == scope.project_id,
                    ArtifactKeyRow.worktree == scope.worktree,
                    ArtifactKeyRow.key_kind == KEY_KIND_PATH,
                    ArtifactKeyRow.key_value == rel_path,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            self._db.add(
                ArtifactKeyRow(
                    user_id=scope.user_id,
                    project_id=scope.project_id,
                    worktree=scope.worktree,
                    key_kind=KEY_KIND_PATH,
                    key_value=rel_path,
                    artifact_id=artifact.id,
                )
            )
        elif existing.artifact_id != artifact.id:
            existing.artifact_id = artifact.id
            existing.updated_at = now_ms()
        await self._db.flush()
        await self.try_add_name_key(
            scope, artifact_id=artifact.id, display_name=display_name, rel_dir=rel_dir
        )

    async def try_add_name_key(
        self, scope: Scope, *, artifact_id: str, display_name: str, rel_dir: str
    ) -> bool:
        """Point the directory-qualified display name at ``artifact_id``, if free.

        Returns False when that name already belongs to another artifact in the
        same directory — the caller keeps going, since the *path* key is what
        makes a delivery findable and the name key is only a fallback. Used both
        at creation and on rename, which is why it is not folded into
        ``create_artifact``.
        """
        value = name_key_value(rel_dir, display_name)
        if not value:
            return False
        taken = (
            await self._db.execute(
                select(ArtifactKeyRow.artifact_id).where(
                    ArtifactKeyRow.user_id == scope.user_id,
                    ArtifactKeyRow.project_id == scope.project_id,
                    ArtifactKeyRow.worktree == scope.worktree,
                    ArtifactKeyRow.key_kind == KEY_KIND_NAME,
                    ArtifactKeyRow.key_value == value,
                )
            )
        ).scalar_one_or_none()
        if taken is not None:
            return taken == artifact_id
        self._db.add(
            ArtifactKeyRow(
                user_id=scope.user_id,
                project_id=scope.project_id,
                worktree=scope.worktree,
                key_kind=KEY_KIND_NAME,
                key_value=value,
                artifact_id=artifact_id,
            )
        )
        await self._db.flush()
        return True

    # ---- Content ----

    async def find_content_by_hash(
        self, user_id: str, content_hash: str
    ) -> ArtifactContentRow | None:
        """An existing snapshot of these exact bytes, if the owner has one.

        Reuse is logical, not physical: the caller still writes its own copy
        into the revision directory (see ``ArtifactContentRow``'s docstring), so
        this only avoids a duplicate identity row.
        """
        return (
            (
                await self._db.execute(
                    select(ArtifactContentRow).where(
                        ArtifactContentRow.user_id == user_id,
                        ArtifactContentRow.content_hash == content_hash,
                    )
                )
            )
            .scalars()
            .first()
        )

    async def create_content(
        self,
        user_id: str,
        *,
        content_hash: str,
        byte_size: int,
        mime_type: str | None,
        storage_key: str | None = None,
        content_inline: str | None = None,
        storage_kind: str = STORAGE_KIND_FILE,
    ) -> ArtifactContentRow:
        row = ArtifactContentRow(
            user_id=user_id,
            storage_kind=storage_kind,
            storage_key=storage_key,
            content_inline=content_inline,
            content_hash=content_hash,
            byte_size=byte_size,
            mime_type=mime_type,
        )
        self._db.add(row)
        await self._db.flush()
        return row

    # ---- Revisions ----

    async def get_head(self, user_id: str, artifact_id: str) -> ArtifactHeadRow | None:
        return (
            await self._db.execute(
                select(ArtifactHeadRow).where(
                    ArtifactHeadRow.user_id == user_id,
                    ArtifactHeadRow.artifact_id == artifact_id,
                )
            )
        ).scalar_one_or_none()

    async def find_revision_by_content(
        self, user_id: str, artifact_id: str, content_hash: str
    ) -> ArtifactRevisionRow | None:
        """The revision already holding these bytes — the idempotency lookup.

        A hit means this delivery is a replay (or a transport retry), and the
        caller returns the existing revision instead of minting a version whose
        only difference is the clock.
        """
        return (
            await self._db.execute(
                select(ArtifactRevisionRow).where(
                    ArtifactRevisionRow.user_id == user_id,
                    ArtifactRevisionRow.artifact_id == artifact_id,
                    ArtifactRevisionRow.content_hash == content_hash,
                )
            )
        ).scalar_one_or_none()

    async def append_revision(
        self,
        user_id: str,
        artifact_id: str,
        *,
        expected_head_revision_id: str | None,
        content: ArtifactContentRow,
        file_name: str,
        abs_path: str | None,
        file_format: str | None = None,
        source_session_id: str | None = None,
        source_tool_call_id: str | None = None,
        status: str = REVISION_STATUS_READY,
        legacy_row_id: str | None = None,
        created_by: str | None = None,
    ) -> ArtifactRevisionRow | None:
        """Append a generation and move the head, or return None on a lost race.

        ``expected_head_revision_id`` is the revision the caller built on:
        ``None`` for the first ever revision, otherwise the head it read. The
        head moves with a conditional UPDATE, so a concurrent delivery that got
        there first leaves this one with zero affected rows — and the caller can
        re-read and retry rather than silently forking the history or clobbering
        the winner.

        **The head moves before the revision is written**, which is why the
        revision id is minted up front. Written the other way round, a delivery
        that lost the race at the UPDATE would already have inserted its
        revision, and the caller's retry — same artifact, same bytes — would
        then collide with the content-hash idempotency constraint and fail a
        delivery that should have succeeded. Ordering it this way means a
        refused attempt leaves nothing at all behind. The two writes commit
        together, so the window where the head names a not-yet-inserted revision
        never escapes this transaction.
        """
        head = await self.get_head(user_id, artifact_id)
        current = head.revision_id if head is not None else None
        if current != expected_head_revision_id:
            return None

        version_no = (head.version_no + 1) if head is not None else 1
        revision_id = _short_id(10)

        if head is None:
            # First generation. The primary key on ``artifact_id`` is the race
            # guard here; a genuine collision would need two deliveries to have
            # separately created the same artifact, which the key table's own
            # uniqueness already rules out.
            self._db.add(
                ArtifactHeadRow(
                    user_id=user_id,
                    artifact_id=artifact_id,
                    revision_id=revision_id,
                    version_no=version_no,
                )
            )
            await self._db.flush()
        else:
            result = await self._db.execute(
                update(ArtifactHeadRow)
                .where(
                    ArtifactHeadRow.artifact_id == artifact_id,
                    ArtifactHeadRow.user_id == user_id,
                    # The compare half of compare-and-set.
                    ArtifactHeadRow.revision_id == current,
                )
                .values(revision_id=revision_id, version_no=version_no, updated_at=now_ms())
            )
            # ``rowcount`` on an UPDATE is how the compare-and-set reports its
            # verdict: 0 means another delivery moved the head between our read
            # and this write. Typed as the generic ``Result`` here, which does
            # not expose it — every DBAPI backing this does.
            if result.rowcount == 0:  # type: ignore[attr-defined]
                return None

        revision = ArtifactRevisionRow(
            id=revision_id,
            user_id=user_id,
            artifact_id=artifact_id,
            parent_revision_id=current,
            version_no=version_no,
            source_session_id=source_session_id,
            source_tool_call_id=source_tool_call_id,
            file_name=file_name,
            file_format=file_format,
            content_id=content.id,
            content_hash=content.content_hash,
            abs_path=abs_path,
            status=status,
            legacy_row_id=legacy_row_id,
            created_by=created_by,
        )
        self._db.add(revision)
        await self._db.flush()
        return revision

    # ---- Reads ----

    async def list_scope_heads(
        self, scope: Scope, *, limit: int = 50
    ) -> list[tuple[ArtifactRow, ArtifactHeadRow, ArtifactRevisionRow]]:
        """Artifacts in a scope with their current revision, most recent first.

        Feeds both the per-scope listing API and the per-turn context block, so
        the model and the panel can never disagree about what "the current
        version" is.
        """
        stmt = (
            select(ArtifactRow, ArtifactHeadRow, ArtifactRevisionRow)
            .join(ArtifactHeadRow, ArtifactHeadRow.artifact_id == ArtifactRow.id)
            .join(ArtifactRevisionRow, ArtifactRevisionRow.id == ArtifactHeadRow.revision_id)
            .where(
                ArtifactRow.user_id == scope.user_id,
                ArtifactRow.project_id == scope.project_id,
                ArtifactRow.worktree == scope.worktree,
                ArtifactRow.archived_at.is_(None),
            )
            .order_by(ArtifactHeadRow.updated_at.desc())
            .limit(limit)
        )
        return [tuple(r) for r in (await self._db.execute(stmt)).all()]

    async def count_scope_artifacts(self, scope: Scope) -> int:
        """How many live artifacts the scope holds.

        Separate from ``list_scope_heads`` because the per-turn context block
        shows a capped list but has to say how many were left out — a truncated
        list that looks complete would have the model conclude a deliverable it
        cannot see does not exist.
        """
        stmt = (
            select(func.count())
            .select_from(ArtifactRow)
            .where(
                ArtifactRow.user_id == scope.user_id,
                ArtifactRow.project_id == scope.project_id,
                ArtifactRow.worktree == scope.worktree,
                ArtifactRow.archived_at.is_(None),
            )
        )
        return int((await self._db.execute(stmt)).scalar_one())

    async def list_revisions(self, user_id: str, artifact_id: str) -> list[ArtifactRevisionRow]:
        """An artifact's full history, oldest first."""
        stmt = (
            select(ArtifactRevisionRow)
            .where(
                ArtifactRevisionRow.user_id == user_id,
                ArtifactRevisionRow.artifact_id == artifact_id,
            )
            .order_by(ArtifactRevisionRow.version_no)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def list_session_revisions(
        self, user_id: str, session_id: str
    ) -> list[ArtifactRevisionRow]:
        """Revisions produced by one session, oldest first.

        What the existing per-session "生成文件" endpoint projects once it reads
        from these tables.
        """
        stmt = (
            select(ArtifactRevisionRow)
            .where(
                ArtifactRevisionRow.user_id == user_id,
                ArtifactRevisionRow.source_session_id == session_id,
            )
            .order_by(ArtifactRevisionRow.created_at)
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_revision(self, user_id: str, revision_id: str) -> ArtifactRevisionRow | None:
        return (
            await self._db.execute(
                select(ArtifactRevisionRow).where(
                    ArtifactRevisionRow.user_id == user_id,
                    ArtifactRevisionRow.id == revision_id,
                )
            )
        ).scalar_one_or_none()

    async def get_contents(
        self, user_id: str, content_ids: Sequence[str]
    ) -> dict[str, ArtifactContentRow]:
        """Load many contents at once, keyed by id.

        Every listing endpoint needs the content row behind each revision it
        returns. Fetching them one at a time is a round trip per row — cheap on
        the desktop's local SQLite, a per-item network hop on the cloud's
        Postgres, and paid on every panel open.
        """
        ids = list(dict.fromkeys(content_ids))
        if not ids:
            return {}
        rows = (
            await self._db.execute(
                select(ArtifactContentRow).where(
                    ArtifactContentRow.user_id == user_id,
                    ArtifactContentRow.id.in_(ids),
                )
            )
        ).scalars()
        return {row.id: row for row in rows}

    async def get_heads(
        self, user_id: str, artifact_ids: Sequence[str]
    ) -> dict[str, ArtifactHeadRow]:
        """Load many heads at once, keyed by artifact id. See ``get_contents``."""
        ids = list(dict.fromkeys(artifact_ids))
        if not ids:
            return {}
        rows = (
            await self._db.execute(
                select(ArtifactHeadRow).where(
                    ArtifactHeadRow.user_id == user_id,
                    ArtifactHeadRow.artifact_id.in_(ids),
                )
            )
        ).scalars()
        return {row.artifact_id: row for row in rows}

    async def get_content(self, user_id: str, content_id: str) -> ArtifactContentRow | None:
        return (
            await self._db.execute(
                select(ArtifactContentRow).where(
                    ArtifactContentRow.user_id == user_id,
                    ArtifactContentRow.id == content_id,
                )
            )
        ).scalar_one_or_none()

    async def get_artifact(self, user_id: str, artifact_id: str) -> ArtifactRow | None:
        return (
            await self._db.execute(
                select(ArtifactRow).where(
                    ArtifactRow.user_id == user_id, ArtifactRow.id == artifact_id
                )
            )
        ).scalar_one_or_none()

    # ---- Mutations that are not new generations ----

    async def rename(
        self, scope: Scope, *, artifact_id: str, display_name: str
    ) -> ArtifactRow | None:
        """Change the label. Explicitly NOT a new revision — no bytes changed.

        Adds a lookup key for the new name (when free) so a later delivery under
        that name finds this artifact, and leaves the old key in place so one
        under the old name still does.
        """
        row = await self.get_artifact(scope.user_id, artifact_id)
        if row is None:
            return None
        row.display_name = display_name
        row.updated_at = now_ms()
        await self.try_add_name_key(
            scope, artifact_id=artifact_id, display_name=display_name, rel_dir=row.rel_dir
        )
        await self._db.flush()
        return row

    async def archive_scope(self, scope: Scope) -> int:
        """Retire every artifact in a scope whose files have just been deleted.

        Used when a worktree is removed: the snapshots lived inside it, so their
        bytes are gone. The rows are deliberately KEPT — a deliverable that
        existed is part of the record, and a dangling reference the UI can
        explain ("delivered in worktree feat-x, no longer available") is better
        than a 404 from a link the user still holds.

        Revisions are stamped ``missing`` rather than deleted. Their
        ``abs_path`` is left in place: it is now a forensic breadcrumb rather
        than an address, and every read path gates on ``status`` before offering
        the path as openable.

        Returns how many artifacts were retired.
        """
        rows = await self.list_scope_heads(scope, limit=10_000)
        for artifact, _head, _revision in rows:
            revisions = await self.list_revisions(scope.user_id, artifact.id)
            for revision in revisions:
                if revision.status != REVISION_STATUS_MISSING:
                    revision.status = REVISION_STATUS_MISSING
                    revision.updated_at = now_ms()
            await self.archive(scope.user_id, artifact.id)
        await self._db.flush()
        return len(rows)

    async def archive(self, user_id: str, artifact_id: str) -> ArtifactRow | None:
        """Retire an artifact and free its keys.

        Keys are deleted rather than left dangling so the name and path become
        available again — an archived deliverable must not be resurrected by a
        later delivery that happens to reuse its name.
        """
        row = await self.get_artifact(user_id, artifact_id)
        if row is None:
            return None
        row.archived_at = now_ms()
        row.updated_at = now_ms()
        keys = (
            (
                await self._db.execute(
                    select(ArtifactKeyRow).where(
                        ArtifactKeyRow.user_id == user_id,
                        ArtifactKeyRow.artifact_id == artifact_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for key in keys:
            await self._db.delete(key)
        await self._db.flush()
        return row
