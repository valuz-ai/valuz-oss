"""0-migration reset tests for ``drop_stale_host_tables``.

The host alembic chain holds exactly one revision — the 0001 baseline that
creates the whole host schema. There is no upgrade path and no schema
fingerprinting: the probe compares the ``alembic_version_host`` stamp against
``BASELINE_REVISION`` and, on any mismatch (legacy multi-revision chain,
missing/empty stamp from a boot that died mid-initialization, an id from a
diverged branch), drops every ``valuz_*`` table plus the stamp so the
following ``upgrade head`` re-initializes the schema cleanly.

This is what fixed the "table valuz_project_session already exists" boot
crash: a DB stamped by one branch's chain, booted under another branch's
chain, used to re-run a CREATE TABLE migration into an existing table. Now
any stamp mismatch resets the host schema wholesale.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from valuz_agent.boot.schema import BASELINE_REVISION, drop_stale_host_tables


def _host_tables(engine) -> set[str]:
    return {t for t in inspect(engine).get_table_names() if t.startswith("valuz_")}


def _stamp(engine) -> str | None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT version_num FROM alembic_version_host")).fetchone()
        return row[0] if row else None


def _create_host_shape(conn, *, stamp: str | None) -> None:
    """A few representative host tables + the version table; ``stamp=None``
    leaves the version table empty (boot died before stamping)."""
    conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY, user_id TEXT)"))
    conn.execute(text("CREATE TABLE valuz_provider (id TEXT PRIMARY KEY, user_id TEXT)"))
    conn.execute(text("CREATE TABLE alembic_version_host (version_num TEXT PRIMARY KEY)"))
    if stamp is not None:
        conn.execute(text(f"INSERT INTO alembic_version_host VALUES ('{stamp}')"))


def test_should_noop_when_stamped_at_baseline(tmp_path) -> None:
    """Stamp == baseline → trust it; tables and data untouched."""
    engine = create_engine(f"sqlite:///{tmp_path / 'on_baseline.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp=BASELINE_REVISION)
        conn.execute(text("INSERT INTO valuz_agent VALUES ('a1', 'local-u')"))

    drop_stale_host_tables(engine)

    assert _stamp(engine) == BASELINE_REVISION
    assert _host_tables(engine) == {"valuz_agent", "valuz_provider"}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT id FROM valuz_agent")).fetchall() == [("a1",)]


def test_should_reset_when_stamped_by_legacy_chain(tmp_path) -> None:
    """Any old multi-revision stamp (0001/0003/0004) → full reset."""
    for legacy in ("0001", "0003", "0004"):
        engine = create_engine(f"sqlite:///{tmp_path / f'at_{legacy}.db'}")
        with engine.begin() as conn:
            _create_host_shape(conn, stamp=legacy)

        drop_stale_host_tables(engine)

        assert _host_tables(engine) == set()
        assert "alembic_version_host" not in set(inspect(engine).get_table_names())


def test_should_reset_when_stamp_row_is_missing(tmp_path) -> None:
    """Tables exist but the version table is empty (boot died before the
    stamp landed) → unknown provenance, reset."""
    engine = create_engine(f"sqlite:///{tmp_path / 'no_stamp.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp=None)

    drop_stale_host_tables(engine)

    assert _host_tables(engine) == set()
    assert "alembic_version_host" not in set(inspect(engine).get_table_names())


def test_should_reset_when_version_table_is_absent(tmp_path) -> None:
    """Host tables without any version table (e.g. ad-hoc create_all) → reset."""
    engine = create_engine(f"sqlite:///{tmp_path / 'no_vt.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY, user_id TEXT)"))

    drop_stale_host_tables(engine)

    assert _host_tables(engine) == set()


def test_should_not_touch_kernel_tables_on_reset(tmp_path) -> None:
    """The reset is host-scoped: kernel tables (sessions/messages/events)
    survive — the kernel chain owns its own lifecycle."""
    engine = create_engine(f"sqlite:///{tmp_path / 'mixed.db'}")
    with engine.begin() as conn:
        _create_host_shape(conn, stamp="0004")
        conn.execute(text("CREATE TABLE sessions (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"))

    drop_stale_host_tables(engine)

    remaining = set(inspect(engine).get_table_names())
    assert _host_tables(engine) == set()
    assert {"sessions", "alembic_version"} <= remaining


def test_should_noop_on_fresh_install(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    drop_stale_host_tables(engine)
    assert set(inspect(engine).get_table_names()) == set()
