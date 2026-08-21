import { Puzzle } from "lucide-react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";
import { Badge } from "../ui/badge";

export interface PluginBadgeInfo {
  /** First owning plugin's name (``plugin.badge`` → "插件：X"). */
  name: string;
  /** Additional owning plugins beyond the first (``plugin.badgeMore`` → "+n"). */
  more?: number;
}

export interface PluginBadgeProps extends PluginBadgeInfo {
  className?: string;
}

/**
 * Meta/ownership chip for a library resource that belongs to ≥1 plugin —
 * "插件：X" plus "+n" when several plugins reference the same slug (D6).
 * Grey-outline per the DESIGN.md tag taxonomy (ownership, not status).
 * Callers render nothing when the resource has no plugin.
 */
export const PluginBadge = ({ name, more = 0, className }: PluginBadgeProps) => {
  const { t } = useI18n();
  return (
    <Badge
      variant="metaOutline"
      data-slot="plugin-badge"
      title={name}
      className={cn("h-4 max-w-[160px] gap-0.5 px-1 text-micro leading-4", className)}
    >
      <Puzzle className="size-2.5 shrink-0" />
      <span className="truncate">{t("plugin.badge", { name })}</span>
      {more > 0 ? (
        <span className="shrink-0 tabular-nums">
          {t("plugin.badgeMore", { count: more })}
        </span>
      ) : null}
    </Badge>
  );
};
