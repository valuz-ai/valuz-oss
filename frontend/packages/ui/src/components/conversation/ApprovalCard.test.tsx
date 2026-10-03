/** @vitest-environment jsdom */
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// MarkdownContent pulls in Streamdown/katex/shiki — stub it so we can assert
// the plan text is handed to the markdown renderer (not the generic fallback).
vi.mock("./MarkdownContent", () => ({
  MarkdownContent: ({ content }: { content: string }) => (
    <pre data-testid="markdown-content">{content}</pre>
  ),
}));

import { ApprovalCard, type ApprovalCardProps } from "./ApprovalCard";

const baseProps: ApprovalCardProps = {
  pendingId: "2dca9135-1abc-48f2-8615-35fa3eb53c31",
  subject: "tool_input",
  payload: {},
  availableDecisions: ["approve", "reject"],
  sessionRulePreviewDisplay: null,
  originalInput: null,
  onApprove: () => {},
  onReject: () => {},
  onApproveWithChanges: () => {},
  onApproveForSession: () => {},
};

describe("ApprovalCard — exit_plan_mode", () => {
  it("renders the plan as markdown, not the '(unknown-tool)' fallback", () => {
    render(
      <ApprovalCard
        {...baseProps}
        subject="exit_plan_mode"
        payload={{ plan: "# UI Redesign\n\nGoal." }}
      />,
    );
    // Plan goes to the markdown renderer …
    expect(screen.getByTestId("markdown-content").textContent).toContain(
      "# UI Redesign",
    );
    // … with the proper title (zh-CN is the default test locale) …
    expect(screen.getByText("计划待确认")).toBeTruthy();
    // … and NOT the generic tool_input fallback.
    expect(screen.queryByText("(unknown-tool)")).toBeNull();
    expect(screen.queryByText("工具调用请求")).toBeNull();
  });

  it("still shows the generic tool_input fallback for an unmodeled tool", () => {
    render(
      <ApprovalCard
        {...baseProps}
        subject="tool_input"
        payload={{ input: { x: 1 } }}
      />,
    );
    expect(screen.getByText("(unknown-tool)")).toBeTruthy();
    expect(screen.queryByTestId("markdown-content")).toBeNull();
  });
});

describe("ApprovalCard — extraActions", () => {
  it("renders exactly the two decision buttons while extraActions is unset", () => {
    render(<ApprovalCard {...baseProps} />);
    expect(
      screen.getAllByRole("button").map((b) => b.textContent?.trim()),
    ).toEqual(["拒绝", "批准"]);
  });

  it("puts the host buttons at the start of the action row, before Reject", () => {
    render(
      <ApprovalCard
        {...baseProps}
        extraActions={<button data-testid="ext-action">extra</button>}
      />,
    );
    const extra = screen.getByTestId("ext-action");
    const row = extra.parentElement!;
    // Same flex row as the decision buttons, no wrapper around the node.
    expect(row.className).toContain("flex");
    expect(extra).toBe(row.firstElementChild);
    expect(extra.nextElementSibling?.textContent?.trim()).toBe("拒绝");
  });
});
