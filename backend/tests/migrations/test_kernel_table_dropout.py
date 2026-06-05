"""Schema-rebuild fingerprint tests for ``m000_legacy_dropout``.

The kernel reuses Alembic revision id ``001`` while iterating its
schema. An existing dev DB stamped at that id will silently skip the
upgrade, leaving the schema mid-rev. ``drop_stale_kernel_tables``
is the host's safety net — it inspects the live DB for column
fingerprints unique to a given kernel rev and drops the kernel quartet
when the schema is stale, so the next ``run_kernel_migrations`` pass
recreates everything.

These tests pin four fingerprints:

1. The pre-MODEL_CATALOG shape (sessions lacks ``model_provider``).
2. The pre-V5+messages shape (events lacks ``message_id``).
3. The pre-decouple-agents shape (agents lacks ``instructions`` —
   ADR-008).
4. The pre-runtime_provider-dispatch shape (sessions lacks
   ``runtime_provider`` — V5+d5f2238).
"""

from __future__ import annotations

from sqlalchemy import create_engine, text

from valuz_agent.boot.kernel import (
    drop_stale_kernel_tables,
)


def _kernel_tables_present(engine) -> set[str]:
    from sqlalchemy import inspect

    inspector = inspect(engine)
    return {
        t
        for t in ("projects", "agents", "sessions", "events", "messages")
        if t in inspector.get_table_names()
    }


def test_should_drop_kernel_quartet_when_events_lacks_message_id_column(tmp_path):
    """Pre-V5+messages DB has events without message_id → drop everything."""
    db_url = f"sqlite:///{tmp_path / 'pre_messages.db'}"
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE agents (id TEXT PRIMARY KEY)"))
        # Sessions DOES have model_provider (post-MODEL_CATALOG) but the
        # post-messages migration also added ``todos`` + drops total_*.
        # The fingerprint we test here is on events, not sessions.
        conn.execute(
            text(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, "
                "model_provider TEXT, total_turns INTEGER, total_cost_usd REAL)"
            )
        )
        # Events has the OLD shape — no message_id column.
        conn.execute(
            text("CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT, type TEXT)")
        )
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"))

    assert _kernel_tables_present(engine) == {"projects", "agents", "sessions", "events"}

    drop_stale_kernel_tables(engine)

    # Cascade should have dropped the quartet AND alembic_version so the
    # kernel's Alembic chain re-runs from a clean slate.
    assert _kernel_tables_present(engine) == set()
    from sqlalchemy import inspect

    assert "alembic_version" not in set(inspect(engine).get_table_names())


def test_should_leave_modern_schema_alone_when_events_already_has_message_id(tmp_path):
    """A DB at the current kernel rev (post-V5+1aae940) → no drop.

    The "modern" shape after V5+1aae940 has:
    - ``agents.instructions`` (renamed from ``system_prompt``, ADR-008)
    - ``events.message_id`` (V5+messages)
    - ``messages`` table present (V5+messages)
    - ``sessions.runtime_provider`` (V5+d5f2238)
    - ``sessions.permission_mode`` (V5+1aae940 — approval contract)
    All five fingerprints must pass simultaneously for a no-op outcome.
    """
    db_url = f"sqlite:///{tmp_path / 'modern.db'}"
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY)"))
        # Modern agents shape: ``instructions`` column (ADR-008).
        conn.execute(text("CREATE TABLE agents (id TEXT PRIMARY KEY, instructions TEXT)"))
        conn.execute(
            text(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, model_provider TEXT, "
                "todos TEXT, runtime_provider TEXT, permission_mode TEXT, mode TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, "
                "session_id TEXT, message_id TEXT, type TEXT)"
            )
        )
        conn.execute(text("CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT)"))

    before = _kernel_tables_present(engine)
    drop_stale_kernel_tables(engine)
    after = _kernel_tables_present(engine)

    assert before == after
    assert "messages" in after


def test_should_drop_kernel_quartet_when_agents_lacks_instructions_column(tmp_path):
    """Pre-ADR-008 DB has agents without ``instructions`` → drop everything."""
    db_url = f"sqlite:///{tmp_path / 'pre_decouple.db'}"
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY, agent_id TEXT)"))
        # Old agents shape: ``system_prompt`` + ``skill_dirs`` (pre-ADR-008).
        conn.execute(
            text("CREATE TABLE agents (id TEXT PRIMARY KEY, system_prompt TEXT, skill_dirs TEXT)")
        )
        # Sessions / events otherwise modern so only the agents fingerprint fires.
        conn.execute(text("CREATE TABLE sessions (id TEXT PRIMARY KEY, model_provider TEXT)"))
        conn.execute(
            text("CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT, message_id TEXT)")
        )
        conn.execute(text("CREATE TABLE messages (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"))

    drop_stale_kernel_tables(engine)

    assert _kernel_tables_present(engine) == set()
    from sqlalchemy import inspect

    assert "alembic_version" not in set(inspect(engine).get_table_names())


