"""One-time migration: move the data dir from ``~/.valuz/app`` to ``~/.valuz-oss``.

The data root was flattened and renamed: the old ``~/.valuz/app`` tree (with
its sibling ``~/.valuz/kb``) becomes the new flat ``~/.valuz-oss`` root (``kb``
folds in as ``~/.valuz-oss/kb``). This step carries an existing install across
that cutover ONCE, with the same checkpoint/copy/verify discipline as
``kernel_db_colocate.py``:

1. BAIL — no-op when the store does not live in the local SQLite files this
   step manipulates (``database_url`` / ``kernel_database_url`` configured, e.g.
   Postgres), or when the cutover already completed at the current version.
2. CHECKPOINT — fold each old DB's WAL into its main file
   (``PRAGMA wal_checkpoint(TRUNCATE)``) so the copied ``*.db`` is self-contained.
3. COPY — clear any partial prior copy (keeping the single-writer lock) and
   ``copytree`` the old tree into the new root (symlinks preserved). The old
   tree is left untouched as a fallback — never deleted.
4. REWRITE — rewrite the leading absolute-path prefix inside the COPIED
   ``valuz.db`` / ``kernel.db`` using stdlib ``sqlite3``. The rewrite is
   SCHEMA-DRIVEN: every text/JSON column of every table is swept (so no
   path-bearing column can be missed), the match is ANCHORED to a path boundary
   (``<prefix>/`` or the bare path) so a sibling like ``~/.valuz/apple`` is never
   mangled, and external user paths (e.g. ``~/Downloads/...``) pass through
   untouched. It works verbatim inside JSON columns because the absolute path
   appears literally.
5. REPOINT — repair every project cwd's skill symlinks that point into the old
   root, both for managed chat projects (copied under the new root) and for
   user/external projects (which live OUTSIDE the data dir and are repaired in
   place).
6. VERIFY — assert the copied DBs exist and no text column still carries the old
   prefix. Only then drop the marker file (stamped with the migration version).

A marker stamped with an OLDER version triggers an in-place rewrite sweep (no
re-copy) so an install migrated by an earlier, less-complete revision self-heals
on the next boot.

Runs synchronously off the event loop, BEFORE any engine opens the files and
under the single-writer lock acquired earlier in boot. The marker file is the
authoritative "done" signal, and every step is re-entrant, so a run interrupted
mid-flight (crash / power loss) is completed by the next boot rather than left
half-migrated.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
from pathlib import Path

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import fs_registry

logger = logging.getLogger(__name__)

# Bumped whenever the rewrite grows to cover more columns/tables, so an install
# migrated by an older revision self-heals in place. v1 used a hand-maintained
# column allowlist (which missed ``valuz_agent`` / ``valuz_session_artifact`` /
# kernel ``events`` + ``messages``); v2 sweeps every text column generically.
_MIGRATION_VERSION = 2

# Name of the active log directory (mirrors
# ``settings.log_file_path.parent``). It holds the
# RUNNING boot process's own open ``backend.log`` — structured logging is
# configured before this migration runs — and on Windows an open file can be
# neither deleted nor overwritten ([WinError 32] sharing violation; POSIX allows
# both, which is why this only ever bit Windows upgrades). Logs are disposable
# runtime output, not migratable data, so the cutover skips them in BOTH
# directions: ``_COPY_IGNORE`` keeps the copy from overwriting the open
# ``backend.log``, and ``_reset_partial_copy`` keeps rmtree from trying to
# delete it.
_LOGS_DIRNAME = "logs"

# Lock/journal noise that must NOT ride along into the copied tree. The WAL is
# checkpointed into the main DB before the copy, so the sidecars are redundant;
# ``logs`` is excluded for the open-file reason documented above.
_COPY_IGNORE = shutil.ignore_patterns(
    ".single-writer.lock", "*.db-wal", "*.db-shm", _LOGS_DIRNAME
)
_LOCK_FILENAME = ".single-writer.lock"

_MARKER_FILENAME = ".migrated-from-valuz-app"
_USER_SCOPE_MARKER_FILENAME = ".migrated-from-unscoped-data-root"
_USER_SCOPE_MIGRATION_VERSION = 3
_USER_SCOPED_DIRS = (
    "projects",
    "docs",
    "attachments",
    "kb",
    "memories",
    "secrets",
    "browser-chrome",
    "skill-creator",
)

# Declared-type hints for columns that can hold a path string. BLOB columns
# (e.g. langgraph ``checkpoints``/``writes`` payloads) are skipped — string
# REPLACE would risk corrupting binary, and they carry no host paths.
_TEXT_AFFINITY_HINTS = ("TEXT", "CHAR", "CLOB", "JSON")

# Skill-symlink dirs under each project cwd that may point into the old root.
_SKILL_LINK_DIRS = (".claude/skills", ".agents/skills")


def migrate_legacy_data_dir() -> None:
    """Move a pre-cutover ``~/.valuz/app`` install into ``~/.valuz-oss``.

    No-op when an external DB is configured or when the cutover already
    completed at the current version. A marker from an older version triggers an
    in-place rewrite sweep. Raises on a verification failure (the old tree stays
    intact) so boot fails loudly rather than running on half-migrated paths.
    """
    # An external/colocated store means the live data is not in the local SQLite
    # files this step copies + rewrites — there is nothing to migrate.
    if settings.database_url or settings.kernel_database_url:
        return

    new_root = settings.data_dir
    # Only the DEFAULT new root participates in the ``~/.valuz/app`` cutover. A
    # custom ``VALUZ_DATA_DIR`` (tests, bespoke installs) is NOT the rename
    # target — skip, so we never copy the real ``~/.valuz/app`` into an unrelated
    # data dir. (The desktop app sets ``VALUZ_DATA_DIR`` to this same default, so
    # it still migrates.)
    if new_root != Path.home() / ".valuz-oss":
        return

    old_app = Path.home() / ".valuz" / "app"
    old_kb = Path.home() / ".valuz" / "kb"
    marker = new_root / _MARKER_FILENAME
    host_db = new_root / settings.db_filename
    kernel_db = new_root / settings.kernel_db_filename
    old_app_prefix = str(old_app)
    pairs = ((old_app_prefix, str(new_root)), (str(old_kb), str(new_root / "kb")))

    if marker.exists():
        if _marker_version(marker) >= _MIGRATION_VERSION:
            return
        # Self-heal: an earlier, less-complete version cut over but left some
        # path-bearing columns un-rewritten. Sweep them in place (no re-copy).
        logger.warning(
            "data-dir migration: upgrading marker to v%d — in-place path sweep",
            _MIGRATION_VERSION,
        )
        host_n = _rewrite_all(host_db, pairs)
        kernel_n = _rewrite_all(kernel_db, pairs)
        repaired = _repoint_symlinks(new_root, host_db, old_app_prefix, str(new_root))
        _assert_dbs_clean(new_root, old_app_prefix)
        _write_marker(marker, old_app)
        logger.warning(
            "data-dir migration: sweep done — rewrote %s; repaired %d symlink(s)",
            _fmt_counts({**host_n, **kernel_n}),
            repaired,
        )
        return

    if not old_app.exists():
        # Fresh install — no legacy tree to carry over.
        return

    logger.warning(
        "data-dir migration: copying %s -> %s (old tree kept as fallback)",
        old_app,
        new_root,
    )

    # CHECKPOINT — fold each old DB's WAL into the main file first.
    for name in (settings.db_filename, settings.kernel_db_filename):
        _checkpoint_wal(old_app / name)

    # COPY — start from a clean destination so a partial prior copy can't leave
    # stale files / symlink conflicts (re-entrancy).
    _reset_partial_copy(new_root)
    _copy_tree(old_app, new_root)
    if old_kb.exists():
        _copy_tree(old_kb, new_root / "kb")

    # REWRITE (schema-driven, anchored to a path boundary).
    host_n = _rewrite_all(host_db, pairs)
    kernel_n = _rewrite_all(kernel_db, pairs)

    # REPOINT skill symlinks.
    repaired = _repoint_symlinks(new_root, host_db, old_app_prefix, str(new_root))

    # VERIFY — raise (old tree intact) before the marker is written.
    _assert_carried_over(old_app, new_root)
    _assert_dbs_clean(new_root, old_app_prefix)

    logger.warning(
        "data-dir migration: done — rewrote %s; repaired %d symlink(s); old tree %s retained",
        _fmt_counts({**host_n, **kernel_n}),
        repaired,
        old_app,
    )
    _write_marker(marker, old_app)


def migrate_unscoped_data_root() -> None:
    """Copy an unscoped data root into a templated per-user data root once.

    When ``VALUZ_DATA_DIR`` contains ``{user_id}``, deployments are asking for
    user-owned files to live under the expanded template. Existing shared roots
    may still have files directly under the template parent (``projects/``,
    ``secrets/``, etc.), so carry those forward before Alembic opens the DB.

    OSS defaults to ``~/.valuz-oss`` without ``{user_id}``; in that shape the
    root is already the final data dir, so this migration is intentionally a
    no-op.
    """
    if settings.database_url or settings.kernel_database_url:
        return

    if "{user_id}" not in str(settings.data_dir):
        return

    root = Path(str(settings.data_dir).replace("{user_id}", "")).expanduser()
    if not root.is_dir():
        return

    if settings.deployment_type == "local":
        from valuz_agent.infra.local_identity import resolve_local_user_id

        user_ids = [resolve_local_user_id()]
        mode = "local"
    else:
        user_ids = _user_ids_in_sqlite(root / settings.db_filename)
        mode = "cloud"

    if not user_ids:
        return

    for name in (settings.db_filename, settings.kernel_db_filename):
        _checkpoint_wal(root / name)

    migrated = 0
    for user_id in user_ids:
        if _migrate_unscoped_user_root(root, user_id, mode=mode):
            migrated += 1

    if migrated:
        logger.warning(
            "user-scope migration: migrated %d owner(s); unscoped root %s retained",
            migrated,
            root,
        )


def _migrate_unscoped_user_root(root: Path, user_id: str, *, mode: str) -> bool:
    target = fs_registry.data_dir(user_id)
    marker = target / _USER_SCOPE_MARKER_FILENAME
    if marker.exists() and _marker_version(marker) >= _USER_SCOPE_MIGRATION_VERSION:
        return False

    copy_plan = (
        _local_unscoped_copy_plan(root)
        if mode == "local"
        else _cloud_unscoped_copy_plan(root, user_id)
    )
    if not copy_plan:
        target.mkdir(parents=True, exist_ok=True)
        _rewrite_user_scoped_db_paths(root, user_id, mode=mode)
        _write_user_scope_marker(marker, root)
        return True

    logger.warning(
        "user-scope migration: copying owner %s files %s -> %s",
        user_id,
        root,
        target,
    )

    target.mkdir(parents=True, exist_ok=True)
    for src, rel in copy_plan:
        _copy_root_entry(src, target / rel)

    host_n, kernel_n = _rewrite_user_scoped_db_paths(root, user_id, mode=mode)
    _write_user_scope_marker(marker, root)

    logger.warning(
        "user-scope migration: owner %s done — rewrote %s",
        user_id,
        _fmt_counts({**host_n, **kernel_n}),
    )
    return True


def _local_unscoped_copy_plan(root: Path) -> list[tuple[Path, Path]]:
    user_dir_name = None
    try:
        from valuz_agent.infra.local_identity import resolve_local_user_id

        user_dir_name = fs_registry.user_dir_name(resolve_local_user_id())
    except Exception:  # noqa: BLE001
        user_dir_name = None

    excluded = {
        _LOCK_FILENAME,
        _LOGS_DIRNAME,
        _MARKER_FILENAME,
        _USER_SCOPE_MARKER_FILENAME,
        ".DS_Store",
        ".env",
        "bin",
        "cache",
        "models",
        "official-skills",
    }
    if user_dir_name:
        excluded.add(user_dir_name)
    entries: list[tuple[Path, Path]] = []
    for entry in root.iterdir():
        if entry.name in excluded:
            continue
        if entry.name.endswith((".db-wal", ".db-shm")):
            continue
        if entry.is_dir() and (entry / settings.installation_filename).exists():
            continue
        if entry.name not in {
            *_USER_SCOPED_DIRS,
            settings.db_filename,
            settings.kernel_db_filename,
            settings.installation_filename,
        }:
            continue
        entries.append((entry, Path(entry.name)))
    return entries


def _cloud_unscoped_copy_plan(root: Path, user_id: str) -> list[tuple[Path, Path]]:
    entries: dict[Path, Path] = {}
    for rel in _cloud_user_relative_roots(root, user_id):
        src = root / rel
        if src.exists():
            entries[rel] = src
    return [(src, rel) for rel, src in sorted(entries.items())]


def _cloud_user_relative_roots(root: Path, user_id: str) -> set[Path]:
    db = root / settings.db_filename
    if not db.exists():
        return set()

    rels: set[Path] = set()
    conn = sqlite3.connect(str(db))
    try:
        if _table_has_columns(conn, "valuz_project", {"id", "user_id"}):
            for (project_id,) in conn.execute(
                "SELECT id FROM valuz_project WHERE user_id = ?", (user_id,)
            ):
                rels.add(Path("projects") / str(project_id))

        if _table_has_columns(conn, "valuz_knowledge_base", {"id", "user_id"}):
            for (kb_id,) in conn.execute(
                "SELECT id FROM valuz_knowledge_base WHERE user_id = ?", (user_id,)
            ):
                rels.add(Path("kb") / str(kb_id))

        if _table_has_columns(conn, "valuz_document_record", {"id", "user_id"}):
            for (doc_id,) in conn.execute(
                "SELECT id FROM valuz_document_record WHERE user_id = ?", (user_id,)
            ):
                rels.add(Path("docs") / "assets" / str(doc_id))
                rels.add(Path("docs") / "preview" / f"{doc_id}.md")

        if _table_has_columns(conn, "valuz_session_attachment", {"session_id", "user_id"}):
            for (session_id,) in conn.execute(
                "SELECT DISTINCT session_id FROM valuz_session_attachment WHERE user_id = ?",
                (user_id,),
            ):
                rels.add(Path("attachments") / str(session_id))

        if _table_has_columns(conn, "valuz_provider", {"secret_ref", "user_id"}):
            for (secret_ref,) in conn.execute(
                "SELECT secret_ref FROM valuz_provider "
                "WHERE user_id = ? AND secret_ref IS NOT NULL",
                (user_id,),
            ):
                safe = str(secret_ref).replace("/", "__").replace("\\", "__")
                rels.add(Path("secrets") / safe)

        if _table_has_columns(conn, "valuz_project", {"id", "user_id"}):
            for (project_id,) in conn.execute(
                "SELECT id FROM valuz_project WHERE user_id = ?", (user_id,)
            ):
                rels.add(Path("memories") / "projects" / str(project_id))
    finally:
        conn.close()

    return {rel for rel in rels if (root / rel).exists()}


def _user_ids_in_sqlite(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    out: set[str] = set()
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _all_tables(conn):
            if "user_id" not in _table_column_names(conn, table):
                continue
            for (user_id,) in conn.execute(
                f'SELECT DISTINCT user_id FROM "{table}" WHERE user_id IS NOT NULL'  # noqa: S608
            ):
                if isinstance(user_id, str) and user_id:
                    out.add(user_id)
    finally:
        conn.close()
    return sorted(out)


def _rewrite_user_scoped_db_paths(
    root: Path, user_id: str, *, mode: str
) -> tuple[dict[tuple[str, str], int], dict[tuple[str, str], int]]:
    target = fs_registry.data_dir(user_id)
    pairs = tuple(
        (str(root / name), str(target / name))
        for name in _USER_SCOPED_DIRS
        if (root / name).exists()
    )
    if not pairs:
        return {}, {}

    if mode == "local":
        host_n = _rewrite_all(target / settings.db_filename, pairs)
        kernel_n = _rewrite_all(target / settings.kernel_db_filename, pairs)
    else:
        host_n = _rewrite_user_rows(root / settings.db_filename, pairs, user_id)
        kernel_n = _rewrite_user_rows(root / settings.kernel_db_filename, pairs, user_id)
    return host_n, kernel_n


def _copy_root_entry(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        if dst.exists() or dst.is_symlink():
            _remove_path(dst)
        os.symlink(os.readlink(src), dst, target_is_directory=src.is_dir())
        return
    if src.is_dir():
        if dst.is_symlink() or (dst.exists() and not dst.is_dir()):
            _remove_path(dst)
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            _copy_root_entry(child, dst / child.name)
        return
    if dst.exists() or dst.is_symlink():
        _remove_path(dst)
    _remove_sqlite_sidecars(dst)
    shutil.copy2(src, dst)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)


def _remove_sqlite_sidecars(path: Path) -> None:
    if path.suffix != ".db":
        return
    for sidecar in (path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if sidecar.exists() or sidecar.is_symlink():
            _remove_path(sidecar)


def _write_user_scope_marker(marker: Path, root: Path) -> None:
    try:
        marker.write_text(
            f"migrated from {root}\nversion={_USER_SCOPE_MIGRATION_VERSION}\n",
            encoding="utf-8",
        )
    except OSError:
        logger.warning("user-scope migration: could not write marker file", exc_info=True)


# --------------------------------------------------------------------------- #
# Marker
# --------------------------------------------------------------------------- #


def _write_marker(marker: Path, old_app: Path) -> None:
    try:
        # Explicit encoding: the marker embeds an install path, which contains
        # the OS username — non-ASCII on e.g. zh-CN Windows, where the locale
        # default is GBK and a later UTF-8 read would fail.
        marker.write_text(
            f"migrated from {old_app}\nversion={_MIGRATION_VERSION}\n",
            encoding="utf-8",
        )
    except OSError:
        logger.warning("data-dir migration: could not write marker file", exc_info=True)


def _marker_version(marker: Path) -> int:
    """Parse ``version=N`` from the marker; a versionless marker is v1."""
    try:
        for line in marker.read_text(encoding="utf-8").splitlines():
            if line.startswith("version="):
                return int(line.split("=", 1)[1].strip())
    except (OSError, ValueError):
        pass
    return 1


def _fmt_counts(counts: dict[tuple[str, str], int]) -> str:
    return ", ".join(f"{t}.{c}({n})" for (t, c), n in counts.items()) or "no rows"


# --------------------------------------------------------------------------- #
# Copy
# --------------------------------------------------------------------------- #


def _checkpoint_wal(db_path: Path) -> None:
    """Fold ``db_path``'s WAL into the main file so the copy is self-contained.

    Safe under the single-writer lock (no engine has opened the file). Best
    effort: a failure is logged, not fatal."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        logger.warning(
            "data-dir migration: WAL checkpoint failed for %s", db_path, exc_info=True
        )


