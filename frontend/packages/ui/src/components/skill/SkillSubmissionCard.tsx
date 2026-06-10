/**
 * Card rendered in the conversation stream when the agent calls the
 * ``submit_skill`` tool. The agent has staged a draft skill; the user
 * decides whether to commit it to their library.
 *
 * Pure presentational — the page wires the actual API calls
 * (``skillsApi.confirmSubmission`` / ``dismissSubmission``) via the
 * ``onConfirm`` / ``onDismiss`` callbacks. State (pending → confirming
 * → confirmed | dismissed | error) is tracked by the parent so multiple
 * cards in the same conversation behave independently.
 */
import { memo, useState } from "react";
import {
  Check,
  ChevronRight,
  FileText,
  Folder,
  Loader2,
  X,
} from "lucide-react";
import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";

export type SkillSubmissionState =
  | "awaiting_files"
  | "pending"
  | "confirming"
  | "confirmed"
  | "dismissing"
  | "dismissed"
  | "error";

export interface SkillSubmissionFileNode {
  path: string;
  type: "file" | "directory";
  size?: number | null;
}

function formatSize(bytes?: number | null): string | null {
  if (bytes == null) return null;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const CHANGE_KIND_I18N_KEY: Record<"create" | "update", string> = {
  create: "skill.newSkill",
  update: "skill.updateSkill",
};

interface SkillSubmissionCardProps {
  slug: string;
  summary?: string;
  changeKind: "create" | "update";
  filesTouched: string[];
  state: SkillSubmissionState;
  /** When ``state === "error"``, the message to display under the card. */
  errorMessage?: string;
  /** When ``state === "confirmed"`` and the entry was a project, the
   * human-readable project label to confirm the binding ("已绑定到
   * Project 名称"). Omit for chat / skills_library entries. */
  boundToProjectLabel?: string | null;
  /** Live file listing from the staging directory — when present and
   * non-empty, the card surfaces a collapsible tree so the user can
   * eyeball what they're about to save. ``undefined`` means the page
   * hasn't scanned yet (initial render); ``[]`` means we scanned and
   * found nothing (the agent didn't write to ``.skill-staging`` —
   * paired with ``state === "awaiting_files"``). */
  stagedFiles?: SkillSubmissionFileNode[];
  /** Absolute path to the staging directory for this slug. Shown in
   * the awaiting-files state so the user (and AI) can debug. */
  stagingPath?: string;
  onConfirm: () => void;
  onDismiss: () => void;
}

export const SkillSubmissionCard = memo(function SkillSubmissionCard({
  slug,
  summary,
  changeKind,
  filesTouched,
  state,
  errorMessage,
  boundToProjectLabel,
  stagedFiles,
  stagingPath,
  onConfirm,
  onDismiss,
}: SkillSubmissionCardProps) {
  const { t } = useI18n();
  const isBusy = state === "confirming" || state === "dismissing";
  const isTerminal = state === "confirmed" || state === "dismissed";
  const isAwaiting = state === "awaiting_files";
  // Save is meaningful only when we have files staged AND we're not in a
  // terminal state. ``awaiting_files`` blocks save outright; ``error``
  // keeps it disabled until the page resets state on retry.
  const canSave = state === "pending" && (stagedFiles?.length ?? 0) > 0;
  const [filesOpen, setFilesOpen] = useState(true);

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface-soft transition-colors",
        state === "confirmed" &&
          "border-[rgba(83,188,118,0.5)] bg-[rgba(83,188,118,0.06)]",
        state === "dismissed" &&
          "border-surface-border bg-surface-2 opacity-80",
        state === "error" && "border-error/40 bg-error-light/40",
        state === "awaiting_files" && "border-warning/40 bg-warning-light/30",
        state !== "confirmed" &&
          state !== "dismissed" &&
          state !== "error" &&
          state !== "awaiting_files"
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
          ) : isAwaiting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <FileText className="h-4 w-4" />
          )}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="truncate text-sm font-medium text-ink-heading">
              {slug}
            </span>
            <span className="shrink-0 text-2xs uppercase tracking-wider text-ink-label">
              {t(CHANGE_KIND_I18N_KEY[changeKind])}
            </span>
          </div>
          {summary ? (
            <p className="mt-1 text-xs leading-snug text-ink-body">{summary}</p>
          ) : null}

          {/* Live file tree from staging — preferred when present.
              Falls back to the agent-supplied ``files_touched`` list
              when we haven't scanned yet (initial render). */}
          {stagedFiles && stagedFiles.length > 0 ? (
            <div className="mt-2">
              <button
                type="button"
                onClick={() => setFilesOpen((v) => !v)}
                className="inline-flex items-center gap-1 text-2xs uppercase tracking-wider text-ink-label transition-colors hover:text-ink-body"
                aria-expanded={filesOpen}
              >
                <ChevronRight
                  className={cn(
                    "h-3 w-3 transition-transform",
                    filesOpen && "rotate-90",
                  )}
                />
                {t("skill.stagingFileCount")}
                {stagedFiles.filter((f) => f.type === "file").length})
              </button>
              {filesOpen ? (
                <ul className="mt-1 space-y-0.5 font-mono text-[11px] leading-tight text-ink-meta">
                  {stagedFiles.map((node) => (
                    <li
                      key={`${node.type}-${node.path}`}
                      className="flex items-center gap-1.5 truncate"
                    >
                      {node.type === "directory" ? (
                        <Folder className="h-3 w-3 shrink-0 text-ink-label" />
                      ) : (
                        <FileText className="h-3 w-3 shrink-0 text-ink-label" />
                      )}
                      <span className="truncate">{node.path}</span>
                      {node.type === "file" && node.size != null ? (
                        <span className="ml-auto shrink-0 text-ink-label">
                          {formatSize(node.size)}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ) : filesTouched.length > 0 && !isAwaiting ? (
            <ul className="mt-2 space-y-0.5 font-mono text-[11px] leading-tight text-ink-meta">
              {filesTouched.slice(0, 6).map((path) => (
                <li key={path} className="truncate">
                  {path}
                </li>
              ))}
              {filesTouched.length > 6 ? (
                <li className="text-ink-label">
                  {t("skill.moreFiles", { count: filesTouched.length - 6 })}
                </li>
              ) : null}
            </ul>
          ) : null}

          {isAwaiting ? (
            <div className="mt-2 space-y-1">
              <p className="text-xs text-ink-body">{t("skill.waitingForAI")}</p>
              {stagingPath ? (
                <p className="break-all font-mono text-[11px] text-ink-label">
                  {stagingPath}
                </p>
              ) : null}
              <p className="text-2xs text-ink-meta">{t("skill.aiHint")}</p>
            </div>
          ) : null}
          {state === "confirmed" ? (
            <p className="mt-2 text-xs text-ink-body">
              {t("skill.savedToLib")}
              {boundToProjectLabel ? (
                <span className="text-ink-meta">
                  {" "}
                  {t("skill.boundToProject", { name: boundToProjectLabel })}
                </span>
              ) : null}
            </p>
          ) : null}
          {state === "dismissed" ? (
            <p className="mt-2 text-xs text-ink-meta">{t("skill.cancelled")}</p>
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
            disabled={!canSave || isBusy}
            onClick={onConfirm}
            className={cn(
              "inline-flex h-7 items-center rounded-md px-3 text-xs font-medium",
              "bg-brand text-white hover:bg-brand/90",
              "disabled:cursor-not-allowed disabled:opacity-50",
            )}
            title={
              isAwaiting
                ? t("skill.waitingAIWrite")
                : !canSave && state === "error"
                  ? t("skill.pleaseRetry")
                  : undefined
            }
          >
            {state === "confirming" ? (
              <Loader2 className="mr-1.5 h-3 w-3 animate-spin" />
            ) : null}
            {t("skill.saveToLib")}
          </button>
        </div>
      ) : null}
    </div>
  );
});