def test_should_drop_kernel_quartet_when_sessions_lacks_runtime_provider_column(tmp_path):
    """Pre-V5+d5f2238 DB has sessions without ``runtime_provider`` → drop everything.

    Fingerprint for the explicit-runtime-dispatch upgrade. Upstream kept
    Alembic revision ``14b5c6e20476`` for the new column, so a DB stamped
    at that id silently skips the upgrade — the host trigger has to drop
    the quartet so ``run_kernel_migrations`` recreates the schema with
    the new column + CHECK constraint.
    """
    db_url = f"sqlite:///{tmp_path / 'pre_runtime_provider.db'}"
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY)"))
        # Modern agents shape (post-ADR-008) so that fingerprint passes.
        conn.execute(text("CREATE TABLE agents (id TEXT PRIMARY KEY, instructions TEXT)"))
        # Sessions lacks ``runtime_provider`` — the fingerprint we test.
        conn.execute(
            text("CREATE TABLE sessions (id TEXT PRIMARY KEY, model_provider TEXT, todos TEXT)")
        )
        # Events shape post-V5+messages so its fingerprint also passes.
        conn.execute(
            text("CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT, message_id TEXT)")
        )
        conn.execute(text("CREATE TABLE messages (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"))

    drop_stale_kernel_tables(engine)

    assert _kernel_tables_present(engine) == set()
    from sqlalchemy import inspect

    assert "alembic_version" not in set(inspect(engine).get_table_names())


def test_should_drop_kernel_quartet_when_sessions_lacks_permission_mode_column(tmp_path):
    """Pre-V5+1aae940 DB has sessions without ``permission_mode`` → drop everything.

    Fingerprint for the approval-contract upgrade (kernel V5+1aae940).
    The new alembic revision ``807642401b71`` chains off
    ``14b5c6e20476`` and is reversible, so an in-place upgrade is the
    happy path — but a DB whose alembic chain has been manually wiped
    or rebased lands here with the kernel quartet present and
    ``sessions.permission_mode`` missing. The host trigger drops the
    quartet so ``run_kernel_migrations`` recreates clean (per
    dev-stage policy — no data preservation).
    """
    db_url = f"sqlite:///{tmp_path / 'pre_permission_mode.db'}"
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE agents (id TEXT PRIMARY KEY, instructions TEXT)"))
        # Sessions lacks ``permission_mode`` — the fingerprint we test.
        # Every other fingerprint passes (runtime_provider, model_provider,
        # todos) so this is the only trigger that should fire.
        conn.execute(
            text(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, model_provider TEXT, "
                "todos TEXT, runtime_provider TEXT)"
            )
        )
        conn.execute(
            text("CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT, message_id TEXT)")
        )
        conn.execute(text("CREATE TABLE messages (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)"))

    drop_stale_kernel_tables(engine)

    assert _kernel_tables_present(engine) == set()
    from sqlalchemy import inspect

    assert "alembic_version" not in set(inspect(engine).get_table_names())


def test_should_cascade_drop_messages_table_when_sessions_marked_stale(tmp_path):
    """Stale sessions schema → cascade includes the new messages table."""
    db_url = f"sqlite:///{tmp_path / 'cascade.db'}"
    engine = create_engine(db_url)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE projects (id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE agents (id TEXT PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE sessions (id TEXT PRIMARY KEY, "
                "model_provider TEXT, total_turns INTEGER)"
            )
        )
        # events lacks message_id (pre-V5+messages fingerprint hits)
        conn.execute(text("CREATE TABLE events (id INTEGER PRIMARY KEY, session_id TEXT)"))
        # A stray ``messages`` table from a half-applied upgrade — must
        # also be dropped or the next CREATE TABLE messages fails.
        conn.execute(text("CREATE TABLE messages (id TEXT PRIMARY KEY)"))

    drop_stale_kernel_tables(engine)

    assert _kernel_tables_present(engine) == set()
