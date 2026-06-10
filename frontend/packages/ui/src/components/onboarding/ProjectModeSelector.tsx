import type { FC } from "react";
import { Laptop, Layers3, Cloud } from "lucide-react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface ProjectModeSelectorProps {
  value: string;
  onChange: (v: string) => void;
}

export const ProjectModeSelector: FC<ProjectModeSelectorProps> = ({
  value,
  onChange,
}) => {
  const { t } = useI18n();

  const modes = [
    {
      id: "local",
      name: t("onboarding.localFirst"),
      description: t("onboarding.localFirstDesc"),
      badge: "offline",
      Icon: Laptop,
    },
    {
      id: "hybrid",
      name: t("onboarding.hybrid"),
      description: t("onboarding.hybridDesc"),
      badge: t("onboarding.recommended"),
      Icon: Layers3,
    },
    {
      id: "cloud",
      name: t("onboarding.cloudCollab"),
      description: t("onboarding.cloudCollabDesc"),
      badge: t("onboarding.cloudCollabDesc"),
      Icon: Cloud,
    },
  ] as const;

  return (
    <div className="grid gap-3 md:grid-cols-3">
      {modes.map((mode) => {
        const selected = value === mode.id;
        return (
          <button
            key={mode.id}
            type="button"
            onClick={() => onChange(mode.id)}
            className={cn(
              "flex flex-col items-start gap-3 rounded-xl border p-5 text-left transition-all",
              selected
                ? "border-brand/25 bg-brand-light/55 shadow-md"
                : "border-surface-border bg-card",
            )}
          >
            <mode.Icon className="h-6 w-6 text-ink-title" />
            <span className="text-sm font-medium text-ink-title">
              {mode.name}
            </span>
            <span className="text-xs text-ink-body">{mode.description}</span>
            <span
              className={cn(
                "inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium",
                mode.id === "hybrid"
                  ? "bg-brand-light text-brand"
                  : "bg-surface-soft text-ink-muted",
              )}
            >
              {mode.badge}
            </span>
          </button>
        );
      })}
    </div>
  );
};
