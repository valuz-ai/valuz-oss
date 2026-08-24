import type { Layout } from "../components/ui/resizable";

const MAXIMIZED_PANEL_LAYOUT: Layout = {
  "shell-main": 50,
  "shell-right": 50,
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
  if (!maximized && wasMaximized && normalLayout) {
    return { normalLayout, targetLayout: normalLayout };
  }
  return { normalLayout, targetLayout: null };
}
