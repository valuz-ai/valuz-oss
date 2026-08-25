import {
  useEffect,
  useRef,
  useState,
  type ComponentType,
  type PropsWithChildren,
  type ReactNode,
} from "react";
import type { NavigationItem } from "@valuz/shared";
import { cn } from "../lib/cn";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
  type GroupImperativeHandle,
  type Layout,
} from "../components/ui/resizable";
import { resolveRightPanelLayoutTransition } from "./right-panel-layout";

/**
 * Props passed to a nav link component. Matches react-router-dom's `Link`
 * contract so apps can pass `Link` (or a thin wrapper) directly.
 */
export interface NavLinkComponentProps {
  to: string;
  className?: string;
  children?: ReactNode;
  onContextMenu?: React.MouseEventHandler;
  /** Forwarded so inner buttons can call preventDefault to suppress
   * navigation, and the parent can run side-effects (e.g. expanding a
   * sidebar group) on the same click that performs the route change. */
  onClick?: React.MouseEventHandler;
}

export type NavLinkComponent = ComponentType<NavLinkComponentProps>;

/**
 * Default nav link — plain anchor. Apps should override with their router's
 * Link (e.g. react-router-dom's `Link`) so clicks stay client-side. Using
 * the default triggers a full page reload on navigation.
 */
const DefaultNavLink: NavLinkComponent = ({
  to,
  className,
  children,
  onClick,
  onContextMenu,
}) => (
  <a
    href={to}
    className={className}
    onClick={onClick}
    onContextMenu={onContextMenu}
  >
    {children}
  </a>
);

interface AppShellProps extends PropsWithChildren {
  appTitle?: string;
  title?: string;
  sidebar?: ReactNode;
  navItems?: NavigationItem[];
  activePath?: string;
  aside?: ReactNode;
  right?: ReactNode;
  /** Shell-level notice strip pinned as the FIRST row of the main card —
   * above the header and outside the page's padded/scrolling content, so it
   * reads as panel chrome on every page (headered pages would otherwise
   * inset it into their content padding, and outer-scroll pages would let
   * it scroll away). */
  notice?: ReactNode;
  header?: ReactNode;
  headerClassName?: string;
  hideHeader?: boolean;
  contentClassName?: string;
  contentInnerClassName?: string;
  mainClassName?: string;
  shellClassName?: string;
  asideClassName?: string;
  /** Allows the main and right cards to be resized with a keyboard-accessible
   * separator. Disabled by default so master-detail layouts keep their
   * existing fixed sizing unless the host explicitly opts in. */
  rightPanelResizable?: boolean;
  /** Controlled half-window preset for the resizable right card. Returning to
   * false restores the last user-selected normal layout. */
  rightPanelMaximized?: boolean;
  /** Accessible label for the otherwise visual resize separator. */
  rightPanelResizeLabel?: string;
  /** Optional brand slot rendered above the app title (e.g. logo or window controls). */
  brandSlot?: ReactNode;
  /** Link component used for sidebar navigation. Pass react-router's Link to avoid full reloads. */
  LinkComponent?: NavLinkComponent;
  /** Custom top bar replacing native window title bar (full-width drag region). */
  topBar?: ReactNode;
}

const isActivePath = (activePath: string, itemPath: string) => {
  if (itemPath === "/") {
    return activePath === "/" || activePath.startsWith("/conversation/");
  }
  return activePath === itemPath || activePath.startsWith(`${itemPath}/`);
};

const rememberedNormalPanelLayouts = new Map<string, Layout>();

