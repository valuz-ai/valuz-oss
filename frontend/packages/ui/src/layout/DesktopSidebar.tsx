import { useState, useRef, useEffect, type ReactNode } from "react";
import {
  Activity,
  BookOpen,
  Bot,
  ChevronDown,
  Clock,
  Compass,
  Download,
  ExternalLink,
  FilePenLine,
  FolderOpen,
  LayoutDashboard,
  Link2,
  ListTodo,
  Loader2,
  MessageCirclePlus,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Puzzle,
  Settings,
  Star,
  Store,
  Trash2,
  Upload,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { cn } from "../lib/cn";
import { statusDotClass } from "../components/common/status-tone";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "../components/ui/tooltip";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { DeleteConfirmDialog } from "../components/common/DeleteConfirmDialog";
import { ForkIcon } from "../components/common/ForkIcon";
import type { NavLinkComponent } from "./AppShell";
import { useI18n } from "../hooks/use-i18n";
import { assetUrl } from "@valuz/shared";

export interface DesktopSidebarItem {
  id: string;
  label: string;
  href: string;
  /**
   * Lightweight status indicator rendered as a colored dot before the
   * label. ``"running"`` shows a pulsing brand-coloured dot so the user
   * can see at a glance which sessions still have an agent turn in
   * flight; ``"failed"`` shows a red dot. Anything else (or omitted)
   * renders no dot.
   */
  status?: "running" | "failed" | "idle" | "created" | "cancelled" | "archived";
  /** Right-aligned label (e.g. "57 分", "1 天") shown after the row title. */
  meta?: string;
}

export interface DesktopSidebarRecents {
  today: DesktopSidebarItem[];
  yesterday: DesktopSidebarItem[];
  lastWeek: DesktopSidebarItem[];
  earlier?: DesktopSidebarItem[];
}

/** A project entry in the sidebar's "项目" section. Each project is an
 * accordion: its own chats + tasks (newest first) nest under it via ``items``
 * and are revealed when the project is expanded. */
export interface DesktopSidebarProjectGroup {
  id: string;
  label: string;
  /** Link target for the project landing page. */
  href: string;
  /** This project's chats + tasks, newest first. The sidebar caps the
   *  visible count and offers a "show more" toggle. */
  items?: DesktopSidebarRecentItem[];
  /** Replaces the default folder glyph — multi-target editions pass an
   * execution-origin icon (local/cloud) here. */
  icon?: ReactNode;
}

/** A task entry in the sidebar's "任务" section. Tasks are split out from
 * RECENTS (chat sessions) because their status model, click target, and
 * context menu actions differ — mixing them in a single time-ordered list
 * conflates "agent is mid-turn" with "long-running workflow is alive". */
export interface DesktopSidebarTaskItem {
  id: string;
  label: string;
  href: string;
  /** Task lifecycle status (backend ``TaskRow.status``). Drives the leading
   * status icon + sort priority within the section. */
  status: "active" | "paused" | "blocked" | "completed" | "stopped" | "failed";
}

export interface DesktopSidebarBottomItem {
  id: string;
  label: string;
  href: string;
  /** Icon id — key of the sidebar icon map; unknown ids fall back to the
   *  gear icon so plugin-supplied items degrade gracefully. */
  icon: string;
  /** Which sidebar region the item renders in (PRD-NEXT §3.4 IA).
   *  ``main`` = top verbs area, ``library``/``settings`` = bottom-pinned.
   *  Any other string = a custom labeled group (see ``navGroups`` prop),
   *  rendered between the main area and the project list. */
  group: "main" | "library" | "settings" | (string & {});
  /** Optional trailing count badge (e.g. running-runs count on Activity).
   *  Falsy / 0 → no badge. */
  badgeCount?: number;
  /** Optional trailing red attention dot (e.g. a failing connector). Shown
   *  only when there's no ``badgeCount``. */
  badgeDot?: boolean;
}

/** A custom labeled sidebar group (label already translated by the caller);
 *  items reference it via ``DesktopSidebarBottomItem.group === id``. */
export interface DesktopSidebarNavGroup {
  id: string;
  label: string;
}

const BOTTOM_ICON_MAP: Record<string, LucideIcon> = {
  assistant: MessageSquare,
  knowledge: BookOpen,
  skills: Zap,
  scheduled: Clock,
  activity: Activity,
  system: Activity,
  settings: Settings,
  agents: Bot,
  connectors: Link2,
  plugins: Puzzle,
  marketplace: Store,
  projectTasks: ListTodo,
  star: Star,
  compass: Compass,
  // Verticals that surface a composed "workbench"/dashboard landing page —
  // ``activity`` was the closest existing key and collided with the Activity
  // item one row above it in the same sidebar.
  dashboard: LayoutDashboard,
};

/** Icon lookup with a gear fallback for unknown (plugin-supplied) ids. */
function bottomIcon(id: string): LucideIcon {
  return BOTTOM_ICON_MAP[id] ?? Settings;
}

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

const isActivePath = (activePath: string, href: string) =>
  activePath === href || activePath.startsWith(`${href}/`);

const SectionLabel = ({
  children,
  action,
  open = true,
  onToggle,
  tight = false,
  sticky = false,
}: {
  children: string;
  action?: React.ReactNode;
  open?: boolean;
  onToggle?: () => void;
  /** Compress the top padding so the label sits tight under a preceding
   * ``GroupHeading`` — visual rhythm tells the user "this section belongs
   * to that heading" without indentation. */
  tight?: boolean;
  /** Keep the label pinned at the top of its scroll container while rows
   * scroll underneath (used for 任务 / 对话 in the RECENTS scroll area so
   * the user never loses track of which section they're scrolling through).
   * Two stacked sticky labels: the second one pushes the first off as it
   * approaches — native CSS sticky behavior, no JS. */
  sticky?: boolean;
}) => {
  // Spec 5.1 Section Header: padding 6px 8px 4px 10px / 11.5px / 400 /
  // tracking 0.06em / color #6E7481. Folding chevron 12px stroke 2 #94A3B8,
  // rotates -90° when collapsed.
  const inner = (
    <span className="flex items-center gap-1 text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
      {children}
      <ChevronDown
        className={cn(
          "h-3 w-3 shrink-0 text-[#94A3B8] transition-transform duration-[150ms]",
          !open && "-rotate-90",
        )}
        strokeWidth={2}
      />
    </span>
  );
  return (
    <div
      className={cn(
        "flex items-center justify-between pr-3 pb-1 pl-[14px]",
        tight ? "pt-1" : "pt-3",
        sticky && "sticky top-0 z-10 bg-card",
      )}
    >
      {onToggle ? (
        <button
          type="button"
          onClick={onToggle}
          className="flex items-center transition-colors duration-[120ms] hover:[&_span]:text-ink-heading"
        >
          {inner}
        </button>
      ) : (
        inner
      )}
      {action}
    </div>
  );
};

/* ── Inline rename input ───────────────────────────────────── */

const isUuidLike = (s: string) => /^[0-9a-f]{8}-/i.test(s);

const RenameInput = ({
  initial,
  onConfirm,
  onCancel,
}: {
  initial: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) => {
  const startValue = isUuidLike(initial) ? "" : initial;
  const [value, setValue] = useState(startValue);
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // Radix's ``DropdownMenu`` runs its close-auto-focus AFTER React
    // commits, which blurs whatever currently has focus (including this
    // input) right after our ``focus()`` lands. The ``onCloseAutoFocus``
    // ``preventDefault`` on the parent ``DropdownMenuContent`` suppresses
    // the focus *return* to the trigger, but doesn't skip the preceding
    // blur. Defer one rAF so our focus call happens AFTER the dropdown's
    // close-focus housekeeping runs — the freshly-mounted input then
    // captures the caret without getting wiped.
    const id = window.requestAnimationFrame(() => {
      ref.current?.focus();
      ref.current?.select();
    });
    return () => window.cancelAnimationFrame(id);
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const trimmed = value.trim();
      if (trimmed) onConfirm(trimmed);
      else onCancel();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    }
  };

  return (
    <input
      ref={ref}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => {
        const trimmed = value.trim();
        if (trimmed) onConfirm(trimmed);
        else onCancel();
      }}
      onKeyDown={handleKeyDown}
      className="h-full w-full rounded-none border-0 border-b border-brand bg-transparent px-1 text-sm text-ink-heading outline-none"
    />
  );
};

