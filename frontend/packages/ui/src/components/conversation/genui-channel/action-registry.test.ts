import { afterEach, describe, expect, it, vi } from "vitest";

import {
  dispatchGenUIAction,
  getGenUIActionSink,
  registerGenUIActionSink,
  unregisterGenUIActionSink,
} from "./action-registry";

describe("genui action registry", () => {
  afterEach(() => unregisterGenUIActionSink());

  it("forwards dispatched actions to the registered sink verbatim", () => {
    const sink = vi.fn();
    registerGenUIActionSink(sink);

    const event = {
      name: "ask_agent",
      surfaceId: "main",
      sourceComponentId: "target-empty",
      context: { intent: "fill" },
      host: { symbol: "US:NVDA" },
    };
    dispatchGenUIAction(event);

    expect(sink).toHaveBeenCalledTimes(1);
    expect(sink).toHaveBeenCalledWith(event);
  });

  it("is a silent no-op without a sink and after unregistering", () => {
    expect(getGenUIActionSink()).toBeUndefined();
    expect(() =>
      dispatchGenUIAction({
        name: "noop",
        surfaceId: "main",
        sourceComponentId: "a",
        context: {},
      }),
    ).not.toThrow();

    const sink = vi.fn();
    registerGenUIActionSink(sink);
    unregisterGenUIActionSink();
    dispatchGenUIAction({
      name: "noop",
      surfaceId: "main",
      sourceComponentId: "a",
      context: {},
    });
    expect(sink).not.toHaveBeenCalled();
  });
});
