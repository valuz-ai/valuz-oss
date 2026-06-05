/**
 * Turn → segment → folded-summary pipeline shared by the conversation
 * page (``ConversationTurnList``) and the activity dashboard
 * (``ActivityPage``). Both surfaces need to fold a turn's thinking + tool
 * calls into a "Called harness 5 times, Ran 6 commands"-style phrase
 * keyed off the assistant text that opened the segment — and the two MUST
 * stay byte-for-byte identical or the dashboard would tell users
 * something different from what the chat view shows.
 *
 * Icon resolution stays in the UI layer (``lucide-react`` is not a
 * ``@valuz/core`` dep); everything else — bucket keys, render templates,
 * phrase joining, segment walking — lives here.
 */
import type { ConversationTurn, PrototypeToolCall } from "../types";
import { t as _t } from "../i18n";

/** Tool category — drives both the verb phrase and (in UI) the leading
 * icon. ``mcp`` and ``other`` are the catch-all buckets for tool names
 * the categorize switch doesn't recognise. */
export type ToolCategory =
  | "search"
  | "fetch"
  | "shell"
  | "read"
  | "write"
  | "edit"
  | "skill"
  | "mcp"
  | "other";

/** Categorize a tool emission and produce a verb-phrase template for it.
 * ``key`` is what we bucket on — same key = same bucket, count rolls up.
 * ``render`` generates the natural-language phrase given the rolled-up
 * count. */
