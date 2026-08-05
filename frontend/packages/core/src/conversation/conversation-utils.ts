import type { SessionEventDTO } from "../api/sessions-api";
import type {
  CitationBundleV1,
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

const parseCitationBundle = (
  raw: string | undefined,
): CitationBundleV1 | undefined => {
  if (!raw) return undefined;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== "object") return undefined;
    const candidate = parsed as Record<string, unknown>;
    if (candidate.version !== 1 || !Array.isArray(candidate.citations)) {
      return undefined;
    }
    const ids = new Set<string>();
    for (const citation of candidate.citations) {
      if (!citation || typeof citation !== "object") return undefined;
      const record = citation as Record<string, unknown>;
      const id = record.citationId;
      if (typeof id !== "string" || !id || ids.has(id)) return undefined;
      if (!isCitationSource(record.source) || !isCitationEvidence(record.evidence)) {
        return undefined;
      }
      ids.add(id);
    }
    return parsed as CitationBundleV1;
  } catch {
    return undefined;
  }
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value) && typeof value === "object" && !Array.isArray(value);

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === "string" && Boolean(value.trim());

const isCitationSource = (value: unknown): boolean => {
  if (!isRecord(value)) return false;
  return (
    isNonEmptyString(value.sourceId) &&
    isNonEmptyString(value.providerId) &&
    isNonEmptyString(value.title) &&
    isNonEmptyString(value.retrievedAt) &&
    typeof value.sourceType === "string" &&
    ["document", "web", "dataset", "tool-result", "conversation"].includes(
      value.sourceType,
    )
  );
};

const isCitationEvidence = (value: unknown): boolean => {
  if (!isRecord(value)) return false;
  if (value.kind === "text") {
    return (
      typeof value.quote === "string" &&
      typeof value.snippet === "string" &&
      isNonEmptyString(value.capturedAt)
    );
  }
  if (value.kind === "structured-data") {
    return (
      isNonEmptyString(value.datasetId) &&
      isNonEmptyString(value.toolName) &&
      isNonEmptyString(value.field) &&
      isNonEmptyString(value.capturedAt) &&
      (value.value === null ||
        ["string", "number", "boolean"].includes(typeof value.value))
    );
  }
  if (value.kind === "calculation") {
    return (
      isNonEmptyString(value.expression) &&
      Array.isArray(value.inputs) &&
      value.inputs.length > 0 &&
      value.inputs.every(
        (input) =>
          isRecord(input) &&
          isNonEmptyString(input.name) &&
          isNonEmptyString(input.citationId) &&
          ["string", "number"].includes(typeof input.value),
      ) &&
      isNonEmptyString(value.calculatedAt) &&
      ["string", "number"].includes(typeof value.result)
    );
  }
  return false;
};

export const resolveToolKind = (name: string): PrototypeToolCall["kind"] => {
  if (name.includes("skill")) return "skill";
  if (name.includes("search") || name.includes("doc")) return "kb";
  if (name.includes("bash") || name.includes("shell")) return "bash";
  if (name.includes("file")) return "file";
  return "fetch";
};

/**
 * Wire marker for a frame that carries a stream's absolute state instead
 * of the next increment. The kernel emits one per open stream when a
 * client joins mid-turn — the deltas sent before it connected are never
 * persisted, so history can't reach them. Backend counterpart:
 * ``kernel/src/core/live_partial.py::SNAPSHOT_FLAG``.
 *
 * Carried as a string because the SSE payload is a flat string map.
 */
export const LIVE_SNAPSHOT_FLAG = "live_snapshot";

export const isLiveSnapshot = (payload: Record<string, string>): boolean =>
  payload[LIVE_SNAPSHOT_FLAG] === "true";

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

/**
 * Classify a ``stop_reason`` / ``category`` value as a user cancel vs a
 * runtime/system interruption vs neither.
 *
 * Both render as a quiet grey line (not an ``ErrorMessageCard``), but they carry
 * DIFFERENT labels: ``user_interrupt`` is the user pressing Stop; ``interrupted``
 * is the agent subprocess being torn down / crashing mid-turn (see the kernel's
 * ``is_runtime_interruption``). Collapsing the two made a runtime crash render as
 * "用户取消了当前对话" — blaming the user for a system failure. Keep them apart.
 *
 * Accepts the bare string or a serialized ``{type|category}`` object.
 */
