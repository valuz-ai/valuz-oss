"""Canonical immutable identities, independent of message-local citation IDs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from valuz_agent.facade.evidence import SealedMessageEvidence, canonical_citation_hash

MAX_PAYLOAD_BYTES = 512 * 1024


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def bounded_payload(value: Any) -> None:
    if len(canonical_json(value)) > MAX_PAYLOAD_BYTES:
        raise ValueError("evidence_payload_too_large")


def sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def source_version(source: dict[str, Any]) -> str | None:
    for key in ("documentVersion", "sourceVersion", "dataVersion", "version"):
        value = source.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def snapshot_values(sealed: SealedMessageEvidence) -> dict[str, Any]:
    """Called only after the command validates owner and canonical source proof.

    Checking a hash prevents accidental mutation; it does not make an arbitrary
    browser/model payload a trusted seal. No route exposes this function.
    """
    bounded_payload(sealed.canonical_payload())
    if (
        sealed.bundle_version != 1
        or canonical_citation_hash(sealed.canonical_payload()) != sealed.citation_hash
    ):
        raise ValueError("evidence_canonical_seal_changed")
    for key in ("sourceType", "providerId", "sourceId"):
        if not isinstance(sealed.source.get(key), str) or not sealed.source[key]:
            raise ValueError("evidence_source_reference_incomplete")
    if not isinstance(sealed.evidence.get("kind"), str) or not sealed.evidence["kind"]:
        raise ValueError("evidence_kind_missing")
    annotations = sealed.annotations
    containers = [sealed.evidence] + [
        annotations[key]
        for key in ("temporal", "semantics")
        if isinstance(annotations.get(key), dict)
    ]

    def pick(*keys: str) -> str | None:
        for container in containers:
            for key in keys:
                value = container.get(key)
                if value is not None and isinstance(value, (str, int, float)):
                    return str(value)
        return None

    temporal = {
        "as_of": pick("asOf", "as_of", "capturedAt", "calculatedAt"),
        "period": pick("period", "coverage"),
        "unit": pick("unit"),
        "currency": pick("currency"),
        "scale": pick("scale"),
        "basis": pick("basis", "scope"),
    }
    content_hash = sha256(canonical_json(sealed.evidence))
    identity = {
        "source_type": sealed.source["sourceType"],
        "provider_id": sealed.source["providerId"],
        "source_id": sealed.source["sourceId"],
        "source_version": source_version(sealed.source),
        "locator": sealed.locator,
        "evidence_kind": sealed.evidence["kind"],
        "content_hash": content_hash,
        **temporal,
    }
    # Ordinary legacy captures retain exactly the same fingerprint. Derived
    # captures include their declared method/input context as well as output.
    derivation = {
        key: annotations[key]
        for key in ("derivation", "methodRef", "inputVersionRefs")
        if annotations.get(key) is not None
    }
    fingerprint = sha256(
        canonical_json({**identity, **({"derivation": derivation} if derivation else {})})
    )
    return {
        **identity,
        "fingerprint": fingerprint,
        "snapshot": sealed.evidence,
        "source_title": str(sealed.source.get("title") or ""),
        "canonical_url": str(sealed.source["canonicalUrl"])
        if sealed.source.get("canonicalUrl")
        else None,
        "status": "ready",
    }
