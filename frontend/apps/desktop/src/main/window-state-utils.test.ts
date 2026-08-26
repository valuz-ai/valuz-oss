import { describe, expect, it } from "vitest";

import {
  defaultBounds,
  parseWindowState,
  restoreWindowState,
} from "./window-state-utils";

/** 1920×1080 desktop, minus a 30px menu bar and a 65px dock. */
const DESKTOP = { x: 0, y: 30, width: 1920, height: 985 };
/** A 1366×768 laptop panel — the ideal 1440×900 does not fit at all. */
const LAPTOP = { x: 0, y: 25, width: 1366, height: 743 };

describe("defaultBounds", () => {
  it("centers the window instead of leaving it under the menu bar", () => {
    const bounds = defaultBounds(DESKTOP);

    // Electron's own placement puts the window at workArea.y + 4 — the whole
    // reason for computing an explicit origin here.
    expect(bounds.height).toBe(886);
    const above = bounds.y - DESKTOP.y;
    const below = DESKTOP.y + DESKTOP.height - (bounds.y + bounds.height);
    // Integer flooring can leave a pixel on one side; both are ~49.
    expect(Math.abs(above - below)).toBeLessThanOrEqual(1);
    expect(above).toBeGreaterThan(45);
  });

  it("shrinks to fit a panel the ideal size overflows", () => {
    const bounds = defaultBounds(LAPTOP);

    expect(bounds.width).toBe(1229);
    expect(bounds.height).toBe(668);
    expect(bounds.x).toBeGreaterThanOrEqual(LAPTOP.x);
    expect(bounds.y).toBeGreaterThanOrEqual(LAPTOP.y);
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(
      LAPTOP.y + LAPTOP.height,
    );
  });

  it("never grows past the ideal size on a huge display", () => {
    const bounds = defaultBounds({ x: 0, y: 0, width: 5120, height: 2880 });

    expect(bounds.width).toBe(1440);
    expect(bounds.height).toBe(900);
  });
});

describe("parseWindowState", () => {
  it("accepts a well-formed state and rounds it", () => {
    expect(
      parseWindowState({ x: 100.4, y: 60.6, width: 1200.2, height: 800.8 }),
    ).toEqual({ x: 100, y: 61, width: 1200, height: 801, maximized: false });
  });

  it("keeps the maximized flag only when it is exactly true", () => {
    const base = { x: 0, y: 0, width: 1200, height: 800 };

    expect(parseWindowState({ ...base, maximized: true })?.maximized).toBe(
      true,
    );
    expect(parseWindowState({ ...base, maximized: "yes" })?.maximized).toBe(
      false,
    );
  });

  it("rejects anything unusable", () => {
    const base = { x: 0, y: 0, width: 1200, height: 800 };

    expect(parseWindowState(null)).toBeNull();
    expect(parseWindowState("{}")).toBeNull();
    expect(parseWindowState({})).toBeNull();
    expect(parseWindowState({ ...base, width: Number.NaN })).toBeNull();
    expect(
      parseWindowState({ ...base, y: Number.POSITIVE_INFINITY }),
    ).toBeNull();
    expect(parseWindowState({ ...base, x: "120" })).toBeNull();
    // A sliver — corrupt state, not a window anyone chose.
    expect(parseWindowState({ ...base, width: 12, height: 8 })).toBeNull();
  });
});

describe("restoreWindowState", () => {
  const state = (overrides = {}) => ({
    x: 200,
    y: 100,
    width: 1200,
    height: 800,
    maximized: false,
    ...overrides,
  });

  it("keeps a state that still fits its display untouched", () => {
    expect(restoreWindowState(state(), [DESKTOP])).toEqual(state());
  });

  it("drops bounds saved on a monitor that is no longer connected", () => {
    // Saved on a second display to the right of the built-in one.
    const external = state({ x: 2400 });

    expect(restoreWindowState(external, [DESKTOP])).toBeNull();
  });

  it("drops bounds left with only a corner on screen", () => {
    const almostOff = state({ x: 1870, y: 950 });

    expect(restoreWindowState(almostOff, [DESKTOP])).toBeNull();
  });

  it("caps a large-monitor state down to the laptop it comes back to", () => {
    const roomy = state({ x: 0, y: 30, width: 1800, height: 950 });

    const fitted = restoreWindowState(roomy, [LAPTOP]);

    expect(fitted).toEqual({
      x: 0,
      y: 25,
      width: 1366,
      height: 743,
      maximized: false,
    });
  });

  it("nudges a window that hangs off the bottom fully back inside", () => {
    const hanging = state({ x: 100, y: 800 });

    const fitted = restoreWindowState(hanging, [DESKTOP]);

    expect(fitted?.y).toBe(DESKTOP.y + DESKTOP.height - 800);
    expect(fitted?.height).toBe(800);
  });

  it("restores onto the display the window mostly sat on", () => {
    const second = { x: 1920, y: 0, width: 1920, height: 1080 };
    // Straddling both, but mostly on the second display.
    const straddling = state({ x: 1700, width: 1200 });

    const fitted = restoreWindowState(straddling, [DESKTOP, second]);

    expect(fitted?.x).toBe(1920);
  });

  it("carries the maximized flag through", () => {
    expect(
      restoreWindowState(state({ maximized: true }), [DESKTOP])?.maximized,
    ).toBe(true);
  });
});
