"""Runtime message integrity at the Citation/Audit sidecar boundary."""

# ruff: noqa: I001 — kernel bootstrap side-effect import must precede src.*
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from typing import Any

import valuz_agent.boot.kernel  # noqa: F401

from src.core.citation import CitationGuard
from src.core.events import Event
from src.core.orchestrator import _MessageObserverSink


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, event: Event) -> None:
        self.events.append(event)


def _observer(
    *,
    citation_enabled: bool = False,
    verification_enabled: bool = False,
    task_coverage_enabled: bool = False,
    citation_quality_policy: dict[str, Any] | None = None,
) -> tuple[_RecordingSink, _MessageObserverSink]:
    sink = _RecordingSink()
    return sink, _MessageObserverSink(
        sink,
        message_id="message-1",
        user_prompt="根据已有资料回答。",
        citation_policy_available=True,
        citation_quality_policy=citation_quality_policy,
        citation_enabled=citation_enabled,
        citation_verification_enabled=verification_enabled,
        task_coverage_enabled=task_coverage_enabled,
    )


#: ``turn_phase`` is host lifecycle metadata marking the post-run verification
#: window (``_MessageObserverSink._begin_post_run_verification``) — "never
#: assistant-authored content". The assertions below are about what the RUNTIME
#: authored and whether a sidecar replaced it, so the markers are filtered out
#: instead of being baked into every expected list.
_LIFECYCLE_EVENT_TYPES = {"turn_phase"}


def _authored_types(sink: _RecordingSink) -> list[str]:
    """Event types excluding host lifecycle markers, in order."""
    return [event.type for event in sink.events if event.type not in _LIFECYCLE_EVENT_TYPES]


def _assert_only_lifecycle_extras(sink: _RecordingSink, expected: list[str]) -> None:
    """The authored stream is exactly ``expected``; any extra is a known marker."""
    assert _authored_types(sink) == expected
    extras = {event.type for event in sink.events} - set(expected)
    assert extras <= _LIFECYCLE_EVENT_TYPES, f"unexpected event types: {sorted(extras)}"


def _evidence_payload() -> str:
    return json.dumps(
        {
            "_valuz_evidence": {
                "evidenceHandle": "ev_revenue_2026_q2",
                "source": {
                    "sourceId": "doc-revenue-2026-q2",
                    "providerId": "reportify",
                    "documentId": "doc-revenue-2026-q2",
                    "sourceType": "document",
                    "title": "Quarterly results",
                    "retrievedAt": "2026-08-04T00:00:00Z",
                },
                "evidence": {
                    "kind": "text",
                    "quote": "Revenue was 100 USD in 2026 Q2.",
                    "snippet": "Revenue was 100 USD in 2026 Q2.",
                    "capturedAt": "2026-08-04T00:00:00Z",
                },
                "locator": {"kind": "chunk", "chunkId": "chunk-revenue"},
            }
        }
    )


async def _mark_external_document_tool_called(observer: _MessageObserverSink) -> None:
    await observer.emit(
        Event(
            type="tool_use",
            data={
                "id": "external-document-tool",
                "name": "mcp__valuz_docs__document_search",
                "input": {},
            },
        )
    )


async def test_interrupted_turn_persists_partial_assistant_before_idle() -> None:
    sink, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "already "}))
    await observer.emit(Event(type="text_delta", data={"text": "streamed"}))
    await observer.emit(
        Event(
            type="session_idle",
            data={
                "stop_reason": {"type": "error", "category": "user_interrupt"},
                "num_turns": 1,
            },
        )
    )

    assert [event.type for event in sink.events] == [
        "text_delta",
        "text_delta",
        "assistant_message",
        "session_idle",
    ]
    assert sink.events[-2].data == {"text": "already streamed"}
    assert observer.assistant_text == "already streamed"


