from __future__ import annotations

from contextlib import suppress

import pytest

from valuz_agent.api import deps


@pytest.mark.asyncio
async def test_skill_service_dependency_finishes_inner_unit_of_work_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request cleanup must let the skill service's transaction commit.

    Closing the delegated async generator with ``aclose()`` injects
    ``GeneratorExit`` at its yield point.  A real ``async_unit_of_work`` then
    skips its success path, so filesystem-backed skill writes return 201 while
    the skill index and lifecycle-hook outbox rows are never committed.
    """

    exits: list[str] = []
    service = object()

    async def fake_skill_service_for_user(user_id: str):  # type: ignore[no-untyped-def]
        assert user_id == "user-1"
        try:
            yield service
        except BaseException as exc:
            exits.append(type(exc).__name__)
            raise
        else:
            exits.append("normal")

    monkeypatch.setattr(deps, "get_skill_service_for_user", fake_skill_service_for_user)

    dependency = deps.get_skill_service("user-1")
    assert await dependency.__anext__() is service
    with suppress(StopAsyncIteration):
        await dependency.__anext__()

    assert exits == ["normal"]
