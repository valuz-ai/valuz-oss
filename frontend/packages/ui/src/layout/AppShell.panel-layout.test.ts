import { describe, expect, it } from "vitest";
import { resolveRightPanelLayoutTransition } from "./right-panel-layout";

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
});
