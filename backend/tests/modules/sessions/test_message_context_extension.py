"""Per-turn message context provider port (ports/message_context).

Covers the extension contract: OSS registers no provider by default; a
registered provider's section rides ``additional_context`` with the
``host_ref`` resolved from the request; a failing provider is skipped and
never blocks the turn.
"""

from collections.abc import Iterator

import pytest

import valuz_agent.boot.kernel  # noqa: F401  (kernel bootstrap side effect)
from valuz_agent.modules.sessions.context_builder import _build_additional_context
from valuz_agent.ports.extensions import Extensions, ext
from valuz_agent.ports.message_context import HostRef


@pytest.fixture
def restore_providers() -> Iterator[None]:
    saved = list(ext.message_context_providers)
    try:
        yield
    finally:
        ext.message_context_providers = saved


class _RecordingProvider:
    def __init__(self, section: str = "workbench section") -> None:
        self.section = section
        self.calls: list[dict[str, object]] = []

    async def build(
        self,
        *,
        user_id: str,
        session_id: str,
        project_id: str,
        host_ref: HostRef | None,
    ) -> str:
        self.calls.append(
            {
                "user_id": user_id,
                "session_id": session_id,
                "project_id": project_id,
                "host_ref": host_ref,
            }
        )
        return self.section


class _ExplodingProvider:
    async def build(self, **_: object) -> str:
        raise RuntimeError("boom")


def test_oss_default_registers_no_provider() -> None:
    assert Extensions().message_context_providers == []


@pytest.mark.asyncio
async def test_provider_section_rides_additional_context(restore_providers: None) -> None:
    provider = _RecordingProvider("host section body")
    ext.message_context_providers = [provider]
    host_ref = HostRef(host_type="finance.research-desk", host_id="desk:u1", slot="main")

    context = await _build_additional_context(
        "session-1",
        "project-1",
        attachment_rows=[],
        user_id="user-1",
        host_ref=host_ref,
    )

    assert "host section body" in context
    assert provider.calls and provider.calls[0]["host_ref"] == host_ref
    assert provider.calls[0]["user_id"] == "user-1"
    assert provider.calls[0]["session_id"] == "session-1"
    assert provider.calls[0]["project_id"] == "project-1"


@pytest.mark.asyncio
async def test_provider_receives_none_without_host_ref(restore_providers: None) -> None:
    provider = _RecordingProvider()
    ext.message_context_providers = [provider]

    await _build_additional_context(
        "session-1",
        "project-1",
        attachment_rows=[],
        user_id="user-1",
    )

    assert provider.calls and provider.calls[0]["host_ref"] is None


@pytest.mark.asyncio
async def test_failing_provider_is_skipped(restore_providers: None) -> None:
    surviving = _RecordingProvider("still here")
    ext.message_context_providers = [_ExplodingProvider(), surviving]

    context = await _build_additional_context(
        "session-1",
        "project-1",
        attachment_rows=[],
        user_id="user-1",
        host_ref=HostRef(host_type="finance.company-research", host_id="company:NVDA"),
    )

    assert "still here" in context


@pytest.mark.asyncio
async def test_empty_section_is_omitted(restore_providers: None) -> None:
    ext.message_context_providers = [_RecordingProvider("")]

    context = await _build_additional_context(
        "session-1",
        "project-1",
        attachment_rows=[],
        user_id="user-1",
    )

    assert "workbench" not in context


@pytest.mark.asyncio
async def test_a_kb_binding_is_announced_not_fatal() -> None:
    """The KB scope must reach the announcement when the project has bindings.

    The datastore went owner-scoped and ``_format_kb_scope`` kept calling it
    one-argument, so the first ``kb``-kind binding raised TypeError — which
    took the WHOLE additional-context block down (the caller swallows). The
    docs skill tells the model to consult the announcement before guessing, so
    an empty one reads as "no knowledge base": binding a KB is exactly what
    switched its retrieval off, and the agent web-searched a question whose
    answer sat in a bound library.

    Driven through ``_format_kb_scope`` with a fake owner-scoped datastore —
    the fake's signatures ARE the assertion that every call carries the owner.
    """
    from types import SimpleNamespace

    from valuz_agent.modules.sessions.context_builder import _format_kb_scope

    class _OwnerScopedDs:
        async def get_kb(self, user_id: str, kb_id: str):
            assert user_id == "user-1"
            return SimpleNamespace(id=kb_id, name="test_cloud")

        async def get_folder(self, user_id: str, folder_id: str):  # pragma: no cover
            raise AssertionError("no folder bindings in this test")

        async def get_by_id(self, user_id: str, doc_id: str):  # pragma: no cover
            raise AssertionError("no document bindings in this test")

    binding = SimpleNamespace(binding_kind="kb", target_id="kb-1")

    section = await _format_kb_scope(_OwnerScopedDs(), [binding], "user-1")

    assert "test_cloud" in section
    assert "doc_search" in section
