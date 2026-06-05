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
    <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3">
      <div className="flex items-start gap-2">
        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-500" />
        <div className="flex-1">
          <div className="text-sm font-medium text-red-700">
            {t("conversation.generatedFailed")}
          </div>
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="mt-0.5 flex items-center gap-1 text-xs text-red-500 hover:underline"
          >
            {t("conversation.viewDetails")}
            <ChevronDown
              className={cn("h-3 w-3 transition", expanded && "rotate-180")}
            />
          </button>
          {expanded && (
            <div className="mt-2 rounded-lg bg-surface px-3 py-2 font-mono text-xs text-red-600">
              {message}
            </div>
          )}
        </div>
      </div>
      {retryCount > 0 && (
        <div className="mt-2 text-2xs text-red-400">
          {t("conversation.retriedCount", {
            current: String(retryCount),
            max: String(maxRetries),
          })}
        </div>
      )}
      <div className="mt-3 flex gap-2">
        {onRetry && retryCount < maxRetries && (
          <Button
            variant="outline"
            size="sm"
            className="h-7 border-red-200 text-xs text-red-600 hover:bg-red-50"
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
