import type { ReactNode } from "react";

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
    /** Already-sanitized HTML. */
    | { kind: "html"; html: string };
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
  /** One-based physical page; PDFs only. */
  page?: number;
  chunkId?: string;
  segmentId?: string;
}

export interface DocumentReaderViewProps {
  doc: DocumentSource | null;
  loading?: boolean;
  error?: string | null;
  /** Re-locates whenever the value changes. */
  location?: DocumentLocation;
  /** Left slot (AI summary, translation, …). Omitted = single column. */
  sidePanel?: ReactNode;
  onClose?: () => void;
  onReload?: () => void;
}
