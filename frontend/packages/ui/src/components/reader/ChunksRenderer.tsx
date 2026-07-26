import { useEffect, useRef } from "react";

import type { DocumentChunk, DocumentLocation } from "./document-reader.types";

/** How long the located block stays highlighted before fading out. */
const HIGHLIGHT_MS = 2000;

function ChunkBody({ chunk }: { chunk: DocumentChunk }) {
  if (chunk.type === "image") {
    return chunk.imageUrl ? (
      <img
        src={chunk.imageUrl}
        alt=""
        className="max-w-full rounded-md border border-surface-border"
      />
    ) : null;
  }
  if (chunk.type === "table") {
    return chunk.html ? (
      // Sanitizing is the host's job (same contract as the artifact HTML
      // renderer) — the reader never sees raw upstream markup.
      <div
        className="overflow-x-auto text-sm [&_table]:w-full [&_td]:border [&_td]:border-surface-border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-surface-border [&_th]:px-2 [&_th]:py-1"
        dangerouslySetInnerHTML={{ __html: chunk.html }}
      />
    ) : null;
  }
  if (chunk.type === "heading") {
    return (
      <h2 className="text-base font-semibold text-ink-heading">{chunk.text}</h2>
    );
  }
  // paragraph / speaker — segments carry their own anchors when present.
  const body = chunk.segments?.length ? (
    <>
      {chunk.segments.map((segment) => (
        <span key={segment.id} data-segment-id={segment.id}>
          {segment.text}
        </span>
      ))}
    </>
  ) : (
    chunk.text
  );
  return (
    <p className="text-sm leading-7 text-ink-body">
      {chunk.type === "speaker" && chunk.speaker ? (
        <span className="mr-2 font-medium text-ink-heading">
          {chunk.speaker}
        </span>
      ) : null}
      {body}
    </p>
  );
}

/**
 * Structured body renderer: one anchored node per chunk, so a host can deep-link
 * to a paragraph. Locating is deliberately silent when the target is missing —
 * a stale link should land on the document, not on an error.
 */
export function ChunksRenderer({
  chunks,
  location,
}: {
  chunks: DocumentChunk[];
  location?: DocumentLocation;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const { chunkId, segmentId } = location ?? {};

  useEffect(() => {
    const root = rootRef.current;
    if (!root || (!chunkId && !segmentId)) return;
    const target = segmentId
      ? root.querySelector<HTMLElement>(
          `[data-segment-id="${CSS.escape(segmentId)}"]`,
        )
      : root.querySelector<HTMLElement>(
          `[data-chunk-id="${CSS.escape(chunkId as string)}"]`,
        );
    if (!target) return;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    // Highlight the block itself even when a segment was requested — a lit-up
    // sentence inside an un-lit paragraph reads as a rendering glitch.
    const block = target.closest<HTMLElement>("[data-chunk-id]") ?? target;
    block.classList.add("bg-brand-light", "transition-colors", "duration-700");
    const timer = window.setTimeout(
      () => block.classList.remove("bg-brand-light"),
      HIGHLIGHT_MS,
    );
    return () => window.clearTimeout(timer);
  }, [chunkId, segmentId, chunks]);

  return (
    <div ref={rootRef} className="mx-auto w-full max-w-[760px] px-6 py-5">
      {chunks.map((chunk) => (
        <div
          key={chunk.id}
          data-chunk-id={chunk.id}
          className="scroll-mt-6 rounded-md px-2 py-1.5"
        >
          <ChunkBody chunk={chunk} />
        </div>
      ))}
    </div>
  );
}
