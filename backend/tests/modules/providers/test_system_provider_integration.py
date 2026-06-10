"""Service-layer integration for overlay-contributed system providers (ADR-007).

Covers: list merge, get fallback, write-op guards, projection fidelity.
The provider table is exercised through the real datastore on an
in-memory SQLite engine; the registry is exercised directly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.secret_store import SecretStorePort
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.errors import ProviderNotFound
from valuz_agent.modules.providers.models import Base, ProviderRow
from valuz_agent.modules.providers.service import ProviderService
from valuz_agent.ports.llm_provider import (
    SystemLLMProvider,
    SystemProviderImmutable,
    _InMemoryRegistry,
    get_llm_registry,
    set_llm_registry,
)


class _InMemorySecretStore(SecretStorePort):
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def put(self, key: str, value: str) -> None:
        self._values[key] = value

    def delete(self, key: str) -> None:
        self._values.pop(key, None)


class _SvcHandle:
    """A ProviderService bound to an async session, plus a sync sessionmaker.

    The host is now fully async: ``ProviderDatastore`` takes an
    ``AsyncSession`` and every service method is ``async``. We build BOTH a
    sync engine (for the test seed helper) and an aiosqlite async engine over
    the SAME sqlite file. The service drives the async session; tests seed
    rows synchronously via ``sync_factory``.
    """

    def __init__(
        self, service: ProviderService, sync_factory: sessionmaker, secrets: _InMemorySecretStore
    ) -> None:
        self.service = service
        self._sync_factory = sync_factory
        self.secrets = secrets

    def seed(self, row: ProviderRow) -> None:
        db = self._sync_factory()
        try:
            db.add(row)
            db.commit()
        finally:
            db.close()


@pytest.fixture
async def svc(tmp_path) -> AsyncIterator[_SvcHandle]:
    db_file = tmp_path / "providers.db"
    sync_engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(sync_engine, tables=[ProviderRow.__table__])
    sync_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    secrets = _InMemorySecretStore()
    async_session = async_factory()
    ds = ProviderDatastore(async_session)
    service = ProviderService(ds, secrets, EventBus())
    try:
        yield _SvcHandle(service, sync_factory, secrets)
    finally:
        await async_session.close()
        await async_engine.dispose()
        sync_engine.dispose()


@pytest.fixture(autouse=True)
def fresh_registry() -> None:
    set_llm_registry(_InMemoryRegistry())
    yield
    set_llm_registry(_InMemoryRegistry())


def _descriptor(
    *,
    provider_id: str = "valuz-channel",
    enabled: bool = True,
    unavailable_reason: str | None = None,
) -> SystemLLMProvider:
    return SystemLLMProvider(
        id=provider_id,
        name="Valuz 系统模型",
        provider_kind="system",
        runtime_provider="claude_agent",
        api_protocol="anthropic",
        api_base="https://cloud.test/v1",
        model_options=("claude-sonnet-4-6",),
        default_model="claude-sonnet-4-6",
        headers=lambda: {"Authorization": "Bearer jwt-xyz"},
        enabled=lambda: enabled,
        unavailable_reason=lambda: unavailable_reason,
    )


def _seed_user_row(svc: _SvcHandle) -> ProviderRow:
    row = ProviderRow(
        id="user-1",
        name="My OpenAI",
        provider_kind="openai",
        source="user",
        enabled=True,
        is_default=False,
        deletable=True,
        default_model="gpt-4o",
        test_status="never",
        credential_source="none",
        auth_type="api_key",
        base_url="https://api.openai.com/v1",
    )
    svc.seed(row)
    return row


class TestListMerge:
    async def test_empty_registry_returns_user_rows_only(self, svc: _SvcHandle) -> None:
        _seed_user_row(svc)
        items = await svc.service.list_providers()
        assert [i.id for i in items] == ["user-1"]

    async def test_registry_prepended_before_user_rows(self, svc: _SvcHandle) -> None:
        # System (registry) providers are prepended to the top of the picker —
        # "platform-provided, no setup needed" belongs first (see
        # ``list_providers``: ``return system_items + user_items``).
        from valuz_agent.ports.llm_provider import get_llm_registry

        _seed_user_row(svc)
        get_llm_registry().register(_descriptor())
        items = await svc.service.list_providers()
        assert [i.id for i in items] == ["valuz-channel", "user-1"]
        sys_item = items[0]
        assert sys_item.source == "system"
        assert sys_item.deletable is False
        assert sys_item.credential_source == "system_managed"
        assert sys_item.auth_type == "oauth"
        assert sys_item.enabled is True
        assert sys_item.compatible_protocols == ["anthropic"]
        assert sys_item.model_options == ["claude-sonnet-4-6"]

    async def test_descriptor_disabled_reflected_in_enabled_flag(self, svc: _SvcHandle) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(enabled=False, unavailable_reason="未登录"))
        items = await svc.service.list_providers()
        assert len(items) == 1
        assert items[0].enabled is False


class TestGetProvider:
    async def test_get_resolves_registry_id(self, svc: _SvcHandle) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor())
        detail = await svc.service.get_provider("valuz-channel")
        assert detail.id == "valuz-channel"
        assert detail.source == "system"
        assert detail.base_url == "https://cloud.test/v1"
        assert detail.supports_connection_test is False
        assert detail.supports_custom_base_url is False

    async def test_get_unknown_raises_not_found(self, svc: _SvcHandle) -> None:
        with pytest.raises(ProviderNotFound):
            await svc.service.get_provider("nope")

    async def test_registry_id_takes_precedence_over_user_row(self, svc: _SvcHandle) -> None:
        # Even if a user row somehow shared the id, registry wins —
        # writes to that id are blocked, so this is the safer default.
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor(provider_id="user-1"))
        _seed_user_row(svc)  # also id="user-1"
        detail = await svc.service.get_provider("user-1")
        assert detail.source == "system"


class TestWriteGuards:
    def setup_method(self) -> None:
        from valuz_agent.ports.llm_provider import get_llm_registry

        get_llm_registry().register(_descriptor())

    async def test_update_rejects_system_id(self, svc: _SvcHandle) -> None:
        with pytest.raises(SystemProviderImmutable):
            await svc.service.update_provider("valuz-channel", name="renamed")

    async def test_delete_rejects_system_id(self, svc: _SvcHandle) -> None:
        with pytest.raises(SystemProviderImmutable):
            await svc.service.delete_provider("valuz-channel")

    async def test_test_provider_rejects_system_id(self, svc: _SvcHandle) -> None:
        with pytest.raises(SystemProviderImmutable):
            await svc.service.test_provider("valuz-channel")

    async def test_discover_models_rejects_system_id(self, svc: _SvcHandle) -> None:
        with pytest.raises(SystemProviderImmutable):
            await svc.service.discover_models("valuz-channel")

    async def test_set_default_rejects_system_id(self, svc: _SvcHandle) -> None:
        with pytest.raises(SystemProviderImmutable):
            await svc.service.set_default("valuz-channel")


class TestDynamicModelList:
    """ADR-007 Phase 2: descriptors with a ``list_models`` callable surface a
    dynamic catalog (used by the commercial org-model card)."""

    @staticmethod
    def _org_descriptor(list_models, *, model_options=()):  # type: ignore[no-untyped-def]
        return SystemLLMProvider(
            id="valuz-org",
            name="组织模型",
            provider_kind="system",
            runtime_provider="claude_agent",
            api_protocol="anthropic",
            api_base="https://cloud.test/v1",
            model_options=model_options,
            default_model=None,
            headers=lambda: {},
            enabled=lambda: True,
            unavailable_reason=lambda: None,
            list_models=list_models,
        )

    async def test_sync_list_models_overrides_static(self, svc: _SvcHandle) -> None:
        get_llm_registry().register(self._org_descriptor(lambda: ["org-gpt-4o", "org-claude"]))
        items = await svc.service.list_providers()
        org = next(i for i in items if i.id == "valuz-org")
        assert org.model_options == ["org-gpt-4o", "org-claude"]

    async def test_async_list_models_is_awaited(self, svc: _SvcHandle) -> None:
        async def _alist() -> list[str]:
            return ["m-async"]

        get_llm_registry().register(self._org_descriptor(_alist))
        items = await svc.service.list_providers()
        assert next(i for i in items if i.id == "valuz-org").model_options == ["m-async"]

    async def test_failure_falls_back_to_static(self, svc: _SvcHandle) -> None:
        def _boom() -> list[str]:
            raise RuntimeError("upstream down")

        get_llm_registry().register(self._org_descriptor(_boom, model_options=("fallback-model",)))
        items = await svc.service.list_providers()
        assert next(i for i in items if i.id == "valuz-org").model_options == ["fallback-model"]

    async def test_empty_list_models_hides_card(self, svc: _SvcHandle) -> None:
        # A descriptor whose dynamic list resolves empty must NOT appear — no
        # noise card for an org with no model of that protocol.
        get_llm_registry().register(self._org_descriptor(lambda: []))
        items = await svc.service.list_providers()
        assert all(i.id != "valuz-org" for i in items)


class TestUserProviderHiding:
    """When the policy reports the org locked, the caller's own (source=user)
    providers are filtered from the list — the '禁止使用' half of the lock."""

    class _LockedPolicy:
        async def authorize_write(self, ctx):  # type: ignore[no-untyped-def]
            from valuz_agent.ports.provider_policy import PolicyDecision

            return PolicyDecision(allowed=False, reason="locked")

        async def hide_user_providers(self) -> bool:
            return True

        async def hidden_provider_ids(self, candidates):  # type: ignore[no-untyped-def]
            # Part of ProviderPolicyPort (the richer by-id filter) — this
            # policy only exercises the coarse hide-user half, so it hides
            # nothing by id.
            return set()

    async def test_locked_hides_user_rows(self, svc: _SvcHandle) -> None:
        from valuz_agent.ports.provider_policy import (
            AllowAllProviderPolicy,
            set_provider_policy,
        )

        _seed_user_row(svc)  # id="user-1", source="user"
        get_llm_registry().register(_descriptor())  # a system card stays visible
        set_provider_policy(self._LockedPolicy())
        try:
            items = await svc.service.list_providers()
            ids = [i.id for i in items]
            assert "user-1" not in ids  # personal provider hidden
            assert "valuz-channel" in ids  # system card unaffected
            assert all(i.source != "user" for i in items)
        finally:
            set_provider_policy(AllowAllProviderPolicy())

    async def test_unlocked_shows_user_rows(self, svc: _SvcHandle) -> None:
        # Default AllowAll policy → user rows visible.
        _seed_user_row(svc)
        items = await svc.service.list_providers()
        assert "user-1" in [i.id for i in items]
