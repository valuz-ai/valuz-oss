import { renderHook, act } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { useRegistryStore } from "../edition/registry-store";
import { useRegisteredSlotNames } from "./use-slots";

const Dummy = () => null;

describe("useRegisteredSlotNames", () => {
  beforeEach(() => {
    useRegistryStore.setState({ slots: {} });
  });

  it("is empty when no overlay has registered anything", () => {
    const { result } = renderHook(() => useRegisteredSlotNames());
    expect(result.current.size).toBe(0);
  });

  it("reports a name once something is registered for it", () => {
    const { result } = renderHook(() => useRegisteredSlotNames());
    act(() => {
      useRegistryStore
        .getState()
        .registerSlot("conversation.tool-card.site_deploy", {
          id: "commercial",
          component: Dummy,
        });
    });
    expect(result.current.has("conversation.tool-card.site_deploy")).toBe(true);
  });

  it("drops a name whose last registration was removed", () => {
    // ``unregisterSlot`` leaves the key behind with an EMPTY array rather than
    // deleting it. A host that reads bare key presence would then skip its own
    // fallback forever while rendering nothing — a blank card that no amount of
    // re-registering fixes. This is the assertion that keeps the filter honest.
    const { result } = renderHook(() => useRegisteredSlotNames());
    act(() => {
      useRegistryStore.getState().registerSlot("s", {
        id: "a",
        component: Dummy,
      });
    });
    expect(result.current.has("s")).toBe(true);

    act(() => {
      useRegistryStore.getState().unregisterSlot("s", "a");
    });
    expect(useRegistryStore.getState().slots.s).toEqual([]);
    expect(result.current.has("s")).toBe(false);
  });

  it("keeps a stable identity across renders that change nothing", () => {
    // Consumers put this in dependency arrays (``useToolCallCards`` does). A
    // fresh Set per render would invalidate their callbacks on every render.
    const { result, rerender } = renderHook(() => useRegisteredSlotNames());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);

    act(() => {
      useRegistryStore.getState().registerSlot("s", {
        id: "a",
        component: Dummy,
      });
    });
    const afterRegister = result.current;
    expect(afterRegister).not.toBe(first);
    rerender();
    expect(result.current).toBe(afterRegister);
  });
});
