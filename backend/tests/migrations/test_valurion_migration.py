"""Migration 0028 installs Valurion without mutating the legacy Helper."""

from __future__ import annotations

import importlib.util
import json
import pathlib

from sqlalchemy import Boolean, create_engine, text

_MIG = (
    pathlib.Path(__file__).resolve().parents[2]
    / "alembic"
    / "host"
    / "versions"
    / "0028_valurion_agent_contract.py"
)


class _Op:
    def __init__(self, conn) -> None:  # type: ignore[no-untyped-def]
        self._conn = conn

    def get_bind(self):  # type: ignore[no-untyped-def]
        return self._conn


def _load():
    spec = importlib.util.spec_from_file_location("mig0028", _MIG)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _RecordingResult:
    def __init__(self, rows=()) -> None:  # type: ignore[no-untyped-def]
        self._rows = rows

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._rows)

    def first(self):  # type: ignore[no-untyped-def]
        return self._rows[0] if self._rows else None

    def mappings(self):  # type: ignore[no-untyped-def]
        return self


class _RecordingBind:
    def __init__(self) -> None:
        self.writes: list[tuple[object, dict]] = []

    def execute(self, statement, params=None):  # type: ignore[no-untyped-def]
        sql = str(statement)
        if "SELECT DISTINCT user_id" in sql:
            return _RecordingResult([("owner-1",)])
        if sql.lstrip().startswith("SELECT"):
            return _RecordingResult()
        self.writes.append((statement, params or {}))
        return _RecordingResult()


