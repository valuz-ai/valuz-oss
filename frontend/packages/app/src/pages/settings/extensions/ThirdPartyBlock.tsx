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
  thirdPartyApi,
  useSlotRegistrations,
  useTranslation,
} from "@valuz/core";
import { usePlatform } from "@valuz/app/platform";
import { Notice } from "./Notice";
import { ThirdPartyInstallDialog } from "./ThirdPartyInstallDialog";
import { ThirdPartyInstalledList } from "./ThirdPartyInstalledList";
import type { InstallMode } from "./ThirdPartyInspectionView";
import { errorDetail } from "./third-party-helpers";
import { useThirdParty } from "./use-third-party";

const INSTALLED_TAB = "installed";
const TABS_SLOT = "settings.extensions.third-party.tabs";

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
      await thirdPartyApi.setSafeMode(false);
      if (platform.windowReload) await platform.windowReload();
      else window.location.reload();
    } catch (error) {
      toast.error(
        t("extensions.thirdParty.safeMode.exitFailed", {
          error: errorDetail(error),
        }),
      );
      setBusy(false);
    }
  };

  return (
    <div className="mb-3" data-slot="third-party-safe-mode">
      <Notice tone="warning">
        <p className="font-medium">
          {t("extensions.thirdParty.safeMode.title")}
        </p>
        <p>{t("extensions.thirdParty.safeMode.desc")}</p>
        {reason ? (
          <p className="break-words">
            {t("extensions.thirdParty.safeMode.reason", { reason })}
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
            {t("extensions.thirdParty.safeMode.exit")}
          </Button>
        </div>
      </Notice>
    </div>
  );
};

/**
 * 「第三方插件」 — the third-party plugins installed on this computer, with the
 * install entries (zip file, URL, dev folder) and the tabs an overlay adds
 * (`settings.extensions.third-party.tabs`: 我的 / 组织 / 市场). Hidden entirely
 * where the backend answers 403 / 404: a cloud deployment, or a build without
 * the ``oss-third-party`` feature.
 */
export const ThirdPartyBlock = () => {
  const { t } = useTranslation();
  const { state, refresh } = useThirdParty();
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
            {t("extensions.thirdParty.install.file")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setInstallMode("url")}
          >
            <Link2 />
            {t("extensions.thirdParty.install.url")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setInstallMode("dev")}
          >
            <FolderSymlink />
            {t("extensions.thirdParty.install.folder")}
          </Button>
        </div>
        {state.data.safe_mode ? (
          <SafeModeBanner reason={state.data.safe_mode_reason} />
        ) : null}
        <ThirdPartyInstalledList
          plugins={state.data.plugins}
          refresh={refresh}
        />
      </>
    ) : (
      <EmptyState
        title={t("extensions.thirdParty.loadFailed")}
        description={state.phase === "error" ? state.message : undefined}
        action={
          <Button variant="outline" size="sm" onClick={refresh}>
            {t("common.retry")}
          </Button>
        }
      />
    );

  return (
    <section aria-labelledby="extensions-third-party-heading" className="mb-8">
      <div className="mb-3">
        <h3
          id="extensions-third-party-heading"
          className="text-sm font-semibold text-ink-heading"
        >
          {t("extensions.thirdParty.title")}
        </h3>
        <p className="text-xs text-ink-body">
          {t("extensions.thirdParty.desc")}
        </p>
      </div>

      {extraTabs.length === 0 ? (
        installed
      ) : (
        <Tabs value={activeTab} onValueChange={setTab}>
          <TabsList
            variant="line"
            aria-label={t("extensions.thirdParty.tabsLabel")}
          >
            <TabsTrigger value={INSTALLED_TAB}>
              {t("extensions.thirdParty.tabs.installed")}
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
          <TabsContent value={INSTALLED_TAB} className="mt-4">
            {installed}
          </TabsContent>
          {extraTabs.map(({ id, registration }) => (
            <TabsContent key={id} value={id} className="mt-4">
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
        <ThirdPartyInstallDialog
          mode={installMode}
          onClose={() => setInstallMode(null)}
          onInstalled={refresh}
        />
      ) : null}
    </section>
  );
};
