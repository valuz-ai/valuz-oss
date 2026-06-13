"""Live recommended-catalog semantics for OAuth subscription rows.

Subscription rows keep ``model_ids IS NULL`` so their picker list and the
session-time model→channel binding both track ``subscription_models.json``
via the descriptor, instead of a seed-time snapshot (the stale-picker bug
class migration 0002 cleans up). These tests pin the resolution path end
to end:

- NULL row resolves models from the live descriptor list;
- NULL row does NOT resolve ids outside that list;
- explicit-empty (``"[]"``, e.g. managed credential anchors) stays empty —
  no descriptor fallback;
- a customised (non-NULL) list still wins over the descriptor.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from valuz_agent.infra.eventbus import EventBus
from valuz_agent.infra.secret_store import SecretStorePort
from valuz_agent.modules.providers.datastore import ProviderDatastore
from valuz_agent.modules.providers.models import Base, ProviderRow
from valuz_agent.modules.providers.service import ProviderService, get_provider


class _NoopSecretStore(SecretStorePort):
    def get(self, key: str) -> str | None:
        return None

    def put(self, key: str, value: str) -> None:  # pragma: no cover - unused
        pass

    def delete(self, key: str) -> None:  # pragma: no cover - unused
        pass


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
    Base.metadata.create_all(sync_engine, tables=[ProviderRow.__table__])
    sync_factory = sessionmaker(bind=sync_engine, expire_on_commit=False)

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async_factory = async_sessionmaker(bind=async_engine, expire_on_commit=False)

    async_session = async_factory()
    ds = ProviderDatastore(async_session)
    service = ProviderService(ds, _NoopSecretStore(), EventBus())
    try:
        yield _SvcHandle(service, sync_factory)
    finally:
        await async_session.close()
        await async_engine.dispose()
        sync_engine.dispose()


def _subscription_row(*, model_ids: str | None) -> ProviderRow:
    return ProviderRow(
        id="ch-claude-subscription",
        name="Claude Pro / Max",
        provider_kind="claude-subscription",
        source="builtin",
        enabled=True,
        is_default=False,
        deletable=False,
        default_model="claude-sonnet-4-6",
        test_status="never",
        credential_source="none",
        auth_type="oauth",
        model_ids=model_ids,
    )


async def test_null_row_resolves_models_from_live_descriptor(svc: _SvcHandle) -> None:
    """The picker shows the descriptor list for NULL rows — the session-time
    binding must agree, or a recommended model would be selectable yet
    unresolvable (the exact gap a stale snapshot caused for fable-5)."""
    svc.seed(_subscription_row(model_ids=None))

    recommended = list(get_provider("claude-subscription").model_options)
    assert recommended, "descriptor list must be hydrated from subscription_models.json"

    row = await svc.service.resolve_provider_for_model("local-test-owner", recommended[0])
    assert row is not None
    assert row.id == "ch-claude-subscription"


async def test_null_row_does_not_resolve_unknown_model(svc: _SvcHandle) -> None:
    svc.seed(_subscription_row(model_ids=None))
    assert await svc.service.resolve_provider_for_model("local-test-owner", "not-a-real-model") is None


async def test_explicit_empty_list_does_not_fall_back(svc: _SvcHandle) -> None:
    """``model_ids = "[]"`` is the explicit-empty state (managed
    credential-only anchors) — it must NOT inherit the descriptor list."""
    svc.seed(_subscription_row(model_ids="[]"))

    recommended = list(get_provider("claude-subscription").model_options)
    assert await svc.service.resolve_provider_for_model("local-test-owner", recommended[0]) is None


async def test_customised_list_wins_over_descriptor(svc: _SvcHandle) -> None:
    """A stored (non-NULL) list is user/customised state and stays
    authoritative: ids only in the descriptor no longer resolve."""
    svc.seed(_subscription_row(model_ids=json.dumps(["my-pinned-model"])))

    row = await svc.service.resolve_provider_for_model("local-test-owner", "my-pinned-model")
    assert row is not None and row.id == "ch-claude-subscription"

    recommended = list(get_provider("claude-subscription").model_options)
    only_in_descriptor = [m for m in recommended if m != "my-pinned-model"]
    assert only_in_descriptor
    assert await svc.service.resolve_provider_for_model("local-test-owner", only_in_descriptor[0]) is None
