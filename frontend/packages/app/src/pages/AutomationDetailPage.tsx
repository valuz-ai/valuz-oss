import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ChevronRight,
  Clock3,
  Eye,
  FilePenLine,
  ListChecks,
  MessageSquare,
  Pause,
  Play,
  Power,
  Trash2,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import {
  Button,
  DeleteConfirmDialog,
  EmptyState,
  PageLoader,
  StatusPill,
} from "@valuz/ui";
import {
  agentsApi,
  automationsApi,
  useEntityOrigin,
  useTranslation,
  type ActionKind,
  type AutomationDetail,
  type AutomationRunItem,
  type ExecutionContract,
  type InputContract,
  type MemberWithAgent,
  type ResultContract,
  type Trigger,
} from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";
import {
  CreateAutomationDialog,
  formatCreatedAt,
  type AutomationAgentChoice,
} from "@valuz/app/components";
import {
  describeRunStatus,
  describeTriggerType,
} from "../components/describe-trigger";
import { AutomationRunDetailPanel } from "./AutomationRunDetailPanel";
import { AutomationRunInputDialog } from "./AutomationRunInputDialog";

type I18nKey = Parameters<ReturnType<typeof useTranslation>["t"]>[0];
const k = (key: string) => key as I18nKey;

/** Runs the empty-run filter keeps even without a session — terminal
 *  non-success outcomes a code run can reach without ever spawning a
 *  session (agent conversation). */
const TERMINAL_NON_SUCCESS_STATUSES = new Set([
  "failed",
  "timeout",
  "cancelled",
]);

function executionSummary(
  execution: ExecutionContract,
  t: ReturnType<typeof useTranslation>["t"],
): string {
  if (execution.kind === "code") {
    return t(k("automation.executionBadgeCode"), {
      entry: execution.entry,
      runtime: execution.runtime,
    });
  }
  return execution.mode === "task"
    ? t(k("automation.executionBadgeAgentTask"))
    : t(k("automation.executionBadgeAgentChat"));
}

function inputKindLabel(
  input: InputContract,
  t: ReturnType<typeof useTranslation>["t"],
): string {
  if (input.kind === "text") return t(k("automation.inputKindText"));
  if (input.kind === "json") return t(k("automation.inputKindJson"));
  return t(k("automation.inputKindNone"));
}

function resultKindLabel(
  result: ResultContract,
  t: ReturnType<typeof useTranslation>["t"],
): string {
  return result.kind === "artifact"
    ? t(k("automation.resultKindArtifact"))
    : t(k("automation.resultKindConversation"));
}

