import { useMemo, useState } from "react";
import {
  Bot,
  CheckCircle2,
  Circle,
  Expand,
  FolderOpen,
  ListTodo,
  Loader2,
  PauseCircle,
  Users,
  X,
  XCircle,
} from "lucide-react";
import {
  RUNTIME_DISPLAY_NAME,
  useTranslation,
  type MemberWithAgent,
  type TaskRun,
} from "@valuz/core";
import { modelLabel } from "@valuz/shared";
import {
  AccordionSection,
  FileRefreshButton,
  ProjectFileTree,
  Sheet,
  SheetContent,
  SheetDescription,
  SheetTitle,
  SheetTrigger,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  type FileTreeNode,
} from "@valuz/ui";
// File count — same recursive sum ProjectContextPanel uses, so the
// header "N files" matches what users see on the project home rail.
const countFiles = (nodes: FileTreeNode[]): number =>
  nodes.reduce(
    (sum, n) => sum + (n.type === "file" ? 1 : countFiles(n.children ?? [])),
    0,
  );

/** One planned subtask node, as surfaced by the backend ``task_plan_update``
 *  event payload (VALUZ-TASK). ``status`` is the panel projection of the
 *  internal lifecycle (planned→pending / in_progress·in_review·rework→active
 *  / done→completed / failed→failed / paused→paused — a user pause/stop parks
 *  the running node as paused, shown non-spinning). */
export interface PlannedSubtask {
  key?: string;
  label: string;
  agent?: string;
  status: "completed" | "failed" | "active" | "pending" | "paused";
  depends_on?: string[];
  parallel_group?: string | null;
  /** Full subtask brief the lead handed to the dispatched agent. Shown in
   *  the plan review popover; absent on older events that pre-date the
   *  field. */
  goal?: string;
  /** Retry counter (>= 1; 1 = first attempt, 2+ = rework). Popover
   *  surfaces "第 N 次尝试" when > 1. */
  attempts?: number;
  /** Acceptance criteria the lead set at plan time. */
  review_criteria?: string | null;
  /** Lead's feedback on the last rework decision. Popover shows this in
   *  red when the subtask was bounced back. */
  review_feedback?: string | null;
}

export interface TaskContextPanelProps {
  /** All runs in the task (lead + sub-Runs). Lead is identified by
   *  ``run.kind === "lead"`` so callers don't pass it separately. */
  runs: TaskRun[];
  /** Workspace members — used to surface ``agent.model`` next to each
   *  team row. Optional: if absent, rows fall back to run status only. */
  members?: MemberWithAgent[];
  /** The lead's subtask plan (from the latest ``task_plan_update`` event).
   *  Empty until the lead calls ``plan_task``. */
  plannedSubtasks?: PlannedSubtask[];
  /** Project file tree for the workspace this task runs in. When
   *  provided, the panel switches into a tabbed shell with a "项目文件"
   *  tab alongside the context sections. Absent → tabs hide and we
   *  fall back to the legacy single-pane layout. */
  fileTree?: FileTreeNode[];
  /** Absolute project root (cwd) — shown above the file tree so the
   *  user can see which directory the files belong to. */
  rootPath?: string;
  /** Manual file-tree refresh action — the same throttled spinner the
   *  project home rail uses (see ``FileRefreshButton``). Optional;
   *  hides the button when absent. */
  onRefreshFiles?: () => void;
  /** Reveal the workspace cwd in the OS file manager. Optional; hides
   *  the button when absent. */
  onOpenInFinder?: () => void;
}

