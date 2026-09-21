"""0050: automation contracts columns + the relaxed agent NOT NULLs, both ways."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config

from alembic import command

_AUTOMATION_COLUMNS = {
    "execution_kind",
    "code_runtime",
    "code_entry",
    "code_timeout_s",
    "input_kind",
    "input_schema",
    "default_input",
    "result_kind",
    "result_schema",
}
_RUN_COLUMNS = {
    "input_json",
    "artifact_json",
    "files_json",
    "executor_ref",
    "log_tail",
    "invoked_by_ref",
    "cancel_requested_at",
}


def config(path: Path) -> Config:
    migrations = Path(__file__).resolve().parents[2] / "alembic" / "host"
    value = Config(str(migrations / "alembic.ini"))
    value.set_main_option("script_location", str(migrations))
    value.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return value


def _cols(engine: sa.Engine, table: str) -> dict[str, dict]:
    return {c["name"]: c for c in sa.inspect(engine).get_columns(table)}


def test_contracts_upgrade_and_downgrade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_path = tmp_path / "host.db"
    cfg = config(db_path)
    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{db_path}")

    automation = _cols(engine, "valuz_automation")
    assert _AUTOMATION_COLUMNS <= set(automation)
    assert automation["agent_kind"]["nullable"] is True
    assert automation["agent_slug"]["nullable"] is True
    run = _cols(engine, "valuz_automation_run")
    assert _RUN_COLUMNS <= set(run)
    checks = {c["name"] for c in sa.inspect(engine).get_check_constraints("valuz_automation")}
    assert {
        "ck_automation_agent_kind",
        "ck_automation_execution_kind",
        "ck_automation_code_entry_when_code",
        "ck_automation_agent_when_agent",
        "ck_automation_input_kind",
        "ck_automation_result_kind",
        "ck_automation_trigger_kind",
    } <= checks

    with engine.begin() as conn:
        base = (
            "INSERT INTO valuz_automation (id, user_id, name, project_id, prompt_template, "
            "action_kind, worktree, trigger_kind, status, execution_kind, input_kind, result_kind, "
            "agent_kind, agent_slug, code_runtime, code_entry, created_at, updated_at) VALUES "
        )
        conn.execute(
            sa.text(
                base
                + (
                    "('agent-1','u','a','p','do it','chat',0,'manual','enabled','agent','none',"
                    "'conversation','library_agent','x',NULL,NULL,0,0)"
                )
            )
        )
        conn.execute(
            sa.text(
                base
                + (
                    "('code-1','u','c','p','','chat',0,'manual','enabled','code','json',"
                    "'artifact',NULL,NULL,'python','automations/x.py',0,0)"
                )
            )
        )
        # A code row without an entry violates ck_automation_code_entry_when_code.
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    base
                    + (
                        "('code-2','u','c','p','','chat',0,'manual','enabled','code','none',"
                        "'artifact',NULL,NULL,'python',NULL,0,0)"
                    )
                )
            )
    with engine.begin() as conn:
        # An agent row without an agent violates ck_automation_agent_when_agent.
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(
                sa.text(
                    base
                    + (
                        "('agent-2','u','a','p','do it','chat',0,'manual','enabled','agent','none',"
                        "'conversation',NULL,NULL,NULL,NULL,0,0)"
                    )
                )
            )
    engine.dispose()

    command.downgrade(cfg, "0049")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    automation = _cols(engine, "valuz_automation")
    assert not (_AUTOMATION_COLUMNS & set(automation))
    assert automation["agent_slug"]["nullable"] is False
    assert not (_RUN_COLUMNS & set(_cols(engine, "valuz_automation_run")))
    with engine.connect() as conn:
        ids = {r[0] for r in conn.execute(sa.text("SELECT id FROM valuz_automation"))}
    assert ids == {"agent-1"}  # the code row cannot exist without its columns
    engine.dispose()

    command.upgrade(cfg, "head")
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        kind = conn.execute(
            sa.text(
                "SELECT execution_kind, input_kind, result_kind FROM valuz_automation "
                "WHERE id='agent-1'"
            )
        ).one()
    assert tuple(kind) == ("agent", "none", "conversation")
    engine.dispose()
