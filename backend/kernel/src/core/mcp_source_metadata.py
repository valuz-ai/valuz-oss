"""Validate MCP result source metadata and project it into Valuz Evidence.

MCP servers keep business data in ``content`` / ``structuredContent`` and may
describe its source semantics in ``_meta["cn.valuz/citation-source"]``.  This
module is the trust-boundary adapter: it verifies the descriptor against the
exact result snapshot, then creates Valuz-private direct Evidence or one lazy
structured Collection.  External MCP servers never allocate Valuz handles.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

MCP_SOURCE_METADATA_KEY = "cn.valuz/citation-source"
MCP_SOURCE_TRANSPORT_KEY = "_valuz_mcp_source_transport_v1"

_MAX_DESCRIPTOR_CHARS = 256_000
_MAX_RESOURCES = 64
_MAX_ITEMS = 2_000
_MAX_QUOTE_CHARS = 32_000
_MAX_STRING_CHARS = 4_096
_SUPPORTED_RESOURCE_KINDS = {
    "document-discovery",
    "document-chunks",
    "document-summary",
    "structured-collection",
    "operational",
}


@dataclass(frozen=True)
class McpSourceAdaptation:
    """Validated model/private projection for one MCP tool result."""

    model_content: Any
    resource_kinds: frozenset[str]
    provider_id: str
    evidence_count: int = 0

    @property
    def discovery_only(self) -> bool:
        return bool(self.resource_kinds) and self.resource_kinds <= {
            "document-discovery",
            "operational",
        }

    @property
    def citable(self) -> bool:
        return self.evidence_count > 0


def wrap_mcp_result_metadata_for_transport(
    result: Any,
    *,
    server_name: str,
) -> Any:
    """Preserve result-level MCP metadata through adapters that drop ``_meta``.

    ``langchain-mcp-adapters`` currently keeps ``structuredContent`` in a
    ToolMessage artifact but discards CallToolResult ``_meta``.  Its tool-call
    interceptor runs before that lossy conversion, so store the descriptor and
    the original structured payload in an internal artifact-only wrapper.  The
    model-visible ``content`` is untouched.
    """

    meta = getattr(result, "meta", None)
    if not isinstance(meta, Mapping) or MCP_SOURCE_METADATA_KEY not in meta:
        return result
    model_copy = getattr(result, "model_copy", None)
    if not callable(model_copy):
        return result
    structured_content = getattr(result, "structuredContent", None)
    transport = {
        MCP_SOURCE_TRANSPORT_KEY: {
            "serverName": server_name,
            "descriptor": copy.deepcopy(meta[MCP_SOURCE_METADATA_KEY]),
            "hasStructuredContent": structured_content is not None,
            "structuredContent": copy.deepcopy(structured_content),
        }
    }
    return model_copy(update={"structuredContent": transport})


def unwrap_mcp_source_transport(
    artifact: Any,
) -> tuple[Any | None, Any | None, dict[str, Any] | None]:
    """Return ``(descriptor, structuredContent, restored_artifact)``.

    The restored artifact contains the MCP adapter's original
    ``structured_content`` value and no Valuz transport wrapper.
    """

    if not isinstance(artifact, Mapping):
        return None, None, None
    structured = artifact.get("structured_content")
    if not isinstance(structured, Mapping):
        return None, None, dict(artifact)
    transport = structured.get(MCP_SOURCE_TRANSPORT_KEY)
    if not isinstance(transport, Mapping):
        return None, None, dict(artifact)
    descriptor = transport.get("descriptor")
    original = transport.get("structuredContent")
    restored = dict(artifact)
    if transport.get("hasStructuredContent"):
        restored["structured_content"] = copy.deepcopy(original)
    else:
        restored.pop("structured_content", None)
    return descriptor, original, restored


def adapt_mcp_source_result(
    content: Any,
    *,
    tool_name: str | None,
    descriptor: Any | None = None,
    structured_content: Any | None = None,
) -> McpSourceAdaptation | None:
    """Validate one descriptor and create Valuz-private evidence envelopes.

    ``descriptor`` / ``structured_content`` are supplied by the DeepAgents
    transport bridge.  Claude's hook normally exposes the original MCP result,
    so the function also discovers top-level ``_meta`` and
    ``structuredContent`` directly from ``content``.
    """

    wire = _decode_wire(content)
    if descriptor is None:
        descriptor = _descriptor_from_wire(wire)
    if not isinstance(descriptor, Mapping):
        return None
    if len(_canonical_json(descriptor)) > _MAX_DESCRIPTOR_CHARS:
        return None

    result_spec = descriptor.get("result")
    provider = descriptor.get("provider")
    operation = descriptor.get("operation")
    resources = descriptor.get("resources")
    if (
        descriptor.get("version") != 1
        or not isinstance(result_spec, Mapping)
        or not isinstance(provider, Mapping)
        or not isinstance(operation, Mapping)
        or not isinstance(resources, list)
        or len(resources) > _MAX_RESOURCES
    ):
        return None
    provider_id = _bounded_string(provider.get("id"), 256)
    declared_tool = _bounded_string(operation.get("toolName"), 512)
    if not provider_id or not declared_tool:
        return None
    simple_tool_name = str(tool_name or "").rsplit("__", 1)[-1]
    if simple_tool_name and declared_tool != simple_tool_name:
        return None

    target_name = result_spec.get("target")
    if target_name not in {"content", "structuredContent"}:
        return None
    if target_name == "structuredContent":
        target = structured_content
        if target is None:
            target = _structured_content_from_wire(wire)
    else:
        target = _content_from_wire(wire)
    target = _json_value(target)
    if target is None or not _valid_result_hash(target, result_spec.get("hash")):
        return None

    captured_at = _bounded_string(result_spec.get("capturedAt"), 128) or _now_iso()
    envelopes: list[dict[str, Any]] = []
    kinds: set[str] = set()
    for raw_resource in resources:
        if not isinstance(raw_resource, Mapping):
            return None
        kind = raw_resource.get("kind")
        if kind not in _SUPPORTED_RESOURCE_KINDS:
            return None
        kinds.add(str(kind))
        if kind == "document-chunks":
            chunk_envelopes = _document_envelopes(
                target,
                descriptor=descriptor,
                resource=raw_resource,
                captured_at=captured_at,
            )
            if chunk_envelopes is None:
                return None
            envelopes.extend(chunk_envelopes)
        elif kind == "document-summary":
            summary_envelopes = _document_summary_envelopes(
                target,
                descriptor=descriptor,
                resource=raw_resource,
                captured_at=captured_at,
            )
            if summary_envelopes is None:
                return None
            envelopes.extend(summary_envelopes)
        elif kind == "document-discovery":
            # Discovery rows select what to read next.  Even their title or
            # doc_id cannot support a business claim, and exposing a generic
            # metadata Collection lets models bind arbitrary prose to search
            # result fields.  Keep the rows in Model Content and Task Coverage
            # only; document_fetch/kb_search supplies citable chunks.
            continue
        elif kind == "structured-collection":
            collection = _structured_collection_envelope(
                target,
                descriptor=descriptor,
                resource=raw_resource,
                captured_at=captured_at,
            )
            if collection is None:
                return None
            envelopes.append(collection)

    model_content = copy.deepcopy(target)
    if envelopes:
        if isinstance(model_content, dict):
            model_content["_valuz_evidence"] = envelopes
        else:
            model_content = {
                "data": model_content,
                "_valuz_evidence": _shift_root_envelopes(envelopes),
            }
    return McpSourceAdaptation(
        model_content=model_content,
        resource_kinds=frozenset(kinds),
        provider_id=provider_id,
        evidence_count=len(envelopes),
    )


def _descriptor_from_wire(value: Any) -> Any | None:
    if not isinstance(value, Mapping):
        return None
    meta = value.get("_meta") or value.get("meta")
    if isinstance(meta, Mapping) and MCP_SOURCE_METADATA_KEY in meta:
        return meta[MCP_SOURCE_METADATA_KEY]
    for key in ("result", "data"):
        nested = value.get(key)
        found = _descriptor_from_wire(nested)
        if found is not None:
            return found
    return None


def _structured_content_from_wire(value: Any) -> Any | None:
    if not isinstance(value, Mapping):
        return None
    for key in ("structuredContent", "structured_content"):
        if key in value:
            return value[key]
    return None


def _content_from_wire(value: Any) -> Any:
    if (
        isinstance(value, Mapping)
        and "content" in value
        and ("_meta" in value or "meta" in value or "structuredContent" in value)
    ):
        return value["content"]
    return value


def _valid_result_hash(target: Any, value: Any) -> bool:
    if not isinstance(value, Mapping) or value.get("algorithm") != "sha256":
        return False
    expected = value.get("value")
    if not isinstance(expected, str) or len(expected) != 64:
        return False
    candidates = {_sha256(target), _sha256(_drop_none(target))}
    return expected.casefold() in candidates


def _document_envelopes(
    target: Any,
    *,
    descriptor: Mapping[str, Any],
    resource: Mapping[str, Any],
    captured_at: str,
) -> list[dict[str, Any]] | None:
    operation = descriptor.get("operation")
    operation = operation if isinstance(operation, Mapping) else {}
    operation_tool = str(operation.get("toolName") or "").rsplit("__", 1)[-1]
    if operation_tool == "document_raw_content":
        # Older Reportify metadata described the root ``content`` string as
        # one document chunk and reused doc_id as chunkId.  Raw content is a
        # retrieval substrate; targeted grep/search creates the actual local
        # Evidence.  This is operation-specific, not a blanket size rule:
        # document_fetch may legitimately return one real chunk for a short
        # or naturally indivisible document.
        return []
    root_pointer = _pointer(resource.get("rootPointer"))
    items_pointer = _pointer(resource.get("itemsPointer"))
    mapping = resource.get("mapping")
    document = resource.get("document")
    if root_pointer is None or items_pointer is None or not isinstance(mapping, Mapping):
        return None
    if not isinstance(document, Mapping) or document.get("scope") not in {"resource", "item"}:
        return None
    found, root = _resolve_pointer(target, root_pointer)
    if not found:
        return None
    found, items = _resolve_pointer(target, items_pointer)
    if not found:
        return None
    if isinstance(items, list):
        item_values = items[:_MAX_ITEMS]
    elif isinstance(items, Mapping) or isinstance(items, str):
        item_values = [items]
    else:
        return None

    output: list[dict[str, Any]] = []
    for item in item_values:
        if not isinstance(item, (Mapping, str)):
            continue
        doc_base = item if document.get("scope") == "item" else root
        text = _mapped_value(item, mapping.get("text"))
        chunk_id = _mapped_value(item, mapping.get("chunkId"))
        if not isinstance(text, str) or not text.strip():
            continue
        source_id = _mapped_value(doc_base, document.get("sourceId"))
        document_id = _mapped_value(doc_base, document.get("documentId")) or source_id
        if not source_id or not document_id:
            continue
        version = _mapped_value(doc_base, document.get("documentVersion"))
        published_at = _mapped_value(doc_base, document.get("publishedAt"))
        url = _mapped_value(doc_base, document.get("url"))
        category = _mapped_value(doc_base, document.get("providerCategory"))
        title = _mapped_value(doc_base, document.get("title")) or _fallback_document_title(
            url,
            category=category,
        )
        quote = text.strip()[:_MAX_QUOTE_CHARS]
        if not isinstance(chunk_id, (str, int)) or not str(chunk_id).strip():
            continue
        stable_chunk_id = str(chunk_id).strip()[:512]
        source = _document_source(
            descriptor,
            source_id=str(source_id),
            document_id=str(document_id),
            title=str(title),
            # Publication time identifies when a document was issued, not
            # which immutable content/locator snapshot produced this chunk.
            # Substituting it for a missing version makes a later canonical
            # content hash look like a document change even when the cited
            # chunk and quote are unchanged.
            version=str(version or "") or None,
            published_at=published_at,
            url=url,
            category=category,
            captured_at=captured_at,
        )
        locator = _document_locator(
            item,
            mapping=mapping,
            chunk_id=stable_chunk_id,
            quote=quote,
        )
        digest = hashlib.sha256(
            f"{source['providerId']}\0{document_id}\0{stable_chunk_id}\0{quote}".encode()
        ).hexdigest()[:24]
        output.append(
            {
                "evidenceHandle": f"ev_mcp_{digest}",
                "source": source,
                "evidence": {
                    "kind": "text",
                    "quote": quote,
                    "snippet": quote[:4_000],
                    "capturedAt": captured_at,
                    "contentHash": f"sha256:{hashlib.sha256(quote.encode()).hexdigest()}",
                },
                "locator": locator,
            }
        )
    return output


def _document_summary_envelopes(
    target: Any,
    *,
    descriptor: Mapping[str, Any],
    resource: Mapping[str, Any],
    captured_at: str,
) -> list[dict[str, Any]] | None:
    """Materialize one fetched document summary as direct text Evidence.

    This resource covers provider-authored text that is already present once in
    the opened-document result but is not represented by any returned chunk.
    It deliberately has no fake chunk id, page, or bbox.  Raw-document tools
    remain retrieval substrates and cannot promote the whole body through this
    lower-confidence summary path.
    """

    operation = descriptor.get("operation")
    operation = operation if isinstance(operation, Mapping) else {}
    operation_tool = str(operation.get("toolName") or "").rsplit("__", 1)[-1]
    if operation_tool == "document_raw_content":
        return []
    if resource.get("authority") != "derived":
        return None
    resource_id = _bounded_string(resource.get("resourceId"), 512)
    root_pointer = _pointer(resource.get("rootPointer"))
    text_pointer = _pointer(resource.get("textPointer"))
    document = resource.get("document")
    locator_spec = resource.get("locator")
    if (
        not resource_id
        or root_pointer is None
        or text_pointer is None
        or not isinstance(document, Mapping)
        or document.get("scope") != "resource"
        or not isinstance(locator_spec, Mapping)
        or locator_spec.get("kind") != "external"
    ):
        return None
    found, root = _resolve_pointer(target, root_pointer)
    if not found or not isinstance(root, Mapping):
        return None
    found, text = _resolve_pointer(root, text_pointer)
    if not found or not isinstance(text, str) or not text.strip():
        return None
    quote = text.strip()
    if len(quote) > _MAX_QUOTE_CHARS:
        return None
    source_id = _mapped_value(root, document.get("sourceId"))
    document_id = _mapped_value(root, document.get("documentId")) or source_id
    if not source_id or not document_id:
        return None
    version = _mapped_value(root, document.get("documentVersion"))
    published_at = _mapped_value(root, document.get("publishedAt"))
    url = _mapped_value(root, document.get("url"))
    category = _mapped_value(root, document.get("providerCategory"))
    title = _mapped_value(root, document.get("title")) or _fallback_document_title(
        url,
        category=category,
    )
    source = _document_source(
        descriptor,
        source_id=str(source_id),
        document_id=str(document_id),
        title=str(title),
        version=str(version or published_at or "") or None,
        published_at=published_at,
        url=url,
        category=category or "document_summary",
        captured_at=captured_at,
    )
    fragment = _bounded_string(locator_spec.get("fragment"), 512)
    if not fragment:
        return None
    provider = descriptor.get("provider")
    provider = provider if isinstance(provider, Mapping) else {}
    digest = hashlib.sha256(
        (
            f"{provider.get('id')}\0{resource_id}\0{document_id}\0"
            f"{text_pointer}\0{hashlib.sha256(quote.encode()).hexdigest()}"
        ).encode()
    ).hexdigest()[:24]
    return [
        {
            "evidenceHandle": f"ev_mcp_{digest}",
            "source": source,
            "evidence": {
                "kind": "text",
                "quote": quote,
                "snippet": quote[:4_000],
                "capturedAt": captured_at,
                "contentHash": f"sha256:{hashlib.sha256(quote.encode()).hexdigest()}",
            },
            "locator": {"kind": "external", "fragment": fragment},
        }
    ]


def _fallback_document_title(value: Any, *, category: Any) -> str:
    """Return a readable fallback without exposing an opaque document id."""

    if isinstance(value, str) and value.strip():
        parsed = urlparse(value.strip())
        if parsed.hostname:
            return parsed.hostname.removeprefix("www.")[:1_024]
    normalized_category = str(category or "").replace("_", " ").strip()
    return normalized_category[:1_024] or "Document"


def _document_source(
    descriptor: Mapping[str, Any],
    *,
    source_id: str,
    document_id: str,
    title: str,
    version: str | None,
    published_at: Any,
    url: Any,
    category: Any,
    captured_at: str,
) -> dict[str, Any]:
    provider = descriptor.get("provider")
    provider = provider if isinstance(provider, Mapping) else {}
    source: dict[str, Any] = {
        "sourceId": source_id[:512],
        "documentId": document_id[:512],
        "providerId": str(provider.get("id") or "mcp")[:512],
        "sourceType": "document",
        "sourceCategory": str(category or "document_chunk")[:1_024],
        "title": title[:1_024],
        "retrievedAt": captured_at,
    }
    if version:
        source["documentVersion"] = version[:512]
    if isinstance(url, str) and url.strip():
        source["canonicalUrl"] = url.strip()[:4_096]
    normalized_published = _timestamp_text(published_at)
    if normalized_published:
        source["publishedAt"] = normalized_published
    return source


def _document_locator(
    item: Any,
    *,
    mapping: Mapping[str, Any],
    chunk_id: str,
    quote: str,
) -> dict[str, Any]:
    page = _mapped_value(item, mapping.get("page"))
    selector = _mapped_value(item, mapping.get("htmlSelector"))
    if isinstance(page, int) and not isinstance(page, bool) and page >= 1:
        locator: dict[str, Any] = {
            "kind": "pdf",
            "page": page,
            "chunkId": chunk_id,
            "quote": {"exact": quote},
        }
        rect = _normalized_rect(
            _mapped_value(item, mapping.get("bbox")),
            page_width=_mapped_value(item, mapping.get("pageWidth")),
            page_height=_mapped_value(item, mapping.get("pageHeight")),
        )
        if rect is not None:
            locator["coordinateSpace"] = "viewport-normalized-v1"
            locator["rects"] = [rect]
        return locator
    if isinstance(selector, str) and selector.strip():
        return {
            "kind": "html",
            "chunkId": chunk_id,
            "cssSelector": selector.strip()[:4_096],
            "quote": {"exact": quote},
        }
    return {
        "kind": "chunk",
        "chunkId": chunk_id,
        "quote": {"exact": quote},
    }


def _normalized_rect(value: Any, *, page_width: Any, page_height: Any) -> dict[str, float] | None:
    if not isinstance(value, Mapping):
        return None
    try:
        width = float(page_width)
        height = float(page_height)
        left = float(str(value.get("left")))
        top = float(str(value.get("top")))
        right = float(str(value.get("right")))
        bottom = float(str(value.get("bottom")))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or right <= left or bottom <= top:
        return None
    rect = {
        "x": left / width,
        "y": top / height,
        "width": (right - left) / width,
        "height": (bottom - top) / height,
    }
    if any(number < 0 or number > 1 for number in rect.values()):
        return None
    return rect


def _structured_collection_envelope(
    target: Any,
    *,
    descriptor: Mapping[str, Any],
    resource: Mapping[str, Any],
    captured_at: str,
) -> dict[str, Any] | None:
    root_pointer = _pointer(resource.get("rootPointer"))
    if root_pointer is None:
        return None
    found, snapshot = _resolve_pointer(target, root_pointer)
    if not found or not isinstance(snapshot, (Mapping, list)):
        return None
    addressing = resource.get("addressing")
    dataset = resource.get("dataset")
    if not isinstance(addressing, Mapping) or not isinstance(dataset, Mapping):
        return None
    if addressing.get("mode") != "json-pointer":
        return None
    items_pointer = _pointer(resource.get("itemsPointer")) or root_pointer
    if not _pointer_inside(items_pointer, root_pointer):
        return None
    allowed_roots = addressing.get("allowedPathRoots")
    allowed_item_paths = addressing.get("allowedItemPaths")
    if not (
        (isinstance(allowed_roots, list) and allowed_roots)
        or (isinstance(allowed_item_paths, list) and allowed_item_paths)
    ):
        return None
    normalized_roots: list[str] = []
    for root in allowed_roots if isinstance(allowed_roots, list) else []:
        pointer = _pointer(root)
        if pointer is None or not _pointer_inside(pointer, root_pointer):
            return None
        normalized_roots.append(pointer)
    normalized_item_paths: list[str] = []
    for item_path in allowed_item_paths if isinstance(allowed_item_paths, list) else []:
        pointer = _pointer(item_path)
        if pointer in {None, ""}:
            return None
        normalized_item_paths.append(pointer)
    dataset_id = _bounded_string(dataset.get("id"), 512)
    resource_id = _bounded_string(resource.get("resourceId"), 512)
    if not dataset_id or not resource_id:
        return None
    provider = descriptor.get("provider")
    provider = provider if isinstance(provider, Mapping) else {}
    operation = descriptor.get("operation")
    operation = operation if isinstance(operation, Mapping) else {}
    raw_result = descriptor.get("result")
    raw_hash = raw_result.get("hash") if isinstance(raw_result, Mapping) else None
    result_hash: Mapping[str, Any] = raw_hash if isinstance(raw_hash, Mapping) else {}
    digest = hashlib.sha256(
        f"{provider.get('id')}\0{operation.get('toolName')}\0{resource_id}\0{result_hash.get('value')}".encode()
    ).hexdigest()[:24]
    source: dict[str, Any] = {
        "sourceId": f"{dataset_id}:{digest}"[:512],
        "providerId": str(provider.get("id") or "mcp")[:512],
        "sourceType": "dataset",
        "sourceCategory": str(dataset.get("sourceCategory") or "structured_data")[:1_024],
        "title": (
            f"{provider.get('name') or provider.get('id') or 'MCP data'}"
            f" · {operation.get('toolName') or dataset_id}"
        )[:1_024],
        "retrievedAt": captured_at,
    }
    identity = resource.get("identity")
    identity_fields = identity.get("fields") if isinstance(identity, Mapping) else []
    identity_fields = identity_fields if isinstance(identity_fields, list) else []
    semantics = resource.get("semantics")
    semantics = copy.deepcopy(semantics) if isinstance(semantics, Mapping) else {}
    schema_ref = addressing.get("fieldSchemaRef") or resource.get("schemaRef")
    normalized_schema: dict[str, str] | None = None
    if isinstance(schema_ref, Mapping):
        schema_id = _bounded_string(schema_ref.get("id") or schema_ref.get("schemaId"), 512)
        revision = _bounded_string(schema_ref.get("revision"), 512)
        if schema_id and revision:
            normalized_schema = {"schemaId": schema_id, "revision": revision}
    collection_addressing: dict[str, Any] = {
        "mode": "json-pointer",
        "contentRoot": root_pointer,
        "itemsPointer": items_pointer,
        "identityFields": [
            pointer for value in identity_fields[:32] if (pointer := _pointer(value)) is not None
        ],
    }
    if normalized_roots:
        collection_addressing["allowedPathRoots"] = normalized_roots
    if normalized_item_paths:
        collection_addressing["allowedItemPaths"] = normalized_item_paths
    if normalized_schema:
        collection_addressing["fieldSchemaRef"] = normalized_schema
    return {
        "version": 1,
        "kind": "structured-evidence-collection",
        "collectionHandle": f"evc_mcp_{digest}",
        "source": source,
        "common": {
            "datasetId": dataset_id,
            "toolName": str(operation.get("toolName") or "mcp_tool")[:1_024],
            "capturedAt": captured_at,
        },
        "addressing": collection_addressing,
        "semantics": semantics,
        "contentHash": _content_hash(snapshot),
    }


def _shift_root_envelopes(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shifted = copy.deepcopy(envelopes)
    for envelope in shifted:
        if envelope.get("kind") != "structured-evidence-collection":
            continue
        addressing = envelope.get("addressing")
        if not isinstance(addressing, dict):
            continue
        for key in ("contentRoot", "itemsPointer"):
            value = str(addressing.get(key) or "")
            addressing[key] = f"/data{value}"
        addressing["allowedPathRoots"] = [
            f"/data{str(value or '')}" for value in addressing.get("allowedPathRoots", [])
        ]
    return shifted


def _mapped_value(value: Any, pointer: Any) -> Any:
    normalized = _pointer(pointer)
    if normalized is None:
        return None
    found, result = _resolve_pointer(value, normalized)
    return result if found else None


def _pointer(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 4_096:
        return None
    if value == "":
        return ""
    if not value.startswith("/"):
        return None
    for token in value[1:].split("/"):
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 >= len(token) or token[index + 1] not in "01":
                    return None
                index += 2
            else:
                index += 1
    return value


def _pointer_tokens(pointer: str) -> list[str]:
    if not pointer:
        return []
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def _resolve_pointer(value: Any, pointer: str) -> tuple[bool, Any]:
    current = value
    for token in _pointer_tokens(pointer):
        if isinstance(current, Mapping):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False, None
    return True, current


def _pointer_inside(pointer: str, root: str) -> bool:
    return pointer == root or pointer.startswith(f"{root.rstrip('/')}/")


def _content_hash(value: Any) -> str:
    return f"sha256:{_sha256(value)}"


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _drop_none(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _json_value(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_value(model_dump(by_alias=True, exclude_none=True))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _decode_wire(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return _json_value(json.loads(stripped))
            except (TypeError, ValueError):
                return value
    return _json_value(value)


def _bounded_string(value: Any, limit: int) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        return None
    return value.strip()


def _timestamp_text(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1_000
        try:
            return datetime.fromtimestamp(timestamp, UTC).isoformat().replace("+00:00", "Z")
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        return value.strip()[:128]
    return None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "MCP_SOURCE_METADATA_KEY",
    "MCP_SOURCE_TRANSPORT_KEY",
    "McpSourceAdaptation",
    "adapt_mcp_source_result",
    "unwrap_mcp_source_transport",
    "wrap_mcp_result_metadata_for_transport",
]
