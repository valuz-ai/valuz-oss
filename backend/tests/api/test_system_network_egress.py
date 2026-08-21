from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException

import valuz_agent.boot.kernel  # noqa: F401 - installs kernel src/app paths
from valuz_agent.api.routes.system import (
    DesktopNetworkEgressInterruptRequest,
    NetworkEgressReconfigureRequest,
    get_network_egress_activity,
    interrupt_network_egress_activity,
    reconfigure_network_egress,
    router,
)


class _FakeOrchestrator:
    def __init__(self, active_sessions: set[str] | None = None) -> None:
        self.active_sessions = active_sessions or set()
        self.calls: list[object] = []

    def warm_runtime_candidates(self, *, limit: int) -> list[tuple[str, str]]:
        self.calls.append(("candidates", limit))
        return [("owner-1", "session-1")]

    async def evict_all_warm_runtimes(self) -> None:
        self.calls.append("evict")

    async def prepare_runtime(self, owner_id: str, session_id: str) -> None:
        self.calls.append(("prepare", owner_id, session_id))

    async def interrupt(self, session_id: str) -> bool:
        self.calls.append(("interrupt", session_id))
        if session_id not in self.active_sessions:
            return False
        self.active_sessions.remove(session_id)
        return True


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


@pytest.mark.asyncio
async def test_desktop_activity_uses_process_registry_not_owner_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import dependencies
    from src.runtimes import network_egress

    token = "desktop-control-" + "x" * 32
    orchestrator = _FakeOrchestrator({"session-b", "session-a"})
    monkeypatch.setattr(network_egress, "_desktop_control_token", token)
    monkeypatch.setattr(dependencies, "get_orchestrator", lambda: orchestrator)

    response = await get_network_egress_activity(token)

    assert response.active_session_ids == ["session-a", "session-b"]
    assert orchestrator.calls == []


@pytest.mark.asyncio
async def test_desktop_interrupts_only_explicitly_confirmed_active_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import dependencies
    from src.runtimes import network_egress

    token = "desktop-control-" + "x" * 32
    orchestrator = _FakeOrchestrator({"session-a", "session-unconfirmed"})
    monkeypatch.setattr(network_egress, "_desktop_control_token", token)
    monkeypatch.setattr(dependencies, "get_orchestrator", lambda: orchestrator)

    response = await interrupt_network_egress_activity(
        DesktopNetworkEgressInterruptRequest(
            session_ids=["session-a", "already-finished", "session-a"],
        ),
        token,
    )

    assert response.interrupted_session_ids == ["session-a"]
    assert response.inactive_session_ids == ["already-finished"]
    assert orchestrator.active_sessions == {"session-unconfirmed"}


@pytest.mark.asyncio
async def test_desktop_activity_and_interrupt_reject_missing_capability() -> None:
    with pytest.raises(HTTPException) as activity_error:
        await get_network_egress_activity(None)
    assert activity_error.value.status_code == 401

    with pytest.raises(HTTPException) as interrupt_error:
        await interrupt_network_egress_activity(
            DesktopNetworkEgressInterruptRequest(session_ids=["session-a"]),
            None,
        )
    assert interrupt_error.value.status_code == 401


def test_desktop_control_openapi_exposes_activity_and_interrupt_contracts() -> None:
    app = FastAPI()
    app.include_router(router)
    schema = app.openapi()

    activity = schema["paths"]["/v1/system/network-egress/activity"]["get"]
    interrupt = schema["paths"]["/v1/system/network-egress/interrupt"]["post"]
    assert activity["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DesktopNetworkEgressActivityResponse"
    }
    assert interrupt["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/DesktopNetworkEgressInterruptRequest"
    }
