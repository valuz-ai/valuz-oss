"""Fixture-tree tests for the kernel boundary rules in
``scripts/check_module_boundaries.py``.

PR #85 review follow-up: the rules (forbidden deep imports incl. the
``kernel.``-prefixed spellings, seam-only prefixes, the ``src.core``
allowlist) had no negative/positive cases pinning them.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_module_boundaries.py"


@pytest.fixture
def checker(tmp_path, monkeypatch):
    """Load the script as a module with HOST_ROOT pointed at a tmp tree."""
    spec = importlib.util.spec_from_file_location("check_module_boundaries", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HOST_ROOT", tmp_path)
    return mod, tmp_path


def _write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_deep_kernel_imports_flagged_in_both_spellings(checker) -> None:
    mod, root = checker
    _write(root, "modules/foo/service.py", "from src.adapters.sqlalchemy_store import store\n")
    _write(root, "modules/bar/service.py", "import kernel.src.adapters.sqlalchemy_store\n")
    _write(root, "modules/baz/service.py", "from kernel.app import main\n")
    _write(root, "modules/ok/service.py", "from app.schemas import SessionData\n")

    problems = mod.check_kernel_boundary()

    flagged = "\n".join(problems)
    assert "modules/foo/service.py" in flagged
    assert "modules/bar/service.py" in flagged
    assert "modules/baz/service.py" in flagged
    assert "modules/ok/service.py" not in flagged


def test_seam_only_prefixes_restricted_to_allowlist(checker) -> None:
    mod, root = checker
    # The seam itself is allowed…
    _write(root, "adapters/kernel_client.py", "from app.dependencies import get_store\n")
    _write(root, "boot/kernel.py", "from app.routes.sessions import router\n")
    # …anyone else is not.
    _write(root, "modules/foo/service.py", "from app.dependencies import get_orchestrator\n")
    _write(root, "modules/bar/service.py", "from app.event_stream import QueueEventSink\n")

    problems = mod.check_kernel_boundary()

    flagged = "\n".join(problems)
    assert "modules/foo/service.py" in flagged
    assert "modules/bar/service.py" in flagged
    assert "adapters/kernel_client.py" not in flagged
    assert "boot/kernel.py" not in flagged


def test_src_core_restricted_to_documented_exemptions(checker) -> None:
    mod, root = checker
    _write(root, "modules/agents/service.py", "from src.core import AgentConfig\n")  # exempt
    _write(root, "modules/foo/service.py", "from src.core.types import Session\n")  # leak

    problems = mod.check_kernel_boundary()

    flagged = "\n".join(problems)
    assert "modules/foo/service.py" in flagged
    assert "modules/agents/service.py" not in flagged


def test_wire_schema_imports_allowed_everywhere(checker) -> None:
    mod, root = checker
    _write(root, "modules/foo/service.py", "from app.schemas import SessionData\n")
    _write(root, "modules/foo/mapper.py", "from app.serializers import session_to_data\n")

    assert mod.check_kernel_boundary() == []
