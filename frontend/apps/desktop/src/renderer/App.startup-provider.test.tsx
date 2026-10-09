/**
 * Regression: the not-ready branch (StartupScreen) must render inside
 * ElectronPlatformProvider.
 *
 * StartupScreen calls usePlatform() for the frameless-window controls
 * (#81). App.tsx used to render it OUTSIDE the provider, so the renderer
 * crashed with "usePlatform() must be used inside <PlatformProvider>"
 * before the backend became ready — a white window on every dev boot.
 *
 * The desktop-startup hook is mocked directly (rather than the transport)
 * so this test pins exactly one thing: ready=false renders the startup
 * screen without a provider crash, regardless of how the bootstrap
 * sequencing evolves.
 */
import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ServiceInfo } from "@valuz/shared";

const initialStartupState = {
  services: [] as ServiceInfo[],
  logs: [],
  loading: false,
  checking: false,
  ready: false,
  error: null,
  retry: () => undefined,
};
let startupState = { ...initialStartupState };

vi.mock("./hooks/use-desktop-startup", () => ({
  useDesktopStartup: () => startupState,
}));

let routerThrows = false;
vi.mock("./routes/router", () => ({
  AppRouter: () => {
    if (routerThrows) throw new Error("boom");
    return <div>Desktop app ready</div>;
  },
}));

vi.mock("@valuz/app/lib/onboarding", () => ({
  isOnboarded: () => true, // skip the providers probe in the ready case
}));

describe("startup screen under the platform provider", () => {
  beforeEach(() => {
    startupState = { ...initialStartupState };
    routerThrows = false;
  });
  afterEach(() => vi.useRealTimers());

  it("enters immediately after a warm readiness probe without showing the boot splash", async () => {
    startupState.checking = true;
    const { rerender } = render(<App />);
    expect(screen.queryByRole("heading", { name: /VALUZ/i })).toBeNull();

    startupState = { ...startupState, checking: false, ready: true };
    rerender(<App />);
    expect(await screen.findByText("Desktop app ready")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /VALUZ/i })).toBeNull();
  });

  it("keeps a cold boot splash mounted until its completion dwell ends", async () => {
    vi.useFakeTimers();
    startupState.services = [{ name: "backend", status: "starting", port: 8000, pid: null }];
    const { container, rerender } = render(<App />);
    expect(screen.getByRole("heading", { name: /VALUZ/i })).toBeTruthy();

    startupState.ready = true;
    startupState.services = [{ name: "backend", status: "running", port: 8000, pid: 100 }];
    await act(async () => rerender(<App />));
    expect(screen.queryByText("Desktop app ready")).toBeNull();
    act(() => vi.advanceTimersByTime(3000));
    expect(container.querySelector(".splash-progress-pct")?.textContent).toBe("100%");
    expect(screen.queryByText("Desktop app ready")).toBeNull();

    act(() => vi.advanceTimersByTime(500));
    expect(screen.getByText("Desktop app ready")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: /VALUZ/i })).toBeNull();
  });

  it("shows a loader (not a blank window) while startup is still checking", () => {
    // Regression: the checking / setup-probe gates rendered ``null`` — a
    // plain white window with no hint of life.
    startupState.checking = true;
    const { container } = render(<App />);

    expect(container.querySelector('img[src="/logo.png"]')).not.toBeNull();
  });

  it("degrades to the error fallback when the routed shell throws", async () => {
    // Regression: with no boundary above the router, an uncaught render
    // throw unmounted the whole tree — a permanently white window.
    startupState.ready = true;
    routerThrows = true;
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(<App />);
      expect(await screen.findByText("Something went wrong.")).toBeTruthy();
      expect(await screen.findByRole("button", { name: "Retry" })).toBeTruthy();
    } finally {
      errSpy.mockRestore();
    }
  });

  it("renders the not-ready branch without a usePlatform provider crash", async () => {
    // A bare render throwing "usePlatform() must be used inside
    // <PlatformProvider>" is exactly the regression this guards against.
    startupState.ready = false;
    render(<App />);

    expect(await screen.findByRole("heading", { name: /VALUZ/i })).toBeTruthy();
  });

  it("renders the routed shell once ready", async () => {
    startupState.ready = true;
    render(<App />);

    expect(await screen.findByText("Desktop app ready")).toBeTruthy();
  });
});