def _reset_partial_copy(new_root: Path) -> None:
    """Clear a partial prior copy under ``new_root`` (keeping the writer lock).

    The OLD tree is the source of truth and is never deleted, so discarding an
    incomplete copy is lossless and makes the copy step deterministic + re-
    entrant (avoids ``copytree`` symlink-already-exists conflicts on a re-run)."""
    if not new_root.exists():
        return
    for entry in new_root.iterdir():
        # Both are held OPEN by the current boot process — the single-writer
        # lock and the logging handler's ``logs/backend.log``. On Windows an
        # open file cannot be removed ([WinError 32]); skipping them is correct
        # anyway (neither is migratable data). See ``_LOGS_DIRNAME``.
        if entry.name in (_LOCK_FILENAME, _LOGS_DIRNAME):
            continue
        try:
            if entry.is_symlink() or entry.is_file():
                entry.unlink()
            else:
                shutil.rmtree(entry)
        except OSError:
            logger.warning(
                "data-dir migration: could not clear partial %s", entry, exc_info=True
            )


def _copy_tree(src: Path, dst: Path) -> None:
    """Copy ``src`` into ``dst`` preserving symlinks (not dereferenced)."""
    shutil.copytree(
        src,
        dst,
        symlinks=True,
        ignore=_COPY_IGNORE,
        ignore_dangling_symlinks=True,
        dirs_exist_ok=True,
    )