export const categorizeTool = (
  toolName: string,
): { category: ToolCategory; key: string; render: (n: number) => string } => {
  // Cross-SDK aliases: each capability maps to the same bucket / phrase
  // regardless of which runtime emitted it.
  switch (toolName) {
    case "WebSearch":
    case "web_search":
      return {
        category: "search",
        key: "websearch",
        render: (n) =>
          _t("conversation.searchedWeb" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "WebFetch":
    case "web_fetch":
    case "fetch_url":
    case "browse":
      return {
        category: "fetch",
        key: "webfetch",
        render: (n) =>
          _t("conversation.browsedPages" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "Bash":
    case "shell":
    case "bash":
    case "terminal":
    case "exec_command":
    case "execute_bash":
      return {
        category: "shell",
        key: "shell",
        render: (n) =>
          _t("conversation.ranCommands" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "Read":
    case "read_file":
    case "view_file":
      return {
        category: "read",
        key: "read",
        render: (n) =>
          _t("conversation.readFiles" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "Write":
    case "write_file":
    case "create_file":
      return {
        category: "write",
        key: "write",
        render: (n) =>
          _t("conversation.createdFiles" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "Edit":
    case "MultiEdit":
    case "edit_file":
    case "str_replace":
    case "str_replace_editor":
    case "apply_patch":
      return {
        category: "edit",
        key: "edit",
        render: (n) =>
          _t("conversation.editedFiles" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "Glob":
    case "glob":
      return {
        category: "search",
        key: "glob",
        render: (n) =>
          _t("conversation.matchedPaths" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "Grep":
    case "grep":
    case "ripgrep":
    case "rg":
      return {
        category: "search",
        key: "grep",
        render: (n) =>
          _t("conversation.searchedText" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "Skill":
    case "skill":
    case "invoke_skill":
      return {
        category: "skill",
        key: "skill",
        render: (n) =>
          _t("conversation.calledSkills" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "TodoWrite":
    case "todo_write":
    case "update_todo":
    case "update_plan":
      return {
        category: "edit",
        key: "todo",
        render: (n) =>
          _t("conversation.updatedTodos" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
    case "Task":
    case "task":
    case "delegate":
    case "dispatch_task":
    case "subagent":
      return {
        category: "other",
        key: "task",
        render: (n) =>
          _t("conversation.delegatedTasks" as Parameters<typeof _t>[0], {
            count: String(n),
          }),
      };
  }
  if (toolName.startsWith("mcp__")) {
    const parts = toolName.split("__");
    const server = parts[1] || "mcp";
    return {
      category: "mcp",
      key: `mcp:${server}`,
      render: (n) =>
        n === 1
          ? _t("conversation.usedServer" as Parameters<typeof _t>[0], {
              server,
            })
          : _t("conversation.calledServer" as Parameters<typeof _t>[0], {
              server,
              count: String(n),
            }),
    };
  }
  if (toolName.includes("/")) {
    const ns = toolName.split("/")[0] || "tool";
    return {
      category: "mcp",
      key: `ns:${ns}`,
      render: (n) =>
        n === 1
          ? _t("conversation.usedServer" as Parameters<typeof _t>[0], {
              server: ns,
            })
          : _t("conversation.calledServer" as Parameters<typeof _t>[0], {
              server: ns,
              count: String(n),
            }),
    };
  }
  return {
    category: "other",
    key: "tool:other",
    render: (n) =>
      _t("conversation.calledTools" as Parameters<typeof _t>[0], {
        count: String(n),
      }),
  };
};

export type ProcessingItem =
  | { kind: "thinking"; text: string }
  | { kind: "tool"; tool: PrototypeToolCall };

/** A "segment" pairs an assistant message (the narration / what's about
 * to happen) with the thinking + tool calls that follow it before the
 * next assistant message. ``header === null`` when the turn opens with
 * thinking/tool before any assistant text. */
export type Segment = {
  header: string | null;
  items: ProcessingItem[];
  elapsedMs?: number;
  /** Index into ``turn.blocks`` where this segment's assistant header
   * sits; ``-1`` when ``header === null``. */
  headerIdx: number;
};

/** Walk a turn's blocks and accumulate one segment at a time. Each new
 * ``assistant`` block flushes the in-flight segment and opens a new one;
 * ``thinking`` / ``tool`` blocks append to the current segment's items
 * (opening a headerless segment if none is in flight). Empty segments
 * (no header, no items) are dropped. */
export const buildSegments = (turn: ConversationTurn): Segment[] => {
  const result: Segment[] = [];
  let cur: {
    header: string | null;
    items: ProcessingItem[];
    elapsedMs: number | undefined;
    headerIdx: number;
  } | null = null;

  const flush = () => {
    if (cur === null) return;
    if (cur.header === null && cur.items.length === 0) {
      cur = null;
      return;
    }
    result.push({
      header: cur.header,
      items: cur.items,
      elapsedMs: cur.elapsedMs,
      headerIdx: cur.headerIdx,
    });
    cur = null;
  };

  for (let i = 0; i < turn.blocks.length; i += 1) {
    const block = turn.blocks[i]!;
    if (block.kind === "assistant") {
      flush();
      cur = {
        header: block.text,
        items: [],
        elapsedMs: undefined,
        headerIdx: i,
      };
      continue;
    }
    if (block.kind === "thinking") {
      if (cur === null) {
        cur = { header: null, items: [], elapsedMs: undefined, headerIdx: -1 };
      }
      if (block.text) cur.items.push({ kind: "thinking", text: block.text });
      if (block.elapsedMs !== undefined) {
        cur.elapsedMs = Math.max(cur.elapsedMs ?? 0, block.elapsedMs);
      }
      continue;
    }
    if (block.kind === "tool") {
      if (cur === null) {
        cur = { header: null, items: [], elapsedMs: undefined, headerIdx: -1 };
      }
      cur.items.push({ kind: "tool", tool: block.tool });
      if (block.elapsedMs !== undefined) {
        cur.elapsedMs = Math.max(cur.elapsedMs ?? 0, block.elapsedMs);
      }
      continue;
    }
  }
  flush();
  return result;
};

/** Build the verb-phrase for a segment's items, plus the dominant tool
 * category (used by the UI to pick a leading icon — the dashboard
 * ignores it). Thinking-only segments render as ``思考中`` / ``处理中``
 * so the strip is never empty. */
export const summarizeSegmentPhrase = (
  items: ProcessingItem[],
): { phrase: string; dominantCategory: ToolCategory } => {
  const buckets = new Map<
    string,
    { category: ToolCategory; count: number; render: (n: number) => string }
  >();
  let thinkingCount = 0;
  for (const item of items) {
    if (item.kind === "thinking") {
      thinkingCount += 1;
      continue;
    }
    const c = categorizeTool(item.tool.title);
    const existing = buckets.get(c.key);
    if (existing) {
      existing.count += 1;
    } else {
      buckets.set(c.key, {
        category: c.category,
        count: 1,
        render: c.render,
      });
    }
  }
  if (buckets.size === 0) {
    return {
      phrase:
        thinkingCount > 0
          ? _t("conversation.thinking" as Parameters<typeof _t>[0])
          : _t("conversation.processing" as Parameters<typeof _t>[0]),
      dominantCategory: "other",
    };
  }
  const categoryTotals = new Map<ToolCategory, number>();
  for (const [, b] of buckets) {
    categoryTotals.set(
      b.category,
      (categoryTotals.get(b.category) ?? 0) + b.count,
    );
  }
  let topCategory: ToolCategory = "other";
  let topCount = 0;
  for (const [cat, count] of categoryTotals) {
    if (count > topCount) {
      topCount = count;
      topCategory = cat;
    }
  }
  const phrases: string[] = [];
  for (const [, b] of buckets) phrases.push(b.render(b.count));
  return {
    phrase: phrases.join("，"),
    dominantCategory: topCategory,
  };
};
