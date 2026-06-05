import { useState, useRef, useEffect, type ReactNode } from "react";
import {
  Activity,
  BookOpen,
  Bot,
  ChevronDown,
  Clock,
  ExternalLink,
  FilePenLine,
  FolderOpen,
  Link2,
  ListTodo,
  MessageCirclePlus,
  MessageSquare,
  MoreHorizontal,
  Plus,
  Settings,
  Trash2,
  Zap,
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
import type { NavLinkComponent } from "./AppShell";
import { useI18n } from "../hooks/use-i18n";

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

/** A project workspace entry in the sidebar's "项目" section. Sessions no
 * longer nest under projects — all sessions (project + chat) live in the
 * unified RECENTS section below. */
export interface DesktopSidebarProjectGroup {
  id: string;
  label: string;
  /** Link target for the project landing page. */
  href: string;
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
  icon:
    | "assistant"
    | "knowledge"
    | "skills"
    | "scheduled"
    | "activity"
    | "system"
    | "settings"
    | "agents"
    | "connectors"
    | "projectTasks";
  /** Which sidebar region the item renders in (PRD-NEXT §3.4 IA). */
  group: "workspace" | "library" | "settings";
  /** Optional trailing count badge (e.g. running-runs count on Activity).
   *  Falsy / 0 → no badge. */
  badgeCount?: number;
}

const BOTTOM_ICON_MAP = {
  assistant: MessageSquare,
  knowledge: BookOpen,
  skills: Zap,
  scheduled: Clock,
  activity: Activity,
  system: Activity,
  settings: Settings,
  agents: Bot,
  connectors: Link2,
  projectTasks: ListTodo,
} as const;

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
  editing = false,
}: {
  active?: boolean;
  children: React.ReactNode;
  href: string;
  LinkComponent: NavLinkComponent;
  onContextMenu?: React.MouseEventHandler;
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
      "relative mx-1 flex cursor-default items-center gap-[9px] px-[10px] py-[7px] text-[13px] font-normal text-ink-heading outline-none transition-[background-color,box-shadow] duration-[120ms] focus-visible:outline-none focus-visible:ring-0 focus-visible:shadow-[0_6px_16px_rgba(17,24,39,0.12)]",
      editing ? "" : "rounded-[7px]",
      editing
        ? ""
        : active
          ? "z-20 bg-card shadow-[0_6px_16px_rgba(17,24,39,0.12)] dark:bg-surface-muted dark:shadow-none"
          : "hover:bg-[rgba(0,0,0,0.03)] dark:hover:bg-surface-muted",
    )}
    onContextMenu={onContextMenu}
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
  /** True when this project is currently in inline-rename mode (header span
   * swaps to a RenameInput). */
  projectRenaming?: boolean;
  onProjectRenameStart?: (projectId: string) => void;
  onProjectRenameConfirm?: (projectId: string, newName: string) => void;
  onProjectRenameCancel?: () => void;
  onProjectOpenInFinder?: (projectId: string) => void;
  onProjectRemove?: (projectId: string) => void;
}