async def test_canonical_assistant_does_not_duplicate_streamed_delta() -> None:
    sink, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "draft"}))
    await observer.emit(Event(type="assistant_message", data={"text": "final"}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistants = [event for event in sink.events if event.type == "assistant_message"]
    assert [event.data["text"] for event in assistants] == ["final"]
    assert observer.assistant_text == "final"


async def test_each_runtime_assistant_is_published_before_its_sidecar() -> None:
    sink, observer = _observer(citation_enabled=True)
    await observer.emit(
        Event(
            type="citation_evidence",
            data={"content": _evidence_payload(), "tool_name": "document_search"},
        )
    )
    first = "Revenue was 100 USD in 2026 Q2 [source](evidence://ev_revenue_2026_q2)."
    second = "The same source reports the quarter [source](evidence://ev_revenue_2026_q2)."

    await observer.emit(Event(type="assistant_message", data={"text": first}))
    await observer.emit(Event(type="assistant_message", data={"text": second}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    relevant = [
        event
        for event in sink.events
        if event.type in {"assistant_message", "assistant_message_sidecar"}
    ]
    assert [event.type for event in relevant] == [
        "assistant_message",
        "assistant_message",
        "assistant_message_sidecar",
        "assistant_message_sidecar",
    ]
    assert [event.data["text"] for event in relevant[:2]] == [first, second]
    assert [event.data["assistant_segment_index"] for event in relevant[2:]] == [0, 1]
    projected_ids = [
        event.data["citation_bundle"]["projection"]["evidenceHandleToCitationId"][
            "ev_revenue_2026_q2"
        ]
        for event in relevant[2:]
    ]
    assert projected_ids[0] == projected_ids[1]
    assert observer.citation_bundle is not None
    assert len(observer.citation_bundle["citations"]) == 1


async def test_claude_private_projection_uses_canonical_pointer_root() -> None:
    sink, observer = _observer(citation_enabled=True)
    quote = "Revenue was 100 USD in 2026 Q2."
    handle = "ev_revenue_2026_q2"
    canonical_projection = {
        "data": {
            "chunks": [
                {
                    "text": quote,
                    "evidenceHandle": handle,
                    "citationLink": f"[source](evidence://{handle})",
                }
            ]
        }
    }
    private_projection = json.dumps(
        {
            "_valuz_evidence": [
                {
                    "source": {
                        "sourceId": "doc-revenue-2026-q2",
                        "providerId": "valuz-data",
                        "documentId": "doc-revenue-2026-q2",
                        "sourceType": "document",
                        "title": "Quarterly results",
                        "retrievedAt": "2026-08-04T00:00:00Z",
                    },
                    "evidence": {
                        "contentHash": f"sha256:{hashlib.sha256(quote.encode()).hexdigest()}",
                        "quoteRef": "/data/chunks/0/text",
                    },
                    "locator": {"chunkId": "chunk-revenue"},
                }
            ],
            "_valuz_evidence_format": 1,
        }
    )
    visible_wrapper = json.dumps([{"type": "text", "text": json.dumps(canonical_projection)}])

    await observer.emit(
        Event(type="tool_use", data={"id": "tool-1", "name": "mcp__valuz-data__chunks"})
    )
    await observer.emit(
        Event(
            type="tool_result",
            data={
                "id": "tool-1",
                "content": visible_wrapper,
                "_citation_content": private_projection,
                "_citation_model_content": canonical_projection,
            },
        )
    )
    await observer.emit(
        Event(
            type="assistant_message",
            data={"text": f"{quote} [source](evidence://{handle})."},
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    tool_result = next(event for event in sink.events if event.type == "tool_result")
    assert tool_result.data["content"] == visible_wrapper
    assert "_citation_content" not in tool_result.data
    assert "_citation_model_content" not in tool_result.data
    sidecar = next(event for event in sink.events if event.type == "assistant_message_sidecar")
    citation = sidecar.data["citation_bundle"]["citations"][0]
    assert citation["source"]["providerId"] == "valuz-data"
    assert citation["source"]["title"] == "Quarterly results"


async def test_chunk_citation_inherits_title_and_url_from_same_document() -> None:
    sink, observer = _observer(citation_enabled=True)
    document_id = "filing-2026-q1"
    rich = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_summary_2026_q1",
            "source": {
                "sourceId": document_id,
                "providerId": "valuz-data",
                "documentId": document_id,
                "sourceType": "document",
                "sourceCategory": "document_summary",
                "title": "Acme 2026 Q1 filing",
                "canonicalUrl": "https://example.com/filing-2026-q1",
                "publishedAt": "2026-04-30T00:00:00Z",
                "retrievedAt": "2026-08-21T00:00:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Acme published its first-quarter filing.",
                "snippet": "Acme published its first-quarter filing.",
                "capturedAt": "2026-08-21T00:00:00Z",
            },
            "locator": {"kind": "external", "fragment": "provider-summary"},
        }
    }
    chunk = {
        "_valuz_evidence": {
            "evidenceHandle": "ev_chunk_2026_q1",
            "source": {
                "sourceId": document_id,
                "providerId": "valuz-data",
                "documentId": document_id,
                "documentVersion": "sha256:content",
                "sourceType": "document",
                "sourceCategory": "document_chunk",
                "title": "Document",
                "canonicalUrl": "",
                "retrievedAt": "2026-08-21T00:01:00Z",
            },
            "evidence": {
                "kind": "text",
                "quote": "Revenue was 100 USD in 2026 Q1.",
                "snippet": "Revenue was 100 USD in 2026 Q1.",
                "capturedAt": "2026-08-21T00:01:00Z",
            },
            "locator": {"kind": "chunk", "chunkId": "chunk-1"},
        }
    }

    await observer.emit(Event(type="citation_evidence", data={"content": json.dumps(rich)}))
    await observer.emit(Event(type="citation_evidence", data={"content": json.dumps(chunk)}))
    await observer.emit(
        Event(
            type="assistant_message",
            data={
                "text": ("Revenue was 100 USD in 2026 Q1 [source](evidence://ev_chunk_2026_q1).")
            },
        )
    )
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    sidecar = next(event for event in sink.events if event.type == "assistant_message_sidecar")
    citation = sidecar.data["citation_bundle"]["citations"][0]
    assert citation["source"]["title"] == "Acme 2026 Q1 filing"
    assert citation["source"]["canonicalUrl"] == "https://example.com/filing-2026-q1"
    assert citation["source"]["publishedAt"] == "2026-04-30T00:00:00Z"
    assert citation["source"]["documentVersion"] == "sha256:content"
    assert citation["locator"] == {"kind": "chunk", "chunkId": "chunk-1"}


async def test_auto_binding_stays_in_sidecar_and_never_rewrites_runtime_message() -> None:
    sink, observer = _observer(citation_enabled=True, verification_enabled=True)
    await _mark_external_document_tool_called(observer)
    await observer.emit(
        Event(
            type="citation_evidence",
            data={"content": _evidence_payload(), "tool_name": "document_search"},
        )
    )
    original = "Revenue was 100 USD in 2026 Q2."

    await observer.emit(Event(type="assistant_message", data={"text": original}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in sink.events if event.type == "assistant_message")
    sidecar = next(event for event in sink.events if event.type == "assistant_message_sidecar")
    assert assistant.data["text"] == original
    assert observer.assistant_text == original
    projection = sidecar.data["citation_bundle"]["projection"]
    assert projection["anchors"][0]["sourceOffset"] == len(original) - 1
    assert projection["anchors"][0]["origin"] == "auto-bound"
    assert sidecar.data["citation_bundle"]["quality"]["claims"]
    assert "claim_audit" not in sidecar.data
    assert observer.claim_audits[0]["claims"]


async def test_final_recap_reuses_verified_binding_from_prior_assistant_message() -> None:
    sink, observer = _observer(citation_enabled=True, verification_enabled=True)
    await _mark_external_document_tool_called(observer)
    handle = "ev_msft_q3_throughput_12345678"
    await observer.emit(
        Event(
            type="citation_evidence",
            data={
                "content": json.dumps(
                    {
                        "_valuz_evidence": {
                            "evidenceHandle": handle,
                            "source": {
                                "sourceId": "msft-2026-q3",
                                "providerId": "reportify",
                                "documentId": "msft-2026-q3",
                                "sourceType": "document",
                                "title": "Microsoft FY2026 Q3 earnings call",
                                "retrievedAt": "2026-08-08T00:00:00Z",
                            },
                            "evidence": {
                                "kind": "text",
                                "quote": ("Fairwater 提前六周投产，推理吞吐量提升40%。"),
                                "snippet": ("Fairwater 提前六周投产，推理吞吐量提升40%。"),
                                "capturedAt": "2026-08-08T00:00:00Z",
                            },
                            "locator": {
                                "kind": "chunk",
                                "chunkId": "chunk-throughput",
                            },
                        }
                    }
                ),
                "tool_name": "document_fetch",
            },
        )
    )
    first = (
        "### FY2026 Q3\n\n"
        "Fairwater 提前六周投产，推理吞吐量提升40% "
        f"[source](evidence://{handle})。"
    )
    recap = (
        "### FY2026 Q3 汇总\n\n"
        "| 维度 | 核心指标 |\n"
        "|---|---|\n"
        "| 核心优化指标 | 推理吞吐量（+40%） |"
    )

    await observer.emit(Event(type="assistant_message", data={"text": first}))
    await observer.emit(Event(type="assistant_message", data={"text": recap}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    sidecars = [event for event in sink.events if event.type == "assistant_message_sidecar"]
    assert len(sidecars) == 2
    recap_bundle = sidecars[1].data["citation_bundle"]
    assert len(recap_bundle["citations"]) == 1
    assert recap_bundle["projection"]["provenanceRegions"]


async def test_final_recap_reuses_materialized_collection_binding_from_prior_message() -> None:
    policy = {
        "mode": "strict-domain",
        "config": {
            "semantics": {
                "metric_ontology": {
                    "metrics": {
                        "market_cap": {
                            "aliases": ["market cap", "市值"],
                            "fields": ["market_cap"],
                        }
                    }
                }
            }
        },
    }
    sink, observer = _observer(
        citation_enabled=True,
        verification_enabled=True,
        citation_quality_policy=policy,
    )
    await _mark_external_document_tool_called(observer)
    data = {
        "items": [
            {
                "symbol": "GOOGL",
                "date": "2026-08-07",
                "market_cap": 4_287_778_028_630,
            }
        ]
    }
    raw_hash = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    collection_handle = "evc_mcp_stock_quote_12345678"
    await observer.emit(
        Event(
            type="citation_evidence",
            data={
                "tool_name": "stock_quote",
                "content": json.dumps(
                    {
                        "data": data,
                        "_valuz_evidence": [
                            {
                                "version": 1,
                                "kind": "structured-evidence-collection",
                                "collectionHandle": collection_handle,
                                "source": {
                                    "sourceId": "reportify-stock-quote:GOOGL",
                                    "providerId": "reportify",
                                    "sourceType": "dataset",
                                    "sourceCategory": "market_data",
                                    "title": "Reportify · stock_quote",
                                    "retrievedAt": "2026-08-08T00:00:00Z",
                                },
                                "common": {
                                    "datasetId": "reportify.stock_quote",
                                    "toolName": "stock_quote",
                                    "capturedAt": "2026-08-08T00:00:00Z",
                                },
                                "addressing": {
                                    "mode": "json-pointer",
                                    "contentRoot": "/data",
                                    "itemsPointer": "/data/items",
                                    "identityFields": ["/symbol", "/date"],
                                    "allowedPathRoots": ["/data"],
                                },
                                "semantics": {
                                    "entity": {"symbol": "/symbol"},
                                    "asOf": {"date": "/date"},
                                    "metric": {
                                        "mode": "field-name",
                                        "valueRoots": [""],
                                    },
                                },
                                "contentHash": (
                                    "sha256:" + hashlib.sha256(raw_hash.encode()).hexdigest()
                                ),
                            }
                        ],
                    }
                ),
            },
        )
    )
    first = (
        "Google current market cap was $4.29T as of August 7, 2026 "
        f"[source](evidence://{collection_handle}#/data/0/market_cap)."
    )
    recap = "| 公司 | 市值 |\n|---|---:|\n| Google | $4.29万亿 |"

    await observer.emit(Event(type="assistant_message", data={"text": first}))
    await observer.emit(Event(type="assistant_message", data={"text": recap}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    sidecars = [event for event in sink.events if event.type == "assistant_message_sidecar"]
    assert len(sidecars) == 2
    first_bundle = sidecars[0].data["citation_bundle"]
    recap_bundle = sidecars[1].data["citation_bundle"]
    assert len(first_bundle["citations"]) == 1
    assert len(recap_bundle["citations"]) == 1
    assert recap_bundle["projection"]["provenanceRegions"]


async def test_sidecar_failure_never_removes_or_replaces_runtime_message(
    monkeypatch: Any,
) -> None:
    sink, observer = _observer(citation_enabled=True)
    original = "The Runtime authored this exact answer."

    def fail_projection(self: CitationGuard, text: str):  # noqa: ANN202, ARG001
        raise RuntimeError("sidecar failed")

    monkeypatch.setattr(CitationGuard, "finalize_projection", fail_projection)

    await observer.emit(Event(type="assistant_message", data={"text": original}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    _assert_only_lifecycle_extras(sink, ["assistant_message", "session_idle"])
    assert sink.events[0].data["text"] == original
    assert observer.assistant_text == original


async def test_private_evidence_is_registered_even_when_all_sidecars_are_off() -> None:
    sink, observer = _observer()

    await observer.emit(
        Event(
            type="citation_evidence",
            data={"content": _evidence_payload(), "tool_name": "document_search"},
        )
    )
    await observer.emit(Event(type="assistant_message", data={"text": "Plain answer."}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assert observer._evidence_registry.resolve("ev_revenue_2026_q2") is not None
    assert not any(event.type == "assistant_message_sidecar" for event in sink.events)


async def test_unknown_evidence_marker_is_not_rewritten_by_host() -> None:
    sink, observer = _observer(citation_enabled=True)
    original = "Revenue was 120 USD [source](evidence://unknown-handle)."

    await observer.emit(Event(type="assistant_message", data={"text": original}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in sink.events if event.type == "assistant_message")
    assert assistant.data["text"] == original
    assert observer.assistant_text == original


async def test_no_claim_message_is_a_safe_audit_noop() -> None:
    sink, observer = _observer(verification_enabled=True)

    await observer.emit(Event(type="assistant_message", data={"text": "正在处理，请稍候。"}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistant = next(event for event in sink.events if event.type == "assistant_message")
    assert assistant.data["text"] == "正在处理，请稍候。"
    sidecars = [event for event in sink.events if event.type == "assistant_message_sidecar"]
    if sidecars:
        claims = sidecars[0].data.get("claim_audit", {}).get("claims", [])
        assert not claims


async def test_sidecar_computation_runs_off_the_event_loop(monkeypatch: Any) -> None:
    sink, observer = _observer(citation_enabled=True)
    started = threading.Event()
    release = threading.Event()

    def slow_projection(self: CitationGuard, text: str):  # noqa: ANN202, ARG001
        started.set()
        assert release.wait(timeout=1)
        return type("Result", (), {"bundle": None})()

    monkeypatch.setattr(CitationGuard, "finalize_projection", slow_projection)
    await observer.emit(Event(type="assistant_message", data={"text": "Visible first."}))
    finalize_task = asyncio.create_task(
        observer.emit(Event(type="session_idle", data={"num_turns": 1}))
    )
    assert await asyncio.to_thread(started.wait, 1)
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.1)
    release.set()
    await finalize_task

    _assert_only_lifecycle_extras(sink, ["assistant_message", "session_idle"])


async def test_partial_after_canonical_message_is_persisted_separately() -> None:
    sink, observer = _observer()

    await observer.emit(Event(type="text_delta", data={"text": "draft one"}))
    await observer.emit(Event(type="assistant_message", data={"text": "final one"}))
    await observer.emit(Event(type="text_delta", data={"text": "partial two"}))
    await observer.emit(Event(type="session_idle", data={"num_turns": 1}))

    assistants = [event for event in sink.events if event.type == "assistant_message"]
    assert [event.data["text"] for event in assistants] == ["final one", "partial two"]
    assert observer.assistant_text == "final one\npartial two"
