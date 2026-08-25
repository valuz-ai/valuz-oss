import { Loader2, Plug, Wrench } from "lucide-react";
import type { ReactNode } from "react";
import type { ToolInfo } from "@valuz/shared";
import { useI18n } from "../../hooks/use-i18n";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { ConnectorIcon } from "./ConnectorIcon";

export interface ConnectorDetailPanelProps {
  name: string;
  iconUrl?: string | null;
  description?: string | null;
  /** True → render the connected view (description + tools). False →
   *  render the "not connected" view with a Connect button. */
  connected: boolean;
  /** Raw connector status for an already-added connector that isn't yet
   *  connected (``pending_auth`` / ``error`` / ``connecting`` / ``idle``).
   *  Drives the error message under the Connect button. */
  errorMessage?: string | null;
  /** Connected-view tool list. ``undefined`` = still loading. */
  tools?: ToolInfo[];
  /** Non-null when the tool probe failed. */
  toolsError?: string | null;
  /** A connect/disconnect request is in flight — disables the buttons. */
  busy?: boolean;
  /** Built-in connector: labelled as such, and it cannot be removed. It can
   *  still be disconnected — the caller decides what that means (disabling a
   *  built-in, deleting anything else), so this no longer disables the button.
   *  Disabling it stranded owners whose credential had expired: a built-in
   *  showing "connected" offers no Connect button and had no way out. */
  systemManaged?: boolean;
  onConnect?: () => void;
  onDisconnect?: () => void;
  /** Edition/overlay-provided actions rendered in the detail header. */
  headerActions?: ReactNode;
}

export const ConnectorDetailPanel = ({
  name,
  iconUrl,
  description,
  connected,
  errorMessage,
  tools,
  toolsError,
  busy,
  systemManaged,
  onConnect,
  onDisconnect,
  headerActions,
}: ConnectorDetailPanelProps) => {
  const { t } = useI18n();

  // ── Not connected ──────────────────────────────────────────────────
  if (!connected) {
    return (
      <aside className="relative flex h-full flex-col items-center justify-center px-6 text-center">
        {headerActions ? (
          <div className="absolute right-4 top-4">{headerActions}</div>
        ) : null}
        <div className="w-[300px] -translate-y-[100px] rounded-xl px-5 py-8 text-center">
          <ConnectorIcon
            name={name}
            iconUrl={iconUrl}
            className="mx-auto h-10 w-10 rounded-xl"
          />
          <p className="mt-3 text-xs leading-[1.6] text-ink-body">
            {t("connector.notConnectedYet", { name })}
          </p>
          {errorMessage ? (
            <p className="mt-1 text-xs leading-[1.6] text-error-text">
              {errorMessage}
            </p>
          ) : null}
          {onConnect ? (
            <Button
              className="mt-3"
              size="sm"
              disabled={busy}
              onClick={onConnect}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Plug className="h-3.5 w-3.5" />
              )}
              {t("connector.connect")}
            </Button>
          ) : null}
        </div>
      </aside>
    );
  }

  // ── Connected ──────────────────────────────────────────────────────
  return (
    <aside className="flex h-full flex-col overflow-hidden">
      <div className="px-4 pb-2 pt-4">
        <div className="flex items-center gap-3">
          <ConnectorIcon name={name} iconUrl={iconUrl} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="truncate text-base font-medium text-ink-heading">
                {name}
              </span>
              {systemManaged ? (
                <Badge
                  variant="brand"
                  className="shrink-0"
                  title={t("connector.systemManaged")}
                >
                  {t("connector.systemManagedBadge")}
                </Badge>
              ) : null}
            </div>
          </div>
          {headerActions}
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={busy || !onDisconnect}
            title={
              systemManaged ? t("connector.systemManaged") : undefined
            }
            onClick={onDisconnect}
          >
            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            {t("connector.disconnect")}
          </Button>
        </div>
        {description ? (
          <p className="mt-3 text-sm leading-relaxed text-ink-body">
            {description}
          </p>
        ) : null}
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="mb-3 flex items-center gap-1.5 text-sm font-medium text-ink-heading">
          <Wrench className="h-3.5 w-3.5 text-ink-meta" />
          <span>{t("connector.toolsTitle")}</span>
          {tools ? <span className="text-ink-meta">{tools.length}</span> : null}
        </div>

        {toolsError ? (
          <p className="text-xs text-error-text">
            {t("connector.toolsError", { error: toolsError })}
          </p>
        ) : tools === undefined ? (
          <div className="flex items-center gap-2 text-xs text-ink-meta">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t("connector.loadingTools")}
          </div>
        ) : tools.length === 0 ? (
          <p className="text-xs text-ink-meta">{t("connector.noTools")}</p>
        ) : (
          <ul className="flex flex-col px-1">
            {tools.map((tool) => (
              <li
                key={tool.name}
                className="border-b border-surface-border/60 py-3 first:pt-1 last:border-b-0 last:pb-1"
              >
                <div className="font-mono text-xs font-medium break-all text-ink-heading">
                  {tool.name}
                </div>
                {tool.description ? (
                  <p className="mt-1.5 text-xs leading-relaxed text-ink-body">
                    {tool.description}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
};
