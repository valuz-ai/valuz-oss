import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AutomationDetailPage } from "./AutomationDetailPage";

const h = vi.hoisted(() => ({
  get: vi.fn(),
  listRuns: vi.fn(),
  runNow: vi.fn(),
  getRun: vi.fn(),
  cancelRun: vi.fn(),
  setHideHeader: vi.fn(),
  setContentInnerClassName: vi.fn(),
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
  Button: ({
    children,
    size,
    variant,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
    size?: string;
    variant?: string;
  }) => <button {...props}>{children}</button>,
  DeleteConfirmDialog: () => null,
  EmptyState: () => null,
  PageLoader: () => null,
  StatusPill: ({ label }: { label: string }) => <span>{label}</span>,
  Dialog: ({
    open,
    children,
  }: {
    open?: boolean;
    children?: React.ReactNode;
  }) => (open ? <div>{children}</div> : null),
  DialogContent: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogFooter: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  FormField: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
  Textarea: (props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => (
    <textarea {...props} />
  ),
  LoadingState: () => null,
}));

const DEFAULT_DETAIL = {
  id: "auto-1",
  automation_id: "auto-1",
  name: "Research",
  project_id: "project-1",
  trigger: { kind: "manual" },
  execution: { kind: "agent", mode: "chat" },
  input: { kind: "none" },
  result: { kind: "conversation" },
};

function mount(status: string, overrides: Record<string, unknown> = {}) {
  h.get.mockResolvedValue({ ...DEFAULT_DETAIL, status, ...overrides });
  return render(
    <MemoryRouter initialEntries={["/automations/auto-1"]}>
      <Routes>
        <Route
          path="/automations/:automationId"
          element={<AutomationDetailPage />}
        />
      </Routes>
    </MemoryRouter>,
  );
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
    h.runNow.mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    mount("enabled");
    const button = await screen.findByRole("button", { name: "cron.runNow" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(h.runNow).toHaveBeenCalledExactlyOnceWith("auto-1");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    await act(async () => finish());
    await waitFor(() =>
      expect((button as HTMLButtonElement).disabled).toBe(false),
    );
  });

  it.each([
    ["running", "cron.running"],
    ["queued", "automation.execStatusPending"],
  ])("distinguishes %s from queueing", async (status, label) => {
    h.listRuns.mockResolvedValue({
      runs: [
        {
          run_id: "run-1",
          status,
          task_status: null,
          session_id: "session-1",
          triggered_at: Date.now(),
        },
      ],
    });
    mount("enabled");
    expect(await screen.findByText(label)).toBeTruthy();
  });

  it("renders timeout and cancelled statuses without crashing", async () => {
    h.listRuns.mockResolvedValue({
      runs: [
        {
          run_id: "run-timeout",
          status: "timeout",
          task_status: null,
          session_id: "session-1",
          triggered_at: Date.now(),
        },
        {
          run_id: "run-cancelled",
          status: "cancelled",
          task_status: null,
          session_id: "session-2",
          triggered_at: Date.now(),
        },
      ],
    });
    mount("enabled");
    expect(
      await screen.findByText("automation.execStatusTimeout"),
    ).toBeTruthy();
    expect(
      await screen.findByText("automation.execStatusCancelled"),
    ).toBeTruthy();
  });

  it("lists a code run that never produced a session (has_artifact/executor_ref instead)", async () => {
    h.listRuns.mockResolvedValue({
      runs: [
        {
          run_id: "run-code-1",
          status: "success",
          task_status: null,
          session_id: null,
          triggered_at: Date.now(),
          has_artifact: true,
          executor_ref: "local:1234",
          trigger_type: "cron",
          result_summary: "Code run result",
        },
      ],
    });
    mount("enabled", {
      execution: {
        kind: "code",
        runtime: "python",
        entry: "automation.py",
        timeout_seconds: 600,
      },
    });
    expect(await screen.findByText("Code run result")).toBeTruthy();
  });
});