def _create_schema(conn) -> None:  # type: ignore[no-untyped-def]
    conn.execute(
        text(
            """
            CREATE TABLE valuz_agent (
                slug TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL,
                instructions TEXT NOT NULL, runtime TEXT NOT NULL, model TEXT NOT NULL,
                skills JSON NOT NULL, connector_types JSON NOT NULL,
                knowledge_scope JSON NOT NULL, provider_id TEXT, effort TEXT,
                kind TEXT NOT NULL, resource_policy TEXT NOT NULL,
                inherit_global_instructions BOOLEAN NOT NULL,
                permission_mode TEXT NOT NULL, source TEXT NOT NULL,
                readonly BOOLEAN NOT NULL, deletable BOOLEAN NOT NULL, avatar TEXT,
                id TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL, user_id TEXT NOT NULL,
                UNIQUE(user_id, slug)
            )
            """
        )
    )
    conn.execute(
        text("CREATE TABLE valuz_project_member (id TEXT PRIMARY KEY, source_agent_slug TEXT)")
    )
    conn.execute(
        text(
            "CREATE TABLE valuz_automation (id TEXT PRIMARY KEY, agent_kind TEXT, agent_slug TEXT)"
        )
    )
    conn.execute(
        text(
            "CREATE TABLE valuz_channel_chat_binding (id TEXT PRIMARY KEY, default_agent_slug TEXT)"
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE valuz_channel_thread_binding (
                id TEXT PRIMARY KEY, user_id TEXT, channel_instance_id TEXT,
                external_chat_id TEXT, external_thread_id TEXT, agent_slug TEXT,
                project_id TEXT,
                UNIQUE(user_id, channel_instance_id, external_chat_id,
                       external_thread_id, agent_slug, project_id)
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE valuz_agent_channel_binding (
                id TEXT PRIMARY KEY, user_id TEXT, platform TEXT,
                channel_instance_id TEXT, agent_slug TEXT, bot_id TEXT,
                secret_ref TEXT, enabled BOOLEAN, bot_name TEXT, ws_url TEXT,
                UNIQUE(user_id, platform, agent_slug)
            )
            """
        )
    )


def _insert_agent(
    conn,  # type: ignore[no-untyped-def]
    *,
    slug: str,
    instructions: str,
    source: str = "official",
    name: str = "Valuz Helper",
    user_id: str = "owner-1",
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO valuz_agent VALUES (
                :slug, :name,
                :description,
                :instructions, 'claude_agent', 'model-1',
                '["valuz-handbook"]', '["valuz-search","valuz-stock"]', '[]',
                'valuz-channel', 'high', 'standard', 'explicit', 1,
                'full_access', :source, 0, 1, 'bot', :id, 1, 2, :user_id
            )
            """
        ),
        {
            "slug": slug,
            "name": name,
            "description": (
                "Valuz onboarding assistant. Ask anything about using Valuz, "
                "how to plan tasks, or how to configure agents."
            ),
            "instructions": instructions,
            "source": source,
            "id": f"id-{user_id}-{slug}",
            "user_id": user_id,
        },
    )


def test_postgresql_boolean_values_use_typed_boolean_bind_params() -> None:
    migration = _load()
    bind = _RecordingBind()
    migration.op = _Op(bind)

    migration._install_valurion()
    migration._uninstall_valurion()

    assert len(bind.writes) == 2
    for statement, params in bind.writes:
        for name in ("readonly", "deletable"):
            assert isinstance(statement._bindparams[name].type, Boolean)
            assert isinstance(params[name], bool)
    insert_stmt, insert_params = bind.writes[0]
    assert isinstance(insert_stmt._bindparams["inherit_global_instructions"].type, Boolean)
    assert insert_params["inherit_global_instructions"] is True


def test_install_valurion_keeps_legacy_helper_and_live_refs_unchanged() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_schema(conn)
        _insert_agent(
            conn,
            slug="valuz-helper",
            instructions="My carefully customized workflow.",
            name="My Helper",
        )
        conn.execute(text("INSERT INTO valuz_project_member VALUES ('member-1', 'valuz-helper')"))
        conn.execute(
            text(
                "INSERT INTO valuz_automation VALUES "
                "('automation-1', 'library_agent', 'valuz-helper')"
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO valuz_agent_channel_binding VALUES (
                    'binding-1', 'owner-1', 'feishu', 'bot-1',
                    'valuz-helper', 'bot-id', 'channel/feishu/valuz-helper',
                    1, NULL, NULL
                )
                """
            )
        )

        migration = _load()
        migration.op = _Op(conn)
        migration._install_valurion()

        rows = (
            conn.execute(
                text(
                    "SELECT id, slug, name, kind, resource_policy, instructions, "
                    "runtime, model, provider_id, effort, skills, connector_types, "
                    "source, readonly, deletable "
                    "FROM valuz_agent ORDER BY slug"
                )
            )
            .mappings()
            .all()
        )
        source_ref = conn.execute(
            text("SELECT source_agent_slug FROM valuz_project_member")
        ).scalar_one()
        automation_ref = conn.execute(text("SELECT agent_slug FROM valuz_automation")).scalar_one()
        binding = conn.execute(
            text("SELECT agent_slug, secret_ref FROM valuz_agent_channel_binding")
        ).one()

    assert [row["slug"] for row in rows] == ["valurion", "valuz-helper"]
    valurion, helper = rows
    assert valurion["kind"] == "system"
    assert valurion["resource_policy"] == "all_available"
    assert valurion["instructions"] == ""
    assert valurion["runtime"] == "claude_agent"
    assert valurion["model"] == "claude-sonnet-4-6"
    assert valurion["provider_id"] is None
    assert valurion["effort"] == "high"
    assert json.loads(valurion["skills"]) == []
    assert valurion["source"] == "builtin"
    assert bool(valurion["readonly"]) is True
    assert bool(valurion["deletable"]) is False
    assert helper["id"] == "id-owner-1-valuz-helper"
    assert helper["name"] == "My Helper"
    assert helper["instructions"] == "My carefully customized workflow."
    assert json.loads(helper["skills"]) == ["valuz-handbook"]
    assert json.loads(helper["connector_types"]) == ["valuz-search", "valuz-stock"]
    assert helper["kind"] == "standard"
    assert helper["resource_policy"] == "explicit"
    assert helper["source"] == "official"
    assert bool(helper["readonly"]) is False
    assert bool(helper["deletable"]) is True
    assert source_ref == "valuz-helper"
    assert automation_ref == "valuz-helper"
    assert binding == ("valuz-helper", "channel/feishu/valuz-helper")


def test_existing_valurion_and_legacy_helper_are_both_left_unchanged() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_schema(conn)
        _insert_agent(
            conn,
            slug="valurion",
            instructions="Existing Valurion instructions.",
            source="builtin",
            name="Valurion",
        )
        conn.execute(
            text(
                """
                UPDATE valuz_agent
                SET runtime = 'codex', model = 'gpt-existing',
                    provider_id = 'provider-existing', effort = 'xhigh',
                    kind = 'system'
                WHERE slug = 'valurion'
                """
            )
        )
        _insert_agent(
            conn,
            slug="valuz-helper",
            instructions="My customized legacy workflow.",
            name="My Legacy Helper",
        )

        migration = _load()
        migration.op = _Op(conn)
        migration._install_valurion()

        rows = (
            conn.execute(
                text(
                    "SELECT id, slug, instructions, runtime, model, provider_id, "
                    "effort, kind FROM valuz_agent ORDER BY slug"
                )
            )
            .mappings()
            .all()
        )

    assert [row["slug"] for row in rows] == ["valurion", "valuz-helper"]
    valurion, helper = rows
    assert valurion["id"] == "id-owner-1-valurion"
    assert valurion["instructions"] == "Existing Valurion instructions."
    assert valurion["runtime"] == "codex"
    assert valurion["model"] == "gpt-existing"
    assert valurion["provider_id"] == "provider-existing"
    assert valurion["effort"] == "xhigh"
    assert valurion["kind"] == "system"
    assert helper["id"] == "id-owner-1-valuz-helper"
    assert helper["instructions"] == "My customized legacy workflow."
    assert helper["kind"] == "standard"


def test_install_valurion_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_schema(conn)
        _insert_agent(conn, slug="valuz-helper", instructions="Keep me.")

        migration = _load()
        migration.op = _Op(conn)
        migration._install_valurion()
        migration._install_valurion()

        slugs = list(conn.execute(text("SELECT slug FROM valuz_agent ORDER BY slug")).scalars())

    assert slugs == ["valurion", "valuz-helper"]


def test_uninstall_valurion_only_removes_the_system_builtin_row() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        _create_schema(conn)
        _insert_agent(
            conn,
            slug="valuz-helper",
            instructions="Keep legacy helper.",
        )
        _insert_agent(
            conn,
            slug="valurion",
            instructions="Keep user-owned same-slug Agent.",
            source="custom",
            user_id="owner-2",
        )

        migration = _load()
        migration.op = _Op(conn)
        migration._install_valurion()
        conn.execute(
            text(
                """
                UPDATE valuz_agent
                SET kind = 'system', source = 'builtin', readonly = 1, deletable = 0
                WHERE user_id = 'owner-1' AND slug = 'valurion'
                """
            )
        )
        migration._uninstall_valurion()

        rows = (
            conn.execute(
                text(
                    "SELECT user_id, slug, source, instructions "
                    "FROM valuz_agent ORDER BY user_id, slug"
                )
            )
            .mappings()
            .all()
        )

    assert rows == [
        {
            "user_id": "owner-1",
            "slug": "valuz-helper",
            "source": "official",
            "instructions": "Keep legacy helper.",
        },
        {
            "user_id": "owner-2",
            "slug": "valurion",
            "source": "custom",
            "instructions": "Keep user-owned same-slug Agent.",
        },
    ]
