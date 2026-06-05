import { createPatch, diffLines } from "diff";
import type { ConversationTurn } from "@valuz/shared";

export interface TurnDiffFileChange {
  /** Absolute path the agent passed in. */
  file_path: string;
  additions: number;
  deletions: number;
  /** Concatenation of unified-diff blocks for every contributing tool
   *  call against this file (in chronological order). */
  unified_diff: string;
  /** True when at least one of the contributing tool calls returned
   *  with status === "error". The card surfaces this so the user knows
   *  the intended change may not have landed on disk. */
  has_error: boolean;
}

export interface TurnDiffSummary {
  changes: TurnDiffFileChange[];
  total_additions: number;
  total_deletions: number;
}

interface EditEntry {
  old_string: string;
  new_string: string;
}

interface ToolInputEdit {
  file_path?: unknown;
  old_string?: unknown;
  new_string?: unknown;
}

interface ToolInputMultiEdit {
  file_path?: unknown;
  edits?: unknown;
}

interface ToolInputWrite {
  file_path?: unknown;
  content?: unknown;
}

const isString = (v: unknown): v is string => typeof v === "string";

const parseInput = (raw: string | undefined): unknown => {
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
};

const countAddsAndDels = (
  oldStr: string,
  newStr: string,
): { additions: number; deletions: number } => {
  let additions = 0;
  let deletions = 0;
  for (const part of diffLines(oldStr, newStr)) {
    if (part.added) additions += part.count ?? 0;
    else if (part.removed) deletions += part.count ?? 0;
  }
  return { additions, deletions };
};

const editEntriesFromTool = (
  toolTitle: string,
  input: unknown,
): { file_path: string; entries: EditEntry[] } | null => {
  if (input === null || typeof input !== "object") return null;

  if (toolTitle === "Edit") {
    const e = input as ToolInputEdit;
    if (
      !isString(e.file_path) ||
      !isString(e.old_string) ||
      !isString(e.new_string)
    ) {
      return null;
    }
    return {
      file_path: e.file_path,
      entries: [{ old_string: e.old_string, new_string: e.new_string }],
    };
  }

  if (toolTitle === "MultiEdit") {
    const e = input as ToolInputMultiEdit;
    if (!isString(e.file_path) || !Array.isArray(e.edits)) return null;
    const entries: EditEntry[] = [];
    for (const item of e.edits) {
      if (item === null || typeof item !== "object") continue;
      const sub = item as ToolInputEdit;
      if (!isString(sub.old_string) || !isString(sub.new_string)) continue;
      entries.push({ old_string: sub.old_string, new_string: sub.new_string });
    }
    if (entries.length === 0) return null;
    return { file_path: e.file_path, entries };
  }

  if (toolTitle === "Write") {
    const e = input as ToolInputWrite;
    if (!isString(e.file_path) || !isString(e.content)) return null;
    // Treat Write as a full-file replacement against an empty baseline.
    // The aggregator can't reach the disk to know whether the file
    // existed before; counting every line as added is consistent with
    // how Cursor / VS Code render a brand-new file.
    return {
      file_path: e.file_path,
      entries: [{ old_string: "", new_string: e.content }],
    };
  }

  return null;
};

interface PerFileAccumulator {
  file_path: string;
  additions: number;
  deletions: number;
  patches: string[];
  has_error: boolean;
}

/**
 * Walk a turn's tool blocks and produce an aggregated file-change
 * summary. Only Edit / MultiEdit / Write tool calls participate; every
 * other tool name is ignored so the renderer still shows them through
 * the generic per-tool card. Returns ``null`` when the turn made no
 * file changes — callers can use that as the "render no card" signal.
 */
export const aggregateTurnFileChanges = (
  turn: ConversationTurn,
): TurnDiffSummary | null => {
  const byFile = new Map<string, PerFileAccumulator>();

  for (const block of turn.blocks) {
    if (block.kind !== "tool") continue;
    const tool = block.tool;
    const title = tool.title;
    if (title !== "Edit" && title !== "MultiEdit" && title !== "Write") {
      continue;
    }
    const parsed = parseInput(tool.input);
    const extracted = editEntriesFromTool(title, parsed);
    if (!extracted) continue;

    const isError = tool.status === "error";

    let acc = byFile.get(extracted.file_path);
    if (!acc) {
      acc = {
        file_path: extracted.file_path,
        additions: 0,
        deletions: 0,
        patches: [],
        has_error: false,
      };
      byFile.set(extracted.file_path, acc);
    }

    for (const entry of extracted.entries) {
      const { additions, deletions } = countAddsAndDels(
        entry.old_string,
        entry.new_string,
      );
      acc.additions += additions;
      acc.deletions += deletions;
      // ``createPatch`` produces a standard unified diff with a header.
      // We strip the leading file header (``Index:`` + ``===``) and
      // keep only ``---``/``+++``/``@@`` lines so concatenating multiple
      // patches per file stays compact and human-readable.
      const patch = createPatch(
        extracted.file_path,
        entry.old_string,
        entry.new_string,
        "",
        "",
      );
      acc.patches.push(patch);
    }
    if (isError) acc.has_error = true;
  }

  if (byFile.size === 0) return null;

  let totalAdditions = 0;
  let totalDeletions = 0;
  const changes: TurnDiffFileChange[] = [];
  for (const acc of byFile.values()) {
    totalAdditions += acc.additions;
    totalDeletions += acc.deletions;
    changes.push({
      file_path: acc.file_path,
      additions: acc.additions,
      deletions: acc.deletions,
      unified_diff: acc.patches.join("\n"),
      has_error: acc.has_error,
    });
  }

  return {
    changes,
    total_additions: totalAdditions,
    total_deletions: totalDeletions,
  };
};
