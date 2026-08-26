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
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.discover import DiscoveredModel, ModelDiscoveryError
from valuz_agent.modules.providers.errors import ProviderNotFound
from valuz_agent.modules.providers.models import Base, ProviderRow
from valuz_agent.modules.providers.service import (
    ProviderService,
    materialize_logged_in_subscription,
    subscription_catalog_kind,
    subscription_login_hint,
)
from valuz_agent.modules.settings.models import AppSettingRow
from valuz_agent.ports.extensions import ext
from valuz_agent.ports.llm_provider import NoopLLMProvider

OWNER = "owner-A"


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
    service = ProviderService(ProviderDatastore(async_session), EventBus())
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

    async def test_enable_normalizes_legacy_catalog_id_row(self, svc: _SvcHandle) -> None:
        # A legacy install seeded the built-in subscription as a real row under
        # the ``ch-*`` catalog id itself, already enabled + cli_keychain but
        # deletable=False. enable_provider used to early-return it untouched,
        # so the settings page kept seeing an "un-materialized template"
        # (oauth + !deletable) and re-POSTed /enable in an infinite loop.
        svc.seed(
            ProviderRow(
                user_id=OWNER,
                id="ch-claude-subscription",
                name="Claude Pro / Max",
                provider_kind="claude-subscription",
                source="builtin",
                enabled=True,
                is_default=False,
                deletable=False,
                default_model=None,
                test_status="never",
                credential_source="cli_keychain",
                auth_type="oauth",
            )
        )
        detail = await svc.service.enable_provider(OWNER, "ch-claude-subscription")
        assert detail.id == "ch-claude-subscription"  # reused, not duplicated
        assert detail.deletable is True
        assert detail.source == "user"  # delete guard requires source="user"
        # The loop terminator: the list must carry no oauth row that still
        # looks like an un-materialized template for this kind.
        items = await svc.service.list_providers(OWNER)
        claude_items = [i for i in items if i.provider_kind == "claude-subscription"]
        assert len(claude_items) == 1
        assert claude_items[0].deletable is True

    async def test_enable_normalizes_legacy_disabled_catalog_id_row(self, svc: _SvcHandle) -> None:
        # Same legacy shape but not yet enabled — takes the non-idempotent
        # branch, which must also land on the canonical CLI-backed state.
        svc.seed(
            ProviderRow(
                user_id=OWNER,
                id="ch-codex-subscription",
                name="Codex · ChatGPT",
                provider_kind="codex-subscription",
                source="builtin",
                enabled=False,
                is_default=False,
                deletable=False,
                default_model=None,
                test_status="never",
                credential_source="none",
                auth_type="oauth",
            )
        )
        detail = await svc.service.enable_provider(OWNER, "ch-codex-subscription")
        assert detail.enabled is True
        assert detail.credential_source == "cli_keychain"
        assert detail.deletable is True
        assert detail.source == "user"

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


