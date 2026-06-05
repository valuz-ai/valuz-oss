import { afterEach, describe, expect, it, vi } from "vitest";
import { sessionsApi } from "../api/sessions-api";
import {
  createSessionStreamController,
  type SessionStreamSnapshot,
} from "./session-stream";

/**
 * Microtask-only flush. We can't use ``setTimeout(_, 0)`` because half
 * the tests below install fake timers, and a real setTimeout would
 * sit forever in the fake queue. ``Promise.resolve()`` chains drain
 * microtasks (the queue ``.then``/``.catch`` callbacks land on),
 * which is enough since the controller's state transitions all live
 * on the microtask queue.
 */
const flushMicrotasks = async () => {
  for (let i = 0; i < 5; i += 1) {
    await Promise.resolve();
  }
};

describe("createSessionStreamController", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("should transition idle -> connecting -> connected on first frame", async () => {
    const states: SessionStreamSnapshot[] = [];
    const subscribeMock = vi
      .spyOn(sessionsApi, "subscribeEvents")
      .mockImplementation((_id, onEvent) => {
        return new Promise<void>(() => {
          // Emit one frame synchronously so connection -> "connected"
          onEvent({
            seq: 1,
            event: {
              event_type: "session.update",
              payload: { status: "running" },
            },
          });
          // Never resolve — the controller stays connected until stop().
        });
      });

    const ctrl = createSessionStreamController({
      sessionId: "s1",
      onEvent: () => {},
      onStateChange: (snap) => states.push(snap),
    });
    ctrl.start();

    await flushMicrotasks();

    expect(states.map((s) => s.state)).toEqual(["connecting", "connected"]);
    expect(subscribeMock).toHaveBeenCalledTimes(1);
    ctrl.stop();
  });

  it("should retry with backoff on transient failure and resume from lastSeq", async () => {
    vi.useFakeTimers();
    const seenSeqs: (number | undefined)[] = [];
    let attempt = 0;

    vi.spyOn(sessionsApi, "subscribeEvents").mockImplementation(
      (_id, onEvent, afterSeq) => {
        seenSeqs.push(afterSeq);
        if (attempt === 0) {
          attempt += 1;
          // Deliver one frame so lastSeq advances, then fail to trigger retry.
          onEvent({
            seq: 7,
            event: {
              event_type: "session.update",
              payload: { status: "running" },
            },
          });
          return Promise.reject(new Error("network blip"));
        }
        return new Promise<void>(() => {
          /* never resolve */
        });
      },
    );

    const ctrl = createSessionStreamController({
      sessionId: "s1",
      onEvent: () => {},
      backoffSchedule: [50, 100],
    });
    ctrl.start();

    await flushMicrotasks();

    expect(ctrl.snapshot().state).toBe("reconnecting");
    expect(ctrl.snapshot().attempt).toBe(1);
    expect(ctrl.snapshot().lastSeq).toBe(7);

    await vi.advanceTimersByTimeAsync(50);
    await flushMicrotasks();

    // Second call should have happened with afterSeq=7
    expect(seenSeqs[1]).toBe(7);

    ctrl.stop();
  });

  it("should transition to error after exhausting backoff schedule", async () => {
    vi.useFakeTimers();
    vi.spyOn(sessionsApi, "subscribeEvents").mockImplementation(() =>
      Promise.reject(new Error("server gone")),
    );

    const ctrl = createSessionStreamController({
      sessionId: "s1",
      onEvent: () => {},
      backoffSchedule: [10, 20], // 2 retries then error
    });
    ctrl.start();

    await flushMicrotasks();
    expect(ctrl.snapshot().state).toBe("reconnecting");

    await vi.advanceTimersByTimeAsync(10);
    await flushMicrotasks();
    expect(ctrl.snapshot().state).toBe("reconnecting");

    await vi.advanceTimersByTimeAsync(20);
    await flushMicrotasks();
    expect(ctrl.snapshot().state).toBe("error");
    expect(ctrl.snapshot().errorMessage).toBe("server gone");

    ctrl.stop();
  });

  it("should reconnect immediately on manual reconnect() call", async () => {
    let calls = 0;
    vi.spyOn(sessionsApi, "subscribeEvents").mockImplementation(() => {
      calls += 1;
      return Promise.reject(new Error("fail"));
    });

    const ctrl = createSessionStreamController({
      sessionId: "s1",
      onEvent: () => {},
      backoffSchedule: [], // no auto-retries — straight to error
    });
    ctrl.start();
    await flushMicrotasks();
    expect(ctrl.snapshot().state).toBe("error");
    expect(calls).toBe(1);

    ctrl.reconnect();
    await flushMicrotasks();
    expect(calls).toBe(2);
    expect(ctrl.snapshot().attempt).toBe(0); // reset on manual reconnect

    ctrl.stop();
  });

  it("should transition to disconnected on clean stream end", async () => {
    vi.spyOn(sessionsApi, "subscribeEvents").mockImplementation(
      (_id, onEvent) => {
        onEvent({
          seq: 1,
          event: {
            event_type: "session.update",
            payload: { status: "idle" },
          },
        });
        return Promise.resolve();
      },
    );

    const ctrl = createSessionStreamController({
      sessionId: "s1",
      onEvent: () => {},
    });
    ctrl.start();
    await flushMicrotasks();

    expect(ctrl.snapshot().state).toBe("disconnected");
    ctrl.stop();
  });
});
