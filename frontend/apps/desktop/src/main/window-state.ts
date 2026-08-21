/**
 * Remembers the main window's size + position across launches.
 *
 * Without this the window reopens at the computed default every single time,
 * so moving or resizing it never sticks. State lives in a small JSON file in
 * ``userData`` (per app/channel, so a dev build cannot fight a release one).
 *
 * Every failure here is non-fatal: a missing, corrupt or unwritable state file
 * just means the window opens at its default.
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { type BrowserWindow, app, screen } from "electron";

import {
  type WindowState,
  defaultBounds,
  parseWindowState,
  restoreWindowState,
} from "./window-state-utils";

const STATE_FILE = "window-state.json";

/** Resize/move fire continuously while dragging — coalesce the writes. */
const SAVE_DEBOUNCE_MS = 400;

const statePath = () => join(app.getPath("userData"), STATE_FILE);

const readState = (): WindowState | null => {
  try {
    return parseWindowState(JSON.parse(readFileSync(statePath(), "utf8")));
  } catch {
    // No file yet on first launch, or unreadable/corrupt — use the default.
    return null;
  }
};

const writeState = (state: WindowState) => {
  try {
    const target = statePath();
    mkdirSync(dirname(target), { recursive: true });
    writeFileSync(target, JSON.stringify(state), "utf8");
  } catch (err) {
    console.warn(`[window-state] failed to persist window bounds: ${err}`);
  }
};

/**
 * Bounds to open the main window with: the remembered ones when they still
 * land on a connected display, the centered default otherwise.
 */
export const loadWindowState = (): WindowState => {
  const saved = readState();
  if (saved) {
    const fitted = restoreWindowState(
      saved,
      screen.getAllDisplays().map((display) => display.workArea),
    );
    if (fitted) return fitted;
  }
  return {
    ...defaultBounds(screen.getPrimaryDisplay().workArea),
    maximized: false,
  };
};

/**
 * Persist ``win``'s geometry as the user changes it. Call once, right after
 * the window is created.
 */
export const trackWindowState = (win: BrowserWindow) => {
  let timer: ReturnType<typeof setTimeout> | null = null;

  const save = () => {
    // Fullscreen bounds are the screen's, not the user's chosen size — coming
    // back from fullscreen should restore what they had before it.
    if (win.isDestroyed() || win.isFullScreen()) return;
    // ``getNormalBounds`` reports the restored geometry even while maximized,
    // so un-maximizing later lands back on the size the user picked.
    writeState({ ...win.getNormalBounds(), maximized: win.isMaximized() });
  };

  const schedule = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(save, SAVE_DEBOUNCE_MS);
  };

  // Listed one by one: Electron types ``on`` as per-event overloads, so a loop
  // over a union of event names does not type-check.
  win.on("resize", schedule);
  win.on("move", schedule);
  win.on("maximize", schedule);
  win.on("unmaximize", schedule);

  // Quitting must not lose the last drag: flush the pending write while the
  // window can still be measured.
  win.on("close", () => {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    save();
  });
};
