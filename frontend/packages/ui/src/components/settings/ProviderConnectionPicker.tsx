import { type FC } from "react";
import { Sparkles, Cpu, KeyRound, ChevronRight } from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../ui/dialog";
import { cn } from "@valuz/ui/lib/utils";
import { useI18n } from "../../hooks/use-i18n";

/**
 * "How would you like to connect?" picker — the first step of the
 * Add-Provider flow (REP-107 Slice 4d). Mirrors the same card layout
 * from the first-launch onboarding so the two entry points feel like
 * one mental model. The user picks a connection type here and the
 * caller routes to the corresponding sub-flow:
 *
 * - ``claude-subscription`` → OAuth subscription dialog (claude /login)
 * - ``codex-subscription``  → OAuth subscription dialog (codex /login)
 * - ``api-key``             → existing ProviderAddDialog (API key form)
 *
 * No state of its own — pure presentational. The parent owns the open
 * flag and route decisions.
 */

export type ProviderConnectionKind =
  | "claude-subscription"
  | "codex-subscription"
  | "api-key";

export interface ProviderConnectionPickerProps {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSelect: (kind: ProviderConnectionKind) => void;
}

interface CardProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  onClick: () => void;
  recommended?: boolean;
}

const ConnectionCard: FC<CardProps> = ({
  icon,
  title,
  description,
  onClick,
  recommended,
}) => {
  const { t } = useI18n();
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "group relative flex w-full items-center gap-3 rounded-xl border bg-surface px-4 py-3 text-left transition-all duration-[120ms]",
        "border-surface-border hover:border-brand/40 hover:shadow-sm",
      )}
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-heading">
        {icon}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-ink-heading">{title}</span>
          {recommended && (
            <span className="rounded bg-brand/10 px-1.5 py-0.5 text-2xs text-brand">
              {t("ui.providerConnection.recommended")}
            </span>
          )}
        </div>
        <div className="mt-0.5 text-2xs text-ink-meta">{description}</div>
      </div>
      <ChevronRight className="h-4 w-4 shrink-0 text-ink-meta transition-transform group-hover:translate-x-0.5 group-hover:text-ink-body" />
    </button>
  );
};

export const ProviderConnectionPicker: FC<ProviderConnectionPickerProps> = ({
  open,
  onOpenChange,
  onSelect,
}) => {
  const { t } = useI18n();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("ui.providerConnection.title")}</DialogTitle>
          <DialogDescription>
            {t("ui.providerConnection.description")}
          </DialogDescription>
        </DialogHeader>
        <div className="mt-1 flex flex-col gap-2">
          <ConnectionCard
            icon={<Sparkles className="h-4 w-4" strokeWidth={1.8} />}
            title={t("ui.providerConnection.claudeTitle")}
            description={t("ui.providerConnection.claudeDesc")}
            recommended
            onClick={() => onSelect("claude-subscription")}
          />
          <ConnectionCard
            icon={<Cpu className="h-4 w-4" strokeWidth={1.8} />}
            title={t("ui.providerConnection.codexTitle")}
            description={t("ui.providerConnection.codexDesc")}
            onClick={() => onSelect("codex-subscription")}
          />
          <ConnectionCard
            icon={<KeyRound className="h-4 w-4" strokeWidth={1.8} />}
            title={t("ui.providerConnection.customTitle")}
            description={t("onboarding.compatibleApi")}
            onClick={() => onSelect("api-key")}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
};
