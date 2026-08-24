/**
 * Card rendered in the conversation stream when the assistant calls the
 * ``automation`` tool with ``action: "create"``. Mirrors ``AgentProposalCard``:
 * ``create`` validates + previews but writes nothing — the user decides whether
 * to actually create the automation. The page wires the API call
 * (``automationsApi.confirmProposal``) via ``onConfirm``; Dismiss is client-side
 * only (nothing was persisted). State (pending → confirming → confirmed |
 * dismissed | error) is tracked by the parent so multiple cards behave
 * independently.
 *
 * ``validationError`` covers the case where the ``create`` tool itself rejected
 * the proposal (``ok === false`` — e.g. a bad cron / task-in-chat): the card
 * renders the message and offers no Confirm button.
 */
import { memo, useState } from "react";
import {
  AlarmClock,
  BookOpen,
  Check,
  Maximize2,
  Sparkles,
  User,
  X,
} from "lucide-react";
import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";
import { MarkdownContent } from "../conversation/MarkdownContent";
import { Button } from "../ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";

export type AutomationProposalState =
  | "pending"
  | "confirming"
  | "confirmed"
  | "dismissing"
  | "dismissed"
  | "error";

interface AutomationProposalCardProps {
  name: string;
  promptTemplate?: string;
  /** Localized schedule line ("every day at 9" / "every 5 minutes"). */
  triggerHuman?: string;
  /** Resolved executing agent — shown as the Lead in ``task`` mode. */
  agentName?: string | null;
  actionKind: "chat" | "task";
  /** Both action kinds: each fire runs in an isolated git worktree. */
  worktree?: boolean;
  /** Immutable Playbook version the server resolved for this proposal. */
  playbookVersion?: number | null;
  state: AutomationProposalState;
  /** When ``state === "error"``, the confirm failure to display. */
  errorMessage?: string;
  /** When the ``create`` tool rejected the proposal (ok === false). */
  validationError?: string | null;
  onConfirm: () => void;
  onDismiss: () => void;
}

