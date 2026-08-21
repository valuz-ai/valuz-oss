/**
 * Column template for the workspace card grids (knowledge bases, projects).
 *
 * A plain `auto-fill` grid either stretches every card to fill the row or
 * leaves a ragged gutter, and a card that wide stops reading as a card. So
 * measure the container, pick the column count, and let each tile grow only
 * up to a maximum. Shared so those grids cannot drift apart again.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

export const CARD_GRID_MIN_WIDTH = 240;
const CARD_GRID_PREFERRED_MIN_WIDTH = 280;
export const CARD_GRID_MAX_WIDTH = 360;
export const CARD_GRID_GAP = 12;

export function useCardGridColumns(count: number) {
  const [node, setNode] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);

  // A callback ref, not useRef: the grid mounts and unmounts as the page
  // switches between loading, empty and list, and a callback ref re-measures
  // on its own instead of needing an effect dep for every one of those states.
  const ref = useCallback((el: HTMLDivElement | null) => setNode(el), []);

  useEffect(() => {
    if (!node) return;
    const update = () => setWidth(node.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(node);
    return () => ro.disconnect();
  }, [node]);

  const columns = useMemo(() => {
    if (width <= 0) {
      return `repeat(auto-fill, ${CARD_GRID_MAX_WIDTH}px)`;
    }

    const maxColumns = Math.max(
      1,
      Math.floor(
        (width + CARD_GRID_GAP) /
          (CARD_GRID_PREFERRED_MIN_WIDTH + CARD_GRID_GAP),
      ),
    );
    const columnCount = Math.min(count || 1, maxColumns);
    const widthAtMaxColumns =
      (width - CARD_GRID_GAP * (columnCount - 1)) / columnCount;
    const cardWidth = Math.max(
      CARD_GRID_MIN_WIDTH,
      Math.min(CARD_GRID_MAX_WIDTH, Math.floor(widthAtMaxColumns)),
    );

    return `repeat(${columnCount}, minmax(${CARD_GRID_MIN_WIDTH}px, ${cardWidth}px))`;
  }, [width, count]);

  return { ref, columns };
}