/* ── Sidebar link with optional right-click ────────────────── */

const SidebarLink = ({
  active,
  children,
  href,
  LinkComponent,
  onContextMenu,
  onClick,
  editing = false,
}: {
  active?: boolean;
  children: React.ReactNode;
  href: string;
  LinkComponent: NavLinkComponent;
  onContextMenu?: React.MouseEventHandler;
  /** Fires on click in addition to navigation. Needed because react-router
   * treats a click on the already-active route as a no-op (no location
   * change), so an effect keyed on the path wouldn't re-run. */
  onClick?: React.MouseEventHandler;
  /** When true, the row is in inline-rename mode — drop the rounded
   * background / hover bg so the inline input shows just its bottom border. */
  editing?: boolean;
}) => (
  // Spec 5.1 Sidebar Row: padding 7px 10px, gap 9px, 13px / 400, radius 7px,
  // active bg-card + shadow 0 6px 16px rgba(17,24,39,0.12), hover bg
  // rgba(0,0,0,0.03). Dark mode keeps the flat surface-muted state.
  <LinkComponent
    to={href}
    className={cn(
      "relative mx-1 flex cursor-default items-center gap-[9px] px-[10px] py-[7px] text-sm font-normal text-ink-heading outline-none transition-[background-color,box-shadow] duration-[120ms] focus-visible:outline-none focus-visible:ring-0 focus-visible:shadow-[0_6px_16px_rgba(17,24,39,0.12)]",
      editing ? "" : "rounded-[7px]",
      editing
        ? ""
        : active
          ? "z-20 bg-card shadow-[0_6px_16px_rgba(17,24,39,0.12)] dark:bg-surface-muted dark:shadow-none"
          : "hover:bg-[rgba(0,0,0,0.03)] dark:hover:bg-surface-muted",
    )}
    onContextMenu={onContextMenu}
    onClick={onClick}
  >
    {children}
  </LinkComponent>
);

const SIDEBAR_ROW_TITLE_CLASS = "min-w-0 truncate text-sm";

/* ── Per-project row (single line — no nested sessions) ────── */

interface ProjectRowProps {
  project: DesktopSidebarProjectGroup;
  activePath: string;
  LinkComponent: NavLinkComponent;
  /** Whether this project has any chats/tasks to reveal. Controls the
   * accordion chevron; the column is always reserved so labels stay aligned
   * whether or not a project has items. */
  expandable?: boolean;
  /** Whether this project's chats/tasks are currently expanded. */
  expanded?: boolean;
  /** Pinned open: this is the project whose conversation is open, so it's kept
   * expanded and its collapse chevron is hidden (it can't be collapsed while
   * you're in it). */
  pinned?: boolean;
  /** Toggle this project's accordion (expand / collapse its chats/tasks). */
  onToggleExpanded?: () => void;
  /** Fires when the row navigates to the project (not the chevron). Lets the
   * parent resume "follow the active project" auto-expand. */
  onNavigate?: () => void;
  /** True when this project is currently in inline-rename mode (header span
   * swaps to a RenameInput). */
  projectRenaming?: boolean;
  onProjectRenameStart?: (projectId: string) => void;
  onProjectRenameConfirm?: (projectId: string, newName: string) => void;
  onProjectRenameCancel?: () => void;
  onProjectOpenInFinder?: (projectId: string) => void;
  onProjectExport?: (projectId: string) => void;
  onProjectRemove?: (projectId: string) => void;
}

