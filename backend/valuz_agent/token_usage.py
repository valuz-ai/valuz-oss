"""Cross-surface Token usage read model.

Message rows persist one normalized usage record per turn. Conversation and
Task surfaces both consume that same durable source through ``DataReader`` so
the numbers stay runtime- and deployment-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from valuz_agent.adapters.data_reader import data_reader


@dataclass(frozen=True)
class TokenUsageBuckets:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
        )

    def __add__(self, other: TokenUsageBuckets) -> TokenUsageBuckets:
        return TokenUsageBuckets(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


def _field(message: Any, name: str) -> int:
    raw = message.get(name) if isinstance(message, dict) else getattr(message, name, None)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


async def read_session_token_usage(
    user_id: str,
    session_id: str,
    *,
    page_size: int = 200,
) -> TokenUsageBuckets:
    """Sum all persisted turn usage for one owned session."""
    reader = data_reader()
    total = TokenUsageBuckets()
    offset = 0
    while True:
        messages = await reader.list_messages(
            user_id,
            session_id,
            limit=page_size,
            offset=offset,
        )
        for message in messages:
            total += TokenUsageBuckets(
                input_tokens=_field(message, "input_tokens"),
                output_tokens=_field(message, "output_tokens"),
                cache_read_tokens=_field(message, "cache_read_tokens"),
                cache_write_tokens=_field(message, "cache_write_tokens"),
            )
        if len(messages) < page_size:
            return total
        offset += len(messages)
