"""The MCP tool error surface: no internal leakage, honest labels."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from valuz_agent.modules.tasks import gate
from valuz_agent.modules.tasks.outcome import Failure
from valuz_agent.modules.tasks.tools.handlers import _guarded


def test_guarded_never_echoes_exception_text() -> None:
    """A crash message can carry SQL + bind parameters (OperationalError) or
    absolute host paths (OSError) — the model must see a generic line, the
    log gets the traceback."""
    secret = "SELECT api_key FROM users WHERE id = 'sk-live-123'"

    async def _boom(args: dict, ctx: object) -> object:
        raise RuntimeError(secret)

    wrapped = _guarded("dispatch", _boom)  # type: ignore[arg-type]
    res = asyncio.run(wrapped({}, SimpleNamespace(session_id="s1")))  # type: ignore[arg-type]
    assert res.is_error
    assert secret not in res.content, "raw exception text must never reach the model"
    assert "RuntimeError" in res.content  # the type name alone is safe + useful


def test_lead_gate_rejection_names_the_actual_tool() -> None:
    """Seven tools share the gate; a finish_task rejection must not read
    'dispatch: ...' — the model acts on that label."""
    non_lead = SimpleNamespace(metadata={"valuz": {"run_kind": "subtask"}})
    verdict = gate.check_lead_gate(non_lead, tool="finish_task")
    assert isinstance(verdict, Failure)
    assert verdict.reason.startswith("finish_task:")
    assert "dispatch" not in verdict.reason
