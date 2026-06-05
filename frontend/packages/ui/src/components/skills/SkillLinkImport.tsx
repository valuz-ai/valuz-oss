import { useState } from "react";
import { Loader2, CheckCircle, AlertCircle } from "lucide-react";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface LinkPreview {
  name: string;
  description: string;
  files: { path: string; size?: number }[];
}

export interface SkillLinkImportProps {
  onFetch: (url: string) => void;
  onImport: () => void;
  onCancel: () => void;
  preview: LinkPreview | null;
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
  fetching,
  importing,
  error,
  className,
}: SkillLinkImportProps) => {
  const { t } = useI18n();
  const [url, setUrl] = useState("");

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
              error && "border-red-300 focus-visible:ring-red-200",
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
          <div className="flex items-start gap-2 rounded-lg bg-red-50 px-3 py-2">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
            <p className="text-xs text-red-700">{error}</p>
          </div>
        )}

        {/* Preview */}
        {preview && !error && (
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
        {preview && (
          <Button onClick={onImport} disabled={importing}>
            {importing && (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            )}
            {t("common.import")}
          </Button>
        )}
      </div>
    </div>
  );
};
