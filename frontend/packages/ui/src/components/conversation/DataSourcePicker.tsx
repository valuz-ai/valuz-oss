import { Database, Check, Plug, AlertCircle } from "lucide-react";
import { useI18n } from "../../hooks/use-i18n";

/**
 * One MCP data source as exposed by `GET /v1/providers/mcp`.
 *
 * Mirrors `McpProviderItem` in `@valuz/core` but kept structurally-typed
 * so this component stays usable from contexts (storybooks, tests) that
 * don't pull in the core API package.
 */
export interface DataSourceOption {
  slug: string;
  display_name: string;
  description?: string | null;
  modules: string[];
  connected: boolean;
  /** When false, the row is rendered but locked off — the catalog row exists
   *  but the host has the provider disabled (e.g. tenant policy). */
  enabled?: boolean;
}

interface DataSourcePickerProps {
  options: DataSourceOption[];
  selectedSlugs: string[];
  onChange: (next: string[]) => void;
  /**
   * Called when the user clicks a disconnected provider card. The shell
   * routes this to the OAuth start endpoint; the picker itself does not
   * navigate.
   */
  onConnect?: (slug: string) => void;
  className?: string;
}

/**
 * A compact toggleable list of MCP data sources for session creation.
 *
 * Visual contract:
 * - Connected providers render as toggleable chips with a check on selection.
 * - Disconnected providers render dimmed with a "Connect" affordance — selecting
 *   them is allowed but only meaningful after connection.
 * - Disabled-by-tenant providers render greyed and non-interactive.
 *
 * State is fully controlled — the parent owns `selectedSlugs` and is responsible
 * for forwarding the resulting list into `sessionsApi.create({ mcp_provider_slugs })`.
 */
export const DataSourcePicker = ({
  options,
  selectedSlugs,
  onChange,
  onConnect,
  className = "",
}: DataSourcePickerProps) => {
  const { t } = useI18n();
  const selected = new Set(selectedSlugs);

  const toggle = (slug: string, enabled: boolean) => {
    if (!enabled) return;
    const next = new Set(selected);
    if (next.has(slug)) next.delete(slug);
    else next.add(slug);
    onChange(Array.from(next));
  };

  if (options.length === 0) {
    return (
      <div className={`text-xs text-ink-meta ${className}`}>
        {t("conversation.noDataSource")}
      </div>
    );
  }

  return (
    <div className={`space-y-1.5 ${className}`}>
      <div className="flex items-center gap-1.5 text-2xs uppercase tracking-wider text-ink-label">
        <Database className="h-3 w-3" />
        {t("conversation.dataSource")}
      </div>
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => {
          const isSelected = selected.has(opt.slug);
          const isEnabled = opt.enabled !== false;
          const isConnected = opt.connected;
          const interactable = isEnabled && isConnected;

          // Disconnected: render as connect-prompt
          if (!isConnected) {
            return (
              <button
                key={opt.slug}
                type="button"
                disabled={!isEnabled}
                onClick={() => isEnabled && onConnect?.(opt.slug)}
                className="flex items-center gap-1.5 rounded-full border border-dashed border-surface-border bg-surface-soft px-3 py-1 text-xs text-ink-meta transition-colors hover:border-brand/40 hover:text-brand disabled:cursor-not-allowed disabled:opacity-40"
                title={t("conversation.dataSourceNotConnected", {
                  name: opt.display_name,
                })}
              >
                <Plug className="h-3 w-3" />
                <span>{opt.display_name}</span>
                <span className="text-2xs text-ink-muted">
                  {t("common.notConnected")}
                </span>
              </button>
            );
          }

          // Connected, may be selected or not
          return (
            <button
              key={opt.slug}
              type="button"
              disabled={!interactable}
              onClick={() => toggle(opt.slug, interactable)}
              className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs transition-colors ${
                isSelected
                  ? "border-brand bg-brand-light text-brand"
                  : "border-surface-border bg-surface text-ink-body hover:border-brand/40"
              } ${!interactable ? "cursor-not-allowed opacity-40" : ""}`}
              title={
                opt.description ??
                `${opt.display_name} (${opt.modules.length} modules)`
              }
              aria-pressed={isSelected}
            >
              {isSelected ? (
                <Check className="h-3 w-3" />
              ) : (
                <Database className="h-3 w-3" />
              )}
              <span>{opt.display_name}</span>
              {opt.modules.length > 0 ? (
                <span className="text-2xs text-ink-muted">
                  {opt.modules.length} modules
                </span>
              ) : null}
              {!isEnabled ? (
                <AlertCircle className="h-3 w-3 text-ink-meta" />
              ) : null}
            </button>
          );
        })}
      </div>
    </div>
  );
};
