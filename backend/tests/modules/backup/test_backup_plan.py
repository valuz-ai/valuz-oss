"""Backup COVERAGE tests — what a plan contains, and the registry tripwire.

The engine tests exercise snapshot mechanics on hand-built plans. These
tests pin the other half: ``build_sources`` must resolve every user-data
directory ``FsRegistry`` knows about, or list it as a deliberate exclusion.
Six weeks of new data directories once landed without a single failure
because nothing asserted this.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from valuz_agent.infra.config import settings
from valuz_agent.infra.fs_registry import FsRegistry, fs_registry
from valuz_agent.modules.backup.schemas import BackupScope
from valuz_agent.modules.backup.service import (
    DEEPAGENTS_CHECKPOINT_DB_NAME,
    EXCLUDED_DATA_DIR_ENTRIES,
    build_sources,
)

USER = "backup-plan-user"


@pytest.fixture
def data_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(settings, "data_dir", data)
    monkeypatch.setattr(settings, "user_skills_dir", tmp_path / "skills")
    monkeypatch.setattr(settings, "user_project_root", tmp_path / "Valuz")
    monkeypatch.setattr(settings, "user_skill_staging_dir", data / "skill-creator" / "staging")
    for var in ("DEEPAGENTS_CHECKPOINT_DB", "DEEPAGENTS_CHECKPOINT_ROOT", "VALUZ_DSH_STATE_DIR"):
        monkeypatch.delenv(var, raising=False)
    fs_registry.set_kb_root_resolver(None)
    yield data
    fs_registry.set_kb_root_resolver(None)


def _rels(specs) -> set[str]:
    return {s.rel for s in specs}


def test_default_scope_covers_every_user_data_directory(data_env: Path) -> None:
    kernel_db = data_env / settings.kernel_db_filename
    kernel_db.write_bytes(b"")
    (data_env / DEEPAGENTS_CHECKPOINT_DB_NAME).write_bytes(b"")
    for sub in ("dsh-state", "deepagents-checkpoints", "plugins", "plugins-data"):
        (data_env / sub).mkdir()
    (data_env / settings.installation_filename).write_text("{}", encoding="utf-8")
    (settings.user_skills_dir).mkdir()
    (settings.user_project_root).mkdir()

    sources, extra_dbs = build_sources(
        USER, BackupScope(), external_projects=[], kb_kinds=[], kernel_db=kernel_db
    )

    assert _rels(sources) == {
        "docs",
        "memories",
        "attachments",
        "plugins",
        "plugins-data",
        "installation.json",
        "kb",
        "deepagents-checkpoints",
        "dsh-state",
        "user-skills",
        "projects",
    }
    assert _rels(extra_dbs) == {DEEPAGENTS_CHECKPOINT_DB_NAME}
    # every source really resolves to the temp tree, never the real home
    for spec in sources + extra_dbs:
        assert spec.src.is_relative_to(data_env.parent), spec


def test_scope_switches_drop_their_categories(data_env: Path) -> None:
    (settings.user_skills_dir).mkdir()
    (settings.user_project_root).mkdir()
    scope = BackupScope(user_skills=False, managed_projects=False, external_projects=False)
    sources, _ = build_sources(USER, scope, external_projects=[], kb_kinds=[], kernel_db=None)
    assert "user-skills" not in _rels(sources)
    assert "projects" not in _rels(sources)


def test_external_projects_skip_managed_children(data_env: Path) -> None:
    projects_root = settings.user_project_root
    inside = projects_root / "chats" / "2026" / "09" / "01" / "ABCDEF"
    inside.mkdir(parents=True)
    outside = data_env.parent / "elsewhere"
    outside.mkdir()
    scope = BackupScope(external_projects=True)
    sources, _ = build_sources(
        USER,
        scope,
        external_projects=[("p-in", inside), ("p-out", outside), ("p-gone", outside / "nope")],
        kb_kinds=[],
        kernel_db=None,
    )
    rels = _rels(sources)
    assert "projects-external/p-out" in rels
    assert "projects-external/p-in" not in rels  # covered by "projects"
    assert "projects-external/p-gone" not in rels


def test_kb_root_resolver_roots_are_covered(data_env: Path) -> None:
    """A host that routes KB classes to separate directories (the seam
    added in #839) must see every class it has content for, not just the
    OSS default root."""

    def _resolver(user_id: str, kind: str) -> Path:
        if kind == "org":
            raise ValueError("org KBs are not owner-scoped")
        return fs_registry.data_dir(user_id) / ("kb" if kind == "normal" else f"kb-{kind}")

    fs_registry.set_kb_root_resolver(_resolver)
    sources, _ = build_sources(
        USER,
        BackupScope(),
        external_projects=[],
        kb_kinds=["normal", "conversation", "publication", "org"],
        kernel_db=None,
    )
    rels = _rels(sources)
    assert {"kb", "kb-conversation", "kb-publication"} <= rels
    assert not any(r.startswith("kb-org") for r in rels)
    # the default root and kind="normal" are the same directory — once
    assert sum(1 for r in rels if r == "kb") == 1


def test_runtime_state_env_overrides_are_honoured(
    data_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alt = tmp_path / "alt"
    alt.mkdir()
    ckpt = alt / "ckpt.db"
    ckpt.write_bytes(b"")
    (alt / "dsh").mkdir()
    monkeypatch.setenv("DEEPAGENTS_CHECKPOINT_DB", str(ckpt))
    monkeypatch.setenv("VALUZ_DSH_STATE_DIR", str(alt / "dsh"))
    sources, extra_dbs = build_sources(
        USER, BackupScope(), external_projects=[], kb_kinds=[], kernel_db=data_env / "kernel.db"
    )
    assert [s.src for s in extra_dbs] == [ckpt]
    assert any(s.rel == "dsh-state" and s.src == alt / "dsh" for s in sources)


def test_registry_data_dir_entries_are_all_accounted_for(data_env: Path) -> None:
    """Tripwire: every top-level entry ``FsRegistry`` can create under the
    data dir must be either backed up or explicitly excluded.

    Enumerates every registry method that takes only ``user_id`` (plus the
    ``memory_dir`` scope form), calls it against the temp data dir, and
    checks the first path segment. A new registry method that lands a new
    directory therefore fails here until the backup module takes a
    position on it."""
    produced: dict[str, str] = {}
    for name, member in inspect.getmembers(FsRegistry, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        params = list(inspect.signature(member).parameters.values())[1:]
        required = [p for p in params if p.default is inspect.Parameter.empty]
        if [p.name for p in required] != ["user_id"]:
            continue
        try:
            result = member(fs_registry, USER)
        except Exception:  # noqa: BLE001 — a method needing more context is not a data dir
            continue
        if not isinstance(result, Path):
            continue
        try:
            first = Path(result).relative_to(data_env).parts[0]
        except (ValueError, IndexError):
            continue  # outside the data dir (shared roots, backup root, temp dir)
        produced[first] = name
    produced["memories"] = "memory_dir"

    kernel_db = data_env / settings.kernel_db_filename
    kernel_db.write_bytes(b"")
    for entry in produced:
        path = data_env / entry
        if not path.exists():
            path.mkdir(parents=True)
    sources, extra_dbs = build_sources(
        USER, BackupScope(), external_projects=[], kb_kinds=[], kernel_db=kernel_db
    )
    covered: set[str] = set()
    for spec in sources + extra_dbs:
        try:
            covered.add(Path(spec.src).relative_to(data_env).parts[0])
        except ValueError:
            continue  # skills / projects roots live outside the data dir
    unaccounted = {
        entry: via
        for entry, via in produced.items()
        if entry not in covered and entry not in EXCLUDED_DATA_DIR_ENTRIES
    }
    assert not unaccounted, (
        "FsRegistry creates data-dir entries the backup neither includes nor "
        f"explicitly excludes: {unaccounted}"
    )
