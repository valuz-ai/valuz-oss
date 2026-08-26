import { useEffect, useMemo, useRef, type ReactNode } from "react";

import type {
  DocumentChunk,
  DocumentLocation,
} from "./document-reader.types";
import { findBestTextQuote, type TextQuoteMatch } from "./text-quote";

interface LocatedChunk {
  chunkId: string;
  segmentId?: string;
  match?: TextQuoteMatch;
  status: "located-exact" | "located-fallback" | "not-found";
}

function chunkText(chunk: DocumentChunk): string {
  if (chunk.segments?.length) {
    return chunk.segments.map((segment) => segment.text).join("");
  }
  return chunk.text ?? "";
}

export function locateChunk(
  chunks: DocumentChunk[],
  location?: DocumentLocation,
): LocatedChunk | null {
  if (!location) return null;
  const chunkById = location.chunkId
    ? chunks.find((chunk) => chunk.id === location.chunkId)
    : undefined;
  const direct = location.segmentId
    ? location.chunkId
      ? chunkById?.segments?.some(
          (segment) => segment.id === location.segmentId,
        )
        ? chunkById
        : undefined
      : chunks.find((chunk) =>
          chunk.segments?.some((segment) => segment.id === location.segmentId),
        )
    : chunkById;

  if (direct) {
    const match = location.quote
      ? findBestTextQuote(chunkText(direct), location.quote)
      : null;
    return {
      chunkId: direct.id,
      segmentId: location.segmentId,
      match: match ?? undefined,
      status: "located-exact",
    };
  }

  if (location.quote) {
    for (const chunk of chunks) {
      const match = findBestTextQuote(chunkText(chunk), location.quote);
      if (match) {
        return {
          chunkId: chunk.id,
          match,
          status: "located-fallback",
        };
      }
    }
  }
  return {
    chunkId: chunks[0]?.id ?? "",
    status: "not-found",
  };
}

function highlightedText(
  text: string,
  start: number,
  end: number,
  key: string,
): ReactNode {
  if (start >= end || start < 0 || end > text.length) return text;
  return (
    <>
      {text.slice(0, start)}
      <mark
        key={key}
        data-citation-highlight="exact"
        className="rounded-sm bg-warning-light px-0.5 text-inherit ring-1 ring-warning/40"
      >
        {text.slice(start, end)}
      </mark>
      {text.slice(end)}
    </>
  );
}

function ChunkBody({
  chunk,
  located,
}: {
  chunk: DocumentChunk;
  located: LocatedChunk | null;
}) {
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
      <div
        className="overflow-x-auto text-sm [&_table]:w-full [&_td]:border [&_td]:border-surface-border [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-surface-border [&_th]:px-2 [&_th]:py-1"
        dangerouslySetInnerHTML={{ __html: chunk.html }}
      />
    ) : null;
  }

  const isLocated = located?.chunkId === chunk.id;
  const quoteMatch = isLocated ? located.match : undefined;
  if (chunk.type === "heading") {
    const text = chunk.text ?? "";
    return (
      <h2 className="text-base font-semibold text-ink-heading">
        {quoteMatch
          ? highlightedText(text, quoteMatch.start, quoteMatch.end, chunk.id)
          : text}
      </h2>
    );
  }

  const positionedSegments = chunk.segments?.map((segment, index, segments) => ({
    segment,
    start: segments
      .slice(0, index)
      .reduce((total, item) => total + item.text.length, 0),
  }));
  const body = chunk.segments?.length ? (
    <>
      {positionedSegments?.map(({ segment, start: segmentStart }) => {
        const segmentEnd = segmentStart + segment.text.length;
        const matchStart = quoteMatch
          ? Math.max(segmentStart, quoteMatch.start)
          : segmentStart;
        const matchEnd = quoteMatch
          ? Math.min(segmentEnd, quoteMatch.end)
          : segmentEnd;
        const highlightWholeSegment =
          isLocated && located?.segmentId === segment.id && !quoteMatch;
        const hasQuoteIntersection =
          Boolean(quoteMatch) && matchStart < matchEnd;
        return (
          <span key={segment.id} data-segment-id={segment.id}>
            {highlightWholeSegment ? (
              <mark
                data-citation-highlight="exact"
                className="rounded-sm bg-warning-light px-0.5 text-inherit ring-1 ring-warning/40"
              >
                {segment.text}
              </mark>
            ) : hasQuoteIntersection ? (
              <>
                {segment.text.slice(0, matchStart - segmentStart)}
                <mark
                  data-citation-highlight="exact"
                  className="rounded-sm bg-warning-light px-0.5 text-inherit ring-1 ring-warning/40"
                >
                  {segment.text.slice(
                    matchStart - segmentStart,
                    matchEnd - segmentStart,
                  )}
                </mark>
                {segment.text.slice(matchEnd - segmentStart)}
              </>
            ) : (
              segment.text
            )}
          </span>
        );
      })}
    </>
  ) : quoteMatch ? (
    highlightedText(
      chunk.text ?? "",
      quoteMatch.start,
      quoteMatch.end,
      chunk.id,
    )
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
 * Structured body renderer with stable index anchors and persistent citation
 * marks. A location change is a React state change, so old marks are removed
 * before the next target is painted rather than left behind in the DOM.
 */
export function ChunksRenderer({
  chunks,
  location,
}: {
  chunks: DocumentChunk[];
  location?: DocumentLocation;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const located = useMemo(() => locateChunk(chunks, location), [chunks, location]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root || !located?.chunkId || located.status === "not-found") return;
    const target =
      root.querySelector<HTMLElement>("[data-citation-highlight]") ??
      root.querySelector<HTMLElement>(
        `[data-segment-id="${CSS.escape(located.segmentId ?? "")}"]`,
      ) ??
      root.querySelector<HTMLElement>(
        `[data-chunk-id="${CSS.escape(located.chunkId)}"]`,
      );
    if (!target) return;
    const reduced = window.matchMedia?.(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    target.scrollIntoView({
      block: "center",
      behavior: reduced ? "auto" : "smooth",
    });
  }, [located]);

  return (
    <div
      ref={rootRef}
      data-locate-status={located?.status ?? "idle"}
      className="mx-auto w-full max-w-[760px] px-6 py-5"
    >
      {chunks.map((chunk) => {
        const wholeBlock =
          located?.chunkId === chunk.id &&
          !located.match &&
          !located.segmentId &&
          located.status !== "not-found";
        return (
          <div
            key={chunk.id}
            data-chunk-id={chunk.id}
            data-citation-block-highlight={wholeBlock ? "true" : undefined}
            className={`scroll-mt-6 rounded-md px-2 py-1.5 ${
              wholeBlock
                ? "bg-warning-light/70 ring-1 ring-warning/30"
                : ""
            }`}
          >
            <ChunkBody chunk={chunk} located={located} />
          </div>
        );
      })}
    </div>
  );
}
