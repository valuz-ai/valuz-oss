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
  Brain,
  Cpu,
  FileText,
  FlaskConical,
  Globe,
  HardDrive,
  Info,
  Palette,
  Radio,
  Settings,
  Wifi,
} from "lucide-react";
import { SettingsNav, cn } from "@valuz/ui";
import { useTranslation } from "@valuz/core";
import { useRegistryStore } from "@valuz/core";
import { useProjectOutlet } from "@valuz/app/layout";

import { ModelSection } from "./settings/ModelSection";
import { ConnectorsSection } from "./settings/ConnectorsSection";
import { GeneralSection } from "./settings/GeneralSection";
import { MemorySection } from "./settings/MemorySection";
import { BrowserSection } from "./settings/BrowserSection";
import { ParsingSection } from "./settings/ParsingSection";
import { BackupSection } from "./settings/BackupSection";
import { SystemLogsSettingsSection } from "./settings/SystemLogsSection";
import { AboutSection } from "./settings/AboutSection";
import { NetworkSection } from "./settings/NetworkSection";

const SETTINGS_TAB_STORAGE_KEY = "valuz-settings-tab";

const SETTINGS_TAB_ALIASES: Record<string, string> = {
  appearance: "general",
  shortcuts: "general",
  memory: "personalization",
  personalize: "personalization",
};

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
  "hard-drive": <HardDrive className="h-4 w-4" />,
  backup: <HardDrive className="h-4 w-4" />,
  brain: <Brain className="h-4 w-4" />,
  globe: <Globe className="h-4 w-4" />,
  browser: <Globe className="h-4 w-4" />,
  network: <Wifi className="h-4 w-4" />,
  flask: <FlaskConical className="h-4 w-4" />,
};

const readStoredTab = (): string => {
  try {
    const raw = localStorage.getItem(SETTINGS_TAB_STORAGE_KEY);
    if (raw) {
      const ids = useRegistryStore.getState().settingsSections.map((s) => s.id);
      const resolved = SETTINGS_TAB_ALIASES[raw] ?? raw;
      if (ids.includes(resolved)) return resolved;
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
  memory: MemorySection,
  personalization: MemorySection,
  browser: BrowserSection,
  parsing: ParsingSection,
  backup: BackupSection,
  "system-logs": SystemLogsSettingsSection,
  network: NetworkSection,
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
        group: section.group
          ? {
              id: section.group.id,
              label: t(section.group.label as Parameters<typeof t>[0]),
            }
          : undefined,
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
      const resolved = SETTINGS_TAB_ALIASES[fromUrl] ?? fromUrl;
      const ids = useRegistryStore.getState().settingsSections.map((s) => s.id);
      if (ids.includes(resolved)) return resolved;
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
      <SettingsNav items={nav} value={tab} onValueChange={setTab} />

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
