"""Bring a SQLite database to the artifact schema 0030 now describes.

Why this is a script and not a migration
----------------------------------------
``0030_artifact_versioning`` was corrected in place rather than followed by a new
revision. When the correction landed the five artifact tables were days old and
held a single row across both deployments, and no released desktop build had run
them — so editing 0030 gave every future database the right shape for free.

Editing a migration does nothing for a database that has already run it, and
alembic will not re-run 0030 on one. The deployed databases were corrected by
hand. A developer machine that started the app before the correction is the same
case, and this is that correction, packaged.

The symptom is unmistakable::

    sqlite3.OperationalError: no such column: valuz_artifact_revision.mime_type

What it changes
---------------
1. ``revision.mime_type`` is added and backfilled from the content rows. MIME is
   read from a file NAME, so it describes one generation. The content row is
   keyed by hash and deliberately shared between deliverables holding identical
   bytes, which made the same content delivered as ``page.html`` and then as
   ``notes.txt`` report ``text/html`` for both.
2. ``content.mime_type`` is dropped, leaving that table holding properties of
   the bytes and nothing else.
3. ``ux_artifact_revision_content`` is removed. Unique over ``(user_id,
   artifact_id, content_hash)`` means a set of bytes may appear at most once in
   a deliverable's history — which is exactly what returning to an earlier
   version repeats, so a rollback could not be recorded at all. Recognising a
   replay is a comparison against the current head, which absorbs a transport
   retry just as well.

SQLite cannot drop a table constraint, so step 3 rebuilds the table. Rows,
indexes and the primary key are preserved, the rebuild runs inside one
transaction, and the database is backed up first.

PostgreSQL deployments are not covered: they take three ``ALTER``s and a
``DROP CONSTRAINT``, and their ordering against a rolling deploy matters (the
``content.mime_type`` drop has to wait until no replica still selects it).

Usage (from backend/)::

    uv run python scripts/fix_artifact_schema_drift.py            # every local database
    uv run python scripts/fix_artifact_schema_drift.py --dry-run  # report only
    uv run python scripts/fix_artifact_schema_drift.py path/to/valuz.db
    uv run python scripts/fix_artifact_schema_drift.py --force    # even if the app has it open

Idempotent: every step is skipped when it has already been applied, so a second
run on a corrected database reports that there is nothing to do.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REVISION = "valuz_artifact_revision"
CONTENT = "valuz_artifact_content"
UNIQUE = "ux_artifact_revision_content"

# Where the launchers keep data: ``scripts/dev.sh`` derives ~/.valuz-<edition>-dev,
# a packaged build uses ~/.valuz (or ~/.valuz-qa), and Electron writes under
# Application Support. Explicit paths bypass all of this.
_SEARCH_ROOTS = (Path.home(), Path.home() / "Library" / "Application Support")


def discover() -> list[Path]:
    found: set[Path] = set()
    for root in _SEARCH_ROOTS:
        if not root.is_dir():
            continue
        for entry in root.iterdir():
            name = entry.name.lower()
            if name == ".valuz" or name.startswith(".valuz-") or name == "valuz":
                found.update(entry.glob("valuz.db"))
                found.update(entry.glob("*/valuz.db"))
    return sorted(found)


def holders(db: Path) -> list[str]:
    """Processes holding the file open.

    A rebuild while the app is writing is asking for a corrupt table, and naming
    the process is more useful than the lock error the attempt would produce.
    """
    try:
        result = subprocess.run(
            ["lsof", "-Fc", str(db)], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({line[1:] for line in result.stdout.splitlines() if line.startswith("c")})


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _table_sql(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row[0] if row else ""


def _without_unique(ddl: str) -> str:
    """Drop the named UNIQUE table constraint from a CREATE TABLE statement.

    Edits the database's OWN ddl rather than writing a fresh one, so the rebuilt
    table keeps every column, type and default exactly as it stands — including
    anything a later migration added that this script has never heard of.
    """
    edited = re.sub(
        rf",\s*CONSTRAINT\s+{UNIQUE}\s+UNIQUE\s*\([^)]*\)", "", ddl, flags=re.IGNORECASE
    )
    if edited == ddl:
        raise RuntimeError(f"could not locate the {UNIQUE} constraint to remove")
    return edited


def fix(db: Path, *, dry_run: bool) -> str:
    conn = sqlite3.connect(db)
    conn.isolation_level = None  # transactions are explicit below
    try:
        if not _table_sql(conn, REVISION):
            return "skipped - no artifact tables (migrations have not reached 0030)"

        revision_columns = _columns(conn, REVISION)
        content_columns = _columns(conn, CONTENT) if _table_sql(conn, CONTENT) else []
        ddl = _table_sql(conn, REVISION)

        add_column = "mime_type" not in revision_columns
        drop_column = "mime_type" in content_columns
        rebuild = UNIQUE.lower() in ddl.lower()

        if not (add_column or drop_column or rebuild):
            return "already correct"

        steps = [
            label
            for label, needed in (
                ("add revision.mime_type", add_column),
                ("drop content.mime_type", drop_column),
                (f"remove {UNIQUE} (table rebuild)", rebuild),
            )
            if needed
        ]
        if dry_run:
            return "would " + ", ".join(steps)

        backup = db.with_name(f"{db.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        # sqlite's own backup rather than a file copy: it is consistent with a
        # WAL sidecar, which cp is not.
        with sqlite3.connect(backup) as target:
            conn.backup(target)

        conn.execute("BEGIN IMMEDIATE")
        try:
            if add_column:
                conn.execute(f"ALTER TABLE {REVISION} ADD COLUMN mime_type VARCHAR(128)")
            if drop_column:
                # Backfill before the source column goes, so nothing already
                # recorded loses its type.
                conn.execute(
                    f"UPDATE {REVISION} SET mime_type = ("
                    f"  SELECT c.mime_type FROM {CONTENT} c WHERE c.id = {REVISION}.content_id"
                    f") WHERE mime_type IS NULL"
                )

            if rebuild:
                indexes = [
                    row[0]
                    for row in conn.execute(
                        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? "
                        "AND sql IS NOT NULL",
                        (REVISION,),
                    )
                ]
                # Re-read: ADD COLUMN above rewrote the stored ddl.
                current = _table_sql(conn, REVISION)
                new_ddl = _without_unique(current).replace(REVISION, f"{REVISION}_new", 1)
                names = ", ".join(f'"{c}"' for c in _columns(conn, REVISION))
                conn.execute(new_ddl)
                conn.execute(f"INSERT INTO {REVISION}_new ({names}) SELECT {names} FROM {REVISION}")
                conn.execute(f"DROP TABLE {REVISION}")
                conn.execute(f"ALTER TABLE {REVISION}_new RENAME TO {REVISION}")
                for statement in indexes:
                    conn.execute(statement)

            if drop_column:
                conn.execute(f"ALTER TABLE {CONTENT} DROP COLUMN mime_type")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

        verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if verdict != "ok":
            raise RuntimeError(f"integrity_check reported: {verdict}")

        rows = conn.execute(f"SELECT COUNT(*) FROM {REVISION}").fetchone()[0]
        typed = conn.execute(
            f"SELECT COUNT(*) FROM {REVISION} WHERE mime_type IS NOT NULL"
        ).fetchone()[0]
        return f"{', '.join(steps)} - {rows} revision(s), {typed} with a type; backup {backup.name}"
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", type=Path, help="databases; omit to search")
    parser.add_argument("--dry-run", action="store_true", help="report, change nothing")
    parser.add_argument(
        "--force", action="store_true", help="proceed even if a process has it open"
    )
    args = parser.parse_args()

    targets = args.paths or discover()
    if not targets:
        print("no valuz.db found")
        return 1

    failed = False
    for db in targets:
        print(f"\n{db}")
        if not db.is_file():
            print("  ! no such file")
            failed = True
            continue
        open_by = holders(db)
        if open_by and not (args.force or args.dry_run):
            print(f"  ! skipped - open in {', '.join(open_by)}; stop it, or pass --force")
            continue
        try:
            print(f"  {fix(db, dry_run=args.dry_run)}")
        except Exception as exc:  # noqa: BLE001 - one database must not end the run
            print(f"  ! failed: {exc}")
            failed = True

    if not args.dry_run:
        print("\nRestart the dev server or desktop app to pick up the corrected schema.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
