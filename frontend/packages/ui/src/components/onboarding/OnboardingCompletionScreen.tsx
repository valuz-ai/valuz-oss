import type { FC } from "react";
import { Check, ArrowRight } from "lucide-react";
import { Button } from "../ui/button";
import { useI18n } from "../../hooks/use-i18n";

export interface OnboardingCompletionScreenProps {
  items: Array<{
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    value: string;
  }>;
  onStart: () => void;
}

export const OnboardingCompletionScreen: FC<
  OnboardingCompletionScreenProps
> = ({ items, onStart }) => {
  const { t } = useI18n();
  return (
    <div className="flex flex-col items-center gap-8 py-12">
      <div className="flex h-16 w-16 items-center justify-center rounded-full bg-[#EAF7EF]">
        <Check className="h-8 w-8 text-[#0E8F4A]" />
      </div>

      <div className="flex flex-col items-center gap-2 text-center">
        <h2 className="text-xl font-semibold text-ink-title">
          {t("onboarding.complete")}
        </h2>
        <p className="text-sm text-ink-body">{t("onboarding.completeDesc")}</p>
      </div>

      <div className="grid w-full max-w-lg gap-4 sm:grid-cols-3">
        {items.map((item) => (
          <div
            key={item.label}
            className="flex flex-col items-center gap-2 rounded-xl border border-surface-border bg-card p-4"
          >
            <item.icon className="h-5 w-5 text-ink-title" />
            <span className="text-xs text-ink-muted">{item.label}</span>
            <span className="text-sm font-medium text-ink-title">
              {item.value}
            </span>
          </div>
        ))}
      </div>

      <Button onClick={onStart} className="gap-2">
        {t("onboarding.getStart")}
        <ArrowRight className="h-4 w-4" />
      </Button>
    </div>
  );
};
