"""Dump the canonical SQLite schema to docs/generated/database-schema.sql.

Run after any ORM model change so the checked-in schema reference stays
honest:

    cd backend && uv run python scripts/dump_schema.py

The generated file is a snapshot of what `Base.metadata.create_all()`
emits against an empty SQLite DB — the same DDL that runs on a fresh
install. It does NOT include data migrations; those live under
backend/valuz_agent/migrations/.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend/ is on sys.path so `valuz_agent` resolves when run as
# `uv run python scripts/dump_schema.py` from the backend dir.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from valuz_agent.infra.database import Base  # noqa: E402
from valuz_agent.modules.agents import models as _agents  # noqa: F401, E402
from valuz_agent.modules.connectors import models as _connectors  # noqa: F401, E402
from valuz_agent.modules.docs import models as _docs  # noqa: F401, E402
from valuz_agent.modules.parser import models as _parser  # noqa: F401, E402
from valuz_agent.modules.projects import models as _projects  # noqa: F401, E402
from valuz_agent.modules.providers import models as _providers  # noqa: F401, E402

# Force-import every module that defines a Mapped class so Base.metadata
# sees it. New domains added in the future need to be added here.
from valuz_agent.modules.schedules import models as _schedules  # noqa: F401, E402
from valuz_agent.modules.sessions import models as _sessions  # noqa: F401, E402
from valuz_agent.modules.settings import models as _settings_models  # noqa: F401, E402
from valuz_agent.modules.skills import models as _skills  # noqa: F401, E402
from valuz_agent.modules.tasks import models as _tasks  # noqa: F401, E402


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    target = repo_root / "docs" / "generated" / "database-schema.sql"
    target.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine("sqlite:///:memory:")
    out: list[str] = [
        "-- Auto-generated from SQLAlchemy ORM by scripts/dump_schema.py.",
        "-- Do NOT hand-edit; regenerate after model changes.",
        "",
    ]
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(engine)).strip().rstrip(";")
        out.append(ddl + ";")
        out.append("")
        # ``table.indexes`` is a set — sort by name for deterministic output.
        for idx in sorted(table.indexes, key=lambda i: i.name or ""):
            idx_ddl = str(CreateIndex(idx).compile(engine)).strip().rstrip(";")
            out.append(idx_ddl + ";")
        out.append("")

    target.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {target} ({len(Base.metadata.sorted_tables)} tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