function ResizableShellPanels({
  mainPanel,
  maximized,
  resizeLabel,
  rightPanel,
}: {
  mainPanel: ReactNode;
  maximized: boolean;
  resizeLabel?: string;
  rightPanel: ReactNode;
}) {
  const layoutId = "app-shell-panels";
  const [initialNormalLayout] = useState<Layout | null>(
    () => rememberedNormalPanelLayouts.get(layoutId) ?? null,
  );
  const panelGroupRef = useRef<GroupImperativeHandle | null>(null);
  const normalPanelLayoutRef = useRef<Layout | null>(initialNormalLayout);
  const wasMaximizedRef = useRef(false);

  useEffect(() => {
    const group = panelGroupRef.current;
    if (!group) return;

    const transition = resolveRightPanelLayoutTransition({
      currentLayout: group.getLayout(),
      maximized,
      normalLayout: normalPanelLayoutRef.current,
      wasMaximized: wasMaximizedRef.current,
    });
    normalPanelLayoutRef.current = transition.normalLayout;
    if (transition.normalLayout) {
      rememberedNormalPanelLayouts.set(layoutId, transition.normalLayout);
    }
    if (transition.targetLayout) group.setLayout(transition.targetLayout);
    wasMaximizedRef.current = maximized;
  }, [maximized]);

  return (
    <ResizablePanelGroup
      id={layoutId}
      className="min-w-0 flex-1"
      groupRef={panelGroupRef}
      defaultLayout={initialNormalLayout ?? undefined}
      onLayoutChanged={(layout) => {
        if (maximized) return;
        normalPanelLayoutRef.current = layout;
        rememberedNormalPanelLayouts.set(layoutId, layout);
      }}
    >
      <ResizablePanel
        id="shell-main"
        minSize="520px"
        style={{ overflow: "hidden" }}
      >
        {mainPanel}
      </ResizablePanel>
      {rightPanel ? (
        <>
          <ResizableHandle
            aria-label={resizeLabel}
            disabled={maximized}
            className="w-2 shrink-0 bg-transparent before:absolute before:inset-y-2 before:left-1/2 before:w-px before:-translate-x-1/2 before:bg-brand before:opacity-0 before:transition-opacity after:w-2 data-[separator=active]:before:opacity-100 data-[separator=focus]:before:opacity-100 data-[separator=hover]:before:opacity-100 focus-visible:ring-0 focus-visible:ring-offset-0"
          />
          <ResizablePanel
            id="shell-right"
            defaultSize="480px"
            minSize="320px"
            maxSize="70%"
            style={{ overflow: "visible" }}
          >
            {rightPanel}
          </ResizablePanel>
        </>
      ) : null}
    </ResizablePanelGroup>
  );
}

