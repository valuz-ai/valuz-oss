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
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const initialStartupState = {
  services: [],
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
