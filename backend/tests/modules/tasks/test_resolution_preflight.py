"""Roster provider pre-flight + the shared ``_provider_gap`` check.

Kickoff/commit sweep every project member through the same provider
resolution a dispatch would use, so an unconfigured member surfaces as an
immediate task-creation error instead of minutes into the run as a
dispatch-time ``subtask_failed``. The gap check exempts OAuth
subscriptions — pinned, or reached via the chat-parity model-hosted
fallback — whose ``model_provider=None`` is the healthy resolver output
(credentials live in the CLI keychain).
"""

# ruff: noqa: I001
from __future__ import annotations

from types import SimpleNamespace

import valuz_agent.boot.kernel  # noqa: F401 — sys.path side-effect for src.*/app.*
import pytest

from valuz_agent.modules.tasks import resolution as res_mod
from valuz_agent.modules.tasks.resolution import _provider_gap, task_session_resolver

U = "local-test-owner"


class _FakeProviderDs:
    """get_by_id keyed off a {provider_id: row} mapping."""

    rows: dict[str, object] = {}

    def __init__(self, _db: object) -> None:
        pass

    async def get_by_id(self, _uid: str, provider_id: str) -> object | None:
        return type(self).rows.get(provider_id)


def _patch_provider_lookups(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: dict[str, object],
    model_match: object | None,
) -> None:
    """Route ``_provider_gap``'s lazy provider imports at fakes."""
    _FakeProviderDs.rows = rows
    monkeypatch.setattr(
        "valuz_agent.modules.providers.datastore.ProviderDatastore", _FakeProviderDs
    )

    class _FakeSvc:
        def __init__(self, *, datastore: object, event_bus: object) -> None:
            pass

        async def resolve_provider_for_model(self, _uid: str, _model: str) -> object | None:
            return model_match

    monkeypatch.setattr("valuz_agent.modules.providers.service.ProviderService", _FakeSvc)


def _row(auth_type: str) -> object:
    return SimpleNamespace(auth_type=auth_type)


# -- _provider_gap ----------------------------------------------------------


async def test_strict_gap_without_db_names_slug_and_model() -> None:
    gap = await _provider_gap(
        None,
        agent_slug="写手",
        pinned_provider_id=None,
        runtime="claude_agent",
        model="m-1",
        user_id=U,
    )
    assert gap is not None
    assert "写手" in gap
    assert "model provider" in gap
    assert "m-1" in gap


async def test_oauth_pin_is_not_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provider_lookups(monkeypatch, rows={"p1": _row("oauth")}, model_match=None)
    gap = await _provider_gap(
        object(),
        agent_slug="writer",
        pinned_provider_id="p1",
        runtime="claude_agent",
        model="m-1",
        user_id=U,
    )
    assert gap is None


async def test_oauth_model_hosted_fallback_is_not_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No pin — but the chat-parity fallback lands on an OAuth subscription,
    # so model_provider=None is the healthy resolver output.
    _patch_provider_lookups(monkeypatch, rows={}, model_match=_row("oauth"))
    gap = await _provider_gap(
        object(),
        agent_slug="writer",
        pinned_provider_id=None,
        runtime="claude_agent",
        model="m-1",
        user_id=U,
    )
    assert gap is None


async def test_non_oauth_fallback_row_is_still_a_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A model-hosted API-key row combined with model_provider=None means the
    # upstream resolution failed — that must stay a gap.
    _patch_provider_lookups(monkeypatch, rows={}, model_match=_row("api_key"))
    gap = await _provider_gap(
        object(),
        agent_slug="writer",
        pinned_provider_id=None,
        runtime="claude_agent",
        model="m-1",
        user_id=U,
    )
    assert gap is not None


async def test_lookup_failure_falls_back_to_strict_gap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Boom:
        def __init__(self, _db: object) -> None:
            raise RuntimeError("no such table")

    monkeypatch.setattr("valuz_agent.modules.providers.datastore.ProviderDatastore", _Boom)
    gap = await _provider_gap(
        object(),
        agent_slug="writer",
        pinned_provider_id="p1",
        runtime="claude_agent",
        model="m-1",
        user_id=U,
    )
    assert gap is not None


# -- preflight_member_providers ---------------------------------------------


async def test_preflight_flags_only_unresolvable_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        SimpleNamespace(agent_slug="worker-ok"),
        SimpleNamespace(agent_slug="worker-bad"),
        SimpleNamespace(agent_slug="orphan"),
    ]

    class _FakeMemberDs:
        def __init__(self, _db: object) -> None:
            pass

        async def list_by_project(self, _uid: str, _wid: str) -> list[object]:
            return rows

    agents = {
        "worker-ok": SimpleNamespace(model="m-1", metadata={}, runtime_provider="claude_agent"),
        "worker-bad": SimpleNamespace(model="m-2", metadata={}, runtime_provider="codex"),
        "orphan": None,  # unresolvable library agent — dispatch has its own error
    }

    async def _agent_config(row: object, _ds: object, *, user_id: str) -> object | None:
        return agents[row.agent_slug]

    async def _resolve(*, agent: object, model: str, providers: object, user_id: str):
        return object() if model == "m-1" else None

    async def _gap(_db: object, *, agent_slug: str, **_k: object) -> str:
        return f"GAP: {agent_slug}"

    monkeypatch.setattr(res_mod, "ProjectMemberDatastore", _FakeMemberDs)
    monkeypatch.setattr(res_mod, "_member_agent_config", _agent_config)
    monkeypatch.setattr(res_mod, "_resolve_agent_provider", _resolve)
    monkeypatch.setattr(res_mod, "_provider_resolver_deps", lambda _db: {})
    monkeypatch.setattr(res_mod, "_provider_gap", _gap)

    gaps = await task_session_resolver.preflight_member_providers(
        object(), user_id=U, project_id="w1"
    )
    assert gaps == ["GAP: worker-bad"]


async def test_preflight_empty_roster_is_all_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeMemberDs:
        def __init__(self, _db: object) -> None:
            pass

        async def list_by_project(self, _uid: str, _wid: str) -> list[object]:
            return []

    monkeypatch.setattr(res_mod, "ProjectMemberDatastore", _FakeMemberDs)
    monkeypatch.setattr(res_mod, "_provider_resolver_deps", lambda _db: {})
    assert (
        await task_session_resolver.preflight_member_providers(object(), user_id=U, project_id="w1")
        == []
    )
