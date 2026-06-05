import { Loader2, Plug, Wrench } from "lucide-react";
import type { ToolInfo } from "@valuz/shared";
import { useI18n } from "../../hooks/use-i18n";
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
  onConnect?: () => void;
  onDisconnect?: () => void;
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
  onConnect,
  onDisconnect,
}: ConnectorDetailPanelProps) => {
  const { t } = useI18n();

  // ── Not connected ──────────────────────────────────────────────────
  if (!connected) {
    return (
      <aside className="flex h-full flex-col items-center justify-center px-6 text-center">
        <ConnectorIcon name={name} iconUrl={iconUrl} className="h-12 w-12" />
        <p className="mt-4 max-w-xs text-sm text-ink-body">
          {t("connector.notConnectedYet", { name })}
        </p>
        {errorMessage ? (
          <p className="mt-2 max-w-sm text-xs text-error-text">
            {errorMessage}
          </p>
        ) : null}
        <Button className="mt-4" size="sm" disabled={busy} onClick={onConnect}>
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Plug className="h-3.5 w-3.5" />
          )}
          {t("connector.connect")}
        </Button>
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
            <div className="truncate text-base font-medium text-ink-heading">
              {name}
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="shrink-0"
            disabled={busy}
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
