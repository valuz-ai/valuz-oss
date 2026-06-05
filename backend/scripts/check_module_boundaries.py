#!/usr/bin/env python3
"""Module boundary guard — forbid cross-module datastore imports.

A business module under ``valuz_agent/modules/<Y>/`` must not import another
module's persistence layer (``valuz_agent.modules.<X>.datastore`` with
``X != Y``). Cross-module collaboration goes through the other module's
**service** API or a ``ports/`` protocol — never its datastore. Reaching into
a sibling's persistence layer is the tightest possible coupling; it makes the
two modules impossible to reason about (or refactor) in isolation.

See ``docs/exec-plans/active/backend-architecture-refactor.md`` (T1.3).

Existing violations are grandfathered in ``ALLOWLIST`` (one ``(importer,
owner)`` pair per edge) and burned down by later refactor slices. The guard's
job is to stop **new** module-pairs from coupling at the datastore layer —
do NOT add entries to grow the list.

Usage::

    uv run python scripts/check_module_boundaries.py    # exits 1 on violation
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MODULES_ROOT = Path(__file__).resolve().parent.parent / "valuz_agent" / "modules"

# Grandfathered ``(importer_module, datastore_owner_module)`` edges, seeded
# 2026-06-03 immediately after the Tier-0 cleanup. Burn these down as the
# T1.1/T1.2 refactor slices route the calls through service APIs; never add.
ALLOWLIST: set[tuple[str, str]] = {
    ("automations", "agents"),
    ("automations", "connectors"),
    ("automations", "docs"),
    ("automations", "projects"),
    ("automations", "providers"),
    ("automations", "skills"),
    ("automations", "tasks"),
    ("decisions", "projects"),
    ("decisions", "tasks"),
    ("projects", "automations"),
    ("projects", "connectors"),
    ("projects", "docs"),
    ("projects", "sessions"),
    ("projects", "skills"),
    ("resources", "connectors"),
    ("resources", "skills"),
    ("runs", "projects"),
    ("runs", "tasks"),
    ("sessions", "agents"),
    ("sessions", "connectors"),
    ("sessions", "docs"),
    ("sessions", "projects"),
    ("sessions", "providers"),
    ("sessions", "skills"),
    ("skills", "projects"),
    ("skills", "sessions"),
    ("tasks", "agents"),
    ("tasks", "projects"),
    ("tasks", "providers"),
}


def _owning_module(path: Path) -> str | None:
    """The ``modules/<Y>/`` package a source file belongs to, if any."""
    try:
        rel = path.relative_to(MODULES_ROOT)
    except ValueError:
        return None
    return rel.parts[0] if len(rel.parts) > 1 else None


def _datastore_owner(dotted: str) -> str | None:
    """``valuz_agent.modules.<X>.datastore[...]`` → ``X``, else ``None``."""
    parts = dotted.split(".")
    if parts[:2] == ["valuz_agent", "modules"] and len(parts) >= 4 and parts[3] == "datastore":
        return parts[2]
    return None


def _imported_dotted_paths(tree: ast.AST):
    """Yield ``(dotted_path, lineno)`` for every import in a parsed module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module, node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno


def main() -> int:
    violations: list[tuple[Path, int, str, str]] = []
    for py in sorted(MODULES_ROOT.rglob("*.py")):
        owner = _owning_module(py)
        if owner is None:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for dotted, lineno in _imported_dotted_paths(tree):
            target = _datastore_owner(dotted)
            if target and target != owner and (owner, target) not in ALLOWLIST:
                violations.append((py, lineno, owner, target))

    if violations:
        print("Module boundary violations — a module imported a sibling's datastore:")
        for py, lineno, owner, target in violations:
            rel = py.relative_to(MODULES_ROOT.parent.parent)
            print(f"  {rel}:{lineno}  {owner} → {target}.datastore")
        print(
            "\nRoute cross-module collaboration through the sibling's service API\n"
            "or a ports/ protocol, not its datastore. If this is a legitimate\n"
            "transitional edge, see T1.3 in the backend-architecture-refactor plan."
        )
        return 1

    print("module boundaries OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
