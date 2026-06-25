import { describe, expect, it } from "vitest";
import {
  automationItemSortKey,
  compareActivityEntries,
  runSummarySortKey,
  type ActivitySortKey,
} from "./activity-sort";
import type { AutomationItem } from "./automations-api";
import type { RunSummary } from "./runs-api";

const key = (o: Partial<ActivitySortKey>): ActivitySortKey => ({
  isRunning: false,
  activeTs: 0,
  id: "x",
  ...o,
});

describe("compareActivityEntries", () => {
  it("pins running entries above non-running", () => {
    const out = [
      key({ id: "a", isRunning: false, activeTs: 9999 }),
      key({ id: "b", isRunning: true, activeTs: 1 }),
    ].sort(compareActivityEntries);
    expect(out.map((e) => e.id)).toEqual(["b", "a"]);
  });

  it("orders within a group by activeTs desc", () => {
    const out = [
      key({ id: "a", activeTs: 100 }),
      key({ id: "b", activeTs: 300 }),
      key({ id: "c", activeTs: 200 }),
    ].sort(compareActivityEntries);
    expect(out.map((e) => e.id)).toEqual(["b", "c", "a"]);
  });

  it("breaks timestamp ties by id asc (stable)", () => {
    const out = [
      key({ id: "z", activeTs: 5 }),
      key({ id: "a", activeTs: 5 }),
      key({ id: "m", activeTs: 5 }),
    ].sort(compareActivityEntries);
    expect(out.map((e) => e.id)).toEqual(["a", "m", "z"]);
  });

  it("mixes sources: running pinned, then recency across types", () => {
    const keys = [
      key({ id: "auto-idle", isRunning: false, activeTs: 500 }),
      key({ id: "chat-run", isRunning: true, activeTs: 10 }),
      key({ id: "auto-run", isRunning: true, activeTs: 20 }),
    ].sort(compareActivityEntries);
    expect(keys.map((e) => e.id)).toEqual(["auto-run", "chat-run", "auto-idle"]);
  });
});

describe("automationItemSortKey", () => {
  const base: AutomationItem = {
    automation_id: "a1",
    project_id: "p1",
    project_name: "P",
    project_kind: "chat",
    name: "n",
    agent_kind: "library_agent",
    agent_slug: "s",
    agent_name: "Agent",
    action_kind: "chat",
    trigger: { kind: "manual" },
    trigger_human_readable: "manual",
    status: "enabled",
    next_run_at: null,
    last_run_at: null,
    last_run_status: null,
    is_running: false,
    created_at: 42,
  };

  it("reads is_running for run state", () => {
    expect(automationItemSortKey({ ...base, is_running: true }).isRunning).toBe(
      true,
    );
  });

  it("activeTs gradient: last_run_at ?? next_run_at ?? created_at", () => {
    expect(automationItemSortKey({ ...base }).activeTs).toBe(42); // created_at
    expect(
      automationItemSortKey({ ...base, next_run_at: 100 }).activeTs,
    ).toBe(100);
    expect(
      automationItemSortKey({ ...base, next_run_at: 100, last_run_at: 200 })
        .activeTs,
    ).toBe(200);
  });
});

describe("runSummarySortKey", () => {
  const run = {
    session_id: "s1",
    source_kind: "automation",
    automation_id: "a1",
    updated_at: 7,
  } as RunSummary;

  it("uses caller-supplied running flag + updated_at + session_id", () => {
    expect(runSummarySortKey(run, true)).toEqual({
      isRunning: true,
      activeTs: 7,
      id: "s1",
    });
  });
});
