/**
 * Card rendered in the conversation stream when the assistant calls the
 * ``propose_agent`` tool. The assistant has assembled a new agent (identity,
 * brain, equipment); the user decides whether to create it (and deploy it into
 * the current project).
 *
 * Pure presentational — the page wires the actual API call
 * (``agentsApi.confirmProposal``) via the ``onConfirm`` callback. Dismiss is
 * client-side only (no server staging to clean up, unlike skills). State
 * (pending → confirming → confirmed | dismissed | error) is tracked by the
 * parent so multiple cards in the same conversation behave independently.
 */
import { memo } from "react";
import { Bot, Check, Loader2, Plug, Wrench, X } from "lucide-react";
import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";

export type AgentProposalState =
  | "pending"
  | "confirming"
  | "confirmed"
  | "dismissing"
  | "dismissed"
  | "error";

interface AgentProposalCardProps {
  name: string;
  description?: string;
  instructions?: string;
  runtime: string;
  model: string;
  skills: string[];
  connectors: string[];
  state: AgentProposalState;
  /** When ``state === "error"``, the message to display under the card. */
  errorMessage?: string;
  /** When confirmed and deployed, the project label ("已部署到 名称"). */
  deployedProjectLabel?: string | null;
  onConfirm: () => void;
  onDismiss: () => void;
}

export const AgentProposalCard = memo(function AgentProposalCard({
  name,
  description,
  instructions,
  runtime,
  model,
  skills,
  connectors,
  state,
  errorMessage,
  deployedProjectLabel,
  onConfirm,
  onDismiss,
}: AgentProposalCardProps) {
  const { t } = useI18n();
  const isBusy = state === "confirming" || state === "dismissing";
  const isTerminal = state === "confirmed" || state === "dismissed";
  const canConfirm = state === "pending" && name.trim().length > 0;
  const none = t("agent.proposalNone");

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface-soft transition-colors",
        state === "confirmed" &&
          "border-[rgba(83,188,118,0.5)] bg-[rgba(83,188,118,0.06)]",
        state === "dismissed" && "border-surface-border bg-surface-2 opacity-80",
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
            <Bot className="h-4 w-4" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium text-ink-heading">
              {name || t("agent.proposalUnnamed")}
            </span>
            <span className="shrink-0 text-2xs uppercase tracking-wider text-ink-label">
              {t("agent.proposalNew")}
            </span>
          </div>
          {description ? (
            <p className="mt-1 text-xs leading-snug text-ink-body">
              {description}
            </p>
          ) : null}

          <p className="mt-2 text-2xs uppercase tracking-wider text-ink-label">
            {t("agent.proposalBrain")}
          </p>
          <p className="font-mono text-2xs text-ink-meta">
            {runtime} · {model}
          </p>

          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-2xs text-ink-meta">
            <span className="inline-flex items-center gap-1">
              <Wrench className="h-3 w-3 shrink-0 text-ink-label" />
              {t("agent.proposalSkills")}: {skills.length > 0 ? skills.join(", ") : none}
            </span>
            <span className="inline-flex items-center gap-1">
              <Plug className="h-3 w-3 shrink-0 text-ink-label" />
              {t("agent.proposalConnectors")}:{" "}
              {connectors.length > 0 ? connectors.join(", ") : none}
            </span>
          </div>

          {instructions ? (
            <p className="mt-2 line-clamp-3 text-xs leading-snug text-ink-body">
              {instructions}
            </p>
          ) : null}

          {state === "confirmed" ? (
            <p className="mt-2 text-xs text-ink-body">
              {deployedProjectLabel
                ? t("agent.proposalCreatedDeployed", { name: deployedProjectLabel })
                : t("agent.proposalCreated")}
            </p>
          ) : null}
          {state === "dismissed" ? (
            <p className="mt-2 text-xs text-ink-meta">
              {t("agent.proposalDismissed")}
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
          <button
            type="button"
            disabled={isBusy}
            onClick={onDismiss}
            className={cn(
              "inline-flex h-7 items-center rounded-md px-3 text-xs font-medium",
              "border border-surface-border text-ink-body hover:bg-surface-2",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {state === "dismissing" ? (
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            ) : null}
            {t("common.cancel")}
          </button>
          <button
            type="button"
            disabled={!canConfirm || isBusy}
            onClick={onConfirm}
            className={cn(
              "inline-flex h-7 items-center rounded-md px-3 text-xs font-medium",
              "bg-brand text-white hover:bg-brand-hover",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
          >
            {state === "confirming" ? (
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            ) : null}
            {t("agent.proposalCreate")}
          </button>
        </div>
      ) : null}
    </div>
  );
});