# --------------------------------------------------------------------------- #
# DB rewrite (schema-driven)
# --------------------------------------------------------------------------- #


def _rewrite_all(
    db_path: Path, pairs: tuple[tuple[str, str], ...]
) -> dict[tuple[str, str], int]:
    """Rewrite every old prefix -> new prefix across ALL text columns of ALL
    tables. Returns the per-column count of rows touched by the FIRST (app)
    prefix. Anchored to a path boundary, so siblings/external paths are safe."""
    counts: dict[tuple[str, str], int] = {}
    if not db_path.exists():
        return counts

    app_prefix = pairs[0][0]
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _all_tables(conn):
            for column in _text_columns(conn, table):
                touched = _count_under_prefix(conn, table, column, app_prefix)
                for old, new in pairs:
                    _replace_anchored(conn, table, column, old, new)
                if touched:
                    counts[(table, column)] = touched
        conn.commit()
    finally:
        conn.close()
    return counts


def _rewrite_user_rows(
    db_path: Path, pairs: tuple[tuple[str, str], ...], user_id: str
) -> dict[tuple[str, str], int]:
    """Rewrite path prefixes only in rows owned by ``user_id``.

    Cloud/shared startup can see multiple owners in the same SQLite file. A
    whole-DB prefix sweep would assign every path to the same target user, so
    this variant only touches tables that carry a ``user_id`` column and filters
    every update by that owner.
    """
    counts: dict[tuple[str, str], int] = {}
    if not db_path.exists():
        return counts

    first_prefix = pairs[0][0]
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _all_tables(conn):
            if "user_id" not in _table_column_names(conn, table):
                continue
            for column in _text_columns(conn, table):
                touched = _count_user_rows_under_prefix(
                    conn, table, column, first_prefix, user_id
                )
                for old, new in pairs:
                    _replace_user_rows_anchored(conn, table, column, old, new, user_id)
                if touched:
                    counts[(table, column)] = touched
        conn.commit()
    finally:
        conn.close()
    return counts


