import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderTree,
  GitFork,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

/**
 * Right-side panel rendered inside the conversation page when a chat is in
 * "Skill Creator" mode. Shows the staging slugs the agent has produced so
 * far and lets the user select which to sync into the skill library.
 */

export type StagingConflictKind = "none" | "same_source" | "diverged";
export type StagingSyncStrategy = "overwrite" | "fork" | "abort";

export interface StagingPanelFile {
  path: string;
  type: "file" | "directory";
  size?: number | null;
}

export interface StagingPanelSlug {
  slug: string;
  name: string;
  description: string;
  fileCount: number;
  totalBytes: number;
  conflictKind: StagingConflictKind;
  suggestedStrategy: StagingSyncStrategy;
  suggestedNewSlug?: string | null;
  sourceSkillId?: string | null;
  version?: number | null;
  files?: StagingPanelFile[];
}

export interface SkillStagingPanelProps {
  slugs: StagingPanelSlug[];
  refreshing: boolean;
  syncing: boolean;
  onRefresh: () => void;
  onSync: (
    items: { slug: string; strategy: StagingSyncStrategy; newSlug?: string }[],
  ) => void;
  /** Resolves the UTF-8 contents of a file under a slug; used for preview. */
  onLoadFile?: (slug: string, path: string) => Promise<string>;
}

const formatBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
};

const conflictLabelKeys: Record<StagingConflictKind, string> = {
  none: "skill.stagingNew",
  same_source: "skill.stagingUpdate",
  diverged: "skill.stagingOriginalChanged",
};

const conflictTone: Record<StagingConflictKind, string> = {
  none: "text-success",
  same_source: "text-brand",
  diverged: "text-amber-500",
};

interface SlugRowState {
  selected: boolean;
  strategy: StagingSyncStrategy;
  newSlug: string;
}

const defaultRowState = (s: StagingPanelSlug): SlugRowState => ({
  selected: true,
  strategy: s.suggestedStrategy,
  newSlug: s.suggestedNewSlug ?? `${s.slug}-v2`,
});

