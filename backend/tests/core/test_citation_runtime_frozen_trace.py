"""Frozen Runtime-event replay for post-run Citation sidecars.

The trace is deliberately provider-neutral: Runtime adapters emit this kernel
event vocabulary, and the observer must preserve it before adding Citation,
Claim Audit, and Task Coverage sidecars. No Agent or Connector runs here.
"""

from __future__ import annotations

import json
from typing import Any

from src.core.events import Event
from src.core.orchestrator import _MessageObserverSink


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _source(
    handle: str,
    *,
    source_id: str,
    source_type: str,
    title: str,
    evidence: dict[str, Any],
    locator: dict[str, Any] | None = None,
) -> Event:
    envelope: dict[str, Any] = {
        "evidenceHandle": handle,
        "source": {
            "sourceId": source_id,
            "providerId": "frozen-trace",
            "sourceType": source_type,
            "title": title,
            "retrievedAt": "2026-08-05T00:00:00Z",
        },
        "evidence": {
            **evidence,
            "capturedAt": "2026-08-05T00:00:00Z",
        },
    }
    if locator is not None:
        envelope["locator"] = locator
    return Event(
        type="citation_evidence",
        data={
            "content": json.dumps({"_valuz_evidence": envelope}),
            "tool_name": "frozen_source_tool",
        },
    )


_FROZEN_PRIMARY_TRACE = (
    _source(
        "ev_doc_revenue_2026",
        source_id="doc-annual-2026",
        source_type="document",
        title="Annual report 2026",
        evidence={
            "kind": "text",
            "quote": "Revenue was USD 100 and cost was USD 20 in FY2026.",
            "snippet": "Revenue was USD 100 and cost was USD 20 in FY2026.",
        },
        locator={"kind": "chunk", "chunkId": "chunk-financial-summary", "page": 8},
    ),
    _source(
        "ev_data_revenue_2026",
        source_id="dataset-income-2026",
        source_type="dataset",
        title="Income statement",
        evidence={
            "kind": "structured-data",
            "datasetId": "income-statement",
            "toolName": "income_statement",
            "recordKey": "ACME|FY2026",
            "field": "revenue",
            "value": 100,
            "unit": "USD",
            "period": "FY2026",
        },
    ),
    _source(
        "ev_data_cost_2026",
        source_id="dataset-income-2026",
        source_type="dataset",
        title="Income statement",
        evidence={
            "kind": "structured-data",
            "datasetId": "income-statement",
            "toolName": "income_statement",
            "recordKey": "ACME|FY2026",
            "field": "cost",
            "value": 20,
            "unit": "USD",
            "period": "FY2026",
        },
    ),
    _source(
        "ev_calc_gross_profit_2026",
        source_id="calculation-gross-profit-2026",
        source_type="tool-result",
        title="Gross profit calculation",
        evidence={
            "kind": "calculation",
            "toolName": "runtime.calculation",
            "expression": "revenue - cost",
            "result": 80,
            "unit": "USD",
            "rounding": "0dp",
            "calculatedAt": "2026-08-05T00:00:00Z",
            "inputs": [
                {
                    "name": "revenue",
                    "value": 100,
                    "citationId": "ev_data_revenue_2026",
                },
                {
                    "name": "cost",
                    "value": 20,
                    "citationId": "ev_data_cost_2026",
                },
            ],
        },
    ),
    Event(
        type="assistant_message",
        data={
            "text": (
                "财报原文显示 FY2026 收入为 100 美元 "
                "[source](evidence://ev_doc_revenue_2026)。"
            )
        },
    ),
    Event(type="tool_use", data={"id": "calc-1", "name": "citation_calculate", "input": {}}),
    Event(type="tool_result", data={"id": "calc-1", "content": "80 USD", "is_error": False}),
    Event(
        type="assistant_message",
        data={
            "text": (
                "更正上一条的口径说明：结构化收入为 100 美元 "
                "[source](evidence://ev_data_revenue_2026)，成本为 20 美元 "
                "[source](evidence://ev_data_cost_2026)，因此毛利为 80 美元 "
                "[source](evidence://ev_calc_gross_profit_2026)。"
            )
        },
    ),
    Event(type="session_idle", data={"stop_reason": {"type": "end_turn"}, "num_turns": 1}),
)


async def test_frozen_trace_preserves_messages_and_aggregates_turn_sources() -> None:
    sink = _RecordingSink()
    observer = _MessageObserverSink(
        sink,
        message_id="frozen-message-1",
        user_prompt="根据财报说明收入、成本与毛利，并核对计算。",
        citation_policy_available=True,
        citation_quality_policy={"mode": "required-on-evidence", "config": {}},
        citation_enabled=True,
        citation_verification_enabled=True,
        task_coverage_enabled=True,
    )

    for event in _FROZEN_PRIMARY_TRACE:
        await observer.emit(event)

    await observer.begin_task_coverage_continuation()
    coverage_text = (
        "补充：同一财报原文也明确列示收入和成本 "
        "[source](evidence://ev_doc_revenue_2026)。"
    )
    await observer.emit(Event(type="assistant_message", data={"text": coverage_text}))
    await observer.emit(
        Event(
            type="session_idle",
            data={"stop_reason": {"type": "end_turn"}, "num_turns": 1},
        )
    )
    await observer.finalize_sidecars()
    await observer.release_session_idle()

    assistant_events = [event for event in sink.events if event.type == "assistant_message"]
    sidecar_events = [
        event for event in sink.events if event.type == "assistant_message_sidecar"
    ]
    assert [event.data["text"] for event in assistant_events] == [
        _FROZEN_PRIMARY_TRACE[4].data["text"],
        _FROZEN_PRIMARY_TRACE[7].data["text"],
        coverage_text,
    ]
    assert sink.events.index(assistant_events[-1]) < sink.events.index(sidecar_events[0])
    assert [event.data["assistant_segment_index"] for event in sidecar_events] == [0, 1, 2]
    assert len(observer.claim_audits) == 3

    bundle = observer.citation_bundle
    assert bundle is not None
    citations = bundle["citations"]
    citation_ids = [item["citationId"] for item in citations]
    assert len(citation_ids) == len(set(citation_ids)) == 4
    projection = bundle["projection"]["evidenceHandleToCitationId"]
    assert projection["ev_doc_revenue_2026"] in citation_ids
    assert sum(
        projection["ev_doc_revenue_2026"]
        in event.data.get("citation_bundle", {}).get("projection", {}).get(
            "evidenceHandleToCitationId", {}
        ).values()
        for event in sidecar_events
    ) == 2

    calculation = next(
        item for item in citations if item["evidence"].get("kind") == "calculation"
    )
    assert calculation["source"].get("canonicalUrl") is None
    assert observer.task_coverage == {
        "status": "complete",
        "supplemented": True,
        "assistant_segment_indices": [2],
    }
