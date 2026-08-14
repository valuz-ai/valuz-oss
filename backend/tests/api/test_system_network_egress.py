from __future__ import annotations

import pytest
from fastapi import HTTPException

import valuz_agent.boot.kernel  # noqa: F401 - installs kernel src/app paths
from valuz_agent.api.routes.system import (
    NetworkEgressReconfigureRequest,
    reconfigure_network_egress,
)


class _FakeOrchestrator:
    active_sessions: set[str] = set()

    def __init__(self) -> None:
        self.calls: list[object] = []

    def warm_runtime_candidates(self, *, limit: int) -> list[tuple[str, str]]:
        self.calls.append(("candidates", limit))
        return [("owner-1", "session-1")]

    async def evict_all_warm_runtimes(self) -> None:
        self.calls.append("evict")

    async def prepare_runtime(self, owner_id: str, session_id: str) -> None:
        self.calls.append(("prepare", owner_id, session_id))


@pytest.mark.asyncio
async def test_desktop_reconfigures_and_prewarms_without_backend_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import dependencies
    from src.runtimes import network_egress

    token = "desktop-control-" + "x" * 32
    orchestrator = _FakeOrchestrator()
    replaced: list[object] = []

    async def replace(payload, *, required_unavailable: bool = False) -> None:
        replaced.append((payload, required_unavailable))

    monkeypatch.setattr(network_egress, "_desktop_control_token", token)
    monkeypatch.setattr(network_egress, "replace_network_egress", replace)
    monkeypatch.setattr(dependencies, "get_orchestrator", lambda: orchestrator)

    response = await reconfigure_network_egress(
        NetworkEgressReconfigureRequest(
            bootstrap={"mode": "auto"},
            prewarm_limit=1,
        ),
        token,
    )

    assert response.configured is True
    assert response.prewarmed_session_ids == ["session-1"]
    assert replaced == [({"mode": "auto"}, False)]
    assert orchestrator.calls == [
        ("candidates", 1),
        "evict",
        ("prepare", "owner-1", "session-1"),
    ]


@pytest.mark.asyncio
async def test_desktop_reconfigure_rejects_missing_capability() -> None:
    with pytest.raises(HTTPException) as error:
        await reconfigure_network_egress(NetworkEgressReconfigureRequest(), None)
    assert error.value.status_code == 401
