"""Owner-aware product instructions resolved at session creation.

Each distribution supplies one complete prompt. Overlays replace the OSS
provider; prompts are never appended through an inheritance chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

OSS_GLOBAL_INSTRUCTIONS = """\
You are the user's Valuz assistant. Help them understand, plan, and complete
work in their current workspace. Use the available tools, connectors, skills,
and knowledge only when they are relevant and authorized for this session.
Keep claims accurate, preserve the user's existing work, and make consequential
actions clear before taking them."""
OSS_GLOBAL_INSTRUCTIONS_REVISION = "oss-2026-07-29.1"


@dataclass(frozen=True)
class PromptSnapshot:
    """One immutable distribution prompt selected for an explicit owner."""

    content: str
    revision: str
    distribution: str
    locale: str | None = None
    version: str | None = None
    updated_at: str | None = None

    def metadata(self) -> dict[str, str]:
        values = {
            "revision": self.revision,
            "distribution": self.distribution,
            "locale": self.locale,
            "version": self.version,
            "updated_at": self.updated_at,
        }
        return {key: value for key, value in values.items() if value is not None}


class GlobalInstructionsPort(Protocol):
    async def resolve(self, user_id: str) -> PromptSnapshot:
        """Resolve this distribution's complete prompt for ``user_id``."""
        ...


class OSSGlobalInstructionsProvider:
    """Complete prompt for the standalone OSS distribution."""

    async def resolve(self, user_id: str) -> PromptSnapshot:
        if not user_id:
            raise ValueError("user_id is required")
        return PromptSnapshot(
            content=OSS_GLOBAL_INSTRUCTIONS,
            revision=OSS_GLOBAL_INSTRUCTIONS_REVISION,
            distribution="oss",
        )


class GlobalInstructionsConfigurationError(RuntimeError):
    pass


async def resolve_global_instructions(user_id: str) -> PromptSnapshot:
    """Resolve and validate the exact prompt snapshot for one owner."""
    if not user_id:
        raise ValueError("user_id is required")

    from valuz_agent.ports.extensions import ext

    snapshot = await ext.global_instructions.resolve(user_id)
    missing = [
        field
        for field in ("content", "revision", "distribution")
        if not str(getattr(snapshot, field, "") or "").strip()
    ]
    if missing:
        raise GlobalInstructionsConfigurationError(
            f"global instructions snapshot missing: {', '.join(missing)}"
        )
    return snapshot


async def global_instructions_preamble(user_id: str) -> str:
    """Compatibility helper returning only the resolved prompt text."""
    return (await resolve_global_instructions(user_id)).content


def agent_inherits_global_instructions(
    *,
    kind: object,
    inherit_global_instructions: object,
) -> bool:
    """System Agents always inherit; standard Agents follow their flag."""
    return kind == "system" or bool(inherit_global_instructions)


# Older overlay imports can keep the type name while implementing ``resolve``.
InstructionsPort = GlobalInstructionsPort

__all__ = [
    "GlobalInstructionsConfigurationError",
    "GlobalInstructionsPort",
    "InstructionsPort",
    "OSS_GLOBAL_INSTRUCTIONS",
    "OSS_GLOBAL_INSTRUCTIONS_REVISION",
    "OSSGlobalInstructionsProvider",
    "PromptSnapshot",
    "agent_inherits_global_instructions",
    "global_instructions_preamble",
    "resolve_global_instructions",
]
