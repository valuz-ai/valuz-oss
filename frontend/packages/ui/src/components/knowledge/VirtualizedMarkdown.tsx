import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useMemo, useRef } from "react";

import { cn } from "../../lib/utils";
import { MarkdownContent } from "../conversation/MarkdownContent";
import { splitIntoUnits } from "./markdown-units";

/**
 * A parsed document rendered a screenful at a time.
 *
 * **Why the ordinary renderer is not enough here.** Its cost tracks the number
 * of DOM nodes it produces, and a spreadsheet flattened to GFM produces one
 * per cell. Measured on a 142 KiB document: 261 ms as prose, 3,274 ms as a
 * 16,000-cell table — and the gap widens with size (3.9x at 2,000 cells, 12.6x
 * at 16,000). Nothing about markdown is slow; building sixteen thousand cells
 * and laying them out is.
 *
 * That is also why a byte cap does not work, and why one was tried and
 * removed: at 142 KiB one document is fine and another stalls, so bytes cannot
 * predict the cost. Only the node count does, and the way to lower it is to
 * stop building nodes for rows nobody is looking at.
 *
 * Deliberately not wired into the conversation. There the markdown arrives a
 * token at a time and the reader is at the bottom of it, which is the opposite
 * of a document someone scrolls; this is scoped to the knowledge base's
 * document detail until it has earned wider use.
 */
export const VirtualizedMarkdown = ({
  content,
  className,
  viewportClassName = "max-h-[70vh]",
}: {
  content: string;
  className?: string;
  /** Bounds the scroll viewport. A *max* height rather than a height: it is
   *  what the virtualizer measures as its viewport, and it lets a short
   *  document sit at its own height instead of in a 70vh box of whitespace.
   *  Percentage heights would not do — the panel body is auto-height, so
   *  ``h-full`` resolves to auto, the container never scrolls, and every unit
   *  renders. */
  viewportClassName?: string;
}) => {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const units = useMemo(() => splitIntoUnits(content), [content]);

  const virtualizer = useVirtualizer({
    count: units.length,
    getScrollElement: () => scrollRef.current,
    // A first guess only — every rendered unit is measured, so a wrong
    // estimate costs a scrollbar that settles rather than a wrong layout.
    estimateSize: useCallback(
      (index: number) => Math.max(48, units[index].split("\n").length * 28),
      [units],
    ),
    overscan: 2,
  });

  const items = virtualizer.getVirtualItems();

  return (
    <div ref={scrollRef} className={cn("overflow-y-auto", viewportClassName)}>
      <div
        className="relative w-full"
        style={{ height: `${virtualizer.getTotalSize()}px` }}
      >
        {items.map((item) => (
          <div
            key={item.key}
            // Measured after layout rather than assumed: a table chunk and a
            // heading differ by an order of magnitude in height, and a fixed
            // row height would make the scrollbar lie.
            ref={virtualizer.measureElement}
            data-index={item.index}
            className="absolute left-0 top-0 w-full"
            style={{ transform: `translateY(${item.start}px)` }}
          >
            <MarkdownContent
              content={units[item.index]}
              className={className}
              // The document is on disk in full; nothing about it is still
              // arriving.
              mode="static"
            />
          </div>
        ))}
      </div>
    </div>
  );
};
