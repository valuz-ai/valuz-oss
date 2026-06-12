"""Tests for RuntimeContext multi-tenant fields (Slice 5)."""

from __future__ import annotations

from valuz_agent.modules.skills.contracts import RuntimeContext


class TestRuntimeContextMultiTenant:
    def test_org_id_field_exists(self) -> None:
        ctx = RuntimeContext()
        assert ctx.org_id is None
        assert ctx.user_id == ""

    def test_org_id_can_be_set(self) -> None:
        ctx = RuntimeContext(user_id="u1", org_id="org-42")
        assert ctx.org_id == "org-42"