def _all_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    ]


def _table_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _table_has_columns(conn: sqlite3.Connection, table: str, names: set[str]) -> bool:
    if table not in _all_tables(conn):
        return False
    return names.issubset(_table_column_names(conn, table))


def _text_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """Columns whose declared type has text affinity (incl. JSON/VARCHAR).

    BLOB/INTEGER/REAL/BOOLEAN columns are skipped — REPLACE could corrupt binary
    and they hold no paths."""
    cols: list[str] = []
    for row in conn.execute(f'PRAGMA table_info("{table}")'):
        name, ctype = row[1], (row[2] or "").upper()
        if any(hint in ctype for hint in _TEXT_AFFINITY_HINTS):
            cols.append(name)
    return cols


def _count_user_rows_under_prefix(
    conn: sqlite3.Connection, table: str, column: str, prefix: str, user_id: str
) -> int:
    boundary = prefix + os.sep
    return int(
        conn.execute(
            f'SELECT COUNT(*) FROM "{table}" '  # noqa: S608 — identifiers from schema
            f'WHERE user_id = ? AND ("{column}" = ? OR "{column}" LIKE \'%\' || ? || \'%\')',
            (user_id, prefix, boundary),
        ).fetchone()[0]
    )


def _count_under_prefix(
    conn: sqlite3.Connection, table: str, column: str, prefix: str
) -> int:
    """Rows whose ``column`` is exactly ``prefix`` or lives under ``prefix/``."""
    boundary = prefix + os.sep
    return int(
        conn.execute(
            f'SELECT COUNT(*) FROM "{table}" '  # noqa: S608 — identifiers from schema
            f'WHERE "{column}" = ? OR "{column}" LIKE \'%\' || ? || \'%\'',
            (prefix, boundary),
        ).fetchone()[0]
    )


