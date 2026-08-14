"""Shared Pydantic schemas for the sessions domain.

The ``SessionModelSelection`` mixin lives here so every entry point
that creates a kernel session — interactive chat, project chat, the
Skill Creator launcher, and any future programmatic creator — accepts
the same nullable ``model_id`` / ``provider_id`` shape and feeds it
through the shared ``adapters.model_resolver.resolve_model`` precedence:

    explicit model_id → channel.default_model →
    project default (forthcoming) → global fallback.

Keeping the input contract in one place stops drift like
``POST /v1/skills/create/chat/start`` quietly hard-coding the channel
default while ``POST /v1/sessions`` exposed the model knob.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# Cross-runtime reasoning-budget lever (kernel ``ModelSettings.effort``).
# Mirrors ``src.core.types.EffortLevel``. ``xhigh`` is supported on Claude
# and Codex SDK; DeepAgents gemini clamps it to ``high``.
EffortLevel = Literal["low", "medium", "high", "xhigh", "max"]


class SessionModelSelection(BaseModel):
    """Optional model + channel + runtime hints for any session creation request.

    Omitted fields fall through to ``adapters.model_resolver`` defaults.
    Nullable strings — ``None`` and an empty string are treated identically
    (resolve falls through). Explicit values are forwarded verbatim; the
    resolver is responsible for honouring them or surfacing a
    ``ProviderNotResolvable`` error.

    ``runtime_id`` lets the user pin the Runtime Agent that drives the
    session — ``claude_agent`` / ``codex`` / ``deepagents`` /
    ``deepseek_harness``. Unknown
    values bubble up from ``provider_resolver`` as 422. Legacy clients
    that omit it fall through to the provider's ``provider_kind``-derived
    default.

    ``effort`` is the cross-runtime reasoning-budget lever (kernel
    ``ModelSettings.effort``). Applies on next Send via the runtime's
    live-reconcile path. ``None`` lets the runtime pick its SDK default.
    """

    model_id: str | None = None
    provider_id: str | None = None
    runtime_id: str | None = None
    effort: EffortLevel | None = None


class SessionWorktreeSpec(BaseModel):
    """Opt-in worktree isolation for a session-creating request.

    Presence of the object (even empty) means "run this session in an
    isolated git worktree of the project repo"; ``name`` optionally reuses /
    labels the worktree (auto-generated when omitted). Requires the project
    cwd to be inside a git repository — otherwise the create 422s
    (``WorktreeNotAvailable``); there is deliberately no silent fallback.
    See docs/design/project-worktree-design.md.
    """

    name: str | None = None


class SessionEffortRequest(BaseModel):
    """Body for ``PATCH /v1/sessions/{id}/effort``.

    The runtime picks up the new effort on the next Send:
      * Claude: cold-reloads the SDK client (effort is a build-time option).
      * Codex: drops it into ``turn_kwargs.reasoning_effort`` for the
        next turn — survives ``--resume``.
      * DeepAgents: drops ``self._graph`` so the next turn rebuilds the
        langchain chat client with the new ``reasoning_effort``.

    ``None`` resets to the SDK default. The validator enforces the 5
    allowed values; anything else 422s before the service is called.
    """

    effort: EffortLevel | None = None
