"""Common channel adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from valuz_agent.modules.channels.schemas import ChannelMentionContext


class ChannelVerificationError(ValueError):
    """Raised when a platform callback fails token/signature verification."""


@dataclass(frozen=True, slots=True)
class InboundChannelMessage:
    """Normalized inbound message emitted by a platform adapter."""

    text: str
    context: ChannelMentionContext
    params: dict[str, Any] = field(default_factory=dict)
    channel_context: dict[str, Any] = field(default_factory=dict)


__all__ = ["ChannelVerificationError", "InboundChannelMessage"]
