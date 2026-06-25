/**
 * AutomationDetailPage — a single automation's own page (/automations/:automationId).
 *
 * One screen for the automation's creator: title + attribution + run state,
 * the full instruction, schedule (next/last run), its OWN recent run history,
 * and every action (edit / run-now / pause-resume / delete). The global
 * `AutomationPage` keeps the cross-project overview; this page owns "view one".
 *
 * Run state reuses the shared `isAutomationRunning` / `runToLogStatus`
 * (`@valuz/core`) so detail / menu / Activity all decide it identically. The
 * page polls runs every 5s while visible (PRD ≤5s freshness) and re-fetches
 * once right after a mutation.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Clock3,
  FolderOpen,
  Pencil,
  Play,
  Pause,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  BackLink,
  Badge,
  Button,
  DeleteConfirmDialog,
  EmptyState,
  ExecutionLog,
  PageLoader,
  Spinner,
  StatusPill,
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@valuz/ui";
import type { ExecutionLogRow } from "@valuz/ui";
import {
  agentsApi,
  automationsApi,
  isAutomationRunning,
  runToLogStatus,
  useTranslation,
  type ActionKind,
  type AutomationDetail,
  type AutomationRunItem,
  type MemberWithAgent,
  type Trigger,
} from "@valuz/core";
import {
  CreateAutomationDialog,
  type AutomationAgentChoice,
} from "@valuz/app/components";

type I18nKey = Parameters<ReturnType<typeof useTranslation>["t"]>[0];
const k = (key: string) => key as I18nKey;

const POLL_MS = 5000;

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

export const AutomationDetailPage = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { automationId = "" } = useParams<{ automationId: string }>();

  const [detail, setDetail] = useState<AutomationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  const [runs, setRuns] = useState<AutomationRunItem[]>([]);
  const [runsLoading, setRunsLoading] = useState(true);
  const [runsError, setRunsError] = useState(false);

  const [members, setMembers] = useState<MemberWithAgent[]>([]);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [runningNow, setRunningNow] = useState(false);

  // ── Data loading ─────────────────────────────────────────────────

  const loadDetail = useCallback(async () => {
    try {
      const d = await automationsApi.get(automationId);
      setDetail(d);
      setNotFound(false);
    } catch {
      // owner-scoped backend returns 403/404 alike — show one "not found /
      // no access" state, no info leak about whether it exists.
      setNotFound(true);
    } finally {
      setDetailLoading(false);
    }
  }, [automationId]);

  const refreshRuns = useCallback(async () => {
    try {
      // Default limit (20) — PRD "最近 N=20 条", newest first.
      const res = await automationsApi.listRuns(automationId);
      setRuns(res.runs);
      setRunsError(false);
    } catch {
      setRunsError(true);
    } finally {
      setRunsLoading(false);
    }
  }, [automationId]);

  useEffect(() => {
    setDetailLoading(true);
    setRunsLoading(true);
    void loadDetail();
    void refreshRuns();
  }, [loadDetail, refreshRuns]);

  // Members of the bound project drive the edit dialog's agent picker. Fetched
  // once detail is known (chat automations bind a lazy-created project, so we
  // can only resolve members after the detail tells us the project_id).
  useEffect(() => {
    if (!detail) return;
    let cancelled = false;
    agentsApi
      .listMembers(detail.project_id)
      .then((res) => !cancelled && setMembers(res.agents))
      .catch(() => !cancelled && setMembers([]));
    return () => {
      cancelled = true;
    };
  }, [detail]);

  // Lightweight 5s poll while the tab is visible: re-pull runs AND detail so
  // status / next_run flips show within ≤5s. Paused while hidden (no flicker,
  // no waste). Refs keep the interval callback stable across re-renders.
  const refreshRef = useRef(refreshRuns);
  const detailRef = useRef(loadDetail);
  refreshRef.current = refreshRuns;
  detailRef.current = loadDetail;
  useEffect(() => {
    if (notFound) return;
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      void refreshRef.current();
      void detailRef.current();
    };
    const id = setInterval(tick, POLL_MS);
    return () => clearInterval(id);
  }, [notFound]);

  // ── Derived ──────────────────────────────────────────────────────

  const latestRun = runs[0] ?? null;
  const running = isAutomationRunning(latestRun);
  const isProject = detail?.project_kind === "project";
  const agentDeleted = detail != null && detail.agent_name === null;
  const canRunNow =
    detail != null && detail.status === "enabled" && !agentDeleted && !running;

  const agentChoices: AutomationAgentChoice[] = useMemo(
    () =>
      members.map((entry) => ({
        slug: entry.member.agent_slug,
        name: entry.agent?.name ?? entry.member.agent_slug,
      })),
    [members],
  );

  // ── Mutations ────────────────────────────────────────────────────

  const handleRunNow = async () => {
    if (!detail) return;
    setRunningNow(true);
    try {
      await automationsApi.runNow(detail.automation_id);
      toast.success(t(k("automation.runQueued")));
      await refreshRuns();
      await loadDetail();
    } catch (error) {
      toast.error(t(k("automation.runFailed"), { error: String(error) }));
    } finally {
      setRunningNow(false);
    }
  };

  const handleToggle = async () => {
    if (!detail) return;
    try {
      if (detail.status === "enabled") {
        await automationsApi.pause(detail.automation_id);
        toast.success(t(k("automation.pauseSuccess"), { name: detail.name }));
      } else {
        await automationsApi.resume(detail.automation_id);
        toast.success(t(k("automation.resumeSuccess"), { name: detail.name }));
      }
      await loadDetail();
    } catch (error) {
      toast.error(t(k("automation.toggleFailed"), { error: String(error) }));
    }
  };

  const handleDelete = async () => {
    if (!detail) return;
    try {
      await automationsApi.delete(detail.automation_id);
      toast.success(t(k("automation.deleteSuccess"), { name: detail.name }));
      setDeleteOpen(false);
      // Back to source: project automations → the project; global → overview.
      navigate(isProject ? `/projects/${detail.project_id}` : "/automations");
    } catch (error) {
      toast.error(t(k("automation.deleteFailed"), { error: String(error) }));
    }
  };

  const handleEditSubmit = async (data: {
    name: string;
    prompt_template: string;
    agent_slug: string;
    trigger: Trigger;
    action_kind: ActionKind;
  }) => {
    if (!detail) return;
    try {
      await automationsApi.update(detail.automation_id, {
        name: data.name,
        prompt_template: data.prompt_template,
        agent_slug: data.agent_slug,
        trigger: data.trigger,
        action_kind: data.action_kind,
      });
      toast.success(t(k("automation.updateSuccess"), { name: data.name }));
      await loadDetail();
      await refreshRuns();
    } catch (error) {
      toast.error(t(k("automation.updateFailed"), { error: String(error) }));
      throw error; // keep the dialog open on failure
    }
  };

  // ── Run rows ─────────────────────────────────────────────────────

  const executionRows: ExecutionLogRow[] = runs.map((run) => ({
    id: run.run_id,
    time: formatRunTime(run.triggered_at),
    status: runToLogStatus(run),
    duration: formatDuration(run.duration_ms),
    output:
      (run.error_message_key ? t(run.error_message_key as I18nKey) : null) ??
      run.result_summary ??
      (run.error_code ? `${run.error_code}` : ""),
    triggerType:
      run.trigger_type === "cron" ||
      run.trigger_type === "interval" ||
      run.trigger_type === "manual" ||
      run.trigger_type === "recovered_skip"
        ? run.trigger_type
        : undefined,
    sessionId: run.session_id,
  }));

  // ── Render ───────────────────────────────────────────────────────

  if (detailLoading) return <PageLoader />;

  if (notFound || !detail) {
    return (
      <div className="flex h-full flex-col bg-card">
        <div className="px-5 pt-4">
          <BackLink
            onClick={() => navigate("/automations")}
            label={t(k("automation.detailBack"))}
          />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <EmptyState
            variant="plain"
            title={t(k("automation.notFoundTitle"))}
            description={t(k("automation.notFoundDesc"))}
            icon={<Clock3 className="h-5 w-5" />}
          />
        </div>
      </div>
    );
  }

  const statusPill = running
    ? { status: "running", label: t(k("automation.statusRunning")) }
    : detail.status === "paused"
      ? { status: "paused", label: t(k("automation.statusPaused")) }
      : { status: "enabled", label: t(k("automation.statusEnabled")) };

  return (
    <div className="relative h-full min-h-0 overflow-y-auto bg-card">
      <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-6 px-5 pb-10 pt-4">
        {/* Back */}
        <BackLink
          onClick={() =>
            navigate(isProject ? `/projects/${detail.project_id}` : "/automations")
          }
          label={t(k("automation.detailBack"))}
        />

        {/* Header: title + attribution + status + actions */}
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="mr-1 break-words text-xl font-semibold text-ink-heading">
              {detail.name}
            </h1>
            <Badge variant={isProject ? "brand" : "secondary"}>
              {t(
                k(isProject ? "automation.badgeProject" : "automation.badgeGlobal"),
              )}
            </Badge>
            {isProject && (
              <span className="text-sm text-ink-meta">{detail.project_name}</span>
            )}
            <StatusPill status={statusPill.status} label={statusPill.label} />
            {agentDeleted && (
              <Badge variant="warning">{t(k("automation.agentDeleted"))}</Badge>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {isProject && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigate(`/projects/${detail.project_id}`)}
              >
                <FolderOpen className="h-3.5 w-3.5" />
                {t(k("automation.backToProject"))}
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              disabled={agentDeleted}
              onClick={() => setEditOpen(true)}
            >
              <Pencil className="h-3.5 w-3.5" />
              {t(k("automation.actionEdit"))}
            </Button>
            <Tooltip>
              <TooltipTrigger asChild>
                {/* span wrapper so the tooltip still fires on a disabled button */}
                <span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!canRunNow || runningNow}
                    onClick={() => void handleRunNow()}
                  >
                    {runningNow ? (
                      <Spinner className="h-3.5 w-3.5" />
                    ) : (
                      <Play className="h-3.5 w-3.5" />
                    )}
                    {t(k("automation.actionRunNow"))}
                  </Button>
                </span>
              </TooltipTrigger>
              {running && (
                <TooltipContent>
                  {t(k("automation.runningTooltip"))}
                </TooltipContent>
              )}
            </Tooltip>
            <Button variant="outline" size="sm" onClick={() => void handleToggle()}>
              {detail.status === "enabled" ? (
                <>
                  <Pause className="h-3.5 w-3.5" />
                  {t(k("automation.actionPause"))}
                </>
              ) : (
                <>
                  <Play className="h-3.5 w-3.5" />
                  {t(k("automation.actionResume"))}
                </>
              )}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="text-error-text"
              onClick={() => setDeleteOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" />
              {t(k("automation.actionDelete"))}
            </Button>
          </div>
        </div>

        {/* Instruction */}
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-ink-heading">
            {t(k("automation.sectionInstruction"))}
          </h2>
          <div className="whitespace-pre-wrap break-words rounded-lg border border-surface-border bg-surface-soft p-3 text-sm text-ink-body">
            {detail.prompt_template}
          </div>
        </section>

        {/* Schedule */}
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-ink-heading">
            {t(k("automation.sectionSchedule"))}
          </h2>
          <div className="rounded-lg border border-surface-border p-3 text-sm">
            <div className="text-ink-body">{detail.trigger_human_readable}</div>
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1 text-xs text-ink-meta">
              <span>
                {t(k("automation.nextRun"))}:{" "}
                {detail.next_run_at
                  ? formatRunTime(detail.next_run_at)
                  : t(k("automation.neverRun"))}
              </span>
              <span>
                {t(k("automation.lastRun"))}:{" "}
                {detail.last_run_at
                  ? formatRunTime(detail.last_run_at)
                  : t(k("automation.neverRun"))}
              </span>
            </div>
          </div>
        </section>

        {/* Run history */}
        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-ink-heading">
            {t(k("automation.recentExecutions"))}
          </h2>
          {runsLoading ? (
            <div className="flex justify-center py-8">
              <Spinner />
            </div>
          ) : runsError ? (
            <div className="flex flex-col items-center gap-3 py-8">
              <div className="text-sm text-ink-meta">
                {t(k("automation.runsLoadFailed"))}
              </div>
              <Button variant="outline" size="sm" onClick={() => void refreshRuns()}>
                {t(k("automation.retry"))}
              </Button>
            </div>
          ) : executionRows.length > 0 ? (
            <ExecutionLog
              rows={executionRows}
              onSessionClick={(sessionId) =>
                navigate(`/conversation/${sessionId}`)
              }
            />
          ) : (
            <div className="flex justify-center py-8">
              <EmptyState
                variant="plain"
                title={t(k("automation.noExecutions"))}
                icon={<Clock3 className="h-5 w-5" />}
              />
            </div>
          )}
        </section>
      </div>

      <CreateAutomationDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        onSubmit={handleEditSubmit}
        agents={agentChoices}
        // Edit mode: project is fixed to the row's project; the target selector
        // is create-only.
        fixedTargetName={detail.project_name}
        allowTaskMode={isProject}
        initial={{
          name: detail.name,
          prompt_template: detail.prompt_template,
          agent_slug: detail.agent_slug,
          trigger: detail.trigger,
          action_kind: (detail.action_kind as ActionKind) ?? "chat",
        }}
        title={t(k("automation.dialogTitleEditNamed"), { name: detail.name })}
      />

      <DeleteConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t(k("automation.deleteTitle"), { name: detail.name })}
        description={t(k("automation.deleteConfirmDesc"))}
        confirmLabel={t(k("common.delete"))}
        onConfirm={handleDelete}
      />
    </div>
  );
};