def _replace_user_rows_anchored(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    old: str,
    new: str,
    user_id: str,
) -> None:
    boundary_old = old + os.sep
    boundary_new = new + os.sep
    conn.execute(
        f'UPDATE "{table}" SET "{column}" = REPLACE("{column}", ?, ?) '  # noqa: S608
        f"WHERE user_id = ? AND \"{column}\" LIKE '%' || ? || '%'",
        (boundary_old, boundary_new, user_id, boundary_old),
    )
    conn.execute(
        f'UPDATE "{table}" SET "{column}" = ? '  # noqa: S608
        f'WHERE user_id = ? AND "{column}" = ?',
        (new, user_id, old),
    )


def _replace_anchored(
    conn: sqlite3.Connection, table: str, column: str, old: str, new: str
) -> None:
    """Replace ``old`` -> ``new`` only at a path boundary (``old/`` or bare ``old``).

    Anchoring on the separator prevents corrupting a sibling path that merely
    shares the string prefix (e.g. ``~/.valuz/apple`` under an ``~/.valuz/app``
    rename)."""
    boundary_old = old + os.sep
    boundary_new = new + os.sep
    conn.execute(
        f'UPDATE "{table}" SET "{column}" = REPLACE("{column}", ?, ?) '  # noqa: S608
        f"WHERE \"{column}\" LIKE '%' || ? || '%'",
        (boundary_old, boundary_new, boundary_old),
    )
    conn.execute(
        f'UPDATE "{table}" SET "{column}" = ? WHERE "{column}" = ?',  # noqa: S608
        (new, old),
    )


