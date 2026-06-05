import type { FC } from "react";
import { useI18n } from "../../hooks/use-i18n";

export interface StepProgressCardProps {
  progress: number;
  current: number;
  total: number;
}

export const StepProgressCard: FC<StepProgressCardProps> = ({
  progress,
  current,
  total,
}) => {
  const { t } = useI18n();
  const clampedProgress = Math.min(100, Math.max(0, progress));

  return (
    <div className="flex flex-col gap-4 rounded-[18px] border border-surface-border bg-card p-5">
      <span className="text-xs font-medium uppercase tracking-wide text-ink-muted">
        Progress
      </span>
      <span className="text-2xl font-semibold text-ink-title">
        {Math.round(clampedProgress)}%
      </span>
      <span className="text-xs text-ink-body">
        {t("onboarding.stepProgress", {
          current: String(current),
          total: String(total),
        })}
      </span>
      <div className="h-2 w-full overflow-hidden rounded-full bg-surface-soft">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{
            width: `${clampedProgress}%`,
            background: "linear-gradient(90deg, #533AFD, #8B7CFF)",
          }}
        />
      </div>
    </div>
  );
};
