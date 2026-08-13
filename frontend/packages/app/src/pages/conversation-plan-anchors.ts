/**
 * Plan-anchor resolution for the conversation timeline.
 *
 * The chat timeline renders the LATEST ``plan_task`` / ``modify_plan`` tool
 * call for a task as the rich, SSE-subscribed ``LiveTaskCard`` (subtask list
 * + status + actions); every earlier plan write degrades to a compact pill.
 * This module figures out WHICH tool block is that anchor and WHICH task id
 * the card should load.
 *
 * The subtle part is the task id. The harness's plan tools
 * (``plan_task`` / ``modify_plan`` / ``get_plan``) operate *ambiently* on the
 * session's own task and do not carry a reliable ``task_id``: most calls omit
 * it, and when the model does pass one it's frequently a hallucinated ordinal
 * (e.g. ``"1"``) that 404s and leaves the card stuck on "loading". For a lead
 * session the authoritative id is the session's ``metadata.valuz.task_id``
 * (surfaced as ``task_id`` on the session shape), so we prefer that and only
 * fall back to tool-arg extraction for sessions with no task binding.
 */

import type { ConversationTurn } from "@valuz/shared";

/**
 * The payload carried by a content block, or the value itself.
 *
 * A tool's real result is nested one level down when the kernel wraps it:
 * ``[{"type": "text", "text": "{\"delivered\": true}"}]``. Callers want the
 * inner object, so unwrap the first text block that holds JSON.
 *
 * A value that is not a content-block envelope comes back untouched — some
 * tools legitimately return an array, and turning that into its first element
 * would be a different kind of wrong.
 */
function unwrapContentBlocks(value: unknown): unknown {
  if (!Array.isArray(value)) return value;
  for (const block of value) {
    if (
      block &&
      typeof block === "object" &&
      (block as { type?: unknown }).type === "text" &&
      typeof (block as { text?: unknown }).text === "string"
    ) {
      try {
        return unwrapContentBlocks(
          JSON.parse((block as { text: string }).text),
        );
      } catch {
        /* that block is prose, not a payload — keep looking */
      }
    }
  }
  return value;
}

/**
 * Best-effort JSON extraction from a kernel tool input/output string.
 *
 * Tool payloads come through the kernel as raw JSON, or wrapped in a
 * content-block envelope — historically a Python repr
 * (``[{'type': 'text', 'text': '{...}'}]``), nowadays valid JSON
 * (``[{"type": "text", "text": "{...}"}]``).
 *
 * That second form is why the unwrap exists. The Python repr fails
 * ``JSON.parse`` and fell through to the scanner below, which found the inner
 * object by accident; the JSON envelope PARSES, so the parse "succeeded" and
 * handed callers the array. Every field they read off it was ``undefined`` —
 * an inject that had been delivered rendered as "指令未送达（unknown）",
 * because `delivered` and `reason` were both missing rather than false.
 */
export function extractToolOutputJson(output: string): unknown | null {
  try {
    return unwrapContentBlocks(JSON.parse(output));
  } catch {
    /* fall through */
  }
  const start = output.indexOf('{"');
  if (start < 0) return null;
  let depth = 0;
  let end = -1;
  let inString = false;
  let escape = false;
  for (let i = start; i < output.length; i++) {
    const ch = output[i];
    if (inString) {
      if (escape) {
        escape = false;
      } else if (ch === "\\") {
        escape = true;
      } else if (ch === '"') {
        inString = false;
      }
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === "{") {
      depth++;
    } else if (ch === "}") {
      depth--;
      if (depth === 0) {
        end = i + 1;
        break;
      }
    }
  }
  if (end < 0) return null;
  try {
    return JSON.parse(output.slice(start, end));
  } catch {
    return null;
  }
}

const PLAN_WRITE_NAMES = new Set(["plan_task", "modify_plan"]);

function matchesPlanWrite(name: string): boolean {
  if (PLAN_WRITE_NAMES.has(name)) return true;
  for (const k of PLAN_WRITE_NAMES) {
    if (name.endsWith(`__${k}`)) return true;
  }
  return false;
}

export interface PlanAnchors {
  /** tool_use_id → task_id for the single rich LiveTaskCard anchor per task. */
  taskByRichTool: Map<string, string>;
}

/**
 * Resolve which plan-write tool block anchors the rich LiveTaskCard for each
 * task, and the task id that card should load.
 *
 * @param turns          The built conversation turns.
 * @param sessionTaskId  The viewing session's authoritative task id
 *                       (``session.metadata.valuz.task_id``), or null for a
 *                       session that isn't bound to a task.
 */
export function computePlanAnchors(
  turns: readonly ConversationTurn[],
  sessionTaskId: string | null,
): PlanAnchors {
  const richToolByTask = new Map<string, string>();
  const taskByRichTool = new Map<string, string>();
  for (const turn of turns) {
    for (const block of turn.blocks) {
      if (block.kind !== "tool") continue;
      const tool = block.tool;
      const name = tool.title || "";
      if (!matchesPlanWrite(name)) continue;
      // Prefer the session's authoritative task id (lead session). Only
      // fall back to the (unreliable) tool args when the session has no
      // task binding — plan_task / modify_plan responses don't echo
      // task_id; it lives in the input arg, and we check output too in
      // case a future runtime echoes it.
      let tid: string | undefined = sessionTaskId ?? undefined;
      if (!tid && tool.input) {
        const parsedIn = extractToolOutputJson(tool.input) as {
          task_id?: string;
        } | null;
        tid = parsedIn?.task_id;
      }
      if (!tid && tool.output) {
        const parsed = extractToolOutputJson(tool.output) as {
          task_id?: string;
        } | null;
        tid = parsed?.task_id;
      }
      if (!tid) continue;
      // Last write wins — drop any earlier tool_use_id for this task.
      const prev = richToolByTask.get(tid);
      if (prev) taskByRichTool.delete(prev);
      richToolByTask.set(tid, tool.id);
      taskByRichTool.set(tool.id, tid);
    }
  }
  return { taskByRichTool };
}
