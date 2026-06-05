import { useRef } from "react";
import type { ConversationTurn } from "@valuz/shared";

export function useStableTurns(
  nextTurns: ConversationTurn[],
): ConversationTurn[] {
  const prevRef = useRef<ConversationTurn[]>([]);
  const prev = prevRef.current;

  const stable = nextTurns.map((next, i) => {
    const old = prev[i];
    if (!old || old.id !== next.id) return next;
    if (old.blocks.length !== next.blocks.length) return next;
    if (old.failedMessage !== next.failedMessage) return next;
    // Compare EVERY block, not just the last one. Mid-turn updates often
    // land on a non-last block — e.g. a tool block transitioning
    // running→done after a thinking block has already been appended after
    // it, or in-place text growth on an earlier block. Only inspecting the
    // last block let those changes slip through, so the memoized turn
    // reference stayed stale and the row didn't re-render until the block
    // count changed or the turn ended (the "everything shows up only at the
    // very end" regression).
    for (let b = 0; b < next.blocks.length; b += 1) {
      const oldB = old.blocks[b];
      const nextB = next.blocks[b];
      if (!oldB || oldB.kind !== nextB.kind) return next;
      if (oldB.kind === "tool" && nextB.kind === "tool") {
        if (
          oldB.tool.status !== nextB.tool.status ||
          oldB.tool.output !== nextB.tool.output
        ) {
          return next;
        }
      } else if (
        (oldB as { text?: string }).text !== (nextB as { text?: string }).text
      ) {
        return next;
      }
    }
    return old;
  });

  prevRef.current = stable;
  return stable;
}