const ProjectRow = ({
  project,
  activePath,
  LinkComponent,
  expandable = false,
  expanded = false,
  pinned = false,
  onToggleExpanded,
  onNavigate,
  projectRenaming = false,
  onProjectRenameStart,
  onProjectRenameConfirm,
  onProjectRenameCancel,
  onProjectOpenInFinder,
  onProjectExport,
  onProjectRemove,
}: ProjectRowProps) => {
  const { t } = useI18n();
  const isActiveProject = isActivePath(activePath, project.href);

  const hasAnyAction =
    !!onProjectOpenInFinder ||
    !!onProjectRenameStart ||
    !!onProjectRemove ||
    !!onProjectExport;

  return (
    <div className="mx-1">
      <LinkComponent
        to={project.href}
        onClick={() => {
          // Clicking a project always opens it; a collapsed expandable one also
          // expands (reveals its chats/tasks) in the same click. Collapsing is
          // the chevron's job — a click never collapses.
          if (expandable && !expanded) {
            onToggleExpanded?.();
          }
          onNavigate?.();
        }}
        className={cn(
          // ``group`` enables ``group-hover`` on the project-row ``...``
          // menu button (hidden until row hover; see below).
          "group relative grid h-[31px] cursor-default grid-cols-[16px_minmax(0,1fr)_auto] items-center gap-[7px] px-[10px] text-sm font-normal text-ink-heading outline-none transition-[background-color,box-shadow] duration-[120ms] focus-visible:outline-none focus-visible:ring-0 focus-visible:shadow-[0_6px_16px_rgba(17,24,39,0.12)]",
          projectRenaming ? "" : "rounded-[7px]",
          projectRenaming
            ? ""
            : isActiveProject
              ? "z-20 bg-card shadow-[0_6px_16px_rgba(17,24,39,0.12)] dark:bg-surface-muted dark:shadow-none"
              : "hover:bg-[rgba(0,0,0,0.03)] dark:hover:bg-surface-muted",
        )}
      >
        {/* Folder icon — first column, so it left-aligns with the "项目"
            section label. For an expandable project the accordion chevron
            overlays it on hover (and toggles expand), so there's no separate
            chevron column pushing the icon inward. A pinned (currently-open)
            project shows only the folder — no collapse chevron. */}
        <div className="relative flex h-4 w-4 items-center justify-start">
          {project.icon ? (
            <span
              className={cn(
                "flex h-3.5 w-3.5 shrink-0 items-center justify-center text-ink-meta",
                expandable && !pinned && "group-hover:opacity-0",
              )}
              aria-hidden="true"
            >
              {project.icon}
            </span>
          ) : (
            <FolderOpen
              className={cn(
                "h-3.5 w-3.5 shrink-0 text-ink-meta",
                expandable && !pinned && "group-hover:opacity-0",
              )}
              strokeWidth={2}
              aria-hidden="true"
            />
          )}
          {expandable && !pinned && (
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onToggleExpanded?.();
              }}
              onPointerDown={(e) => e.stopPropagation()}
              aria-label={
                expanded ? t("sidebar.showLess") : t("sidebar.showMore")
              }
              className="absolute inset-0 flex items-center justify-start rounded text-ink-muted opacity-0 transition-opacity hover:text-ink-body group-hover:opacity-100"
            >
              <ChevronDown
                className={cn(
                  "h-3 w-3 transition-transform duration-[150ms]",
                  !expanded && "-rotate-90",
                )}
                strokeWidth={2}
              />
            </button>
          )}
        </div>
        <span
          className={cn(
            SIDEBAR_ROW_TITLE_CLASS,
            projectRenaming && "overflow-visible",
          )}
        >
          {projectRenaming ? (
            <RenameInput
              initial={project.label}
              onConfirm={(v) =>
                onProjectRenameConfirm
                  ? onProjectRenameConfirm(project.id, v)
                  : onProjectRenameCancel?.()
              }
              onCancel={() => onProjectRenameCancel?.()}
            />
          ) : (
            project.label
          )}
        </span>
        {hasAnyAction && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                // Suppress the parent Link's onClick (navigation). Radix opens
                // via pointerdown and composeEventHandlers would skip its own
                // handler if defaultPrevented were true, so we don't
                // preventDefault.
                onClick={(e) => e.stopPropagation()}
                onPointerDown={(e) => e.stopPropagation()}
                // Hidden by default; revealed on row hover (``group-hover``),
                // when the trigger is focused, or while the menu itself is
                // open (Radix sets ``data-state="open"`` on the trigger).
                // Without the open-state rule the menu would visually
                // collapse onto an invisible anchor as soon as the pointer
                // leaves the row, even though the dropdown is still showing.
                className="flex h-6 w-6 items-center justify-center justify-self-end rounded-md text-ink-body opacity-0 transition-[opacity,background-color] hover:bg-surface-muted focus-visible:opacity-100 group-hover:opacity-100 data-[state=open]:opacity-100"
                aria-label={t("sidebar.moreActions")}
              >
                <MoreHorizontal className="h-3.5 w-3.5" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              className="min-w-[180px]"
              // Radix returns focus to the ``...`` trigger on close by
              // default. That stomps the ``RenameInput`` autofocus —
              // useEffect runs ``input.focus()`` on mount, then the
              // dropdown's close-auto-focus snaps it back to the trigger
              // and the user sees the row with no caret. Suppressing it
              // here lets the freshly-mounted input keep focus. Other
              // menu items (Open in Finder navigates; Delete opens its
              // own confirm dialog) don't rely on the auto-restore.
              onCloseAutoFocus={(e) => e.preventDefault()}
            >
              {onProjectRenameStart && (
                <DropdownMenuItem
                  onSelect={() => onProjectRenameStart(project.id)}
                >
                  <FilePenLine />
                  {t("sidebar.rename")}
                </DropdownMenuItem>
              )}
              {onProjectExport && (
                <DropdownMenuItem onSelect={() => onProjectExport(project.id)}>
                  <Download />
                  {t("project.export")}
                </DropdownMenuItem>
              )}
              {onProjectOpenInFinder && (
                <DropdownMenuItem
                  onSelect={() => onProjectOpenInFinder(project.id)}
                >
                  <ExternalLink />
                  {t("sidebar.openInFinder")}
                </DropdownMenuItem>
              )}
              {onProjectRemove && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    variant="destructive"
                    onSelect={() => onProjectRemove(project.id)}
                  >
                    <Trash2 />
                    {t("common.remove")}
                  </DropdownMenuItem>
                </>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </LinkComponent>
    </div>
  );
};

/**
 * Lightweight per-row session status indicator. Rendered to the left of
 * the label so the user can see which conversations still have a turn
 * in flight without opening them. ``running`` pulses to draw the eye;
 * everything else either shows a static dot or hides entirely.
 */

/* ── Main sidebar ──────────────────────────────────────────── */

/** One chat/task row in the sidebar — either nested under its project or in
 * the loose "对话 / Chats" group. Lets the user resume their last few
 * conversations / tasks without leaving the current page. */
