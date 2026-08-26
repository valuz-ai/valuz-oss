/**
 * Canonical citation sidecar stored under
 * ``Message.metadata.citation_bundle``.
 *
 * Display numbers such as ``[1]`` are deliberately absent: they are derived
 * per message from the first appearance of ``citation://<citationId>`` links.
 */
export interface CitationBundleV1 {
  version: 1;
  citations: CitationRefV1[];
  /** Post-publish projection from Runtime evidence links to trusted ids. */
  projection?: {
    evidenceHandleToCitationId: Record<string, string>;
    /** Render-only anchors for deterministic post-publish bindings. */
    anchors?: CitationAnchorV1[];
    /** Rectangular table scopes derived from cell lineage. */
    provenanceRegions?: ProvenanceRegionV1[];
    /** Render-only, fail-closed corrections against immutable source text. */
    textCorrections?: CitationTextCorrectionV1[];
  };
  integrity?: CitationIntegrityV1;
  quality?: CitationQualityResultV1;
}

export interface CitationTextCorrectionV1 {
  claimId: string;
  citationId: string;
  sourceStart: number;
  sourceEnd: number;
  originalText: string;
  replacementText: string;
  reason: "structured-value-conflict" | "document-value-conflict";
}

export interface CitationAnchorV1 {
  citationId: string;
  claimId: string;
  location: ClaimLocationV1;
  /** Raw Markdown insertion offset; the stored assistant text stays immutable. */
  sourceOffset: number;
  origin: "explicit" | "auto-bound" | "equivalent-claim";
}

export interface ProvenanceRegionV1 {
  regionId: string;
  /** Evidence-lineage group; several disconnected regions may share it. */
  claimGroupId?: string;
  blockIndex: number;
  rowStart: number;
  rowEnd: number;
  columnStart: number;
  columnEnd: number;
  citationIds: string[];
  anchor: ClaimLocationV1;
  /** Raw Markdown insertion offset of the terminal table cell. */
  sourceOffset: number;
}

export interface CitationRefV1 {
  citationId: string;
  source: CitationSourceV1;
  evidence: CitationEvidenceV1;
  locator?: CitationLocatorV1;
  resolutionStatus?:
    | "ready"
    | "stale"
    | "missing"
    | "forbidden"
    | "degraded";
  annotations?: Record<string, unknown>;
}

export interface CitationSourceV1 {
  sourceId: string;
  providerId: string;
  documentId?: string;
  documentVersion?: string;
  sourceType:
    | "document"
    | "web"
    | "dataset"
    | "tool-result"
    | "conversation";
  /** Stable provider category used by edition quality policies. */
  sourceCategory?: string;
  mimeType?: string;
  title: string;
  organization?: string;
  author?: string;
  publishedAt?: string;
  retrievedAt: string;
  canonicalUrl?: string;
}

export interface TextCitationEvidenceV1 {
  kind: "text";
  quote: string;
  snippet: string;
  prefix?: string;
  suffix?: string;
  language?: string;
  capturedAt: string;
  contentHash?: string;
}

export interface StructuredDataEvidenceV1 {
  kind: "structured-data";
  datasetId: string;
  toolName: string;
  recordKey?: string;
  entityId?: string;
  entityName?: string;
  field: string;
  metric?: string;
  value: string | number | boolean | null;
  unit?: string;
  currency?: string;
  scale?: string | number;
  period?: string;
  asOf?: string;
  scope?: string;
  basis?: string;
  capturedAt: string;
  toolTraceRef?: string;
  /** Authoritative returned data window; never inferred from today's date. */
  coverage?: {
    start?: string;
    end?: string;
  };
}

export interface CalculationEvidenceV1 {
  kind: "calculation";
  expression: string;
  inputs: Array<{
    name: string;
    citationId: string;
    value: string | number;
    unit?: string;
  }>;
  result: string | number;
  unit?: string;
  rounding?: string;
  calculatedAt: string;
  entityId?: string;
  entityName?: string;
  metric?: string;
  period?: string;
  scope?: string;
  basis?: string;
}

