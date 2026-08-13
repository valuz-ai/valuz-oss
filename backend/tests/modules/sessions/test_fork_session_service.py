"""Host half of session fork (docs/design/session-fork.md §6.5).

The service owns what the kernel deliberately doesn't: source gating (D3 —
user chats only), composing the COMPLETE ``valuz`` metadata for the new
session (D2 — the kernel inherits nothing implicitly), codex-app style
name numbering (D1), project-index registration and ``SESSION_CREATED``.
Kernel seam errors map to typed module errors so the API layer renders
409/422/502 faithfully.
"""

# ruff: noqa: I001
from __future__ import annotations

from types import SimpleNamespace

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (puts kernel on the import path)
from valuz_agent.adapters.kernel_client import (
    KernelClientError,
    KernelConflictError,
    KernelSessionNotFoundError,
)
from valuz_agent.modules.sessions import service as svc_mod
from valuz_agent.modules.sessions.errors import (
    ForkRejected,
    ForkRuntimeFailed,
    ForkUnsupported,
    SessionNotFound,
)


def _source_session(**meta_overrides):
    valuz = {
        "name": "研究",
        "origin": "user",
        "project_id": "p1",
        "last_user_message_text": "latest question",
        "locked_provider_id": "prov-1",
        **meta_overrides,
    }
    return SimpleNamespace(
        id="src-1",
        user_id="u1",
        status="idle",
        runtime_provider="codex",
        metadata={"valuz": valuz},
    )


class _Bus:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    def publish(self, event, **kwargs) -> None:
        self.published.append((event, kwargs))


def _service() -> svc_mod.SessionService:
    return svc_mod.SessionService(
        event_bus=_Bus(),  # type: ignore[arg-type]
        project_svc=None,  # type: ignore[arg-type]
        providers=None,  # type: ignore[arg-type]
        skills=None,  # type: ignore[arg-type]
        projects=None,  # type: ignore[arg-type]
    )


def _wire(
    monkeypatch,
    svc,
    source,
    *,
    sibling_names: list[str] | None = None,
    fork_error: Exception | None = None,
):
    """Stub every collaborator ``fork_session`` touches; return the trace."""
    trace = SimpleNamespace(fork_calls=[], index_records=[], get_message_ids=[])

    class _Reader:
        async def get_session(self, *_a, **_k):
            return source

    monkeypatch.setattr(svc_mod, "data_reader", lambda: _Reader())

    async def _list_sessions(project_id=None, query=None, user_id=None):
        return [SimpleNamespace(name=n) for n in (sibling_names or [])]

    monkeypatch.setattr(svc, "list_sessions", _list_sessions)

    async def _get_message(user_id, message_id):
        trace.get_message_ids.append(message_id)
        return SimpleNamespace(user_message=SimpleNamespace(text=f"prompt of {message_id}"))

    async def _fork(user_id, session_id, req):
        trace.fork_calls.append((session_id, req))
        if fork_error is not None:
            raise fork_error
        return SimpleNamespace(id="forked-1", metadata=req.metadata)

    fake_kernel = SimpleNamespace(
        get_message=_get_message,
        fork_session=_fork,
        KernelClientError=KernelClientError,
        KernelConflictError=KernelConflictError,
        KernelSessionNotFoundError=KernelSessionNotFoundError,
    )
    monkeypatch.setattr(svc_mod, "kernel_client", fake_kernel)

    async def _record(project_id, session_id, **kwargs):
        trace.index_records.append((project_id, session_id, kwargs))

    monkeypatch.setattr(svc_mod, "project_index", SimpleNamespace(record=_record))
    monkeypatch.setattr(svc_mod, "_session_to_detail", lambda s: s)
    return trace


async def test_message_fork_composes_full_metadata_and_registers(monkeypatch) -> None:
    svc = _service()
    source = _source_session()
    trace = _wire(monkeypatch, svc, source, sibling_names=["研究"])

    detail = await svc.fork_session("src-1", message_id="m2", user_id="u1")

    assert detail.id == "forked-1"
    session_id, req = trace.fork_calls[0]
    assert session_id == "src-1"
    assert req.message_id == "m2"
    valuz = req.metadata["valuz"]
    # D2: the full source valuz dict rides along; only host-owned fields
    # change. The forgotten-field failure mode (losing project_id) is the
    # thing this pins.
    assert valuz["project_id"] == "p1"
    assert valuz["locked_provider_id"] == "prov-1"
    assert valuz["name"] == "研究 (2)"
    # Message fork: the Recents preview reflects the anchor's user text.
    assert valuz["last_user_message_text"] == "prompt of m2"
    assert trace.get_message_ids == ["m2"]
    # Host-side registration + live-list event.
    assert trace.index_records == [
        ("p1", "forked-1", {"kind": "chat", "origin": "user", "user_id": "u1"})
    ]
    assert svc._bus.published == [
        (svc_mod.SESSION_CREATED, {"session_id": "forked-1", "project_id": "p1"})
    ]


