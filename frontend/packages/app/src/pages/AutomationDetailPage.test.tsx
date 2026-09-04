import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AutomationDetailPage } from "./AutomationDetailPage";

const h = vi.hoisted(() => ({
  get: vi.fn(), listRuns: vi.fn(), runNow: vi.fn(),
  setHideHeader: vi.fn(), setContentInnerClassName: vi.fn(),
}));
vi.mock("@valuz/core", () => ({
  useEntityOrigin: () => "local",
  useTranslation: () => ({ t: (key: string) => key }),
  automationsApi: h,
  agentsApi: { listMembers: async () => ({ agents: [] }) },
}));
vi.mock("@valuz/app/layout", () => ({ useProjectOutlet: () => h }));
vi.mock("@valuz/app/components", () => ({
  CreateAutomationDialog: () => null,
  formatCreatedAt: () => "now",
}));
vi.mock("@valuz/ui", () => ({
  Button: ({ children, size, variant, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement> & { size?: string; variant?: string }) => <button {...props}>{children}</button>,
  DeleteConfirmDialog: () => null,
  EmptyState: () => null,
  PageLoader: () => null,
  StatusPill: ({ label }: { label: string }) => <span>{label}</span>,
}));

function mount(status: string) {
  h.get.mockResolvedValue({ id: "auto-1", name: "Research", status, project_id: "project-1", trigger: { kind: "manual" } });
  return render(<MemoryRouter initialEntries={["/automations/auto-1"]}><Routes><Route path="/automations/:automationId" element={<AutomationDetailPage />} /></Routes></MemoryRouter>);
}

describe("Automation execution controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    h.listRuns.mockResolvedValue({ runs: [] });
  });

  it("does not submit a paused automation", async () => {
    mount("paused");
    const button = await screen.findByRole("button", { name: "cron.runNow" });
    expect((button as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(button);
    expect(h.runNow).not.toHaveBeenCalled();
  });

  it("submits once and disables repeat clicks while the request is pending", async () => {
    let finish!: () => void;
    h.runNow.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
    mount("enabled");
    const button = await screen.findByRole("button", { name: "cron.runNow" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(h.runNow).toHaveBeenCalledExactlyOnceWith("auto-1");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    await act(async () => finish());
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  });

  it.each([["running", "cron.running"], ["queued", "automation.execStatusPending"]])("distinguishes %s from queueing", async (status, label) => {
    h.listRuns.mockResolvedValue({ runs: [{ run_id: "run-1", status, task_status: null, session_id: "session-1", triggered_at: Date.now() }] });
    mount("enabled");
    expect(await screen.findByText(label)).toBeTruthy();
  });
});