/**
 * Right-rail context panel for the Task detail page.
 *
 * v30 design (PRD-PAAT §3.5 — three sections only):
 *   1. 👥 Agent Team — lead + dispatched sub-agents with status
 *   2. 📋 待办清单    — lead's self-decomposed plan
 *   3. 🏃 运行记录    — lead Run + sub-Runs, click → conversation
 *
 * Sits inside AppShell's right-panel slot via
 * ``useWorkspaceOutlet().setRightPanel(<TaskContextPanel … />)`` so it
 * inherits the standard ``rounded-[14px] border bg-card`` card shell and
 * the collapse toggle the rest of the workspace pages use.
 *
 * Deliberately removed in v30 (per PRD §3.5 v30 changelog):
 *   - Live stream views (Lead / Subtask)  — center timeline carries the
 *     same event feed; live trace lives in the lead conversation page.
 *   - Uploaded files / Generated files     — referenced via Goal card +
 *     center 交付结果 card respectively.
 *   - Activity / Archived sections        — low-frequency; reachable via
 *     filters on the global task list.
 */
export const TaskContextPanel = ({
  runs,
  members,
  plannedSubtasks = [],
  fileTree,
  rootPath,
  onRefreshFiles,
  onOpenInFinder,
}: TaskContextPanelProps) => {
  const { t } = useTranslation();
  const [planReviewOpen, setPlanReviewOpen] = useState(false);

  // slug → AgentSummary, so each team row can show the bound model
  // without an extra API roundtrip.
  const agentBySlug = useMemo(() => {
    const map = new Map<string, NonNullable<MemberWithAgent["agent"]>>();
    for (const m of members ?? []) {
      if (m.agent) map.set(m.member.agent_slug, m.agent);
    }
    return map;
  }, [members]);

  // Subtask key → { index, label }, so dependency chips render as
  // ``依赖 #1`` (with the dependee's title on hover) instead of the
  // opaque kernel key string.
  const subtaskByKey = useMemo(() => {
    const map = new Map<string, { idx: number; label: string }>();
    plannedSubtasks.forEach((task, idx) => {
      if (task.key) map.set(task.key, { idx: idx + 1, label: task.label });
    });
    return map;
  }, [plannedSubtasks]);

  // Team = project member roster (every agent this project has
  // configured + available to the lead). Not "agents actually
  // dispatched on this task" — users want to see the full bench
  // upfront, including those still standing by.
  //
  // Lead is identified by joining ``runs`` (kind === "lead") to find
  // the agent_slug, then pinning that row to the top.
  const team = useMemo(() => {
    const leadSlug = runs.find((r) => r.kind === "lead")?.agent_slug ?? null;
    const items = (members ?? []).map((m) => ({
      slug: m.member.agent_slug,
      isLead: m.member.agent_slug === leadSlug,
    }));
    items.sort((a, b) => (a.isLead === b.isLead ? 0 : a.isLead ? -1 : 1));
    return items;
  }, [members, runs]);

  const showFilesTab = fileTree !== undefined;

  // The 3 accordion sections (Team / Todo / Runs) — built from the
  // shared ``AccordionSection`` so this right rail looks identical to
  // the one on the project home / conversation page (same chrome,
  // chevron, count semantics).
  const contextSections = (
    <div className="flex flex-col">
      {/* 👥 Agent Team — lead pinned to the top, dispatched sub-agents follow. */}
      <AccordionSection
        title={t("task.panel.team" as Parameters<typeof t>[0])}
        icon={Users}
        count={team.length || undefined}
        defaultOpen
        contentClassName="p-0"
      >
        {team.length === 0 ? (
          <div className="px-3 py-3 text-2xs text-ink-meta">
            {t("task.panel.teamEmpty" as Parameters<typeof t>[0])}
          </div>
        ) : (
          <ul className="py-3">
            {team.map(({ slug, isLead }) => {
              const agent = agentBySlug.get(slug);
              const modelText = agent?.model
                ? modelLabel(agent.model)
                : t("task.panel.modelUnknown" as Parameters<typeof t>[0]);
              const runtimeLabel = agent?.runtime_provider
                ? (RUNTIME_DISPLAY_NAME[
                    agent.runtime_provider as keyof typeof RUNTIME_DISPLAY_NAME
                  ] ?? agent.runtime_provider)
                : null;
              return (
                <li
                  key={slug}
                  className="relative flex items-center gap-2.5 px-3 py-2.5 before:absolute before:left-3 before:right-3 before:top-0 before:hidden before:h-px before:bg-[#f7f8fa] first:before:hidden [&:not(:first-child)]:before:block"
                >
                  <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-brand/8 text-brand">
                    <Bot className="h-3 w-3" />
                  </div>
                  <div className="flex min-w-0 flex-1 flex-col">
                    <div className="flex items-center gap-1.5">
                      <span className="truncate text-xs font-medium text-ink-heading">
                        {/* Display name first; kernel slug is only a
                            fallback. Stays in sync with renames done
                            from the project view. */}
                        {agent?.name ?? slug}
                      </span>
                      {isLead && (
                        <span className="rounded-sm bg-[#f3f2ff] px-1 py-0 text-[10px] font-normal text-[#725cf9]">
                          {t("task.runLead")}
                        </span>
                      )}
                    </div>
                    <span className="truncate text-2xs text-ink-meta">
                      {runtimeLabel ? `${runtimeLabel} · ` : ""}
                      {modelText}
                    </span>
                  </div>
                  {/* No trailing status icon: Team is a role roster,
                      not an execution view. Per-run status lives in
                      the center timeline + task list. */}
                </li>
              );
            })}
          </ul>
        )}
      </AccordionSection>

      {/* 📋 待办清单 — real plan from the latest ``task_plan_update`` event
          (VALUZ-TASK). Each node shows its status chip + dependency hints so
          the lead's DAG (parallel / blocked / done) is visible. */}
      <AccordionSection
        title={t("task.panel.todo" as Parameters<typeof t>[0])}
        icon={ListTodo}
        count={plannedSubtasks.length || undefined}
        defaultOpen
        contentClassName="p-0"
        action={
          plannedSubtasks.length > 0 ? (
            <Sheet open={planReviewOpen} onOpenChange={setPlanReviewOpen}>
              <SheetTrigger asChild>
                <button
                  type="button"
                  onClick={(e) => e.stopPropagation()}
                  className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-border"
                  title={t(
                    "task.panel.planReviewTrigger" as Parameters<typeof t>[0],
                  )}
                >
                  <Expand className="h-3.5 w-3.5" />
                </button>
              </SheetTrigger>
              <SheetContent
                side="right"
                showCloseButton={false}
                className="!bottom-5 left-auto !right-5 !top-5 !h-auto w-[600px] max-w-[calc(100vw-48px)] gap-0 overflow-hidden rounded-xl border-l-0 bg-popover p-0 shadow-xl data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right sm:max-w-none"
              >
                <SheetTitle className="sr-only">
                  {t("task.panel.planReviewTitle" as Parameters<typeof t>[0])}
                </SheetTitle>
                <SheetDescription className="sr-only">
                  {t(
                    "task.panel.planReviewSubtitle" as Parameters<typeof t>[0],
                    {
                      count: plannedSubtasks.length,
                    },
                  )}
                </SheetDescription>
                <PlanReviewPopover
                  subtasks={plannedSubtasks}
                  agentBySlug={agentBySlug}
                  subtaskByKey={subtaskByKey}
                  onClose={() => setPlanReviewOpen(false)}
                  t={t}
                />
              </SheetContent>
            </Sheet>
          ) : undefined
        }
      >
        {plannedSubtasks.length === 0 ? (
          <p className="px-3 py-3 text-2xs text-ink-meta">
            {t("task.panel.todoEmpty" as Parameters<typeof t>[0])}
          </p>
        ) : (
          <ol className="py-3">
            {plannedSubtasks.map((task, idx) => (
              <li
                key={task.key ?? task.label}
                className="relative flex items-start gap-2.5 px-3 py-3 before:absolute before:left-3 before:right-3 before:top-0 before:hidden before:h-px before:bg-[#f7f8fa] first:before:hidden [&:not(:first-child)]:before:block"
              >
                <span className="font-mono text-2xs leading-5 text-ink-meta">
                  #{idx + 1}
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <span className="truncate text-xs font-medium text-ink-heading">
                    {task.label}
                  </span>
                  <div className="flex flex-wrap items-center gap-1.5">
                    {task.agent && (
                      <span className="text-2xs text-ink-meta">
                        {/* Same slug→display-name join as Team rows:
                            "agent-2" reads as "高级前端架构型原型工程师2"
                            so plan items and team roster speak the
                            same language. */}
                        {agentBySlug.get(task.agent)?.name ?? task.agent}
                      </span>
                    )}
                    {task.depends_on && task.depends_on.length > 0 && (
                      <span
                        className="rounded bg-surface-soft px-1 py-0.5 text-2xs text-ink-meta"
                        title={task.depends_on
                          .map((k) => subtaskByKey.get(k)?.label ?? k)
                          .join("\n")}
                      >
                        {t("task.panel.dependsOn" as Parameters<typeof t>[0])}{" "}
                        {task.depends_on
                          .map((k) => {
                            const dep = subtaskByKey.get(k);
                            return dep ? `#${dep.idx}` : k;
                          })
                          .join(", ")}
                      </span>
                    )}
                    <SubtaskStatusChip status={task.status} />
                  </div>
                </div>
                <StatusIcon status={task.status} />
              </li>
            ))}
          </ol>
        )}
      </AccordionSection>

      {/* 运行记录 (sub-Runs list) deliberately removed: the central
          activity timeline already nests ``subtask_spawned`` +
          ``subtask_completed`` per run with status + click-through, so
          a separate sequenced list was pure duplication. The right
          rail is now the *role/plan* view (Team + Plan), the center
          is the *execution* view (Timeline). One concept per surface. */}
    </div>
  );

  if (!showFilesTab) {
    // Single-pane shell — caller didn't wire a file tree. Same outer
    // chrome as ProjectContextPanel (``bg-surface-base``, ``px-2``)
    // so the rail visual matches across pages.
    return (
      <div className="flex h-full min-h-0 flex-col bg-surface-base">
        <div className="min-h-0 flex-1 overflow-y-auto px-2 pt-0">
          {contextSections}
        </div>
      </div>
    );
  }

  // Tabbed shell — mirrors ProjectContextPanel's structure: 12px-tall
  // header + ``line`` variant tabs, content area padded to ``px-2``,
  // outer wrapper on ``bg-surface-base``. Keeping all three values
  // identical is what makes the two right rails feel like the same
  // surface across project home / conversation / task pages.
  return (
    <Tabs
      defaultValue="context"
      className="flex h-full min-h-0 flex-col gap-0 bg-surface-base"
    >
      <header className="flex h-12 shrink-0 items-end px-3">
        <TabsList
          variant="line"
          className="group-data-[orientation=horizontal]/tabs:h-full justify-start gap-1 border-0 p-0 [&_[data-slot=tabs-trigger]]:pt-2.5"
        >
          <TabsTrigger value="context" className="after:!opacity-0">
            {/* Match the project home / conversation rail label
                ("工作区" / "Workspace") — same surface, same word. */}
            {t("conversation.workspace" as Parameters<typeof t>[0])}
          </TabsTrigger>
          <TabsTrigger value="files" className="after:!opacity-0">
            {/* Reuse the project home / conversation key so the rail
                label stays in sync across surfaces. */}
            {t("project.projectFiles" as Parameters<typeof t>[0])}
          </TabsTrigger>
        </TabsList>
      </header>
      <TabsContent
        value="context"
        className="min-h-0 overflow-y-auto px-2 pt-0"
      >
        {contextSections}
      </TabsContent>
      <TabsContent value="files" className="min-h-0 overflow-hidden">
        {/* Pixel-mirror of ProjectContextPanel's ``fileTreePanel`` —
            ``bg-surface`` plate (no card chrome), ``px-3.5`` root row
            with file count, tree padded to ``px-3.5 pb-2``, every
            folder starts collapsed (``defaultOpenDepth={0}``). Keeps
            the right rail identical across project / task pages. */}
        <div className="flex h-full min-h-0 flex-col bg-surface">
          <div className="flex shrink-0 items-center gap-2 px-3.5 py-[1px]">
            <FolderOpen className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
            <span
              className="min-w-0 flex-1 truncate text-xs text-ink-label"
              title={rootPath ?? ""}
            >
              {rootPath ?? ""}
            </span>
            {fileTree.length > 0 && (
              <span className="shrink-0 text-xs text-ink-body">
                {countFiles(fileTree)}
              </span>
            )}
            {/* Same refresh + reveal-in-finder pair the project home
                rail shows. Throttled spinner button matches that page;
                reveal button uses the same ``open_in_finder`` IPC. */}
            {(onRefreshFiles || onOpenInFinder) && (
              <div className="flex items-center gap-1.5">
                {onRefreshFiles && (
                  <FileRefreshButton onClick={onRefreshFiles} />
                )}
                {onOpenInFinder && (
                  <button
                    type="button"
                    onClick={onOpenInFinder}
                    className="flex h-6 w-6 items-center justify-center rounded text-ink-body transition-colors hover:bg-surface-border"
                    title={t("project.inFinder" as Parameters<typeof t>[0])}
                  >
                    <FolderOpen className="h-3.5 w-3.5" />
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-3.5 pb-2">
            {fileTree.length === 0 ? (
              <p className="px-0 py-2 text-2xs text-ink-meta">
                {t("task.panel.filesEmpty" as Parameters<typeof t>[0])}
              </p>
            ) : (
              <ProjectFileTree
                rootPath={rootPath ?? ""}
                tree={fileTree}
                defaultOpenDepth={0}
                hideRootRow
              />
            )}
          </div>
        </div>
      </TabsContent>
    </Tabs>
  );
};

function StatusIcon({ status }: { status: string }) {
  if (status === "completed") {
    return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500" />;
  }
  if (status === "failed" || status === "stopped") {
    return <XCircle className="h-3.5 w-3.5 text-red-500" />;
  }
  if (status === "paused") {
    return <PauseCircle className="h-3.5 w-3.5 text-ink-meta" />;
  }
  if (status === "active") {
    return <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" />;
  }
  return <Circle className="h-3 w-3 text-ink-meta" />;
}

/** Status chip for a planned subtask — color-coded so the lead's plan reads
 *  at a glance (pending / running / done / failed). */
function SubtaskStatusChip({ status }: { status: string }) {
  const { t } = useTranslation();
  const map: Record<string, { cls: string; key: string }> = {
    completed: {
      cls: "bg-emerald-500/10 text-emerald-600",
      key: "task.subtaskStatus.completed",
    },
    failed: {
      cls: "bg-red-500/10 text-red-600",
      key: "task.subtaskStatus.failed",
    },
    active: { cls: "bg-brand/10 text-brand", key: "task.subtaskStatus.active" },
    pending: {
      cls: "bg-surface-soft text-ink-meta",
      key: "task.subtaskStatus.pending",
    },
    paused: {
      cls: "bg-amber-500/10 text-amber-600",
      key: "task.subtaskStatus.paused",
    },
  };
  const m = map[status] ?? map.pending;
  if (status === "active" || status === "completed") return null;
  return (
    <span
      className={`rounded-full px-1.5 py-0.5 text-2xs font-medium ${m.cls}`}
    >
      {t(m.key as Parameters<typeof t>[0])}
    </span>
  );
}

/** Full-plan review popover content — one expanded card per subtask
 *  with all the fields the compact list omits: goal text, attempts,
 *  parallel group, review feedback. Triggered from the task list
 *  section's header [↗] button. */
function PlanReviewPopover({
  subtasks,
  agentBySlug,
  subtaskByKey,
  onClose,
  t,
}: {
  subtasks: PlannedSubtask[];
  agentBySlug: Map<string, NonNullable<MemberWithAgent["agent"]>>;
  subtaskByKey: Map<string, { idx: number; label: string }>;
  onClose: () => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <header className="sticky top-0 z-10 border-b border-[#f7f8fa] bg-card px-4 py-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <ListTodo className="h-3.5 w-3.5 text-ink-meta" />
              <h3 className="text-sm font-semibold text-ink-heading">
                {t("task.panel.planReviewTitle" as Parameters<typeof t>[0])}
              </h3>
            </div>
            <p className="mt-0.5 text-2xs text-ink-meta">
              {t("task.panel.planReviewSubtitle" as Parameters<typeof t>[0], {
                count: subtasks.length,
              })}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-ink-meta transition-colors hover:bg-surface-soft hover:text-ink-heading"
            aria-label={t("common.close" as Parameters<typeof t>[0])}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </header>
      <ol className="flex-1 overflow-y-auto">
        {subtasks.map((task, idx) => {
          const agentLabel = task.agent
            ? (agentBySlug.get(task.agent)?.name ?? task.agent)
            : null;
          const depLabel =
            task.depends_on && task.depends_on.length > 0
              ? task.depends_on
                  .map((k) => {
                    const dep = subtaskByKey.get(k);
                    return dep ? `#${dep.idx}` : k;
                  })
                  .join(", ")
              : null;
          return (
            <li
              key={task.key ?? task.label}
              className="flex flex-col gap-2 px-4 py-3"
            >
              <div className="flex items-start gap-2.5">
                <span className="mt-0.5 font-mono text-2xs text-ink-meta">
                  #{idx + 1}
                </span>
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium text-ink-heading">
                      {task.label}
                    </span>
                    <SubtaskStatusChip status={task.status} />
                    {task.attempts !== undefined && task.attempts > 1 && (
                      <span className="rounded bg-amber-100 px-1.5 py-0.5 text-2xs font-medium text-amber-700">
                        {t(
                          "task.panel.planRowAttempts" as Parameters<
                            typeof t
                          >[0],
                          { count: task.attempts },
                        )}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-ink-meta">
                    {agentLabel && (
                      <span>
                        {t(
                          "task.panel.planRowOwner" as Parameters<typeof t>[0],
                        )}
                        : {agentLabel}
                      </span>
                    )}
                    {depLabel && (
                      <span
                        title={(task.depends_on ?? [])
                          .map((k) => subtaskByKey.get(k)?.label ?? k)
                          .join("\n")}
                      >
                        {t("task.panel.planRowDeps" as Parameters<typeof t>[0])}
                        : {depLabel}
                      </span>
                    )}
                    {task.parallel_group && (
                      <span>
                        {t(
                          "task.panel.planRowParallel" as Parameters<
                            typeof t
                          >[0],
                        )}
                        : {task.parallel_group}
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="ml-7">
                {task.goal ? (
                  <p className="whitespace-pre-wrap rounded-md bg-surface-soft px-3 py-2 text-xs leading-5 text-ink-body">
                    {task.goal}
                  </p>
                ) : (
                  <p className="text-2xs italic text-ink-meta">
                    {t(
                      "task.panel.planRowGoalEmpty" as Parameters<typeof t>[0],
                    )}
                  </p>
                )}
                {task.review_criteria && (
                  <div className="mt-2 rounded-md border border-[#eae6ff] bg-[#f3f2ff] px-3 py-2">
                    <p className="text-2xs font-semibold text-[#725cf9]">
                      {t(
                        "task.panel.planRowCriteria" as Parameters<typeof t>[0],
                      )}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-ink-body">
                      {task.review_criteria}
                    </p>
                  </div>
                )}
                {task.review_feedback && (
                  <div className="mt-2 rounded-md border border-red-500/30 bg-red-50 px-3 py-2 dark:bg-red-500/10">
                    <p className="text-2xs font-semibold text-red-700 dark:text-red-400">
                      {t(
                        "task.panel.planRowFeedback" as Parameters<typeof t>[0],
                      )}
                    </p>
                    <p className="mt-1 whitespace-pre-wrap text-xs leading-5 text-ink-body">
                      {task.review_feedback}
                    </p>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