async def test_session_fork_keeps_source_preview_text(monkeypatch) -> None:
    svc = _service()
    trace = _wire(monkeypatch, svc, _source_session())

    await svc.fork_session("src-1", user_id="u1")

    _sid, req = trace.fork_calls[0]
    assert req.message_id is None
    assert req.metadata["valuz"]["last_user_message_text"] == "latest question"
    assert trace.get_message_ids == []


@pytest.mark.parametrize(
    ("existing", "expected"),
    [
        (["研究"], "研究 (2)"),
        (["研究", "研究 (2)", "研究 (3)"], "研究 (4)"),
        (["研究 (2)", "unrelated"], "研究 (3)"),
        ([], "研究 (2)"),
    ],
)
async def test_fork_name_numbering(monkeypatch, existing, expected) -> None:
    svc = _service()
    trace = _wire(monkeypatch, svc, _source_session(), sibling_names=existing)

    await svc.fork_session("src-1", user_id="u1")

    assert trace.fork_calls[0][1].metadata["valuz"]["name"] == expected


async def test_forking_a_fork_stays_in_one_number_family(monkeypatch) -> None:
    svc = _service()
    source = _source_session(name="研究 (2)")
    trace = _wire(monkeypatch, svc, source, sibling_names=["研究", "研究 (2)"])

    await svc.fork_session("src-1", user_id="u1")

    assert trace.fork_calls[0][1].metadata["valuz"]["name"] == "研究 (3)"


@pytest.mark.parametrize(
    "source",
    [
        _source_session(origin="task"),
        _source_session(origin="automation"),
        SimpleNamespace(
            id="src-1",
            user_id="u1",
            status="idle",
            runtime_provider="codex",
            metadata={"valuz": {"origin": "user"}, "bare_completion": True},
        ),
    ],
)
async def test_non_user_sources_are_gated(monkeypatch, source) -> None:
    svc = _service()
    _wire(monkeypatch, svc, source)

    with pytest.raises(ForkUnsupported):
        await svc.fork_session("src-1", user_id="u1")


@pytest.mark.parametrize(
    ("kernel_error", "module_error"),
    [
        (KernelConflictError(409, "no native anchor"), ForkRejected),
        (KernelClientError(422, "runtime not wired"), ForkUnsupported),
        (KernelClientError(502, "thread/fork failed"), ForkRuntimeFailed),
        (KernelSessionNotFoundError(404, "Anchor message not found"), SessionNotFound),
    ],
)
async def test_kernel_errors_map_to_module_errors(monkeypatch, kernel_error, module_error) -> None:
    svc = _service()
    _wire(monkeypatch, svc, _source_session(), fork_error=kernel_error)

    with pytest.raises(module_error):
        await svc.fork_session("src-1", user_id="u1")


def test_mappers_surface_kernel_fork_provenance() -> None:
    from valuz_agent.modules.sessions.mappers import _session_to_detail, _session_to_list_item

    session = SimpleNamespace(
        id="forked-1",
        user_id="u1",
        status="idle",
        model="m1",
        created_at=1,
        instructions="",
        todos=None,
        model_settings=None,
        runtime_provider="codex",
        permission_mode="full_access",
        metadata={
            "valuz": {"name": "研究 (2)", "origin": "user", "project_id": "p1"},
            # Kernel-stamped, metadata TOP level — not the valuz namespace.
            "forked_from": {"session_id": "src-1", "message_id": "m2"},
        },
    )

    assert _session_to_list_item(session).forked_from_session_id == "src-1"
    assert _session_to_detail(session).forked_from_session_id == "src-1"

    session.metadata = {"valuz": {"name": "plain"}}
    assert _session_to_list_item(session).forked_from_session_id is None
