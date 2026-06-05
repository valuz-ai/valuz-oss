import type { FC } from "react";
import { Cloud } from "lucide-react";
import { cn } from "../../lib/cn";
import { useI18n } from "../../hooks/use-i18n";

export interface ModelProviderSelectorProps {
  providers: Array<{
    id: string;
    name: string;
    desc: string;
    endpoint: string;
    accent: string;
    connected: boolean;
  }>;
  selectedId: string;
  onSelect: (id: string) => void;
}

export const ModelProviderSelector: FC<ModelProviderSelectorProps> = ({
  providers,
  selectedId,
  onSelect,
}) => {
  const { t } = useI18n();
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {providers.map((provider) => {
        const selected = selectedId === provider.id;
        return (
          <button
            key={provider.id}
            type="button"
            onClick={() => onSelect(provider.id)}
            className={cn(
              "flex flex-col gap-3 rounded-xl border p-5 text-left transition-all",
              selected
                ? "border-brand/25 bg-brand-light/55 shadow-md"
                : "border-surface-border bg-card",
            )}
          >
            <div
              className="h-1.5 w-full rounded-full"
              style={{ background: provider.accent }}
            />
            <div className="flex items-center gap-2">
              <Cloud className="h-5 w-5 text-ink-title" />
              <span className="text-sm font-medium text-ink-title">
                {provider.name}
              </span>
            </div>
            <span className="text-xs text-ink-body">{provider.desc}</span>
            <span
              className={cn(
                "inline-flex w-fit rounded-full px-2 py-0.5 text-[10px] font-medium",
                provider.connected
                  ? "bg-success-light text-success-text"
                  : "bg-surface-soft text-ink-muted",
              )}
            >
              {provider.connected
                ? t("onboarding.connected")
                : t("onboarding.pending")}
            </span>
            <span className="truncate font-mono text-[11px] text-ink-muted">
              {provider.endpoint}
            </span>
          </button>
        );
      })}
    </div>
  );
};