const interruptKind = (value: unknown): "user" | "runtime" | null => {
  const classify = (s: string): "user" | "runtime" | null => {
    const n = s.trim().toLowerCase();
    if (n === "user_interrupt") return "user";
    if (n === "interrupted") return "runtime";
    return null;
  };
  if (typeof value !== "string") return null;
  const direct = classify(value);
  if (direct) return direct;
  try {
    const parsed = JSON.parse(value) as unknown;
    if (!parsed || typeof parsed !== "object") return null;
    const obj = parsed as Record<string, unknown>;
    for (const key of ["type", "category"] as const) {
      const v = obj[key];
      if (typeof v === "string") {
        const c = classify(v);
        if (c) return c;
      }
    }
    return null;
  } catch {
    return null;
  }
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

/* ── Event-window merge ────────────────────────────────────── */

/**
 * Merge a fetched transcript window into the live ``events`` array without
 * disturbing what's already there.
 *
 * ``buildTurns`` consumes events in ARRAY ORDER, and the array mixes entries
 * from TWO independent seq spaces: history rows (durable-store seq — what the
 * ``incoming`` window carries), live persisted frames (the kernel's LOCAL
 * seq — numerically unrelated), and live unpersisted deltas (``seq === 0``).
 * Cross-segment identity is ``event_uid`` (present on persisted events from
 * both paths; absent on live-only deltas and legacy rows). The merge rules:
 *
 * - Dedup keys on ``event_uid`` when the incoming row has one; uid-less
 *   incoming rows keep the historical seq-based dedup — but only against
 *   uid-less prev rows (a uid-bearing prev row may be a live frame whose
 *   kernel seq coincidentally collides with a history seq).
 * - Missing rows keep the incoming window's own (history-seq) order.
 * - Positioning is ANCHOR-based: a prev entry that is also present in the
 *   incoming window (matched by uid, or by seq for uid-less rows) pins a
 *   position in history space; each missing row is inserted before the first
 *   anchor whose history seq exceeds it. Live entries that are not anchors
 *   stay glued exactly where they arrived — never re-sorted by seq across
 *   segments.
 * - With NO anchors at all, fall back to the legacy algorithm over the
 *   uid-less persisted prev rows only (same-space by construction); when
 *   prev has none of those either (e.g. a resume whose prev is purely the
 *   in-flight live tail), the whole window goes to the FRONT — history must
 *   render before the live tail.
 * - Missing rows newer than every anchor land at the tail — the same
 *   position the live stream would have delivered them to.
 */
export const mergeEventWindow = (
  prev: SessionEventDTO[],
  incoming: SessionEventDTO[],
): SessionEventDTO[] => {
  const prevUids = new Set<string>();
  const prevLegacySeqs = new Set<number>();
  for (const e of prev) {
    if (e.event_uid) prevUids.add(e.event_uid);
    else if (e.seq > 0) prevLegacySeqs.add(e.seq);
  }
  const missing = incoming
    .filter((e) =>
      e.event_uid
        ? !prevUids.has(e.event_uid)
        : e.seq > 0 && !prevLegacySeqs.has(e.seq),
    )
    .sort((a, b) => a.seq - b.seq);
  if (missing.length === 0) return prev;

  // History-space position of each prev entry that also appears in the
  // incoming window. For uid rows the anchor seq is the INCOMING row's seq
  // (the prev copy may carry a kernel-local seq); uid-less rows anchor by
  // seq equality (same store by construction).
  const incomingUidSeq = new Map<string, number>();
  const incomingSeqs = new Set<number>();
  for (const e of incoming) {
    if (e.event_uid) incomingUidSeq.set(e.event_uid, e.seq);
    if (e.seq > 0) incomingSeqs.add(e.seq);
  }
  const anchorSeqOf = (e: SessionEventDTO): number | null => {
    if (e.event_uid) return incomingUidSeq.get(e.event_uid) ?? null;
    if (e.seq > 0 && incomingSeqs.has(e.seq)) return e.seq;
    return null;
  };

  const out: SessionEventDTO[] = [];
  let mi = 0;
  if (prev.some((e) => anchorSeqOf(e) !== null)) {
    for (const e of prev) {
      const anchor = anchorSeqOf(e);
      if (anchor !== null) {
        while (mi < missing.length && missing[mi].seq < anchor) {
          out.push(missing[mi]);
          mi += 1;
        }
      }
      out.push(e);
    }
  } else {
    // No anchors: legacy positioning over uid-less persisted rows only.
    // A LEADING run of non-comparable entries (live deltas, uid-bearing
    // live frames) has no persisted anchor to glue to — it is the
    // in-flight tail of a resume that hasn't loaded history yet (the blank
    // case), so history smaller than the first comparable row must go
    // BEFORE it, not after.
    const firstComparableSeq =
      prev.find((e) => e.seq > 0 && !e.event_uid)?.seq ?? Infinity;
    while (mi < missing.length && missing[mi].seq < firstComparableSeq) {
      out.push(missing[mi]);
      mi += 1;
    }
    for (const e of prev) {
      if (e.seq > 0 && !e.event_uid) {
        while (mi < missing.length && missing[mi].seq < e.seq) {
          out.push(missing[mi]);
          mi += 1;
        }
      }
      out.push(e);
    }
  }
  while (mi < missing.length) {
    out.push(missing[mi]);
    mi += 1;
  }
  return out;
};

/* ── Turn builder ──────────────────────────────────────────── */

/**
 * Resumable turn builder. The event-fold state (turns, currentTurn,
 * activeToolCalls, dedup set, pending meta events) lives in this closure so a
 * caller can feed events in successive slices — ``pushAll(sliceA)`` then
 * ``pushAll(sliceB)`` — and get the SAME result as one ``pushAll([...A, ...B])``.
 * That resumability is what lets the streaming transcript append a token
 * without re-folding the whole event history each render (see
 * ``createIncrementalTurns`` / ``useIncrementalTurns``). ``buildTurns`` below is
 * the one-shot form used everywhere a full rebuild is wanted.
 */
const createTurnsBuilder = () => {
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
        cancelled: false,
      };
      turns.push(currentTurn);
    }
    return currentTurn;
  };

  // A turn can carry several concurrent FLOWS: the lead's own sequential
  // stream (``parentToolUseId === undefined``) plus one per subagent
  // (Task/Agent tool run, keyed by that call's tool_use_id — stamped on the
  // wire as ``parent_tool_use_id``). A background agent executes
  // CONCURRENTLY with the lead's streaming, so its events land interleaved
  // between the lead's delta frames. Every helper below therefore operates
  // on ONE flow at a time and treats blocks of other flows as invisible —
  // within a single flow the original sequential semantics (tool call
  // terminates the open text; canonical seals per segment) are unchanged.
  // Untagged events only ever see untagged blocks, so pre-existing behavior
  // (and any event stream from an older backend, which carries no tags) is
  // byte-for-byte identical.
  const flowOf = (b: ConversationBlock): string | undefined =>
    "parentToolUseId" in b ? b.parentToolUseId || undefined : undefined;

  const matchesLastUnsealed = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    messageId: string | undefined,
    parentToolUseId: string | undefined,
  ): (ConversationBlock & { kind: "assistant" | "thinking" }) | null => {
    for (let i = turn.blocks.length - 1; i >= 0; i--) {
      const b = turn.blocks[i];
      if (flowOf(b) !== parentToolUseId) continue; // other flow — invisible
      if (b.kind === "tool") return null;
      if (b.kind === kind) {
        if (b.sealed) return null;
        if (messageId !== undefined && b.messageId !== messageId) return null;
        return b as ConversationBlock & { kind: "assistant" | "thinking" };
      }
    }
    return null;
  };

  /** Last block of the given flow — so the sealed-redelivery check in
   * ``appendDelta`` still sees this flow's sealed canonical even when
   * another flow's events landed after it. */
  const lastFlowBlock = (
    turn: ConversationTurn,
    parentToolUseId: string | undefined,
  ): ConversationBlock | null => {
    for (let i = turn.blocks.length - 1; i >= 0; i--) {
      const b = turn.blocks[i];
      if (flowOf(b) !== parentToolUseId) continue;
      return b;
    }
    return null;
  };

  /**
   * Fold one streamed text chunk into the turn.
   *
   * ``snapshot`` marks a frame that carries the stream's ABSOLUTE state
   * rather than the next increment — the kernel sends one per open stream
   * when a client joins mid-turn, because the deltas emitted before it
   * connected are never persisted and so can never arrive any other way.
   * The only behavioral difference is replace-vs-append; every staleness
   * rule below applies unchanged, and deliberately so. A snapshot can
   * arrive AFTER the canonical event that superseded it (it is taken when
   * the tap attaches, which precedes the server's history read), and the
   * sealed-redelivery guard is exactly the test that catches it.
   */
  const appendDelta = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    text: string,
    messageId: string | undefined,
    parentToolUseId: string | undefined,
    snapshot = false,
  ) => {
    if (!text) return;
    const open = matchesLastUnsealed(turn, kind, messageId, parentToolUseId);
    if (open) {
      // Absolute state replaces; an increment extends. Neither seals —
      // the turn is still streaming, and sealing here would send the next
      // live chunk into a fresh block and split the message in two.
      open.text = snapshot ? text : open.text + text;
      return;
    }
    const last = lastFlowBlock(turn, parentToolUseId);
    if (
      last &&
      last.kind === kind &&
      last.sealed &&
      (messageId === undefined || last.messageId === messageId) &&
      // Drop only a genuine re-delivery — a chunk the sealed canonical text
      // already contains. A chunk with NEW content is a CONTINUATION segment:
      // runtimes that seal mid-turn (canonical per segment, e.g. around
      // provider-native search with no tool block in between) keep streaming
      // the next segment under the same turn-scoped message_id. The old
      // blanket drop rendered that whole segment blank until its canonical
      // landed ("no streaming, everything pops at once").
      last.text.includes(text)
    ) {
      return;
    }
    turn.blocks.push({ kind, text, messageId, sealed: false, parentToolUseId });
  };

  const replaceWithCanonical = (
    turn: ConversationTurn,
    kind: "assistant" | "thinking",
    text: string,
    messageId: string | undefined,
    elapsedMs?: number,
    parentToolUseId?: string,
    citationBundle?: CitationBundleV1,
  ) => {
    if (!text) return;
    const open = matchesLastUnsealed(turn, kind, messageId, parentToolUseId);
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
      if (open.kind === "assistant" && citationBundle) {
        open.citationBundle = citationBundle;
      }
      return;
    }
    turn.blocks.push(
      kind === "thinking"
        ? {
            kind,
            text,
            messageId,
            sealed: messageId != null,
            elapsedMs,
            parentToolUseId,
          }
        : {
            kind,
            text,
            messageId,
            sealed: messageId != null,
            parentToolUseId,
            ...(citationBundle ? { citationBundle } : {}),
          },
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

  const pushAll = (events: SessionEventDTO[]): void => {
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
          // ``message_id`` (UUID) when available, then the store-independent
          // ``event_uid`` (a bare seq can collide across the history/live
          // seq spaces), and only then ``envelope.seq``.
          id: payload.message_id
            ? `turn-${payload.message_id}`
            : `turn-${envelope.event_uid ?? envelope.seq}`,
          userMessageSeq: envelope.seq,
          userText,
          blocks: [],
          failedMessage: null,
          cancelled: false,
          attachments: payload.attachments
            ? parseTurnAttachments(payload.attachments)
            : undefined,
          userTimestamp: envelope.timestamp,
        };
        turns.push(currentTurn);
        activeToolCalls.clear();
        continue;
      }

      if (eventType === "session.idle") {
        if (currentTurn) {
          const kind = interruptKind(payload.stop_reason);
          if (kind === "user") currentTurn.cancelled = true;
          else if (kind === "runtime") currentTurn.interrupted = true;
        }
        continue;
      }

      if (eventType === "session.update") {
        if (payload.status === "cancelled" && currentTurn) {
          currentTurn.cancelled = true;
        }
        continue;
      }

      const turn = ensureTurn();

      if (eventType === "message.assistant.sidecar") {
        const segmentIndex = Number(payload.assistant_segment_index);
        const bundle = parseCitationBundle(payload.citation_bundle);
        if (Number.isInteger(segmentIndex) && segmentIndex >= 0 && bundle) {
          const assistantBlocks = turn.blocks.filter(
            (
              block,
            ): block is Extract<ConversationBlock, { kind: "assistant" }> =>
              block.kind === "assistant" &&
              (payload.message_id === undefined ||
                block.messageId === payload.message_id),
          );
          const target = assistantBlocks[segmentIndex];
          if (target) target.citationBundle = bundle;
        }
        continue;
      }

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
        appendDelta(
          turn,
          "assistant",
          payload.text ?? "",
          payload.message_id,
          payload.parent_tool_use_id || undefined,
          isLiveSnapshot(payload),
        );
        continue;
      }

      if (eventType === "message.assistant.thinking_delta") {
        appendDelta(
          turn,
          "thinking",
          payload.text ?? "",
          payload.message_id,
          payload.parent_tool_use_id || undefined,
          isLiveSnapshot(payload),
        );
        continue;
      }

      if (eventType === "message.assistant.delta") {
        replaceWithCanonical(
          turn,
          "assistant",
          payload.text ?? "",
          payload.message_id,
          undefined,
          payload.parent_tool_use_id || undefined,
          parseCitationBundle(payload.citation_bundle),
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
          payload.parent_tool_use_id || undefined,
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

      if (eventType === "tool.call.input_delta") {
        // Live, non-persisted: partial tool-call input JSON streaming in
        // before the canonical tool.call.started. The first chunk builds a
        // provisional running card so a large file write shows immediate
        // progress instead of a dead wait; later chunks accumulate onto it.
        // started reconciles the card with the canonical full input.
        const id = payload.tool_use_id || "";
        if (!id) continue;
        const text = payload.text ?? "";
        const streaming = activeToolCalls.get(id);
        if (streaming) {
          streaming.input = (streaming.input ?? "") + text;
          continue;
        }
        const title = payload.name || "tool";
        const card: PrototypeToolCall = {
          id,
          kind: resolveToolKind(title.toLowerCase()),
          title,
          // Left empty while input streams — raw partial JSON would look
          // noisy in the always-visible header; started fills in a proper
          // summary and the expandable Input block shows the live content.
          subtitle: "",
          status: "running",
          input: text,
        };
        activeToolCalls.set(id, card);
        turn.blocks.push({
          kind: "tool",
          tool: card,
          elapsedMs: elapsedSince(turn.userTimestamp, envelope.timestamp),
          parentToolUseId: payload.parent_tool_use_id || undefined,
        });
        continue;
      }

      if (eventType === "tool.call.output_delta") {
        // Live, non-persisted: streamed tool output between started and
        // completed. The card already exists; accumulate onto it. completed
        // later replaces it with the canonical aggregated output.
        const id = payload.tool_use_id || "";
        if (!id) continue;
        const streaming = activeToolCalls.get(id);
        if (streaming) {
          streaming.output = (streaming.output ?? "") + (payload.text ?? "");
        }
        continue;
      }

      if (eventType === "tool.call.thinking_delta") {
        // Live, non-persisted: tool-scoped reasoning stream (the ephemeral
        // generate_ui session's thinking forwarded onto this session).
        // Accumulates onto ``thinking`` — NEVER ``output``, which is the
        // tool's result stream (the OpenUI code the renderer paints).
        const id = payload.tool_use_id || "";
        if (!id) continue;
        const streaming = activeToolCalls.get(id);
        if (streaming) {
          streaming.thinking =
            (streaming.thinking ?? "") + (payload.text ?? "");
        }
        continue;
      }

      if (eventType === "tool.call.started") {
        const title =
          payload.name || payload.tool_name || payload.tool || "tool";
        const id =
          payload.id ||
          payload.call_id ||
          payload.tool_use_id ||
          // Stable fallback key: ``event_uid`` can't collide across the
          // history/live seq spaces the way a bare seq can.
          `${title}-${envelope.event_uid ?? envelope.seq}`;
        // A preceding tool.call.input_delta may already have built a
        // provisional running card for this id (streaming the partial input).
        const streamed = activeToolCalls.get(id);
        const card: PrototypeToolCall = {
          id,
          kind: resolveToolKind(title.toLowerCase()),
          title,
          subtitle:
            payload.summary ||
            payload.input ||
            payload.arguments ||
            streamed?.subtitle ||
            "",
          status: "running",
          // Canonical full input replaces the partial-JSON preview; fall back
          // to the streamed text if the started event omits the input.
          input: payload.input || payload.arguments || streamed?.input,
        };
        activeToolCalls.set(id, card);
        const startedElapsedMs = elapsedSince(
          turn.userTimestamp,
          envelope.timestamp,
        );
        // Reconcile the provisional block in place when input_delta already
        // pushed one, so started doesn't render a duplicate card.
        const startedIdx = turn.blocks.findIndex(
          (b) => b.kind === "tool" && b.tool.id === id,
        );
        const startedParent =
          payload.parent_tool_use_id ||
          (startedIdx >= 0
            ? (turn.blocks[startedIdx] as ConversationBlock & { kind: "tool" })
                .parentToolUseId
            : undefined) ||
          undefined;
        if (startedIdx >= 0) {
          turn.blocks[startedIdx] = {
            kind: "tool",
            tool: card,
            elapsedMs: startedElapsedMs,
            parentToolUseId: startedParent,
          };
        } else {
          turn.blocks.push({
            kind: "tool",
            tool: card,
            elapsedMs: startedElapsedMs,
            parentToolUseId: startedParent,
          });
        }
        continue;
      }

      if (eventType === "tool.call.completed") {
        const id =
          payload.id ||
          payload.call_id ||
          payload.tool_use_id ||
          // Same cross-space-safe fallback as ``tool.call.started``.
          `tool-${envelope.event_uid ?? envelope.seq}`;
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
        const completedParent =
          payload.parent_tool_use_id ||
          (blockIndex >= 0
            ? (turn.blocks[blockIndex] as ConversationBlock & { kind: "tool" })
                .parentToolUseId
            : undefined) ||
          undefined;
        if (blockIndex >= 0) {
          turn.blocks[blockIndex] = {
            kind: "tool",
            tool: next,
            elapsedMs,
            parentToolUseId: completedParent,
          };
        } else {
          turn.blocks.push({
            kind: "tool",
            tool: next,
            elapsedMs,
            parentToolUseId: completedParent,
          });
        }
        activeToolCalls.delete(id);
        continue;
      }

      if (eventType === "run.failed") {
        const kind = interruptKind(payload.category);
        if (kind === "user") {
          // User cancelled the run — render a quiet grey line, not the
          // ``ErrorMessageCard`` (with retry / switch-model) a real failure gets.
          turn.cancelled = true;
        } else if (kind === "runtime") {
          // Runtime/agent subprocess torn down or crashed mid-turn — same quiet
          // grey line, but a distinct label (NOT "user cancelled").
          turn.interrupted = true;
        } else {
          turn.failedMessage =
            payload.message ??
            t("conversation.runFailed" as Parameters<typeof t>[0]);
        }
      }
    }
  };

  // Trailing meta events (runtime.* with no following user message) attach to
  // the last turn. Two forms: the mutating one bakes them into the persistent
  // ``turns`` (used by the one-shot ``buildTurns``); the pure one returns them
  // as fresh blocks so the incremental snapshot can overlay them WITHOUT
  // mutating fold state (mutating would double-count on the next ``pushAll``).
  const applyTrailingMetaMutating = (): void => {
    if (metaEvents.length && turns.length > 0) {
      const lastTurn = turns[turns.length - 1];
      for (const [i, item] of metaEvents.entries()) {
        const tool = toMetaToolCall(item.type, item.payload, turns.length + i);
        if (tool) {
          const elapsedMs = elapsedSince(
            lastTurn.userTimestamp,
            item.timestamp,
          );
          lastTurn.blocks.push({ kind: "tool", tool, elapsedMs });
        }
      }
    }
  };

  const computeTrailingMetaBlocks = (): ConversationBlock[] => {
    const out: ConversationBlock[] = [];
    if (metaEvents.length && turns.length > 0) {
      const lastTurn = turns[turns.length - 1];
      for (const [i, item] of metaEvents.entries()) {
        const tool = toMetaToolCall(item.type, item.payload, turns.length + i);
        if (tool) {
          const elapsedMs = elapsedSince(
            lastTurn.userTimestamp,
            item.timestamp,
          );
          out.push({ kind: "tool", tool, elapsedMs });
        }
      }
    }
    return out;
  };

  return {
    turns,
    pushAll,
    applyTrailingMetaMutating,
    computeTrailingMetaBlocks,
  };
};

