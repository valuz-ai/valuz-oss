"""Virtual built-in channels + materialize-on-login (Option C).

Built-ins are no longer seeded as rows. ``list_providers`` surfaces the OAuth
CLI-subscription channels (Claude Pro·Max, Codex) as virtual templates, and a
real row is created only when the user logs in (``enable_provider``). api_key
built-ins are NOT virtualized (discovered via the add-provider dialog and
materialized by ``create_provider``).
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
from valuz_agent.modules.settings.models import AppSettingRow
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.llm_provider import NoopLLMProvider

OWNER = "owner-A"


class _InMemorySecretStore(SecretStorePort):
    def __init__(self) -> None:
        self._values: dict[tuple[str, str], str] = {}

    def get(self, user_id: str, key: str) -> str | None:
        return self._values.get((user_id, key))

    def put(self, user_id: str, key: str, value: str) -> None:
        self._values[(user_id, key)] = value

    def delete(self, user_id: str, key: str) -> None:
        self._values.pop((user_id, key), None)


class _SvcHandle:
    def __init__(self, service: ProviderService, sync_factory: sessionmaker) -> None:
        self.service = service
        self._sync_factory = sync_factory

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
    Base.metadata.create_all(sync_engine, tables=[ProviderRow.__table__, AppSettingRow.__table__])
    sync_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)
    async_session = async_factory()
    service = ProviderService(ProviderDatastore(async_session), _InMemorySecretStore(), EventBus())
    try:
        yield _SvcHandle(service, sync_factory)
    finally:
        await async_session.close()
        await async_engine.dispose()
        sync_engine.dispose()


@pytest.fixture(autouse=True)
def fresh_catalog() -> None:
    """Keep the contributed provider catalog empty so the list only carries the
    virtual templates under test."""
    ext.llm_provider = NoopLLMProvider()
    yield
    ext.llm_provider = NoopLLMProvider()


class TestVirtualTemplates:
    async def test_subscription_kinds_appear_as_virtual_templates(self, svc: _SvcHandle) -> None:
        items = await svc.service.list_providers(OWNER)
        by_id = {i.id: i for i in items}
        assert "ch-claude-subscription" in by_id
        assert "ch-codex-subscription" in by_id
        claude = by_id["ch-claude-subscription"]
        assert claude.source == "builtin"
        assert claude.auth_type == "oauth"
        assert claude.credential_source == "none"
        assert claude.compatible_protocols == ["anthropic"]

    async def test_api_key_builtins_are_not_virtualized(self, svc: _SvcHandle) -> None:
        ids = {i.id for i in await svc.service.list_providers(OWNER)}
        assert "ch-anthropic" not in ids
        assert "ch-openai" not in ids
        assert "ch-deepseek" not in ids

    async def test_disabled_when_subscription_login_off(self, svc: _SvcHandle, monkeypatch) -> None:
        from valuz_agent.infra.config import settings

        monkeypatch.setattr(settings, "subscription_login_enabled", False)
        ids = {i.id for i in await svc.service.list_providers(OWNER)}
        assert "ch-claude-subscription" not in ids
        assert "ch-codex-subscription" not in ids

    async def test_template_suppressed_when_kind_already_configured(self, svc: _SvcHandle) -> None:
        svc.seed(
            ProviderRow(
                user_id=OWNER,
                id="my-claude",
                name="Claude Pro / Max",
                provider_kind="claude-subscription",
                source="user",
                enabled=True,
                is_default=False,
                deletable=True,
                default_model=None,
                test_status="never",
                credential_source="cli_keychain",
                auth_type="oauth",
            )
        )
        ids = [i.id for i in await svc.service.list_providers(OWNER)]
        assert "ch-claude-subscription" not in ids  # template suppressed
        assert "my-claude" in ids  # configured row shown instead
        assert "ch-codex-subscription" in ids  # the other kind still a template

    async def test_templates_isolated_per_owner(self, svc: _SvcHandle) -> None:
        # Virtual templates carry no DB row, so a second owner sees them too —
        # no global PK to collide on (the whole point of Option C).
        a = {i.id for i in await svc.service.list_providers("owner-A")}
        b = {i.id for i in await svc.service.list_providers("owner-B")}
        assert "ch-claude-subscription" in a
        assert "ch-claude-subscription" in b


class TestMaterializeOnLogin:
    async def test_enable_materializes_a_uuid_row(self, svc: _SvcHandle) -> None:
        detail = await svc.service.enable_provider(OWNER, "ch-claude-subscription")
        assert detail.id != "ch-claude-subscription"  # fresh uuid, not the catalog id
        assert detail.provider_kind == "claude-subscription"
        assert detail.source == "user"
        assert detail.auth_type == "oauth"
        assert detail.credential_source == "cli_keychain"
        assert detail.enabled is True
        # Now listed as a configured row, not a template.
        items = await svc.service.list_providers(OWNER)
        claude_items = [i for i in items if i.provider_kind == "claude-subscription"]
        assert len(claude_items) == 1
        assert claude_items[0].id == detail.id

    async def test_enable_is_idempotent_by_kind(self, svc: _SvcHandle) -> None:
        first = await svc.service.enable_provider(OWNER, "ch-claude-subscription")
        second = await svc.service.enable_provider(OWNER, "ch-claude-subscription")
        assert first.id == second.id  # reused, not duplicated
        items = await svc.service.list_providers(OWNER)
        assert sum(1 for i in items if i.provider_kind == "claude-subscription") == 1

    async def test_enable_unknown_id_raises_not_found(self, svc: _SvcHandle) -> None:
        with pytest.raises(ProviderNotFound):
            await svc.service.enable_provider(OWNER, "ch-does-not-exist")

    async def test_enable_builtin_blocked_when_subscription_login_off(
        self, svc: _SvcHandle, monkeypatch
    ) -> None:
        from valuz_agent.infra.config import settings

        monkeypatch.setattr(settings, "subscription_login_enabled", False)
        with pytest.raises(ProviderNotFound):
            await svc.service.enable_provider(OWNER, "ch-claude-subscription")


class TestSetDefaultMaterializes:
    """Selecting an unconfigured CLI-subscription channel as the default
    model must materialize it on demand instead of 404ing — the virtual
    template carries only a catalog id, with no DB row until first use."""

    async def test_set_default_materializes_subscription(self, svc: _SvcHandle) -> None:
        ds = svc.service._ds  # noqa: SLF001 — test reaches into the datastore for row state
        # No real rows yet — exactly the fresh-install repro for the 404.
        assert await ds.list_providers(OWNER) == []
        await svc.service.set_default(OWNER, "ch-claude-subscription")
        # A real row now exists (fresh uuid, not the catalog id) and is the default.
        rows = await ds.list_providers(OWNER)
        assert len(rows) == 1
        assert rows[0].provider_kind == "claude-subscription"
        assert rows[0].id != "ch-claude-subscription"
        assert rows[0].is_default is True
        assert rows[0].enabled is True
        default_row = await ds.get_default(OWNER)
        assert default_row is not None and default_row.id == rows[0].id

    async def test_set_default_with_model_materializes(self, svc: _SvcHandle) -> None:
        ds = svc.service._ds  # noqa: SLF001
        await svc.service.set_default(OWNER, "ch-codex-subscription", default_model="gpt-5-codex")
        rows = await ds.list_providers(OWNER)
        codex = [r for r in rows if r.provider_kind == "codex-subscription"]
        assert len(codex) == 1
        assert codex[0].is_default is True
        assert codex[0].default_model == "gpt-5-codex"

    async def test_set_default_unknown_id_still_404s(self, svc: _SvcHandle) -> None:
        with pytest.raises(ProviderNotFound):
            await svc.service.set_default(OWNER, "ch-does-not-exist")


class TestGetVirtualTemplate:
    """The edit dialog calls GET /v1/providers/{id}; a virtual template has no
    row, so ``get_provider`` must resolve it instead of 404ing."""

    async def test_get_resolves_virtual_template(self, svc: _SvcHandle) -> None:
        detail = await svc.service.get_provider(OWNER, "ch-claude-subscription")
        assert detail.id == "ch-claude-subscription"
        assert detail.provider_kind == "claude-subscription"
        assert detail.source == "builtin"
        assert detail.auth_type == "oauth"
        assert detail.supports_connection_test is False
        assert detail.models  # recommended subscription catalog, non-empty

    async def test_get_unknown_id_still_404s(self, svc: _SvcHandle) -> None:
        with pytest.raises(ProviderNotFound):
            await svc.service.get_provider(OWNER, "ch-nope")

    async def test_get_virtual_blocked_when_subscription_login_off(
        self, svc: _SvcHandle, monkeypatch
    ) -> None:
        from valuz_agent.infra.config import settings

        monkeypatch.setattr(settings, "subscription_login_enabled", False)
        with pytest.raises(ProviderNotFound):
            await svc.service.get_provider(OWNER, "ch-claude-subscription")

    async def test_empty_save_on_virtual_template_is_noop(self, svc: _SvcHandle) -> None:
        # The OAuth edit dialog persists nothing → an empty save must not 404.
        detail = await svc.service.update_provider(OWNER, "ch-claude-subscription")
        assert detail.id == "ch-claude-subscription"
        assert detail.provider_kind == "claude-subscription"
        # No row was created by the no-op save.
        ids = [i.id for i in await svc.service.list_providers(OWNER)]
        assert ids.count("ch-claude-subscription") == 1  # still the virtual template

    async def test_real_edit_on_virtual_template_still_404s(self, svc: _SvcHandle) -> None:
        # A patch that carries an actual change can't apply to a non-existent
        # row — configure/login the channel first.
        with pytest.raises(ProviderNotFound):
            await svc.service.update_provider(OWNER, "ch-claude-subscription", name="Renamed")


class TestSubscriptionLoginGate:
    """A logged-out subscription channel keeps its card but drops its models — but
    ONLY on the per-channel detail path (``get_provider``), which is what the chat
    composer fetches. ``list_providers`` (which feeds model-options →
    onboarding ConnectStep + Settings default-model picker) is deliberately NOT
    gated: those surfaces already gate client-side on the keychain probe, and
    stripping there would drop the channel from model-options and break the
    onboarding login card. Covers the virtual template AND a persisted
    ``cli_keychain`` row (the real-world repro — logged in once, keychain since
    cleared, composer still showing Claude·Codex models)."""

    @staticmethod
    def _set_logged_out(monkeypatch) -> None:
        async def _no(_tool: str) -> bool:
            return False

        monkeypatch.setattr(
            "valuz_agent.modules.providers.service.detect_cli_login", _no, raising=True
        )

    async def test_detail_models_hidden_when_logged_out(self, svc: _SvcHandle, monkeypatch) -> None:
        self._set_logged_out(monkeypatch)
        detail = await svc.service.get_provider(OWNER, "ch-claude-subscription")
        assert detail.models == []  # card stays, models gone
        assert detail.default_model is None

    async def test_list_not_gated_so_onboarding_keeps_models(
        self, svc: _SvcHandle, monkeypatch
    ) -> None:
        # Even logged out, the list feed must keep its models — model-options /
        # onboarding gate client-side, and an empty list drops the login card.
        self._set_logged_out(monkeypatch)
        by_id = {i.id: i for i in await svc.service.list_providers(OWNER)}
        assert by_id["ch-claude-subscription"].models

    async def test_detail_models_shown_when_logged_in(self, svc: _SvcHandle) -> None:
        # Default autouse fixture = logged in → recommended catalog surfaces.
        detail = await svc.service.get_provider(OWNER, "ch-claude-subscription")
        assert detail.models

    async def test_persisted_keychain_row_detail_hidden_when_logged_out(
        self, svc: _SvcHandle, monkeypatch
    ) -> None:
        svc.seed(
            ProviderRow(
                user_id=OWNER,
                id="my-claude",
                name="Claude Pro / Max",
                provider_kind="claude-subscription",
                source="user",
                enabled=True,
                is_default=False,
                deletable=True,
                default_model="claude-sonnet-4-6",
                test_status="never",
                credential_source="cli_keychain",
                auth_type="oauth",
                model_ids=None,  # tracks the live recommended catalog
            )
        )
        self._set_logged_out(monkeypatch)
        detail = await svc.service.get_provider(OWNER, "my-claude")
        assert detail.models == []
        assert detail.default_model is None
