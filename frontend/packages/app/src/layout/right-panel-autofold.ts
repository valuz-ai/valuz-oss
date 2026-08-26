/**
 * Decides what a viewport-width change should do to the right panel.
 *
 * Narrow windows fold the panel away to make room, and widening unfolds it —
 * but only when the fold was automatic. A panel the reader closed on purpose
 * stays closed however the window is resized afterwards; that's the line
 * between "make room" and "override the reader".
 *
 * Kept as a pure function because the wiring around it is a `matchMedia`
 * listener, which no test environment fires on a real resize.
 */
export interface RightPanelAutoFoldState {
  /** Viewport is below the fold width. */
  narrow: boolean;
  /** Panel is currently collapsed. */
  collapsed: boolean;
  /** The current collapse was ours, not the reader's. */
  autoCollapsed: boolean;
}

export interface RightPanelAutoFoldAction {
  /** New collapsed value to apply, or null to leave the panel alone. */
  setCollapsed: boolean | null;
  /** Whether we still own the collapse after this transition. */
  autoCollapsed: boolean;
}

export function resolveRightPanelAutoFold({
  narrow,
  collapsed,
  autoCollapsed,
}: RightPanelAutoFoldState): RightPanelAutoFoldAction {
  if (narrow) {
    // Already closed by hand — leave it, and don't claim it as ours, or
    // widening later would reopen something the reader chose to close.
    if (collapsed) return { setCollapsed: null, autoCollapsed };
    return { setCollapsed: true, autoCollapsed: true };
  }
  if (!autoCollapsed) return { setCollapsed: null, autoCollapsed };
  return { setCollapsed: false, autoCollapsed: false };
}
