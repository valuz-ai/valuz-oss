/**
 * Which files an agent has finished writing, read off the conversation turns.
 *
 * Surfaces that keep a document preview open next to a running agent use this
 * to re-read exactly the files that changed. It is deliberately cheap — paths
 * only, no diffing — because it runs on every turn update, unlike
 * ``aggregateTurnFileChanges`` which computes the per-turn diff card once a
 * turn is on screen. The two agree on which tools write: Edit, MultiEdit,
 * Write, and Codex's apply_patch.
 */

import type { ConversationTurn, PrototypeToolCall } from "../types/conversation";

/** Tool titles whose input names a file this agent just wrote. */
const WRITING_TOOLS: ReadonlySet<string> = new Set([
  "Edit",
  "MultiEdit",
  "Write",
  "apply_patch",
]);

const isString = (v: unknown): v is string => typeof v === "string";

const parseInput = (raw: string | undefined): unknown => {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

/** Paths named by one tool call's input, or an empty array. */
function pathsFromToolInput(tool: PrototypeToolCall): string[] {
  const input = parseInput(tool.input);
  if (input === null || typeof input !== "object") return [];

  if (tool.title === "apply_patch") {
    // Codex batches several files into one call.
    const changes = (input as { changes?: unknown }).changes;
    if (!Array.isArray(changes)) return [];
    return changes
      .map((raw) =>
        raw !== null && typeof raw === "object"
          ? (raw as { path?: unknown }).path
          : undefined,
      )
      .filter(isString);
  }

  const filePath = (input as { file_path?: unknown }).file_path;
  return isString(filePath) ? [filePath] : [];
}

/** One file written by one tool call. */
export interface TurnFileWrite {
  /**
   * The tool call that wrote it. Carried so a watcher can tell a write it has
   * already reacted to from a new one — editing the same file twice in a turn
   * has to refresh twice, so the path alone is not a usable identity.
   */
  toolCallId: string;
  /** Path exactly as the tool reported it (absolute, in practice). */
  path: string;
}

/**
 * Files written by tool calls that have come back, across ``turns``.
 *
 * A call still ``running`` is excluded: its bytes are not on disk yet, and the
 * next update carries it again with a settled status. An ``error`` call is
 * excluded too — nothing landed, so re-reading would only cost a round trip.
 * Order follows the turns.
 */
export function fileWritesInTurns(
  turns: readonly ConversationTurn[],
): TurnFileWrite[] {
  const writes: TurnFileWrite[] = [];
  for (const turn of turns) {
    for (const block of turn.blocks) {
      if (block.kind !== "tool") continue;
      const tool = block.tool;
      if (!WRITING_TOOLS.has(tool.title)) continue;
      if (tool.status === "running" || tool.status === "error") continue;
      for (const path of pathsFromToolInput(tool)) {
        writes.push({ toolCallId: tool.id, path });
      }
    }
  }
  return writes;
}