export interface DesktopSidebarRecentItem {
  id: string;
  title: string;
  href: string;
  kind: "chat" | "task";
  /** ``true`` when the run is currently in the live ``running`` pool;
   * decorates the row with a brand-tinted pulsing dot. */
  isRunning?: boolean;
  /** Whether the row's session can be forked (host computes it from
   * runtime/origin/status — docs/design/session-fork.md). Rows without it
   * render no Fork entry. */
  canFork?: boolean;
  /** Optional icon rendered BEFORE the title — multi-target editions pass
   * an execution-origin icon (local/cloud) here. */
  leadingIcon?: ReactNode;
}

export interface DesktopSidebarProps {
  activePath: string;
  /** The project that owns the current route, resolved by the host (e.g. from
   * the active session's ``project_id``). Drives auto-expand of that project.
   * The host computes it because it can resolve the active conversation →
   * project instantly from its session store, whereas matching the route
   * against ``projectGroups`` items lags behind the runs list. */
  activeProjectId?: string | null;
  /** One entry per project project. */
  projectGroups: DesktopSidebarProjectGroup[];
  bottomItems: DesktopSidebarBottomItem[];
  /** Custom labeled groups (e.g. an edition's "市场" section). Each renders
   * with a Library-style heading between the main verbs area and the project
   * list; its items are the ``bottomItems`` whose ``group`` matches the
   * group id. Order = render order. */
  navGroups?: DesktopSidebarNavGroup[];
  /** Loose chats + tasks that don't belong to any project — rendered in the
   * "对话 / Chats" group below Projects. Newest first; the sidebar caps the
   * visible count with a "show more" toggle. Pass an empty array / omit to
   * hide the group. */
  chats?: DesktopSidebarRecentItem[];
  /** Optional content rendered at the very top of the sidebar, above the
   * primary action ("新对话"). Overlay editions use this to inject an org /
   * account switcher. Rendered in both collapsed and expanded states. */
  sidebarHeader?: ReactNode;
  sidebarExtraItems?: ReactNode;
  /** Optional content pinned at the very bottom of the sidebar, below the
   * Library / Settings nav block. Overlay editions use this to inject a
   * bottom-left account / org menu. Rendered in both collapsed and expanded
   * states. */
  sidebarFooter?: ReactNode;
  mascotSrc?: string | null;
  LinkComponent?: NavLinkComponent;
  primaryActionHref?: string;
  /** Fires whenever the primary action ("新对话") is clicked, in addition to
   * its navigation — including when the home route is already active (a
   * same-path click that wouldn't otherwise trigger a route effect). */
  onPrimaryAction?: () => void;
  /** Callback when the "+" button next to Projects is clicked. When
   *  provided alongside ``onImportProject`` the "+" becomes a dropdown
   *  with Create (this) + Import actions. */
  onAddProject?: () => void;
  /** When provided, the "+" Projects dropdown shows an "Import project…"
   *  item that calls this. Hidden otherwise (the "+" stays a single action). */
  onImportProject?: () => void;
  /** Additional menu items rendered at the end of the Projects "+" dropdown.
   * Overlay editions use this stable slot to add project creation/import
   * actions without replacing the OSS menu. */
  projectAddMenuItems?: ReactNode;
  /** Project row "..." actions. Pass ``undefined`` to hide an option. */
  onProjectOpenInFinder?: (projectId: string) => void;
  /** When provided, the project "..." menu shows an "Export project" item
   *  that calls this with the project id. */
  onProjectExport?: (projectId: string) => void;
  /** When provided, the "..." menu shows a Rename entry. The callback is
   * triggered with the new name after the user inline-edits the project
   * header. The sidebar manages the inline edit state itself; the host
   * just persists the rename. */
  onProjectRename?: (projectId: string, newName: string) => void;
  onProjectRemove?: (projectId: string) => void;
  /** When provided, chat rows in RECENTS show a "..." menu with a Rename
   * entry that swaps the title for an inline input. Tasks are skipped —
   * no task-rename endpoint exists yet. */
  onRecentRename?: (recentId: string, newName: string) => void;
  /** When provided, chat rows in RECENTS show a "..." menu with a Delete
   * entry that opens a confirm dialog. Same scope as ``onRecentRename``:
   * chats only (no backend ``DELETE /v1/tasks/{id}``). */
  onRecentDelete?: (recentId: string) => void;
  /** When provided, chat rows whose ``canFork`` is true show a Fork entry
   * (whole-session fork — docs/design/session-fork.md). */
  onRecentFork?: (recentId: string) => void;
  /** Row whose fork request is in flight (forks can take seconds on
   * remote-kernel deployments — #879). That row's right-edge slot shows a
   * spinner, and every Fork entry is disabled until the request settles. */
  recentForkPendingId?: string | null;
  /** Whether sidebar is collapsed (controlled externally) */
  collapsed?: boolean;
}

