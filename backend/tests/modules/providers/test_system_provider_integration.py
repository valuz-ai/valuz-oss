"""Service-layer integration for overlay-contributed channels (ADR-011).

Covers: list merge (catalog rows prepended), get fallback to the catalog,
write-op guards by ``deletable``, empty-model rows dropped, user-row hiding.
The provider table is exercised through the real datastore on an in-memory
SQLite engine; contributed rows come from a fake ``LLMProvider`` bound to
``ext.llm_provider``.
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
from valuz_agent.modules.providers.schemas import LLMChannel, LLMModel
from valuz_agent.modules.providers.service import ProviderService
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.llm_provider import (
    NoopLLMProvider,
    ResolvedCredential,
    SystemProviderImmutable,
)


class _InMemorySecretStore(SecretStorePort):
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def get(self, user_id: str, key: str) -> str | None:
        return self._values.get((user_id, key))

    def put(self, user_id: str, key: str, value: str) -> None:
        self._values[(user_id, key)] = value

    def delete(self, user_id: str, key: str) -> None:
        self._values.pop((user_id, key), None)


class _FakeCatalog:
    """A ``LLMProvider`` returning fixed rows + resolving fixed creds."""

    def __init__(
        self,
        rows: list[LLMChannel],
        creds: dict[str, ResolvedCredential] | None = None,
    ) -> None:
        self._rows = rows
        self._creds = creds or {}

    async def list(self) -> list[LLMChannel]:
        return list(self._rows)

    async def resolve(self, provider_id: str) -> ResolvedCredential | None:
        return self._creds.get(provider_id)


class _SvcHandle:
    """A ProviderService bound to an async session, plus a sync sessionmaker."""

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
def fresh_catalog() -> None:
    ext.llm_provider = NoopLLMProvider()
    yield
    ext.llm_provider = NoopLLMProvider()


@pytest.fixture(autouse=True)
def _no_subscription_templates(monkeypatch) -> None:
    """These tests assert exact catalog/user list membership. Keep the virtual
    CLI-subscription templates (added by ``list_providers``) out so the
    assertions stay focused on the catalog-merge behaviour under test."""
    from valuz_agent.infra.config import settings

    monkeypatch.setattr(settings, "subscription_login_enabled", False)


def _set_catalog(*rows: LLMChannel, creds: dict[str, ResolvedCredential] | None = None) -> None:
    ext.llm_provider = _FakeCatalog(list(rows), creds)


def _sys_row(
    *,
    provider_id: str = "valuz-channel",
    enabled: bool = True,
    unavailable_reason: str | None = None,
    models: list[LLMModel] | None = None,
) -> LLMChannel:
    return LLMChannel(
        id=provider_id,
        name="Valuz 系统模型",
        provider_kind="system",
        source="system",
        deletable=False,
        is_default=False,
        credential_source="system_managed",
        auth_type="oauth",
        enabled=enabled,
        unavailable_reason=unavailable_reason if not enabled else None,
        compatible_protocols=["anthropic"],
        group="system",
        group_rank=20,
        default_model="claude-sonnet-4-6",
        models=models if models is not None else [LLMModel(id="claude-sonnet-4-6")],
    )


def _seed_user_row(svc: _SvcHandle) -> ProviderRow:
    row = ProviderRow(
        user_id="local-test-owner",
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
    async def test_empty_catalog_returns_user_rows_only(self, svc: _SvcHandle) -> None:
        _seed_user_row(svc)
        items = await svc.service.list_providers("local-test-owner")
        assert [i.id for i in items] == ["user-1"]

    async def test_catalog_prepended_before_user_rows(self, svc: _SvcHandle) -> None:
        # Contributed rows are prepended to the top of the picker —
        # "platform-provided, no setup needed" belongs first
        # (``list_providers``: ``extra_items + user_items``).
        _seed_user_row(svc)
        _set_catalog(_sys_row())
        items = await svc.service.list_providers("local-test-owner")
        assert [i.id for i in items] == ["valuz-channel", "user-1"]
        sys_item = items[0]
        assert sys_item.source == "system"
        assert sys_item.deletable is False
        assert sys_item.credential_source == "system_managed"
        assert sys_item.auth_type == "oauth"
        assert sys_item.enabled is True
        assert sys_item.compatible_protocols == ["anthropic"]
        assert [m.id for m in sys_item.models] == ["claude-sonnet-4-6"]

    async def test_disabled_row_reflected_in_enabled_flag(self, svc: _SvcHandle) -> None:
        _set_catalog(_sys_row(enabled=False, unavailable_reason="未登录"))
        items = await svc.service.list_providers("local-test-owner")
        assert len(items) == 1
        assert items[0].enabled is False


class TestGetProvider:
    async def test_get_resolves_catalog_id(self, svc: _SvcHandle) -> None:
        _set_catalog(_sys_row())
        detail = await svc.service.get_provider("local-test-owner", "valuz-channel")
        assert detail.id == "valuz-channel"
        assert detail.source == "system"
        # Contributed rows carry no editable base_url — creds live on resolve().
        assert detail.base_url is None
        assert detail.supports_connection_test is False
        assert detail.supports_custom_base_url is False

    async def test_get_unknown_raises_not_found(self, svc: _SvcHandle) -> None:
        with pytest.raises(ProviderNotFound):
            await svc.service.get_provider("local-test-owner", "nope")

    async def test_user_row_takes_precedence_over_catalog_id(self, svc: _SvcHandle) -> None:
        # ADR-011: catalog ids don't collide with user UUIDs, so get_provider
        # checks the user table first. If a row somehow shares the id, the user
        # row wins (it's the editable, owned one).
        _set_catalog(_sys_row(provider_id="user-1"))
        _seed_user_row(svc)  # also id="user-1"
        detail = await svc.service.get_provider("local-test-owner", "user-1")
        assert detail.source == "user"


class TestWriteGuards:
    """Writes targeting a non-deletable contributed row → 409 (ADR-011 治理)."""

    async def test_update_rejects_system_id(self, svc: _SvcHandle) -> None:
        _set_catalog(_sys_row())
        with pytest.raises(SystemProviderImmutable):
            await svc.service.update_provider("local-test-owner", "valuz-channel", name="renamed")

    async def test_delete_rejects_system_id(self, svc: _SvcHandle) -> None:
        _set_catalog(_sys_row())
        with pytest.raises(SystemProviderImmutable):
            await svc.service.delete_provider("local-test-owner", "valuz-channel")

    async def test_test_provider_rejects_system_id(self, svc: _SvcHandle) -> None:
        _set_catalog(_sys_row())
        with pytest.raises(SystemProviderImmutable):
            await svc.service.test_provider("local-test-owner", "valuz-channel")

    async def test_discover_models_rejects_system_id(self, svc: _SvcHandle) -> None:
        _set_catalog(_sys_row())
        with pytest.raises(SystemProviderImmutable):
            await svc.service.discover_models("local-test-owner", "valuz-channel")

    async def test_set_default_rejects_system_id(self, svc: _SvcHandle) -> None:
        _set_catalog(_sys_row())
        with pytest.raises(SystemProviderImmutable):
            await svc.service.set_default("local-test-owner", "valuz-channel")


class TestCatalogRowVisibility:
    """OSS drops contributed rows with no selectable models — a card with
    nothing to pick is noise (e.g. the 组织模型 card when the org has no model
    of that protocol). The dynamic catalog fetch itself is the overlay's job."""

    async def test_row_with_models_appears(self, svc: _SvcHandle) -> None:
        _set_catalog(
            _sys_row(
                provider_id="valuz-org",
                models=[LLMModel(id="org-gpt-4o"), LLMModel(id="org-claude")],
            )
        )
        items = await svc.service.list_providers("local-test-owner")
        org = next(i for i in items if i.id == "valuz-org")
        assert [m.id for m in org.models] == ["org-gpt-4o", "org-claude"]

    async def test_empty_models_row_hidden(self, svc: _SvcHandle) -> None:
        _set_catalog(_sys_row(provider_id="valuz-org", models=[]))
        items = await svc.service.list_providers("local-test-owner")
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
            return set()

    async def test_locked_hides_user_rows(self, svc: _SvcHandle) -> None:
        from valuz_agent.ports.provider_policy import (
            AllowAllProviderPolicy,
            set_provider_policy,
        )

        _seed_user_row(svc)  # id="user-1", source="user"
        _set_catalog(_sys_row())  # a system card stays visible
        set_provider_policy(self._LockedPolicy())
        try:
            items = await svc.service.list_providers("local-test-owner")
            ids = [i.id for i in items]
            assert "user-1" not in ids  # personal provider hidden
            assert "valuz-channel" in ids  # system card unaffected
            assert all(i.source != "user" for i in items)
        finally:
            set_provider_policy(AllowAllProviderPolicy())

    async def test_unlocked_shows_user_rows(self, svc: _SvcHandle) -> None:
        _seed_user_row(svc)
        items = await svc.service.list_providers("local-test-owner")
        assert "user-1" in [i.id for i in items]
