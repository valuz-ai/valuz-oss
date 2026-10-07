import { useMemo, useState } from "react";
import { toast } from "sonner";
import { FileArchive, FolderSymlink, Link2 } from "lucide-react";
import {
  Button,
  EmptyState,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@valuz/ui";
import {
  SlotContribution,
  appPluginsApi,
  useSlotRegistrations,
  useTranslation,
} from "@valuz/core";
import { usePlatform } from "@valuz/app/platform";
import { Notice } from "./Notice";
import { AppPluginInstallDialog } from "./AppPluginInstallDialog";
import { AppPluginInstalledList } from "./AppPluginInstalledList";
import type { InstallMode } from "./AppPluginInspectionView";
import { errorDetail } from "./app-plugin-helpers";
import { useAppPlugin } from "./use-app-plugins";

const INSTALLED_TAB = "installed";
const TABS_SLOT = "settings.plugins.app-plugins.tabs";

const SafeModeBanner = ({ reason }: { reason: string | null }) => {
  const { t } = useTranslation();
  const platform = usePlatform();
  const [busy, setBusy] = useState(false);

  // Leave safe mode, then reload the window so the renderer loads the
  // plugins it had paused (the platform bridge on the desktop, the browser's
  // own reload in the webui).
  const exit = async () => {
    setBusy(true);
    try {
      await appPluginsApi.setSafeMode(false);
      if (platform.windowReload) await platform.windowReload();
      else window.location.reload();
    } catch (error) {
      toast.error(
        t("pluginSettings.appPlugins.safeMode.exitFailed", {
          error: errorDetail(error),
        }),
      );
      setBusy(false);
    }
  };

  return (
    <div className="mb-3" data-slot="app-plugin-safe-mode">
      <Notice tone="warning">
        <p className="font-medium">
          {t("pluginSettings.appPlugins.safeMode.title")}
        </p>
        <p>{t("pluginSettings.appPlugins.safeMode.desc")}</p>
        {reason ? (
          <p className="break-words">
            {t("pluginSettings.appPlugins.safeMode.reason", { reason })}
          </p>
        ) : null}
        <div className="pt-1">
          <Button
            variant="outline"
            size="sm"
            disabled={busy}
            loading={busy}
            onClick={() => void exit()}
          >
            {t("pluginSettings.appPlugins.safeMode.exit")}
          </Button>
        </div>
      </Notice>
    </div>
  );
};

/**
 * 「第三方插件」 — the app plugins installed on this computer, with the
 * install entries (zip file, URL, dev folder) and the tabs an overlay adds
 * (`settings.plugins.app-plugins.tabs`: 我的 / 组织 / 市场). Hidden entirely
 * where the backend answers 403 / 404: a cloud deployment, or a build without
 * the ``oss-app-plugins`` feature.
 */
export const AppPluginBlock = () => {
  const { t } = useTranslation();
  const { state, refresh } = useAppPlugin();
  const [installMode, setInstallMode] = useState<InstallMode | null>(null);
  const [tab, setTab] = useState(INSTALLED_TAB);

  const registrations = useSlotRegistrations(TABS_SLOT);
  // One tab per registration, keyed by its slot key. The Installed tab is this
  // block's own, and a repeated key would hand Radix two triggers for one
  // value, so the higher-priority registration of a key wins.
  const extraTabs = useMemo(() => {
    const seen = new Set<string>([INSTALLED_TAB]);
    return registrations.flatMap((registration) => {
      const id = registration.key ?? registration.id;
      if (seen.has(id)) return [];
      seen.add(id);
      return [{ id, registration }];
    });
  }, [registrations]);

  // A tab an overlay took back (plugin unloaded) must not leave the user on a
  // blank panel.
  const activeTab =
    tab === INSTALLED_TAB || extraTabs.some((entry) => entry.id === tab)
      ? tab
      : INSTALLED_TAB;

  // While the first read is in flight nothing is shown: a bare OSS app or a
  // cloud deployment answers 403 / 404 and must not flash a heading first.
  if (state.phase === "loading" || state.phase === "unavailable") return null;

  const installed =
    state.phase === "ready" ? (
      <>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setInstallMode("file")}
          >
            <FileArchive />
            {t("pluginSettings.appPlugins.install.file")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setInstallMode("url")}
          >
            <Link2 />
            {t("pluginSettings.appPlugins.install.url")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setInstallMode("dev")}
          >
            <FolderSymlink />
            {t("pluginSettings.appPlugins.install.folder")}
          </Button>
        </div>
        {state.data.safe_mode ? (
          <SafeModeBanner reason={state.data.safe_mode_reason} />
        ) : null}
        <AppPluginInstalledList
          plugins={state.data.plugins}
          refresh={refresh}
        />
      </>
    ) : (
      <EmptyState
        title={t("pluginSettings.appPlugins.loadFailed")}
        description={state.phase === "error" ? state.message : undefined}
        action={
          <Button variant="outline" size="sm" onClick={refresh}>
            {t("common.retry")}
          </Button>
        }
      />
    );

  return (
    <section aria-labelledby="app-plugins-heading" className="mb-8">
      <div className="mb-3">
        <h3
          id="app-plugins-heading"
          className="text-sm font-semibold text-ink-heading"
        >
          {t("pluginSettings.appPlugins.title")}
        </h3>
        <p className="text-xs text-ink-body">
          {t("pluginSettings.appPlugins.desc")}
        </p>
      </div>

      {extraTabs.length === 0 ? (
        installed
      ) : (
        <Tabs
          value={activeTab}
          onValueChange={setTab}
          className="w-full min-w-0 gap-0"
        >
          <TabsList
            variant="line"
            aria-label={t("pluginSettings.appPlugins.tabsLabel")}
          >
            <TabsTrigger value={INSTALLED_TAB}>
              {t("pluginSettings.appPlugins.tabs.installed")}
            </TabsTrigger>
            {extraTabs.map(({ id, registration }) => (
              <TabsTrigger key={id} value={id}>
                {t(
                  (registration.label ?? registration.id) as Parameters<
                    typeof t
                  >[0],
                )}
              </TabsTrigger>
            ))}
          </TabsList>
          <TabsContent value={INSTALLED_TAB} className="mt-3 min-w-0">
            {installed}
          </TabsContent>
          {extraTabs.map(({ id, registration }) => (
            <TabsContent key={id} value={id} className="mt-3 min-w-0 px-3">
              <SlotContribution
                name={TABS_SLOT}
                registration={registration}
                context={{ refresh }}
              />
            </TabsContent>
          ))}
        </Tabs>
      )}

      {installMode ? (
        <AppPluginInstallDialog
          mode={installMode}
          onClose={() => setInstallMode(null)}
          onInstalled={refresh}
        />
      ) : null}
    </section>
  );
};
