import { afterEach, describe, expect, it } from "vitest";

import {
  _resetUiBusForTests,
  uiBus,
  uiPushFromFrame,
  uiPushKind,
  useUiBusStore,
  type UiPushEvent,
} from "./index";

afterEach(() => {
  _resetUiBusForTests();
});

const push = (
  kind: string,
  payload: Record<string, string>,
  extra: Partial<UiPushEvent> = {},
): UiPushEvent => ({ kind, sessionId: "s1", payload, ...extra });

describe("ui bus frames", () => {
  it("recognises ui.* frames only", () => {
    expect(uiPushKind("ui.toast")).toBe("toast");
    expect(uiPushKind("message.user")).toBeNull();
    expect(uiPushFromFrame("message.user", {}, "s1")).toBeNull();
    expect(
      uiPushFromFrame("ui.log", { text: "x", push_id: "a" }, "", 5),
    ).toEqual({
      kind: "log",
      sessionId: null,
      payload: { text: "x" },
      pushId: "a",
      timestamp: 5,
    });
  });
});

describe("uiBus", () => {
  it("drops a second copy of the same push and isolates a throwing listener", () => {
    const seen: string[] = [];
    uiBus.subscribe(() => {
      throw new Error("listener bug");
    });
    uiBus.subscribe((event) => seen.push(event.kind));
    uiBus.emit(push("toast", { text: "hi" }, { pushId: "same" }));
    uiBus.emit(push("toast", { text: "hi" }, { pushId: "same" }));
    uiBus.emit(push("toast", { text: "no id" }));
    expect(seen).toEqual(["toast", "toast"]);
  });

  it("bumps redraw counters on invalidate", () => {
    uiBus.emit(push("invalidate", { site: "conversation.composer.dock" }));
    expect(useUiBusStore.getState().generation).toEqual({
      "conversation.composer.dock": 1,
    });
    uiBus.emit(push("invalidate", {}));
    expect(useUiBusStore.getState().generation).toEqual({
      "conversation.composer.dock": 2,
      "*": 1,
    });
  });

  it("keeps a status line per owner and session; empty text clears it", () => {
    uiBus.emit(push("status", { owner: "a", text: "indexing" }));
    uiBus.emit(push("status", { owner: "b", text: "2 tasks" }));
    expect(useUiBusStore.getState().status.s1).toEqual({
      a: "indexing",
      b: "2 tasks",
    });
    uiBus.emit(push("status", { owner: "a", text: "" }));
    expect(useUiBusStore.getState().status.s1).toEqual({ b: "2 tasks" });
  });

  it("collects log lines per session and notices per tool call, bounded", () => {
    for (let i = 0; i < 105; i += 1) {
      uiBus.emit(push("log", { owner: "p", text: `line ${i}` }, { timestamp: i }));
    }
    const logs = useUiBusStore.getState().logs.s1!;
    expect(logs).toHaveLength(100);
    expect(logs[0]).toEqual({ owner: "p", text: "line 5", tone: undefined, timestamp: 5 });

    uiBus.emit(
      push("notice", { owner: "p", tool_use_id: "t1", text: "slow", tone: "warning" }, { timestamp: 9 }),
    );
    uiBus.emit(push("notice", { owner: "p", text: "no tool id" }));
    expect(useUiBusStore.getState().notices).toEqual({
      t1: [{ owner: "p", text: "slow", tone: "warning", timestamp: 9 }],
    });
  });
});
