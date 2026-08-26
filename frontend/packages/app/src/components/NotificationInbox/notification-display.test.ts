import { describe, expect, it } from "vitest";

import type { NotificationEntry } from "@valuz/core";

import { notificationDisplay } from "./notification-display";

const base: NotificationEntry = {
  id: "n1",
  kind: "question",
  title: "architect",
  body: "选哪种布局？",
  route: "/tasks/t1",
  action: "answer",
  urgency: "actionable",
  task_id: "t1",
  project_id: "w1",
  session_id: "s1",
  pending_id: "p1",
  payload: {},
  created_at: 1,
  read_at: null,
  resolved_at: null,
};

describe("notificationDisplay", () => {
  it("question: composes agent title, keeps route, tags by pending", () => {
    const d = notificationDisplay(base);
    expect(d.title).toContain("architect");
    expect(d.body).toBe("选哪种布局？");
    expect(d.route).toBe("/tasks/t1");
    expect(d.tag).toBe("question:p1");
  });

  it("task_failed: failure title, per-task tag", () => {
    const d = notificationDisplay({
      ...base,
      kind: "task_failed",
      title: "季度报告",
      body: "lead crashed",
      action: "resume",
      route: "/tasks/t9",
      task_id: "t9",
    });
    expect(d.body).toBe("lead crashed");
    expect(d.route).toBe("/tasks/t9");
    expect(d.tag).toBe("failure:t9");
  });

  it("task_failed with no body falls back to a generic line", () => {
    const d = notificationDisplay({
      ...base,
      kind: "task_failed",
      title: "季度报告",
      body: "",
      task_id: "t9",
    });
    expect(d.body).toContain("季度报告");
  });

  it("falls back to session route when there is no task", () => {
    const d = notificationDisplay({ ...base, route: null, task_id: null });
    expect(d.route).toBe("/conversation/s1");
  });

  it("backup_failed: localized title despite empty backend title, single tag", () => {
    const d = notificationDisplay({
      ...base,
      kind: "backup_failed",
      title: "",
      body: "not enough free space",
      route: "/settings?tab=backup",
      task_id: null,
      session_id: null,
      pending_id: null,
    });
    expect(d.title).not.toBe(""); // localized label composed frontend-side
    expect(d.body).toBe("not enough free space");
    expect(d.route).toBe("/settings?tab=backup");
    expect(d.tag).toBe("backup_failed");
  });
});

describe("notificationDisplay body clamp", () => {
  it("caps a multi-KB provider error dump at 300 chars for alert surfaces", () => {
    const d = notificationDisplay({
      ...base,
      kind: "run_failed",
      title: "analyst",
      body: "x".repeat(5000),
    });
    expect(d.body.length).toBe(300);
    expect(d.body.endsWith("…")).toBe(true);
  });

  it("leaves short bodies untouched", () => {
    const d = notificationDisplay({ ...base, kind: "run_failed", body: "rate limited" });
    expect(d.body).toBe("rate limited");
  });
});
