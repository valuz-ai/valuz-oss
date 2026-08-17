import { useState } from "react";
import { Loader2, CheckCircle, AlertCircle, Layers } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Checkbox } from "../ui/checkbox";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface LinkPreview {
  name: string;
  description: string;
  files: { path: string; size?: number }[];
}

/** One skill detected inside the fetched source. When the source holds more
 * than one (a collection/plugin), the dialog passes the full list so the user
 * can multi-select which skills to import. */
export interface LinkSkillCandidate {
  preview_id: string;
  name: string;
  description: string;
  file_count?: number;
  relpath?: string;
}

export interface SkillLinkImportProps {
  onFetch: (url: string) => void;
  onImport: () => void;
  onCancel: () => void;
  preview: LinkPreview | null;
  /** Multi-skill sources (length > 1) switch the preview into a checklist.
   * The dialog owns selection state and passes it down. */
  candidates?: LinkSkillCandidate[];
  selectedIds?: string[];
  onToggleCandidate?: (previewId: string) => void;
  fetching: boolean;
  importing: boolean;
  error: string | null;
  className?: string;
}

export const SkillLinkImport = ({
  onFetch,
  onImport,
  onCancel,
  preview,
  candidates,
  selectedIds,
  onToggleCandidate,
  fetching,
  importing,
  error,
  className,
}: SkillLinkImportProps) => {
  const { t } = useI18n();
  const [url, setUrl] = useState("");

  const isMulti = (candidates?.length ?? 0) > 1;
  const selectedCount = selectedIds?.length ?? 0;

  return (
    <div className={cn("flex flex-col p-4", className)}>
      <div className="space-y-4">
        {/* URL input */}
        <div className="flex gap-2">
          <Input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://github.com/user/repo/tree/main/skills/my-skill"
            className={cn(
              "flex-1 font-mono text-xs",
              error && "border-error-border focus-visible:ring-error",
            )}
            onKeyDown={(e) => {
              if (e.key === "Enter" && url.trim() && !fetching) {
                onFetch(url.trim());
              }
            }}
          />
          <Button
            disabled={!url.trim() || fetching}
            onClick={() => onFetch(url.trim())}
          >
            {fetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {t("skill.fetchSkill")}
          </Button>
        </div>

        {/* Error state */}
        {error && (
          <div className="flex items-start gap-2 rounded-lg bg-error-light px-3 py-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-error-text" />
            <p className="text-xs text-error-text">{error}</p>
          </div>
        )}

        {/* Multi-skill checklist */}
        {isMulti && !error && candidates && (
          <div className="rounded-xl border border-surface-border bg-surface-soft p-3">
            <div className="mb-1 flex items-center gap-2">
              <Layers className="h-4 w-4 text-brand" />
              <span className="text-sm font-medium text-ink-heading">
                {t("skill.multiSkillDetected", { count: candidates.length })}
              </span>
            </div>
            <p className="mb-3 text-xs text-ink-meta">
              {t("skill.multiSkillHint")}
            </p>
            <div className="max-h-[260px] space-y-1.5 overflow-y-auto">
              {candidates.map((c) => {
                const checked = selectedIds?.includes(c.preview_id) ?? false;
                return (
                  <label
                    key={c.preview_id}
                    className={cn(
                      "flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2 transition-colors",
                      checked
                        ? "border-brand/40 bg-brand-light/30"
                        : "border-surface-border bg-background hover:border-brand/30",
                    )}
                  >
                    <Checkbox
                      checked={checked}
                      onCheckedChange={() => onToggleCandidate?.(c.preview_id)}
                      className="mt-0.5"
                    />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium text-ink-heading">
                          {c.name}
                        </span>
                        {c.file_count != null && c.file_count > 0 && (
                          <span className="shrink-0 text-xs text-ink-meta">
                            {t("skill.fileCount", { count: c.file_count })}
                          </span>
                        )}
                      </div>
                      {c.description && (
                        <p className="mt-0.5 line-clamp-2 text-xs text-ink-body">
                          {c.description}
                        </p>
                      )}
                      {c.relpath && (
                        <p className="mt-0.5 truncate font-mono text-2xs text-ink-meta">
                          {c.relpath}
                        </p>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
          </div>
        )}

        {/* Single-skill preview */}
        {!isMulti && preview && !error && (
          <div className="rounded-xl border border-surface-border bg-surface-soft p-3">
            <div className="mb-2 flex items-center gap-2">
              <CheckCircle className="h-4 w-4 text-green-500" />
              <span className="text-sm font-medium text-ink-heading">
                {preview.name}
              </span>
            </div>
            <p className="mb-3 text-xs text-ink-body">{preview.description}</p>

            {preview.files.length > 0 && (
              <div className="max-h-[160px] overflow-y-auto font-mono text-xs leading-6 text-ink-label">
                {preview.files.map((f) => (
                  <div
                    key={f.path}
                    className={cn(
                      "flex items-center gap-1.5",
                      f.path.endsWith("skill.md") && "font-medium text-brand",
                    )}
                  >
                    {f.path}
                    {f.size != null && (
                      <span className="text-ink-meta">
                        ({(f.size / 1024).toFixed(1)}K)
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="mt-5 flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          {t("common.cancel")}
        </Button>
        {(isMulti || preview) && (
          <Button
            onClick={onImport}
            disabled={importing || (isMulti && selectedCount === 0)}
          >
            {importing && (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            )}
            {isMulti
              ? t("skill.importSelected", { count: selectedCount })
              : t("common.import")}
          </Button>
        )}
      </div>
    </div>
  );
};
