import { describe, expect, it } from "vitest";

import {
  resolveRightPanelAutoFold,
  type RightPanelAutoFoldState,
} from "./right-panel-autofold";

/** Walks a sequence of viewport states, threading the auto-collapse claim. */
function run(
  start: { collapsed: boolean; autoCollapsed: boolean },
  steps: (boolean | "open" | "close")[],
) {
  let { collapsed, autoCollapsed } = start;
  for (const step of steps) {
    if (step === "open") {
      // A deliberate re-open drops our claim, the way the store subscription
      // does at runtime.
      collapsed = false;
      autoCollapsed = false;
      continue;
    }
    if (step === "close") {
      collapsed = true;
      continue;
    }
    const state: RightPanelAutoFoldState = {
      narrow: step,
      collapsed,
      autoCollapsed,
    };
    const action = resolveRightPanelAutoFold(state);
    if (action.setCollapsed !== null) collapsed = action.setCollapsed;
    autoCollapsed = action.autoCollapsed;
  }
  return { collapsed, autoCollapsed };
}

describe("resolveRightPanelAutoFold", () => {
  it("folds an open panel away when the window narrows", () => {
    expect(
      resolveRightPanelAutoFold({
        narrow: true,
        collapsed: false,
        autoCollapsed: false,
      }),
    ).toEqual({ setCollapsed: true, autoCollapsed: true });
  });

  it("restores the panel when the window widens again", () => {
    expect(
      run({ collapsed: false, autoCollapsed: false }, [true, false]),
    ).toEqual({ collapsed: false, autoCollapsed: false });
  });

  it("leaves a hand-closed panel closed through a narrow/wide round trip", () => {
    // The reader closed it while wide; resizing must not reopen it.
    expect(
      run({ collapsed: true, autoCollapsed: false }, [true, false]),
    ).toEqual({ collapsed: true, autoCollapsed: false });
  });

  it("never claims a collapse it did not perform", () => {
    expect(
      resolveRightPanelAutoFold({
        narrow: true,
        collapsed: true,
        autoCollapsed: false,
      }),
    ).toEqual({ setCollapsed: null, autoCollapsed: false });
  });

  it("drops its claim once the reader reopens the panel", () => {
    // narrow (auto-fold) → reader reopens → reader closes → widen: stays closed.
    expect(
      run({ collapsed: false, autoCollapsed: false }, [
        true,
        "open",
        "close",
        false,
      ]),
    ).toEqual({ collapsed: true, autoCollapsed: false });
  });

  it("is idempotent while the width stays on one side of the fold", () => {
    expect(
      run({ collapsed: false, autoCollapsed: false }, [true, true, true]),
    ).toEqual({ collapsed: true, autoCollapsed: true });
    expect(
      run({ collapsed: false, autoCollapsed: false }, [false, false]),
    ).toEqual({ collapsed: false, autoCollapsed: false });
  });
});
