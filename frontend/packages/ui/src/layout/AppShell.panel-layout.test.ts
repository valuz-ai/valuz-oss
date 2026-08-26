import { describe, expect, it } from "vitest";
import {
  NORMAL_PANEL_LAYOUT,
  resolveRightPanelLayoutTransition,
} from "./right-panel-layout";

describe("resolveRightPanelLayoutTransition", () => {
  it("captures the manual layout before maximizing to an even split", () => {
    expect(
      resolveRightPanelLayoutTransition({
        currentLayout: { "shell-main": 72, "shell-right": 28 },
        maximized: true,
        normalLayout: null,
        wasMaximized: false,
      }),
    ).toEqual({
      normalLayout: { "shell-main": 72, "shell-right": 28 },
      targetLayout: { "shell-main": 50, "shell-right": 50 },
    });
  });

  it("restores the captured manual layout when leaving maximized mode", () => {
    const normalLayout = { "shell-main": 64, "shell-right": 36 };
    expect(
      resolveRightPanelLayoutTransition({
        currentLayout: { "shell-main": 50, "shell-right": 50 },
        maximized: false,
        normalLayout,
        wasMaximized: true,
      }),
    ).toEqual({ normalLayout, targetLayout: normalLayout });
  });

  it("should restore the standard split when no manual layout was captured", () => {
    // Without this the panel stayed at the maximized 50/50 and the restore
    // button read as a no-op.
    expect(
      resolveRightPanelLayoutTransition({
        currentLayout: { "shell-main": 50, "shell-right": 50 },
        maximized: false,
        normalLayout: null,
        wasMaximized: true,
      }),
    ).toEqual({
      normalLayout: NORMAL_PANEL_LAYOUT,
      targetLayout: NORMAL_PANEL_LAYOUT,
    });
  });

  it("should keep the right panel at 35 percent in the standard split", () => {
    expect(NORMAL_PANEL_LAYOUT["shell-right"]).toBe(35);
  });
});
