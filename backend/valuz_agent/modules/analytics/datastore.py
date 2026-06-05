"""Datastore for usage analytics.

Owns the read query that rolls up token/turn usage from the kernel's
``messages`` + ``sessions`` tables. The service keeps the pure-Python
aggregation; this layer holds the SQL so the service never issues a raw
``select`` (API→Service→Datastore).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# TD-007 (known boundary debt): the host should not import kernel
# ``src.adapters.*`` internals — but token billing needs the kernel's
# ``messages`` + ``sessions`` tables and there's no host-side mirror. Tracked in
# docs/exec-plans/tech-debt-tracker.md; the eventual fix routes this through a
# kernel public usage API (or a host read-model) via ``adapters/kernel_store``.
from kernel.src.adapters.sqlalchemy_store.models import MessageModel, SessionModel


class AnalyticsDatastore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def monthly_usage_rows(self, start_ms: int, end_ms: int) -> list[Any]:
        """Per-(day, model) usage rollup for completed messages in a half-open
        ``[start_ms, end_ms)`` window (Unix epoch milliseconds).

        Each row exposes ``day``, ``model``, and summed ``request_count`` /
        ``input_tokens`` / ``output_tokens`` / ``cache_read_tokens`` /
        ``cache_write_tokens``.
        """
        # MessageModel.started_at is epoch ms (BIGINT): /1000 → seconds for
        # SQLite's 'unixepoch' modifier so the UTC day bucket is correct.
        day_col = func.strftime("%Y-%m-%d", MessageModel.started_at / 1000, "unixepoch").label(
            "day"
        )
        model_col = SessionModel.model.label("model")

        stmt = (
            select(
                day_col,
                model_col,
                func.coalesce(func.sum(MessageModel.total_turns), 0).label("request_count"),
                func.coalesce(func.sum(MessageModel.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(MessageModel.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(MessageModel.cache_read_tokens), 0).label(
                    "cache_read_tokens"
                ),
                func.coalesce(func.sum(MessageModel.cache_write_tokens), 0).label(
                    "cache_write_tokens"
                ),
            )
            .join(SessionModel, MessageModel.session_id == SessionModel.id)
            .where(
                MessageModel.started_at >= start_ms,
                MessageModel.started_at < end_ms,
                MessageModel.status == "completed",
            )
            .group_by(day_col, model_col)
            .order_by(day_col)
        )

        return list((await self._db.execute(stmt)).all())