export const SkillStagingPanel = ({
  slugs,
  refreshing,
  syncing,
  onRefresh,
  onSync,
  onLoadFile,
}: SkillStagingPanelProps) => {
  const { t } = useI18n();
  const [state, setState] = useState<Record<string, SlugRowState>>(() =>
    Object.fromEntries(slugs.map((s) => [s.slug, defaultRowState(s)])),
  );
  // Tracks which slug rows are expanded to show their file tree.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  // Currently previewed file: { slug, path } and its loaded content.
  const [previewKey, setPreviewKey] = useState<{
    slug: string;
    path: string;
  } | null>(null);
  const [previewContent, setPreviewContent] = useState<string>("");
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    if (!previewKey || !onLoadFile) return;
    let cancelled = false;
    setPreviewLoading(true);
    onLoadFile(previewKey.slug, previewKey.path)
      .then((c) => {
        if (!cancelled) setPreviewContent(c);
      })
      .catch((err) => {
        if (!cancelled)
          setPreviewContent(
            `Error loading file: ${err instanceof Error ? err.message : "unknown"}`,
          );
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [previewKey, onLoadFile]);

  // Keep state in sync as the scan refreshes (new slugs appear / disappear).
  useMemo(() => {
    setState((prev) => {
      const next: Record<string, SlugRowState> = {};
      for (const s of slugs) {
        next[s.slug] = prev[s.slug] ?? defaultRowState(s);
      }
      return next;
    });
    // Re-run only when the slug set changes by identity / count.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slugs.map((s) => s.slug).join("|")]);

  const selectedCount = Object.values(state).filter((r) => r.selected).length;

  const handleSync = () => {
    const items = slugs
      .filter((s) => state[s.slug]?.selected)
      .map((s) => {
        const r = state[s.slug];
        if (r.strategy === "fork") {
          return {
            slug: s.slug,
            strategy: "fork" as const,
            newSlug: r.newSlug,
          };
        }
        return { slug: s.slug, strategy: r.strategy };
      });
    if (items.length === 0) return;
    onSync(items);
  };

  return (
    <div className="flex h-full flex-col border-l border-surface-border bg-surface">
      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-1.5">
          <Sparkles className="h-3.5 w-3.5 text-brand" />
          <span className="text-xs font-medium text-ink-heading">
            {t("skill.generatedSkills")}
          </span>
          {slugs.length > 0 && (
            <span className="rounded bg-surface-soft px-1.5 py-0.5 text-2xs text-ink-meta">
              {slugs.length}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="rounded p-1 text-ink-muted hover:bg-surface-soft disabled:opacity-50"
          aria-label={t("common.refresh")}
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", refreshing && "animate-spin")}
          />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2">
        {slugs.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-ink-meta">
            <FolderTree className="h-8 w-8" />
            <p className="text-xs">{t("skill.noGenerated")}</p>
            <p className="text-2xs">{t("skill.noGeneratedHint")}</p>
          </div>
        ) : (
          <ul className="space-y-2">
            {slugs.map((s) => {
              const row = state[s.slug] ?? defaultRowState(s);
              return (
                <li
                  key={s.slug}
                  className={cn(
                    "rounded-md border p-2 transition",
                    row.selected
                      ? "border-brand/40 bg-brand/5"
                      : "border-surface-border bg-card",
                  )}
                >
                  <label className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      className="mt-0.5"
                      checked={row.selected}
                      onChange={(e) =>
                        setState((prev) => ({
                          ...prev,
                          [s.slug]: { ...row, selected: e.target.checked },
                        }))
                      }
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="min-w-0 truncate text-xs font-medium text-ink-heading">
                          {s.name}
                        </span>
                        {s.version != null && (
                          <span className="rounded bg-surface-soft px-1.5 py-0.5 text-2xs text-ink-meta">
                            v{s.version}
                          </span>
                        )}
                        <span
                          className={cn(
                            "rounded bg-surface-soft px-1.5 py-0.5 text-2xs",
                            conflictTone[s.conflictKind],
                          )}
                        >
                          {t(conflictLabelKeys[s.conflictKind])}
                        </span>
                        {s.sourceSkillId && (
                          <span className="flex items-center gap-0.5 text-2xs text-ink-meta">
                            <GitFork className="h-2.5 w-2.5" />
                            {t("skill.optimize")}
                          </span>
                        )}
                      </div>
                      <div className="mt-0.5 truncate text-2xs text-ink-body">
                        {s.description || `slug: ${s.slug}`}
                      </div>
                      <div className="mt-1 flex items-center gap-2 text-2xs text-ink-meta">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.preventDefault();
                            setExpanded((prev) => ({
                              ...prev,
                              [s.slug]: !prev[s.slug],
                            }));
                          }}
                          className="flex items-center gap-0.5 hover:text-ink-body"
                        >
                          {expanded[s.slug] ? (
                            <ChevronDown className="h-2.5 w-2.5" />
                          ) : (
                            <ChevronRight className="h-2.5 w-2.5" />
                          )}
                          <FileText className="h-2.5 w-2.5" />
                          {t("skill.fileCount", { count: s.fileCount })}
                        </button>
                        <span>·</span>
                        <span>{formatBytes(s.totalBytes)}</span>
                      </div>

                      {expanded[s.slug] && s.files && s.files.length > 0 && (
                        <div className="mt-1.5 rounded-md border border-surface-border bg-card">
                          <ul className="max-h-[180px] overflow-y-auto py-1">
                            {s.files
                              .filter((f) => f.type === "file")
                              .map((f) => {
                                const active =
                                  previewKey?.slug === s.slug &&
                                  previewKey?.path === f.path;
                                return (
                                  <li key={f.path}>
                                    <button
                                      type="button"
                                      onClick={(e) => {
                                        e.preventDefault();
                                        if (!onLoadFile) return;
                                        setPreviewKey({
                                          slug: s.slug,
                                          path: f.path,
                                        });
                                      }}
                                      className={cn(
                                        "flex w-full items-center gap-1 px-2 py-0.5 text-left font-mono text-2xs transition",
                                        active
                                          ? "bg-brand/10 text-brand"
                                          : "text-ink-body hover:bg-surface-soft",
                                        !onLoadFile &&
                                          "cursor-default opacity-60",
                                      )}
                                    >
                                      <Folder className="h-2.5 w-2.5 shrink-0 opacity-50" />
                                      <span className="truncate">{f.path}</span>
                                      {f.size != null && (
                                        <span className="ml-auto shrink-0 text-ink-meta">
                                          {formatBytes(f.size)}
                                        </span>
                                      )}
                                    </button>
                                  </li>
                                );
                              })}
                          </ul>
                          {previewKey?.slug === s.slug && (
                            <div className="border-t border-surface-border bg-surface-soft p-2">
                              <div className="mb-1 flex items-center justify-between text-2xs text-ink-meta">
                                <span className="truncate font-mono">
                                  {previewKey.path}
                                </span>
                                <button
                                  type="button"
                                  className="text-ink-meta hover:text-ink-body"
                                  onClick={(e) => {
                                    e.preventDefault();
                                    setPreviewKey(null);
                                  }}
                                >
                                  {t("common.close")}
                                </button>
                              </div>
                              <pre className="max-h-[180px] overflow-auto whitespace-pre-wrap break-all font-mono text-2xs leading-snug text-ink-label">
                                {previewLoading
                                  ? t("common.loading")
                                  : previewContent}
                              </pre>
                            </div>
                          )}
                        </div>
                      )}

                      {row.selected && s.conflictKind !== "none" && (
                        <div className="mt-1.5 flex items-center gap-1.5 text-2xs">
                          <select
                            className="rounded border border-surface-border bg-surface px-1.5 py-0.5 text-2xs text-ink-body"
                            value={row.strategy}
                            onChange={(e) =>
                              setState((prev) => ({
                                ...prev,
                                [s.slug]: {
                                  ...row,
                                  strategy: e.target
                                    .value as StagingSyncStrategy,
                                },
                              }))
                            }
                          >
                            <option value="overwrite">
                              {t("skill.overwrite")}
                            </option>
                            <option value="fork">{t("skill.saveAsNew")}</option>
                            <option value="abort">{t("skill.skip")}</option>
                          </select>
                          {row.strategy === "fork" && (
                            <input
                              type="text"
                              className="flex-1 rounded border border-surface-border bg-surface px-1.5 py-0.5 text-2xs text-ink-heading"
                              value={row.newSlug}
                              onChange={(e) =>
                                setState((prev) => ({
                                  ...prev,
                                  [s.slug]: { ...row, newSlug: e.target.value },
                                }))
                              }
                              placeholder="new-slug"
                            />
                          )}
                        </div>
                      )}
                    </div>
                  </label>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="px-3 py-2">
        <button
          type="button"
          disabled={selectedCount === 0 || syncing}
          onClick={handleSync}
          className={cn(
            "flex w-full items-center justify-center gap-1.5 rounded bg-brand py-1.5 text-xs font-medium text-white transition",
            (selectedCount === 0 || syncing) && "cursor-not-allowed opacity-50",
          )}
        >
          {syncing ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5" />
          )}
          {syncing
            ? t("skill.syncing")
            : t("skill.syncCount", { count: selectedCount })}
        </button>
      </div>
    </div>
  );
};
