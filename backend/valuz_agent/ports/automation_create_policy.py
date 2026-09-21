"""Port: may the ``automation`` tool persist a ``create`` WITHOUT the card?

The tool's ``create`` proposes: the frontend renders a confirmation card and
nothing is written until the user approves ("tool proposes, user disposes").
That is the right default for a conversation — a scheduled automation spends
the user's credits on a cadence the model chose.

One flow legitimately needs the automation to exist the moment it is asked
for: an agent building a *site* whose page reads the automation's output. The
user already said yes to that site and its data lane; a card per automation
is a second, redundant consent that stalls the build. The caller asks for it
with ``confirmation="skip"``; whether that is honoured is this port's
decision, and OSS says **no** — the card-only policy is the default. An
edition that runs the site flow binds a policy that allows it where that
flow lives (the cloud), and its site skill is what tells the model to use it.

The policy answers with a *refusal reason* or ``None``: a refusal never fails
the tool call — the handler falls back to the card and says why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class UnconfirmedCreateContext:
    """What the policy can decide on. ``session_id`` is the calling session
    (``""`` when unknown); ``project_kind`` is ``chat`` / ``project``."""

    user_id: str
    session_id: str
    project_kind: str
    project_id: str | None
    execution_kind: str
    action_kind: str


class AutomationCreatePolicyPort(Protocol):
    async def unconfirmed_create_refusal(self, ctx: UnconfirmedCreateContext) -> str | None:
        """``None`` = persist without the card; a string = why not (the
        handler shows the card instead and relays this reason)."""
        ...


class CardOnlyAutomationCreatePolicy:
    """OSS default: every ``create`` goes through the confirmation card."""

    REASON = (
        "creating an automation without the confirmation card is not enabled on this deployment"
    )

    async def unconfirmed_create_refusal(self, ctx: UnconfirmedCreateContext) -> str | None:
        return self.REASON


__all__ = [
    "AutomationCreatePolicyPort",
    "CardOnlyAutomationCreatePolicy",
    "UnconfirmedCreateContext",
]
