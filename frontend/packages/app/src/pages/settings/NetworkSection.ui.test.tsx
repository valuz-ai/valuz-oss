import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@valuz/core", () => ({
  useRunningRuns: () => ({ runs: [{}, {}], count: 2 }),
  useTranslation: () => ({
    t: (key: string, values?: { count?: number }) =>
      values?.count === undefined ? key : `${key}:${values.count}`,
  }),
}));

vi.mock("sonner", () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}));

import { NetworkSection } from "./NetworkSection";

describe("NetworkSection mode switching", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    Reflect.deleteProperty(window, "valuzDesktop");
  });

  it("lets the user confirm interruption before switching an active task", async () => {
    const invoke = vi.fn(async (channel: string) => {
      if (channel === "desktop_get_capabilities") {
        return {
          schemaVersion: 1,
          networkEgress: {
            available: true,
            contractVersion: 1,
            policy: {
              defaultMode: "auto",
              allowedModes: ["off", "auto"],
              userConfigurable: true,
            },
          },
        };
      }
      if (channel === "egress_get_status") {
        return {
          mode: "off",
          enabled: true,
          started: false,
          emergencyOverride: false,
          snapshotCount: 0,
          diagnosticEventCount: 0,
        };
      }
      if (channel === "egress_set_mode") {
        return {
          mode: "auto",
          enabled: true,
          started: true,
          emergencyOverride: false,
          snapshotCount: 0,
          diagnosticEventCount: 0,
        };
      }
      return [];
    });
    Object.defineProperty(window, "valuzDesktop", {
      configurable: true,
      value: { invoke, on: vi.fn(), off: vi.fn() },
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);

    render(<NetworkSection />);

    const button = await screen.findByRole("button", {
      name: "settings.network.useAuto",
    });
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(button);

    await waitFor(() => {
      expect(confirm).toHaveBeenCalledWith(
        "settings.network.activeRunsConfirm:2",
      );
      expect(invoke).toHaveBeenCalledWith("egress_set_mode", {
        mode: "auto",
        interruptActiveRuns: true,
      });
    });
  });

  it("does not poll unsupported desktop hosts", async () => {
    const invoke = vi.fn(async (channel: string) => {
      void channel;
      throw new Error("No handler registered for desktop_get_capabilities");
    });
    Object.defineProperty(window, "valuzDesktop", {
      configurable: true,
      value: { invoke, on: vi.fn(), off: vi.fn() },
    });

    render(<NetworkSection />);

    expect(
      await screen.findByText("settings.network.canaryDisabled"),
    ).toBeTruthy();
    expect(invoke).toHaveBeenCalledWith("desktop_get_capabilities");
    expect(
      invoke.mock.calls.every(
        ([channel]) => channel === "desktop_get_capabilities",
      ),
    ).toBe(true);
  });

  it("does not poll a desktop host with an incompatible egress contract", async () => {
    const invoke = vi.fn(async (channel: string) => {
      if (channel === "desktop_get_capabilities") {
        return {
          schemaVersion: 1,
          networkEgress: {
            available: true,
            contractVersion: 2,
            policy: {
              defaultMode: "auto",
              allowedModes: ["off", "auto"],
              userConfigurable: true,
            },
          },
        };
      }
      throw new Error(`Unexpected network IPC call: ${channel}`);
    });
    Object.defineProperty(window, "valuzDesktop", {
      configurable: true,
      value: { invoke, on: vi.fn(), off: vi.fn() },
    });

    render(<NetworkSection />);

    expect(
      await screen.findByText("settings.network.canaryDisabled"),
    ).toBeTruthy();
    expect(invoke).toHaveBeenCalledWith("desktop_get_capabilities");
    expect(
      invoke.mock.calls.every(
        ([channel]) => channel === "desktop_get_capabilities",
      ),
    ).toBe(true);
  });

  it("renders an honest desktop-only fallback in WebUI", () => {
    render(<NetworkSection />);

    expect(screen.getByText("settings.network.desktopOnly")).toBeTruthy();
  });
});