# --------------------------------------------------------------------------- #
# Symlink repoint
# --------------------------------------------------------------------------- #


def _repoint_symlinks(
    new_root: Path, host_db: Path, old_app_prefix: str, new_prefix: str
) -> int:
    """Repair skill symlinks under every project cwd that point into the old root.

    Two cwd sources: (a) managed chat projects — every dir under
    ``new_root/projects`` (copied here, so repaired in the new tree); (b) user/
    external projects — ``valuz_project.root_path WHERE kind='project'`` (live
    OUTSIDE the data dir, never copied — repaired IN PLACE). Per-link work is
    wrapped so one bad link can't abort the whole migration.
    """
    cwds: list[Path] = []

    projects_dir = new_root / "projects"
    if projects_dir.is_dir():
        cwds.extend(p for p in projects_dir.iterdir() if p.is_dir())

    if host_db.exists():
        conn = sqlite3.connect(str(host_db))
        try:
            if "valuz_project" in _all_tables(conn):
                for (root_path,) in conn.execute(
                    "SELECT root_path FROM valuz_project "
                    "WHERE kind = 'project' AND root_path IS NOT NULL"
                ).fetchall():
                    cwds.append(Path(root_path))
        finally:
            conn.close()

    repaired = 0
    for cwd in cwds:
        for rel in _SKILL_LINK_DIRS:
            skills_dir = cwd / rel
            if not skills_dir.is_dir():
                continue
            try:
                entries = list(skills_dir.iterdir())
            except OSError:
                logger.warning(
                    "data-dir migration: cannot list %s", skills_dir, exc_info=True
                )
                continue
            for entry in entries:
                repaired += _repoint_one(entry, old_app_prefix, new_prefix)
    return repaired


