/**
 * AutomationPage — global automation overview.
 *
 * Replaces the legacy ScheduledPage per ADR-021. Drives off the new
 * `automationsApi` (no model/runtime concept; agent-based) and opens the
 * new `CreateAutomationDialog` which exposes cron + interval triggers
 * and an agent picker instead of a model picker.
 *
 * Reuses `ScheduledTaskTable` + `ExecutionLog` UI by mapping the new
 * `AutomationItem` / `AutomationRunItem` shapes into the table's generic
 * row form — a future slice can swap to dedicated components that
 * surface interval / manual triggers more naturally.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Clock3, Plus } from "lucide-react";
import { toast } from "sonner";
import {
  Button,
  DeleteConfirmDialog,
  ExecutionLog,
  PageLoader,
  ScheduledTaskTable,
} from "@valuz/ui";
import type { ExecutionLogRow } from "@valuz/ui";
import {
  agentsApi,
  automationsApi,
  useTranslation,
  type Agent,
  type AutomationDetail,
  type AutomationGroup,
  type AutomationItem,
  type AutomationRunItem,
  type AutomationWorkspaceTarget,
  type MemberWithAgent,
  type ActionKind,
  type Trigger,
} from "@valuz/core";
import { useWorkspaceOutlet } from "@valuz/app/layout";
import {
  CreateAutomationDialog,
  type AutomationAgentChoice,
} from "@valuz/app/components";

type I18nKey = Parameters<ReturnType<typeof useTranslation>["t"]>[0];
const k = (key: string) => key as I18nKey;

// ── Run-row mapping helpers ──────────────────────────────────────

function runStatusToLogStatus(
  status: AutomationRunItem["status"],
): ExecutionLogRow["status"] {
  if (status === "success") return "ok";
  if (status === "failed") return "err";
  if (status === "queued" || status === "running") return "pending";
  return "skip";
}

function formatRunTime(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = secs % 60;
  return `${mins}m${remSecs > 0 ? `${remSecs}s` : ""}`;
}

// "just now" / "5m ago" / "3h ago" / "2d ago". Mirrors the formatting
// the legacy ScheduledPage used so the column reads identically — the
// design review baked these strings in.
function relativeTime(ms: number | null): string {
  if (ms == null) return "—";
  const diff = Date.now() - ms;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// Trigger column — original ScheduledTaskTable shows the cron expression
// in monospace next to the human-readable subtitle. The column is
// ``font-mono`` so we keep it locale-neutral: raw cron expression for
// cron, ``Ns`` for interval, and an em-dash for manual rows (which
// never fire on the tick; the human-readable subtitle below already
// says "Manual" for context).
function triggerColumn(item: AutomationItem): string {
  if (item.trigger.kind === "cron") return item.trigger.cron_expr;
  if (item.trigger.kind === "interval") return `${item.trigger.seconds}s`;
  return "—";
}

// Map AutomationItem → the generic shape `ScheduledTaskTable` expects.
// Field-by-field equivalence with the legacy `mapTasksForTable`:
//
//   name              → name
//   prompt (subtitle) → trigger_human_readable     (was: cron_human_readable)
//   trigger (mono)    → cron_expr / "every Ns"     (was: cron_expr)
//   triggerTimezone   → trigger.timezone (cron only)
//   last              → relativeTime(last_run_at)  (was: relativeTime)
//   status            → enabled→on / *→off
function automationToTableRow(item: AutomationItem) {
  return {
    id: item.automation_id,
    name: item.name,
    prompt: item.trigger_human_readable,
    trigger: triggerColumn(item),
    triggerTimezone:
      item.trigger.kind === "cron"
        ? (item.trigger.timezone ?? undefined)
        : undefined,
    last: relativeTime(item.last_run_at),
    status: (item.status === "enabled" ? "on" : "off") as "on" | "off",
  };
}

// "Last run 5m ago" badge on the right of the group header. Returns
// undefined when the whole group has never fired, in which case the
// table omits the badge entirely (same contract as the legacy page).
// Takes ``t`` so it can localize the ``cron.lastRunColumn`` prefix —
// the legacy page used that exact key, and reusing it keeps the badge
// reading identically across the rename.
function latestGroupRunLabel(
  items: AutomationItem[],
  t: ReturnType<typeof useTranslation>["t"],
): string | undefined {
  const latest = items
    .map((item) => item.last_run_at)
    .filter((value): value is number => value !== null)
    .sort((a, b) => b - a)[0];
  if (!latest) return undefined;
  return `${t("cron.lastRunColumn" as Parameters<typeof t>[0])} ${relativeTime(latest)}`;
}

export const AutomationPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { setHeader, setHeaderClassName, setContentInnerClassName } =
    useWorkspaceOutlet();

  const [loading, setLoading] = useState(true);
  const [groups, setGroups] = useState<AutomationGroup[]>([]);
  const [targets, setTargets] = useState<AutomationWorkspaceTarget[]>([]);
  const [libraryAgents, setLibraryAgents] = useState<Agent[]>([]);
  const [projectMembers, setProjectMembers] = useState<
    Record<string, MemberWithAgent[]>
  >({});
  // Aggregated recent runs across every automation, newest first. Keyed
  // off the run id so duplicates collapse cleanly on re-poll.
  const [recentRuns, setRecentRuns] = useState<
    Array<AutomationRunItem & { automation_name: string }>
  >([]);

  const [createOpen, setCreateOpen] = useState(false);
  const [selectedTargetId, setSelectedTargetId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AutomationItem | null>(null);
  // Edit-mode state: the AutomationDetail whose row was clicked, plus
  // the workspace it's bound to (drives the agent picker). When set,
  // the same dialog opens in edit mode and submits route to the update
  // API. ``null`` = create flow.
  const [editTarget, setEditTarget] = useState<{
    detail: AutomationDetail;
    workspaceKind: "chat" | "project";
    workspaceId: string;
  } | null>(null);
  // Per-group collapse state — mirrors the legacy ScheduledPage so users
  // can fold the per-workspace tables once they grow long. Persisted
  // only for the current page lifetime; the design didn't ask for cross-
  // session persistence.
  const [collapsedGroupIds, setCollapsedGroupIds] = useState<Set<string>>(
    new Set(),
  );

  const toggleGroupCollapsed = useCallback((workspaceId: string) => {
    setCollapsedGroupIds((prev) => {
      const next = new Set(prev);
      if (next.has(workspaceId)) next.delete(workspaceId);
      else next.add(workspaceId);
      return next;
    });
  }, []);

  // ── Data loading ─────────────────────────────────────────────────

  const loadAll = useCallback(async () => {
    try {
      const [groupsRes, targetsRes, agentsRes] = await Promise.all([
        automationsApi.listGroups(),
        automationsApi.listWorkspaceTargets(),
        agentsApi.listAgents(),
      ]);
      setGroups(groupsRes.groups);
      setTargets(targetsRes.targets);
      setLibraryAgents(agentsRes.agents);

      // Pre-load members per project target so the dialog switch is
      // instant. Failures per-workspace shouldn't blow up the page.
      const projectTargets = targetsRes.targets.filter(
        (target) => target.kind === "project" && target.workspace_id,
      );
      const memberPairs = await Promise.all(
        projectTargets.map(async (target) => {
          try {
            const res = await agentsApi.listMembers(target.workspace_id!);
            return [target.workspace_id!, res.agents] as const;
          } catch {
            return [target.workspace_id!, [] as MemberWithAgent[]] as const;
          }
        }),
      );
      setProjectMembers(Object.fromEntries(memberPairs));

      // Recent execution log — fan out N parallel `listRuns(id, 3)`
      // calls, merge + sort by triggered_at desc, cap to a screen-worth.
      // 3 per automation is enough for "what fired today" while keeping
      // the round-trip count proportional to user-perceived activity.
      const allAutomations = groupsRes.groups.flatMap(
        (group) => group.automations,
      );
      const runFetches = await Promise.all(
        allAutomations.map(async (automation) => {
          try {
            const res = await automationsApi.listRuns(
              automation.automation_id,
              3,
            );
            return res.runs.map((run) => ({
              ...run,
              automation_name: automation.name,
            }));
          } catch {
            return [] as Array<AutomationRunItem & { automation_name: string }>;
          }
        }),
      );
      const merged = runFetches
        .flat()
        .sort(
          (a, b) =>
            b.triggered_at - a.triggered_at,
        )
        .slice(0, 30);
      setRecentRuns(merged);
    } catch (error) {
      toast.error(t(k("automation.loadFailed"), { error: String(error) }));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // ── Derived state ───────────────────────────────────────────────

  const totalCount = groups.reduce(
    (acc, group) => acc + group.automations.length,
    0,
  );
  const enabledCount = groups.reduce(
    (acc, group) =>
      acc +
      group.automations.filter((automation) => automation.status === "enabled")
        .length,
    0,
  );
  const hasAutomations = totalCount > 0;

  const openCreate = useCallback(() => {
    setSelectedTargetId(targets[0]?.id ?? null);
    setCreateOpen(true);
  }, [targets]);

  // ── Header ──────────────────────────────────────────────────────

  const pageHeader = useMemo(
    () => (
      <div className="flex w-full items-center justify-between gap-4">
        <div className="flex min-w-0 flex-col justify-center">
          <span className="text-base font-semibold leading-5 text-ink-heading">
            {t(k("automation.title"))}
          </span>
          <span className="truncate text-xs leading-4 text-ink-body">
            {t(k("automation.subtitle"))}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <div className="hidden h-8 items-center gap-2 rounded-lg border border-surface-border bg-surface-soft px-3 text-xs md:flex">
            <span className="font-medium text-ink-heading">
              {t(
                k(
                  totalCount === 1
                    ? "automation.headerCount"
                    : "automation.headerCountPlural",
                ),
                { count: totalCount },
              )}
            </span>
            <span className="text-ink-meta">·</span>
            <span className="text-ink-meta">
              {t(k("automation.headerEnabled"), { count: enabledCount })}
            </span>
          </div>
          <Button
            variant="default"
            size="sm"
            className="shrink-0"
            onClick={openCreate}
          >
            <Plus className="h-3.5 w-3.5" />
            {hasAutomations
              ? t(k("automation.actionNew"))
              : t(k("automation.actionCreate"))}
          </Button>
        </div>
      </div>
    ),
    [totalCount, enabledCount, hasAutomations, openCreate, t],
  );

  useEffect(() => {
    setHeader(pageHeader);
    setHeaderClassName("h-auto px-5 py-5");
    setContentInnerClassName("p-0");
    return () => {
      setHeader(null);
      setHeaderClassName(undefined);
      setContentInnerClassName(undefined);
    };
  }, [pageHeader, setContentInnerClassName, setHeader, setHeaderClassName]);

  // ── Mutations ────────────────────────────────────────────────────

  const toggleAutomation = async (automationId: string) => {
    const automation = groups
      .flatMap((group) => group.automations)
      .find((item) => item.automation_id === automationId);
    if (!automation) return;
    try {
      if (automation.status === "enabled") {
        await automationsApi.pause(automationId);
        toast.success(
          t(k("automation.pauseSuccess"), { name: automation.name }),
        );
      } else {
        await automationsApi.resume(automationId);
        toast.success(
          t(k("automation.resumeSuccess"), { name: automation.name }),
        );
      }
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.toggleFailed"), { error: String(error) }));
    }
  };

  const runNow = async (automationId: string) => {
    try {
      await automationsApi.runNow(automationId);
      toast.success(t(k("automation.runQueued")));
      void loadAll();
    } catch (error) {
      toast.error(t(k("automation.runFailed"), { error: String(error) }));
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await automationsApi.delete(deleteTarget.automation_id);
      toast.success(
        t(k("automation.deleteSuccess"), { name: deleteTarget.name }),
      );
      setDeleteTarget(null);
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.deleteFailed"), { error: String(error) }));
    }
  };

  // ── Create / Edit dialog wiring ──────────────────────────────────

  const selectedTarget = targets.find(
    (target) => target.id === selectedTargetId,
  );
  // Cache of members per workspace, populated on-demand when an edit
  // dialog opens. Chat workspaces are lazy-created (one per automation),
  // so we can't pre-load them all upfront — we fetch when the user
  // actually clicks a row. Project members are pre-loaded in ``loadAll``
  // so the create flow stays instant.
  const [editWorkspaceMembers, setEditWorkspaceMembers] = useState<
    MemberWithAgent[] | null
  >(null);

  /**
   * Edit mode resolves candidates from the row's workspace members —
   * for BOTH chat and project workspaces. The chat case is the subtle
   * one: ``library_agent`` rows store the instantiated member slug
   * (something like ``qa-engineer-ab12cd34``), not the library agent's
   * own slug, so listing library agents would render an empty picker
   * (the stored slug doesn't match anything in that list).
   *
   * Create mode keeps the original split: library agents when the user
   * picks the Chat sentinel, members when they pick a project.
   */
  const agentChoices: AutomationAgentChoice[] = useMemo(() => {
    if (editTarget) {
      const members =
        editWorkspaceMembers ?? projectMembers[editTarget.workspaceId] ?? [];
      return members.map((entry) => ({
        slug: entry.member.agent_slug,
        name: entry.agent?.name ?? entry.member.agent_slug,
      }));
    }
    if (!selectedTarget || selectedTarget.kind === "chat") {
      return libraryAgents.map((agent) => ({
        slug: agent.slug,
        name: agent.name,
      }));
    }
    const members = projectMembers[selectedTarget.workspace_id ?? ""] ?? [];
    return members.map((entry) => ({
      slug: entry.member.agent_slug,
      name: entry.agent?.name ?? entry.member.agent_slug,
    }));
  }, [
    editTarget,
    editWorkspaceMembers,
    selectedTarget,
    libraryAgents,
    projectMembers,
  ]);

  /**
   * Open the edit dialog for a clicked automation row.
   *
   * Row mode → AutomationItem doesn't carry ``prompt_template`` (the
   * group listing is intentionally trimmed); we fetch the detail before
   * opening the dialog so all fields are pre-filled in one round trip.
   * Workspace kind comes from the group the row was clicked under so
   * the agent picker shows the right candidates without an extra lookup.
   */
  const openEditDialog = async (automationId: string) => {
    // Find the AutomationItem itself, not just the group it lives in.
    // The "Chat" virtual group's ``workspace_id`` is the sentinel
    // ``"chat"`` (a React-key, not a real workspace), while each
    // automation row inside carries the real lazy-created workspace
    // it's bound to. Resolving from the item ensures we hand a valid id
    // to ``agentsApi.listMembers``.
    let itemHit: AutomationItem | undefined;
    let kindHit: "chat" | "project" = "chat";
    for (const group of groups) {
      const found = group.automations.find(
        (item) => item.automation_id === automationId,
      );
      if (found) {
        itemHit = found;
        kindHit = group.workspace_kind;
        break;
      }
    }
    if (!itemHit) return;
    try {
      // Detail + members fetch in parallel: detail for prompt_template +
      // trigger, members so the agent picker has the right candidates
      // for this exact workspace (load-bearing for chat-kind
      // automations whose lazy-created workspaces aren't in the
      // pre-loaded ``projectMembers`` map).
      const [detail, membersRes] = await Promise.all([
        automationsApi.get(automationId),
        agentsApi.listMembers(itemHit.workspace_id).catch(() => ({
          agents: [] as MemberWithAgent[],
        })),
      ]);
      setEditWorkspaceMembers(membersRes.agents);
      setEditTarget({
        detail,
        workspaceKind: kindHit,
        workspaceId: itemHit.workspace_id,
      });
      setCreateOpen(true);
    } catch (error) {
      toast.error(t(k("automation.loadFailed"), { error: String(error) }));
    }
  };

  const handleDialogSubmit = async (data: {
    name: string;
    prompt_template: string;
    agent_slug: string;
    trigger: Trigger;
    action_kind: ActionKind;
  }) => {
    // Branch: edit (PATCH) vs create (POST) on the same callback so the
    // dialog stays stateless.
    if (editTarget) {
      try {
        await automationsApi.update(editTarget.detail.automation_id, {
          name: data.name,
          prompt_template: data.prompt_template,
          agent_slug: data.agent_slug,
          trigger: data.trigger,
          action_kind: data.action_kind,
        });
        toast.success(t(k("automation.updateSuccess"), { name: data.name }));
        await loadAll();
      } catch (error) {
        toast.error(t(k("automation.updateFailed"), { error: String(error) }));
        throw error;
      }
      return;
    }
    if (!selectedTarget) {
      toast.error(t(k("automation.pickWorkspaceFirst")));
      return;
    }
    try {
      await automationsApi.create({
        name: data.name,
        workspace_kind: selectedTarget.kind,
        workspace_id: selectedTarget.workspace_id,
        agent_kind:
          selectedTarget.kind === "chat" ? "library_agent" : "project_member",
        agent_slug: data.agent_slug,
        prompt_template: data.prompt_template,
        trigger: data.trigger,
        action_kind: data.action_kind,
      });
      toast.success(t(k("automation.createSuccess"), { name: data.name }));
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.createFailed"), { error: String(error) }));
      throw error;
    }
  };

  // Reset edit context whenever the dialog closes so the next "+ New"
  // click starts fresh in create mode.
  const handleDialogOpenChange = (open: boolean) => {
    if (!open) {
      setEditTarget(null);
      setEditWorkspaceMembers(null);
    }
    setCreateOpen(open);
  };

  // ── Execution log rows ───────────────────────────────────────────

  const executionRows: ExecutionLogRow[] = recentRuns.map((run) => ({
    id: run.run_id,
    time: formatRunTime(run.triggered_at),
    status: runStatusToLogStatus(run.status),
    duration: formatDuration(run.duration_ms),
    output:
      run.result_summary ??
      run.error_message ??
      (run.error_code ? `${run.error_code}` : ""),
    triggerType:
      // ExecutionLog now recognises all four trigger kinds the runner
      // emits (cron / interval / manual / recovered_skip). System-emitted
      // ``auto_paused_notice`` rows (ADR-012) and any unknown future
      // values fall through to undefined → no badge.
      run.trigger_type === "cron" ||
      run.trigger_type === "interval" ||
      run.trigger_type === "manual" ||
      run.trigger_type === "recovered_skip"
        ? run.trigger_type
        : undefined,
    taskName: run.automation_name,
    sessionId: run.session_id,
  }));

  // ── Render ──────────────────────────────────────────────────────

  if (loading) return <PageLoader />;

  return (
    <div className="relative h-full min-h-0 overflow-y-auto bg-card">
      <div className="flex min-h-full flex-col px-5 pb-5 pt-3">
        {!hasAutomations ? (
          <div className="flex flex-1 justify-center pt-[160px]">
            <div className="flex flex-col items-center px-5 text-center">
              <div className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-[#f7f8fa] text-[#444b54] dark:bg-surface-soft dark:text-ink-body">
                <Clock3 className="h-5 w-5" />
              </div>
              <div className="mt-3 text-sm font-medium text-ink-heading">
                {t(k("automation.emptyTitle"))}
              </div>
              <div className="mt-1 max-w-[460px] text-xs leading-5 text-ink-body">
                {t(k("automation.emptyDesc"))}
              </div>
              <Button
                className="mt-4"
                variant="default"
                size="sm"
                onClick={openCreate}
              >
                <Plus className="h-3 w-3" />
                {t(k("automation.emptyAction"))}
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="space-y-5">
              {groups
                .filter((group) => group.automations.length > 0)
                .map((group) => (
                  <section key={group.workspace_id}>
                    <ScheduledTaskTable
                      tasks={group.automations.map(automationToTableRow)}
                      title={group.workspace_name}
                      taskCountLabel={t(
                        k(
                          group.automations.length === 1
                            ? "automation.groupCount"
                            : "automation.groupCountPlural",
                        ),
                        { count: group.automations.length },
                      )}
                      lastRunLabel={latestGroupRunLabel(group.automations, t)}
                      collapsed={collapsedGroupIds.has(group.workspace_id)}
                      onToggleCollapse={() =>
                        toggleGroupCollapsed(group.workspace_id)
                      }
                      onRowClick={(id) => {
                        // Edit affordance: clicking a row opens the
                        // dialog in edit mode pre-filled with the row's
                        // values. Same code path as the create flow
                        // (PATCH vs POST decided by handleDialogSubmit).
                        void openEditDialog(id);
                      }}
                      onToggle={(id) => toggleAutomation(id)}
                      onRunNow={(id) => runNow(id)}
                      onDelete={(id) => {
                        const automation = group.automations.find(
                          (item) => item.automation_id === id,
                        );
                        if (automation) setDeleteTarget(automation);
                      }}
                    />
                  </section>
                ))}
            </div>

            <section className="mt-8">
              <div className="mb-3 border-b border-[#f7f8fa] pb-3 dark:border-surface-border">
                <div className="text-base font-semibold text-ink-heading">
                  {t(k("automation.recentExecutions"))}
                </div>
              </div>

              {executionRows.length > 0 ? (
                <ExecutionLog
                  rows={executionRows}
                  onSessionClick={(sessionId) =>
                    navigate(`/conversation/${sessionId}`)
                  }
                />
              ) : (
                <div className="flex flex-col items-center justify-center px-5 py-8 text-center">
                  <div className="flex h-11 w-11 items-center justify-center rounded-[14px] bg-[#f7f8fa] text-[#444b54] dark:bg-surface-soft dark:text-ink-body">
                    <Clock3 className="h-5 w-5" />
                  </div>
                  <p className="mt-3 text-sm font-medium text-ink-heading">
                    {t(k("automation.noExecutions"))}
                  </p>
                </div>
              )}
            </section>
          </>
        )}
      </div>

      <CreateAutomationDialog
        open={createOpen}
        onOpenChange={handleDialogOpenChange}
        onSubmit={handleDialogSubmit}
        agents={agentChoices}
        allowTaskMode={
          // Task mode only valid on project workspaces (backend rejects
          // ``task`` on chat). In edit mode the row's existing workspace
          // wins; in create mode the picked target decides.
          editTarget
            ? editTarget.workspaceKind === "project"
            : selectedTarget?.kind === "project"
        }
        initial={
          editTarget
            ? {
                name: editTarget.detail.name,
                prompt_template: editTarget.detail.prompt_template,
                agent_slug: editTarget.detail.agent_slug,
                trigger: editTarget.detail.trigger,
                action_kind:
                  (editTarget.detail.action_kind as ActionKind) ?? "chat",
              }
            : undefined
        }
        title={
          editTarget
            ? t(k("automation.dialogTitleEditNamed"), {
                name: editTarget.detail.name,
              })
            : selectedTarget?.kind === "chat"
              ? t(k("automation.dialogTitleChat"))
              : selectedTarget
                ? t(k("automation.dialogTitleProject"), {
                    workspace: selectedTarget.name,
                  })
                : t(k("automation.dialogTitleNew"))
        }
      />

      <DeleteConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
        title={t(k("automation.deleteTitle"), {
          name: deleteTarget?.name ?? "",
        })}
        description={t(k("automation.deleteConfirmDesc"))}
        confirmLabel={t(k("common.delete"))}
        onConfirm={confirmDelete}
      />
    </div>
  );
};