export const AutomationProposalCard = memo(function AutomationProposalCard({
  name,
  promptTemplate,
  triggerHuman,
  agentName,
  actionKind,
  worktree = false,
  playbookVersion,
  state,
  errorMessage,
  validationError,
  onConfirm,
  onDismiss,
}: AutomationProposalCardProps) {
  const { t } = useI18n();
  const [detailsOpen, setDetailsOpen] = useState(false);

  // The create tool rejected the proposal outright — render a terminal error
  // with no actions (there's nothing to confirm).
  if (validationError) {
    return (
      <div className="rounded-lg border border-error/40 bg-error-light/40 px-4 py-3">
        <div className="flex items-center gap-1.5 text-xs font-medium text-error">
          <AlarmClock className="h-3.5 w-3.5" aria-hidden="true" />
          {t("automation.proposalFailed")}
        </div>
        <p className="mt-1 text-xs leading-snug text-ink-body">{validationError}</p>
      </div>
    );
  }

  const isBusy = state === "confirming" || state === "dismissing";
  const isTerminal = state === "confirmed" || state === "dismissed";
  const canConfirm =
    (state === "pending" || state === "error") && name.trim().length > 0;
  const modeLabel =
    actionKind === "task"
      ? t("automation.actionKindTask")
      : t("automation.actionKindChat");

  return (
    <>
      <div
        data-slot="automation-proposal-card"
        className={cn(
          "rounded-lg border bg-surface-soft transition-colors",
          state === "confirmed" && "border-success/40 bg-success/5",
          state === "dismissed" &&
            "border-surface-border bg-surface-2 opacity-80",
          state === "error" && "border-error/40 bg-error-light/40",
          state !== "confirmed" && state !== "dismissed" && state !== "error"
            ? "border-surface-border"
            : "",
        )}
      >
      <div className="flex items-start gap-3 px-4 py-3">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-brand/10 text-brand">
          {state === "confirmed" ? (
            <Check className="h-4 w-4" />
          ) : state === "dismissed" ? (
            <X className="h-4 w-4 text-ink-muted" />
          ) : (
            <AlarmClock className="h-4 w-4" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium text-ink-heading">
              {name || t("automation.proposalUnnamed")}
            </span>
            <span className="shrink-0 text-2xs uppercase tracking-wider text-ink-label">
              {t("automation.proposalNew")}
            </span>
            <span className="shrink-0 rounded-full bg-surface-muted px-1.5 text-2xs font-medium text-ink-label">
              {modeLabel}
            </span>
            {worktree ? (
              <span className="shrink-0 rounded-full bg-brand/10 px-1.5 text-2xs font-medium text-brand">
                {t("automation.worktreeLabel")}
              </span>
            ) : null}
          </div>

          {triggerHuman || agentName || playbookVersion ? (
            <div className="mt-1 flex flex-wrap items-center gap-3 text-xs">
              {triggerHuman ? (
                <span className="flex shrink-0 items-center gap-1 text-ink-body">
                  <Sparkles className="h-3 w-3 shrink-0 text-ink-label" />
                  {triggerHuman}
                </span>
              ) : null}
              {agentName ? (
                <span className="flex min-w-0 items-center gap-1 text-ink-meta">
                  <User className="h-3 w-3 shrink-0 text-ink-label" />
                  <span className="truncate">
                    {actionKind === "task"
                      ? `${t("automation.proposalLead")}: ${agentName}`
                      : `${t("automation.agentLabel")}: ${agentName}`}
                  </span>
                </span>
              ) : null}
              {playbookVersion ? (
                <span className="flex shrink-0 items-center gap-1 text-ink-meta">
                  <BookOpen className="h-3 w-3 shrink-0 text-ink-label" />
                  Playbook v{playbookVersion}
                </span>
              ) : null}
            </div>
          ) : null}

          {promptTemplate ? (
            <div
              data-slot="automation-prompt-preview"
              className="relative mt-2 pr-9"
            >
              <div className="line-clamp-3 whitespace-pre-wrap break-words text-xs leading-snug text-ink-body">
                {promptTemplate}
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon-xs"
                onClick={() => setDetailsOpen(true)}
                className="absolute right-0 top-0"
                title={t("automation.viewPrompt")}
                aria-label={t("automation.viewPrompt")}
              >
                <Maximize2 />
              </Button>
            </div>
          ) : null}

          {state === "confirmed" ? (
            <p className="mt-2 text-xs text-ink-body">
              {t("automation.proposalCreated")}
            </p>
          ) : null}
          {state === "dismissed" ? (
            <p className="mt-2 text-xs text-ink-meta">
              {t("automation.proposalDismissed")}
            </p>
          ) : null}
          {state === "error" && errorMessage ? (
            <p className="mt-2 text-xs text-error">
              {t("skill.operationFailed", { error: errorMessage })}
            </p>
          ) : null}
        </div>
      </div>

      {!isTerminal ? (
        <div className="flex items-center justify-end gap-2 border-t border-surface-border px-4 py-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={isBusy}
            loading={state === "dismissing"}
            onClick={onDismiss}
          >
            {t("common.cancel")}
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={!canConfirm || isBusy}
            loading={state === "confirming"}
            onClick={onConfirm}
          >
            {state === "error" ? t("common.retry") : t("automation.actionCreate")}
          </Button>
        </div>
      ) : null}
      </div>

      {promptTemplate ? (
        <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
          <DialogContent className="flex max-h-[88vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl">
            <DialogHeader className="border-b border-surface-border px-5 pb-4 pt-5 pr-12">
              <DialogTitle>{name || t("automation.proposalUnnamed")}</DialogTitle>
              <DialogDescription>{t("automation.promptLabel")}</DialogDescription>
            </DialogHeader>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
              <MarkdownContent
                content={promptTemplate}
                showCitationSources={false}
                className="text-sm leading-6 text-ink-body [&_h1]:mb-3 [&_h1]:mt-0 [&_h1]:text-xl [&_h2]:mb-2 [&_h2]:mt-5 [&_h2]:text-lg [&_h3]:mb-2 [&_h3]:mt-4 [&_h3]:text-base [&_li]:my-1 [&_ol]:my-3 [&_p]:my-2 [&_table]:text-xs [&_ul]:my-3"
              />
            </div>
          </DialogContent>
        </Dialog>
      ) : null}
    </>
  );
});
