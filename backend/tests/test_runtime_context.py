"""Tests for RuntimeContext multi-tenant fields (Slice 5)."""

from __future__ import annotations

from valuz_agent.api.deps import build_runtime_context
from valuz_agent.modules.skills.contracts import RuntimeContext
from valuz_agent.ports.identity import ANONYMOUS, UserIdentity


class TestRuntimeContextMultiTenant:
    def test_org_id_field_exists(self) -> None:
        ctx = RuntimeContext()
        assert ctx.org_id is None
        assert ctx.user_id == "local-user"

    def test_org_id_can_be_set(self) -> None:
        ctx = RuntimeContext(user_id="u1", org_id="org-42")
        assert ctx.org_id == "org-42"

    def test_build_from_anonymous(self) -> None:
        ctx = build_runtime_context(ANONYMOUS)
        assert ctx.user_id == "local-user"
        assert ctx.org_id is None

    def test_build_from_commercial_user(self) -> None:
        user = UserIdentity(user_id="u1", org_id="org-1", email="a@b.com")
        ctx = build_runtime_context(user)
        assert ctx.user_id == "u1"
        assert ctx.org_id == "org-1"

    def test_build_from_none_defaults_to_anonymous(self) -> None:
        ctx = build_runtime_context(None)
        assert ctx.user_id == "local-user"
        assert ctx.org_id is None
