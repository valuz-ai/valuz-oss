/**
 * Card rendered in the conversation stream when the agent calls the
 * ``submit_skill`` tool. The agent has staged a draft skill; the user
 * decides whether to commit it to their library.
 *
 * Pure presentational — the page wires the actual API calls (the
 * ``skill.submit`` operation's confirm / cancel) via ``onConfirm`` /
 * ``onDismiss``. State is tracked by the parent so multiple cards in the
 * same conversation behave independently; with the operation flow that
 * state comes from the server, so it survives a page reload.
 *
 * When the staged slug collides with a library skill the draft was NOT
 * prepared from, the card cannot save on its own: the user picks between
 * "save as the next version of that skill" and "save under a new name",
 * and the choice rides along with the confirmation.
 */
import { memo, useEffect, useState } from "react";
import {
  AlertTriangle,
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

/** How the staged draft relates to the user's library. Mirrors the
 *  backend's ``skill.submit`` preview. */
export type SkillSubmissionConflict =
  | "none"
  | "same_source"
  | "diverged"
  | "unprepared_collision";

/** The answer to a collision the card had to ask about. */
export type SkillSubmissionDecision =
  | { mode: "new_version" }
  | { mode: "rename"; new_slug: string };

const SLUG_RE = /^[a-z0-9][a-z0-9_-]*$/;

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
  /** The version this save would create. Shown on the save button so the
   * user knows whether they are adding v1 or v4. */
  nextVersion?: number | null;
  /** Set once the save landed — what the card reports afterwards. */
  savedVersion?: number | null;
  /** How the draft relates to the library. ``unprepared_collision`` makes
   * the card ask before it can save. */
  conflictKind?: SkillSubmissionConflict;
  onConfirm: (decision?: SkillSubmissionDecision) => void;
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
  nextVersion,
  savedVersion,
  conflictKind = "none",
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
  // Collision: the draft's slug is taken by a library skill it was not
  // prepared from, so saving means one of two different things and the
  // user has to say which.
  const needsDecision = conflictKind === "unprepared_collision";
  const [mode, setMode] = useState<"new_version" | "rename">("new_version");
  const [newSlug, setNewSlug] = useState(`${slug}-2`);
  useEffect(() => {
    setNewSlug(`${slug}-2`);
  }, [slug]);
  const renameInvalid = mode === "rename" && !SLUG_RE.test(newSlug.trim());
  const blockedByDecision = needsDecision && renameInvalid;
  const decision: SkillSubmissionDecision | undefined = !needsDecision
    ? undefined
    : mode === "rename"
      ? { mode: "rename", new_slug: newSlug.trim() }
      : { mode: "new_version" };

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface-soft transition-colors",
        state === "confirmed" &&
          "border-success-border bg-success-light",
        state === "dismissed" &&
          "border-surface-border bg-surface-2 opacity-80",
        state === "error" && "border-error-border bg-error-light",
        state === "awaiting_files" && "border-warning-border bg-warning-light",
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
                <ul className="mt-1 space-y-0.5 font-mono text-2xs leading-tight text-ink-meta">
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
            <ul className="mt-2 space-y-0.5 font-mono text-2xs leading-tight text-ink-meta">
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

          {needsDecision && !isTerminal && !isAwaiting ? (
            <div className="mt-2 rounded-md border border-warning-border bg-warning-light px-2.5 py-2">
              <p className="flex items-start gap-1.5 text-xs text-ink-body">
                <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning-text" />
                <span>{t("skill.collisionTitle", { slug })}</span>
              </p>
              <div className="mt-2 space-y-1.5">
                <label className="flex items-center gap-2 text-xs text-ink-body">
                  <input
                    type="radio"
                    className="h-3 w-3"
                    checked={mode === "new_version"}
                    onChange={() => setMode("new_version")}
                    disabled={isBusy}
                  />
                  <span>
                    {t("skill.collisionKeepSlug", {
                      version: String(nextVersion ?? ""),
                    })}
                  </span>
                </label>
                <label className="flex items-center gap-2 text-xs text-ink-body">
                  <input
                    type="radio"
                    className="h-3 w-3"
                    checked={mode === "rename"}
                    onChange={() => setMode("rename")}
                    disabled={isBusy}
                  />
                  <span>{t("skill.collisionRename")}</span>
                </label>
                {mode === "rename" ? (
                  <div>
                    <input
                      type="text"
                      value={newSlug}
                      onChange={(event) => setNewSlug(event.target.value)}
                      disabled={isBusy}
                      spellCheck={false}
                      className={cn(
                        "h-7 w-full rounded-md border px-2 font-mono text-2xs",
                        "bg-surface-base text-ink-body",
                        renameInvalid
                          ? "border-error-border"
                          : "border-surface-border",
                      )}
                    />
                    {renameInvalid ? (
                      <p className="mt-1 text-2xs text-error">
                        {t("skill.collisionSlugInvalid")}
                      </p>
                    ) : null}
                  </div>
                ) : null}
              </div>
            </div>
          ) : null}

          {isAwaiting ? (
            <div className="mt-2 space-y-1">
              <p className="text-xs text-ink-body">{t("skill.waitingForAI")}</p>
              {stagingPath ? (
                <p className="break-all font-mono text-2xs text-ink-label">
                  {stagingPath}
                </p>
              ) : null}
              <p className="text-2xs text-ink-meta">{t("skill.aiHint")}</p>
            </div>
          ) : null}
          {state === "confirmed" ? (
            <p className="mt-2 text-xs text-ink-body">
              {savedVersion != null
                ? t("skill.savedAsVersion", { version: String(savedVersion) })
                : t("skill.savedToLib")}
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
        <div className="flex items-center justify-end gap-2 px-4 py-2">
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
            disabled={!canSave || isBusy || blockedByDecision}
            onClick={() => onConfirm(decision)}
            className={cn(
              "inline-flex h-7 items-center rounded-md px-3 text-xs font-medium",
              "bg-brand text-white hover:bg-brand-hover",
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
            {nextVersion != null && !needsDecision
              ? t("skill.saveAsVersion", { version: String(nextVersion) })
              : t("skill.saveToLib")}
          </button>
        </div>
      ) : null}
    </div>
  );
});
