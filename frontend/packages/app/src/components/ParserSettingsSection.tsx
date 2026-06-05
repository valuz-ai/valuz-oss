/**
 * Parser settings UI — replaces the PR-1 hybrid/local/cloud placeholder.
 *
 * Two stacked sections:
 *
 * 1. **Configured Engines** — one card per plugin. Shows API-key config
 *    state, test button, and an inline key editor. Cloud plugins
 *    surface ``has_secret`` + ``is_configured`` so the user can spot
 *    "needs setup" at a glance.
 *
 * 2. **Parse Routing** — primary-engine dropdown + auto-inferred routing
 *    table. ``effective_by_kind`` is server-rendered so the table
 *    matches what the router actually does at parse time. The
 *    "fallback to local on error" switch lives here.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import {
  parserApi,
  type ParserRoutingResponse,
  type PluginDescriptor,
} from "@valuz/core";
import {
  Badge,
  Button,
  Card,
  CardContent,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogField,
  DialogFooter,
  DialogHeader,
  DialogInput,
  DialogTitle,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Switch,
} from "@valuz/ui";
import { Loader2, RefreshCw } from "lucide-react";
import { useTranslation } from "@valuz/core";
import type { I18nKey } from "@valuz/shared";

// Resolve a plugin-contributed i18n key (e.g. ``parser_mineru.config.
// api_token.label``) with a literal-string fallback. When ``key`` is
// null/undefined or resolves to the key itself (translation missing
// in the current locale), we return the fallback. The plugin's
// ``register()`` runs at bootstrap and merges its locale resources
// into the i18n state, so keys defined in ``locale.*.json`` resolve
// just like main-app keys.
function _withFallback(
  t: (
    k: I18nKey,
    fallback?: string | Record<string, string | number>,
  ) => string,
  key: string | null | undefined,
  fallback: string,
): string {
  if (!key) return fallback;
  const resolved = t(key as I18nKey);
  // ``t()`` returns the key string itself on miss; treat that as
  // "missing" so the inline ``*_zh`` keeps showing up.
  return resolved === key ? fallback : resolved;
}

// Radix Select treats value="" as "clear selection" and rejects it on
// SelectItem. Use this sentinel for the "inherit primary engine" option
// and translate back to "" before sending to the API.
const INHERIT_PRIMARY_SENTINEL = "__inherit_primary__";

const ALL_KINDS: readonly string[] = [
  "pdf",
  "image",
  "office",
  "spreadsheet",
  "web",
  "text",
];

const KIND_KEY: Record<string, string> = {
  pdf: "settings.parsing.kindPdf",
  image: "settings.parsing.kindImage",
  office: "settings.parsing.kindOffice",
  spreadsheet: "settings.parsing.kindSpreadsheet",
  web: "settings.parsing.kindWeb",
  text: "settings.parsing.kindText",
};

interface PluginCardProps {
  plugin: PluginDescriptor;
  onConfigure: (pluginId: string) => void;
  onTested: (pluginId: string, ok: boolean) => void;
  t: (
    key: I18nKey,
    fallback?: string | Record<string, string | number>,
  ) => string;
}

function PluginCard({ plugin, onConfigure, onTested, t }: PluginCardProps) {
  const [testing, setTesting] = useState(false);

  // Resolve the display name once so toast / labels never leak the raw
  // ``name_zh`` (which is the back-compat fallback string, hardcoded
  // Chinese on the backend descriptor).
  const pluginName = _withFallback(t, plugin.name_key, plugin.name_zh);

  const handleTest = useCallback(async () => {
    setTesting(true);
    try {
      const result = await parserApi.testPlugin(plugin.id);
      onTested(plugin.id, result.ok);
      if (result.ok) {
        toast.success(
          result.latency_ms
            ? t("settings.parsing.connectOkLatency", {
                name: pluginName,
                latency: String(result.latency_ms),
              })
            : t("settings.parsing.connectOk", { name: pluginName }),
        );
      } else {
        toast.error(
          t("settings.parsing.testPluginFailed", {
            name: pluginName,
            error: result.error ?? t("common.error"),
          }),
        );
      }
    } catch (err) {
      onTested(plugin.id, false);
      toast.error(
        t("settings.parsing.testRequestFailed", {
          error: err instanceof Error ? err.message : String(err),
        }),
      );
    } finally {
      setTesting(false);
    }
  }, [plugin.id, pluginName, onTested, t]);

  const statusBadge = !plugin.is_configured ? (
    <Badge variant="outline" className="text-2xs">
      {t("settings.parsing.notConfigured")}
    </Badge>
  ) : (
    <Badge variant="brand" className="bg-[#f3f2ff] text-2xs dark:bg-brand-light">
      {t("settings.parsing.configured")}
    </Badge>
  );

  const displayDescription = _withFallback(
    t,
    plugin.description_key,
    plugin.description_zh,
  );

  return (
    <Card className="rounded-xl">
      <CardContent className="space-y-2 px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-ink-heading">
                {pluginName}
              </span>
              {statusBadge}
              <Badge variant="outline" className="text-2xs">
                {plugin.mode === "sync"
                  ? t("settings.parsing.sync")
                  : t("settings.parsing.asyncPolling")}
              </Badge>
            </div>
            <div className="mt-1 text-xs leading-5 text-ink-body">
              {displayDescription}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              size="sm"
              variant="secondary"
              className="w-20"
              onClick={handleTest}
              disabled={testing}
            >
              {testing ? (
                <Loader2 className="mr-1 h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="mr-1 h-3 w-3" />
              )}
              {t("common.test")}
            </Button>
            {plugin.requires_secret ? (
              <Button
                size="sm"
                className="w-20"
                onClick={() => onConfigure(plugin.id)}
              >
                {plugin.is_configured
                  ? t("settings.parsing.updateConfig")
                  : t("settings.parsing.config")}
              </Button>
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

interface RoutingTableProps {
  plugins: PluginDescriptor[];
  routing: ParserRoutingResponse;
  onByKindChange: (kind: string, pluginId: string) => Promise<void>;
  t: (
    key: I18nKey,
    fallback?: string | Record<string, string | number>,
  ) => string;
}

function RoutingTable({
  plugins,
  routing,
  onByKindChange,
  t,
}: RoutingTableProps) {
  const pluginById = useMemo(
    () => new Map(plugins.map((p) => [p.id, p])),
    [plugins],
  );
  const lockedKinds = new Set(routing.locked_kinds);
  // Always resolve through the i18n helper so the rendered name follows
  // the current locale; plain ``p.name_zh`` lookups always return the
  // backend's hardcoded Chinese fallback.
  const resolveName = (p: PluginDescriptor | undefined): string =>
    p ? _withFallback(t, p.name_key, p.name_zh) : "—";

  return (
    <div>
      {ALL_KINDS.map((kind) => {
        const effectivePluginId = routing.effective_by_kind[kind];
        const effectivePlugin = pluginById.get(effectivePluginId ?? "");
        const isLocked = lockedKinds.has(kind);
        const override = routing.by_kind[kind];
        return (
          <div
            key={kind}
            className="flex items-center justify-between gap-3 py-3"
          >
            <div className="flex-1">
              <div className="text-sm text-ink-heading">
                {t((KIND_KEY[kind] ?? kind) as I18nKey)}
              </div>
              {isLocked ? (
                <div className="text-2xs text-ink-section">
                  {t("settings.parsing.lockedHint")}
                </div>
              ) : !effectivePlugin ? null : effectivePluginId ===
                routing.primary_plugin_id ? (
                <div className="text-2xs text-ink-section">
                  {t("settings.parsing.followPrimary", {
                    name: resolveName(effectivePlugin),
                  })}
                </div>
              ) : (
                <div className="text-2xs text-ink-section">
                  {t("settings.parsing.autoFallback", {
                    name: resolveName(effectivePlugin),
                  })}
                </div>
              )}
            </div>
            {isLocked ? (
              <Badge variant="outline">light_local</Badge>
            ) : (
              <Select
                // Radix Select forbids empty string as an item value
                // (it reserves "" for "clear selection"), so we use a
                // sentinel and translate it back to "" in the handler.
                value={override ?? INHERIT_PRIMARY_SENTINEL}
                onValueChange={(v) =>
                  onByKindChange(kind, v === INHERIT_PRIMARY_SENTINEL ? "" : v)
                }
              >
                <SelectTrigger className="w-[200px] text-xs">
                  <SelectValue
                    placeholder={t(
                      "settings.parsing.followPrimaryPlaceholder",
                      { name: resolveName(effectivePlugin) },
                    )}
                  />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={INHERIT_PRIMARY_SENTINEL}>
                    {t("settings.parsing.followPrimaryOption")}
                  </SelectItem>
                  {plugins
                    .filter((p) => p.supported_kinds.includes(kind))
                    .map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {resolveName(p)}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            )}
          </div>
        );
      })}
    </div>
  );
}

interface InlineKeyEditorProps {
  plugin: PluginDescriptor;
  onClose: () => void;
  onSaved: () => void;
  t: (
    key: I18nKey,
    fallback?: string | Record<string, string | number>,
  ) => string;
}

function InlineKeyEditor({
  plugin,
  onClose,
  onSaved,
  t,
}: InlineKeyEditorProps) {
  const [secret, setSecret] = useState("");
  const [options, setOptions] = useState<Record<string, string | boolean>>({});
  const [busy, setBusy] = useState(false);

  const visibleFields = plugin.config_schema;

  const handleSave = useCallback(async () => {
    setBusy(true);
    try {
      const patch: {
        enabled?: boolean;
        secret?: string;
        options?: Record<string, unknown>;
      } = { enabled: true };
      if (secret) patch.secret = secret;
      if (Object.keys(options).length > 0) patch.options = options;
      await parserApi.patchPluginConfig(plugin.id, patch);
      toast.success(t("settings.parsing.configSaved"));
      onSaved();
      onClose();
    } catch (err) {
      toast.error(
        t("settings.parsing.saveFailed", {
          error: err instanceof Error ? err.message : String(err),
        }),
      );
    } finally {
      setBusy(false);
    }
  }, [plugin.id, secret, options, onSaved, onClose, t]);

  const dialogName = _withFallback(t, plugin.name_key, plugin.name_zh);
  const dialogDescription = _withFallback(
    t,
    plugin.description_key,
    plugin.description_zh,
  );

  // Per-field label/help/placeholder helpers — pull from the plugin's
  // own i18n namespace via the ``*_key`` fields when present, otherwise
  // fall back to the inline ``*_zh`` strings the backend descriptor
  // still ships for back-compat. Same pattern for select options'
  // labels (``option_keys`` is parallel to ``options``).
  const fieldLabel = (f: (typeof visibleFields)[number]) =>
    _withFallback(t, f.label_key, f.label_zh);
  const fieldHelp = (f: (typeof visibleFields)[number]) =>
    f.help_key || f.help_zh
      ? _withFallback(t, f.help_key, f.help_zh ?? "")
      : "";
  const fieldPlaceholder = (f: (typeof visibleFields)[number]) =>
    f.placeholder_key || f.placeholder
      ? _withFallback(t, f.placeholder_key, f.placeholder ?? "")
      : "";
  const optionLabel = (
    f: (typeof visibleFields)[number],
    idx: number,
    inlineLabel: string,
  ) => {
    const k = f.option_keys?.[idx];
    return k ? _withFallback(t, k, inlineLabel) : inlineLabel;
  };

  return (
    // Uses the design-system Dialog (same as the Connectors config
    // dialog in SettingsPage) so padding / radius / shadow / overlay /
    // close button / centering all match. Was previously a bespoke
    // ``fixed inset-0`` + ``Card`` overlay with mismatched
    // ``rounded-2xl`` / ``max-w-md`` / ``shadow-xl``.
    <Dialog
      open
      onOpenChange={(o) => {
        if (!o) onClose();
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {t("settings.parsing.configurePlugin", { name: dialogName })}
          </DialogTitle>
          <DialogDescription>{dialogDescription}</DialogDescription>
        </DialogHeader>
        <div className="space-y-3 py-2">
          {visibleFields.map((f) => {
            if (f.type === "secret") {
              const help = fieldHelp(f);
              return (
                <DialogField
                  key={f.key}
                  label={fieldLabel(f)}
                  required={f.required}
                  help={help || undefined}
                  helpUrl={f.help_url || undefined}
                  helpLinkText={t("settings.parsing.getTokenLink")}
                >
                  <DialogInput
                    type="password"
                    value={secret}
                    placeholder={fieldPlaceholder(f)}
                    onChange={(e) => setSecret(e.target.value)}
                  />
                </DialogField>
              );
            }
            if (f.type === "bool") {
              const v = options[f.key];
              const help = fieldHelp(f);
              return (
                <div
                  key={f.key}
                  className="flex items-center justify-between gap-2"
                >
                  <div>
                    <div className="text-xs text-ink-meta">{fieldLabel(f)}</div>
                    {help ? (
                      <div className="mt-1 text-2xs text-ink-meta">{help}</div>
                    ) : null}
                  </div>
                  <Switch
                    checked={typeof v === "boolean" ? v : Boolean(f.default)}
                    onCheckedChange={(c) =>
                      setOptions((prev) => ({ ...prev, [f.key]: c }))
                    }
                  />
                </div>
              );
            }
            if (f.type === "select" && f.options) {
              const v = options[f.key] ?? f.default ?? f.options[0][0];
              return (
                <DialogField key={f.key} label={fieldLabel(f)}>
                  <Select
                    value={String(v)}
                    onValueChange={(value) =>
                      setOptions((prev) => ({ ...prev, [f.key]: value }))
                    }
                  >
                    <SelectTrigger className="w-full text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {f.options.map(([value, inlineLabel], idx) => (
                        <SelectItem key={value} value={value}>
                          {optionLabel(f, idx, inlineLabel)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </DialogField>
              );
            }
            const help = fieldHelp(f);
            return (
              <DialogField
                key={f.key}
                label={fieldLabel(f)}
                help={help || undefined}
              >
                <DialogInput
                  type="text"
                  value={String(options[f.key] ?? f.default ?? "")}
                  placeholder={fieldPlaceholder(f)}
                  onChange={(e) =>
                    setOptions((prev) => ({
                      ...prev,
                      [f.key]: e.target.value,
                    }))
                  }
                />
              </DialogField>
            );
          })}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </Button>
          <Button onClick={handleSave} disabled={busy}>
            {busy ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : null}
            {t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function ParserSettingsSection() {
  const { t } = useTranslation();
  const [plugins, setPlugins] = useState<PluginDescriptor[]>([]);
  const [routing, setRouting] = useState<ParserRoutingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<PluginDescriptor | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [pluginsResp, routingResp] = await Promise.all([
        parserApi.listPlugins(),
        parserApi.getRouting(),
      ]);
      setPlugins(pluginsResp.plugins);
      setRouting(routingResp);
    } catch (err) {
      console.error("parserApi load failed", err);
      toast.error(
        t("settings.parsing.loadFailed", {
          error: err instanceof Error ? err.message : String(err),
        }),
      );
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handlePrimaryChange = useCallback(
    async (pluginId: string) => {
      try {
        const updated = await parserApi.patchRouting({
          primary_plugin_id: pluginId,
        });
        setRouting(updated);
        const switched = plugins.find((p) => p.id === pluginId);
        const switchedName = switched
          ? _withFallback(t, switched.name_key, switched.name_zh)
          : pluginId;
        toast.success(
          t("settings.parsing.primarySwitched", { name: switchedName }),
        );
      } catch (err) {
        toast.error(
          t("settings.parsing.switchFailed", {
            error: err instanceof Error ? err.message : String(err),
          }),
        );
      }
    },
    [plugins, t],
  );

  const handleByKindChange = useCallback(
    async (kind: string, pluginId: string) => {
      if (!routing) return;
      try {
        const next = { ...routing.by_kind };
        if (!pluginId) {
          delete next[kind];
        } else {
          next[kind] = pluginId;
        }
        const updated = await parserApi.patchRouting({ by_kind: next });
        setRouting(updated);
      } catch (err) {
        toast.error(
          t("settings.parsing.updateFailed", {
            error: err instanceof Error ? err.message : String(err),
          }),
        );
      }
    },
    [routing, t],
  );

  const handleFallbackToggle = useCallback(
    async (value: boolean) => {
      try {
        const updated = await parserApi.patchRouting({
          fallback_to_local_on_error: value,
        });
        setRouting(updated);
      } catch (err) {
        toast.error(
          t("settings.parsing.updateFailed", {
            error: err instanceof Error ? err.message : String(err),
          }),
        );
      }
    },
    [t],
  );

  if (loading || !routing) {
    return (
      <div className="flex items-center gap-2 text-xs text-ink-section">
        <Loader2 className="h-3 w-3 animate-spin" />{" "}
        {t("settings.parsing.loadingConfig")}
      </div>
    );
  }

  // Sort by the backend descriptor's ``sort_weight``. Lower = earlier.
  // Built-ins: light_local=10 (always-available fallback first),
  // mineru=20, paddleocr=30, valuz_ocr=90 (placeholder last). Plugins
  // that don't carry a weight fall back to 50 (middle of the pack)
  // and break ties on id for determinism.
  const orderedPlugins = [...plugins].sort((a, b) => {
    const aw = a.sort_weight ?? 50;
    const bw = b.sort_weight ?? 50;
    if (aw !== bw) return aw - bw;
    return a.id.localeCompare(b.id);
  });

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-3">
          <div className="text-sm font-medium text-ink-heading">
            {t("settings.parsing.enginesSection")}
          </div>
          <div className="mt-0.5 text-xs text-ink-meta">
            {t(
              "settings.parsing.enginesSectionDesc" as Parameters<typeof t>[0],
            )}
          </div>
        </div>
        <div className="space-y-2">
          {orderedPlugins.map((p) => (
            <PluginCard
              key={p.id}
              plugin={p}
              onConfigure={() => setEditing(p)}
              onTested={() => void refresh()}
              t={t}
            />
          ))}
        </div>
      </div>

      <div>
        <div className="mb-3">
          <div className="text-sm font-medium text-ink-heading">
            {t("settings.parsing.routingSection")}
          </div>
          <div className="mt-0.5 text-xs text-ink-meta">
            {t(
              "settings.parsing.routingSectionDesc" as Parameters<typeof t>[0],
            )}
          </div>
        </div>
        <Card className="rounded-xl">
          <CardContent className="space-y-4 px-5 py-4">
            <div className="-mx-5 border-b border-surface-border px-5 pb-4">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium text-ink-heading">
                    {t("settings.parsing.primaryEngine")}
                  </div>
                  <div className="text-xs text-ink-body">
                    {t("settings.parsing.primaryEngineHint")}
                  </div>
                </div>
                <Select
                  value={routing.primary_plugin_id}
                  onValueChange={handlePrimaryChange}
                >
                  <SelectTrigger className="w-[200px] text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {orderedPlugins
                      .filter((p) => p.is_configured)
                      .map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {_withFallback(t, p.name_key, p.name_zh)}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="px-3">
              <RoutingTable
                plugins={orderedPlugins}
                routing={routing}
                onByKindChange={handleByKindChange}
                t={t}
              />
            </div>

            <div className="flex items-center justify-between gap-3 rounded-lg bg-surface-soft px-4 py-3">
              <div className="text-xs">
                <div className="font-medium text-ink-heading">
                  {t("settings.parsing.fallbackTitle")}
                </div>
                <div className="text-2xs text-ink-section">
                  {t("settings.parsing.fallbackDesc")}
                </div>
              </div>
              <Switch
                checked={routing.fallback_to_local_on_error}
                onCheckedChange={handleFallbackToggle}
              />
            </div>
          </CardContent>
        </Card>
      </div>

      {editing ? (
        <InlineKeyEditor
          plugin={editing}
          onClose={() => setEditing(null)}
          onSaved={() => void refresh()}
          t={t}
        />
      ) : null}
    </div>
  );
}
