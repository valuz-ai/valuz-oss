"""The WS control reply is private to the request, not the session event bus."""

# ruff: noqa: I001 — kernel bootstrap precedes app imports
from types import SimpleNamespace
import pytest
import valuz_agent.boot.kernel  # noqa: F401
from app.routes import run
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.core.events import Event


@pytest.mark.parametrize("correlated", [True, False])
def test_run_route_negotiates_before_input_and_returns_actual_message(monkeypatch, correlated):
    monkeypatch.setenv("KERNEL_AUTH_TOKEN", "synthetic-token")
    calls = []

    class Store:
        async def load_session(self, owner, session_id):
            assert owner == "owner" and session_id == "main"
            return SimpleNamespace(id="main")

    class Orch:
        async def attach_session_sink(self, owner, session_id, sink):
            self.sink = sink

        async def detach_session_sink(self, session_id, sink):
            pass

        async def run_turn(self, owner, session_id, message, **kwargs):
            calls.append((owner, session_id, message.text))
            await self.sink.emit(
                Event(type="session_idle", data={"message_id": "actual-execution"})
            )
            return SimpleNamespace(id="actual-execution")

    monkeypatch.setattr(run, "get_store", lambda: Store())
    monkeypatch.setattr(run, "get_orchestrator", lambda: Orch())
    app = FastAPI()
    app.include_router(run.router)
    headers = {"Authorization": "Bearer synthetic-token", "X-Valuz-Owner-Id": "owner"}
    if correlated:
        headers["X-Valuz-Completion-Correlation"] = "1"
    with (
        TestClient(app) as http,
        http.websocket_connect("/kernel/v1/sessions/main/run", headers=headers) as ws,
    ):
        if correlated:
            assert ws.receive_json() == {
                "type": "run_capabilities",
                "data": {"completion_correlation": "execution_request_id-v1"},
            }
            assert calls == []
        ws.send_json(
            {"execution_request_id": "owned-request", "message": {"text": "synthetic input"}}
        )
        assert ws.receive_json()["type"] == "session_idle"
        if correlated:
            assert ws.receive_json() == {
                "type": "run_result",
                "data": {"execution_request_id": "owned-request", "message_id": "actual-execution"},
            }
    assert calls == [("owner", "main", "synthetic input")]
