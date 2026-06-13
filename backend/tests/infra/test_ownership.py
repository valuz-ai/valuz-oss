"""Tests for the ``user_id`` ownership feature.

Covers the three moving parts of the cutover:

1. ``infra.local_identity.resolve_local_user_id`` — device-derived, stable,
   persisted once to ``installation.json``.
2. ``infra.auth_context`` — the request-scoped owner ContextVar. Explicit-only:
   no implicit fallback; an unset context raises ``LookupError``.
3. ``infra.database.UserMixin`` — auto-stamps the active owner on insert.
4. ``boot.schema.drop_stale_host_tables`` — fires only on a pre-cutover schema.
"""

from __future__ import annotations

import contextvars

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import DeclarativeBase, Session

from valuz_agent.boot.schema import drop_stale_host_tables
from valuz_agent.infra import auth_context
from valuz_agent.infra.config import settings
from valuz_agent.infra.database import PrimaryKeyMixin, UserMixin
from valuz_agent.infra.local_identity import resolve_local_user_id


# --------------------------------------------------------------------------- #
# 1. local install id
# --------------------------------------------------------------------------- #
class TestResolveLocalUserId:
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setattr(settings, "data_dir", tmp_path)
        resolve_local_user_id.cache_clear()

    def test_is_stable_nonempty_string(self, tmp_path, monkeypatch) -> None:
        self._isolate(tmp_path, monkeypatch)
        uid = resolve_local_user_id()
        assert isinstance(uid, str)
        assert uid
        assert uid.startswith("local-")

    def test_persists_once_and_rereads(self, tmp_path, monkeypatch) -> None:
        self._isolate(tmp_path, monkeypatch)
        first = resolve_local_user_id()
        assert settings.installation_file.is_file()

        # A fresh resolution (cache cleared) reads the persisted file rather
        # than regenerating, so the value is identical even if the fingerprint
        # were to drift.
        resolve_local_user_id.cache_clear()
        assert resolve_local_user_id() == first

    def test_corrupt_file_regenerates(self, tmp_path, monkeypatch) -> None:
        self._isolate(tmp_path, monkeypatch)
        settings.installation_file.parent.mkdir(parents=True, exist_ok=True)
        settings.installation_file.write_text("not json", encoding="utf-8")
        resolve_local_user_id.cache_clear()
        uid = resolve_local_user_id()
        assert uid.startswith("local-")


# --------------------------------------------------------------------------- #
# 2. owner context
# --------------------------------------------------------------------------- #
class TestAuthContext:
    """Explicit-only semantics: no boot-seeded global fallback.

    Each case runs inside a fresh ``contextvars.Context`` so the module-level
    ContextVar starts genuinely unset, regardless of what other tests (or the
    test runner itself) set in the ambient context.
    """

    def test_unset_context_raises_lookup_error(self) -> None:
        def probe() -> None:
            with pytest.raises(LookupError):
                auth_context.get_current_user_id()

        contextvars.Context().run(probe)

    def test_set_then_get_then_reset(self) -> None:
        def roundtrip() -> None:
            token = auth_context.set_current_user_id("u-42")
            assert auth_context.get_current_user_id() == "u-42"
            auth_context.reset_current_user_id(token)
            # Reset returns to the prior (unset) state — reads fail loudly
            # again rather than falling back to any implicit owner.
            with pytest.raises(LookupError):
                auth_context.get_current_user_id()

        contextvars.Context().run(roundtrip)

    def test_nested_override_restores_outer_owner(self) -> None:
        def nested() -> None:
            outer = auth_context.set_current_user_id("local-xyz")
            inner = auth_context.set_current_user_id("u-42")
            assert auth_context.get_current_user_id() == "u-42"
            auth_context.reset_current_user_id(inner)
            assert auth_context.get_current_user_id() == "local-xyz"
            auth_context.reset_current_user_id(outer)

        contextvars.Context().run(nested)


# --------------------------------------------------------------------------- #
# 3. OwnedMixin stamping
# --------------------------------------------------------------------------- #
class _OwnedBase(DeclarativeBase):
    pass


class _Thing(_OwnedBase, PrimaryKeyMixin, UserMixin):
    __tablename__ = "t_owned_thing"


class TestUserMixinStamping:
    def test_explicit_owner_persists_on_insert(self) -> None:
        # There is no column ``default=`` reading the ContextVar anymore — the
        # owner is stamped EXPLICITLY by the writer. An explicitly-set
        # ``user_id`` round-trips; an unset one fails loudly
        # (see ``test_insert_without_owner_fails_loudly``).
        engine = create_engine("sqlite://")
        _OwnedBase.metadata.create_all(engine)

        # user_id column is NOT NULL + present on the table.
        cols = {c["name"]: c for c in inspect(engine).get_columns("t_owned_thing")}
        assert "user_id" in cols
        assert cols["user_id"]["nullable"] is False

        with Session(engine) as s:
            row = _Thing(user_id="u-7")
            s.add(row)
            s.commit()
            assert s.get(_Thing, row.id).user_id == "u-7"

    def test_insert_without_owner_fails_loudly(self) -> None:
        # No implicit fallback: an insert from a context that never set the
        # owner must error out instead of being attributed to the install id.
        engine = create_engine("sqlite://")
        _OwnedBase.metadata.create_all(engine)

        def insert_unowned() -> None:
            with Session(engine) as s:
                s.add(_Thing())
                with pytest.raises((LookupError, StatementError)):
                    s.commit()

        contextvars.Context().run(insert_unowned)


# --------------------------------------------------------------------------- #
# 4. drop_stale_host_tables
# --------------------------------------------------------------------------- #
class TestDropStaleHostTables:
    """The probe is a pure stamp gate now (0-migration policy) — full
    behavioral coverage lives in ``tests/migrations/test_host_baseline_reset``.
    These two cases pin the ownership-relevant ends: an unstamped legacy DB
    resets; a baseline-stamped DB is trusted as-is."""

    def test_drops_legacy_db_without_baseline_stamp(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'pre.db'}")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE valuz_provider (id TEXT PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY)"))
            conn.execute(text("CREATE TABLE alembic_version_host (version_num TEXT PRIMARY KEY)"))

        drop_stale_host_tables(engine)

        remaining = set(inspect(engine).get_table_names())
        assert "valuz_provider" not in remaining
        assert "valuz_agent" not in remaining
        assert "alembic_version_host" not in remaining

    def test_noop_when_stamped_at_baseline(self, tmp_path) -> None:
        from valuz_agent.boot.schema import BASELINE_REVISION

        engine = create_engine(f"sqlite:///{tmp_path / 'modern.db'}")
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE valuz_provider (id TEXT PRIMARY KEY, user_id TEXT)"))
            conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY, user_id TEXT)"))
            conn.execute(text("CREATE TABLE alembic_version_host (version_num TEXT PRIMARY KEY)"))
            conn.execute(text(f"INSERT INTO alembic_version_host VALUES ('{BASELINE_REVISION}')"))

        drop_stale_host_tables(engine)

        remaining = set(inspect(engine).get_table_names())
        assert {"valuz_provider", "valuz_agent"} <= remaining

    def test_noop_on_fresh_install(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
        # No host tables at all → nothing to reset.
        drop_stale_host_tables(engine)
        assert set(inspect(engine).get_table_names()) == set()
