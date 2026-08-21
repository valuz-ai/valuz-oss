import { EventEmitter } from "node:events";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { BrowserWindow } from "electron";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** 1920×1080 desktop minus a 30px menu bar and a 65px dock. */
const DESKTOP = { x: 0, y: 30, width: 1920, height: 985 };

// ``vi.mock`` is hoisted above the imports, so the fake electron reads its
// answers out of this holder rather than closing over module-scope consts.
const fake = vi.hoisted(() => ({
  userData: "",
  displays: [] as { workArea: (typeof DESKTOP)[] }[] & unknown[],
}));

vi.mock("electron", () => ({
  app: { getPath: () => fake.userData },
  screen: {
    getAllDisplays: () => fake.displays,
    getPrimaryDisplay: () => fake.displays[0],
  },
}));

import { loadWindowState, trackWindowState } from "./window-state";

const statePath = () => join(fake.userData, "window-state.json");
const savedState = () => JSON.parse(readFileSync(statePath(), "utf8"));

class FakeWindow extends EventEmitter {
  bounds = { x: 100, y: 100, width: 1200, height: 800 };
  maximized = false;
  fullScreen = false;
  destroyed = false;

  getNormalBounds() {
    return this.bounds;
  }
  isMaximized() {
    return this.maximized;
  }
  isFullScreen() {
    return this.fullScreen;
  }
  isDestroyed() {
    return this.destroyed;
  }
}

const track = () => {
  const win = new FakeWindow();
  trackWindowState(win as unknown as BrowserWindow);
  return win;
};

beforeEach(() => {
  fake.userData = mkdtempSync(join(tmpdir(), "valuz-window-state-"));
  fake.displays = [{ workArea: DESKTOP }] as never;
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("loadWindowState", () => {
  it("centers a size-capped window when nothing is remembered", () => {
    const state = loadWindowState();

    expect(state).toEqual({
      x: 240,
      y: 79,
      width: 1440,
      height: 886,
      maximized: false,
    });
    // The whole point: it no longer opens flush against the top edge.
    expect(state.y - DESKTOP.y).toBeGreaterThan(45);
  });

  it("reuses geometry saved by a previous launch", () => {
    writeFileSync(
      statePath(),
      JSON.stringify({
        x: 300,
        y: 200,
        width: 1000,
        height: 700,
        maximized: true,
      }),
    );

    expect(loadWindowState()).toEqual({
      x: 300,
      y: 200,
      width: 1000,
      height: 700,
      maximized: true,
    });
  });

  it("falls back to the default when the saved display is gone", () => {
    writeFileSync(
      statePath(),
      JSON.stringify({ x: 2400, y: 120, width: 1200, height: 800 }),
    );

    expect(loadWindowState().x).toBe(240);
  });

  it("falls back to the default when the state file is corrupt", () => {
    writeFileSync(statePath(), "{ not json");

    expect(loadWindowState().x).toBe(240);
  });
});

describe("trackWindowState", () => {
  it("persists the window's geometry after a move settles", () => {
    const win = track();
    win.bounds = { x: 240, y: 160, width: 1100, height: 720 };

    win.emit("move");
    expect(existsSync(statePath())).toBe(false); // debounced, not yet written
    vi.advanceTimersByTime(400);

    expect(savedState()).toEqual({
      x: 240,
      y: 160,
      width: 1100,
      height: 720,
      maximized: false,
    });
  });

  it("coalesces a drag into a single write of the final position", () => {
    const win = track();

    for (const x of [110, 140, 180]) {
      win.bounds = { ...win.bounds, x };
      win.emit("move");
      vi.advanceTimersByTime(100);
    }
    expect(existsSync(statePath())).toBe(false);
    vi.advanceTimersByTime(400);

    expect(savedState().x).toBe(180);
  });

  it("records the maximized flag alongside the restored size", () => {
    const win = track();
    win.maximized = true;

    win.emit("maximize");
    vi.advanceTimersByTime(400);

    // Bounds stay the pre-maximize ones so un-maximizing restores them.
    expect(savedState()).toMatchObject({
      width: 1200,
      height: 800,
      maximized: true,
    });
  });

  it("ignores fullscreen, whose bounds are the screen's, not the user's", () => {
    const win = track();
    win.fullScreen = true;

    win.emit("resize");
    vi.advanceTimersByTime(400);

    expect(existsSync(statePath())).toBe(false);
  });

  it("flushes the pending write on close so the last drag is not lost", () => {
    const win = track();
    win.bounds = { x: 400, y: 300, width: 900, height: 640 };

    win.emit("move");
    win.emit("close");

    // Written straight away — no timer left to fire after the app quits.
    expect(savedState().x).toBe(400);
  });
});
