import { describe, expect, it } from "vitest";
import {
  isAutomationRunning,
  runStatusToLogStatus,
  runToLogStatus,
} from "./automation-run-status";
import type { AutomationRunItem } from "./automations-api";

function run(overrides: Partial<AutomationRunItem>): AutomationRunItem {
  return {
    run_id: "r1",
    automation_id: "a1",
    project_id: "p1",
    trigger_type: "manual",
    status: "success",
    triggered_at: 1000,
    started_at: null,
    completed_at: null,
    duration_ms: null,
    result_summary: null,
    error_code: null,
    error_message_key: null,
    session_id: null,
    created_files: [],
    task_status: null,
    ...overrides,
  };
}

describe("isAutomationRunning (PRD run-state table)", () => {
  it("never-run → not running", () => {
    expect(isAutomationRunning(null)).toBe(false);
    expect(isAutomationRunning(undefined)).toBe(false);
  });

  it.each(["queued", "running"] as const)("%s → running", (status) => {
    expect(isAutomationRunning(run({ status }))).toBe(true);
  });

  it.each(["success", "failed", "skipped", "interrupted_by_shutdown"] as const)(
    "settled chat run %s → not running",
    (status) => {
      expect(isAutomationRunning(run({ status }))).toBe(false);
    },
  );

  it("task active after success → running", () => {
    expect(
      isAutomationRunning(run({ status: "success", task_status: "active" })),
    ).toBe(true);
  });

  it("task paused after success → not running", () => {
    expect(
      isAutomationRunning(run({ status: "success", task_status: "paused" })),
    ).toBe(false);
  });

  it("run.status wins: queued + paused task → running", () => {
    expect(
      isAutomationRunning(run({ status: "queued", task_status: "paused" })),
    ).toBe(true);
  });
});

describe("runStatusToLogStatus", () => {
  it("maps raw statuses to log tones", () => {
    expect(runStatusToLogStatus("success")).toBe("ok");
    expect(runStatusToLogStatus("failed")).toBe("err");
    expect(runStatusToLogStatus("queued")).toBe("pending");
    expect(runStatusToLogStatus("running")).toBe("pending");
    expect(runStatusToLogStatus("skipped")).toBe("skip");
    expect(runStatusToLogStatus("interrupted_by_shutdown")).toBe("skip");
  });
});

describe("runToLogStatus (task_status preferred)", () => {
  it("prefers live task status over the frozen run status", () => {
    expect(runToLogStatus(run({ status: "success", task_status: "active" }))).toBe(
      "pending",
    );
    expect(
      runToLogStatus(run({ status: "success", task_status: "completed" })),
    ).toBe("ok");
    expect(runToLogStatus(run({ status: "success", task_status: "failed" }))).toBe(
      "err",
    );
  });

  it("falls back to run status for non-task runs", () => {
    expect(runToLogStatus(run({ status: "failed", task_status: null }))).toBe(
      "err",
    );
  });
});
