import { AlertTriangle, Check, Loader2, FileQuestion } from "lucide-react";
import { Badge } from "../ui/badge";
import { useI18n } from "../../hooks/use-i18n";

export interface IndexingStatusBadgeProps {
  status: "ready" | "indexing" | "failed" | "queued" | "missing";
  chunks?: number;
  progress?: number;
}

export const IndexingStatusBadge = ({
  status,
  progress,
}: IndexingStatusBadgeProps) => {
  const { t } = useI18n();
  if (status === "ready") {
    return (
      <Badge variant="success" className="gap-1">
        <Check className="h-2.5 w-2.5" />
        {t("knowledge.indexed")}
      </Badge>
    );
  }

  if (status === "indexing") {
    return (
      <div className="flex items-center gap-2">
        <Badge variant="brand" className="gap-1">
          <Loader2 className="h-2.5 w-2.5 animate-spin" />
          {t("knowledge.indexing")}
        </Badge>
        <div className="h-1.5 w-20 overflow-hidden rounded-full bg-surface-border">
          <div
            className="h-full rounded-full bg-brand transition-all"
            style={{ width: `${progress ?? 0}%` }}
          />
        </div>
        <span className="tabular text-2xs text-ink-muted">{progress}%</span>
      </div>
    );
  }

  if (status === "failed") {
    return (
      <Badge variant="error" className="gap-1">
        <AlertTriangle className="h-2.5 w-2.5" />
        {t("common.failed")}
      </Badge>
    );
  }

  if (status === "missing") {
    return (
      <Badge variant="warning" className="gap-1">
        <FileQuestion className="h-2.5 w-2.5" />
        {t("knowledge.statusSourceMissing")}
      </Badge>
    );
  }

  return <Badge variant="outline">{t("knowledge.statusWaiting")}</Badge>;
};
