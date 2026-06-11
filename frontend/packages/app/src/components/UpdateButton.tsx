import { Download } from "lucide-react";
import { useTranslation, useUpdaterStore } from "@valuz/core";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@valuz/ui";

interface UpdateButtonProps {
  onClick: () => void;
}

export const UpdateButton = ({ onClick }: UpdateButtonProps) => {
  const { t } = useTranslation();
  const status = useUpdaterStore((s) => s.status);

  if (status !== "available") return null;

  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            onClick={onClick}
            className="flex h-[22px] w-[22px] animate-pulse items-center justify-center rounded-[5px] text-blue-500 transition-colors hover:bg-surface-muted"
          >
            <Download className="h-4 w-4" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          {t("updater.updateAvailable" as Parameters<typeof t>[0])}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};