def _repoint_one(entry: Path, old_app_prefix: str, new_prefix: str) -> int:
    """Repoint a single symlink if it targets the old root. Returns 1 if repaired.

    Anchored like the DB rewrite: only a target equal to the prefix or under
    ``<prefix>/`` is repointed, so a sibling target is left alone."""
    try:
        if not os.path.islink(entry):
            return 0
        target = os.readlink(entry)
        if target == old_app_prefix:
            new_target = new_prefix
        elif target.startswith(old_app_prefix + os.sep):
            new_target = new_prefix + target[len(old_app_prefix) :]
        else:
            return 0
        is_dir = not os.path.isfile(entry)  # entry resolves through the link
        entry.unlink()
        os.symlink(new_target, entry, target_is_directory=is_dir)
        return 1
    except OSError:
        logger.warning("data-dir migration: failed to repoint %s", entry, exc_info=True)
        return 0


# --------------------------------------------------------------------------- #
# Verify
# --------------------------------------------------------------------------- #


def _assert_carried_over(old_app: Path, new_root: Path) -> None:
    """Each CRITICAL file the OLD tree had MUST survive the copy.

    - A missing DB is data loss.
    - A missing ``installation.json`` would let identity re-derive a fresh owner
      id — **changing ``user_id``** and orphaning owner-scoped state (onboarding,
      skill index). Preserving it (so identity, resolved right after this step,
      reads the SAME owner) is the load-bearing guarantee that ``user_id`` is
      invariant across the migration.

    Absence in the OLD tree is fine: a PRE-SPLIT install has no ``kernel.db``
    (created later by the kernel-store split), and a never-booted tree may have
    neither a DB nor an installation file."""
    critical = (
        settings.db_filename,
        settings.kernel_db_filename,
        settings.installation_filename,
    )
    for name in critical:
        if (old_app / name).exists() and not (new_root / name).exists():
            raise RuntimeError(
                f"data-dir migration verify failed: {new_root / name} missing after copy"
            )


def _assert_dbs_clean(new_root: Path, old_app_prefix: str) -> None:
    """No surviving DB may still carry the old prefix. Skips DBs that don't
    exist (nothing to verify). Leaves the old tree intact on failure."""
    for name in (settings.db_filename, settings.kernel_db_filename):
        db = new_root / name
        if db.exists():
            _assert_no_old_prefix(db, old_app_prefix)


def _assert_no_old_prefix(db_path: Path, old_app_prefix: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        for table in _all_tables(conn):
            for column in _text_columns(conn, table):
                stragglers = _count_under_prefix(conn, table, column, old_app_prefix)
                if stragglers:
                    raise RuntimeError(
                        f"data-dir migration verify failed: {table}.{column} still has "
                        f"{stragglers} row(s) under the old prefix {old_app_prefix!r}"
                    )
    finally:
        conn.close()
