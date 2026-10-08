"""Memory P2 #6: management API route handler tests."""

# ruff: noqa: I001
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import valuz_agent.boot.kernel  # noqa: F401
import valuz_agent.api.routes.memory as m
from valuz_agent.modules.memory import memory_store


class _UOW:
    async def __aenter__(self):  # noqa: ANN204
        return object()

    async def __aexit__(self, *_a):  # noqa: ANN002, ANN204
        return False


@pytest.fixture
def patched(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setattr(memory_store._fs, "data_dir", lambda user_id: tmp_path / "app")
    monkeypatch.setattr(m, "async_unit_of_work", lambda *_a, **_k: _UOW())

    async def _owned(_user_id, _project_id):  # noqa: ANN001, ANN202
        return None

    # The legacy handler fixture supplies no project database. Ownership is
    # exercised separately at the HTTP boundary with the real verifier.
    monkeypatch.setattr(m, "_require_owned_project", _owned)
    state = {"enabled": True, "auto_extract": True, "custom_instructions": ""}

    async def _ge(_db, _user_id: str | None = None, *_args, **_kwargs):  # noqa: ANN001, ANN202
        return state["enabled"]

    async def _ga(_db, _user_id: str | None = None, *_args, **_kwargs):  # noqa: ANN001, ANN202
        return state["auto_extract"]

    async def _gc(_db, _user_id: str | None = None, *_args, **_kwargs):  # noqa: ANN001, ANN202
        return state["custom_instructions"]

    async def _se(_db, v, _user_id: str | None = None, *_args, **_kwargs):  # noqa: ANN001, ANN202
        state["enabled"] = v

    async def _sa(_db, v, _user_id: str | None = None, *_args, **_kwargs):  # noqa: ANN001, ANN202
        state["auto_extract"] = v

    async def _sc(_db, v, _user_id: str | None = None, *_args, **_kwargs):  # noqa: ANN001, ANN202
        state["custom_instructions"] = v.strip()[:1500]

    monkeypatch.setattr(m, "get_memory_enabled", _ge)
    monkeypatch.setattr(m, "get_memory_auto_extract", _ga)
    monkeypatch.setattr(m, "get_memory_custom_instructions", _gc)
    monkeypatch.setattr(m, "set_memory_enabled", _se)
    monkeypatch.setattr(m, "set_memory_auto_extract", _sa)
    monkeypatch.setattr(m, "set_memory_custom_instructions", _sc)
    return state


def test_get_memory_view(patched):
    memory_store.add("local-test-owner", "user", "be terse")
    memory_store.add("local-test-owner", "global", "prefers pnpm")
    memory_store.add("local-test-owner", "project", "tracks ACME", project_id="p1")
    view = asyncio.run(m.get_memory(user_id="local-test-owner", project_id="p1"))
    assert view.enabled and view.auto_extract
    assert view.entries["user"] == ["be terse"]
    assert view.entries["global"] == ["prefers pnpm"]
    assert view.entries["project"] == ["tracks ACME"]
    # no project_id -> no project key in the view
    assert "project" not in asyncio.run(m.get_memory(user_id="local-test-owner", )).entries


def test_patch_settings(patched):
    out = asyncio.run(m.patch_memory_settings(m.MemorySettingsPatch(enabled=False)))
    assert out.enabled is False and out.auto_extract is True
    assert patched["enabled"] is False


def test_delete_entry(patched):
    memory_store.add("local-test-owner", "global", "alpha one")
    memory_store.add("local-test-owner", "global", "beta two")
    view = asyncio.run(
        m.delete_memory_entry(
            m.MemoryEntryDelete(target="global", old_text="alpha"),
            user_id="local-test-owner",
        )
    )
    assert view.entries["global"] == ["beta two"]


def test_delete_entry_404(patched):
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            m.delete_memory_entry(
                m.MemoryEntryDelete(target="global", old_text="nope"),
                user_id="local-test-owner",
            )
        )
    assert ei.value.status_code == 404


def test_clear_scope(patched):
    memory_store.add("local-test-owner", "global", "x")
    memory_store.add("local-test-owner", "global", "y")
    view = asyncio.run(
        m.clear_memory_scope(m.MemoryClear(target="global"), user_id="local-test-owner")
    )
    assert view.entries["global"] == []


def test_patch_and_view_custom_instructions(patched):
    out = asyncio.run(
        m.patch_memory_settings(
            m.MemorySettingsPatch(custom_instructions="  Remember key conclusions.  ")
        )
    )
    # Stub setter trims; toggles untouched when not sent.
    assert out.custom_instructions == "Remember key conclusions."
    assert out.enabled is True and out.auto_extract is True
    # And it surfaces in the full view.
    view = asyncio.run(m.get_memory(user_id="local-test-owner"))
    assert view.custom_instructions == "Remember key conclusions."


def test_set_custom_instructions_trims_and_caps(monkeypatch):
    """The real setter strips whitespace and hard-caps to the max length so the
    review prompt stays bounded."""
    import valuz_agent.modules.settings.preferences as prefs

    captured: dict[str, str] = {}

    async def _capture_write(_db, key, value, user_id: str | None = None):  # noqa: ANN001, ANN202
        captured[key] = value

    monkeypatch.setattr(prefs, "_write", _capture_write)
    asyncio.run(prefs.set_memory_custom_instructions(object(), "  " + "x" * 5000 + "  "))
    written = captured[prefs.KEY_MEMORY_CUSTOM_INSTRUCTIONS]
    assert len(written) == prefs.MEMORY_CUSTOM_INSTRUCTIONS_MAX_CHARS
    assert written == "x" * prefs.MEMORY_CUSTOM_INSTRUCTIONS_MAX_CHARS  # trimmed, no spaces
