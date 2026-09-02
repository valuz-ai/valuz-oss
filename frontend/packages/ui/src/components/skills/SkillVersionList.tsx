/**
 * A skill's saved versions, newest first, with restore.
 *
 * Every save through the library records one version; restoring an older
 * one is itself a new version on top of the history, so the list only ever
 * grows and "restore" is never destructive. A skill that was never saved
 * through the library (hand-dropped into the skills directory, imported
 * before versioning) simply has no history yet — that is the empty state,
 * not an error.
 */
import { memo, useState } from "react";
import { History, Loader2, RotateCcw } from "lucide-react";

import { useI18n } from "../../hooks/use-i18n";
import { cn } from "../../lib/cn";

export interface SkillVersionEntry {
  revisionId: string;
  versionNo: number;
  createdAt: number;
  byteSize: number;
  isCurrent: boolean;
  /** ``"baseline"`` — content captured from the library directory right
   *  before it was overwritten, i.e. edits that were never saved through
   *  the library. Worth labelling: it is the one version the user did not
   *  explicitly create. */
  createdBy?: string | null;
}

export interface SkillVersionListProps {
  versions: SkillVersionEntry[];
  loading?: boolean;
  /** Restoring revision id, while the request is in flight. */
  restoringId?: string | null;
  onRestore?: (revisionId: string) => void;
  className?: string;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatWhen(ms: number): string {
  if (!ms) return "";
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const SkillVersionList = memo(function SkillVersionList({
  versions,
  loading = false,
  restoringId,
  onRestore,
  className,
}: SkillVersionListProps) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  const ordered = [...versions].sort((a, b) => b.versionNo - a.versionNo);

  return (
    <div className={cn("text-2xs", className)}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-1.5 text-2xs uppercase tracking-wider text-ink-label transition-colors hover:text-ink-body"
      >
        <History className="h-3 w-3" />
        {t("skill.versionsTitle")}
        {versions.length > 0 ? (
          <span className="text-ink-meta">({versions.length})</span>
        ) : null}
      </button>

      {open ? (
        loading ? (
          <div className="mt-2 flex items-center gap-1.5 text-ink-meta">
            <Loader2 className="h-3 w-3 animate-spin" />
            {t("common.loading")}
          </div>
        ) : ordered.length === 0 ? (
          <p className="mt-2 leading-relaxed text-ink-meta">
            {t("skill.versionsEmpty")}
          </p>
        ) : (
          <ul className="mt-2 space-y-1">
            {ordered.map((version) => {
              const busy = restoringId === version.revisionId;
              return (
                <li
                  key={version.revisionId}
                  className={cn(
                    "flex items-center gap-2 rounded-md px-1.5 py-1",
                    version.isCurrent ? "bg-brand-light" : "hover:bg-surface-2",
                  )}
                >
                  <span
                    className={cn(
                      "shrink-0 font-mono",
                      version.isCurrent ? "text-brand" : "text-ink-body",
                    )}
                  >
                    v{version.versionNo}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-ink-meta">
                    {formatWhen(version.createdAt)}
                    {version.createdBy === "baseline"
                      ? ` · ${t("skill.versionBaseline")}`
                      : ""}
                  </span>
                  <span className="shrink-0 text-ink-label">
                    {formatSize(version.byteSize)}
                  </span>
                  {version.isCurrent ? (
                    <span className="shrink-0 text-brand">
                      {t("skill.versionCurrent")}
                    </span>
                  ) : onRestore ? (
                    <button
                      type="button"
                      disabled={busy || !!restoringId}
                      onClick={() => onRestore(version.revisionId)}
                      title={t("skill.versionRestore")}
                      className={cn(
                        "inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5",
                        "text-ink-meta transition-colors hover:bg-surface-muted hover:text-ink-heading",
                        "disabled:cursor-not-allowed disabled:opacity-50",
                      )}
                    >
                      {busy ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <RotateCcw className="h-3 w-3" />
                      )}
                      {t("skill.versionRestore")}
                    </button>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )
      ) : null}
    </div>
  );
});
