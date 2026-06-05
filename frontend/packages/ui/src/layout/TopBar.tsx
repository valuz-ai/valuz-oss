import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../components/ui/tooltip";
import { useI18n } from "../hooks/use-i18n";

export interface TopBarProps {
  /** Whether the sidebar is currently collapsed */
  sidebarCollapsed?: boolean;
  /** Toggle sidebar collapse */
  onToggleSidebar?: () => void;
  /** Optional brand slot rendered to the right of the toggle (e.g. logo + name) */
  logo?: ReactNode;
  /** Optional control rendered at the far right (typically the right-panel
   * collapse / expand toggle, mirroring the sidebar toggle on the left). */
  rightControl?: ReactNode;
  /** Browser-style back navigation. When provided renders a chevron-left
   * button next to the sidebar toggle. */
  onGoBack?: () => void;
  /** Browser-style forward navigation. When provided renders a chevron-right
   * button next to the back button. */
  onGoForward?: () => void;
  /** Disabled state for the back button (no history to pop). */
  canGoBack?: boolean;
  /** Disabled state for the forward button (no future entry). */
  canGoForward?: boolean;
  /**
   * Reserve ~58px on the left for the macOS traffic-light buttons.
   * Defaults to ``true`` — Electron-rendered windows always need it.
   * Pass ``false`` for non-Electron contexts (browser SPA, embedded
   * webview without window chrome) so the logo + nav icons sit flush
   * against the window edge instead of leaving a gap.
   */
  trafficLightPad?: boolean;
}

/**
 * Custom title bar replacing the native window chrome.
 * Provides a drag region for window movement and a sidebar collapse toggle.
 * The macOS traffic light buttons overlay the left side automatically at ~12px from top.
 */
export const TopBar = ({
  sidebarCollapsed = false,
  onToggleSidebar,
  logo,
  rightControl,
  onGoBack,
  onGoForward,
  canGoBack = true,
  canGoForward = true,
  trafficLightPad = true,
}: TopBarProps) => {
  const { t } = useI18n();

  return (
    // Root is NOT drag — only the middle spacer is. This guarantees both
    // toggle buttons are click-targets without relying on child no-drag
    // overrides (which Electron 36 occasionally swallows).
    <div className="flex h-[36px] shrink-0 items-center px-4">
      {/* Left: traffic light spacer + brand logo + collapse toggle */}
      <div
        className={`flex items-center gap-2 pt-[4px] ${trafficLightPad ? "ml-[58px]" : ""}`}
      >
        {logo}
        {onToggleSidebar && (
          <TooltipProvider delayDuration={150}>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  aria-label={t("sidebar.topbar.toggleSidebar")}
                  onClick={onToggleSidebar}
                  className="flex h-[22px] w-[22px] items-center justify-center rounded-[5px] text-ink-body transition-colors hover:bg-surface-muted"
                >
                  {/* Mirror of the right-panel toggle in the workspace
                      layout: rounded rect + a divider line whose position
                      signals the sidebar state. Collapsed pushes the line
                      further LEFT (sidebar squeezed off) and shortens it
                      so it doesn't touch the top/bottom edges; expanded
                      puts the line at the standard ~1/4 mark and runs it
                      edge-to-edge. ``currentColor`` so themes can recolor
                      without touching the SVG. */}
                  <svg
                    width="14"
                    height="14"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                    <line
                      x1={sidebarCollapsed ? 7 : 9}
                      y1={sidebarCollapsed ? 7 : 3}
                      x2={sidebarCollapsed ? 7 : 9}
                      y2={sidebarCollapsed ? 17 : 21}
                    />
                  </svg>
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {sidebarCollapsed
                  ? t("sidebar.topbar.expandSidebar")
                  : t("sidebar.topbar.collapseSidebar")}
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
        {(onGoBack || onGoForward) && (
          <TooltipProvider delayDuration={150}>
            <div className="flex items-center gap-px">
              {onGoBack && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label={t("sidebar.topbar.goBack")}
                      onClick={onGoBack}
                      disabled={!canGoBack}
                      className="flex h-[22px] w-[22px] items-center justify-center rounded-[5px] text-ink-body transition-colors hover:bg-surface-muted disabled:cursor-default disabled:text-ink-meta disabled:opacity-40 disabled:hover:bg-transparent"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    {t("sidebar.topbar.goBack")}
                  </TooltipContent>
                </Tooltip>
              )}
              {onGoForward && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label={t("sidebar.topbar.goForward")}
                      onClick={onGoForward}
                      disabled={!canGoForward}
                      className="flex h-[22px] w-[22px] items-center justify-center rounded-[5px] text-ink-body transition-colors hover:bg-surface-muted disabled:cursor-default disabled:text-ink-meta disabled:opacity-40 disabled:hover:bg-transparent"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    {t("sidebar.topbar.goForward")}
                  </TooltipContent>
                </Tooltip>
              )}
            </div>
          </TooltipProvider>
        )}
      </div>

      {/* Drag spacer fills the middle so the user can drag the window from
        anywhere except the buttons themselves. */}
      <div
        className="flex-1 self-stretch"
        style={{ WebkitAppRegion: "drag" } as React.CSSProperties}
      />

      {/* Right: optional control (e.g. right-panel toggle). */}
      <div className="mr-[4px] flex items-center pt-[4px]">{rightControl}</div>
    </div>
  );
};