// ── Helpers ─────────────────────────────────────────────────────────

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m${s % 60 > 0 ? `${s % 60}s` : ""}`;
}

// Time-bucket grouping — mirrors the activity (动态) history list so the two
// surfaces read identically.
type TimeBucket = "today" | "yesterday" | "thisWeek" | "earlier";

const bucketOf = (ms: number, now: Date): TimeBucket => {
  const d = new Date(ms);
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startYesterday = new Date(startToday);
  startYesterday.setDate(startYesterday.getDate() - 1);
  const startWeek = new Date(startToday);
  startWeek.setDate(startWeek.getDate() - 7);
  if (d >= startToday) return "today";
  if (d >= startYesterday) return "yesterday";
  if (d >= startWeek) return "thisWeek";
  return "earlier";
};

const BUCKET_ORDER: TimeBucket[] = [
  "today",
  "yesterday",
  "thisWeek",
  "earlier",
];
const BUCKET_KEY: Record<TimeBucket, string> = {
  today: "activity.today",
  yesterday: "activity.yesterday",
  thisWeek: "activity.thisWeek",
  earlier: "activity.earlier",
};

// ── Page ────────────────────────────────────────────────────────────

export const AutomationDetailPage = () => {
  const { automationId = "" } = useParams<{ automationId: string }>();
  // Cold deep-link recovery: on a cache miss this fires ensureOrigin, which
  // probes both backends and resolves the origin; adding it to loadAll's deps
  // re-fetches against the owning backend once it lands.
  const automationOrigin = useEntityOrigin(automationId, "automation");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const { t } = useTranslation();
  const { setHideHeader, setContentInnerClassName } = useProjectOutlet();

  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<AutomationDetail | null>(null);
  const [runs, setRuns] = useState<AutomationRunItem[]>([]);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [editMembers, setEditMembers] = useState<MemberWithAgent[] | null>(
    null,
  );
  const [runInputOpen, setRunInputOpen] = useState(false);
  const [runDetailId, setRunDetailId] = useState<string | null>(null);
  const [cancellingRunId, setCancellingRunId] = useState<string | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      const res = await automationsApi.listRuns(automationId, 50);
      setRuns(res.runs);
    } catch {
      /* silent poll */
    }
  }, [automationId]);

  const loadAll = useCallback(async () => {
    try {
      const [det, runsRes] = await Promise.all([
        automationsApi.get(automationId),
        automationsApi.listRuns(automationId, 50),
      ]);
      setDetail(det);
      setRuns(runsRes.runs);
      // Pre-load members for the edit dialog
      const membersRes = await agentsApi
        .listMembers(det.project_id)
        .catch(() => ({ agents: [] as MemberWithAgent[] }));
      setEditMembers(membersRes.agents);
    } catch (error) {
      toast.error(String(error));
    } finally {
      setLoading(false);
    }
  }, [automationId, automationOrigin]);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  // Poll runs every 5s for live task-status updates
  useEffect(() => {
    if (!detail) return;
    const id = setInterval(() => void refreshRuns(), 5000);
    return () => clearInterval(id);
  }, [detail, refreshRuns]);

  // ── Header ─────────────────────────────────────────────────────────

  useEffect(() => {
    setHideHeader(true);
    setContentInnerClassName("p-0");
    return () => {
      setHideHeader(false);
      setContentInnerClassName(undefined);
    };
  }, [setHideHeader, setContentInnerClassName]);

  // ── Mutations ───────────────────────────────────────────────────────

  const handleToggle = async () => {
    if (!detail) return;
    try {
      if (detail.status === "enabled") {
        await automationsApi.pause(automationId);
        toast.success(t(k("automation.pauseSuccess"), { name: detail.name }));
      } else {
        await automationsApi.resume(automationId);
        toast.success(t(k("automation.resumeSuccess"), { name: detail.name }));
      }
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.toggleFailed"), { error: String(error) }));
    }
  };

  const runNowWithInput = async (input?: string | Record<string, unknown>) => {
    if (!detail) return;
    setRunBusy(true);
    try {
      if (input !== undefined) {
        await automationsApi.runNow(automationId, { input });
      } else {
        await automationsApi.runNow(automationId);
      }
      toast.success(t(k("automation.runQueued")));
      setRunInputOpen(false);
      void refreshRuns();
    } catch (error) {
      toast.error(t(k("automation.runFailed"), { error: String(error) }));
    } finally {
      setRunBusy(false);
    }
  };

  const handleRunNow = async () => {
    if (!detail || detail.status !== "enabled" || runBusy) return;
    // ``none`` input runs immediately; ``text``/``json`` open the input
    // prompt so the run's input is collected before firing.
    if (detail.input.kind !== "none") {
      setRunInputOpen(true);
      return;
    }
    await runNowWithInput();
  };

  const handleCancelRun = async (runId: string) => {
    if (cancellingRunId) return;
    setCancellingRunId(runId);
    try {
      await automationsApi.cancelRun(automationId, runId);
      toast.success(t(k("automation.runCancelSuccess")));
      void refreshRuns();
    } catch (error) {
      toast.error(t(k("automation.runCancelFailed"), { error: String(error) }));
    } finally {
      setCancellingRunId(null);
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    try {
      await automationsApi.delete(automationId);
      toast.success(t(k("automation.deleteSuccess"), { name: detail.name }));
      navigate("/automations");
    } catch (error) {
      toast.error(t(k("automation.deleteFailed"), { error: String(error) }));
    }
  };

  const handleEditSubmit = async (data: {
    name: string;
    prompt_template: string;
    agent_slug: string | null;
    trigger: Trigger;
    action_kind: ActionKind;
    worktree: boolean;
    playbook_definition_id: string | null;
    playbook_version: number | null;
    event_source: string | null;
    event_refs: string[] | null;
    execution: ExecutionContract;
    input: InputContract;
    result: ResultContract;
  }) => {
    try {
      await automationsApi.update(automationId, data);
      toast.success(t(k("automation.updateSuccess"), { name: data.name }));
      await loadAll();
    } catch (error) {
      toast.error(t(k("automation.updateFailed"), { error: String(error) }));
      throw error;
    }
  };

  const agentChoices: AutomationAgentChoice[] = useMemo(
    () =>
      (editMembers ?? []).map((entry) => ({
        slug: entry.member.agent_slug,
        name: entry.agent?.name ?? entry.member.agent_slug,
      })),
    [editMembers],
  );

  // ── Render ─────────────────────────────────────────────────────────

  if (loading) return <PageLoader />;
  if (!detail) return null;

  // Back nav returns to the exact view the user came from — ``from`` carries
  // a full path (query included, so a workspace reopens on its own tab).
  // ``from=project`` is the older shorthand and still resolves; with nothing
  // to go on, the standalone list is the only sensible destination.
  const from = searchParams.get("from");
  const backTarget =
    from === "project"
      ? `/projects/${detail.project_id}`
      : (from ?? "/automations");
  const backLabel = t(k("common.back"));

  // Drop only the truly empty rows — interrupted-on-shutdown / recovered-skip
  // ticks that fired but produced nothing at all. A code run never has a
  // session, so it stays visible when it has an artifact, ran somewhere
  // (executor_ref), or reached a terminal non-success outcome worth seeing.
  const visibleRuns = runs.filter(
    (r) =>
      Boolean(r.session_id) ||
      r.has_artifact ||
      Boolean(r.executor_ref) ||
      TERMINAL_NON_SUCCESS_STATUSES.has(r.status),
  );

  const groupedRuns = (() => {
    const now = new Date();
    const groups = new Map<TimeBucket, typeof visibleRuns>();
    for (const r of visibleRuns) {
      const b = bucketOf(r.triggered_at, now);
      const list = groups.get(b) ?? [];
      list.push(r);
      groups.set(b, list);
    }
    return groups;
  })();

  const renderRunRow = (run: (typeof visibleRuns)[number]) => {
    const isTask = run.task_status !== null;
    const Icon = isTask ? ListChecks : MessageSquare;
    const eff = run.task_status ?? run.status;
    const { pillStatus, label: pillLabel } = describeRunStatus(eff, t);
    const canOpen = Boolean(run.task_id || run.session_id);
    const isCodeRun = detail.execution.kind === "code";
    const canCancel =
      isCodeRun && (run.status === "queued" || run.status === "running");
    return (
      <div
        key={run.run_id}
        className="group flex w-full items-center gap-2 rounded-xl px-3 py-3 text-left transition-colors hover:bg-surface-soft"
      >
        <button
          type="button"
          disabled={!canOpen}
          onClick={() => {
            if (run.task_id) navigate(`/tasks/${run.task_id}`);
            else if (run.session_id)
              navigate(`/conversation/${run.session_id}`);
          }}
          className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:cursor-default"
        >
          <Icon className="h-3 w-3 shrink-0 text-ink-meta" strokeWidth={2} />
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-ink-heading">
            {run.result_summary?.trim() || detail.name}
          </span>
        </button>
        <span className="shrink-0 text-2xs text-ink-meta">
          {describeTriggerType(run.trigger_type, t)}
          {" · "}
          {formatCreatedAt(run.triggered_at, t)}
          {run.duration_ms ? ` · ${formatDuration(run.duration_ms)}` : ""}
        </span>
        <StatusPill status={pillStatus} label={pillLabel} />
        {canCancel ? (
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={t(k("automation.runCancel"))}
            title={t(k("automation.runCancel"))}
            disabled={cancellingRunId === run.run_id}
            onClick={() => void handleCancelRun(run.run_id)}
          >
            <XCircle className="h-3.5 w-3.5" />
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={t(k("automation.runDetailTitle"))}
          title={t(k("automation.runDetailTitle"))}
          onClick={() => setRunDetailId(run.run_id)}
        >
          <Eye className="h-3.5 w-3.5" />
        </Button>
      </div>
    );
  };

  return (
    <div className="flex h-full flex-col overflow-hidden bg-card">
      {/* Breadcrumb nav — top-left, full width, mirrors task detail. */}
      <div className="flex min-w-0 shrink-0 items-center gap-2 px-5 pt-5 text-sm leading-5">
        <button
          type="button"
          onClick={() => navigate(backTarget)}
          className="inline-flex shrink-0 items-center gap-1 text-ink-meta transition-colors hover:text-ink-heading"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span>{backLabel}</span>
        </button>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-ink-muted" />
        <span className="min-w-0 truncate font-medium text-ink-heading">
          {t(k("automation.detailTitle"))}
        </span>
      </div>

      <div className="flex min-h-0 w-full flex-1 flex-col overflow-hidden px-5">
        {/* Title + actions section */}
        <div className="flex items-start justify-between pt-4 pb-5 shrink-0">
          <div>
            <h1 className="text-2xl font-semibold text-ink-heading">
              {detail.name}
            </h1>
            <p className="mt-1 flex flex-wrap items-center gap-2 text-sm text-ink-meta">
              <span>{detail.trigger_human_readable}</span>
              <span>·</span>
              <span>{executionSummary(detail.execution, t)}</span>
              {detail.execution.kind === "agent" ? (
                <>
                  <span>·</span>
                  <span>{detail.agent_name ?? "—"}</span>
                </>
              ) : null}
              {detail.playbook_definition_id && detail.playbook_version ? (
                <>
                  <span>·</span>
                  <span>Playbook v{detail.playbook_version}</span>
                </>
              ) : null}
              <span>·</span>
              <span>{inputKindLabel(detail.input, t)}</span>
              <span>·</span>
              <span>{resultKindLabel(detail.result, t)}</span>
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2 pt-1">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleToggle()}
            >
              {detail.status === "enabled" ? (
                <>
                  <Pause className="h-3.5 w-3.5" />
                  {t(k("cron.pause"))}
                </>
              ) : (
                <>
                  <Power className="h-3.5 w-3.5" />
                  {t(k("cron.enable"))}
                </>
              )}
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              aria-label={t(k("common.edit"))}
              onClick={() => setEditOpen(true)}
            >
              <FilePenLine className="h-4 w-4" />
            </Button>
            <Button
              variant="outline"
              size="icon-sm"
              className="text-destructive hover:text-destructive"
              aria-label={t(k("common.delete"))}
              onClick={() => setDeleteOpen(true)}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
            <Button
              size="sm"
              disabled={detail.status !== "enabled" || runBusy}
              onClick={() => void handleRunNow()}
            >
              <Play className="h-3.5 w-3.5" />
              {t(k("cron.runNow"))}
            </Button>
          </div>
        </div>

        {/* Two-column layout — each column scrolls independently */}
        <div className="grid min-h-0 flex-1 grid-cols-[minmax(0,1fr)_minmax(380px,32%)] overflow-hidden border-t border-surface-border/70">
          {/* Left: execution history */}
          <div className="flex min-w-0 flex-col overflow-hidden pt-5">
            <h2 className="mb-3 shrink-0 pr-8 text-sm font-medium text-ink-meta">
              {t(k("cron.executionHistory"))}
            </h2>
            {visibleRuns.length > 0 ? (
              <div className="min-h-0 flex-1 overflow-y-auto pr-8 pb-6">
                {BUCKET_ORDER.filter((b) => groupedRuns.has(b)).map((b) => (
                  <div key={b} className="mb-4">
                    <div className="mb-1.5 px-3 text-[11.5px] font-normal uppercase tracking-[0.06em] text-ink-body">
                      {t(k(BUCKET_KEY[b]))}
                    </div>
                    {(groupedRuns.get(b) ?? []).map((run) => renderRunRow(run))}
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex flex-1 justify-center py-8">
                <EmptyState
                  variant="plain"
                  title={t(k("automation.noExecutions"))}
                  icon={<Clock3 className="h-5 w-5" />}
                />
              </div>
            )}
          </div>

          {/* Right: instructions */}
          <div className="flex min-w-0 flex-col overflow-hidden border-l border-surface-border pl-6 pt-5 pb-6 text-sm">
            <h2 className="mb-3 shrink-0 text-sm font-medium text-ink-meta">
              {t(k("cron.instruction"))}
            </h2>
            <div className="min-h-0 flex-1 overflow-y-auto pr-1">
              <p className="whitespace-pre-wrap text-sm leading-6 text-ink-body">
                {detail.prompt_template}
              </p>
            </div>
          </div>
        </div>
      </div>

      <CreateAutomationDialog
        open={editOpen}
        onOpenChange={(open) => setEditOpen(open)}
        onSubmit={handleEditSubmit}
        agents={agentChoices}
        allowTaskMode={detail.project_kind === "project"}
        fixedTargetName={detail.project_name}
        fixedProjectId={detail.project_id}
        initial={{
          name: detail.name,
          prompt_template: detail.prompt_template,
          agent_slug: detail.agent_slug,
          trigger: detail.trigger,
          action_kind: (detail.action_kind as ActionKind) ?? "chat",
          worktree: detail.worktree ?? false,
          playbook_definition_id: detail.playbook_definition_id,
          playbook_version: detail.playbook_version,
          event_source: detail.event_source ?? null,
          event_refs: detail.event_refs ?? null,
          execution: detail.execution,
          input: detail.input,
          result: detail.result,
        }}
        title={t(k("automation.dialogTitleEditNamed"), { name: detail.name })}
      />

      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={(open) => setDeleteOpen(open)}
        title={t(k("automation.deleteTitle"), { name: detail.name })}
        description={t(k("automation.deleteConfirmDesc"))}
        confirmLabel={t(k("common.delete"))}
        onConfirm={handleDelete}
      />

      <AutomationRunInputDialog
        open={runInputOpen}
        onOpenChange={setRunInputOpen}
        inputContract={detail.input}
        busy={runBusy}
        onSubmit={(input) => runNowWithInput(input)}
      />

      <AutomationRunDetailPanel
        automationId={automationId}
        runId={runDetailId}
        open={runDetailId !== null}
        onOpenChange={(open) => {
          if (!open) setRunDetailId(null);
        }}
      />
    </div>
  );
};
