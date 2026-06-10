import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  type ReactNode,
} from "react";
import { useSearchParams } from "react-router-dom";
import {
  Activity,
  Cpu,
  FileText,
  Info,
  Palette,
  Radio,
  Settings,
} from "lucide-react";
import { cn } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { useRegistryStore } from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";

import { ModelSection } from "./settings/ModelSection";
import { ConnectorsSection } from "./settings/ConnectorsSection";
import { GeneralSection } from "./settings/GeneralSection";
import { ParsingSection } from "./settings/ParsingSection";
import { SystemLogsSettingsSection } from "./settings/SystemLogsSection";
import { AboutSection } from "./settings/AboutSection";

const SETTINGS_TAB_STORAGE_KEY = "valuz-settings-tab";

const TAB_ICON_MAP: Record<string, ReactNode> = {
  general: <Palette className="h-4 w-4" />,
  model: <Cpu className="h-4 w-4" />,
  parsing: <FileText className="h-4 w-4" />,
  "system-logs": <Activity className="h-4 w-4" />,
  about: <Info className="h-4 w-4" />,
  // Lucide icon names — used by overlay sections via `icon` field
  palette: <Palette className="h-4 w-4" />,
  cpu: <Cpu className="h-4 w-4" />,
  "file-text": <FileText className="h-4 w-4" />,
  activity: <Activity className="h-4 w-4" />,
  info: <Info className="h-4 w-4" />,
  radio: <Radio className="h-4 w-4" />,
};

const readStoredTab = (): string => {
  try {
    const raw = localStorage.getItem(SETTINGS_TAB_STORAGE_KEY);
    if (raw === "personalize" || raw === "appearance" || raw === "shortcuts") {
      return "general";
    }
    if (raw) {
      const ids = useRegistryStore.getState().settingsSections.map((s) => s.id);
      if (ids.includes(raw)) return raw;
    }
  } catch {
    // ignore
  }
  return "general";
};

const SECTION_MAP: Record<string, React.ComponentType> = {
  model: ModelSection,
  connectors: ConnectorsSection,
  general: GeneralSection,
  parsing: ParsingSection,
  "system-logs": SystemLogsSettingsSection,
  about: AboutSection,
};

export const SettingsPage = () => {
  const [searchParams] = useSearchParams();
  const { setHideHeader } = useProjectOutlet();
  const { t } = useTranslation();
  const settingsSections = useRegistryStore((s) => s.settingsSections);

  const nav = useMemo(
    () =>
      settingsSections.map((section) => ({
        id: section.id,
        icon: TAB_ICON_MAP[section.id] ??
          (section.icon ? TAB_ICON_MAP[section.icon] : undefined) ?? (
            <Settings className="h-4 w-4" />
          ),
        label: t(section.label as Parameters<typeof t>[0]),
        desc: t(section.description as Parameters<typeof t>[0]),
      })),
    [settingsSections, t],
  );

  useEffect(() => {
    setHideHeader(true);
    return () => setHideHeader(false);
  }, [setHideHeader]);

  const [tab, setTabState] = useState<string>(() => {
    const fromUrl = searchParams.get("tab");
    if (fromUrl) {
      const ids = useRegistryStore.getState().settingsSections.map((s) => s.id);
      if (ids.includes(fromUrl)) return fromUrl;
    }
    return readStoredTab();
  });

  const activeSectionComponent = settingsSections.find(
    (s) => s.id === tab,
  )?.component;

  const setTab = useCallback((next: string) => {
    setTabState(next);
    try {
      localStorage.setItem(SETTINGS_TAB_STORAGE_KEY, next);
    } catch {
      // ignore
    }
  }, []);

  const ActiveSection = activeSectionComponent ?? SECTION_MAP[tab] ?? null;

  return (
    <div className="flex h-full min-h-0 overflow-hidden bg-card">
      {/* Desktop sidebar */}
      <aside className="hidden w-[240px] shrink-0 overflow-y-auto border-r border-surface-border bg-surface-soft p-4 md:block">
        <div>
          <div className="space-y-0.5">
            {nav.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                className={cn(
                  "w-full rounded-lg px-2.5 py-2 text-left transition-all",
                  tab === item.id
                    ? "bg-[#F1F2F4] dark:bg-surface-muted"
                    : "hover:bg-surface-muted",
                )}
              >
                <div className="flex items-center gap-2.5">
                  <span
                    className={cn(
                      "shrink-0",
                      tab === item.id
                        ? "text-[#725cf9] dark:text-brand"
                        : "text-ink-body",
                    )}
                  >
                    {item.icon}
                  </span>
                  <span
                    className={cn(
                      "text-sm",
                      tab === item.id
                        ? "text-[#725cf9] dark:text-brand"
                        : "text-ink-heading",
                    )}
                  >
                    {item.label}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      </aside>

      {/* Mobile tab bar */}
      <div className="flex flex-1 flex-col md:hidden">
        <div className="flex items-center gap-1 overflow-x-auto border-b border-surface-border px-3 py-2">
          {nav.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={cn(
                "whitespace-nowrap rounded-full px-3 py-1.5 text-xs font-medium transition-colors",
                tab === item.id
                  ? "bg-brand text-white"
                  : "text-ink-body hover:bg-surface-muted",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto bg-[linear-gradient(180deg,#FFFFFF_0%,#FBFBFD_100%)] dark:bg-[linear-gradient(180deg,#131418_0%,#0f1012_100%)]">
        <div
          className={cn(
            "mx-auto max-w-[920px] px-5 md:px-8",
            tab === "system-logs"
              ? "flex h-full min-h-0 flex-col py-5 md:py-6"
              : "py-5 pb-12 md:py-6 md:pb-14",
          )}
        >
          {ActiveSection ? <ActiveSection /> : null}
        </div>
      </div>
    </div>
  );
};
