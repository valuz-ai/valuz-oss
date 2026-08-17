import { memo, useState } from "react";
import { ChevronRight, FileText, Workflow } from "lucide-react";
import type { WorkflowState } from "@valuz/shared";
import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";
import { StatusPill } from "../common/StatusPill";
import { Progress } from "../ui/progress";

export interface WorkflowProgressCardProps {
  state: WorkflowState;
  /** Shown when the run hasn't reported a workflow name yet. */
  fallbackTitle?: string;
  defaultOpen?: boolean;
  /**
   * Reveal/open the run's full result file (``state.statePath``) in the OS.
   * Wired to the platform ``revealInFinder``; omitted on hosts that can't open
   * local files (web), where the "view details" link is hidden.
   */
  onOpenStateFile?: (path: string) => void;
}

/**
 * Live progress of a Claude dynamic-workflow (``Workflow`` tool) run. Mirrors
 * the ``/workflows`` TUI the SDK can't expose: an overview header (name +
 * status + agents-done/total + a progress bar) over an expandable per-agent
 * list. Fed by ``session.workflow_progress`` SSE snapshots, keyed in the
 * conversation page by the launch tool_use_id.
 */
export const WorkflowProgressCard = memo(
  function WorkflowProgressCard({
    state,
    fallbackTitle,
    defaultOpen = false,
    onOpenStateFile,
  }: WorkflowProgressCardProps) {
    const [open, setOpen] = useState(defaultOpen);
    const { t } = useI18n();

    const title =
      state.workflowName || fallbackTitle || t("conversation.workflow.title");
    const total = state.agentCount;
    const done = state.agentsDone;
    // Percentage drives the bar. With no agents discovered yet, a completed run
    // still reads as 100%; a running one sits at 0 until the first agent lands.
    const pct =
      total > 0
        ? Math.round((done / total) * 100)
        : state.status === "completed"
          ? 100
          : 0;

    // The kernel normalizes the terminal snapshot's status to a stable verb:
    // ``running`` while live, ``completed`` on a clean end, or the runtime's
    // own terminal verb (``killed`` / ``aborted`` / ``failed`` / ``cancelled``)
    // when the run didn't finish. Map each to a localized label; StatusPill
    // pulses only for ``running`` (via status-tone), so terminal states settle.
    const statusLabel =
      state.status === "running"
        ? t("conversation.workflow.statusRunning")
        : state.status === "completed"
          ? t("conversation.workflow.statusCompleted")
          : state.status === "failed" || state.status === "error"
            ? t("conversation.workflow.statusFailed")
            : state.status === "killed" ||
                state.status === "aborted" ||
                state.status === "cancelled" ||
                state.status === "stopped"
              ? t("conversation.workflow.statusStopped")
              : state.status;

    const agents = state.workflowProgress;
    // The run's output, surfaced once it finishes so a completed card isn't a
    // dead end. ``resultQuestion`` / ``resultSummary`` show inline; ``statePath``
    // links to the full result file (desktop only — ``onOpenStateFile`` is the
    // platform reveal, absent on web).
    const hasResultText = Boolean(state.resultQuestion || state.resultSummary);
    const canOpenStateFile = Boolean(state.statePath && onOpenStateFile);

    return (
      <div className="overflow-hidden rounded-lg border border-surface-border bg-surface-soft">
        <button
          type="button"
          aria-expanded={open}
          className="flex w-full items-center gap-2 px-3 py-[9px] text-left transition-colors duration-[120ms] hover:bg-[rgba(0,0,0,0.02)]"
          onClick={() => setOpen((current) => !current)}
        >
          <ChevronRight
            className={cn(
              "h-3 w-3 shrink-0 text-[#94A3B8] transition-transform duration-[150ms]",
              open && "rotate-90",
            )}
          />
          <Workflow className="h-3.5 w-3.5 shrink-0 text-brand" aria-hidden />
          <span className="min-w-0 shrink-0 truncate font-mono text-xs font-medium text-ink-heading">
            {title}
          </span>
          <span className="min-w-0 flex-1 truncate text-xs text-ink-meta tabular">
            {t("conversation.workflow.agentsDone", { done, total })}
          </span>
          <StatusPill status={state.status} label={statusLabel} />
        </button>

        <div className="px-3 pb-2.5">
          <Progress value={pct} className="h-1.5" />
        </div>

        {hasResultText || canOpenStateFile ? (
          <div className="space-y-2 border-t border-surface-border px-3 pt-2.5 pb-3 text-xs">
            {state.resultQuestion ? (
              <div>
                <div className="label-mono mb-0.5 text-ink-meta">
                  {t("conversation.workflow.question")}
                </div>
                <div className="text-ink-body">{state.resultQuestion}</div>
              </div>
            ) : null}
            {state.resultSummary ? (
              <div>
                <div className="label-mono mb-0.5 text-ink-meta">
                  {t("conversation.workflow.summary")}
                </div>
                <div className="max-h-48 overflow-auto whitespace-pre-wrap leading-[1.6] text-ink-body">
                  {state.resultSummary}
                </div>
              </div>
            ) : null}
            {canOpenStateFile ? (
              <button
                type="button"
                onClick={() => onOpenStateFile?.(state.statePath as string)}
                className="inline-flex items-center gap-1.5 text-2xs text-brand transition-colors hover:underline"
              >
                <FileText className="h-3 w-3 shrink-0" aria-hidden />
                {t("conversation.workflow.viewDetails")}
              </button>
            ) : null}
          </div>
        ) : null}

        {open && agents.length > 0 ? (
          <div className="max-h-60 space-y-1 overflow-auto border-t border-surface-border px-3 pt-2 pb-3 pl-8">
            {agents.map((agent) => {
              const isDone = agent.state === "done";
              return (
                <div
                  key={agent.agentId}
                  className="flex items-center gap-2 text-[11.5px]"
                >
                  <span
                    className={cn(
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      isDone
                        ? "bg-emerald-500"
                        : "bg-brand animate-pulse",
                    )}
                    aria-hidden
                  />
                  <span className="min-w-0 flex-1 truncate font-mono text-ink-body">
                    {agent.label || agent.agentId}
                  </span>
                  <span
                    className={cn(
                      "shrink-0 text-2xs",
                      isDone ? "text-success-text" : "text-ink-meta",
                    )}
                  >
                    {isDone
                      ? t("conversation.workflow.stateDone")
                      : t("conversation.workflow.stateRunning")}
                  </span>
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    );
  },
  (prev, next) =>
    prev.state.runId === next.state.runId &&
    prev.state.status === next.state.status &&
    prev.state.agentCount === next.state.agentCount &&
    prev.state.agentsDone === next.state.agentsDone &&
    prev.state.workflowProgress === next.state.workflowProgress &&
    prev.state.resultSummary === next.state.resultSummary &&
    prev.state.resultQuestion === next.state.resultQuestion &&
    prev.state.statePath === next.state.statePath &&
    prev.onOpenStateFile === next.onOpenStateFile &&
    (prev.defaultOpen ?? false) === (next.defaultOpen ?? false),
);
