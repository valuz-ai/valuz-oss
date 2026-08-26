/**
 * User-level control-plane stream — the always-on multiplexed lifecycle SSE.
 *
 * One shared connection to ``GET /v1/stream`` carries lifecycle frames
 * (``run.started`` / ``run.finished`` / ``run.status``) across ALL of the
 * caller's sessions, each stamped with its ``session_id``. Consumers (the
 * running/finished run lists, and later the conversation store) subscribe and
 * react — replacing the old periodic ``/v1/runs`` polls. Reconnect + cursor
 * resume are handled by {@link fetchEventSource}; a single ref-counted
 * connection backs every subscriber (never N connections).
 *
 * See docs/design/event-delivery-unification.md §4 (control plane).
 */

import { fetchEventSource } from "./fetch-event-source";

let _apiBase =
  (import.meta as unknown as Record<string, Record<string, string> | undefined>).env
    ?.VITE_API_BASE_URL || "http://localhost:8000";

/** Test/override hook — mirrors the other api modules' base setters. */
export const setUserStreamApiBase = (url: string): void => {
  _apiBase = url;
};

/** One control-plane lifecycle frame, decoded from the SSE wire shape. */
export interface ControlFrame {
  seq: number;
  /** ``run.started`` | ``run.finished`` | ``run.status``. */
  eventType: string;
  sessionId: string;
  payload: Record<string, string>;
  timestamp: number | null;
}

type Subscriber = (frame: ControlFrame) => void;

const _subscribers = new Set<Subscriber>();
// In-memory resume cursor (the global durable ``events.id``). Advances as
// frames arrive so a transient reconnect resumes from the last-seen event
// instead of replaying. Starts at 0: the first connect replays the user's
// lifecycle history once — consumers collapse that into a single snapshot
// refresh. (A future ``tail``-from-latest mode can drop even that one-time
// replay; see design §8 bounded backfill.)
let _cursor = 0;
let _close: (() => void) | null = null;

const _streamUrl = (): string => `${_apiBase}/v1/stream?after_seq=${_cursor}`;

const _advance = (raw: string): number | null => {
  try {
    const seq = (JSON.parse(raw) as { seq?: unknown }).seq;
    return typeof seq === "number" ? seq : null;
  } catch {
    return null;
  }
};

const _onFrame = (frame: { event: string; data: string }): void => {
  // Heartbeats carry only ``{seq}`` — keep the resume cursor fresh, no dispatch.
  if (frame.event === "heartbeat") {
    const seq = _advance(frame.data);
    if (seq !== null && seq > _cursor) _cursor = seq;
    return;
  }
  let decoded: {
    seq?: number;
    event_type?: string;
    session_id?: string;
    payload?: Record<string, string>;
    timestamp?: number | null;
  };
  try {
    decoded = JSON.parse(frame.data);
  } catch {
    return;
  }
  if (typeof decoded.seq === "number" && decoded.seq > _cursor) _cursor = decoded.seq;
  const control: ControlFrame = {
    seq: typeof decoded.seq === "number" ? decoded.seq : 0,
    eventType: decoded.event_type ?? frame.event,
    sessionId: decoded.session_id ?? "",
    payload: decoded.payload ?? {},
    timestamp: decoded.timestamp ?? null,
  };
  _subscribers.forEach((fn) => fn(control));
};

const _start = (): void => {
  if (_close) return;
  _close = fetchEventSource(_streamUrl, _onFrame, { reconnectDelayMs: 1000 });
};

const _stop = (): void => {
  _close?.();
  _close = null;
};

/**
 * Subscribe to the shared control-plane stream. Opens the single connection on
 * the first subscriber and closes it when the last one leaves. Returns an
 * unsubscribe function.
 */
export const subscribeUserStream = (cb: Subscriber): (() => void) => {
  _subscribers.add(cb);
  _start();
  return () => {
    _subscribers.delete(cb);
    if (_subscribers.size === 0) _stop();
  };
};

/** Test-only: reset the module singleton between cases. */
export const _resetUserStreamForTests = (): void => {
  _stop();
  _subscribers.clear();
  _cursor = 0;
};
