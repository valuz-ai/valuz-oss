"""Missing caller identity never falls through to an unscoped session read."""

from __future__ import annotations

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from valuz_agent.modules.sessions.service import SessionService


async def test_missing_session_owner_fails_before_data_read(monkeypatch):
    from valuz_agent.modules.sessions import service as module

    reader = AsyncMock()
    monkeypatch.setattr(module, "data_reader", lambda: SimpleNamespace(get_session=reader))
    service = SessionService.__new__(SessionService)
    # The suite's compatibility wrapper normally injects its test owner.
    # Exercise the actual public boundary with an explicitly absent identity.
    with pytest.raises(ValueError, match="user_id is required"):
        await unwrap(SessionService.get_session)(service, "private", user_id=None)
    reader.assert_not_awaited()
