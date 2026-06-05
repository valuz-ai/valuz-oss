import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import type { TurnDiffSummary } from "./diff-aggregator";
import { TurnDiffSummaryCard } from "./TurnDiffSummaryCard";

const summary: TurnDiffSummary = {
  changes: [
    {
      file_path: "/repo/src/foo.ts",
      additions: 3,
      deletions: 1,
      unified_diff:
        "--- /repo/src/foo.ts\n+++ /repo/src/foo.ts\n@@ -1,1 +1,3 @@\n-old\n+a\n+b\n+c",
      has_error: false,
    },
    {
      file_path: "/repo/src/bar.ts",
      additions: 2,
      deletions: 0,
      unified_diff:
        "--- /repo/src/bar.ts\n+++ /repo/src/bar.ts\n@@ -0,0 +1,2 @@\n+x\n+y",
      has_error: true,
    },
  ],
  total_additions: 5,
  total_deletions: 1,
};

// Card body defaults to collapsed; tests that need the file rows have
// to click the header chevron first.
const expandCard = () =>
  fireEvent.click(screen.getByTestId("turn-diff-card-toggle"));

describe("TurnDiffSummaryCard", () => {
  it("should render header totals matching the summary", () => {
    render(<TurnDiffSummaryCard summary={summary} />);
    expect(screen.getByText("2 个文件已更改")).toBeTruthy();
    expect(screen.getByText("+5")).toBeTruthy();
    expect(screen.getByText("−1")).toBeTruthy();
  });

  it("should start with the file-row body collapsed", () => {
    render(<TurnDiffSummaryCard summary={summary} />);
    expect(screen.queryByTestId("turn-diff-body")).toBeNull();
  });

  it("should keep diff bodies hidden until the row chevron is clicked", () => {
    render(<TurnDiffSummaryCard summary={summary} />);
    expandCard();
    expect(screen.queryByText(/\+a/)).toBeNull();
    fireEvent.click(screen.getAllByLabelText("展开")[0]!);
    expect(screen.getByText(/\+a/)).toBeTruthy();
  });

  it("should open the side drawer with every file's diff when 审核 is clicked", () => {
    render(<TurnDiffSummaryCard summary={summary} />);
    expect(screen.queryByTestId("turn-diff-review-drawer")).toBeNull();

    fireEvent.click(screen.getByTestId("turn-diff-review"));

    const drawer = screen.getByTestId("turn-diff-review-drawer");
    expect(drawer).toBeTruthy();
    // Every file's diff is rendered inside the drawer body.
    expect(within(drawer).getByText(/\+a/)).toBeTruthy();
    expect(within(drawer).getByText(/\+x/)).toBeTruthy();
  });

  it("should keep 审核 visible whether the card body is collapsed or expanded", () => {
    render(<TurnDiffSummaryCard summary={summary} />);
    // Default — collapsed.
    expect(screen.getByTestId("turn-diff-review")).toBeTruthy();
    expandCard();
    expect(screen.getByTestId("turn-diff-review")).toBeTruthy();
  });

  it("should call onRevealFile with the absolute path when the external-link icon is clicked", () => {
    const onReveal = vi.fn();
    render(<TurnDiffSummaryCard summary={summary} onRevealFile={onReveal} />);
    expandCard();
    const buttons = screen.getAllByLabelText("在访达中显示");
    fireEvent.click(buttons[0]!);
    expect(onReveal).toHaveBeenCalledWith("/repo/src/foo.ts");
  });

  it("should hide the external-link icon when onRevealFile is omitted", () => {
    render(<TurnDiffSummaryCard summary={summary} />);
    expandCard();
    expect(screen.queryAllByLabelText("在访达中显示")).toHaveLength(0);
  });

  it("should render a 失败 badge for rows with has_error: true", () => {
    render(<TurnDiffSummaryCard summary={summary} />);
    expandCard();
    const card = screen.getByTestId("turn-diff-summary");
    expect(within(card).getAllByText("失败")).toHaveLength(1);
  });

  it("should toggle the file-row body open and closed via the card-level chevron", () => {
    render(<TurnDiffSummaryCard summary={summary} />);
    expect(screen.queryByTestId("turn-diff-body")).toBeNull();
    expandCard();
    expect(screen.getByTestId("turn-diff-body")).toBeTruthy();
    fireEvent.click(screen.getByTestId("turn-diff-card-toggle"));
    expect(screen.queryByTestId("turn-diff-body")).toBeNull();
    // Header totals stay visible regardless of fold state.
    expect(screen.getByText("2 个文件已更改")).toBeTruthy();
  });
});