export type CitationEvidenceV1 =
  | TextCitationEvidenceV1
  | StructuredDataEvidenceV1
  | CalculationEvidenceV1;

export interface TextQuoteSelectorV1 {
  exact: string;
  prefix?: string;
  suffix?: string;
}

export interface NormalizedRectV1 {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ChunkLocatorV1 {
  kind: "chunk";
  chunkId: string;
  segmentId?: string;
  quote?: TextQuoteSelectorV1;
}

export interface HtmlLocatorV1 {
  kind: "html";
  chunkId?: string;
  elementId?: string;
  cssSelector?: string;
  quote: TextQuoteSelectorV1;
}

export interface PdfLocatorV1 {
  kind: "pdf";
  page: number;
  /** Authority chunk id retained for resolver diagnostics and quote fallback. */
  chunkId?: string;
  rects?: NormalizedRectV1[];
  quote?: TextQuoteSelectorV1;
  coordinateSpace?: "viewport-normalized-v1";
  pageRotation?: 0 | 90 | 180 | 270;
}

export interface ExternalLocatorV1 {
  kind: "external";
  fragment?: string;
}

export type CitationLocatorV1 =
  | ChunkLocatorV1
  | HtmlLocatorV1
  | PdfLocatorV1
  | ExternalLocatorV1;

export interface CitationIntegrityV1 {
  status: "passed" | "repaired" | "degraded" | "not-required";
  unknownCitationIds: string[];
  unusedCitationIds: string[];
  missingLocatorCitationIds: string[];
  repairAttempts: number;
  policyRevision: string;
  evidenceRegisteredCount?: number;
  evidenceCollectionCount?: number;
  evidenceAddressRequestedCount?: number;
  evidenceMaterializedCount?: number;
  evidenceMaterializationRejectedCount?: number;
  evidenceRejectedCount?: number;
  evidenceOverflowReasons?: string[];
  publicationBlocked?: boolean;
}

export interface CitationQualityIssueV1 {
  code: string;
  layer: "L0" | "L1" | "L2" | "L3" | "L4" | "L5" | string;
  severity: "degraded" | "unverified" | string;
  citationIds?: string[];
  claimId?: string;
  claim?: TextQuoteSelectorV1;
  location?: ClaimLocationV1;
  policyLayer?: "oss" | "commercial" | "distribution" | string;
}

export interface CitationPolicyLayerV1 {
  layer: "oss" | "commercial" | "distribution" | string;
  policy_id: string;
  revision: string;
  status: "active" | "unavailable" | string;
}

export type ClaimLocationV1 =
  | {
      kind: "text";
      blockIndex: number;
      start: number;
      end: number;
      sourceStart?: number;
      sourceEnd?: number;
    }
  | {
      kind: "list-item";
      blockIndex: number;
      itemIndex: number;
      start: number;
      end: number;
      sourceStart?: number;
      sourceEnd?: number;
    }
  | {
      kind: "table-cell";
      blockIndex: number;
      rowIndex: number;
      columnIndex: number;
      sourceStart?: number;
      sourceEnd?: number;
    }
  | { kind: "legacy" };

export interface ClaimEvidenceBindingV1 {
  citationId: string;
  role:
    | "primary"
    | "corroborating"
    | "component"
    | "calculation-input"
    | "conflicting";
  supportStatus:
    | "supported"
    | "partially-supported"
    | "contradicted"
    | "not-found";
}

export interface CitationClaimAuditV1 {
  claimId: string;
  exact: string;
  segmentIndex: number;
  citationRequired: boolean;
  auditPriority?: "critical" | "supporting" | "optional";
  auditSelected?: boolean;
  selectionReasons?: string[];
  claimGroupId?: string;
  citationIds: string[];
  bindings?: ClaimEvidenceBindingV1[];
  status:
    | "passed"
    | "auto-bound"
    | "corrected"
    | "repaired"
    | "degraded"
    | "unverified"
    | "unsupported"
    | "not-selected";
  issueCodes: string[];
  location?: ClaimLocationV1;
  correction?: {
    originalText: string;
    replacementText: string;
    reason: "structured-value-conflict" | "document-value-conflict";
    citationId: string;
  };
}

export interface CitationQualityResultV1 {
  policyId: string;
  policyRevision: string;
  policyLayers?: CitationPolicyLayerV1[];
  mode: "required-on-evidence" | "strict-domain" | string;
  status: "passed" | "unverified" | "degraded";
  /** Bounded critical-Claim outcome; does not describe whole-answer truth. */
  auditOutcome?: "passed" | "partial" | "needs-review";
  publishStatus: "ready" | "draft-only" | "blocked" | string;
  layers: Record<string, "passed" | "degraded" | string>;
  issues: CitationQualityIssueV1[];
  claims?: CitationClaimAuditV1[];
  extractorRevision?: string;
  selectorRevision?: string;
  verifierRevision?: string;
  metrics: {
    citationCount: number;
    claimDetectedCount?: number;
    claimCitationRequiredCount?: number;
    claimBoundCount?: number;
    claimAutoBoundCount?: number;
    claimUnsupportedCount?: number;
    claimSemanticMismatchCount?: number;
    claimAmbiguousCount?: number;
    correctedClaimCount?: number;
    claimAuditTruncated?: boolean;
    criticalClaimSelectedCount?: number;
    criticalClaimSupportedCount?: number;
    criticalClaimUnresolvedCount?: number;
    criticalConfirmedConflictCount?: number;
    optionalClaimObservedCount?: number;
    unsourcedClaimCount: number;
    unverifiedClaimCount: number;
    tierCounts: Record<string, number>;
  };
}

export interface EvidenceItemV1 {
  evidenceHandle: string;
  source: CitationSourceV1;
  evidence: CitationEvidenceV1;
  locator?: CitationLocatorV1;
}

export interface DocumentSummaryArtifactV1 {
  version: 1;
  summary_id: string;
  document_id: string;
  document_version: string;
  status: "pending" | "ready" | "degraded" | "failed" | "stale";
  profile: "brief" | "detailed" | string;
  content: string;
  citation_bundle: CitationBundleV1;
  generated_at: string | null;
  model_id: string | null;
  prompt_revision: string;
  policy_revision: string;
  research_session_id: string | null;
  message_id: string | null;
  error_message: string | null;
}

export interface DocumentResearchSessionV1 {
  session_id: string;
  purpose: "document-research";
  document_ids: string[];
  document_versions: string[];
  source_scope: "locked";
  origin_session_id: string | null;
  origin_message_id: string | null;
  reused: boolean;
}

export interface SharedResearchMessageV1 {
  target_session_id: string;
  message_id: string;
  source_session_id: string;
  source_message_id: string;
}

export interface OpenCitationInput {
  messageId?: string;
  citationId: string;
}

export type CitationResolutionStatus =
  | "ready"
  | "stale"
  | "missing"
  | "forbidden"
  | "degraded";

export interface ResolvedCitationFileAddress {
  kind: "local" | "remote";
  absPath: string | null;
  url: string | null;
  expiresAt: number | null;
}

export interface ResolvedCitationChunk {
  id: string;
  type: "paragraph" | "heading" | "table" | "image" | "speaker";
  text?: string;
  speaker?: string;
  html?: string;
  imageUrl?: string;
  segments?: { id: string; text: string }[];
}

export interface ResolvedCitationDocumentSource {
  id: string;
  title: string;
  source?: { name: string; logoUrl?: string };
  render:
    | {
        kind: "file";
        mimeType: string;
        address: ResolvedCitationFileAddress;
      }
    | {
        /** Trusted backend-fetched HTML; sanitized again by the app host. */
        kind: "html";
        html: string;
      }
    | { kind: "chunks"; chunks: ResolvedCitationChunk[] }
    | { kind: "external"; url: string };
  /** Locator index independent of the primary PDF/HTML/chunks renderer. */
  chunks?: ResolvedCitationChunk[];
  documentVersion?: string | null;
  originalUrl?: string | null;
}

export interface ResolveCitationResult {
  document: ResolvedCitationDocumentSource | null;
  effective_locator: CitationLocatorV1 | null;
  status: CitationResolutionStatus;
  fallback_reason: string | null;
  canonical_url: string | null;
}
