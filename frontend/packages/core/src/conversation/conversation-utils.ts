import type { SessionEventDTO } from "../api/sessions-api";
import type {
  ConversationBlock,
  ConversationTurn,
  ConversationTurnAttachment,
  PrototypeToolCall,
} from "@valuz/shared";
import { t } from "@valuz/shared/i18n";

/* ── Helpers ───────────────────────────────────────────────── */

const parseTurnAttachments = (raw: string): ConversationTurnAttachment[] => {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.map((entry) => {
    if (!entry || typeof entry !== "object") {
      return { name: "unknown", size: 0 };
    }
    const obj = entry as Record<string, unknown>;
    const explicitName = typeof obj.name === "string" ? obj.name : undefined;
    // `source_path` is the original file; `filepath` is the legacy single-path
    // key still present on user_message events persisted before the split.
    const sourcePath =
      typeof obj.source_path === "string"
        ? obj.source_path
        : typeof obj.filepath === "string"
          ? obj.filepath
          : undefined;
    const fromPath = sourcePath
      ? (sourcePath.split("/").pop() ?? sourcePath).replace(/\.parsed\.md$/, "")
      : undefined;
    const size = typeof obj.size === "number" ? obj.size : 0;
    return {
      name: explicitName ?? fromPath ?? "unknown",
      size,
    };
  });
};

export const resolveToolKind = (name: string): PrototypeToolCall["kind"] => {
  if (name.includes("skill")) return "skill";
  if (name.includes("search") || name.includes("doc")) return "kb";
  if (name.includes("bash") || name.includes("shell")) return "bash";
  if (name.includes("file")) return "file";
  return "fetch";
};

const payloadToBlock = (payload: Record<string, string>) =>
  Object.entries(payload)
    .filter(([, value]) => value)
    .map(([key, value]) => `${key}: ${value}`)
    .join("\n");

const elapsedSince = (
  startTimestamp: number | undefined,
  endTimestamp: number | undefined,
): number | undefined => {
  if (!startTimestamp || !endTimestamp) return undefined;
  const start = new Date(startTimestamp).getTime();
  const end = new Date(endTimestamp).getTime();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) {
    return undefined;
  }
  return end - start;
};

const toMetaToolCall = (
  eventType: string,
  payload: Record<string, string>,
  seq: number,
): PrototypeToolCall | null => {
  if (eventType === "runtime.context.compiled") {
    return {
      id: `meta-compiled-${seq}`,
      kind: "kb",
      title: "runtime.context.compiled",
      subtitle: `project ${payload.project_id || "none"} · model ${payload.model || "default"}`,
      status: "success",
      output: payloadToBlock(payload),
    };
  }
  if (eventType === "runtime.engine.bound") {
    return {
      id: `meta-engine-${seq}`,
      kind: "fetch",
      title: "runtime.engine.bound",
      subtitle: `engine ${payload.engine || "unknown"}`,
      status: "success",
      output: payloadToBlock(payload),
    };
  }
  if (eventType === "runtime.engine.cost") {
    return {
      id: `meta-cost-${seq}`,
      kind: "fetch",
      title: "runtime.engine.cost",
      subtitle: "usage summary",
      status: "cached",
      output: payloadToBlock(payload),
    };
  }
  return null;
};

/* ── Turn builder ──────────────────────────────────────────── */

