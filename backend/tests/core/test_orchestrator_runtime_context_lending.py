"""What the RUNTIME sees, not what ``create_runtime`` was handed.

``materialize_runtime_context`` was applied to the session passed to
``create_runtime`` and nowhere else. But only ``model_provider.api_key`` is
read at construction: every ``RuntimePort`` method takes the session as an
ARGUMENT and reads its live fields, and that is where ``mcp_servers`` is read.
Claude assembles ``--mcp-config`` inside ``run()``; codex emits its
``mcp_servers.*`` overrides the same way.

So a deployment that stores a marker (the whole point of the per-operation
context) got its model credential filled and shipped the literal 40-character
placeholder as every MCP header. The host gate 403s it, the runtime parks
those servers, and the model reports its tools missing — three layers from the
session that was, by every existing assertion, correctly materialized.

The existing ``test_orchestrator_runtime_context_paths`` checks the
construction argument, which is exactly the half that worked. These tests
assert the other half, for every method that takes a session, plus the
contract that makes lending safe: the runtime's writes still land, and the
session goes back to holding the marker before anything can persist it.
"""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import valuz_agent.boot.kernel  # noqa: F401 — sets sys.path for ``src`` / ``app``

from src.core.agent_config import AgentConfig
from src.core.events import Event
from src.core.orchestrator import SessionOrchestrator
from src.core.runtime_context import runtime_context_marker
from src.core.types import (
    McpHttpServerConfig,
    ModelProvider,
    Session,
    UserMessage,
)

_KEY = "example.runtime"
_MARKER = runtime_context_marker(_KEY)
_CONTEXT = {_KEY: "resolved-secret"}
_HEADER = "X-Internal"


def _session(tmp_path, *, session_id: str = "sess-lend") -> Session:
    return Session(
        id=session_id,
        agent_config=AgentConfig(id="agent-1", name="tester"),
        cwd=str(tmp_path),
        user_id="owner-1",
        model="managed-model",
        model_provider=ModelProvider(
            api_key=_MARKER,
            base_url="https://gateway.test",
            api_protocol="anthropic",
        ),
        mcp_servers=(
            McpHttpServerConfig(
                name="docs",
                url="https://host.test/_internal/mcp/docs/mcp",
                headers={_HEADER: _MARKER, "X-Session-Id": session_id},
            ),
        ),
    )


def _header_of(session: Session) -> str:
    return session.mcp_servers[0].headers[_HEADER]


class _Store:
    def __init__(self, session: Session | None) -> None:
        self._session = session
        self.saved_headers: list[str] = []

    async def load_session(self, user_id: str, session_id: str) -> Session | None:
        return self._session

    async def save_session(self, session: Session) -> None:
        self.saved_headers.append(_header_of(session))
        self._session = session

    async def save_message(self, user_id: str, message: object) -> None:
        pass

    async def append_event(
        self, user_id: str, session_id: str, message_id: str, event: Event, **kw: object
    ) -> int:
        return 1


class _Runtime:
    """Records the MCP header as seen from inside each RuntimePort method."""

    def __init__(self, session: Session) -> None:
        self.built_with_api_key = session.model_provider.api_key if session.model_provider else None
        self.seen: dict[str, str] = {}
        self.has_live_background_tasks = False

    @property
    def approval_rule_matcher(self) -> object:
        return object()

    def update_sink(self, sink: object) -> None:
        pass

    def set_session_rule_finder(self, finder: object) -> None:
        pass

    async def run(self, session: Session, user_message: UserMessage) -> None:
        self.seen["run"] = _header_of(session)
        # A lifecycle write, to prove lending did not hand over a copy.
        session.status = "idle"
        session.runtime_session_id = "native-1"

    async def prepare(self, session: Session) -> None:
        self.seen["prepare"] = _header_of(session)
        session.runtime_session_id = "native-prepared"

    async def fork_session(self, session: Session, **_: object) -> str:
        self.seen["fork_session"] = _header_of(session)
        return "th-forked"

    async def interrupt(self) -> None:  # pragma: no cover
        pass

    async def close(self) -> None:
        pass


