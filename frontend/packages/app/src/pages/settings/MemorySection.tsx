import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Trash2 } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  DeleteConfirmDialog,
  SettingsRow,
  SettingsSection,
  Switch,
  Textarea,
} from "@valuz/ui";
import {
  memoryApi,
  useTranslation,
  type MemoryTarget,
  type MemoryView,
} from "@valuz/core";

export const MemorySection = () => {
  const { t } = useTranslation();
  const [view, setView] = useState<MemoryView | null>(null);
  const [loading, setLoading] = useState(true);
  const [clearTarget, setClearTarget] = useState<MemoryTarget | null>(null);
  // Locally-edited custom instructions; persisted on blur (see saveCustom).
  const [customInstructions, setCustomInstructions] = useState("");

  const load = useCallback(async () => {
    try {
      const v = await memoryApi.getMemory();
      setView(v);
      setCustomInstructions(v.custom_instructions);
    } catch {
      toast.error(t("settings.memory.loadFailed"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (key: "enabled" | "auto_extract", value: boolean) => {
    try {
      const next = await memoryApi.patchSettings({ [key]: value });
      setView((v) =>
        v
          ? { ...v, enabled: next.enabled, auto_extract: next.auto_extract }
          : v,
      );
    } catch {
      toast.error(t("settings.memory.saveFailed"));
    }
  };

  const saveCustom = async () => {
    const next = customInstructions.trim();
    if (next === (view?.custom_instructions ?? "")) return; // no change
    try {
      const res = await memoryApi.patchSettings({ custom_instructions: next });
      setCustomInstructions(res.custom_instructions);
      setView((v) =>
        v ? { ...v, custom_instructions: res.custom_instructions } : v,
      );
    } catch {
      toast.error(t("settings.memory.saveFailed"));
    }
  };

  const removeEntry = async (target: MemoryTarget, text: string) => {
    try {
      setView(await memoryApi.deleteEntry({ target, old_text: text }));
      toast.success(t("settings.memory.deleted"));
    } catch {
      toast.error(t("settings.memory.saveFailed"));
    }
  };

  const clearScope = async (target: MemoryTarget) => {
    try {
      setView(await memoryApi.clearScope({ target }));
      toast.success(t("settings.memory.cleared"));
    } catch {
      toast.error(t("settings.memory.saveFailed"));
    } finally {
      setClearTarget(null);
    }
  };

  const scopes: { target: MemoryTarget; label: string }[] = [
    { target: "user", label: t("settings.memory.scopeUser") },
    { target: "global", label: t("settings.memory.scopeGlobal") },
  ];

  const masterOn = view?.enabled ?? true;

  return (
    <SettingsSection
      title={t("settings.tab.memory.label")}
      desc={t("settings.tab.memory.desc")}
    >
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.memory.enabledLabel")}
            desc={t("settings.memory.enabledDesc")}
          >
            <Switch
              checked={masterOn}
              onCheckedChange={(v) => void toggle("enabled", v)}
            />
          </SettingsRow>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.memory.autoExtractLabel")}
            desc={t("settings.memory.autoExtractDesc")}
          >
            <Switch
              checked={view?.auto_extract ?? true}
              disabled={!masterOn}
              onCheckedChange={(v) => void toggle("auto_extract", v)}
            />
          </SettingsRow>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <div className="flex flex-col gap-2">
            <div>
              <div className="text-sm font-medium text-ink-heading">
                {t("settings.memory.customInstructionsLabel")}
              </div>
              <div className="mt-0.5 text-xs text-ink-meta">
                {t("settings.memory.customInstructionsDesc")}
              </div>
            </div>
            <Textarea
              value={customInstructions}
              maxLength={1500}
              rows={3}
              disabled={!masterOn}
              placeholder={t("settings.memory.customInstructionsPlaceholder")}
              onChange={(e) => setCustomInstructions(e.target.value)}
              onBlur={() => void saveCustom()}
            />
          </div>
        </CardContent>
      </Card>

      {scopes.map(({ target, label }) => {
        const entries = view?.entries[target] ?? [];
        return (
          <div key={target} className="mb-5">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-medium text-ink-heading">
                {label}
              </span>
              {entries.length > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setClearTarget(target)}
                >
                  {t("settings.memory.clearScope")}
                </Button>
              )}
            </div>
            <Card className="rounded-xl shadow-xs">
              <CardContent className="py-2">
                {loading ? (
                  <div className="py-3 text-sm text-ink-meta">
                    {t("settings.memory.loading")}
                  </div>
                ) : entries.length === 0 ? (
                  <div className="py-8 text-center text-sm text-ink-meta">
                    {t("settings.memory.empty")}
                  </div>
                ) : (
                  entries.map((text, idx) => (
                    <div
                      key={`${target}-${idx}`}
                      className="flex items-start justify-between gap-2 border-b border-[#f7f8fa] py-2.5 last:border-b-0 dark:border-surface-border"
                    >
                      <span className="whitespace-pre-wrap text-sm text-ink-body">
                        {text}
                      </span>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="shrink-0"
                        aria-label={t("common.delete")}
                        onClick={() => void removeEntry(target, text)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </div>
        );
      })}

      <DeleteConfirmDialog
        open={clearTarget !== null}
        onOpenChange={(open) => {
          if (!open) setClearTarget(null);
        }}
        itemName={
          clearTarget === "user"
            ? t("settings.memory.scopeUser")
            : clearTarget === "global"
              ? t("settings.memory.scopeGlobal")
              : undefined
        }
        onConfirm={() => {
          if (clearTarget) void clearScope(clearTarget);
        }}
      />
    </SettingsSection>
  );
};
