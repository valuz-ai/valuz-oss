"""Tests for the ``user_id`` ownership feature.

Covers the three moving parts of the cutover:

1. ``infra.local_identity.resolve_local_user_id`` — device-derived, stable,
   persisted once to ``installation.json``.
2. ``infra.owner_context`` — the request-scoped owner ContextVar + boot default.
3. ``infra.database.OwnedMixin`` — auto-stamps the active owner on insert.
4. ``boot.schema.drop_stale_host_tables`` — fires only on a pre-cutover schema.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session

from valuz_agent.boot.schema import drop_stale_host_tables
from valuz_agent.infra import owner_context
from valuz_agent.infra.config import settings
from valuz_agent.infra.database import OwnedMixin, PrimaryKeyMixin
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
class TestOwnerContext:
    def teardown_method(self) -> None:
        owner_context.set_default_user_id("")

    def test_default_fallback(self) -> None:
        owner_context.set_default_user_id("local-xyz")
        assert owner_context.get_current_user_id() == "local-xyz"

    def test_request_override_then_reset(self) -> None:
        owner_context.set_default_user_id("local-xyz")
        token = owner_context.set_current_user_id("u-42")
        assert owner_context.get_current_user_id() == "u-42"
        owner_context.reset_current_user_id(token)
        assert owner_context.get_current_user_id() == "local-xyz"

    def test_empty_override_falls_through_to_default(self) -> None:
        owner_context.set_default_user_id("local-xyz")
        token = owner_context.set_current_user_id("")
        assert owner_context.get_current_user_id() == "local-xyz"
        owner_context.reset_current_user_id(token)


# --------------------------------------------------------------------------- #
# 3. OwnedMixin stamping
# --------------------------------------------------------------------------- #
class _OwnedBase(DeclarativeBase):
    pass


class _Thing(_OwnedBase, PrimaryKeyMixin, OwnedMixin):
    __tablename__ = "t_owned_thing"


class TestOwnedMixinStamping:
    def teardown_method(self) -> None:
        owner_context.set_default_user_id("")

    def test_stamps_active_owner_on_insert(self) -> None:
        owner_context.set_default_user_id("local-owner")
        engine = create_engine("sqlite://")
        _OwnedBase.metadata.create_all(engine)

        # user_id column is NOT NULL + present on the table.
        cols = {c["name"]: c for c in inspect(engine).get_columns("t_owned_thing")}
        assert "user_id" in cols
        assert cols["user_id"]["nullable"] is False

        with Session(engine) as s:
            token = owner_context.set_current_user_id("u-7")
            try:
                row = _Thing()
                s.add(row)
                s.commit()
                rid = row.id
            finally:
                owner_context.reset_current_user_id(token)
            assert s.get(_Thing, rid).user_id == "u-7"

        # Outside a request override, the boot default is stamped instead.
        with Session(engine) as s:
            row = _Thing()
            s.add(row)
            s.commit()
            assert row.user_id == "local-owner"


# --------------------------------------------------------------------------- #
# 4. drop_stale_host_tables
# --------------------------------------------------------------------------- #
class TestDropStaleHostTables:
    def test_drops_when_marker_lacks_user_id(self, tmp_path) -> None:
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

    def test_noop_when_marker_has_user_id(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'modern.db'}")
        with engine.begin() as conn:
            conn.execute(
                text("CREATE TABLE valuz_provider (id TEXT PRIMARY KEY, user_id TEXT)")
            )
            conn.execute(text("CREATE TABLE valuz_agent (id TEXT PRIMARY KEY, user_id TEXT)"))

        drop_stale_host_tables(engine)

        remaining = set(inspect(engine).get_table_names())
        assert {"valuz_provider", "valuz_agent"} <= remaining

    def test_noop_on_fresh_install(self, tmp_path) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
        # No marker table at all → nothing to wipe.
        drop_stale_host_tables(engine)
        assert set(inspect(engine).get_table_names()) == set()