export const buildTurns = (events: SessionEventDTO[]): ConversationTurn[] => {
  const builder = createTurnsBuilder();
  builder.pushAll(events);
  builder.applyTrailingMetaMutating();
  return builder.turns;
};

/**
 * Incremental transcript builder — the streaming-perf counterpart to
 * ``buildTurns``. ``buildTurns(events)`` re-folds the ENTIRE event array on
 * every call; driven per-token during a long streamed reply that is O(N²) (each
 * token re-walks all prior events and re-concatenates the growing assistant
 * text from scratch), which stalls the main thread and makes deltas arrive in
 * visible bursts. This keeps the fold state alive across calls and, when
 * ``events`` is an append-only extension of what it already processed, folds
 * ONLY the new events, then clones just the growing tail turn(s) so React still
 * sees fresh references for what changed. Non-append changes (window replace,
 * reconcile splice, session switch) transparently fall back to a full rebuild.
 *
 * The returned turns honour the ``useStableTurns`` reference contract directly
 * (stable refs for sealed turns, fresh refs + fresh block/tool refs for the
 * mutated tail), so callers do NOT need to additionally wrap the result.
 */
export interface IncrementalTurns {
  update(events: SessionEventDTO[]): ConversationTurn[];
}

export const createIncrementalTurns = (): IncrementalTurns => {
  let builder = createTurnsBuilder();
  let processed = 0;
  let lastEnvelope: SessionEventDTO | null = null;
  let snapshot: ConversationTurn[] = [];

  const cloneBlock = (b: ConversationBlock): ConversationBlock =>
    b.kind === "tool" ? { ...b, tool: { ...b.tool } } : { ...b };
  const cloneTurn = (t: ConversationTurn): ConversationTurn => ({
    ...t,
    blocks: t.blocks.map(cloneBlock),
  });

  const buildSnapshot = (): ConversationTurn[] => {
    const src = builder.turns;
    // Reuse every turn strictly before the last of the PREVIOUS snapshot: only
    // the last turn (deltas) and — at a turn boundary — the just-sealed
    // second-to-last (meta flush) are ever mutated, so anything below that line
    // is final and its reference can be shared verbatim.
    const reuseBoundary = Math.max(0, snapshot.length - 1);
    const out: ConversationTurn[] = [];
    for (let i = 0; i < src.length; i += 1) {
      out.push(
        i < reuseBoundary && snapshot[i] ? snapshot[i] : cloneTurn(src[i]),
      );
    }
    // Overlay any pending trailing meta onto the (already fresh-cloned) last
    // turn. Never touches builder state, so the next pushAll won't double-count.
    const trailing = builder.computeTrailingMetaBlocks();
    if (trailing.length > 0 && out.length > 0) {
      const lastIdx = out.length - 1;
      const last =
        lastIdx < reuseBoundary ? cloneTurn(src[lastIdx]) : out[lastIdx];
      out[lastIdx] = {
        ...last,
        blocks: [...last.blocks, ...trailing.map(cloneBlock)],
      };
    }
    snapshot = out;
    return out;
  };

  const update = (events: SessionEventDTO[]): ConversationTurn[] => {
    const appendOnly =
      events.length >= processed &&
      (processed === 0 || events[processed - 1] === lastEnvelope);
    if (!appendOnly) {
      builder = createTurnsBuilder();
      processed = 0;
      snapshot = [];
    }
    if (events.length > processed) {
      builder.pushAll(events.slice(processed));
    }
    processed = events.length;
    lastEnvelope = events.length > 0 ? events[events.length - 1] : null;
    return buildSnapshot();
  };

  return { update };
};
