import { AlertCircle, RefreshCw, ChevronDown } from "lucide-react";
import { useState } from "react";
import { Button } from "../ui/button";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface ErrorMessageCardProps {
  message: string;
  retryCount?: number;
  maxRetries?: number;
  onRetry?: () => void;
  onSwitchModel?: () => void;
}

export const ErrorMessageCard = ({
  message,
  retryCount = 0,
  maxRetries = 3,
  onRetry,
  onSwitchModel,
}: ErrorMessageCardProps) => {
  const { t } = useI18n();
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="rounded-xl border border-error-border bg-error-light px-4 py-3">
      <div className="flex items-start gap-2">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-error-text" />
        <div className="min-w-0 flex-1" data-slot="error-card-content">
          <div className="text-sm font-medium text-error-text">
            {t("conversation.generatedFailed")}
          </div>
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="mt-0.5 flex items-center gap-1 text-xs text-error-text hover:underline"
          >
            {t("conversation.viewDetails")}
            <ChevronDown
              className={cn("h-3 w-3 transition", expanded && "rotate-180")}
            />
          </button>
          {expanded && (
            <div className="mt-2 max-w-full whitespace-pre-wrap break-words rounded-lg bg-surface px-3 py-2 font-mono text-xs text-error-text">
              {message}
            </div>
          )}
        </div>
      </div>
      {retryCount > 0 && (
        <div className="mt-2 text-2xs text-error-text">
          {t("conversation.retriedCount", {
            current: String(retryCount),
            max: String(maxRetries),
          })}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        {onRetry && retryCount < maxRetries && (
          <Button
            variant="outline"
            size="sm"
            className="h-7 border-error-border text-xs text-error-text hover:bg-error-light"
            onClick={onRetry}
          >
            <RefreshCw className="mr-1 h-3 w-3" /> {t("system.retry")}
          </Button>
        )}
        {onSwitchModel && (
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={onSwitchModel}
          >
            {t("conversation.switchModel")}
          </Button>
        )}
      </div>
    </div>
  );
};
