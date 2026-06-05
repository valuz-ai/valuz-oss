import type { ComponentType, PropsWithChildren, ReactNode } from "react";
import type { NavigationItem } from "@valuz/shared";
import { cn } from "../lib/cn";

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
  header?: ReactNode;
  headerClassName?: string;
  hideHeader?: boolean;
  contentClassName?: string;
  contentInnerClassName?: string;
  mainClassName?: string;
  shellClassName?: string;
  asideClassName?: string;
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
  right,
  sidebar,
  shellClassName,
  title,
  topBar,
}: AppShellProps) => (
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
            aria-label="Workspace sections"
            className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 pt-3"
          >
            <div className="label-mono px-3 pb-1 pt-2">Workspace</div>
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
          "flex min-w-0 flex-1 gap-2 p-4 pt-0",
          // sidebar=false signals "explicitly hidden" — give main the same
          // left padding as the right edge so the bordered card doesn't
          // bleed into the window edge. null/undefined falls back to the
          // default sidebar which already occupies the left strip.
          sidebar === false ? "pl-4" : "pl-0",
        )}
      >
        <main
          className={cn(
            "flex min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-card",
            mainClassName,
          )}
        >
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

        {aside
          ? (() => {
              console.log("[AppShell] rendering aside element");
              return (
                <aside
                  className={cn(
                    "hidden shrink-0 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-card lg:flex",
                    asideClassName,
                  )}
                >
                  {aside}
                </aside>
              );
            })()
          : right
            ? (() => {
                console.log("[AppShell] rendering right element");
                return (
                  <aside
                    className={cn(
                      "hidden shrink-0 flex-col overflow-hidden rounded-[14px] border border-surface-border bg-card lg:flex",
                      asideClassName,
                    )}
                  >
                    {right}
                  </aside>
                );
              })()
            : (() => {
                console.log(
                  "[AppShell] NOT rendering aside (both null/undefined)",
                );
                return null;
              })()}
      </div>
    </div>
  </div>
);
