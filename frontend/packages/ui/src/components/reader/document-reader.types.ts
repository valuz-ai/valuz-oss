import type { ReactNode } from "react";
import type {
  NormalizedRectV1,
  TextQuoteSelectorV1,
} from "@valuz/shared";

/**
 * A block of parsed document body. ``id`` is the anchor target — it lands on the
 * rendered node as ``data-chunk-id`` so hosts can deep-link to a paragraph
 * without the component knowing anything about routing.
 */
export interface DocumentChunk {
  id: string;
  type: "paragraph" | "heading" | "table" | "image" | "speaker";
  /** paragraph / heading / speaker body. */
  text?: string;
  /** Speaker name for transcript-style blocks. */
  speaker?: string;
  /** Sanitized table markup. Callers are responsible for sanitizing. */
  html?: string;
  imageUrl?: string;
  /** Optional finer-grained spans, addressable via ``location.segmentId``. */
  segments?: { id: string; text: string }[];
}

/**
 * What the reader renders. Hosts build this from whatever their backend
 * returns — the component never fetches and never guesses the shape.
 */
export interface DocumentSource {
  id: string;
  title: string;
  /** Publisher / channel / site shown under the title. */
  source?: { name: string; logoUrl?: string };
  /** Epoch milliseconds. */
  publishedAt?: number;
  render:
    | { kind: "file"; url: string; mimeType: string }
    | { kind: "chunks"; chunks: DocumentChunk[] }
    | { kind: "media"; url: string; mimeType: string }
    /**
     * Already-sanitized HTML.
     *
     * ``truncated`` says the host cut the document short — the desktop file
     * reader caps at 5 MiB. It must be carried, not assumed false: a reader
     * that shows part of a document while looking complete is worse than one
     * that refuses, because a citation can point into the part that is gone
     * and nothing on screen says so.
     */
    | { kind: "html"; html: string; truncated?: boolean }
    | { kind: "external"; url: string };
  /** Optional text index used as a quote fallback for any primary renderer. */
  chunks?: DocumentChunk[];
  documentVersion?: string | null;
  /** Header action: open the publisher's original. */
  originalUrl?: string;
  /** Header action: download the source file. */
  downloadUrl?: string;
}

/**
 * Where to scroll on open. Mirrors the frozen deep-link query contract
 * (``?page=&chunkId=&segmentId=``) — the host parses the URL, the component
 * only consumes the resolved values.
 */
export interface DocumentLocation {
  kind?: "chunk" | "html" | "pdf" | "external";
  /** One-based physical page; PDFs only. */
  page?: number;
  chunkId?: string;
  segmentId?: string;
  elementId?: string;
  cssSelector?: string;
  quote?: TextQuoteSelectorV1;
  rects?: NormalizedRectV1[];
  pageRotation?: 0 | 90 | 180 | 270;
}

export interface DocumentReaderViewProps {
  doc: DocumentSource | null;
  loading?: boolean;
  error?: string | null;
  /** Draw the reader's own panel frame. Disable when the host already owns it. */
  framed?: boolean;
  /** Re-locates whenever the value changes. */
  location?: DocumentLocation;
  /** Right research slot (AI summary, document Q&A, …). */
  sidePanel?: ReactNode;
  onClose?: () => void;
  onReload?: () => void;
  /** One automatic resolver refresh when a temporary file address fails. */
  onLoadError?: () => void;
}
