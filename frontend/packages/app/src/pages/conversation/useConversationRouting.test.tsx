/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { NEW_SESSION_ID } from "./session-events";
import { useConversationRouting } from "./useConversationRouting";

const wrapper = ({ children }: { children: ReactNode }) => (
  <MemoryRouter>{children}</MemoryRouter>
);

describe("useConversationRouting route refs", () => {
  it("mirrors the shown id and bumps the epoch only when it changes", () => {
    const { result, rerender } = renderHook(
      ({ sessionId }: { sessionId: string | undefined }) =>
        useConversationRouting({ sessionId, variant: "page" }),
      {
        wrapper,
        initialProps: { sessionId: undefined as string | undefined },
      },
    );
    expect(result.current.routeIdRef.current).toBe(NEW_SESSION_ID);
    expect(result.current.routeEpochRef.current).toBe(0);

    // A re-render on the same id is not a move.
    rerender({ sessionId: undefined });
    expect(result.current.routeEpochRef.current).toBe(0);

    rerender({ sessionId: "a" });
    expect(result.current.routeIdRef.current).toBe("a");
    expect(result.current.routeEpochRef.current).toBe(1);

    rerender({ sessionId: "b" });
    expect(result.current.routeIdRef.current).toBe("b");
    expect(result.current.routeEpochRef.current).toBe(2);

    // Coming back to the draft is a move too — a fresh draft, not the one a
    // send may still be in flight from.
    rerender({ sessionId: undefined });
    expect(result.current.routeIdRef.current).toBe(NEW_SESSION_ID);
    expect(result.current.routeEpochRef.current).toBe(3);
  });

  it("panel variant: a promotion moves the shown id without a route", () => {
    const { result } = renderHook(
      () => useConversationRouting({ sessionId: undefined, variant: "panel" }),
      { wrapper },
    );
    expect(result.current.id).toBe(NEW_SESSION_ID);

    act(() => {
      result.current.onSessionPromoted("minted");
    });
    expect(result.current.id).toBe("minted");
    expect(result.current.routeIdRef.current).toBe("minted");
    expect(result.current.routeEpochRef.current).toBe(1);
  });
});
