/** @vitest-environment jsdom */

import { act, render, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import {
  readPageMemory,
  resetPageMemory,
  usePageMemoryState,
  useRestoredScroll,
} from "./use-page-memory";

afterEach(() => resetPageMemory());

describe("usePageMemoryState", () => {
  it("survives an unmount and re-initializes from memory", () => {
    const first = renderHook(() =>
      usePageMemoryState<string[] | null>("test:list", null),
    );
    expect(first.result.current[0]).toBeNull();
    act(() => first.result.current[1](["a", "b"]));
    first.unmount();

    const second = renderHook(() =>
      usePageMemoryState<string[] | null>("test:list", null),
    );
    expect(second.result.current[0]).toEqual(["a", "b"]);
    expect(readPageMemory<string[]>("test:list")).toEqual(["a", "b"]);
  });

  it("switches to the other memory when the key changes", () => {
    const hook = renderHook(
      ({ id }) => usePageMemoryState<number>(`test:view:${id}`, 0),
      { initialProps: { id: "x" } },
    );
    act(() => hook.result.current[1](7));
    hook.rerender({ id: "y" });
    expect(hook.result.current[0]).toBe(0);
    act(() => hook.result.current[1]((value) => value + 3));
    hook.rerender({ id: "x" });
    expect(hook.result.current[0]).toBe(7);
    hook.rerender({ id: "y" });
    expect(hook.result.current[0]).toBe(3);
  });
});

describe("useRestoredScroll", () => {
  function Page({ ready }: { ready: boolean }) {
    const ref = useRestoredScroll("test:page", ready);
    return (
      <div data-testid="scroller" style={{ overflowY: "auto" }}>
        <div ref={ref}>content</div>
      </div>
    );
  }

  it("records the scroll position and restores it on the next mount", () => {
    const first = render(<Page ready />);
    const scroller = first.getByTestId("scroller");
    scroller.scrollTop = 120;
    act(() => {
      scroller.dispatchEvent(new Event("scroll"));
    });
    first.unmount();

    const second = render(<Page ready />);
    expect(second.getByTestId("scroller").scrollTop).toBe(120);
  });

  it("waits for ready before restoring", () => {
    const first = render(<Page ready />);
    const scroller = first.getByTestId("scroller");
    scroller.scrollTop = 40;
    act(() => {
      scroller.dispatchEvent(new Event("scroll"));
    });
    first.unmount();

    const second = render(<Page ready={false} />);
    expect(second.getByTestId("scroller").scrollTop).toBe(0);
    second.rerender(<Page ready />);
    expect(second.getByTestId("scroller").scrollTop).toBe(40);
  });
});
