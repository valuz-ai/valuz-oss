import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

vi.mock("../../hooks/use-i18n", () => ({
  useI18n: () => ({ t: (k: string) => k }),
}));

import { OfflineBanner } from "./OfflineBanner";

function setOnline(value: boolean) {
  Object.defineProperty(navigator, "onLine", {
    get: () => value,
    configurable: true,
  });
}

afterEach(() => setOnline(true));

describe("OfflineBanner", () => {
  it("renders nothing while the browser reports a connection", () => {
    setOnline(true);
    const { container } = render(<OfflineBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("overlays the title bar row instead of pushing the shell down", () => {
    // The banner used to sit in the layout flow, which shifted the whole shell
    // (TopBar included) down by its height while the macOS traffic lights —
    // positioned by the window, not by CSS — stayed put and ended up on top of
    // the strip. It must stay a fixed overlay, 36px tall like TopBar, and above
    // an overlay edition's own top strip (z-[100]).
    setOnline(false);
    render(<OfflineBanner />);
    const banner = screen.getByText("offline.banner");
    expect(banner.className).toContain("fixed");
    expect(banner.className).toContain("top-0");
    expect(banner.className).toContain("h-[36px]");
    expect(banner.className).toContain("z-[110]");
  });

  it("appears when the connection drops and clears when it returns", () => {
    setOnline(true);
    render(<OfflineBanner />);
    expect(screen.queryByText("offline.banner")).toBeNull();

    setOnline(false);
    act(() => window.dispatchEvent(new Event("offline")));
    expect(screen.getByText("offline.banner")).toBeTruthy();

    setOnline(true);
    act(() => window.dispatchEvent(new Event("online")));
    expect(screen.queryByText("offline.banner")).toBeNull();
  });
});
