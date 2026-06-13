"""Datastore for usage analytics.

Sources the per-(day, model) usage rollup from the kernel's public usage
API through the ``KernelClient`` seam (``GET /api/v1/usage``); the service
keeps the pure-Python aggregation. This retired TD-007 — the host no
longer imports kernel storage models or queries kernel tables directly.
"""

from __future__ import annotations

from valuz_agent.adapters import kernel_client
from valuz_agent.infra.auth_context import require_current_user_id


class AnalyticsDatastore:
    async def monthly_usage_rows(self, start_ms: int, end_ms: int) -> list:
        """Per-(day, model) usage rollup for completed messages in a half-open
        ``[start_ms, end_ms)`` window (Unix epoch milliseconds).

        Each row exposes ``day``, ``model``, and summed ``request_count`` /
        ``input_tokens`` / ``output_tokens`` / ``cache_read_tokens`` /
        ``cache_write_tokens``.
        """
        return await kernel_client.usage_rollup(require_current_user_id(), start_ms, end_ms)
