import { ArrowLeft } from "lucide-react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface BackLinkProps {
  /** Navigation callback */
  onClick: () => void;
  /** Label text next to the arrow */
  label?: string;
  /** Extra class */
  className?: string;
}

/**
 * Back navigation button with ArrowLeft icon.
 * Used in detail pages to return to the parent list.
 */
export const BackLink = ({ onClick, label, className }: BackLinkProps) => {
  const { t } = useI18n();
  const resolvedLabel = label ?? t("common.back");
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1 text-xs text-ink-meta transition-colors hover:text-ink-heading",
        className,
      )}
    >
      <ArrowLeft className="h-3 w-3" />
      {resolvedLabel}
    </button>
  );
};
