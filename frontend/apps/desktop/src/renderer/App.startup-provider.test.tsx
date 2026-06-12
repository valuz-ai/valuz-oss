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

vi.mock("./routes/router", () => ({
  AppRouter: () => <div>Desktop app ready</div>,
}));

vi.mock("@valuz/app/lib/onboarding", () => ({
  isOnboarded: () => true, // skip the providers probe in the ready case
}));

describe("startup screen under the platform provider", () => {
  beforeEach(() => {
    startupState = { ...initialStartupState };
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
