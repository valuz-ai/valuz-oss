import pytest

from valuz_agent.ports.automation_create_policy import (
    CardOnlyAutomationCreatePolicy,
    UnconfirmedCreateContext,
)
from valuz_agent.ports.extensions import ext


def _ctx(**kw):
    base = dict(
        user_id="u1",
        session_id="s1",
        project_kind="chat",
        project_id="ws-1",
        execution_kind="code",
        action_kind="chat",
    )
    base.update(kw)
    return UnconfirmedCreateContext(**base)


@pytest.mark.asyncio
async def test_oss_default_never_skips_the_card() -> None:
    policy = CardOnlyAutomationCreatePolicy()
    reason = await policy.unconfirmed_create_refusal(_ctx())
    assert reason and "not enabled" in reason
    # whatever the caller claims about the automation, OSS answers the same
    assert await policy.unconfirmed_create_refusal(_ctx(execution_kind="agent")) == reason


def test_the_default_binding_is_card_only() -> None:
    assert isinstance(ext.automation_create_policy, CardOnlyAutomationCreatePolicy)
