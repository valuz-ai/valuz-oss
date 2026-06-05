import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Keyboard } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  SettingsRow,
  SettingsSection,
  ThemeSelector,
  FontSizeSelector,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  cn,
} from "@valuz/ui";
import { clearOnboarded } from "../../lib/onboarding";
import { useTheme, type Theme, type FontSize } from "@valuz/core";
import { useSettingsStore } from "@valuz/core";
import { useTranslation } from "@valuz/core";
import {
  setLocale as setI18nLocale,
  type LocaleCode,
} from "@valuz/shared/i18n";

const defaultShortcuts: Record<string, string> = {
  "new-chat": "⌘ N",
  "command-palette": "⌘ K",
  send: "⌘ ⏎",
  upload: "⌘ U",
  settings: "⌘ ,",
};

export const GeneralSection = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { locale, setLocale: setStoreLocale } = useSettingsStore();
  const language = locale.startsWith("zh") ? "zh" : "en";
  const {
    theme: themeState,
    fontSize,
    setTheme: setThemeState,
    setFontSize,
  } = useTheme();

  const [rebindingKey, setRebindingKey] = useState<string | null>(null);
  const [shortcuts, setShortcuts] = useState<Record<string, string>>(() => {
    try {
      return JSON.parse(localStorage.getItem("valuz-shortcuts") ?? "{}");
    } catch {
      return {};
    }
  });

  const getKey = (id: string) => shortcuts[id] || defaultShortcuts[id] || "—";

  useEffect(() => {
    if (!rebindingKey) return;
    const handler = (e: KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const parts: string[] = [];
      if (e.metaKey || e.ctrlKey) parts.push("⌘");
      if (e.shiftKey) parts.push("⇧");
      if (e.altKey) parts.push("⌥");
      const key = e.key;
      if (!["Meta", "Control", "Shift", "Alt"].includes(key)) {
        parts.push(key === "Enter" ? "⏎" : key.toUpperCase());
        const combo = parts.join(" ");
        setShortcuts((prev) => {
          const next = { ...prev, [rebindingKey]: combo };
          localStorage.setItem("valuz-shortcuts", JSON.stringify(next));
          return next;
        });
        setRebindingKey(null);
        toast.success(t("settings.shortcuts.shortcutUpdated"));
      }
    };
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [rebindingKey, t]);

  return (
    <SettingsSection
      title={t("settings.tab.general.label")}
      desc={t("settings.tab.general.desc")}
    >
      <div className="mb-2 mt-5 text-sm font-medium text-ink-heading">
        {t("settings.appearance.title")}
      </div>
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.appearance.themeLabel")}
            desc={t("settings.appearance.themeDesc")}
          >
            <ThemeSelector
              theme={themeState}
              onChange={(th) => {
                setThemeState(th as Theme);
                toast.success(
                  th === "light"
                    ? t("settings.appearance.themeLight")
                    : th === "dark"
                      ? t("settings.appearance.themeDark")
                      : t("settings.appearance.themeAuto"),
                );
              }}
            />
          </SettingsRow>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.appearance.fontSizeLabel")}
            desc={t("settings.appearance.fontSizeDesc")}
          >
            <FontSizeSelector
              fontSize={fontSize}
              onChange={(s) => {
                setFontSize(s as FontSize);
                toast.success(t("settings.appearance.fontSizeChanged"));
              }}
            />
          </SettingsRow>
          <div className="my-5 h-px bg-[#f7f8fa] dark:bg-surface-border" />
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.appearance.languageLabel")}
            desc={t("settings.appearance.languageDesc")}
          >
            <div className="flex items-center">
              <Select
                value={language}
                onValueChange={(v) => {
                  const code = v === "zh" ? "zh-CN" : "en-US";
                  setStoreLocale(code);
                  setI18nLocale(code as LocaleCode);
                  toast.success(
                    v === "zh"
                      ? t("settings.appearance.languageZh")
                      : t("settings.appearance.languageEn"),
                  );
                }}
              >
                <SelectTrigger
                  size="sm"
                  className="h-7 w-auto min-w-[100px] text-xs"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="zh">中文</SelectItem>
                  <SelectItem value="en">English</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </SettingsRow>
        </CardContent>
      </Card>

      <div className="mb-2 text-sm font-medium text-ink-heading">
        {t("settings.onboarding.title")}
      </div>
      <Card className="mb-5 rounded-xl shadow-xs">
        <CardContent className="py-5">
          <SettingsRow
            className="px-0 py-0"
            label={t("settings.onboarding.rerunLabel")}
            desc={t("settings.onboarding.rerunDesc")}
          >
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                clearOnboarded();
                navigate("/welcome");
              }}
            >
              {t("settings.onboarding.rerunButton")}
            </Button>
          </SettingsRow>
        </CardContent>
      </Card>

      <div className="mb-2 text-sm font-medium text-ink-heading">
        {t("settings.shortcuts.title")}
      </div>
      <Card className="rounded-xl shadow-xs">
        <CardContent className="py-0">
          {[
            {
              label: t("settings.shortcuts.newChat"),
              id: "new-chat",
            },
            {
              label: t("settings.shortcuts.commandPalette"),
              id: "command-palette",
            },
            {
              label: t("settings.shortcuts.sendMessage"),
              id: "send",
            },
            {
              label: t("settings.shortcuts.uploadFile"),
              id: "upload",
            },
            {
              label: t("settings.shortcuts.openSettings"),
              id: "settings",
            },
          ].map((shortcut) => (
            <div
              key={shortcut.id}
              className="flex items-center justify-between border-b border-[#f7f8fa] px-0 py-3 last:border-b-0 dark:border-surface-border"
            >
              <span className="text-sm font-medium text-ink-heading">
                {shortcut.label}
              </span>
              <button
                type="button"
                onClick={() => setRebindingKey(shortcut.id)}
                className={cn(
                  "rounded-md border px-2.5 py-1 text-sm transition",
                  rebindingKey === shortcut.id
                    ? "border-brand bg-brand/5 text-brand animate-pulse"
                    : "border-surface-border bg-surface-soft text-ink-body hover:border-brand/40",
                )}
              >
                {rebindingKey === shortcut.id
                  ? t("settings.shortcuts.pressNewKey")
                  : getKey(shortcut.id)}
              </button>
            </div>
          ))}
        </CardContent>
      </Card>
      {rebindingKey && (
        <div className="mt-2 flex items-center gap-2 text-xs text-ink-meta">
          <Keyboard className="h-3 w-3" />
          {t("settings.shortcuts.pressNewHint")} ·{" "}
          <button
            type="button"
            className="rounded-md bg-secondary text-secondary-foreground transition-colors hover:bg-surface-muted hover:text-[#131313] dark:hover:text-ink-heading"
            onClick={() => setRebindingKey(null)}
          >
            {t("common.cancel")}
          </button>{" "}
          ·{" "}
          <button
            type="button"
            className="rounded-md bg-secondary text-secondary-foreground transition-colors hover:bg-surface-muted hover:text-[#131313] dark:hover:text-ink-heading"
            onClick={() => {
              setRebindingKey(null);
              setShortcuts({});
              localStorage.removeItem("valuz-shortcuts");
              toast.success(t("settings.shortcuts.defaultsRestored"));
            }}
          >
            {t("settings.shortcuts.resetDefaults")}
          </button>
        </div>
      )}
    </SettingsSection>
  );
};
