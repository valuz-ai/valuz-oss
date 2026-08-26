import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WindowControls } from "./WindowControls";

const noop = () => {};

/**
 * The maximize button is the one control whose glyph is state-dependent, and
 * it shipped inverted: a maximized window showed ``Maximize2``'s outward
 * arrows ("enlarge") where Windows shows the restore-down glyph.  These tests
 * pin both the accessible name and the drawn shape so the pair cannot drift
 * apart again.
 */
describe("WindowControls", () => {
  const maximizeButton = (isMaximized: boolean) => {
    render(
      <WindowControls
        onMinimize={noop}
        onMaximize={noop}
        onClose={noop}
        isMaximized={isMaximized}
      />,
    );
    return screen.getByRole("button", {
      name: isMaximized ? "Restore" : "Maximize",
    });
  };

  it("draws a single square when the window is not maximized", () => {
    const button = maximizeButton(false);
    const svg = button.querySelector("svg");

    expect(svg?.querySelectorAll("rect")).toHaveLength(1);
    // No second shape behind it — a plain square, not the restore glyph.
    expect(svg?.querySelectorAll("path")).toHaveLength(0);
  });

  it("draws the Windows restore glyph when the window is maximized", () => {
    const button = maximizeButton(true);
    const svg = button.querySelector("svg");

    // Front square plus the trailing edges of the one behind it.
    expect(svg?.querySelectorAll("rect")).toHaveLength(1);
    expect(svg?.querySelectorAll("path")).toHaveLength(1);

    // Front square sits lower-left; the second shape starts above and to the
    // right of it. lucide's ``Copy`` is this construction mirrored, which is
    // why it cannot stand in for the Windows glyph.
    const rect = svg?.querySelector("rect");
    expect(rect?.getAttribute("x")).toBe("3");
    expect(rect?.getAttribute("y")).toBe("8");
  });

  it("never shows the same glyph for both states", () => {
    const { unmount } = render(
      <WindowControls
        onMinimize={noop}
        onMaximize={noop}
        onClose={noop}
        isMaximized={false}
      />,
    );
    const collapsed = screen
      .getByRole("button", { name: "Maximize" })
      .querySelector("svg")?.innerHTML;
    unmount();

    render(
      <WindowControls
        onMinimize={noop}
        onMaximize={noop}
        onClose={noop}
        isMaximized
      />,
    );
    const expanded = screen
      .getByRole("button", { name: "Restore" })
      .querySelector("svg")?.innerHTML;

    expect(collapsed).toBeTruthy();
    expect(expanded).toBeTruthy();
    expect(collapsed).not.toBe(expanded);
  });

  it("wires each button to its handler", () => {
    const onMinimize = vi.fn();
    const onMaximize = vi.fn();
    const onClose = vi.fn();
    render(
      <WindowControls
        onMinimize={onMinimize}
        onMaximize={onMaximize}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Minimize" }));
    fireEvent.click(screen.getByRole("button", { name: "Maximize" }));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));

    expect(onMinimize).toHaveBeenCalledOnce();
    expect(onMaximize).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });
});
