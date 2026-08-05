"""Targeted evidence extraction from a retrieved original document."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

_REQUEST_FIELD_BLOCK_RE = re.compile(
    r"(?:列出|给出|查找|查询|提取|说明)\s*(?P<fields>[^。；\n]{2,220}?)"
    r"(?=，?\s*并(?:逐|给|附|引用|注明)|[。；\n]|$)",
    re.IGNORECASE,
)
_REQUEST_FIELD_SPLIT_RE = re.compile(r"\s*(?:、|，|,|以及|及|与|和|\band\b)\s*", re.I)
_REQUEST_TERM_TRIM_RE = re.compile(
    r"^(?:请|分别|只用\s*[一二两三四五六七八九十\d]+\s*行|只|仅|逐项)\s*"
)


def constrain_indexed_document_scope(
    content: Any,
    *,
    document_ids: tuple[str, ...],
) -> Any:
    """Remove indexed chunks that escaped an explicit document scope.

    Some remote search providers accept an unknown singular ``doc_id`` field
    but silently execute a global search. Returned chunks still carry stable
    document ids, so enforce the requested scope before either the model or
    Evidence Registry sees them.
    """

    allowed = {str(item).strip() for item in document_ids if str(item).strip()}
    if not allowed:
        return content
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            return content
        constrained = constrain_indexed_document_scope(
            decoded,
            document_ids=tuple(allowed),
        )
        return json.dumps(constrained, ensure_ascii=False, separators=(",", ":"))
    if isinstance(content, list):
        output: list[Any] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                output.append(block)
                continue
            text = block.get("text")
            if not isinstance(text, str):
                output.append(block)
                continue
            output.append(
                {
                    **block,
                    "text": constrain_indexed_document_scope(
                        text,
                        document_ids=tuple(allowed),
                    ),
                }
            )
        return output
    if not isinstance(content, dict) or not isinstance(content.get("chunks"), list):
        return content
    identified_chunks = [
        chunk
        for chunk in content["chunks"]
        if isinstance(chunk, dict) and _indexed_chunk_document_id(chunk)
    ]
    if not identified_chunks:
        # Some internal/legacy adapters return already-scoped plain strings.
        # There is no contrary document identity to prove leakage, so preserve
        # them. Scope enforcement activates only on explicit returned ids.
        return content
    chunks = [chunk for chunk in identified_chunks if _indexed_chunk_document_id(chunk) in allowed]
    result = {**content, "chunks": chunks}
    if len(chunks) != len(content["chunks"]):
        result["_valuz_scope"] = {
            "documentIds": sorted(allowed),
            "discardedOutOfScopeChunks": len(content["chunks"]) - len(chunks),
        }
    return result


def _indexed_chunk_document_id(chunk: dict[str, Any]) -> str:
    document = chunk.get("doc")
    document = document if isinstance(document, dict) else {}
    return str(
        document.get("doc_id")
        or document.get("document_id")
        or chunk.get("doc_id")
        or chunk.get("document_id")
        or ""
    ).strip()


def augment_indexed_document_evidence(
    content: Any,
    *,
    tool_name: str,
    captured_at: str,
) -> Any | None:
    """Attach trusted Evidence envelopes to indexed document chunks.

    Some document-search connectors return exact indexed chunks with stable
    document/chunk metadata but no ``_valuz_evidence`` envelope.  The runtime
    is already inside the trusted tool boundary, so standardize those immutable
    rows here instead of forcing the model to fall back to ordinary URLs.
    """

    if tool_name.rsplit("__", 1)[-1] != "kb_search":
        return None
    if isinstance(content, str):
        try:
            decoded = json.loads(content)
        except (TypeError, ValueError):
            return None
        augmented = augment_indexed_document_evidence(
            decoded,
            tool_name=tool_name,
            captured_at=captured_at,
        )
        if augmented is None:
            return None
        return json.dumps(augmented, ensure_ascii=False, separators=(",", ":"))
    if isinstance(content, list):
        changed = False
        output: list[Any] = []
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                output.append(block)
                continue
            text = block.get("text")
            augmented = (
                augment_indexed_document_evidence(
                    text,
                    tool_name=tool_name,
                    captured_at=captured_at,
                )
                if isinstance(text, str)
                else None
            )
            if augmented is None:
                output.append(block)
                continue
            output.append({**block, "text": augmented})
            changed = True
        return output if changed else None
    if not isinstance(content, dict) or not isinstance(content.get("chunks"), list):
        return None
    if content.get("_valuz_evidence"):
        return None

    envelopes = [
        envelope
        for chunk in content["chunks"]
        if isinstance(chunk, dict)
        if not _is_low_value_transcript_chunk(chunk)
        if (
            envelope := _indexed_chunk_evidence(
                chunk,
                captured_at=captured_at,
            )
        )
        is not None
    ]
    if not envelopes:
        return None
    return {**content, "_valuz_evidence": envelopes}


def _is_low_value_transcript_chunk(chunk: dict[str, Any]) -> bool:
    """Exclude navigation/boilerplate chunks from answer evidence.

    Indexed transcript searches often prepend participant lists, operator
    greetings, replay instructions and forward-looking disclaimers ahead of
    the semantically matched business passages. Those chunks are authentic but
    cannot support a user-facing business claim; registering them wastes model
    and focused Evidence slots. Apply this only to transcript/minutes sources
    and only for unambiguous boilerplate templates.
    """

    document = chunk.get("doc")
    document = document if isinstance(document, dict) else {}
    category = str(document.get("category") or "").strip().casefold()
    if category not in {"transcript", "transcripts", "minutes", "meeting_minutes"}:
        return False
    quote = " ".join(str(chunk.get("content") or "").split()).casefold()
    if not quote:
        return True
    return bool(
        re.search(r"\bcompany participants\b|\bconference call participants\b", quote)
        or (
            "greetings" in quote
            and "earnings" in quote
            and "question and answer session will follow" in quote
        )
        or ("on the call with me are" in quote and "chief financial officer" in quote)
        or "included as additional clarifying items to aid investors" in quote
        or ("you can replay the call" in quote and "forward-looking statements" in quote)
    )


def _indexed_chunk_evidence(
    chunk: dict[str, Any],
    *,
    captured_at: str,
) -> dict[str, Any] | None:
    quote = str(chunk.get("content") or "").strip()
    document = chunk.get("doc")
    document = document if isinstance(document, dict) else {}
    document_id = _indexed_chunk_document_id(chunk)
    chunk_id = str(chunk.get("id") or chunk.get("chunk_id") or "").strip()
    if not quote or not document_id or not chunk_id:
        return None
    quote = quote[:32_000]
    url = str(document.get("url") or document.get("file_url") or "").strip()
    title = str(document.get("title") or _document_title_fallback(url)).strip()
    published_at = _published_at(document.get("published_at"))
    digest = hashlib.sha256(
        f"valuz-search\0{document_id}\0{chunk_id}\0{quote}".encode()
    ).hexdigest()[:24]
    source: dict[str, Any] = {
        "sourceId": document_id[:512],
        "documentId": document_id[:512],
        "providerId": "valuz-search",
        "sourceType": "document",
        "sourceCategory": str(document.get("category") or "search_document")[:1_024],
        "title": title[:1_024],
        "retrievedAt": captured_at,
    }
    if url:
        source["canonicalUrl"] = url
    if published_at:
        source["publishedAt"] = published_at
        source["documentVersion"] = published_at[:512]

    metadata = chunk.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    page = metadata.get("document_page")
    locator: dict[str, Any]
    if isinstance(page, int) and not isinstance(page, bool) and page >= 1:
        locator = {
            "kind": "pdf",
            "page": page,
            "chunkId": chunk_id[:512],
            "quote": {"exact": quote},
        }
    else:
        locator = {
            "kind": "chunk",
            "chunkId": chunk_id[:512],
            "quote": {"exact": quote},
        }
    return {
        "evidenceHandle": f"ev_chunk_{digest}",
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


def _document_title_fallback(url: str) -> str:
    """Avoid surfacing an opaque document id as a source title."""

    if url:
        hostname = urlparse(url).hostname
        if hostname:
            return hostname.removeprefix("www.")[:1_024]
    return "Document"


def _published_at(value: Any) -> str | None:
    if isinstance(value, bool):
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


def extract_raw_document(content: Any) -> dict[str, Any] | None:
    """Find the original-document payload inside common tool wire shapes."""

    if isinstance(content, list):
        for block in content:
            candidate = extract_raw_document(block)
            if candidate is not None:
                return candidate
        return None
    if isinstance(content, dict):
        text = content.get("text") if content.get("type") == "text" else None
        if isinstance(text, str):
            candidate = extract_raw_document(text)
            if candidate is not None:
                return candidate
        raw = content.get("content")
        if content.get("doc_id") and isinstance(raw, str) and raw.strip():
            return dict(content)
        return None
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    return extract_raw_document(payload)


def grep_document_evidence(
    content: Any,
    *,
    tool_args: dict[str, Any],
    raw_documents: dict[str, dict[str, Any]],
    captured_at: str,
) -> tuple[str, dict[str, Any]] | None:
    """Turn exact grep matches over a cached original into trusted Evidence."""

    serialized_args = _serialize(tool_args)
    output = f"{_serialize(content)}\n{serialized_args}"
    referenced_ids = re.findall(
        r"/(?:large_tool_results|tool-results)/([A-Za-z0-9_-]+)(?:\.txt)?",
        output,
    )
    raw_document = next(
        (raw_documents[item] for item in referenced_ids if item in raw_documents),
        next(iter(raw_documents.values())) if len(raw_documents) == 1 else None,
    )
    if raw_document is None:
        return None
    pattern = _targeted_search_pattern(tool_args)
    raw_text = str(raw_document.get("content") or "")
    if not pattern or not raw_text:
        return None
    matches = _document_matches(raw_text, pattern)[:500]
    if not matches:
        return None

    matches.sort(
        key=lambda match: _document_match_score(raw_text, match),
        reverse=True,
    )
    matches = matches[:4]

    excerpts: list[str] = []
    for match in matches:
        excerpt = _document_match_excerpt(raw_text, match)
        if excerpt and excerpt not in excerpts:
            excerpts.append(excerpt)
    quote = "\n…\n".join(excerpts)[:32_000]
    first = matches[0]
    snippet = raw_text[max(0, first.start() - 260) : min(len(raw_text), first.end() + 420)].strip()[
        :4_000
    ]
    document_id = str(raw_document.get("doc_id") or "document")
    url = str(raw_document.get("url") or raw_document.get("original_url") or "").strip()
    title = str(raw_document.get("title") or f"Reportify document · {document_id}")
    digest = hashlib.sha256(f"{document_id}\0{pattern}\0{quote}".encode()).hexdigest()[:24]
    source: dict[str, Any] = {
        "sourceId": document_id[:512],
        "documentId": document_id[:512],
        "providerId": "valuz-search",
        "sourceType": "document",
        "sourceCategory": "financials" if "/financials/" in url else "search_document",
        "title": title[:1_024],
        "retrievedAt": captured_at,
    }
    if url:
        source["canonicalUrl"] = url
    file_url = str(raw_document.get("file_url") or "")
    if file_url.lower().split("?", 1)[0].endswith(".pdf"):
        source["mimeType"] = "application/pdf"
    envelope = {
        "evidenceHandle": f"ev_grep_{digest}",
        "source": source,
        "evidence": {
            "kind": "text",
            "quote": quote,
            "snippet": snippet,
            "capturedAt": captured_at,
        },
        "locator": {"kind": "external", "fragment": pattern[:512]},
    }
    visible = json.dumps(
        {
            "pattern": pattern,
            "matches": snippet,
            "_valuz_evidence": [
                {
                    "evidenceHandle": envelope["evidenceHandle"],
                    "kind": "text",
                    "sourceTitle": title,
                    "excerpt": snippet,
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return visible, envelope


@dataclass(frozen=True)
class _DocumentMatch:
    start_offset: int
    end_offset: int

    def start(self) -> int:
        return self.start_offset

    def end(self) -> int:
        return self.end_offset


def _document_matches(raw_text: str, pattern: str) -> list[re.Match[str] | _DocumentMatch]:
    """Find exact regex matches, then tolerate PDF-only whitespace wrapping.

    PDF text extraction commonly inserts a newline in the middle of one CJK
    word (for example ``归属\n于``). The user's/tool's literal query is still
    exact, but ordinary regex matching cannot see it. Only when the original
    regex has no match do we retry literal alternatives against a whitespace-
    free view and map the offsets back to the immutable raw text. Regex
    semantics never get broadened and the Evidence quote remains byte-for-byte
    from the retrieved document.
    """

    try:
        matcher = re.compile(pattern, re.IGNORECASE)
    except re.error:
        matcher = re.compile(re.escape(pattern), re.IGNORECASE)
    direct = list(matcher.finditer(raw_text))
    if direct:
        return direct

    normalized_chars: list[str] = []
    raw_offsets: list[int] = []
    for index, char in enumerate(raw_text):
        if char.isspace():
            continue
        normalized_chars.append(char.casefold())
        raw_offsets.append(index)
    if not normalized_chars:
        return []
    normalized_text = "".join(normalized_chars)

    output: list[_DocumentMatch] = []
    seen: set[tuple[int, int]] = set()
    for alternative in pattern.split("|"):
        literal = re.sub(r"\\(.)", r"\1", alternative).strip()
        # This fallback is deliberately literal. Do not reinterpret arbitrary
        # regular expressions or loosen short tokens that would create noisy
        # evidence windows throughout a long report.
        if len(literal) < 4 or re.search(r"[.^$*+?{}\[\]()]", literal):
            continue
        normalized_literal = "".join(
            char.casefold() for char in literal if not char.isspace()
        )
        if len(normalized_literal) < 4:
            continue
        cursor = 0
        while True:
            found = normalized_text.find(normalized_literal, cursor)
            if found < 0:
                break
            start = raw_offsets[found]
            end = raw_offsets[found + len(normalized_literal) - 1] + 1
            identity = (start, end)
            if identity not in seen:
                seen.add(identity)
                output.append(_DocumentMatch(start, end))
            cursor = found + max(1, len(normalized_literal))
    return sorted(output, key=lambda item: item.start())


def request_search_terms(request: str) -> tuple[str, ...]:
    """Extract explicitly requested fields without embedding domain vocabulary."""

    terms: list[str] = []
    for match in _REQUEST_FIELD_BLOCK_RE.finditer(request):
        fields = re.sub(r"[（(][^）)]{0,120}[）)]", "", match.group("fields"))
        for raw in _REQUEST_FIELD_SPLIT_RE.split(fields):
            term = _REQUEST_TERM_TRIM_RE.sub("", raw).strip()
            term = re.sub(r"(?:对应的)?(?:年度)?报告原文$", "", term).strip()
            candidates = [term]
            if "的" in term:
                candidates.extend(part.strip() for part in term.split("的"))
            for candidate in candidates:
                if 2 <= len(candidate) <= 64 and candidate not in terms:
                    terms.append(candidate)
    for quoted in re.findall(r"[`“\"]([^`”\"]{2,64})[`”\"]", request):
        term = quoted.strip()
        if term and term not in terms:
            terms.append(term)
    return tuple(terms[:8])


def targeted_document_evidence(
    raw_document: dict[str, Any],
    *,
    terms: tuple[str, ...],
    captured_at: str,
) -> tuple[str, list[dict[str, Any]]] | None:
    """Resolve requested field terms to compact, host-owned document Evidence."""

    if not terms:
        return None
    raw_documents = {"document": raw_document}
    rows: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []
    for term in terms:
        pattern_terms = [term]
        if re.fullmatch(r"[\u3400-\u9fff]{4,}", term):
            pattern_terms.append(term[:-2])
        pattern = "|".join(
            re.escape(candidate)
            for candidate in dict.fromkeys(pattern_terms)
            if len(candidate) >= 2
        )
        focused = grep_document_evidence(
            "",
            tool_args={"pattern": pattern},
            raw_documents=raw_documents,
            captured_at=captured_at,
        )
        if focused is None and len(term) >= 4 and re.fullmatch(r"[\u3400-\u9fff]+", term):
            focused = grep_document_evidence(
                "",
                tool_args={"pattern": f"{re.escape(term[:2])}|{re.escape(term[-2:])}"},
                raw_documents=raw_documents,
                captured_at=captured_at,
            )
        if focused is None:
            continue
        visible, envelope = focused
        parsed = json.loads(visible)
        if envelope["evidenceHandle"] in {item["evidenceHandle"] for item in envelopes}:
            continue
        envelopes.append(envelope)
        rows.append(
            {
                "requestedField": term,
                "matches": parsed.get("matches"),
                "evidenceHandle": envelope["evidenceHandle"],
                "sourceTitle": envelope["source"].get("title"),
            }
        )
    if not rows:
        return None
    return (
        json.dumps(
            {
                "targetedEvidence": rows,
                "nextAction": (
                    "Use these exact evidenceHandle values for the matching claims. "
                    "Do not scan the full document or page sequentially."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        envelopes,
    )


def _document_match_score(
    raw_text: str,
    match: re.Match[str] | _DocumentMatch,
) -> tuple[int, int]:
    """Prefer numeric/table rows over an earlier prose definition match."""

    start = max(0, match.start() - 800)
    end = min(len(raw_text), match.end() + 1_600)
    context = raw_text[start:end]
    line_start = raw_text.rfind("\n", 0, match.start()) + 1
    line_end = raw_text.find("\n", match.end())
    if line_end < 0:
        line_end = len(raw_text)
    line = raw_text[line_start:line_end]
    numeric_tokens = len(re.findall(r"\d[\d,.%]*", context))
    table_markers = context.count("|") + context.count("\t")
    line_numeric_tokens = len(re.findall(r"\d[\d,.%]*", line))
    score = numeric_tokens + table_markers * 3 + line_numeric_tokens * 8
    return score, -match.start()


def _document_match_excerpt(
    raw_text: str,
    match: re.Match[str] | _DocumentMatch,
) -> str:
    """Return a bounded row/paragraph window around one exact match.

    A multi-thousand-character window made a table-header occurrence outrank
    and obscure the actual row.  A small line-aware window retains table
    headers and wrapped PDF prose while keeping the quoted evidence directly
    inspectable and usable by deterministic verification.
    """

    start = raw_text.rfind("\n", 0, match.start()) + 1
    end = raw_text.find("\n", match.end())
    if end < 0:
        end = len(raw_text)
    for _ in range(6):
        previous = raw_text.rfind("\n", 0, max(0, start - 1))
        if previous < 0:
            start = 0
            break
        start = previous + 1
    for _ in range(10):
        following = raw_text.find("\n", end + 1)
        if following < 0:
            end = len(raw_text)
            break
        end = following
    if end - start > 8_000:
        start = max(start, match.start() - 2_000)
        end = min(end, match.end() + 6_000)
    return raw_text[start:end].strip()


def _targeted_search_pattern(tool_args: dict[str, Any]) -> str:
    pattern = str(tool_args.get("pattern") or "").strip()
    if pattern:
        return pattern
    command = str(tool_args.get("command") or "").strip()
    if not command:
        return ""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    for index, token in enumerate(tokens):
        if token.rsplit("/", 1)[-1] not in {"grep", "egrep", "rg"}:
            continue
        for candidate in tokens[index + 1 :]:
            if candidate.startswith("-"):
                continue
            if candidate.startswith("/") or candidate.endswith(".txt"):
                break
            return candidate.replace(r"\|", "|")
    find_terms = re.findall(r"\.find\(\s*['\"]([^'\"]{1,120})['\"]\s*\)", command)
    return "|".join(dict.fromkeys(find_terms))


def _serialize(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(content)
