"""Actual owner-scoped SQL CAS recovery, with no replacement conversation or turn."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
from app.routes.sessions import recover_failed_session
from app.schemas import RecoverFailedSessionRequest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.adapters.sqlalchemy_store.models import Base as KernelBase
from src.adapters.sqlalchemy_store.models import SessionModel
from src.adapters.sqlalchemy_store.store import SQLAlchemyStore
from src.core.agent_config import AgentConfig
from src.core.types import Error, Message, Session, UserInterrupt, UserMessage
from src.runtimes.deepagents.runtime import _allows_explicit_owner_retry

import valuz_agent.boot.kernel  # noqa: F401
from valuz_agent.adapters import data_reader, kernel_client
from valuz_agent.adapters.kernel_client_http import HttpKernelClient
from valuz_agent.facade.sessions import SessionLibrary
from valuz_agent.infra import db as host_db
from valuz_agent.modules.sessions.errors import SessionNotRunnable
from valuz_agent.modules.sessions.models import QueuedInputRow
from valuz_agent.ports.billing import BudgetStatus
from valuz_agent.ports.extensions import ext


@pytest.fixture
async def recovery(tmp_path, monkeypatch):
    kernel_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/kernel.sqlite")
    host_engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/host.sqlite")
    async with kernel_engine.begin() as conn:
        await conn.run_sync(KernelBase.metadata.create_all)
    async with host_engine.begin() as conn:
        await conn.run_sync(QueuedInputRow.__table__.create)
    factory = async_sessionmaker(host_engine, expire_on_commit=False)
    monkeypatch.setattr(host_db, "AsyncSessionLocal", factory)
    monkeypatch.setattr(data_reader, "_reader", None)
    store = SQLAlchemyStore(async_sessionmaker(kernel_engine, expire_on_commit=False))
    monkeypatch.setattr(kernel_client, "client", kernel_client.InProcessKernelClient(lambda: store))
    kernel_client.bind_host_data_store(lambda: store)
    reason = Error(
        category="execution_error", retry_status="exhausted", message="synthetic failure"
    )
    session = Session(
        id="main",
        user_id="owner",
        agent_config=AgentConfig(id="a", name="a"),
        cwd=str(tmp_path),
        status="terminated",
        stop_reason=reason,
        instructions="Frozen original instructions",
        runtime_session_id="original-native-thread",
        permission_mode="default",
        metadata={"valuz": {"project_id": "chat", "agent_slug": "valurion"}},
    )
    await store.save_session(session)
    message = Message(
        id="failed-message",
        session_id="main",
        user_message=UserMessage(text="original background input"),
        started_at=100,
        status="errored",
        stop_reason=reason,
        error_message={"category": "execution_error", "recovery": "explicit_owner_retry"},
    )
    await store.save_message("owner", message)
    async with factory() as db:
        db.add(
            QueuedInputRow(
                id="original-input",
                user_id="owner",
                session_id="main",
                project_id="chat",
                status="failed",
                input={"text": "original background input", "source": "background"},
                output_message_id=message.id,
            )
        )
        await db.commit()
    yield store, session, message, kernel_engine, host_engine
    kernel_client.bind_host_data_store(None)
    await kernel_engine.dispose()
    await host_engine.dispose()


async def _restore(owner="owner", input_id="original-input"):
    return await SessionLibrary(owner).recover_failed_background_input(
        "main", input_id, project_id="chat", agent_slug="valurion"
    )


async def _request():
    session = await kernel_client.get_session("owner", "main")
    fingerprint = hashlib.sha256(
        json.dumps(session.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RecoverFailedSessionRequest(
        failed_message_id="failed-message",
        project_id="chat",
        agent_slug="valurion",
        expected_snapshot_hash=fingerprint,
    )


async def test_exact_failed_input_recovers_only_status_and_reopens_sql(recovery):
    store, session, _message, kernel_engine, _ = recovery
    async with kernel_engine.connect() as connection:
        before = dict((await connection.execute(select(SessionModel.__table__))).mappings().one())
    assert await _restore()
    current = await store.load_session("owner", "main")
    async with kernel_engine.connect() as connection:
        after = dict((await connection.execute(select(SessionModel.__table__))).mappings().one())
    assert after == {**before, "status": "idle"}
    assert current.instructions == session.instructions
    assert current.runtime_session_id == session.runtime_session_id
    assert await _restore() is False
    assert len(await store.list_messages_for_session("owner", "main")) == 1
    await kernel_engine.dispose()
    reopened = create_async_engine(kernel_engine.url)
    try:
        actual = SQLAlchemyStore(async_sessionmaker(reopened, expire_on_commit=False))
        current = await actual.load_session("owner", "main")
        assert current.status == "idle"
        async with reopened.connect() as connection:
            actual_row = dict(
                (await connection.execute(select(SessionModel.__table__))).mappings().one()
            )
        assert actual_row == after
    finally:
        await reopened.dispose()


@pytest.mark.parametrize("invalid", ["foreign", "input", "task", "worktree", "agent", "project"])
async def test_foreign_input_or_nonordinary_binding_refuses_recovery(recovery, invalid):
    store, session, _, _, _ = recovery
    if invalid in {"task", "worktree", "agent", "project"}:
        meta = dict(session.metadata["valuz"])
        meta[
            {"agent": "agent_slug", "project": "project_id"}.get(
                invalid, invalid if invalid == "worktree" else "task_id"
            )
        ] = "changed"
        session.metadata = {"valuz": meta}
        await store.save_session(session)
    with pytest.raises(SessionNotRunnable):
        await _restore(
            "foreign" if invalid == "foreign" else "owner",
            "missing" if invalid == "input" else "original-input",
        )
    assert (await store.load_session("owner", "main")).status == "terminated"


@pytest.mark.parametrize("invalid", ["unmarked", "permission", "context", "budget", "user_stop"])
async def test_unclassified_or_protected_stop_refuses_recovery(recovery, invalid):
    store, session, message, _, _ = recovery
    if invalid == "unmarked":
        message.error_message = {"category": "execution_error", "message": "even says 503"}
        await store.save_message("owner", message)
        expected = kernel_client.KernelConflictError
    else:
        session.stop_reason = (
            UserInterrupt()
            if invalid == "user_stop"
            else Error(category=invalid, retry_status="terminal")
        )
        await store.save_session(session)
        expected = SessionNotRunnable
    with pytest.raises(expected):
        await _restore()
    assert (await store.load_session("owner", "main")).status == "terminated"


@pytest.mark.parametrize(
    "race", ["running", "metadata", "permission", "cancel", "new_message", "equal_time_message"]
)
async def test_atomic_cas_refuses_newer_foreground_or_owner_stop(recovery, race):
    store, session, _message, _, _ = recovery
    request = await _request()
    captured = await store.load_session("owner", "main")
    # Simulate another actor after the caller captured its exact source snapshot.
    if race in {"new_message", "equal_time_message"}:
        await store.save_message(
            "owner",
            Message(
                id="new-foreground",
                session_id="main",
                user_message=UserMessage(text="new owner request"),
                started_at=101 if race == "new_message" else 100,
            ),
        )
    else:
        if race == "running":
            session.status = "running"
        elif race == "cancel":
            session.stop_reason = UserInterrupt()
        elif race == "permission":
            session.permission_mode = "full_access"
        else:
            session.metadata = {**session.metadata, "new_owner_edit": True}
        await store.save_session(session)
    # Also exercise the actual UPDATE after a route could already have loaded
    # its old snapshot, rather than relying only on the HTTP hash precheck.
    assert not await store.recover_failed_session_if_current("owner", captured, "failed-message")
    with pytest.raises(kernel_client.KernelConflictError):
        await kernel_client.recover_failed_session("owner", "main", request)
    current = await store.load_session("owner", "main")
    assert current.status == ("running" if race == "running" else "terminated")
    assert current.stop_reason == session.stop_reason


async def test_unsupported_store_requires_manual_recovery_without_save(recovery, monkeypatch):
    store, _, _, _, _ = recovery

    class UnsupportedStore:
        async def load_session(self, *args):
            return await store.load_session(*args)

    request = await _request()
    kernel_client.bind_host_data_store(lambda: UnsupportedStore())
    monkeypatch.setattr(
        kernel_client, "client", kernel_client.InProcessKernelClient(lambda: UnsupportedStore())
    )
    with pytest.raises(kernel_client.KernelNotImplementedError):
        await kernel_client.recover_failed_session("owner", "main", request)
    assert (await store.load_session("owner", "main")).status == "terminated"


async def test_current_budget_denial_prevents_recovery(recovery, monkeypatch):
    store, _, _, _, _ = recovery

    class DeniedBudget:
        async def check_budget(self, *args, **kwargs):
            return BudgetStatus(allowed=False, reason="synthetic denied budget")

    monkeypatch.setattr(ext, "billing", DeniedBudget())
    from valuz_agent.modules.sessions.errors import BudgetExceeded

    with pytest.raises(BudgetExceeded):
        await _restore()
    assert (await store.load_session("owner", "main")).status == "terminated"


@pytest.mark.parametrize(
    "invalid", ["message_owner", "message_session", "message_status", "message_id"]
)
async def test_original_failed_input_requires_exact_owned_errored_output_message(recovery, invalid):
    store, _session, message, _, _ = recovery
    if invalid == "message_owner":
        await store.save_message("foreign", message)
    elif invalid == "message_session":
        message.session_id = "another-session"
        await store.save_message("owner", message)
    elif invalid == "message_status":
        message.status = "completed"
        await store.save_message("owner", message)
    else:
        async with host_db.async_unit_of_work() as db:
            row = await db.get(QueuedInputRow, "original-input")
            row.output_message_id = "not-the-original-output"
    with pytest.raises(kernel_client.KernelConflictError):
        await _restore()
    assert (await store.load_session("owner", "main")).status == "terminated"


async def test_actual_host_cancel_records_user_stop_and_refuses_later_recovery(recovery):
    store, _, _, _, _ = recovery
    from valuz_agent.modules.automations.in_process_runner import InProcessAutomationRunner

    async with host_db.async_unit_of_work(commit=False) as db:
        service = InProcessAutomationRunner()._build_session_service(db)
        await service.cancel("main", user_id="owner")
    current = await store.load_session("owner", "main")
    assert current.status == "terminated" and isinstance(current.stop_reason, UserInterrupt)
    with pytest.raises(SessionNotRunnable):
        await _restore()


@pytest.mark.parametrize("status", ["archived", "cancelled"])
async def test_host_reader_archive_or_cancel_is_never_recovered(recovery, monkeypatch, status):
    store, _, _, _, _ = recovery
    captured = await kernel_client.get_session("owner", "main")

    class RetiredAuthorityReader:
        async def get_session(self, owner, session_id):
            assert (owner, session_id) == ("owner", "main")
            return captured.model_copy(update={"status": status})

    monkeypatch.setattr(data_reader, "_reader", RetiredAuthorityReader())
    with pytest.raises(SessionNotRunnable):
        await _restore()
    assert (await store.load_session("owner", "main")).status == "terminated"


async def test_http_client_uses_the_same_owned_recovery_route(recovery):
    store, _, _, _, _ = recovery
    app = FastAPI()

    @app.post("/kernel/v1/sessions/{session_id}/recover-failed")
    async def actual(session_id: str, body: RecoverFailedSessionRequest):
        return await recover_failed_session(session_id, body, store, "owner")

    client = HttpKernelClient("http://isolated-kernel")
    await client._http.aclose()
    client._http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://isolated-kernel"
    )
    try:
        result = await client.recover_failed_session("owner", "main", await _request())
        assert result.status == "idle" and result.id == "main"
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "status,allowed",
    [
        (400, False),
        (401, False),
        (402, False),
        (403, False),
        (429, False),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
    ],
)
def test_structured_gateway_status_does_not_parse_error_text(status, allowed):
    request = httpx.Request("POST", "http://isolated-gateway")
    error = httpx.HTTPStatusError(
        "message says 503 but is untrusted",
        request=request,
        response=httpx.Response(status, request=request),
    )
    assert _allows_explicit_owner_retry(error) is allowed
    assert not _allows_explicit_owner_retry(RuntimeError("503 budget permission context"))


def test_connection_timeout_and_mixed_exception_group_classification():
    connection = httpx.ConnectError("synthetic connection failure")
    timeout = httpx.ReadTimeout("synthetic timeout")
    assert _allows_explicit_owner_retry(connection)
    assert _allows_explicit_owner_retry(timeout)
    assert _allows_explicit_owner_retry(ExceptionGroup("network", [connection, timeout]))
    assert not _allows_explicit_owner_retry(
        ExceptionGroup("ambiguous", [connection, PermissionError("denied")])
    )


@pytest.mark.parametrize(
    "code", ["insufficient_quota", "permission_denied", "context_length_exceeded", "user_stop"]
)
@pytest.mark.parametrize("wrapper", ["root", "error", "detail"])
def test_structured_protected_code_overrides_a_misleading_5xx_status(code, wrapper):
    request = httpx.Request("POST", "http://isolated-gateway")
    protected = {"code": code, "type": code, "message": "message text is not classification"}
    body = protected if wrapper == "root" else {wrapper: protected}
    error = httpx.HTTPStatusError(
        "looks like 503", request=request, response=httpx.Response(503, json=body, request=request)
    )
    assert not _allows_explicit_owner_retry(error)


async def test_postgres_cas_uses_structural_jsonb_for_every_json_snapshot_field(recovery):
    actual, _, _, _, _ = recovery
    expected = await actual.load_session("owner", "main")
    statements = []

    class CompileOnlySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def execute(self, statement):
            statements.append(statement)
            return SimpleNamespace(scalar_one_or_none=lambda: None)

        async def commit(self):
            return None

    class CompileOnlyFactory:
        kw = {"bind": SimpleNamespace(dialect=postgresql.dialect())}

        def __call__(self):
            return CompileOnlySession()

    compiling = SQLAlchemyStore(CompileOnlyFactory())
    assert not await compiling.recover_failed_session_if_current(
        "owner", expected, "failed-message"
    )
    sql = str(statements[0].compile(dialect=postgresql.dialect()))
    for field in (
        "agent_config",
        "skills",
        "mcp_servers",
        "model_provider",
        "model_settings",
        "stop_reason",
        "metadata",
        "todos",
    ):
        assert f"CAST(sessions.{field} AS JSONB)" in sql
        assert f"sessions.{field} =" not in sql
    assert "CAST(messages.stop_reason AS JSONB)" in sql
    assert "messages.stop_reason =" not in sql
