/**
 * Pure geometry for the main window's placement — where it opens on a fresh
 * install, and how a remembered position is validated before being reused.
 *
 * Deliberately free of ``electron`` imports so it stays unit-testable: the
 * disk + BrowserWindow glue lives in ``window-state.ts``.
 */

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface WindowState extends Rect {
  maximized: boolean;
}

const IDEAL_WIDTH = 1440;
const IDEAL_HEIGHT = 900;

/**
 * Share of the work area a default-placed window may take. 1440×900 is only
 * an *ideal*: on a 1920×1080 desktop the work area is ~1920×985 once the menu
 * bar and dock are subtracted, leaving 85px of slack — and on a 1366×768 or a
 * scaled laptop panel the ideal does not fit at all.
 *
 * Capping is only half the fix. Omitting ``x``/``y`` does NOT center the
 * window: Electron centers it horizontally but drops it near the top of the
 * work area (measured: y = workArea.y + 4 on macOS 15), which is why it opens
 * glued under the menu bar. ``defaultBounds`` therefore always returns an
 * explicit origin.
 */
const DEFAULT_FILL = 0.9;

/**
 * A remembered window must keep at least this much of itself inside a display
 * to be restored there. Guards the unplugged-monitor case: bounds saved on a
 * second screen would otherwise reopen the window off in the void.
 */
const MIN_VISIBLE_WIDTH = 240;
const MIN_VISIBLE_HEIGHT = 120;

/** Below this a restored window is unusable; treat the state as corrupt. */
const MIN_WIDTH = 400;
const MIN_HEIGHT = 300;

const clamp = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, value));

const overlap = (a: Rect, b: Rect) => ({
  width: Math.max(
    0,
    Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x),
  ),
  height: Math.max(
    0,
    Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y),
  ),
});

/**
 * Where the window opens when nothing is remembered: the ideal size capped to
 * the work area, centered inside it — origin included, because leaving
 * ``x``/``y`` to Electron is what pins the window under the menu bar. Applied
 * on every platform: the cap is what keeps the window inside panels the ideal
 * overflows (Windows/Linux do not clamp on their own), the explicit origin is
 * what stops it hugging the top edge everywhere.
 */
export const defaultBounds = (work: Rect): Rect => {
  const width = Math.min(IDEAL_WIDTH, Math.floor(work.width * DEFAULT_FILL));
  const height = Math.min(IDEAL_HEIGHT, Math.floor(work.height * DEFAULT_FILL));
  return {
    width,
    height,
    x: work.x + Math.floor((work.width - width) / 2),
    y: work.y + Math.floor((work.height - height) / 2),
  };
};

/**
 * Validate a state file's contents. Returns ``null`` for anything we would not
 * want to hand to ``BrowserWindow`` — a hand-edited file, a truncated write, a
 * shape from some future version.
 */
export const parseWindowState = (raw: unknown): WindowState | null => {
  if (typeof raw !== "object" || raw === null) return null;
  const record = raw as Record<string, unknown>;

  const read = (key: keyof Rect) => {
    const value = record[key];
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  };
  const x = read("x");
  const y = read("y");
  const width = read("width");
  const height = read("height");
  if (x === null || y === null || width === null || height === null) {
    return null;
  }
  if (width < MIN_WIDTH || height < MIN_HEIGHT) return null;

  return {
    x: Math.round(x),
    y: Math.round(y),
    width: Math.round(width),
    height: Math.round(height),
    maximized: record.maximized === true,
  };
};

/**
 * Fit a remembered state to the displays that exist *now*, returning ``null``
 * when it no longer lands on any of them (caller falls back to
 * ``defaultBounds``). The window is matched to the display it overlaps most,
 * then capped and nudged fully inside that display's work area — a state saved
 * on a large monitor must not reopen taller than the laptop panel it comes
 * back to.
 */
export const restoreWindowState = (
  state: WindowState,
  workAreas: Rect[],
): WindowState | null => {
  let host: Rect | null = null;
  let hostArea = 0;
  for (const work of workAreas) {
    const { width, height } = overlap(state, work);
    const area = width * height;
    if (
      width >= MIN_VISIBLE_WIDTH &&
      height >= MIN_VISIBLE_HEIGHT &&
      area > hostArea
    ) {
      host = work;
      hostArea = area;
    }
  }
  if (!host) return null;

  const width = Math.min(state.width, host.width);
  const height = Math.min(state.height, host.height);
  return {
    width,
    height,
    x: clamp(state.x, host.x, host.x + host.width - width),
    y: clamp(state.y, host.y, host.y + host.height - height),
    maximized: state.maximized,
  };
};