export const DesktopSidebar = ({
  activePath,
  activeProjectId = null,
  bottomItems,
  navGroups = [],
  chats = [],
  sidebarHeader,
  sidebarExtraItems,
  sidebarFooter,
  mascotSrc,
  LinkComponent = DefaultNavLink,
  primaryActionHref = "/conversation/new",
  onPrimaryAction,
  projectGroups,
  onAddProject,
  onImportProject,
  projectAddMenuItems,
  onProjectOpenInFinder,
  onProjectExport,
  onProjectRename,
  onProjectRemove,
  onRecentRename,
  onRecentDelete,
  onRecentFork,
  recentForkPendingId = null,
  collapsed = false,
}: DesktopSidebarProps) => {
  const { t } = useI18n();
  const resolvedMascotSrc = mascotSrc ?? assetUrl("mascot.png");
  const [projectRenamingId, setProjectRenamingId] = useState<string | null>(
    null,
  );
  const [projectsSectionOpen, setProjectsSectionOpen] = useState(true);
  const [chatsSectionOpen, setChatsSectionOpen] = useState(true);
  // Navigation-following accordion: navigating to any menu item outside an open
  // project collapses it (the effect below keeps only the active project open).
  // Between navigations the row chevron can expand additional projects — a
  // chevron toggle never collapses another project, only a navigation does.
  const [expandedProjectIds, setExpandedProjectIds] = useState<Set<string>>(
    () => new Set(),
  );
  // Per-group "show more" state (keyed by project id, or "chats" for the
  // loose group): false → first few rows, true → all rows.
  const [groupExpanded, setGroupExpanded] = useState<Record<string, boolean>>(
    {},
  );
  // Inline rename + delete confirmation state for chat rows (in projects and
  // the Chats group alike). Both null when nothing is in flight.
  const [recentRenamingId, setRecentRenamingId] = useState<string | null>(null);
  const [recentDeleting, setRecentDeleting] =
    useState<DesktopSidebarRecentItem | null>(null);
  const [recentDeleteInFlight, setRecentDeleteInFlight] = useState(false);
  // Project-nested runs collapse early (each project keeps a short preview);
  // the no-project chats group is the main recents list, so it shows more
  // before the show-more toggle appears.
  const RUNS_COLLAPSED = 5;
  const CHATS_COLLAPSED = 10;

  // Collapse-on-navigate: selecting any menu item outside an open project
  // collapses it. On every navigation keep only the active project's accordion
  // open; a chevron toggle doesn't change activePath, so peeking another project
  // (or toggling the active one) never collapses anything — only navigating
  // does. The active project's own collapse chevron is hidden, so it stays open.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setExpandedProjectIds((prev) => {
      const next = activeProjectId
        ? new Set<string>([activeProjectId])
        : new Set<string>();
      const unchanged =
        prev.size === next.size && [...prev].every((id) => next.has(id));
      return unchanged ? prev : next;
    });
  }, [activePath, activeProjectId]);

  const toggleGroup = (key: string) =>
    setGroupExpanded((m) => ({ ...m, [key]: !m[key] }));

  // One chat/task row. ``depth`` sets the left indent so project-nested rows
  // sit under the project label while Chats-group rows align with the section
  // label. Chat rows carry a Rename/Delete menu; tasks don't (no task-rename
  // / task-delete endpoint).
  const renderRunRow = (
    item: DesktopSidebarRecentItem,
    depth: "project" | "chats",
  ) => {
    // Chats-group rows align strictly with the "对话" section header: the row
    // carries mx-1 (4px) that the header doesn't, so pl-[10px] lands the text
    // at the same x as the header (14px). Project-nested rows indent by one
    // level so their text lands under the project label (mx-1 4 + pl 33 = 37,
    // + nav px-3 12 = 49px, matching the project row's label start).
    const padClass = depth === "project" ? "pl-[33px]" : "pl-[10px]";
    const active = isActivePath(activePath, item.href);
    if (recentRenamingId === item.id) {
      return (
        <div
          key={`run-${item.id}`}
          className={cn(
            "relative mx-1 flex items-center gap-2 py-[5px] pr-[10px]",
            padClass,
          )}
        >
          <RenameInput
            initial={item.title}
            onConfirm={(v) => {
              onRecentRename?.(item.id, v);
              setRecentRenamingId(null);
            }}
            onCancel={() => setRecentRenamingId(null)}
          />
        </div>
      );
    }
    const showRowMenu =
      item.kind === "chat" && (onRecentRename || onRecentDelete || onRecentFork);
    // This row's fork request is in flight — the right-edge slot swaps to a
    // spinner (replacing the dot / "…" menu) until the request settles.
    const forkPending = recentForkPendingId === item.id;
    return (
      <LinkComponent
        key={`run-${item.id}`}
        to={item.href}
        className={cn(
          "group/recent-row relative mx-1 flex cursor-default items-center gap-2 rounded-[7px] py-[5px] pr-[10px] text-[12.5px] outline-none transition-colors duration-[120ms] focus-visible:outline-none focus-visible:shadow-[0_6px_16px_rgba(17,24,39,0.12)]",
          padClass,
          // The selected row carries the bg-card highlight + drop shadow. Don't
          // layer a hover background on top of it (the hover affordance is for
          // non-selected rows only), and lift it with z-10 so its shadow paints
          // above the adjacent rows — otherwise hovering the row right below it
          // covers that shadow with the hover background.
          active
            ? "z-10 bg-card text-ink-heading shadow-[0_6px_16px_rgba(17,24,39,0.12)] dark:bg-surface-muted dark:shadow-none"
            : "text-ink-meta hover:bg-surface-soft hover:text-ink-heading",
        )}
      >
        {item.leadingIcon ?? null}
        <span className="min-w-0 flex-1 truncate">{item.title}</span>
        {(item.isRunning || showRowMenu || forkPending) && (
          <span className="relative flex h-5 w-5 shrink-0 items-center justify-center">
            {forkPending && (
              <Loader2
                aria-label={t("sidebar.forking")}
                className="h-3.5 w-3.5 animate-spin text-ink-muted"
              />
            )}
            {!forkPending && item.isRunning && (
              <span
                aria-label={t("sidebar.runningIndicator")}
                className={cn(
                  // Running dot in the right-edge slot. When the row also has a
                  // "…" menu the dot fades on hover so the menu can take its
                  // place; rows without a menu (tasks) keep it visible.
                  "pointer-events-none h-1.5 w-1.5 rounded-full bg-brand animate-pulse",
                  showRowMenu && "group-hover/recent-row:opacity-0",
                )}
              />
            )}
            {!forkPending && showRowMenu && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    onClick={(e) => e.stopPropagation()}
                    onPointerDown={(e) => e.stopPropagation()}
                    aria-label={t("sidebar.moreActions")}
                    className="absolute inset-0 flex items-center justify-center rounded text-ink-muted opacity-0 transition-opacity hover:bg-surface-muted hover:text-ink-body focus-visible:opacity-100 group-hover/recent-row:opacity-100 data-[state=open]:opacity-100"
                  >
                    <MoreHorizontal className="h-3.5 w-3.5" />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  className="min-w-[160px]"
                  onCloseAutoFocus={(e) => e.preventDefault()}
                >
                  {onRecentRename && (
                    <DropdownMenuItem
                      onSelect={() => setRecentRenamingId(item.id)}
                    >
                      <FilePenLine />
                      {t("sidebar.rename")}
                    </DropdownMenuItem>
                  )}
                  {onRecentFork && item.canFork && (
                    <DropdownMenuItem
                      disabled={recentForkPendingId != null}
                      onSelect={() => onRecentFork(item.id)}
                    >
                      <ForkIcon />
                      {t("sidebar.fork")}
                    </DropdownMenuItem>
                  )}
                  {onRecentDelete && (
                    <>
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => setRecentDeleting(item)}
                      >
                        <Trash2 />
                        {t("common.delete")}
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
          </span>
        )}
      </LinkComponent>
    );
  };

  // A group's rows (project-nested or the Chats group), capped at
  // ``RUNS_COLLAPSED`` with a trailing show-more / show-less toggle.
  const renderGroupItems = (
    key: string,
    items: DesktopSidebarRecentItem[],
    depth: "project" | "chats",
  ) => {
    const expanded = !!groupExpanded[key];
    const collapsedLimit = depth === "chats" ? CHATS_COLLAPSED : RUNS_COLLAPSED;
    const visible = expanded ? items : items.slice(0, collapsedLimit);
    // Match renderRunRow's indent (pl-[33px] project / pl-[10px] chats) so the
    // show-more toggle's text lines up with the rows above it.
    const padClass = depth === "project" ? "pl-[33px]" : "pl-[10px]";
    return (
      <>
        {visible.map((item) => renderRunRow(item, depth))}
        {items.length > collapsedLimit && (
          <button
            type="button"
            onClick={() => toggleGroup(key)}
            className={cn(
              "mx-1 flex items-center gap-1 py-[3px] pr-[10px] text-[11.5px] text-ink-muted transition-colors hover:text-ink-body",
              padClass,
            )}
          >
            {expanded ? t("sidebar.showLess") : t("sidebar.showMore")}
            <ChevronDown
              className={cn(
                "h-3 w-3 transition-transform duration-[150ms]",
                expanded && "rotate-180",
              )}
              strokeWidth={2}
            />
          </button>
        )}
      </>
    );
  };

  const libraryItems = bottomItems.filter((item) => item.group === "library");

  return (
    <>
      <aside
        className={cn(
          "relative flex h-full shrink-0 flex-col transition-[width] duration-[180ms] ease-out",
          collapsed ? "w-[56px]" : "w-[220px] rounded-[14px]",
        )}
      >
        {collapsed ? (
          <TooltipProvider delayDuration={150}>
            {/* Top: 快速对话 + 知识库 / 技能库 / 自动化 + 项目入口 */}
            <div className="flex flex-col items-center gap-2 px-0 pt-2">
              {sidebarHeader}
              <Tooltip>
                <TooltipTrigger asChild>
                  <LinkComponent
                    to={primaryActionHref}
                    onClick={onPrimaryAction}
                    className="flex h-9 w-9 cursor-default items-center justify-center rounded-lg text-ink-body transition-colors duration-[120ms] hover:bg-surface-soft"
                  >
                    <MessageCirclePlus className="h-4 w-4" />
                  </LinkComponent>
                </TooltipTrigger>
                <TooltipContent side="right">
                  {t("sidebar.newConversation")}
                </TooltipContent>
              </Tooltip>
              {bottomItems
                .filter((item) => item.group === "main")
                .map((item) => {
                  const Icon = bottomIcon(item.icon);
                  return (
                    <Tooltip key={item.id}>
                      <TooltipTrigger asChild>
                        <LinkComponent
                          to={item.href}
                          className="flex h-9 w-9 cursor-default items-center justify-center rounded-lg text-ink-body transition-colors duration-[120ms] hover:bg-surface-soft"
                        >
                          <Icon className="h-4 w-4" />
                        </LinkComponent>
                      </TooltipTrigger>
                      <TooltipContent side="right">{item.label}</TooltipContent>
                    </Tooltip>
                  );
                })}
              {sidebarExtraItems}
              {/* Custom-group items (rail mode: flat icon list, no headings) */}
              {navGroups.flatMap((groupDef) =>
                bottomItems
                  .filter((item) => item.group === groupDef.id)
                  .map((item) => {
                    const Icon = bottomIcon(item.icon);
                    return (
                      <Tooltip key={item.id}>
                        <TooltipTrigger asChild>
                          <LinkComponent
                            to={item.href}
                            className="flex h-9 w-9 cursor-default items-center justify-center rounded-lg text-ink-body transition-colors duration-[120ms] hover:bg-surface-soft"
                          >
                            <Icon className="h-4 w-4" />
                          </LinkComponent>
                        </TooltipTrigger>
                        <TooltipContent side="right">
                          {item.label}
                        </TooltipContent>
                      </Tooltip>
                    );
                  }),
              )}
              <Tooltip>
                <TooltipTrigger asChild>
                  <LinkComponent
                    to="/projects"
                    className="flex h-9 w-9 cursor-default items-center justify-center rounded-lg text-ink-body transition-colors duration-[120ms] hover:bg-surface-soft"
                  >
                    <FolderOpen className="h-4 w-4" />
                  </LinkComponent>
                </TooltipTrigger>
                <TooltipContent side="right">
                  {t("sidebar.projects")}
                </TooltipContent>
              </Tooltip>
            </div>
            <div className="flex-1" />
            {/* Bottom: Library (Agents / Skills / Connectors / Knowledge) then
                Settings. Library lives down here so the top stays focused on
                ``Assistant`` and other project verbs; configuration-y
                resources sit together near Settings. */}
            <div className="flex flex-col items-center gap-2 pb-4">
              {bottomItems
                .filter((item) => item.group === "library")
                .map((item) => {
                  const Icon = bottomIcon(item.icon);
                  return (
                    <Tooltip key={item.id}>
                      <TooltipTrigger asChild>
                        <LinkComponent
                          to={item.href}
                          className="relative flex h-9 w-9 cursor-default items-center justify-center rounded-lg text-ink-body transition-colors duration-[120ms] hover:bg-surface-soft"
                        >
                          <Icon className="h-4 w-4" />
                          {item.badgeDot ? (
                            <span className="absolute right-1.5 top-1.5 h-1.5 w-1.5 rounded-full bg-[#f54b4b]" />
                          ) : null}
                        </LinkComponent>
                      </TooltipTrigger>
                      <TooltipContent side="right">{item.label}</TooltipContent>
                    </Tooltip>
                  );
                })}
              {/* Spacer keeps Library and Settings visually distinct in the
                  collapsed rail too. */}
              <div className="h-3" aria-hidden />
              {bottomItems
                .filter((item) => item.group === "settings")
                .map((item) => {
                  const Icon = bottomIcon(item.icon);
                  return (
                    <Tooltip key={item.id}>
                      <TooltipTrigger asChild>
                        <LinkComponent
                          to={item.href}
                          className="flex h-9 w-9 cursor-default items-center justify-center rounded-lg text-ink-body transition-colors duration-[120ms] hover:bg-surface-soft"
                        >
                          <Icon className="h-4 w-4" />
                        </LinkComponent>
                      </TooltipTrigger>
                      <TooltipContent side="right">{item.label}</TooltipContent>
                    </Tooltip>
                  );
                })}
              {sidebarFooter}
            </div>
          </TooltipProvider>
        ) : (
          <>
            {/* Two-region nav: a fixed-height upper block (新对话 +
                知识库 / 技能库 / 自动化 + 项目 + "RECENTS" section
                header) above an independently scrollable list of
                recent sessions. ``min-h-0`` on the outer flex column
                lets the inner ``overflow-y-auto`` actually shrink and
                scroll instead of pushing the page. */}
            <nav
              className="relative z-10 flex min-h-0 flex-1 flex-col px-3 pt-2"
              aria-label="Prototype desktop sidebar"
            >
              <div className="flex shrink-0 flex-col gap-0.5">
                {sidebarHeader}
                <SidebarLink
                  href={primaryActionHref}
                  active={isActivePath(activePath, primaryActionHref)}
                  LinkComponent={LinkComponent}
                  onClick={onPrimaryAction}
                >
                  <MessageCirclePlus
                    className="h-3.5 w-3.5 shrink-0"
                    strokeWidth={2}
                  />
                  <span>{t("sidebar.newConversation")}</span>
                </SidebarLink>
                {/* Project utility links (PRD-NEXT §3.4): 小助手 / 自动化，
                  紧跟新对话。Library 分组在「项目」之后单独成区，设置固定在
                  sidebar 最底部。 */}
                {bottomItems
                  .filter((item) => item.group === "main")
                  .map((item) => {
                    const Icon = bottomIcon(item.icon);
                    return (
                      <SidebarLink
                        key={item.id}
                        href={item.href}
                        active={isActivePath(activePath, item.href)}
                        LinkComponent={LinkComponent}
                      >
                        <Icon
                          className="h-3.5 w-3.5 shrink-0"
                          strokeWidth={2}
                        />
                        <span>{item.label}</span>
                        {item.badgeCount ? (
                          <span className="ml-auto flex items-center gap-1 text-micro font-medium text-ink-meta">
                            <span
                              className={cn(
                                "h-1.5 w-1.5 rounded-full animate-pulse",
                                statusDotClass("running"),
                              )}
                            />
                            {item.badgeCount}
                          </span>
                        ) : item.badgeDot ? (
                          <span className="ml-auto flex items-center">
                            <span className="h-1.5 w-1.5 rounded-full bg-[#f54b4b]" />
                          </span>
                        ) : null}
                      </SidebarLink>
                    );
                  })}
                {sidebarExtraItems}
                {/* Custom labeled groups (edition-declared, e.g. 市场) —
                    Library-style heading + items, pinned between the main
                    verbs and the scrollable project list. */}
                {navGroups.map((groupDef) => {
                  const groupItems = bottomItems.filter(
                    (item) => item.group === groupDef.id,
                  );
                  if (groupItems.length === 0) return null;
                  return (
                    <div key={groupDef.id} className="pt-2">
                      <div className="pb-1 pl-[14px] pr-3 pt-1">
                        <span className="text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
                          {groupDef.label}
                        </span>
                      </div>
                      {groupItems.map((item) => {
                        const Icon = bottomIcon(item.icon);
                        return (
                          <SidebarLink
                            key={item.id}
                            href={item.href}
                            active={isActivePath(activePath, item.href)}
                            LinkComponent={LinkComponent}
                          >
                            <Icon
                              className="h-3.5 w-3.5 shrink-0"
                              strokeWidth={2}
                            />
                            <span>{item.label}</span>
                            {item.badgeDot ? (
                              <span className="ml-auto flex items-center">
                                <span className="h-1.5 w-1.5 rounded-full bg-[#f54b4b]" />
                              </span>
                            ) : null}
                          </SidebarLink>
                        );
                      })}
                    </div>
                  );
                })}
              </div>
              {/* Scrollable region: projects + conversations. The block above
                  (新对话 + utility links) stays pinned; this list owns the
                  ``overflow-y-auto`` so a long project / chat list scrolls
                  within the nav instead of bleeding past it into the footer.
                  ``min-h-0`` lets it actually shrink under flex. ``-mr-3 pr-3``
                  pushes the scrollbar out to the sidebar's right edge (against
                  the gap before the main panel) while keeping the rows inset, so
                  the bar rides the edge instead of overlapping the row text.
                  ``pl-1.5 -ml-1.5`` keeps row text visually aligned while
                  giving active-row shadows room to paint on the left edge. */}
              <div className="-mr-3 -ml-1.5 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto pr-3 pl-1.5">
                <SectionLabel
                  open={projectsSectionOpen}
                  onToggle={() => setProjectsSectionOpen((v) => !v)}
                  action={
                    onImportProject || projectAddMenuItems ? (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <button
                            type="button"
                            aria-label={t("sidebar.addProject")}
                            className="flex h-6 w-6 items-center justify-center rounded-md text-ink-body transition-colors hover:bg-surface-muted"
                          >
                            <Plus className="h-3 w-3" />
                          </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          align="start"
                          className="min-w-[180px]"
                          onCloseAutoFocus={(e) => e.preventDefault()}
                        >
                          {onAddProject && (
                            <DropdownMenuItem onSelect={onAddProject}>
                              <Plus />
                              {t("project.create")}
                            </DropdownMenuItem>
                          )}
                          {onImportProject && (
                            <DropdownMenuItem onSelect={onImportProject}>
                              <Upload />
                              {t("project.import")}
                            </DropdownMenuItem>
                          )}
                          {projectAddMenuItems && (
                            <>
                              <DropdownMenuSeparator />
                              {projectAddMenuItems}
                            </>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    ) : (
                      <button
                        type="button"
                        className="flex h-6 w-6 items-center justify-center rounded-md text-ink-body transition-colors hover:bg-surface-muted"
                        onClick={onAddProject}
                      >
                        <Plus className="h-3 w-3" />
                      </button>
                    )
                  }
                >
                  {t("sidebar.projects")}
                </SectionLabel>
                {!projectsSectionOpen ? null : projectGroups.length === 0 ? (
                  <div className="mx-1 px-[10px] py-[7px] text-2xs text-ink-meta">
                    {t("sidebar.noProjects")}
                  </div>
                ) : (
                  projectGroups.map((project) => {
                    const expandable = (project.items?.length ?? 0) > 0;
                    // The project you're currently in is pinned open — always
                    // expanded, and its collapse chevron is hidden (you can't
                    // collapse the project you're working in; it'd just reopen).
                    const pinned = project.id === activeProjectId;
                    const expanded =
                      expandable &&
                      (expandedProjectIds.has(project.id) || pinned);
                    return (
                      <div key={project.id}>
                        <ProjectRow
                          project={project}
                          activePath={activePath}
                          LinkComponent={LinkComponent}
                          expandable={expandable}
                          expanded={expanded}
                          pinned={pinned}
                          onToggleExpanded={() =>
                            setExpandedProjectIds((prev) => {
                              const next = new Set(prev);
                              if (next.has(project.id)) next.delete(project.id);
                              else next.add(project.id);
                              return next;
                            })
                          }
                          projectRenaming={projectRenamingId === project.id}
                          onProjectRenameStart={
                            onProjectRename
                              ? (id) => setProjectRenamingId(id)
                              : undefined
                          }
                          onProjectRenameConfirm={(id, newName) => {
                            onProjectRename?.(id, newName);
                            setProjectRenamingId(null);
                          }}
                          onProjectRenameCancel={() =>
                            setProjectRenamingId(null)
                          }
                          onProjectOpenInFinder={onProjectOpenInFinder}
                          onProjectExport={onProjectExport}
                          onProjectRemove={onProjectRemove}
                        />
                        {expanded &&
                          project.items &&
                          renderGroupItems(
                            project.id,
                            project.items,
                            "project",
                          )}
                      </div>
                    );
                  })
                )}

                {/* 对话 / Chats — chats + tasks that don't belong to any
                    project (quick conversations, project-less tasks). The
                    section header stays visible even when there is no history
                    yet, so the "对话" label never disappears. */}
                <>
                  <SectionLabel
                    open={chatsSectionOpen}
                    onToggle={() => setChatsSectionOpen((v) => !v)}
                  >
                    {t("sidebar.chats")}
                  </SectionLabel>
                  {chatsSectionOpen &&
                    chats.length > 0 &&
                    renderGroupItems("chats", chats, "chats")}
                </>
              </div>
            </nav>

            {/* Mascot — anchored absolutely at the bottom area
                (above the settings link), z-0 so the scrollable nav
                sits above it. When the session list is short, the
                empty space at the bottom of the nav reveals the
                mascot. When the user expands a project / the Chats
                group and the list gets long, the nav scrolls over the
                mascot — line-drawing bleeds through behind the
                links so it's still felt without obscuring text. */}
            {resolvedMascotSrc ? (
              <img
                src={resolvedMascotSrc}
                alt=""
                aria-hidden="true"
                className="pointer-events-none absolute bottom-[64px] left-1/2 z-0 h-[170px] w-auto -translate-x-1/2 select-none opacity-60"
              />
            ) : null}

            {/* Bottom-pinned: Library + Settings. Library (Agents / Skills /
                Connectors / Knowledge) sits right above Settings so resource
                management groups with app config; the scrollable nav above
                stays focused on project verbs + projects. ``relative z-10``
                so links sit in front of the absolute-positioned mascot; the
                opaque ``bg-background`` (the sidebar's own colour) hides the
                scrollable nav when a long list scrolls up behind it instead of
                letting the text bleed through. */}
            <div className="relative z-10 flex flex-col gap-0.5 bg-background px-3 pb-4 pt-2">
              {libraryItems.length > 0 && (
                <>
                  <div className="pb-1 pl-[14px] pr-3 pt-1">
                    <span className="text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
                      {t("sidebar.library")}
                    </span>
                  </div>
                  {libraryItems.map((item) => {
                    const Icon = bottomIcon(item.icon);
                    return (
                      <SidebarLink
                        key={item.id}
                        href={item.href}
                        active={isActivePath(activePath, item.href)}
                        LinkComponent={LinkComponent}
                      >
                        <Icon
                          className="h-3.5 w-3.5 shrink-0"
                          strokeWidth={2}
                        />
                        <span>{item.label}</span>
                        {item.badgeDot ? (
                          <span className="ml-auto flex items-center">
                            <span className="h-1.5 w-1.5 rounded-full bg-[#f54b4b]" />
                          </span>
                        ) : null}
                      </SidebarLink>
                    );
                  })}
                </>
              )}
              {/* Spacer pushes Settings away from the Library block so they
                  still read as two distinct things — Library is "resources",
                  Settings is "app config" — even though they share the bottom
                  pinned slot. */}
              <div className="h-3" aria-hidden />
              {bottomItems
                .filter((item) => item.group === "settings")
                .map((item) => {
                  const Icon = bottomIcon(item.icon);
                  return (
                    <SidebarLink
                      key={item.id}
                      href={item.href}
                      active={isActivePath(activePath, item.href)}
                      LinkComponent={LinkComponent}
                    >
                      <Icon className="h-3.5 w-3.5 shrink-0" strokeWidth={2} />
                      <span>{item.label}</span>
                    </SidebarLink>
                  );
                })}
              {sidebarFooter}
            </div>
          </>
        )}
      </aside>
      <DeleteConfirmDialog
        open={recentDeleting !== null}
        onOpenChange={(open) => {
          if (!open && !recentDeleteInFlight) setRecentDeleting(null);
        }}
        itemName={recentDeleting?.title ?? ""}
        loading={recentDeleteInFlight}
        onConfirm={() => {
          const target = recentDeleting;
          if (!target || !onRecentDelete) return;
          setRecentDeleteInFlight(true);
          try {
            onRecentDelete(target.id);
          } finally {
            setRecentDeleteInFlight(false);
            setRecentDeleting(null);
          }
        }}
      />
    </>
  );
};