const ProjectRow = ({
  project,
  activePath,
  LinkComponent,
  projectRenaming = false,
  onProjectRenameStart,
  onProjectRenameConfirm,
  onProjectRenameCancel,
  onProjectOpenInFinder,
  onProjectRemove,
}: ProjectRowProps) => {
  const { t } = useI18n();
  const isActiveProject = isActivePath(activePath, project.href);

  const hasAnyAction =
    !!onProjectOpenInFinder || !!onProjectRenameStart || !!onProjectRemove;

  return (
    <div className="mx-1">
      <LinkComponent
        to={project.href}
        className={cn(
          // ``group`` enables ``group-hover`` on the project-row ``...``
          // menu button (hidden until row hover; see below).
          "group relative grid h-[31px] cursor-default grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-[9px] px-[10px] text-[13px] font-normal text-ink-heading outline-none transition-[background-color,box-shadow] duration-[120ms] focus-visible:outline-none focus-visible:ring-0 focus-visible:shadow-[0_6px_16px_rgba(17,24,39,0.12)]",
          projectRenaming ? "" : "rounded-[7px]",
          projectRenaming
            ? ""
            : isActiveProject
              ? "z-20 bg-card shadow-[0_6px_16px_rgba(17,24,39,0.12)] dark:bg-surface-muted dark:shadow-none"
              : "hover:bg-[rgba(0,0,0,0.03)] dark:hover:bg-surface-muted",
        )}
      >
        <FolderOpen
          className="h-3.5 w-3.5 shrink-0 text-ink-meta"
          strokeWidth={2}
          aria-hidden="true"
        />
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

/** One row in the sidebar's RECENTS strip — a chat session or a task.
 * Surfaced under the Activity link so the user can resume their last few
 * conversations / tasks without leaving the current page. */
export interface DesktopSidebarRecentItem {
  id: string;
  title: string;
  href: string;
  kind: "chat" | "task";
  /** ``true`` when the run is currently in the live ``running`` pool;
   * decorates the row with a brand-tinted pulsing dot. */
  isRunning?: boolean;
}

export interface DesktopSidebarProps {
  activePath: string;
  /** One entry per project workspace. */
  projectGroups: DesktopSidebarProjectGroup[];
  bottomItems: DesktopSidebarBottomItem[];
  /** Optional Recents list rendered under the Activity link. Folded shows
   * 3 rows, expanded shows up to 8. Pass an empty array / omit to hide. */
  recents?: DesktopSidebarRecentItem[];
  /** Optional content rendered at the very top of the sidebar, above the
   * primary action ("新对话"). Overlay editions use this to inject an org /
   * account switcher. Rendered in both collapsed and expanded states. */
  sidebarHeader?: ReactNode;
  sidebarExtraItems?: ReactNode;
  mascotSrc?: string | null;
  LinkComponent?: NavLinkComponent;
  primaryActionHref?: string;
  /** Callback when the "+" button next to Projects is clicked */
  onAddProject?: () => void;
  /** Project row "..." actions. Pass ``undefined`` to hide an option. */
  onProjectOpenInFinder?: (projectId: string) => void;
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
  /** Whether sidebar is collapsed (controlled externally) */
  collapsed?: boolean;
}

export const DesktopSidebar = ({
  activePath,
  bottomItems,
  recents,
  sidebarHeader,
  sidebarExtraItems,
  mascotSrc = "./mascot.png",
  LinkComponent = DefaultNavLink,
  primaryActionHref = "/conversation/new",
  projectGroups,
  onAddProject,
  onProjectOpenInFinder,
  onProjectRename,
  onProjectRemove,
  onRecentRename,
  onRecentDelete,
  collapsed = false,
}: DesktopSidebarProps) => {
  const { t } = useI18n();
  const [projectRenamingId, setProjectRenamingId] = useState<string | null>(
    null,
  );
  const [projectsSectionOpen, setProjectsSectionOpen] = useState(true);
  // Recents fold state: collapsed by default (only 3 rows), opens to 8.
  // Lives here (not lifted) because no other sidebar feature needs to
  // observe it.
  const [recentsExpanded, setRecentsExpanded] = useState(false);
  // Inline rename + delete confirmation state for the RECENTS chat rows.
  // Both null when nothing is in flight.
  const [recentRenamingId, setRecentRenamingId] = useState<string | null>(null);
  const [recentDeleting, setRecentDeleting] =
    useState<DesktopSidebarRecentItem | null>(null);
  const [recentDeleteInFlight, setRecentDeleteInFlight] = useState(false);
  const RECENTS_COLLAPSED = 3;
  const RECENTS_EXPANDED = 8;
  const visibleRecents = recents
    ? recents.slice(0, recentsExpanded ? RECENTS_EXPANDED : RECENTS_COLLAPSED)
    : [];
  const canExpandRecents = (recents?.length ?? 0) > RECENTS_COLLAPSED;

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
                .filter((item) => item.group === "workspace")
                .map((item) => {
                  const Icon = BOTTOM_ICON_MAP[item.icon];
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
                ``Assistant`` and other workspace verbs; configuration-y
                resources sit together near Settings. */}
            <div className="flex flex-col items-center gap-2 pb-4">
              {bottomItems
                .filter((item) => item.group === "library")
                .map((item) => {
                  const Icon = BOTTOM_ICON_MAP[item.icon];
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
              {/* Spacer keeps Library and Settings visually distinct in the
                  collapsed rail too. */}
              <div className="h-3" aria-hidden />
              {bottomItems
                .filter((item) => item.group === "settings")
                .map((item) => {
                  const Icon = BOTTOM_ICON_MAP[item.icon];
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
                >
                  <MessageCirclePlus
                    className="h-3.5 w-3.5 shrink-0"
                    strokeWidth={2}
                  />
                  <span>{t("sidebar.newConversation")}</span>
                </SidebarLink>
                {/* Workspace utility links (PRD-NEXT §3.4): 小助手 / 自动化，
                  紧跟新对话。Library 分组在「项目」之后单独成区，设置固定在
                  sidebar 最底部。 */}
                {bottomItems
                  .filter((item) => item.group === "workspace")
                  .map((item) => {
                    const Icon = BOTTOM_ICON_MAP[item.icon];
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
                          <span className="ml-auto flex items-center gap-1 text-[10px] font-medium text-ink-meta">
                            <span
                              className={cn(
                                "h-1.5 w-1.5 rounded-full animate-pulse",
                                statusDotClass("running"),
                              )}
                            />
                            {item.badgeCount}
                          </span>
                        ) : null}
                      </SidebarLink>
                    );
                  })}
                {sidebarExtraItems}

                {/* RECENTS — rendered as Activity's sub-items: no group
                    label, no row icons, title indented to line up with
                    Activity's label (pl-[33px] matches the workspace-link
                    icon column). Color steps down to ``ink-meta`` so the
                    rows read as secondary to the top-level nav. Folded
                    shows 3; a tail chevron toggles to 8. Live runs get a
                    small brand-tinted pulsing dot on the right. */}
                {visibleRecents.length > 0 && (
                  // ``group/recents`` lets the tail toggle reveal on hover
                  // anywhere within the recents block — over the rows or
                  // the trailing white space. Default-hidden keeps the
                  // sidebar visually quiet when the user isn't pointing
                  // at this region.
                  <div className="group/recents">
                    {visibleRecents.map((item, idx) => {
                      const active = isActivePath(activePath, item.href);
                      const showTailToggle =
                        canExpandRecents && idx === visibleRecents.length - 1;
                      const isRenamingThis = recentRenamingId === item.id;
                      // Inline rename mode: the row swaps to a plain
                      // input wrapper (no LinkComponent) so the typing
                      // session is never interrupted by the row's own
                      // navigation. Same pattern as project rename.
                      if (isRenamingThis) {
                        return (
                          <div
                            key={`recent-${item.id}`}
                            className="relative mx-1 flex items-center gap-2 py-[5px] pl-[33px] pr-[10px]"
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
                      // Chat rows get a ``...`` menu (Rename + Delete);
                      // tasks don't (no rename / no delete endpoint, see
                      // openapi.yaml). The menu is hover-revealed via
                      // ``group-hover`` and stays open while the Radix
                      // dropdown is showing (``data-state=open``).
                      const showRowMenu =
                        item.kind === "chat" &&
                        (onRecentRename || onRecentDelete);
                      return (
                        <LinkComponent
                          key={`recent-${item.id}`}
                          to={item.href}
                          className={cn(
                            "group/recent-row relative mx-1 flex cursor-default items-center gap-2 rounded-[7px] py-[5px] pl-[33px] pr-[10px] text-[12.5px] outline-none transition-colors duration-[120ms] hover:bg-surface-soft hover:text-ink-heading focus-visible:outline-none focus-visible:shadow-[0_6px_16px_rgba(17,24,39,0.12)]",
                            active
                              ? "bg-card text-ink-heading shadow-[0_6px_16px_rgba(17,24,39,0.12)] dark:bg-surface-muted dark:shadow-none"
                              : "text-ink-meta",
                          )}
                        >
                          {item.isRunning && (
                            // Absolutely-positioned dot in the row's
                            // "icon column" — visually aligned with the
                            // Activity nav icon above. Out of flow so the
                            // title's pl-[33px] anchor stays stable
                            // whether the run is live or not (no horizontal
                            // shift when isRunning toggles).
                            <span
                              aria-label={t("sidebar.runningIndicator")}
                              className="pointer-events-none absolute left-[14px] top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-brand animate-pulse"
                            />
                          )}
                          <span className="min-w-0 flex-1 truncate">
                            {item.title}
                          </span>
                          {showRowMenu && (
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <button
                                  type="button"
                                  // Suppress LinkComponent navigation
                                  // when opening the menu (Radix opens on
                                  // pointerdown, so block both).
                                  onClick={(e) => e.stopPropagation()}
                                  onPointerDown={(e) => e.stopPropagation()}
                                  aria-label={t("sidebar.moreActions")}
                                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-ink-muted opacity-0 transition-opacity hover:bg-surface-muted hover:text-ink-body focus-visible:opacity-100 group-hover/recent-row:opacity-100 data-[state=open]:opacity-100"
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
                                    onSelect={() =>
                                      setRecentRenamingId(item.id)
                                    }
                                  >
                                    <FilePenLine />
                                    {t("sidebar.rename")}
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
                          {showTailToggle && (
                            <button
                              type="button"
                              aria-label={
                                recentsExpanded
                                  ? t("sidebar.showLess")
                                  : t("sidebar.showMore")
                              }
                              onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                setRecentsExpanded((v) => !v);
                              }}
                              className="flex h-4 w-4 shrink-0 items-center justify-center rounded text-ink-muted opacity-0 transition-opacity hover:text-ink-body focus-visible:opacity-100 group-hover/recents:opacity-100"
                            >
                              <ChevronDown
                                className={cn(
                                  "h-3 w-3 transition-transform duration-[150ms]",
                                  recentsExpanded && "rotate-180",
                                )}
                                strokeWidth={2}
                              />
                            </button>
                          )}
                        </LinkComponent>
                      );
                    })}
                  </div>
                )}

                <SectionLabel
                  open={projectsSectionOpen}
                  onToggle={() => setProjectsSectionOpen((v) => !v)}
                  action={
                    <button
                      type="button"
                      className="flex h-6 w-6 items-center justify-center rounded-md text-ink-body transition-colors hover:bg-surface-muted"
                      onClick={onAddProject}
                    >
                      <Plus className="h-3 w-3" />
                    </button>
                  }
                >
                  {t("sidebar.projects")}
                </SectionLabel>
                {!projectsSectionOpen ? null : projectGroups.length === 0 ? (
                  <div className="mx-1 px-[10px] py-[7px] text-2xs text-ink-meta">
                    {t("sidebar.noProjects")}
                  </div>
                ) : (
                  projectGroups.map((project) => (
                    <ProjectRow
                      key={project.id}
                      project={project}
                      activePath={activePath}
                      LinkComponent={LinkComponent}
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
                      onProjectRenameCancel={() => setProjectRenamingId(null)}
                      onProjectOpenInFinder={onProjectOpenInFinder}
                      onProjectRemove={onProjectRemove}
                    />
                  ))
                )}
              </div>
            </nav>

            {/* Mascot — anchored absolutely at the bottom area
                (above the settings link), z-0 so the scrollable nav
                sits above it. When the session list is short, the
                empty space at the bottom of the nav reveals the
                mascot. When the user expands the recents and the
                list gets long, the nav content scrolls over the
                mascot — line-drawing bleeds through behind the
                links so it's still felt without obscuring text. */}
            {mascotSrc ? (
              <img
                src={mascotSrc}
                alt=""
                aria-hidden="true"
                className="pointer-events-none absolute bottom-[64px] left-1/2 z-0 h-[170px] w-auto -translate-x-1/2 select-none opacity-60"
              />
            ) : null}

            {/* Bottom-pinned: Library + Settings. Library (Agents / Skills /
                Connectors / Knowledge) sits right above Settings so resource
                management groups with app config; the scrollable nav above
                stays focused on workspace verbs + projects. ``relative z-10``
                so links sit in front of the absolute-positioned mascot. */}
            <div className="relative z-10 flex flex-col gap-0.5 px-3 pb-4 pt-2">
              {libraryItems.length > 0 && (
                <>
                  <div className="pb-1 pl-[14px] pr-3 pt-1">
                    <span className="text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
                      {t("sidebar.library")}
                    </span>
                  </div>
                  {libraryItems.map((item) => {
                    const Icon = BOTTOM_ICON_MAP[item.icon];
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
                  const Icon = BOTTOM_ICON_MAP[item.icon];
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