def _wire(monkeypatch) -> list[_Runtime]:
    runtimes: list[_Runtime] = []

    def create_runtime(*args, **kwargs) -> _Runtime:  # noqa: ANN002, ANN003
        runtime = _Runtime(args[1])
        runtimes.append(runtime)
        return runtime

    monkeypatch.setattr("src.runtimes.factory.create_runtime", create_runtime)
    return runtimes


async def test_run_sees_materialized_mcp_headers(tmp_path, monkeypatch) -> None:
    runtimes = _wire(monkeypatch)
    session = _session(tmp_path)
    store = _Store(session)

    await SessionOrchestrator(store).run_turn(  # type: ignore[arg-type]
        "owner-1", session.id, UserMessage(text="hi"), runtime_context=_CONTEXT
    )

    assert runtimes[0].seen["run"] == "resolved-secret"


async def test_prepare_sees_materialized_mcp_headers(tmp_path, monkeypatch) -> None:
    runtimes = _wire(monkeypatch)
    session = _session(tmp_path)

    await SessionOrchestrator(_Store(session)).prepare_runtime(  # type: ignore[arg-type]
        "owner-1", session.id, runtime_context=_CONTEXT
    )

    assert runtimes[0].seen["prepare"] == "resolved-secret"


async def test_fork_sees_materialized_mcp_headers(tmp_path, monkeypatch) -> None:
    runtimes = _wire(monkeypatch)
    session = _session(tmp_path)

    await SessionOrchestrator(_Store(None)).fork_session(  # type: ignore[arg-type]
        "owner-1", session, source_native_session_id="th-src", runtime_context=_CONTEXT
    )

    assert runtimes[0].seen["fork_session"] == "resolved-secret"


async def test_the_credential_is_lent_never_persisted(tmp_path, monkeypatch) -> None:
    """Every ``save_session`` during the turn must write the marker back.

    Lending materialized values onto the live session object is what keeps the
    runtime's lifecycle writes visible; the price is that a save landing while
    they are lent would write a real credential into a row that is explicitly
    not supposed to hold one.
    """
    _wire(monkeypatch)
    session = _session(tmp_path)
    store = _Store(session)

    await SessionOrchestrator(store).run_turn(  # type: ignore[arg-type]
        "owner-1", session.id, UserMessage(text="hi"), runtime_context=_CONTEXT
    )

    assert store.saved_headers, "the turn should have persisted the session"
    assert set(store.saved_headers) == {_MARKER}
    assert _header_of(session) == _MARKER
    assert session.model_provider is not None
    assert session.model_provider.api_key == _MARKER


async def test_lending_keeps_the_runtimes_writes(tmp_path, monkeypatch) -> None:
    """The runtime is handed the SAME object, not a materialized copy — its
    ``status`` / ``runtime_session_id`` writes have to survive."""
    _wire(monkeypatch)
    session = _session(tmp_path)

    await SessionOrchestrator(_Store(session)).run_turn(  # type: ignore[arg-type]
        "owner-1", session.id, UserMessage(text="hi"), runtime_context=_CONTEXT
    )

    assert session.runtime_session_id == "native-1"
    assert session.status == "idle"


async def test_a_session_without_markers_is_untouched(tmp_path, monkeypatch) -> None:
    runtimes = _wire(monkeypatch)
    session = _session(tmp_path)
    session.model_provider = ModelProvider(
        api_key="plain-key", base_url="https://gateway.test", api_protocol="anthropic"
    )
    session.mcp_servers = (
        McpHttpServerConfig(
            name="docs", url="https://host.test/mcp", headers={_HEADER: "plain-token"}
        ),
    )

    await SessionOrchestrator(_Store(session)).run_turn(  # type: ignore[arg-type]
        "owner-1", session.id, UserMessage(text="hi"), runtime_context=None
    )

    assert runtimes[0].seen["run"] == "plain-token"
    assert _header_of(session) == "plain-token"
