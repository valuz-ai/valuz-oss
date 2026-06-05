import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";

const invokeMock =
  vi.fn<
    (command: string, args?: Record<string, unknown>) => Promise<unknown>
  >();
const listenMock = vi.fn();
const closeMock = vi.fn();

vi.mock("@valuz/core", () => ({
  createTransport: () => ({
    invoke: (command: string, args?: Record<string, unknown>) =>
      invokeMock(command, args),
    listen: (...args: unknown[]) => listenMock(...args),
    close: () => closeMock(),
  }),
}));

vi.mock("./routes/router", () => ({
  AppRouter: () => <div>Desktop app ready</div>,
}));

describe("desktop app startup flow", () => {
  beforeEach(() => {
    invokeMock.mockReset();
    listenMock.mockReset();
    closeMock.mockReset();
    listenMock.mockReturnValue(() => undefined);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows the startup screen while the initial runtime snapshot is loading", async () => {
    invokeMock.mockImplementation(
      () =>
        new Promise(() => {
          // Keep the runtime bootstrap pending so the startup shell stays visible.
        }),
    );

    render(<App />);

    expect(await screen.findByRole("heading", { name: /VALUZ/ })).toBeTruthy();
    expect(screen.getByText(/正在唤醒桌面运行时/)).toBeTruthy();
  });

  it("starts stopped services before revealing the routed desktop shell", async () => {
    invokeMock.mockImplementation(async (command) => {
      if (command === "get_services_status") {
        return [
          {
            name: "agent-server",
            status: "stopped",
            port: 19100,
            pid: null,
            detail: "Pending",
          },
        ];
      }

      if (command === "start_all_services") {
        return [
          {
            name: "agent-server",
            status: "running",
            port: 19100,
            pid: 100,
            detail: "Ready",
          },
        ];
      }

      if (command === "get_service_logs") {
        return ["[12:00:00.000] Service started"];
      }

      return { ok: true };
    });

    render(<App />);

    expect(await screen.findByText("Desktop app ready")).toBeTruthy();
    expect(invokeMock).toHaveBeenCalledWith("start_all_services", undefined);
    expect(invokeMock).toHaveBeenCalledWith("get_service_logs", {
      serviceName: "agent-server",
    });
  });
});
