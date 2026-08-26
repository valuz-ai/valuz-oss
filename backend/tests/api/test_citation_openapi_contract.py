from __future__ import annotations

from pathlib import Path

import yaml


def test_public_openapi_contains_complete_citation_and_research_contract() -> None:
    spec = yaml.safe_load(
        (Path(__file__).parents[3] / "api" / "openapi.yaml").read_text(encoding="utf-8")
    )
    paths = spec["paths"]
    schemas = spec["components"]["schemas"]

    assert "post" in paths["/v1/citations/resolve"]
    assert "post" in paths["/v1/document-research/sessions"]
    assert {"get", "post"} <= set(paths["/v1/document-research/documents/{document_id}/summary"])
    assert "post" in paths["/v1/document-research/share"]
    assert schemas["CitationBundleV1"]["properties"]["quality"]["$ref"].endswith(
        "CitationQualityResultV1"
    )
    assert schemas["CitationBundleV1"]["properties"]["projection"]["$ref"].endswith(
        "CitationProjectionV1"
    )
    assert {"anchors", "provenanceRegions"} <= set(schemas["CitationProjectionV1"]["properties"])
    assert "sourceOffset" in schemas["CitationAnchorV1"]["required"]
    assert "sourceOffset" in schemas["ProvenanceRegionV1"]["required"]
    assert schemas["CitationQualityResultV1"]["properties"]["claims"]["items"]["$ref"].endswith(
        "CitationClaimAuditV1"
    )
    assert set(schemas["ClaimLocationV1"]["oneOf"][2]["required"]) == {
        "kind",
        "blockIndex",
        "rowIndex",
        "columnIndex",
    }
    assert schemas["ClaimEvidenceBindingV1"]["properties"]["role"]["enum"] == [
        "primary",
        "corroborating",
        "component",
        "calculation-input",
        "conflicting",
    ]
    assert schemas["CitationSourceV1"]["properties"]["sourceCategory"] == {"type": "string"}
    assert {
        "entityId",
        "entityName",
        "metric",
        "currency",
        "scale",
        "scope",
        "basis",
        "coverage",
    } <= set(schemas["StructuredDataEvidenceV1"]["properties"])
    assert {"entityId", "entityName", "metric", "period", "scope", "basis"} <= set(
        schemas["CalculationEvidenceV1"]["properties"]
    )
    assert {
        "evidenceRegisteredCount",
        "evidenceRejectedCount",
        "evidenceOverflowReasons",
    } <= set(schemas["CitationIntegrityV1"]["properties"])
    assert (
        "claimAuditTruncated"
        in (schemas["CitationQualityResultV1"]["properties"]["metrics"]["properties"])
    )
    assert "chunkId" in schemas["PdfLocatorV1"]["properties"]
    render_variants = schemas["ResolvedCitationDocumentSource"]["properties"]["render"]["oneOf"]
    assert any(
        variant.get("properties", {}).get("kind", {}).get("enum") == ["html"]
        for variant in render_variants
    )
