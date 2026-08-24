import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { RightPanelControls } from "./RightPanelControls";

vi.mock("@valuz/ui", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => (
    <span>{children}</span>
  ),
  TooltipProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));

const labels = {
  collapse: "Collapse right panel",
  expand: "Expand right panel",
  maximize: "Maximize right panel",
  restore: "Restore right panel",
};

describe("RightPanelControls", () => {
  it("places maximize before collapse and invokes the matching actions", () => {
    const onToggleCollapsed = vi.fn();
    const onToggleMaximized = vi.fn();
    render(
      <RightPanelControls
        collapsed={false}
        maximized={false}
        labels={labels}
        onToggleCollapsed={onToggleCollapsed}
        onToggleMaximized={onToggleMaximized}
      />,
    );

    const maximize = screen.getByRole("button", { name: labels.maximize });
    const collapse = screen.getByRole("button", { name: labels.collapse });
    expect(
      maximize.compareDocumentPosition(collapse) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    fireEvent.click(maximize);
    fireEvent.click(collapse);
    expect(onToggleMaximized).toHaveBeenCalledTimes(1);
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1);
  });

  it("shows restore while maximized and hides the size control when collapsed", () => {
    const { rerender } = render(
      <RightPanelControls
        collapsed={false}
        maximized
        labels={labels}
        onToggleCollapsed={() => undefined}
        onToggleMaximized={() => undefined}
      />,
    );

    expect(screen.getByRole("button", { name: labels.restore })).toBeTruthy();

    rerender(
      <RightPanelControls
        collapsed
        maximized={false}
        labels={labels}
        onToggleCollapsed={() => undefined}
        onToggleMaximized={() => undefined}
      />,
    );
    expect(screen.queryByRole("button", { name: labels.maximize })).toBeNull();
    expect(screen.getByRole("button", { name: labels.expand })).toBeTruthy();
  });
});
