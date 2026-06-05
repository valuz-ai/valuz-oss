/**
 * Smoke tests for the inline Plan Card (VALUZ-CHATPLAN S3).
 *
 * Pins the rendering contract for the four status variants the
 * conversation flow has to handle: draft (action buttons) → active
 * (Open Task Detail) → terminal (View Final State), plus the
 * latest-vs-old visual gating that makes older cards read-only.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { PlanSubtask } from "@valuz/core";
import { PlanCard } from "./PlanCard";

const SUBTASKS: PlanSubtask[] = [
  {
    key: "extract",
    label: "Pull annual report",
    agent: "researcher",
    status: "planned",
    depends_on: [],
    parallel_group: null,
    goal: null,
    attempts: 0,
    review_criteria: null,
    review_feedback: null,
  },
  {
    key: "compare",
    label: "Industry comparison",
    agent: "researcher",
    status: "in_progress",
    depends_on: [],
    parallel_group: null,
    goal: null,
    attempts: 0,
    review_criteria: null,
    review_feedback: null,
  },
  {
    key: "report",
    label: "Final write-up",
    agent: "writer",
    status: "done",
    depends_on: ["extract", "compare"],
    parallel_group: null,
    goal: null,
    attempts: 0,
    review_criteria: null,
    review_feedback: null,
  },
];

describe("PlanCard", () => {
  it("should expose Execute/Abandon buttons for a draft + latest card", () => {
    const onExecute = vi.fn();
    const onAbandon = vi.fn();
    render(
      <PlanCard
        taskId="t1"
        taskTitle="Maotai research report"
        status="draft"
        planVersion={2}
        subtasks={SUBTASKS}
        isLatest
        onExecute={onExecute}
        onAbandon={onAbandon}
      />,
    );
    expect(screen.getByText(/Plan v2/)).toBeTruthy();
    expect(screen.getByText(/Maotai research report/)).toBeTruthy();
    expect((screen.getByText("Execute") as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect((screen.getByText("Abandon") as HTMLButtonElement).disabled).toBe(
      false,
    );
    expect(screen.getByText("(latest)")).toBeTruthy();
  });

  it("should disable Execute on a draft card that is not latest", () => {
    render(
      <PlanCard
        taskId="t1"
        taskTitle="Maotai research report"
        status="draft"
        planVersion={1}
        subtasks={SUBTASKS}
        isLatest={false}
        onExecute={vi.fn()}
        onAbandon={vi.fn()}
      />,
    );
    expect((screen.getByText("Execute") as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByText("Abandon") as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(screen.queryByText("(latest)")).toBeNull();
  });

  it("should expose Open Task Detail for an active card", () => {
    render(
      <PlanCard
        taskId="t1"
        taskTitle="Maotai research report"
        status="active"
        planVersion={3}
        subtasks={SUBTASKS}
        isLatest
        onOpenDetail={vi.fn()}
      />,
    );
    expect(screen.getByText("Open Task Detail")).toBeTruthy();
    expect(screen.queryByText("Execute")).toBeNull();
  });

  it("should expose View Final State for a terminal card", () => {
    render(
      <PlanCard
        taskId="t1"
        taskTitle="Maotai research report"
        status="completed"
        planVersion={4}
        subtasks={SUBTASKS}
        isLatest
        onOpenDetail={vi.fn()}
      />,
    );
    expect(screen.getByText("View Final State")).toBeTruthy();
    expect(screen.queryByText("Open Task Detail")).toBeNull();
  });
});
