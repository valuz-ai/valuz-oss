import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchEventSource, type SSEFrame } from "./fetch-event-source";
import {
  subscribeUserStream,
  _resetUserStreamForTests,
  type ControlFrame,
} from "./user-stream";

vi.mock("./fetch-event-source");
const mockFES = vi.mocked(fetchEventSource);

/** Wire the mocked SSE transport and capture its ``getUrl`` / ``onFrame``. */
function connect() {
  let onFrame!: (f: SSEFrame) => void;
  let getUrl!: () => string;
  const close = vi.fn();
  mockFES.mockImplementation((gu, of) => {
    getUrl = gu;
    onFrame = of;
    return close;
  });
  return {
    feed: (f: { event: string; data: string }) => onFrame({ id: null, ...f }),
    url: () => getUrl(),
    close,
  };
}

afterEach(() => {
  _resetUserStreamForTests();
  mockFES.mockReset();
});

describe("subscribeUserStream", () => {
  it("decodes control frames and dispatches to subscribers", () => {
    const h = connect();
    const seen: ControlFrame[] = [];
    subscribeUserStream((f) => seen.push(f));

    h.feed({
      event: "run.started",
      data: JSON.stringify({
        seq: 5,
        event_type: "run.started",
        session_id: "s1",
        payload: {},
        timestamp: 5,
      }),
    });

    expect(seen).toHaveLength(1);
    expect(seen[0]).toMatchObject({ seq: 5, eventType: "run.started", sessionId: "s1" });
  });

  it("advances the resume cursor from heartbeats without dispatching", () => {
    const h = connect();
    const seen: ControlFrame[] = [];
    subscribeUserStream((f) => seen.push(f));

    h.feed({ event: "heartbeat", data: JSON.stringify({ seq: 9 }) });

    expect(seen).toHaveLength(0);
    // Next (re)connect resumes from the heartbeat cursor, not 0.
    expect(h.url()).toContain("after_seq=9");
  });

  it("resumes from the last dispatched frame's seq", () => {
    const h = connect();
    subscribeUserStream(() => {});
    h.feed({
      event: "run.finished",
      data: JSON.stringify({ seq: 42, event_type: "run.finished", session_id: "s1", payload: {} }),
    });
    expect(h.url()).toContain("after_seq=42");
  });

  it("ignores malformed frame data without throwing", () => {
    const h = connect();
    const seen: ControlFrame[] = [];
    subscribeUserStream((f) => seen.push(f));
    expect(() => h.feed({ event: "run.started", data: "not json" })).not.toThrow();
    expect(seen).toHaveLength(0);
  });

  it("shares one connection and ref-counts start/stop", () => {
    const h = connect();
    const un1 = subscribeUserStream(() => {});
    const un2 = subscribeUserStream(() => {});

    expect(mockFES).toHaveBeenCalledTimes(1); // single shared connection
    un1();
    expect(h.close).not.toHaveBeenCalled(); // one subscriber remains
    un2();
    expect(h.close).toHaveBeenCalledTimes(1); // last unsubscribe closes it
  });
});
