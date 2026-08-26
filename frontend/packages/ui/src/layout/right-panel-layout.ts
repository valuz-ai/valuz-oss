import type { Layout } from "../components/ui/resizable";

const MAXIMIZED_PANEL_LAYOUT: Layout = {
  "shell-main": 50,
  "shell-right": 50,
};

/** Where the right panel sits when nothing else decides — the same ratio the
 *  group opens at. A share, not a pixel width: the split then reads the same
 *  on a 1440 and a 1920 window, where a fixed card would shrink to a sliver. */
export const NORMAL_PANEL_LAYOUT: Layout = {
  "shell-main": 65,
  "shell-right": 35,
};

const isCompletePanelLayout = (layout: Layout): boolean =>
  typeof layout["shell-main"] === "number" &&
  typeof layout["shell-right"] === "number";

export function resolveRightPanelLayoutTransition({
  currentLayout,
  maximized,
  normalLayout,
  wasMaximized,
}: {
  currentLayout: Layout;
  maximized: boolean;
  normalLayout: Layout | null;
  wasMaximized: boolean;
}): { normalLayout: Layout | null; targetLayout: Layout | null } {
  if (maximized && !wasMaximized) {
    const capturedLayout = isCompletePanelLayout(currentLayout)
      ? currentLayout
      : normalLayout;
    return {
      normalLayout: capturedLayout,
      targetLayout: MAXIMIZED_PANEL_LAYOUT,
    };
  }
  if (!maximized && wasMaximized) {
    // Restoring without a captured layout used to leave the panel sitting at
    // the maximized 50/50 — the button then read as a no-op. Fall back to the
    // standard split so "restore" always restores something.
    const target = normalLayout ?? NORMAL_PANEL_LAYOUT;
    return { normalLayout: target, targetLayout: target };
  }
  return { normalLayout, targetLayout: null };
}
