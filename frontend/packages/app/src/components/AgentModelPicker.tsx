import { useEffect, useMemo, useState } from "react";
import {
  providersApi,
  useComposerProviders,
  useModelDefaults,
  useRuntimes,
  type ProviderDetail,
  type ProviderListItem,
  type RuntimeProvider,
} from "@valuz/core";
import {
  DialogField,
  cn,
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@valuz/ui";
import { useI18n } from "@valuz/ui";
import { modelLabel } from "@valuz/shared";

export interface AgentModelSelection {
  runtime: string;
  providerId: string | null;
  model: string;
}

export interface AgentModelPickerProps {
  /** Current selection (controlled). */
  value: AgentModelSelection;
  onChange: (next: AgentModelSelection) => void;
  className?: string;
  /** ``"grid"`` (default): runtime + model as two top-labelled fields in a
   *  2-column grid (create dialog). ``"rows"``: settings-style rows —
   *  label + description on the left, a fixed-width select on the right,
   *  divider between — matching the global Settings → Model section. */
  layout?: "grid" | "rows";
}

const COMPOSITE = (providerId: string, modelId: string) =>
  `${providerId}::${modelId}`;

/**
 * Runtime + provider/model picker for a project agent, mirroring the chat
 * composer: pick a runtime first, then a (provider, model) pair filtered to
 * that runtime. Self-contained — loads configured providers internally.
 */
export const AgentModelPicker = ({
  value,
  onChange,
  className,
  layout = "grid",
}: AgentModelPickerProps) => {
  const { t } = useI18n();
  const [providers, setProviders] = useState<ProviderDetail[]>([]);
  const { runtimes } = useRuntimes();
  const { defaults } = useModelDefaults();

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const list = await providersApi
          .list()
          .catch(() => ({ providers: [] as ProviderListItem[] }));
        const details = await Promise.all(
          list.providers
            .filter((p) => p.enabled)
            .map((p) => providersApi.get(p.id).catch(() => null)),
        );
        if (!cancelled) {
          setProviders(details.filter((d): d is ProviderDetail => d !== null));
        }
      } catch {
        if (!cancelled) setProviders([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const runtimeId =
    value.runtime || defaults?.default_runtime || "claude_agent";
  const models = useComposerProviders(providers, runtimeId as RuntimeProvider);

  const selectedKey = useMemo(() => {
    if (value.providerId && value.model) {
      return COMPOSITE(value.providerId, value.model);
    }
    // No explicit provider pinned — match the first model row carrying the
    // current model id so the dropdown still reflects the agent's model.
    const byModel = models.find((m) => m.modelId === value.model);
    return byModel ? COMPOSITE(byModel.providerId, byModel.modelId) : "";
  }, [value.providerId, value.model, models]);

  // Back-fill the resolved provider so what's displayed becomes what's saved:
  // when the agent has a model but no explicit provider pin, adopt the
  // provider hosting that model once the provider list loads. Fires once —
  // after it sets providerId the guard is false, so no render loop.
  useEffect(() => {
    if (value.providerId || !value.model) return;
    const byModel = models.find((m) => m.modelId === value.model);
    if (byModel) {
      onChange({
        runtime: runtimeId,
        providerId: byModel.providerId,
        model: value.model,
      });
    }
  }, [models, value.providerId, value.model, runtimeId, onChange]);

  const runtimeOptions = useMemo(
    () =>
      runtimes.map((r) => ({
        id: r.id,
        label: r.display_name,
        available: r.available,
      })),
    [runtimes],
  );

  // The currently-selected model row (for the rows layout's provider-name
  // hint + a clean model-id-only trigger label).
  const selectedRow = useMemo(
    () =>
      models.find((m) => COMPOSITE(m.providerId, m.modelId) === selectedKey),
    [models, selectedKey],
  );

  // Group models by provider so the dropdown matches the Settings → Model
  // list: provider as a group header, the item shows only the model name
  // (no inline "· provider" suffix, and no provider label outside the
  // trigger).
  const modelGroups = useMemo(() => {
    const groups: {
      providerId: string;
      providerName: string;
      rows: typeof models;
    }[] = [];
    for (const m of models) {
      let g = groups.find((x) => x.providerId === m.providerId);
      if (!g) {
        g = { providerId: m.providerId, providerName: m.providerName, rows: [] };
        groups.push(g);
      }
      g.rows.push(m);
    }
    return groups;
  }, [models]);

  const modelMenu = (
    <SelectContent>
      {modelGroups.map((g, idx) => (
        <SelectGroup key={g.providerId}>
          {idx > 0 && (
            <div className="my-1 border-t border-[#f7f8fa] dark:border-surface-border" />
          )}
          <SelectLabel className="text-2xs font-medium uppercase tracking-wide text-ink-meta">
            {g.providerName}
          </SelectLabel>
          {g.rows.map((m) => (
            <SelectItem
              key={COMPOSITE(m.providerId, m.modelId)}
              value={COMPOSITE(m.providerId, m.modelId)}
            >
              {modelLabel(m.modelId)}
            </SelectItem>
          ))}
        </SelectGroup>
      ))}
    </SelectContent>
  );

  const runtimeSelect = (
    <Select
      value={runtimeId}
      onValueChange={(rt) =>
        onChange({ runtime: rt, providerId: null, model: "" })
      }
    >
      <SelectTrigger size="sm" className="h-8 w-[200px] text-xs">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {runtimeOptions.map((r) => (
          <SelectItem key={r.id} value={r.id} disabled={!r.available}>
            {r.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );

  const modelSelect = (
    <Select
      value={selectedKey}
      onValueChange={(key) => {
        const [providerId, modelId] = key.split("::");
        onChange({ runtime: runtimeId, providerId, model: modelId });
      }}
    >
      <SelectTrigger size="sm" className="h-8 w-[200px] text-xs">
        {selectedRow ? (
          <span className="truncate text-ink-heading">
            {modelLabel(selectedRow.modelId)}
          </span>
        ) : (
          <SelectValue
            placeholder={t("agent.selectModel" as Parameters<typeof t>[0])}
          />
        )}
      </SelectTrigger>
      {modelMenu}
    </Select>
  );

  if (layout === "rows") {
    // Settings-style rows: label + description left, fixed select right,
    // divider between (matches the global Settings → Model section).
    return (
      <div className={className}>
        <div className="flex items-center gap-4 border-b border-[#f7f8fa] py-3 dark:border-surface-border">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-ink-heading">
              {t("agent.runtimeLabel" as Parameters<typeof t>[0])}
            </div>
            <div className="mt-0.5 text-xs text-ink-body">
              {t("agent.runtimeDesc" as Parameters<typeof t>[0])}
            </div>
          </div>
          {runtimeSelect}
        </div>
        <div className="flex items-center gap-4 border-b border-[#f7f8fa] py-3 dark:border-surface-border">
          <div className="min-w-0 flex-1">
            <div className="text-sm font-medium text-ink-heading">
              {t("agent.model")}
            </div>
            <div className="mt-0.5 text-xs text-ink-body">
              {t("agent.modelDesc" as Parameters<typeof t>[0])}
            </div>
          </div>
          {modelSelect}
        </div>
      </div>
    );
  }

  return (
    // When a ``className`` is supplied it REPLACES the default grid (e.g.
    // the agent detail tab passes ``"contents"`` so these two fields flow
    // into a parent 3-column row alongside 推理强度). Standalone callers
    // (create dialog) omit it and get the default 2-column grid.
    <div
      className={cn(
        className ?? "grid gap-3 sm:grid-cols-[160px_minmax(0,1fr)]",
      )}
    >
      <DialogField label={t("agent.runtimeLabel" as Parameters<typeof t>[0])}>
        <Select
          value={runtimeId}
          onValueChange={(rt) =>
            // Switching runtime clears the model — it's re-picked from the
            // newly-filtered list (matches composer behaviour).
            onChange({ runtime: rt, providerId: null, model: "" })
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {runtimeOptions.map((r) => (
              <SelectItem key={r.id} value={r.id} disabled={!r.available}>
                {r.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </DialogField>
      <DialogField label={t("agent.model")}>
        <Select
          value={selectedKey}
          onValueChange={(key) => {
            const [providerId, modelId] = key.split("::");
            onChange({ runtime: runtimeId, providerId, model: modelId });
          }}
        >
          <SelectTrigger className="w-full">
            <SelectValue
              placeholder={t("agent.selectModel" as Parameters<typeof t>[0])}
            />
          </SelectTrigger>
          {modelMenu}
        </Select>
      </DialogField>
    </div>
  );
};
