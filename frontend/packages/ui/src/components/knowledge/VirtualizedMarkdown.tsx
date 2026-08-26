import { useVirtualizer } from "@tanstack/react-virtual";
import { useCallback, useMemo, useRef } from "react";

import { cn } from "../../lib/utils";
import { MarkdownContent } from "../conversation/MarkdownContent";
import { countTableRows, splitIntoUnits } from "./markdown-units";

/**
 * Below this many table rows the document is rendered whole.
 *
 * Rendering cost tracks table *cells*, and only tables produce them in bulk —
 * 142 KiB of prose renders in 261 ms and stays linear, while the same bytes as
 * a 16,000-cell table take 3,274 ms. So windowing earns its cost only once a
 * document is mostly table; 300 rows is roughly 300 ms at eight columns.
 *
 * The cost being avoided is real: text that is not in the DOM cannot be found
 * by the browser's find-in-page, and an anchor cannot scroll to a heading that
 * was never built. An ordinary document should not pay that.
 */
export const TABLE_ROWS_BEFORE_WINDOWING = 300;

/**
 * A parsed document, windowed to a screenful once it is large enough to need
 * it.
 *
 * **Why the ordinary renderer is not enough.** Its cost tracks the number of
 * DOM nodes it produces, and a spreadsheet flattened to GFM produces one per
 * cell: 3.9x a paragraph's cost at 2,000 cells, 12.6x at 16,000, while prose
 * stays linear. Nothing about markdown is slow; building sixteen thousand
 * cells and laying them out is.
 *
 * That is also why a byte cap does not work, and why one was tried and
 * removed: at 142 KiB one document is fine and another stalls, so bytes cannot
 * predict the cost. Only the node count does, and the way to lower it is to
 * stop building nodes for rows nobody is looking at.
 */
export const VirtualizedMarkdown = ({
  content,
  className,
  viewportClassName = "max-h-[70vh]",
  sizerClassName,
}: {
  content: string;
  className?: string;
  /** Bounds the scroll viewport. A *max* height rather than a height: it is
   *  what the virtualizer measures as its viewport, and it lets a short
   *  document sit at its own height instead of in a box of whitespace.
   *  Percentage heights would not do where the parent is auto-height —
   *  ``h-full`` resolves to auto, the container never scrolls, and every unit
   *  renders. A host that already has a bounded flex column passes
   *  ``min-h-0 flex-1`` instead. */
  viewportClassName?: string;
  /** Applied to the element carrying the scroll height, so a host keeps its
   *  reading column (``mx-auto max-w-[820px]``) — the windowed units are laid
   *  out inside it. */
  sizerClassName?: string;
}) => {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const units = useMemo(() => splitIntoUnits(content), [content]);
  const windowed = useMemo(
    () => countTableRows(content) > TABLE_ROWS_BEFORE_WINDOWING,
    [content],
  );

  const virtualizer = useVirtualizer({
    count: windowed ? units.length : 0,
    getScrollElement: () => scrollRef.current,
    // A first guess only — every rendered unit is measured, so a wrong
    // estimate costs a scrollbar that settles rather than a wrong layout.
    estimateSize: useCallback(
      (index: number) => Math.max(48, units[index].split("\n").length * 28),
      [units],
    ),
    overscan: 2,
  });

  if (!windowed) {
    return (
      <div className={cn("overflow-y-auto", viewportClassName)}>
        <div className={sizerClassName}>
          {/* The document is on disk in full; nothing about it is still
              arriving, so it should not be patched up as if it were
              half-written. */}
          <MarkdownContent
            content={content}
            className={className}
            mode="static"
          />
        </div>
      </div>
    );
  }

  const items = virtualizer.getVirtualItems();

  return (
    <div ref={scrollRef} className={cn("overflow-y-auto", viewportClassName)}>
      <div
        className={cn("relative w-full", sizerClassName)}
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
              mode="static"
            />
          </div>
        ))}
      </div>
    </div>
  );
};