class TestProviderEndpointFallback:
    async def test_zhipu_create_falls_back_to_coding_endpoint(
        self, svc: _SvcHandle, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        async def fake_discover_model_entries(
            *, base_url: str, api_key: str, protocol: str
        ) -> list[DiscoveredModel]:
            seen.append(base_url)
            assert api_key == "sk-zhipu"
            assert protocol == "openai"
            if base_url == "https://open.bigmodel.cn/api/paas/v4":
                raise ModelDiscoveryError("API Key 无效，请检查后重试")
            if base_url == "https://open.bigmodel.cn/api/coding/paas/v4":
                return [DiscoveredModel(id="glm-5.2")]
            raise AssertionError(f"unexpected base_url: {base_url}")

        monkeypatch.setattr(
            "valuz_agent.modules.providers.service.discover_model_entries",
            fake_discover_model_entries,
        )

        detail = await svc.service.create_provider(
            OWNER,
            name="智谱 (GLM)",
            provider_kind="zhipu",
            api_key="sk-zhipu",
        )

        assert seen == [
            "https://open.bigmodel.cn/api/paas/v4",
            "https://open.bigmodel.cn/api/coding/paas/v4",
        ]
        assert detail.base_url == "https://open.bigmodel.cn/api/coding/paas/v4"
        assert detail.default_model == "glm-5.2"

    async def test_create_persists_upstream_model_display_name(
        self, svc: _SvcHandle, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_discover_model_entries(
            *, base_url: str, api_key: str, protocol: str
        ) -> list[DiscoveredModel]:
            assert base_url == "https://api.kimi.com/coding/v1"
            assert api_key == "sk-kimi"
            assert protocol == "openai"
            return [DiscoveredModel(id="kimi-for-coding", label="K2.7 Code")]

        monkeypatch.setattr(
            "valuz_agent.modules.providers.service.discover_model_entries",
            fake_discover_model_entries,
        )

        detail = await svc.service.create_provider(
            OWNER,
            name="Moonshot (Kimi Coding)",
            provider_kind="moonshot-kimi-coding",
            api_key="sk-kimi",
        )

        assert detail.models[0].id == "kimi-for-coding"
        assert detail.models[0].label == "K2.7 Code"


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
    """A logged-out subscription channel keeps its card but drops its models — on
    the per-channel detail path (``get_provider``) and on the opt-in gated list
    (``list_providers(gated=True)``, the composer's single-request feed). The
    DEFAULT ``list_providers`` (which feeds model-options → onboarding
    ConnectStep + Settings default-model picker) is deliberately NOT gated:
    those surfaces already gate client-side on the keychain probe, and
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

    async def test_gated_list_hides_models_when_logged_out(
        self, svc: _SvcHandle, monkeypatch
    ) -> None:
        # The composer's single-request feed (``?gated=1``): a logged-out
        # subscription channel keeps its card but its models are stripped,
        # exactly like the per-channel detail path.
        self._set_logged_out(monkeypatch)
        by_id = {i.id: i for i in await svc.service.list_providers(OWNER, gated=True)}
        assert by_id["ch-claude-subscription"].models == []
        assert by_id["ch-claude-subscription"].default_model is None

    async def test_gated_list_keeps_models_when_logged_in(self, svc: _SvcHandle) -> None:
        # Default autouse fixture = logged in → the gate is a no-op.
        by_id = {i.id: i for i in await svc.service.list_providers(OWNER, gated=True)}
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


def _patch_login(monkeypatch, value: bool) -> None:
    async def _probe(_tool: str) -> bool:
        return value

    monkeypatch.setattr(
        "valuz_agent.modules.providers.service.detect_cli_login", _probe, raising=True
    )


class TestMaterializeLoggedInSubscription:
    """The session-resolution backstop: a stale virtual ``ch-*`` id is swapped
    onto a real row only when its CLI is logged in. This is the server half of
    "可用 = configurable" — the frontend auto-materializes on detection, this
    catches an already-saved reference at session-creation time."""

    async def test_materializes_real_row_when_logged_in(self, svc: _SvcHandle, monkeypatch) -> None:
        _patch_login(monkeypatch, True)
        ds = svc.service._ds  # noqa: SLF001
        row = await materialize_logged_in_subscription(ds, OWNER, "ch-codex-subscription")
        assert row is not None
        assert row.id != "ch-codex-subscription"  # fresh uuid, not the catalog id
        assert row.provider_kind == "codex-subscription"
        assert row.deletable is True
        assert row.enabled is True
        assert row.credential_source == "cli_keychain"
        assert row.auth_type == "oauth"

    async def test_returns_none_when_logged_out(self, svc: _SvcHandle, monkeypatch) -> None:
        _patch_login(monkeypatch, False)
        ds = svc.service._ds  # noqa: SLF001
        row = await materialize_logged_in_subscription(ds, OWNER, "ch-codex-subscription")
        assert row is None
        assert await ds.list_providers(OWNER) == []  # no row conjured from a logged-out CLI

    async def test_returns_none_for_non_subscription_id(self, svc: _SvcHandle, monkeypatch) -> None:
        _patch_login(monkeypatch, True)
        ds = svc.service._ds  # noqa: SLF001
        assert await materialize_logged_in_subscription(ds, OWNER, "ch-anthropic") is None
        assert await materialize_logged_in_subscription(ds, OWNER, "some-user-uuid") is None

    async def test_idempotent_by_kind(self, svc: _SvcHandle, monkeypatch) -> None:
        _patch_login(monkeypatch, True)
        ds = svc.service._ds  # noqa: SLF001
        a = await materialize_logged_in_subscription(ds, OWNER, "ch-codex-subscription")
        b = await materialize_logged_in_subscription(ds, OWNER, "ch-codex-subscription")
        assert a is not None and b is not None and a.id == b.id
        codex = [
            r for r in await ds.list_providers(OWNER) if r.provider_kind == "codex-subscription"
        ]
        assert len(codex) == 1

    async def test_normalizes_legacy_undeletable_row(self, svc: _SvcHandle, monkeypatch) -> None:
        # A legacy seeded row (deletable=False, not yet cli_keychain) is reused
        # by kind AND normalized — otherwise it would stay stuck without its
        # management affordance and read as un-credentialed.
        svc.seed(
            ProviderRow(
                user_id=OWNER,
                id="legacy-codex",
                name="Codex · ChatGPT",
                provider_kind="codex-subscription",
                source="user",
                enabled=False,
                is_default=False,
                deletable=False,
                default_model=None,
                test_status="never",
                credential_source="none",
                auth_type="oauth",
            )
        )
        _patch_login(monkeypatch, True)
        ds = svc.service._ds  # noqa: SLF001
        row = await materialize_logged_in_subscription(ds, OWNER, "ch-codex-subscription")
        assert row is not None and row.id == "legacy-codex"  # reused, not duplicated
        assert row.deletable is True
        assert row.enabled is True
        assert row.credential_source == "cli_keychain"


class TestSubscriptionLoginHint:
    """The friendly error a logged-out subscription raises at session creation,
    instead of the raw "provider 'ch-codex-subscription' not found"."""

    def test_catalog_kind_helper(self) -> None:
        assert subscription_catalog_kind("ch-codex-subscription") == "codex-subscription"
        assert subscription_catalog_kind("ch-claude-subscription") == "claude-subscription"
        assert subscription_catalog_kind("ch-anthropic") is None
        assert subscription_catalog_kind("some-user-uuid") is None

    def test_hint_for_subscription_id_is_actionable(self) -> None:
        hint = subscription_login_hint("ch-codex-subscription")
        assert hint is not None
        assert hint != "settings.model.subscriptionLoginRequired"  # i18n resolved, not the raw key
        assert "codex" in hint.lower()  # names the channel / its login command

    def test_no_hint_for_non_subscription_id(self) -> None:
        assert subscription_login_hint("ch-anthropic") is None
        assert subscription_login_hint("some-user-uuid") is None