export const AppShell = ({
  activePath = "/",
  appTitle,
  aside,
  asideClassName,
  brandSlot,
  children,
  contentClassName,
  contentInnerClassName,
  header,
  headerClassName,
  hideHeader = false,
  LinkComponent = DefaultNavLink,
  mainClassName,
  navItems = [],
  notice,
  right,
  rightPanelMaximized = false,
  rightPanelResizable = false,
  rightPanelResizeLabel,
  sidebar,
  shellClassName,
  title,
  topBar,
}: AppShellProps) => {
  const rightPanel = aside || right;
  // Treat this flag as a layout capability, not as a reflection of whether
  // async right-panel content has arrived yet. Switching between the plain
  // fragment and ResizableShellPanels would otherwise remount the complete
  // main route whenever the context panel changes null -> node (and the route
  // bootstrap would start over in a request loop).
  const useResizablePanels = rightPanelResizable;

  const mainPanel = (
    <main
      className={cn(
        "flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-card",
        useResizablePanels && "h-full w-full",
        mainClassName,
      )}
    >
      {notice}
      {!hideHeader && header ? (
        <header
          className={cn(
            "flex h-12 shrink-0 items-center px-5",
            headerClassName,
          )}
        >
          {header}
        </header>
      ) : null}
      {!hideHeader && !header && title ? (
        <header
          className={cn(
            "flex h-12 shrink-0 items-center px-5",
            headerClassName,
          )}
        >
          <span className="text-base font-medium text-ink-heading">
            {title}
          </span>
        </header>
      ) : null}
      {/* Scroll container hugs the card's inner border so the scrollbar
        renders flush against the right edge. Padding lives on the inner
        wrapper, which is h-full so children using `h-full` (the standard
        page-shell pattern) get a real viewport-sized box and can manage
        their own internal scroll without bleeding into the outer scroll. */}
      <div
        className={cn(
          "min-h-0 flex-1 overflow-auto",
          hideHeader && "overflow-hidden",
          contentClassName,
        )}
      >
        <div
          className={cn(
            "h-full",
            // Default keeps full 4-side padding so legacy pages
            // (KnowledgePage's negative-margin fullbleed hack
            // depends on the outer 28 px to stay above the
            // header; ProjectDetailPage's centered column
            // expects 24 px top breathing room) continue to
            // render correctly. Pages that want different
            // inner padding override via
            // ``setContentInnerClassName`` (ActivityPage uses
            // ``"px-6 sm:px-7"`` to drop the vertical padding,
            // for example).
            !hideHeader && (contentInnerClassName ?? "p-6 sm:p-7"),
          )}
        >
          {children}
        </div>
      </div>
    </main>
  );

  const rightPanelCard = rightPanel ? (
    <aside
      className={cn(
        "hidden shrink-0 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-card lg:flex",
        asideClassName,
        useResizablePanels && "h-full w-full",
      )}
    >
      {rightPanel}
    </aside>
  ) : null;

  return (
    <div
      className={cn(
        "flex h-screen flex-col text-ink-heading",
        shellClassName ?? "soft-gradient",
      )}
    >
      {topBar}
      <div className="flex min-h-0 flex-1">
        {sidebar ?? (
          <aside className="flex w-[240px] shrink-0 flex-col">
            <div className="space-y-4 px-4 pb-2 pt-5">
              {brandSlot ? <div>{brandSlot}</div> : null}
              <div className="space-y-1">
                <div className="gradient-brand inline-flex h-9 w-9 items-center justify-center rounded-xl text-sm font-semibold text-white">
                  {(appTitle ?? title ?? "V").slice(0, 1).toUpperCase()}
                </div>
                <div className="font-heading text-base font-medium text-ink-heading">
                  {appTitle ?? title ?? "Valuz Agent"}
                </div>
              </div>
            </div>

            <nav
              aria-label="Project sections"
              className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 pt-3"
            >
              <div className="label-mono px-3 pb-1 pt-2">Project</div>
              {navItems.map((item) => {
                const active = isActivePath(activePath, item.path);
                return (
                  <LinkComponent
                    key={item.path}
                    to={item.path}
                    className={cn(
                      "flex h-auto items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm transition-all",
                      active
                        ? "bg-card text-ink-heading shadow-md"
                        : "text-ink-label hover:bg-surface-muted",
                    )}
                  >
                    <span className="flex flex-col gap-0.5 text-left">
                      <span
                        className={cn(
                          "truncate text-sm",
                          active ? "font-medium" : "font-normal",
                        )}
                      >
                        {item.label}
                      </span>
                      <span className="text-2xs leading-4 text-ink-meta">
                        {item.description}
                      </span>
                    </span>
                  </LinkComponent>
                );
              })}
            </nav>
          </aside>
        )}

        {/* Content + right panel as floating white cards */}
        <div
          className={cn(
            "flex min-w-0 flex-1 p-4 pt-0",
            !useResizablePanels && "gap-2",
            // sidebar=false signals "explicitly hidden" — give main the same
            // left padding as the right edge so the bordered card doesn't
            // bleed into the window edge. null/undefined falls back to the
            // default sidebar which already occupies the left strip.
            sidebar === false ? "pl-4" : "pl-0",
          )}
        >
          {useResizablePanels ? (
            <ResizableShellPanels
              mainPanel={mainPanel}
              maximized={rightPanelMaximized}
              resizeLabel={rightPanelResizeLabel}
              rightPanel={rightPanelCard}
            />
          ) : (
            <>
              {mainPanel}
              {rightPanelCard}
            </>
          )}
        </div>
      </div>
    </div>
  );
};
