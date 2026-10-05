/**
 * Frontend side of the UI bus (hooks-and-plugin-ui.md §6).
 *
 * - ``useUiBusStore`` — which UI slots plugins draw into (from
 *   ``/v1/ui/sites``), a redraw counter per site, and the latest status
 *   line each plugin pushed per session.
 * - ``uiBus`` — the push channel: ``ui.*`` frames from a session's event
 *   stream and from the global ``/v1/stream`` are emitted here instead of
 *   entering the transcript.
 */

import { create } from "zustand";
import type { UiSite } from "../api/ui-api";

/** One ``ui.*`` push (``invalidate`` / ``toast`` / ``status`` / ``log`` / ``notice``). */
export interface UiPushEvent {
  kind: string;
  /** The session whose stream carried it; ``null`` for global pushes. */
  sessionId: string | null;
  payload: Record<string, string>;
  /** Identity of the push, for dropping a copy from a second stream. */
  pushId?: string;
  timestamp?: number;
}

type Listener = (event: UiPushEvent) => void;
const listeners = new Set<Listener>();
// ``push_id``s already delivered — two streams open on one session both
// carry a push; the second copy is dropped. Bounded: oldest half shed at cap.
const seenPushIds = new Set<string>();

function firstDelivery(pushId: string | undefined): boolean {
  if (!pushId) return true;
  if (seenPushIds.has(pushId)) return false;
  if (seenPushIds.size >= 1024) {
    let shed = 0;
    for (const id of seenPushIds) {
      seenPushIds.delete(id);
      if (++shed >= 512) break;
    }
  }
  seenPushIds.add(pushId);
  return true;
}

/**
 * The push carried by a stream frame (``ui.toast`` …), or ``null`` for any
 * other frame. ``push_id`` is lifted out of the payload.
 */
export function uiPushFromFrame(
  eventType: string,
  payload: Record<string, string> | undefined,
  sessionId: string | null,
  timestamp?: number | null,
): UiPushEvent | null {
  const kind = uiPushKind(eventType);
  if (kind === null) return null;
  const { push_id: pushId, ...rest } = payload ?? {};
  return {
    kind,
    sessionId: sessionId || null,
    payload: rest,
    pushId,
    timestamp: timestamp ?? undefined,
  };
}

export const uiBus = {
  emit(event: UiPushEvent): void {
    if (!firstDelivery(event.pushId)) return;
    useUiBusStore.getState().apply(event);
    for (const listener of [...listeners]) {
      try {
        listener(event);
      } catch {
        // one listener's bug never blocks the others
      }
    }
  },
  subscribe(listener: Listener): () => void {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

/** Test-only: forget delivered pushes and listeners. */
export function _resetUiBusForTests(): void {
  seenPushIds.clear();
  listeners.clear();
  useUiBusStore.setState({
    sites: [],
    generation: {},
    status: {},
    logs: {},
    notices: {},
  });
}

/** ``ui.toast`` → ``toast``; ``null`` for frames that are not UI pushes. */
export function uiPushKind(eventType: string): string | null {
  return eventType.startsWith("ui.") ? eventType.slice(3) : null;
}

/** A ``ui.log`` line or a ``ui.notice`` (payload keys: ``modules/plugin_ui/push.py``). */
export interface UiLine {
  owner: string;
  text: string;
  tone?: string;
  timestamp: number;
}

const MAX_LOG_LINES = 100;
const MAX_NOTICES = 20;

const appendBounded = (
  lines: readonly UiLine[] | undefined,
  line: UiLine,
  cap: number,
): UiLine[] => [...(lines ?? []), line].slice(-cap);

const lineOf = (event: UiPushEvent): UiLine => ({
  owner: event.payload.owner || "plugin",
  text: event.payload.text ?? "",
  tone: event.payload.tone || undefined,
  timestamp: event.timestamp ?? Date.now(),
});

interface UiBusState {
  sites: UiSite[];
  /** site → redraw counter, bumped by ``ui.invalidate``. */
  generation: Record<string, number>;
  /** sessionId → owner → status text. */
  status: Record<string, Record<string, string>>;
  /** sessionId → ``ui.log`` lines (live only, bounded). */
  logs: Record<string, UiLine[]>;
  /** toolUseId → ``ui.notice`` lines (live only, bounded). */
  notices: Record<string, UiLine[]>;
  setSites: (sites: UiSite[]) => void;
  apply: (event: UiPushEvent) => void;
}

export const useUiBusStore = create<UiBusState>((set) => ({
  sites: [],
  generation: {},
  status: {},
  logs: {},
  notices: {},
  setSites: (sites) => set({ sites }),
  apply: (event) =>
    set((state) => {
      if (event.kind === "invalidate") {
        const site = event.payload.site || "*";
        const generation = { ...state.generation };
        if (site === "*") {
          for (const key of Object.keys(generation))
            generation[key] = (generation[key] ?? 0) + 1;
          generation["*"] = (generation["*"] ?? 0) + 1;
        } else {
          generation[site] = (generation[site] ?? 0) + 1;
        }
        return { generation };
      }
      if (event.kind === "status" && event.sessionId) {
        const owner = event.payload.owner || "plugin";
        const forSession = { ...(state.status[event.sessionId] ?? {}) };
        if (event.payload.text) forSession[owner] = event.payload.text;
        else delete forSession[owner];
        return { status: { ...state.status, [event.sessionId]: forSession } };
      }
      if (event.kind === "log" && event.sessionId && event.payload.text) {
        return {
          logs: {
            ...state.logs,
            [event.sessionId]: appendBounded(
              state.logs[event.sessionId],
              lineOf(event),
              MAX_LOG_LINES,
            ),
          },
        };
      }
      if (event.kind === "notice") {
        const toolUseId = event.payload.tool_use_id;
        if (!toolUseId || !event.payload.text) return {};
        return {
          notices: {
            ...state.notices,
            [toolUseId]: appendBounded(
              state.notices[toolUseId],
              lineOf(event),
              MAX_NOTICES,
            ),
          },
        };
      }
      return {};
    }),
}));
