import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PlatformProvider, usePlatform } from "./context";
import { webCapabilities } from "./web-capabilities";

describe("usePlatform", () => {
  it("returns the provided value when mounted inside <PlatformProvider>", () => {
    const provided = { ...webCapabilities, isElectron: true };

    const { result } = renderHook(() => usePlatform(), {
      wrapper: ({ children }) => (
        <PlatformProvider value={provided}>{children}</PlatformProvider>
      ),
    });

    expect(result.current).toBe(provided);
    expect(result.current.isElectron).toBe(true);
  });

  it("falls back to webCapabilities instead of throwing when no provider is mounted", () => {
    // Previously this threw:
    //   "usePlatform() must be used inside <PlatformProvider>…"
    // which crashed the renderer whenever a render branch escaped the
    // provider wrapper (e.g. an isolated test, or a future portal/tooltip).
    const { result } = renderHook(() => usePlatform());

    expect(result.current).toBe(webCapabilities);
    expect(result.current.isElectron).toBe(false);
    expect(result.current.isMac).toBe(false);
  });

  it("fallback selectDirectory resolves to null (no-op)", async () => {
    const { result } = renderHook(() => usePlatform());
    await expect(result.current.selectDirectory()).resolves.toBeNull();
  });
});
