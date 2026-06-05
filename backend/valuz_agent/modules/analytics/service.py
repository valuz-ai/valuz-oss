from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from valuz_agent.modules.analytics.datastore import AnalyticsDatastore


class AnalyticsService:
    def __init__(self, datastore: AnalyticsDatastore) -> None:
        self._ds = datastore

    async def get_monthly_usage(self, year: int, month: int) -> dict[str, Any]:
        # Kernel MessageModel.started_at is Unix epoch ms (BIGINT) since the
        # timestamps-epoch-millis kernel bump — bound the window in epoch ms.
        end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
        start_ms = int(datetime(year, month, 1, tzinfo=UTC).timestamp() * 1000)
        end_ms = int(datetime(end_year, end_month, 1, tzinfo=UTC).timestamp() * 1000)

        rows = await self._ds.monthly_usage_rows(start_ms, end_ms)

        models: dict[str, dict[str, Any]] = {}
        daily_overview: dict[str, dict[str, int]] = {}

        for row in rows:
            day = row.day
            model = row.model
            request_count = int(row.request_count)
            input_t = int(row.input_tokens)
            output_t = int(row.output_tokens)
            cache_read_t = int(row.cache_read_tokens)
            cache_write_t = int(row.cache_write_tokens)
            total_tokens = input_t + output_t + cache_read_t + cache_write_t

            if model not in models:
                models[model] = {
                    "model": model,
                    "total_requests": 0,
                    "total_tokens": 0,
                    "total_input_tokens": 0,
                    "total_output_tokens": 0,
                    "total_cache_read_tokens": 0,
                    "total_cache_write_tokens": 0,
                    "daily": [],
                }
            m = models[model]
            m["total_requests"] += request_count
            m["total_tokens"] += total_tokens
            m["total_input_tokens"] += input_t
            m["total_output_tokens"] += output_t
            m["total_cache_read_tokens"] += cache_read_t
            m["total_cache_write_tokens"] += cache_write_t
            m["daily"].append(
                {
                    "date": day,
                    "request_count": request_count,
                    "input_tokens": input_t,
                    "output_tokens": output_t,
                    "cache_read_tokens": cache_read_t,
                    "cache_write_tokens": cache_write_t,
                    "total_tokens": total_tokens,
                }
            )

            if day not in daily_overview:
                daily_overview[day] = {}
            daily_overview[day][model] = total_tokens

        overview_list = [
            {"date": day, **tokens_by_model}
            for day, tokens_by_model in sorted(daily_overview.items())
        ]

        grand_total_tokens = sum(m["total_tokens"] for m in models.values())
        grand_total_requests = sum(m["total_requests"] for m in models.values())

        return {
            "year": year,
            "month": month,
            "total_tokens": grand_total_tokens,
            "total_requests": grand_total_requests,
            "models": list(models.keys()),
            "overview": overview_list,
            "by_model": list(models.values()),
        }