export const buildTurns = (events: SessionEventDTO[]): ConversationTurn[] => {
  const turns: ConversationTurn[] = [];
  let currentTurn: ConversationTurn | null = null;
  const activeToolCalls = new Map<string, PrototypeToolCall>();
  let lastUserSig: string | null = null;

  const ensureTurn = () => {
    if (!currentTurn) {
      currentTurn = {
        id: `turn-${turns.length + 1}`,
        userMessageSeq: 0,
        userText: "",
        blocks: [],
        failedMessage: null,
      };
      turns.push(currentTurn);
    }
    return currentTurn;
  };

  const matchesLastUnsealed = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    messageId: string | undefined,
  ): (ConversationBlock & { kind: "assistant" | "thinking" }) | null => {
    for (let i = turn.blocks.length - 1; i >= 0; i--) {
      const b = turn.blocks[i];
      if (b.kind === "tool") return null;
      if (b.kind === kind) {
        if (b.sealed) return null;
        if (messageId !== undefined && b.messageId !== messageId) return null;
        return b as ConversationBlock & { kind: "assistant" | "thinking" };
      }
    }
    return null;
  };

  const appendDelta = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    text: string,
    messageId: string | undefined,
  ) => {
    if (!text) return;
    const open = matchesLastUnsealed(turn, kind, messageId);
    if (open) {
      open.text += text;
      return;
    }
    const last = turn.blocks[turn.blocks.length - 1];
    if (
      last &&
      last.kind === kind &&
      last.sealed &&
      (messageId === undefined || last.messageId === messageId)
    ) {
      return;
    }
    turn.blocks.push({ kind, text, messageId, sealed: false });
  };

  const replaceWithCanonical = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    text: string,
    messageId: string | undefined,
    elapsedMs?: number,
  ) => {
    if (!text) return;
    const open = matchesLastUnsealed(turn, kind, messageId);
    if (open) {
      if (messageId != null) {
        open.text = text;
        open.sealed = true;
      } else {
        open.text += text;
      }
      if (open.kind === "thinking" && elapsedMs !== undefined) {
        open.elapsedMs = elapsedMs;
      }
      return;
    }
    turn.blocks.push(
      kind === "thinking"
        ? { kind, text, messageId, sealed: messageId != null, elapsedMs }
        : { kind, text, messageId, sealed: messageId != null },
    );
  };

  interface MetaEvent {
    type: string;
    payload: Record<string, string>;
    timestamp: number | undefined;
  }
  const metaEvents: MetaEvent[] = [];

  const seenEventSigs = new Set<string>();
  const eventSig = (type: string, p: Record<string, string>): string | null => {
    if (type === "message.user")
      return `u::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "message.assistant.delta")
      return `a::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "message.assistant.thinking")
      return `t::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "message.assistant.text_delta")
      return `xd::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "message.assistant.thinking_delta")
      return `td::${p.message_id ?? ""}::${p.text ?? ""}`;
    if (type === "tool.call.started")
      return `ts::${p.id || p.tool_use_id || p.call_id || ""}`;
    if (type === "tool.call.completed")
      return `tc::${p.id || p.tool_use_id || p.call_id || ""}`;
    if (type === "session.compaction") return `cmp::${p.message_id ?? ""}`;
    return null;
  };

  for (const envelope of events) {
    const { event_type: eventType, payload } = envelope.event;

    const sig = eventSig(eventType, payload);
    if (sig !== null) {
      if (seenEventSigs.has(sig)) continue;
      seenEventSigs.add(sig);
    }

    // Track the latest timestamp seen within the current turn so the
    // header can show ``已处理 X 秒`` even for turns that never fired a
    // thinking/tool block (a plain Q&A would otherwise have totalElapsedMs
    // = 0 and skip the header). Updated on EVERY event in the turn so
    // ``endTimestamp`` always reflects the most recent activity.
    if (currentTurn && envelope.timestamp) {
      currentTurn.endTimestamp = envelope.timestamp;
    }

    if (eventType === "message.user") {
      const userText = payload.text ?? "";
      const userSig = `${payload.message_id ?? ""}::${userText}`;
      if (userSig === lastUserSig) {
        continue;
      }
      lastUserSig = userSig;
      if (metaEvents.length && turns.length > 0) {
        const previousTurn = turns[turns.length - 1];
        for (const [i, item] of metaEvents.entries()) {
          const tool = toMetaToolCall(
            item.type,
            item.payload,
            envelope.seq + i,
          );
          if (tool) {
            const elapsedMs = elapsedSince(
              previousTurn.userTimestamp,
              item.timestamp,
            );
            previousTurn.blocks.push({ kind: "tool", tool, elapsedMs });
          }
        }
        metaEvents.length = 0;
      }
      currentTurn = {
        // ``envelope.seq`` is 0 for live SSE frames that haven't been
        // persisted yet (the kernel's broadcast sink emits them with
        // ``seq=0`` before the DB id is assigned). Two unpersisted
        // user-message frames in the same render — the broadcast +
        // its later DB-replay copy — would both produce ``turn-0`` and
        // collide on the React key, so the virtualizer would reuse
        // the same DOM node for two distinct turns. Prefer the stable
        // ``message_id`` (UUID) when available, fall back to the
        // ``envelope.seq`` only when message_id is missing.
        id: payload.message_id
          ? `turn-${payload.message_id}`
          : `turn-${envelope.seq}`,
        userMessageSeq: envelope.seq,
        userText,
        blocks: [],
        failedMessage: null,
        attachments: payload.attachments
          ? parseTurnAttachments(payload.attachments)
          : undefined,
        userTimestamp: envelope.timestamp,
      };
      turns.push(currentTurn);
      activeToolCalls.clear();
      continue;
    }

    const turn = ensureTurn();

    if (eventType === "session.compaction") {
      // A context compaction happened in this turn (``/compact`` or
      // autocompact). Push a single label-only marker block; the event's
      // raw data is intentionally NOT parsed for display. For codex's
      // ``/compact`` the "Compacted." reply is suppressed upstream, so this
      // marker is the only visible artifact of the turn.
      turn.blocks.push({ kind: "compaction", messageId: payload.message_id });
      continue;
    }

    if (eventType === "message.assistant.text_delta") {
      appendDelta(turn, "assistant", payload.text ?? "", payload.message_id);
      continue;
    }

    if (eventType === "message.assistant.thinking_delta") {
      appendDelta(turn, "thinking", payload.text ?? "", payload.message_id);
      continue;
    }

    if (eventType === "message.assistant.delta") {
      replaceWithCanonical(
        turn,
        "assistant",
        payload.text ?? "",
        payload.message_id,
      );
      continue;
    }

    if (eventType === "message.assistant.thinking") {
      replaceWithCanonical(
        turn,
        "thinking",
        payload.text ?? "",
        payload.message_id,
        elapsedSince(turn.userTimestamp, envelope.timestamp),
      );
      continue;
    }

    if (
      eventType === "runtime.context.compiled" ||
      eventType === "runtime.engine.bound" ||
      eventType === "runtime.engine.cost"
    ) {
      metaEvents.push({
        type: eventType,
        payload,
        timestamp: envelope.timestamp,
      });
      continue;
    }

    if (eventType === "tool.call.started") {
      const title = payload.name || payload.tool_name || payload.tool || "tool";
      const id =
        payload.id ||
        payload.call_id ||
        payload.tool_use_id ||
        `${title}-${envelope.seq}`;
      const card: PrototypeToolCall = {
        id,
        kind: resolveToolKind(title.toLowerCase()),
        title,
        subtitle: payload.summary || payload.input || payload.arguments || "",
        status: "running",
        input: payload.input || payload.arguments,
      };
      activeToolCalls.set(id, card);
      const startedElapsedMs = elapsedSince(
        turn.userTimestamp,
        envelope.timestamp,
      );
      turn.blocks.push({
        kind: "tool",
        tool: card,
        elapsedMs: startedElapsedMs,
      });
      continue;
    }

    if (eventType === "tool.call.completed") {
      const id =
        payload.id ||
        payload.call_id ||
        payload.tool_use_id ||
        `tool-${envelope.seq}`;
      const existing = activeToolCalls.get(id);
      const title =
        existing?.title ||
        payload.name ||
        payload.tool_name ||
        payload.tool ||
        "tool";
      const isError =
        payload.is_error === "True" ||
        payload.is_error === "true" ||
        Boolean(payload.error_message);
      const next: PrototypeToolCall = {
        id,
        kind: resolveToolKind(title.toLowerCase()),
        title,
        subtitle: existing?.subtitle ?? payload.summary ?? "",
        status: isError ? "error" : "success",
        input: existing?.input || payload.input || payload.arguments,
        output:
          payload.content ||
          payload.output ||
          payload.result ||
          payload.error_message,
      };
      const elapsedMs = elapsedSince(turn.userTimestamp, envelope.timestamp);
      const blockIndex = turn.blocks.findIndex(
        (b) => b.kind === "tool" && b.tool.id === id,
      );
      if (blockIndex >= 0) {
        turn.blocks[blockIndex] = { kind: "tool", tool: next, elapsedMs };
      } else {
        turn.blocks.push({ kind: "tool", tool: next, elapsedMs });
      }
      activeToolCalls.delete(id);
      continue;
    }

    if (eventType === "run.failed") {
      turn.failedMessage =
        payload.message ??
        t("conversation.runFailed" as Parameters<typeof t>[0]);
    }
  }

  if (metaEvents.length && turns.length > 0) {
    const lastTurn = turns[turns.length - 1];
    for (const [i, item] of metaEvents.entries()) {
      const tool = toMetaToolCall(item.type, item.payload, turns.length + i);
      if (tool) {
        const elapsedMs = elapsedSince(lastTurn.userTimestamp, item.timestamp);
        lastTurn.blocks.push({ kind: "tool", tool, elapsedMs });
      }
    }
  }

  return turns;
};
